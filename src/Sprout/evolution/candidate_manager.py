"""Candidate lifecycle management."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from typing import TYPE_CHECKING
from uuid import uuid4

from Sprout.artifacts.models import Artifact, ArtifactKind, ArtifactStatus, GrowthCandidate
from Sprout.events import EVOLUTION_PUBLISHED, SKILL_PUBLISHED, Event, EventBus
from Sprout.evolution.candidate_evaluator import CandidateEvaluator
from Sprout.evolution.hard_gate import GateChecks
from Sprout.evolution.validation import artifact_fingerprint
from Sprout.runtime.state import ArtifactStateMachine
from Sprout.skills.models import Skill, TrustLevel
from Sprout.storage.contracts.knowledge import KnowledgeItem
from Sprout.storage.contracts.metadata import MetadataStore

if TYPE_CHECKING:
    from Sprout.skills.registry import SkillRegistry
    from Sprout.skills.repository import SkillRepository
    from Sprout.storage.contracts.knowledge import KnowledgeStore


class CandidateManager:
    """Lists, evaluates, approves, and rejects new growth candidates."""

    def __init__(
        self,
        metadata: MetadataStore,
        *,
        skills: SkillRegistry | None = None,
        skill_repository: SkillRepository | None = None,
        knowledge: KnowledgeStore | None = None,
        events: EventBus | None = None,
    ) -> None:
        self._metadata = metadata
        self._evaluator = CandidateEvaluator()
        self._skills = skills
        self._skill_repository = skill_repository
        self._knowledge = knowledge
        self._events = events

    async def list_candidates(self) -> list[GrowthCandidate]:
        artifacts = await self._metadata.list_artifacts()
        return [self._to_candidate(artifact) for artifact in artifacts]

    async def get_candidate_report(self, artifact_id: str) -> dict:
        """Read-only inspection; never releases publication ownership."""
        artifact = await self._require_artifact(artifact_id)
        record = artifact.metadata.get("growth_validation", {})
        guard = artifact.metadata.get("publication_guard", {})
        return {
            "artifact_id": artifact.id,
            "status": artifact.status.value,
            "name": artifact.name,
            "kind": artifact.kind.value,
            "validation_current": bool(record)
            and record.get("fingerprint") == artifact_fingerprint(artifact),
            "validation": record,
            "evaluation": artifact.metadata.get("growth_evaluation", {}),
            "publication": guard,
            "publication_error": artifact.metadata.get("publication_error"),
            "requires_manual_review": guard.get("state") in {"active", "uncertain"},
        }

    async def evaluate_all(self) -> dict[str, str]:
        changes: dict[str, str] = {}
        for candidate in await self.list_candidates():
            artifact = candidate.artifact
            if artifact is None or artifact.status is not ArtifactStatus.CANDIDATE:
                continue
            report = self._evaluator.evaluate(candidate)
            target = ArtifactStatus.VALIDATED if report.passed else ArtifactStatus.REJECTED
            if not report.pending:
                ArtifactStateMachine.validate(artifact.status, target)
            updated = replace(
                artifact,
                status=ArtifactStatus.CANDIDATE if report.pending else target,
                updated_at=artifact.updated_at,
                metadata={
                    **artifact.metadata,
                    "growth_evaluation": {
                        "state": "waiting_validation" if report.pending else target.value,
                        "reasons": list(report.hard_gate.reasons),
                        "utility_score": report.utility.score,
                    },
                },
            )
            await self._metadata.save_artifact(updated)
            changes[artifact.id] = updated.status.value
        return changes

    async def approve(self, artifact_id: str, *, decided_by: str = "cli") -> Artifact:
        artifact = await self._require_artifact(artifact_id)
        if artifact.metadata.get("publication_guard"):
            raise ValueError("Publication is occupied or settled; do not retry")
        if artifact.status not in {ArtifactStatus.VALIDATED, ArtifactStatus.PENDING_APPROVAL}:
            raise ValueError(
                f"Artifact {artifact.id} cannot be approved from {artifact.status.value}"
            )
        validation = artifact.metadata.get("growth_validation", {})
        if (
            validation.get("schema") != 1
            or validation.get("fingerprint") != artifact_fingerprint(artifact)
            or validation.get("tests_passed") is not True
            or validation.get("regression_free") is not True
            or not validation.get("source_run_ids")
        ):
            raise ValueError("Artifact lacks a current trusted validation record")
        if artifact.status not in {ArtifactStatus.VALIDATED, ArtifactStatus.PENDING_APPROVAL}:
            raise ValueError(
                f"Artifact {artifact.id} cannot be approved from {artifact.status.value}"
            )
        ArtifactStateMachine.validate(artifact.status, ArtifactStatus.PUBLISHED)
        updated = replace(
            artifact,
            status=ArtifactStatus.PUBLISHED,
            metadata={**artifact.metadata, "decided_by": decided_by},
        )
        # Reserve durable ownership before any filesystem/registry/knowledge
        # side effect. A dead process never releases this non-expiring claim.
        self._preflight_publication(artifact)
        owner = str(uuid4())
        if not await self._metadata.claim_artifact_publication(artifact, owner, decided_by):
            raise ValueError("Publication ownership changed; do not retry")
        occupied = await self._metadata.get_artifact(artifact.id)
        updated = replace(updated, metadata={**occupied.metadata, "decided_by": decided_by})
        try:
            await self._publish(updated)
            if not await self._metadata.finish_artifact_publication(updated, owner, "completed"):
                raise RuntimeError("Publication completion could not be committed")
        except (Exception, asyncio.CancelledError) as exc:
            uncertain = replace(
                occupied,
                metadata={
                    **occupied.metadata,
                    "decided_by": decided_by,
                    "publication_error": f"{type(exc).__name__}: {exc}",
                },
            )
            await self._metadata.finish_artifact_publication(uncertain, owner, "uncertain")
            raise
        updated = await self._metadata.get_artifact(artifact.id)
        try:
            await self._notify_published(updated)
        except Exception:
            logging.getLogger(__name__).exception("Publication committed; notification failed")
        return updated

    def _preflight_publication(self, artifact: Artifact) -> None:
        if artifact.kind in {ArtifactKind.SKILL, ArtifactKind.WORKFLOW}:
            if not artifact.name or any(
                not (char.isalnum() or char in "-_") for char in artifact.name
            ):
                raise ValueError("Skill publication requires an unambiguous safe name")
            if self._skills is None or self._skill_repository is None:
                raise ValueError("Skill publication requires registry and durable repository")
        elif artifact.kind is ArtifactKind.PROJECT_KNOWLEDGE:
            if self._knowledge is None:
                raise ValueError("Knowledge publication requires KnowledgeStore")
        else:
            raise ValueError(f"No publisher for artifact kind {artifact.kind.value}")

    async def record_validation(
        self, artifact_id: str, checks: GateChecks, *, source_run_ids: tuple[str, ...],
        comparison: dict | None = None,
        expected_fingerprint: str | None = None,
    ) -> Artifact:
        """Trusted internal runner handoff; not exposed as a CLI approval flag.

        The caller owns measurement provenance. Do not call from model output,
        imported artifact metadata, or user-supplied success booleans.
        """
        artifact = await self._require_artifact(artifact_id)
        if (expected_fingerprint is not None
                and artifact_fingerprint(artifact) != expected_fingerprint):
            raise ValueError("Candidate changed after measurement review")
        if artifact.status not in {
            ArtifactStatus.CANDIDATE,
            ArtifactStatus.VALIDATED,
            ArtifactStatus.PENDING_APPROVAL,
        }:
            raise ValueError("Artifact is not eligible for validation")
        if not source_run_ids or any(not item.strip() for item in source_run_ids):
            raise ValueError("Independent measurement run references are required")
        # Utility's candidate-stage bonus must not disappear during historical
        # validation; the actual stored artifact stays unchanged until CAS.
        candidate = self._to_candidate(replace(artifact, status=ArtifactStatus.CANDIDATE))
        if not self._evaluator.evaluate(candidate, checks).passed:
            raise ValueError("Tests, regressions and safety gates must explicitly pass")
        record = {
            "schema": 1,
            "fingerprint": artifact_fingerprint(artifact),
            "tests_passed": True,
            "regression_free": True,
            "source_run_ids": list(source_run_ids),
        }
        if comparison is not None:
            record["comparison"] = comparison
        if not await self._metadata.record_artifact_validation(artifact, record):
            raise ValueError("Artifact changed or publication is occupied")
        return await self._metadata.get_artifact(artifact.id)

    async def reject(self, artifact_id: str, *, reason: str = "") -> Artifact:
        artifact = await self._require_artifact(artifact_id)
        if artifact.status not in {
            ArtifactStatus.CANDIDATE,
            ArtifactStatus.VALIDATED,
            ArtifactStatus.PENDING_APPROVAL,
        }:
            raise ValueError(
                f"Artifact {artifact.id} cannot be rejected from {artifact.status.value}"
            )
        ArtifactStateMachine.validate(artifact.status, ArtifactStatus.REJECTED)
        updated = replace(
            artifact,
            status=ArtifactStatus.REJECTED,
            metadata={**artifact.metadata, "rejection_reason": reason},
        )
        await self._metadata.save_artifact(updated)
        return updated

    async def mark_monitoring(self, artifact_id: str) -> Artifact:
        return await self._transition(
            artifact_id,
            allowed={ArtifactStatus.PUBLISHED},
            target=ArtifactStatus.MONITORING,
        )

    async def mark_stable(self, artifact_id: str) -> Artifact:
        return await self._transition(
            artifact_id,
            allowed={ArtifactStatus.MONITORING},
            target=ArtifactStatus.STABLE,
        )

    async def deprecate(self, artifact_id: str) -> Artifact:
        return await self._transition(
            artifact_id,
            allowed={ArtifactStatus.STABLE},
            target=ArtifactStatus.DEPRECATED,
        )

    async def _require_artifact(self, prefix: str) -> Artifact:
        exact = await self._metadata.get_artifact(prefix)
        if exact is not None:
            return exact
        matches = [
            artifact
            for artifact in await self._metadata.list_artifacts()
            if prefix and artifact.id.startswith(prefix)
        ]
        if len(matches) > 1:
            raise ValueError(f"Ambiguous artifact prefix {prefix!r}")
        if matches:
            return matches[0]
        raise LookupError(f"No artifact matches id prefix {prefix!r}")

    async def _transition(
        self,
        artifact_id: str,
        *,
        allowed: set[ArtifactStatus],
        target: ArtifactStatus,
    ) -> Artifact:
        artifact = await self._require_artifact(artifact_id)
        if artifact.status not in allowed:
            raise ValueError(
                f"Artifact {artifact.id} cannot transition from {artifact.status.value} "
                f"to {target.value}"
            )
        ArtifactStateMachine.validate(artifact.status, target)
        updated = replace(artifact, status=target)
        await self._metadata.save_artifact(updated)
        return updated

    @staticmethod
    def _to_candidate(artifact: Artifact) -> GrowthCandidate:
        return GrowthCandidate(
            artifact=artifact,
            evidence_ids=artifact.evidence_ids,
            diagnosis=str(artifact.metadata.get("diagnosis", "")),
            rationale=str(artifact.metadata.get("rationale", "")),
        )

    async def _publish(self, artifact: Artifact) -> None:
        if artifact.kind in (ArtifactKind.SKILL, ArtifactKind.WORKFLOW):
            # A workflow is "a mature repeated orchestration shape" — lessons
            # about how this workspace's tasks actually go. That reaches the
            # agent through the same channel as a skill, because both are
            # instruction text prepended to the prompt; the distinction the
            # spec draws is kept in ``artifacts.kind``, not in how it travels.
            #
            # Without this branch a workflow artifact was marked PUBLISHED and
            # nothing else happened: no consumer existed, so a learned pattern
            # could never influence the next task.
            # ``trust`` must be set explicitly: the dataclass default is
            # ``untrusted``, and ``AgentContext.visible_skills`` requires
            # ``trusted``, so leaving it off registered the skill but kept it
            # out of the prompt. Approving is the human decision that grants
            # trust — ``approve`` records ``decided_by`` — so this is the one
            # place a self-evolved skill may become injectable. It also makes
            # the in-memory registry agree with what the loader reports after a
            # restart, instead of flipping the other way.
            skill = Skill(
                name=artifact.name,
                version=artifact.version,
                instructions=artifact.content,
                required_tools=(),
                enabled=True,
                trust=TrustLevel.TRUSTED.value,
            )
            if self._skill_repository is not None:
                self._skill_repository.save(skill)
            if self._skills is not None:
                self._skills.register(skill)
        elif artifact.kind is ArtifactKind.PROJECT_KNOWLEDGE:
            if self._knowledge is None:
                raise ValueError("No KnowledgeStore configured for project knowledge publish")
            await self._knowledge.put(
                KnowledgeItem(
                    id=f"ka-{artifact.id}",
                    content=artifact.content,
                    kind="knowledge",
                    evidence_ids=artifact.evidence_ids,
                )
            )

    async def _notify_published(self, artifact: Artifact) -> None:
        payload = {
            "artifact_id": artifact.id,
            "kind": artifact.kind.value,
            "name": artifact.name,
            "version": artifact.version,
        }
        if artifact.kind in {ArtifactKind.SKILL, ArtifactKind.WORKFLOW}:
            await self._emit(SKILL_PUBLISHED, payload)
        await self._emit(
            EVOLUTION_PUBLISHED,
            {
                "artifact_id": artifact.id,
                "kind": artifact.kind.value,
                "name": artifact.name,
                "version": artifact.version,
            },
        )

    async def _emit(self, name: str, payload: dict[str, object]) -> None:
        if self._events is not None:
            await self._events.publish(Event(name, payload))
