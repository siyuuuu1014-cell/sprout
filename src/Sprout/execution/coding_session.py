"""Host-owned per-node export, container dispatch and guarded artifact import."""

import hashlib
import json
from dataclasses import replace

from Sprout.execution.acceptance import acceptance_demo_path, acceptance_test_path
from Sprout.execution.coding_container import CodingCommand, coding_node_directory_name
from Sprout.execution.coding_dispatch import IsolatedNodeDispatcher
from Sprout.execution.coding_import import import_artifacts
from Sprout.execution.docker_verification import safe_path
from Sprout.execution.isolated_acceptance import verify_isolated_cases
from Sprout.execution.isolated_auxiliary import auxiliary_commands, run_auxiliary_checks
from Sprout.execution.verification_export import export_input
from Sprout.execution.verification_run import input_manifest
from Sprout.strategy.acceptance import AcceptanceSpec


class CodingNodeSession:
    """Install only from trusted Runtime assembly with a live guarded node.

    Inputs are sealed before dispatch. No replacing a partial export and no
    whole-tree import. This does not install itself into ordinary Runtime tasks.
    """

    def __init__(self, metadata, runner, file_broker, *, evidence, original, image,
                 oracle_paths, sandbox, task, node, regression_spec=None):
        self.metadata, self.runner, self.file_broker = metadata, runner, file_broker
        self.evidence, self.original, self.image = evidence, original, image
        if not oracle_paths:
            raise ValueError("Explicit frozen oracle paths required")
        source_id = task.metadata.get("replay_source_task_id")
        source_paths = ((acceptance_test_path(source_id), acceptance_demo_path(source_id))
                        if isinstance(source_id, str) and source_id else ())
        self.oracle_paths = tuple(dict.fromkeys((*oracle_paths, acceptance_test_path(task.id),
                                               acceptance_demo_path(task.id), *source_paths)))
        self.sandbox = sandbox
        self.task, self.node = task, node
        self.regression_spec = (AcceptanceSpec.from_payload(regression_spec).to_payload()
                                if regression_spec is not None else None)

    async def prepare(self):
        node_directory = coding_node_directory_name(self.node.id)
        evidence, target = safe_path(self.evidence), safe_path(self.sandbox.root)
        original = safe_path(self.original)
        if any(evidence.is_relative_to(root) or root.is_relative_to(evidence)
               for root in (target, original)):
            raise ValueError("Evidence must not overlap code or original workspace")
        directory = evidence / node_directory
        if directory.is_symlink():
            raise ValueError("Linked coding node directory")
        exported = directory / "coding-input"
        manifest = export_input(target, exported, original, exclude_paths=self.oracle_paths,
                                manifest_only=True)
        contract = {"task_id": self.task.id, "node_id": self.node.id, "baseline": manifest,
                    "target": str(target), "image": self.image,
                    "oracle_paths": list(self.oracle_paths)}
        owner = self.node.metadata.get("execution_guard", {}).get("owner")
        await self.metadata.ensure_coding_intent({
            "id": self.node.id + ":export", "node_id": self.node.id, "task_id": self.task.id,
            "owner": owner, "signature": hashlib.sha256(
                json.dumps(contract, sort_keys=True).encode()).hexdigest(), "contract": contract})
        directory.mkdir(exist_ok=True)
        seal = directory / "coding-baseline.json"
        if not exported.exists():
            if seal.exists():
                raise ValueError("Missing export with existing seal; cannot recreate")
            actual = export_input(target, exported, original, exclude_paths=self.oracle_paths)
            if actual != manifest:
                raise ValueError("Source changed while exporting")
            with seal.open("x", encoding="utf-8") as stream:
                json.dump(contract, stream, sort_keys=True)
        if (seal.is_symlink() or not seal.is_file()
                or seal.stat().st_size > 8 * 1024 * 1024
                or json.loads(seal.read_text(encoding="utf-8")) != contract
                or input_manifest(exported) != manifest):
            raise ValueError("Incomplete or changed coding export; cannot replace")
        context = CodingCommand(self.task.id, original, directory, exported,
                                self.image, ("python",), self.oracle_paths)
        return context, manifest

    async def execute_command(self, command, *, cwd, command_slot):
        context, baseline = await self.prepare()
        relative = safe_path(cwd).relative_to(safe_path(self.sandbox.root)).as_posix()
        request = replace(context, command=tuple(command), relative_cwd=relative)
        dispatch = IsolatedNodeDispatcher(self.metadata, self.runner,
                                         lambda **kwargs: request)
        process = await dispatch(task=self.task, node=self.node, command=tuple(command),
                                 cwd=cwd, command_slot=command_slot)
        if not process.allowed or process.exit_code != 0:
            return {"process": process, "imported": False}
        imported = await import_artifacts(
            self.metadata, self.file_broker, exported=context.exported, baseline=baseline,
            oracle_paths=self.oracle_paths, sandbox=self.sandbox, task=self.task, node=self.node,
            command_slot=command_slot + "-import")
        return {"process": process, "import": imported, "imported": imported.get("applied", False)}

    async def verify(self, *, acceptance_spec, regression_spec, phase="evaluation"):
        context, _ = await self.prepare()
        return await verify_isolated_cases(
            self.metadata, self.runner, context=context, node=self.node,
            acceptance_spec=acceptance_spec, regression_spec=regression_spec, phase=phase)

    async def verify_auxiliary(self, command_metadata):
        commands = auxiliary_commands(command_metadata)
        context, _ = await self.prepare()
        return await run_auxiliary_checks(self.metadata, self.runner, context=context,
                                         node=self.node, commands=commands)
