"""Read-only project analysis tool backed by workspace intelligence."""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any

from Sprout.tools.result import ToolResult
from Sprout.tools.spec import ToolSpec
from Sprout.workspace.intelligence import WorkspaceAnalysis, WorkspaceIntelligence


class ProjectAnalyzeTool:
    """Analyze a project workspace with safe built-in and optional system probes."""

    def __init__(
        self, *, intelligence: WorkspaceIntelligence | None = None,
        analyze: Callable[[Path, str], Awaitable[WorkspaceAnalysis]] | None = None,
    ) -> None:
        self._intelligence = intelligence
        self._analyze = analyze
        self.spec = ToolSpec(
            name="project_analyze",
            description=(
                "Read-only project analyzer for languages, manifests, README, Docker, "
                "compose, env keys, project database assets, interfaces, graph, and "
                "Sprout database topology."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Workspace path to analyze.",
                    },
                    "instruction": {
                        "type": "string",
                        "description": "Optional analysis focus.",
                    },
                    "include_system_tools": {
                        "type": "boolean",
                        "description": "Probe safe local analysis tools when present.",
                    },
                },
                "required": ["path"],
            },
            risk_level="low",
        )

    async def invoke(self, arguments: Mapping[str, Any]) -> ToolResult:
        if arguments.get("include_system_tools") is True:
            return ToolResult.denied("native_project_probe_unavailable")
        from Sprout.tools.context_keys import _current_project_analysis_call

        frame = _current_project_analysis_call()
        if frame is None or frame.tool is not self or frame.node_scope is None:
            return ToolResult.denied("project_analysis_scope_unavailable")
        try:
            return await frame.node_scope.runtime._invoke_project_tool(self, arguments)
        except (PermissionError, ValueError):
            return ToolResult.denied("project_analysis_scope_unavailable")


async def _system_tool_probe(root: Path) -> dict[str, Any]:
    probes: dict[str, Any] = {
        "available": {},
        "outputs": {},
    }
    commands = {
        "git": ["git", "status", "--short"],
        "rg": ["rg", "--files", "--hidden", "-g", "!.git"],
        "tokei": ["tokei", "--output", "json"],
        "cloc": ["cloc", "--json", "."],
    }
    for name, command in commands.items():
        if shutil.which(command[0]) is None:
            probes["available"][name] = False
            continue
        probes["available"][name] = True
        result = await _run(command, root)
        probes["outputs"][name] = result
    if probes["available"].get("rg") and not (
        probes["available"].get("tokei") or probes["available"].get("cloc")
    ):
        probes["line_estimate"] = _estimate_lines_from_rg_output(
            root,
            probes["outputs"].get("rg", {}),
        )
    return probes


def _estimate_lines_from_rg_output(root: Path, rg_output: Mapping[str, Any]) -> dict[str, Any]:
    if rg_output.get("ok") is not True:
        return {
            "ok": False,
            "source": "rg --files fallback",
            "error": "rg file listing failed",
        }
    files = [
        line.strip()
        for line in str(rg_output.get("stdout") or "").splitlines()
        if line.strip()
    ]
    total_lines = 0
    counted_files = 0
    skipped_files = 0
    by_extension: dict[str, dict[str, int]] = {}
    for file_name in files:
        path = (root / file_name).resolve()
        try:
            relative = path.relative_to(root)
        except ValueError:
            skipped_files += 1
            continue
        if not path.is_file():
            skipped_files += 1
            continue
        try:
            data = path.read_bytes()
        except OSError:
            skipped_files += 1
            continue
        if b"\0" in data:
            skipped_files += 1
            continue
        lines = data.count(b"\n")
        if data and not data.endswith(b"\n"):
            lines += 1
        counted_files += 1
        total_lines += lines
        extension = relative.suffix.lower() or "<none>"
        bucket = by_extension.setdefault(extension, {"files": 0, "lines": 0})
        bucket["files"] += 1
        bucket["lines"] += lines
    return {
        "ok": True,
        "source": "rg --files fallback",
        "files": counted_files,
        "lines": total_lines,
        "skipped_files": skipped_files,
        "by_extension": dict(sorted(by_extension.items())),
    }


async def _run(command: list[str], cwd: Path) -> dict[str, Any]:
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=8)
    except TimeoutError:
        return {"ok": False, "error": "timeout"}
    except OSError as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    output = stdout.decode("utf-8", errors="replace")
    error = stderr.decode("utf-8", errors="replace")
    return {
        "ok": process.returncode == 0,
        "returncode": process.returncode,
        "stdout": output[:8000],
        "stderr": error[:2000],
    }


__all__ = ["ProjectAnalyzeTool"]
