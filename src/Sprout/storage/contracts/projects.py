"""User project analysis authority, separate from Sprout runtime state."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from Sprout.workspace.intelligence import WorkspaceAnalysis


class ProjectSnapshotConflict(RuntimeError):
    """The analyzed baseline changed before publication; retry analysis."""


class ProjectAnalysisStore(Protocol):
    async def current_snapshot_id(self, workspace_id: str) -> str | None: ...
    async def save_analysis(
        self, analysis: WorkspaceAnalysis, *, expected_snapshot_id: str | None,
    ) -> str: ...
    async def load_analysis(self, workspace_id: str) -> WorkspaceAnalysis | None: ...
    async def status(self) -> dict[str, object]: ...
    def close(self) -> None: ...
