"""Runtime: coordinates one online turn.

The Runtime knows about agents, sessions, context, tools, and skills. It never
knows about transports (CLI, MCP, Web), vendors, or the growth layer; see
``Sprout.runtime.factory`` for assembly and the constraint docs in the README.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from contextlib import contextmanager
from dataclasses import replace
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4
from weakref import WeakKeyDictionary

from Sprout.agent.base import Agent, AgentResult
from Sprout.agent.router import AgentRouter
from Sprout.context.builder import ContextBuilder
from Sprout.events import (
    AGENT_COMPLETED,
    AGENT_FAILED,
    AGENT_STARTED,
    INTENT_RECOGNIZED,
    INTENT_TASK_REQUESTED,
    MESSAGE_PERSISTED,
    MESSAGE_RECEIVED,
    MESSAGE_SENT,
    RUNTIME_STARTED,
    RUNTIME_STOPPED,
    SESSION_CREATED,
    Event,
    EventBus,
    register_builtin_events,
)
from Sprout.events.catalog import TASK_MODE_CONFIRMED
from Sprout.execution.apply import ApplyBroker
from Sprout.execution.database_broker import DatabaseBroker
from Sprout.execution.file_broker import FileBroker
from Sprout.execution.git_broker import GitBroker
from Sprout.execution.models import (
    ApplyResult,
    ChangeProposal,
    ChangeProposalStatus,
    ProcessResult,
    SandboxRef,
)
from Sprout.execution.network_broker import NetworkBroker
from Sprout.execution.process_broker import ProcessBroker
from Sprout.execution.sandbox_tool import SandboxReadTool
from Sprout.gateway.identity import Principal
from Sprout.intents import (
    INTENT_EVENTS,
    Intent,
    WriteIntentDisposition,
    detect_task_hint,
    intent_event,
    write_intent_disposition,
)
from Sprout.llm.language import detect_language
from Sprout.llm.messages import LLMMessage
from Sprout.message.attachment import Attachment
from Sprout.message.converter import assistant_envelope, turn_envelope
from Sprout.message.models import Message, OutboundMessage, StreamChunk
from Sprout.orchestration.compiler import ExecutionGraphBuilder
from Sprout.orchestration.models import ExecutionGraph, ExecutionNode, NodeStatus, NodeType
from Sprout.orchestration.terminal import TemporalOrchestratorBackend
from Sprout.registry.base import Registry
from Sprout.rootstock.contract import SessionStore
from Sprout.runtime.changes import ChangeProposalService
from Sprout.runtime.clarification import (
    clarified_instruction,
    route_clarification,
    route_semantic_clarification,
)
from Sprout.runtime.intent_analysis import PROTOCOL, parse_analysis, response_example
from Sprout.runtime.locks import WorkspaceLockManager
from Sprout.runtime.middleware import LoggingMiddleware, MiddlewareChain
from Sprout.runtime.nodes import NodeExecutor
from Sprout.runtime.queue import TaskQueueBackend, TemporalTaskQueue, create_task_queue
from Sprout.runtime.result import RuntimeInfo
from Sprout.runtime.state import TaskStateMachine
from Sprout.runtime.workspaces import WorkspaceService
from Sprout.sandbox.lifecycle import SandboxLifecycleService
from Sprout.security.approval import ApprovalManager, ApprovalRecord, ApprovalStatus
from Sprout.security.audit import SecurityAuditLog
from Sprout.security.engine import PolicyEngine
from Sprout.security.layer import SecurityLayer
from Sprout.security.layered_policy import LayeredPolicyEngine
from Sprout.security.redact import Redactor
from Sprout.security.secrets import EnvSecretProvider, SecretProvider
from Sprout.session.manager import SessionManager
from Sprout.session.models import Session, Turn
from Sprout.skills.models import Skill
from Sprout.skills.resolver import SkillResolver
from Sprout.storage.contracts.knowledge import KnowledgeItem
from Sprout.storage.contracts.metadata import MetadataStore
from Sprout.storage.embeddings import HashingEmbedder
from Sprout.storage.lanes import CONTEXT_SNAPSHOTS_NS, SixLaneFanout
from Sprout.task.models import (
    DelegationScope,
    Task,
    TaskBudget,
    TaskExecutionMode,
    TaskResult,
    TaskStatus,
    execution_metadata,
)
from Sprout.trajectory.models import ArtifactSnapshot, TrajectoryEvent
from Sprout.trajectory.recorder import JsonlTrajectoryRecorder, trajectory_path
from Sprout.workspace.intelligence import WorkspaceAnalysis
from Sprout.workspace.models import ReadPlan, Workspace, WorkspaceManifest
from Sprout.workspace.read_broker import ReadBroker
from Sprout.workspace.scanner import WorkspaceScanner

logger = logging.getLogger("Sprout.runtime")

_DEFAULT_TRAJECTORY_DIR = Path.home() / ".sprout" / "data" / "trajectory"

_INTENT_EVENTS: dict[str, str] = {str(intent): event for intent, event in INTENT_EVENTS.items()}
_PROJECT_ANALYZE_TERMS = (
    "分析当前整个项目", "分析整个项目", "分析完整项目", "entire project", "whole project",
    "scan project",
    "analyze project",
    "inspect project",
    "project structure",
    "entry file",
    "dependencies",
    "manifest",
    "docker",
    "workspace",
    "扫描项目",
    "掃描專案",
    "分析项目",
    "分析当前项目",
    "分析專案",
    "项目结构",
    "專案結構",
    "入口文件",
    "依赖",
    "依賴",
    "プロジェクトをスキャン",
    "プロジェクトを分析",
    "構成",
    "依存関係",
    "프로젝트 스캔",
    "프로젝트 분석",
    "프로젝트 구조",
    "의존성",
    "сканируй проект",
    "проанализируй проект",
    "структура проекта",
    "зависимости",
    "analizar proyecto",
    "escanear proyecto",
    "estructura del proyecto",
    "dependencias",
    "analisar projeto",
    "escanear projeto",
    "estrutura do projeto",
    "dependências",
)


def _package_version() -> str:
    try:
        return version("seam-sprout")
    except PackageNotFoundError:
        return "0.0.0.dev0"


def _clamp_float(value: Any, *, default: float) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, score))


def _parse_intent_json(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if not stripped:
        return {"intent": "conversation", "confidence": 0.0, "reason": "empty_llm_response"}
    if "```" in stripped:
        chunks = stripped.split("```")
        stripped = next(
            (
                chunk.removeprefix("json").strip()
                for chunk in chunks
                if chunk.strip().startswith(("{", "json"))
            ),
            stripped,
        )
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        stripped = stripped[start : end + 1]
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return {"intent": "conversation", "confidence": 0.0, "reason": "invalid_llm_json"}
    return parsed if isinstance(parsed, dict) else {"intent": "conversation"}


def _agent_metadata(metadata: Mapping[str, Any]) -> Mapping[str, Any]:
    if metadata.get("approval_required") or metadata.get("pending_tool_calls"):
        return metadata
    agent = metadata.get("agent")
    if isinstance(agent, Mapping):
        return agent
    return {}


def _project_analyze_tool_call(message: Message, intent: str) -> dict[str, Any] | None:
    if intent != "workspace":
        return None
    text = message.content.casefold()
    if not any(term.casefold() in text for term in _PROJECT_ANALYZE_TERMS):
        return None
    metadata = dict(message.metadata)
    raw_path = (
        metadata.get("workspace_path")
        or metadata.get("project_path")
        or metadata.get("path")
        or metadata.get("root")
        or Path.cwd().as_posix()
    )
    path = str(raw_path)
    return {
        "id": "intent-project-analyze",
        "name": "project_analyze",
        "arguments": {
            "path": path,
            "instruction": message.content,
        },
    }


def _weighted_context_summary(text: str, score: float) -> dict[str, Any]:
    weight = _clamp_float(score, default=0.0)
    sentences = _split_sentences(text)
    if weight >= 0.72:
        detail_level = "high"
        distilled = " ".join(sentences[:6])[:2000] if sentences else text[:2000]
    elif weight >= 0.38:
        detail_level = "medium"
        distilled = " ".join(sentences[:3])[:900] if sentences else text[:900]
    else:
        detail_level = "low"
        distilled = _abstract_context_sentence(sentences, text)
    return {
        "context_weight": round(weight, 4),
        "detail_level": detail_level,
        "distilled_text": distilled,
    }


def _split_sentences(text: str) -> list[str]:
    import re

    pieces = re.split(r"(?<=[。！？.!?])\s+|\n+", text.strip())
    return [piece.strip() for piece in pieces if piece.strip()]


def _abstract_context_sentence(sentences: list[str], text: str) -> str:
    source = sentences[0] if sentences else text.strip()
    if not source:
        return ""
    words = source.split()
    if len(words) > 28:
        return " ".join(words[:28]) + " ..."
    return source[:280]


#: Turn bodies larger than this are offloaded to the blobstore and the turn
#: keeps a preview plus the envelope reference (RUNTIME_DATA_PLAN §2.5).
TURN_BLOB_THRESHOLD_BYTES = 8192

#: How much of an offloaded body stays inline in the turn row.
TURN_PREVIEW_CHARS = 4096

#: Marks a held user turn as awaiting an answer to "which workspace?". The turn
#: is persisted under this key so the consent survives the process that asked
#: for it, and is consumed exactly once.
WORKSPACE_CONSENT_KEY = "workspace_consent_required"

#: Set by the agent loop when the model invoked ``request_workspace``. The
#: agent knows what the user meant from the whole turn; the text-level gate can
#: only guess from the wording, and a typo or an unusual phrasing defeats it.
#: This is the reliable route, and the keyword route stays as the cheap one.
WORKSPACE_REQUESTED_KEY = "workspace_requested"


if TYPE_CHECKING:
    from Sprout.config.settings import Settings
    from Sprout.llm.registry import ModelRegistry
    from Sprout.memory.outbox import OutboxWorker
    from Sprout.skills.registry import SkillRegistry
    from Sprout.storage.bundle import StorageBundle
    from Sprout.tools.registry import ToolRegistry
    from Sprout.tools.spec import ToolSpec


class Runtime:
    """Coordinates one online turn without knowing transports, vendors, or databases."""

    def __init__(
        self,
        *,
        storage: StorageBundle,
        models: ModelRegistry,
        tools: ToolRegistry,
        skills: SkillRegistry,
        events: EventBus | None = None,
        middleware: MiddlewareChain | None = None,
        policy_engine: PolicyEngine | LayeredPolicyEngine | None = None,
        trajectory_dir: Path = _DEFAULT_TRAJECTORY_DIR,
        default_agent: str = "assistant",
        security: SecurityLayer | None = None,
        skills_dir: str | None = None,
        orchestrator_backend: TemporalOrchestratorBackend | None = None,
        task_queue: TaskQueueBackend | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.storage = storage
        self._project_analysis_tools = WeakKeyDictionary()
        # Host admissions live only for one local dispatch, never in model metadata.
        self._online_analysis_requests = {}
        # Temporary preparation Tasks are data, not portable read grants.
        self._preparation_tasks = {}
        self._project_analysis_selections = WeakKeyDictionary()
        self._project_tool_paths = WeakKeyDictionary()
        self._project_manifest_tools = WeakKeyDictionary()
        # Live issuance receipts; side records require completed-owner checks
        # and fresh resource capture before they may enter this local registry.
        self._node_products = {}
        self._node_publications = {}
        self._original_user_turns = {}
        self._ordinary_assistant_turns = {}
        self._settings = settings
        self.models = models
        self.tools = tools
        self.skills = skills
        self.events = register_builtin_events(events or EventBus())
        self.middleware = middleware or MiddlewareChain(middlewares=(LoggingMiddleware(),))
        self.agents: Registry[Agent] = Registry()
        self._default_agent = default_agent
        self._session_store: SessionStore = storage.session_store()
        self._sessions = SessionManager(self._session_store)
        self._scanner = WorkspaceScanner()
        self._orchestrator_backend = orchestrator_backend
        self._workspaces = WorkspaceService(
            storage,
            self._scanner,
            skills_dir=skills_dir,
        )
        # Expose the read-only workspace query surface to agents. The import is
        # lazy because the tools package pulls the security/system tool surface.
        from Sprout.tools.project_analyze_tool import ProjectAnalyzeTool
        from Sprout.tools.workspace_query_tool import WorkspaceQueryTool

        self.tools.register(WorkspaceQueryTool())
        self.tools.register(ProjectAnalyzeTool(analyze=self._workspaces.analyze_path))
        self._contexts = ContextBuilder(
            storage,
            isolate_project_context=True,
            conversation_provider=self._ordinary_conversation_context,
            skill_disclosure=(settings.skills.disclosure if settings is not None else ""),
        )
        self._sandbox_lifecycle = SandboxLifecycleService(storage.metadata)
        self._policy_engine = policy_engine or PolicyEngine()
        self.security = security
        self._approvals = (
            security.approvals
            if security is not None and security.approvals is not None
            else ApprovalManager(storage.operational, events=self.events)
        )
        self._read_broker = ReadBroker(
            self._policy_engine,
            redactor=Redactor(provider=self._secrets_provider()),
            classify_overrides=security.classify_overrides if security is not None else {},
        )
        self._process_broker = ProcessBroker(
            self._policy_engine,
            commands=security.commands if security is not None else None,
            secrets=security.secrets if security is not None else None,
            events=self.events,
            extra_env_names=security.extra_env_names if security is not None else (),
            # Without this the broker refused every REQUIRE_APPROVAL verdict
            # outright, so verification commands never ran and nobody was asked
            # to grant them.
            approvals=self._approvals,
        )
        self._file_broker = FileBroker(
            self._policy_engine,
            approvals=self._approvals,
            classify_overrides=security.classify_overrides if security is not None else {},
        )
        self._apply_broker = ApplyBroker(
            self._policy_engine,
            approvals=self._approvals,
        )
        self._network_broker = NetworkBroker(
            self._policy_engine,
            approvals=self._approvals,
            guard=security.guard if security is not None else None,
            secrets=security.secrets if security is not None else None,
        )
        self._database_broker = DatabaseBroker(self._policy_engine)
        self._git_broker = GitBroker(
            self._policy_engine,
            approvals=self._approvals,
        )
        from Sprout.tools.database_tool import DatabaseQueryTool
        from Sprout.tools.system_tools import (
            CliDownloadTool,
            CliRunTool,
            GitInspectTool,
            GitWriteTool,
        )

        self.tools.register(DatabaseQueryTool(
            self._database_broker, invoke_query=self._invoke_database_query))
        self.tools.register(
            CliRunTool(
                secrets=security.secrets if security is not None else None,
                process_broker=self._process_broker,
            )
        )
        self.tools.register(
            CliDownloadTool(
                guard=security.guard if security is not None else None,
                network_broker=self._network_broker,
            )
        )
        self.tools.register(GitInspectTool(git_broker=self._git_broker))
        self.tools.register(GitWriteTool(git_broker=self._git_broker))
        self.tools.register(
            SandboxReadTool(
                SandboxRef(
                    id="cwd",
                    kind="local_directory",
                    root=Path.cwd(),
                ),
                self._file_broker,
            )
        )
        self._skills_dir = Path(skills_dir) if skills_dir else None
        self._skill_resolver: SkillResolver | None = None
        self._register_skill_tools()
        self._trajectory_dir = trajectory_dir
        self._workspace_locks = WorkspaceLockManager()
        # Production assembly supplies the Temporal queue. Injection keeps
        # isolated unit tests from requiring a running Temporal server.
        self._task_queue = task_queue or create_task_queue()
        if isinstance(self._task_queue, TemporalTaskQueue):
            if (
                not isinstance(self._orchestrator_backend, TemporalOrchestratorBackend)
                or storage.metadata is None
            ):
                raise ValueError("Temporal queue requires a durable Temporal orchestrator")
            self._task_queue.bind(self.execute)
        self._worker_task: asyncio.Task[None] | None = None
        self._outbox_worker: OutboxWorker | None = None
        self._background_tasks: set[asyncio.Task[None]] = set()
        self._graph_builder = ExecutionGraphBuilder()
        self._router: AgentRouter | None = None
        self._started = False
        self._worker_stop_requested = False
        #: Compatibility-only observation; never authorizes or prevents a claim.
        #: Durable authority resides in WorkspaceConsentStore, not this set.
        self._consumed_consents: set[str] = set()

    def _register_skill_tools(self) -> None:
        """Expose skill_view / skill_search / skill_install to the agent.

        The L0 index injected into the prompt tells the model to call
        ``skill_view`` for a skill's body, so that tool has to exist or the index
        is a dead end. ``skill_view`` gets a *provider* rather than a snapshot so
        a skill installed during the session becomes viewable immediately.

        Search and install need a resolver, which needs the skill store and the
        skill index snapshot. Both are optional in a bare runtime, so the tools
        are registered whenever the directory is known and degrade to
        "not configured" when the store is absent.
        """
        from Sprout.skills.index import SkillIndex
        from Sprout.skills.layout import index_path
        from Sprout.skills.tools import SkillInstallTool, SkillSearchTool, SkillViewTool

        self.tools.register(SkillViewTool(self.skills.list))
        if self._skills_dir is None:
            return

        index = SkillIndex(index_path(self._skills_dir))
        resolver = SkillResolver(
            index=index,
            sources=self._skill_sources(),
            broker=self._skill_broker(),
            skills_dir=self._skills_dir,
            # Without this, a skill installed mid-session would not be loadable
            # until restart: the registry is a snapshot taken at assembly time.
            on_install=lambda: self.skills.reload(self._skills_dir, index=index),
        )
        self._skill_resolver = resolver
        self.tools.register(SkillSearchTool(resolver))
        self.tools.register(SkillInstallTool(resolver))

    def _skill_sources(self) -> list[Any]:
        """The sources a remote skill search may consult (design §7.3).

        Only ``catalog`` (the discovered-candidate cache) is wired: it reads
        ``.hub/remote.json`` and never touches the network on search, so an
        ordinary turn cannot be turned into a crawl. Refreshing that cache is an
        explicit ``sprout skills crawl``, or ``skill_search`` with ``refresh``.
        """
        if self._skills_dir is None:
            return []
        from Sprout.skills.sources.crawl import build_crawl_source

        settings = self._settings.skills if self._settings is not None else None
        sites = list(settings.catalog_sites) if settings is not None else []
        return [
            build_crawl_source(
                self._skills_dir,
                sites,
                max_results=settings.crawl_max_results if settings else 5,
                timeout=settings.crawl_timeout if settings else 15.0,
                seeds=(
                    tuple(settings.marketplace_seeds)
                    if settings and settings.marketplace_seeds
                    else None
                ),
                awesome=(
                    tuple(settings.marketplace_awesome)
                    if settings and settings.marketplace_awesome
                    else None
                ),
                topics=(
                    tuple(settings.marketplace_topics)
                    if settings and settings.marketplace_topics
                    else None
                ),
            )
        ]

    def _skill_broker(self) -> Any:
        """The install broker, so skill_install obeys the policy engine (§8.4)."""
        if self._skills_dir is None or self.storage.skills is None:
            return None
        from Sprout.skills.broker import SkillInstallBroker

        return SkillInstallBroker(
            self._policy_engine,
            approvals=self._approvals,
            skills=self.storage.skills,
        )

    def _secrets_provider(self) -> SecretProvider:
        if self.security is not None:
            return self.security.secrets.provider
        return EnvSecretProvider()

    @property
    def approvals(self) -> ApprovalManager:
        """The single approval manager for this runtime (AUTHZ §5.1)."""
        return self._approvals

    @property
    def audit(self) -> SecurityAuditLog | None:
        return self.security.audit if self.security is not None else None

    # -- collaborators -----------------------------------------------------
    def _metadata_or_raise(self) -> MetadataStore:
        metadata = self.storage.metadata
        if metadata is None:
            raise RuntimeError("MetadataStore is not configured")
        return metadata

    def _changes(self, *, local_acceptance: bool | None = None) -> ChangeProposalService:
        """The change proposal state machine; built per call, it holds no state."""
        return ChangeProposalService(
            self._metadata_or_raise(),
            self.storage.operational,
            self._apply_broker,
            events=self.events,
            approvals=self._approvals,
            local_acceptance=(not isinstance(
                self._orchestrator_backend, TemporalOrchestratorBackend)
                and not getattr(self, "_isolated_coding_required", False)
                if local_acceptance is None else local_acceptance),
        )

    # Node budgets come from configuration, not from the compiler's defaults:
    # ``graph_builder.build`` falls back to ``verify_loops=1`` when the caller
    # does not pass these, which silently disables the configured retry loop.
    def _verify_loops(self) -> int:
        if self._settings is not None:
            return max(1, self._settings.runtime.verify_loops)
        return 2

    def _agent_max_attempts(self) -> int:
        if self._settings is not None:
            return max(1, self._settings.runtime.agent_max_attempts)
        return 2

    def _agent_timeout_seconds(self) -> float:
        if self._settings is not None:
            return max(1.0, self._settings.runtime.agent_timeout_seconds)
        return 300.0

    def _evaluation_timeout_seconds(self) -> float:
        if self._settings is not None:
            return max(1.0, self._settings.runtime.evaluation_timeout_seconds)
        return 600.0

    def _evaluation_attempts(self) -> int:
        if self._settings is not None:
            return max(1, self._settings.runtime.evaluation_attempts)
        return 2

    def _executor(self, metadata: MetadataStore) -> NodeExecutor:
        """The node runner for one task; owns brokers, prompts, and clamping."""
        return NodeExecutor(
            metadata=metadata,
            router=self._router,
            contexts=self._contexts,
            tools=self.tools,
            skills=self.skills,
            read_broker=self._read_broker,
            file_broker=self._file_broker,
            process_broker=self._process_broker,
            apply_broker=self._apply_broker,
            changes=self._changes(),
            coding_dispatch=getattr(self, "_isolated_coding_dispatch", None),
            isolated_coding_required=getattr(self, "_isolated_coding_required", False),
            coding_session_factory=getattr(self, "_isolated_coding_session_factory", None),
            coding_commands=getattr(self, "_isolated_coding_commands", None),
            local_acceptance=not isinstance(
                self._orchestrator_backend, TemporalOrchestratorBackend),
            project_analysis_scope=self._project_analysis_scope,
        )

    @contextmanager
    def _project_analysis_scope(self, task, workspace, node):
        """Only wraps host node execution; never expands its visible tool set."""
        from Sprout.tools.context_keys import _project_node_scope

        with _project_node_scope(self, task, workspace, node) as scope:
            try:
                yield scope
            finally:
                self._node_publications.pop(node.id, None)
                for tool, issued in tuple(self._project_analysis_tools.items()):
                    if issued is scope:
                        del self._project_analysis_tools[tool]
                        self._project_tool_paths.pop(tool, None)
                        self._project_manifest_tools.pop(tool, None)
                for selection, record in tuple(self._project_analysis_selections.items()):
                    if record[0].node_scope is scope:
                        del self._project_analysis_selections[selection]

    async def _project_analysis_subject(self, scope):
        from Sprout.tools.context_keys import _current_project_node

        if scope is not _current_project_node() or scope.runtime is not self:
            raise PermissionError("Project analysis execution domain unavailable")
        metadata = self._metadata_or_raise()
        task = await metadata.get_task(scope.task.id)
        if task is not None and task.metadata.get("plan_kind") == "r04_preparation_v1":
            issued = self._preparation_tasks.get(task.id)
            if (issued is None or task.actor != issued[0].actor
                    or task.delegation_scope != issued[0].delegation_scope):
                raise PermissionError("preparation_instance_unavailable")
            if issued[1] is not None:
                await self._check_preparation_parent(issued[1], scope.workspace, issued[2])
        if (task is None or task.id != scope.task.id or task.status is not TaskStatus.RUNNING
                or task.workspace_id != scope.workspace.id
                or task.actor != scope.task.actor or task.source != scope.task.source
                or task.actor.authenticated is not True
                or not isinstance(task.actor.user_id, str) or not task.actor.user_id
                or not isinstance(task.actor.source, str) or not task.actor.source
                or task.source != task.actor.source):
            raise PermissionError("Project analysis task identity unavailable")
        workspace = await metadata.get_workspace(task.workspace_id)
        if (workspace is None or workspace.id != task.workspace_id
                or workspace.root != scope.workspace.root):
            raise PermissionError("Project analysis workspace identity changed")
        node = await metadata.get_execution_node(scope.node.id)
        guard = node.metadata.get("execution_guard", {}) if node is not None else {}
        if (node is None or node.id != scope.node.id or node.task_id != task.id
                or node.type != scope.node.type or node.status is not NodeStatus.RUNNING
                or not isinstance(scope.guard_owner, str) or not scope.guard_owner
                or guard.get("owner") != scope.guard_owner or guard.get("state") != "active"):
            raise PermissionError("Project analysis node ownership unavailable")
        if (not isinstance(scope.session_id, str)
                or task.metadata.get("session_id", "") != scope.session_id):
            raise PermissionError("Project analysis session association changed")
        if scope.session_id:
            session = await self._sessions.require_owner(scope.session_id, task.actor)
            principal = self._sessions.frozen_principal(session)
            if (principal.authenticated is not True or principal.user_id != task.actor.user_id
                    or principal.source != task.actor.source):
                raise PermissionError("Project analysis session identity unavailable")
        return task, workspace

    async def _issue_project_analysis_tool(self, *, paths=None, discover=False):
        from Sprout.tools.context_keys import _current_project_node
        from Sprout.tools.project_analyze_tool import ProjectAnalyzeTool

        scope = _current_project_node()
        if scope is None:
            raise PermissionError("Project analysis requires a live node execution domain")
        await self._project_analysis_subject(scope)
        tool = ProjectAnalyzeTool()
        self._project_analysis_tools[tool] = scope
        if discover:
            # Host choice only, never derived from model arguments or metadata.
            self._project_tool_paths[tool] = None
        if paths is not None:
            self._set_project_tool_paths(tool, paths)
            admission = self._online_analysis_requests.get(scope.node.id)
            if (admission is not None and len(paths) > 1
                    and paths == admission["paths"]):
                # Private, instance-bound operation: the entry file is NOT an
                # authorization substitute for the rest of this fixed manifest.
                self._project_manifest_tools[tool] = (scope, paths)
                tool.spec = replace(tool.spec, description=tool.spec.description +
                    " This instance analyzes the entire fixed host-selected manifest; "
                    "path must identify its entry file and cannot change the manifest.")
        return tool

    def _set_project_tool_paths(self, tool, paths):
        from Sprout.workspace.authorized_content import ContentLimits, relative_label

        if (type(paths) is not tuple or not paths or len(paths) > ContentLimits().max_files
                or len(set(paths)) != len(paths)):
            raise PermissionError("project_capture_selection_invalid")
        for path in paths:
            relative_label(path)
        self._project_tool_paths[tool] = paths

    async def _invoke_database_query(self, tool, database, sql, workspace_id):
        """Use the existing live node authority, never JSON attribution."""
        from Sprout.security.access import AccessDecision, ActionRequest, ActionType
        from Sprout.task.paths import bound_scope_path, scope_path
        from Sprout.tools.context_keys import _current_project_node
        from Sprout.workspace.models import ResourceKind, ResourceRef

        scope = _current_project_node()
        if (scope is None or scope.runtime is not self
                or self.tools.get("database_query") is not tool):
            raise PermissionError("database_task_binding_unavailable")
        task, workspace = await self._project_analysis_subject(scope)
        if workspace_id not in {None, "", workspace.id}:
            raise PermissionError("database_workspace_mismatch")
        path = bound_scope_path(database, str(workspace.root))
        target, root = scope_path(path), scope_path(str(workspace.root))
        effective = task.delegation_scope.bind_paths(str(workspace.root)).narrow(
            scope.task.delegation_scope.bind_paths(str(workspace.root)))
        if (target is None or root is None or not root.contains(target)
                or not effective.permits("db.read", target.text())
                or not effective.permits("tool.invoke", target.text())):
            raise PermissionError("database_scope_denied")
        request = ActionRequest(task_id=task.id, actor=task.actor,
            action=ActionType.TOOL_INVOKE, scope=effective,
            resource=ResourceRef(workspace.id, target.text(), ResourceKind.DATA),
            arguments={"tool": tool.spec.name, "sql": sql})
        decision = self._policy_engine.decide(request)
        await self._record_project_capture_policy(request, decision)
        if decision.decision is not AccessDecision.ALLOW:
            raise PermissionError("database_tool_invocation_denied")
        scope.untracked_tool_use = True
        result = await self._database_broker.query(
            workspace, target.text(), sql, task_id=task.id, scope=effective, actor=task.actor)
        current, current_workspace = await self._project_analysis_subject(scope)
        if current.delegation_scope != task.delegation_scope or current_workspace != workspace:
            raise PermissionError("database_authority_changed")
        return result

    async def _issue_project_query_tool(self, *, paths=None, discover=False):
        from Sprout.tools.context_keys import _current_project_node
        from Sprout.tools.workspace_query_tool import WorkspaceQueryTool

        scope = _current_project_node()
        if scope is None:
            raise PermissionError("project_capture_frame_unavailable")
        await self._project_analysis_subject(scope)
        tool = WorkspaceQueryTool()
        if discover and paths is None:
            self._project_tool_paths[tool] = None
        else:
            self._set_project_tool_paths(tool, paths)
        self._project_analysis_tools[tool] = scope
        return tool

    def _project_tool_ready(self, tool, arguments, frame):
        from Sprout.task.paths import bound_scope_path, scope_path
        from Sprout.tools.context_keys import _current_project_node
        from Sprout.tools.workspace_query_tool import WorkspaceQueryTool

        manifest = self._project_manifest_tools.get(tool)
        if manifest is not None:
            scope, paths = manifest
            target = bound_scope_path(arguments.get("path"), str(scope.workspace.root))
            if (frame is None or frame.node_scope is not scope
                    or self._project_tool_paths.get(tool) != paths
                    or scope_path(target) != scope_path(str(scope.workspace.root / paths[0]))):
                return False
        return (frame is not None and frame.node_scope is _current_project_node()
                and frame.node_scope is not None
                and self._project_analysis_tools.get(tool) is frame.node_scope
                and (bool(self._project_tool_paths.get(tool))
                     or (tool in self._project_tool_paths
                         and self._project_tool_paths[tool] is None))
                and (not isinstance(tool, WorkspaceQueryTool)
                     or arguments.get("workspace_id") == frame.node_scope.workspace.id))

    async def _invoke_project_tool(self, tool, arguments):
        from copy import deepcopy

        from Sprout.security.access import AccessDecision, ActionType
        from Sprout.task.paths import scope_path
        from Sprout.tools.context_keys import _current_project_analysis_call
        from Sprout.tools.project_analyze_tool import ProjectAnalyzeTool
        from Sprout.tools.result import ToolResult
        from Sprout.workspace.authorized_content import VISIBLE_KINDS
        from Sprout.workspace.models import ResourceRef

        arguments = deepcopy(dict(arguments))
        frame = _current_project_analysis_call()
        if not self._project_tool_ready(tool, arguments, frame):
            raise PermissionError("project_analysis_scope_unavailable")
        subject = await self._project_analysis_request(tool, arguments, frame, "")
        _, workspace = await self._project_analysis_subject(frame.node_scope)
        await self._check_project_selection((frame, subject, workspace))
        paths = self._project_tool_paths[tool]
        discovered = paths is None
        identities = None
        if discovered:
            sandbox = SandboxRef(frame.node_scope.workspace.id, "restricted_analysis",
                                 frame.node_scope.workspace.root)
            directory = Path(subject.resource.path)
            listing = await self._file_broker.enumerate_readable(
                sandbox, directory.relative_to(sandbox.root).as_posix(), subject=subject,
            )
            if not listing.ok:
                admission = self._online_analysis_requests.get(frame.node_scope.node.id)
                if admission is not None:
                    admission["unavailable_reason"] = "project_auto_discovery_unavailable"
                raise PermissionError("project_auto_discovery_unavailable")
            identities = {}
            for entry in listing.entries:
                if entry.is_directory:
                    continue
                path = sandbox.root / entry.relative_path
                kind = self._file_broker._classify(path, sandbox)
                if kind not in VISIBLE_KINDS:
                    continue
                current = await self._project_analysis_request(
                    tool, {"path": str(path)}, frame, frame.node_scope.session_id,
                )
                request = replace(current, action=ActionType.FILE_READ,
                                  resource=ResourceRef(sandbox.id, path.as_posix(), kind))
                decision = self._policy_engine.decide(request)
                await self._record_project_capture_policy(request, decision)
                if decision.decision is AccessDecision.ALLOW:
                    identities[entry.relative_path] = entry.identity
            paths = tuple(identities)
        if isinstance(tool, ProjectAnalyzeTool):
            manifest = self._project_manifest_tools.get(tool)
            if manifest is not None:
                # All members must independently pass the existing ToolInvoke
                # and FILE_READ contracts before opening ANY manifest member.
                record = (frame, subject, workspace)
                sandbox = SandboxRef(workspace.id, "restricted_analysis", workspace.root)
                for path in paths:
                    absolute = str(workspace.root / path)
                    await self._check_project_selection(record, resource_path=absolute)
                    current = await self._project_analysis_request(
                        tool, {"path": absolute}, frame, frame.node_scope.session_id,
                    )
                    request = replace(current, action=ActionType.FILE_READ, resource=ResourceRef(
                        workspace.id, current.resource.path,
                        kind=self._file_broker._classify(workspace.root / path, sandbox),
                    ))
                    decision = self._policy_engine.decide(request)
                    await self._record_project_capture_policy(request, decision)
                    if (decision.decision is not AccessDecision.ALLOW
                            or not request.scope.permits(ActionType.FILE_READ.value,
                                                         request.resource.path)):
                        raise PermissionError("project_capture_read_denied")
            else:
                target = scope_path(subject.resource.path)
                paths = tuple(path for path in paths if target.contains(
                    scope_path(str(frame.node_scope.workspace.root / path))))
        if not paths:
            raise PermissionError("project_capture_selection_unavailable")
        selection = await self._issue_project_analysis_selection(paths)
        if not isinstance(tool, ProjectAnalyzeTool):
            return await self._query_project_selection(selection, arguments)

        def project(analysis):
            admission = self._online_analysis_requests.get(frame.node_scope.node.id)
            if admission is not None:
                # Provisional data only; the host returns it only after capture's
                # publication checks and the real Guard settlement succeed.
                admission["analysis_data"] = analysis
            payload = {
                "workspace_id": analysis.workspace.id,
                "root": analysis.workspace.root.as_posix(),
                "languages": list(analysis.manifest.detected_languages),
                "framework_hints": list(analysis.manifest.framework_hints),
                "entry_points": list(analysis.manifest.entry_points),
                "test_commands": list(analysis.manifest.test_commands),
                "build_commands": list(analysis.manifest.build_commands),
                "read_plan_resources": len(analysis.read_plan.resources),
                "graph": {"nodes": len(analysis.graph.nodes), "edges": len(analysis.graph.edges),
                          "node_kinds": sorted({node.kind for node in analysis.graph.nodes}),
                          "relations": sorted({edge.relation for edge in analysis.graph.edges})},
                "knowledge": [
                    {"kind": item.kind, "statement": item.statement,
                     "confidence": item.confidence, "evidence_ids": list(item.evidence_ids),
                     "details": dict(item.details)} for item in analysis.knowledge.items
                ],
                "coverage": {"mode": "discovery" if discovered else "files",
                             "file_count": len(paths),
                             "complete_project": False,
                             "visibility": "authorized_only"},
            }
            return ToolResult.success(analysis.context_summary(), data=payload)

        admission = self._online_analysis_requests.get(frame.node_scope.node.id)
        projector = admission.get("content_project") if admission is not None else None

        def project_contents(analysis, view):
            result = project(analysis)
            if projector is not None:
                admission["projected_data"] = projector(analysis, view)
            return result

        result = await self._capture_project_selection(
            selection, content_project=project_contents, expected_identities=identities,
        )
        admission = self._online_analysis_requests.get(frame.node_scope.node.id)
        if admission is not None and result.ok:
            admission["analysis_result"] = result
        return result

    async def _project_analysis_request(self, tool, arguments, frame, session_id):
        from Sprout.security.access import ActionRequest, ActionType
        from Sprout.task.paths import bound_scope_path, scope_path
        from Sprout.tools.context_keys import _current_project_analysis_call, _current_project_node
        from Sprout.workspace.models import ResourceKind, ResourceRef

        scope = _current_project_node()
        if (frame is not _current_project_analysis_call() or frame.tool is not tool
                or scope is None or frame.node_scope is not scope
                or self._project_analysis_tools.get(tool) is not scope):
            raise PermissionError("Project analysis instance/call binding unavailable")
        task, workspace = await self._project_analysis_subject(scope)
        if session_id not in {"", scope.session_id, f"node-{scope.node.id}"}:
            raise PermissionError("Project analysis caller session mismatch")
        root = scope_path(str(workspace.root))
        from Sprout.tools.workspace_query_tool import WorkspaceQueryTool

        if isinstance(tool, WorkspaceQueryTool):
            if arguments.get("workspace_id", workspace.id) != workspace.id:
                raise PermissionError("workspace_query_workspace_mismatch")
            paths = self._project_tool_paths.get(tool)
            admission = self._online_analysis_requests.get(scope.node.id)
            directory = admission["target"] if admission is not None else None
            raw = (str(workspace.root / paths[0]) if paths else directory) if (
                "workspace_id" in arguments) else arguments.get("path")
        else:
            raw = arguments.get("path")
        path = bound_scope_path(raw, str(workspace.root)) if isinstance(raw, str) else None
        target = scope_path(path) if path is not None else None
        if (root is None or not root.anchor or root.anchor.startswith("//")
                or target is None or not root.contains(target)):
            raise PermissionError("Project analysis requested resource unavailable")
        effective = task.delegation_scope.bind_paths(str(workspace.root)).narrow(
            scope.task.delegation_scope.bind_paths(str(workspace.root)),
        )
        preparation = self._preparation_tasks.get(task.id)
        # The read service owns a real separate node, but original CODE effects
        # remain charged to the authoritative parent for task-specific Policy.
        # Only a live Runtime registration, not child metadata, selects it.
        resource_task = preparation[1] if preparation and preparation[1] is not None else task
        return ActionRequest(
            task_id=resource_task.id, actor=task.actor, action=ActionType.TOOL_INVOKE,
            resource=ResourceRef(workspace_id=workspace.id, path=target.text(),
                                 kind=ResourceKind.OTHER),
            scope=effective,
        )

    async def _check_project_selection(self, record, *, record_policy=True, resource_path=None):
        """Recheck B1 authority and existing policies; never ask for approvals."""
        from Sprout.security.access import AccessDecision, PolicyDecision
        from Sprout.security.policy import SecurityPolicy

        frame, initial, workspace = record
        subject = await self._project_analysis_request(
            frame.tool, {"path": resource_path or initial.resource.path}, frame,
            frame.node_scope.session_id,
        )
        task, current = await self._project_analysis_subject(frame.node_scope)
        if (subject.actor != initial.actor or subject.task_id != initial.task_id
                or subject.scope != initial.scope or current.id != workspace.id
                or current.root != workspace.root or current.revision != workspace.revision):
            raise PermissionError("project_capture_authority_changed")
        policy = self.security.tool_policy if self.security is not None else SecurityPolicy()
        risk = policy.decide_for_spec(frame.tool.spec)
        request = replace(
            subject, arguments={"tool_name": frame.tool.spec.name, "path": subject.resource.path},
        )
        boundary = self._policy_engine.decide(request)
        if (subject.scope.is_expired()
                or not subject.scope.permits(subject.action.value, subject.resource.path)):
            boundary = PolicyDecision(AccessDecision.DENY, "Project capture Scope denied",
                                      matched_rules=("scope:expired" if subject.scope.is_expired()
                                                     else "scope:denied",))
        decision = replace(
            boundary, decision=LayeredPolicyEngine._intersect(boundary.decision, risk.decision),
            matched_rules=(*boundary.matched_rules, *risk.matched_rules),
            reason="; ".join(filter(None, (boundary.reason, risk.reason))),
        )
        if record_policy:
            await self._record_project_capture_policy(request, decision)
        if decision.decision is not AccessDecision.ALLOW:
            raise PermissionError("project_capture_policy_denied")
        return task, current

    async def _record_project_capture_policy(self, request, decision):
        from Sprout.execution.policy_emit import emit_policy_decision

        if self.audit is not None:
            self.audit.record_decision(request, decision)
        await emit_policy_decision(self.events, request, decision)

    async def _issue_project_analysis_selection(self, paths, *, limits=None):
        """Trusted host planning only; never called with model tool arguments.

        This fixed selector does NOT grant directory visibility or FILE_READ.
        Every selected known path still goes through the existing FileBroker.
        The returned process-local instance is registered to the live B1 frame.
        """
        from Sprout.tools.context_keys import _current_project_analysis_call
        from Sprout.workspace.authorized_content import (
            ContentLimits,
            _CaptureSelection,
            relative_label,
        )

        frame = _current_project_analysis_call()
        if frame is None or frame.node_scope is None:
            raise PermissionError("project_capture_frame_unavailable")
        limits = ContentLimits() if limits is None else limits
        if (type(paths) is not tuple or not paths or type(limits) is not ContentLimits
                or len(paths) > limits.max_files):
            raise PermissionError("project_capture_selection_invalid")
        for path in paths:
            relative_label(path)
            if len(path.split("/")) > limits.max_depth:
                raise PermissionError("project_capture_depth_exceeded")
        if len({path.casefold() for path in paths}) != len(paths):
            raise PermissionError("project_capture_duplicate_path")
        root = frame.node_scope.workspace.root
        initial = await self._project_analysis_request(
            frame.tool, {"path": str(root / paths[0])}, frame, frame.node_scope.session_id,
        )
        _, workspace = await self._project_analysis_subject(frame.node_scope)
        record = (frame, initial, workspace)
        await self._check_project_selection(record)
        selection = _CaptureSelection(paths, limits)
        self._project_analysis_selections[selection] = record
        return selection

    async def _analyze_project_selection(self, selection):
        """Internal restricted pipeline; not a model-visible tool or A bypass."""
        return await self._capture_project_selection(selection)

    async def _query_project_selection(self, selection, arguments):
        """Fresh per-call query, no snapshot/cache lookup or portable grant."""
        from copy import deepcopy

        from Sprout.tools.workspace_query_tool import WorkspaceQueryTool
        from Sprout.workspace.authorized_content import _CaptureSelection

        arguments = deepcopy(dict(arguments))
        if type(selection) is not _CaptureSelection:
            raise PermissionError("project_capture_selection_unregistered")
        record = self._project_analysis_selections.get(selection)
        if record is None or arguments.get("workspace_id") != record[2].id:
            raise PermissionError("workspace_query_workspace_mismatch")
        return await self._capture_project_selection(
            selection, project=lambda analysis: WorkspaceQueryTool._project(analysis, arguments),
            consumer_spec=WorkspaceQueryTool().spec,
        )

    async def _build_project_context(self, selection, message, session):
        """Trusted host integration, not a model-visible or JSON-bound entry."""
        from copy import deepcopy

        from Sprout.tools.context_keys import _current_project_analysis_call
        from Sprout.workspace.authorized_content import _CaptureSelection

        if type(selection) is not _CaptureSelection:
            raise PermissionError("project_capture_selection_unregistered")
        message = replace(message, metadata=deepcopy(dict(message.metadata)))
        record = self._project_analysis_selections.get(selection)
        if record is None or record[0] is not _current_project_analysis_call():
            raise PermissionError("project_capture_selection_unregistered")
        task, _ = await self._check_project_selection(record)
        scope = record[0].node_scope
        if (not scope.session_id or session.id != scope.session_id
                or session.user_id != task.actor.user_id or message.user_id != task.actor.user_id
                or message.session_id not in {None, session.id}):
            raise PermissionError("project_context_subject_mismatch")
        # The passed Session is not identity authority; use the fresh server row.
        session = await self._sessions.require_owner(scope.session_id, task.actor)
        return await self._capture_project_selection(
            selection, project=lambda analysis: self._contexts._isolated_context(
                message, session, {}, {}, analysis=analysis),
        )

    def _project_consumer_request(self, subject, spec):
        """A data consumer may tighten, never replace, capture authorization."""
        from Sprout.security.policy import SecurityPolicy

        policy = self.security.tool_policy if self.security is not None else SecurityPolicy()
        request = replace(subject, arguments={"tool_name": spec.name,
                                              "path": subject.resource.path})
        decision = self._policy_engine.decide(request)
        risk = policy.decide_for_spec(spec)
        decision = replace(
            decision, decision=LayeredPolicyEngine._intersect(decision.decision, risk.decision),
            reason="; ".join(filter(None, (decision.reason, risk.reason))),
            matched_rules=(*decision.matched_rules, *risk.matched_rules),
        )
        return request, decision

    async def _capture_project_selection(
        self, selection, *, project=None, consumer_spec=None, content_project=None,
        expected_identities=None,
    ):
        """Capture and project before final checks; never cache a projection."""
        import time

        from Sprout.security.access import AccessDecision, ActionType
        from Sprout.tools.context_keys import _current_project_analysis_call
        from Sprout.workspace.authorized_content import (
            AuthorizedContentView,
            AuthorizedEntry,
            _CaptureSelection,
        )
        from Sprout.workspace.models import ResourceRef

        if type(selection) is not _CaptureSelection:
            raise PermissionError("project_capture_selection_unregistered")
        record = self._project_analysis_selections.get(selection)
        if record is None or record[0] is not _current_project_analysis_call():
            raise PermissionError("project_capture_selection_unregistered")
        del self._project_analysis_selections[selection]  # Single capture, no implicit retry.
        task, workspace = await self._check_project_selection(record)
        if consumer_spec is not None:
            request, decision = self._project_consumer_request(record[1], consumer_spec)
            await self._record_project_capture_policy(request, decision)
            if decision.decision is not AccessDecision.ALLOW:
                raise PermissionError("project_consumer_policy_denied")
        sandbox = SandboxRef(workspace.id, "restricted_analysis", workspace.root)
        entries, total, view = [], 0, None
        deadline = time.monotonic() + selection.limits.max_seconds
        try:
            for path in selection.paths:
                if time.monotonic() > deadline:
                    raise PermissionError("project_capture_expired")
                await self._check_project_selection(
                    record, resource_path=str(workspace.root / path),
                )
                subject = await self._project_analysis_request(
                    record[0].tool, {"path": str(workspace.root / path)}, record[0],
                    record[0].node_scope.session_id,
                )
                if consumer_spec is not None:
                    request, decision = self._project_consumer_request(subject, consumer_spec)
                    await self._record_project_capture_policy(request, decision)
                    if decision.decision is not AccessDecision.ALLOW:
                        raise PermissionError("project_consumer_policy_denied")
                remaining = selection.limits.max_total_bytes - total
                if remaining <= 0:
                    raise PermissionError("project_capture_total_budget")
                kind = self._file_broker._classify(workspace.root / path, sandbox)
                request = replace(subject, action=ActionType.FILE_READ, resource=ResourceRef(
                    workspace.id, subject.resource.path, kind=kind,
                ))
                decision = self._policy_engine.decide(request)
                await self._record_project_capture_policy(request, decision)
                if (request.scope.is_expired()
                        or not request.scope.permits(request.action.value, request.resource.path)
                        or decision.decision is not AccessDecision.ALLOW):
                    raise PermissionError("project_capture_read_denied")
                result = await self._file_broker.read_snapshot(
                    sandbox, path, subject=subject,
                    max_bytes=min(selection.limits.max_file_bytes, remaining),
                    expected_identity=(expected_identities.get(path)
                                       if expected_identities is not None else None),
                )
                if not result.ok:
                    raise PermissionError("project_capture_read_denied")
                entries.append(AuthorizedEntry(path, kind, result.data, result.digest,
                                               result.identity))
                total += len(result.data)
            await self._check_project_selection(record)
            view = AuthorizedContentView(workspace.id, tuple(entries), selection.limits,
                                         revision=workspace.revision)
            # Never use a manifest or cache captured under a broader scope.
            analysis = await self._workspaces.analyze_view(
                replace(workspace, manifest=None), task, view,
            )
            result = (content_project(analysis, view) if content_project is not None
                      else analysis if project is None else project(analysis))
            await self._check_project_selection(record)
            publication_requests = []
            for entry in entries:
                await self._check_project_selection(
                    record, resource_path=str(workspace.root / entry.relative_path),
                )
                subject = await self._project_analysis_request(
                    record[0].tool, {"path": str(workspace.root / entry.relative_path)}, record[0],
                    record[0].node_scope.session_id,
                )
                publication_requests.append(replace(subject, arguments={
                    "tool_name": record[0].tool.spec.name, "path": subject.resource.path,
                }))
                if consumer_spec is not None:
                    request, decision = self._project_consumer_request(subject, consumer_spec)
                    publication_requests.append(request)
                    await self._record_project_capture_policy(request, decision)
                    if decision.decision is not AccessDecision.ALLOW:
                        raise PermissionError("project_consumer_policy_denied")
                request = replace(subject, action=ActionType.FILE_READ, resource=ResourceRef(
                    workspace.id, subject.resource.path, kind=entry.kind,
                ))
                publication_requests.append(request)
                decision = self._policy_engine.decide(request)
                await self._record_project_capture_policy(request, decision)
                if (request.scope.is_expired()
                        or not request.scope.permits(request.action.value, request.resource.path)
                        or decision.decision is not AccessDecision.ALLOW):
                    raise PermissionError("project_capture_publish_denied")
            # Last authority refresh follows async audit/event callbacks. No
            # publish callback may invalidate a checked subject unnoticed here.
            await self._check_project_selection(record, record_policy=False)
            # Recheck all selected paths after the last asynchronous callback;
            # an ALLOW on the anchor must not override another path's DENY.
            for request in publication_requests:
                if (request.scope.is_expired()
                        or not request.scope.permits(request.action.value, request.resource.path)
                        or self._policy_engine.decide(request).decision
                        is not AccessDecision.ALLOW):
                    raise PermissionError("project_capture_publish_denied")
                if consumer_spec is not None:
                    _, decision = self._project_consumer_request(request, consumer_spec)
                    if decision.decision is not AccessDecision.ALLOW:
                        raise PermissionError("project_consumer_policy_denied")
            if time.monotonic() > deadline:
                raise PermissionError("project_capture_expired")
            view.check()
            captured_receipts = tuple(
                {"path": entry.relative_path, "kind": entry.kind.value,
                 "digest": entry.digest,
                 "identity": {name: getattr(entry.identity, name) for name in
                              ("device", "inode", "size", "mtime_ns", "ctime_ns")}}
                for entry in entries
            )
            node_scope = record[0].node_scope
            receipts = {r["path"]: r for r in node_scope.resource_receipts}
            for receipt in captured_receipts:
                if (receipt["path"] in receipts
                        and receipts[receipt["path"]] != receipt):
                    raise PermissionError("node_resource_content_changed")
                receipts[receipt["path"]] = receipt
            node_scope.resource_receipts = tuple(receipts[key] for key in sorted(receipts))
            node_scope.captured_paths = tuple(sorted(
                set(node_scope.captured_paths) | set(receipts),
            ))
            return result
        finally:
            if view is not None:
                view.invalidate()

    async def _read_node_selection(self, paths):
        """Known-path READ using the same checked B2 content, never the old reader."""
        from Sprout.tools.context_keys import _current_project_node, _project_analysis_call

        scope = _current_project_node()
        tool = await self._issue_project_analysis_tool(paths=paths)
        with _project_analysis_call(tool):
            selected = await self._issue_project_analysis_selection(paths)
            result = await self._capture_project_selection(
                selected, content_project=lambda analysis, view: [
                    {"path": entry.relative_path, "decision": "allow",
                     "content": entry.text(), "error": None} for entry in view.entries],
            )
        scope.captured_paths = paths
        scope.read_captured = True
        return result

    def _known_read_only_plan(self, workspace, task, *, scheduling=False):
        """Prepare existing explicit targets without host scanning or cache reuse.

        The empty manifest is only graph preparation for READ_ONLY, never an
        analysis result or a substitute for CODE verification discovery.
        """
        from Sprout.task.paths import bound_scope_path, scope_path
        from Sprout.workspace.authorized_content import ContentLimits, relative_label
        from Sprout.workspace.models import ResourceRef

        raw = task.metadata.get("required_paths")
        if raw is None:
            # A finite, server-issued scope is also an explicit resource selection.
            # Directories are not scanned here; the capture broker still decides
            # whether each selected resource can actually be opened.
            raw = task.delegation_scope.allowed_paths
        if not raw and scheduling:
            # Graph preparation performs no read. The worker independently builds
            # its authoritative read plan when it executes the persistent node.
            manifest = WorkspaceManifest(workspace.id, revision=workspace.revision)
            return replace(workspace, manifest=manifest), ReadPlan(
                task_id=task.id, purpose=task.instruction,
            )
        if (type(raw) not in {tuple, list} or not raw
                or len(raw) > ContentLimits().max_files):
            raise PermissionError("project_auto_discovery_unavailable")
        root = scope_path(str(workspace.root))
        labels = []
        for value in raw:
            target = scope_path(bound_scope_path(value, str(workspace.root)))
            if target is None or not root.contains(target):
                raise PermissionError("project_capture_selection_invalid")
            label = target.text()[len(root.text().rstrip("/")) + 1:]
            relative_label(label)
            labels.append(label)
        manifest = WorkspaceManifest(workspace.id, revision=workspace.revision)
        return replace(workspace, manifest=manifest), ReadPlan(
            task_id=task.id, purpose=task.instruction,
            resources=tuple(ResourceRef(workspace.id, path) for path in labels),
        )

    async def _ordinary_conversation_context(self, message, session, tools, skills):
        """Restore newly recorded original input, not unlabelled derived memory.

        Old assistant/facts/recall/knowledge rows cannot be certified from a role,
        scope string or model metadata. Their admission needs the pending Origin
        contract, so this deliberately reports partial conversation coverage.
        """
        from Sprout.memory.models import ContextMemory

        stored = await self._session_store.get_session(session.id)
        if stored is None:
            return None
        try:
            principal = self._sessions.frozen_principal(stored)
        except PermissionError:
            # Optional history is unavailable, not authority to retrieve old
            # content. ContextBuilder retains its existing isolated chat path.
            return None
        if (principal.authenticated is not True or principal.user_id != message.user_id
                or message.session_id not in {None, stored.id}):
            return None
        stored = await self._sessions.require_owner(stored.id, principal)
        turns = await self._session_store.recent_turns(
            stored.id, limit=self._contexts._settings.working_window,
        )
        working = tuple(turn for turn in turns if (
            (turn.role == "user" and self._original_user_turns.get((stored.id, turn.id))
             == (turn.content, dict(turn.metadata)))
            or (turn.role == "assistant"
                and self._ordinary_assistant_turns.get((stored.id, turn.id))
                == (turn.content, dict(turn.metadata)))))
        base = self._contexts._isolated_context(message, stored, tools, skills)
        return replace(base, memory=ContextMemory(session=base.session, working=working))

    def _ordinary_response_has_source(self, context, agent, result):
        """Only the normal online call may attest a resource-free response.

        No persisted role, model flag or caller-supplied result grants origin.
        Tool-using/project responses still require the existing project chain.
        Receipts remain local; this does not upgrade legacy stored memories.
        """
        from Sprout.agent.base import ModelAgent
        from Sprout.agent.loop import AgentLoop
        from Sprout.memory.models import ContextMemory

        if (context is None or context.workspace is not None or context.files
                or context.knowledge or type(context.memory) is not ContextMemory
                or context.memory.summary or context.memory.facts
                or context.memory.recalled or context.memory.knowledge
                or context.metadata.get("resume_tool_calls")):
            return False
        for turn in context.memory.working:
            records = (self._original_user_turns if turn.role == "user"
                       else self._ordinary_assistant_turns if turn.role == "assistant" else {})
            if records.get((context.session.id, turn.id)) != (turn.content, dict(turn.metadata)):
                return False
        return (type(agent) is ModelAgent or (
            type(agent) is AgentLoop and type(result.metadata.get("tool_calls")) is int
            and result.metadata["tool_calls"] == 0
            and not any(result.metadata.get(key) for key in (
                "approval_required", "workspace_requested", "truncated", "degraded"))))

    def _remember_node_product(self, scope, output):
        from copy import deepcopy

        if scope.untracked_tool_use:
            return  # Tool effects have no transitive resource receipt yet.
        allowed = scope.node.type in {NodeType.PLAN, NodeType.SUBTASK, NodeType.AGENT}
        if not (scope.read_captured or scope.node.type is NodeType.SANDBOX
                or (allowed and self._node_publications.get(scope.node.id) is scope)):
            return
        payload = deepcopy((
            scope.task, replace(scope.workspace, manifest=None), scope.node,
            output, scope.captured_paths,
        ))
        self._node_products[(scope.task.id, scope.node.id)] = (*payload, self)
        from Sprout.execution.node_result_source import NodeResultSourceV1, payload_digest

        parents = []
        parent_ids = set(scope.node.dependencies) | set(scope.parent_receipts)
        for dependency in sorted(parent_ids):
            parent = self._node_products.get((scope.task.id, dependency))
            if parent is None or len(parent) != 7:
                return  # Missing transitive inputs cannot acquire a new origin.
            parents.append({"node_id": dependency,
                            "output_digest": payload_digest(parent[3])})
        if scope.captured_paths and not scope.resource_receipts:
            return
        source = NodeResultSourceV1(
            scope.task.id, scope.node.id, scope.node.type.value, scope.guard_owner,
            self._node_subject_digest(scope), payload_digest(output),
            json.dumps(scope.resource_receipts, sort_keys=True),
            json.dumps(parents, sort_keys=True),
        )
        self._node_products[(scope.task.id, scope.node.id)] = (*payload, self, source)

    @staticmethod
    def _node_subject_digest(scope):
        from dataclasses import asdict

        from Sprout.execution.node_result_source import payload_digest

        return payload_digest({
            "task": scope.task.id, "instruction": scope.task.instruction,
            "actor": asdict(scope.task.actor), "source": scope.task.source,
            "session": scope.session_id, "workspace": scope.workspace.id,
            "root": str(scope.workspace.root), "revision": scope.workspace.revision,
            "workspace_kind": scope.workspace.kind.value,
        })

    async def _persist_node_products(self):
        """After ordinary settlement only. Missing side record never triggers replay."""
        metadata = self._metadata_or_raise()
        save = getattr(metadata, "save_node_result_source", None)
        if save is None:
            return
        for record in tuple(self._node_products.values()):
            if len(record) == 7 and record[5] is self:
                await save(record[6])  # Store CAS requires the exact completed owner/output.

    async def _load_node_product(self, scope, node):
        from Sprout.execution.node_result_source import payload_digest

        metadata = self._metadata_or_raise()
        load = getattr(metadata, "get_node_result_source", None)
        source = await load(node.id) if load else None
        guard = node.metadata.get("execution_guard", {})
        if (source is None or source.task_id != scope.task.id
                or source.node_id != node.id or source.node_type != node.type.value
                or source.owner != guard.get("owner") or guard.get("state") != "completed"
                or source.subject_digest != self._node_subject_digest(scope)
                or source.output_digest != payload_digest(node.metadata.get("output"))):
            raise PermissionError("node_artifact_provenance_unavailable")
        resources = json.loads(source.resources_json)
        parents = json.loads(source.parents_json)
        if not set(node.dependencies).issubset({p["node_id"] for p in parents}):
            raise PermissionError("node_artifact_provenance_unavailable")
        for parent in parents:
            stored = await metadata.get_execution_node(parent["node_id"])
            if (stored is None or stored.task_id != scope.task.id
                    or stored.status is not NodeStatus.COMPLETED
                    or payload_digest(stored.metadata.get("output")) != parent["output_digest"]):
                raise PermissionError("node_artifact_provenance_unavailable")
        # The side record is data. Fresh capture/policy checks below grant visibility.
        record = (scope.task, replace(scope.workspace, manifest=None), node,
                  node.metadata.get("output"), tuple(r["path"] for r in resources), self, source)
        self._node_products[(scope.task.id, node.id)] = record
        return record

    async def _admit_node_products(self, scope, nodes):
        """Live receipts or V1 side records, never a bare persisted payload.

        Persisted reuse stays same-Task/Principal/Session/Workspace and performs
        fresh per-file authorization and content/identity verification. It does
        not admit cross-Task conversation references or widen a current Scope.
        """
        from Sprout.tools.context_keys import _project_analysis_call

        await self._persist_node_products()
        paths, found, admitted_ids = set(), False, set()
        for node in nodes:
            if node.result_ref is not None:
                raise PermissionError("node_artifact_provenance_unavailable")
            output = node.metadata.get("output")
            if output is None:
                continue
            record = self._node_products.get((scope.task.id, node.id))
            if record is None:
                record = await self._load_node_product(scope, node)
            if (record is None or record[5] is not self or node.status is not NodeStatus.COMPLETED
                    or record[0] != scope.task
                    or record[1] != replace(scope.workspace, manifest=None)
                    or record[2].type != node.type or record[3] != output
                    or record[2].metadata.get("execution_guard", {}).get("owner")
                    != node.metadata.get("execution_guard", {}).get("owner")):
                raise PermissionError("node_artifact_provenance_unavailable")
            found = True
            admitted_ids.add(node.id)
            paths.update(record[4])
        if any(dependency not in admitted_ids for dependency in scope.node.dependencies):
            raise PermissionError("node_artifact_provenance_unavailable")
        # Re-capture to authorize and verify READ content before every publication.
        if paths:
            ordered = tuple(sorted(paths))
            tool = await self._issue_project_analysis_tool(paths=ordered)
            with _project_analysis_call(tool):
                selection = await self._issue_project_analysis_selection(ordered)
                fresh = await self._capture_project_selection(
                    selection, content_project=lambda analysis, view: {
                        entry.relative_path: entry.text() for entry in view.entries},
                )
            captured = {r["path"]: r for r in scope.resource_receipts}
            for node in nodes:
                record = self._node_products.get((scope.task.id, node.id))
                if record is not None and len(record) == 7:
                    if any(captured.get(r["path"]) != r
                           for r in json.loads(record[6].resources_json)):
                        raise PermissionError("node_resource_content_changed")
            for node in nodes:
                if node.type is NodeType.READ and node.metadata.get("output"):
                    if any(fresh.get(item["path"]) != item["content"]
                           for item in node.metadata["output"]):
                        raise PermissionError("node_resource_content_changed")
        scope.captured_paths = tuple(sorted(paths))
        receipts = {}
        for node in nodes:
            record = self._node_products.get((scope.task.id, node.id))
            if record is not None and len(record) == 7:
                for receipt in json.loads(record[6].resources_json):
                    prior = receipts.setdefault(receipt["path"], receipt)
                    if prior != receipt:
                        raise PermissionError("node_resource_content_changed")
        scope.resource_receipts = tuple(receipts[key] for key in sorted(receipts))
        scope.parent_receipts = tuple(sorted(admitted_ids))
        return found

    def _node_context_resources(self, scope, files, resources):
        """A live frame is not a licence to inject arbitrary resource dictionaries."""
        if not resources:
            return not files
        known = [item for record in self._node_products.values()
                 if record[0] == scope.task
                 and record[1] == replace(scope.workspace, manifest=None)
                 and record[5] is self
                 and record[2].type is NodeType.READ for item in record[3]]
        return (tuple(item.get("path") for item in resources) == tuple(files)
                and all(item in known for item in resources))

    # -- assembly ---------------------------------------------------------
    def register_agent(
        self, name: str, agent: Agent, *, default: bool = False, replace: bool = True
    ) -> None:
        self.agents.register(name, agent, replace=replace)
        if default or self._router is None:
            self._default_agent = name if default else self._default_agent
            self._router = AgentRouter(self.agents, default=self._default_agent)

    def set_router(self, router: AgentRouter) -> None:
        """Advanced override for rule-based routing; replaces the default router."""
        self._router = router

    @property
    def default_agent(self) -> str:
        return self._default_agent

    # -- lifecycle ---------------------------------------------------------
    async def start(self) -> None:
        if self._started:
            return
        if self._settings is not None:
            from Sprout.storage.bootstrap import ensure_storage_ready

            await ensure_storage_ready(self._settings)
        self._started = True
        await self.events.publish(Event(RUNTIME_STARTED, {"version": _package_version()}))
        await self._sandbox_lifecycle.reconcile_orphans()
        await self._sweep_stale_approvals()
        await self._start_outbox_worker()

    async def _sweep_stale_approvals(self) -> None:
        """Settle approvals that lapsed while nothing was running.

        ``sweep_expired`` existed but nothing called it: the only route was an
        operator typing ``sprout approvals sweep``. So an approval whose TTL ran
        out while the process was closed stayed ``PENDING`` forever, and every
        consumer of "what is waiting on a human" counted it — nine had piled up
        across two days, several already hours past expiry, one prompting every
        new session to answer a question whose task was long gone.

        Same shape as ``reconcile_orphans`` above, and for the same reason: work
        parked before a restart is not resumed by the restart, so it has to be
        settled at startup rather than waited on. A failure here is recorded and
        swallowed — expiry is housekeeping, and it must not block startup.
        """
        try:
            swept = await self._approvals.sweep_expired()
        except Exception as exc:  # noqa: BLE001 - housekeeping must not block start
            self._record_security(
                "approval.sweep_failed",
                {"error": f"{type(exc).__name__}: {exc}"},
            )
            return
        if swept:
            self._record_security("approval.swept_at_startup", {"count": swept})

    async def stop(self) -> None:
        # Workers can be started independently of start(). Join them even when
        # there is no started Runtime/storage lifecycle to close.
        try:
            await self.stop_worker()
        finally:
            if self._started:
                self._started = False
                try:
                    if self._background_tasks:
                        await asyncio.gather(*self._background_tasks, return_exceptions=True)
                    # Flush after local writers finish, while the database is open.
                    await self._stop_outbox_worker()
                    await self.events.publish(Event(RUNTIME_STOPPED, {}))
                finally:
                    # A failed worker/flush must not leave authority connections open.
                    await self.storage.close()

    async def _start_outbox_worker(self) -> None:
        """Drain the transactional outbox into the derived layers.

        ``SqliteSessionStore.append_turn`` writes the turn and its outbox rows
        in one transaction; this worker delivers them to the memory index and
        the observation store, idempotently on ``event_id``. Stores without an
        outbox table (in-memory backends) are skipped.
        """
        db = getattr(self._session_store, "database", None)
        if db is None:
            return
        from Sprout.memory.outbox import OutboxWorker

        worker = OutboxWorker(
            db=db,
            session_search=self.storage.session_search,
            observations=self.storage.observations,
        )
        await worker.start()
        self._outbox_worker = worker

    async def _stop_outbox_worker(self) -> None:
        worker = self._outbox_worker
        if worker is None:
            return
        self._outbox_worker = None
        await worker.stop()

    # -- online path ---------------------------------------------------------
    async def _dispatch_project_analysis(
        self, message, session, *, principal, analysis_task, classification,
        analysis_task_provider=None,
    ):
        """Admit an explicit host reference; routing hints confer no authority.

        This local-only lane does not select Tasks by Session, create a Scope,
        or reinterpret old/running graphs. The existing guard owns execution.
        """
        if _project_analyze_tool_call(message, str(classification.get("intent", ""))) is None:
            return None
        from Sprout.orchestration.service import SequentialOrchestrator
        from Sprout.security.access import AccessDecision, ActionRequest, ActionType
        from Sprout.task.paths import bound_scope_path, scope_path
        from Sprout.workspace.classifier import classify
        from Sprout.workspace.models import ResourceRef

        node = None
        reason = "authorized_analysis_task_required"
        content = reason
        try:
            if analysis_task is None and analysis_task_provider is not None:
                # Python host capability only. Neither classification nor model
                # metadata supplies this callable or an authenticated identity.
                if principal is None or principal.authenticated is not True:
                    raise PermissionError("analysis_task_subject_unavailable")
                owned = await self._sessions.require_owner(session.id, principal)
                frozen = self._sessions.frozen_principal(owned)
                if (frozen.user_id, frozen.source, frozen.authenticated) != (
                        principal.user_id, principal.source, principal.authenticated):
                    raise PermissionError("analysis_task_subject_unavailable")
                from copy import deepcopy

                analysis_task = await analysis_task_provider(
                    message=replace(message, metadata={}), session=deepcopy(owned),
                    principal=principal,
                )
                if analysis_task is None:
                    raise PermissionError("project_analysis_cancelled")
            if not isinstance(analysis_task, Task) or principal is None:
                raise PermissionError(reason)
            store = self.storage.metadata
            if store is None:
                raise PermissionError("analysis_metadata_unavailable")
            task = await store.get_task(analysis_task.id)
            owned = await self._sessions.require_owner(session.id, principal)
            frozen = self._sessions.frozen_principal(owned)
            if (principal.authenticated is not True
                    or (frozen.user_id, frozen.source, frozen.authenticated)
                    != (principal.user_id, principal.source, principal.authenticated)
                    or task is None
                    or task.actor != principal or task.source != principal.source
                    or task.metadata.get("session_id") != session.id
                    or task.execution_mode is not TaskExecutionMode.READ_ONLY
                    or task.status not in {TaskStatus.CREATED, TaskStatus.READY}
                    or await store.list_execution_nodes(task.id)):
                raise PermissionError("analysis_task_subject_unavailable")
            workspace = await store.get_workspace(task.workspace_id)
            if workspace is None:
                raise PermissionError("analysis_workspace_unavailable")
            root = scope_path(str(workspace.root))
            scope = task.delegation_scope.bind_paths(str(workspace.root))
            if (root is None or scope.is_expired() or not scope.allowed_paths
                    or any(scope_path(p) is None or not root.contains(scope_path(p))
                           for p in scope.allowed_paths)):
                raise PermissionError("analysis_task_scope_unavailable")
            requested_workspace = message.metadata.get("workspace_id")
            if requested_workspace and requested_workspace != workspace.id:
                raise PermissionError("analysis_workspace_mismatch")
            raw_paths = task.metadata.get("required_paths")
            if raw_paths is not None and type(raw_paths) not in {tuple, list}:
                raise PermissionError("project_capture_selection_invalid")
            if raw_paths:
                _, plan = self._known_read_only_plan(workspace, task)
                paths = tuple(resource.path for resource in plan.resources)
                # Negative preflight prevents denied host-selected names entering
                # model tool-call messages. It grants no access: B1/Broker still
                # revalidate the live subject and every actual open afterwards.
                for path in paths:
                    absolute = (workspace.root / path).as_posix()
                    request = ActionRequest(
                        task_id=task.id, actor=task.actor, action=ActionType.FILE_READ,
                        resource=ResourceRef(workspace.id, absolute, kind=classify(
                            path, overrides=self._file_broker._classify_overrides)), scope=scope,
                    )
                    decision = self._policy_engine.decide(request)
                    await self._record_project_capture_policy(request, decision)
                    if (decision.decision is not AccessDecision.ALLOW
                            or not scope.permits(ActionType.FILE_READ.value, absolute)):
                        raise PermissionError("analysis_task_resource_unavailable")
                # A real entry file reaches ToolExecutor. A registered manifest
                # still requires separate per-file authorization below it.
                target = str(workspace.root / paths[0])
            else:
                plan = ReadPlan(task_id=task.id, purpose=task.instruction)
                paths = ()
                target = str(workspace.root)
                directory = task.metadata.get("analysis_directory")
                if directory is not None:
                    from Sprout.workspace.authorized_content import relative_label

                    if directory != ".":
                        directory = relative_label(directory)
                        target = str(workspace.root / directory)
            # Explicit path hints may constrain, never enlarge, the host selection.
            for key in ("workspace_path", "project_path", "path", "root"):
                hint = message.metadata.get(key)
                if hint:
                    requested = scope_path(bound_scope_path(hint, str(workspace.root)))
                    if requested is None or not root.contains(requested):
                        raise PermissionError("analysis_workspace_mismatch")
                    if paths and any(not requested.contains(scope_path(
                            str(workspace.root / p))) for p in paths):
                        raise PermissionError("analysis_workspace_mismatch")
                    if len(paths) <= 1:
                        target = requested.text()
                    break
            node = ExecutionNode(task_id=task.id, type=NodeType.AGENT)
            admission = {"task": task, "workspace": workspace, "paths": paths,
                         "target": target, "message": message, "analysis_result": None}
            self._online_analysis_requests[node.id] = admission
            graph = ExecutionGraph(task_id=task.id, nodes=(node,), entry_node_ids=(node.id,))
            result = await SequentialOrchestrator(store).execute(
                graph, handler=self._executor(store).handler(task, workspace, plan),
            )
            current = await store.get_task(task.id)
            current_workspace = await store.get_workspace(workspace.id)
            current_session = await self._sessions.require_owner(session.id, principal)
            if (result.status is not TaskStatus.COMPLETED or current is None
                    or current.status is not TaskStatus.COMPLETED
                    or current.execution_mode is not TaskExecutionMode.READ_ONLY
                    or current.actor != task.actor or current.source != task.source
                    or current.workspace_id != task.workspace_id
                    or current.metadata.get("session_id") != session.id
                    or current.delegation_scope != task.delegation_scope
                    or current.delegation_scope.is_expired() or current_workspace != workspace
                    or self._sessions.frozen_principal(current_session) != frozen):
                raise PermissionError("analysis_publication_unavailable")
            saved = await store.get_execution_node(node.id)
            output = saved.metadata.get("output", {}) if saved else {}
            if admission["analysis_result"] is None or not output.get("analysis_ok"):
                raise PermissionError(admission.get(
                    "unavailable_reason", "project_analysis_unavailable"))
            await self._persist_node_products()
            content = output["content"]
            coverage = admission["analysis_result"].data.get("coverage", {})
            if coverage.get("mode") == "discovery":
                content += ("\n\n覆盖范围 / coverage: authorized project files; "
                            f"{coverage.get('file_count', 0)} files; "
                            "partial project coverage (policy/scope and supported resource types).")
            reason = ""
        except PermissionError as exc:
            content = reason = str(exc)
        finally:
            if node is not None:
                self._online_analysis_requests.pop(node.id, None)
        outbound = OutboundMessage(content=content, channel=message.channel,
                                   session_id=session.id, correlation_id=message.id,
                                   metadata={"analysis_status": "unavailable" if reason else "ok",
                                             "reason": reason})
        await self._persist_turns(session, message, AgentResult(content=content))
        return outbound

    async def handle(
        self, message: Message, *, principal: Principal | None = None,
        foreground: bool = False,
        _resume_calls: list | None = None,
        analysis_task: Task | None = None,
        analysis_task_provider: Callable[..., Awaitable[Task | None]] | None = None,
    ) -> OutboundMessage:
        """Run one online turn. Middleware -> session -> router -> context -> agent."""
        if self._router is None:
            raise RuntimeError("No agent router configured")

        await self.events.publish(
            Event(
                MESSAGE_RECEIVED,
                {"message_id": message.id, "channel": message.channel},
                message.id,
            )
        )
        message = self._entry_message(message, principal)
        if _resume_calls is not None:
            message = replace(
                message, metadata={**message.metadata, "resume_tool_calls": _resume_calls}
            )
        message = await self.middleware.before(message)
        session = await self._sessions.resolve(message, principal=principal)
        classification: dict[str, Any] = {}
        clarification = await route_clarification(
            self, message, session, principal, classification=classification,
        )
        if clarification is not None:
            return await self.middleware.after(message, clarification)
        context = await self._contexts.build(
            message=message,
            session=session,
            tools=self.tools.list(),
            skills=self.skills.list(),
        )
        if _resume_calls is not None:
            # Host-only replay, recovered after Session ownership validation.
            # Never recover this payload from model/message metadata here.
            from copy import deepcopy

            context = replace(context, metadata={
                **context.metadata, "resume_tool_calls": deepcopy(_resume_calls),
            })
        if not classification:
            classification = await self._classify_intent_with_llm(message, context)
        clarification = await route_semantic_clarification(
            self, message, session, principal, classification,
        )
        if clarification is not None:
            return await self.middleware.after(message, clarification)
        message, context = await self._recognize_intent(
            message, context, classification=classification,
        )

        analysis = await self._dispatch_project_analysis(
            message, session, principal=principal, analysis_task=analysis_task,
            classification=classification,
            analysis_task_provider=analysis_task_provider,
        )
        if analysis is not None:
            return await self.middleware.after(message, analysis)

        # A recognised task intent is queued for background execution and acked
        # immediately; it never runs inline. A task intent *without* a workspace
        # used to fall through to the conversation path silently — where the
        # agent has no write tools at all, since those live on AGENT nodes that
        # only exist once a task is compiled — so an explicit "write me a file"
        # was answered by an agent that could not do it, and nothing said why.
        # Now it asks which directory instead of degrading.
        handled = await self._dispatch_task_intent(
            message, context, session, principal=principal, foreground=foreground
        )
        if handled is not None:
            outbound, held = handled
            if held is not None:
                # The mark that lets a later consent find this instruction
                # lives on the stored turn, so the held case is the one that
                # has to be written here.
                await self._persist_turns(
                    session,
                    held,
                    AgentResult(content=outbound.content, metadata=dict(outbound.metadata)),
                )
            return await self.middleware.after(held or message, outbound)

        agent = self._router.route(message)
        await self.events.publish(Event(AGENT_STARTED, {"session_id": session.id}, message.id))
        try:
            result = await agent.run(message, context)
        except Exception as exc:
            await self.events.publish(
                Event(
                    AGENT_FAILED,
                    {"error_type": type(exc).__name__, "error": str(exc)},
                    message.id,
                )
            )
            raise

        # The agent may have discovered mid-turn that it needs a workspace
        # (``request_workspace``). That supersedes its own answer: it has no
        # write tools, so its prose could only be an explanation of the
        # obstacle. Hold the instruction and ask the operator instead.
        requested = await self._workspace_request_from_agent(result, message, session)
        if requested is not None:
            held, outbound = requested
            await self._persist_turns(
                session,
                held,
                AgentResult(content=outbound.content, metadata=dict(outbound.metadata)),
            )
            return await self.middleware.after(held, outbound)

        await self._persist_turns(session, message, result,
                                 ordinary_context=context, ordinary_agent=agent)
        await self.events.publish(Event(AGENT_COMPLETED, {"session_id": session.id}, message.id))
        await self.events.publish(Event(MESSAGE_SENT, {"session_id": session.id}, message.id))
        outbound = OutboundMessage(
            content=result.content,
            channel=message.channel,
            session_id=session.id,
            correlation_id=message.id,
            metadata=dict(result.metadata),
        )
        return await self.middleware.after(message, outbound)

    @staticmethod
    def _entry_message(message: Message, principal: Principal | None) -> Message:
        """Embedding arguments are trusted; message payload/metadata never are.

        Transport adapters must resolve the principal themselves and must not
        deserialize these keyword arguments from external input.
        """
        reserved = {
            "intent", "intent_name", "intent_event", "intent_confidence", "trigger_event",
            "origin_channel", "foreground_task", "workspace_consent", "execution_mode",
            "resume_tool_calls", "pending_tool_calls", "roles", "role", "authenticated",
            "permissions", "allowed_actions", "delegation_scope",
            "entry_principal_v1", WORKSPACE_CONSENT_KEY, "proposed_workspace_root",
            "intent_clarification_v1", "clarification_id", "clarification_original",
            "clarification_required", "clarification_resolution", "confirmed",
        }
        return replace(
            message,
            user_id=principal.user_id if principal else "anonymous",
            channel=(
                {"mcp_client": "mcp"}.get(principal.source, principal.source)
                if principal and principal.source else "automation"
            ),
            metadata={key: value for key, value in message.metadata.items() if key not in reserved},
        )

    async def _handle_task(
        self, message: Message, session, *, principal: Principal | None = None,
        foreground: bool = False,
    ) -> OutboundMessage:
        """Queue a coding task for background execution and ack immediately.

        Callers reach this when the message carries a workspace id. A task
        intent *without* one no longer falls through to the conversation path;
        it is held at :meth:`_request_workspace` until a human names the
        directory, then resumes here via :meth:`grant_workspace_consent`.
        """
        workspace_id = str(message.metadata.get("workspace_id") or "")
        workspace = await self.get_workspace(workspace_id)
        if workspace is not None and not (workspace.root / ".git").exists():
            return OutboundMessage(
                content="编码任务需要 Git 仓库。请先自行初始化 Git，再重新提交；只读分析仍可使用。",
                channel=message.channel,
                session_id=session.id,
                correlation_id=message.id,
                metadata={"intent": "task", "blocked_reason": "git_repository_required"},
            )
        # The surface the operator used, not the one the resume happens to run
        # on. A consented task resumes over an internal message, and taking the
        # source from there filed every such task as ``unknown``.
        # No caller-selected channel or metadata can turn unattended input into
        # an interactive approval source. Unresolved embeddings are unattended.
        source = principal.source if principal is not None else "automation"
        task = await self.create_task(
            workspace_id,
            message.content,
            actor=principal or Principal(user_id="anonymous", source=source),
            source=source,
            # Recorded so a later turn can report this task back to the session
            # that asked for it. Without it a parked task belongs to no
            # conversation, and the person who asked is never told.
            metadata={
                **({"session_id": session.id} if session.id else {}),
                "response_language": str(
                    message.metadata.get("cli_language")
                    or message.metadata.get("response_language")
                    or detect_language(message.content)
                ),
            },
        )
        if foreground:
            result = await self.execute(task)
            return OutboundMessage(
                content="",
                channel=message.channel,
                session_id=session.id,
                correlation_id=message.id,
                metadata={
                    "intent": "task",
                    "task_id": task.id,
                    "foreground_task": True,
                    "task_status": result.status.value,
                },
            )
        await self.submit_task(task)
        self.start_worker()
        return OutboundMessage(
            content=f"任务已提交（id={task.id}），正在后台执行。",
            channel=message.channel,
            session_id=session.id,
            correlation_id=message.id,
            metadata={"intent": "task", "task_id": task.id},
        )

    # -- telling the asker what happened -----------------------------------

    #: Task statuses that are waiting on a human. Each names *what* the human
    #: has to decide, because "waiting_approval" alone does not say whether the
    #: next move is `sprout approvals approve` or `sprout project approve` —
    #: and those take different ids.
    _PARKED_TASK_STATUSES = frozenset(
        {
            TaskStatus.WAITING_APPROVAL,
            TaskStatus.WAITING_RESOURCE,
            TaskStatus.READY_TO_APPLY,
        }
    )

    async def parked_tasks_for_session(self, session_id: str) -> list[Task]:
        """Tasks this session started that are waiting on a human decision.

        A background task runs detached from the turn that created it, so
        nothing on the chat path can observe it finishing or stalling. Without
        this lookup the operator is told "I will report back" and then hears
        nothing until they think to run ``sprout approvals list`` — which is
        exactly what happened: a task wrote its file and parked, and nobody was
        told for the rest of the session.
        """
        if not session_id or self.storage.metadata is None:
            return []
        parked: list[Task] = []
        for task in await self.list_tasks():
            if task.status not in self._PARKED_TASK_STATUSES:
                continue
            if str(task.metadata.get("session_id") or "") == session_id:
                parked.append(task)
        return parked

    async def task_progress(self, task: Task) -> tuple[str, str]:
        """What a parked task is waiting for: ``(kind, id)``.

        ``kind`` is ``"approval"`` or ``"proposal"`` and ``id`` is what the
        matching command takes. Resolved by looking at what actually exists
        rather than inferring from the status, since a task can sit in
        ``WAITING_APPROVAL`` for either reason.
        """
        proposals = await self.list_change_proposals(task.id)
        pending = [
            proposal for proposal in proposals if proposal.status is ChangeProposalStatus.PENDING
        ]
        if pending:
            return "proposal", pending[0].id
        records = await self.pending_approvals()
        for record in records:
            if str(record.task_id or "") == task.id:
                return "approval", record.id
        return "", ""

    #: Intent classification values that name the coding path. Only ``task``
    #: reaches a compiled graph, hence a sandbox, hence the write tools.
    _WORKSPACE_ASKING_INTENTS = frozenset({"task"})

    def _asks_for_workspace(self, message: Message, context, session=None) -> bool:
        """Is this a coding intent that cannot proceed without a workspace?

        Two conditions, both required:

        1. The classifier called it ``task`` — a workspace-bearing message
           takes the existing path regardless, so this only guards the case
           where there is nothing to compile against.
        2. :func:`detect_task_hint` agrees on the text itself.

        The second is not redundancy. Interrupting a conversation to ask a
        question is the expensive move here, so it asks for *deterministic*
        evidence of a coding request rather than the model's opinion alone. An
        intent mis-classification — which the test doubles produce readily —
        would otherwise turn ordinary chat into a workspace prompt.

        This gate is best-effort by nature: it reads the wording, so a typo or
        an unfamiliar phrasing walks straight past it. That is affordable only
        because it is not the sole route — an agent that reaches the model and
        recognises a coding request calls ``request_workspace``, which
        :meth:`_workspace_request_from_agent` honours after the turn. This one
        exists to save a model round-trip and to interrupt *before* an agent
        starts work it cannot finish.

        A message that already carries the answer (the resume from
        :meth:`grant_workspace_consent`) is never asked again.
        """
        intent = str(context.metadata.get("intent") or "")
        if intent not in self._WORKSPACE_ASKING_INTENTS:
            return False
        if str(message.metadata.get("workspace_id") or ""):
            return False
        if message.metadata.get("workspace_consent"):
            return False
        if session is not None and self.session_workspace_id(session):
            return False
        return detect_task_hint(message.content)

    async def _dispatch_task_intent(
        self, message: Message, context, session, *, principal: Principal | None = None,
        foreground: bool = False,
    ) -> tuple[OutboundMessage, Message | None] | None:
        """Handle a coding intent, or return ``None`` to run the turn normally.

        Returns the reply, plus a message to persist when the turn was *held*
        (``None`` otherwise). Holding marks the message and ``Message`` is
        frozen, so the marked copy travels back rather than being written in
        place — and only the held case persists, because that mark is the only
        way :meth:`grant_workspace_consent` can find the instruction later. The
        queued-task path persists nothing, as it always has.

        One place for both online paths to ask the same question, so ``handle``
        and ``handle_stream`` cannot drift apart. They had: ``handle_stream``
        had no task branch at all, so the CLI REPL — the path a user actually
        types into — never reached the coding graph even *with* a workspace,
        and certainly never asked for one.

        This covers the *before-the-agent* half only. The other half,
        :meth:`_workspace_request_from_agent`, runs after the agent and handles
        the case this gate cannot see.
        """
        if not detect_task_hint(message.content or ""):
            return None
        if self._asks_for_workspace(message, context, session):
            held, outbound = await self._request_workspace(message, session)
            return outbound, held
        if str(context.metadata.get("intent") or "") != "task":
            return None
        explicit = str(message.metadata.get("workspace_id") or "")
        if explicit:
            # The message names its workspace: the resume from a consent grant,
            # or a caller that bound one deliberately. Already an instruction.
            return await self._handle_task(
                message, session, principal=principal, foreground=foreground
            ), None
        # A workspace the operator consented to earlier in this session. Without
        # this the session only stopped *asking*; the coding intent still fell
        # through to the conversation path, where the agent has no write tools —
        # so "yes" would silence the question and still not run the task, which
        # is worse than asking.
        workspace_id = self.session_workspace_id(session)
        if not workspace_id:
            return None
        # Creating a task is at least as expensive as asking for a workspace, so
        # it demands no less evidence. The ask gate already requires the
        # classifier *and* :func:`detect_task_hint`, and a bound workspace must
        # not lower that bar: "好了吗" is a status question, the classifier
        # labelled it ``task`` ("are you done?"), and queueing a full
        # read→sandbox→plan→agent→evaluate run for it is exactly the mislabel
        # the keyword requirement exists to absorb. Such a turn stays a
        # conversation, where the agent can answer from what it already knows.
        if not detect_task_hint(message.content or ""):
            return None
        message = replace(
            message,
            metadata={**dict(message.metadata), "workspace_id": workspace_id},
        )
        return await self._handle_task(
            message, session, principal=principal, foreground=foreground
        ), None

    async def _workspace_request_from_agent(
        self, result: AgentResult, message: Message, session
    ) -> tuple[Message, OutboundMessage] | None:
        """Hold the turn when the agent itself asked for a workspace.

        :meth:`_asks_for_workspace` decides from the *text*, before the agent
        runs; this decides from what the agent concluded *during* the turn, via
        the ``request_workspace`` tool. The two are complementary: the keyword
        gate is cheap and fires before any model call, while this one sees the
        whole turn and so survives a typo or an unusual phrasing that no
        substring would match. Both end in the same hold.

        Returns ``None`` when nothing was asked for, or when asking again could
        not help: a bound workspace means the request already has an answer, and
        re-asking would loop.
        """
        if not result.metadata.get(WORKSPACE_REQUESTED_KEY):
            return None
        if write_intent_disposition(message.content) is WriteIntentDisposition.KNOWN_NONWRITE:
            return None
        if str(message.metadata.get("workspace_id") or ""):
            return None
        if message.metadata.get("workspace_consent"):
            return None
        if await self._session_has_active_task(session.id):
            return message, OutboundMessage(
                content=(
                    "当前会话已经有正在执行或等待审批的任务。"
                    "请用 /status <task_id> 查看它，或先处理已有任务，"
                    "不要再发起新的编码任务。"
                ),
                channel=message.channel,
                session_id=session.id,
                correlation_id=message.id,
                metadata={"intent": "conversation"},
            )
        # Already answered for this session: the request has a workspace, so
        # asking again is the loop this guard exists to prevent.
        if self.session_workspace_id(session):
            return None
        return await self._request_workspace(message, session)

    async def _session_has_active_task(self, session_id: str) -> bool:
        """Whether this session already owns a task that is not finished."""
        if not session_id or self.storage.metadata is None:
            return False
        terminal = {
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.ROLLED_BACK,
        }
        for task in await self.list_tasks():
            if str(task.metadata.get("session_id") or "") == session_id:
                if task.status not in terminal:
                    return True
        return False

    async def _request_workspace(
        self, message: Message, session
    ) -> tuple[Message, OutboundMessage]:
        """Hold a coding task until a human names the directory to work in.

        The consent asked for is the smallest one that unblocks the request: a
        single directory, for a single task. The agent never writes to that
        directory directly — it writes to a git worktree sandbox and the result
        parks on a change proposal — so granting this does not hand over write
        access, it only lets a task be compiled.

        The hold is recorded by returning a *marked copy* of the message rather
        than writing a turn here: ``_persist_turns`` is the single authority
        write point of the online path, and a second writer produced two user
        turns for one instruction. The caller persists the marked copy through
        the normal path, and :meth:`grant_workspace_consent` reads the mark.

        Nothing else is written: no workspace is registered and no task is
        created until the operator answers.
        """
        root = Path.cwd()
        proposed = root.as_posix()
        held = replace(
            message,
            metadata={
                **dict(message.metadata),
                WORKSPACE_CONSENT_KEY: True,
                "proposed_workspace_root": proposed,
                # The channel the operator actually typed into. The resume runs
                # on an internal message, and if the channel is taken from
                # *that* the task's source degrades to ``unknown``: the approval
                # list then shows a source nobody can interpret, and the
                # audit trail loses which surface asked for the work.
                "origin_channel": message.channel,
            },
        )
        confirmation = (
            "还不能确定你是否要求写入。请明确确认：是否将下面保存的原始输入作为编码任务执行，"
            f"并以 {proposed} 作为本次任务的工作区？\n\n原始输入：\n{message.content}\n\n"
            "只有明确同意才会打开工作区并编排此任务；修改落地仍需后续验证与审批。"
            if write_intent_disposition(message.content) is WriteIntentDisposition.UNKNOWN
            else (
                "这是一个编码任务，但当前会话没有绑定工作区，所以我暂时不能动手"
                "（这个会话里的 agent 没有写文件的工具）。\n\n"
                f"是否以 {proposed} 作为本次任务的工作区？\n"
                "同意后改动会先做在 git 沙箱副本里，落地到你的目录之前还会再问你一次。"
            )
        )
        await self.events.publish(
            Event(
                INTENT_TASK_REQUESTED,
                {
                    "message_id": message.id,
                    "channel": message.channel,
                    WORKSPACE_CONSENT_KEY: True,
                    "proposed_workspace_root": proposed,
                },
                message.id,
            )
        )
        return held, OutboundMessage(
            content=confirmation,
            channel=message.channel,
            session_id=session.id,
            correlation_id=message.id,
            metadata={
                "intent": "task",
                WORKSPACE_CONSENT_KEY: True,
                "proposed_workspace_root": proposed,
                "proposed_workspace_is_git": (root / ".git").exists(),
            },
        )

    #: Session metadata key holding the workspace a session has been granted.
    #: Kept on the session rather than passed per message because that is what
    #: the operator believes they answered: "yes, for this task" was remembered
    #: only on the one resumed message, so the next coding request in the same
    #: conversation asked again — four times in a row for one operator, each
    #: consent consumed as it was given.
    SESSION_WORKSPACE_KEY = "workspace_id"

    def session_workspace_id(self, session) -> str:
        """The workspace this session already has consent for, if any."""
        metadata = dict(getattr(session, "metadata", None) or {})
        return str(metadata.get(self.SESSION_WORKSPACE_KEY) or "")

    async def _remember_session_workspace(self, session_id: str, workspace_id: str) -> None:
        """Bind a granted workspace to its session so the question is asked once.

        Registration alone does not do this: a workspace can be opened without
        any session involved (``sprout project``, an API caller), and a session
        can outlive several workspaces. Writing it here ties it to the consent
        that produced it.
        """
        session = await self._session_store.get_session(session_id)
        if session is None:
            return
        metadata = dict(session.metadata or {})
        if str(metadata.get(self.SESSION_WORKSPACE_KEY) or "") == workspace_id:
            return
        metadata[self.SESSION_WORKSPACE_KEY] = workspace_id
        session.metadata = metadata
        await self._session_store.save_session(session)

    async def _held_workspace_consent(self, session_id: str):
        """The most recent unanswered consent request for a session, if any.

        The mark lives in the stored *envelope*, which nests the message's own
        metadata under ``message_metadata`` rather than flattening it, so the
        lookup goes one level down.
        """
        for turn in reversed(await self.history(session_id, limit=50)):
            if turn.role != "user":
                continue
            envelope = dict(turn.metadata or {})
            metadata = dict(envelope.get("message_metadata") or {})
            if not metadata.get(WORKSPACE_CONSENT_KEY):
                continue
            return turn, metadata
        return None, None

    def _workspace_consent_store(self):
        # The existing fan-out exposes its authoritative store publicly. Claims
        # are not derived writes and must not fan out or swallow store errors.
        store = self._session_store
        if isinstance(store, SixLaneFanout):
            store = store.authority
        if not all(callable(getattr(store, name, None)) for name in (
            "issue_workspace_consent", "claim_workspace_consent",
        )):
            raise LookupError("Backend lacks durable workspace consent capability")
        return store

    async def grant_workspace_consent(
        self,
        session_id: str,
        *,
        root: str | Path | None = None,
        user_id: str = "anonymous",
        principal: Principal | None = None,
        foreground: bool = False,
    ) -> OutboundMessage:
        """Record consent for the instruction held by :meth:`_request_workspace`.

        The instruction is read back from the held turn rather than taken from
        the caller, so what runs is what the operator was shown — a caller
        cannot attach this consent to a different task.

        Single-task by construction: the held turn is consumed once, and the
        workspace id it produces rides only the resumed message. Answering
        again is an error, not a second task.

        This separate trusted-adapter action is affirmative human confirmation
        of the saved instruction, never an automatic response to a model flag.
        """
        owned = await self._sessions.require_owner(session_id, principal)
        original = self._sessions.frozen_principal(owned)
        held, metadata = await self._held_workspace_consent(session_id)
        if held is None or metadata is None:
            raise LookupError(f"No pending workspace consent for session {session_id!r}")
        instruction = await clarified_instruction(self, owned, held, metadata)
        if write_intent_disposition(instruction) is WriteIntentDisposition.KNOWN_NONWRITE:
            raise LookupError("Held instruction is not a coding request; issue a fresh instruction")
        store = self._workspace_consent_store()
        if await store.claim_workspace_consent(owned, held, str(uuid4())) is not True:
            raise LookupError("Consent unavailable or already claimed; issue a fresh instruction")
        proposed = str(metadata.get("proposed_workspace_root") or "")
        workspace = await self.open_workspace(
            root if root is not None else Path(proposed or Path.cwd())
        )
        # The durable claim remains consumed even if open/bind/create/submit
        # raises. Never release it or infer Task success from consumption.
        session = await self._session_store.get_session(session_id)
        if session is None:
            raise LookupError(f"Unknown session: {session_id!r}")
        # Remember before queueing: if the queue path raises, the operator has
        # still answered, and re-asking is the failure mode being fixed here.
        await self._remember_session_workspace(session_id, workspace.id)
        session = await self._session_store.get_session(session_id) or session
        return await self._handle_task(
            Message(
                content=instruction,
                channel="internal",
                user_id=original.user_id,
                session_id=session_id,
                metadata={
                    "workspace_id": workspace.id,
                    "workspace_consent": True,
                    **(
                        {"cli_language": str(metadata["cli_language"])}
                        if metadata.get("cli_language")
                        else {}
                    ),
                    **({"foreground_task": True} if metadata.get("foreground_task") else {}),
                    # Carried over from the held turn so the task is filed under
                    # the surface the operator used rather than "internal".
                    "origin_channel": str(metadata.get("origin_channel") or ""),
                },
            ),
            session,
            principal=original,
            foreground=foreground and original.source == "cli",
        )

    async def _recognize_intent(self, message: Message, context, *, classification=None):
        if classification is None:
            classification = await self._classify_intent_with_llm(message, context)
        intent = str(classification.get("intent") or "conversation")
        if intent not in _INTENT_EVENTS:
            intent = "conversation"
        trigger_event = _INTENT_EVENTS[intent]
        confidence = _clamp_float(classification.get("confidence"), default=0.5)
        payload = {
            "intent": intent,
            "trigger_event": trigger_event,
            "confidence": confidence,
            "message_id": message.id,
            "channel": message.channel,
            "user_id": message.user_id,
        }
        reason = classification.get("reason")
        if isinstance(reason, str) and reason:
            payload["reason"] = reason[:400]
        cli_command_family = classification.get("cli_command_family")
        if isinstance(cli_command_family, str) and cli_command_family:
            payload["cli_command_family"] = cli_command_family
        workspace_id = message.metadata.get("workspace_id")
        if isinstance(workspace_id, str) and workspace_id:
            payload["workspace_id"] = workspace_id
        await self.events.publish(Event(INTENT_RECOGNIZED, payload, message.id))
        await self.events.publish(Event(trigger_event, payload, message.id))
        if intent == "conversation":
            return message, context
        metadata: dict[str, Any] = {
            **dict(message.metadata),
            "intent": intent,
            "intent_confidence": confidence,
            "intent_event": trigger_event,
        }
        if isinstance(cli_command_family, str) and cli_command_family:
            metadata["cli_command_family"] = cli_command_family
        project_analyze_call = _project_analyze_tool_call(message, intent)
        if project_analyze_call is not None:
            metadata["resume_tool_calls"] = [project_analyze_call]
        enriched = replace(message, metadata=metadata)
        context = replace(
            context,
            metadata={
                **dict(context.metadata),
                "intent": intent,
                "intent_confidence": confidence,
                "intent_event": trigger_event,
                **(
                    {"resume_tool_calls": [project_analyze_call]}
                    if project_analyze_call is not None
                    else {}
                ),
            },
        )
        return enriched, context

    async def _classify_intent_with_llm(
        self, message: Message, context, *, pending_question: str | None = None,
    ) -> dict[str, Any]:
        # Cheap task hint before spending an LLM call: an explicit coding task
        # ("修复…", "/task …") is already decidable from the text alone.
        if pending_question is None and detect_task_hint(message.content or ""):
            return {
                "intent": Intent.TASK.value,
                "trigger_event": intent_event(Intent.TASK),
                "confidence": 0.8,
                "reason": "task_hint",
            }

        settings = self._settings.runtime if self._settings else None
        enabled = settings.intent_semantics_enabled if settings else True
        timeout = settings.intent_semantics_timeout_seconds if settings else 5.0
        if pending_question is not None and not enabled:
            return {"intent": "conversation", "confidence": 0.0, "reason": "semantics_disabled"}
        similar_context = ([] if pending_question else
                           await self._similar_context_for_intent(message))
        prompt = self._intent_prompt(
            message, context, similar_context=similar_context, pending_question=pending_question,
        )
        started = asyncio.get_running_loop().time()
        outcome = "ok"
        calls = 0
        try:
            async with asyncio.timeout(max(0.01, min(float(timeout), 30.0))):
                model = self.models.default()
                calls = 1
                response = await model.chat(
                    [
                        LLMMessage.system(
                            "Classify the user's intent for SEAM Sprout event routing. "
                            "Return only compact JSON.\n" + (PROTOCOL if enabled else
                            "Return exactly three top-level fields: intent, confidence, reason.")
                        ),
                        LLMMessage.user(prompt),
                    ],
                    tools=(),
                )
            parsed = parse_analysis(response.text) if enabled and not response.tool_calls else None
            if parsed is not None:
                return {**parsed, "reason": "semantic_routing_only"}
            outcome = "invalid_or_legacy_response"
            # A legacy intent is still a routing hint, never semantic/write
            # evidence. Discard arbitrary model fields, including tool calls.
            legacy = _parse_intent_json(response.text)
            intent = legacy.get("intent")
            return {"intent": intent if intent in _INTENT_EVENTS else "conversation",
                    "confidence": _clamp_float(legacy.get("confidence"), default=0.0),
                    "reason": "legacy_routing_only"}
        except TimeoutError:
            outcome = "timeout"
            return {"intent": "conversation", "confidence": 0.0, "reason": "llm_timeout"}
        except Exception:
            outcome = "model_error"
            return {"intent": "conversation", "confidence": 0.0, "reason": "llm_error"}
        finally:
            logger.info("intent_analysis calls=%d fallback=%s latency_ms=%d", calls, outcome,
                        int((asyncio.get_running_loop().time() - started) * 1000))

    async def _similar_context_for_intent(self, message: Message) -> list[dict[str, Any]]:
        hits: list[dict[str, Any]] = []
        query = message.content.strip()
        if not query:
            return hits
        context_store = self.storage.context
        if self.storage.vectors is not None:
            embed = getattr(self.storage.lanes, "embed", None)
            vector = embed(query) if callable(embed) else HashingEmbedder()(query)
            try:
                vector_hits = await self.storage.vectors.search(
                    vector,
                    limit=5,
                    namespace=CONTEXT_SNAPSHOTS_NS,
                )
            except Exception:
                vector_hits = ()
            for hit in vector_hits:
                record = None
                session_id = hit.metadata.get("session_id")
                snapshot_hash = hit.metadata.get("hash")
                if context_store is not None and isinstance(session_id, str):
                    try:
                        records = await context_store.list_records(session_id, limit=50)
                    except Exception:
                        records = []
                    record = next(
                        (
                            item
                            for item in records
                            if not isinstance(snapshot_hash, str)
                            or item.snapshot_hash == snapshot_hash
                        ),
                        None,
                    )
                text = record.text if record is not None else ""
                weighted = _weighted_context_summary(text, hit.score)
                hits.append(
                    {
                        "score": hit.score,
                        **weighted,
                        "key": hit.key,
                        "session_id": session_id or "",
                        "snapshot_hash": snapshot_hash or "",
                    }
                )
        if not hits and context_store is not None:
            try:
                records = await context_store.search(query, limit=5)
            except Exception:
                records = []
            hits.extend(
                {
                    "score": 0.0,
                    **_weighted_context_summary(record.text, 0.0),
                    "key": f"context:{record.session_id}:{record.snapshot_hash[:16]}",
                    "session_id": record.session_id,
                    "snapshot_hash": record.snapshot_hash,
                }
                for record in records
            )
        return hits

    def _intent_prompt(
        self,
        message: Message,
        context,
        *,
        similar_context: list[dict[str, Any]],
        pending_question: str | None = None,
    ) -> str:
        recent = []
        for item in list(context.memory)[-6:]:
            role = getattr(item, "role", "")
            content = str(getattr(item, "content", ""))[:500]
            if role and content:
                recent.append({"role": role, "content": content})
        context_payload = {
            "message": message.content,
            "channel": message.channel,
            # Authority, credentials and arbitrary caller metadata are not
            # classification inputs. Recent text is bounded, owned context.
            "recent_conversation": recent,
            "similar_context_from_vector_db": [
                {key: item[key] for key in
                 ("score", "context_weight", "detail_level", "distilled_text") if key in item}
                for item in similar_context[:5]
            ],
            **({"pending_question": pending_question[:500]} if pending_question else {}),
        }
        allowed = _INTENT_EVENTS
        serialized = Redactor().redact(
            json.dumps(context_payload, ensure_ascii=False, default=str),
        ).text
        enabled = self._settings.runtime.intent_semantics_enabled if self._settings else True
        return (
            "Choose exactly one intent from this JSON object, where the value is "
            "the event to trigger:\n"
            f"{json.dumps(allowed, ensure_ascii=False)}\n\n"
            "Use the current message plus context/recent conversation. Return JSON "
            f"like {response_example(enabled)}.\n\n"
            f"Input:\n{serialized}"
        )

    async def handle_stream(
        self, message: Message, *, principal: Principal | None = None, foreground: bool = False,
        analysis_task: Task | None = None,
        analysis_task_provider: Callable[..., Awaitable[Task | None]] | None = None,
    ):
        """Run one online turn and stream the agent's text chunks."""
        if self._router is None:
            raise RuntimeError("No agent router configured")

        await self.events.publish(
            Event(
                MESSAGE_RECEIVED,
                {"message_id": message.id, "channel": message.channel},
                message.id,
            )
        )
        message = self._entry_message(message, principal)
        message = await self.middleware.before(message)
        session = await self._sessions.resolve(message, principal=principal)
        classification: dict[str, Any] = {}
        clarification = await route_clarification(
            self, message, session, principal, classification=classification,
        )
        if clarification is not None:
            clarification = await self.middleware.after(message, clarification)
            yield StreamChunk(content=clarification.content)
            yield StreamChunk(outbound=clarification)
            return
        context = await self._contexts.build(
            message=message,
            session=session,
            tools=self.tools.list(),
            skills=self.skills.list(),
        )
        if not classification:
            classification = await self._classify_intent_with_llm(message, context)
        clarification = await route_semantic_clarification(
            self, message, session, principal, classification,
        )
        if clarification is not None:
            clarification = await self.middleware.after(message, clarification)
            yield StreamChunk(content=clarification.content)
            yield StreamChunk(outbound=clarification)
            return
        message, context = await self._recognize_intent(
            message, context, classification=classification,
        )

        analysis = await self._dispatch_project_analysis(
            message, session, principal=principal, analysis_task=analysis_task,
            classification=classification,
            analysis_task_provider=analysis_task_provider,
        )
        if analysis is not None:
            analysis = await self.middleware.after(message, analysis)
            yield StreamChunk(content=analysis.content)
            yield StreamChunk(outbound=analysis)
            return

        # Same dispatch as ``handle``. This path had no task branch at all, so
        # the CLI REPL never reached the coding graph even when a workspace was
        # present — the ask-for-workspace fix would have been unreachable from
        # the one surface a user types into.
        handled = await self._dispatch_task_intent(
            message, context, session, principal=principal, foreground=foreground
        )
        if handled is not None:
            outbound, held = handled
            yield StreamChunk(content=outbound.content)
            yield StreamChunk(outbound=outbound)
            if held is not None:
                # ``_finish_stream_turn`` persists through the normal path, so
                # it must receive the marked copy — this is the only writer for
                # a held turn, and writing it twice is what produced duplicate
                # user rows for one instruction.
                await self._finish_stream_turn(
                    session,
                    held,
                    AgentResult(content=outbound.content, metadata=dict(outbound.metadata)),
                    outbound,
                )
            return

        agent = self._router.route(message)
        await self.events.publish(Event(AGENT_STARTED, {"session_id": session.id}, message.id))

        chunks: list[str] = []
        stream = getattr(agent, "stream", None)
        result_metadata: dict[str, Any] = {}
        try:
            if stream is None:
                result = await agent.run(message, context)
                chunks.append(result.content)
                result_metadata = dict(result.metadata)
            else:
                from Sprout.agent.loop import AgentLoop

                # Opt in only for the known stream contract; legacy text-only
                # consumers and other agents retain their existing signature.
                options = {"include_result": True} if type(agent) is AgentLoop else {}
                async for chunk in stream(message, context, **options):
                    if isinstance(chunk, AgentResult):
                        if chunk.content:
                            chunks.append(chunk.content)
                            yield StreamChunk(content=chunk.content)
                        result_metadata = dict(chunk.metadata)
                        continue
                    if chunk:
                        chunks.append(chunk)
                        yield StreamChunk(content=chunk)
        except Exception as exc:
            await self.events.publish(
                Event(
                    AGENT_FAILED,
                    {"error_type": type(exc).__name__, "error": str(exc)},
                    message.id,
                )
            )
            yield StreamChunk(error=f"{type(exc).__name__}: {exc}")
            raise

        # Same mid-turn workspace request as ``handle``. Any prose the model
        # streamed before calling the tool has already reached the caller, so
        # the hold adds the runtime's own question after it rather than
        # replacing it �� the agent cannot ask for a directory in a way the
        # runtime can act on, which is precisely why the tool exists.
        requested = await self._workspace_request_from_agent(
            AgentResult(content="".join(chunks), metadata=result_metadata),
            message,
            session,
        )
        if requested is not None:
            held, outbound = requested
            yield StreamChunk(content=outbound.content)
            yield StreamChunk(outbound=outbound)
            await self._finish_stream_turn(
                session,
                held,
                AgentResult(content=outbound.content, metadata=dict(outbound.metadata)),
                outbound,
            )
            return

        result = AgentResult(
            content="".join(chunks),
            metadata=result_metadata,
        )
        outbound = OutboundMessage(
            content=result.content,
            channel=message.channel,
            session_id=session.id,
            correlation_id=message.id,
            metadata=dict(result.metadata),
        )
        yield StreamChunk(outbound=outbound)
        self._schedule_background_task(self._finish_stream_turn(
            session, message, result, outbound, ordinary_context=context, ordinary_agent=agent))

    def schedule_background(self, coroutine) -> None:
        """Run a coroutine off the turn path, drained by ``stop()``.

        Public so attached layers (growth, MCP) can do background work without
        reaching into Runtime internals; their tasks are awaited on shutdown
        like the runtime's own.
        """
        self._schedule_background_task(coroutine)

    def _schedule_background_task(self, coroutine) -> None:
        task = asyncio.create_task(coroutine)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _finish_stream_turn(
        self,
        session: Session,
        message: Message,
        result: AgentResult,
        outbound: OutboundMessage,
        *, ordinary_context=None, ordinary_agent=None,
    ) -> None:
        """Finish a streamed turn after its response has reached the caller."""
        try:
            await self._persist_turns(session, message, result,
                                     ordinary_context=ordinary_context,
                                     ordinary_agent=ordinary_agent)
            await self.events.publish(
                Event(AGENT_COMPLETED, {"session_id": session.id}, message.id)
            )
            await self.events.publish(Event(MESSAGE_SENT, {"session_id": session.id}, message.id))
            await self.middleware.after(message, outbound)
        except Exception as exc:
            await self.events.publish(
                Event(
                    AGENT_FAILED,
                    {"error_type": type(exc).__name__, "error": str(exc)},
                    message.id,
                )
            )

    async def resume_pending(
        self,
        session_id: str,
        *,
        user_id: str = "anonymous",
        content: str = "Continue",
        principal: Principal | None = None,
    ) -> OutboundMessage:
        """Resume a turn that paused on an approval.

        Looks up the latest assistant turn that carried ``pending_tool_calls``
        and re-enters the online path with those calls attached.
        """
        owned = await self._sessions.require_owner(session_id, principal)
        original = self._sessions.frozen_principal(owned)
        pending = None
        for turn in reversed(await self.history(session_id, limit=50)):
            if turn.role != "assistant":
                continue
            metadata = _agent_metadata(turn.metadata or {})
            if metadata.get("approval_required") and metadata.get("pending_tool_calls"):
                pending = metadata["pending_tool_calls"]
                break
        if pending is None:
            raise LookupError(f"No pending approval found for session {session_id!r}")
        message = Message(
            content=content,
            channel=original.source,
            user_id=original.user_id,
            session_id=session_id,
            metadata={"resume_tool_calls": pending},
        )
        return await self.handle(message, principal=original, _resume_calls=pending)

    async def resume_pending_task(
        self,
        session_id: str,
        *,
        user_id: str = "anonymous",
        response_language: str = "",
        principal: Principal | None = None,
    ) -> OutboundMessage:
        """Resume approved pending tool calls without appending a new user turn."""
        owned = await self._sessions.require_owner(session_id, principal)
        original = self._sessions.frozen_principal(owned)
        if self._router is None:
            raise RuntimeError("No agent router configured")
        pending = None
        for turn in reversed(await self.history(session_id, limit=50)):
            if turn.role != "assistant":
                continue
            metadata = _agent_metadata(turn.metadata or {})
            if metadata.get("approval_required") and metadata.get("pending_tool_calls"):
                pending = metadata["pending_tool_calls"]
                break
        if pending is None:
            raise LookupError(f"No pending approval found for session {session_id!r}")
        message = Message(
            content="",
            channel=original.source,
            user_id=original.user_id,
            session_id=session_id,
            metadata={
                "resume_tool_calls": pending,
                **({"cli_language": response_language} if response_language else {}),
            },
        )
        message = await self.middleware.before(message)
        session = await self._sessions.resolve(message, principal=principal)
        context = await self._contexts.build(
            message=message,
            session=session,
            tools=self.tools.list(),
            skills=self.skills.list(),
        )
        # ``pending`` came from the owned Session's authoritative turn above,
        # not from the middleware or the Message's mutable metadata.
        from copy import deepcopy

        context = replace(context, metadata={
            **context.metadata, "resume_tool_calls": deepcopy(pending),
        })
        agent = self._router.route(message)
        await self.events.publish(Event(AGENT_STARTED, {"session_id": session.id}, message.id))
        try:
            result = await agent.run(message, context)
        except Exception as exc:
            await self.events.publish(
                Event(
                    AGENT_FAILED,
                    {"error_type": type(exc).__name__, "error": str(exc)},
                    message.id,
                )
            )
            raise
        await self._persist_assistant_turn(session, message, result)
        await self.events.publish(Event(AGENT_COMPLETED, {"session_id": session.id}, message.id))
        await self.events.publish(Event(MESSAGE_SENT, {"session_id": session.id}, message.id))
        outbound = OutboundMessage(
            content=result.content,
            channel="cli",
            session_id=session.id,
            correlation_id=message.id,
            metadata=dict(result.metadata),
        )
        return await self.middleware.after(message, outbound)

    # -- turn persistence (the single authority write point) -----------------

    async def _offload_body(self, content: str, envelope: dict) -> tuple[str, dict]:
        """Move an oversized turn body to the blobstore (fail-open).

        Below the threshold, or when no blobstore is wired, the body stays
        inline and the envelope is returned unchanged. A blobstore failure
        degrades to inline storage: an oversized authority row beats a lost
        message (RUNTIME_DATA_PLAN §2.5, P4).
        """
        blobs = self.storage.blobs
        size = len(content.encode("utf-8"))
        if blobs is None or size <= TURN_BLOB_THRESHOLD_BYTES:
            return content, envelope
        try:
            uri = await blobs.put(content.encode("utf-8"), mime_type="text/plain")
        except Exception:  # noqa: BLE001 - blobstore is a lane, not authority
            return content, envelope
        offloaded = {**envelope, "blob_uri": uri, "content_bytes": size}
        return content[:TURN_PREVIEW_CHARS], offloaded

    async def _persist_turns(
        self, session: Session, message: Message, result: AgentResult, *,
        ordinary_context=None, ordinary_agent=None,
    ) -> Turn:
        """Append the user and assistant turns with their full envelopes.

        This is the only authority write point of the online path (P1): the
        envelopes make the stored rows lossless (P2), oversized bodies go to
        the blobstore (P4 fail-open), and the ``message.persisted`` event
        publishes the stored turn ids/seqs so derived lanes and observers can
        react without re-reading the session store.
        """
        user_body, user_envelope = await self._offload_body(message.content, turn_envelope(message))
        assistant_body, assistant_envelope_dict = await self._offload_body(
            result.content, assistant_envelope(message, result.metadata)
        )
        user_turn = Turn(session.id, "user", user_body, metadata=user_envelope)
        assistant_turn = Turn(
            session.id, "assistant", assistant_body, metadata=assistant_envelope_dict
        )
        await self._session_store.append_turn(user_turn)
        from copy import deepcopy

        self._original_user_turns[(session.id, user_turn.id)] = deepcopy((
            user_turn.content, dict(user_turn.metadata),
        ))
        if message.metadata.get(WORKSPACE_CONSENT_KEY) is True:
            # Only this freshly persisted server-held turn can be issued. Old
            # history is never scanned to create eligibility. A crash between
            # append and issue leaves an unclaimable turn, not an inferred grant.
            try:
                consent_store = self._workspace_consent_store()
            except LookupError:
                consent_store = None  # Ordinary unsupported conversations still persist.
            if consent_store is not None:
                if await consent_store.issue_workspace_consent(session, user_turn) is not True:
                    raise LookupError("Could not issue durable workspace consent")
        await Runtime._persist_attachments(self, user_turn, message)
        await self._session_store.append_turn(assistant_turn)
        if self._ordinary_response_has_source(ordinary_context, ordinary_agent, result):
            self._ordinary_assistant_turns[(session.id, assistant_turn.id)] = deepcopy((
                assistant_turn.content, dict(assistant_turn.metadata),
            ))
        stored = list(await self._session_store.recent_turns(session.id, limit=2))
        payload: dict = {"session_id": session.id}
        if len(stored) == 2:
            payload["turn_ids"] = [stored[0].id, stored[1].id]
            payload["seqs"] = [stored[0].seq, stored[1].seq]
        else:  # fallback when a backend cannot read back
            payload["turn_ids"] = [user_turn.id, assistant_turn.id]
            payload["seqs"] = [user_turn.seq, assistant_turn.seq]
        await self.events.publish(Event(MESSAGE_PERSISTED, payload, message.id))
        return user_turn

    @staticmethod
    async def _persist_attachments(runtime: Runtime, turn: Turn, message: Message) -> None:
        """Persist ``message.metadata["attachments"]`` against the user turn.

        This is the fail-open attachment lane: a malformed entry is skipped, and
        no failure here may break the online turn write. Each entry must be a
        mapping; anything else (strings, numbers) is ignored.
        """
        attachments = (message.metadata or {}).get("attachments")
        if not isinstance(attachments, list):
            return
        for entry in attachments:
            if not isinstance(entry, Mapping):
                continue
            try:
                attachment = Attachment(
                    id=str(entry.get("id") or uuid4()),
                    filename=str(entry.get("filename") or ""),
                    mime_type=str(entry.get("mime_type") or "application/octet-stream"),
                    size=int(entry.get("size") or 0),
                    uri=entry.get("uri") if isinstance(entry.get("uri"), str) else None,
                )
                await runtime._session_store.save_attachment(
                    attachment,
                    message_id=turn.id,
                    kind=str(entry.get("kind") or ""),
                    content_hash=str(entry.get("content_hash") or ""),
                    scan_status=str(entry.get("scan_status") or "unscanned"),
                    metadata=entry.get("metadata")
                    if isinstance(entry.get("metadata"), Mapping)
                    else None,
                )
            except Exception as exc:  # noqa: BLE001 - the attachment lane is fail-open
                # Still fail-open, but never silent: iterating over the rest is
                # what hid a missing store method behind a green test suite.
                logger.warning(
                    "attachment lane skipped an entry: %s: %s",
                    type(exc).__name__,
                    exc,
                )
                continue

    async def _persist_assistant_turn(
        self,
        session: Session,
        message: Message,
        result: AgentResult,
    ) -> None:
        """Append only the resumed assistant result for an approved parked turn."""
        assistant_body, assistant_envelope_dict = await self._offload_body(
            result.content, assistant_envelope(message, result.metadata)
        )
        assistant_turn = Turn(
            session.id, "assistant", assistant_body, metadata=assistant_envelope_dict
        )
        await self._session_store.append_turn(assistant_turn)
        stored = list(await self._session_store.recent_turns(session.id, limit=1))
        payload: dict = {"session_id": session.id}
        if stored:
            payload["turn_ids"] = [stored[0].id]
            payload["seqs"] = [stored[0].seq]
        else:
            payload["turn_ids"] = [assistant_turn.id]
            payload["seqs"] = [assistant_turn.seq]
        await self.events.publish(Event(MESSAGE_PERSISTED, payload, message.id))

    # -- public application services (used by CLI, MCP, Web API) -------------
    def describe(self) -> RuntimeInfo:
        return RuntimeInfo(
            name="SEAM_Sprout",
            version=_package_version(),
            default_agent=self._default_agent,
            agents=tuple(self.agents.list(enabled_only=False)),
            tools=tuple(self.tools.list()),
            skills=tuple(self.skills.list()),
            model_providers=tuple(self.models.list()),
            default_model=self.models.default_name,
            storage={
                "sessions": type(self._session_store).__name__,
                "operational": type(self.storage.operational).__name__,
                "knowledge": type(self.storage.knowledge).__name__,
                "metadata": (
                    type(self.storage.metadata).__name__
                    if self.storage.metadata is not None
                    else "disabled"
                ),
                "observations": (
                    type(self.storage.observations).__name__
                    if self.storage.observations is not None
                    else "disabled"
                ),
                "vectors": (
                    type(self.storage.vectors).__name__
                    if self.storage.vectors is not None
                    else "disabled"
                ),
                "cache": (
                    type(self.storage.cache).__name__
                    if self.storage.cache is not None
                    else "disabled"
                ),
                "blobs": (
                    type(self.storage.blobs).__name__
                    if self.storage.blobs is not None
                    else "disabled"
                ),
            },
        )

    async def create_session(
        self, user_id: str, *, session_id: str | None = None, principal: Principal | None = None,
    ) -> Session:
        if principal is not None:
            if principal.user_id != user_id or not principal.source:
                raise PermissionError("Session creation identity unavailable")
            session = await self._sessions.resolve(
                Message("", session_id=session_id), principal=principal,
            )
        else:
            # Preserve the trusted embedding API; never upgrade old records.
            session = await self._sessions.create(user_id, session_id=session_id)
        await self.events.publish(Event(SESSION_CREATED, {"session_id": session.id}, session.id))
        return session

    async def history(
        self, session_id: str, limit: int = 50, *, principal: Principal | None = None
    ) -> list[Turn]:
        """Trusted embedding read; adapters must supply their resolved principal.

        Without a principal this retains the privileged in-process inspection
        API. It must not be exposed as a remote identity-free history endpoint.
        """
        if principal is not None:
            await self._sessions.require_owner(session_id, principal)
        return list(await self._session_store.recent_turns(session_id, limit=limit))

    async def get_session(self, session_id: str) -> Session | None:
        """Look up one session so a caller can be checked against its owner."""
        return await self._session_store.get_session(session_id)

    # -- SEMA project/task services -------------------------------------
    async def open_workspace(
        self,
        root: str | Path,
        *,
        workspace_id: str | None = None,
    ) -> Workspace:
        """Register a local or Git workspace in the metadata store."""
        return await self._workspaces.open(root, workspace_id=workspace_id)

    async def get_workspace(self, workspace_id: str) -> Workspace | None:
        return await self._workspaces.get(workspace_id)

    async def list_workspaces(self) -> list[Workspace]:
        return await self._workspaces.list()

    async def scan_workspace(self, workspace_id: str) -> WorkspaceManifest:
        return await self._workspaces.scan(workspace_id)

    async def analyze_workspace(
        self, workspace_id: str, *, principal: Principal | None = None,
        analysis_task: Task | None = None,
    ) -> WorkspaceAnalysis:
        return await self._analyze_host_task(
            workspace_id, principal=principal, analysis_task=analysis_task)

    async def _analyze_host_task(
        self, workspace_id, *, principal, analysis_task, project=None, refine=None,
    ):
        """Fresh host analysis through the existing owned-node/Broker/View lane.

        Task is a reference, never a credential. Neither cached snapshots nor
        model-supplied paths participate in this host admission.
        """
        from Sprout.orchestration.service import SequentialOrchestrator
        from Sprout.security.policy import SecurityPolicy
        from Sprout.tools.executor import ToolExecutor

        if (not isinstance(principal, Principal) or principal.authenticated is not True
                or not isinstance(analysis_task, Task)):
            raise PermissionError("authorized_workspace_analysis_required")
        store = self._metadata_or_raise()
        task = await store.get_task(analysis_task.id)
        workspace = await store.get_workspace(workspace_id)
        if (task is None or workspace is None or task.workspace_id != workspace_id
                or task.actor != principal or task.source != principal.source
                or task.execution_mode is not TaskExecutionMode.READ_ONLY
                or task.status not in {TaskStatus.CREATED, TaskStatus.READY}
                or task.delegation_scope.is_expired()
                or not task.delegation_scope.allowed_paths
                or await store.list_execution_nodes(task.id)):
            raise PermissionError("analysis_task_subject_unavailable")
        session_id = task.metadata.get("session_id", "")
        if session_id:
            owned = await self._sessions.require_owner(session_id, principal)
            if self._sessions.frozen_principal(owned) != principal:
                raise PermissionError("analysis_task_subject_unavailable")
        raw = task.metadata.get("required_paths")
        if raw:
            _, plan = self._known_read_only_plan(workspace, task)
            paths = tuple(resource.path for resource in plan.resources)
            target = str(workspace.root / paths[0])
        elif raw is not None:
            raise PermissionError("project_capture_selection_unavailable")
        else:
            # Discovery is an explicit host Task choice. DIRECTORY_LIST policy
            # and the platform Broker, not root existence, decide visibility.
            from Sprout.workspace.authorized_content import relative_label

            directory = task.metadata.get("analysis_directory")
            if not isinstance(directory, str):
                raise PermissionError("project_capture_selection_unavailable")
            directory = directory if directory == "." else relative_label(directory)
            paths = ()
            target = str(workspace.root / directory)
        node = ExecutionNode(task_id=task.id, type=NodeType.AGENT)
        admission = {"paths": paths, "analysis_result": None, "analysis_data": None,
                     "content_project": project, "projected_data": None}
        self._online_analysis_requests[node.id] = admission

        async def capture(running):
            with self._project_analysis_scope(task, workspace, running) as live:
                tool = await self._issue_project_analysis_tool(
                    paths=paths or None, discover=not paths)
                executor = ToolExecutor(
                    self.tools,
                    policy=self.security.tool_policy if self.security else SecurityPolicy(),
                    policy_engine=self._policy_engine,
                    approvals=self._approvals, audit=self.audit, events=self.events,
                    scoped={tool.spec.name: tool}, scoped_only=True,
                )
                result = await executor.execute(
                    tool.spec.name, {"path": target, "instruction": task.instruction},
                    task_id=task.id, session_id=session_id, source=task.source,
                    requested_by=principal.user_id,
                )
                if not result.ok or admission["analysis_data"] is None:
                    raise PermissionError("project_analysis_unavailable")
                if refine is not None:
                    from Sprout.security.access import AccessDecision, ActionType
                    from Sprout.tools.context_keys import _project_analysis_call
                    from Sprout.workspace.models import ResourceKind, ResourceRef

                    async def recheck():
                        await self._project_analysis_subject(live)
                        with _project_analysis_call(tool) as frame:
                            for receipt in live.resource_receipts:
                                request = await self._project_analysis_request(
                                    tool, {"path": str(workspace.root / receipt["path"])},
                                    frame, session_id)
                                await self._check_project_selection((frame, request, workspace))
                                request = replace(request, action=ActionType.FILE_READ,
                                    resource=ResourceRef(workspace.id, request.resource.path,
                                                         kind=ResourceKind(receipt["kind"])))
                                decision = self._policy_engine.decide(request)
                                await self._record_project_capture_policy(request, decision)
                                if (decision.decision is not AccessDecision.ALLOW
                                        or not request.scope.permits(
                                            ActionType.FILE_READ.value, request.resource.path)):
                                    raise PermissionError("preparation_publication_denied")

                    await recheck()
                    admission["projected_data"] = await refine(admission["projected_data"])
                    await recheck()
                return {"analysis_ok": True}

        try:
            graph = ExecutionGraph(task_id=task.id, nodes=(node,), entry_node_ids=(node.id,))
            result = await SequentialOrchestrator(store).execute(graph, handler=capture)
            current = await store.get_task(task.id)
            if (result.status is not TaskStatus.COMPLETED or current is None
                    or current.status is not TaskStatus.COMPLETED
                    or current.execution_mode is not TaskExecutionMode.READ_ONLY
                    or current.actor != principal or current.source != task.source
                    or current.workspace_id != workspace_id
                    or current.metadata.get("session_id", "") != session_id
                    or current.delegation_scope != task.delegation_scope
                    or current.delegation_scope.is_expired()
                    or await store.get_workspace(workspace_id) != workspace
                    or admission["analysis_data"] is None):
                raise PermissionError("analysis_publication_unavailable")
            if session_id:
                await self._sessions.require_owner(session_id, principal)
            return admission["analysis_data"] if project is None else admission["projected_data"]
        finally:
            self._online_analysis_requests.pop(node.id, None)

    async def _check_preparation_parent(self, expected, workspace, owner_node=None):
        current = await self._metadata_or_raise().get_task(expected.id)
        if (current is None or current.actor != expected.actor or current.source != expected.source
                or current.workspace_id != workspace.id
                or current.delegation_scope != expected.delegation_scope
                or current.delegation_scope.is_expired()
                or current.metadata.get("session_id") != expected.metadata.get("session_id")
                or current.status in {TaskStatus.CANCELLED, TaskStatus.ROLLED_BACK,
                                      TaskStatus.COMPLETED, TaskStatus.FAILED}):
            raise PermissionError("preparation_authority_changed")
        if owner_node is not None:
            node = await self._metadata_or_raise().get_execution_node(owner_node.id)
            guard = node.metadata.get("execution_guard", {}) if node else {}
            if (node is None or node.task_id != expected.id or node.type != owner_node.type
                    or node.status is not NodeStatus.RUNNING or guard.get("state") != "active"
                    or not guard.get("owner")
                    or guard.get("owner") != owner_node.metadata.get(
                        "execution_guard", {}).get("owner")):
                raise PermissionError("preparation_owner_changed")
        return current

    async def _capture_preparation(
        self, workspace, *, actor, source, scope, instruction, metadata, parent=None,
        project=None, refine=None, owner_node=None,
    ):
        """A new real read owner, finite authority, and no transferable grant.

        Existing plan_kind is a negative marker only. Missing live registration
        (including another worker/Runtime) refuses; neither it nor a callback
        grants access. Scope is revoked on exit; UNKNOWN owners are never reset.
        """
        from datetime import UTC, datetime, timedelta

        if (not isinstance(actor, Principal) or actor.authenticated is not True
                or not actor.user_id or not actor.source or source != actor.source
                or not isinstance(scope, DelegationScope) or not scope.allowed_paths
                or scope.is_expired()):
            raise PermissionError("preparation_subject_unavailable")
        if parent is not None:
            await self._check_preparation_parent(parent, workspace, owner_node)
        deadline = datetime.now(UTC) + timedelta(seconds=self._agent_timeout_seconds())
        child_scope = replace(scope, expires_at=min(scope.expires_at, deadline)
                              if scope.expires_at else deadline)
        child = await self.create_task(
            workspace.id, instruction, actor=actor, source=source,
            execution_mode=TaskExecutionMode.READ_ONLY, delegation_scope=child_scope,
            metadata={**metadata, "plan_kind": "r04_preparation_v1"},
        )
        self._preparation_tasks[child.id] = (child, parent, owner_node)
        try:
            result = await self._analyze_host_task(
                workspace.id, principal=actor, analysis_task=child, project=project, refine=refine)
            if parent is not None:
                await self._check_preparation_parent(parent, workspace, owner_node)
            return result
        finally:
            try:
                current = await self._metadata_or_raise().get_task(child.id)
                if current is not None:
                    now = datetime.now(UTC)
                    expiry = current.delegation_scope.expires_at
                    await self._metadata_or_raise().save_task(replace(
                        current, delegation_scope=replace(current.delegation_scope,
                            expires_at=min(expiry, now) if expiry else now)))
            finally:
                self._preparation_tasks.pop(child.id, None)

    async def _prepare_code_resources(self, task, workspace, *, owned_node=None):
        store = self._metadata_or_raise()
        authoritative = await store.get_task(task.id)
        if authoritative is not None:
            if authoritative.actor != task.actor or authoritative.workspace_id != workspace.id:
                raise PermissionError("preparation_subject_unavailable")
            task = authoritative
        elif (task.actor.authenticated is not True or not task.delegation_scope.allowed_paths
              or task.source != task.actor.source):
            raise PermissionError("preparation_subject_unavailable")
        else:
            await store.save_task(task)  # SDK Tasks may not yet have a durable row.
        from Sprout.orchestration.service import SequentialOrchestrator

        if any(SequentialOrchestrator._blocks_local_replay(node)
               for node in await store.list_execution_nodes(task.id)
               if owned_node is None or node.id != owned_node.id):
            raise PermissionError("code_preparation_unconfirmed")
        selection = {key: task.metadata[key] for key in
                     ("required_paths", "analysis_directory", "session_id") if key in task.metadata}
        if "required_paths" not in selection and "analysis_directory" not in selection:
            _, known = self._known_read_only_plan(workspace, task)
            selection["required_paths"] = [resource.path for resource in known.resources]
        analysis = await self._capture_preparation(
            replace(workspace, manifest=None), actor=task.actor, source=task.source,
            scope=task.delegation_scope, instruction=task.instruction,
            metadata=selection, parent=task,
            owner_node=owned_node,
        )
        return (replace(workspace, manifest=analysis.manifest),
                replace(analysis.read_plan, task_id=task.id, purpose=task.instruction))

    async def invalidate_workspace(self, workspace_id: str | None = None) -> None:
        await self._workspaces.invalidate_workspace(workspace_id)

    async def plan_task(self, task_id: str, *, principal: Principal | None = None) -> ReadPlan:
        store = self._metadata_or_raise()
        if not isinstance(principal, Principal) or principal.authenticated is not True:
            raise PermissionError("authorized_workspace_analysis_required")
        task = await store.get_task(task_id)
        if task is None:
            raise LookupError(f"Task not found: {task_id}")
        if (task.actor != principal or task.source != principal.source
                or task.status not in {TaskStatus.CREATED, TaskStatus.READY, TaskStatus.RUNNING}
                or task.delegation_scope.is_expired()
                or not task.delegation_scope.allowed_paths):
            raise PermissionError("analysis_task_subject_unavailable")
        reference = task
        if (task.execution_mode is not TaskExecutionMode.READ_ONLY
                or task.status is TaskStatus.RUNNING
                or await store.list_execution_nodes(task.id)):
            # Planning must not complete/reclaim a CODE or already-owned graph.
            # Use a separate read-only task with the EXACT existing authority.
            # No Scope widening, implicit directory discovery or disk scanner.
            workspace = await store.get_workspace(task.workspace_id)
            if workspace is None:
                raise PermissionError("analysis_workspace_unavailable")
            _, selected = self._known_read_only_plan(workspace, task)
            reference = await self.create_task(
                task.workspace_id, task.instruction, actor=principal, source=task.source,
                execution_mode=TaskExecutionMode.READ_ONLY,
                delegation_scope=task.delegation_scope,
                metadata={"required_paths": [r.path for r in selected.resources],
                          **({"session_id": task.metadata["session_id"]}
                             if task.metadata.get("session_id") else {})},
            )
        plan = (await self.analyze_workspace(
            task.workspace_id, principal=principal, analysis_task=reference)).read_plan
        current = await store.get_task(task_id)
        if (current is None or current.actor != task.actor or current.source != task.source
                or current.workspace_id != task.workspace_id
                or current.delegation_scope != task.delegation_scope
                or current.delegation_scope.is_expired()
                or current.metadata.get("session_id") != task.metadata.get("session_id")
                or current.status not in {TaskStatus.CREATED, TaskStatus.READY,
                                          TaskStatus.RUNNING, TaskStatus.COMPLETED}):
            raise PermissionError("analysis_publication_unavailable")
        return replace(plan, task_id=task_id)

    async def create_task(
        self,
        workspace_id: str,
        instruction: str,
        *,
        actor: Principal | None = None,
        source: str = "cli",
        delegation_scope: DelegationScope | None = None,
        budget: TaskBudget | None = None,
        metadata: dict[str, object] | None = None,
        execution_mode: str = TaskExecutionMode.CODE,
    ) -> Task:
        """Create and persist a task without starting orchestration yet."""
        store = self.storage.metadata
        if store is None:
            raise RuntimeError("MetadataStore is not configured")
        workspace = await store.get_workspace(workspace_id)
        if workspace is None:
            raise LookupError(f"Workspace not found: {workspace_id}")
        task_metadata = execution_metadata(metadata or {}, execution_mode)
        if execution_mode == TaskExecutionMode.CODE and not (workspace.root / ".git").exists():
            raise ValueError(
                "Coding tasks require a Git repository. Initialize Git yourself first; "
                "Sprout will not initialize or modify repository state automatically."
            )
        task = Task(
            workspace_id=workspace_id,
            instruction=instruction,
            actor=actor or Principal(user_id="cli-user"),
            source=source,
            delegation_scope=delegation_scope or DelegationScope(),
            budget=budget or TaskBudget(),
            metadata=task_metadata,
        )
        await store.save_task(task)
        return task

    async def confirm_task_mode(self, task_id: str, execution_mode: str) -> Task:
        """Confirm an untouched historical task, never reinterpret executed work."""
        store = self.storage.metadata
        if store is None:
            raise RuntimeError("MetadataStore is not configured")
        task = await store.get_task(task_id)
        if task is None:
            raise LookupError(f"Task not found: {task_id}")
        if task.execution_mode != TaskExecutionMode.UNCONFIRMED:
            raise ValueError("Task mode is already confirmed")
        if (
            task.status != TaskStatus.CREATED
            or await store.list_execution_nodes(task_id)
            or await store.list_change_proposals(task_id)
        ):
            raise ValueError("Historical task has execution evidence; create a new typed task")
        task_metadata = execution_metadata(task.metadata, execution_mode)
        workspace = await store.get_workspace(task.workspace_id)
        if workspace is None:
            raise LookupError(f"Workspace not found: {task.workspace_id}")
        if execution_mode == TaskExecutionMode.CODE and not (workspace.root / ".git").exists():
            raise ValueError("Coding tasks require Git; initialize Git yourself first")
        confirmed = replace(task, metadata=task_metadata)
        await store.save_task(confirmed)
        await self.events.publish(
            Event(
                TASK_MODE_CONFIRMED,
                {"task_id": task_id, "execution_mode": confirmed.execution_mode.value},
                task_id,
            )
        )
        return confirmed

    async def create_task_from_strategy(
        self,
        workspace_id: str,
        requirement: str,
        *,
        actor: Principal | None = None,
        source: str = "cli",
        delegation_scope: DelegationScope | None = None,
        language: str = "",
        use_model_planner: bool = False,
        preparation_paths: tuple[str, ...] | None = None,
    ) -> Task:
        """Plan a requirement with the strategy layer, then materialize a task.

        ``use_model_planner`` opts into the LLM-backed planner, which produces
        requirement-specific structured steps. It is off by default because it
        spends a model call: without a configured provider it would either fail
        or silently degrade, and callers that only want the cheap local plan
        should not pay for it.
        """
        workspace = await self.get_workspace(workspace_id)
        if workspace is None:
            raise LookupError(f"Workspace not found: {workspace_id}")

        from Sprout.strategy.execution import ExecutionPlanner
        from Sprout.strategy.pipeline import StrategyPipeline

        pipeline = StrategyPipeline()
        if (not isinstance(actor, Principal) or actor.authenticated is not True
                or not isinstance(delegation_scope, DelegationScope)
                or not delegation_scope.allowed_paths):
            raise PermissionError("preparation_subject_unavailable")
        # A finite host Scope is an existing known-resource selector, not an
        # implicit permission to list a directory. Actual opens remain Brokered.
        paths = (delegation_scope.allowed_paths if preparation_paths is None
                 else preparation_paths)
        if (type(paths) not in {tuple, list} or not paths
                or any(type(path) is not str for path in paths)):
            raise PermissionError("preparation_inputs_unavailable")
        paths = tuple(paths)
        selection = {"required_paths": list(paths)}

        def project(analysis, view):
            return pipeline.plan_view(analysis, view, requirement, language=language)

        async def refine(plan):
            return await pipeline.model_planner.refine(plan, self.models.default())

        plan = await self._capture_preparation(
            replace(workspace, manifest=None), actor=actor, source=source,
            scope=delegation_scope, instruction=requirement, metadata=selection,
            project=project, refine=refine if use_model_planner else None,
        )
        spec = ExecutionPlanner().plan_to_task_spec(plan)
        return await self.create_task(
            workspace_id,
            spec.instruction,
            actor=actor,
            source=source,
            delegation_scope=delegation_scope,
            budget=spec.budget,
            metadata={
                "tool_names": list(spec.tools),
                "plan_kind": "model" if use_model_planner else "local",
                **({"response_language": language} if language else {}),
                **spec.metadata,
                # Planner output and future write authority cannot add READ inputs.
                "required_paths": list(paths),
            },
        )

    async def create_task_planned(
        self,
        workspace_id: str,
        instruction: str,
        *,
        actor: Principal | None = None,
        source: str = "cli",
        delegation_scope: DelegationScope | None = None,
        language: str = "",
        use_model_planner: bool = False,
        execution_mode: str = TaskExecutionMode.CODE,
        preparation_paths: tuple[str, ...] | None = None,
    ) -> Task:
        """Create a task through strategy planning, falling back if it fails.

        This is the path real entry points use. Planning is best-effort by
        design: a workspace that is not a Git repo, a missing model, or a
        planner bug must degrade to a plain task rather than lose the user's
        request — the bridge adds decomposition, it is not a precondition for
        creating work.

        ``use_model_planner`` is off by default, on measured evidence rather
        than caution. An A/B on one real requirement found the model planner
        costs ~55s more per task and does not make the edit round faster or
        better:

            with decomposition:    plan 10.9s + 5xSUBTASK 47.6s + agent 19.6s = 78.5s
            without:               plan  6.4s +           -    + agent 16.4s = 23.0s

        The agent took essentially the same time either way, so the
        exploration was not buying anything the agent did not already do —
        both nodes read the same READ-node resources. Enabling this also
        emits SUBTASK nodes, making it the slower path even if those were
        later parallelised: the ceiling for any subtask optimisation is the
        no-decomposition baseline.

        Turn it on deliberately (it needs a real provider) when investigating
        whether decomposition helps a *complex*, multi-file requirement. The
        capability is kept, not deleted — it is just not the default.
        """
        workspace = await self.get_workspace(workspace_id)
        if workspace is None:
            raise LookupError(f"Workspace not found: {workspace_id}")
        execution_metadata({}, execution_mode)
        if (preparation_paths is not None
                and (type(preparation_paths) not in {tuple, list} or not preparation_paths
                     or any(type(path) is not str for path in preparation_paths))):
            raise PermissionError("preparation_inputs_unavailable")
        if preparation_paths is not None:
            preparation_paths = tuple(preparation_paths)
            # A selector is not authority, including the scheduling-only lane.
            if (not isinstance(actor, Principal) or actor.authenticated is not True
                    or not actor.user_id or not actor.source or source != actor.source
                    or not isinstance(delegation_scope, DelegationScope)
                    or not delegation_scope.allowed_paths or delegation_scope.is_expired()):
                raise PermissionError("preparation_subject_unavailable")
        if execution_mode == TaskExecutionMode.READ_ONLY or not (workspace.root / ".git").exists():
            return await self.create_task(
                workspace_id,
                instruction,
                actor=actor,
                source=source,
                execution_mode=execution_mode,
                delegation_scope=delegation_scope,
                metadata=({"required_paths": list(preparation_paths)}
                          if preparation_paths is not None else None),
            )
        try:
            return await self.create_task_from_strategy(
                workspace_id,
                instruction,
                actor=actor,
                source=source,
                language=language,
                use_model_planner=use_model_planner,
                delegation_scope=delegation_scope,
                preparation_paths=preparation_paths,
            )
        except PermissionError:
            # A denied resource preparation is not a planner bug. Never hide
            # it behind an unscoped plain-task fallback.
            raise
        except Exception as exc:  # noqa: BLE001 - planning must never block the task
            logger.warning(
                "strategy planning failed (%s: %s); creating a plain task",
                type(exc).__name__,
                exc,
            )
            return await self.create_task(
                workspace_id,
                instruction,
                actor=actor,
                source=source,
                delegation_scope=delegation_scope,
                metadata={
                    "plan_kind": "fallback",
                    "require_acceptance_tests": True,
                    **({"response_language": language} if language else {}),
                    **({"required_paths": list(preparation_paths)}
                       if preparation_paths is not None else {}),
                },
            )

    async def get_task(self, task_id: str) -> Task | None:
        metadata = self.storage.metadata
        if metadata is None:
            raise RuntimeError("MetadataStore is not configured")
        return await metadata.get_task(task_id)

    async def list_tasks(self, workspace_id: str | None = None) -> list[Task]:
        metadata = self.storage.metadata
        if metadata is None:
            raise RuntimeError("MetadataStore is not configured")
        return await metadata.list_tasks(workspace_id)

    async def get_task_submissions(self, task_id: str) -> list[dict[str, Any]]:
        """Inspect durable transport state without contacting the scheduler."""
        if await self.get_task(task_id) is None:
            raise LookupError(f"Task not found: {task_id}")
        return await self._metadata_or_raise().list_task_submissions(task_id)

    async def synchronize_task_result(self, expected, result):
        """Internal projection port; callers use authenticated TaskActions.sync."""
        writer = getattr(self._metadata_or_raise(), "synchronize_task_result", None)
        if writer is None:
            raise ValueError("Metadata store does not support atomic result synchronization")
        return await writer(expected, result)

    async def get_task_execution_report(self, task_id: str) -> dict[str, Any]:
        """Inspect node ownership without clearing it or contacting the worker."""
        task = await self.get_task(task_id)
        if task is None:
            raise LookupError(f"Task not found: {task_id}")
        nodes = []
        for node in await self._metadata_or_raise().list_execution_nodes(task_id):
            guard = node.metadata.get("execution_guard", {})
            state = guard.get("state", "unclaimed")
            if state == "active":
                state = "occupied_or_unconfirmed"
            elif not guard and node.status in {NodeStatus.RUNNING, NodeStatus.FAILED}:
                state = "legacy_unconfirmed"
            output = node.metadata.get("output")
            nodes.append(
                {
                    "node_id": node.id,
                    "type": node.type.value,
                    "status": node.status.value,
                    "attempts": node.attempts,
                    "execution_state": state,
                    "owner": guard.get("owner"),
                    "started_at": guard.get("started_at"),
                    "settled_at": guard.get("settled_at"),
                    "reason": output.get("reason") if isinstance(output, dict) else None,
                    "error": node.metadata.get("error"),
                    "budget_stopped": any(kind in str(node.metadata.get("error") or "")
                        for kind in ("ModelBudgetExhausted", "LiveRequestLimit")),
                }
            )
        return {"version": 1, "task_id": task_id, "status": task.status.value, "nodes": nodes}

    async def get_task_budget_report(self, task_id: str) -> dict[str, Any]:
        """Read durable allowance evidence; unsupported stores are not zero usage."""
        from Sprout.task.reporting import budget_projection

        task = await self.get_task(task_id)
        if task is None:
            raise LookupError(f"Task not found: {task_id}")
        reader = getattr(self._metadata_or_raise(), "model_budget_report", None)
        if reader is None:
            return budget_projection(None, task.budget.max_model_calls)
        return await reader(task_id, task.budget.max_model_calls)

    async def get_verification_report(self, task_id: str) -> dict[str, object]:
        """Read existing evidence without executing checks or trusting cached reports."""
        from Sprout.execution.verification import (
            build_verification_report,
            proposal_verification_report,
        )

        task = await self.get_task(task_id)
        if task is None:
            raise LookupError(f"Task not found: {task_id}")
        proposals = await self.list_change_proposals(task_id)
        proposal = max(proposals, key=lambda item: item.created_at) if proposals else None
        node_id = None
        if proposal is not None:
            report = proposal_verification_report(proposal)
            verification = proposal.metadata.get("verification")
        else:
            nodes = await self.storage.metadata.list_execution_nodes(task_id)
            evaluation = None
            for node in reversed(nodes):
                output = node.metadata.get("output")
                if (
                    node.type is NodeType.EVALUATION
                    and isinstance(output, dict)
                    and not output.get("skipped")
                ):
                    evaluation = output
                    node_id = node.id
                    break
            report = build_verification_report(
                NodeExecutor._test_results_from(evaluation),
                evaluation,
            )
            verification = evaluation
        payload = {
            "task_id": task_id,
            "task_status": task.status.value,
            "proposal_id": proposal.id if proposal else None,
            "evaluation_node_id": node_id,
            **report.to_payload(),
        }
        local_context = (not isinstance(self._orchestrator_backend, TemporalOrchestratorBackend)
                         and not getattr(self, "_isolated_coding_required", False))
        from Sprout.execution.acceptance import verification_evidence_applicability

        evidence_state = verification_evidence_applicability(
            verification, local_execution_available=local_context)
        # Preserve existing public match/mismatch/unknown values; evidence
        # without a proven origin must be projected even on non-Local runtimes.
        applicability = evidence_state.removeprefix("local_")
        payload["historical_verification"] = report.to_payload()
        payload["current_applicability"] = applicability
        if evidence_state != "local_match":
            payload.update(verified=False, status="incomplete",
                           blocker="Verification evidence executor " + evidence_state,
                           failure_codes=list(dict.fromkeys(
                               [*report.failure_codes, "local_executor_" + applicability])))
        return payload

    async def read_trajectory(self, task_id: str) -> list[TrajectoryEvent]:
        recorder = JsonlTrajectoryRecorder(trajectory_path(self._trajectory_dir, task_id))
        return await recorder.read_all()

    async def run_task_process(
        self,
        task_id: str,
        command: tuple[str, ...],
        *,
        timeout_seconds: float | None = None,
        max_output_bytes: int | None = None,
    ) -> ProcessResult:
        task = await self.get_task(task_id)
        if task is None:
            raise LookupError(f"Task not found: {task_id}")
        workspace = await self.get_workspace(task.workspace_id)
        if workspace is None:
            raise LookupError(f"Workspace not found: {task.workspace_id}")
        return await self._process_broker.run(
            workspace,
            command,
            cwd=workspace.root,
            task_id=task_id,
            timeout_seconds=timeout_seconds,
            **({"max_output_bytes": max_output_bytes} if max_output_bytes is not None else {}),
            scope=task.delegation_scope,
            source=task.source,
            requested_by=task.actor.user_id,
            actor=task.actor,
            resource_scope=workspace.id,
        )

    async def authorize_task_process(self, task_id: str, command: tuple[str, ...]) -> ProcessResult:
        """Broker-only preflight, never a reusable execution authorization token."""
        task = await self.get_task(task_id)
        if task is None:
            raise LookupError("Task not found")
        workspace = await self.get_workspace(task.workspace_id)
        if workspace is None:
            raise LookupError("Workspace not found")
        return await self._process_broker.run(
            workspace, command, cwd=workspace.root, task_id=task_id,
            scope=task.delegation_scope, source=task.source,
            requested_by=task.actor.user_id, actor=task.actor, resource_scope=workspace.id,
            preflight_only=True)

    async def get_change_proposal(self, proposal_id: str) -> ChangeProposal | None:
        return await self._changes().get(proposal_id)

    async def list_change_proposals(self, task_id: str) -> list[ChangeProposal]:
        return await self._changes().list_for_task(task_id)

    async def find_change_proposal(self, prefix: str) -> ChangeProposal | None:
        return await self._changes().find(prefix)

    async def request_change_approval(self, proposal_id: str) -> ChangeProposal:
        return await self._changes().request_approval(proposal_id)

    async def approve_change_proposal(
        self,
        proposal_id: str,
        *,
        decided_by: str = "cli",
    ) -> ChangeProposal:
        proposal = await self._changes().approve(proposal_id, decided_by=decided_by)
        await self._resume_task_after_approval(proposal.task_id)
        return proposal

    async def reject_change_proposal(
        self,
        proposal_id: str,
        *,
        reason: str = "",
    ) -> ChangeProposal:
        proposal = await self._changes().reject(proposal_id, reason=reason)
        await self._sandbox_lifecycle.cleanup_proposal(proposal)
        await self._cancel_task_after_rejection(proposal.task_id)
        return proposal

    async def _cancel_task_after_rejection(self, task_id: str) -> None:
        """Terminate a WAITING_APPROVAL task once its only proposal is rejected."""
        metadata = self.storage.metadata
        if metadata is None:
            return
        task = await metadata.get_task(task_id)
        if task is None or task.status is not TaskStatus.WAITING_APPROVAL:
            return
        TaskStateMachine.validate(task.status, TaskStatus.CANCELLED)
        await metadata.update_task_status(task_id, TaskStatus.CANCELLED)

    async def cancel_task(self, task_id: str, *, reason: str = "") -> Task:
        """Stop a task that is waiting on, or running for, a human.

        There was no way to do this from the CLI at all: approvals could be
        decided and proposals rejected, but a task parked on either gate had no
        exit, and the only cancellation in the codebase was the private
        ``_cancel_task_after_rejection`` above. Eight tasks had accumulated in
        ``waiting_approval`` with no supported way to retire them.

        Deliberately a status change, not a delete: the execution nodes,
        change proposals and audit records all cite this task id, and dropping
        the row would orphan them. ``CANCELLED`` says "decided against", which
        is the truth, where a missing row would say "never happened".

        The state machine is the contract, not a hand-kept list: whatever
        ``TaskStateMachine`` permits is allowed and whatever it forbids is
        refused with a ``ValueError``. That leaves ``FAILED`` cancellable —
        it is settled, not terminal, since the machine gives it an exit
        (``RETRYING``) — and refuses ``COMPLETED``, ``CANCELLED`` and
        ``ROLLED_BACK``, which have no way out. Overwriting one of those with
        ``CANCELLED`` would rewrite what actually happened.
        """
        metadata = self.storage.metadata
        if metadata is None:
            raise RuntimeError("MetadataStore is not configured")
        task = await metadata.get_task(task_id)
        if task is None:
            raise LookupError(f"Task not found: {task_id}")
        if not TaskStateMachine.can_transition(task.status, TaskStatus.CANCELLED):
            raise ValueError(f"Task {task_id} is {task.status.value}; it cannot be cancelled")
        await metadata.update_task_status(task_id, TaskStatus.CANCELLED)
        self._record_security(
            "task.cancelled",
            {"task_id": task_id, "from": task.status.value, "reason": reason},
        )
        cancelled = await metadata.get_task(task_id)
        return cancelled if cancelled is not None else task

    async def resolve_orphaned_approvals(self, task_id: str) -> int:
        """Clear a cancelled task's still-PENDING approvals; returns how many.

        Cancelling the task is not enough on its own: the approval record stays
        ``pending``, so anything listing open questions keeps offering one about
        a task that can no longer act on the answer — the operator is asked to
        decide something with no effect. Called alongside :meth:`cancel_task` so
        the two cannot drift.

        A rejection rather than a silent delete: ``decide`` writes the audit
        record and settles the state machine, and the record stays readable as
        the explanation for why the task stopped.
        """
        cleared = 0
        for record in await self.pending_approvals():
            if getattr(record, "task_id", "") != task_id:
                continue
            await self._approvals.decide(
                record.id,
                False,
                decided_by="system",
                reason="Task was cancelled",
                channel="cli",
            )
            cleared += 1
        return cleared

    async def apply_change_proposal(
        self,
        proposal_id: str,
        *,
        allow_failing_tests: bool = False,
        force: bool = False,
        override_reason: str = "",
        decided_by: str = "cli",
    ) -> ApplyResult:
        result = await self._changes().apply(
            proposal_id,
            allow_failing_tests=allow_failing_tests,
            force=force,
            override_reason=override_reason,
            decided_by=decided_by,
        )
        if result.applied:
            proposal = await self._changes().get(proposal_id)
            if proposal is not None:
                await self._sandbox_lifecycle.cleanup_proposal(proposal)
        return result

    async def rollback_change_proposal(
        self,
        proposal_id: str,
        *,
        decided_by: str = "cli",
    ) -> ApplyResult:
        # ``decided_by`` is kept for parity with approve; rollback reuses the
        # grant that was created when the proposal was approved.
        result = await self._changes().rollback(proposal_id)
        if result.applied:
            proposal = await self._changes().get(proposal_id)
            if proposal is not None:
                await self._sandbox_lifecycle.cleanup_proposal(proposal)
        return result

    async def execute(
        self,
        task: Task,
        *,
        artifact_snapshot: ArtifactSnapshot | None = None,
        replay_resume_owner: str | None = None,
    ) -> TaskResult:
        async with self._workspace_locks.lock(task.workspace_id):
            authoritative = await self._metadata_or_raise().get_task(task.id)
            if (authoritative is not None
                    and authoritative.status == TaskStatus.WAITING_RESOURCE
                    and authoritative.metadata.get("replay_stage") == "image_preflight"):
                claim = await self._metadata_or_raise().get_replay_resume(task.id)
                if (not claim or claim["state"] != "active"
                        or claim["owner"] != replay_resume_owner
                        or claim.get("requested_by") != authoritative.actor.user_id
                        or claim.get("plan_fingerprint") != authoritative.metadata.get(
                            "replay_plan", {}).get("fingerprint")):
                    raise ValueError("Replay preflight waits require the dedicated recovery entry")
                task = authoritative
            return await self._execute_task(
                task,
                artifact_snapshot=artifact_snapshot,
            )

    async def submit_persisted_task(self, task_id: str):
        """Fresh submission only, using a deployment-configured Temporal route.

        Existing histories are observation-only, never recovery authorization.
        Isolated replay experiments retain their dedicated admission service.
        """
        from dataclasses import asdict

        metadata = self._metadata_or_raise()
        task = await metadata.get_task(task_id)
        if task is None:
            raise LookupError(f"Task not found: {task_id}")
        async with self._workspace_locks.lock(task.workspace_id):
            task = await metadata.get_task(task_id)
            if task is None:
                raise LookupError(f"Task not found: {task_id}")
            records = await metadata.list_task_submissions(task_id)
            if records:
                return {"task_id": task_id, "status": task.status.value,
                        "new_submission": False, "worker_started": False,
                        "submission_ids": [record["id"] for record in records],
                        "next_action": "Observe existing submissions; no automatic retry"}
            if task.metadata.get("replay_plan"):
                raise ValueError("Isolated experiments require dedicated coding-submit admission")
            if task.status not in {TaskStatus.CREATED, TaskStatus.READY}:
                raise ValueError("Fresh created/ready task required; not a recovery entry")
            if await metadata.list_execution_nodes(task_id):
                raise ValueError("Existing nodes require observation, not fresh submission")
            if not isinstance(self._orchestrator_backend, TemporalOrchestratorBackend):
                raise ValueError("Explicit deployment Temporal backend required; no local fallback")
            backend = self._orchestrator_backend.submission_only(metadata=metadata)
            result = await self._execute_task(task, artifact_snapshot=None, backend=backend)
            submitted = await metadata.list_task_submissions(task_id)
            return {**asdict(result), "new_submission": bool(submitted), "worker_started": False,
                    "result_waited": False}

    async def retry_pending_submission(self, task_id: str, *, submission_id: str):
        """Explicit retry of a known connection failure, never an execution retry."""
        from dataclasses import asdict

        from Sprout.orchestration.models import ExecutionGraph
        from Sprout.orchestration.terminal.temporal import TemporalConfig

        metadata = self._metadata_or_raise()
        task = await metadata.get_task(task_id)
        if task is None:
            raise LookupError(f"Task not found: {task_id}")
        async with self._workspace_locks.lock(task.workspace_id):
            task = await metadata.get_task(task_id)
            records = await metadata.list_task_submissions(task_id)
            if len(records) != 1 or records[0]["id"] != submission_id:
                raise ValueError("One exact original submission required")
            record = records[0]
            if (task is None or task.status != TaskStatus.WAITING_RESOURCE
                    or task.metadata.get("replay_plan")):
                raise ValueError("Ordinary task waiting for scheduling required")
            if (record.get("state") != "pending" or record.get("phase") != "connect"
                    or record.get("accepted") or record.get("run_id")):
                raise ValueError("Only known pre-submission connection failures may be retried")
            if (record.get("task_id") != task_id
                    or record.get("payload", {}).get("task_id") != task_id
                    or not all(isinstance(record.get(key), str) and record[key].strip()
                               for key in ("host", "namespace", "task_queue"))):
                raise ValueError("Original submission payload and route required")
            if await metadata.list_execution_nodes(task_id):
                raise ValueError("Existing execution evidence requires observation, not retry")
            backend = TemporalOrchestratorBackend(TemporalConfig(
                host=record["host"], namespace=record["namespace"],
                task_queue=record["task_queue"]), metadata=metadata, wait_for_result=False)
            # Backend selects the existing record and its frozen payload, not a
            # newly compiled graph. REJECT_DUPLICATE preserves workflow identity.
            result = await backend.execute(ExecutionGraph(task_id=task_id))
            return {**asdict(result), "new_submission": False, "retry_requested": True,
                    "worker_started": False, "result_waited": False}

    async def submit_task(self, task: Task) -> None:
        """Queue a task for later execution by a worker."""
        await self._task_queue.put(task)

    async def run_task_worker(self) -> None:
        """Consume queued tasks and execute them serially per workspace."""
        while not self._worker_stop_requested:
            task = await self._task_queue.get()
            try:
                await self.execute(task)
            finally:
                self._task_queue.task_done()

    def start_worker(self) -> None:
        if self._worker_task is None or self._worker_task.done():
            self._worker_stop_requested = False
            self._worker_task = asyncio.create_task(
                self.run_task_worker(),
                name="sema-task-worker",
            )

    async def stop_worker(self) -> None:
        worker = self._worker_task
        if worker is not None:
            # Cancellation may be consumed by an execution adapter. The explicit
            # flag still prevents consuming the next queued task in that case.
            if not self._worker_stop_requested:
                self._worker_stop_requested = True
                worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass
            finally:
                if self._worker_task is worker:
                    self._worker_task = None

    @staticmethod
    def _task_for_execution(task: Task, workspace: Workspace) -> Task:
        """Enforce the same admission contract on execution, resume and workers."""
        if task.execution_mode == TaskExecutionMode.UNCONFIRMED:
            raise ValueError(
                "Historical task execution mode is unconfirmed; confirm mode or create a new task"
            )
        if task.execution_mode == TaskExecutionMode.CODE and not (workspace.root / ".git").exists():
            raise ValueError("Coding tasks require Git; initialize Git yourself first")
        return replace(task, metadata=execution_metadata(task.metadata, task.execution_mode))

    async def _isolated_execution_wait(self, task: Task) -> TaskResult | None:
        """Apply the same fail-closed admission to initial and resumed execution.

        A callable dispatcher is only a necessary condition, not commissioning
        or write authority; node-level binding and validation remain mandatory.
        """
        if not getattr(self, "_isolated_coding_required", False):
            return None
        if callable(getattr(self, "_isolated_coding_dispatch", None)):
            return None
        metadata = self.storage.metadata
        if metadata is None:
            raise RuntimeError("MetadataStore is not configured")
        current = await metadata.get_task(task.id)
        if current and current.status in {
            TaskStatus.COMPLETED, TaskStatus.CANCELLED, TaskStatus.ROLLED_BACK
        }:
            return TaskResult(task_id=task.id, status=current.status)
        await metadata.update_task_status(task.id, TaskStatus.WAITING_RESOURCE)
        return TaskResult(task_id=task.id, status=TaskStatus.WAITING_RESOURCE,
                          error="Isolated coding backend not ready; no host fallback")

    async def _execute_task(
        self,
        task: Task,
        *,
        artifact_snapshot: ArtifactSnapshot | None,
        backend=None,
    ) -> TaskResult:
        """Run a task through the V0.1 project execution pipeline."""
        metadata = self.storage.metadata
        if metadata is None:
            raise RuntimeError("MetadataStore is not configured")
        isolated_wait = await self._isolated_execution_wait(task)
        if isolated_wait is not None:
            return isolated_wait
        execution_backend = backend or self._orchestrator_for(metadata)
        authoritative = await metadata.get_task(task.id)
        if authoritative is not None:
            if authoritative.actor != task.actor or authoritative.workspace_id != task.workspace_id:
                raise PermissionError("preparation_subject_unavailable")
            task = authoritative
            if isinstance(execution_backend, TemporalOrchestratorBackend):
                if task.status in {
                    TaskStatus.COMPLETED,
                    TaskStatus.CANCELLED,
                    TaskStatus.ROLLED_BACK,
                }:
                    return TaskResult(task_id=task.id, status=task.status)
        workspace = await metadata.get_workspace(task.workspace_id)
        if workspace is None:
            raise LookupError(f"Workspace not found: {task.workspace_id}")

        task = self._task_for_execution(task, workspace)

        if task.execution_mode is TaskExecutionMode.READ_ONLY:
            workspace, read_plan = self._known_read_only_plan(
                workspace, task,
                scheduling=isinstance(execution_backend, TemporalOrchestratorBackend),
            )
            manifest = workspace.manifest
        else:
            try:
                workspace, read_plan = await self._prepare_code_resources(task, workspace)
            except PermissionError as exc:
                if str(exc) != "code_preparation_unconfirmed":
                    raise
                return TaskResult(task_id=task.id, status=TaskStatus.WAITING_RESOURCE,
                                  error=str(exc))
            manifest = workspace.manifest
        if await metadata.get_task(task.id) is None:
            await metadata.save_task(task)

        tool_names = tuple(task.metadata.get("tool_names") or ())
        steps = tuple(task.metadata.get("steps") or ())
        verification_commands = tuple(task.metadata.get("verification_commands") or ())
        graph = self._graph_builder.build(
            task,
            read_plan,
            manifest,
            agent_tool_names=tool_names,
            verify_loops=self._verify_loops(),
            agent_max_attempts=self._agent_max_attempts(),
            agent_timeout_seconds=self._agent_timeout_seconds(),
            evaluation_timeout_seconds=self._evaluation_timeout_seconds(),
            evaluation_attempts=self._evaluation_attempts(),
            steps=steps,
            verification_commands=verification_commands,
        )
        graph = await self._restore_graph(graph)
        recorder = JsonlTrajectoryRecorder(trajectory_path(self._trajectory_dir, task.id))
        executor = self._executor(metadata)
        previous_skills = self.skills.list()
        pinned_skill = await executor.pin_snapshot(recorder, artifact_snapshot, task.id)
        try:
            result = await execution_backend.execute(
                graph,
                handler=executor.handler(task, workspace, read_plan),
                recorder=recorder,
                budget=task.budget,
            )
            await self._persist_node_products()
            await self._sandbox_lifecycle.finalize(task, result)
            return result
        finally:
            if pinned_skill is not None:
                previous = previous_skills.get(pinned_skill.name)
                if previous is None:
                    self.skills.unregister(pinned_skill.name)
                else:
                    self.skills.register(previous)

    async def resume_task(self, task: Task) -> TaskResult:
        """Continue a task parked in WAITING_APPROVAL after a human decision.

        Already completed nodes are skipped, so approval decisions never
        replay side effects (SEMA spec 19.2).
        """
        metadata = self.storage.metadata
        if metadata is None:
            raise RuntimeError("MetadataStore is not configured")
        authoritative = await metadata.get_task(task.id)
        if authoritative is None:
            raise LookupError(f"Task not found: {task.id}")
        # Callers may retain a pre-approval snapshot. Never compile a resumed
        # graph from its stale workspace, mode, actor, scope or acceptance data.
        task = authoritative
        if task.status in {
            TaskStatus.COMPLETED, TaskStatus.CANCELLED, TaskStatus.ROLLED_BACK
        }:
            return TaskResult(task_id=task.id, status=task.status)
        if (task.status == TaskStatus.WAITING_RESOURCE
                and task.metadata.get("replay_stage") == "image_preflight"):
            raise ValueError("Replay preflight waits require the dedicated recovery entry")
        isolated_wait = await self._isolated_execution_wait(task)
        if isolated_wait is not None:
            return isolated_wait
        workspace = await metadata.get_workspace(task.workspace_id)
        if workspace is None:
            raise LookupError(f"Workspace not found: {task.workspace_id}")
        task = self._task_for_execution(task, workspace)
        if task.execution_mode is TaskExecutionMode.READ_ONLY:
            workspace, read_plan = self._known_read_only_plan(workspace, task)
            manifest = workspace.manifest
        else:
            try:
                workspace, read_plan = await self._prepare_code_resources(task, workspace)
            except PermissionError as exc:
                if str(exc) != "code_preparation_unconfirmed":
                    raise
                return TaskResult(task_id=task.id, status=TaskStatus.WAITING_RESOURCE,
                                  error=str(exc))
            manifest = workspace.manifest
        tool_names = tuple(task.metadata.get("tool_names") or ())
        steps = tuple(task.metadata.get("steps") or ())
        verification_commands = tuple(task.metadata.get("verification_commands") or ())
        graph = self._graph_builder.build(
            task,
            read_plan,
            manifest,
            agent_tool_names=tool_names,
            verify_loops=self._verify_loops(),
            agent_max_attempts=self._agent_max_attempts(),
            agent_timeout_seconds=self._agent_timeout_seconds(),
            evaluation_timeout_seconds=self._evaluation_timeout_seconds(),
            evaluation_attempts=self._evaluation_attempts(),
            steps=steps,
            verification_commands=verification_commands,
        )
        graph = await self._restore_graph(graph)
        recorder = JsonlTrajectoryRecorder(trajectory_path(self._trajectory_dir, task.id))
        executor = self._executor(metadata)
        result = await self._orchestrator_for(metadata).execute(
            graph,
            handler=executor.handler(task, workspace, read_plan),
            recorder=recorder,
            budget=task.budget,
        )
        await self._persist_node_products()
        await self._sandbox_lifecycle.finalize(task, result)
        return result

    async def _restore_graph(self, graph: ExecutionGraph) -> ExecutionGraph:
        """Overlay persisted node state onto a freshly compiled graph.

        The Temporal backend does not run ``SequentialOrchestrator._restore``,
        so a resumed workflow needs the already-completed statuses here.
        """
        metadata = self._metadata_or_raise()
        graph = replace(
            graph,
            dispatch_key=(await self._execution_resume_key(graph.task_id)) or json.dumps([[], []]),
        )
        stored = {node.id: node for node in await metadata.list_execution_nodes(graph.task_id)}
        confirmed = []
        for record in await metadata.list_task_submissions(graph.task_id):
            result = record.get("result", {})
            previous = stored.get(result.get("node_id"))
            if result.get("status") == "waiting_resource" and previous is not None:
                if previous.status in {NodeStatus.COMPLETED, NodeStatus.SKIPPED}:
                    confirmed.append(record["id"])
        if confirmed:
            graph = replace(graph, dispatch_key=graph.dispatch_key + json.dumps(sorted(confirmed)))
        if not stored:
            return graph
        restored: list[ExecutionNode] = []
        for node in graph.nodes:
            previous = stored.get(node.id)
            if previous is None:
                restored.append(node)
                continue
            restored.append(
                replace(
                    node,
                    status=previous.status,
                    attempts=previous.attempts,
                    metadata=dict(previous.metadata),
                )
            )
        return replace(graph, nodes=tuple(restored))

    async def _execution_resume_key(self, task_id: str) -> str:
        """Only persisted decisions may resume a known approval wait."""
        metadata = self._metadata_or_raise()
        decisions = sorted(
            f"{record.id}:{record.decided_at}"
            for record in await self.list_approvals()
            if record.task_id == task_id and record.decided_at is not None
        )
        proposals = sorted(
            f"{proposal.id}:"
            f"{'approved' if proposal.status.value == 'applied' else proposal.status.value}"
            for proposal in await metadata.list_change_proposals(task_id)
            if proposal.status.value in {"approved", "applied", "rejected"}
        )
        return json.dumps([decisions, proposals]) if decisions or proposals else ""

    async def execute_node(self, node: ExecutionNode) -> dict[str, Any]:
        """Run one orchestration node on behalf of an external scheduler.

        Temporal activities run in a separate process from the submission path,
        so they need a narrow public entry point that prepares the workspace,
        read plan, executor, and trajectory recorder without reaching into
        Runtime private fields.
        """
        metadata = self._metadata_or_raise()
        task = await metadata.get_task(node.task_id)
        if task is None:
            raise LookupError(f"Task not found: {node.task_id}")
        if task.status in {TaskStatus.CANCELLED, TaskStatus.ROLLED_BACK}:
            return {
                "node_id": node.id,
                "type": node.type.value,
                "status": "cancelled",
                "output": {},
            }
        workspace = await metadata.get_workspace(task.workspace_id)
        if workspace is None:
            raise LookupError(f"Workspace not found: {task.workspace_id}")
        task = self._task_for_execution(task, workspace)
        persisted = await metadata.get_execution_node(node.id)
        if persisted is not None and persisted.task_id == task.id:
            if persisted.type != node.type:
                raise ValueError("Execution node type does not match persisted authority")
            if persisted.status == NodeStatus.COMPLETED:
                return {
                    "node_id": persisted.id,
                    "type": persisted.type.value,
                    "status": "completed",
                    "output": dict(persisted.metadata.get("output") or {}),
                }
            if persisted.status == NodeStatus.SKIPPED:
                return {"status": "skipped"}
        isolated_experiment = task.execution_mode == TaskExecutionMode.CODE and (
            "replay_plan" in task.metadata or "replay_source_task_id" in task.metadata
        )
        if isolated_experiment and (
            getattr(self, "_isolated_coding_required", False) is not True
            or not callable(getattr(self, "_isolated_coding_dispatch", None))
            or not callable(getattr(self, "_isolated_coding_session_factory", None))
        ):
            # Activities bypass submission admission and may run in another
            # process. Never infer host execution authority from its defaults.
            # Do not claim the node: no effect was attempted and a commissioned
            # worker may subsequently accept the same persisted task.
            return {
                "node_id": node.id,
                "type": node.type.value,
                "status": "waiting_resource",
                "output": {"waiting": True, "waiting_kind": "resource",
                           "waiting_reason": "isolated_worker_not_assembled",
                           "error": "Isolated worker assembly required; no host fallback"},
            }
        if task.execution_mode == TaskExecutionMode.READ_ONLY:
            if node.type not in {NodeType.READ, NodeType.AGENT}:
                raise ValueError("Read-only tasks cannot execute mutation or verification nodes")
            if node.type is NodeType.AGENT:
                node = replace(node, metadata={**node.metadata, "read_only": True})
        from Sprout.orchestration.execution_guard import NodeExecutionGuard

        recorder = None

        async def execute_owned(running: ExecutionNode):
            nonlocal recorder
            if task.execution_mode is TaskExecutionMode.READ_ONLY:
                prepared, read_plan = self._known_read_only_plan(workspace, task)
            else:
                prepared, read_plan = await self._prepare_code_resources(
                    task, workspace, owned_node=running)
            executor = self._executor(metadata)
            if isinstance(executor, NodeExecutor):
                # External scheduler activities retain their own executor;
                # never bind submission-host Python into a worker command.
                executor._local_acceptance = False
                executor._changes = self._changes(local_acceptance=False)
            recorder = JsonlTrajectoryRecorder(trajectory_path(self._trajectory_dir, task.id))
            await recorder.record(
                TrajectoryEvent(
                    name="node.started",
                    task_id=task.id,
                    payload={"node_id": running.id},
                )
            )
            if (
                task.execution_mode == TaskExecutionMode.READ_ONLY
                and running.type == NodeType.AGENT
            ):
                running = replace(running, metadata={**running.metadata, "read_only": True})
            return await executor.execute_node(running, prepared, read_plan, task)

        result = await NodeExecutionGuard(metadata).execute(
            node,
            execute_owned,
            resume_key=await self._execution_resume_key(task.id),
        )
        await self._persist_node_products()
        if recorder is not None:
            await recorder.record(
                TrajectoryEvent(
                    name="node.completed" if result["status"] == "completed" else "node.waiting",
                    task_id=task.id,
                    payload={"node_id": node.id, "output": result.get("output")},
                )
            )
        return result

    def list_skills(self) -> dict[str, Skill]:
        return self.skills.list()

    def _orchestrator_for(self, metadata):
        if self._orchestrator_backend is not None:
            return self._orchestrator_backend
        return TemporalOrchestratorBackend(metadata=metadata)

    def list_tool_specs(self) -> list[ToolSpec]:
        return self.tools.specs()

    async def search_knowledge(self, query: str, limit: int = 5) -> list[KnowledgeItem]:
        return list(await self.storage.knowledge.search(query, limit=limit))

    async def list_approvals(self, status: ApprovalStatus | None = None) -> list[ApprovalRecord]:
        return list(await self.storage.operational.list_approvals(status))

    async def pending_approvals(self) -> list[ApprovalRecord]:
        return await self._approvals.pending()

    async def decide_approval(
        self,
        approval_id: str,
        approved: bool,
        *,
        decided_by: str = "cli",
        reason: str | None = None,
        channel: str = "cli",
        resume_session: bool = True,
    ) -> ApprovalRecord:
        """Record a human decision and unblock the task that was waiting (AUTHZ §5.2)."""
        record = await self._approvals.decide(
            approval_id,
            approved,
            decided_by=decided_by,
            reason=reason,
            channel=channel,
        )
        await self._resume_after_decision(record, resume_session=resume_session)
        return record

    async def sweep_approvals(self) -> int:
        """Expire stale pending approvals; returns how many were swept."""
        return await self._approvals.sweep_expired()

    async def approval_backlog_age(self) -> float | None:
        """Age in seconds of the oldest pending approval (monitoring metric)."""
        return await self._approvals.oldest_pending_age()

    async def _resume_after_decision(
        self, record: ApprovalRecord, *, resume_session: bool = True
    ) -> None:
        """Resume a task parked in WAITING_APPROVAL once its grant is approved.

        Rejections leave the task parked: the caller decides whether to retry.
        A resume failure never rolls back the decision, so it is recorded in the
        audit stream instead of raising out of the decide path.
        """
        if record.status is not ApprovalStatus.APPROVED:
            return
        if record.task_id:
            await self._resume_task_after_approval(
                record.task_id,
                approval_id=record.id,
            )
        if resume_session and record.session_id:
            await self._resume_session_after_approval(record)

    async def _resume_session_after_approval(self, record: ApprovalRecord) -> None:
        """Continue a chat turn that parked on this approval's pending tool calls."""
        try:
            session = await self._session_store.get_session(record.session_id)
            if session is None:
                raise PermissionError("Session unavailable")
            original = self._sessions.frozen_principal(session)
            if record.requested_by != original.user_id:
                raise PermissionError("Approval session identity mismatch")
            await self.resume_pending_task(
                record.session_id,
                user_id=original.user_id,
                principal=original,
            )
        except LookupError as exc:
            # Another channel may have already resumed the same parked turn.
            if "No pending approval found" in str(exc):
                return
            self._record_security(
                "approval.resume_failed",
                {
                    "approval_id": record.id,
                    "session_id": record.session_id,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
        except Exception as exc:  # noqa: BLE001 - the decision stays valid
            self._record_security(
                "approval.resume_failed",
                {
                    "approval_id": record.id,
                    "session_id": record.session_id,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )

    async def _resume_task_after_approval(
        self,
        task_id: str,
        *,
        approval_id: str = "",
    ) -> None:
        """Resume a WAITING_APPROVAL task after its change proposal is approved.

        ``approval_id`` is optional and only enriches the audit record when the
        resume path came from a security approval decision rather than the
        change-proposal service.
        """
        metadata = self.storage.metadata
        if metadata is None:
            return
        try:
            task = await metadata.get_task(task_id)
            if task is None or task.status is not TaskStatus.WAITING_APPROVAL:
                return
            await self.resume_task(task)
        except Exception as exc:  # noqa: BLE001 - the decision stays valid
            self._record_security(
                "approval.resume_failed",
                {
                    "approval_id": approval_id,
                    "task_id": task_id,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )

    def _record_security(self, kind: str, payload: dict) -> None:
        if self.security is not None:
            self.security.audit.record(kind, payload)
