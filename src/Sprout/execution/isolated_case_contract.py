"""Host-defined node JSON commands, shared by execution and evidence inspection."""

import base64
import json
from pathlib import Path

from Sprout.execution import function_worker


def public_demo(case):
    inputs = json.dumps({"target": case.target, "args": case.args, "kwargs": case.kwargs},
                       allow_nan=False)
    return Path(function_worker.__file__).read_text(encoding="utf-8").replace(
        'request = json.loads(base64.b64decode(sys.argv[1], validate=True))',
        f"request = json.loads({inputs!r})")


def planned_cases(acceptance, regression, phase):
    normal = next((case for case in acceptance.cases if case.category == "normal"), None)
    if normal is None:
        raise ValueError("Normal acceptance case required for minimal demo")
    cases = [("acceptance", index, case) for index, case in enumerate(acceptance.cases)]
    if phase == "evaluation":
        if regression is None:
            raise ValueError("Frozen regression required")
        cases.extend(("regression", index, case) for index, case in enumerate(regression.cases))
        cases.append(("poc", 0, normal))
    return cases, public_demo(normal)


def case_command(kind, case, demo_source):
    payload = {"target": case.target, "args": case.args, "kwargs": case.kwargs}
    encoded = base64.b64encode(json.dumps(payload, allow_nan=False).encode()).decode()
    source = (demo_source if kind == "poc"
              else Path(function_worker.__file__).read_text(encoding="utf-8"))
    return ("python", "-I", "-c", source, encoded)
