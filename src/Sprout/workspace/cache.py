"""Version-addressed project analysis cache; the authority chooses the version.

There is deliberately no mutable latest-version pointer in the derived lane.
Delayed writers cannot replace another version. Foreign process mirrors expire
via TTL; local invalidation removes only the versions this instance knows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from Sprout.workspace.invalidation import snapshot_id
from Sprout.workspace.serialization import from_jsonable, to_jsonable

if TYPE_CHECKING:
    from Sprout.storage.contracts.cache import CacheStore
    from Sprout.workspace.intelligence import WorkspaceAnalysis


@dataclass(slots=True)
class WorkspaceAnalysisCache:
    cache_store: CacheStore | None = None
    ttl_seconds: int = 3600
    _entries: dict[tuple[str, str], WorkspaceAnalysis] = field(default_factory=dict)
    _known_versions: dict[str, set[str]] = field(default_factory=dict)

    async def get(
        self, workspace_id: str, *, expected_snapshot_id: str | None,
    ) -> WorkspaceAnalysis | None:
        """Read only the version selected by ProjectAnalysisStore; failures miss."""
        if expected_snapshot_id is None:
            return None
        local = self._entries.get((workspace_id, expected_snapshot_id))
        if local is not None:
            return local if self._matches(local, workspace_id, expected_snapshot_id) else None
        if self.cache_store is None:
            return None
        try:
            from Sprout.workspace.intelligence import WorkspaceAnalysis

            raw = await self.cache_store.get(self._key(workspace_id, expected_snapshot_id))
            if not isinstance(raw, dict) or raw.get("snapshot_id") != expected_snapshot_id:
                return None
            analysis = from_jsonable(raw["analysis"], WorkspaceAnalysis)
            if not self._matches(analysis, workspace_id, expected_snapshot_id):
                return None
            self.put(workspace_id, analysis)
            return analysis
        except Exception:  # noqa: BLE001 - a stale/unreadable snapshot is a miss
            return None

    @staticmethod
    def _matches(analysis: WorkspaceAnalysis, workspace_id: str, version: str) -> bool:
        return analysis.workspace.id == workspace_id and snapshot_id(analysis) == version

    def put(self, workspace_id: str, analysis: WorkspaceAnalysis) -> None:
        if analysis.workspace.id != workspace_id:
            raise ValueError("Project cache workspace identity mismatch")
        version = snapshot_id(analysis)
        self._entries[(workspace_id, version)] = analysis
        self._known_versions.setdefault(workspace_id, set()).add(version)

    def _key(self, workspace_id: str, version: str) -> str:
        return f"project:{workspace_id}:analysis:{version}"

    async def persist(
        self,
        workspace_id: str,
        analysis: WorkspaceAnalysis,
    ) -> None:
        """Mirror the full analysis snapshot into the hot lane."""
        if self.cache_store is None:
            return
        if analysis.workspace.id != workspace_id:
            raise ValueError("Project cache workspace identity mismatch")
        version = snapshot_id(analysis)
        await self.cache_store.set(
            self._key(workspace_id, version),
            {"snapshot_id": version, "analysis": to_jsonable(analysis)},
            ttl_seconds=self.ttl_seconds,
        )
        self._known_versions.setdefault(workspace_id, set()).add(version)

    async def forget(self, workspace_id: str | None = None) -> None:
        """Drop the hot-lane mirror for one workspace or all workspaces."""
        self.invalidate(workspace_id)
        targets = (list(self._known_versions) if workspace_id is None else [workspace_id])
        for target in targets:
            versions = self._known_versions.get(target, set())
            if self.cache_store is not None:
                for version in tuple(versions):
                    await self.cache_store.delete(self._key(target, version))
            self._known_versions.pop(target, None)

    def invalidate(self, workspace_id: str | None = None) -> None:
        if workspace_id is None:
            self._entries.clear()
            return
        for key in tuple(self._entries):
            if key[0] == workspace_id:
                self._entries.pop(key, None)


__all__ = ["WorkspaceAnalysisCache"]
