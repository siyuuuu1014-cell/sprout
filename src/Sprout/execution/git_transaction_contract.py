"""Canonical credential-free fingerprints shared by Git control and persistence."""

import hashlib
import json
import re


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def commit_write_contract(git_plan, *, author_name, author_email, message):
    """Freeze operator-supplied commit identity/options, without granting writes."""
    if (not isinstance(git_plan, dict) or git_plan.get("operation") != "commit"
            or git_plan.get("write_authorized") is not False):
        raise ValueError("Read-only commit plan required")
    for value, limit in ((author_name, 256), (author_email, 320), (message, 512)):
        if (not isinstance(value, str) or not value.strip() or value != value.strip()
                or len(value) > limit or any(ord(char) < 32 for char in value)):
            raise ValueError("Explicit bounded commit identity and message required")
    if (any(char in author_name + author_email for char in "<>")
            or not re.fullmatch(r"[^\s@]+@[^\s@]+", author_email)):
        raise ValueError("Unambiguous Git author name and email required")
    return {"schema_version": 1, "boundary": "isolated_git_commit",
            "git_plan_fingerprint": fingerprint(git_plan),
            "author_name": author_name, "author_email": author_email, "message": message}


def validate_commit_write_contract(contract, git_plan):
    if (not isinstance(contract, dict) or set(contract) != {
            "schema_version", "boundary", "git_plan_fingerprint", "author_name", "author_email",
            "message"} or type(contract.get("schema_version")) is not int):
        raise ValueError("Exact commit write contract required")
    expected = commit_write_contract(git_plan, author_name=contract["author_name"],
                                    author_email=contract["author_email"],
                                    message=contract["message"])
    if expected != contract:
        raise ValueError("Commit write contract changed")
