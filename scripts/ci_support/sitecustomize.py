"""Opt-in process-local home and network isolation for baseline tests.

The runner adds this directory to PYTHONPATH only in test subprocesses. This is
test hygiene, not an OS sandbox. No production modules or user settings change.
"""

import ipaddress
import os
import socket
from pathlib import Path

if os.environ.get("SPROUT_BASELINE_HOME"):
    _test_home = Path(os.environ["SPROUT_BASELINE_HOME"])
    Path.home = classmethod(lambda cls: cls(_test_home))

if os.environ.get("SPROUT_BASELINE_OFFLINE") == "1":
    _connect = socket.socket.connect
    _connect_ex = socket.socket.connect_ex

    def _check(address):
        if isinstance(address, tuple):
            host = str(address[0])
            if host == "localhost":
                return
            try:
                if ipaddress.ip_address(host).is_loopback:
                    return
            except ValueError:
                pass
            raise OSError("baseline L1 forbids non-loopback network connections")

    def _offline_connect(self, address):
        _check(address)
        return _connect(self, address)

    def _offline_connect_ex(self, address):
        _check(address)
        return _connect_ex(self, address)

    socket.socket.connect = _offline_connect
    socket.socket.connect_ex = _offline_connect_ex
