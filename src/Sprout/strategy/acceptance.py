"""Requirement acceptance cases, independent of models and transports."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class AcceptanceCase:
    id: str
    target: str
    args: tuple[Any, ...]
    kwargs: dict[str, Any]
    expected_output: Any
    category: str = "normal"


# Exact builtin types. The worker never resolves types from model-supplied import paths.
ACCEPTANCE_EXCEPTIONS = frozenset({
    "ValueError", "TypeError", "KeyError", "IndexError", "ZeroDivisionError",
    "OverflowError", "RuntimeError", "AssertionError",
})


@dataclass(frozen=True, slots=True)
class ExtendedAcceptanceCase(AcceptanceCase):
    iterable_args: tuple[tuple[int | str, str], ...] = ()
    expected_exception: str | None = None


def _iterable_args(raw, args, kwargs):
    if not isinstance(raw, list):
        raise ValueError("Iterable arguments must be an array of descriptors")
    descriptors = {}
    for item in raw:
        if not isinstance(item, dict) or item.keys() != {"argument", "kind"}:
            raise ValueError("Iterable descriptor needs argument and kind")
        argument, kind = item["argument"], item["kind"]
        if type(argument) is int and 0 <= argument < len(args):
            value = args[argument]
        elif type(argument) is str and argument in kwargs:
            value = kwargs[argument]
        else:
            raise ValueError("Iterable argument is absent")
        if not isinstance(value, list):
            raise ValueError("Iterable argument must reference a JSON array, not an element")
        if kind not in ("generator", "iterator"):
            raise ValueError("Iterable kind must be generator or iterator")
        if argument in descriptors:
            raise ValueError("Iterable argument descriptor must be unique")
        descriptors[argument] = kind
    return tuple(descriptors.items())


@dataclass(frozen=True, slots=True)
class AcceptanceSpec:
    cases: tuple[AcceptanceCase, ...]
    version: int = 1

    @classmethod
    def from_payload(cls, payload: object) -> AcceptanceSpec:
        if not isinstance(payload, dict) or not isinstance(payload.get("cases"), list):
            raise ValueError("Acceptance design must contain a cases array")
        version = payload.get("version")
        if "version" in payload and (type(version) is not int or version not in (1, 2)):
            raise ValueError("Unsupported acceptance version")
        cases = []
        ids = set()
        for item in payload["cases"]:
            if not isinstance(item, dict):
                raise ValueError("Acceptance case must be an object")
            case_id = item.get("id", "")
            target = item.get("target", "")
            if not isinstance(case_id, str) or not re.fullmatch(r"[A-Za-z_]\w{0,63}", case_id):
                raise ValueError("Acceptance case id must be a Python identifier")
            if case_id in ids:
                raise ValueError(f"Duplicate acceptance case: {case_id}")
            if not isinstance(target, str) or not re.fullmatch(
                r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*", target,
            ):
                raise ValueError("Acceptance target must be an importable module:function")
            if ("expected_output" in item) == ("expected_exception" in item):
                raise ValueError(f"Case {case_id} needs exactly one expected outcome")
            exception = item.get("expected_exception")
            if "expected_exception" in item and (
                not isinstance(exception, str) or exception not in ACCEPTANCE_EXCEPTIONS
            ):
                raise ValueError("Unsupported expected exception type")
            args = item.get("args", [])
            kwargs = item.get("kwargs", {})
            if not isinstance(args, list) or not isinstance(kwargs, dict):
                raise ValueError("Case args must be an array and kwargs must be an object")
            category = item.get("category", "normal")
            if category not in {"normal", "boundary", "invalid"}:
                raise ValueError("Unknown acceptance case category")
            ids.add(case_id)
            iterable_args = _iterable_args(item.get("iterable_args", []), args, kwargs)
            if iterable_args or exception:
                if version == 1:
                    raise ValueError("Extended cases require acceptance version 2")
                version = 2
                cases.append(ExtendedAcceptanceCase(
                    case_id, target, tuple(args), kwargs, item.get("expected_output"),
                    category, iterable_args, exception,
                ))
            else:
                cases.append(AcceptanceCase(
                    case_id, target, tuple(args), kwargs, item["expected_output"], category,
                ))
        if not cases:
            raise ValueError("At least one concrete acceptance case is required")
        if len(cases) > 30:
            raise ValueError("Acceptance design is limited to 30 cases")
        # Reject non-JSON inputs, including NaN, before generating executable tests.
        json.dumps(payload, allow_nan=False)
        return cls(tuple(cases), version or 1)

    def to_payload(self) -> dict[str, object]:
        cases = []
        for case in self.cases:
            payload = {**asdict(case), "args": list(case.args)}
            if isinstance(case, ExtendedAcceptanceCase):
                payload.pop("iterable_args")
                payload.pop("expected_exception")
                if case.iterable_args:
                    payload["iterable_args"] = [
                        {"argument": argument, "kind": kind}
                        for argument, kind in case.iterable_args
                    ]
                if case.expected_exception is not None:
                    payload.pop("expected_output")
                    payload["expected_exception"] = case.expected_exception
            cases.append(payload)
        return {"version": self.version, "cases": cases}


def acceptance_prompt(instruction: str) -> str:
    return (
        "Design acceptance tests before implementing the requirement. "
        "Return only JSON with a cases array. Each case must contain id, target "
        "(module:function), args (JSON array), kwargs (JSON object), expected_output "
        "(concrete JSON value), and category (normal|boundary|invalid). "
        "Use actual project evidence and public callable functions. Include the happy "
        "path and applicable boundaries. Do not use placeholder expected values. "
        "Tests will be generated and frozen by the runtime before any implementation "
        "edit. If the project cannot be tested through Python functions, report an "
        "unsupported adapter instead of inventing a function. Do not edit files.\n"
        "ACCEPTANCE_DESIGN_JSON\nRequirement:\n" + instruction
    )
