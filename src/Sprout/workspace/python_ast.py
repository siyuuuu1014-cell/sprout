"""Shared syntax helpers for conservative Python source analyzers."""

from __future__ import annotations

import ast


def _resolve(node, bindings):
    if isinstance(node, ast.Name):
        return bindings.get(node.id, "")
    if isinstance(node, ast.Attribute):
        parent = _resolve(node.value, bindings)
        return f"{parent}.{node.attr}" if parent else ""
    return ""


def _keyword(call, name):
    return next((keyword.value for keyword in call.keywords if keyword.arg == name), None)


def _expanded(call):
    return any(keyword.arg is None for keyword in call.keywords) or any(
        isinstance(argument, ast.Starred) for argument in call.args
    )


def _string(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _scalar(node):
    return node.value if isinstance(node, ast.Constant) else None


def _boolean_keyword(call, name, default):
    node = _keyword(call, name)
    value = _scalar(node)
    return default if node is None else value if isinstance(value, bool) else None


def _expression(node):
    return ast.unparse(node) if node is not None else None


def _bound_names(node):
    """Possible bindings in a statement, without descending into nested scopes."""
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store | ast.Del):
        yield node.id
    elif isinstance(node, ast.Import):
        for alias in node.names:
            yield alias.asname or alias.name.split(".")[0]
    elif isinstance(node, ast.ImportFrom):
        for alias in node.names:
            yield None if alias.name == "*" else alias.asname or alias.name
    elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        yield node.name
    else:
        for child in ast.iter_child_nodes(node):
            yield from _bound_names(child)
