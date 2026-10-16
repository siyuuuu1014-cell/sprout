"""Progressive disclosure tests (design §6.1): L0 index, trust + activation gates."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from Sprout.agent.loop import AgentLoop
from Sprout.context.context import AgentContext
from Sprout.llm.messages import LLMMessage, LLMResponse, LLMStreamEvent, Role
from Sprout.message.models import Message
from Sprout.session.models import Session
from Sprout.skills.models import Skill, SkillSource, TrustLevel
from Sprout.tools.spec import ToolSpec


def _skill(name: str, **overrides: object) -> Skill:
    base: dict[str, object] = {
        "version": "1.0",
        "instructions": f"Long body for {name}\nsecond line",
        "enabled": True,
        "description": "",
        "tags": (),
        "source": SkillSource.LOCAL.value,
        "trust": TrustLevel.TRUSTED.value,
    }
    base.update(overrides)
    return Skill(name=name, **base)  # type: ignore[arg-type]


def _ctx(
    skills: Sequence[Skill],
    *,
    tools: Mapping[str, object] | None = None,
    disclosure: str = "progressive",
) -> AgentContext:
    return AgentContext(
        session=Session(id="s1", user_id="u1"),
        user={"id": "u1"},
        skills={skill.name: skill for skill in skills},
        tools=tools or {},  # type: ignore[arg-type]
        skill_disclosure=disclosure,
    )


# -- L0 index ------------------------------------------------------------------


def test_index_lists_visible_skills_with_summary() -> None:
    ctx = _ctx([_skill("pdf", description="fill pdf forms", tags=("pdf", "form"))])
    text = ctx.skills_index()
    assert "pdf" in text
    assert "fill pdf forms" in text
    assert "tags: pdf, form" in text


def test_index_falls_back_to_first_body_line() -> None:
    ctx = _ctx([_skill("alpha", instructions="First line here\nmore detail")])
    assert "First line here" in ctx.skills_index()
    assert "more detail" not in ctx.skills_index()


def test_index_prefers_description_over_body() -> None:
    ctx = _ctx(
        [_skill("alpha", description="short summary", instructions="SECRET BODY CONTENT")]
    )
    text = ctx.skills_index()
    assert "short summary" in text
    assert "SECRET BODY CONTENT" not in text


def test_index_only_carries_first_body_line() -> None:
    ctx = _ctx([_skill("alpha", instructions="Summary line\nSECRET BODY CONTENT")])
    text = ctx.skills_index()
    assert "Summary line" in text
    assert "SECRET BODY CONTENT" not in text


def test_index_empty_when_no_visible_skills() -> None:
    assert _ctx([]).skills_index() == ""


# -- trust gate ----------------------------------------------------------------


def test_untrusted_skill_is_hidden() -> None:
    ctx = _ctx([_skill("dl", trust=TrustLevel.UNTRUSTED.value)])
    assert ctx.skills_index() == ""
    assert ctx.visible_skills() == ()


def test_quarantined_skill_is_hidden() -> None:
    assert _ctx([_skill("dl", trust=TrustLevel.QUARANTINED.value)]).skills_index() == ""


def test_disabled_skill_is_hidden() -> None:
    assert _ctx([_skill("off", enabled=False)]).skills_index() == ""


# -- conditional activation ----------------------------------------------------


def test_requires_tools_hides_skill_until_present() -> None:
    skill = _skill("net", requires_toolsets=("http_get",))
    assert _ctx([skill]).skills_index() == ""
    assert "net" in _ctx([skill], tools={"http_get": object()}).skills_index()


def test_fallback_hidden_when_replacement_present() -> None:
    skill = _skill("legacy", fallback_for_toolsets=("http_get",))
    assert "legacy" in _ctx([skill]).skills_index()
    assert _ctx([skill], tools={"http_get": object()}).skills_index() == ""


# -- eager mode / defaults -----------------------------------------------------


def test_eager_mode_keeps_full_body() -> None:
    ctx = _ctx([_skill("alpha", instructions="FULL BODY")], disclosure="eager")
    assert "FULL BODY" in ctx.skill_instructions()
    assert ctx.skill_disclosure == "eager"


def test_default_disclosure_is_progressive() -> None:
    assert _ctx([]).skill_disclosure == "progressive"


# -- shared eligibility / model-facing system messages (R00 / F16) ------------


_ELIGIBILITY_CASES = [
    pytest.param({"trust": "untrusted"}, (), False, id="G01-untrusted"),
    pytest.param({"requires_toolsets": ("http_get",)}, (), False, id="G02-missing-tools"),
    pytest.param({"enabled": False}, (), False, id="disabled"),
    pytest.param({"trust": "quarantined"}, (), False, id="quarantined"),
    pytest.param({"trust": "rejected"}, (), False, id="rejected"),
    pytest.param(
        {"fallback_for_toolsets": ("http_get",)}, ("http_get",), False, id="fallback-hidden"
    ),
    pytest.param({}, (), True, id="trusted-enabled"),
    pytest.param(
        {"requires_toolsets": ("http_get",)}, ("http_get",), True, id="required-tools-present"
    ),
    pytest.param({"fallback_for_toolsets": ("http_get",)}, (), True, id="fallback-active"),
    pytest.param(
        {"requires_toolsets": ("http_get", "read")},
        ("http_get",), False, id="partially-required",
    ),
    pytest.param(
        {"fallback_for_toolsets": ("http_get", "read")},
        ("http_get",), True, id="partially-replaced-fallback",
    ),
]


@pytest.mark.parametrize("overrides,available,eligible", _ELIGIBILITY_CASES)
def test_disclosure_modes_share_eligibility(overrides, available, eligible) -> None:
    skill = _skill("eligibility-probe", description="PROBE SUMMARY", **overrides)
    tools = {name: object() for name in available}
    for disclosure in ("eager", "progressive"):
        context = _ctx([skill], tools=tools, disclosure=disclosure)
        assert context.visible_skills() == ((skill,) if eligible else ())
        assert bool(context.skills_index()) is eligible
        assert bool(context.skill_instructions()) is eligible


class _DisclosureRecorder:
    """Records actual model input without network calls or tool execution."""

    name = "disclosure-recorder"

    def __init__(self) -> None:
        self.calls: list[tuple[LLMMessage, ...]] = []

    async def chat(self, messages, *, tools=()) -> LLMResponse:
        self.calls.append(tuple(messages))
        return LLMResponse(content="ok", finish_reason="stop", model=self.name)

    async def stream_events(self, messages, *, tools=()) -> AsyncIterator[LLMStreamEvent]:
        self.calls.append(tuple(messages))
        yield LLMStreamEvent(content="ok", finish_reason="stop")


@pytest.mark.parametrize("overrides,available,eligible", _ELIGIBILITY_CASES)
@pytest.mark.parametrize("disclosure", ["eager", "progressive"])
@pytest.mark.parametrize("entrypoint", ["run", "stream"])
async def test_skill_eligibility_in_model_system_messages(
    overrides, available, eligible, disclosure, entrypoint, monkeypatch,
) -> None:
    # Telemetry is unrelated to disclosure and must not write to the user's DB.
    monkeypatch.setattr("Sprout.agent.loop.get_agent_step_store", Mock())
    monkeypatch.setattr("Sprout.llm.client.get_usage_recorder", Mock())
    skill = _skill(
        "eligibility-probe", description="PROBE SUMMARY", instructions="PROBE BODY\nDETAIL",
        **overrides,
    )
    control = _skill("trusted-control", description="CONTROL SUMMARY", instructions="CONTROL BODY")
    tools = {
        name: SimpleNamespace(spec=ToolSpec(name=name, description="fixture", input_schema={}))
        for name in available
    }
    context = _ctx([skill, control], tools=tools, disclosure=disclosure)
    model = _DisclosureRecorder()
    agent = AgentLoop(model=model)
    message = Message(content="hello", channel="test", user_id="u1", session_id="s1")

    if entrypoint == "run":
        assert (await agent.run(message, context)).content == "ok"
    else:
        assert [chunk async for chunk in agent.stream(message, context)] == ["ok"]

    assert len(model.calls) == 1
    system_text = "\n".join(
        item.content or "" for item in model.calls[0] if item.role is Role.SYSTEM
    )
    assert "trusted-control" in system_text
    assert ("eligibility-probe" in system_text) is eligible
    assert ("PROBE BODY" in system_text) is (eligible and disclosure == "eager")
    assert ("PROBE SUMMARY" in system_text) is (eligible and disclosure == "progressive")
    assert ("CONTROL BODY" in system_text) is (disclosure == "eager")
    assert ("CONTROL SUMMARY" in system_text) is (disclosure == "progressive")
