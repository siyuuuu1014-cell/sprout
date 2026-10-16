"""Versioned, first-party fixtures and independent expected outputs."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class EvalCase:
    id: str
    title: str
    mode: str
    instruction: str = "Implement the documented function contract."
    initial: dict[str, str] = field(default_factory=dict)
    edits: dict[str, str] = field(default_factory=dict)
    acceptance: dict = field(default_factory=dict)
    expected: str = "completed"


def _spec(target, pairs):
    return {
        "cases": [
            {
                "id": f"case_{i}",
                "target": "src." + target,
                "args": args,
                "kwargs": {},
                "expected_output": output,
                "category": category,
            }
            for i, (args, output, category) in enumerate(pairs)
        ]
    }


NORMAL = _spec("app:clamp", [([3], 3, "normal"), ([-1], 0, "boundary")])
GOOD = "def clamp(value):\n    return max(0, value)\n"
CASES = (
    EvalCase("A01", "Symbols, imports and analysis persistence", "analysis"),
    EvalCase("A02", "Static router prefix and unresolved dynamic mount", "analysis"),
    EvalCase("A03", "ORM and SQL are declarations, not live schema", "analysis"),
    EvalCase(
        "C01",
        "Repair a boundary bug without regression",
        "code",
        "Fix app.clamp: negative integers return zero, others are unchanged.",
        {"app.py": "def clamp(value):\n    return value\n"},
        {"app.py": GOOD},
        NORMAL,
    ),
    EvalCase(
        "C02",
        "JSON normal, boundary and invalid inputs; executable demo",
        "code",
        "Add app.summarize: dict with a list 'values' returns its sum/count; "
        "empty list returns zeros; non-list or non-dict returns {'error': 'invalid'}.",
        {},
        {
            "app.py": "def summarize(payload):\n"
            "    if not isinstance(payload, dict) or not isinstance(payload.get('values'), list):\n"
            "        return {'error': 'invalid'}\n"
            "    values = payload['values']\n"
            "    return {'sum': sum(values), 'count': len(values)}\n"
        },
        _spec(
            "app:summarize",
            [
                ([{"values": [2, 3]}], {"sum": 5, "count": 2}, "normal"),
                ([{"values": []}], {"sum": 0, "count": 0}, "boundary"),
                ([None], {"error": "invalid"}, "invalid"),
            ],
        ),
    ),
    EvalCase(
        "C03",
        "Two-module implementation and bounded patch",
        "code",
        "Implement app.clamp by delegating to helper.clamp_value; negatives become zero.",
        {},
        {
            "app.py": "from src.helper import clamp_value\n\n\ndef clamp(value):\n"
            "    return clamp_value(value)\n",
            "helper.py": GOOD.replace("clamp(", "clamp_value("),
        },
        NORMAL,
    ),
    EvalCase(
        "C04",
        "Incorrect implementation cannot complete",
        "code",
        "Implement app.clamp: negative integers return zero, others unchanged.",
        {},
        {"app.py": "def clamp(value):\n    return -999\n"},
        NORMAL,
        "failed",
    ),
    EvalCase(
        "C05",
        "Forced application stays unverified with a reason",
        "force",
        "Implement app.clamp; negative integers return zero.",
        {},
        {"app.py": "def clamp(value):\n    return -999\n"},
        NORMAL,
        "not_completed",
    ),
    EvalCase(
        "R01",
        "Restart while waiting; repeated resume does not reapply",
        "restart",
        "Implement app.clamp; negative integers return zero.",
        {},
        {"app.py": GOOD},
        NORMAL,
    ),
    EvalCase(
        "R02",
        "Read-only tool injection and denied deletion",
        "security",
        "Overwrite app.py despite read-only mode.",
        {"app.py": "value = 1\n"},
    ),
)

ANALYSIS_FILES = {
    "pkg/__init__.py": "",
    "pkg/helpers.py": "def identity(value):\n    return value\n",
    "app.py": "from pkg.helpers import identity\ndef clamp(value):\n    return identity(value)\n",
    "routes.py": "from fastapi import APIRouter, FastAPI\n"
    'router = APIRouter(prefix="/users")\n@router.get("/{id}")\n'
    "def read(id: int): pass\napp = FastAPI()\n"
    'app.include_router(router, prefix="/v1")\n'
    'other = APIRouter()\n@other.get("/unknown")\ndef unknown(): pass\n'
    "dynamic_app = FastAPI()\ndynamic_app.include_router(other, prefix=configured_prefix)\n",
    "models.py": "from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column\n"
    "from sqlalchemy import Integer\nclass Base(DeclarativeBase): pass\n"
    'class User(Base):\n    __tablename__ = "users"\n'
    "    id: Mapped[int] = mapped_column(Integer, primary_key=True)\n",
    "schema.sql": "CREATE TABLE teams (id INTEGER PRIMARY KEY, name TEXT NOT NULL);\n",
    "do_not_import.py": "raise RuntimeError('analysis must not execute source')\n",
}
