"""Container entry point: return an actual value, never judge expected results."""

import asyncio
import base64
import importlib
import inspect
import json
import sys


def main():
    request = json.loads(base64.b64decode(sys.argv[1], validate=True))
    sys.path.insert(0, "/workspace")
    module, function = request["target"].split(":")
    value = getattr(importlib.import_module(module), function)(
        *request["args"], **request["kwargs"]
    )
    if inspect.isawaitable(value):
        value = asyncio.run(value)
    print(json.dumps(value, allow_nan=False, ensure_ascii=True))


if __name__ == "__main__":
    main()
