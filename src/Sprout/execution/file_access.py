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


class ConditionalWriteRefused(FileBoundaryError):
    """The pinned conditional primitive proved refusal before any write."""


async def directory_entries(root: Path, target: Path, limit: int, authorize_entry):
    """Private handle-bound listing. No ordinary pathname traversal fallback.

    Names remain broker-internal until separately authorized. Child metadata is
    not opened before its visibility check. No worker thread outlives cancellation;
    ExitStack closes pinned descriptors even across a cancelled authority await.
    """
    from Sprout.execution.file_snapshot import DirectoryEntry, ResourceIdentity

    if os.name == "nt":
        try:
            return await _windows_directory_entries(root, target, limit, authorize_entry)
        except FileBoundaryError as exc:
            if str(exc) == "local_volume_identity_unproved":
                raise FileBoundaryError("directory_enumeration_unavailable") from exc
            raise
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
    if operation == "conditional_write" and os.name != "nt":
        raise FileBoundaryError("conditional_write_platform_unavailable")
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
    if operation == "conditional_write":
        return _conditional_write_fd(fd, content)
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


def _conditional_write_fd(fd, options):
    """Private R03 primitive, not authority; caller must persist write-start first.

    Windows secure-open holds no SHARE_WRITE/DELETE throughout this operation.
    Any exception after write-start is an uncertain effect to the caller.
    """
    import hashlib

    from Sprout.execution.file_snapshot import ResourceIdentity

    if not isinstance(options, tuple) or len(options) != 4:
        raise ConditionalWriteRefused("conditional_write_options_invalid")
    expected, old_digest, data, check = options
    if (type(expected) is not ResourceIdentity or type(data) is not bytes
            or len(data) > 16 * 1024 * 1024 or not callable(check)
            or type(old_digest) is not str or len(old_digest) != 64):
        raise ConditionalWriteRefused("conditional_write_options_invalid")
    before = ResourceIdentity.from_stat(os.fstat(fd))
    if before != expected or before.size > 16 * 1024 * 1024:
        raise ConditionalWriteRefused("conditional_write_identity_changed")
    os.lseek(fd, 0, os.SEEK_SET)
    old = bytearray()
    while chunk := os.read(fd, 65536):
        old.extend(chunk)
        if len(old) > before.size:
            raise ConditionalWriteRefused("conditional_write_identity_changed")
    if (ResourceIdentity.from_stat(os.fstat(fd)) != before
            or hashlib.sha256(old).hexdigest() != old_digest):
        raise ConditionalWriteRefused("conditional_write_version_changed")
    check()  # Final authority check on the same exclusive, verified handle.
    os.lseek(fd, 0, os.SEEK_SET)
    view = memoryview(data)
    while view:
        count = os.write(fd, view)
        if count <= 0:
            raise FileBoundaryError("conditional_write_unconfirmed")
        view = view[count:]
    os.ftruncate(fd, len(data))
    os.fsync(fd)
    os.lseek(fd, 0, os.SEEK_SET)
    actual = bytearray()
    while chunk := os.read(fd, 65536):
        actual.extend(chunk)
        if len(actual) > len(data):
            raise FileBoundaryError("conditional_write_unconfirmed")
    if actual != data:
        raise FileBoundaryError("conditional_write_unconfirmed")
    return {"sha256": hashlib.sha256(actual).hexdigest(), "size": len(actual)}


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


def _win_volume(root):
    if root.drive.startswith("\\\\"):
        raise FileBoundaryError("remote_filesystem_not_supported")
    device = ctypes.create_unicode_buffer(1024)
    if (not _kernel().QueryDosDeviceW(root.drive, device, len(device))
            or not re.fullmatch(r"\\Device\\HarddiskVolume\d+", device.value)):
        raise FileBoundaryError("local_volume_identity_unproved")


