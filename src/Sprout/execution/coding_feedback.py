"""Bounded model-visible diagnostics, not execution or verification authority."""

from Sprout.security.redact import Redactor

OUTPUT_LIMIT = 8192


def command_feedback(process):
    raw = "stdout:\n" + process.stdout + "\nstderr:\n" + process.stderr
    if process.error:
        raw += "\nrunner error:\n" + process.error
    text = Redactor().scrub(raw)
    truncated = len(text) > OUTPUT_LIMIT
    if truncated:
        lines = text.splitlines()
        summary = [line for line in lines if line.startswith(("FAILED ", "ERROR "))]
        details = [line for line in lines if line.startswith(("E ", "> "))
                   or "ERROR collecting" in line or "Traceback" in line]
        if summary or details:
            summary_text = "\n".join(dict.fromkeys(summary))
            details_text = "\n".join(dict.fromkeys(details))
            limit = OUTPUT_LIMIT - 1000
            important = summary_text[:limit]
            details_budget = limit - len(important)
            text = ("[output truncated; selected failure diagnostics]\n" + important
                    + "\n" + details_text[:details_budget] + "\n[output tail]\n" + text[-880:])
        else:
            half = (OUTPUT_LIMIT - 40) // 2
            text = text[:half] + "\n[output truncated; head and tail]\n" + text[-half:]
    return {"exit_code": process.exit_code, "started": process.started,
            "duration_ms": process.duration_ms, "output": text,
            "output_truncated": truncated, "validation_committed": False}


def feedback_text(feedback):
    return (f"exit_code={feedback['exit_code']}; duration_ms={feedback['duration_ms']}; "
            f"output_truncated={feedback['output_truncated']}\n" + feedback["output"])
