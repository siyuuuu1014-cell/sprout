"""Public JUnit projection: identities come from source AST, never failure text.

Locations identify test declarations, NOT traceback frames. Parameter values,
failure bodies/messages and captured output are deliberately never inspected.
"""

from __future__ import annotations

import ast
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,159}\Z")
_COUNT = re.compile(r"(?:0|[1-9][0-9]{0,6})\Z")
_EXCEPTIONS = frozenset({
    "AssertionError", "PermissionError", "FileNotFoundError", "OSError",
    "RuntimeError", "ValueError", "TypeError", "ImportError", "ModuleNotFoundError",
    "TimeoutError", "AttributeError", "FileBoundaryError",
})


def _source_tests(root: Path) -> dict:
    result = {}
    for directory in ("src/Sprout/tests", "web/tests"):
        for path in (root / directory).rglob("test_*.py"):
            if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
                continue
            relative = path.relative_to(root).as_posix()
            if (len(relative) > 240 or not re.fullmatch(r"[A-Za-z0-9_/]+\.py", relative)
                    or path.stat().st_size > 2_000_000):
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            except (SyntaxError, UnicodeError):
                continue
            module = relative[:-3].replace("/", ".")

            def visit(nodes, classes=(), module=module, relative=relative):
                for node in nodes:
                    if not isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                        continue
                    if not _NAME.fullmatch(node.name):
                        continue
                    if isinstance(node, ast.ClassDef):
                        visit(node.body, (*classes, node.name))
                    elif node.name.startswith("test_"):
                        classname = ".".join((module, *classes))
                        result[classname, node.name] = {
                            "path": relative, "function": node.name, "line": node.lineno,
                        }

            visit(tree.body)
    return result


def _count(suite, name):
    value = suite.get(name, "0")
    if not _COUNT.fullmatch(value):
        raise ValueError("invalid count")
    return int(value)


def failure_summary(junit: Path, source_root: Path) -> dict:
    """On unprovable input return only a fixed error; never echo the input."""
    safe = {"schema_version": 1, "status": "unreadable", "counts": None, "cases": []}
    try:
        if junit.stat().st_size > 32_000_000:
            return safe
        raw = junit.read_bytes().decode("utf-8-sig")
        if "<!DOCTYPE" in raw.upper() or "<!ENTITY" in raw.upper():
            return safe
        tree = ET.fromstring(raw)
        if tree.tag not in {"testsuite", "testsuites"}:
            return safe
        suites = ([tree] if tree.tag == "testsuite" else list(tree.iter("testsuite")))
        if not suites or any(suite.find("testsuite") is not None for suite in suites):
            return safe
        counts = {key: sum(_count(s, key) for s in suites)
                  for key in ("tests", "failures", "errors")}
        if counts["tests"] > 1_000_000:
            return safe
        identities = _source_tests(source_root)
        ordinals = defaultdict(int)
        records = []
        actual = {"tests": 0, "failures": 0, "errors": 0}
        for suite in suites:
            for case in suite.findall("testcase"):
                actual["tests"] += 1
                classname, name = case.get("classname", ""), case.get("name", "")
                # Only the AST-proven base name participates. Parameter contents
                # are opaque, unexported and do not influence any error category.
                base = name.split("[", 1)[0] if len(name) <= 4096 else ""
                key = (classname, base)
                ordinals[key] += 1
                source = identities.get(key)
                identifier = (classname + "::" + base if source is not None else None)
                if identifier is not None and len(identifier) > 400:
                    identifier, source = None, None
                for tag, category in (("failure", "test_failed"), ("error", "test_error")):
                    for outcome in case.findall(tag):
                        actual[tag + "s" if tag == "error" else "failures"] += 1
                        exception = outcome.get("type")
                        records.append({
                            "test_id": identifier,
                            "case_ordinal": ordinals[key] if identifier is not None else None,
                            "source": source,
                            "exception_class": exception if exception in _EXCEPTIONS else None,
                            "category": category,
                        })
        return {"schema_version": 1, "status": "ok" if actual == counts else "count_mismatch",
                "counts": counts, "cases": records}
    except (OSError, ValueError, TypeError, ET.ParseError, RecursionError):
        return safe
