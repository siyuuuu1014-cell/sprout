"""Immutable captured analysis inputs, NOT file-access credentials.

Synthetic views are valid algorithm inputs. Only Runtime's live B1 registration
and FileBroker can admit real resources; constructing this type grants nothing.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from threading import Event

from Sprout.execution.file_snapshot import ResourceIdentity, checked_snapshot
from Sprout.workspace.classifier import classify
from Sprout.workspace.models import ResourceKind, ResourceRef, Workspace

VISIBLE_KINDS = frozenset({ResourceKind.PUBLIC, ResourceKind.SOURCE, ResourceKind.TEST,
                           ResourceKind.CONFIG, ResourceKind.DOCUMENTATION})


def relative_label(value: str) -> str:
    """Validate an in-memory label, not a replacement for R03 path authority."""
    if (type(value) is not str or not value or len(value) > 1024
            or any(character in value for character in ("\\", ":", "\0"))
            or any(ord(character) < 32 for character in value)
            or any(part in {"", ".", ".."} for part in value.split("/"))):
        raise ValueError("invalid_content_label")
    return value


@dataclass(frozen=True, slots=True)
class ContentLimits:
    max_files: int = 256
    max_depth: int = 16
    max_file_bytes: int = 100_000
    max_total_bytes: int = 8_000_000
    max_seconds: int = 30

    def __post_init__(self):
        for value, ceiling in ((self.max_files, 512), (self.max_depth, 64),
                               (self.max_file_bytes, 16 * 1024 * 1024),
                               (self.max_total_bytes, 64 * 1024 * 1024),
                               (self.max_seconds, 60)):
            if type(value) is not int or not 0 < value <= ceiling:
                raise ValueError("invalid_content_budget")


@dataclass(frozen=True, slots=True)
class AuthorizedEntry:
    relative_path: str
    kind: ResourceKind
    data: bytes
    digest: str
    identity: ResourceIdentity | None = None

    def __post_init__(self):
        relative_label(self.relative_path)
        if (type(self.kind) is not ResourceKind or self.kind not in VISIBLE_KINDS
                or classify(self.relative_path) not in VISIBLE_KINDS
                or type(self.data) is not bytes or type(self.digest) is not str
                or self.digest != hashlib.sha256(self.data).hexdigest()):
            raise ValueError("invalid_or_unsafe_content_entry")
        if self.identity is not None:
            from Sprout.execution.file_snapshot import SnapshotResult

            if not checked_snapshot(SnapshotResult(True, data=self.data, digest=self.digest,
                                                  identity=self.identity), len(self.data), None).ok:
                raise ValueError("invalid_content_identity")

    @classmethod
    def synthetic(cls, path: str, data: bytes, kind: ResourceKind = ResourceKind.SOURCE):
        """For pure computation only, never a statement of file authorization."""
        return cls(path, kind, data, hashlib.sha256(data).hexdigest())

    def text(self, encoding: str = "utf-8") -> str:
        if encoding not in {"utf-8", "utf-8-sig"}:
            raise ValueError("unsupported_content_encoding")
        return self.data.decode(encoding, errors="strict")


@dataclass(frozen=True, slots=True)
class AuthorizedContentView:
    workspace_id: str
    entries: tuple[AuthorizedEntry, ...]
    limits: ContentLimits = field(default_factory=ContentLimits)
    revision: str | None = None
    _closed: Event = field(default_factory=Event, init=False, repr=False, compare=False)
    _created: float = field(default_factory=time.monotonic, init=False, repr=False)

    def __post_init__(self):
        if (type(self.workspace_id) is not str or not self.workspace_id
                or (self.revision is not None and type(self.revision) is not str)
                or type(self.entries) is not tuple or type(self.limits) is not ContentLimits
                or any(type(entry) is not AuthorizedEntry for entry in self.entries)):
            raise ValueError("invalid_content_view")
        names = [entry.relative_path.casefold() for entry in self.entries]
        if (len(names) != len(set(names)) or len(names) > self.limits.max_files
                or sum(len(entry.data) for entry in self.entries) > self.limits.max_total_bytes
                or any(len(entry.data) > self.limits.max_file_bytes
                       or len(entry.relative_path.split("/")) > self.limits.max_depth
                       for entry in self.entries)):
            raise ValueError("content_budget_or_collision")
        # A file cannot simultaneously be another entry's directory.
        if any(prefix in names for name in names
               for prefix in ("/".join(name.split("/")[:n])
                              for n in range(1, len(name.split("/"))))):
            raise ValueError("content_file_directory_collision")

    def check(self, workspace_id: str | None = None):
        if (self._closed.is_set() or time.monotonic() - self._created > self.limits.max_seconds
                or (workspace_id is not None and workspace_id != self.workspace_id)):
            raise PermissionError("content_view_unavailable")

    def invalidate(self):
        self._closed.set()

    def get(self, path: str) -> AuthorizedEntry:
        self.check()
        relative_label(path)
        for entry in self.entries:
            if entry.relative_path == path:
                return entry
        raise KeyError("content_not_in_view")

    def resources(self) -> tuple[ResourceRef, ...]:
        self.check()
        return tuple(ResourceRef(self.workspace_id, entry.relative_path, self.revision,
                                 entry.digest, entry.kind) for entry in self.entries)


@dataclass(frozen=True, slots=True, eq=False, weakref_slot=True)
class _CaptureSelection:
    """An opaque selector; only a Runtime instance registry can authorize use."""
    paths: tuple[str, ...]
    limits: ContentLimits


class _EvidenceLabel(PurePosixPath):
    """Lexical source-location label for old AST-only evidence helpers.

    Not pathlib.Path: no open/read/stat/traversal API. resolve is identity on
    already validated relative labels, never filesystem canonicalization.
    """

    def resolve(self):
        return self


def evidence_workspace(view: AuthorizedContentView) -> Workspace:
    view.check()
    return Workspace(view.workspace_id, _EvidenceLabel("."), revision=view.revision)
