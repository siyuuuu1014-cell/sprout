"""Workspace registration and pure captured-content analysis.

Legacy disk APIs retain explicit refusals. Host analysis and read planning use
Runtime's owned-node/Broker/View admission, never a workspace-wide snapshot.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

from Sprout.storage.bundle import StorageBundle
from Sprout.storage.contracts.metadata import MetadataStore
from Sprout.workspace.cache import WorkspaceAnalysisCache
from Sprout.workspace.intelligence import WorkspaceAnalysis, WorkspaceIntelligence
from Sprout.workspace.models import (
    ReadPlan,
    Workspace,
    WorkspaceKind,
    WorkspaceManifest,
)
from Sprout.workspace.scanner import WorkspaceScanner


class WorkspaceService:
    """Registers workspaces and builds the read plans that bound a task."""

    def __init__(
        self,
        storage: StorageBundle,
        scanner: WorkspaceScanner | None = None,
        skills_dir: str | None = None,
    ) -> None:
        self._storage = storage
        self._scanner = scanner or WorkspaceScanner()
        self._intelligence = WorkspaceIntelligence(
            scanner=self._scanner,
            skills_dir=skills_dir,
        )
        self._analysis_locks: dict[str, asyncio.Lock] = {}
        self._analysis_cache = WorkspaceAnalysisCache(
            cache_store=storage.cache,
        )

    @property
    def _metadata(self) -> MetadataStore:
        metadata = self._storage.metadata
        if metadata is None:
            raise RuntimeError("MetadataStore is not configured")
        return metadata

    async def open(
        self,
        root: str | Path,
        *,
        workspace_id: str | None = None,
    ) -> Workspace:
        """Register a local or Git workspace in the metadata store."""
        path = Path(root).resolve()
        if not path.exists():
            raise FileNotFoundError(f"Workspace path does not exist: {path}")
        if workspace_id is None:
            for existing in await self.list():
                if existing.root.resolve() == path:
                    return existing
        workspace = Workspace(
            id=workspace_id or str(uuid4()),
            root=path,
            kind=(
                WorkspaceKind.GIT_REPOSITORY
                if (path / ".git").exists()
                else WorkspaceKind.LOCAL_DIRECTORY
            ),
        )
        await self._metadata.save_workspace(workspace)
        return workspace

    async def get(self, workspace_id: str) -> Workspace | None:
        return await self._metadata.get_workspace(workspace_id)

    async def list(self) -> list[Workspace]:
        return await self._metadata.list_workspaces()

    async def scan(self, workspace_id: str) -> WorkspaceManifest:
        """A workspace identifier alone does not authorize directory visibility."""
        raise PermissionError("authorized_workspace_analysis_required")

    async def read_plan(self, task_id: str) -> ReadPlan:
        """Task IDs alone are not caller credentials; use Runtime.plan_task."""
        raise PermissionError("authorized_workspace_analysis_required")

    async def analyze(
        self,
        workspace_id: str,
        *,
        task_id: str | None = None,
        instruction: str = "understand project",
    ) -> WorkspaceAnalysis:
        raise PermissionError("authorized_workspace_analysis_required")

    async def analyze_view(self, workspace, task, view):
        """Pure captured-content service, not a new source of file authority."""
        view.check(workspace.id)
        return await self._intelligence.analyze_view(workspace, task, view)

    async def analyze_path(
        self, root: Path, instruction: str = "analyze project",
    ) -> WorkspaceAnalysis:
        raise PermissionError("authorized_workspace_analysis_required")

    async def invalidate_workspace(self, workspace_id: str | None = None) -> None:
        """Drop cached workspace analyses for one workspace or all workspaces."""
        self._analysis_cache.invalidate(workspace_id)
        await self._analysis_cache.forget(workspace_id)
