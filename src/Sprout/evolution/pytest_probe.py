"""Standalone pytest observer launched with Python -I; no Sprout imports.

This is observation, not protection against malicious tests in the same process.
"""

import hashlib
import json
import sys
from pathlib import Path


class Observer:
    __name__ = "sprout_growth_observer"

    def __init__(self):
        self.collected = []
        self.reports = []
        self.collection_errors = []
        self.configuration = {}

    def pytest_configure(self, config):
        config_path = config.inipath
        self.configuration = {
            "rootpath": str(config.rootpath),
            "inipath": str(config_path) if config_path else None,
            "config_hash": hashlib.sha256(config_path.read_bytes()).hexdigest()
            if config_path
            else None,
            "relative_config": config_path.relative_to(config.rootpath).as_posix()
            if config_path and config_path.is_relative_to(config.rootpath)
            else None,
            "plugins": sorted(
                (
                    f"{type(plugin).__module__}:{type(plugin).__qualname__}"
                    if str(name).isdigit()
                    else str(name).replace(str(config.rootpath), "<repo>")
                )
                for name, plugin in config.pluginmanager.list_name_plugin()
            ),
        }

    def pytest_collection_finish(self, session):
        self.pytest_configure(session.config)
        self.collected = [item.nodeid for item in session.items]

    def pytest_collectreport(self, report):
        if report.failed:
            self.collection_errors.append(report.nodeid)

    def pytest_runtest_logreport(self, report):
        self.reports.append(
            {
                "nodeid": report.nodeid,
                "phase": report.when,
                "outcome": report.outcome,
                "duration": report.duration,
                "wasxfail": bool(getattr(report, "wasxfail", False)),
            }
        )


def main():
    output, nonce, *arguments = sys.argv[1:]
    import pytest

    observer = Observer()
    exit_code = int(pytest.main(arguments, plugins=[observer]))
    payload = {
        "schema_version": 1,
        "nonce": nonce,
        "exit_code": exit_code,
        "collected": observer.collected,
        "reports": observer.reports,
        "collection_errors": observer.collection_errors,
        "configuration": observer.configuration,
        "runner": {
            "python": sys.version,
            "executable": sys.executable,
            "pytest_version": pytest.__version__,
            "pytest_module": str(Path(pytest.__file__).resolve()),
            "probe_hash": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        },
        "trusted_execution": False,
    }
    if output == "-":
        # The host evidence directory is never writable/mounted in the container.
        print("SPROUT_PYTEST_REPORT " + nonce + " " + json.dumps(payload, ensure_ascii=False))
    else:
        with Path(output).open("x", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
