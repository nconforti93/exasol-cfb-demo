"""Generic SQL-object files: parse, lint and render (the pure part of the model deployer).

A directory holds one object per file, `NNN_<object>.sql`: a reference table (its `CREATE` plus an
`INSERT` of its rows), a function (`CREATE FUNCTION ... /` plus `COMMENT ON FUNCTION`) or a view.
`load` parses the files in name order, `lint` returns every problem as
`"file: object: rule: detail"` (none stops at the first), and `render` fills the namespace
placeholders (`{MODEL_SCHEMA}`, `{CORE_SCHEMA}`, `{META_SCHEMA}`, ...). None of these touches a
database. `deploy` does, in one transaction (see its docstring).

Conventions the parser relies on:

* Statements end with `;` outside strings and comments; a function or script ends with a line that
  holds only `/`. `--` and `/* */` comments are removed from the statements.
* A data table is `CREATE TABLE IF NOT EXISTS` in a file named `<object>.sql` (no number): `deploy`
  creates it when missing and never replaces or drops it, so its rows survive every redeploy.
* A reference is `SCHEMA.NAME` after `FROM`, `JOIN` or `INTO`, a qualified function call, or any
  `{PLACEHOLDER}.NAME`. A bare qualifier counts as a schema only when it is an upper-case
  identifier, so lower-case aliases (`g."SEASON"`) never look like schemas.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loader.runlock import HeldLock, first_line
from vs.cfbd_vs.sqlrender import qualified_name, string_literal
from vs.host.deploy import DeployError
from vs.host.metaddl import fill_placeholders, placeholder_values
from vs.host.namespace import Namespace

MODEL_SCHEMA_PLACEHOLDER = "{MODEL_SCHEMA}"
SIM_SCHEMA_PLACEHOLDER = "{SIM_SCHEMA}"
SIM_ALLOWED_REFS = (
    "{SIM_SCHEMA}.*",
    "{MODEL_SCHEMA}.*",
)
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
_DATA_FILE_NAME = re.compile(r"([a-z][a-z0-9_]*)\.sql")
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
    r"(?P<kind>TABLE|VIEW|FUNCTION|SCRIPT)\b\s*(?P<exists>IF\s+NOT\s+EXISTS\s+)?"
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
    that `lint` reports. `keep_existing` marks a data table (`CREATE TABLE IF NOT EXISTS`).
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
    keep_existing: bool = False

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
    objects: Iterable[SqlObject],
    *,
    allowed_refs: Iterable[str],
    reserved: frozenset[str],
    schema_placeholder: str = MODEL_SCHEMA_PLACEHOLDER,
) -> list[str]:
    """Every problem of `objects` as `"file: object: rule: detail"`; `[]` means clean.

    Every object must be created in `schema_placeholder`; data tables (`keep_existing`) are
    exempt from `CREATE OR REPLACE` and from the numbered file name.
    """
    allowed = tuple(allowed_refs)
    placeholders = set(placeholder_values(Namespace("")))
    problems: list[str] = []
    seen: dict[tuple[str, str], str] = {}
    for obj in objects:
        label = obj.name or "-"
        found = [
            *obj.problems,
            *_lint_object(obj, allowed, placeholders, reserved, schema_placeholder),
        ]
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
    obj: SqlObject,
    allowed: tuple[str, ...],
    placeholders: set[str],
    reserved: frozenset[str],
    schema_placeholder: str,
) -> list[str]:
    found: list[str] = []
    found.extend(_lint_file_name(obj))
    found.extend(_lint_creation(obj, schema_placeholder))
    found.extend(_lint_names(obj, reserved))
    found.extend(_lint_comments(obj))
    found.extend(_lint_references(obj, allowed))
    found.extend(_lint_placeholders(obj, placeholders))
    return found


def _lint_file_name(obj: SqlObject) -> list[str]:
    if obj.keep_existing:
        data = _DATA_FILE_NAME.fullmatch(obj.file)
        if data is None or (obj.name and data.group(1) != obj.name.lower()):
            return [f"file-name: data table {obj.name} must be defined in {obj.name.lower()}.sql"]
        return []
    match = _FILE_NAME.fullmatch(obj.file)
    if match is None:
        return ["file-name: the file name must be NNN_<object>.sql (three digits, lower case)"]
    if obj.name and match.group(2) != obj.name.lower():
        expected = f"{match.group(1)}_{obj.name.lower()}.sql"
        return [f"file-name: object {obj.name} must be defined in {expected}"]
    return []


def _lint_creation(obj: SqlObject, schema_placeholder: str) -> list[str]:
    if not obj.kind:
        return []
    found: list[str] = []
    if obj.schema != schema_placeholder:
        found.append(f"schema: the object must be created in {schema_placeholder}")
    if obj.keep_existing:
        if obj.kind != "TABLE":
            found.append(f"data-table: IF NOT EXISTS is for tables, not for a {obj.kind}")
        if obj.is_replace:
            found.append("data-table: a data table is never created with OR REPLACE")
        if len(obj.statements) != 1:
            found.append("data-table: the file holds the CREATE statement and nothing else")
    elif not obj.is_replace:
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
        keep_existing=head.group("exists") is not None,
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


@dataclass(frozen=True)
class DeployReport:
    """What `deploy` did: the objects in file order, what it dropped, counts and printable lines.

    `retyped` names objects that existed with another kind and were dropped first (`GAME (was
    TABLE)`); `dropped` names objects no file defines, `VIEW OLD_VIEW`. The comment counts are
    those of the database after the compile pass. `kept` names the data tables (and `keep` names)
    that existed before and were left as they were; `differences` lists where such a table's
    columns or comments differ from its file (reported, never migrated).
    """

    schema: str
    created: tuple[str, ...]
    dropped: tuple[str, ...]
    retyped: tuple[str, ...]
    tables: int
    views: int
    functions: int
    scripts: int
    comments_found: int
    comments_expected: int
    extra_statements: int
    lines: tuple[str, ...]
    kept: tuple[str, ...] = ()
    differences: tuple[str, ...] = ()

    @property
    def summary(self) -> str:
        """One line for the run lock's `DETAIL`."""
        return (
            f"{self.schema}: {self.tables} tables, {self.views} views, {self.functions} "
            f"functions; dropped {len(self.dropped)}; comments {self.comment_percent} %"
        )

    @property
    def comment_percent(self) -> int:
        if not self.comments_expected:
            return 100
        return self.comments_found * 100 // self.comments_expected


