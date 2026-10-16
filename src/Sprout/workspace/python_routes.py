"""Conservative static composition of explicit FastAPI router declarations.

No imports or calls are executed. Only local module exports, literal constructors,
and top-level include_router statements participate. Later child mutations make
earlier inclusions unresolved: FastAPI versions differ in snapshot/live behavior.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field, replace
from hashlib import sha256
from pathlib import Path

from Sprout.workspace.python_ast import _bound_names, _expanded, _keyword, _string
from Sprout.workspace.source_contracts import source_fact


@dataclass(eq=False)
class _Router:
    path: Path
    name: str
    line: int
    prefix: str
    app: bool
    routes: list = field(default_factory=list)
    uncertain: bool = False
    parents: list = field(default_factory=list)


@dataclass(frozen=True)
class _Module:
    name: str


class PythonRouteComposer:
    """One instance per analysis; all state is discardable source-derived data."""

    def compose(self, workspace, trees, declarations):
        self.workspace = workspace
        self.trees = trees
        self.modules = {}
        self.names = {}
        self.exports = {}
        self.active = set()
        self.warnings = []
        self.declarations = {}
        self.warned_locations = {
            (item.details.get("path"), item.details.get("line"))
            for item in declarations if item.kind == "analysis_warning"
        }
        for item in declarations:
            if item.kind == "interface_endpoint":
                key = (item.details["path"], item.details["line"])
                self.declarations.setdefault(key, []).append(item)
        for path in sorted(trees):
            relative = path.relative_to(workspace.root.resolve())
            parts = list(relative.with_suffix("").parts)
            if parts[-1] == "__init__":
                parts.pop()
            variants = [parts, parts[1:]] if parts and parts[0] == "src" else [parts]
            self.names[path] = ".".join(variants[-1])
            for variant in variants:
                name = ".".join(variant)
                if name:
                    self.modules.setdefault(name, set()).add(path)
        for path in sorted(trees):
            self._module(path)
        mounted = []
        seen = set()
        for env in self.exports.values():
            for obj in env.values():
                if not isinstance(obj, _Router) or not obj.app or obj.uncertain or id(obj) in seen:
                    continue
                seen.add(id(obj))
                for declaration, route, chain in obj.routes:
                    if not chain:
                        continue  # Direct app decorators remain ordinary declarations.
                    signature = repr((declaration.id, obj.path.as_posix(), obj.line, chain))
                    stamp = sha256(signature.encode()).hexdigest()[:16]
                    item = source_fact(
                        workspace, obj.path, obj.line, "interface_mounted_endpoint",
                        f"Declared mounted FastAPI endpoint: {declaration.details['method']} "
                        f"{route} -> {obj.name} [{stamp}]",
                        {**declaration.details, "path": obj.path.as_posix(), "line": obj.line,
                         "declaration_id": declaration.id, "application": obj.name,
                         "mounted_path": route, "mount_chain": chain,
                         "mount_path_resolved": True, "scope": "source_composition",
                         "inclusion_semantics": "order_independent_subset",
                         "deployed": False, "complete": False},
                    )
                    evidence = tuple(dict.fromkeys([
                        *declaration.evidence_ids, *(edge["path"] for edge in chain),
                    ]))
                    mounted.append(replace(item, evidence_ids=evidence))
        return tuple([*mounted, *self.warnings])

    def _warning(self, path, line, reason):
        if (path.as_posix(), line) in self.warned_locations:
            return
        self.warned_locations.add((path.as_posix(), line))
        self.warnings.append(source_fact(
            self.workspace, path, line, "analysis_warning", reason, {"reason": reason},
        ))

    def _local(self, name):
        candidates = self.modules.get(name, set())
        return next(iter(candidates)) if len(candidates) == 1 else None

    def _load_module(self, name):
        parts = name.split(".")
        for length in range(1, len(parts)):
            package = self._local(".".join(parts[:length]))
            if package is not None and package.name == "__init__.py":
                self._module(package)
        path = self._local(name)
        return self._module(path) if path is not None else {}

    def _value(self, node, env):
        if isinstance(node, ast.Name):
            return env.get(node.id)
        if isinstance(node, ast.Attribute):
            parent = self._value(node.value, env)
            if isinstance(parent, _Module):
                exports = self._load_module(parent.name)
                if node.attr in exports:
                    return exports[node.attr]
                submodule = parent.name + "." + node.attr
                return _Module(submodule) if self._local(submodule) is not None else None
            if isinstance(parent, str):
                return parent + "." + node.attr
        return None

    def _module(self, path):
        if path in self.exports:
            return self.exports[path]
        if path in self.active or len(self.active) >= 64:
            self._warning(path, 0, "Cyclic or excessive module depth; router export unresolved")
            return {}
        self.active.add(path)
        env = {}
        for node in self.trees[path].body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.name if alias.asname else alias.name.split(".")[0]
                    env[alias.asname or name] = (
                        "fastapi" if name == "fastapi" and "fastapi" not in self.modules
                        else _Module(name)
                    )
            elif isinstance(node, ast.ImportFrom):
                package = self.names[path].split(".")
                if path.name != "__init__.py":
                    package.pop()
                if node.level:
                    if node.level > len(package):
                        module = "__unresolved_relative_import__"
                    else:
                        package = package[:len(package) - node.level + 1]
                        module = ".".join([*package, *([node.module] if node.module else [])])
                else:
                    module = node.module or ""
                for alias in node.names:
                    if alias.name == "*":
                        self._invalidate(env)
                        env.clear()
                        continue
                    name = alias.asname or alias.name
                    if module == "fastapi" and not node.level and module not in self.modules:
                        env[name] = module + "." + alias.name
                    else:
                        env[name] = self._value(
                            ast.Attribute(value=ast.Name(id="__module"), attr=alias.name),
                            {"__module": _Module(module)},
                        )
            elif isinstance(node, ast.Assign | ast.AnnAssign):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                value = self._value(node.value, env)
                if isinstance(node.value, ast.Call):
                    constructor = self._value(node.value.func, env)
                    if constructor not in {"fastapi.FastAPI", "fastapi.APIRouter"}:
                        self._call(path, node.value, env)
                    if constructor in {"fastapi.FastAPI", "fastapi.APIRouter"}:
                        prefix_node = _keyword(node.value, "prefix")
                        prefix = "" if prefix_node is None else _string(prefix_node)
                        uncertain = (
                            _expanded(node.value) or bool(node.value.args)
                            or not _valid_prefix(prefix)
                        )
                        # FastAPI does not use APIRouter's constructor prefix.
                        is_app = constructor == "fastapi.FastAPI"
                        if is_app:
                            uncertain |= prefix_node is not None
                        if _keyword(node.value, "routes") is not None:
                            uncertain = True
                        if _keyword(node.value, "route_class") is not None:
                            uncertain = True
                        value = _Router(
                            path, getattr(targets[0], "id", ""), node.lineno,
                            prefix or "", is_app, uncertain=uncertain,
                        )
                        if uncertain:
                            self._warning(path, node.lineno, "Router constructor unresolved")
                for target in targets:
                    if isinstance(target, ast.Name):
                        env[target.id] = value
                    else:
                        affected = any(isinstance(obj, _Router) for obj in env.values())
                        if affected:
                            self._invalidate(env)
                        if isinstance(target, ast.Attribute):
                            owner = self._value(target.value, env)
                            if isinstance(owner, _Router):
                                self._uncertain(owner)
                                affected = True
                            elif isinstance(owner, _Module):
                                owner_path = self._local(owner.name)
                                if owner_path is not None:
                                    exports = self._module(owner_path)
                                    affected |= any(isinstance(obj, _Router)
                                                    for obj in exports.values())
                                    self._invalidate(exports)
                        if affected:
                            self._warning(
                                path, node.lineno, "Non-name assignment unresolved for routers",
                            )
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                for decorator in node.decorator_list:
                    if not isinstance(decorator, ast.Call) or not isinstance(
                        decorator.func, ast.Attribute,
                    ):
                        continue
                    obj = self._value(decorator.func.value, env)
                    if not isinstance(obj, _Router):
                        continue
                    facts = self.declarations.get((path.as_posix(), decorator.lineno), [])
                    if facts:
                        if any(not fact.details["declared_path"].startswith("/") for fact in facts):
                            self._uncertain(obj)
                            self._warning(path, decorator.lineno, "Router path unresolved")
                        else:
                            self._changed(obj, path, decorator.lineno)
                            obj.routes.extend(
                                (fact, fact.details["declared_path"], []) for fact in facts
                            )
                    else:
                        self._uncertain(obj)
                        self._warning(path, decorator.lineno, "Router decorator unresolved")
                env.pop(node.name, None)
            elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                self._call(path, node.value, env)
            elif isinstance(node, ast.ClassDef):
                env.pop(node.name, None)
            else:
                for child in ast.walk(node):
                    if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
                        obj = self._value(child.func.value, env)
                        if isinstance(obj, _Router):
                            self._uncertain(obj)
                            self._warning(
                                path, child.lineno, "Conditional router mutation unresolved",
                            )
                for name in _bound_names(node):
                    if name is None:
                        self._invalidate(env)
                        env.clear()
                    else:
                        env.pop(name, None)
        self.active.remove(path)
        self.exports[path] = env
        return env

    def _invalidate(self, env):
        for value in env.values():
            if isinstance(value, _Router):
                self._uncertain(value)

    @staticmethod
    def _uncertain(obj):
        pending, seen = [obj], set()
        while pending:
            current = pending.pop()
            if id(current) in seen:
                continue
            seen.add(id(current))
            current.uncertain = True
            pending.extend(current.parents)

    def _changed(self, obj, path, line):
        if obj.parents:
            for parent in obj.parents:
                self._uncertain(parent)
            self._warning(
                path, line,
                "Router changed after inclusion; framework-version semantics unresolved",
            )

    def _call(self, path, call, env):
        known_include = isinstance(call.func, ast.Attribute) and call.func.attr == "include_router"
        if not known_include:
            for argument in [*call.args, *(kw.value for kw in call.keywords)]:
                value = self._value(argument, env)
                if isinstance(value, _Router):
                    self._uncertain(value)
                    self._warning(path, call.lineno, "Router passed to an unresolved call")
        if not isinstance(call.func, ast.Attribute):
            return
        parent = self._value(call.func.value, env)
        if not isinstance(parent, _Router):
            if call.func.attr == "include_router":
                self._warning(path, call.lineno, "Router include receiver unresolved")
            return
        child_node = call.args[0] if call.args else _keyword(call, "router")
        child = self._value(child_node, env)
        prefix_node = _keyword(call, "prefix")
        prefix = "" if prefix_node is None else _string(prefix_node)
        if (call.func.attr != "include_router" or _expanded(call) or len(call.args) > 1
                or not isinstance(child, _Router) or child.app or child.uncertain
                or parent is child or not _valid_prefix(prefix)):
            self._uncertain(parent)
            self._warning(path, call.lineno, "Router mutation or include target/prefix unresolved")
            return
        if len(parent.routes) + len(child.routes) > 10_000:
            self._uncertain(parent)
            self._warning(path, call.lineno, "Router composition route limit exceeded")
            return
        edge = {"path": path.as_posix(), "line": call.lineno, "prefix": prefix,
                "receiver": parent.name, "router": child.name}
        self._changed(parent, path, call.lineno)
        parent.routes.extend(
            (fact, parent.prefix + prefix + route, [*chain, edge])
            for fact, route, chain in list(child.routes)
        )
        if parent not in child.parents:
            child.parents.append(parent)


def _valid_prefix(prefix):
    return isinstance(prefix, str) and (
        not prefix or (prefix.startswith("/") and not prefix.endswith("/"))
    )
