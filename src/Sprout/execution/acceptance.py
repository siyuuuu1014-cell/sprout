"""Generate executable Python input/output tests from a frozen acceptance spec."""

from hashlib import sha256

from Sprout.strategy.acceptance import AcceptanceSpec


def acceptance_test_path(task_id: str) -> str:
    return f"tests/test_sprout_acceptance_{sha256(task_id.encode()).hexdigest()[:16]}.py"


def render_acceptance_tests(spec: AcceptanceSpec) -> str:
    lines = [
        '"""Requirement input/output cases generated before implementation."""',
        "import asyncio", "import importlib", "import inspect", "import sys",
        "from pathlib import Path", "",
        "sys.path.insert(0, str(Path(__file__).resolve().parents[1]))", "",
    ]
    for case in spec.cases:
        module, function = case.target.split(":")
        lines.extend([
            f"def test_{case.id}():",
            f"    target = importlib.import_module({module!r}).{function}",
            f"    actual = target(*{case.args!r}, **{case.kwargs!r})  # noqa: E501",
            "    if inspect.isawaitable(actual):",
            "        actual = asyncio.run(actual)",
            f"    assert actual == {case.expected_output!r}  # noqa: E501", "",
        ])
    return "\n".join(lines)


def acceptance_commands(spec: AcceptanceSpec, path: str) -> tuple[tuple[str, ...], ...]:
    return tuple(
        ("python", "-m", "pytest", "-q", f"{path}::test_{case.id}")
        for case in spec.cases
    )


def acceptance_demo_path(task_id: str) -> str:
    return f"examples/sprout_demo_{sha256(task_id.encode()).hexdigest()[:16]}.py"


def render_acceptance_demo(spec: AcceptanceSpec) -> str:
    """A standalone public-call demo with an explicit output oracle, not pytest."""
    case = next((case for case in spec.cases if case.category == "normal"), None)
    if case is None:
        raise ValueError("A minimal demo requires a normal acceptance case")
    module, function = case.target.split(":")
    return "\n".join([
        '"""Run this demo from the project root with python examples/<this-file>.py."""',
        "import asyncio", "import importlib", "import inspect", "import json", "import sys",
        "from pathlib import Path", "",
        "sys.path.insert(0, str(Path(__file__).resolve().parents[1]))", "",
        "def main():",
        f"    target = importlib.import_module({module!r}).{function}",
        f"    actual = target(*{case.args!r}, **{case.kwargs!r})  # noqa: E501",
        "    if inspect.isawaitable(actual):",
        "        actual = asyncio.run(actual)",
        f"    assert actual == {case.expected_output!r}  # noqa: E501",
        "    print(json.dumps(actual, ensure_ascii=False))", "",
        "if __name__ == '__main__':", "    main()", "",
    ])
