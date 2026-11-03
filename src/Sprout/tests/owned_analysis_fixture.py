"""Real Task/Session/Guard owner for finite project-tool integration tests.

No source records, completed nodes or analysis results are injected. The test
operation can exercise approvals inside the live owner before Guard settlement.
"""
from Sprout.gateway.project_gateway import ProjectGateway
from Sprout.orchestration.models import ExecutionGraph, ExecutionNode, NodeType
from Sprout.orchestration.service import SequentialOrchestrator
from Sprout.security.policy import SecurityPolicy
from Sprout.tests.test_project_cli import _subject
from Sprout.tests.test_r04_gateway_authority_native import _runtime
from Sprout.tools.executor import ToolExecutor


async def owned_analysis(tmp_path, operation, *, policy=None, security_policy=None):
    runtime, workspace = await _runtime(tmp_path)
    gateway = ProjectGateway(runtime, transport="cli", default_user="display-only")
    subject = await _subject(gateway, workspace.id)
    task = subject["analysis_task"]
    node = ExecutionNode(task_id=task.id, type=NodeType.AGENT)
    result = []

    async def execute(running):
        current = await runtime.get_task(task.id)
        with runtime._project_analysis_scope(current, workspace, running):
            tool = await runtime._issue_project_analysis_tool(paths=("service.py",))
            dispatch = ToolExecutor(runtime.tools, approvals=runtime._approvals,
                policy_engine=policy or runtime._policy_engine,
                policy=security_policy or SecurityPolicy(),
                scoped={tool.spec.name: tool}, scoped_only=True)

            async def call(arguments):
                return await dispatch.execute(tool.spec.name, arguments, task_id=task.id,
                    session_id=task.metadata["session_id"], source=task.source,
                    requested_by=task.actor.user_id)

            value = await operation(tool, call, runtime._approvals, workspace)
            result.append(value)
            return {"operation_completed": True}

    try:
        outcome = await SequentialOrchestrator(runtime.storage.metadata).execute(
            ExecutionGraph(task_id=task.id, nodes=(node,), entry_node_ids=(node.id,)),
            handler=execute,
        )
        assert outcome.status.value == "completed", outcome
        saved = await runtime.storage.metadata.get_execution_node(node.id)
        assert saved.metadata["execution_guard"]["state"] == "completed"
        return result[0]
    finally:
        await runtime.stop()
