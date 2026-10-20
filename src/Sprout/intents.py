"""Single source of truth for runtime intent recognition and routing.

The seven intents used to be re-declared in ``runtime.py``, the intent prompt,
and the event catalog. They live here once; callers derive their tables from
:data:`INTENT_EVENTS` instead of maintaining a second copy.
"""

from __future__ import annotations

import re
from enum import StrEnum

from Sprout.events.catalog import (
    INTENT_APPROVAL_REQUESTED,
    INTENT_CONVERSATION_REQUESTED,
    INTENT_EVOLUTION_REQUESTED,
    INTENT_MEMORY_REQUESTED,
    INTENT_STRATEGY_REQUESTED,
    INTENT_TASK_REQUESTED,
    INTENT_TOOL_REQUESTED,
    INTENT_WORKSPACE_REQUESTED,
)


class Intent(StrEnum):
    CONVERSATION = "conversation"
    TASK = "task"
    WORKSPACE = "workspace"
    TOOL = "tool"
    APPROVAL = "approval"
    MEMORY = "memory"
    EVOLUTION = "evolution"
    STRATEGY = "strategy"


DEFAULT_INTENT = Intent.CONVERSATION

#: intent -> the ``intent.*.requested`` event it triggers.
INTENT_EVENTS: dict[Intent, str] = {
    Intent.CONVERSATION: INTENT_CONVERSATION_REQUESTED,
    Intent.TASK: INTENT_TASK_REQUESTED,
    Intent.WORKSPACE: INTENT_WORKSPACE_REQUESTED,
    Intent.TOOL: INTENT_TOOL_REQUESTED,
    Intent.APPROVAL: INTENT_APPROVAL_REQUESTED,
    Intent.MEMORY: INTENT_MEMORY_REQUESTED,
    Intent.EVOLUTION: INTENT_EVOLUTION_REQUESTED,
    Intent.STRATEGY: INTENT_STRATEGY_REQUESTED,
}

#: Substrings that cheaply identify a coding task without an LLM round-trip.
#: This is deliberately conservative; ambiguous text falls through to the LLM.
TASK_KEYWORDS: tuple[str, ...] = (
    "修复",
    "修改",
    "重构",
    "实现",
    "新增",
    "补充",
    "改代码",
    "写代码",
    "加个",
    "写个",
    "加一个",
    "写一个",
    "补个",
    "fix",
    "implement",
    "refactor",
    "write a",
    "add a",
    "create a",
    "modify",
)


def normalize_intent(value: str | None) -> Intent:
    """Coerce a raw intent name to a known :class:`Intent`, defaulting safely."""
    if not value:
        return DEFAULT_INTENT
    try:
        return Intent(value)
    except ValueError:
        return DEFAULT_INTENT


def intent_event(intent: Intent | str) -> str:
    return INTENT_EVENTS[normalize_intent(str(intent))]


class WriteIntentDisposition(StrEnum):
    """Text admission evidence, distinct from model intents and authorization."""

    EXPLICIT_WRITE = "explicit_write"
    KNOWN_NONWRITE = "known_nonwrite"
    UNKNOWN = "unknown"


