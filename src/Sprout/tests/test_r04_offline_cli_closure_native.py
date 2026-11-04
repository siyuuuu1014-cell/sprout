"""Offline `sprout chat` acceptance: only model and terminal input are controlled.

Factory, compiler, storage, native I/O, approval consumption and Apply stay real.
"""
import asyncio
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import pytest
from typer.testing import CliRunner, _NamedTextIOWrapper

from Sprout.cli.app import app
from Sprout.cli.commands import chat
from Sprout.execution.acceptance import render_acceptance_demo, render_acceptance_tests
from Sprout.execution.process_broker import ProcessBroker
from Sprout.llm.messages import LLMResponse, ToolCall
from Sprout.llm.registry import ModelRegistry
from Sprout.runtime import factory
from Sprout.runtime.intent_analysis import response_example
from Sprout.runtime.runtime import Runtime
from Sprout.strategy.acceptance import AcceptanceSpec

pytestmark = pytest.mark.skipif(
    os.name != "nt" or os.environ.get("SPROUT_NATIVE_LOCAL_RECEIPT") != "1",
    reason="Explicit Windows native offline CLI acceptance required",
)

GIT = Path("D:/dev/Git/mingw64/bin/git.exe")
GIT_SHA = "cab4c4eea1d869cf9f7be73868dc9a90ad2df1b1b673e5f8c8714a576c25ea96"
OLD = "def clamp_zero(value):\n    return value\n"
NEW = "def clamp_zero(value):\n    return 0 if value < 0 else value\n"


def save(path, value):
    path.write_text(json.dumps(value, default=str, ensure_ascii=False, indent=2), "utf-8")


def test_cli_cold_import(tmp_path):
    result = subprocess.run([sys.executable, "-B", "-m", "Sprout.cli.app", "--help"],
                            capture_output=True, timeout=30)
    save(tmp_path / "COLD_CLI.json", {"exit_code": result.returncode,
        "stdout": result.stdout.decode("utf-8", "replace"),
        "stderr": result.stderr.decode("utf-8", "replace")})
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("retry", [False, True, "ruff"],
                         ids=["normal", "denied_then_retry", "ruff_then_retry"])
