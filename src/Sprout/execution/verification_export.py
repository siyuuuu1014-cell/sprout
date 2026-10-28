"""Export dedicated verification inputs without Git/runtime/private resources."""

import hashlib
import os
from pathlib import Path

from Sprout.execution.docker_verification import safe_path
from Sprout.workspace.classifier import classify
from Sprout.workspace.models import ResourceKind

EXCLUDED = {
    ".git",
    ".venv",
    ".ssh",
    ".sprout",
    ".workbuddy",
    ".codex",
    ".agents",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
}


def export_authorized_view(view, destination, *, receipt):
    """F3-only byte-preserving export. No source path reads or discovery.

    The live Runtime receipt authorizes this exact managed destination, not a
    caller-provided path or a synthetic view. Failed partial exports stay sealed
    from publication and are never implicitly overwritten/reused.
    """
    from threading import Event

    from Sprout.execution.file_access import file_operation
    from Sprout.execution.file_snapshot import _ReadOptions, checked_snapshot
    from Sprout.execution.host_acceptance import pin_execution_directory
    from Sprout.execution.verification_manifest import current_frame

    frame = current_frame()
    record = frame.issuer._verification_frames[frame]
    destination = Path(destination)
    if (receipt is not frame or record.get("view") is not view
            or record["destination"] != destination or record.get("export_state") is not None):
        raise PermissionError("verification_export_receipt_denied")
    view.check(record["workspace"].id)
    prepared = []
    for entry in view.entries:
        if (entry.identity is None or private_resource(Path(entry.relative_path), destination)
                or any(part.casefold() in EXCLUDED for part in Path(entry.relative_path).parts)):
            raise PermissionError("verification_export_resource_denied")
        # Explicit current capability: UTF-8 source/config/test inputs, retaining
        # BOM and CRLF bytes. Non-UTF8 never falls back to lossy text decoding.
        text = entry.data.decode("utf-8", errors="strict")
        if text.encode("utf-8") != entry.data:
            raise PermissionError("verification_export_encoding")
        prepared.append((entry, text))
    if not prepared or destination.exists() or destination.is_symlink():
        raise PermissionError("verification_export_destination_exists")
    record["export_state"] = "writing"
    try:
        with pin_execution_directory(record["parent"]) as parent_pin:
            if parent_pin.identity != record["parent_identity"]:
                raise PermissionError("verification_export_parent_changed")
            def check():
                if current_frame() is not frame or record.get("export_state") != "writing":
                    raise PermissionError("verification_export_cancelled")
                view.check()
                record["pin"].check()
                parent_pin.check()
            receipts = {}
            for entry, text in prepared:
                target = destination / entry.relative_path
                check()
                file_operation(record["parent"], target, "write", check,
                               content=text, newline="")
                result = file_operation(record["parent"], target, "snapshot", check,
                    content=_ReadOptions(len(entry.data) or 1, None, Event(), check))
                result = checked_snapshot(result, len(entry.data), None)
                if not result.ok or result.data != entry.data or result.digest != entry.digest:
                    raise PermissionError("verification_export_content_changed")
                from dataclasses import asdict

                receipts[entry.relative_path] = {"digest": result.digest,
                                                "identity": asdict(result.identity)}
            check()
            with pin_execution_directory(destination) as export_pin:
                root_identity = export_pin.identity
            record["export_state"] = "sealed"
            return {"root": str(destination), "root_identity": root_identity,
                    "files": receipts, "workspace": view.workspace_id}
    except BaseException:
        record["export_state"] = "failed"
        raise


