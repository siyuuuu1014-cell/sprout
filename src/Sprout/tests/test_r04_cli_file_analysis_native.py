"""Controlled terminal/model; actual Windows CLI/Runtime/Broker analysis.

Wrappers observe actual results/exceptions; none supply successful read or tool
results. All databases/configuration/project copies belong to tmp_path.
"""
import json
import os
from dataclasses import asdict
from pathlib import Path

import pytest
from typer.testing import CliRunner, _NamedTextIOWrapper

from Sprout.cli.app import app
from Sprout.cli.commands import chat
from Sprout.execution import file_broker
from Sprout.execution.file_broker import FileBroker
from Sprout.llm.messages import LLMResponse
from Sprout.llm.registry import ModelRegistry
from Sprout.runtime import factory
from Sprout.runtime.intent_analysis import response_example
from Sprout.runtime.runtime import Runtime
from Sprout.tools.executor import ToolExecutor
from Sprout.workspace.authorized_content import ContentLimits

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Real Windows file analysis")


def save(path, value):
    path.write_text(json.dumps(value, default=str, ensure_ascii=False, indent=2), "utf-8")


@pytest.mark.parametrize("case", ["small", "large", "missing", "denied", "oversize", "recovery",
                                  "total_budget", "metrics", "metrics_denied"])
