"""Bounded, non-executing SQL declaration inventory, not a dialect validator."""

import re
from pathlib import Path

from Sprout.workspace.models import ResourceKind
from Sprout.workspace.source_contracts import source_fact

_TOKEN = re.compile(
    r"(?P<space>\s+|--[^\n]*|/\*.*?\*/)"
    r"|(?P<string>'(?:''|[^'])*'|\$(?P<tag>[A-Za-z_][A-Za-z_0-9]*|)\$.*?\$(?P=tag)\$)"
    r'|(?P<quoted>"(?:""|[^"])*"|`(?:``|[^`])*`|\[[^\]]*\])'
    r"|(?P<word>[A-Za-z_][A-Za-z_0-9$]*)|(?P<symbol>.)", re.S,
)
_CREATE = re.compile(
    r"CREATE\s+(?:(?:TEMP|TEMPORARY|UNLOGGED)\s+)?TABLE\s+"
    r"(?:IF\s+NOT\s+EXISTS\s+)?", re.I,
)


def _tokens(source):
    result = []
    for match in _TOKEN.finditer(source):
        if match.lastgroup == "space":
            if match.group().startswith("/*") and "/*" in match.group()[2:]:
                raise ValueError("Nested SQL comments are unsupported")
            continue
        token = match.group()
        if match.lastgroup == "string" and token.startswith("'") and "\\" in token:
            raise ValueError("Dialect-specific string escapes are unsupported")
        if match.lastgroup == "symbol" and source[match.start():].startswith("/*"):
            raise ValueError("Unterminated SQL comment")
        if match.lastgroup == "symbol" and token in "'\"`[$":
            raise ValueError("Unsupported or unterminated SQL quoting")
        result.append((token, match.start(), match.end(), match.lastgroup))
        if len(result) > 100_000:
            raise ValueError("SQL token limit exceeded")
    return result


def _identifier(token):
    value, _, _, kind = token
    if kind == "word":
        return value
    if kind != "quoted":
        raise ValueError("Expected a literal SQL identifier")
    return value[1:-1].replace(value[0] * 2, value[0])


class SqlContractAnalyzer:
    """Record CREATE TABLE elements verbatim; never infer migration results."""

    def analyze(self, workspace, resources):
        items = []
        for resource in resources:
            if resource.kind != ResourceKind.SOURCE or Path(resource.path).suffix.lower() != ".sql":
                continue
            path = Path(workspace.root) / resource.path
            try:
                path.resolve().relative_to(workspace.root.resolve())
                if path.stat().st_size > 1_000_000:
                    raise ValueError("SQL source size limit exceeded")
                source = path.read_text(encoding="utf-8-sig")
                tokens = _tokens(source)
                statements, start, depth = [], 0, 0
                for index, token in enumerate(tokens):
                    if token[3] == "symbol":
                        depth += (token[0] == "(") - (token[0] == ")")
                        if depth < 0:
                            raise ValueError("Unbalanced SQL parentheses")
                        if token[0] == ";" and depth == 0:
                            statements.append(tokens[start:index])
                            start = index + 1
                if depth:
                    raise ValueError("Unbalanced SQL parentheses")
                statements.append(tokens[start:])
                for statement in statements:
                    if not statement:
                        continue
                    first = statement[0]
                    line = source.count("\n", 0, first[1]) + 1
                    head = " ".join(t[0] for t in statement)
                    match = _CREATE.match(head)
                    if match is None:
                        if first[3] == "word" and first[0].upper() in {"CREATE", "ALTER", "DROP"}:
                            items.append(self._warning(
                                workspace, path, line, "Unsupported SQL DDL declaration",
                            ))
                        continue
                    try:
                        details = self._table(source, statement, match)
                        items.append(source_fact(
                            workspace, path, line, "database_table",
                            f"Declared SQL table: {details['table']}", details,
                        ))
                    except (ValueError, IndexError) as exc:
                        items.append(self._warning(
                            workspace, path, line, str(exc) or "Incomplete SQL declaration",
                        ))
            except (OSError, UnicodeError, ValueError) as exc:
                items.append(self._warning(workspace, path, 0, str(exc)))
        return tuple(items)

    @staticmethod
    def _warning(workspace, path, line, reason):
        return source_fact(workspace, path, line, "analysis_warning", reason, {"reason": reason})

    @staticmethod
    def _table(source, tokens, match):
        # Prefix keywords are tokenized separately, including comments between them.
        count = len(match.group().split())
        index = count
        names = [_identifier(tokens[index])]
        index += 1
        while index < len(tokens) and tokens[index][0] == ".":
            names.append(_identifier(tokens[index + 1]))
            index += 2
        if index >= len(tokens) or tokens[index][0] != "(":
            raise ValueError("CREATE TABLE without an explicit column body")
        depth, start, elements = 1, index + 1, []
        index += 1
        while index < len(tokens):
            token = tokens[index]
            if token[3] == "symbol":
                depth += (token[0] == "(") - (token[0] == ")")
                if depth == 0 or (depth == 1 and token[0] == ","):
                    elements.append(tokens[start:index])
                    start = index + 1
                if depth == 0:
                    break
            index += 1
        columns, constraints = [], []
        for element in elements:
            if not element:
                raise ValueError("Empty SQL table element")
            text = source[element[0][1]:element[-1][2]]
            if element[0][3] == "word" and element[0][0].upper() in {
                "CONSTRAINT", "PRIMARY", "FOREIGN", "UNIQUE", "CHECK", "EXCLUDE",
            }:
                constraints.append(text)
            else:
                if element[0][3] == "word" and element[0][0].upper() in {"LIKE", "PERIOD"}:
                    raise ValueError("Unsupported SQL table element")
                name = _identifier(element[0])
                columns.append({"name": name, "declaration_expression": text,
                                "line": source.count("\n", 0, element[0][1]) + 1})
        return {
            "framework": "sql", "declaration_type": "create_table",
            "table": names[-1], "qualifiers": names[:-1], "columns": columns,
            "constraint_expressions": constraints,
            "options_expression": source[tokens[index][2]:tokens[-1][2]].strip(),
            "scope": "source_declaration", "dialect": "unspecified",
            "complete": False, "migration_state_resolved": False,
        }
