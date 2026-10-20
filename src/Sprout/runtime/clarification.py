"""Pre-agent clarification routing. No tools, workspace or task effects here.

Write selections only issue the existing workspace question. Its independent
durable Consent claim remains mandatory. Claimed answers are never replayed.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import TYPE_CHECKING, Any

from Sprout.agent.base import AgentResult
from Sprout.context.context import AgentContext
from Sprout.gateway.identity import Principal
from Sprout.intents import (
    WriteIntentDisposition,
    clarification_answer,
    needs_intent_clarification,
    write_intent_disposition,
)
from Sprout.llm.messages import LLMMessage
from Sprout.message.models import Message, OutboundMessage
from Sprout.runtime.intent_analysis import (
    material_ambiguity,
    natural_answer,
    selection_scope,
    semantic_selection,
)
from Sprout.storage.lanes import SixLaneFanout

if TYPE_CHECKING:
    from Sprout.rootstock.contract import IntentClarificationStore
    from Sprout.runtime.runtime import Runtime
    from Sprout.session.models import Session, Turn

CLARIFICATION_KEY = "intent_clarification_v1"


def clarification_store(runtime: Runtime) -> IntentClarificationStore | None:
    store = runtime._session_store
    if isinstance(store, SixLaneFanout):
        store = store.authority
    names = ("issue_intent_clarification", "get_intent_clarification",
             "resolve_intent_clarification", "has_consumed_clarification_message",
             "has_pending_clarification_operation")
    return store if all(callable(getattr(store, name, None)) for name in names) else None


def _outbound(
    message: Message, session: Session, content: str, **metadata: Any,
) -> OutboundMessage:
    return OutboundMessage(content, message.channel, session.id, message.id, metadata)


async def _reply(
    runtime: Runtime, message: Message, session: Session, content: str, *,
    persist: bool = True, **metadata: Any,
) -> OutboundMessage:
    outbound = _outbound(message, session, content, **metadata)
    if persist:
        await runtime._persist_turns(session, message, AgentResult(content, metadata=metadata))
    return outbound


async def route_clarification(
    runtime: Runtime, message: Message, session: Session, principal: Principal | None,
    *, classification: dict[str, Any] | None = None,
) -> OutboundMessage | None:
    """Return an early response or None to preserve ordinary routing.

    Called identically by handle and handle_stream after owner resolution and
    before context construction, classification, event publication or AgentLoop.
    """
    store = clarification_store(runtime)
    if store and await store.has_consumed_clarification_message(message.id):
        return await _reply(runtime, message, session,
                            "这个回答已经处理，不能重复执行；请查看历史结果。", persist=False)
    record = await store.get_intent_clarification(session.id) if store else None
    if record and record["state"] == "pending":
        snapshot = json.loads(record["snapshot_json"])
        frozen = runtime._sessions.frozen_principal(session)
        identity = {"user_id": principal.user_id, "source": principal.source,
                    "authenticated": principal.authenticated} if principal else None
        if (identity != snapshot["principal"]
                or session.metadata.get("entry_principal_v1") != snapshot["principal"]):
            raise PermissionError("Clarification identity unavailable")
        expired = datetime.fromisoformat(record["expires_at"]) <= datetime.now(UTC)
        # Only a live question interprets ordinary text as a selection. An
        # expired row is retired atomically, then this is a normal chat turn.
        new_write_request = False
        choice = "expired" if expired else clarification_answer(message.content)
        if (not expired and choice is None and (
            needs_intent_clarification(message.content)
            or ("write" not in json.loads(record["choices_json"])
                and write_intent_disposition(message.content)
                is WriteIntentDisposition.EXPLICIT_WRITE)
        )):
            choice = "superseded"
        if not expired and choice is None:
            choice = natural_answer(message.content)
            if choice is None:
                # One bounded classification request; carry a new-topic result
                # forward on the server, never in caller-controlled metadata.
                recent = await runtime._session_store.recent_turns(session.id, limit=4)
                result = await runtime._classify_intent_with_llm(
                    message, AgentContext(session, memory=recent),
                    pending_question=record["question"],
                )
                if classification is not None:
                    classification.update(result)
                semantic = result.get("semantic")
                choice = semantic_selection(message.content, semantic)
                # Clearly independent commands/read-only topics may supersede
                # the old question even with an offline/legacy classifier.
                if (not semantic and write_intent_disposition(message.content)
                        is not WriteIntentDisposition.UNKNOWN):
                    choice = "new_topic"
                if choice == "new_topic":
                    choice = None
        if choice in {"read", "write"} and clarification_answer(message.content) is None:
            scope = selection_scope(snapshot["request"], message.content)
            if scope == "different":
                new_write_request = choice == "write"
                choice = "superseded"
            elif scope == "unknown":
                return await _reply(
                    runtime, message, session,
                    "无法确认这个回答是否仍指向原请求，请明确原对象或发送完整的新请求。\n"
                    + record["question"],
                    clarification_required=True, clarification_id=record["id"],
                    blocked_reason="clarification_scope_unknown",
                )
        if choice == "unclear":
            return await _reply(runtime, message, session, record["question"],
                                clarification_required=True, clarification_id=record["id"])
        if choice == "write" and "write" not in json.loads(record["choices_json"]):
            return await _reply(runtime, message, session, record["question"],
                                clarification_required=True, clarification_id=record["id"])
        if choice is None:
            choice = "superseded"
        if await store.resolve_intent_clarification(
            session, record["id"], message.id, message.content, choice,
        ) is not True:
            return await _reply(runtime, message, session,
                                "澄清已失效或已被处理。请重新发送明确请求。", persist=False)
        if choice in {"superseded", "expired"}:
            # Neither a new topic nor a reply after expiry selects the old goal.
            if new_write_request:
                # An independently explicit command belongs to its own target,
                # not a rewritten /task of the frozen question. Require fresh
                # Workspace Consent even if a workspace was already bound.
                held, outbound = await runtime._request_workspace(message, session)
                outbound = replace(outbound, content=(
                    "已按新的操作对象处理，未确认或恢复上一条请求。\n\n" + outbound.content
                ))
                await runtime._persist_turns(
                    session, held,
                    AgentResult(outbound.content, metadata=dict(outbound.metadata)),
                )
                return outbound
            record = None
        elif choice == "write":
            instruction = "/task " + snapshot["request"]
            routed = Message(
                instruction, channel={"mcp_client": "mcp"}.get(frozen.source, frozen.source),
                user_id=frozen.user_id, session_id=session.id, id=message.id,
                metadata={CLARIFICATION_KEY: record["id"]},
            )
            held, outbound = await runtime._request_workspace(routed, session)
            # Persist the real answer exactly once, not a fabricated user turn.
            held = replace(held, content=message.content)
            outbound = replace(outbound, content=(
                f"已明确修改意图，尚未授予操作权限。原始请求：\n{snapshot['request']}\n\n"
                + outbound.content
            ))
            await runtime._persist_turns(
                session, held, AgentResult(outbound.content, metadata=dict(outbound.metadata)),
            )
            return outbound
        elif choice == "read":
            # No Agent executor or tools are available on this strict analysis
            # route, even if a model emits unsolicited tool calls.
            try:
                response = await runtime.models.default().chat([
                    LLMMessage.system("Analyze only the supplied request/context. Do not execute "
                                      "tools or claim to have inspected files you have not read."),
                    LLMMessage.user("只分析，不修改、不执行：\n" + snapshot["request"]),
                ], tools=())
                content = response.text
            except Exception:
                content = "已选择只读分析，但分析模型暂不可用。未执行任何操作，请重新发起只读请求。"
            content = "说明：以下仅基于你提供的请求文本，未读取或检查真实仓库文件。\n\n" + content
            return await _reply(runtime, message, session, content, intent="conversation",
                                clarification_resolution="read", analysis_scope="request_text_only",
                                repository_inspected=False)
        else:
            return await _reply(runtime, message, session,
                                "已取消，未执行原请求。" if choice == "cancel" else
                                "明白，仅作讨论，不执行原请求。",
                                clarification_resolution=choice)
    # Without a pending question, assent/cancel/read-only words belong to the
    # original chat router. Only object-less write selections need a scope
    # question: they must never reach AgentLoop or reuse a bound workspace.
    if clarification_answer(message.content) == "write":
        return await _reply(runtime, message, session,
                            "请说明要修改的具体对象和要求；这条短回复不会执行历史请求。",
                            blocked_reason="operation_target_required")
    if not needs_intent_clarification(message.content):
        return None
    return await _ask(runtime, message, session, principal)


async def route_semantic_clarification(
    runtime: Runtime, message: Message, session: Session, principal: Principal | None,
    classification: dict[str, Any],
) -> OutboundMessage | None:
    """Before event publication or Agent dispatch; same path for run and stream."""
    if not material_ambiguity(message.content, classification.get("semantic")):
        return None
    modes = {item["action_mode"] for item in classification["semantic"]["candidates"]}
    missing_scope = classification["semantic"]["ambiguity_type"] == "missing_scope"
    return await _ask(runtime, message, session, principal,
                      allow_write="code_modification" in modes and not missing_scope,
                      missing_scope=missing_scope)


async def _ask(
    runtime: Runtime, message: Message, session: Session, principal: Principal | None,
    *, allow_write: bool = True, missing_scope: bool = False,
) -> OutboundMessage:
    store = clarification_store(runtime)
    if store is None or principal is None or not session.user_id:
        return await _reply(runtime, message, session,
                            "当前身份或存储不支持持久化澄清。请重新发送明确的只读或编码请求。",
                            blocked_reason="durable_clarification_unavailable")
    try:
        runtime._sessions.frozen_principal(session)
    except PermissionError:
        return await _reply(runtime, message, session,
                            "历史会话缺少可信恢复来源。请创建新会话并重新发送明确请求。",
                            blocked_reason="clarification_identity_unavailable")
    if await store.has_pending_clarification_operation(session.id):
        return await _reply(runtime, message, session,
                            "当前会话有尚未处理的工作区确认或衔接结果待核对。"
                            "请先处理原请求；若不继续，可新建会话发起新请求。不会替换原授权对象。",
                            blocked_reason="workspace_consent_pending")
    question = (f"关于“{message.content.strip()}”，你希望只分析／检查并提出建议，还是直接修改代码？"
                "请明确回答“只分析”或“直接修改”，也可以取消。")
    choices = ("read", "write", "chat", "cancel")
    if not allow_write:
        question = (f"关于“{message.content.strip()}”，你希望只分析信息，还是讨论操作方案？"
                    "请回答“只分析”“只是问问”或“取消”。若需实际操作，请另发明确的完整请求；"
                    "这里不会授予工具执行权限。")
        choices = ("read", "chat", "cancel")
    if missing_scope:
        question = (f"关于“{message.content.strip()}”，还缺少具体操作对象或范围。"
                    "请重新发送包含对象、目标和范围的完整请求；也可回答“只分析”或“取消”。"
                    "“好的”或“直接修改”不会授权执行这个未明确的请求。")
    outbound = _outbound(message, session, question, clarification_required=True)
    turn = await runtime._persist_turns(
        session, message, AgentResult(question, metadata=dict(outbound.metadata)),
    )
    expires = (datetime.now(UTC) + timedelta(minutes=30)).isoformat()
    if await store.issue_intent_clarification(
        session, turn, question, expires, choices=choices,
    ) is not True:
        raise LookupError("Could not issue durable clarification; no execution authorized")
    return replace(outbound, metadata={**outbound.metadata, "clarification_id": turn.id})


async def clarified_instruction(
    runtime: Runtime, session: Session, held: Turn, metadata: dict[str, Any],
) -> str:
    """Resolve server proof for a selected goal; never trust envelope text alone."""
    record_id = metadata.get(CLARIFICATION_KEY)
    if record_id is None:
        return held.content
    store = clarification_store(runtime)
    record = (
        await store.get_intent_clarification(session.id, record_id=record_id) if store else None
    )
    if (record is None or record["id"] != record_id or record["state"] != "resolved"
            or record["resolution"] != "write"
            or record["answer_message_id"] != held.metadata.get("message_id")
            or record["answer_sha256"] != sha256(held.content.encode()).hexdigest()):
        raise LookupError("Clarified instruction proof unavailable; issue a fresh request")
    snapshot = json.loads(record["snapshot_json"])
    if snapshot["principal"] != session.metadata.get("entry_principal_v1"):
        raise PermissionError("Clarified instruction identity unavailable")
    return "/task " + snapshot["request"]
