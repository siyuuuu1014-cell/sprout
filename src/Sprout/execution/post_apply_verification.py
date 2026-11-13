"""Run frozen checks on a fresh, hash-verified export of the applied project."""

from Sprout.execution.coding_container import CodingCommand, coding_node_directory_name
from Sprout.execution.isolated_acceptance import verify_isolated_cases
from Sprout.execution.isolated_auxiliary import run_auxiliary_checks
from Sprout.security.local_diagnostics import report


async def verify_written_candidate(
    runtime, session, expected, capture, *, acceptance, regression, bundle_sha256,
):
    report(runtime, "Verification", "post-Apply pytest and frozen cases: started")
    directory = session.evidence / coding_node_directory_name(session.node.id)
    directory.mkdir()
    exported = directory / "coding-input"
    exported.mkdir()
    for path, digest in expected.items():
        snapshot = capture(path)
        if snapshot is None or snapshot.digest != digest:
            raise OSError(f"post_apply_test_input_changed:{path}")
        file = exported / path
        file.parent.mkdir(parents=True, exist_ok=True)
        with file.open("xb") as stream:
            stream.write(snapshot.data)

    context = CodingCommand(session.task.id, session.original, directory, exported,
                            session.image, ("python",), session.oracle_paths)
    pytest_result = await run_auxiliary_checks(
        session.metadata, session.runner, context=context, node=session.node,
        commands=(("test", ("python", "-m", "pytest", "-q")),),
    )
    cases = await verify_isolated_cases(
        session.metadata, session.runner, context=context, node=session.node,
        acceptance_spec=acceptance, regression_spec=regression, expected_manifest=expected,
    )
    verified = pytest_result.get("verified") is True and cases.get("verified") is True
    for path, digest in expected.items():
        snapshot = capture(path)
        if snapshot is None or snapshot.digest != digest:
            raise OSError(f"post_apply_test_input_changed:{path}")
    report(runtime, "Verification", f"post-Apply: {'passed' if verified else 'failed'}")
    return {"verified": verified, "input_manifest": expected, "bundle_sha256": bundle_sha256,
            "pytest": pytest_result, "cases": cases}
