"""Offline coding command entry point; contains no model or host authority."""

import base64
import json
import subprocess
import sys


def main():
    command = json.loads(base64.b64decode(sys.argv[1], validate=True))
    if (not isinstance(command, list) or not command
            or any(not isinstance(value, str) or "\x00" in value for value in command)):
        raise ValueError("Structured command required")
    # Docker fixes the cwd after host confinement checks; do not override it.
    raise SystemExit(subprocess.run(command, check=False).returncode)


if __name__ == "__main__":
    main()
