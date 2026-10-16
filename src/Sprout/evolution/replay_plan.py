"""Versioned replay intent, persisted with the fresh task before external work.

This is evidence for a future recovery gate, never an authorization or resume token.
No model settings, credentials, instruction text, or approval grants are serialized.
"""

from pathlib import Path

from Sprout.evolution.replay_measurement import command_parts
from Sprout.evolution.replay_oracle import digest
from Sprout.execution.docker_verification import validate_image


def build_plan(
    *, source_task_id, revision, mode, original, repo, tests, regressions,
    oracle_paths, frozen, image, candidate=None, configuration=None, runtime=None, creator=None,
    coding=None,
) -> dict:
    if mode not in {"code", "read_only"}:
        raise ValueError("Replay mode must be confirmed")
    if len(revision) not in {40, 64} or any(c not in "0123456789abcdef" for c in revision):
        raise ValueError("Replay plan requires an immutable Git commit")
    if not tests or not regressions:
        raise ValueError("Replay plan requires test and regression commands")
    for command in (*tests, *regressions):
        command_parts(command)
    if mode == "code":
        if not oracle_paths or not frozen or not image:
            raise ValueError("Code plan requires image and frozen oracle")
        validate_image(image)
    payload = {
        "schema_version": (5 if coding is not None else 4 if creator is not None
                           else 3 if runtime is not None
                           else 2 if configuration is not None else 1),
        "source_task_id": source_task_id,
        "revision": revision,
        "execution_mode": mode,
        "original_root": str(Path(original).resolve()),
        "repository_root": str(Path(repo).resolve()),
        "test_commands": list(tests),
        "regression_commands": list(regressions),
        "oracle_paths": list(oracle_paths),
        "oracle_snapshot": frozen,
        "verification_image": image,
        "candidate": candidate,
    }
    if configuration is not None:
        payload["configuration"] = configuration
    if runtime is not None:
        if configuration is None:
            raise ValueError("Runtime snapshot requires configuration contract")
        payload["runtime_snapshot"] = runtime
    if creator is not None:
        if runtime is None:
            raise ValueError("Creator binding requires runtime snapshot")
        payload["creator"] = creator
    if coding is not None:
        from Sprout.evolution.coding_profile import verify_coding_profile

        if creator is None or mode != "code":
            raise ValueError("Coding profile requires creator-bound code plan")
        payload["coding_profile"] = verify_coding_profile(coding)
    return {"payload": payload, "fingerprint": digest(payload)}


def verify_plan(plan: dict) -> dict:
    """Detect corrupt/unsupported plans; integrity does not imply authenticity."""
    if not isinstance(plan, dict) or not isinstance(plan.get("payload"), dict):
        raise ValueError("Replay plan is missing or malformed")
    payload = plan["payload"]
    expected = {
        "schema_version", "source_task_id", "revision", "execution_mode",
        "original_root", "repository_root", "test_commands", "regression_commands",
        "oracle_paths", "oracle_snapshot", "verification_image", "candidate",
    }
    if payload.get("schema_version") in {2, 3, 4, 5}:
        expected.add("configuration")
    if payload.get("schema_version") in {3, 4, 5}:
        expected.add("runtime_snapshot")
    if payload.get("schema_version") in {4, 5}:
        expected.add("creator")
    if payload.get("schema_version") == 5:
        expected.add("coding_profile")
    if set(payload) != expected or set(plan) != {"payload", "fingerprint"}:
        raise ValueError("Replay plan fields are missing or unsupported")
    if (payload.get("schema_version") not in {1, 2, 3, 4, 5}
            or digest(payload) != plan.get("fingerprint")):
        raise ValueError("Replay plan version or fingerprint mismatch")
    rebuilt = build_plan(
        source_task_id=payload["source_task_id"], revision=payload["revision"],
        mode=payload["execution_mode"], original=payload["original_root"],
        repo=payload["repository_root"], tests=payload["test_commands"],
        regressions=payload["regression_commands"], oracle_paths=payload["oracle_paths"],
        frozen=payload["oracle_snapshot"], image=payload["verification_image"],
        candidate=payload["candidate"],
        configuration=payload.get("configuration"),
        runtime=payload.get("runtime_snapshot"),
        creator=payload.get("creator"),
        coding=payload.get("coding_profile"),
    )
    if rebuilt != plan:
        raise ValueError("Replay plan is not canonical")
    return payload
