"""Generate executable Python input/output tests from a frozen acceptance spec."""

import inspect
import json
import sys
from hashlib import sha256
from pathlib import Path

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


def acceptance_commands(
    spec: AcceptanceSpec, path: str, *, python: str | None = None,
) -> tuple[tuple[str, ...], ...]:
    return tuple(
        (python or sys.executable, "-m", "pytest", "-q", f"{path}::test_{case.id}")
        for case in spec.cases
    )


def _local_identity(protocol):
    """Self-contained code shared with the isolated-mode Python launcher.

    Do not resolve argv[0]'s symlink: on POSIX it can erase the venv identity.
    No workspace, caller-supplied interpreter, PATH search, or subprocess probe.
    """
    import importlib.metadata
    import importlib.util
    import json
    import os
    import sys
    from hashlib import sha256
    from pathlib import Path

    executable = Path(os.path.abspath(sys.executable))
    if not executable.is_file():
        raise ValueError("local_executor_unavailable")
    distribution = importlib.metadata.distribution("pytest")
    spec = importlib.util.find_spec("pytest")
    if spec is None or not spec.origin:
        raise ValueError("pytest_missing")
    expected = Path(distribution.locate_file("pytest/__init__.py")).resolve()
    if Path(spec.origin).resolve() != expected:
        raise ValueError("pytest_origin_unknown")
    files = []
    for folder in (expected.parent, expected.parent.parent / "_pytest"):
        if not folder.is_dir():
            raise ValueError("pytest_missing")
        for path in sorted(folder.rglob("*.py")):
            if path.is_symlink():
                raise ValueError("pytest_origin_unknown")
            files.append((str(path), sha256(path.read_bytes()).hexdigest()))
    inventory = sorted((item.metadata["Name"], item.version)
                       for item in importlib.metadata.distributions())
    config = Path(sys.prefix) / "pyvenv.cfg"
    payload = [protocol, str(executable), sys.prefix, sys.base_prefix, sys.version,
               sha256(executable.read_bytes()).hexdigest(), distribution.version, files, inventory,
               sha256(config.read_bytes()).hexdigest() if config.is_file() else None]
    return {"version": 1, "domain": "sprout.local-acceptance",
            "fingerprint": sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()}


def local_executor_binding() -> dict:
    """Server-resolved, path-free public binding. Unavailable identity is not a match."""
    return _local_identity(_local_protocol_hash())


def _local_protocol_hash():
    source = inspect.getsource(_local_identity) + inspect.getsource(_local_acceptance_main)
    return sha256(source.encode()).hexdigest()


def local_executor_applicability(frozen: object) -> str:
    if (not isinstance(frozen, dict) or set(frozen) != {"version", "domain", "fingerprint"}
            or frozen.get("version") != 1 or frozen.get("domain") != "sprout.local-acceptance"
            or not isinstance(frozen.get("fingerprint"), str)
            or len(frozen["fingerprint"]) != 64):
        return "unknown"
    try:
        current = local_executor_binding()
    except (OSError, ValueError, ImportError):
        return "unknown"
    return "match" if current == frozen else "mismatch"


def local_evidence_applicability(verification: object) -> str:
    if (not isinstance(verification, dict)
            or verification.get("local_baseline_valid") is not True):
        return "unknown"
    return local_executor_applicability(verification.get("local_executor"))


