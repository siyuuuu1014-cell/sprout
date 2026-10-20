"""Generated acceptance and PoC sources obey the locked formatter contract."""

import json
import subprocess
import sys
from hashlib import sha256
from types import ModuleType

import pytest

from Sprout.execution import acceptance
from Sprout.strategy.acceptance import AcceptanceSpec


@pytest.mark.parametrize("kind", ["tests", "demo"])
@pytest.mark.parametrize("values", [
    {"args": ["hi"], "expected_output": "hi"},
    {"args": [], "kwargs": {"value": "x" * 160}, "expected_output": "x" * 160},
])
def test_generated_source_locked_ruff(tmp_path, kind, values):
    spec = AcceptanceSpec.from_payload({"cases": [{
        "id": "normal", "target": "sample:echo", **values,
    }]})
    render = (acceptance.render_acceptance_tests if kind == "tests"
              else acceptance.render_acceptance_demo)
    source = render(spec)
    path = tmp_path / (kind + ".py")
    path.write_text(source, encoding="utf-8")
    version = subprocess.run([sys.executable, "-m", "ruff", "--version"],
                             capture_output=True, text=True, check=True)
    assert version.stdout.strip() == "ruff 0.16.8"
    result = subprocess.run([
        sys.executable, "-m", "ruff", "check", "--isolated", "--no-cache",
        "--select", "PIE804,RUF100,E501", "--line-length", "100",
        "--output-format", "json", str(path),
    ], capture_output=True, text=True)
    (tmp_path / "ruff.json").write_text(result.stdout, encoding="utf-8")
    assert result.returncode == 0, result.stdout
    assert json.loads(result.stdout) == []


def test_new_generated_content_hash():
    spec = AcceptanceSpec.from_payload({"cases": [{
        "id": "normal", "target": "sample:echo", "args": ["hi"], "expected_output": "hi",
    }]})
    command = acceptance.local_acceptance_command(spec, "tests/test_io.py", "normal", {})
    assert json.loads(command[-1])["hash"] == sha256(
        acceptance.render_acceptance_tests(spec).encode()).hexdigest()


@pytest.mark.parametrize("old_file", [False, True])
def test_changed_source_does_not_adopt_frozen_evidence(tmp_path, old_file):
    spec = AcceptanceSpec.from_payload({"cases": [{
        "id": "normal", "target": "sample:echo", "args": ["hi"], "expected_output": "hi",
    }]})
    source = acceptance.render_acceptance_tests(spec)
    old_source = source.replace("target(*('hi',))", "target(*('hi',), **{})  # noqa: E501")
    assert old_source != source
    binding = acceptance.local_executor_binding()
    command = list(acceptance.local_acceptance_command(spec, "test_io.py", "normal", binding))
    if old_file:
        (tmp_path / "test_io.py").write_text(old_source, encoding="utf-8")
    else:
        (tmp_path / "test_io.py").write_text(source, encoding="utf-8")
        payload = json.loads(command[-1])
        payload["hash"] = sha256(old_source.encode()).hexdigest()
        command[-1] = json.dumps(payload)
    result = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 1
    marker = "SPROUT_LOCAL_ACCEPTANCE_V1:"
    records = [json.loads(line.removeprefix(marker)) for line in result.stdout.splitlines()
               if line.startswith(marker)]
    assert len(records) == 1
    assert records[0]["identity_matches"] is True
    assert records[0]["category"] == "unknown"
    assert records[0]["collected"] is False
    assert records[0]["executed"] is False


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("correct", [False, True])
def test_public_call_and_oracle_unchanged(monkeypatch, tmp_path, asynchronous, correct):
    module = ModuleType("r02_format_example")

    def echo(*args, **kwargs):
        assert args == ([1, 2],)
        assert kwargs == {"suffix": "ok"}
        return {"result": [1, 2], "suffix": "ok"}

    async def async_echo(*args, **kwargs):
        return echo(*args, **kwargs)

    module.echo = async_echo if asynchronous else echo
    monkeypatch.setitem(sys.modules, module.__name__, module)
    expected = {"result": [1, 2], "suffix": "ok"} if correct else None
    spec = AcceptanceSpec.from_payload({"cases": [{
        "id": "normal", "target": module.__name__ + ":echo", "args": [[1, 2]],
        "kwargs": {"suffix": "ok"}, "expected_output": expected,
    }]})
    for render, entry in [(acceptance.render_acceptance_tests, "test_normal"),
                          (acceptance.render_acceptance_demo, "main")]:
        namespace = {"__file__": str(tmp_path / "generated.py"), "__name__": "generated"}
        exec(compile(render(spec), "generated.py", "exec"), namespace)
        if correct:
            namespace[entry]()
        else:
            with pytest.raises(AssertionError):
                namespace[entry]()
