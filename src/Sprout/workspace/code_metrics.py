"""Compute Python source metrics from a captured, authorized entry."""

import ast
import io
import tokenize

from Sprout.tools.result import ToolResult
from Sprout.workspace.authorized_content import AuthorizedEntry


def python_metrics(entry: AuthorizedEntry) -> ToolResult:
    """Count physical lines; inline comments remain code lines."""
    code = set()
    comments = set()
    try:
        tokens = tokenize.tokenize(io.BytesIO(entry.data).readline)
        for token in tokens:
            if token.type == tokenize.COMMENT:
                comments.add(token.start[0])
            elif token.type not in {
                tokenize.ENCODING, tokenize.INDENT, tokenize.DEDENT,
                tokenize.NL, tokenize.NEWLINE, tokenize.ENDMARKER,
            }:
                code.update(range(token.start[0], token.end[0] + 1))
        tree = ast.parse(entry.data)
    except (SyntaxError, UnicodeError, tokenize.TokenError) as exc:
        raise PermissionError("code_metrics_invalid_python") from exc
    total = len(entry.data.splitlines())
    comment_only = len(comments - code)
    metrics = {
        "path": entry.relative_path,
        "sha256": entry.digest,
        "total_lines": total,
        "code_lines": len(code),
        "comment_lines": comment_only,
        "blank_lines": total - len(code) - comment_only,
        "functions": sum(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                         for node in ast.walk(tree)),
    }
    return ToolResult.success(
        f"{entry.relative_path}\n"
        f"总行数 / Total lines: {total}\n"
        f"代码行数 / Code lines: {len(code)}\n"
        f"纯注释行数 / Comment-only lines: {comment_only}\n"
        f"空行 / Blank lines: {metrics['blank_lines']}\n"
        f"函数 / Functions: {metrics['functions']}",
        data=metrics,
    )
