"""CLI directory confirmation bridged to existing approvals and policy layers.

No model tool exposes these Host functions. Persisted approvals alone cannot
install a rule: the live Runtime must bind an owned Task and refresh it at I/O.
"""
import re
from pathlib import Path

from Sprout.security.access import AccessDecision, ActionRequest, ActionType
from Sprout.security.layered_policy import LayeredPolicyEngine, PolicyLayer, PolicyRule
from Sprout.task.models import DelegationScope
from Sprout.task.paths import scope_path
from Sprout.workspace.models import ResourceKind, ResourceRef


def explicit_request(text):
    """A CLI imperative can select analysis; it never grants its effects."""
    text = text.strip().casefold()
    if re.search(r'(?:不要|无需|不必|停止|取消)\s*(?:再|重新|继续)?\s*(?:分析|扫描)|'
                 r"\b(?:do not|don't)\s+(?:analy[sz]e|scan)", text):
        return False
    return bool(re.search(
        r'^(?:请|帮我|麻烦)?\s*(?:重新)?(?:分析|扫描|analy[sz]e\b|inspect\b)|'
        r'^(?:请)?继续.*(?:未完成|失败|被拒绝).*(?:项目分析|分析项目)', text))


class _DirectoryLayer(PolicyLayer):
    def __init__(self, runtime):
        super().__init__('cli-directory-consent')
        self.runtime = runtime

    def match(self, request):
        record = self.runtime._cli_directory_tasks.get(request.task_id)
        if (request.action is not ActionType.DIRECTORY_LIST or record is None
                or request.resource is None or request.actor != record['principal']
                or request.scope != record['scope']
                or request.resource.workspace_id != record['workspace_id']):
            return None
        target, path = scope_path(record['arguments']['path']), scope_path(request.resource.path)
        if target is None or path is None or not target.contains(path):
            return None
        return PolicyRule(action='directory.list', decision=AccessDecision.ALLOW,
                          id='cli-directory:' + record['approval_id'])


async def context(runtime, workspace, session, principal, target):
    """Resolve the exact Host resource and any still-valid same-Session grant."""
    owned = await runtime._sessions.require_owner(session.id, principal)
    current = await runtime.get_workspace(workspace.id)
    if (not principal.authenticated or principal.source != 'cli'
            or runtime._sessions.frozen_principal(owned) != principal or current != workspace
            or runtime.session_workspace_id(owned) not in {'', workspace.id}):
        raise PermissionError('cli_analysis_identity_unavailable')
    root = workspace.root.resolve(strict=True)
    target = Path(target).resolve(strict=True)
    if not target.is_relative_to(root) or not target.is_dir():
        raise PermissionError('analysis_directory_selection_invalid')
    scope = DelegationScope(
        allowed_actions=frozenset({'tool.invoke', 'file.read', 'directory.list'}),
        allowed_paths=(str(target),))
    policy = runtime._policy_engine
    if not isinstance(policy, LayeredPolicyEngine) or runtime._file_broker._policy is not policy:
        raise PermissionError('cli_directory_policy_binding_unavailable')
    decision = policy.decide(ActionRequest(actor=principal, action=ActionType.DIRECTORY_LIST,
        resource=ResourceRef(workspace.id, str(target), ResourceKind.OTHER), scope=scope))
    if decision.decision not in {AccessDecision.ALLOW, AccessDecision.REQUIRE_APPROVAL}:
        raise PermissionError('directory.list denied by current Policy; review the directory rule')
    arguments = {'workspace_id': workspace.id, 'path': target.as_posix(), 'recursive': True}
    reusable = await runtime.approvals._usable_record('directory.list', arguments,
        task_id='', session_id=owned.id, requested_by=principal.user_id, source=principal.source,
        resource_scope=arguments['path'])
    return owned, scope, arguments, reusable


async def bind(runtime, task, workspace, owned, principal, arguments):
    """Called only after the foreground CLI confirmed this exact directory."""
    from Sprout.task.models import TaskExecutionMode, TaskStatus

    _, expected_scope, expected_arguments, _ = await context(
        runtime, workspace, owned, principal, arguments['path'])
    stored = await runtime.get_task(task.id)
    if (stored != task or task.actor != principal or task.source != principal.source
            or task.workspace_id != workspace.id or task.metadata.get('session_id') != owned.id
            or task.delegation_scope != expected_scope or arguments != expected_arguments
            or task.execution_mode is not TaskExecutionMode.READ_ONLY
            or task.status not in {TaskStatus.CREATED, TaskStatus.READY}
            or await runtime._metadata_or_raise().list_execution_nodes(task.id)):
        raise PermissionError('cli_directory_task_binding_unavailable')
    if not await runtime.approvals.is_approved('directory.list', arguments, task_id=task.id,
            session_id=owned.id, requested_by=principal.user_id, source=principal.source,
            resource_scope=arguments['path']):
        record = await runtime.approvals.request('directory.list', arguments, task_id=task.id,
            session_id=owned.id, requested_by=principal.user_id, source=principal.source,
            single_use=False, resource_scope=arguments['path'])
        await runtime.approvals.decide(record.id, True, decided_by=principal.user_id,
            channel='cli', reason='Foreground CLI confirmed this exact read-only directory')
    record = await runtime.approvals._usable_record('directory.list', arguments,
        task_id=task.id, session_id=owned.id, requested_by=principal.user_id,
        source=principal.source,
        resource_scope=arguments['path'])
    if record is None:
        raise PermissionError('cli_directory_approval_unavailable')
    scope = task.delegation_scope.bind_paths(str(workspace.root))
    runtime._cli_directory_tasks[task.id] = {
        'principal': principal, 'session_id': owned.id, 'workspace_id': workspace.id,
        'scope': scope.narrow(scope), 'arguments': arguments, 'approval_id': record.id}
    if runtime._cli_directory_layer is None:
        runtime._cli_directory_layer = _DirectoryLayer(runtime)
        runtime._policy_engine.add_runtime_layer(runtime._cli_directory_layer)


async def refresh(runtime, task, workspace):
    record = runtime._cli_directory_tasks.get(task.id)
    if record is None:
        return
    owned = await runtime._sessions.require_owner(record['session_id'], task.actor)
    scope = task.delegation_scope.bind_paths(str(workspace.root))
    approved = await runtime.approvals._usable_record('directory.list', record['arguments'],
        task_id=task.id, session_id=owned.id, requested_by=task.actor.user_id,
        source=task.actor.source, resource_scope=record['arguments']['path'])
    if (approved is None or approved.id != record['approval_id']
            or task.actor != record['principal'] or workspace.id != record['workspace_id']
            or task.metadata.get('session_id') != owned.id
            or runtime._sessions.frozen_principal(owned) != task.actor
            or runtime.session_workspace_id(owned) not in {'', workspace.id}
            or scope.narrow(scope) != record['scope']):
        runtime._cli_directory_tasks.pop(task.id, None)
        raise PermissionError('cli_directory_consent_changed; confirm the directory again')
