"""FileBroker's private handle-bound I/O primitive, not an alternate broker.

No policy, caller identity or approvals originate here. The broker's final
authorization callback runs while trusted ancestors are pinned, before effects.
Symlinks/reparse points (including within-root links) are conservatively refused.
"""

from __future__ import annotations

import ctypes
import os
import re
import stat
from collections.abc import Callable
from contextlib import ExitStack
from functools import lru_cache
from pathlib import Path


class FileBoundaryError(OSError):
    """A secure resource identity could not be established; never retry loosely."""


async def directory_entries(root: Path, target: Path, limit: int, authorize_entry):
    """Private pinned POSIX listing. No Windows/path traversal fallback.

    Names remain broker-internal until separately authorized. Child metadata is
    not opened before its visibility check. No worker thread outlives cancellation;
    ExitStack closes pinned descriptors even across a cancelled authority await.
    """
    from Sprout.execution.file_snapshot import DirectoryEntry, ResourceIdentity

    if (os.name != "posix" or os.scandir not in os.supports_fd
            or os.open not in os.supports_dir_fd or os.stat not in os.supports_dir_fd
            or os.stat not in os.supports_follow_symlinks
            or not all(hasattr(os, flag) for flag in ("O_NOFOLLOW", "O_DIRECTORY"))):
        raise FileBoundaryError("directory_enumeration_unavailable")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    with ExitStack() as stack:
        fd = os.open("/", flags)
        stack.callback(os.close, fd)
        for part in (*root.parts[1:], *target.relative_to(root).parts):
            fd = os.open(part, flags, dir_fd=fd)
            stack.callback(os.close, fd)
        before = ResourceIdentity.from_stat(os.fstat(fd))
        entries = []
        # scandir(fd) streams bounded entries; listdir would allocate unbounded names.
        with os.scandir(fd) as stream:
            for index, entry in enumerate(stream):
                if index >= limit:
                    raise FileBoundaryError("directory_budget_exceeded")
                name = entry.name
                if name in {".", ".."} or "/" in name or "\\" in name:
                    raise FileBoundaryError("directory_entry_invalid")
                if not await authorize_entry(target / name):
                    continue
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                    raise FileBoundaryError("directory_link_or_special_file")
                if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
                    raise FileBoundaryError("directory_shared_file")
                entries.append(DirectoryEntry(
                    (target / name).relative_to(root).as_posix(), stat.S_ISDIR(info.st_mode),
                    ResourceIdentity.from_stat(info),
                ))
        if ResourceIdentity.from_stat(os.fstat(fd)) != before:
            raise FileBoundaryError("directory_identity_changed")
        return tuple(entries)


def file_operation(
    root: Path, target: Path, operation: str, authorize: Callable[[], None],
    *, content: str = "", newline: str | None = None,
):
    if os.name == "nt":
        return _windows(root, target, operation, authorize, content, newline)
    if (os.name == "posix"
            and {os.open, os.mkdir, os.stat, os.unlink} <= os.supports_dir_fd
            and os.stat in os.supports_follow_symlinks
            and all(hasattr(os, flag) for flag in ("O_NOFOLLOW", "O_DIRECTORY", "O_NONBLOCK"))):
        return _posix(root, target, operation, authorize, content, newline)
    raise FileBoundaryError("secure_open_unavailable")


def _missing(operation, authorize):
    authorize()
    return "" if operation == "read" else False


def _stream(fd: int, operation: str, content: str, newline: str | None):
    if operation == "snapshot":
        from Sprout.execution.file_snapshot import read_fd

        return read_fd(fd, content)
    if operation == "write":
        # Never truncate at pathname-open time: identity must already be proved.
        os.ftruncate(fd, 0)
    with open(fd, "w" if operation == "write" else "r", encoding="utf-8",
                 newline=newline, closefd=False) as stream:
        if operation == "read":
            return stream.read()
        stream.write(content)
    return True


