"""Container entry point for iterable inputs and observed Python exceptions."""

import asyncio
import base64
import builtins
import importlib
import inspect
import json
import sys


def main():
    request = json.loads(base64.b64decode(sys.argv[1], validate=True))
    sys.path.insert(0, "/workspace")
    module, function = request["target"].split(":")
    target = getattr(importlib.import_module(module), function)
    args, kwargs = request["args"], request["kwargs"]
    for argument, kind in request["iterable_args"]:
        collection = args if type(argument) is int else kwargs
        items = collection[argument]
        collection[argument] = (item for item in items) if kind == "generator" else iter(items)
    try:
        value = target(*args, **kwargs)
        if inspect.isawaitable(value):
            value = asyncio.run(value)
    except Exception as exc:
        exception_type = type(exc)
        name = exception_type.__name__
        exact_builtin = getattr(builtins, name, None) is exception_type
        observation = {"outcome": "raised", "exception": name if exact_builtin else "non_builtin"}
    else:
        observation = {"outcome": "returned", "value": value}
    print(json.dumps(observation, allow_nan=False, ensure_ascii=True))


if __name__ == "__main__":
    main()
