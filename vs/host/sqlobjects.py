"""Generic SQL-object files: parse, lint and render (the pure part of the model deployer).

A directory holds one object per file, `NNN_<object>.sql`: a reference table (its `CREATE` plus an
`INSERT` of its rows), a function (`CREATE FUNCTION ... /` plus `COMMENT ON FUNCTION`) or a view.
`load` parses the files in name order, `lint` returns every problem as
`"file: object: rule: detail"` (none stops at the first), and `render` fills the namespace
placeholders (`{MODEL_SCHEMA}`, `{CORE_SCHEMA}`, `{META_SCHEMA}`, ...). Nothing here touches a
database.

Conventions the parser relies on:

* Statements end with `;` outside strings and comments; a function or script ends with a line that
  holds only `/`. `--` and `/* */` comments are removed from the statements.
* A reference is `SCHEMA.NAME` after `FROM`, `JOIN` or `INTO`, a qualified function call, or any
  `{PLACEHOLDER}.NAME`. A bare qualifier counts as a schema only when it is an upper-case
  identifier, so lower-case aliases (`g."SEASON"`) never look like schemas.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from vs.host.metaddl import fill_placeholders, placeholder_values
from vs.host.namespace import Namespace

MODEL_SCHEMA_PLACEHOLDER = "{MODEL_SCHEMA}"
MODEL_ALLOWED_REFS = (
    "{MODEL_SCHEMA}.*",
    "{CORE_SCHEMA}.*",
    "{META_SCHEMA}.LOAD_PLAN",
    "{META_SCHEMA}.LOAD_PLAN_GAPS",
    "{META_SCHEMA}.LOAD_SLICES",
)
MAX_COMMENT_LENGTH = 2000
GRAIN_PREFIX = "One row per "
RESERVED_KEYWORDS_FILE = (
    Path(__file__).resolve().parents[1] / "gen" / "exasol_reserved_keywords.txt"
)

_FILE_NAME = re.compile(r"(\d{3})_([a-z][a-z0-9_]*)\.sql")
_DDL_NAME = re.compile(r"[A-Z][A-Z0-9_]*")
_PLACEHOLDER = re.compile(r"\{[A-Z][A-Z_]*\}")
_QUOTED = r'"(?:[^"]|"")*"'
_IDENT = r"\w+"
_NAME = rf"(?:{_QUOTED}|{_IDENT})"
_QNAME = rf"(?:(?P<schema>\{{[A-Z][A-Z_]*\}}|{_NAME})\s*\.\s*)?(?P<name>{_NAME})"
_QNAME_RE = re.compile(_QNAME)
_COLUMN_NAME = re.compile(rf"\s*(?P<name>{_NAME})")
_CREATE_HEAD = re.compile(
    r"CREATE\s+(?P<replace>OR\s+REPLACE\s+)?(?P<force>FORCE\s+)?"
    r"(?:(?:LUA|PYTHON3?|JAVA|R)\s+)?(?:(?:SCALAR|SET|ADAPTER)\s+)?"
    r"(?P<kind>TABLE|VIEW|FUNCTION|SCRIPT)\b\s*"
)
_INSERT_HEAD = re.compile(r"INSERT\s+INTO\s+")
_COMMENT_ON_HEAD = re.compile(r"COMMENT\s+ON\s+(?P<kind>TABLE|COLUMN|FUNCTION|SCRIPT)\s+")
_COMMENT_ON_LINE = re.compile(r"^[ \t]*COMMENT\s+ON\b", re.MULTILINE)
_COMMENT_IS = re.compile(r"COMMENT\s+IS\s+(?P<text>'x*')")
_IS_TEXT = re.compile(r"\s*IS\s+(?P<text>'x*')\s*$")
_TRAILING_COMMENT = re.compile(r"COMMENT\s+IS\s+(?P<text>'x*')\s*$")
_SLASH_LINE = re.compile(r"^[ \t]*/[ \t]*$", re.MULTILINE)
_FIRST_WORD = re.compile(r"\s*(?P<word>[A-Za-z_]+)")
_REFERENCE_PLACEHOLDER = re.compile(
    rf"(?<![\w\"}}.])(?P<schema>\{{[A-Z][A-Z_]*\}})\s*\.\s*(?P<name>{_NAME})"
)
_TABLE_LIST_START = re.compile(r"\b(?:FROM|JOIN|INTO)\s+")
_TABLE_ITEM = re.compile(rf"\s*{_QNAME}(?:\s+(?:AS\s+)?(?P<alias>{_IDENT}))?\s*(?P<more>,)?")
_NOT_AN_ALIAS = frozenset(
    {
        "WHERE", "GROUP", "ORDER", "JOIN", "ON", "LEFT", "RIGHT", "INNER", "FULL", "CROSS",
        "OUTER", "QUALIFY", "HAVING", "UNION", "INTERSECT", "MINUS", "LIMIT", "USING", "VALUES",
        "SELECT", "SET", "WITH",
    }
)  # fmt: skip
_REFERENCE_CALL = re.compile(rf"(?<![\w\"}}.])(?P<schema>{_NAME})\s*\.\s*(?P<name>{_NAME})\s*\(")
_CONSTRAINT_WORDS = frozenset(
    {"CONSTRAINT", "PRIMARY", "FOREIGN", "UNIQUE", "DISTRIBUTE", "PARTITION"}
)


@dataclass(frozen=True)
class Column:
    """A declared column of a table or view; `quoted` is True if it was written in quotes."""

    name: str
    comment: str | None
    quoted: bool = False


@dataclass(frozen=True)
class SqlObject:
    """One object file: its statements (comment-free, without `;` and `/`) and parsed facts.

    `kind` is `TABLE`, `VIEW`, `FUNCTION` or `SCRIPT`, empty if the file defines none. `schema` is
    the placeholder or schema as written. `problems` are `"rule: detail"` findings of the parser
    that `lint` reports.
    """

    file: str
    kind: str
    schema: str
    name: str
    name_quoted: bool
    is_replace: bool
    is_force: bool
    statements: tuple[str, ...]
    columns: tuple[Column, ...]
    comment: str | None
    references: tuple[str, ...]
    problems: tuple[str, ...] = ()

    @property
    def qualified_name(self) -> str:
        return f"{self.schema}.{self.name}" if self.schema else self.name


@dataclass(frozen=True)
class _Text:
    clean: str
    code: str
    skeleton: str


def load_reserved_keywords(path: Path = RESERVED_KEYWORDS_FILE) -> frozenset[str]:
    """The pinned list of Exasol reserved words."""
    return frozenset(path.read_text(encoding="utf-8").split())


def load(directory: Path) -> list[SqlObject]:
    """Parse every `*.sql` file of `directory` (not its subdirectories) in name order."""
    return [_parse_file(path) for path in sorted(directory.glob("*.sql"))]


def lint(
    objects: Iterable[SqlObject], *, allowed_refs: Iterable[str], reserved: frozenset[str]
) -> list[str]:
    """Every problem of `objects` as `"file: object: rule: detail"`; `[]` means clean."""
    allowed = tuple(allowed_refs)
    placeholders = set(placeholder_values(Namespace("")))
    problems: list[str] = []
    seen: dict[tuple[str, str], str] = {}
    for obj in objects:
        label = obj.name or "-"
        found = [*obj.problems, *_lint_object(obj, allowed, placeholders, reserved)]
        key = (obj.schema, obj.name)
        if obj.name and key in seen:
            found.append(f"duplicate: also defined in {seen[key]}")
        elif obj.name:
            seen[key] = obj.file
        problems.extend(f"{obj.file}: {label}: {rule}" for rule in found)
    return problems


def render(objects: Iterable[SqlObject], namespace: Namespace) -> list[SqlObject]:
    """Copies of `objects` with every placeholder replaced by the names of `namespace`."""

    def fill(text: str) -> str:
        return fill_placeholders(text, namespace)

    return [
        dataclasses.replace(
            obj,
            schema=fill(obj.schema),
            statements=tuple(fill(statement) for statement in obj.statements),
            columns=tuple(
                dataclasses.replace(
                    column, comment=None if column.comment is None else fill(column.comment)
                )
                for column in obj.columns
            ),
            comment=None if obj.comment is None else fill(obj.comment),
            references=tuple(fill(reference) for reference in obj.references),
        )
        for obj in objects
    ]


def unquote(identifier: str) -> str:
    """The name inside double quotes, or the identifier itself."""
    if identifier.startswith('"'):
        return identifier[1:-1].replace('""', '"')
    return identifier


def _lint_object(
    obj: SqlObject, allowed: tuple[str, ...], placeholders: set[str], reserved: frozenset[str]
) -> list[str]:
    found: list[str] = []
    found.extend(_lint_file_name(obj))
    found.extend(_lint_creation(obj))
    found.extend(_lint_names(obj, reserved))
    found.extend(_lint_comments(obj))
    found.extend(_lint_references(obj, allowed))
    found.extend(_lint_placeholders(obj, placeholders))
    return found


def _lint_file_name(obj: SqlObject) -> list[str]:
    match = _FILE_NAME.fullmatch(obj.file)
    if match is None:
        return ["file-name: the file name must be NNN_<object>.sql (three digits, lower case)"]
    if obj.name and match.group(2) != obj.name.lower():
        expected = f"{match.group(1)}_{obj.name.lower()}.sql"
        return [f"file-name: object {obj.name} must be defined in {expected}"]
    return []


def _lint_creation(obj: SqlObject) -> list[str]:
    if not obj.kind:
        return []
    found: list[str] = []
    if obj.schema != MODEL_SCHEMA_PLACEHOLDER:
        found.append(f"schema: the object must be created in {MODEL_SCHEMA_PLACEHOLDER}")
    if not obj.is_replace:
        found.append(f"replace: {obj.kind} must be created with CREATE OR REPLACE")
    elif obj.kind == "VIEW" and not obj.is_force:
        found.append("force: a view must be created with CREATE OR REPLACE FORCE VIEW")
    return found


def _lint_names(obj: SqlObject, reserved: frozenset[str]) -> list[str]:
    if not obj.kind:
        return []
    found = _lint_identifier(obj.name, obj.name_quoted, reserved, "object name")
    seen: set[str] = set()
    for column in obj.columns:
        found.extend(_lint_identifier(column.name, column.quoted, reserved, "column"))
        if column.name in seen:
            found.append(f"columns: column {column.name} is declared twice")
        seen.add(column.name)
    return found


def _lint_identifier(name: str, quoted: bool, reserved: frozenset[str], what: str) -> list[str]:
    if quoted:
        return [f"ddl-name: {what} {name} is quoted; DDL names are unquoted UPPER_CASE"]
    if not _DDL_NAME.fullmatch(name):
        return [f"ddl-name: {what} {name} is not an UPPER_CASE identifier (A-Z, digits, _)"]
    if name in reserved:
        return [f"reserved-word: {what} {name} is an Exasol reserved word"]
    return []


def _lint_comments(obj: SqlObject) -> list[str]:
    if not obj.kind:
        return []
    found: list[str] = []
    has_columns = obj.kind in ("TABLE", "VIEW")
    if has_columns and not obj.columns:
        found.append(f"columns: the {obj.kind.lower()} declares no columns with comments")
    for column in obj.columns:
        if not column.comment or not column.comment.strip():
            found.append(f"comment: column {column.name} has no comment")
        elif len(column.comment) > MAX_COMMENT_LENGTH:
            found.append(f"comment-length: column {column.name} comment is over 2000 characters")
    if not obj.comment or not obj.comment.strip():
        found.append("comment: the object has no comment")
        return found
    if len(obj.comment) > MAX_COMMENT_LENGTH:
        found.append("comment-length: the object comment is over 2000 characters")
    if has_columns and not obj.comment.startswith(GRAIN_PREFIX):
        found.append(f'grain: the comment must start with "{GRAIN_PREFIX}<grain>"')
    return found


def _lint_references(obj: SqlObject, allowed: tuple[str, ...]) -> list[str]:
    return [
        f"reference: {reference} is not an allowed reference"
        for reference in obj.references
        if not _is_allowed(reference, allowed)
    ]


def _is_allowed(reference: str, allowed: tuple[str, ...]) -> bool:
    schema, _, name = reference.rpartition(".")
    return any(
        entry == reference or (entry.endswith(".*") and entry[:-2] == schema and name != "")
        for entry in allowed
    )


def _lint_placeholders(obj: SqlObject, known: set[str]) -> list[str]:
    text = "\n".join(obj.statements)
    unknown = sorted(set(_PLACEHOLDER.findall(text)) - known)
    return [f"placeholder: unknown placeholder {name}" for name in unknown]


def _parse_file(path: Path) -> SqlObject:
    raw = path.read_text(encoding="utf-8")
    text = _scan(_remove_comments(raw))
    spans, problems = _split(text)
    statements = tuple(text.clean[start:end].strip() for start, end in spans)
    definition = _find_definition(text, spans)
    if definition is None:
        problems.append("parse: no CREATE TABLE, VIEW, FUNCTION or SCRIPT statement found")
        return SqlObject(
            file=path.name,
            kind="",
            schema="",
            name="",
            name_quoted=False,
            is_replace=False,
            is_force=False,
            statements=statements,
            columns=(),
            comment=None,
            references=(),
            problems=tuple(problems),
        )
    return _build_object(path.name, text, spans, statements, definition, problems)


def _build_object(
    file: str,
    text: _Text,
    spans: list[tuple[int, int]],
    statements: tuple[str, ...],
    definition: tuple[int, re.Match[str], re.Match[str]],
    problems: list[str],
) -> SqlObject:
    index, head, qname = definition
    _, end = spans[index]
    kind = head.group("kind")
    name = qname.group("name")
    schema = qname.group("schema") or ""
    own = (unquote(schema), unquote(name))
    comment = None
    columns: tuple[Column, ...] = ()
    if kind in ("TABLE", "VIEW"):
        columns, comment = _columns_and_comment(text, qname.end(), end)
    for other, (other_start, other_end) in enumerate(spans):
        if other == index:
            continue
        comment_on = _check_other_statement(text, other_start, other_end, kind, own, problems)
        if comment_on is not None and kind in ("FUNCTION", "SCRIPT"):
            comment = comment_on
    for later, (other_start, _) in enumerate(spans):
        if later > index and _CREATE_HEAD.match(text.skeleton, other_start):
            problems.append("statement: a second object is defined; one object per file")
    return SqlObject(
        file=file,
        kind=kind,
        schema=schema,
        name=unquote(name),
        name_quoted=name.startswith('"'),
        is_replace=head.group("replace") is not None,
        is_force=head.group("force") is not None,
        statements=statements,
        columns=columns,
        comment=comment,
        references=_references(text, spans, own),
        problems=tuple(problems),
    )


def _find_definition(
    text: _Text, spans: list[tuple[int, int]]
) -> tuple[int, re.Match[str], re.Match[str]] | None:
    for index, (start, end) in enumerate(spans):
        head = _CREATE_HEAD.match(text.skeleton, start, end)
        if head is None:
            continue
        qname = _QNAME_RE.match(text.code, head.end(), end)
        if qname is not None:
            return index, head, qname
    return None


def _check_other_statement(
    text: _Text, start: int, end: int, kind: str, own: tuple[str, str], problems: list[str]
) -> str | None:
    skeleton = text.skeleton
    if _CREATE_HEAD.match(skeleton, start, end):
        return None
    insert = _INSERT_HEAD.match(skeleton, start, end)
    if insert is not None:
        target = _QNAME_RE.match(text.code, insert.end(), end)
        if kind != "TABLE" or target is None or _qualified(target) != own:
            shown = ".".join(_qualified(target)) if target else "?"
            problems.append(f"statement: INSERT INTO {shown} is not an insert into {own[1]}")
        return None
    comment_on = _COMMENT_ON_HEAD.match(skeleton, start, end)
    if comment_on is not None:
        target = _QNAME_RE.match(text.code, comment_on.end(), end)
        tail = _IS_TEXT.match(skeleton, target.end(), end) if target else None
        if kind in ("FUNCTION", "SCRIPT") and target is not None and _qualified(target) == own:
            return _unescape(text.clean, tail) if tail else None
        if kind == "VIEW":
            problems.append("statement: COMMENT ON cannot target a view; use the inline COMMENT IS")
        else:
            problems.append(
                "statement: COMMENT ON is only allowed for the object's function or script"
            )
        return None
    first = _FIRST_WORD.match(skeleton, start, end)
    word = first.group("word").upper() if first else "?"
    problems.append(f"statement: unexpected {word} statement; a file holds one object and its rows")
    return None


def _qualified(match: re.Match[str]) -> tuple[str, str]:
    return unquote(match.group("schema") or ""), unquote(match.group("name"))


def _columns_and_comment(
    text: _Text, position: int, end: int
) -> tuple[tuple[Column, ...], str | None]:
    skeleton = text.skeleton
    opening = _skip_space(skeleton, position, end)
    columns: tuple[Column, ...] = ()
    after = opening
    if opening < end and skeleton[opening] == "(":
        closing = _matching_paren(skeleton, opening, end)
        columns = tuple(_columns(text, opening + 1, closing))
        after = closing + 1
    trailing = _TRAILING_COMMENT.search(skeleton, after, end)
    return columns, _unescape(text.clean, trailing) if trailing else None


def _columns(text: _Text, start: int, end: int) -> list[Column]:
    columns = []
    for item_start, item_end in _split_commas(text.skeleton, start, end):
        name = _COLUMN_NAME.match(text.code, item_start, item_end)
        if name is None or name.group("name").upper() in _CONSTRAINT_WORDS:
            continue
        comment = _COMMENT_IS.search(text.skeleton, name.end(), item_end)
        columns.append(
            Column(
                name=unquote(name.group("name")),
                comment=_unescape(text.clean, comment) if comment else None,
                quoted=name.group("name").startswith('"'),
            )
        )
    return columns


def _references(text: _Text, spans: list[tuple[int, int]], own: tuple[str, str]) -> tuple[str, ...]:
    found: list[tuple[int, str]] = []
    for start, end in spans:
        segment = text.code[start:end]
        for pattern in (_REFERENCE_PLACEHOLDER, _REFERENCE_CALL):
            for match in pattern.finditer(segment):
                schema = unquote(match.group("schema"))
                name = unquote(match.group("name"))
                is_schema = schema.startswith("{") or _DDL_NAME.fullmatch(schema)
                if is_schema and (schema, name) != own:
                    found.append((start + match.start(), f"{schema}.{name}"))
        for position, schema, name in _table_list_references(text.code, start, end):
            if _DDL_NAME.fullmatch(schema) and (schema, name) != own:
                found.append((position, f"{schema}.{name}"))
    ordered: list[str] = []
    for _, reference in sorted(found):
        if reference not in ordered:
            ordered.append(reference)
    return tuple(ordered)


def _table_list_references(code: str, start: int, end: int) -> list[tuple[int, str, str]]:
    found = []
    for opening in _TABLE_LIST_START.finditer(code, start, end):
        position = opening.end()
        while position < end:
            item = _TABLE_ITEM.match(code, position, end)
            if item is None:
                break
            alias = item.group("alias")
            qualifier = item.group("schema")
            if qualifier:
                found.append((item.start("name"), unquote(qualifier), unquote(item.group("name"))))
            starts_clause = alias is not None and alias.upper() in _NOT_AN_ALIAS
            if starts_clause or not item.group("more"):
                break
            position = item.end()
    return found


def _unescape(clean: str, match: re.Match[str]) -> str:
    start, end = match.span("text")
    return clean[start + 1 : end - 1].replace("''", "'")


def _skip_space(text: str, position: int, end: int) -> int:
    while position < end and text[position].isspace():
        position += 1
    return position


def _matching_paren(skeleton: str, opening: int, end: int) -> int:
    depth = 0
    for position in range(opening, end):
        if skeleton[position] == "(":
            depth += 1
        elif skeleton[position] == ")":
            depth -= 1
            if depth == 0:
                return position
    return end


def _split_commas(skeleton: str, start: int, end: int) -> list[tuple[int, int]]:
    parts = []
    depth = 0
    item_start = start
    for position in range(start, end):
        char = skeleton[position]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append((item_start, position))
            item_start = position + 1
    parts.append((item_start, end))
    return parts


def _split(text: _Text) -> tuple[list[tuple[int, int]], list[str]]:
    skeleton = text.skeleton
    size = len(skeleton)
    spans: list[tuple[int, int]] = []
    problems: list[str] = []
    position = 0
    while True:
        position = _skip_space(skeleton, position, size)
        if position >= size:
            break
        head = _CREATE_HEAD.match(skeleton, position)
        if head is not None and head.group("kind") in ("FUNCTION", "SCRIPT"):
            position = _add_routine_span(skeleton, position, spans)
            continue
        semicolon = skeleton.find(";", position)
        if semicolon == -1:
            problems.append("parse: a statement is not terminated by ;")
            spans.append((position, size))
            break
        spans.append((position, semicolon))
        position = semicolon + 1
    return spans, problems


def _add_routine_span(skeleton: str, position: int, spans: list[tuple[int, int]]) -> int:
    comment = _COMMENT_ON_LINE.search(skeleton, position)
    limit = comment.start() if comment else len(skeleton)
    slash = _SLASH_LINE.search(skeleton, position)
    if slash is not None and slash.start() < limit:
        spans.append((position, slash.start()))
        return slash.end()
    spans.append((position, limit))
    return limit


def _remove_comments(raw: str) -> str:
    out: list[str] = []
    position = 0
    size = len(raw)
    quote = ""
    while position < size:
        char = raw[position]
        if quote:
            out.append(char)
            if char == quote:
                if raw.startswith(quote, position + 1):
                    out.append(quote)
                    position += 1
                else:
                    quote = ""
            position += 1
        elif char in ("'", '"'):
            quote = char
            out.append(char)
            position += 1
        elif raw.startswith("--", position):
            newline = raw.find("\n", position)
            position = size if newline == -1 else newline
            position = _drop_comment_line(out, raw, position)
        elif raw.startswith("/*", position):
            closing = raw.find("*/", position + 2)
            position = size if closing == -1 else closing + 2
            _trim_trailing_blanks(out)
        else:
            out.append(char)
            position += 1
    return "".join(out)


def _trim_trailing_blanks(out: list[str]) -> bool:
    while out and out[-1] in " \t":
        out.pop()
    return not out or out[-1] == "\n"


def _drop_comment_line(out: list[str], raw: str, position: int) -> int:
    if _trim_trailing_blanks(out) and raw.startswith("\n", position):
        return position + 1
    return position


def _scan(clean: str) -> _Text:
    code: list[str] = []
    skeleton: list[str] = []
    position = 0
    size = len(clean)
    quote = ""
    while position < size:
        char = clean[position]
        if not quote:
            code.append(char)
            skeleton.append(char)
            if char in ("'", '"'):
                quote = char
            position += 1
            continue
        if char == quote:
            if clean.startswith(quote, position + 1):
                mask_code = "x" if quote == "'" else char
                code.extend([mask_code, mask_code])
                skeleton.extend("xx" if quote == "'" else "qq")
                position += 2
                continue
            code.append(char)
            skeleton.append(char)
            quote = ""
        else:
            code.append("x" if quote == "'" else char)
            skeleton.append("x" if quote == "'" else "q")
        position += 1
    return _Text(clean=clean, code="".join(code), skeleton="".join(skeleton))


__all__ = [
    "MODEL_ALLOWED_REFS",
    "Column",
    "SqlObject",
    "lint",
    "load",
    "load_reserved_keywords",
    "render",
    "unquote",
]
