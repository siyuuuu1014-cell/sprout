"""Versioned project facts in five project-owned SQLite authorities.

Snapshots are written before publishing the manifest's active snapshot pointer.
A failed partition write leaves the previous complete snapshot readable. Readers
always use the published version; removed files and facts remain only in history.
"""

from __future__ import annotations

import json
import re
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING

from Sprout.storage.contracts.projects import ProjectSnapshotConflict
from Sprout.storage.local.sqlite.driver import SqliteDatabase, SqlitePragmas
from Sprout.workspace.invalidation import ANALYZER_VERSION, snapshot_id
from Sprout.workspace.serialization import from_jsonable, to_jsonable

if TYPE_CHECKING:
    from Sprout.workspace.intelligence import WorkspaceAnalysis


SCHEMAS = {
    "manifest": """
        CREATE TABLE IF NOT EXISTS Project (
            id TEXT PRIMARY KEY, root_path TEXT NOT NULL, active_snapshot_id TEXT
        );
        CREATE TABLE IF NOT EXISTS AnalysisManifest (
            snapshot_id TEXT PRIMARY KEY, manifest_json TEXT NOT NULL
        );
    """,
    "structure": """
        CREATE TABLE IF NOT EXISTS WorktreeSnapshot (
            id TEXT PRIMARY KEY, analyzer_version TEXT NOT NULL,
            snapshot_json TEXT NOT NULL, scanned_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS StructureNode (
            snapshot_id TEXT NOT NULL, id TEXT NOT NULL, kind TEXT NOT NULL,
            path TEXT, qualified_name TEXT NOT NULL, line INTEGER NOT NULL,
            node_json TEXT NOT NULL, PRIMARY KEY(snapshot_id, id)
        );
        CREATE TABLE IF NOT EXISTS CodeRelation (
            snapshot_id TEXT NOT NULL, id TEXT NOT NULL, source_id TEXT NOT NULL,
            target_id TEXT NOT NULL, relation_type TEXT NOT NULL,
            confidence REAL NOT NULL, evidence_json TEXT NOT NULL,
            PRIMARY KEY(snapshot_id, id)
        );
    """,
    "architecture": """
        CREATE TABLE IF NOT EXISTS ArchitectureFact (
            snapshot_id TEXT NOT NULL, id TEXT NOT NULL, ordinal INTEGER NOT NULL,
            kind TEXT NOT NULL, statement TEXT NOT NULL, confidence REAL NOT NULL,
            evidence_json TEXT NOT NULL, fact_json TEXT NOT NULL,
            PRIMARY KEY(snapshot_id, id)
        );
    """,
    "database": """
        CREATE TABLE IF NOT EXISTS DatabaseFact (
            snapshot_id TEXT NOT NULL, id TEXT NOT NULL, ordinal INTEGER NOT NULL,
            kind TEXT NOT NULL, statement TEXT NOT NULL, confidence REAL NOT NULL,
            evidence_json TEXT NOT NULL, fact_json TEXT NOT NULL,
            PRIMARY KEY(snapshot_id, id)
        );
    """,
    "evolution": """
        CREATE TABLE IF NOT EXISTS ProjectVersion (
            id TEXT PRIMARY KEY, revision TEXT, changed_resources_json TEXT NOT NULL,
            stale_knowledge_json TEXT NOT NULL, created_at TEXT NOT NULL
        );
    """,
}


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class SqliteProjectAnalysisStore:
    def __init__(self, root: str | Path, pragmas: SqlitePragmas | None = None) -> None:
        self.root = Path(root)
        self._pragmas = pragmas
        self._projects: dict[str, dict[str, SqliteDatabase]] = {}

    def _databases(self, workspace_id: str) -> dict[str, SqliteDatabase]:
        if workspace_id in self._projects:
            return self._projects[workspace_id]
        # Caller-provided workspace identifiers must not become filesystem paths.
        reserved = {"CON", "PRN", "AUX", "NUL"}
        reserved.update(f"{prefix}{number}" for prefix in ("COM", "LPT") for number in range(10))
        safe = re.fullmatch(r"[A-Za-z0-9_-]{1,128}", workspace_id)
        directory = workspace_id if safe and workspace_id.upper() not in reserved else (
            sha256(workspace_id.encode()).hexdigest()
        )
        databases = {}
        try:
            for partition, schema in SCHEMAS.items():
                db = SqliteDatabase.open(
                    self.root / directory / f"project_{partition}.db", self._pragmas,
                )
                databases[partition] = db
                db.executescript(schema)
        except Exception:
            for db in databases.values():
                db.close()
            raise
        self._projects[workspace_id] = databases
        return databases

    async def current_snapshot_id(self, workspace_id: str) -> str | None:
        database = self._databases(workspace_id)["manifest"]
        return await database.scalar(
            "SELECT active_snapshot_id FROM Project WHERE id=?", (workspace_id,),
        )

    async def save_analysis(
        self, analysis: WorkspaceAnalysis, *, expected_snapshot_id: str | None,
    ) -> str:
        databases = self._databases(analysis.workspace.id)
        version = snapshot_id(analysis)
        active = await databases["manifest"].fetchone(
            "SELECT active_snapshot_id FROM Project WHERE id=?", (analysis.workspace.id,),
        )
        if active is not None and active[0] == version:
            return version
        if (active[0] if active is not None else None) != expected_snapshot_id:
            raise ProjectSnapshotConflict("Project snapshot changed; retry project analysis")
        payload = to_jsonable(analysis)
        payload["project_fact_ids"] = [fact.id for fact in analysis.knowledge.items]
        fields = payload["fields"]
        manifest = fields.pop("manifest")
        fields["workspace"]["fields"]["manifest"] = None
        fields.pop("knowledge")
        # The complete structural snapshot retains every resource, edge,
        # evidence reference, read-plan field and invalidation marker.
        await databases["structure"].execute_batch([
            ("INSERT OR REPLACE INTO WorktreeSnapshot VALUES (?, ?, ?, ?)", (
                version, ANALYZER_VERSION, _json(payload),
                analysis.fingerprint.created_at.isoformat(),
            )),
            *[("INSERT OR REPLACE INTO StructureNode VALUES (?, ?, ?, ?, ?, ?, ?)", (
                version, node.id, node.kind,
                node.resource.path if node.resource else None,
                node.qualified_name, node.line, _json(to_jsonable(node)),
            )) for node in analysis.graph.nodes],
            *[("INSERT OR REPLACE INTO CodeRelation VALUES (?, ?, ?, ?, ?, ?, ?)", (
                version, edge.id, edge.source, edge.target, edge.relation, edge.confidence,
                _json({"evidence": edge.evidence, "refs": to_jsonable(edge.evidence_refs)}),
            )) for edge in analysis.graph.edges],
        ])
        partitions: dict[str, list] = {"architecture": [], "database": []}
        for index, fact in enumerate(analysis.knowledge.items):
            partition = "database" if "database" in fact.kind or fact.kind == "migration" else (
                "architecture"
            )
            table = "DatabaseFact" if partition == "database" else "ArchitectureFact"
            partitions[partition].append((f"INSERT OR REPLACE INTO {table} VALUES "
                                          "(?, ?, ?, ?, ?, ?, ?, ?)", (
                version, fact.id, index, fact.kind, fact.statement, fact.confidence,
                _json(fact.evidence_ids), _json(to_jsonable(fact)),
            )))
        for partition, statements in partitions.items():
            await databases[partition].execute_batch(statements)
        await databases["evolution"].execute(
            "INSERT OR REPLACE INTO ProjectVersion VALUES (?, ?, ?, ?, ?)", (
                version, analysis.workspace.revision, _json(analysis.changed_resources),
                _json(analysis.stale_knowledge_ids), analysis.fingerprint.created_at.isoformat(),
            ),
        )
        # Publish only after every other authority partition succeeded.
        await databases["manifest"].execute(
            "INSERT OR REPLACE INTO AnalysisManifest VALUES (?, ?)", (version, _json(manifest)),
        )
        # One conditional write is the cross-process publication linearization point.
        if expected_snapshot_id is None:
            changed = await databases["manifest"].execute(
                "INSERT INTO Project VALUES (?, ?, ?) ON CONFLICT(id) DO NOTHING",
                (analysis.workspace.id, str(analysis.workspace.root), version),
            )
        else:
            changed = await databases["manifest"].execute(
                "UPDATE Project SET root_path=?, active_snapshot_id=? "
                "WHERE id=? AND active_snapshot_id=?",
                (str(analysis.workspace.root), version,
                 analysis.workspace.id, expected_snapshot_id),
            )
        if changed != 1:
            if await self.current_snapshot_id(analysis.workspace.id) != version:
                raise ProjectSnapshotConflict("Project snapshot changed; retry project analysis")
        return version

    async def load_analysis(self, workspace_id: str) -> WorkspaceAnalysis | None:
        from Sprout.workspace.intelligence import WorkspaceAnalysis

        databases = self._databases(workspace_id)
        project = await databases["manifest"].fetchone(
            "SELECT active_snapshot_id FROM Project WHERE id=?", (workspace_id,),
        )
        if project is None or not project[0]:
            return None
        version = project[0]
        row = await databases["structure"].fetchone(
            "SELECT snapshot_json, analyzer_version FROM WorktreeSnapshot WHERE id=?", (version,),
        )
        if row is None:
            raise RuntimeError(f"Published project snapshot {version} is missing")
        if row[1] != ANALYZER_VERSION:
            return None
        payload = json.loads(row[0])
        expected_fact_ids = payload.pop("project_fact_ids", [])
        manifest = await databases["manifest"].fetchone(
            "SELECT manifest_json FROM AnalysisManifest WHERE snapshot_id=?", (version,),
        )
        if manifest is None:
            raise RuntimeError(f"Published project manifest {version} is missing")
        payload["fields"]["manifest"] = json.loads(manifest[0])
        payload["fields"]["workspace"]["fields"]["manifest"] = json.loads(manifest[0])
        facts = []
        tables = (("architecture", "ArchitectureFact"), ("database", "DatabaseFact"))
        for partition, table in tables:
            facts.extend(await databases[partition].fetchall(
                f"SELECT ordinal, fact_json FROM {table} WHERE snapshot_id=?", (version,),
            ))
        payload["fields"]["knowledge"] = {
            "__dataclass__": "Sprout.workspace.knowledge.ProjectKnowledge",
            "fields": {"workspace_id": workspace_id, "items": [
                json.loads(row[1]) for row in sorted(facts, key=lambda row: row[0])
            ]},
        }
        actual_fact_ids = [json.loads(row[1])["fields"]["id"]
                           for row in sorted(facts, key=lambda row: row[0])]
        if actual_fact_ids != expected_fact_ids:
            raise RuntimeError(f"Published project snapshot {version} has incomplete facts")
        return from_jsonable(payload, WorkspaceAnalysis)

    async def status(self) -> dict[str, object]:
        return {"backend": type(self).__name__, "root": str(self.root),
                "opened_projects": len(self._projects), "partitions": list(SCHEMAS)}

    def close(self) -> None:
        for databases in self._projects.values():
            for db in databases.values():
                db.close()
        self._projects.clear()
