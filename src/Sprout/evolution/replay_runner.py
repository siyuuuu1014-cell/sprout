"""Minimal runtime-backed task replay."""

from __future__ import annotations

import asyncio
import os
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from Sprout.task.models import TaskStatus
from Sprout.trajectory.models import ArtifactSnapshot

if TYPE_CHECKING:
    from Sprout.runtime.runtime import Runtime


@dataclass(frozen=True, slots=True)
class ReplayResult:
    task_id: str
    status: TaskStatus
    error: str | None = None
    artifact_snapshot: ArtifactSnapshot | None = None
    source_task_id: str = ""
    workspace_revision: str | None = None
    evidence_dir: str | None = None
    measurement_path: str | None = None
    measurement_status: str = "not_measured"


class ReplayRunner:
    """Explicit new-run replay; never resume the source task's completed nodes."""

    def __init__(self, runtime: Runtime) -> None:
        self._runtime = runtime

    async def run(
        self,
        task_id: str,
        *,
        artifact_snapshot: ArtifactSnapshot | None = None,
        revision: str | None = None,
        verification_commands: tuple[str, ...] = (),
        regression_commands: tuple[str, ...] = (),
        oracle_paths: tuple[str, ...] = (),
        verification_image: str | None = None,
        settings=None,
        output_dir: str | Path | None = None,
        cli_principal=None,
        regression_spec=None,
        coding_image=None,
        temporal_host: str | None = None,
        temporal_namespace: str | None = None,
    ) -> ReplayResult:
        task = await self._runtime.get_task(task_id)
        if task is None:
            raise LookupError(f"Task not found: {task_id}")

        def waiting(reason):
            return ReplayResult(
                task_id="",
                source_task_id=task_id,
                status=TaskStatus.WAITING_RESOURCE,
                error=reason,
                artifact_snapshot=artifact_snapshot,
            )

        if (
            not revision
            or not verification_commands
            or not regression_commands
            or settings is None
            or output_dir is None
        ):
            return waiting(
                "Replay requires revision, tests, regressions, settings and output directory"
            )
        if os.getenv("TEMPORAL_HOST"):
            return waiting(
                "Isolated replay cannot inherit a shared Temporal worker; no local fallback"
            )
        from Sprout.gateway.local_identity import creator_record, current_cli_principal

        try:
            if cli_principal is None:
                return waiting("Trusted local CLI entry point identity required")
            creator = current_cli_principal()
            if creator_record(cli_principal) != creator_record(creator):
                return waiting("CLI entry point identity does not match current OS account")
        except ValueError:
            return waiting("Trusted current local CLI identity required")
        from Sprout.evolution.replay_measurement import command_parts

        for command in (*verification_commands, *regression_commands):
            command_parts(command)
        workspace = await self._runtime.get_workspace(task.workspace_id)
        if workspace is None:
            return waiting("Source workspace is unavailable")
        if task.execution_mode not in {"code", "read_only"}:
            return waiting("Source task execution mode is unconfirmed")
        from Sprout.evolution.coding_profile import coding_profile

        if coding_image and task.execution_mode != "code":
            return waiting("Coding image is only supported for explicit code experiments")
        coding = coding_profile(coding_image) if coding_image else None
        commissioned = temporal_host is not None or temporal_namespace is not None
        if commissioned and (
            not temporal_host or not temporal_host.strip()
            or not temporal_namespace or not temporal_namespace.strip()
            or coding is None or task.execution_mode != "code"
            or regression_spec is None
        ):
            return waiting(
                "Coding execution requires image, regression spec, "
                "explicit Temporal host and namespace"
            )
        if commissioned and settings.mcp.clients:
            return waiting("External MCP clients are not supported in isolated coding execution")
        if task.execution_mode == "code" and not oracle_paths:
            return waiting("Code replay requires explicit frozen test/config oracle paths")
        if task.execution_mode == "code":
            if not verification_image:
                return waiting("Docker verification requires an explicitly pinned local image")
            from Sprout.execution.docker_verification import validate_image

            validate_image(verification_image)
            from Sprout.evolution.pytest_measurement import pytest_arguments

            if any(
                pytest_arguments(command_parts(command)) is None
                for command in (*verification_commands, *regression_commands)
            ):
                return waiting("Independent Docker verification only supports pytest commands")
        from Sprout.artifacts.models import ArtifactKind
        from Sprout.evolution.isolated_replay import content_digest, prepare
        from Sprout.runtime.factory import create_runtime
        from Sprout.skills.models import Skill, TrustLevel

        artifact = None
        if artifact_snapshot is not None:
            artifact = await self._runtime.storage.metadata.get_artifact(
                artifact_snapshot.artifact_id
            )
            if (
                artifact is None
                or artifact.version != artifact_snapshot.artifact_version
                or not artifact_snapshot.content_hash
                or content_digest(artifact.content) != artifact_snapshot.content_hash
            ):
                return waiting("Candidate snapshot must match exact version and content hash")
            if artifact.kind not in {ArtifactKind.SKILL, ArtifactKind.WORKFLOW}:
                return waiting(
                    "Replay currently supports only skill/workflow instruction candidates"
                )
            from Sprout.evolution.candidate_evaluator import CandidateEvaluator
            from Sprout.evolution.candidate_manager import CandidateManager

            report = CandidateEvaluator().evaluate(CandidateManager._to_candidate(artifact))
            if not report.passed and not report.pending:
                return waiting("Candidate failed static safety or utility gates")
        directory, repo, commit, isolated_settings = await asyncio.to_thread(
            prepare, workspace.root, revision, Path(output_dir), settings
        )
        from Sprout.evolution.replay_oracle import integrity, snapshot

        frozen = snapshot(repo, oracle_paths) if oracle_paths else None
        from Sprout.evolution.regression_spec import freeze

        regression_fingerprint = freeze(directory, regression_spec)
        isolated = create_runtime(isolated_settings)
        isolated._isolated_coding_required = task.execution_mode == "code"
        measurement_path = None
        execution_scope = AsyncExitStack()
        try:
            if not commissioned:
                await isolated.start()
            if artifact is not None:
                # Explicit operator-requested experiment, isolated registry only.
                # No approval/publication of the source artifact takes place.
                isolated.skills.register(
                    Skill(
                        name=artifact.name,
                        version=artifact.version,
                        instructions=artifact.content,
                        enabled=True,
                        trust=TrustLevel.TRUSTED.value,
                    )
                )
            replay_workspace = await isolated.open_workspace(repo)
            metadata = {
                key: value
                for key, value in task.metadata.items()
                if key in {"acceptance_spec", "require_acceptance_tests", "required_paths", "steps"}
            }
            metadata.update(
                regression_fingerprint=regression_fingerprint,
                verification_commands=list(
                    dict.fromkeys((*verification_commands, *regression_commands))
                ),
                replay_source_task_id=task.id,
                replay_revision=commit,
                replay_candidate=artifact_snapshot.artifact_id if artifact_snapshot else None,
                replay_candidate_hash=artifact_snapshot.content_hash if artifact_snapshot else None,
                replay_stage="image_preflight",
            )
            from dataclasses import asdict

            from Sprout.evolution.replay_config import configuration_contract
            from Sprout.evolution.replay_plan import build_plan
            from Sprout.evolution.replay_snapshot import runtime_snapshot

            metadata["replay_plan"] = build_plan(
                source_task_id=task.id, revision=commit, mode=task.execution_mode,
                original=workspace.root, repo=repo, tests=verification_commands,
                regressions=regression_commands, oracle_paths=oracle_paths,
                frozen=frozen, image=verification_image,
                candidate=asdict(artifact_snapshot) if artifact_snapshot else None,
                configuration=configuration_contract(isolated_settings),
                runtime=runtime_snapshot(isolated),
                creator=creator_record(creator),
                coding=coding,
            )
            fresh = await isolated.create_task(
                replay_workspace.id,
                task.instruction,
                source="cli",
                actor=creator,
                budget=task.budget,
                metadata=metadata,
                execution_mode=task.execution_mode,
            )
            if task.execution_mode == "code":
                from Sprout.execution.docker_verification import (
                    DockerVerificationBackend,
                    image_ready,
                )

                inspection = await DockerVerificationBackend().inspect_image(
                    isolated, fresh.id, verification_image
                )
                if not image_ready(inspection):
                    await isolated.storage.metadata.update_task_status(
                        fresh.id, TaskStatus.WAITING_RESOURCE
                    )
                    return ReplayResult(
                        task_id=fresh.id,
                        source_task_id=task_id,
                        status=TaskStatus.WAITING_RESOURCE,
                        error="Docker image unavailable/unapproved/incompatible; no host fallback",
                        workspace_revision=commit,
                        evidence_dir=str(directory),
                        artifact_snapshot=artifact_snapshot,
                    )
            if commissioned:
                from Sprout.evolution.coding_run import coding_execution

                execute = await execution_scope.enter_async_context(coding_execution(
                    isolated, fresh, host=temporal_host, namespace=temporal_namespace))
                result = await execute()
            else:
                result = await isolated.execute(fresh)
            from Sprout.evolution.replay_measurement import write_evidence

            records = []
            function_records = []
            regression_records = []
            oracle_unchanged = bool(frozen) and integrity(repo, oracle_paths, frozen)
            if (
                result.status == TaskStatus.COMPLETED
                and task.execution_mode == "code"
                and oracle_unchanged
            ):
                from Sprout.evolution.replay_docker import measure_docker

                try:
                    records = await measure_docker(
                        isolated,
                        fresh.id,
                        verification_commands,
                        regression_commands,
                        directory=directory,
                        repo=repo,
                        original=workspace.root,
                        image=verification_image,
                        oracle_paths=oracle_paths,
                        frozen=frozen,
                        classify_overrides=isolated_settings.security.classify.rules,
                    )
                except (ValueError, OSError):
                    records = [
                        {"waiting": True, "reason": "verification_input_unavailable_or_changed"}
                    ]
                oracle_unchanged = integrity(repo, oracle_paths, frozen)
                if oracle_unchanged and not any(record.get("waiting") for record in records):
                    from Sprout.evolution.replay_functions import measure_functions

                    function_records = await measure_functions(
                        isolated, fresh.id, directory=directory, repo=repo,
                        original=workspace.root, image=verification_image,
                        oracle_paths=oracle_paths, frozen=frozen,
                        acceptance_spec=task.metadata.get("acceptance_spec"),
                        classify_overrides=isolated_settings.security.classify.rules,
                        regression_fingerprint=regression_fingerprint,
                    )
                    if not any(record.get("waiting") for record in function_records):
                        regression_records = await measure_functions(
                            isolated, fresh.id, directory=directory, repo=repo,
                            original=workspace.root, image=verification_image,
                            oracle_paths=oracle_paths, frozen=frozen, acceptance_spec=None,
                            classify_overrides=isolated_settings.security.classify.rules,
                            regression_fingerprint=regression_fingerprint, purpose="regression",
                        )
            measurement_status = (
                "waiting_resource"
                if any(record.get("waiting") for record in
                       (*records, *function_records, *regression_records))
                else "observed"
                if records
                else "not_measured"
            )
            measurement_path = write_evidence(
                directory,
                {
                    "schema_version": 1,
                    "source_task_id": task.id,
                    "task_id": fresh.id,
                    "revision": commit,
                    "candidate_id": artifact.id if artifact else None,
                    "candidate_hash": artifact_snapshot.content_hash if artifact_snapshot else None,
                    "test_commands": verification_commands,
                    "acceptance_spec": task.metadata.get("acceptance_spec"),
                    "regression_commands": regression_commands,
                    "task_status": result.status,
                    "records": records,
                    "function_records": function_records,
                    "regression_records": regression_records,
                    "regression_fingerprint": regression_fingerprint,
                    "measurement_status": measurement_status,
                    "oracle_paths": oracle_paths,
                    "oracle_snapshot": frozen,
                    "oracle_unchanged": oracle_unchanged,
                    "validation_committed": False,
                    "blocker": (
                        "Frozen oracle integrity and paired comparison are not yet verified"
                        if records
                        else "Only completed code tasks can be independently measured"
                    ),
                },
            )
        finally:
            try:
                await execution_scope.aclose()
            finally:
                if getattr(isolated, "_isolated_lifecycle_owned", False) is not True:
                    if commissioned:
                        await isolated.storage.close()
                    else:
                        await isolated.stop()
        return ReplayResult(
            task_id=fresh.id,
            status=result.status,
            error=result.error,
            artifact_snapshot=artifact_snapshot,
            source_task_id=task_id,
            workspace_revision=commit,
            evidence_dir=str(directory),
            measurement_path=measurement_path,
            measurement_status=measurement_status,
        )