def _posix(root, target, operation, authorize, content, newline):
    relative = target.relative_to(root).parts
    if not relative and operation != "exists":
        raise FileBoundaryError("sandbox_root_is_not_a_file")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    with ExitStack() as stack:
        parent = os.open("/", flags)
        stack.callback(os.close, parent)
        # Pin even ancestors above the explicit sandbox: never follow a linked
        # root, and never derive an absolute base from the process CWD.
        for part in root.parts[1:]:
            parent = os.open(part, flags, dir_fd=parent)
            stack.callback(os.close, parent)
        if not relative:
            authorize()  # The sandbox root itself is a pinned directory.
            return True
        for part in relative[:-1]:
            try:
                child = os.open(part, flags, dir_fd=parent)
            except FileNotFoundError:
                if operation != "write":
                    return _missing(operation, authorize)
                authorize()
                os.mkdir(part, dir_fd=parent)
                child = os.open(part, flags, dir_fd=parent)
            stack.callback(os.close, child)
            parent = child
        authorize()  # Directory creation is not authority for a later leaf effect.
        name = relative[-1]
        if operation == "delete":
            # unlinkat removes this directory entry, never follows a swapped
            # symlink. Refuse a currently linked entry, but keep dir_fd binding
            # even if it changes immediately after this metadata observation.
            try:
                info = os.stat(name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                return False
            if not stat.S_ISREG(info.st_mode):
                raise FileBoundaryError("non_regular_file")
            authorize()
            os.unlink(name, dir_fd=parent)
            return True
        access = os.O_WRONLY | os.O_CREAT if operation == "write" else os.O_RDONLY
        try:
            fd = os.open(name, access | os.O_NOFOLLOW | os.O_NONBLOCK
                         | getattr(os, "O_CLOEXEC", 0), 0o666, dir_fd=parent)
        except FileNotFoundError:
            return "" if operation == "read" else False
        stack.callback(os.close, fd)
        info = os.fstat(fd)
        if operation == "exists" and stat.S_ISDIR(info.st_mode):
            authorize()
            return True
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise FileBoundaryError("non_regular_or_shared_file")
        authorize()  # Recheck before reading data or truncating the pinned file.
        if operation in {"probe", "exists"}:
            return True
        return _stream(fd, operation, content, newline)


@lru_cache(maxsize=1)
def _kernel():
    # Imported/configured only on Windows, not at package import on POSIX.
    from ctypes import wintypes as w

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    definitions = {
        "CreateFileW": ([w.LPCWSTR, w.DWORD, w.DWORD, w.LPVOID, w.DWORD, w.DWORD, w.HANDLE],
                        w.HANDLE),
        "CloseHandle": ([w.HANDLE], w.BOOL),
        "GetFileInformationByHandle": ([w.HANDLE, w.LPVOID], w.BOOL),
        "GetFileInformationByHandleEx": ([w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD], w.BOOL),
        "SetFileInformationByHandle": ([w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD], w.BOOL),
        "GetFileType": ([w.HANDLE], w.DWORD),
        "CreateDirectoryW": ([w.LPCWSTR, w.LPVOID], w.BOOL),
        "QueryDosDeviceW": ([w.LPCWSTR, w.LPWSTR, w.DWORD], w.DWORD),
        "GetFinalPathNameByHandleW": ([w.HANDLE, w.LPWSTR, w.DWORD, w.DWORD], w.DWORD),
    }
    for name, (args, result) in definitions.items():
        method = getattr(kernel, name)
        method.argtypes, method.restype = args, result
    return kernel


def _win_info(handle):
    from ctypes import wintypes as w

    class Information(ctypes.Structure):
        _fields_ = [("attributes", w.DWORD), ("created", w.FILETIME),
                    ("accessed", w.FILETIME), ("written", w.FILETIME),
                    ("volume", w.DWORD), ("size_high", w.DWORD), ("size_low", w.DWORD),
                    ("links", w.DWORD), ("index_high", w.DWORD), ("index_low", w.DWORD)]

    info = Information()
    kernel = _kernel()
    if not kernel.GetFileInformationByHandle(handle, ctypes.byref(info)):
        raise FileBoundaryError("file_information_unavailable")
    if info.attributes & 0x400 or kernel.GetFileType(handle) != 1:
        raise FileBoundaryError("reparse_or_non_disk_file")
    return info


def _win_open(path, access, *, disposition=3):
    # OPEN_REPARSE_POINT; BACKUP_SEMANTICS permits opening directory metadata.
    # No SHARE_DELETE or SHARE_WRITE: prior incompatible writers prevent the
    # open, and subsequent renames/reparse mutations are denied until close.
    kernel = _kernel()
    handle = kernel.CreateFileW(str(path), access, 1, None, disposition, 0x02200000, None)
    if handle == ctypes.c_void_p(-1).value:
        code = ctypes.get_last_error()
        if code in {2, 3}:
            raise FileNotFoundError("secure_target_missing")
        raise FileBoundaryError(f"secure_open_refused:{code}")
    return handle


def _win_directory(path, stack):
    from ctypes import wintypes as w

    handle = _win_open(path, 0x80)  # FILE_READ_ATTRIBUTES
    stack.callback(_kernel().CloseHandle, handle)
    if not _win_info(handle).attributes & 0x10:
        raise FileBoundaryError("ancestor_not_directory")
    _win_name(handle, path)
    flags = w.DWORD()
    # Windows lexical scope comparison is case-insensitive. A directory whose
    # filesystem overrides that convention cannot use this backend safely.
    if (not _kernel().GetFileInformationByHandleEx(handle, 23, ctypes.byref(flags), 4)
            or flags.value & 1):
        raise FileBoundaryError("case_sensitive_or_unproved_directory")
    return handle


def _win_name(handle, expected):
    buffer = ctypes.create_unicode_buffer(32768)
    size = _kernel().GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
    if not size or size >= len(buffer):
        raise FileBoundaryError("final_path_unproved")
    actual = buffer.value
    if not actual.startswith("\\\\?\\"):
        raise FileBoundaryError("final_path_namespace_unproved")
    # This prefix is from the OS handle, never accepted from input. Refuse
    # DOS short-name aliases rather than classify a secret under a benign alias.
    if Path(actual[4:]) != expected:
        raise FileBoundaryError("final_path_alias_mismatch")


def _windows(root, target, operation, authorize, content, newline):
    import msvcrt
    from ctypes import wintypes as w

    if root.drive.startswith("\\\\"):
        raise FileBoundaryError("remote_filesystem_not_supported")
    relative = target.relative_to(root).parts
    if not relative and operation != "exists":
        raise FileBoundaryError("sandbox_root_is_not_a_file")
    kernel = _kernel()
    device = ctypes.create_unicode_buffer(1024)
    if (not kernel.QueryDosDeviceW(root.drive, device, len(device))
            or not re.fullmatch(r"\\Device\\HarddiskVolume\d+", device.value)):
        raise FileBoundaryError("local_volume_identity_unproved")
    with ExitStack() as stack:
        parent = Path(root.anchor)
        _win_directory(parent, stack)
        for part in root.parts[1:]:
            parent = parent / part
            _win_directory(parent, stack)
        if not relative:
            authorize()  # Root identity/reparse/case checks have already run.
            return True
        for part in relative[:-1]:
            parent = parent / part
            try:
                _win_directory(parent, stack)
            except FileNotFoundError:
                if operation != "write":
                    return _missing(operation, authorize)
                authorize()
                if not kernel.CreateDirectoryW(str(parent), None):
                    raise FileBoundaryError("secure_directory_creation_refused") from None
                _win_directory(parent, stack)
        authorize()  # Recheck before OPEN_ALWAYS may create a leaf.
        access = {"write": 0x40000000, "read": 0x80000000, "snapshot": 0x80000000,
                  "delete": 0x10080, "probe": 0x80, "exists": 0x80}[operation]
        try:
            handle = _win_open(parent / relative[-1], access,
                               disposition=4 if operation == "write" else 3)
        except FileNotFoundError:
            return "" if operation == "read" else False
        try:
            info = _win_info(handle)
            _win_name(handle, parent / relative[-1])
            if operation == "exists" and info.attributes & 0x10:
                _win_directory(parent / relative[-1], stack)
                authorize()
                return True
            if info.attributes & 0x10 or info.links != 1:
                raise FileBoundaryError("non_regular_or_shared_file")
            authorize()  # Pinned identity does not freeze time-dependent authority.
            if operation in {"probe", "exists"}:
                return True
            if operation == "delete":
                delete = w.BOOL(True)
                if not kernel.SetFileInformationByHandle(handle, 4, ctypes.byref(delete), 4):
                    raise FileBoundaryError("handle_delete_refused")
                return True
            fd = msvcrt.open_osfhandle(handle, os.O_BINARY | (
                os.O_WRONLY if operation == "write" else os.O_RDONLY
            ))
            handle = None  # Ownership transferred to the CRT fd.
            try:
                return _stream(fd, operation, content, newline)
            finally:
                os.close(fd)
        finally:
            if handle is not None:
                kernel.CloseHandle(handle)
