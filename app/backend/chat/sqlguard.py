"""The static checks of `run_sql` (`chat/data-tools`): a lexer for Exasol SQL text, the checks that
stop a statement before the database, and the identifiers the rehearsal grader reads.

The guard is a typo-catcher with clear messages; the role `CFB_MCP` is the boundary (decision [9]).

Lexing follows Exasol: `'...'` strings with `''` for a quote, `"..."` names with `""`, `--` comments
to the end of the line, `/* ... */` comments, and numbers as in SQL (`1INTO` is `1` and `INTO`).
Where Exasol's lexer and this one could disagree, the text is refused instead of guessed: a `/*`
inside a block comment (nesting), a line break other than a newline inside a `--` comment, an
unterminated string, name or comment, control and direction characters anywhere, and invisible
format characters outside strings and quoted names. The check that lets a statement through is
strict ASCII (`SELECT`, `WITH`); the checks that refuse fold names (compatibility forms, accents,
case, invisible characters), so look-alike spellings are refused as well.

The text sent is the statement as written, cut before one trailing `;` and right-stripped: always
a prefix of the tokens that were checked.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

MAX_CHARS = 20_000
LIVE_SCHEMA = "CFB_API_LIVE"
SYSTEM_SCHEMA = "SYS"
SYSTEM_PREFIX = "EXA_"
LIVE_SCHEMA_MESSAGE = (
    "The chat cannot reach CFBD. A live preview is one click on the head-to-head page."
)
SYSTEM_TABLES_MESSAGE = (
    "System tables are not part of the chat's data. describe_view gives a view's columns."
)

TokenKind = Literal[
    "word",
    "number",
    "string",
    "ident",
    "line_comment",
    "block_comment",
    "space",
    "semicolon",
    "symbol",
]

_NUMBER = re.compile(r"[0-9]+(?:\.[0-9]*)?(?:[eE][+-]?[0-9]+)?|\.[0-9]+(?:[eE][+-]?[0-9]+)?")
_LINE_BREAKS = frozenset("\n\r\x0b\x0c\x1c\x1d\x1e\x85\N{LINE SEPARATOR}\N{PARAGRAPH SEPARATOR}")
_ALLOWED_CONTROLS = frozenset("\t\n\r")
_DIRECTION_MARKS = frozenset(
    "\N{ARABIC LETTER MARK}\N{LEFT-TO-RIGHT MARK}\N{RIGHT-TO-LEFT MARK}"
    "\N{LEFT-TO-RIGHT EMBEDDING}\N{RIGHT-TO-LEFT EMBEDDING}\N{POP DIRECTIONAL FORMATTING}"
    "\N{LEFT-TO-RIGHT OVERRIDE}\N{RIGHT-TO-LEFT OVERRIDE}\N{LEFT-TO-RIGHT ISOLATE}"
    "\N{RIGHT-TO-LEFT ISOLATE}\N{FIRST STRONG ISOLATE}\N{POP DIRECTIONAL ISOLATE}"
)
_FOLD_DROPPED = frozenset({"Mn", "Mc", "Me", "Cf"})
_QUOTED_KINDS = frozenset({"string", "ident"})
_NOT_CODE = frozenset({"space", "line_comment", "block_comment"})
_LEADING = frozenset({"SELECT", "WITH"})


class Rule(StrEnum):
    """The rule a refused statement breaks, as named in the tool's error result."""

    LENGTH = "length"
    CHARACTERS = "characters"
    SYNTAX = "syntax"
    ONE_STATEMENT = "one_statement"
    SELECT_ONLY = "select_only"
    INTO_TABLE = "into_table"
    LIVE_SCHEMA = "live_schema"
    SYSTEM_TABLES = "system_tables"


class LexError(ValueError):
    """The text cannot be split into tokens without guessing; the message names the position."""


@dataclass(frozen=True, slots=True)
class Token:
    """One token: its kind, its exact text and its 0-based start in the statement."""

    kind: TokenKind
    text: str
    start: int


@dataclass(frozen=True, slots=True)
class Accepted:
    """A statement that passed the checks, with the exact text to send."""

    sql: str


@dataclass(frozen=True, slots=True)
class Refused:
    """A statement stopped before the database: the rule it breaks and a message for the model."""

    rule: Rule
    message: str


def lex(sql: str) -> list[Token]:
    """Split the text into tokens whose texts concatenate to it; raises `LexError` where the text
    is unterminated or ambiguous (see the module documentation)."""
    tokens: list[Token] = []
    index = 0
    while index < len(sql):
        kind, end = _next_token(sql, index)
        tokens.append(Token(kind, sql[index:end], index))
        index = end
    return tokens


def check(sql: str, max_chars: int = MAX_CHARS) -> Accepted | Refused:
    """The static checks of `run_sql`, in order: length, characters, lexing, one statement, the
    leading keyword, `INTO`, the live schema, system tables."""
    if len(sql) > max_chars:
        return Refused(
            Rule.LENGTH, f"The statement has {len(sql):,} characters; the limit is {max_chars:,}."
        )
    problem = _forbidden_character(sql)
    if problem is not None:
        return Refused(Rule.CHARACTERS, problem)
    try:
        tokens = lex(sql)
    except LexError as error:
        return Refused(Rule.SYNTAX, str(error))
    problem = _invisible_outside_quotes(tokens)
    if problem is not None:
        return Refused(Rule.CHARACTERS, problem)
    code = [token for token in tokens if token.kind not in _NOT_CODE]
    sent = sql.rstrip()
    if code and code[-1].kind == "semicolon":
        sent = sql[: code[-1].start].rstrip()
        code = code[:-1]
    return _check_code(code) or Accepted(sent)


