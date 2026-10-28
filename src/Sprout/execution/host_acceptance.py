"""Host-created frozen acceptance data. It never grants file/process authority."""
import json
import os
import stat
from contextlib import ExitStack, contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path

from Sprout.execution.node_result_source import payload_digest
from Sprout.strategy.acceptance import AcceptanceSpec


@dataclass(frozen=True, slots=True, eq=False, weakref_slot=True)
class _FrozenExecutionContract:
    """Opaque Runtime registry key, not serializable authority."""

    issuer: object


class _WorktreePin:
    def __init__(self, root, stack):
        from Sprout.execution import file_access as access

        self.root, self.stack, self.access = Path(root), stack, access
        self.leaves = {}
        self.directories = {}
        if not self.root.is_absolute():
            raise PermissionError("frozen_cwd_not_absolute")
        if os.name == "nt":
            access._win_volume(self.root)
            current = Path(self.root.anchor)
            handle = access._win_directory(current, stack)
            for part in self.root.parts[1:]:
                current /= part
                handle = access._win_directory(current, stack)
            info = access._win_info(handle)
            self.identity = ("nt", info.volume, info.index_high, info.index_low)
            self.root_handle = handle
            self.spawn = {"cwd": str(self.root)}
        elif os.name == "posix" and hasattr(os, "O_NOFOLLOW"):
            current = Path("/")
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
            fd = os.open(current, flags)
            stack.callback(os.close, fd)
            for part in self.root.parts[1:]:
                current /= part
                fd = os.open(part, flags, dir_fd=fd)
                stack.callback(os.close, fd)
                info = os.fstat(fd)
                self.directories[current] = (info.st_dev, info.st_ino)
            self.root_fd = fd
            info = os.fstat(fd)
            self.identity = ("posix", info.st_dev, info.st_ino)
            # The child enters the actual pinned directory, not a racing pathname.
            descriptor = f"/proc/self/fd/{fd}"
            if not Path(descriptor).is_dir():
                raise PermissionError("frozen_descriptor_cwd_unavailable")
            self.spawn = {"cwd": descriptor, "pass_fds": (fd,)}
        else:
            raise PermissionError("frozen_cwd_platform_unavailable")

    def pin_file(self, relative):
        from Sprout.execution.file_snapshot import ResourceIdentity
        from Sprout.workspace.authorized_content import relative_label

        relative = relative_label(relative)
        if relative in self.leaves:
            return self.leaves[relative]
        target = self.root / relative
        if os.name == "nt":
            parent = self.root
            for part in Path(relative).parts[:-1]:
                parent /= part
                self.access._win_directory(parent, self.stack)
            handle = self.access._win_open(target, 0x80)
            self.stack.callback(self.access._kernel().CloseHandle, handle)
            info = self.access._win_info(handle)
            self.access._win_name(handle, target)
            if info.attributes & 0x10 or info.links != 1:
                raise PermissionError("frozen_input_not_regular")
            identity = ResourceIdentity.from_stat(target.stat(follow_symlinks=False))
        else:
            fd = self.root_fd
            for part in Path(relative).parts[:-1]:
                fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                             | os.O_CLOEXEC, dir_fd=fd)
                self.stack.callback(os.close, fd)
            leaf = os.open(Path(relative).name, os.O_RDONLY | os.O_NOFOLLOW
                           | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=fd)
            self.stack.callback(os.close, leaf)
            info = os.fstat(leaf)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise PermissionError("frozen_input_not_regular")
            identity = ResourceIdentity.from_stat(info)
        self.leaves[relative] = identity
        return identity

    def check(self):
        from Sprout.execution.file_snapshot import ResourceIdentity

        if os.name == "nt":
            info = self.access._win_info(self.root_handle)
            self.access._win_name(self.root_handle, self.root)
            current = ("nt", info.volume, info.index_high, info.index_low)
        else:
            for path, identity in self.directories.items():
                info = path.stat(follow_symlinks=False)
                if not stat.S_ISDIR(info.st_mode) or (info.st_dev, info.st_ino) != identity:
                    raise PermissionError("frozen_cwd_identity_changed")
            info = os.fstat(self.root_fd)
            current = ("posix", info.st_dev, info.st_ino)
        if current != self.identity:
            raise PermissionError("frozen_cwd_identity_changed")
        for path, identity in self.leaves.items():
            current = ResourceIdentity.from_stat((self.root / path).stat(follow_symlinks=False))
            if current != identity:
                raise PermissionError("frozen_input_identity_changed")