def test_offline_interactive_cli_closure(tmp_path, monkeypatch, retry):
    assert hashlib.sha256(GIT.read_bytes()).hexdigest() == GIT_SHA
    project = tmp_path / "project"
    project.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("PATH", str(GIT.parent) + os.pathsep + os.environ["PATH"])
    monkeypatch.delenv("TEMPORAL_HOST", raising=False)
    spec = AcceptanceSpec.from_payload({"cases": [
        {"id": "positive", "target": "src.app:clamp_zero", "args": [3],
         "expected_output": 3},
        {"id": "negative", "target": "src.app:clamp_zero", "args": [-2],
         "expected_output": 0}]})
    files = {
        "src/__init__.py": "", "src/app.py": OLD,
        "tests/test_app.py": "from src.app import clamp_zero\n\n\ndef test_positive():\n"
            "    assert clamp_zero(3) == 3\n\n\ndef test_negative():\n"
            "    assert clamp_zero(-2) == 0\n",
        "examples/demo.py": "from src.app import clamp_zero\n\nprint(clamp_zero(-2))\n",
        "pyproject.toml": '[project]\nname = "offline-cli"\nversion = "0.1.0"\n'
            '[tool.pytest.ini_options]\ntestpaths = ["tests"]\npythonpath = ["."]\n',
        "tests/test_acceptance.py": render_acceptance_tests(spec),
        "examples/acceptance_demo.py": render_acceptance_demo(spec),
    }
    for name, text in files.items():
        path = project / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(text.encode())
    git_env = {k: v for k, v in os.environ.items()
               if k in {"SystemRoot", "TEMP", "TMP", "PATH"}}
    git_env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                   GIT_TERMINAL_PROMPT="0")
    for args in [("init",), ("add", "--", *files), ("commit", "-m", "offline baseline")]:
        subprocess.run([str(GIT), "-c", "core.hooksPath=NUL", "-c", "commit.gpgSign=false",
            "-c", "user.name=Offline fixture", "-c", "user.email=offline@example.invalid",
            "-C", str(project), *args], env=git_env, check=True, capture_output=True, timeout=20)
    spec_path = tmp_path / "spec.json"
    save(spec_path, spec.to_payload())
    config = tmp_path / "sprout.toml"
    data = tmp_path / "data"
    data.mkdir()
    def quote(p):
        return json.dumps(str(p).replace("\\", "/"))
    settings = ['skills_dir = ' + quote(home / "skills"), '[model]', 'provider = "aiyallm"',
        'model = "offline-controlled-substitution"',
        '[storage]']
    for field in ("operational", "knowledge", "metadata", "session", "context", "usage"):
        settings.append(f'{field} = "sqlite:///{(data / (field + ".db")).as_posix()}"')
    for field in ("blobs_dir", "trajectory_dir", "project_root"):
        settings.append(f"{field} = {quote(data / field)}")
    settings += ['[storage.observations]', 'enabled = false', '[memory]', 'enabled = false',
        '[security.audit]', 'path = ' + quote(data / "security.jsonl"),
        '[security.rules.workspace]',
        quote("directory.list:" + str(project)) + ' = "allow"',
        quote("directory.list:" + str(project / "**")) + ' = "allow"']
    config.write_text("\n".join(settings), "utf-8")
    monkeypatch.chdir(project)
    events, calls, snapshots, inputs, stages = [], [], [], [], {}
    current = {"message": "", "session": ""}

    class ControlledModel:
        name = "offline-cli-controlled"

        async def chat(self, messages, **kwargs):
            text = "\n".join(str(m.content or "") for m in messages)
            classifier = "Classify the user's intent" in str(messages[0].content)
            response = LLMResponse(content="Offline response.")
            if classifier:
                instruction = current["message"]
                intent = "task" if "修复 src/app.py" in instruction else (
                    "workspace" if "分析当前项目" in instruction else "conversation")
                action = {"task": "code_modification", "workspace": "read_only_analysis",
                          "conversation": "conversation"}[intent]
                value = json.loads(response_example(True))
                value.update(intent=intent, confidence=0.99, reason="controlled offline intent")
                value["semantic"].update(action_mode=action,
                    candidates=[{"intent": intent, "action_mode": action}], ambiguity_type="none",
                    missing_information=[], needs_clarification=False, question="")
                response = LLMResponse(content=json.dumps(value))
            elif "For code changes, prefer sandbox_apply_patch" in text:
                feedback = "Previous verification feedback:" in text
                step = stages.get(feedback, 0)
                stages[feedback] = step + 1
                if step == 0:
                    response = LLMResponse(tool_calls=(ToolCall("read-" + str(feedback),
                        "sandbox_read_file", {"path": "src/app.py"}),))
                elif step == 1:
                    denied = retry is True and not feedback
                    content = "import os\n\n" + NEW if retry == "ruff" and not feedback else NEW
                    response = LLMResponse(tool_calls=(ToolCall("write-" + str(feedback),
                        "sandbox_write_file", {"path": "tests/test_app.py" if denied
                            else "src/app.py", "content": "forbidden" if denied else content}),))
                else:
                    response = LLMResponse(content="Attempt finished; use actual verification.")
            elif "Produce a concrete implementation plan" in text:
                response = LLMResponse(content="Only replace src/app.py; preserve frozen tests.")
            elif "host-project-analysis" in text or any(m.role == "tool" for m in messages):
                response = LLMResponse(content="ANALYSIS_RESULT: src.app defines clamp_zero; "
                    "tests verify positive and negative behavior; examples.demo imports src.app.")
            elif "进一步解释" in current["message"]:
                assert "ANALYSIS_RESULT" in text
                response = LLMResponse(content="FOLLOWUP_RESULT: tests and demo depend on src.app.")
            elif "恢复" in current["message"]:
                # Project publications (including sourced follow-ups) deliberately
                # stay out of plain history. Original user dialogue and the
                # ordinary Task reply must survive alongside the owned Apply row.
                assert "分析当前项目结构和依赖" in text
                assert "waiting_approval" in text and "COMPLETED" in text
                response = LLMResponse(content="RESTORED_RESULT: clamp_zero was fixed and applied.")
            calls.append({"messages": [asdict(m) for m in messages], "response": asdict(response),
                          "classifier": classifier})
            save(tmp_path / "MODEL_CALLS.json", calls)
            return response

    def models(_settings):
        registry = ModelRegistry()
        registry.register(ControlledModel(), default=True)
        return registry

    monkeypatch.setattr(factory, "create_model_registry", models)
    # A missing offline substitution must fail rather than contact a provider.
    monkeypatch.setattr(factory, "_build_provider", lambda *a, **k: pytest.fail("online provider"))
    original_start, original_stop = Runtime.start, Runtime.stop
    robots = {}
    process_records = []
    verification_records = []
    broker_run = ProcessBroker.run_evaluation_command

    async def observed_verification(broker, *args, **kwargs):
        result = await broker_run(broker, *args, **kwargs)
        verification_records.append(asdict(result))
        save(tmp_path / "VERIFICATION_RESULTS.json", verification_records)
        return result

    monkeypatch.setattr(ProcessBroker, "run_evaluation_command", observed_verification)
    spawn = asyncio.create_subprocess_exec

    async def observed_spawn(*argv, **kwargs):
        process = await spawn(*argv, **kwargs)
        process_records.append((process, {"argv": argv, "cwd": kwargs.get("cwd"),
                                          "pid": process.pid}))
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", observed_spawn)

    async def approval_robot(runtime):
        seen = set()
        while True:
            for record in await runtime.approvals.store.list_approvals():
                if record.status.value != "pending" or record.id in seen:
                    continue
                if record.tool == "file.apply":
                    continue  # Only the foreground exact-Plan CLI prompt may decide this.
                seen.add(record.id)
                argv = [sys.executable, "-B", "-m", "Sprout.cli.app", "approvals", "approve",
                        record.id, "--no-resume", "--config", str(config)]
                process = await asyncio.create_subprocess_exec(
                    *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                out, err = await process.communicate()
                events.append({"kind": "simulated_user_cli_approval", "record": asdict(record),
                    "argv": argv, "pid": process.pid, "exit_code": process.returncode,
                    "stdout": out.decode("utf-8", "replace"),
                    "stderr": err.decode("utf-8", "replace")})
                save(tmp_path / "APPROVALS.json", events)
                assert process.returncode == 0, events[-1]
            await asyncio.sleep(0.05)

    async def started(runtime):
        await original_start(runtime)
        robots[runtime] = asyncio.create_task(approval_robot(runtime))
        owner = asyncio.current_task()

        def failed(robot):
            if not robot.cancelled() and robot.exception() is not None:
                owner.cancel()

        robots[runtime].add_done_callback(failed)

    async def stopping(runtime):
        robot = robots.pop(runtime, None)
        if robot:
            robot.cancel()
            await asyncio.gather(robot, return_exceptions=True)
        tasks = await runtime.storage.metadata.list_tasks()
        session_ids = {t.metadata.get("session_id") for t in tasks if t.metadata.get("session_id")}
        sessions = [await runtime.get_session(sid) for sid in sorted(session_ids)]
        nodes, proposals, transactions = [], [], []
        for task in tasks:
            nodes.extend(await runtime.storage.metadata.list_execution_nodes(task.id))
            for proposal in await runtime.list_change_proposals(task.id):
                proposals.append(proposal)
                transactions.append(await runtime.storage.metadata.get_apply_transaction(
                    str(uuid5(NAMESPACE_URL, "ordinary-apply:" + proposal.id))))
        receipts = [{"receipt": str(r), "payload": p[0]}
                    for r, p in runtime._local_acceptance_receipts.items()]
        sources = {}
        for node in nodes:
            source = await runtime.storage.metadata.get_node_result_source(node.id)
            if source is not None:
                sources[node.id] = asdict(source)
        snapshots.append({"tasks": [asdict(t) for t in tasks],
            "sessions": [asdict(s) for s in sessions], "nodes": [asdict(n) for n in nodes],
            "proposals": [asdict(p) for p in proposals], "transactions": transactions,
            "receipts": receipts, "sources": sources})
        save(tmp_path / "RUNTIME_SNAPSHOTS.json", snapshots)
        save(tmp_path / "PROCESSES.json", [{**record, "exit_code": proc.returncode}
                                           for proc, record in process_records])
        await original_stop(runtime)
        await runtime.storage.close()

    monkeypatch.setattr(Runtime, "start", started)
    monkeypatch.setattr(Runtime, "stop", stopping)
    # Input adapters stand in for a terminal user, not for business handlers.
    monkeypatch.setattr(_NamedTextIOWrapper, "isatty", lambda self: self.name == "<stdin>")

    class Prompt:
        def __init__(self, value):
            self.value = value

        async def ask_async(self):
            return self.value

    def prompt(kind, message, **kwargs):
        text = str(message)
        inputs.append({"kind": kind, "prompt": text})
        if kind == "text" and "entire exact Apply Plan" in text:
            answer = text.split("Type ", 1)[1].split(" to confirm:", 1)[0]
            inputs[-1]["answer"] = answer  # Explicitly simulated terminal user.
            return Prompt(answer)
        if kind == "select":
            return Prompt("current")
        if kind == "confirm":
            return Prompt(True)
        options = {"Task model request budget": "24",
            "READ inputs": ",".join(files), "WRITE targets": "src/app.py",
            "Acceptance specification": str(spec_path), "Frozen acceptance test":
            "tests/test_acceptance.py", "Frozen acceptance Demo": "examples/acceptance_demo.py",
            "Trusted Git executable absolute": str(GIT), "Trusted Git executable SHA256": GIT_SHA}
        return Prompt(next(value for key, value in options.items() if key in text))

    for kind in ("text", "select", "confirm"):
        monkeypatch.setattr(chat.questionary, kind,
                            lambda message, _kind=kind, **kw: prompt(_kind, message, **kw))
    lines = iter(["分析当前项目结构和依赖，暂时不要修改代码",
                  "进一步解释核心模块之间的关系，不重新扫描",
                  "修复 src/app.py 中的负数错误，使负数返回0，非负数保持原值，并运行测试", "/exit"])

    async def readline():
        value = next(lines)
        current["message"] = value
        return value

    monkeypatch.setattr(chat, "_readline_with_slash_hint", readline)
    result = CliRunner().invoke(app, ["chat", "--config", str(config)])
    (tmp_path / "CLI_FIRST.log").write_text(result.output, "utf-8")
    save(tmp_path / "CLI_RESULT.json", {"exit_code": result.exit_code,
                                      "exception": repr(result.exception), "inputs": inputs})
    assert result.exit_code == 0, (result.output, result.exception)
    assert snapshots and len(snapshots[0]["proposals"]) == 1, snapshots
    first = snapshots[0]
    code_tasks = [task for task in first['tasks'] if task['metadata'].get('cli_write_paths')]
    assert len(code_tasks) == 1
    assert code_tasks[0]['budget']['max_model_calls'] == 24
    assert 'Test files to add or update:' not in code_tasks[0]['instruction']
    assert 'WRITE targets (exclusive):\n- src/app.py' in code_tasks[0]['instruction']
    assert first["transactions"][0]["phase"] == "COMPLETED"
    assert len(first["receipts"]) == 1
    approved_file = first["proposals"][0]["metadata"]["apply_plan"]["files"][0]
    assert (project / "src/app.py").read_bytes() == approved_file["new_text"].encode()
    assert (project / "src/app.py").read_text() == NEW
    assert hashlib.sha256((project / "src/app.py").read_bytes()).hexdigest() == (
        approved_file["after"])
    assert all((project / p).read_bytes() == text.encode() for p, text in files.items()
               if p != "src/app.py")
    assert len([i for i in inputs if "entire exact Apply Plan" in i["prompt"]]) == 1
    assert "ANALYSIS_RESULT" in result.output and "FOLLOWUP_RESULT" in result.output
    assert len({m["tool_call_id"] for c in calls for m in c["messages"]
                if m["role"] == "tool" and m["name"] == "project_analyze"}) == 1
    analyzed = next(m["content"] for c in calls for m in c["messages"]
                    if m["role"] == "tool" and m["name"] == "project_analyze")
    assert all(term in analyzed for term in ("Graph:", "clamp_zero", "imports:", "offline-cli"))
    session_id = first["sessions"][0]["id"]
    code_task = first["proposals"][0]["task_id"]
    agents = [n for n in first["nodes"] if n["type"] == "agent" and n["task_id"] == code_task]
    evaluations = [n for n in first["nodes"] if n["type"] == "evaluation"
                   and n["task_id"] == code_task and n["status"] == "completed"]
    checks = evaluations[-1]["metadata"]["output"]["results"]
    assert len(checks) == 7 and all(r["exit_code"] == 0 and r["passed"] for r in checks)
    git = first["proposals"][0]["metadata"]["git_diff"]
    assert git["status"] == "available" and len(git["commands"]) == 5
    assert git["changed_paths"] == ["src/app.py"]
    assert all(proc.returncode is not None for proc, _ in process_records)
    if retry:
        assert any("Previous verification feedback:" in str(c) for c in calls)
        assert all(n["metadata"]["execution_guard"]["state"] == "completed" for n in agents)
        assert len(agents) == 2 and len(evaluations) == 2
        assert evaluations[0]["metadata"]["output"]["status"] == "failed"
        if retry is True:
            assert "node_tool_binding_denied" in str(calls)
        else:
            first_checks = evaluations[0]["metadata"]["output"]["results"]
            assert any(r["command"] == ["ruff", "check", "src/app.py"]
                       and r["exit_code"] == 1 for r in first_checks)
            assert any(r["exit_code"] == 1 and "F401" in str(r)
                       for r in verification_records)
        for node in [*agents, *evaluations]:
            assert first["sources"][node["id"]]["owner"] == (
                node["metadata"]["execution_guard"]["owner"])
    monkeypatch.chdir(home)
    lines = iter(["恢复后请说明上一轮修改和验证结果", "/exit"])
    resumed = CliRunner().invoke(app, ["chat", "--config", str(config), "--session", session_id])
    (tmp_path / "CLI_RESTART.log").write_text(resumed.output, "utf-8")
    assert resumed.exit_code == 0, (resumed.output, resumed.exception)
    assert "RESTORED_RESULT" in resumed.output
    assert snapshots[1]["sessions"][0]["id"] == session_id
    assert snapshots[1]["sessions"][0]["metadata"]["workspace_id"] == (
        first["sessions"][0]["metadata"]["workspace_id"])
    with sqlite3.connect((data / "metadata.db").as_uri() + "?mode=ro", uri=True) as db:
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        save(tmp_path / "SQLITE_TABLES.json", tables)
        rows = db.execute("SELECT * FROM isolated_git_transactions "
                          "WHERE operation='apply_v1'").fetchall()
        save(tmp_path / "SQLITE_INDEPENDENT.json", rows)
        assert len(rows) == 1 and "COMPLETED" in str(rows)
    checked = subprocess.run([sys.executable, "-B", "-m", "pytest", "-q"], cwd=project,
                             capture_output=True, timeout=30)
    save(tmp_path / "POST_APPLY_TEST.json", {"exit_code": checked.returncode,
        "stdout": checked.stdout.decode("utf-8", "replace"),
        "stderr": checked.stderr.decode("utf-8", "replace")})
    assert checked.returncode == 0
    save(tmp_path / "FINAL.json", {"session_id": session_id, "retry": retry,
        "before_sha256": hashlib.sha256(OLD.encode()).hexdigest(),
        "after_sha256": hashlib.sha256((project / "src/app.py").read_bytes()).hexdigest(),
        "first_cli_exit": result.exit_code, "restart_cli_exit": resumed.exit_code})
