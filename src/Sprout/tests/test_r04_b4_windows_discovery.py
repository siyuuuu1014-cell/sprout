"""Handle-enumeration contract tests; substitutes are not OS safety evidence."""
import ctypes
import os
import struct
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from Sprout.execution import file_access as access

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows handle ABI; not POSIX evidence")


def page(name="sample.py", attributes=0x20, file_id=7, next_offset=0):
    encoded = name.encode("utf-16-le")
    data = bytearray(104 + len(encoded) + 8)
    struct.pack_into("<I", data, 0, next_offset)
    struct.pack_into("<II", data, 56, attributes, len(encoded))
    struct.pack_into("<Q", data, 96, file_id)
    data[104:104 + len(encoded)] = encoded
    return bytes(data)


@pytest.mark.parametrize("kind", ["normal", "multiple", "bad_offset", "bad_name", "api_failure"])
def test_pinned_windows_page_parser(monkeypatch, kind):
    first = page()
    if kind == "multiple":
        first = page(next_offset=128)[:128] + page("other.py", file_id=8)
    elif kind == "bad_offset":
        first = page(next_offset=1)
    elif kind == "bad_name":
        data = bytearray(first)
        struct.pack_into("<I", data, 60, 65535)
        first = bytes(data)
    calls = []
    def query(handle, information, buffer, size):
        calls.append((handle, information, size))
        if kind == "api_failure":
            ctypes.set_last_error(5)
            return False
        if len(calls) == 1:
            ctypes.memmove(buffer, first, len(first))
            return True
        ctypes.set_last_error(18)
        return False
    monkeypatch.setattr(access, "_kernel", lambda: SimpleNamespace(
        GetFileInformationByHandleEx=query))
    if kind in {"bad_offset", "bad_name", "api_failure"}:
        with pytest.raises(access.FileBoundaryError):
            list(access._win_directory_records(17))
    else:
        records = list(access._win_directory_records(17))
        assert records == ([('sample.py', 0x20, 7), ('other.py', 0x20, 8)]
                           if kind == "multiple" else [('sample.py', 0x20, 7)])
        assert [call[1] for call in calls] == [11, 10]


@pytest.mark.parametrize("query_ok,device", [(False, ""), (True, r"\Device\Other")])
def test_volume_identity_gate_not_weakened(tmp_path, monkeypatch, query_ok, device):
    def query(drive, buffer, length):
        buffer.value = device
        return query_ok
    monkeypatch.setattr(access, "_kernel", lambda: SimpleNamespace(QueryDosDeviceW=query))
    with pytest.raises(access.FileBoundaryError, match="local_volume_identity_unproved"):
        access._win_volume(tmp_path)


async def test_windows_listing_volume_failure_does_not_authorize(tmp_path, monkeypatch):
    monkeypatch.setattr(access, "_kernel", lambda: SimpleNamespace(
        QueryDosDeviceW=lambda *a: False))
    authorize = Mock(side_effect=AssertionError("unproved platform reached authorization"))
    with pytest.raises(access.FileBoundaryError, match="directory_enumeration_unavailable"):
        await access.directory_entries(tmp_path, tmp_path, 10, authorize)
    authorize.assert_not_called()


@pytest.mark.parametrize("kind", ["normal", "denied", "link", "budget", "replaced", "cancel"])
async def test_windows_handle_contract_with_substitutes(tmp_path, monkeypatch, kind):
    import asyncio
    import msvcrt

    (tmp_path / "sample.py").write_text("controlled", encoding="utf-8")
    closed, opened = [], []
    monkeypatch.setattr(access, "_win_volume", lambda root: None)
    monkeypatch.setattr(access, "_win_directory", lambda *a: None)
    monkeypatch.setattr(access, "_win_name", lambda *a: None)
    def opening(path, flags):
        opened.append(path)
        return 1 if path == tmp_path else 2
    monkeypatch.setattr(access, "_win_open", opening)
    monkeypatch.setattr(access, "_kernel", lambda: SimpleNamespace(
        CloseHandle=closed.append))
    def info(handle):
        return SimpleNamespace(attributes=0x10 if handle == 1 else 0x20,
            volume=3, index_high=0, index_low=1 if handle == 1 else 7,
            links=1, written=SimpleNamespace(dwHighDateTime=0, dwLowDateTime=1))
    monkeypatch.setattr(access, "_win_info", info)
    monkeypatch.setattr(access, "_win_directory_records", lambda h: iter([
        ("sample.py", 0x400 if kind == "link" else 0x20, 8 if kind == "replaced" else 7)]))
    monkeypatch.setattr(msvcrt, "open_osfhandle", lambda *a: os.open(
        tmp_path / "sample.py", os.O_RDONLY))
    async def authorize(path):
        if kind == "cancel":
            raise asyncio.CancelledError()
        return kind != "denied"
    if kind in {"link", "budget", "replaced", "cancel"}:
        expected = asyncio.CancelledError if kind == "cancel" else access.FileBoundaryError
        with pytest.raises(expected):
            await access._windows_directory_entries(
                tmp_path, tmp_path, 0 if kind == "budget" else 10, authorize)
    else:
        result = await access._windows_directory_entries(tmp_path, tmp_path, 10, authorize)
        assert len(result) == (0 if kind == "denied" else 1)
        if result:
            assert result[0].identity.size == len(b"controlled")
    assert 1 in closed
    if kind in {"denied", "link", "budget", "cancel"}:
        assert opened == [tmp_path]


async def test_real_windows_discovery_and_read(tmp_path, capsys):
    """Real controlled Windows probe: failure is retained, not skipped or mocked."""
    root = tmp_path / "project"
    root.mkdir()
    (root / "main.py").write_text("def main(): return 1", encoding="utf-8")
    device = ctypes.create_unicode_buffer(1024)
    ctypes.set_last_error(0)
    result = access._kernel().QueryDosDeviceW(root.drive, device, len(device))
    code = ctypes.get_last_error()
    print("WINDOWS_VOLUME_EVIDENCE", root.drive, result, code, repr(device.value))
    async def authorize(path):
        assert path.parent == root
        return True
    entries = await access.directory_entries(root, root, 10, authorize)
    assert [entry.relative_path for entry in entries] == ["main.py"]
    assert access.file_operation(root, root / "main.py", "read", lambda: None) \
        == "def main(): return 1"
