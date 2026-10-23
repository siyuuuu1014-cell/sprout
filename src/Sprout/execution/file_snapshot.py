"""Internal bounded snapshots. No directory visibility or portable grants.

Only a live B1 call can supply a subject; the Runtime revalidates its own
instance registry and Metadata on every refresh. A Python type/JSON actor is
not authority. These helpers do not enable any model-visible tool.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import stat
from collections.abc import Callable
from dataclasses import dataclass
from threading import Event


@dataclass(frozen=True, slots=True)
class ResourceIdentity:
    device: int
    inode: int
    size: int
    mtime_ns: int
    ctime_ns: int

    @classmethod
    def from_stat(cls, info):
        return cls(info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


@dataclass(frozen=True, slots=True)
class SnapshotResult:
    ok: bool = False
    reason: str = ""
    data: bytes = b""
    digest: str = ""
    identity: ResourceIdentity | None = None


@dataclass(frozen=True, slots=True)
class DirectoryReadLimits:
    max_entries: int = 2048
    max_depth: int = 16
    max_files: int = 512


@dataclass(frozen=True, slots=True)
class DirectoryEntry:
    relative_path: str
    is_directory: bool
    identity: ResourceIdentity


@dataclass(frozen=True, slots=True)
class DirectoryListingResult:
    ok: bool = False
    reason: str = ""
    entries: tuple[DirectoryEntry, ...] = ()
    # Visibility is always scoped; never assert a full unfiltered inventory.
    coverage: str = "authorized_only"


def valid_identity(identity: object) -> bool:
    """Conservative receipt domain; booleans are not filesystem integers.

    This snapshot API refuses pre-epoch timestamps rather than guessing their
    platform interpretation. Legacy file APIs are unaffected.
    """
    return type(identity) is ResourceIdentity and all(
        type(value) is int and value >= minimum
        for value, minimum in (
            (identity.device, 0), (identity.inode, 1), (identity.size, 0),
            (identity.mtime_ns, 0), (identity.ctime_ns, 0),
        )
    )


def checked_snapshot(result: object, max_bytes: int, expected: ResourceIdentity | None):
    """Validate only captured evidence, never perform a second file read."""
    if (type(result) is not SnapshotResult or result.ok is not True
            or type(result.reason) is not str or result.reason != ""
            or type(result.data) is not bytes or len(result.data) > max_bytes
            or type(result.digest) is not str or not valid_identity(result.identity)
            or result.identity.size != len(result.data)
            or (expected is not None and result.identity != expected)
            or result.digest != hashlib.sha256(result.data).hexdigest()):
        return SnapshotResult(reason="snapshot_result_invalid")
    return SnapshotResult(
        True, data=result.data, digest=result.digest, identity=result.identity,
    )


@dataclass(frozen=True, slots=True)
class _ReadOptions:
    max_bytes: int
    expected: ResourceIdentity | None
    cancelled: Event
    check_authority: Callable[[], None] | None = None


async def current_read_subject(path: str):
    """Revalidate the existing B1 issuer, never accept a supplied callback."""
    from Sprout.tools.context_keys import _current_project_analysis_call

    frame = _current_project_analysis_call()
    if frame is None or frame.node_scope is None:
        raise PermissionError("snapshot_subject_unavailable")
    scope = frame.node_scope
    request = await scope.runtime._project_analysis_request(
        frame.tool, {"path": path}, frame, scope.session_id,
    )
    if frame.owner is not asyncio.current_task() or not frame.active:
        raise PermissionError("snapshot_subject_unavailable")
    return request


def read_fd(fd: int, options: _ReadOptions):
    """Called only after the R03 secure-open checks; fd owner closes it."""
    from Sprout.execution.file_access import FileBoundaryError

    def check():
        if options.cancelled.is_set():
            raise FileBoundaryError("snapshot_cancelled")
        if options.check_authority is not None:
            options.check_authority()
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not info.st_ino:
            raise FileBoundaryError("snapshot_identity_unavailable")
        identity = ResourceIdentity.from_stat(info)
        if identity.size > options.max_bytes:
            raise FileBoundaryError("snapshot_budget_exceeded")
        return identity

    before = check()
    if options.expected is not None and before != options.expected:
        raise FileBoundaryError("snapshot_identity_changed")
    chunks, total = [], 0
    while True:
        check()
        chunk = os.read(fd, min(65536, options.max_bytes - total + 1))
        if not chunk:
            break
        total += len(chunk)
        if total > options.max_bytes:
            raise FileBoundaryError("snapshot_budget_exceeded")
        chunks.append(chunk)
    if check() != before or total != before.size:
        raise FileBoundaryError("snapshot_identity_changed")
    data = b"".join(chunks)
    return SnapshotResult(True, data=data, digest=hashlib.sha256(data).hexdigest(), identity=before)
