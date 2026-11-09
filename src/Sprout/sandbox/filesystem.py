"""Foreground task-owned filesystem experiments, separate from Docker isolation."""

import shutil
import tempfile
from dataclasses import asdict
from pathlib import Path
from threading import Event
from uuid import uuid4

from Sprout.execution.docker_verification import safe_path
from Sprout.execution.file_access import FileBoundaryError, file_operation
from Sprout.execution.file_snapshot import SnapshotResult, _ReadOptions, checked_snapshot
from Sprout.execution.host_acceptance import pin_execution_directory
from Sprout.execution.models import SandboxRef
from Sprout.execution.verification_export import EXCLUDED, private_resource
from Sprout.workspace.authorized_content import relative_label

KIND = "filesystem_experiment"


def checked_label(label, root, overrides=None):
    label = relative_label(label)
    relative = Path(label)
    if (any(part.casefold() in EXCLUDED for part in relative.parts)
            or private_resource(relative, root, overrides)):
        raise PermissionError("filesystem_experiment_private_resource")
    return label


def capture_snapshot(root, target, authorize, *, limit=256 * 1024):
    try:
        result = file_operation(
            root, target, "snapshot", authorize,
            content=_ReadOptions(limit, None, Event(), authorize))
    except FileBoundaryError as exc:
        if limit != 0 or str(exc) != "snapshot_budget_exceeded":
            raise
        return SnapshotResult(reason="snapshot_present")
    if result == SnapshotResult(reason="snapshot_not_found"):
        return result
    return checked_snapshot(result, limit, None)


class FilesystemSandbox:
    """A live host allocation, never reconstructed from persisted paths."""

    def __init__(self, original):
        self.original = safe_path(original)
        self.ref = None
        self.sources = {}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def check(self):
        if self.ref is None:
            raise PermissionError("filesystem_experiment_unavailable")
        with pin_execution_directory(self.ref.root) as pin:
            if pin.identity != self.identity:
                raise PermissionError("filesystem_experiment_identity_changed")

    def create(self, paths, authorize, *, overrides=None):
        if self.ref is not None:
            raise PermissionError("filesystem_experiment_already_allocated")
        labels = tuple(checked_label(path, self.original, overrides) for path in paths)
        if not labels or len(labels) > 64 or len(set(x.casefold() for x in labels)) != len(labels):
            raise ValueError("Explicit unique filesystem experiment inputs required")
        parent = safe_path(Path(tempfile.gettempdir()))
        if parent.is_relative_to(self.original):
            raise PermissionError("filesystem_experiment_overlaps_original")
        root = Path(tempfile.mkdtemp(prefix="sprout-filesystem-", dir=parent))
        with pin_execution_directory(root) as pin:
            self.identity = pin.identity
        self.ref = SandboxRef(str(uuid4()), KIND, root)
        receipts = {}
        try:
            with pin_execution_directory(self.original) as source_pin:
                self.original_identity = source_pin.identity
                for label in labels:
                    target = self.original / label

                    def check(target=target):
                        source_pin.check()
                        authorize(target)

                    snapshot = capture_snapshot(self.original, target, check)
                    if not snapshot.ok:
                        raise PermissionError("filesystem_experiment_snapshot_unavailable")
                    text = snapshot.data.decode("utf-8")
                    file_operation(root, root / label, "write", self.check,
                                   content=text, newline="")
                    self.sources[label] = snapshot
                    receipts[label] = {
                        "sha256": snapshot.digest, "identity": asdict(snapshot.identity),
                    }
        except BaseException:
            self.close()
            raise
        return self.ref, receipts

    def close(self):
        if self.ref is not None:
            self.check()
            shutil.rmtree(self.ref.root)
            self.ref = None