def _win_directory_records(handle):
    """Bounded FILE_ID_BOTH_DIR_INFO pages, obtained from the pinned handle.

    FileIdBothDirectoryRestartInfo=11, continuation=10 (Win32 API).
    Records are broker-internal; caller must authorize names before opening or
    publishing child resources. Unsupported drivers fail closed.
    """
    from ctypes import wintypes as w

    class Entry(ctypes.Structure):
        _fields_ = [("next", w.DWORD), ("index", w.DWORD),
                    ("created", ctypes.c_longlong), ("accessed", ctypes.c_longlong),
                    ("written", ctypes.c_longlong), ("changed", ctypes.c_longlong),
                    ("size", ctypes.c_longlong), ("allocated", ctypes.c_longlong),
                    ("attributes", w.DWORD), ("name_length", w.DWORD),
                    ("ea", w.DWORD), ("short_length", ctypes.c_byte),
                    ("short_name", w.WCHAR * 12), ("file_id", ctypes.c_longlong),
                    ("name", w.WCHAR * 1)]

    if Entry.name.offset != 104:
        raise FileBoundaryError("directory_layout_unavailable")
    buffer = ctypes.create_string_buffer(65536)
    info_class = 11
    while True:
        if not _kernel().GetFileInformationByHandleEx(handle, info_class, buffer, len(buffer)):
            if ctypes.get_last_error() == 18:  # ERROR_NO_MORE_FILES, not a guessed empty list.
                return
            raise FileBoundaryError("directory_enumeration_unavailable")
        info_class = 10
        offset = 0
        while True:
            if offset + ctypes.sizeof(Entry) > len(buffer):
                raise FileBoundaryError("directory_record_invalid")
            item = Entry.from_buffer(buffer, offset)
            size = item.name_length
            end = offset + Entry.name.offset + size
            if (size == 0 or size % 2 or size > 32760 or end > len(buffer)
                    or (item.next and (item.next % 8 or item.next < Entry.name.offset + size
                                       or offset + item.next >= len(buffer)))):
                raise FileBoundaryError("directory_record_invalid")
            name = bytes(buffer[offset + Entry.name.offset:end]).decode("utf-16-le", "strict")
            yield name, item.attributes, item.file_id & ((1 << 64) - 1)
            if not item.next:
                break
            offset += item.next


async def _windows_directory_entries(root, target, limit, authorize_entry):
    import msvcrt

    from Sprout.execution.file_snapshot import DirectoryEntry, ResourceIdentity

    _win_volume(root)
    relative = target.relative_to(root).parts
    with ExitStack() as stack:
        parent = Path(root.anchor)
        _win_directory(parent, stack)
        for part in (*root.parts[1:], *relative):
            parent /= part
            _win_directory(parent, stack)
        handle = _win_open(target, 0x81)  # FILE_LIST_DIRECTORY | FILE_READ_ATTRIBUTES
        stack.callback(_kernel().CloseHandle, handle)
        _win_name(handle, target)

        def signature():
            info = _win_info(handle)
            if not info.attributes & 0x10:
                raise FileBoundaryError("ancestor_not_directory")
            return (info.attributes, info.volume, info.index_high, info.index_low,
                    info.written.dwHighDateTime, info.written.dwLowDateTime)

        before = signature()
        entries, count = [], 0
        for name, attributes, file_id in _win_directory_records(handle):
            if name in {".", ".."}:
                continue
            count += 1
            if count > limit:
                raise FileBoundaryError("directory_budget_exceeded")
            if (any(c in name for c in ("/", "\\", ":", "\x00"))
                    or name.endswith((".", " "))):
                raise FileBoundaryError("directory_entry_invalid")
            child = target / name
            if not await authorize_entry(child):
                continue
            if attributes & (0x400 | 0x40):  # reparse or device; never traverse.
                raise FileBoundaryError("directory_link_or_special_file")
            child_handle = _win_open(child, 0x80)
            try:
                info = _win_info(child_handle)
                _win_name(child_handle, child)
                if (info.volume != before[1] or info.attributes != attributes
                        or (info.index_high << 32 | info.index_low) != file_id
                        or (not attributes & 0x10 and info.links != 1)):
                    raise FileBoundaryError("directory_identity_changed")
                fd = msvcrt.open_osfhandle(child_handle, os.O_RDONLY | os.O_BINARY)
                child_handle = None
                try:
                    identity = ResourceIdentity.from_stat(os.fstat(fd))
                finally:
                    os.close(fd)
            finally:
                if child_handle is not None:
                    _kernel().CloseHandle(child_handle)
            entries.append(DirectoryEntry(child.relative_to(root).as_posix(),
                                          bool(attributes & 0x10), identity))
        if signature() != before:
            raise FileBoundaryError("directory_identity_changed")
        return tuple(entries)


def _windows(root, target, operation, authorize, content, newline):
    import msvcrt
    from ctypes import wintypes as w

    _win_volume(root)
    relative = target.relative_to(root).parts
    if not relative and operation != "exists":
        raise FileBoundaryError("sandbox_root_is_not_a_file")
    kernel = _kernel()
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
        access = {"conditional_write": 0xC0000000, "write": 0x40000000,
                  "read": 0x80000000, "snapshot": 0x80000000,
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
                os.O_RDWR if operation == "conditional_write" else
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
