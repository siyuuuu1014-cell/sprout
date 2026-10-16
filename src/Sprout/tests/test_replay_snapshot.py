from dataclasses import replace
from types import SimpleNamespace

from Sprout.evolution.replay_snapshot import runtime_snapshot, snapshot_blockers
from Sprout.skills.models import Skill
from Sprout.skills.registry import SkillRegistry
from Sprout.tools.registry import RequestWorkspaceTool, ToolRegistry


def runtime():
    tools, skills = ToolRegistry(), SkillRegistry()
    tools.register(RequestWorkspaceTool())
    skills.register(Skill(name="fixture", version="1", instructions="private-instructions"))
    return SimpleNamespace(tools=tools, skills=skills)


def test_snapshot_is_stable_and_does_not_serialize_skill_content():
    instance = runtime()
    snapshot = runtime_snapshot(instance)
    assert snapshot_blockers(snapshot, instance) == []
    assert "private-instructions" not in str(snapshot)
    assert snapshot_blockers(snapshot) == ["current_runtime_snapshot_required"]


def test_changed_skill_instructions_and_trust_are_detected():
    instance = runtime()
    snapshot = runtime_snapshot(instance)
    skill = instance.skills.get("fixture")
    instance.skills.register(replace(skill, instructions="changed", trust="trusted"))
    assert "skills_fingerprint_changed" in snapshot_blockers(snapshot, instance)


def test_tool_spec_change_is_detected():
    instance = runtime()
    snapshot = runtime_snapshot(instance)
    tool = instance.tools.get("request_workspace")
    tool.spec = replace(tool.spec, risk_level="high")
    assert "tools_fingerprint_changed" in snapshot_blockers(snapshot, instance)


def test_missing_snapshot_does_not_get_backfilled():
    assert snapshot_blockers(None, runtime()) == ["runtime_snapshot_missing_or_unsupported"]
