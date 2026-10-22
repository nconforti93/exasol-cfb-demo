"""Virtual schema properties: defaults, validation and merging (stdlib only).

Exasol hands the adapter every property as text. `parse_properties` turns them into a typed,
validated `Properties`, with the spec defaults for unset, NULL or empty values. It rejects
unknown names and invalid values, listing every problem, one per line, each naming the property
and its allowed values. Keyword values (`live`, `TRUE`, `off`) are case-insensitive.

The values become SQL string literals or quoted identifiers in the pushdown SQL and arguments of
the fetch UDF. They are therefore restricted to safe shapes: schema and connection names are
upper-case regular identifiers, and the replay path uses only plain path characters.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from vs.cfbd_vs import fetch, resolver
from vs.cfbd_vs.filters import check_text
from vs.cfbd_vs.replay import DEFAULT_REPLAY_PATH
from vs.cfbd_vs.rules import KNOWN_FEATURES, parse_api_features

ERROR_PREFIX = "CFBD_VS"
DEFAULT_MAX_CALLS_PER_QUERY = 10
MIN_MAX_CALLS_PER_QUERY = 1
MAX_MAX_CALLS_PER_QUERY = 1000
MAX_UDF_TEXT_LENGTH = 1024
MAX_IDENTIFIER_LENGTH = 128
MESSAGE_VALUE_LIMIT = 60

KNOWN_PROPERTIES = (
    "CONNECTION_NAME",
    "FETCH_MODE",
    "REPLAY_PATH",
    "MAX_CALLS_PER_QUERY",
    "MAX_PARALLEL_FETCHES",
    "READ_THROUGH",
    "API_FEATURES",
    "DOH_URL",
    "INFRA_SCHEMA",
    "META_SCHEMA",
    "CORE_SCHEMA",
)

_IDENTIFIER = re.compile(rf"[A-Z][A-Z0-9_]{{0,{MAX_IDENTIFIER_LENGTH - 1}}}")
_DIGITS = re.compile(r"[0-9]{1,9}")
_BUCKETFS_PATH = re.compile(r"/buckets(?:/[A-Za-z0-9._-]+){2,}/?")
_DOT_SEGMENTS = (".", "..")


class PropertyError(ValueError):
    """Invalid or unknown virtual schema properties; one problem per message line."""

    def __init__(self, problems: tuple[str, ...]) -> None:
        super().__init__("\n".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class Properties:
    """The validated settings of one virtual schema, with the spec defaults."""

    connection_name: str = fetch.DEFAULT_CONNECTION_NAME
    fetch_mode: str = fetch.MODE_LIVE
    replay_path: str = DEFAULT_REPLAY_PATH
    max_calls_per_query: int = DEFAULT_MAX_CALLS_PER_QUERY
    max_parallel_fetches: int = fetch.DEFAULT_PARALLEL_FETCHES
    read_through: bool = True
    api_features: frozenset[str] = frozenset()
    doh_url: str = resolver.DEFAULT_DOH_URL
    infra_schema: str = "CFB_INFRA"
    meta_schema: str = "CFB_META"
    core_schema: str = "CFB_CORE"


@dataclass(frozen=True)
class _Spec:
    field: str
    allowed: str
    parse: Callable[[str], Any]


def parse_properties(raw: Mapping[str, Any] | None) -> Properties:
    """Validate the raw property map and apply the defaults; raises `PropertyError`."""
    values = dict(raw or {})
    problems: list[str] = []
    fields: dict[str, Any] = {}
    for name in KNOWN_PROPERTIES:
        value = values.get(name)
        if value is None or value == "":
            continue
        spec = _SPECS[name]
        try:
            fields[spec.field] = spec.parse(_text(value))
        except ValueError:
            problems.append(
                f"{ERROR_PREFIX}: property {name} must be {spec.allowed}, got {_show(value)}"
            )
    unknown = sorted(name for name in values if name not in _SPECS)
    if unknown:
        noun = "property" if len(unknown) == 1 else "properties"
        problems.append(
            f"{ERROR_PREFIX}: unknown {noun} {', '.join(unknown)}; "
            f"known properties: {', '.join(KNOWN_PROPERTIES)}"
        )
    if problems:
        raise PropertyError(tuple(problems))
    return Properties(**fields)


def merge_properties(
    current: Mapping[str, Any] | None, changes: Mapping[str, Any] | None
) -> dict[str, Any]:
    """The properties after `ALTER VIRTUAL SCHEMA … SET`: NULL or empty values delete a property."""
    merged = {name: value for name, value in (current or {}).items() if not _unset(value)}
    for name, value in (changes or {}).items():
        if _unset(value):
            merged.pop(name, None)
        else:
            merged[name] = value
    return merged


def _unset(value: Any) -> bool:
    return value is None or value == ""


def _text(value: Any) -> str:
    if not isinstance(value, str) or check_text(value) is not None:
        raise ValueError
    return value


def _show(value: Any) -> str:
    if not isinstance(value, str):
        return repr(value)[:MESSAGE_VALUE_LIMIT]
    shown = repr(value[:MESSAGE_VALUE_LIMIT])
    return shown + "..." if len(value) > MESSAGE_VALUE_LIMIT else shown


def _integer(low: int, high: int) -> Callable[[str], int]:
    def parse(text: str) -> int:
        if not _DIGITS.fullmatch(text) or not low <= int(text) <= high:
            raise ValueError
        return int(text)

    return parse


def _keyword(options: Mapping[str, Any]) -> Callable[[str], Any]:
    def parse(text: str) -> Any:
        if text.lower() not in options:
            raise ValueError
        return options[text.lower()]

    return parse


def _features(text: str) -> frozenset[str]:
    features = parse_api_features(text)
    if not features <= frozenset(KNOWN_FEATURES):
        raise ValueError
    return features


def _doh_url(text: str) -> str:
    if text.lower() == resolver.DOH_OFF:
        return resolver.DOH_OFF
    if len(text) > MAX_UDF_TEXT_LENGTH:
        raise ValueError
    return resolver.validate_doh_url(text)


def _identifier(text: str) -> str:
    if not _IDENTIFIER.fullmatch(text):
        raise ValueError
    return text


def _replay_path(text: str) -> str:
    if len(text) > MAX_UDF_TEXT_LENGTH or not _BUCKETFS_PATH.fullmatch(text):
        raise ValueError
    if any(segment in _DOT_SEGMENTS for segment in text.split("/")):
        raise ValueError
    return text


_IDENTIFIER_ALLOWED = (
    "an upper-case Exasol identifier (A-Z, 0-9 and _, starting with a letter, "
    f"at most {MAX_IDENTIFIER_LENGTH} characters)"
)
_SPECS: Mapping[str, _Spec] = {
    "CONNECTION_NAME": _Spec("connection_name", _IDENTIFIER_ALLOWED, _identifier),
    "FETCH_MODE": _Spec(
        "fetch_mode",
        f"one of {fetch.MODE_LIVE}, {fetch.MODE_REPLAY}",
        _keyword({fetch.MODE_LIVE: fetch.MODE_LIVE, fetch.MODE_REPLAY: fetch.MODE_REPLAY}),
    ),
    "REPLAY_PATH": _Spec(
        "replay_path",
        "an absolute UDF-visible BucketFS path /buckets/<service>/<bucket>[/<directory>...] "
        "with segments of A-Z, a-z, 0-9, '.', '_' or '-' (at most "
        f"{MAX_UDF_TEXT_LENGTH} characters), such as {DEFAULT_REPLAY_PATH}",
        _replay_path,
    ),
    "MAX_CALLS_PER_QUERY": _Spec(
        "max_calls_per_query",
        f"an integer from {MIN_MAX_CALLS_PER_QUERY} to {MAX_MAX_CALLS_PER_QUERY}",
        _integer(MIN_MAX_CALLS_PER_QUERY, MAX_MAX_CALLS_PER_QUERY),
    ),
    "MAX_PARALLEL_FETCHES": _Spec(
        "max_parallel_fetches",
        f"an integer from {fetch.MIN_PARALLEL_FETCHES} to {fetch.MAX_PARALLEL_FETCHES}",
        _integer(fetch.MIN_PARALLEL_FETCHES, fetch.MAX_PARALLEL_FETCHES),
    ),
    "READ_THROUGH": _Spec(
        "read_through", "one of TRUE, FALSE", _keyword({"true": True, "false": False})
    ),
    "API_FEATURES": _Spec(
        "api_features",
        f"a comma-separated subset of {', '.join(KNOWN_FEATURES)} (empty means none)",
        _features,
    ),
    "DOH_URL": _Spec(
        "doh_url",
        f"'{resolver.DOH_OFF}' or an https URL whose host is an IP literal "
        f"(at most {MAX_UDF_TEXT_LENGTH} characters)",
        _doh_url,
    ),
    "INFRA_SCHEMA": _Spec("infra_schema", _IDENTIFIER_ALLOWED, _identifier),
    "META_SCHEMA": _Spec("meta_schema", _IDENTIFIER_ALLOWED, _identifier),
    "CORE_SCHEMA": _Spec("core_schema", _IDENTIFIER_ALLOWED, _identifier),
}
