"""Runtime-installed node dispatcher; request assembly is trusted host code only."""

import re

from Sprout.execution.coding_backend import DockerCodingBackend
from Sprout.execution.coding_container import CodingCommand


class IsolatedNodeDispatcher:
    """Connect NodeExecutor callbacks to one node-owned container command.

    request_factory must be installed by Runtime and bind its fixed image,
    dedicated export and cwd mapping. It is not a tool/model parameter, and this
    class does not itself grant permission or copy container artifacts.
    """

    def __init__(self, metadata, authorized_runner, request_factory):
        if not callable(request_factory):
            raise ValueError("Trusted host request factory required")
        self._metadata = metadata
        self._runner = authorized_runner
        self._request_factory = request_factory

    async def __call__(self, *, task, node, command, cwd, command_slot):
        if not re.fullmatch(r"(?:baseline|evaluation)-[0-9]+", command_slot):
            raise ValueError("Host-generated node command slot required")
        guard = node.metadata.get("execution_guard", {})
        owner = guard.get("owner")
        if node.task_id != task.id or not owner or guard.get("state") != "active":
            raise ValueError("Live guarded task/node callback required")
        request = self._request_factory(task=task, node=node, command=command,
                                        cwd=cwd, command_slot=command_slot)
        if (not isinstance(request, CodingCommand) or request.task_id != task.id
                or request.command != tuple(command)):
            raise ValueError("Coding request identity or command changed during assembly")
        backend = DockerCodingBackend(self._metadata, self._runner,
                                     node_id=node.id, owner=owner, command_slot=command_slot)
        return await backend.execute(request, node_id=node.id)
