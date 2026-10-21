"""Reserved argument keys the tool layer injects, and no tool declares.

``Tool.invoke`` takes only ``arguments``, so a tool that delegates onward has no
other channel to learn *which task* and *which source* it is acting for. The
keys here are the ones the executor adds for that purpose.

They live in their own module because both sides need them and must not import
each other: ``tools/executor.py`` imports the registry, which imports
``tools/system_tools.py``, which needs the key names. A constant defined in the
executor and imported by the tools closed that cycle and broke application
startup.

**A reserved key is never part of an approval fingerprint.** It is injected
after the decision, and a tool that forwards it into its own policy arguments
would make identical calls in different turns fingerprint differently — one
approval would then cover only the turn that produced it.

The dictionary carries attribution, NOT authorization. The private v1 dynamic
handoff below transfers a consumed approval only for one exact in-process call.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

#: Carries ``{task_id, source, requested_by}`` from ``ToolExecutor`` to a tool
#: that hands work to a broker. The broker gates the delegated action under its
#: own tool name and a task-scoped grant, so dropping this context makes an
#: already-approved call look unapproved one layer down.
CONTEXT_KEY = "_sprout_context"

#: Tools the executor attaches the context to. An explicit list rather than
#: every tool: a tool that cannot reach a second policy layer has no use for it,
#: and adding a reserved key to arguments whose schema does not declare one is
#: noise at best.
CONTEXT_TOOLS = frozenset({"cli_tool_run"})

__all__ = ["CONTEXT_KEY", "CONTEXT_TOOLS", "NodeToolInvocationContext"]


@dataclass(frozen=True, slots=True)
class NodeToolInvocationContext:
    """Host-bound approval attribution; never deserialize from model arguments."""

    task_id: str
    source: str
    requested_by: str
    resource_scope: str


def _cli_process_binding(command, cwd, env, timeout, task_id, session_id,
                         source, requested_by, resource_scope, workspace_id, workspace_kind):
    """Private, exact execution subject; environment never enters public logs."""
    return (tuple(command), str(Path(cwd).resolve()), tuple(sorted((env or {}).items())),
            timeout, task_id, session_id, source, requested_by, resource_scope,
            workspace_id, workspace_kind)


@dataclass(slots=True, repr=False)
class _CliHandoff:
    broker: object
    approvals: object
    owner: object
    binding: tuple
    expires: float
    active: bool = True
    consumed: bool = False


_CLI_HANDOFF: ContextVar[_CliHandoff | None] = ContextVar("cli_approval_handoff_v1", default=None)
_CLI_HANDOFF_LOCK = Lock()
_CLI_HANDOFF_TTL = 5.0


@contextmanager
def _approved_cli_call(broker, approvals, binding):
    """Executor-only handoff after ApprovalManager consumption, not a token API.

    Private in-process service code is trusted; arbitrary Python extensions are
    not sandboxed. JSON arguments cannot construct this dynamic scope. ContextVar
    propagation to child tasks is insufficient: the consumer checks task identity.
    """
    handoff = _CliHandoff(broker, approvals, asyncio.current_task(), binding,
                          time.monotonic() + _CLI_HANDOFF_TTL)
    token = _CLI_HANDOFF.set(handoff)
    try:
        yield
    finally:
        with _CLI_HANDOFF_LOCK:
            handoff.active = False
        _CLI_HANDOFF.reset(token)


def _consume_cli_approval(broker, approvals, binding):
    handoff = _CLI_HANDOFF.get()
    if handoff is None or binding is None or approvals is None:
        return False
    # No await between check and consume, also serialized across Python threads.
    with _CLI_HANDOFF_LOCK:
        if (not handoff.active or handoff.consumed or handoff.broker is not broker
                or handoff.approvals is not approvals
                or handoff.owner is not asyncio.current_task()
                or time.monotonic() >= handoff.expires or handoff.binding != binding):
            return False
        handoff.consumed = True
        return True
