"""Single source of truth for runtime intent recognition and routing.

The seven intents used to be re-declared in ``runtime.py``, the intent prompt,
and the event catalog. They live here once; callers derive their tables from
:data:`INTENT_EVENTS` instead of maintaining a second copy.
"""

from __future__ import annotations

import json
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


def capability_question(text: str) -> bool:
    """Narrow resource-free product questions, never a project inspection request."""
    value = text.strip().casefold().rstrip("?？。!！")
    if any(term in value for term in ("/", "\\", "`", "\n", "项目", "仓库", "文件",
                                      "project", "repo", "file", "扫描", "统计")):
        return False
    return bool(re.fullmatch(
        r"(?:你|sprout)(?:能做什么|会做什么|有哪些功能|支持什么|可以做什么|有什么能力)"
        r"|(?:你|sprout)(?:能|会|可以|支持)(?:编程|写代码|生成代码|运行测试)吗?"
        r"|(?:what can (?:you|sprout) do|what (?:are your|is sprout's) capabilities)"
        r"|can (?:you|sprout) (?:code|write code|run tests)", value))

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


def project_request_text(text: str) -> str:
    """Keep literal data out of operation classification; preserve the original request."""
    decoder = json.JSONDecoder()
    pieces = []
    end = 0
    for match in re.finditer(r"[\[{]", text):
        if match.start() < end:
            continue
        try:
            value, length = decoder.raw_decode(text[match.start():])
        except (ValueError, RecursionError):
            continue
        if isinstance(value, (dict, list)):
            pieces.extend((text[end:match.start()], " input_data "))
            end = match.start() + length
    pieces.append(text[end:])
    normalized = "".join(pieces)
    # Quoted filenames identify artifacts; quoted sentences remain quotations.
    return re.sub(
        r'["“‘`]([A-Za-z0-9_./\\:-]+\.[A-Za-z][\w-]*)["”’`]',
        lambda match: match[1] if normalized[:match.start()].strip() else match[0],
        normalized,
    )


def strip_project_reference(clause: str) -> str:
    """Remove a leading file citation so the following operation can be classified."""
    return re.sub(
        r"^(?:请)?(?:严格\s*)?(?:按照|按|参考|依据)\s*(?:已经读取的\s*)?"
        r"[A-Za-z0-9_./-]+\.[A-Za-z][A-Za-z0-9_]*(?=\s|[^\x00-\x7F]|$)",
        "",
        clause,
    ).strip()


