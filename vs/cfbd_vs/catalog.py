"""Catalog model: the single description of every virtual table (stdlib only).

The generator writes it, the adapter and the fetch UDF read it. Serialization is canonical, so
the same catalog always produces the same bytes.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from vs.cfbd_vs.sqltypes import SqlTypeError, parse_sql_type

RULE_TYPES = (
    "required",
    "one_of",
    "requires_any",
    "excludes",
    "requires_value",
    "bounds",
    "default",
    "row_cap",
)
COLUMN_KINDS = ("position", "parent_key", "value", "raw_json", "param", "meta")
TABLE_KINDS = ("endpoint", "child")
PARAM_LOCATIONS = ("query", "path")


class CatalogError(ValueError):
    """The catalog is malformed or a lookup failed."""


def _check_member(value: str, allowed: tuple[str, ...], what: str) -> None:
    if value not in allowed:
        raise CatalogError(f"unknown {what} {value!r}, expected one of {', '.join(allowed)}")


def _canonical(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _canonical(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    return value


def _get(data: Mapping[str, Any], key: str, context: str) -> Any:
    if key not in data:
        raise CatalogError(f"{context}: missing key {key!r}")
    return data[key]


def _as_mapping(value: Any, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CatalogError(f"{context}: expected a JSON object")
    return value


def _as_list(value: Any, context: str) -> list[Any]:
    if not isinstance(value, list):
        raise CatalogError(f"{context}: expected a JSON array")
    return value


@dataclass(frozen=True)
class Rule:
    """A requirement rule enforced at plan time. `details` holds the type-specific fields."""

    id: str
    type: str
    message: str
    params: tuple[str, ...] = ()
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _check_member(self.type, RULE_TYPES, "rule type")

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "type": self.type,
            "params": list(self.params),
            "message": self.message,
        }
        if self.details:
            data["details"] = _canonical(self.details)
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Rule:
        context = "rule"
        return cls(
            id=_get(data, "id", context),
            type=_get(data, "type", context),
            message=_get(data, "message", context),
            params=tuple(_get(data, "params", context)),
            details=dict(data.get("details", {})),
        )


@dataclass(frozen=True)
class Param:
    """An API parameter and its echo column."""

    name: str
    column: str
    location: str
    type: str
    sql_type: str
    enum: tuple[str, ...] | None

    def __post_init__(self) -> None:
        _check_member(self.location, PARAM_LOCATIONS, "parameter location")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "column": self.column,
            "in": self.location,
            "type": self.type,
            "sql_type": self.sql_type,
            "enum": None if self.enum is None else list(self.enum),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Param:
        context = "param"
        enum = _get(data, "enum", context)
        return cls(
            name=_get(data, "name", context),
            column=_get(data, "column", context),
            location=_get(data, "in", context),
            type=_get(data, "type", context),
            sql_type=_get(data, "sql_type", context),
            enum=None if enum is None else tuple(enum),
        )


@dataclass(frozen=True)
class Column:
    """A table column. `path` is a tuple of JSON keys and depends on the kind:

    - `value`: relative to the table's own row element (`()` is the element itself);
    - `parent_key`: from the endpoint's row element, crossing array names (`polls.poll`);
    - `position`: the array it numbers, relative to the enclosing element; `ROW_NO` has `()` for
      an array response and no path for a single-object response (always 1);
    - `raw_json`, `param`, `meta`: no path.

    `json` marks a column that holds JSON text rather than a plain string.
    """

    name: str
    kind: str
    path: tuple[str, ...] | None = None
    sql_type: str | None = None
    comment: str | None = None
    json: bool = False

    def __post_init__(self) -> None:
        _check_member(self.kind, COLUMN_KINDS, "column kind")

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"name": self.name, "kind": self.kind}
        if self.path is not None:
            data["path"] = list(self.path)
        if self.sql_type is not None:
            data["sql_type"] = self.sql_type
        if self.comment is not None:
            data["comment"] = self.comment
        if self.json:
            data["json"] = True
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Column:
        context = "column"
        path = data.get("path")
        return cls(
            name=_get(data, "name", context),
            kind=_get(data, "kind", context),
            path=None if path is None else tuple(path),
            sql_type=data.get("sql_type"),
            comment=data.get("comment"),
            json=bool(data.get("json", False)),
        )


@dataclass(frozen=True)
class Table:
    """One virtual table: an endpoint table or a child table of a nested array.

    `hoisted` is informational (the child array was lifted into this endpoint table); nothing at
    runtime reads it.
    """

    name: str
    endpoint: str
    kind: str
    parent: str | None
    array_path: tuple[str, ...]
    hoisted: bool
    read_through: bool
    gated_by: str | None
    params: tuple[Param, ...]
    columns: tuple[Column, ...]
    rules: tuple[Rule, ...]

    def __post_init__(self) -> None:
        _check_member(self.kind, TABLE_KINDS, "table kind")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "endpoint": self.endpoint,
            "kind": self.kind,
            "parent": self.parent,
            "array_path": list(self.array_path),
            "hoisted": self.hoisted,
            "read_through": self.read_through,
            "gated_by": self.gated_by,
            "params": [p.to_dict() for p in self.params],
            "columns": [c.to_dict() for c in self.columns],
            "rules": [r.to_dict() for r in self.rules],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Table:
        context = f"table {data.get('name', '?')}"
        return cls(
            name=_get(data, "name", context),
            endpoint=_get(data, "endpoint", context),
            kind=_get(data, "kind", context),
            parent=_get(data, "parent", context),
            array_path=tuple(_get(data, "array_path", context)),
            hoisted=_get(data, "hoisted", context),
            read_through=_get(data, "read_through", context),
            gated_by=_get(data, "gated_by", context),
            params=tuple(Param.from_dict(p) for p in _get(data, "params", context)),
            columns=tuple(Column.from_dict(c) for c in _get(data, "columns", context)),
            rules=tuple(Rule.from_dict(r) for r in _get(data, "rules", context)),
        )


@dataclass(frozen=True)
class ExcludedEndpoint:
    """A spec operation that is deliberately not a table, with the reason."""

    path: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "reason": self.reason}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ExcludedEndpoint:
        return cls(path=_get(data, "path", "excluded"), reason=_get(data, "reason", "excluded"))


@dataclass(frozen=True)
class Catalog:
    """The complete catalog. Tables and exclusions serialize in sorted order."""

    catalog_hash: str
    spec_version: str
    generator_version: str
    excluded: tuple[ExcludedEndpoint, ...]
    tables: tuple[Table, ...]

    def __post_init__(self) -> None:
        seen: set[str] = set()
        for table in self.tables:
            if table.name in seen:
                raise CatalogError(f"duplicate table name {table.name}")
            seen.add(table.name)
            _check_types(table)

    def table(self, name: str) -> Table:
        """Return the table called `name`."""
        for table in self.tables:
            if table.name == name:
                return table
        raise CatalogError(f"catalog has no table {name}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "catalog_hash": self.catalog_hash,
            "spec_version": self.spec_version,
            "generator_version": self.generator_version,
            "excluded": [e.to_dict() for e in sorted(self.excluded, key=lambda e: e.path)],
            "tables": [t.to_dict() for t in sorted(self.tables, key=lambda t: t.name)],
        }

    def content_hash(self) -> str:
        """SHA-256 of the canonical compact JSON with `catalog_hash` blanked: covers all content."""
        blank = dataclasses.replace(self, catalog_hash="")
        return hashlib.sha256(blank.to_json(indent=None).encode("utf-8")).hexdigest()

    def with_content_hash(self) -> Catalog:
        """Return a copy whose `catalog_hash` is its `content_hash()`."""
        return dataclasses.replace(self, catalog_hash=self.content_hash())

    def to_json(self, indent: int | None = 2) -> str:
        """Canonical JSON text; pass `indent=None` for the compact form embedded in scripts."""
        separators = (",", ":") if indent is None else None
        text = json.dumps(self.to_dict(), indent=indent, separators=separators, ensure_ascii=False)
        return text + "\n"

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Catalog:
        context = "catalog"
        return cls(
            catalog_hash=_get(data, "catalog_hash", context),
            spec_version=_get(data, "spec_version", context),
            generator_version=_get(data, "generator_version", context),
            excluded=_load_all(ExcludedEndpoint, _get(data, "excluded", context), "excluded"),
            tables=_load_all(Table, _get(data, "tables", context), "tables"),
        )

    @classmethod
    def from_json(cls, text: str) -> Catalog:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as error:
            raise CatalogError(f"catalog is not valid JSON: {error}") from None
        return cls.from_dict(_as_mapping(data, "catalog"))


def _check_types(table: Table) -> None:
    typed = [(c.name, c.sql_type) for c in table.columns] + [
        (p.name, p.sql_type) for p in table.params
    ]
    for name, sql_type in typed:
        if sql_type is None:
            continue
        try:
            parse_sql_type(sql_type)
        except SqlTypeError as error:
            raise CatalogError(f"{table.name}.{name}: {error}") from None


def _load_all(loader: Any, items: Any, context: str) -> tuple[Any, ...]:
    return tuple(loader.from_dict(_as_mapping(item, context)) for item in _as_list(items, context))