async def check_export_receipt(receipt, *, revalidate=None):
    """Check a Runtime-held receipt, never turn a supplied JSON into a grant."""
    from threading import Event

    from Sprout.execution.file_access import directory_entries, file_operation
    from Sprout.execution.file_snapshot import ResourceIdentity, _ReadOptions, checked_snapshot
    from Sprout.execution.host_acceptance import pin_execution_directory

    root = Path(receipt["root"])
    with pin_execution_directory(root) as pin:
        if tuple(receipt["root_identity"]) != pin.identity:
            raise PermissionError("verification_export_root_changed")
        # Only names/identities are observed here. An unexpected name is denied
        # before opening it or reading any content, including hard-link aliases.
        expected_names = set(receipt["files"])
        directories = {""}
        for name in expected_names:
            from Sprout.workspace.authorized_content import relative_label

            relative_label(name)
            directories.update(p.as_posix() for p in Path(name).parents if p != Path("."))

        async def inventory():
            seen = set()
            for directory in sorted(directories):
                if revalidate is not None:
                    await revalidate()
                async def selected(path):
                    pin.check()
                    relative = path.relative_to(root).as_posix()
                    if relative not in expected_names | directories:
                        raise PermissionError("verification_export_inventory_changed")
                    return True
                entries = await directory_entries(root, root / directory, 129, selected)
                for entry in entries:
                    name = entry.relative_path
                    if entry.is_directory != (name in directories):
                        raise PermissionError("verification_export_entry_type_changed")
                    seen.add(name)
            if seen != (expected_names | (directories - {""})):
                raise PermissionError("verification_export_inventory_changed")
            pin.check()

        await inventory()
        for name, entry in receipt["files"].items():
            if revalidate is not None:
                await revalidate()
            from Sprout.workspace.authorized_content import relative_label

            relative_label(name)
            expected = ResourceIdentity(**entry["identity"])
            result = file_operation(root, root / name, "snapshot", pin.check,
                content=_ReadOptions(expected.size or 1, expected, Event(), pin.check))
            result = checked_snapshot(result, expected.size, expected)
            if not result.ok or result.digest != entry["digest"]:
                raise PermissionError("verification_export_changed")
        await inventory()
        if revalidate is not None:
            await revalidate()
        pin.check()
        return {name: entry["digest"] for name, entry in receipt["files"].items()}


def private_resource(relative, root, overrides=None):
    denied = {ResourceKind.SECRET, ResourceKind.SENSITIVE, ResourceKind.DATA, ResourceKind.EXTERNAL}
    return (
        classify(relative, workspace_root=root) in denied
        or classify(relative, workspace_root=root, overrides=overrides) in denied
    )


def export_input(
    repo: Path, destination: Path, original: Path, *, classify_overrides=None, exclude_paths=(),
    manifest_only=False
) -> dict[str, str]:
    repo, original = safe_path(repo), safe_path(original)
    exclusions = []
    for name in exclude_paths:
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise ValueError("Export exclusions must be confined relative paths")
        exclusions.append(relative)

    def excluded(path):
        relative = path.relative_to(repo)
        return any(relative == name or relative.is_relative_to(name) for name in exclusions)
    destination = destination.resolve()
    if any(
        destination.is_relative_to(root) or root.is_relative_to(destination)
        for root in (repo, original)
    ):
        raise ValueError("Export destination must not overlap input or original workspace")
    if not manifest_only:
        destination.mkdir(parents=True, exist_ok=False)
    manifest = {}
    total = 0
    for parent, directories, files in os.walk(repo, followlinks=False):
        for name in (*directories, *files):
            path = Path(parent) / name
            if path.is_symlink() or not path.resolve().is_relative_to(repo):
                raise ValueError("Export input contains a link or external resource")
        directories[:] = [name for name in directories
                          if name.casefold() not in EXCLUDED and not excluded(Path(parent) / name)]
        for name in files:
            path = Path(parent) / name
            relative = path.relative_to(repo)
            if name.casefold() in EXCLUDED or excluded(path):
                continue
            if private_resource(relative, repo, classify_overrides):
                raise ValueError("Private/data input requires an explicit supported adapter")
            content = path.read_bytes()
            total += len(content)
            if total > 32 * 1024 * 1024 or len(manifest) >= 10000:
                raise ValueError("Verification export size limit exceeded")
            if not manifest_only:
                target = destination / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            manifest[relative.as_posix()] = hashlib.sha256(content).hexdigest()
    if not manifest:
        raise ValueError("Verification export must not be empty")
    return manifest