def write_intent_disposition(text: str) -> WriteIntentDisposition:
    """Pure, conservative text judgment; UNKNOWN needs explicit human confirmation.

    No metadata, model labels or workspace signals can turn a known non-command
    into a write instruction. Unfamiliar wording is not a negative judgment.
    """
    stripped = project_request_text(text).strip()
    if not stripped:
        return WriteIntentDisposition.KNOWN_NONWRITE
    if stripped == "/task" or stripped.startswith("/task "):
        return WriteIntentDisposition.EXPLICIT_WRITE
    lowered = stripped.casefold()
    # A hint is admission to a write task, not just topical classification.
    # Ambiguous/mixed/quoted requests require a fresh explicit instruction.
    if any(mark in lowered for mark in ('"', "“", "”", "‘", "’", "`")):
        return WriteIntentDisposition.KNOWN_NONWRITE
    stripped = strip_project_reference(stripped)
    lowered = stripped.casefold()
    clauses = project_clauses(stripped)
    if _project_write_cancelled(clauses):
        return WriteIntentDisposition.KNOWN_NONWRITE
    if len(clauses) > 1 and "\n" not in stripped and not stripped.endswith(("?", "？", "吗", "么")):
        primary, *details = clauses
        if (write_intent_disposition(primary) is WriteIntentDisposition.EXPLICIT_WRITE
                and all(_coding_detail(clause) for clause in details)):
            return WriteIntentDisposition.EXPLICIT_WRITE
        if _inspect_then_edit(clauses):
            return WriteIntentDisposition.EXPLICIT_WRITE
    if "\n" in stripped:
        first = clauses[0] if clauses else ""
        # Classify the primary operation independently of subsequent execution
        # requirements and file exclusions. A claimed authorization is not an
        # operation; an execution preamble still needs a concrete edit below.
        primary = write_intent_disposition(first)
        resume = re.fullmatch(r"(?:请)?(?:直接|立即)执行[^。；;\n]*代码修改任务", first)
        edits = [c.removeprefix("只") for c in clauses]
        if (primary is WriteIntentDisposition.EXPLICIT_WRITE or (resume and any(
            write_intent_disposition(c) is WriteIntentDisposition.EXPLICIT_WRITE
            for c in edits
        ))):
            return WriteIntentDisposition.EXPLICIT_WRITE
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
    # A bounded action/object grammar, after narration/negation/question guards.
    # "生成测试方案" or "解释如何生成测试" is not a request to write test code.
    if re.fullmatch(
        r"(?:(?:为|给)[^，。！？;\n]{1,100}?)?\s*(?:生成|编写|添加|创建|补写)\s*"
        r"(?:一(?:个|些|组|份))?(?:单元测试|测试代码|测试文件|测试用例|测试)"
        r"(?:[，,]\s*覆盖\s*[^，,。！？;\n]{1,160})?"
        r"(?:[，,]\s*(?:并|并且)?\s*(?:运行|执行)测试)?", body,
    ):
        return WriteIntentDisposition.EXPLICIT_WRITE
    if re.match(r"^(?:请)?帮我|^请", lowered) and any(
        keyword in lowered for keyword in TASK_KEYWORDS
    ):
        return WriteIntentDisposition.EXPLICIT_WRITE
    # A bounded imperative with an artifact object. Bare document nouns
    # require a creation verb; arbitrary filenames must look like paths.
    if re.fullmatch(
        r"(?:那(?:你)?)?(?:请(?:帮我)?|帮我)?\s*"
        r"(?:(?:为|给)(?:当前|这个|当前这个|该|本)?项目\s*)?"
        r"(?:补齐|创建|生成|增加|添加|编写|补写)\s*"
        r"(?:一(?:份|个)\s*[a-z][a-z0-9_-]*|(?:一(?:份|个))?\s*"
        r"(?:[\w/-]+(?:\.[\w-]+)+|测试文件|测试代码|配置文件|文档))",
        body,
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


def project_clauses(text: str) -> tuple[str, ...]:
    """Separate operation clauses from list markers and dependent clauses."""
    return tuple(
        re.sub(r"^\d+[.、]\s*", "", part.strip())
        for part in re.split(
            r"[。；;\n，,]|并且?(?!不)|然后|同时|最后", project_request_text(text),
        )
        if part.strip()
    )


def _project_write_cancelled(clauses: tuple[str, ...]) -> bool:
    """Global cancellation remains distinct from file-specific exclusions."""
    return any(strip_project_reference(c).removeprefix("请").casefold().startswith((
        "取消", "先别执行", "不要执行", "不执行",
        "不要修改任何", "不修改任何", "不做任何修改",
        "只分析", "仅分析", "do not modify any", "don't modify any",
        "only analyze", "only analyse",
    )) for c in clauses)


def _inspect_then_edit(clauses: tuple[str, ...]) -> bool:
    """Inspection can prepare an explicit edit in the same request."""
    for index, clause in enumerate(clauses):
        if re.match(r"^(?:请)?(?:先)?(?:分析|检查|读取|阅读|查看)(?=.)", clause):
            continue
        action = strip_project_reference(clause)
        if (index == 0 or re.search(r"(?:方案|建议|思路|计划)$", action)
                or write_intent_disposition(action) is not WriteIntentDisposition.EXPLICIT_WRITE):
            return False
        return all(_coding_detail(detail) for detail in clauses[index + 1:])
    return False


def _coding_detail(clause: str) -> bool:
    """A trailing file restriction or execution phase qualifies the primary edit."""
    if re.fullmatch(
        r"(?:请)?(?:不要|不)(?:修改|改动|写入)\s*"
        r"(?:[\w./-]+(?:\.[\w-]+|/)|(?:其他|现有|原有)(?:测试)?文件|规格(?:文件)?|"
        r"(?:现有|原有)测试(?:文件)?)"
        r"(?:\s*(?:和|及|以及)\s*(?:现有|原有)?测试(?:文件)?)?",
        clause,
    ):
        return True
    # A phase list may contain a read-only check after applying changes.
    phases = re.split(r"、|和|以及|及", clause.removeprefix("完成"))
    if all(re.fullmatch(
        r"独立验证|验收|审查|review|apply|写回|(?:写回|apply)后只读(?:复测|验证)",
        phase.strip(), flags=re.IGNORECASE,
    ) for phase in phases):
        return True
    return write_intent_disposition(clause) is not WriteIntentDisposition.KNOWN_NONWRITE


def conditional_test_extension(text: str) -> bool:
    clauses = project_clauses(text)
    return (bool(clauses) and clauses[0].startswith(("检查", "请检查"))
            and "测试" in clauses[0]
            and any(c.startswith(("不足时补充", "不足时添加")) for c in clauses[1:])
            and not any(c.startswith(("不要", "只分析", "不修改")) for c in clauses))


def project_feature_edit(clause: str) -> bool:
    """A direct feature edit, including one without a named file."""
    return re.fullmatch(
        r"(?:请)?(?:(?:为|给)(?:当前|当前这个|这个|该|本)项目\s*)?"
        r"(?:完善|新增|添加|增加|实现)[^，。；！？]{1,60}功能",
        clause,
    ) is not None


def detect_task_hint(text: str) -> bool:
    """Compatible bool fast path; UNKNOWN is not permission to create a Task."""
    text = project_request_text(text)
    if _project_write_cancelled(project_clauses(text)):
        return False
    if write_intent_disposition(text) is WriteIntentDisposition.EXPLICIT_WRITE:
        return True
    if any(mark in text for mark in ('"', '“', '”', '‘', '’', chr(96), '?', '？')):
        return False
    clauses = project_clauses(text)
    feature_edit = any(project_feature_edit(c) for c in clauses)
    leading_write = (
        bool(clauses)
        and write_intent_disposition(clauses[0]) is WriteIntentDisposition.EXPLICIT_WRITE
    )
    if not clauses or any(c.startswith(("或者", "还是", "只分析", "仅分析"))
                          or (c.startswith("如果") and not (
                              (feature_edit or leading_write)
                              and c in {"如果发现失败", "如果测试失败"}))
                          or (c.startswith("不要") and not (
                              c.startswith("不要修改其他") or (
                                  feature_edit and re.fullmatch(
                                      r"不要修改\s+[\w./-]+\.\w+", c))))
                          or ("不修改" in c and "其他" not in c)
                          for c in clauses):
        return False
    first = clauses[0]
    if feature_edit or conditional_test_extension(text):
        return True
    if first.startswith(("参考", "依据", "按照", "严格依据")):
        return any(write_intent_disposition(c) is WriteIntentDisposition.EXPLICIT_WRITE
                   for c in clauses[1:])
    if not first.startswith(("分析", "检查", "请分析", "请检查")):
        return leading_write
    return any(c.startswith(("补充缺失的测试", "补充测试", "生成单元测试", "修改代码",
                             "修复失败的测试"))
               and write_intent_disposition(c) is WriteIntentDisposition.EXPLICIT_WRITE
               for c in clauses[1:])


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
        "write": {"直接改", "直接修改", "修改代码", "直接修改代码", "直接修复", "modify the code",
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
