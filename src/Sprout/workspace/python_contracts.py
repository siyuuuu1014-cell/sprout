"""Conservative AST-only extraction of explicit Python interface and schema declarations.

These records describe source declarations, not deployed routes or live schemas.
Only imported FastAPI/SQLAlchemy constructors and literal names are recognized.
No project module is imported and no expression is evaluated.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from Sprout.workspace.knowledge import ProjectKnowledgeItem
from Sprout.workspace.models import ResourceKind, ResourceRef, Workspace
from Sprout.workspace.python_ast import (
    _bound_names,
    _expanded,
    _expression,
    _keyword,
    _resolve,
    _scalar,
    _string,
)
from Sprout.workspace.python_database import BASE_FACTORIES, column_declaration, declarative_model
from Sprout.workspace.python_routes import PythonRouteComposer
from Sprout.workspace.source_contracts import source_fact


@dataclass(frozen=True, slots=True)
class PythonContractAnalysis:
    items: tuple[ProjectKnowledgeItem, ...] = ()


class PythonContractAnalyzer:
    max_source_bytes = 1_000_000

    def analyze(
        self, workspace: Workspace, resources: tuple[ResourceRef, ...],
    ) -> PythonContractAnalysis:
        items: list[ProjectKnowledgeItem] = []
        trees = {}
        for resource in resources:
            if resource.kind != ResourceKind.SOURCE or Path(resource.path).suffix != ".py":
                continue
            path = Path(workspace.root) / resource.path
            try:
                path.resolve().relative_to(workspace.root.resolve())
                if path.stat().st_size > self.max_source_bytes:
                    items.append(self._warning(workspace, path, 0, "Source size limit exceeded"))
                    continue
                tree = ast.parse(path.read_bytes(), filename=str(path))
            except (OSError, UnicodeError, SyntaxError, ValueError):
                items.append(self._warning(workspace, path, 0, "Source could not be parsed"))
                continue
            trees[path.resolve()] = tree
            bindings: dict[str, str] = {}
            receivers: dict[str, tuple[str, str | None]] = {}
            orm_bases: set[str] = set()
            for node in tree.body:
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        receivers.pop(alias.asname or alias.name.split(".")[0], None)
                        orm_bases.discard(alias.asname or alias.name.split(".")[0])
                        bindings[alias.asname or alias.name.split(".")[0]] = (
                            alias.name if alias.asname else alias.name.split(".")[0]
                        )
                elif isinstance(node, ast.ImportFrom):
                    for alias in node.names:
                        if alias.name == "*":
                            bindings.clear()
                            receivers.clear()
                            orm_bases.clear()
                            continue
                        receivers.pop(alias.asname or alias.name, None)
                        orm_bases.discard(alias.asname or alias.name)
                        bindings.pop(alias.asname or alias.name, None)
                        if node.module and not node.level:
                            bindings[alias.asname or alias.name] = f"{node.module}.{alias.name}"
                elif isinstance(node, ast.Assign | ast.AnnAssign):
                    value = node.value
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    constructor = ""
                    if isinstance(value, ast.Call):
                        constructor = _resolve(value.func, bindings)
                        if constructor == "sqlalchemy.Table":
                            items.append(self._table(workspace, path, value, bindings))
                        if constructor in BASE_FACTORIES and (value.args or value.keywords):
                            items.append(self._warning(
                                workspace, path, node.lineno, "Customized declarative base skipped",
                            ))
                        for target in targets:
                            if isinstance(target, ast.Name) and constructor in {
                                "fastapi.FastAPI", "fastapi.APIRouter",
                            }:
                                prefix = _keyword(value, "prefix")
                                receivers[target.id] = (
                                    constructor,
                                    "" if prefix is None and not _expanded(value)
                                    else _string(prefix),
                                )
                    for target in targets:
                        for name in _assigned_names(target):
                            bindings.pop(name, None)
                            orm_bases.discard(name)
                            if (
                                isinstance(target, ast.Name) and constructor in BASE_FACTORIES
                                and not value.args and not value.keywords
                            ):
                                orm_bases.add(name)
                            if not (isinstance(value, ast.Call) and _resolve(value.func, bindings)
                                    in {"fastapi.FastAPI", "fastapi.APIRouter"}):
                                receivers.pop(name, None)
                elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                    items.extend(self._routes(workspace, path, node, receivers))
                    bindings.pop(node.name, None)
                    receivers.pop(node.name, None)
                    orm_bases.discard(node.name)
                elif isinstance(node, ast.ClassDef):
                    model = declarative_model(node, bindings, orm_bases)
                    if model.details is not None:
                        details = model.details
                        items.append(self._fact(
                            workspace, path, node.lineno, "database_table",
                            f"Declared SQLAlchemy model: {node.name} -> {details['table']}",
                            details,
                        ))
                    if model.warning:
                        items.append(self._warning(workspace, path, node.lineno, model.warning))
                    orm_bases.discard(node.name)
                    if model.is_base:
                        orm_bases.add(node.name)
                    bindings.pop(node.name, None)
                    receivers.pop(node.name, None)
                else:
                    # Branches, augmented assignment and deletion may replace
                    # a receiver. Do not guess the environment after them.
                    for name in _bound_names(node):
                        if name is None:
                            bindings.clear()
                            receivers.clear()
                            orm_bases.clear()
                        else:
                            bindings.pop(name, None)
                            receivers.pop(name, None)
                            orm_bases.discard(name)
        items.extend(PythonRouteComposer().compose(workspace, trees, items))
        return PythonContractAnalysis(tuple(items))

    def _routes(self, workspace, path, function, receivers):
        items = []
        for decorator in function.decorator_list:
            if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                continue
            receiver = decorator.func.value
            if not isinstance(receiver, ast.Name) or receiver.id not in receivers:
                continue
            method = decorator.func.attr
            if method not in {"get", "post", "put", "patch", "delete", "head", "options",
                              "trace", "api_route", "websocket"}:
                continue
            route = _string(decorator.args[0] if decorator.args else _keyword(decorator, "path"))
            framework, prefix = receivers[receiver.id]
            methods = [method.upper()]
            if method == "api_route":
                raw = _keyword(decorator, "methods")
                methods = ["GET"] if raw is None else _strings(raw)
            if route is None or prefix is None or methods is None or _expanded(decorator):
                items.append(self._warning(
                    workspace, path, decorator.lineno, "Dynamic FastAPI route declaration skipped",
                ))
                continue
            for verb in sorted({method.upper() for method in methods}):
                details = {
                    "framework": "fastapi", "receiver": receiver.id,
                    "receiver_type": framework, "method": verb,
                    "declared_path": prefix + route, "handler": function.name,
                    "parameters": _parameters(function),
                    "return_annotation": _expression(function.returns),
                    "response_model_expression": _expression(_keyword(decorator, "response_model")),
                    "status_code": _scalar(_keyword(decorator, "status_code")),
                    "scope": "source_declaration", "mount_path_resolved": False,
                }
                items.append(self._fact(
                    workspace, path, decorator.lineno, "interface_endpoint",
                    f"Declared FastAPI endpoint: {verb} {prefix + route} -> {function.name}",
                    details,
                ))
        return items

    def _table(self, workspace, path, call, bindings):
        name = _string(call.args[0]) if call.args else None
        schema_node = _keyword(call, "schema")
        schema = _string(schema_node) if schema_node is not None else None
        if name is None or (schema_node is not None and schema is None) or _expanded(call):
            return self._warning(workspace, path, call.lineno, "Dynamic SQLAlchemy table skipped")
        columns = []
        unresolved = []
        for argument in call.args[2:]:
            if not isinstance(argument, ast.Call) or _resolve(argument.func, bindings) != (
                "sqlalchemy.Column"
            ):
                unresolved.append(_expression(argument))
                continue
            column = column_declaration(argument, bindings, len(columns))
            if column is None:
                unresolved.append(_expression(argument))
                continue
            columns.append(column)
        details = {
            "framework": "sqlalchemy", "declaration_type": "core_table",
            "table": name, "schema": schema, "columns": columns,
            "unresolved_elements": unresolved, "complete": not unresolved,
            "scope": "source_declaration",
        }
        return self._fact(
            workspace, path, call.lineno, "database_table",
            f"Declared SQLAlchemy table: {schema + '.' if schema else ''}{name}", details,
        )

    @staticmethod
    def _fact(workspace, path, line, kind, statement, details):
        return source_fact(workspace, path, line, kind, statement, details)

    def _warning(self, workspace, path, line, reason):
        return self._fact(workspace, path, line, "analysis_warning", reason, {"reason": reason})


def _strings(node):
    if not isinstance(node, ast.List | ast.Tuple):
        return None
    values = [_string(item) for item in node.elts]
    return values if all(isinstance(value, str) for value in values) else None


def _assigned_names(node):
    return [item.id for item in ast.walk(node) if isinstance(item, ast.Name)]


def _parameters(function):
    positional = [*function.args.posonlyargs, *function.args.args]
    defaults = [None] * (len(positional) - len(function.args.defaults)) + function.args.defaults
    result = [
        {"name": arg.arg, "annotation_expression": _expression(arg.annotation),
         "default_expression": _expression(default)}
        for arg, default in zip(positional, defaults, strict=True)
    ]
    result.extend(
        {"name": arg.arg, "annotation_expression": _expression(arg.annotation),
         "default_expression": _expression(default)}
        for arg, default in zip(function.args.kwonlyargs, function.args.kw_defaults, strict=True)
    )
    return result
