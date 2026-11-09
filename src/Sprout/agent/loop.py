"""AgentLoop: the reactive LLM <-> tool execution loop."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from Sprout.agent.base import AgentResult, history_messages
from Sprout.agent.executor import ActionExecutor
from Sprout.agent.planner import DirectPlanner, Planner
from Sprout.agent.step_store import get_agent_step_store
from Sprout.llm.base import ModelBudgetExhausted
from Sprout.llm.client import invoke_model
from Sprout.llm.language import detect_language
from Sprout.llm.messages import LLMMessage, Role, ToolCall, Usage

logger = logging.getLogger("Sprout.agent.loop")

if TYPE_CHECKING:
    from Sprout.context.context import AgentContext
    from Sprout.llm.base import ModelProvider
    from Sprout.message.models import Message


def _output_limit_notice(language):
    return ("\n\n[输出达到模型 token 上限；回答尚未完成。请回复“继续”分段获取后续内容。]"
            if language == "中文" or language.startswith("zh") else
            "\n\n[Model output token limit reached; this answer is incomplete. "
            "Reply 'continue' for the next part.]")


def _skill_message(context: AgentContext) -> LLMMessage | None:
    """Skill system message, honouring the disclosure mode (design §6.1).

    ``progressive`` injects the L0 index only (names + summaries); ``eager``
    keeps the legacy full-body injection.
    """
    if context.skill_disclosure == "eager":
        text = context.skill_instructions()
        heading = "Available skills"
    else:
        text = context.skills_index()
        heading = "Available skills (call skill_view for the full instructions)"
    if not text:
        return None
    return LLMMessage.system(f"{heading}:\n\n{text}")


def _memory_message(context: AgentContext) -> LLMMessage | None:
    """The memory block (summary, facts, recalled snippets), or ``None``.

    These live on ``ContextMemory`` *beside* its turns, and only the turns are
    reachable by iterating it — so they need their own message. Returns ``None``
    when the block would be empty, keeping the prompt unchanged for sessions
    with no curated memory.
    """
    from Sprout.memory.snapshot import memory_block_for

    block = memory_block_for(context.memory)
    return LLMMessage.system(block) if block else None


class AgentLoop:
    """Runs the model until it answers without requesting tools.

    Each step: chat with the model (passing every tool spec from the context),
    execute requested tool calls through the ActionExecutor, append results,
    repeat. ``max_steps`` bounds the loop.
    """

    def __init__(
        self,
        model: ModelProvider,
        *,
        executor: ActionExecutor | None = None,
        max_steps: int = 8,
        max_model_calls: int = 0,
        max_tool_calls: int = 0,
        max_tokens: int = 0,
        planner: Planner | None = None,
    ) -> None:
        self.model = model
        self.executor = executor
        self.max_steps = max_steps
        self.max_model_calls = max_model_calls
        self.max_tool_calls = max_tool_calls
        self.max_tokens = max_tokens
        self.planner = planner or DirectPlanner()

    async def run(self, message: Message, context: AgentContext) -> AgentResult:
        # System prompt first (anchors the prefix cache and orders priority),
        # then any skill instructions, then history, then the current user
        # message — that order keeps the cache warm across turns.
        messages: list[LLMMessage] = []
        response_language = _response_language(message)
        messages.append(
            LLMMessage.system(
                "You are SEAM Sprout, a self-evolving AI runtime for software projects. "
                "When users ask who you are or what you can do, answer as SEAM Sprout "
                "and describe project analysis, code generation, isolated execution, "
                "verification, integration, and task/storage/log/model/web management. "
                "Execute the user's natural-language instruction directly. Do not merely "
                "explain, ask for permission, or wait unless the request is truly "
                "ambiguous. Use available tools when they help complete the requested "
                "change; system-level safety and approvals are handled outside the model. "
                "Use only the tools supplied for this request. If a requested tool is "
                "unavailable, explain that without claiming execution. "
                f"Answer in language '{response_language}'. This is the configured "
                "response language for this turn, so even progress/status text such as "
                "'I'll start by analyzing the project structure' must use it. Keep code, "
                "commands, file paths, and identifiers unchanged. Only use another "
                "language when the user explicitly requests translation or a different "
                "response language."
            )
        )
        skill_message = _skill_message(context)
        if skill_message is not None:
            messages.append(skill_message)
        plan = await self.planner.plan(message, context)
        if plan.steps:
            messages.insert(
                1,
                LLMMessage.system("Plan:\n" + "\n".join(plan.steps)),
            )
        self._last_plan = tuple(plan.steps)
        memory_message = _memory_message(context)
        if memory_message is not None:
            messages.append(memory_message)
        messages.extend(history_messages(context.memory))
        messages.append(LLMMessage.user(message.content))

        specs = [tool.spec for tool in context.tools.values()]
        requested_by = str(context.user.get("id", "unknown"))
        usage = Usage()
        steps = 0
        model_calls = 0
        tool_calls = 0
        seen_calls = {}
        task_budget = context.metadata.get("task_budget", {})
        budget_model_calls = _budget_int(task_budget, "max_model_calls", self.max_model_calls)
        budget_tool_calls = _budget_int(task_budget, "max_tool_calls", self.max_tool_calls)
        budget_tokens = _budget_int(task_budget, "max_tokens", self.max_tokens)

        resume_calls = _tool_calls_from_metadata(context.metadata.get("resume_tool_calls"))
        if resume_calls:
            # A resumed tool result still belongs to the assistant tool-call
            # turn that originally requested it. Providers such as DeepSeek
            # reject a bare role=tool message without this preceding message.
            messages.append(LLMMessage.assistant("", resume_calls))
            tool_messages, tool_calls, approval_ids, tool_summary = await self._execute_tool_calls(
                resume_calls,
                requested_by=requested_by,
                session_id=context.session.id,
                tool_calls=tool_calls,
                budget_tool_calls=budget_tool_calls,
                node_tools=context.tools,
                seen_calls=seen_calls,
            )
            messages.extend(tool_messages)
            if tool_summary.get("workspace_requested"):
                return AgentResult(content="", metadata={"workspace_requested": True})
            blocked = _blocked_tool_result(
                tool_messages, tool_summary, response_language,
                steps=steps, model_calls=model_calls, tool_calls=tool_calls)
            if blocked is not None and not approval_ids:
                return blocked
            if approval_ids:
                return AgentResult(
                    content=(
                        "This action requires human approval. "
                        f"Approval ids: {', '.join(approval_ids)}"
                    ),
                    metadata={
                        "steps": steps,
                        "model_calls": model_calls,
                        "tool_calls": tool_calls,
                        "plan": self._last_plan,
                        "tool_summary": tool_summary,
                        "approval_required": True,
                        "approval_ids": approval_ids,
                        "pending_tool_calls": [
                            {
                                "id": call.id,
                                "name": call.name,
                                "arguments": dict(call.arguments),
                            }
                            for call in resume_calls
                        ],
                    },
                )

        while steps < self.max_steps:
            if budget_model_calls and model_calls >= budget_model_calls:
                break
            try:
                response = await invoke_model(self.model, messages, tools=specs)
            except ModelBudgetExhausted:
                raise
            except Exception as exc:
                fallback = _tool_result_fallback(messages, exc, response_language)
                if fallback is not None:
                    return AgentResult(
                        content=fallback,
                        metadata={
                            "steps": steps,
                            "model_calls": model_calls,
                            "tool_calls": tool_calls,
                            "plan": self._last_plan,
                            "model_error": f"{type(exc).__name__}: {exc}",
                            "degraded": True,
                        },
                    )
                raise
            steps += 1
            model_calls += 1
            usage = Usage(
                prompt_tokens=usage.prompt_tokens + response.usage.prompt_tokens,
                completion_tokens=usage.completion_tokens + response.usage.completion_tokens,
                total_tokens=usage.total_tokens + response.usage.total_tokens,
            )
            self._record_step(context.session.id, steps, response.content,
                              response.tool_calls, response.usage)
            if budget_tokens and usage.total_tokens >= budget_tokens:
                break
            if not response.tool_calls:
                return AgentResult(
                    content=response.text + (_output_limit_notice(response_language)
                                             if response.finish_reason == "length" else ""),
                    metadata={
                        "truncated": response.finish_reason == "length",
                        "steps": steps,
                        "finish_reason": response.finish_reason,
                        "model": response.model or getattr(self.model, "model", None),
                        "model_calls": model_calls,
                        "tool_calls": tool_calls,
                        "plan": self._last_plan,
                        "usage": {
                            "prompt_tokens": usage.prompt_tokens,
                            "completion_tokens": usage.completion_tokens,
                            "total_tokens": usage.total_tokens,
                        },
                    },
                )
            messages.append(LLMMessage.assistant(response.content, response.tool_calls))
            tool_messages, tool_calls, approval_ids, tool_summary = await self._execute_tool_calls(
                response.tool_calls,
                requested_by=requested_by,
                session_id=context.session.id,
                tool_calls=tool_calls,
                budget_tool_calls=budget_tool_calls,
                node_tools=context.tools,
                seen_calls=seen_calls,
            )
            if not tool_messages:
                continue
            messages.extend(tool_messages)
            if tool_summary.get("workspace_requested"):
                return AgentResult(content="", metadata={"workspace_requested": True})
            blocked = _blocked_tool_result(
                tool_messages, tool_summary, response_language,
                steps=steps, model_calls=model_calls, tool_calls=tool_calls)
            if blocked is not None and not approval_ids:
                return blocked
            if approval_ids:
                return AgentResult(
                    content=(
                        "This action requires human approval. "
                        f"Approval ids: {', '.join(approval_ids)}"
                    ),
                    metadata={
                        "steps": steps,
                        "model_calls": model_calls,
                        "tool_calls": tool_calls,
                        "plan": self._last_plan,
                        "tool_summary": tool_summary,
                        "approval_required": True,
                        "approval_ids": approval_ids,
                        "pending_tool_calls": [
                            {
                                "id": call.id,
                                "name": call.name,
                                "arguments": dict(call.arguments),
                            }
                            for call in response.tool_calls
                        ],
                    },
                )

        last_assistant = next(
            (m.content for m in reversed(messages) if m.role is Role.ASSISTANT and m.content),
            "",
        )
        # A resumed turn can inherit an approval prompt as its last assistant
        # text. Only current tool messages can describe what actually ran.
        resumed_output = (
            _tool_result_fallback(messages, None, response_language) if resume_calls else None
        )
        content = (
            (resumed_output if resume_calls else last_assistant)
            or "Reached the maximum number of tool steps."
        )
        return AgentResult(
            content=content,
            metadata={
                "steps": steps,
                "truncated": True,
                "model_calls": model_calls,
                "tool_calls": tool_calls,
                "plan": self._last_plan,
                "usage": {"total_tokens": usage.total_tokens},
            },
        )

    async def stream(
        self, message: Message, context: AgentContext, *, include_result: bool = False,
    ):
        """Run the same tool loop while yielding text chunks.

        Providers that expose ``stream_events`` stream both content and tool
        calls. Providers without it fall back to the non-streaming loop.
        Runtime can opt in to the existing AgentResult terminal; default text
        consumers keep their original token sequence and approval terminals.
        """
        messages: list[LLMMessage] = []
        response_language = _response_language(message)
        messages.append(
            LLMMessage.system(
                "You are SEAM Sprout, a self-evolving AI runtime for software projects. "
                "When users ask who you are or what you can do, answer as SEAM Sprout "
                "and describe project analysis, code generation, isolated execution, "
                "verification, integration, and task/storage/log/model/web management. "
                "Execute the user's natural-language instruction directly. Do not merely "
                "explain, ask for permission, or wait unless the request is truly "
                "ambiguous. Use available tools when they help complete the requested "
                "change; system-level safety and approvals are handled outside the model. "
                "Use only the tools supplied for this request. If a requested tool is "
                "unavailable, explain that without claiming execution. "
                f"Answer in language '{response_language}'. This is the configured "
                "response language for this turn, so even progress/status text such as "
                "'I'll start by analyzing the project structure' must use it. Keep code, "
                "commands, file paths, and identifiers unchanged. Only use another "
                "language when the user explicitly requests translation or a different "
                "response language."
            )
        )
        skill_message = _skill_message(context)
        if skill_message is not None:
            messages.append(skill_message)
        plan = await self.planner.plan(message, context)
        if plan.steps:
            messages.insert(
                1,
                LLMMessage.system("Plan:\n" + "\n".join(plan.steps)),
            )
        self._last_plan = tuple(plan.steps)
        memory_message = _memory_message(context)
        if memory_message is not None:
            messages.append(memory_message)
        messages.extend(history_messages(context.memory))
        messages.append(LLMMessage.user(message.content))

        specs = [tool.spec for tool in context.tools.values()]
        requested_by = str(context.user.get("id", "unknown"))
        steps = 0
        model_calls = 0
        tool_calls = 0
        seen_calls = {}
        task_budget = context.metadata.get("task_budget", {})
        budget_model_calls = _budget_int(task_budget, "max_model_calls", self.max_model_calls)
        budget_tool_calls = _budget_int(task_budget, "max_tool_calls", self.max_tool_calls)

        while steps < self.max_steps:
            if budget_model_calls and model_calls >= budget_model_calls:
                return
            stream_events = getattr(self.model, "stream_events", None)
            if stream_events is None:
                response = await invoke_model(self.model, messages, tools=specs)
                steps += 1
                model_calls += 1
                self._record_step(context.session.id, steps, response.content,
                                  response.tool_calls, response.usage)
                if not response.tool_calls:
                    if response.text:
                        yield response.text
                    if response.finish_reason == "length":
                        yield _output_limit_notice(response_language)
                    if include_result and response.finish_reason in {
                            None, "stop", "end_turn", "length"}:
                        yield AgentResult(content="", metadata={
                            "steps": steps, "model_calls": model_calls,
                            "finish_reason": response.finish_reason,
                            "model": response.model or getattr(self.model, "model", None),
                            "truncated": response.finish_reason == "length",
                            "usage": {"prompt_tokens": response.usage.prompt_tokens,
                                      "completion_tokens": response.usage.completion_tokens,
                                      "total_tokens": response.usage.total_tokens},
                            "tool_calls": tool_calls, "plan": self._last_plan,
                        })
                    return
                messages.append(LLMMessage.assistant(response.content, response.tool_calls))
                (
                    tool_messages,
                    tool_calls,
                    approval_ids,
                    tool_summary,
                ) = await self._execute_tool_calls(
                    response.tool_calls,
                    requested_by=requested_by,
                    session_id=context.session.id,
                    tool_calls=tool_calls,
                    budget_tool_calls=budget_tool_calls,
                    node_tools=context.tools,
                    seen_calls=seen_calls,
                )
                if not tool_messages:
                    continue
                messages.extend(tool_messages)
                if tool_summary.get("workspace_requested"):
                    yield AgentResult(content="", metadata={"workspace_requested": True})
                    return
                blocked = _blocked_tool_result(
                    tool_messages, tool_summary, response_language,
                    steps=steps, model_calls=model_calls, tool_calls=tool_calls)
                if blocked is not None and not approval_ids:
                    yield blocked if include_result else blocked.content
                    return
                if approval_ids:
                    yield AgentResult(
                        content="",
                        metadata=_approval_metadata(
                            steps=steps,
                            model_calls=model_calls,
                            tool_calls=tool_calls,
                            plan=self._last_plan,
                            approval_ids=approval_ids,
                            pending_tool_calls=response.tool_calls,
                        ),
                    )
                    return
                continue

            content_parts: list[str] = []
            final_tool_calls = ()
            finish_reason = None
            step_usage = Usage()
            model_name = getattr(self.model, "model", None)
            async for event in stream_events(messages, tools=specs):
                model_name = event.model or model_name
                if event.usage is not None:
                    step_usage = event.usage
                if event.finish_reason is not None:
                    finish_reason = event.finish_reason
                if event.content:
                    content_parts.append(event.content)
                    yield event.content
                if event.tool_calls:
                    final_tool_calls = event.tool_calls
            steps += 1
            model_calls += 1
            self._record_step(context.session.id, steps, "".join(content_parts),
                              final_tool_calls, step_usage)
            if not final_tool_calls:
                if finish_reason == "length":
                    yield _output_limit_notice(response_language)
                if include_result and finish_reason in {None, "stop", "end_turn", "length"}:
                    yield AgentResult(content="", metadata={
                        "steps": steps, "model_calls": model_calls,
                        "finish_reason": finish_reason, "truncated": finish_reason == "length",
                        "model": model_name,
                        "usage": {"prompt_tokens": step_usage.prompt_tokens,
                                  "completion_tokens": step_usage.completion_tokens,
                                  "total_tokens": step_usage.total_tokens},
                        "tool_calls": tool_calls, "plan": self._last_plan,
                    })
                return
            messages.append(LLMMessage.assistant("".join(content_parts), final_tool_calls))
            tool_messages, tool_calls, approval_ids, tool_summary = await self._execute_tool_calls(
                final_tool_calls,
                requested_by=requested_by,
                session_id=context.session.id,
                tool_calls=tool_calls,
                budget_tool_calls=budget_tool_calls,
                node_tools=context.tools,
                seen_calls=seen_calls,
            )
            if not tool_messages:
                continue
            messages.extend(tool_messages)
            if tool_summary.get("workspace_requested"):
                yield AgentResult(content="", metadata={"workspace_requested": True})
                return
            blocked = _blocked_tool_result(
                tool_messages, tool_summary, response_language,
                steps=steps, model_calls=model_calls, tool_calls=tool_calls)
            if blocked is not None and not approval_ids:
                yield blocked if include_result else blocked.content
                return
            if approval_ids:
                yield AgentResult(
                    content="",
                    metadata=_approval_metadata(
                        steps=steps,
                        model_calls=model_calls,
                        tool_calls=tool_calls,
                        plan=self._last_plan,
                        approval_ids=approval_ids,
                        pending_tool_calls=final_tool_calls,
                    ),
                )
                return

        return

    @staticmethod
    def _record_step(session_id, step, content, tool_calls, usage):
        try:
            get_agent_step_store().record(
                session_id=session_id, step=step, content=content, tool_calls=tool_calls,
                prompt_tokens=usage.prompt_tokens, completion_tokens=usage.completion_tokens,
                total_tokens=usage.total_tokens,
            )
        except sqlite3.Error as exc:
            # Derived telemetry failure cannot change the execution result.
            logger.warning("agent step telemetry unavailable: %s", exc)

    async def _execute_tool_calls(
        self,
        calls,
        *,
        requested_by: str,
        session_id: str = "",
        tool_calls: int,
        budget_tool_calls: int,
        node_tools: Mapping[str, Any] | None = None,
        seen_calls: dict | None = None,
    ) -> tuple[list[LLMMessage], int, list[str], dict[str, int]]:
        summary = {"success": 0, "failure": 0, "approval": 0}
        if self.executor is None:
            return (
                [
                    LLMMessage.tool_result(
                        calls[0].id,
                        calls[0].name,
                        "Tool execution is disabled for this agent.",
                    )
                ],
                tool_calls,
                [],
                summary,
            )
        if budget_tool_calls:
            remaining = max(0, budget_tool_calls - tool_calls)
            allowed_calls = calls[:remaining]
            skipped_calls = calls[remaining:]
        else:
            allowed_calls = calls
            skipped_calls = ()
        # Dispatch through this node's tool set when it has one. The agent is
        # shared across nodes, so the scope travels with the call rather than
        # living on the executor.
        executor = (
            self.executor.with_scoped_tools(node_tools, strict=True)
            if node_tools is not None else self.executor
        )
        outcomes = await executor.run_calls_detailed(
            allowed_calls,
            seen_calls=seen_calls,
            requested_by=requested_by,
            session_id=session_id,
        )
        messages = [
            LLMMessage.tool_result(
                outcome.call.id,
                outcome.call.name,
                outcome.result.to_text(),
            )
            for outcome in outcomes
        ]
        tool_calls += len(allowed_calls)
        approval_ids = list(dict.fromkeys(
            outcome.result.approval_id for outcome in outcomes if outcome.requires_approval
        ))
        for outcome in outcomes:
            error = outcome.result.error or ""
            if (error.startswith("Diagnostic mode refuses")
                    or error == "native_project_probe_unavailable"
                    or error.endswith("is denied by the security policy")):
                summary["execution_blocked"] = True
            if outcome.result.workspace_request:
                summary["workspace_requested"] = True
            if outcome.result.ok:
                summary["success"] += 1
            elif outcome.requires_approval:
                summary["approval"] += 1
            else:
                summary["failure"] += 1
        for call in skipped_calls:
            messages.append(
                LLMMessage.tool_result(
                    call.id,
                    call.name,
                    "Skipped: tool call budget exceeded",
                )
            )
        return messages, tool_calls, approval_ids, summary


def _blocked_tool_result(messages, summary, language, **metrics):
    if not summary.get("execution_blocked"):
        return None
    header = (
        "已停止后续工具尝试。以下是本轮工具返回的限制，"
        "不表示先前的项目分析数据不存在：" if language in {"中文", "繁體中文"} else
        "Stopped further tool attempts after an execution restriction. "
        "This does not invalidate earlier project analysis. Tool results:"
    )
    details = "\n\n".join(f"[{item.name}] {item.content}" for item in messages[-3:])
    return AgentResult(header + "\n\n" + details,
                       metadata={"execution_blocked": True, "tool_summary": summary, **metrics})


def _budget_int(budget: object, key: str, default: int) -> int:
    if not isinstance(budget, dict):
        return default
    value = budget.get(key, default)
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def _response_language(message: Message) -> str:
    configured = (
        message.metadata.get("response_language")
        or message.metadata.get("cli_language")
        or message.metadata.get("language")
    )
    if isinstance(configured, str) and configured.strip():
        return _language_label(configured.strip())
    return _language_label(detect_language(message.content))


def _language_label(language: str) -> str:
    labels = {
        "zh": "中文",
        "zh-cn": "中文",
        "zh_cn": "中文",
        "en": "English",
        "ja": "日本語",
        "jp": "日本語",
        "ko": "한국어",
        "kr": "한국어",
        "ru": "Русский",
        "es": "Español",
        "pt": "Português",
        "zh-hant": "繁體中文",
        "zh_tw": "繁體中文",
        "zh-tw": "繁體中文",
        "zh_hant": "繁體中文",
    }
    return labels.get(language.casefold(), language)


def _tool_result_fallback(
    messages: list[LLMMessage],
    error: Exception | None,
    language: str,
) -> str | None:
    tool_results = [
        message
        for message in messages
        if message.role is Role.TOOL and (message.content or "").strip()
    ]
    if not tool_results:
        return None
    header = _localized_tool_fallback_header(language)
    details = "\n\n".join(
        f"[{message.name or message.tool_call_id or 'tool'}]\n{message.content}"
        for message in tool_results[-3:]
    )
    suffix = f"\n\n{type(error).__name__}: {error}" if error is not None else ""
    return f"{header}\n\n{details}{suffix}"


def _localized_tool_fallback_header(language: str) -> str:
    headers = {
        "中文": "工具已执行，但模型暂时无法生成后续总结。下面是工具返回结果：",
        "繁體中文": "工具已執行，但模型暫時無法產生後續總結。以下是工具返回結果：",
        "English": (
            "The tool ran, but the model could not generate the follow-up summary. Tool result:"
        ),
        "日本語": "ツールは実行されましたが、モデルが後続の要約を生成できませんでした。ツール結果:",
        "한국어": "도구는 실행되었지만 모델이 후속 요약을 생성하지 못했습니다. 도구 결과:",
        "Русский": (
            "Инструмент выполнен, но модель не смогла создать итоговый ответ. "
            "Результат инструмента:"
        ),
        "Español": (
            "La herramienta se ejecutó, pero el modelo no pudo generar el resumen "
            "posterior. Resultado:"
        ),
        "Português": (
            "A ferramenta foi executada, mas o modelo não conseguiu gerar o resumo "
            "seguinte. Resultado:"
        ),
    }
    return headers.get(language, headers["English"])


def _approval_metadata(
    *,
    steps: int,
    model_calls: int,
    tool_calls: int,
    plan: tuple[str, ...],
    approval_ids: list[str],
    pending_tool_calls: tuple[ToolCall, ...],
) -> dict[str, object]:
    return {
        "steps": steps,
        "model_calls": model_calls,
        "tool_calls": tool_calls,
        "plan": plan,
        "approval_required": True,
        "approval_ids": approval_ids,
        "pending_tool_calls": [
            {
                "id": call.id,
                "name": call.name,
                "arguments": dict(call.arguments),
            }
            for call in pending_tool_calls
        ],
    }


def _tool_calls_from_metadata(value: object) -> tuple[ToolCall, ...]:
    if not isinstance(value, list):
        return ()
    calls: list[ToolCall] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        calls.append(
            ToolCall(
                id=str(item.get("id", "")),
                name=str(item.get("name", "")),
                arguments=dict(item.get("arguments") or {}),
            )
        )
    return tuple(calls)


__all__ = ["AgentLoop"]
