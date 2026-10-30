"""Native conditional primitive only; no Apply authorization is implied."""
import hashlib
import os

import pytest

from Sprout.execution import file_access as access
from Sprout.execution.file_snapshot import ResourceIdentity


@pytest.mark.parametrize("mode", ["success", "version", "identity", "denied"])
def test_windows_conditional_handle_write(tmp_path, mode):
    if os.name != "nt":
        pytest.skip("Windows native test; separate fail-closed test covers other platforms")
    target = tmp_path / "target.py"
    target.write_bytes(b"original\n")
    expected = ResourceIdentity.from_stat(target.stat())
    old = hashlib.sha256(target.read_bytes()).hexdigest()
    if mode == "version":
        old = "0" * 64
    if mode == "identity":
        target.unlink()
        target.write_bytes(b"replacement\n")
    before = target.read_bytes()
    # Prove the native boundary is available before testing a negative outcome.
    access.file_operation(tmp_path, target, "probe", lambda: None)

    def authorize():
        if mode == "denied":
            raise PermissionError("denied")

    options = (expected, old, b"changed\n", authorize)
    if mode == "success":
        result = access.file_operation(tmp_path, target, "conditional_write", authorize,
                                       content=options)
        assert target.read_bytes() == b"changed\n"
        assert result["sha256"] == hashlib.sha256(b"changed\n").hexdigest()
    else:
        with pytest.raises((access.FileBoundaryError, PermissionError)):
            access.file_operation(tmp_path, target, "conditional_write", authorize,
                                  content=options)
        assert target.read_bytes() == before


def test_conditional_write_posix_refuses_before_effect(tmp_path, monkeypatch):
    target = tmp_path / "target.py"
    target.write_bytes(b"unchanged")
    monkeypatch.setattr(access.os, "name", "posix")
    with pytest.raises(access.FileBoundaryError, match="platform_unavailable"):
        access.file_operation(tmp_path, target, "conditional_write", lambda: None)
    assert target.read_bytes() == b"unchanged"
