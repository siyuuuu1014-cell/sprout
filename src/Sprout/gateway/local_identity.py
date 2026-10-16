"""Trusted local CLI identity; no environment/user-supplied username fallback."""

import csv
import ctypes
import os
import subprocess

from Sprout.gateway.identity import Principal


def _windows_sid() -> str:
    # Resolve the system binary using the OS API, not PATH/WINDIR/USERNAME.
    buffer = ctypes.create_unicode_buffer(32768)
    length = ctypes.windll.kernel32.GetSystemDirectoryW(buffer, len(buffer))
    if not length or length >= len(buffer):
        raise ValueError("System identity executable unavailable")
    result = subprocess.run(
        [os.path.join(buffer.value, "whoami.exe"), "/user", "/fo", "csv", "/nh"],
        capture_output=True, text=True, check=True, timeout=5,
    )
    rows = list(csv.reader(result.stdout.splitlines()))
    if len(rows) != 1 or len(rows[0]) != 2:
        raise ValueError("OS identity output is malformed")
    sid = rows[0][1].strip()
    if not sid.startswith("S-1-") or any(not part.isdigit() for part in sid.split("-")[1:]):
        raise ValueError("OS identity SID is malformed")
    return sid


def current_cli_principal() -> Principal:
    """The process account is the local CLI owner; this grants no action approval."""
    try:
        if os.name == "nt":
            identity = "windows:" + _windows_sid()
        else:
            # Bind POSIX numeric identity to this host, not a caller-declared name.
            identity = "posix:" + os.uname().nodename + ":" + str(os.geteuid())
    except Exception as exc:
        raise ValueError("Current OS identity cannot be verified") from exc
    return Principal(user_id=identity, authenticated=True, source="cli")


def creator_record(principal: Principal) -> dict:
    if not principal.authenticated or principal.source != "cli":
        raise ValueError("Trusted local CLI identity required")
    return {"user_id": principal.user_id, "source": "cli", "authenticated": True}


def creator_blockers(creator, principal: Principal | None) -> list[str]:
    if not isinstance(creator, dict) or set(creator) != {"user_id", "source", "authenticated"}:
        return ["trusted_creator_missing"]
    if creator.get("source") != "cli" or creator.get("authenticated") is not True:
        return ["trusted_creator_missing"]
    if principal is None or not principal.authenticated or principal.source != "cli":
        return ["trusted_current_cli_identity_required"]
    if creator["user_id"] != principal.user_id:
        return ["current_operator_not_experiment_creator"]
    return []
