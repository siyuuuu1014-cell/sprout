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


@dataclass(frozen=True, slots=True)
class AcceptanceSpec:
    cases: tuple[AcceptanceCase, ...]
    version: int = 1

    @classmethod
    def from_payload(cls, payload: object) -> AcceptanceSpec:
        if not isinstance(payload, dict) or not isinstance(payload.get("cases"), list):
            raise ValueError("Acceptance design must contain a cases array")
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
            if "expected_output" not in item:
                raise ValueError(f"Case {case_id} needs an explicit expected_output")
            args = item.get("args", [])
            kwargs = item.get("kwargs", {})
            if not isinstance(args, list) or not isinstance(kwargs, dict):
                raise ValueError("Case args must be an array and kwargs must be an object")
            category = item.get("category", "normal")
            if category not in {"normal", "boundary", "invalid"}:
                raise ValueError("Unknown acceptance case category")
            ids.add(case_id)
            cases.append(AcceptanceCase(
                case_id, target, tuple(args), kwargs, item["expected_output"], category,
            ))
        if not cases:
            raise ValueError("At least one concrete acceptance case is required")
        if len(cases) > 30:
            raise ValueError("Acceptance design is limited to 30 cases")
        # Reject non-JSON inputs, including NaN, before generating executable tests.
        json.dumps(payload, allow_nan=False)
        return cls(tuple(cases))

    def to_payload(self) -> dict[str, object]:
        return {"version": self.version, "cases": [
            {**asdict(case), "args": list(case.args)} for case in self.cases
        ]}


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
