"""Explicit two-request paid probe; never collected by pytest or auto-retried.

Run only after the human authorizes these two requests. Output contains no key,
headers, environment, raw error body or project code. No Runtime is started.
"""

import argparse
import asyncio
import json
import re
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlparse

from Sprout.config.loader import load_env_file, load_settings
from Sprout.llm.messages import LLMMessage
from Sprout.runtime.factory import _build_provider
from Sprout.security.secrets import get_secret
from Sprout.tools.spec import ToolSpec


class ProbeConfigurationError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


async def probe(config):
    settings = load_settings(config).model
    if settings.api_key_env != "DEEPSEEK_API_KEY":
        raise ProbeConfigurationError("api_key_env_is_not_variable_name",
            "api_key_env must be DEEPSEEK_API_KEY, not the secret value")
    endpoint = urlparse(settings.base_url)
    if (settings.provider != "openai_compatible" or settings.model != "deepseek-flash"
            or endpoint.scheme != "https" or endpoint.netloc != "api.deepseek.com"
            or endpoint.path not in {"", "/", "/v1", "/v1/"}
            or settings.thinking_mode != "disabled"
            or type(settings.max_output_tokens) is not int
            or not 1 <= settings.max_output_tokens <= 256):
        raise ValueError("Probe requires DeepSeek, disabled thinking and output limit <=256")
    load_env_file()  # Local process only; never print environment or credentials.
    if not get_secret("DEEPSEEK_API_KEY"):
        raise ProbeConfigurationError("deepseek_environment_key_missing",
            "Dedicated DeepSeek environment variable required; no credential fallback")
    # Direct provider: deliberately bypass ModelOrchestrator retry/fallback.
    model = _build_provider(settings.provider, settings.model, settings.base_url,
                             settings.api_key_env, settings)
    result = {"requests_attempted": 0, "output_limit_per_request": settings.max_output_tokens,
              "automatic_retry": False, "coding_task_started": False, "observations": []}
    try:
        result["requests_attempted"] += 1
        first = await model.chat([LLMMessage.user("Reply exactly OK. No explanation.")])
        result["observations"].append({"kind": "plain_reply", "passed": first.content == "OK",
                                       "usage": asdict(first.usage)})
        tool = ToolSpec(name="add", description="Add two integers. Use this tool for addition.",
            input_schema={"type": "object", "properties": {
                "a": {"type": "integer"}, "b": {"type": "integer"}},
                "required": ["a", "b"], "additionalProperties": False})
        result["requests_attempted"] += 1
        second = await model.chat([LLMMessage.user(
            "Use add with a=2 and b=3. Return a tool call, not text.")], tools=[tool])
        valid = (len(second.tool_calls) == 1 and second.tool_calls[0].name == "add"
                 and second.tool_calls[0].arguments == {"a": 2, "b": 3})
        result["observations"].append({"kind": "tool_call_generation", "passed": valid,
                                       "usage": asdict(second.usage)})
        result["passed"] = all(item["passed"] for item in result["observations"])
    except Exception as exc:
        # API errors can contain arbitrary bodies; never print the raw exception.
        status = re.search(r"HTTP (\d{3})", str(exc))
        result.update(passed=False, error_type=type(exc).__name__,
                      http_status=int(status[1]) if status else None)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    arguments = parser.parse_args()
    destination = Path(arguments.output)
    if destination.exists() or not destination.parent.is_dir():
        raise SystemExit("Fresh output path in an existing test directory required")
    try:
        output = asyncio.run(probe(arguments.config))
    except Exception as exc:
        output = {"passed": False, "error_type": type(exc).__name__, "automatic_retry": False,
                  "requests_attempted": 0}
        if isinstance(exc, ProbeConfigurationError):
            output["error_code"] = exc.code
    with destination.open("x", encoding="utf-8") as stream:
        json.dump(output, stream, indent=2)
    print(json.dumps(output))
    raise SystemExit(0 if output["passed"] else 1)
