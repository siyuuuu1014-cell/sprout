"""Explicit local operator regression oracle, frozen outside candidate inputs."""

import hashlib
import json

from Sprout.strategy.acceptance import AcceptanceSpec


def normalized(payload):
    value = AcceptanceSpec.from_payload(payload).to_payload()
    raw = json.dumps(value, sort_keys=True, allow_nan=False).encode()
    if len(raw) > 64 * 1024:
        raise ValueError("Regression oracle exceeds 64 KiB")
    return value, raw, hashlib.sha256(raw).hexdigest()


def load_operator_spec(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 64 * 1024:
        raise ValueError("Existing regular regression JSON up to 64 KiB required")
    return normalized(json.loads(path.read_text(encoding="utf-8")))[0]


def freeze(directory, payload):
    if payload is None:
        return None
    _, raw, fingerprint = normalized(payload)
    with (directory / "regression-spec.json").open("xb") as stream:
        stream.write(raw)
    return fingerprint


def frozen(directory, fingerprint):
    if not fingerprint:
        raise ValueError("Explicit frozen regression oracle required")
    value = load_operator_spec(directory / "regression-spec.json")
    if normalized(value)[2] != fingerprint:
        raise ValueError("Frozen regression oracle changed")
    return value