def write_intent_disposition(text: str) -> WriteIntentDisposition:
    """Pure, conservative text judgment; UNKNOWN needs explicit human confirmation.

    No metadata, model labels or workspace signals can turn a known non-command
    into a write instruction. Unfamiliar wording is not a negative judgment.
    """
    stripped = text.strip()
    if not stripped:
        return WriteIntentDisposition.KNOWN_NONWRITE
    if stripped == "/task" or stripped.startswith("/task "):
        return WriteIntentDisposition.EXPLICIT_WRITE
    lowered = stripped.casefold()
    # A hint is admission to a write task, not just topical classification.
    # Ambiguous/mixed/quoted requests require a fresh explicit instruction.
    if any(mark in lowered for mark in ('"', "“", "”", "‘", "’", "`")):
        return WriteIntentDisposition.KNOWN_NONWRITE
    if any(term in lowered for term in (
        "不要", "不需要", "不是让", "先别", "别执行", "不执行", "不改", "仅分析",
        "只分析", "只读", "解释", "讨论", "总结", "现状", "进度", "已经", "昨天",
        "上次", "如果", "是否", "为什么", "以后", "完成了吗", "不实施", "未授权",
    )):
        return WriteIntentDisposition.KNOWN_NONWRITE
    if re.search(
        r"\b(don't|do not|not|never|without|only|quote|quoted|explain|discuss|review|"
        r"status|yesterday|finished|already|if|maybe|later|why|whether)\b", lowered
    ):
        return WriteIntentDisposition.KNOWN_NONWRITE
    question = lowered.endswith(("?", "？", "吗", "么"))
    # A leading action word can also name the subject of a statement. Match
    # subject/predicate structure, not global words such as "任务" or "未完成":
    # "请修复尚未完成的登录问题" still has an imperative with a relative object.
    request = re.match(r"^(?:(?:please|kindly)\s+|请(?:帮我)?|帮我)", lowered)
    body = lowered[request.end():] if request else lowered
    body = body.rstrip("。.!！ ?？")
    subject = re.search(r"的(?:任务|工作|需求|计划|进展|状态|工单|记录|方案)(.*)$", body)
    if subject:
        predicate = subject[1].lstrip("，,:： ")
        if predicate and subject[1].lstrip().startswith((":", "：")):
            return WriteIntentDisposition.KNOWN_NONWRITE
        if predicate and re.match(
            r"(?:目前|当前|仍|还|尚|此前|之前|早先|曾|正在|处于|处在|剩余|是|已|完成|结束)",
            predicate,
        ):
            return WriteIntentDisposition.KNOWN_NONWRITE
        relative_object = re.match(
            r"(?:修复|修改|重构|实现|新增|补充)(?:目前|当前)?"
            r"(?:尚未|仍未|未完成|待完成|还在|仍在|正在)", body
        )
        if not predicate and not request and not relative_object:
            return WriteIntentDisposition.KNOWN_NONWRITE
    if re.search(
        r"(?:目前|当前|仍然|仍|还|尚)(?:仍然|仍|还|尚)?"
        r"(?:未|没(?:有)?|在|正在|处于|处在|已(?:经)?|是|完成|结束|开始|排队|暂停|取消|失败|成功)"
        r"[^的，。！？]*$", body
    ) or re.search(r"(?:可能|也许|或许)(?:需要|要|会|得|可以|应当|应该)[^的，。！？]*$", body):
        return WriteIntentDisposition.KNOWN_NONWRITE
    if re.search(
        r"\b(?:task|work|ticket|request|plan|progress)\s+"
        r"(?:is|are|was|were|has|have|remains?|stays?)\b", body
    ) or re.match(r"^(?:fix|refactor|implement|modify)\s+(?:for|of|is|was|remains|has)\b", body):
        return WriteIntentDisposition.KNOWN_NONWRITE
    if re.match(r"^(?:implementation|refactoring|modification|creation|addition)\s+(?:of|for)\b",
                body):
        return WriteIntentDisposition.KNOWN_NONWRITE
    if re.search(r":\s*(?:pending|queued|paused|completed|unfinished)$", body):
        return WriteIntentDisposition.KNOWN_NONWRITE
    finite = re.search(r"\b(?:is|are|was|were|has been|have been|remains?)\b", body)
    if finite and not re.search(
        r"\b(?:that|which|who|whose|so|because|when)\b", body[:finite.start()]
    ):
        return WriteIntentDisposition.KNOWN_NONWRITE
    if not request and re.search(r"\b(?:task|ticket|request|plan|progress)$", body):
        return WriteIntentDisposition.KNOWN_NONWRITE
    if question:
        return WriteIntentDisposition.UNKNOWN
    if re.match(r"^(?:请)?帮我|^请", lowered) and any(
        keyword in lowered for keyword in TASK_KEYWORDS
    ):
        return WriteIntentDisposition.EXPLICIT_WRITE
    # Require an imperative, not an arbitrary keyword buried in narration.
    imperative = re.match(
        r"^(?:(?:please|kindly)\s+|请(?:帮我)?|帮我)?(?:"
        + "|".join(re.escape(keyword) + (r"n?\b" if keyword.endswith(" a")
                                       else r"\b" if keyword.isascii() else "")
                   for keyword in TASK_KEYWORDS)
        + r")", lowered
    )
    return (WriteIntentDisposition.EXPLICIT_WRITE if imperative
            else WriteIntentDisposition.UNKNOWN)


