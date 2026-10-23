"""GitBroker-only bounded read profile. Production execution remains HOLD.

There is deliberately NO installation/view issuer in this patch. A pathname,
Workspace.kind, metadata, or freshly computed hash cannot attest a trusted Git
installation or a stable managed view. Both authority resolvers fail closed.
The private runner is tested with mocked authority and process I/O, NOT offered
as a public capability accepting caller-supplied profiles.

A future issuer must keep the installation (including its loader dependencies),
view, sanitized local config/attributes, empty hooks/config and private paths
stable for the entire operation; exclude includes, filters, remotes, promisor
objects, external alternates and helper execution; and prove the view/output
fits the original Scope. Environment/argv pins alone do not establish this.
Do not enable either resolver based on a one-time scan of a mutable repository.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from Sprout.execution.models import GitResult
from Sprout.execution.proc import capture_bounded, terminate
from Sprout.task.models import DelegationScope
from Sprout.tools.result import ToolExecutionUnconfirmed
from Sprout.workspace.models import Workspace

TIMEOUT_SECONDS = 30.0
CLEANUP_SECONDS = 5.0
MAX_OUTPUT_BYTES = 1024 * 1024


@dataclass(frozen=True, slots=True)
class _Installation:
    """Internal evidence shape only; constructing it does NOT establish trust."""

    path: Path
    version: str
    identity: str


@dataclass(frozen=True, slots=True)
class _Plan:
    """Future issuer-owned inputs. Never parsed from tools or workspace text."""

    installation: _Installation
    view: Path
    home: Path
    hooks: Path
    config: Path
    temp: Path
    system_env: dict[str, str]


def _resolve_installation() -> _Installation | None:
    # No approved installation authority currently exists. No PATH discovery,
    # first-seen hash enrollment, arbitrary path option, or version subprocess.
    return None


def _resolve_view(
    workspace: Workspace, scope: DelegationScope | None, installation: _Installation,
) -> _Plan | None:
    # No existing provider proves an immutable, scope-constrained sanitized
    # view. Do not infer one from workspace.root/kind or accept a trusted flag.
    return None


async def run_read(
    workspace: Workspace, operation: str, scope: DelegationScope | None,
) -> GitResult:
    if operation not in {"status", "diff"}:
        return GitResult(action=operation, allowed=False, reason="s1_operation_not_supported")
    installation = _resolve_installation()
    if installation is None:
        return GitResult(action=operation, allowed=False,
                         reason="s1_trusted_installation_unavailable")
    plan = _resolve_view(workspace, scope, installation)
    if plan is None:
        return GitResult(action=operation, allowed=False, reason="s1_managed_view_unavailable")
    return await _execute(plan, operation)


def _environment(plan: _Plan) -> dict[str, str]:
    # No os.environ copy: not even PATH, HOME, GIT_CONFIG_* or credentials.
    env = {key: value for key, value in plan.system_env.items()
           if key in {"SYSTEMROOT", "WINDIR"}}
    env.update({
        "HOME": str(plan.home), "USERPROFILE": str(plan.home),
        "XDG_CONFIG_HOME": str(plan.home), "APPDATA": str(plan.home),
        "LOCALAPPDATA": str(plan.home), "TEMP": str(plan.temp),
        "TMP": str(plan.temp), "TMPDIR": str(plan.temp),
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_SYSTEM": str(plan.config),
        "GIT_CONFIG_GLOBAL": str(plan.config), "GIT_ATTR_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0",
        "GIT_NO_LAZY_FETCH": "1", "GIT_PAGER": "", "LC_ALL": "C",
    })
    return env


def _argv(plan: _Plan, operation: str) -> tuple[str, ...]:
    commands = {
        "status": ("status", "--porcelain=v1", "--ignore-submodules=all"),
        "diff": ("diff", "--no-color", "--no-ext-diff", "--no-textconv",
                 "--ignore-submodules=all"),
    }
    pins = (f"core.hooksPath={plan.hooks}", "core.fsmonitor=false", "core.pager=",
            "core.untrackedCache=false", f"core.attributesFile={plan.config}",
            "diff.ignoreSubmodules=all", "submodule.recurse=false", "protocol.allow=never",
            "maintenance.auto=false", "gc.auto=0")
    return (str(plan.installation.path), "--no-pager", "--no-optional-locks",
            *(part for pin in pins for part in ("-c", pin)),
            "-C", str(plan.view), *commands[operation])


async def _execute(plan: _Plan, operation: str) -> GitResult:
    """Runner mechanics only; only run_read may supply issuer-verified inputs.

No current production issuer can reach this function. Native helper exclusion,
installation identity and filesystem stability are NOT proven by its mocks.
All failures after attempting spawn are conservative UNCONFIRMED, even if the
child was reaped: termination does not prove no effects. No automatic retry.
"""
    argv = _argv(plan, operation)
    process = None
    try:
        process = await asyncio.wait_for(
            asyncio.create_subprocess_exec(
                *argv, cwd=str(plan.view), env=_environment(plan),
                stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, limit=4096,
            ), timeout=TIMEOUT_SECONDS,
        )
        stdout, _stderr = await asyncio.wait_for(
            capture_bounded(process, MAX_OUTPUT_BYTES), timeout=TIMEOUT_SECONDS,
        )
        if process.returncode != 0:
            raise ToolExecutionUnconfirmed("s1_nonzero_or_unknown_exit")
        return GitResult(action=operation, output=stdout.decode("utf-8", errors="replace"))
    except BaseException as exc:
        cleanup_confirmed = False
        if process is not None:
            try:
                await asyncio.wait_for(terminate(process), timeout=CLEANUP_SECONDS)
                cleanup_confirmed = process.returncode is not None
            except BaseException:
                pass  # Still unconfirmed; never convert cleanup failure to success.
        reason = ("s1_execution_unconfirmed" if cleanup_confirmed
                  else "s1_execution_and_cleanup_unconfirmed")
        if isinstance(exc, asyncio.CancelledError):
            cancelled = asyncio.CancelledError()
            cancelled.add_note(f"UNCONFIRMED: {reason}; do not automatically retry")
            raise cancelled from None
        raise ToolExecutionUnconfirmed(reason) from None