_OBJECT_TYPES = ("TABLE", "VIEW", "FUNCTION", "SCRIPT")
_DROP_ORDER = ("VIEW", "FUNCTION", "SCRIPT", "TABLE")


def deploy(
    connection: Any,
    schema: str,
    objects: Sequence[SqlObject],
    *,
    extra_statements: Sequence[str] = (),
    keep: Collection[str] = (),
    lock: HeldLock,
) -> DeployReport:
    """Install rendered `objects` into `schema` in one transaction; `lock` is the held run lock.

    The caller takes the loader's run lock first (`loader.runlock.run_lock`, kind `DEPLOY`), because
    acquiring commits, and passes the `HeldLock`; the connection must be in autocommit mode. Then,
    in one transaction:

    1. `CREATE SCHEMA IF NOT EXISTS`, then one read of the schema's objects: an object that exists
       with another kind than its file defines is dropped (a view that became a table), and so is
       every table, view, function or script that no file defines (orphans, only in `schema`);
    2. the statements of every object in file order (`FORCE` views may reference later files),
       then `extra_statements` (for example a Lua script of another schema);
    3. data tables (`keep_existing` objects) are created when missing and never replaced; names in
       `keep` are never dropped as orphans either (a table whose file was removed keeps its rows);
    4. the compile pass: `SELECT 1 FROM <view> WHERE 1 = 0` for every view, in file order. A
       `FORCE` view fails only at its first query, and the database lists a view's columns and
       comments only once it compiled, and marks views of a replaced table `OUTDATED`;
    5. the read-back: every object and column comment must equal the declared one, the columns of
       a view must be its declared columns and not `OUTDATED`. A data table that existed before
       is only compared: a difference goes into `DeployReport.differences`, not into an error.

    Any failure rolls the whole transaction back, so the previous schema stays as it was, and
    raises `DeployError` naming file, object and the database's message. `lock.detail` gets the
    summary. The report lines are returned, not printed.
    """
    _check_objects(schema, objects)
    _check_autocommit(connection)
    switch = getattr(connection, "set_autocommit", None)
    if switch is not None:
        switch(False)
    try:
        report = _Transaction(connection, schema, objects, extra_statements, keep).run()
        connection.execute("COMMIT")
    except BaseException as error:
        _roll_back(connection, error)
        if isinstance(error, DeployError):
            raise
        if isinstance(error, Exception):
            raise DeployError(f"{schema}: {first_line(error)}") from None
        raise
    finally:
        if switch is not None:
            switch(True)
    lock.detail = report.summary
    return report