def detect_task_hint(text: str) -> bool:
    """Compatible bool fast path; UNKNOWN is not permission to create a Task."""
    return write_intent_disposition(text) is WriteIntentDisposition.EXPLICIT_WRITE


def needs_intent_clarification(text: str) -> bool:
    """Ask only about concrete inspect/modify ambiguity, not all UNKNOWN text.

    Model confidence is not evidence of permission. Explicit commands, read-only
    requests, narration and greetings retain their established routing.
    """
    value = text.strip().casefold()
    disposition = write_intent_disposition(value)
    if value == "/task" or value.startswith("/task "):
        return False
    # A choice between inspect and modify is not an imperative to modify,
    # even when a leading "帮我" also contains an action keyword.
    alternatives = re.search(
        r"(?:分析|检查|看看|inspect).*?(?:还是|或者|\bor\b).*?(?:修改|修复|modify|fix)", value,
    )
    # In this construction "还是" is a choice connector, not the "还/是"
    # status predicate understood by the legacy narrative guard. Reuse that
    # guard on an equivalent connector; quoted/negated narration stays nonwrite.
    alternative_disposition = write_intent_disposition(value.replace("还是", "或者"))
    if alternatives and alternative_disposition is not WriteIntentDisposition.KNOWN_NONWRITE:
        return True
    if disposition is not WriteIntentDisposition.UNKNOWN:
        return False
    inspect = re.search(
        r"看看|看一下|看下|过一下|处理一下|处理下|\b(?:look at|take a look at|check out)\b", value,
    )
    target = re.search(
        r"项目|代码|功能|登录|接口|模块|缓存|bug|\b(?:project|code|login|feature|module|cache)\b",
        value,
    )
    return bool(inspect and target)


def clarification_answer(text: str) -> str | None:
    """Exact choices only: generic assent is never a write selection.

    None denotes a new topic; 'unclear' keeps the pending question open.
    These are intent choices, not workspace, tool or Apply authorizations.
    """
    value = text.strip().casefold().rstrip("。.!！?？ ")
    choices = {
        "read": {"只分析", "仅分析", "只检查", "只检查bug", "检查bug", "提出优化建议",
                 "只给建议", "analyze only", "analyse only", "inspect only", "read only"},
        "write": {"直接修改", "修改代码", "直接修改代码", "直接修复", "modify the code",
                  "modify directly", "fix it", "make changes"},
        "cancel": {"先别做了", "取消", "不做了", "cancel", "stop", "never mind"},
        "chat": {"我只是问问", "只是问问", "just asking"},
        "unclear": {"好的", "嗯", "是的", "好", "可以", "yes", "ok", "okay", "sure"},
    }
    return next((choice for choice, values in choices.items() if value in values), None)


__all__ = [
    "DEFAULT_INTENT",
    "INTENT_EVENTS",
    "TASK_KEYWORDS",
    "Intent",
    "WriteIntentDisposition",
    "detect_task_hint",
    "needs_intent_clarification",
    "clarification_answer",
    "intent_event",
    "normalize_intent",
    "write_intent_disposition",
]
