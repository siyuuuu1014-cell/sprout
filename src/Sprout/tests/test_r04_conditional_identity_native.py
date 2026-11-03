"""Same-boundary stat/fstat diagnostics; no Runtime or project Apply authority."""
import hashlib
import json
import os
from dataclasses import asdict

import pytest

from Sprout.execution import file_access as access
from Sprout.execution.file_snapshot import ResourceIdentity

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows native handle boundary')


@pytest.mark.parametrize('mode', ['stable', 'changed_identity', 'wrong_handle'])
def test_conditional_identity_boundary(tmp_path, monkeypatch, mode):
    target = tmp_path / 'target.py'
    target.write_bytes(b'original\n')
    # Force write and creation timestamps apart; immediate writes can otherwise
    # mask Python 3.13's stat/fstat ctime disagreement on Windows.
    os.utime(target, ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))
    expected = ResourceIdentity.from_stat(target.stat())
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    actual_target = target
    if mode == 'changed_identity':
        replacement = tmp_path / 'replacement.py'
        replacement.write_bytes(b'original\n')
        os.replace(replacement, target)
    elif mode == 'wrong_handle':
        actual_target = tmp_path / 'other.py'
        actual_target.write_bytes(b'original\n')
    evidence = []
    original = access._conditional_write_fd

    def observe(fd, options):
        pathname = ResourceIdentity.from_stat(actual_target.stat())
        handle = ResourceIdentity.from_stat(os.fstat(fd))
        evidence.append({'mode': mode, 'expected': asdict(expected),
                         'pathname_stat': asdict(pathname), 'handle_fstat': asdict(handle),
                         'different_fields': [k for k in asdict(handle)
                                              if asdict(pathname)[k] != asdict(handle)[k]]})
        return original(fd, options)

    monkeypatch.setattr(access, '_conditional_write_fd', observe)
    try:
        if mode == 'stable':
            access.file_operation(tmp_path, actual_target, 'conditional_write', lambda: None,
                                  content=(expected, digest, b'changed\n', lambda: None))
            assert target.read_bytes() == b'changed\n'
        else:
            with pytest.raises(access.ConditionalWriteRefused, match='identity_changed'):
                access.file_operation(tmp_path, actual_target, 'conditional_write', lambda: None,
                                      content=(expected, digest, b'changed\n', lambda: None))
            assert target.read_bytes() == actual_target.read_bytes() == b'original\n'
    finally:
        (tmp_path / 'IDENTITY_BOUNDARY.json').write_text(json.dumps(evidence, indent=2))
