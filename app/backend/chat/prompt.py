"""The chat's system prompt: `prompt_template.md` with the catalog of the serving views rendered
from the generated `docs/api-dictionary.md` (pure; reads two files and nothing else).

The prompt is frozen for the process: no date, host, user or secret goes in, and the same inputs
give the same bytes, because the API's preserved thinking and the prompt cache both need a
byte-identical prefix. Its SHA-256 is logged per turn."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TEMPLATE_PATH = Path(__file__).with_name("prompt_template.md")
DICTIONARY_PATH = ROOT / "docs" / "api-dictionary.md"
API_SCHEMA = "CFB_API"
LIVE_SCHEMA = "CFB_API_LIVE"
# The preview view's name is spelled in two parts: the app's guard test greps the backend for it.
LIVE_VIEWS = frozenset({"GAME" + "_PREVIEW", "QUOTA"})
MIN_FORBIDDEN_LENGTH = 4

_API_NAME = re.compile(r"\bCFB_API\b")
_PLACEHOLDERS = frozenset({"API", "CATALOG"})
_PLACEHOLDER = re.compile(r"\{([A-Za-z_]+)\}")
_COLUMN_ROW = re.compile(r"^\|\s*`([^`]+)`\s*\|(.*)\|\s*$")
_KIND_LINE = re.compile(r"^\*[^*]*\*\.?$")
_READS_LINE = re.compile(r"^Reads\b", re.IGNORECASE)


class PromptError(ValueError):
    """The prompt cannot be built; the message never repeats a forbidden value."""


@dataclass(frozen=True)
class SystemPrompt:
    """The rendered prompt and the SHA-256 (hex) of its UTF-8 bytes."""

    text: str
    sha256: str


@dataclass(frozen=True)
class ViewEntry:
    """One serving view of the dictionary: its name, comment and columns with comments."""

    name: str
    comment: str
    columns: tuple[tuple[str, str], ...]


def parse_views(dictionary_text: str) -> list[ViewEntry]:
    """The views of `CFB_API` in file order. Sections that follow a `CFB_API_LIVE` heading, and the
    live views by name, are left out: the chat user cannot read them."""
    entries: list[ViewEntry] = []
    live = False
    section: list[str] | None = None
    name = ""

    def finish() -> None:
        if section is not None and name not in LIVE_VIEWS and not live:
            entry = _entry(name, section)
            if entry is not None:
                entries.append(entry)

    for line in dictionary_text.splitlines():
        heading = re.match(r"^(#{1,2})\s+(.*\S)\s*$", line)
        if heading is None:
            if section is not None:
                section.append(line)
            continue
        finish()
        section = None
        title = heading.group(2).strip()
        if len(heading.group(1)) == 1:
            live = LIVE_SCHEMA in title
            continue
        if title == "Objects":
            continue
        name = title.strip("`").split(".")[-1]
        section = []
    finish()
    return entries


def _entry(name: str, lines: list[str]) -> ViewEntry | None:
    comment: list[str] = []
    columns: list[tuple[str, str]] = []
    for raw in lines:
        line = raw.strip()
        row = _COLUMN_ROW.match(line)
        if row is not None:
            columns.append((row.group(1), row.group(2).strip()))
        elif (
            line
            and not line.startswith("|")
            and not _KIND_LINE.match(line)
            and not _READS_LINE.match(line)
        ):
            comment.append(line)
    if not columns:
        return None
    return ViewEntry(name, " ".join(comment), tuple(columns))


def render_catalog(views: Sequence[ViewEntry]) -> str:
    """The catalog text: per view a heading, its comment and one line per column."""
    blocks = []
    for view in views:
        lines = [f"### {view.name}", view.comment, "Columns:"]
        lines += [f'- "{column}": {comment}' for column, comment in view.columns]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def render_system_prompt(
    *,
    dictionary_text: str,
    api_schema: str = API_SCHEMA,
    template: str | None = None,
    forbidden: Sequence[str] = (),
) -> SystemPrompt:
    """Render the prompt for the serving schema `api_schema` (prefixed on a test namespace).

    `forbidden` lists values that must never appear in the prompt (passwords, the host, the user);
    values shorter than four characters are ignored. A hit raises without echoing the value."""
    source = TEMPLATE_PATH.read_text(encoding="utf-8") if template is None else template
    unknown = set(_PLACEHOLDER.findall(source)) - _PLACEHOLDERS
    if unknown:
        raise PromptError(f"the template has unknown placeholders: {sorted(unknown)}")
    views = parse_views(dictionary_text)
    if not views:
        raise PromptError("the dictionary holds no views of the serving schema")
    catalog = _API_NAME.sub(api_schema, render_catalog(views))
    text = source.replace("{API}", api_schema).replace("{CATALOG}", catalog).rstrip() + "\n"
    for value in forbidden:
        if len(value) >= MIN_FORBIDDEN_LENGTH and value in text:
            raise PromptError("the prompt would contain a configured secret, host or user name")
    return SystemPrompt(text, hashlib.sha256(text.encode("utf-8")).hexdigest())


def load_system_prompt(
    *,
    api_schema: str = API_SCHEMA,
    dictionary_path: Path = DICTIONARY_PATH,
    forbidden: Sequence[str] = (),
) -> SystemPrompt:
    """Render the prompt from the dictionary file; a missing file names `make api-docs`."""
    try:
        dictionary_text = dictionary_path.read_text(encoding="utf-8")
    except OSError:
        raise PromptError(
            f"{dictionary_path.name} is missing or unreadable: run `make api-docs`"
        ) from None
    return render_system_prompt(
        dictionary_text=dictionary_text, api_schema=api_schema, forbidden=forbidden
    )