def _local_acceptance_main():
    """Trusted launcher: observe collection/call phases, never infer from logs.

    This is a Local logical boundary, not protection against hostile Python
    introspection or pytest plugins. Restricted execution retains its own runner.
    """
    import importlib.machinery
    import json
    import sys
    from hashlib import sha256
    from pathlib import Path

    payload = json.loads(sys.argv[1])
    evidence = {"version": 1, "binding": payload["binding"], "category": "unknown",
                "collected": False, "executed": False, "identity_matches": False}
    marker = "SPROUT_LOCAL_ACCEPTANCE_V1:"
    try:
        evidence["identity_matches"] = _local_identity(payload["protocol"]) == payload["binding"]
        if not evidence["identity_matches"]:
            raise ValueError("interpreter_mismatch")
        import pytest

        root = Path.cwd().resolve()
        test = (root / payload["path"]).resolve()
        if (not test.is_relative_to(root) or test.is_symlink()
                or sha256(test.read_text(encoding="utf-8").encode()).hexdigest()
                != payload["hash"]):
            raise ValueError("unknown")
        module, function = payload["target"].split(":")
        pieces = module.split(".")
        parents = []
        for index in range(1, len(pieces)):
            package = root.joinpath(*pieces[:index])
            init = package / "__init__.py"
            if (not init.is_file() or package.is_symlink() or init.is_symlink()
                    or not package.resolve().is_relative_to(root)):
                raise ValueError("unknown")
            parents.append((str(init), sha256(init.read_bytes()).hexdigest()))
        search = root.joinpath(*pieces[:-1])
        target_spec = importlib.machinery.PathFinder.find_spec(module, [str(search)])
        missing = target_spec is None
        candidates = [search / (pieces[-1] + suffix)
                      for suffix in importlib.machinery.all_suffixes()]
        candidates.append(search / pieces[-1])
        if missing and any(path.exists() or path.is_symlink() for path in candidates):
            raise ValueError("unknown")
        target_file = None if missing else Path(target_spec.origin or "").resolve()
        if not missing and (Path(target_spec.origin or "").is_symlink()
                            or not target_file.is_file() or not target_file.is_relative_to(root)):
            raise ValueError("unknown")
        target_hash = None if missing else sha256(target_file.read_bytes()).hexdigest()
        nodeid = payload["path"] + "::test_" + payload["case_id"]

        def unchanged():
            if sha256(test.read_text(encoding="utf-8").encode()).hexdigest() != payload["hash"]:
                return False
            if any(sha256(Path(name).read_bytes()).hexdigest() != digest
                   for name, digest in parents):
                return False
            current = importlib.machinery.PathFinder.find_spec(module, [str(search)])
            if missing:
                return current is None
            return (current is not None and Path(current.origin or "").resolve() == target_file
                    and sha256(target_file.read_bytes()).hexdigest() == target_hash)

        class Observer:
            def matches(self, item):
                return (Path(str(item.path)).resolve() == test
                        and item.name == "test_" + payload["case_id"])

            @pytest.hookimpl(tryfirst=True)
            def pytest_collection_finish(self, session):
                evidence["collected"] = (not session.testsfailed
                    and len(session.items) == 1 and self.matches(session.items[0]))

            @pytest.hookimpl(tryfirst=True)
            def pytest_runtest_makereport(self, item, call):
                if call.when != "call" or not self.matches(item) or not evidence["collected"]:
                    return
                evidence["executed"] = True
                if not unchanged():
                    return
                if call.excinfo is None:
                    evidence["category"] = "passed"
                    return
                tb = list(call.excinfo.traceback)
                controlled = [entry for entry in tb if Path(str(entry.path)).resolve() == test]
                if len(controlled) != 1:
                    return
                entry = controlled[0]
                line = str(entry.statement).strip()
                tail = tb[tb.index(entry) + 1:]
                exc = call.excinfo.value
                if line.startswith("assert actual ==") and not tail and type(exc) is AssertionError:
                    evidence["category"] = "assertion_failed"
                elif line.startswith("target = importlib.import_module("):
                    imported = sys.modules.get(module)
                    if (type(exc) is AttributeError and not tail and imported is not None
                            and not missing and function not in vars(imported)
                            and "__getattr__" not in vars(imported)
                            and isinstance(vars(imported).get("__loader__"),
                                           importlib.machinery.SourceFileLoader)
                            and Path(vars(imported).get("__file__", "")).resolve() == target_file):
                        evidence["category"] = "requirement_not_implemented"
                    elif (type(exc) is ModuleNotFoundError and missing and exc.name == module
                          and all(str(part.path).startswith("<frozen importlib.")
                              or Path(str(part.path)).resolve()
                              == Path(importlib.__file__).resolve()
                              for part in tail)
                          and all(name in sys.modules and
                              "__getattr__" not in vars(sys.modules[name]) and
                              Path(vars(sys.modules[name]).get("__file__", "")).resolve()
                              == Path(parents[index][0]).resolve()
                              and list(vars(sys.modules[name]).get("__path__", ()))
                              == [str(Path(parents[index][0]).parent)]
                              for index, name in enumerate(
                                  ".".join(pieces[:i]) for i in range(1, len(pieces))))):
                        evidence["category"] = "target_module_not_implemented"

        exit_code = int(pytest.main(["-q", nodeid], plugins=[Observer()]))
        if not unchanged():
            evidence["category"] = "unknown"
        if exit_code not in (0, 1):
            evidence["category"] = "collection_error" if exit_code == 2 else "runner_error"
    except ImportError:
        exit_code = 1
        evidence["category"] = "pytest_missing"
    except (OSError, ValueError) as exc:
        exit_code = 1
        if str(exc) == "interpreter_mismatch":
            evidence["category"] = "interpreter_mismatch"
    print("\n" + marker + json.dumps(evidence, sort_keys=True), flush=True)
    sys.exit(exit_code)


def local_acceptance_command(spec: AcceptanceSpec, path: str, case_id: str, binding: dict):
    """Only server source constructs the launcher; no arbitrary caller argv/code."""
    case = next(case for case in spec.cases if case.id == case_id)
    code = inspect.getsource(_local_identity) + "\n" + inspect.getsource(_local_acceptance_main)
    code += "\n_local_acceptance_main()\n"
    payload = {"binding": binding, "protocol": _local_protocol_hash(),
               "path": path, "case_id": case.id, "target": case.target,
               "hash": sha256(render_acceptance_tests(spec).encode()).hexdigest()}
    return (str(Path(sys.executable).absolute()), "-I", "-c", code, json.dumps(payload))


def local_acceptance_evidence(result, binding: dict) -> str:
    if not result.allowed or not result.started or result.exit_code is None:
        return "not_executed"
    lines = [line for line in result.stdout.splitlines()
             if line.startswith("SPROUT_LOCAL_ACCEPTANCE_V1:")]
    if len(lines) != 1:
        return "unknown"
    try:
        evidence = json.loads(lines[0].split(":", 1)[1])
    except (ValueError, TypeError):
        return "unknown"
    if (not isinstance(evidence, dict) or evidence.get("version") != 1
            or evidence.get("binding") != binding or evidence.get("identity_matches") is not True
            or evidence.get("collected") is not True or evidence.get("executed") is not True
            or local_executor_applicability(binding) != "match"):
        return "unknown"
    category = evidence.get("category")
    red = {"assertion_failed", "requirement_not_implemented", "target_module_not_implemented"}
    if category == "passed" and result.exit_code == 0:
        return "passed"
    return category if category in red and result.exit_code == 1 else "unknown"


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