def test_cli_selected_file_analysis(tmp_path, monkeypatch, case):
    real = case == "real"  # Explicit external runner only; never selected by pytest.
    project, home, data = (tmp_path / name for name in ("project", "home", "data"))
    for directory in (home, data):
        directory.mkdir()
    if real:
        project = Path("D:/dev/projects/sprout")
    else:
        project.mkdir()
    sources = Path(__file__).parents[1] / "runtime"
    paths = ("src/Sprout/runtime/dialogue.py", "src/Sprout/runtime/runtime.py")
    if not real:
        for label in paths:
            target = project / label
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((sources / Path(label).name).read_bytes())
        (project / "oversize.py").write_bytes(b"# bounded input\n" * 75_000)
    selected = {"small": paths[0], "large": paths[1], "missing": "missing.py",
                "denied": paths[0], "oversize": "oversize.py", "recovery": "oversize.py",
                "real": paths[1], "total_budget": "total0.py",
                "metrics": paths[1], "metrics_denied": paths[1]}[case]
    manifest = [f"total{i}.py" for i in range(9)]
    if case == "total_budget":
        for label in manifest:
            (project / label).write_bytes(b"#bounded\n" * 100_000)
    config = tmp_path / "sprout.toml"
    lines = ['skills_dir=' + json.dumps(str(home / "skills")), '[model]',
             'provider="aiyallm"', 'model="controlled-file-analysis"', '[storage]']
    if real:
        lines = lines[:-3] + ['provider="openai_compatible"', 'model="deepseek-flash"',
            'base_url="https://api.deepseek.com"', 'api_key_env="DEEPSEEK_API_KEY"',
            'thinking_mode="disabled"', 'max_output_tokens=8192', 'timeout_seconds=120.0',
            '[storage]']
    for key in ("operational", "metadata", "knowledge", "session", "context", "usage"):
        lines.append(f'{key}="sqlite:///{(data / (key + ".db")).as_posix()}"')
    for key in ("blobs_dir", "trajectory_dir", "project_root"):
        lines.append(key + "=" + json.dumps(str(data / key)))
    lines += ['[storage.observations]', 'enabled=false', '[memory]', 'enabled=false',
              '[security.audit]', 'path=' + json.dumps(str(data / "audit.jsonl"))]
    if case in {"denied", "metrics_denied"}:
        lines += ['[security.rules.workspace]',
                  json.dumps("file.read:" + str(project / selected)) + '="deny"']
    config.write_text("\n".join(lines), "utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("TEMPORAL_HOST", raising=False)
    observations = {"tools": [], "snapshots": [], "primitive_errors": [],
                    "policies": [], "nodes": [], "tasks": [], "models": [], "inputs": []}

    class Model:
        name = "controlled-file-analysis"

        async def chat(self, messages, **kwargs):
            text = "\n".join(str(m.content or "") for m in messages)
            if "Classify the user's intent" in text:
                value = json.loads(response_example(True))
                value.update(intent="workspace", confidence=.99)
                value["semantic"].update(action_mode="read_only_analysis",
                    candidates=[{"intent": "workspace", "action_mode": "read_only_analysis"}],
                    ambiguity_type="none", missing_information=[], needs_clarification=False,
                    question="")
                response = LLMResponse(content=json.dumps(value))
            elif any(m.role == "tool" and m.name == "project_analyze" for m in messages):
                content = "ANALYZED: actual graph received" if "Graph:" in text else "NO_ANALYSIS"
                response = LLMResponse(content=content)
            else:
                response = LLMResponse(content="FOLLOWUP: use completed analysis")
            observations["models"].append({"messages": [asdict(m) for m in messages],
                                           "response": asdict(response)})
            return response

    def registry(settings):
        result = ModelRegistry()
        result.register(Model(), default=True)
        return result

    if not real:
        monkeypatch.setattr(factory, "create_model_registry", registry)
        monkeypatch.setattr(factory, "_build_provider",
                            lambda *a, **kw: pytest.fail("online model"))
    else:
        original_registry = factory.create_model_registry

        def real_registry(settings):
            result = original_registry(settings)
            model = result.default()
            original_chat = model.chat

            async def observed_chat(messages, **kwargs):
                response = await original_chat(messages, **kwargs)
                observations["models"].append({"messages": [asdict(m) for m in messages],
                                               "response": asdict(response)})
                return response

            monkeypatch.setattr(model, "chat", observed_chat)
            return result

        monkeypatch.setattr(factory, "create_model_registry", real_registry)
    original_execute = ToolExecutor.execute

    async def execute(executor, name, *args, **kwargs):
        result = await original_execute(executor, name, *args, **kwargs)
        if name == "project_analyze":
            observations["tools"].append(asdict(result))
        return result

    monkeypatch.setattr(ToolExecutor, "execute", execute)
    original_read = FileBroker.read_snapshot

    async def read(broker, sandbox, path, **kwargs):
        result = await original_read(broker, sandbox, path, **kwargs)
        observations["snapshots"].append({"path": path, "max_bytes": kwargs["max_bytes"],
            "ok": result.ok, "reason": result.reason, "identity": asdict(result.identity)
            if result.identity else None, "bytes": len(result.data), "sha256": result.digest})
        return result

    monkeypatch.setattr(FileBroker, "read_snapshot", read)
    primitive = file_broker.file_operation

    def operation(*args, **kwargs):
        try:
            return primitive(*args, **kwargs)
        except OSError as exc:
            observations["primitive_errors"].append({"type": type(exc).__name__,
                                                      "reason": str(exc)})
            raise

    monkeypatch.setattr(file_broker, "file_operation", operation)
    policy = Runtime._record_project_capture_policy

    async def record(runtime, request, decision):
        if request.action.value == "file.read":
            observations["policies"].append({"path": request.resource.path,
                "decision": asdict(decision), "scope": asdict(request.scope)})
        return await policy(runtime, request, decision)

    monkeypatch.setattr(Runtime, "_record_project_capture_policy", record)
    stop = Runtime.stop

    async def stopping(runtime):
        observations["approvals"] = [
            asdict(record) for record in await runtime.approvals.store.list_approvals()
        ]
        for task in await runtime.storage.metadata.list_tasks():
            observations["tasks"].append(asdict(task))
            observations["nodes"].extend(asdict(n) for n in
                await runtime.storage.metadata.list_execution_nodes(task.id))
        await stop(runtime)
        await runtime.storage.close()

    monkeypatch.setattr(Runtime, "stop", stopping)
    monkeypatch.setattr(_NamedTextIOWrapper, "isatty", lambda self: self.name == "<stdin>")

    class Answer:
        def __init__(self, value=True):
            self.value = value

        async def ask_async(self):
            return self.value

    def confirm(message, **kwargs):
        observations["inputs"].append(str(message))
        return Answer()

    monkeypatch.setattr(chat.questionary, "confirm", confirm)
    monkeypatch.setattr(chat.questionary, "select", lambda *a, **kw: Answer("current"))
    monkeypatch.setattr(chat.questionary, "text", lambda *a, **kw: Answer(json.dumps(manifest)))
    sequence = [f"分析 {selected} 的结构"]
    if case in {"metrics", "metrics_denied"}:
        sequence = [f"统计{selected}的总行数、代码行数和注释行数"]
    if case == "metrics":
        sequence.append("刚才统计的文件有多少注释行？")
    if case == "total_budget":
        sequence = ["分析 " + "、".join(manifest) + " 的结构"]
    if case in {"small", "large"}:
        sequence.append("继续解释刚才分析的核心模块")
    if case == "recovery":
        sequence.append(f"分析 {paths[0]} 的结构")
    sequence = iter([*sequence, "/exit"])

    async def readline():
        return next(sequence)

    monkeypatch.setattr(chat, "_readline_with_slash_hint", readline)
    monkeypatch.chdir(project)
    result = CliRunner().invoke(app, ["chat", "--config", str(config)])
    observations.update(case=case, exit=result.exit_code, exception=repr(result.exception),
        limits=asdict(ContentLimits()), sizes={p: (project / p).stat().st_size for p in paths})
    save(tmp_path / "OBSERVATIONS.json", observations)
    (tmp_path / "CLI.log").write_text(result.output, "utf-8")
    assert result.exit_code == 0, (result.output, result.exception)
    if case == "metrics":
        assert len(observations["tools"]) == 1, observations
        tool = observations["tools"][0]
        assert tool["ok"], observations
        metrics = tool["data"]
        assert metrics["total_lines"] == len((project / selected).read_bytes().splitlines())
        assert metrics["code_lines"] + metrics["blank_lines"] + metrics["comment_lines"] == (
            metrics["total_lines"])
        assert "Total lines" in result.output
        assert any("Comment-only lines" in str(message["content"])
                   for model in observations["models"] for message in model["messages"])
        assert not any(record["tool"] == "cli_tool_run"
                       for record in observations["approvals"])
        assert len(observations["tasks"]) == 1
    elif case in {"small", "large", "recovery", "real"}:
        success = [t for t in observations["tools"] if t["ok"]]
        assert len(success) == 1, observations
        if not real:
            assert "ANALYZED" in result.output
        assert any(n["metadata"].get("output", {}).get("analysis_ok")
                   and n["metadata"]["execution_guard"]["state"] == "completed"
                   for n in observations["nodes"])
        if case in {"large", "real"}:
            assert any(s["ok"] and s["bytes"] > 300_000 for s in observations["snapshots"])
            assert "class" in success[0]["data"]["graph"]["node_kinds"]
        if case not in {"recovery", "real"}:
            assert len(observations["tools"]) == 1, "follow-up must not rescan"
            assert "FOLLOWUP" in result.output
    expected = {"missing": "project_capture_file_not_found",
                "denied": "analysis_file_policy_denied",
                "oversize": "project_capture_file_too_large",
                "recovery": "project_capture_file_too_large",
                "total_budget": "project_capture_total_budget"}
    error_case = "denied" if case == "metrics_denied" else case
    if error_case in expected:
        assert expected[error_case] in result.output, observations
    if error_case in {"missing", "denied", "oversize", "total_budget"}:
        assert not any(t["ok"] for t in observations["tools"])
    if case == "oversize":
        assert "1048576" in result.output
    if case == "total_budget":
        assert sum(s["bytes"] for s in observations["snapshots"]) <= 8_000_000
        assert "8000000" in result.output
    assert len({t["metadata"]["session_id"] for t in observations["tasks"]}) == 1
    assert len({t["workspace_id"] for t in observations["tasks"]}) == 1
