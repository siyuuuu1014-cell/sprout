"""Separate per-function measurements using an oracle-free dedicated export."""

import hashlib
import json

from Sprout.evolution.replay_oracle import integrity
from Sprout.execution.acceptance import render_acceptance_demo, render_acceptance_tests
from Sprout.execution.function_verification import FunctionVerificationRun
from Sprout.execution.verification_export import export_input
from Sprout.execution.verification_run import input_manifest
from Sprout.strategy.acceptance import AcceptanceSpec


async def measure_functions(runtime, task_id, *, directory, repo, original, image,
                            oracle_paths, frozen, acceptance_spec, classify_overrides=None,
                            regression_fingerprint=None, purpose="acceptance"):
    if acceptance_spec is None and purpose == "acceptance":
        return []
    try:
        from Sprout.evolution.regression_spec import frozen as frozen_regression

        regression = (frozen_regression(directory, regression_fingerprint)
                      if regression_fingerprint else None)
        if purpose == "regression":
            if regression is None:
                raise ValueError("Frozen regression oracle missing")
            acceptance_spec = regression
        spec = AcceptanceSpec.from_payload(acceptance_spec)
        if not integrity(repo, oracle_paths, frozen):
            raise ValueError("Frozen oracle changed")
        expected_hash = hashlib.sha256(render_acceptance_tests(spec).encode()).hexdigest()
        if purpose == "acceptance" and expected_hash not in frozen.values():
            raise ValueError("Acceptance spec is not bound to frozen oracle")
        # Generated demos also embed expected values. Remove all byte-identical copies
        # of generated tests/demos, not just their conventional file names.
        oracle_hashes = {expected_hash}
        if regression:
            regression_spec = AcceptanceSpec.from_payload(regression)
            oracle_hashes.add(hashlib.sha256(render_acceptance_tests(regression_spec).encode()).hexdigest())
            try:
                oracle_hashes.add(hashlib.sha256(
                    render_acceptance_demo(regression_spec).encode()).hexdigest())
            except ValueError:
                pass
        try:
            oracle_hashes.add(hashlib.sha256(render_acceptance_demo(spec).encode()).hexdigest())
        except ValueError:
            pass
        exclusions = set(oracle_paths)
        for path in repo.rglob("*"):
            if path.is_symlink():
                raise ValueError("Linked repository input")
            if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() in oracle_hashes:
                exclusions.add(path.relative_to(repo).as_posix())
        exported = directory / ("function-input" if purpose == "acceptance" else "regression-input")
        seal = directory / ("function-input-manifest.json" if purpose == "acceptance"
                            else "regression-input-manifest.json")
        if not exported.exists():
            manifest = export_input(repo, exported, original,
                                    classify_overrides=classify_overrides,
                                    exclude_paths=tuple(sorted(exclusions)))
            with seal.open("x", encoding="utf-8") as stream:
                json.dump(manifest, stream, sort_keys=True)
        if (not seal.is_file() or seal.is_symlink()
                or input_manifest(exported) != json.loads(seal.read_text(encoding="utf-8"))):
            raise ValueError("Function export incomplete or changed")
        current_manifest = export_input(
            repo, exported, original, classify_overrides=classify_overrides,
            exclude_paths=tuple(sorted(exclusions)), manifest_only=True)
        if current_manifest != input_manifest(exported):
            raise ValueError("Function source changed after export")
        runner = FunctionVerificationRun(runtime.storage.metadata)
        records = []
        for case in spec.cases:
            output = await runner.execute(
                runtime, task_id=task_id, original=original, exported=exported,
                image=image, case=case, oracle_paths=tuple(sorted(exclusions)), purpose=purpose)
            records.append(output)
            if output.get("waiting") or not output.get("input_unchanged"):
                break
        return records
    except (ValueError, TypeError, OSError):
        return [{"waiting": True, "reason": "function_input_unavailable_or_unbound",
                 "validation_committed": False}]
