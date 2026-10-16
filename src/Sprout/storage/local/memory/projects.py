"""Isolated project snapshot authority for ephemeral runtimes and tests."""

from Sprout.storage.contracts.projects import ProjectSnapshotConflict
from Sprout.workspace.invalidation import snapshot_id
from Sprout.workspace.serialization import from_jsonable, to_jsonable


class MemoryProjectAnalysisStore:
    def __init__(self) -> None:
        self._snapshots = {}
        self._versions = {}

    async def current_snapshot_id(self, workspace_id: str) -> str | None:
        return self._versions.get(workspace_id)

    async def save_analysis(self, analysis, *, expected_snapshot_id: str | None) -> str:
        version = snapshot_id(analysis)
        current = self._versions.get(analysis.workspace.id)
        if current == version:
            return version
        if current != expected_snapshot_id:
            raise ProjectSnapshotConflict("Project snapshot changed; retry project analysis")
        self._snapshots[analysis.workspace.id] = to_jsonable(analysis)
        self._versions[analysis.workspace.id] = version
        return version

    async def load_analysis(self, workspace_id):
        from Sprout.workspace.intelligence import WorkspaceAnalysis

        payload = self._snapshots.get(workspace_id)
        return from_jsonable(payload, WorkspaceAnalysis) if payload is not None else None

    async def status(self) -> dict[str, object]:
        return {"backend": type(self).__name__, "projects": len(self._snapshots)}

    def close(self) -> None:
        self._snapshots.clear()
        self._versions.clear()
