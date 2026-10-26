"""Explicit host fixtures, not OS identity or secure-open platform evidence."""

import pytest

from Sprout.execution import file_broker
from Sprout.execution.file_snapshot import read_fd
from Sprout.gateway.identity import Principal
from Sprout.task.models import DelegationScope


@pytest.fixture
def authorized_read_host(tmp_path, monkeypatch):
    """Issue a controlled server subject and bounded selection for host tests.

    Keep real Policy/Scope/Broker checks. Only the platform open primitive is a
    temporary-file substitute, as in B2 tests; no directory discovery is mocked.
    """
    root = tmp_path.resolve()

    def boundary(workspace_root, target, operation, authorize, **kwargs):
        assert target.resolve().is_relative_to(root)
        assert kwargs.get("content") is not None, "Only snapshot reads are modeled"
        authorize()
        with target.open("rb") as stream:
            return read_fd(stream.fileno(), kwargs["content"])

    monkeypatch.setattr(file_broker, "file_operation", boundary)

    def issue(path, *, source):
        assert path.resolve().is_relative_to(root)
        principal = Principal(user_id="controlled-host-owner", source=source, authenticated=True)
        scope = DelegationScope(
            allowed_actions=frozenset({"tool.invoke", "file.read"}),
            allowed_paths=(str(path),),
        )
        return principal, scope

    return issue