@contextmanager
def pin_execution_directory(root):
    """Reuse R03 native no-follow primitives. Unsupported platforms refuse."""
    with ExitStack() as stack:
        pin = _WorktreePin(root, stack)
        pin.check()
        yield pin


def check_module_dependencies(spec, contents):
    """Known local inputs and stdlib only; no filesystem discovery/import probe."""
    import ast
    import sys

    modules = {path[:-3].replace("/", "."): text for path, text in contents.items()
               if path.endswith(".py")}
    required = set()
    for case in spec.cases:
        module = case.target.split(":")[0]
        pieces = module.split(".")
        required.add(module.replace(".", "/") + ".py")
        required.update("/".join(pieces[:i]) + "/__init__.py"
                        for i in range(1, len(pieces)))
    if not required.issubset(contents):
        raise PermissionError("frozen_module_inputs_unavailable")
    pending, visited = list(required), set()
    while pending:
        path = pending.pop()
        if path in visited:
            continue
        visited.add(path)
        tree = ast.parse(contents[path])
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and (
                isinstance(node.func, ast.Name) and node.func.id in {"eval", "exec", "__import__"}
                or isinstance(node.func, ast.Attribute) and node.func.attr in {
                    "import_module", "exec_module"}
            ):
                raise PermissionError("frozen_dynamic_import_unavailable")
            if isinstance(node, ast.ImportFrom) and node.level:
                raise PermissionError("frozen_relative_import_unavailable")
            names = ([x.name for x in node.names] if isinstance(node, ast.Import) else
                     [node.module] if isinstance(node, ast.ImportFrom) else [])
            for name in names:
                if name.split(".")[0] not in sys.stdlib_module_names and name not in modules:
                    raise PermissionError("frozen_dependency_unavailable")
                if name in modules:
                    module_path = name.replace(".", "/") + ".py"
                    pieces = name.split(".")
                    dependencies = {module_path} | {
                        "/".join(pieces[:i]) + "/__init__.py" for i in range(1, len(pieces))}
                    if not dependencies.issubset(contents):
                        raise PermissionError("frozen_module_inputs_unavailable")
                    required.update(dependencies)
                    pending.extend(dependencies - visited)
    return tuple(sorted(required))


def binding_subject(task, workspace):
    return json.loads(json.dumps({"task_id": task.id, "workspace_id": workspace.id,
            "root": str(workspace.root), "revision": workspace.revision,
            "actor": asdict(task.actor), "source": task.source,
            "instruction_sha256": payload_digest(task.instruction)}, allow_nan=False))


def check_binding(task, workspace, binding):
    try:
        if (type(binding) is not dict or type(binding.get("version")) is not int
                or binding["version"] != 1 or task.delegation_scope.is_expired()
                or task.actor.authenticated is not True or task.source != task.actor.source
                or task.workspace_id != workspace.id
                or binding["subject"] != binding_subject(task, workspace)
                or binding["sha256"] != payload_digest(
                    {k: v for k, v in binding.items() if k != "sha256"})):
            raise ValueError("binding changed")
        spec = AcceptanceSpec.from_payload(binding["spec"])
        if (spec.to_payload() != binding["spec"]
                or payload_digest(spec.to_payload()) != binding["spec_sha256"]):
            raise ValueError("spec changed")
        from Sprout.execution.file_snapshot import ResourceIdentity, valid_identity
        from Sprout.workspace.authorized_content import relative_label

        paths = (relative_label(binding["test_path"]), relative_label(binding["demo_path"]))
        if paths[0] == paths[1] or set(binding["resources"]) != set(paths):
            raise ValueError("frozen resources changed")
        for resource in binding["resources"].values():
            if (not valid_identity(ResourceIdentity(**resource["identity"]))
                    or type(resource["digest"]) is not str or len(resource["digest"]) != 64
                    or any(c not in "0123456789abcdef" for c in resource["digest"])):
                raise ValueError("frozen identity invalid")
        return spec
    except (ValueError, TypeError, KeyError) as exc:
        raise PermissionError("host_acceptance_binding_invalid") from exc
