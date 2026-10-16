from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from Sprout.artifacts.models import ArtifactKind
from Sprout.runtime.nodes import NodeExecutor
from Sprout.skills.models import Skill
from Sprout.skills.registry import SkillRegistry
from Sprout.trajectory.models import ArtifactSnapshot


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [True, False])
async def test_exact_loaded_skill_keeps_all_attributes_and_needs_no_cleanup(enabled):
    skills = SkillRegistry()
    existing = Skill("procedure", "1", "body", required_tools=("skill_view",),
                     trust="trusted", enabled=enabled, origin="published.toml")
    skills.register(existing)
    executor = SimpleNamespace(
        _skills=skills,
        _metadata=SimpleNamespace(get_artifact=AsyncMock(return_value=SimpleNamespace(
            id="candidate", name="procedure", version="1", content="body",
            kind=ArtifactKind.SKILL,
        ))),
    )
    recorder = SimpleNamespace(record=AsyncMock())
    result = await NodeExecutor.pin_snapshot(
        executor, recorder, ArtifactSnapshot("candidate", "1", sha256(b"body").hexdigest()),
        "task",
    )
    assert result is None
    assert skills.get("procedure") is existing
    recorder.record.assert_awaited_once()


@pytest.mark.asyncio
async def test_changed_snapshot_content_cannot_replace_loaded_skill():
    skills = SkillRegistry()
    existing = Skill("procedure", "1", "original", trust="trusted")
    skills.register(existing)
    executor = SimpleNamespace(
        _skills=skills,
        _metadata=SimpleNamespace(get_artifact=AsyncMock(return_value=SimpleNamespace(
            id="candidate", name="procedure", version="1", content="changed",
            kind=ArtifactKind.SKILL,
        ))),
    )
    recorder = SimpleNamespace(record=AsyncMock())
    with pytest.raises(ValueError, match="content does not match"):
        await NodeExecutor.pin_snapshot(
            executor, recorder,
            ArtifactSnapshot("candidate", "1", sha256(b"original").hexdigest()), "task",
        )
    assert skills.get("procedure") is existing
    recorder.record.assert_not_awaited()
