from Sprout.execution.coding_feedback import OUTPUT_LIMIT, command_feedback
from Sprout.execution.models import ProcessResult


def test_output_is_bounded_and_keeps_failure_tail():
    feedback = command_feedback(ProcessResult(("test",), stdout="a" * 20000,
        stderr="AssertionError: last diagnostic", exit_code=1))
    assert len(feedback["output"]) <= OUTPUT_LIMIT
    assert feedback["output_truncated"]
    assert feedback["output"].endswith("last diagnostic")
    assert feedback["validation_committed"] is False


def test_credential_url_is_redacted_before_display():
    feedback = command_feedback(ProcessResult(("test",), stderr="https://user:password@example.com"))
    assert "user:password@" not in feedback["output"]