def _roll_back(connection: Any, error: BaseException) -> None:
    try:
        connection.execute("ROLLBACK")
    except Exception as problem:
        error.add_note(
            f"ROLLBACK failed ({first_line(problem)}); the transaction ends with the session"
        )


def _check_objects(schema: str, objects: Sequence[SqlObject]) -> None:
    problems = []
    for obj in objects:
        if not obj.kind:
            problems.append(f"{obj.file}: defines no table, view, function or script")
        elif obj.schema != schema:
            problems.append(f"{obj.file}: {obj.name} is created in {obj.schema}, not {schema}")
    if problems:
        raise DeployError("nothing was sent:\n  " + "\n  ".join(problems))


def _check_autocommit(connection: Any) -> None:
    attributes = getattr(connection, "attr", None)
    if isinstance(attributes, dict) and attributes.get("autocommit") is False:
        raise DeployError(
            "the deploy needs a connection in autocommit mode: it starts its own transaction "
            "after the run lock was taken"
        )


class _Transaction:
    def __init__(
        self,
        connection: Any,
        schema: str,
        objects: Sequence[SqlObject],
        extra: Sequence[str],
        keep: Collection[str] = (),
    ) -> None:
        self._connection = connection
        self._schema = schema
        self._objects = objects
        self._extra = extra
        self._keep = frozenset(keep)
        self._existing_data: tuple[str, ...] = ()

    def run(self) -> DeployReport:
        self._execute(f"CREATE SCHEMA IF NOT EXISTS {self._schema}")
        retyped, dropped = self._drop_stale()
        for obj in self._objects:
            self._create(obj)
        for number, statement in enumerate(self._extra, start=1):
            self._run_statement(statement, f"extra statement {number}")
        views = [obj for obj in self._objects if obj.kind == "VIEW"]
        for view in views:
            self._compile(view)
        found, expected, differences = self._read_back()
        return self._report(retyped, dropped, len(views), found, expected, differences)

    def _drop_stale(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        defined = {obj.name: obj.kind for obj in self._objects}
        data = {obj.name for obj in self._objects if obj.keep_existing}
        existing = self._existing()
        self._existing_data = tuple(sorted(name for name, _ in existing if name in data))
        retyped = [
            (name, kind)
            for name, kind in existing
            if name in defined and defined[name] != kind and name not in self._keep
        ]
        orphans = [
            (name, kind)
            for name, kind in existing
            if name not in defined and name not in self._keep
        ]
        for name, kind in sorted([*retyped, *orphans], key=lambda item: _DROP_ORDER.index(item[1])):
            self._run_statement(
                f"DROP {kind} {qualified_name(self._schema, name)}", f"{kind} {name}"
            )
        return (
            tuple(f"{name} (was {kind})" for name, kind in retyped),
            tuple(f"{kind} {name}" for name, kind in orphans),
        )

    def _existing(self) -> list[tuple[str, str]]:
        return [(name, kind) for name, kind, _ in self._catalog() if kind in _OBJECT_TYPES]

    def _catalog(self) -> list[tuple[str, str, str | None]]:
        rows = self._execute(
            "SELECT OBJECT_NAME, OBJECT_TYPE, OBJECT_COMMENT FROM EXA_ALL_OBJECTS "
            f"WHERE ROOT_NAME = {string_literal(self._schema)} ORDER BY OBJECT_NAME"
        ).fetchall()
        return [(str(row[0]), str(row[1]), row[2]) for row in rows]

    def _create(self, obj: SqlObject) -> None:
        for statement in obj.statements:
            self._run_statement(statement, f"{obj.file}: {obj.kind} {obj.name}")

    def _compile(self, view: SqlObject) -> None:
        sql = f"SELECT 1 FROM {qualified_name(self._schema, view.name)} WHERE 1 = 0"
        self._run_statement(sql, f"compile {view.file}: VIEW {view.name}")

    def _run_statement(self, statement: str, label: str) -> None:
        try:
            self._connection.execute(statement)
        except Exception as error:
            raise DeployError(f"{label}: {first_line(error)}") from None

    def _execute(self, sql: str) -> Any:
        return self._connection.execute(sql)

    def _read_back(self) -> tuple[int, int, tuple[str, ...]]:
        actual_objects = {name: (kind, comment) for name, kind, comment in self._catalog()}
        actual_columns = self._columns()
        problems: list[str] = []
        differences: list[str] = []
        found = expected = 0
        for obj in self._objects:
            kept = obj.name in self._existing_data
            found_here, expected_here = _compare_object(
                obj,
                actual_objects.get(obj.name),
                actual_columns.get(obj.name, []),
                differences if kept else problems,
            )
            found += found_here
            expected += expected_here
        if problems:
            raise DeployError(
                f"{self._schema} read back from the database does not match the files:\n  "
                + "\n  ".join(problems)
            )
        return found, expected, tuple(differences)

    def _columns(self) -> dict[str, list[tuple[str, str | None, str | None]]]:
        rows = self._execute(
            "SELECT COLUMN_TABLE, COLUMN_NAME, COLUMN_COMMENT, COLUMN_ORDINAL_POSITION, STATUS "
            f"FROM EXA_ALL_COLUMNS WHERE COLUMN_SCHEMA = {string_literal(self._schema)} "
            "ORDER BY COLUMN_TABLE, COLUMN_ORDINAL_POSITION"
        ).fetchall()
        columns: dict[str, list[tuple[str, str | None, str | None]]] = {}
        for table, name, comment, _, status in rows:
            columns.setdefault(str(table), []).append((str(name), comment, status))
        return columns

    def _report(
        self,
        retyped: tuple[str, ...],
        dropped: tuple[str, ...],
        views: int,
        found: int,
        expected: int,
        differences: tuple[str, ...],
    ) -> DeployReport:
        counts = {kind: sum(1 for o in self._objects if o.kind == kind) for kind in _OBJECT_TYPES}
        percent = found * 100 // expected if expected else 100
        lines = (
            f"schema {self._schema}: ready",
            f"objects in {self._schema}: {counts['TABLE']} tables, {counts['VIEW']} views, "
            f"{counts['FUNCTION']} functions, {counts['SCRIPT']} scripts replaced",
            *(f"retyped: {name}" for name in retyped),
            f"dropped: {', '.join(dropped) if dropped else 'none'}",
            *self._data_lines(),
            *(f"differs: {text}" for text in differences),
            f"extra statements: {len(self._extra)} run",
            f"views compiled: {views}",
            f"comments: {found} of {expected} ({percent} %)",
        )
        return DeployReport(
            schema=self._schema,
            created=tuple(obj.name for obj in self._objects),
            dropped=dropped,
            retyped=retyped,
            tables=counts["TABLE"],
            views=counts["VIEW"],
            functions=counts["FUNCTION"],
            scripts=counts["SCRIPT"],
            comments_found=found,
            comments_expected=expected,
            extra_statements=len(self._extra),
            lines=lines,
            kept=self._existing_data,
            differences=differences,
        )

    def _data_lines(self) -> tuple[str, ...]:
        data = [obj.name for obj in self._objects if obj.keep_existing]
        if not data:
            return ()
        created = [name for name in data if name not in self._existing_data]
        return (
            f"data tables: {len(self._existing_data)} kept as they were, "
            f"{len(created)} created ({', '.join(created) if created else 'none'})",
        )


def _compare_object(
    obj: SqlObject,
    actual: tuple[str, str | None] | None,
    columns: list[tuple[str, str | None, str | None]],
    problems: list[str],
) -> tuple[int, int]:
    if actual is None or actual[0] != obj.kind:
        problems.append(
            f"{obj.file}: {obj.kind} {obj.name} does not exist after the statements ran"
        )
        return 0, 1 + len(obj.columns)
    found = 0
    if _same_comment(obj.comment, actual[1]):
        found += 1
    else:
        problems.append(f"{obj.file}: {obj.name}: the object comment differs from the file")
    if obj.kind not in ("TABLE", "VIEW"):
        return found, 1
    if [name for name, _, _ in columns] != [column.name for column in obj.columns]:
        problems.append(
            f"{obj.file}: {obj.name}: columns in the database are "
            f"{[name for name, _, _ in columns]}, the file declares "
            f"{[column.name for column in obj.columns]}"
        )
    actual_by_name = {name: (comment, status) for name, comment, status in columns}
    for column in obj.columns:
        comment, status = actual_by_name.get(column.name, (None, None))
        if status is not None:
            problems.append(f"{obj.file}: {obj.name}.{column.name} is {status}")
        elif _same_comment(column.comment, comment):
            found += 1
        else:
            problems.append(
                f"{obj.file}: {obj.name}.{column.name}: the comment differs from the file"
            )
    return found, 1 + len(obj.columns)


def _same_comment(declared: str | None, actual: str | None) -> bool:
    return bool(declared) and declared == actual


__all__ = [
    "MODEL_ALLOWED_REFS",
    "SIM_ALLOWED_REFS",
    "SIM_SCHEMA_PLACEHOLDER",
    "Column",
    "DeployReport",
    "SqlObject",
    "deploy",
    "lint",
    "load",
    "load_reserved_keywords",
    "render",
    "unquote",
]