def identifiers(sql: str) -> tuple[str, ...]:
    """Every name outside strings and comments, in order: unquoted words upper-cased as Exasol
    resolves them (keywords included), quoted names as written. Raises `LexError` like `lex`."""
    return tuple(
        token.text.upper() if token.kind == "word" else _unquote(token.text)
        for token in lex(sql)
        if token.kind in ("word", "ident")
    )


def _check_code(code: list[Token]) -> Refused | None:
    if any(token.kind == "semicolon" for token in code):
        return Refused(
            Rule.ONE_STATEMENT,
            "Only one statement can run per call; send each statement in its own run_sql call.",
        )
    first = next((token for token in code if token.text != "("), None)
    if first is None:
        return Refused(Rule.SELECT_ONLY, "The statement is empty; send one SELECT or WITH.")
    if first.kind != "word" or not first.text.isascii() or first.text.upper() not in _LEADING:
        return Refused(
            Rule.SELECT_ONLY,
            "Only one SELECT or WITH statement can run here; this one starts with "
            f"{first.text[:40]}.",
        )
    words = [_fold(token.text) for token in code if token.kind == "word"]
    names = words + [_fold(_unquote(token.text)) for token in code if token.kind == "ident"]
    if "INTO" in words:
        return Refused(
            Rule.INTO_TABLE,
            "SELECT ... INTO TABLE creates a table, and the chat is read-only; "
            "remove the INTO clause.",
        )
    if any(LIVE_SCHEMA in name for name in names):
        return Refused(Rule.LIVE_SCHEMA, LIVE_SCHEMA_MESSAGE)
    if any(_is_system(name) for name in names):
        return Refused(Rule.SYSTEM_TABLES, SYSTEM_TABLES_MESSAGE)
    return None


def _next_token(sql: str, index: int) -> tuple[TokenKind, int]:
    char = sql[index]
    if sql.startswith("--", index):
        return "line_comment", _line_comment_end(sql, index)
    if sql.startswith("/*", index):
        return "block_comment", _block_comment_end(sql, index)
    if char == "'":
        return "string", _quoted_end(sql, index, "'", "string literal")
    if char == '"':
        return "ident", _quoted_end(sql, index, '"', "quoted name")
    if char == ";":
        return "semicolon", index + 1
    if char.isspace():
        return "space", _run_end(sql, index, str.isspace)
    number = _NUMBER.match(sql, index)
    if number is not None:
        return "number", number.end()
    if _starts_word(char):
        return "word", _run_end(sql, index + 1, _continues_word)
    return "symbol", index + 1


def _run_end(sql: str, index: int, belongs: Callable[[str], bool]) -> int:
    while index < len(sql) and belongs(sql[index]):
        index += 1
    return index


def _starts_word(char: str) -> bool:
    return char in "_$" or unicodedata.category(char).startswith("L")


def _continues_word(char: str) -> bool:
    category = unicodedata.category(char)
    return char in "_$" or category[0] in "LNM" or category == "Pc"


def _line_comment_end(sql: str, start: int) -> int:
    newline = sql.find("\n", start)
    end = len(sql) if newline < 0 else newline
    body = sql[start:end]
    if newline >= 0 and body.endswith("\r"):
        body = body[:-1]
    for offset, char in enumerate(body):
        if char in _LINE_BREAKS:
            raise LexError(
                f"The -- comment at character {start + 1} holds a line break other than a "
                f"newline (U+{ord(char):04X} at character {start + offset + 1}); end comments "
                "with a plain newline."
            )
    return end


def _block_comment_end(sql: str, start: int) -> int:
    close = sql.find("*/", start + 2)
    if close < 0:
        raise LexError(f"The /* comment at character {start + 1} is not closed.")
    nested = sql.find("/*", start + 2, close + 1)
    if nested >= 0:
        raise LexError(
            f"The /* comment at character {start + 1} holds another /* at character "
            f"{nested + 1}; nested comments are not allowed."
        )
    return close + 2


def _quoted_end(sql: str, start: int, quote: str, what: str) -> int:
    index = start + 1
    while True:
        close = sql.find(quote, index)
        if close < 0:
            raise LexError(f"The {what} starting at character {start + 1} is not closed.")
        if not sql.startswith(quote * 2, close):
            return close + 1
        index = close + 2


def _forbidden_character(sql: str) -> str | None:
    for index, char in enumerate(sql):
        control = unicodedata.category(char) == "Cc" and char not in _ALLOWED_CONTROLS
        if control or char in _DIRECTION_MARKS:
            return _character_message(char, index)
    return None


def _invisible_outside_quotes(tokens: list[Token]) -> str | None:
    for token in tokens:
        if token.kind in _QUOTED_KINDS:
            continue
        for offset, char in enumerate(token.text):
            if unicodedata.category(char) == "Cf":
                return _character_message(char, token.start + offset)
    return None


def _character_message(char: str, index: int) -> str:
    return (
        f"The statement holds the control or invisible character U+{ord(char):04X} at character "
        f"{index + 1}; write it without such characters."
    )


def _unquote(text: str) -> str:
    return text[1:-1].replace('""', '"')


def _fold(name: str) -> str:
    """A name as loosely as any spelling could match it: compatibility forms, accents, invisible
    characters and case removed. Used only to refuse, never to allow."""
    once = _strip_marks(unicodedata.normalize("NFKD", name)).casefold()
    return _strip_marks(unicodedata.normalize("NFKD", once)).upper()


def _strip_marks(text: str) -> str:
    return "".join(char for char in text if unicodedata.category(char) not in _FOLD_DROPPED)


def _is_system(folded: str) -> bool:
    return any(
        part == SYSTEM_SCHEMA or part.startswith(SYSTEM_PREFIX) for part in folded.split("$")
    )
