"""Identity helper must not inherit model credentials (unit double only)."""
from types import SimpleNamespace

from Sprout.gateway import local_identity


def test_windows_identity_uses_credential_free_child_environment(monkeypatch):
    import ctypes

    monkeypatch.setenv("DEEPSEEK_API_KEY", "controlled-test-credential")
    monkeypatch.setenv("UNRELATED_SECRET", "controlled-other-credential")
    monkeypatch.setenv("SYSTEMROOT", "C:\\Windows")
    def directory(buffer, size):
        buffer.value = "C:\\Windows\\System32"
        return len(buffer.value)
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(kernel32=SimpleNamespace(
        GetSystemDirectoryW=directory)), raising=False)
    captured = {}
    def run(command, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(stdout='"controlled-user","S-1-5-21-123"\n')
    monkeypatch.setattr(local_identity.subprocess, "run", run)
    assert local_identity._windows_sid() == "S-1-5-21-123"
    assert "env" in captured
    assert "DEEPSEEK_API_KEY" not in captured["env"]
    assert "UNRELATED_SECRET" not in captured["env"]
    assert captured["env"]["SYSTEMROOT"] == "C:\\Windows"
