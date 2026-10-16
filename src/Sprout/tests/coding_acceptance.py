"""Developer acceptance entry, not a production task API.

Requires the source checkout and dev dependencies. Paid coding and Git approval
are separate explicit commands; status/check never run a model or a worker.
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

from Sprout.config.loader import load_env_file, load_settings
from Sprout.gateway.local_identity import current_cli_principal
from Sprout.storage.local.sqlite.commit_status import commit_status_report


def request_limit(value):
    number = int(value)
    if not 1 <= number <= 20:
        raise argparse.ArgumentTypeError("Authorized remaining requests must be 1..20")
    return number


def status(report_path):
    report = json.loads(report_path.read_text(encoding="utf-8"))
    database = Path(report["evidence_dir"]).resolve() / "metadata.db"
    if not database.is_relative_to(report_path.parent.resolve()):
        raise ValueError("Report must refer to its own experiment directory")
    # The report only locates evidence. Current authority comes from SQLite.
    return commit_status_report(database, report["task_id"],
                                principal=current_cli_principal())


def run(config, output, limit, image, sample="addition"):
    from Sprout.tests.acceptance_samples import sample_definition

    sample_definition(sample)
    output = output.resolve()
    if output.exists() or not output.parent.is_dir():
        raise ValueError("Fresh experiment directory under an existing parent required")
    if not config.is_file():
        raise ValueError("Explicit existing config file required")
    if not image.startswith("sha256:") or len(image) != 71:
        raise ValueError("Pinned local coding image digest required")
    environment = dict(os.environ)
    environment.update(SPROUT_DEEPSEEK_LIVE_CODING="1", SPROUT_LIVE_REQUEST_LIMIT=str(limit),
        SPROUT_LIVE_CONFIG=str(config.resolve()), SPROUT_TEMPORAL_TEST_HOST="127.0.0.1:17233",
        SPROUT_CODING_TEST_IMAGE=image)
    environment["SPROUT_ACCEPTANCE_SAMPLE"] = sample
    return subprocess.run([sys.executable, "-m", "pytest",
        "src/Sprout/tests/test_deepseek_live_coding.py", "-q", "-p", "no:cacheprovider",
        "--basetemp", str(output)], env=environment, check=False).returncode


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="Local config checks only; no external requests")
    check.add_argument("--config", type=Path, required=True)
    execute = commands.add_parser("run", help="Explicit paid trial; stop at code review")
    execute.add_argument("--config", type=Path, required=True)
    execute.add_argument("--output-dir", type=Path, required=True)
    execute.add_argument("--authorize-requests", type=request_limit, required=True)
    execute.add_argument("--coding-image", required=True)
    execute.add_argument("--sample", choices=("addition", "cart"), default="addition")
    inspect = commands.add_parser("status", help="Read current database, not report booleans")
    inspect.add_argument("--report", type=Path, required=True)
    commit = commands.add_parser("commit", help="Explicit reviewed trial approval and commit")
    commit.add_argument("--report", type=Path, required=True)
    commit.add_argument("--config", type=Path, required=True)
    commit.add_argument("--output", type=Path, required=True)
    commit.add_argument("--approve-reviewed", action="store_true", required=True)
    args = parser.parse_args(argv)
    if args.command == "check":
        load_env_file()
        settings = load_settings(args.config).model
        print(json.dumps({"compatible_provider": settings.provider == "openai_compatible",
            "expected_model": settings.model == "deepseek-flash",
            "official_endpoint": settings.base_url.rstrip("/") == "https://api.deepseek.com",
            "thinking_disabled": settings.thinking_mode == "disabled",
            "dedicated_key_present": bool(os.environ.get("DEEPSEEK_API_KEY", "").strip()),
            "dedicated_key_configured": settings.api_key_env == "DEEPSEEK_API_KEY",
            "model_requests": 0, "infrastructure_checked": False}))
        return 0
    if args.command == "run":
        return run(args.config, args.output_dir, args.authorize_requests, args.coding_image,
                   args.sample)
    if args.command == "status":
        print(json.dumps(status(args.report)))
        return 0
    from Sprout.tests.live_coding_commit import complete

    if args.output.exists() or not args.output.parent.is_dir():
        raise ValueError("Fresh output in an existing directory required")
    asyncio.run(complete(args.report, args.config, args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
