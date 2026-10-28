"""Ordinary CODE verification data and private frame. Neither is a grant."""
import asyncio
import hashlib
import json
import re
import time
from contextvars import ContextVar
from dataclasses import dataclass

from Sprout.execution.docker_verification import validate_image
from Sprout.workspace.authorized_content import relative_label


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True, slots=True, eq=False, weakref_slot=True)
class VerificationManifestV1:
    """Canonical immutable evidence; parsing/copying it never creates authority."""
    canonical_json: str

    @classmethod
    def from_payload(cls, value):
        required = {"version", "task", "workspace", "node", "owner", "principal", "source",
                    "scope", "sandbox", "worktree_identity", "acceptance", "diff", "inputs",
                    "obligations", "budgets", "approval", "host_requirements", "export"}
        if (type(value) is not dict or set(value) != required
                or type(value["version"]) is not int or value["version"] != 1):
            raise ValueError("verification_manifest_schema")
        for key in ("task", "workspace", "node", "owner", "source", "diff", "host_requirements"):
            if type(value[key]) is not str or not value[key]:
                raise ValueError("verification_manifest_identity")
        if (type(value["principal"]) is not dict
                or value["principal"].get("authenticated") is not True
                or not value["principal"].get("user_id")
                or value["principal"].get("source") != value["source"]
                or type(value["scope"]) is not dict
                or type(value["obligations"]) is not list):
            raise ValueError("verification_manifest_subject")
        if (type(value["inputs"]) is not dict or not value["inputs"]
                or len(value["inputs"]) > 64):
            raise ValueError("verification_manifest_inputs")
        total = 0
        for name, receipt in value["inputs"].items():
            relative_label(name)
            if (type(receipt) is not dict
                    or set(receipt) != {"digest", "identity", "size", "kind"}
                    or type(receipt["digest"]) is not str
                    or not re.fullmatch(r"[0-9a-f]{64}", receipt["digest"])
                    or type(receipt["size"]) is not int or not 0 <= receipt["size"] <= 100_000):
                raise ValueError("verification_manifest_receipt")
            from Sprout.execution.file_snapshot import ResourceIdentity, valid_identity

            try:
                identity = ResourceIdentity(**receipt["identity"])
            except (TypeError, ValueError) as exc:
                raise ValueError("verification_manifest_identity") from exc
            if not valid_identity(identity) or identity.size != receipt["size"]:
                raise ValueError("verification_manifest_identity")
            total += receipt["size"]
        if total > 1_000_000:
            raise ValueError("verification_manifest_budget")
        ids = set()
        for item in value["obligations"]:
            if item["id"] in ids or not item["required"] or not item["argv"]:
                raise ValueError("verification_manifest_obligation")
            ids.add(item["id"])
            if not set(item["targets"]) <= set(value["inputs"]):
                raise ValueError("verification_manifest_targets")
            validate_image(item["image"])
            if (item["cwd"] != "/workspace" or any(type(a) is not str or "\0" in a
                                                  for a in item["argv"])):
                raise ValueError("verification_manifest_command")
        if not {"project-pytest", "strategy-pytest", "compileall", "ruff", "demo"} <= ids:
            raise ValueError("verification_manifest_missing_obligation")
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return cls(encoded)

    @property
    def version(self):
        return 1

    @property
    def sha256(self):
        return hashlib.sha256(self.canonical_json.encode()).hexdigest()

    def to_payload(self):
        return json.loads(self.canonical_json)


@dataclass(eq=False, slots=True, weakref_slot=True)
class _VerificationFrame:
    issuer: object
    scope: object
    sandbox: object
    owner: object
    expires: float
    active: bool = True


@dataclass(frozen=True, slots=True, eq=False)
class _AgentCompletionCandidate:
    """Provisional live-call linkage, never resource provenance or a grant.

    It is unusable until Runtime rechecks persisted COMPLETED Guard settlement.
    """
    issuer: object
    scope: object
    owner: str
    subject_digest: str
    scope_digest: str
    output_digest: str


