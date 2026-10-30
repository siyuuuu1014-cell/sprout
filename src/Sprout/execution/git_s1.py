"""GitBroker-only bounded read profile. Production execution remains HOLD.

There is deliberately NO installation/view issuer in this patch. A pathname,
Workspace.kind, metadata, or freshly computed hash cannot attest a trusted Git
installation or a stable managed view. Both authority resolvers fail closed.
The private runner is tested with mocked authority and process I/O, NOT offered
as a public capability accepting caller-supplied profiles.

A future issuer must keep the installation (including its loader dependencies),
view, sanitized local config/attributes, empty hooks/config and private paths
stable for the entire operation; exclude includes, filters, remotes, promisor
objects, external alternates and helper execution; and prove the view/output
fits the original Scope. Environment/argv pins alone do not establish this.
Do not enable either resolver based on a one-time scan of a mutable repository.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from Sprout.execution.models import GitResult
from Sprout.execution.proc import capture_bounded, terminate
from Sprout.task.models import DelegationScope
from Sprout.tools.result import ToolExecutionUnconfirmed
from Sprout.workspace.models import Workspace

TIMEOUT_SECONDS = 30.0
REVIEW_TOTAL_SECONDS = 90.0
CLEANUP_SECONDS = 5.0
MAX_OUTPUT_BYTES = 1024 * 1024


@dataclass(frozen=True, slots=True)
class _Installation:
    """Internal evidence shape only; constructing it does NOT establish trust."""

    path: Path
    version: str
    identity: str


@dataclass(frozen=True, slots=True)
class _Plan:
    """Future issuer-owned inputs. Never parsed from tools or workspace text."""

    installation: _Installation
    view: Path
    home: Path
    hooks: Path
    config: Path
    temp: Path
    system_env: dict[str, str]


def _resolve_installation() -> _Installation | None:
    # No approved installation authority currently exists. No PATH discovery,
    # first-seen hash enrollment, arbitrary path option, or version subprocess.
    return None


def _resolve_view(
    workspace: Workspace, scope: DelegationScope | None, installation: _Installation,
) -> _Plan | None:
    # No existing provider proves an immutable, scope-constrained sanitized
    # view. Do not infer one from workspace.root/kind or accept a trusted flag.
    return None


async def run_read(
    workspace: Workspace, operation: str, scope: DelegationScope | None,
) -> GitResult:
    if operation not in {"status", "diff"}:
        return GitResult(action=operation, allowed=False, reason="s1_operation_not_supported")
    installation = _resolve_installation()
    if installation is None:
        return GitResult(action=operation, allowed=False,
                         reason="s1_trusted_installation_unavailable")
    plan = _resolve_view(workspace, scope, installation)
    if plan is None:
        return GitResult(action=operation, allowed=False, reason="s1_managed_view_unavailable")
    return await _execute(plan, operation)


def _environment(plan: _Plan) -> dict[str, str]:
    # No os.environ copy: not even PATH, HOME, GIT_CONFIG_* or credentials.
    env = {key: value for key, value in plan.system_env.items()
           if key in {"SYSTEMROOT", "WINDIR"}}
    env.update({
        "HOME": str(plan.home), "USERPROFILE": str(plan.home),
        "XDG_CONFIG_HOME": str(plan.home), "APPDATA": str(plan.home),
        "LOCALAPPDATA": str(plan.home), "TEMP": str(plan.temp),
        "TMP": str(plan.temp), "TMPDIR": str(plan.temp),
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_SYSTEM": str(plan.config),
        "GIT_CONFIG_GLOBAL": str(plan.config), "GIT_ATTR_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0",
        "GIT_NO_LAZY_FETCH": "1", "GIT_PAGER": "", "LC_ALL": "C",
    })
    return env


def _argv(plan: _Plan, operation: str) -> tuple[str, ...]:
    commands = {
        "status": ("status", "--porcelain=v1", "--ignore-submodules=all"),
        "diff": ("diff", "--no-color", "--no-ext-diff", "--no-textconv",
                 "--ignore-submodules=all"),
    }
    pins = (f"core.hooksPath={plan.hooks}", "core.fsmonitor=false", "core.pager=",
            "core.untrackedCache=false", f"core.attributesFile={plan.config}",
            "diff.ignoreSubmodules=all", "submodule.recurse=false", "protocol.allow=never",
            "maintenance.auto=false", "gc.auto=0")
    return (str(plan.installation.path), "--no-pager", "--no-optional-locks",
            *(part for pin in pins for part in ("-c", pin)),
            "-C", str(plan.view), *commands[operation])


async def _execute(plan: _Plan, operation: str) -> GitResult:
    """Runner mechanics only; only run_read may supply issuer-verified inputs.

