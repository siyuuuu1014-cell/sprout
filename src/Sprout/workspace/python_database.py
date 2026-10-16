"""Shared SQLAlchemy column syntax and local declarative model extraction.

Only source declarations are described. Annotation type maps, inherited fields,
custom factories, and runtime schema state are deliberately not resolved.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

from Sprout.workspace.python_ast import (
    _boolean_keyword,
    _expanded,
    _expression,
    _keyword,
    _resolve,
    _scalar,
    _string,
)

BASE_FACTORIES = frozenset({
    "sqlalchemy.orm.declarative_base", "sqlalchemy.ext.declarative.declarative_base",
})
DECLARATIVE_BASES = frozenset({
    "sqlalchemy.orm.DeclarativeBase", "sqlalchemy.orm.DeclarativeBaseNoMeta",
})
COLUMN_CONSTRUCTORS = frozenset({"sqlalchemy.Column", "sqlalchemy.orm.mapped_column"})


@dataclass(frozen=True, slots=True)
class DeclarativeModel:
    is_base: bool = False
    details: dict | None = None
    warning: str = ""


def column_declaration(
    call, bindings, ordinal, *, attribute=None, annotation=None, line=0, end_line=0,
):
    """Return explicit column syntax, never infer SQL types from Mapped[T]."""
    arguments = list(call.args) if call is not None else []
    name = attribute
    type_expression = None
    foreign_keys = []
    if call is not None:
        if _expanded(call):
            return None
        explicit_name = _keyword(call, "name")
        if explicit_name is not None:
            name = _string(explicit_name)
            if name is None:
                return None
        if arguments and _string(arguments[0]) is not None:
            if explicit_name is not None:
                return None
            name = _string(arguments.pop(0))
        elif arguments:
            first = arguments[0]
            symbol = _resolve(first.func if isinstance(first, ast.Call) else first, bindings)
            # The first positional value can be a name or a type. Do not
            # invent an attribute-backed column name for an unknown variable.
            if symbol != "sqlalchemy.ForeignKey" and not (
                symbol.startswith("sqlalchemy.") and symbol.rsplit(".", 1)[-1][:1].isupper()
            ):
                return None
        if name is None:
            return None
        type_expression = _expression(_keyword(call, "type_"))
        for value in arguments:
            symbol = _resolve(value.func if isinstance(value, ast.Call) else value, bindings)
            if symbol == "sqlalchemy.ForeignKey" and isinstance(value, ast.Call):
                foreign_keys.append(_string(value.args[0]) if value.args else None)
            elif type_expression is None:
                type_expression = _expression(value)
        primary = _boolean_keyword(call, "primary_key", False)
        nullable = _boolean_keyword(call, "nullable", False if primary is True else None)
    else:
        primary, nullable = False, None
    result = {
        "name": name, "ordinal": ordinal, "type_expression": type_expression,
        "primary_key": primary, "nullable": nullable,
        "unique": _boolean_keyword(call, "unique", False) if call else False,
        "index": _boolean_keyword(call, "index", False) if call else False,
        "default_expression": _expression(_keyword(call, "default")) if call else None,
        "server_default_expression": (
            _expression(_keyword(call, "server_default")) if call else None
        ),
        "foreign_key_targets": foreign_keys,
        "line": call.lineno if call else line,
        "end_line": call.end_lineno if call else end_line,
        "declaration_expression": _expression(call),
    }
    if attribute is not None:
        result.update(attribute=attribute, annotation_expression=_expression(annotation))
        annotation_configuration = annotation is not None and (
            call is None or _resolve(call.func, bindings) == "sqlalchemy.orm.mapped_column"
        )
        result["annotation_configuration_resolved"] = not annotation_configuration
        if annotation_configuration:
            # Annotated/type aliases may carry mapped_column settings. Only
            # explicitly supplied flags are facts until that metadata is parsed.
            for flag in ("primary_key", "unique", "index"):
                if call is None or _keyword(call, flag) is None:
                    result[flag] = None
    return result


def declarative_model(node, bindings, known_bases):
    direct_base = any(_resolve(base, bindings) in DECLARATIVE_BASES for base in node.bases)
    inherits = any(isinstance(base, ast.Name) and base.id in known_bases for base in node.bases)
    if not direct_base and not inherits:
        candidate = any(
            isinstance(member, ast.Assign | ast.AnnAssign)
            and isinstance(member.value, ast.Call)
            and _resolve(member.value.func, bindings) in COLUMN_CONSTRUCTORS
            for member in node.body
        )
        if candidate:
            return DeclarativeModel(warning="SQLAlchemy column class has an unresolved local base")
        return DeclarativeModel()
    if node.decorator_list or node.keywords:
        return DeclarativeModel(warning="Decorated or customized declarative class skipped")
    local = dict(bindings)
    assignments = {}
    columns = {}
    relationships = {}
    unresolved = []
    for member in node.body:
        if isinstance(member, ast.Assign | ast.AnnAssign):
            targets = member.targets if isinstance(member, ast.Assign) else [member.target]
            if len(targets) != 1 or not isinstance(targets[0], ast.Name):
                unresolved.append(_expression(member))
                continue
            name = targets[0].id
            value = member.value
            annotation = member.annotation if isinstance(member, ast.AnnAssign) else None
            if isinstance(member, ast.AnnAssign) and value is None and name in columns:
                # An annotation without a value does not overwrite the earlier
                # Python class attribute or its explicit Column declaration.
                columns[name]["annotation_expression"] = _expression(annotation)
                if columns[name].get("annotation_configuration_resolved") is False:
                    unresolved.append(_expression(member))
                continue
            columns.pop(name, None)
            relationships.pop(name, None)
            assignments[name] = value
            constructor = _resolve(value.func, local) if isinstance(value, ast.Call) else ""
            mapped = (
                isinstance(annotation, ast.Subscript)
                and _resolve(annotation.value, local) == "sqlalchemy.orm.Mapped"
            )
            if constructor in COLUMN_CONSTRUCTORS or (mapped and value is None):
                column = column_declaration(
                    value, local, 0, attribute=name, annotation=annotation,
                    line=member.lineno, end_line=member.end_lineno,
                )
                if column is None:
                    unresolved.append(_expression(member))
                else:
                    columns[name] = column
            elif constructor == "sqlalchemy.orm.relationship":
                if _expanded(value):
                    unresolved.append(_expression(member))
                else:
                    target = value.args[0] if value.args else _keyword(value, "argument")
                    relationships[name] = {
                        "attribute": name, "target": _string(target),
                        "target_expression": _expression(target), "target_resolved": False,
                        "annotation_expression": _expression(annotation),
                        "back_populates": _string(_keyword(value, "back_populates")),
                        "secondary_expression": _expression(_keyword(value, "secondary")),
                        "uselist": _boolean_keyword(value, "uselist", None),
                        "line": member.lineno, "end_line": member.end_lineno,
                        "declaration_expression": _expression(value),
                    }
            elif not name.startswith("__") and (mapped or isinstance(value, ast.Call)):
                unresolved.append(_expression(member))
            local.pop(name, None)
        elif isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            columns.pop(member.name, None)
            relationships.pop(member.name, None)
            local.pop(member.name, None)
            if member.decorator_list:
                unresolved.append(_expression(member))
            if member.name in {"__tablename__", "__table_args__", "__abstract__"}:
                assignments[member.name] = member
        elif not isinstance(member, ast.Pass | ast.Expr):
            # Conditional class bodies can replace both metadata and fields.
            return DeclarativeModel(warning="Conditional declarative class body skipped")
    abstract = assignments.get("__abstract__")
    if abstract is not None:
        flag = _scalar(abstract)
        if flag is True:
            return DeclarativeModel(is_base=True)
        if flag is not False:
            return DeclarativeModel(warning="Dynamic declarative abstract flag skipped")
    table_node = assignments.get("__tablename__")
    if direct_base and table_node is None:
        return DeclarativeModel(is_base=True)
    table = _string(table_node)
    if direct_base or table is None:
        return DeclarativeModel(warning="Declarative model without a literal local table skipped")
    table_args = assignments.get("__table_args__")
    schema, schema_resolved, table_unresolved = _table_schema(table_args)
    unresolved.extend(table_unresolved)
    for ordinal, column in enumerate(columns.values()):
        column["ordinal"] = ordinal
    return DeclarativeModel(details={
        "framework": "sqlalchemy", "declaration_type": "declarative_model",
        "model": node.name, "table": table, "schema": schema,
        "schema_resolved": schema_resolved,
        "base_expressions": [_expression(base) for base in node.bases],
        "columns": list(columns.values()), "relationships": list(relationships.values()),
        "table_arguments_expression": _expression(table_args),
        "unresolved_elements": unresolved, "declared_elements_complete": not unresolved,
        "inherited_elements_resolved": False, "complete": False,
        "scope": "source_declaration",
    })


def _table_schema(node):
    if node is None:
        return None, False, []
    schema = None
    resolved = False
    unresolved = []
    options = node.elts if isinstance(node, ast.Tuple) else [node]
    for option in options:
        if not isinstance(option, ast.Dict):
            unresolved.append(_expression(option))
            continue
        for key, value in zip(option.keys, option.values, strict=True):
            if _string(key) == "schema":
                schema = _string(value)
                resolved = schema is not None or (
                    isinstance(value, ast.Constant) and value.value is None
                )
                if not resolved:
                    unresolved.append(_expression(option))
            else:
                unresolved.append(_expression(option))
    return schema, resolved, unresolved
