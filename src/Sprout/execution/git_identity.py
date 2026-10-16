"""Read existing operator Git identity, never synthesize or modify configuration."""

import asyncio
from pathlib import Path

from Sprout.evolution.replay_plan import verify_plan
from Sprout.execution.docker_verification import safe_path
from Sprout.execution.git_env import git_args, git_env
from Sprout.execution.git_transaction_contract import commit_write_contract
from Sprout.execution.proc import capture_bounded, terminate
from Sprout.gateway.local_identity import creator_blockers, current_cli_principal


class GitIdentityRequired(ValueError):
    """Known pre-write configuration wait with an actionable missing-field list."""

    def __init__(self, missing):
        self.missing = tuple(missing)
        super().__init__("Git identity missing: " + ", ".join(self.missing))

    def guidance(self, original):
        return {"status": "waiting_configuration", "reason": "git_identity_missing",
                "missing_fields": list(self.missing), "read_only": True,
                "configuration_commands": [["git", "-C", str(original), "config", "--local",
                    key, "<your-name>" if key == "user.name" else "<your-email>"]
                    for key in self.missing],
                "next_action": "Recheck identity, rebuild exact approval, then explicitly continue",
                "cancel_available": True, "automatic_retry": False}


async def _read_config(root, key):
    process = await asyncio.create_subprocess_exec("git", *git_args(
        "-C", str(root), "config", "--get", key), env=git_env(),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        stdout, _ = await asyncio.wait_for(capture_bounded(process, 4096), timeout=15)
        if process.returncode == 1:
            return None
        if process.returncode != 0:
            raise ValueError("Existing Git identity unavailable; configure it explicitly")
        return stdout.decode("utf-8", errors="strict").removesuffix("\n").removesuffix("\r")
    except BaseException:
        await terminate(process)
        raise


async def resolve_commit_contract(replay_plan, git_plan, *, message,
                                  author_name=None, author_email=None):
    """Local creator-only read; Git normally prefers project identity over global.

    Frozen results must be shown/bound to the exact approval before a future
    writer uses them. This function neither approves nor executes a commit.
    """
    payload = verify_plan(replay_plan)
    if (payload["schema_version"] != 5 or payload["execution_mode"] != "code"
            or creator_blockers(payload.get("creator"), current_cli_principal())):
        raise ValueError("Current CLI experiment creator required")
    root = safe_path(Path(payload["original_root"]))
    marker = root / ".git"
    if (not root.is_dir() or marker.is_symlink()
            or not (marker.is_dir() or marker.is_file())):
        raise ValueError("Original Git workspace required")
    if safe_path(Path(git_plan["target"])) != safe_path(Path(payload["repository_root"])):
        raise ValueError("Experiment target binding mismatch")
    # Explicit local-operator input is task-scoped; never write Git configuration.
    name = author_name if author_name is not None else await _read_config(root, "user.name")
    email = author_email if author_email is not None else await _read_config(root, "user.email")
    missing = [key for key, value in (("user.name", name), ("user.email", email))
               if value is None or not value.strip()]
    if missing:
        raise GitIdentityRequired(missing)
    return commit_write_contract(git_plan, author_name=name, author_email=email, message=message)