No current production issuer can reach this function. Native helper exclusion,
installation identity and filesystem stability are NOT proven by its mocks.
All failures after attempting spawn are conservative UNCONFIRMED, even if the
child was reaped: termination does not prove no effects. No automatic retry.
"""
    argv = _argv(plan, operation)
    process = None
    try:
        process = await asyncio.wait_for(
            asyncio.create_subprocess_exec(
                *argv, cwd=str(plan.view), env=_environment(plan),
                stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, limit=4096,
            ), timeout=TIMEOUT_SECONDS,
        )
        stdout, _stderr = await asyncio.wait_for(
            capture_bounded(process, MAX_OUTPUT_BYTES), timeout=TIMEOUT_SECONDS,
        )
        if process.returncode != 0:
            raise ToolExecutionUnconfirmed("s1_nonzero_or_unknown_exit")
        return GitResult(action=operation, output=stdout.decode("utf-8", errors="replace"))
    except BaseException as exc:
        cleanup_confirmed = False
        if process is not None:
            try:
                await asyncio.wait_for(terminate(process), timeout=CLEANUP_SECONDS)
                cleanup_confirmed = process.returncode is not None
            except BaseException:
                pass  # Still unconfirmed; never convert cleanup failure to success.
        reason = ("s1_execution_unconfirmed" if cleanup_confirmed
                  else "s1_execution_and_cleanup_unconfirmed")
        if isinstance(exc, asyncio.CancelledError):
            cancelled = asyncio.CancelledError()
            cancelled.add_note(f"UNCONFIRMED: {reason}; do not automatically retry")
            raise cancelled from None
        raise ToolExecutionUnconfirmed(reason) from None


# G1 is separate from the still-retired unbound S1 profile above. A data object
# is never authority: every use must match the current Runtime's private key.
@dataclass(frozen=True, eq=False)
class _ReviewView:
    executable: Path
    executable_sha256: str
    chain: tuple[tuple[str, str], ...]
    baseline: str
    metadata: Path
    common: Path
    paths: tuple[str, ...]
    sandbox: object
    root_identity: tuple
    parent: Path
    parent_identity: tuple
    subject: str
    metadata_identity: tuple
    common_identity: tuple


def _snapshot(root, relative, authorize, limit=1_000_000):
    from threading import Event

    from Sprout.execution import file_access
    from Sprout.execution.file_snapshot import _ReadOptions, checked_snapshot

    # Private Host callback may bind this exact native operation to its selected
    # source resource. R03 still invokes it at every original boundary/chunk.
    factory = getattr(authorize, "for_resource", None)
    if factory is not None:
        authorize = factory(Path(root), relative)
    value = file_access.file_operation(Path(root), Path(root) / relative, "snapshot",
        authorize, content=_ReadOptions(limit, None, Event(), authorize))
    if value is False:
        return None  # Native, authorized missing-leaf result, never a guessed absence.
    result = checked_snapshot(value, limit, None)
    if not result.ok:
        raise PermissionError("git_snapshot_unavailable")
    return result


def _source(binding, authorize):
    """Only explicitly paired management data; no config, credentials or discovery."""
    import os
    import re
    from dataclasses import asdict

    from Sprout.execution.host_acceptance import pin_execution_directory

    state = {}
    def read(root, path, limit=1_000_000):
        result = _snapshot(root, path, authorize, limit)
        state[str(Path(root) / path)] = (None if result is None else
            {"sha256": result.digest, "identity": asdict(result.identity)})
        return result
    def canonical(p):
        return os.path.normcase(os.path.abspath(p))
    root = binding.sandbox.root
    if canonical(binding.metadata) == canonical(root / ".git"):
        if canonical(binding.common) != canonical(binding.metadata):
            raise PermissionError("git_main_common_mismatch")
        with pin_execution_directory(binding.metadata) as pin:
            pin.check()
    else:
        pointer = read(root, ".git", 4096)
        if pointer is None or not pointer.data.startswith(b"gitdir: "):
            raise PermissionError("git_worktree_pointer_unavailable")
        path = pointer.data[8:].decode("utf-8", "strict").strip()
        if not Path(path).is_absolute() or canonical(path) != canonical(binding.metadata):
            raise PermissionError("git_worktree_pointer_changed")
        common = read(binding.metadata, "commondir", 4096)
        back = read(binding.metadata, "gitdir", 4096)
        if (common is None or back is None
                or canonical(binding.metadata / common.data.decode().strip())
                    != canonical(binding.common)
                or canonical(back.data.decode().strip()) != canonical(root / ".git")):
            raise PermissionError("git_worktree_management_mismatch")
    head = read(binding.metadata, "HEAD", 4096)
    index = read(binding.metadata, "index", 4_000_000)
    if head is None or index is None:
        raise PermissionError("git_head_or_index_unavailable")
    value = head.data.decode("ascii", "strict").strip()
    if value.startswith("ref: "):
        ref = value[5:]
        if (not re.fullmatch(r"refs/heads/[A-Za-z0-9_./-]+", ref)
                or any(p in {"", ".", ".."} for p in ref.split("/"))
                or ".." in ref or "@{" in ref
                or any(p.startswith(".") or p.endswith((".", ".lock")) for p in ref.split("/"))):
            raise PermissionError("git_ref_invalid")
        target = read(binding.common, ref, 128)
        if target is None:
            packed = read(binding.common, "packed-refs")
            matches = ([] if packed is None else [line.split()[0]
                for line in packed.data.decode("ascii").splitlines()
                if not line.startswith(("#", "^")) and line.endswith(" " + ref)])
            if len(matches) != 1:
                raise PermissionError("git_ref_unavailable")
            value = matches[0]
        else:
            value = target.data.decode("ascii").strip()
    if value != binding.baseline:
        raise PermissionError("git_baseline_changed")
    return index.data, state, read


class _LooseObjects:
    """Bounded selected-object reader, not a Git executor or pack decoder.

    Packed/missing objects fail closed. No alternates, replacements, lazy fetch,
    config or unselected blobs are consulted. Git still generates every patch.
    """
    def __init__(self, binding, read):
        self.binding, self.read, self.cache, self.total = binding, read, {}, 0
        self.compressed_total = 0

    def object(self, oid, kind):
        import hashlib
        import re
        import zlib

        if not re.fullmatch(r"[0-9a-f]{40}", oid):
            raise PermissionError("git_object_id_invalid")
        if oid not in self.cache:
            value = self.read(self.binding.common, "objects/" + oid[:2] + "/" + oid[2:],
                              min(2_000_000, 4_000_000 - self.compressed_total))
            if value is None:
                raise PermissionError("git_packed_or_missing_object_unavailable")
            self.compressed_total += len(value.data)
            if self.compressed_total > 4_000_000:
                raise PermissionError("git_compressed_object_budget")
            decoder = zlib.decompressobj()
            data = decoder.decompress(value.data, min(2_000_065, 4_000_001 - self.total))
            self.total += len(data)
            if (not decoder.eof or decoder.unused_data or decoder.unconsumed_tail
                    or self.total > 4_000_000 or hashlib.sha1(data).hexdigest() != oid):
                raise PermissionError("git_object_identity_or_budget")
            header, body = data.split(b"\0", 1)
            typ, size = header.split(b" ", 1)
            if int(size) != len(body):
                raise PermissionError("git_object_size_invalid")
            self.cache[oid] = (typ.decode("ascii"), body)
        typ, body = self.cache[oid]
        if typ != kind:
            raise PermissionError("git_object_type_invalid")
        return body

    def file(self, path):
        commit = self.object(self.binding.baseline, "commit")
        if not commit.startswith(b"tree "):
            raise PermissionError("git_commit_tree_unavailable")
        oid = commit.splitlines()[0][5:].decode("ascii")
        parts = path.split("/")
        for index, part in enumerate(parts):
            tree = self.object(oid, "tree")
            cursor, selected = 0, None
            while cursor < len(tree):
                space = tree.index(b" ", cursor)
                end = tree.index(b"\0", space)
                mode, name = tree[cursor:space], tree[space+1:end]
                child = tree[end+1:end+21]
                if len(child) != 20:
                    raise PermissionError("git_tree_invalid")
                if name == part.encode("utf-8"):
                    if selected is not None:
                        raise PermissionError("git_tree_duplicate")
                    selected = (mode, child.hex())
                cursor = end + 21
            if selected is None:
                return None
            mode, oid = selected
            if index < len(parts)-1:
                if mode != b"40000":
                    raise PermissionError("git_tree_path_type")
            elif mode not in {b"100644", b"100755"}:
                raise PermissionError("git_nonregular_base_path")
        return self.object(oid, "blob")


def _review_trace(frame, phase, state, **values):
    """Host diagnostics only; never an execution credential or verdict."""
    import time
    active = frame.runtime._git_review_state(frame.task.id).get("git_active")
    if active is not None and len(active["trace"]) < 256:
        active["trace"].append({"phase": phase, "state": state,
                                "elapsed": time.monotonic() - active["started"], **values})


async def _cleanup_review_child(active, custody, frame):
    """Only terminate the exact handle returned by this Git spawn; never retry Git."""
    process = custody["process"]
    try:
        if process.returncode is None:
            process.kill()
        await asyncio.wait_for(process.wait(), CLEANUP_SECONDS)
        if process.returncode is None:
            raise RuntimeError("git_child_exit_unconfirmed")
        custody["state"] = "reaped"
        _review_trace(frame, "cleanup", "reaped", pid=process.pid,
                      exit_code=process.returncode)
        active["processes"].pop(custody["id"], None)
    except BaseException as error:
        custody["state"] = "unconfirmed"
        _review_trace(frame, "cleanup", "unconfirmed", pid=process.pid,
                      error_type=type(error).__name__)


async def _review_command(binding, view, arguments, refresh, *, issuer, frame, broker):
    """Fixed read commands only. No legacy runner or generic shell capability."""
    import os

    from Sprout.execution.host_acceptance import pin_execution_directory

    await issuer._git_review_record(binding, broker, frame)
    active = issuer._git_review_state(frame.task.id).get("git_active")
    if (active is None or active["binding"] is not binding or active["frame"] is not frame
            or active["owner"] is not asyncio.current_task() or active.get("view") != view):
        raise PermissionError("git_review_execution_frame_denied")
    fixed = ("diff", "--no-index", "--no-color", "--no-ext-diff", "--no-textconv",
             "--find-renames", "--")
    allowed = {("ls-files", "--stage", "-z", "--", *binding.paths),
               *(fixed + pair for pair in (("base", "current"), ("base", "index"),
                                           ("index", "current"), ("empty", "new")))}
    if arguments not in allowed:
        raise PermissionError("git_review_operation_denied")
    _review_trace(frame, "command_authority", "begin", operation=list(arguments))
    await refresh()
    _review_trace(frame, "command_authority", "end")
    env = {k: v for k, v in os.environ.items() if k.upper() in {"SYSTEMROOT", "WINDIR"}}
    control = view / "control"
    env.update(HOME=str(control), USERPROFILE=str(control),
        GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_SYSTEM="control/config",
        GIT_CONFIG_GLOBAL="control/config", GIT_ATTR_NOSYSTEM="1",
        GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0", GIT_NO_LAZY_FETCH="1",
        GIT_LITERAL_PATHSPECS="1", GIT_DIR="control/git", GIT_WORK_TREE=".",
        TMP=str(control), TEMP=str(control))
    argv = (str(binding.executable), "--no-pager", "--no-optional-locks",
        "-c", "core.hooksPath=control/hooks", "-c", "core.longpaths=true",
        "-c", "core.fsmonitor=false",
        "-c", "credential.helper=", "-c", "protocol.allow=never",
        "-c", "core.attributesFile=control/config",
        "-c", "core.pager=", "-c", "gc.auto=0", "-c", "maintenance.auto=false", *arguments)
    from uuid import uuid4

    process = None
    custody = {"id": uuid4().hex, "process": None, "state": "spawning", "abandoned": False}
    active.setdefault("processes", {})[custody["id"]] = custody
    def cleanup():
        if custody.get("cleanup") is None:
            custody["cleanup"] = asyncio.create_task(_cleanup_review_child(active, custody, frame))
        return custody["cleanup"]
    def spawned(done):
        try:
            child = done.result()
        except BaseException:
            # No returned handle is not proof that an OS spawn had no effect.
            custody["state"] = "spawn_unconfirmed"
            return
        custody["process"] = child
        if custody["abandoned"]:
            _review_trace(frame, "spawn", "late_handle", pid=child.pid)
            cleanup()
    _review_trace(frame, "command", "begin", argv=list(argv), cwd=str(view))
    try:
        with pin_execution_directory(view) as pin:
            # Runtime execution-domain checks must stay in the node owner.
            admitted_task, admitted_workspace = await issuer._git_review_record(
                binding, broker, frame)
            await broker._authorize_review(frame, binding.paths, binding.sandbox.root)
            _review_trace(frame, "owner_authority", "confirmed")
            async def spawn():
                from Sprout.orchestration.models import NodeStatus
                from Sprout.task.models import TaskStatus

                # Revocation checks, not a transferable authorization. This
                # child never invokes/rebinds the Runtime's owner-only domain.
                store = issuer._metadata_or_raise()
                current = await store.get_task(admitted_task.id)
                workspace = await store.get_workspace(admitted_workspace.id)
                node = await store.get_execution_node(frame.node.id)
                guard = node.metadata.get("execution_guard", {}) if node is not None else {}
                host = issuer._git_review_state(frame.task.id)
                if (host is None or host.get("git_active") is not active
                        or host.get("git") is not binding
                        or active["binding"] is not binding or active["frame"] is not frame
                        or active["view"] != view or not frame.active
                        or active["owner"].done() or active["owner"].cancelling()
                        or custody["abandoned"] or current != admitted_task
                        or workspace != admitted_workspace
                        or current.status is not TaskStatus.RUNNING
                        or current.delegation_scope.is_expired()
                        or node is None or node.task_id != admitted_task.id
                        or node.status is not NodeStatus.RUNNING
                        or guard.get("state") != "active" or guard.get("owner") != frame.guard_owner
                        or getattr(issuer, "_verification_stopping", False)):
                    raise PermissionError("git_spawn_authority_changed")
                # No await between final live checks, current policy and the
                # fixed native create. Cancellation after create starts is
                # handled only by the existing exact-handle custody/reaper.
                broker._check_review_policy(current, binding.paths, binding.sandbox.root)
                pin.check()
                _review_trace(frame, "spawn_gate", "confirmed",
                              is_node_owner=asyncio.current_task() is active["owner"])
                return await asyncio.create_subprocess_exec(
                    *argv, cwd=str(view), env=env, stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=4096)
            spawn_task = asyncio.create_task(spawn())
            custody["spawn"] = spawn_task
            spawn_task.add_done_callback(spawned)
            process = await asyncio.wait_for(asyncio.shield(spawn_task), TIMEOUT_SECONDS)
            custody["process"] = process
            _review_trace(frame, "spawn", "returned", pid=process.pid)
            out, err = await asyncio.wait_for(capture_bounded(process, MAX_OUTPUT_BYTES),
                                               TIMEOUT_SECONDS)
            if err.strip():
                raise ToolExecutionUnconfirmed("git_review_stderr:" + err.decode(
                    "utf-8", "replace")[:400])
            if process.returncode not in ({0, 1} if arguments[0] == "diff" else {0}):
                raise ToolExecutionUnconfirmed(
                    "git_review_nonzero_exit:" + str(process.returncode) + ":"
                    + err.decode("utf-8", "replace")[:400])
            pin.check()
        import hashlib
        _review_trace(frame, "command", "exited", pid=process.pid, exit_code=process.returncode,
                      stdout_sha256=hashlib.sha256(out).hexdigest(),
                      stderr_sha256=hashlib.sha256(err).hexdigest())
        await refresh()
        active["processes"].pop(custody["id"], None)
        return out, {"argv": list(argv), "cwd": str(view), "exit_code": process.returncode,
                     "stdout": out.decode("utf-8", "strict"),
                     "stderr": err.decode("utf-8", "replace")}
    except BaseException as error:
        _review_trace(frame, "command", "interrupted", error_type=type(error).__name__,
                      pid=getattr(process, "pid", None), handle_acquired=process is not None)
        custody["abandoned"] = True
        process = process or custody["process"]
        if process is not None:
            custody["process"] = process
            pending = cleanup()
            import time

            deadline = time.monotonic() + CLEANUP_SECONDS
            # Repeated caller cancellation cannot cancel the exact child reaper.
            while not pending.done() and time.monotonic() < deadline:
                try:
                    await asyncio.wait({pending}, timeout=max(0, deadline - time.monotonic()))
                except asyncio.CancelledError:
                    continue
        # A pending spawn retains custody and its done callback. A late handle
        # can only be terminated, never used to resume this interrupted command.
        if isinstance(error, asyncio.CancelledError):
            raise
        raise ToolExecutionUnconfirmed(
            "git_review_execution_unconfirmed; pid=" + str(getattr(process, "pid", None))
            + "; " + type(error).__name__ + ":" + str(error)[:500]
            + "; no automatic retry") from None


def _write_view_file(root, relative, data):
    """Exclusive binary export into a newly issued private View, not public I/O."""
    import os

    from Sprout.execution import file_access
    from Sprout.execution.host_acceptance import pin_execution_directory
    from Sprout.workspace.authorized_content import relative_label

    relative = relative_label(relative)
    parent = root
    for part in Path(relative).parts[:-1]:
        with pin_execution_directory(parent):
            try:
                (parent / part).mkdir()
            except FileExistsError:
                pass  # The next native pin rejects links/aliases, never follows them.
        parent /= part
    with pin_execution_directory(parent) as pin:
        if os.name == "nt":
            import msvcrt
            handle = file_access._win_open(parent / Path(relative).name, 0x40000000,
                                            disposition=1)  # CREATE_NEW, never truncate/follow.
            fd = msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_BINARY)
        else:
            fd = os.open(Path(relative).name, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=pin.root_fd)
        try:
            remaining = memoryview(data)
            while remaining:
                written = os.write(fd, remaining)
                if not written:
                    raise OSError("git_view_short_write")
                remaining = remaining[written:]
            pin.check()
        finally:
            os.close(fd)


async def _check_review_files(root, files, refresh, authorize):
    """Name checks precede content checks; never read an unexpected extra file."""
    import hashlib

    from Sprout.execution.file_access import directory_entries

    directories = {"", "base", "index", "current", "new", "empty", "control",
                   "control/hooks", "control/git", "control/git/objects", "control/git/refs"}
    for path in files:
        directories.update(p.as_posix() for p in Path(path).parents if p != Path("."))
    allowed = set(files) | directories
    async def visible(path):
        if path.relative_to(root).as_posix() not in allowed:
            raise PermissionError("git_view_unselected_entry")
        return True
    seen, pending = set(), [root]
    while pending:
        directory = pending.pop()
        for entry in (await directory_entries(root, directory, 256, visible)):
            if entry.is_directory:
                pending.append(root / entry.relative_path)
            else:
                seen.add(entry.relative_path)
    if seen != set(files):
        raise PermissionError("git_view_inventory_changed")
    await refresh()
    for name, data in files.items():
        captured = _snapshot(root, name, authorize, max(len(data), 1))
        if captured is None or captured.digest != hashlib.sha256(data).hexdigest():
            raise PermissionError("git_view_content_changed")


def _pin_installation(path, expected, authorize):
    """Host-pinned installed Git may have distribution hardlinks.

    Project/metadata snapshots still require nlink=1. Windows sharing denies
    writes/deletion through every installation alias while the fd is alive.
    """
    import hashlib
    import os
    from contextlib import contextmanager

    from Sprout.execution import file_access
    from Sprout.execution.file_snapshot import ResourceIdentity
    from Sprout.execution.host_acceptance import pin_execution_directory

    @contextmanager
    def held():
        if os.name != "nt":
            raise PermissionError("git_installation_native_pin_unavailable")
        import msvcrt

        with pin_execution_directory(path.parent):
            handle = file_access._win_open(path, 0x80000080)
            try:
                info = file_access._win_info(handle)
                file_access._win_name(handle, path)
                if info.attributes & 0x10 or info.links < 1:
                    raise PermissionError("git_installation_not_regular")
            except BaseException:
                file_access._kernel().CloseHandle(handle)
                raise
            fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
            try:
                identity = ResourceIdentity.from_stat(os.fstat(fd))
                def check():
                    authorize()
                    if (ResourceIdentity.from_stat(os.fstat(fd)) != identity
                            or os.fstat(fd).st_nlink != info.links or identity.size > 32_000_000):
                        raise PermissionError("git_installation_identity_changed")
                    os.lseek(fd, 0, os.SEEK_SET)
                    value = hashlib.sha256()
                    total = 0
                    while block := os.read(fd, 65536):
                        total += len(block)
                        if total > 32_000_000:
                            raise PermissionError("git_installation_budget")
                        value.update(block)
                    if value.hexdigest() != expected:
                        raise PermissionError("git_installation_changed")
                check()
                yield {"identity": identity, "links": info.links}
                check()
            finally:
                os.close(fd)
    return held()


async def _run_review(binding, issuer, frame, broker):
    """Task-only Git-generated snapshot diffs; the original repository is never executed."""
    import hashlib
    import json
    from contextlib import ExitStack
    from dataclasses import asdict
    from uuid import uuid4

    from Sprout.execution.host_acceptance import pin_execution_directory

    if type(binding) is not _ReviewView:
        raise PermissionError("git_view_binding_required")
    async def refresh():
        _review_trace(frame, "authority", "begin")
        await issuer._git_review_record(binding, broker, frame)
        await broker._authorize_review(frame, binding.paths, binding.sandbox.root)
        _review_trace(frame, "authority", "end")
    def check_selected(paths):
        active = issuer._git_review_state(frame.task.id).get("git_active")
        if (active is None or active["binding"] is not binding or active["frame"] is not frame
                or active["owner"] is not asyncio.current_task()
                or frame.task.delegation_scope.is_expired()
                or getattr(issuer, "_verification_stopping", False)):
            raise PermissionError("git_authority_expired")
        active["authorization_callbacks"] += 1
        broker._check_review_policy(frame.task, paths, binding.sandbox.root)
    def authorize():
        check_selected(binding.paths)
    def for_resource(root, relative):
        # Source files and their exact Host materializations have one owning
        # selected resource. Metadata, installation and control retain the full
        # set check: they cannot acquire authority as arbitrary project paths.
        selected = None
        if root == binding.sandbox.root and relative in binding.paths:
            selected = relative
        else:
            active = issuer._git_review_state(frame.task.id).get("git_active")
            parts = Path(relative).parts
            if (active is not None and root == active.get("view") and len(parts) > 1
                    and parts[0] in {"base", "index", "current", "new"}):
                candidate = Path(*parts[1:]).as_posix()
                if candidate in binding.paths:
                    selected = candidate
        if selected is None:
            return authorize
        return lambda: check_selected((selected,))
    authorize.for_resource = for_resource
    await refresh()
    with ExitStack() as stack:
        pin = stack.enter_context(pin_execution_directory(binding.sandbox.root))
        parent = stack.enter_context(pin_execution_directory(binding.parent))
        if pin.identity != binding.root_identity or parent.identity != binding.parent_identity:
            raise PermissionError("git_view_root_changed")
        installation = ((str(binding.executable), binding.executable_sha256), *binding.chain)
        for name, expected in installation:
            path = Path(name)
            stack.enter_context(_pin_installation(path, expected, authorize))
        meta_pin = stack.enter_context(pin_execution_directory(binding.metadata))
        common_pin = stack.enter_context(pin_execution_directory(binding.common))
        if (meta_pin.identity != binding.metadata_identity
                or common_pin.identity != binding.common_identity):
            raise PermissionError("git_metadata_root_changed")
        index, metadata, reader = _source(binding, authorize)
        objects = _LooseObjects(binding, reader)
        base = {path: objects.file(path) for path in binding.paths}
        current, current_state, total_current = {}, {}, 0
        for path in binding.paths:
            await refresh()
            value = _snapshot(binding.sandbox.root, path, authorize, 1_000_000 - total_current)
            total_current += 0 if value is None else len(value.data)
            current[path] = None if value is None else value.data
            current_state[path] = (None if value is None else {
                "sha256": value.digest, "size": len(value.data),
                "identity": asdict(value.identity)})
        if sum(len(v) for v in (*base.values(), *current.values()) if v is not None) > 2_000_000:
            raise PermissionError("git_content_budget")
        parent.check()
        view = binding.parent / ("git-review-" + uuid4().hex)
        view.mkdir()  # Exclusive, under the pinned Host output parent.
        issuer._git_review_state(frame.task.id)["git_active"]["view"] = view
        directories = ("base", "index", "current", "new", "empty", "control",
                       "control/hooks", "control/git", "control/git/objects", "control/git/refs")
        for path in directories:
            with pin_execution_directory(view):
                (view / path).mkdir()
        payload = {"control/config": b"", "control/git/config": b"",
                   "control/git/HEAD": b"ref: refs/heads/view\n", "control/git/index": index,
                   "control/git/info/attributes": b"* -filter !diff !text !working-tree-encoding\n"}
        for name, data in payload.items():
            _write_view_file(view, name, data)
        await _check_review_files(view, payload, refresh, authorize)
        _, listing = await _review_command(binding, view,
            ("ls-files", "--stage", "-z", "--", *binding.paths), refresh,
            issuer=issuer, frame=frame, broker=broker)
        indexed = {}
        for line in listing["stdout"].split("\0"):
            if not line:
                continue
            fields, path = line.split("\t", 1)
            mode, oid, stage = fields.split()
            if (path not in binding.paths or stage != "0" or path in indexed
                    or mode not in {"100644", "100755"}):
                raise PermissionError("git_unmerged_or_nonregular_index")
            indexed[path] = objects.object(oid, "blob")
        if sum(map(len, indexed.values())) > 1_000_000:
            raise PermissionError("git_index_content_budget")
        for path in binding.paths:
            versions = {"base": base[path], "index": indexed.get(path),
                "current": current[path] if path in indexed else None,
                "new": current[path] if path not in indexed else None}
            for prefix, data in versions.items():
                if data is not None:
                    payload[prefix + "/" + path] = data
                    _write_view_file(view, prefix + "/" + path, data)
        # Empty fixed directories are legitimate too; content inventory contains
        # only authorized copies plus exact Host control files.
        logs, patches = [listing], {}
        async def ready():
            pin.check()
            parent.check()
            await refresh()
            await _check_review_files(view, payload, refresh, authorize)
        for key, left, right in (("tracked", "base", "current"), ("staged", "base", "index"),
                                 ("unstaged", "index", "current"), ("untracked", "empty", "new")):
            await _check_review_files(view, payload, refresh, authorize)
            _, result = await _review_command(binding, view,
                ("diff", "--no-index", "--no-color", "--no-ext-diff", "--no-textconv",
                 "--find-renames", "--", left, right), ready,
                issuer=issuer, frame=frame, broker=broker)
            logs.append(result)
            patches[key] = result["stdout"]
        await _check_review_files(view, payload, refresh, authorize)
        # Re-read exact original resources before publishing, not just export bytes.
        _source(binding, authorize)  # Recheck pairing/HEAD as well as the exact source bytes.
        for name, expected in metadata.items():
            path = Path(name)
            value = _snapshot(path.parent, path.name, authorize, 4_000_000)
            actual = None if value is None else {
                "sha256": value.digest, "identity": asdict(value.identity)}
            if actual != expected:
                raise PermissionError("git_metadata_changed")
        meta_pin.check()
        common_pin.check()
        for path, expected in current_state.items():
            value = _snapshot(binding.sandbox.root, path, authorize)
            actual = None if value is None else {"sha256": value.digest,
                "size": len(value.data), "identity": asdict(value.identity)}
            if actual != expected:
                raise PermissionError("git_worktree_content_changed")
        await ready()
        if sum(len(x["stdout"].encode()) for x in logs) > MAX_OUTPUT_BYTES:
            raise PermissionError("git_total_output_budget")
        evidence = {"status": "available", "source": "git_no_index_managed_view",
            "trust_level": "host_bound_git_generated_partial",
            "task_id": frame.task.id, "node_id": frame.node.id, "owner": frame.guard_owner,
            "workspace_id": frame.workspace.id, "sandbox_id": binding.sandbox.id,
            "worktree": str(binding.sandbox.root), "root_identity": binding.root_identity,
            "baseline": binding.baseline, "metadata": metadata, "current_files": current_state,
            "paths": list(binding.paths), "changed_paths": [p for p in binding.paths
                if (base[p] != indexed.get(p)
                    or p in indexed and indexed[p] != current[p]
                    or p not in indexed and current[p] is not None)],
            "patches": patches, "commands": logs,
            "coverage": {"status": "partial", "content_paths": list(binding.paths),
                "limits": ["no_global_directory_discovery", "file_modes_attributes_not_preserved",
                           "special_index_flags_not_interpreted"],
                "object_storage": "loose_sha1_only",
                "staged_and_unstaged": True, "untracked_and_deleted": True},
            "installation": {"path": str(binding.executable), "sha256": binding.executable_sha256,
                             "launch_chain": dict(binding.chain)},
            "budgets": {"seconds": REVIEW_TOTAL_SECONDS, "command_seconds": TIMEOUT_SECONDS,
                        "elapsed_seconds": __import__("time").monotonic()
                            - issuer._git_review_state(frame.task.id)["git_active"]["started"],
                        "cleanup_seconds": CLEANUP_SECONDS,
                        "output_bytes": MAX_OUTPUT_BYTES, "selected_paths": 64},
            "view": str(view)}
        evidence["sha256"] = hashlib.sha256(
            json.dumps(evidence, sort_keys=True).encode()).hexdigest()
        return GitResult(action="diff", output=json.dumps(evidence))


async def validate_review(binding, issuer, frame, broker, evidence):
    """Publication freshness only. Never reconstruct authority from the evidence."""
    from dataclasses import asdict

    from Sprout.execution.host_acceptance import pin_execution_directory

    await issuer._git_review_record(binding, broker, frame)
    await broker._authorize_review(frame, binding.paths, binding.sandbox.root)
    def authorize():
        broker._check_review_policy(frame.task, binding.paths, binding.sandbox.root)
    with pin_execution_directory(binding.sandbox.root) as pin:
        if pin.identity != binding.root_identity or evidence["baseline"] != binding.baseline:
            raise PermissionError("git_review_publication_root_changed")
        _, source_metadata, _ = _source(binding, authorize)
        for name, expected in evidence["metadata"].items():
            path = Path(name)
            # Linked Worktrees have an exact management pointer in the pinned
            # sandbox, outside both management roots. Only _source's freshly
            # paired pointer receipt may extend those existing origin bounds.
            pointer = (path == binding.sandbox.root / ".git"
                       and name in source_metadata and source_metadata[name] == expected)
            if not (pointer or path.is_relative_to(binding.metadata)
                    or path.is_relative_to(binding.common)):
                raise PermissionError("git_review_metadata_origin")
            value = _snapshot(path.parent, path.name, authorize, 4_000_000)
            actual = None if value is None else {
                "sha256": value.digest, "identity": asdict(value.identity)}
            if actual != expected:
                raise PermissionError("git_review_metadata_stale")
        if set(evidence["current_files"]) != set(binding.paths):
            raise PermissionError("git_review_selection_changed")
        for path, expected in evidence["current_files"].items():
            value = _snapshot(binding.sandbox.root, path, authorize)
            actual = None if value is None else {"sha256": value.digest,
                "size": len(value.data), "identity": asdict(value.identity)}
            if actual != expected:
                raise PermissionError("git_review_file_stale")
        pin.check()
    await issuer._git_review_record(binding, broker, frame)


async def run_review(binding, issuer, frame, broker):
    await issuer._git_review_record(binding, broker, frame)
    from datetime import UTC, datetime

    current = await issuer._metadata_or_raise().get_execution_node(frame.node.id)
    guard = current.metadata.get("execution_guard", {}) if current is not None else {}
    if (current is None or current.task_id != frame.task.id
            or guard.get("owner") != frame.guard_owner or guard.get("state") != "active"
            or not guard.get("started_at") or current.budget.timeout_seconds <= 0):
        raise PermissionError("git_review_node_budget_unproved")
    remaining = current.budget.timeout_seconds - (
        datetime.now(UTC) - datetime.fromisoformat(guard["started_at"])).total_seconds()
    if remaining < REVIEW_TOTAL_SECONDS + CLEANUP_SECONDS:
        raise PermissionError("git_review_node_budget_insufficient:" + str(remaining))
    host = issuer._git_review_state(frame.task.id)
    if host.get("git_active") is not None:
        raise PermissionError("git_review_already_active")
    import time
    from uuid import uuid4
    active = {"binding": binding, "frame": frame, "owner": asyncio.current_task(),
              "review_id": uuid4().hex, "started": time.monotonic(), "trace": [],
              "authorization_callbacks": 0}
    host["git_active"] = active
    try:
        async with asyncio.timeout(REVIEW_TOTAL_SECONDS):
            return await _run_review(binding, issuer, frame, broker)
    except TimeoutError:
        raise ToolExecutionUnconfirmed("git_review_total_timeout; no automatic retry") from None
    finally:
        import json

        from Sprout.execution.host_acceptance import pin_execution_directory
        diagnostic = {"task": frame.task.id, "node": frame.node.id,
            "owner": frame.guard_owner, "review_id": active["review_id"],
            "seconds": time.monotonic() - active["started"], "budget_seconds": REVIEW_TOTAL_SECONDS,
            "command_seconds": TIMEOUT_SECONDS, "cleanup_seconds": CLEANUP_SECONDS,
            "node_remaining_at_admission": remaining,
            "unresolved_processes": [{"id": c["id"], "state": c["state"],
                "pid": getattr(c["process"], "pid", None)}
                for c in active.get("processes", {}).values()],
            "authorization_callbacks": active["authorization_callbacks"], "trace": active["trace"]}
        # Unique Host output under the already pinned registered export parent.
        # Failure to record diagnostics never changes the execution verdict.
        try:
            with pin_execution_directory(binding.parent) as parent:
                if parent.identity != binding.parent_identity:
                    raise PermissionError("git_diagnostic_parent_changed")
                _write_view_file(binding.parent, "git-review-diagnostic-" + active["review_id"]
                                 + ".json", json.dumps(diagnostic).encode())
        except (OSError, ValueError):
            pass
        if host.get("git_active") is active and not active.get("processes"):
            host.pop("git_active", None)
