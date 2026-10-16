"""Scripted provider; deterministic fixture actions, not model-quality evidence."""

import json

from Sprout.llm.messages import LLMResponse, ToolCall


class ScriptedModel:
    name = "sprout-eval-script-v1"
    model = name

    def __init__(self, case):
        self.case = case
        self.written = set()
        self.calls = 0
        self.injected = False

    async def chat(self, messages, *, tools=()):
        self.calls += 1
        prompt = "\n".join(message.content or "" for message in messages)
        content = "Fixture analysis/plan complete."
        if "ACCEPTANCE_DESIGN_JSON" in prompt:
            content = json.dumps(self.case.acceptance)
        elif "output_schema" in prompt:
            content = json.dumps(
                {"mode": "modify", "strategy": "eval", "steps": [], "test_files": [], "risks": []}
            )
        elif self.case.mode == "security" and not self.injected:
            # Deliberately invent a tool even though read-only contexts offer none.
            self.injected = True
            return LLMResponse(
                content=None,
                tool_calls=(
                    ToolCall(
                        id="injection",
                        name="write_file",
                        arguments={"path": "app.py", "content": "compromised"},
                    ),
                ),
                finish_reason="tool_use",
                model=self.name,
            )
        elif "sandbox_write_file" in {tool.name for tool in tools}:
            for path, source in self.case.edits.items():
                if path not in self.written:
                    self.written.add(path)
                    return LLMResponse(
                        content=None,
                        tool_calls=(
                            ToolCall(
                                id=f"write-{len(self.written)}",
                                name="sandbox_write_file",
                                arguments={"path": "src/" + path, "content": source},
                            ),
                        ),
                        finish_reason="tool_use",
                        model=self.name,
                    )
            if self.case.mode == "delete" and not self.injected:
                self.injected = True
                return LLMResponse(
                    content=None,
                    tool_calls=(
                        ToolCall(
                            id="delete",
                            name="sandbox_apply_patch",
                            arguments={
                                "patch": "--- a/src/obsolete.py\n+++ /dev/null\n"
                                "@@ -1 +0,0 @@\n-value = 1\n"
                            },
                        ),
                    ),
                    finish_reason="tool_use",
                    model=self.name,
                )
        return LLMResponse(content=content, finish_reason="stop", model=self.name)
