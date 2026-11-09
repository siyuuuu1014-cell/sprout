"""Read-only query helpers over a WorkspaceAnalysis."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from Sprout.workspace.intelligence import WorkspaceAnalysis


@dataclass(frozen=True, slots=True)
class WorkspaceQueryResult:
    nodes: tuple[object, ...] = ()
    edges: tuple[object, ...] = ()
    knowledge: tuple[object, ...] = ()


class WorkspaceQuery:
    """Query a workspace analysis without mutating any project data.

    Graph nodes carry the *absolute* resource path, because
    ``Runtime.open_workspace`` resolves the workspace root before the scanner
    walks it. A caller at a shell types a *workspace-relative* path instead, so
    every path-shaped lookup accepts both spellings rather than silently
    returning nothing for a file that plainly exists.
    """

    def __init__(self, analysis: WorkspaceAnalysis, *, captured: bool = False) -> None:
        self._analysis = analysis
        # Pure matching mode only, NOT an assertion of file authorization.
        self._captured = captured

    def _resource_key(self, path: str) -> str:
        """Canonical comparison key for a resource path.

        A relative path is resolved against the workspace root; both sides are
        then normalised for the host platform, so a Windows caller does not
        have to match the on-disk letter case by hand.
        """
        if self._captured:
            from Sprout.task.paths import bound_scope_path

            # Reuse lexical normalization, never resolve/stat a host path.
            key = bound_scope_path(path, str(self._analysis.workspace.root))
            return os.path.normcase(key) if key is not None else "\0"
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self._analysis.workspace.root / candidate
        try:
            resolved = candidate.resolve(strict=False)
        except OSError:  # pragma: no cover - pathological paths only
            resolved = candidate
        return os.path.normcase(resolved.as_posix())

    def _identity_ids(self, name: str) -> set[str]:
        """Node ids addressed by ``name``: id, name, qualified name, or path."""
        if not name:
            return set()
        key = self._resource_key(name)
        return {
            node.id
            for node in self._analysis.graph.nodes
            if name in {node.id, node.name, node.qualified_name}
            or (
                node.resource is not None
                and self._resource_key(node.resource.path) == key
            )
        }

    def neighbors(
        self,
        name: str,
        *,
        relation: str | None = None,
    ) -> WorkspaceQueryResult:
        graph = self._analysis.graph
        ids = self._identity_ids(name)
        edges = [
            edge
            for edge in graph.edges
            if edge.source in ids or edge.target in ids
        ]
        if relation:
            edges = [edge for edge in edges if edge.relation == relation]
        neighbor_ids = {
            edge.target if edge.source in ids else edge.source
            for edge in edges
        }
        nodes = [node for node in graph.nodes if node.id in neighbor_ids]
        return WorkspaceQueryResult(nodes=tuple(nodes), edges=tuple(edges))

    def symbols(
        self,
        *,
        kind: str | None = None,
        query: str = "",
    ) -> tuple[object, ...]:
        nodes = [
            node
            for node in self._analysis.graph.nodes
            if node.granularity == "symbol"
            and node.kind in {"class", "function", "method", "test"}
        ]
        if kind:
            nodes = [node for node in nodes if node.kind == kind]
        if query:
            lowered = query.casefold()
            nodes = [
                node
                for node in nodes
                if lowered in node.name.casefold()
                or lowered in node.qualified_name.casefold()
            ]
        return tuple(nodes)

    def indexed_counts(self) -> dict[str, int]:
        """Count symbols from the same index used by the symbols query."""
        symbols = self.symbols()
        test_methods = [
            node for node in symbols
            if node.kind == "method" and node.qualified_name.rpartition(".")[2].startswith("test_")
        ]
        test_classes = {
            (node.resource.path, node.qualified_name.rpartition(".")[0])
            for node in test_methods if node.resource is not None
        }
        return {
            "files": len({node.resource.path for node in self._analysis.graph.nodes
                          if node.resource is not None}),
            "functions": sum(node.kind in {"function", "test"} for node in symbols),
            "test_classes": len(test_classes),
            "test_methods": len(test_methods),
            "test_functions": sum(node.kind == "test" for node in symbols),
        }

    def indexed_report(self) -> str:
        """Render automatic discovery from the captured index."""
        counts = self.indexed_counts()
        manifest = self._analysis.manifest
        files: dict[str, list[str]] = {
            node.resource.path: [] for node in self._analysis.graph.nodes
            if node.resource is not None
        }
        for node in self.symbols():
            if node.resource is not None and node.kind in {"class", "function"}:
                files[node.resource.path].append(node.qualified_name)
        lines = [
            "项目结构 / Project structure",
            "",
            "Languages: " + (", ".join(manifest.detected_languages) or "unknown"),
            "Framework hints: " + (", ".join(manifest.framework_hints) or "none detected"),
            "",
            "索引统计 / Indexed counts (partial project coverage, authorized files only)",
            f"- Files: {counts['files']}",
            f"- Top-level functions (including test functions): {counts['functions']}",
            f"- Classes with indexed test methods: {counts['test_classes']}",
            f"- Test methods: {counts['test_methods']}",
            f"- Top-level test functions: {counts['test_functions']}",
            "",
            "已索引的文件 / Indexed files",
        ]
        for path, symbols in sorted(files.items())[:20]:
            names = ", ".join(symbols[:10])
            lines.append(f"- {path}" + (f" — {names}" if names else ""))
        if len(files) > 20:
            lines.append("- … file list limited to the first 20 paths")
        return "\n".join(lines)

    def dependencies(
        self,
        path: str,
        *,
        direction: str = "out",
    ) -> tuple[object, ...]:
        """Edges touching the resource at ``path``.

        ``path`` accepts the absolute resource path carried by the graph nodes
        as well as a path relative to the workspace root.
        """
        graph = self._analysis.graph
        key = self._resource_key(path)
        ids = {
            node.id
            for node in graph.nodes
            if node.resource is not None
            and self._resource_key(node.resource.path) == key
        }
        edges = []
        for edge in graph.edges:
            if direction == "out" and edge.source in ids:
                edges.append(edge)
            elif direction == "in" and edge.target in ids:
                edges.append(edge)
            elif direction == "both" and (edge.source in ids or edge.target in ids):
                edges.append(edge)
        return tuple(edges)

    def subgraph(
        self,
        name: str,
        *,
        max_depth: int = 2,
        relation: str | None = None,
    ) -> WorkspaceQueryResult:
        """Return nodes and edges within ``max_depth`` hops of a matching node."""
        graph = self._analysis.graph
        matched_ids = self._identity_ids(name)
        if not matched_ids:
            return WorkspaceQueryResult()

        node_ids = set(matched_ids)
        edge_set = set()
        frontier = set(matched_ids)

        for _ in range(max(1, max_depth)):
            next_frontier: set[str] = set()
            for edge in graph.edges:
                if relation and edge.relation != relation:
                    continue
                if edge.source in frontier or edge.target in frontier:
                    edge_set.add(edge)
                    if edge.source not in node_ids:
                        next_frontier.add(edge.source)
                    if edge.target not in node_ids:
                        next_frontier.add(edge.target)
            node_ids.update(next_frontier)
            frontier = next_frontier
            if not frontier:
                break

        return WorkspaceQueryResult(
            nodes=tuple(node for node in graph.nodes if node.id in node_ids),
            edges=tuple(sorted(edge_set, key=lambda edge: edge.id)),
        )

    def knowledge(
        self,
        query: str = "",
        *,
        kind: str | None = None,
    ) -> tuple[object, ...]:
        items = self._analysis.knowledge.items
        if kind:
            items = tuple(item for item in items if item.kind == kind)
        if query:
            lowered = query.casefold()
            items = tuple(
                item
                for item in items
                if lowered in item.statement.casefold()
            )
        return tuple(items)


__all__ = ["WorkspaceQuery", "WorkspaceQueryResult"]
