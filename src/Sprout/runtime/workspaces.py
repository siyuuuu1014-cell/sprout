"""Workspace registration, scanning, and read planning.

The service owns workspace persistence so the Runtime stays a thin coordinator
(Herness spec 12): registering a root, scanning a manifest, and building the
bounded read plan a task is allowed to touch.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from Sprout.storage.bundle import StorageBundle
from Sprout.storage.contracts.metadata import MetadataStore
from Sprout.task.models import Task
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
        """Scan a workspace and persist its manifest."""
        workspace = await self.get(workspace_id)
        if workspace is None:
            raise LookupError(f"Workspace not found: {workspace_id}")
        manifest = self._scanner.scan(workspace)
        await self._metadata.save_workspace(
            Workspace(
                id=workspace.id,
                root=workspace.root,
                kind=workspace.kind,
                revision=workspace.revision,
                manifest=manifest,
            )
        )
        return manifest

    async def read_plan(self, task_id: str) -> ReadPlan:
        """Build the read plan for a task, scanning its workspace first."""
        task = await self._metadata.get_task(task_id)
        if task is None:
            raise LookupError(f"Task not found: {task_id}")
        workspace = await self.get(task.workspace_id)
        if workspace is None:
            raise LookupError(f"Workspace not found: {task.workspace_id}")
        manifest = await self.scan(workspace.id)
        return self._scanner.build_read_plan(
            Workspace(
                id=workspace.id,
                root=workspace.root,
                kind=workspace.kind,
                revision=workspace.revision,
                manifest=manifest,
            ),
            task,
        )

    async def analyze(
        self,
        workspace_id: str,
        *,
        task_id: str | None = None,
        instruction: str = "understand project",
    ) -> WorkspaceAnalysis:
        lock = self._analysis_locks.setdefault(workspace_id, asyncio.Lock())
        async with lock:
            return await self._analyze(workspace_id, task_id=task_id, instruction=instruction)

    async def _analyze(
        self, workspace_id: str, *, task_id: str | None = None,
        instruction: str = "understand project",
    ) -> WorkspaceAnalysis:
        """Build manifest, read plan, graph, and project knowledge together."""
        workspace = await self.get(workspace_id)
        if workspace is None:
            raise LookupError(f"Workspace not found: {workspace_id}")

        task = None
        if task_id is not None:
            task = await self._metadata.get_task(task_id)
            if task is None:
                raise LookupError(f"Task not found: {task_id}")
        task = task or Task(
            workspace_id=workspace.id,
            instruction=instruction,
            source="workspace",
        )

        if self._storage.projects is None:
            raise RuntimeError("ProjectAnalysisStore is not configured")
        # Only the durable project authority selects a cache version.
        expected_snapshot_id = await self._storage.projects.current_snapshot_id(workspace.id)
        previous = await self._analysis_cache.get(
            workspace.id, expected_snapshot_id=expected_snapshot_id,
        )
        if previous is None:
            previous = await self._storage.projects.load_analysis(workspace.id)
        analysis = await self._intelligence.analyze(
            replace(workspace, manifest=self._scanner.scan(workspace)),
            task,
            previous=previous,
        )
        await self._storage.projects.save_analysis(
            analysis, expected_snapshot_id=expected_snapshot_id,
        )
        self._analysis_cache.put(workspace.id, analysis)
        try:
            await self._analysis_cache.persist(workspace.id, analysis)
        except Exception:  # noqa: BLE001 - a cache cannot reject an authority write
            logging.getLogger(__name__).warning("Project cache mirror failed", exc_info=True)
        return analysis

    async def analyze_view(self, workspace, task, view):
        """Pure captured-content service, not a new source of file authority."""
        view.check(workspace.id)
        return await self._intelligence.analyze_view(workspace, task, view)

    async def analyze_path(
        self, root: Path, instruction: str = "analyze project",
    ) -> WorkspaceAnalysis:
        workspace = await self.open(root)
        return await self.analyze(workspace.id, instruction=instruction)

    async def invalidate_workspace(self, workspace_id: str | None = None) -> None:
        """Drop cached workspace analyses for one workspace or all workspaces."""
        self._analysis_cache.invalidate(workspace_id)
        await self._analysis_cache.forget(workspace_id)
