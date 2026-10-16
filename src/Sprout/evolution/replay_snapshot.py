"""Digest loaded registries and local Sprout implementation, without executing tools."""

import hashlib
import inspect
from dataclasses import asdict
from pathlib import Path

from Sprout.evolution.replay_oracle import digest


def runtime_snapshot(runtime) -> dict:
    package = Path(__file__).resolve().parents[1]
    implementation = {}
    for path in sorted(package.rglob("*.py")):
        relative = path.relative_to(package)
        if "tests" in relative.parts or "__pycache__" in relative.parts:
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(package):
            raise ValueError("Linked runtime implementation cannot be snapshotted")
        implementation[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    tools = {}
    unsupported = False
    for name, tool in sorted(runtime.tools.list().items()):
        cls = type(tool)
        try:
            source = inspect.getsourcefile(cls)
        except TypeError:
            source = None
        if source is None or not Path(source).resolve().is_relative_to(package):
            unsupported = True
        tools[name] = {"class": cls.__module__ + "." + cls.__qualname__,
                       "spec": asdict(tool.spec)}
    skills = {name: asdict(skill) for name, skill in sorted(runtime.skills.list().items())}
    return {
        "schema_version": 2,
        "implementation_fingerprint": digest(implementation),
        "tools_fingerprint": digest(tools),
        "skills_fingerprint": digest(skills),
        "skill_manifest": {name: {"fingerprint": digest(skill), "version": skill["version"],
                                  "instructions_hash": hashlib.sha256(
                                      skill["instructions"].encode()).hexdigest()}
                           for name, skill in skills.items()},
        "external_tool_implementation": unsupported,
    }


def snapshot_blockers(expected, runtime=None) -> list[str]:
    if not isinstance(expected, dict) or expected.get("schema_version") != 2:
        return ["runtime_snapshot_missing_or_unsupported"]
    if runtime is None:
        return ["current_runtime_snapshot_required"]
    current = runtime_snapshot(runtime)
    if set(current) != set(expected):
        return ["runtime_snapshot_missing_or_unsupported"]
    blockers = [key + "_changed" for key in current if current[key] != expected[key]]
    if expected["external_tool_implementation"] or current["external_tool_implementation"]:
        blockers.append("external_tool_implementation_requires_adapter")
    return blockers