@dataclass(frozen=True, eq=False, slots=True)
class _VerificationExecution:
    """Private same-call key; only Broker's Runtime registry makes it usable."""
    issuer: object
    scope: object
    manifest: object
    exported: dict
    obligation: str
    approval_id: str
    owner: object


def scope_digest(scope):
    from dataclasses import asdict

    return digest(json.loads(json.dumps(asdict(scope), default=lambda value:
        sorted(value) if isinstance(value, (set, frozenset)) else value.isoformat())))


_FRAME = ContextVar("sprout_f3_verification_frame", default=None)


def execution_resources(data, obligation):
    """An entry test cannot authorize other imported executable inputs."""
    return sorted(set(obligation["targets"]) |
                  {name for name in data["inputs"] if name.endswith(".py")})


def current_frame():
    from Sprout.tools.context_keys import _current_project_node

    frame = _FRAME.get()
    if (type(frame) is not _VerificationFrame or not frame.active
            or frame.owner is not asyncio.current_task() or time.monotonic() >= frame.expires
            or frame.scope is not _current_project_node() or frame.issuer is not frame.scope.runtime
            or frame not in frame.issuer._verification_frames):
        raise PermissionError("verification_frame_unavailable")
    return frame


async def snapshot_subject(sandbox, path):
    frame = current_frame()
    return await frame.issuer._verification_snapshot_request(frame, sandbox, path)


def ordinary_obligations(host, node, spec, frozen, changed):
    """Fixed ordinary CODE envelope; unknown commands retain a blocked obligation."""
    paths = tuple(p for p in changed if p.endswith(".py"))
    discovery = host["discovery"]
    discovered = (type(discovery) is dict and discovery.get("confirmed") is True
                  and type(discovery.get("test_files")) is list
                  and bool(discovery["test_files"])
                  and set(discovery["test_files"]) <= set(host["paths"])
                  and set(discovery.get("config_files", ())) <= set(host["paths"])
                  and discovery.get("plugins") == []
                  and discovery.get("plugin_autoload") is False)
    targets = discovery["test_files"] if discovered else []
    envelope = (node.metadata.get("test_commands") == ["pytest"]
        and not node.metadata.get("build_commands")
        and len(node.metadata.get("verification_commands", ())) == 1
        and node.metadata["verification_commands"][0].get("command") == "pytest -q"
        and node.metadata["verification_commands"][0].get("required", True) is True)
    candidates = [
        ("project-pytest", "test: pytest", ["pytest"], "WorkspaceManifest", targets,
         "" if discovered else "verification_discovery_unproved"),
        ("strategy-pytest", "code: pytest -q", ["pytest", "-q"], "Strategy", targets,
         "" if discovered else "verification_discovery_unproved"),
        ("compileall", "check: compileall", ["python", "-m", "compileall", "-q", *paths],
         "StaticCheck", paths, "verification_disposable_copy_required"),
        ("ruff", "check: ruff", ["ruff", "check", *paths], "StaticCheck", paths,
         "" if (host["tools"].get("ruff", {}).get("configuration_confirmed") is True
                and discovered and set(host["tools"]["ruff"].get("config_files", ()))
                <= set(discovery["config_files"])) else "verification_ruff_config_unproved"),
        ("demo", "poc: original frozen Demo", ["python", frozen["demo_path"]], "HostAcceptance",
         [frozen["demo_path"]], ""),
    ]
    candidates += [("acceptance:" + case.id, "acceptance:" + case.id + ": frozen pytest",
        ["python", "-m", "pytest", "-q", frozen["test_path"] + "::test_" + case.id],
        "HostAcceptance", [frozen["test_path"]], "") for case in spec.cases]
    return [{"id": key, "name": name, "argv": argv, "source": source, "required": True,
             "targets": list(resources), "cwd": "/workspace",
             "image": host["images"]["coding" if key == "compileall" else "verification"],
             "tool_identity": host["tools"].get(argv[0]),
             "effects": "disposable-copy-only" if key == "compileall" else "readonly-input",
             "blocked": ("verification_command_envelope_changed" if not envelope else reason)}
            for key, name, argv, source, resources, reason in candidates]
