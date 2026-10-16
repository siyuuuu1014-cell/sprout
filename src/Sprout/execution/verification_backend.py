"""Independent verification boundary; never selects a host fallback."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from Sprout.execution.models import ProcessResult


@dataclass(frozen=True, slots=True)
class VerificationRequest:
    task_id: str
    source_root: Path
    input_root: Path
    observer: Path
    image: str
    arguments: tuple[str, ...]


class VerificationBackend(Protocol):
    def plan(self, request: VerificationRequest) -> tuple[str, ...]: ...


class AuthorizedProcessRunner(Protocol):
    async def run_task_process(
        self, task_id: str, command: tuple[str, ...], *, timeout_seconds: float | None = None,
        max_output_bytes: int | None = None
    ) -> ProcessResult: ...
