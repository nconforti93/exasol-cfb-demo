"""Reviewed catalog overrides: schema, loader and validator (host-side, uses PyYAML).

`vs/catalog-overrides.yaml` holds what the OpenAPI spec states only in prose. `parse_overrides`
validates it against a `SpecIndex` (endpoints, parameters, response fields), lists every
offending entry in one `OverridesError`, and returns typed entries. Rule entries become
`catalog.Rule` objects with the ids `<endpoint slug>.<local id>` and the `details` layout that
`vs.cfbd_vs.rules` evaluates.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from vs.cfbd_vs.catalog import ExcludedEndpoint, Rule
from vs.cfbd_vs.naming import NamingError, table_name_from_path, to_upper_snake
from vs.cfbd_vs.rules import KNOWN_FEATURES
from vs.cfbd_vs.sqltypes import MAX_VARCHAR_SIZE

FieldPath = tuple[str, ...]

PARAM_TYPES = ("integer", "number", "boolean", "string")
FIELD_TYPES = ("string", "integer", "number", "boolean", "json")
MAX_SCHEMA_DEPTH = 12

_LOCAL_ID = re.compile(r"^[a-z][a-z0-9_]*$")
_COLUMN_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
_REQUIRED_RULE_TYPES = ("required",)

ENDPOINT_KEYS = frozenset(
    {
        "defaults",
        "verify",
        "hoist",
        "rules",
        "row_cap",
        "gated_by",
        "curated",
        "raw_json",
        "read_through",
        "params",
        "fields",
    }
)
TOP_LEVEL_KEYS = frozenset({"endpoints", "children", "acknowledged", "exclude"})


class OverridesError(ValueError):
    """The overrides file is invalid; `errors` lists every offending entry."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("invalid catalog overrides:\n" + "\n".join(f"  - {e}" for e in errors))
        self.errors = tuple(errors)


@dataclass(frozen=True)
class ParamInfo:
    """An API parameter as the spec declares it."""

    name: str
    type: str
    enum: tuple[str, ...] | None
    location: str


@dataclass(frozen=True)
class EndpointInfo:
    """What the overrides may refer to on one endpoint."""

    path: str
    params: Mapping[str, ParamInfo]
    fields: frozenset[FieldPath]
    arrays: frozenset[FieldPath]


@dataclass(frozen=True)
class SpecIndex:
    """The pinned spec reduced to the facts overrides are validated against."""

    endpoints: Mapping[str, EndpointInfo]

    @classmethod
    def from_openapi(cls, spec: Mapping[str, Any]) -> SpecIndex:
        """Index every GET operation: parameters (refs resolved) and response field paths."""
        resolver = _SchemaResolver(spec)
        endpoints: dict[str, EndpointInfo] = {}
        for path, operations in spec.get("paths", {}).items():
            operation = operations.get("get")
            if operation is None:
                continue
            fields: set[FieldPath] = set()
            arrays: set[FieldPath] = set()
            resolver.collect(_response_schema(operation), (), (), fields, arrays)
            endpoints[path] = EndpointInfo(
                path=path,
                params={p.name: p for p in resolver.parameters(operation)},
                fields=frozenset(fields),
                arrays=frozenset(arrays),
            )
        return cls(endpoints)


@dataclass(frozen=True)
class Hoist:
    """Promote the elements of a nested array to the rows of the endpoint table."""

    array: FieldPath
    carry: Mapping[FieldPath, str]


@dataclass(frozen=True)
class ParamOverride:
    type: str


@dataclass(frozen=True)
class FieldOverride:
    type: str | None
    size: int | None
    rename: str | None


@dataclass(frozen=True)
class EndpointOverrides:
    """All overrides of one endpoint. `rules` includes the default and row-cap rules."""

    path: str
    rules: tuple[Rule, ...] = ()
    defaults: Mapping[str, Any] = field(default_factory=dict)
    gated_by: str | None = None
    hoist: Hoist | None = None
    curated: tuple[FieldPath, ...] | None = None
    raw_json: bool = False
    read_through: bool | None = None
    verify: tuple[str, ...] = ()
    params: Mapping[str, ParamOverride] = field(default_factory=dict)
    fields: Mapping[FieldPath, FieldOverride] = field(default_factory=dict)


@dataclass(frozen=True)
class ChildOverrides:
    table: str
    parent_keys: tuple[FieldPath, ...]


@dataclass(frozen=True)
class Acknowledgement:
    """Prose that the lint matches but that needs no rule."""

    endpoint: str
    param: str | None
    why: str


@dataclass(frozen=True)
class Overrides:
    endpoints: Mapping[str, EndpointOverrides]
    children: Mapping[str, ChildOverrides]
    acknowledged: tuple[Acknowledgement, ...]
    excluded: tuple[ExcludedEndpoint, ...]


def load_overrides(path: Path, spec: SpecIndex) -> Overrides:
    """Read and validate an overrides file."""
    return parse_overrides(path.read_text(encoding="utf-8"), spec)


def parse_overrides(text: str, spec: SpecIndex) -> Overrides:
    """Validate overrides YAML text against the spec index; raise `OverridesError` on any error."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise OverridesError([f"not valid YAML: {error}"]) from None
    parser = _Parser(spec)
    result = parser.parse(data)
    if parser.errors:
        raise OverridesError(parser.errors)
    return result


def rule_slug(path: str) -> str:
    """The rule id prefix of an endpoint: its table name, lowercase."""
    return table_name_from_path(path).lower()


def _response_schema(operation: Mapping[str, Any]) -> Mapping[str, Any]:
    content = operation.get("responses", {}).get("200", {}).get("content", {})
    schema: Mapping[str, Any] = content.get("application/json", {}).get("schema", {})
    return schema


class _SchemaResolver:
    def __init__(self, spec: Mapping[str, Any]) -> None:
        self._components = spec.get("components", {})

    def deref(self, schema: Mapping[str, Any]) -> tuple[Mapping[str, Any], str | None]:
        ref = schema.get("$ref")
        if ref is None:
            return schema, None
        parts = ref.removeprefix("#/").split("/")
        node: Any = {"components": self._components}
        for part in parts:
            node = node[part]
        return node, ref

    def parameters(self, operation: Mapping[str, Any]) -> list[ParamInfo]:
        result = []
        for raw in operation.get("parameters", []):
            declared, _ = self.deref(raw)
            result.append(self._param(declared))
        return result

    def _param(self, declared: Mapping[str, Any]) -> ParamInfo:
        schema = self._flatten(declared.get("schema", {}))
        enum = schema.get("enum")
        return ParamInfo(
            name=declared["name"],
            type=schema.get("type", "string"),
            enum=None if enum is None else tuple(str(v) for v in enum),
            location=declared.get("in", "query"),
        )

    def _flatten(self, schema: Mapping[str, Any]) -> Mapping[str, Any]:
        resolved, _ = self.deref(schema)
        for branch in resolved.get("allOf", []):
            resolved = {**self._flatten(branch), **resolved}
        return resolved

    def collect(
        self,
        schema: Mapping[str, Any],
        path: FieldPath,
        seen: tuple[str, ...],
        fields: set[FieldPath],
        arrays: set[FieldPath],
    ) -> None:
        resolved, ref = self.deref(schema)
        if ref is not None:
            if ref in seen:
                return
            seen = (*seen, ref)
        if len(path) > MAX_SCHEMA_DEPTH:
            return
        for keyword in ("allOf", "oneOf", "anyOf"):
            for branch in resolved.get(keyword, []):
                self.collect(branch, path, seen, fields, arrays)
        if "items" in resolved:
            arrays.add(path)
            self.collect(resolved["items"], path, seen, fields, arrays)
        for name, child in resolved.get("properties", {}).items():
            fields.add((*path, name))
            self.collect(child, (*path, name), seen, fields, arrays)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _dotted(text: str) -> FieldPath:
    return tuple(text.split("."))


class _Parser:
    def __init__(self, spec: SpecIndex) -> None:
        self._spec = spec
        self.errors: list[str] = []

    def error(self, where: str, message: str) -> None:
        self.errors.append(f"{where}: {message}")

    def parse(self, data: Any) -> Overrides:
        if data is None:
            data = {}
        if not isinstance(data, dict):
            self.error("document", "expected a mapping at the top level")
            return Overrides({}, {}, (), ())
        for key in data:
            if key not in TOP_LEVEL_KEYS:
                self.error(repr(key), f"unknown top-level key (expected {sorted(TOP_LEVEL_KEYS)})")
        excluded = self._excluded(data.get("exclude"))
        endpoints = self._endpoints(data.get("endpoints"), {e.path for e in excluded})
        children = self._children(data.get("children"))
        acknowledged = self._acknowledged(data.get("acknowledged"))
        return Overrides(endpoints, children, tuple(acknowledged), tuple(excluded))

    def mapping(self, value: Any, where: str) -> dict[str, Any] | None:
        if value is None:
            return {}
        if not isinstance(value, dict):
            self.error(where, "expected a mapping")
            return None
        return value

    def sequence(self, value: Any, where: str) -> list[Any] | None:
        if value is None:
            return []
        if not isinstance(value, list):
            self.error(where, "expected a list")
            return None
        return value

    def entry_keys(
        self, entry: Mapping[str, Any], required: set[str], optional: set[str], where: str
    ) -> bool:
        ok = True
        for key in sorted(required - entry.keys()):
            self.error(where, f"missing key {key!r}")
            ok = False
        for key in sorted(entry.keys() - required - optional):
            self.error(where, f"unknown key {key!r}")
            ok = False
        return ok

    def _text(self, value: Any, where: str, what: str) -> str | None:
        if not isinstance(value, str) or not value.strip():
            self.error(where, f"{what} must be a non-empty string")
            return None
        return value

    def _excluded(self, raw: Any) -> list[ExcludedEndpoint]:
        result: list[ExcludedEndpoint] = []
        entries = self.sequence(raw, "exclude") or []
        seen: set[str] = set()
        for index, entry in enumerate(entries):
            where = f"exclude[{index}]"
            if not isinstance(entry, dict):
                self.error(where, "expected a mapping with path and reason")
                continue
            if not self.entry_keys(entry, {"path"}, {"reason"}, where):
                continue
            path = entry["path"]
            if path not in self._spec.endpoints:
                self.error(where, f"unknown endpoint {path!r} (not a GET operation of the spec)")
                continue
            reason = self._text(entry.get("reason"), where, "reason")
            if path in seen:
                self.error(where, f"endpoint {path!r} is excluded twice")
            seen.add(path)
            if reason is not None:
                result.append(ExcludedEndpoint(path, reason))
        return result

    def _endpoints(self, raw: Any, excluded: set[str]) -> dict[str, EndpointOverrides]:
        result: dict[str, EndpointOverrides] = {}
        entries = self.mapping(raw, "endpoints") or {}
        for path, entry in entries.items():
            where = f"endpoints[{path!r}]"
            info = self._spec.endpoints.get(path)
            if info is None:
                self.error(where, "unknown endpoint (not a GET operation of the spec)")
                continue
            if path in excluded:
                self.error(where, "endpoint is excluded, so it cannot have other entries")
                continue
            body = self.mapping(entry, where)
            if body is not None:
                result[path] = self._endpoint(info, body, where)
        return result

    def _endpoint(
        self, info: EndpointInfo, body: Mapping[str, Any], where: str
    ) -> EndpointOverrides:
        for key in sorted(body.keys() - ENDPOINT_KEYS):
            self.error(where, f"unknown key {key!r}")
        param_overrides = self._param_overrides(info, body.get("params"), where)
        types = {name: p.type for name, p in info.params.items()}
        types.update({name: o.type for name, o in param_overrides.items()})
        rules = _RuleBuilder(self, info, types, where)
        rules.add_rule_entries(body.get("rules"))
        rules.add_defaults(body.get("defaults"))
        rules.add_row_cap_sugar(body.get("row_cap"))
        rules.check_defaults_not_required()
        curated = self._curated(info, body.get("curated"), where)
        raw_json = self._flag(body, "raw_json", where, False)
        if raw_json and curated is None:
            self.error(where, "raw_json applies only to a curated endpoint")
        read_through = self._flag(body, "read_through", where, None)
        return EndpointOverrides(
            path=info.path,
            rules=tuple(rules.rules),
            defaults=rules.defaults,
            gated_by=self._gated_by(body.get("gated_by"), where),
            hoist=self._hoist(info, body.get("hoist"), where),
            curated=curated,
            raw_json=bool(raw_json),
            read_through=read_through,
            verify=self._verify(body.get("verify"), where),
            params=param_overrides,
            fields=self._field_overrides(info, body.get("fields"), where),
        )

    def _flag(self, body: Mapping[str, Any], key: str, where: str, default: bool | None) -> Any:
        if key not in body:
            return default
        if not isinstance(body[key], bool):
            self.error(f"{where}.{key}", "expected true or false")
            return default
        return body[key]

    def _gated_by(self, value: Any, where: str) -> str | None:
        if value is None:
            return None
        if value not in KNOWN_FEATURES:
            self.error(
                f"{where}.gated_by",
                f"unknown feature {value!r} (known: {', '.join(KNOWN_FEATURES)})",
            )
            return None
        return str(value)

    def _verify(self, raw: Any, where: str) -> tuple[str, ...]:
        entries = self.sequence(raw, f"{where}.verify") or []
        if not all(isinstance(e, str) and e for e in entries):
            self.error(f"{where}.verify", "expected a list of non-empty strings")
            return ()
        return tuple(entries)

    def _field_path(self, info: EndpointInfo, text: Any, where: str) -> FieldPath | None:
        if not isinstance(text, str) or not text:
            self.error(where, f"response field {text!r} must be a dotted path string")
            return None
        path = _dotted(text)
        if path not in info.fields:
            self.error(where, f"unknown response field {text!r} on {info.path}")
            return None
        return path

    def _curated(self, info: EndpointInfo, raw: Any, where: str) -> tuple[FieldPath, ...] | None:
        if raw is None:
            return None
        entries = self.sequence(raw, f"{where}.curated")
        if entries is None:
            return None
        paths = [self._field_path(info, e, f"{where}.curated") for e in entries]
        return tuple(p for p in paths if p is not None)

    def _hoist(self, info: EndpointInfo, raw: Any, where: str) -> Hoist | None:
        if raw is None:
            return None
        here = f"{where}.hoist"
        body = self.mapping(raw, here)
        if body is None or not self.entry_keys(body, {"array", "carry"}, set(), here):
            return None
        array = self._field_path(info, body["array"], here)
        if array is not None and array not in info.arrays:
            self.error(here, f"{body['array']!r} is not an array on {info.path}")
            array = None
        carry_raw = self.mapping(body["carry"], f"{here}.carry") or {}
        carry: dict[FieldPath, str] = {}
        for source, column in carry_raw.items():
            path = self._field_path(info, source, f"{here}.carry")
            if not isinstance(column, str) or not _COLUMN_NAME.match(column):
                self.error(f"{here}.carry", f"column {column!r} must match {_COLUMN_NAME.pattern}")
            elif path is not None:
                carry[path] = column
        return None if array is None else Hoist(array, carry)

    def _param_overrides(
        self, info: EndpointInfo, raw: Any, where: str
    ) -> dict[str, ParamOverride]:
        result: dict[str, ParamOverride] = {}
        for name, entry in (self.mapping(raw, f"{where}.params") or {}).items():
            here = f"{where}.params[{name!r}]"
            if name not in info.params:
                self.error(here, f"unknown parameter {name!r} on {info.path}")
                continue
            body = self.mapping(entry, here)
            if body is None or not self.entry_keys(body, {"type"}, set(), here):
                continue
            if body["type"] not in PARAM_TYPES:
                self.error(here, f"type {body['type']!r} must be one of {', '.join(PARAM_TYPES)}")
                continue
            result[name] = ParamOverride(body["type"])
        return result

    def _field_overrides(
        self, info: EndpointInfo, raw: Any, where: str
    ) -> dict[FieldPath, FieldOverride]:
        result: dict[FieldPath, FieldOverride] = {}
        for name, entry in (self.mapping(raw, f"{where}.fields") or {}).items():
            here = f"{where}.fields[{name!r}]"
            path = self._field_path(info, name, here)
            body = self.mapping(entry, here)
            if path is None or body is None:
                continue
            override = self._field_override(body, here)
            if override is not None:
                result[path] = override
        return result

    def _field_override(self, body: Mapping[str, Any], where: str) -> FieldOverride | None:
        if not body:
            self.error(where, "expected at least one of type, size, rename")
            return None
        if not self.entry_keys(body, set(), {"type", "size", "rename"}, where):
            return None
        count = len(self.errors)
        field_type = body.get("type")
        if field_type is not None and field_type not in FIELD_TYPES:
            self.error(where, f"type {field_type!r} must be one of {', '.join(FIELD_TYPES)}")
        size = body.get("size")
        if size is not None:
            if not _is_int(size) or not 1 <= size <= MAX_VARCHAR_SIZE:
                self.error(where, f"size must be an integer from 1 to {MAX_VARCHAR_SIZE}")
            if field_type not in (None, "string"):
                self.error(where, "size applies only to the type string")
        rename = body.get("rename")
        if rename is not None and (not isinstance(rename, str) or not _COLUMN_NAME.match(rename)):
            self.error(where, f"rename {rename!r} must match {_COLUMN_NAME.pattern}")
        if len(self.errors) > count:
            return None
        return FieldOverride(field_type, size, rename)

    def _children(self, raw: Any) -> dict[str, ChildOverrides]:
        result: dict[str, ChildOverrides] = {}
        owners = self._table_owners()
        for table, entry in (self.mapping(raw, "children") or {}).items():
            where = f"children[{table!r}]"
            if not isinstance(table, str) or not _COLUMN_NAME.match(table):
                self.error(where, f"table name must match {_COLUMN_NAME.pattern}")
                continue
            body = self.mapping(entry, where)
            if body is None or not self.entry_keys(body, {"parent_keys"}, set(), where):
                continue
            candidates = [info for name, info in owners if table.startswith(name + "_")]
            if not candidates:
                self.error(where, "no endpoint table is a name prefix of this child table")
                continue
            keys = self._parent_keys(candidates, body["parent_keys"], where)
            if keys is not None:
                result[table] = ChildOverrides(table, keys)
        return result

    def _table_owners(self) -> list[tuple[str, EndpointInfo]]:
        owners = []
        for path, info in self._spec.endpoints.items():
            try:
                owners.append((table_name_from_path(path), info))
            except NamingError:
                continue
        return owners

    def _parent_keys(
        self, candidates: list[EndpointInfo], raw: Any, where: str
    ) -> tuple[FieldPath, ...] | None:
        entries = self.sequence(raw, f"{where}.parent_keys")
        if not entries:
            if entries is not None:
                self.error(where, "parent_keys must not be empty")
            return None
        paths: list[FieldPath] = []
        for entry in entries:
            if not isinstance(entry, str) or not entry:
                self.error(where, f"parent key {entry!r} must be a dotted path string")
                return None
            path = _dotted(entry)
            if not any(path in c.fields for c in candidates):
                self.error(where, f"unknown response field {entry!r} on the parent endpoint")
                return None
            paths.append(path)
        return tuple(paths)

    def _acknowledged(self, raw: Any) -> list[Acknowledgement]:
        result = []
        for index, entry in enumerate(self.sequence(raw, "acknowledged") or []):
            where = f"acknowledged[{index}]"
            if not isinstance(entry, dict):
                self.error(where, "expected a mapping with endpoint, optional param and why")
                continue
            if not self.entry_keys(entry, {"endpoint", "why"}, {"param"}, where):
                continue
            info = self._spec.endpoints.get(entry["endpoint"])
            if info is None:
                self.error(where, f"unknown endpoint {entry['endpoint']!r}")
                continue
            param = entry.get("param")
            if param is not None and param not in info.params:
                self.error(where, f"unknown parameter {param!r} on {info.path}")
                continue
            why = self._text(entry["why"], where, "why")
            if why is not None:
                result.append(Acknowledgement(info.path, param, why))
        return result


class _RuleBuilder:
    """Builds the catalog rules of one endpoint and reports every invalid entry."""

    def __init__(
        self, parser: _Parser, info: EndpointInfo, types: Mapping[str, str], where: str
    ) -> None:
        self._parser = parser
        self._info = info
        self._types = types
        self._where = where
        self._slug = rule_slug(info.path)
        self.rules: list[Rule] = []
        self.defaults: dict[str, Any] = {}
        self._has_row_cap = False

    def error(self, where: str, message: str) -> None:
        self._parser.error(where, message)

    def add_rule_entries(self, raw: Any) -> None:
        where = f"{self._where}.rules"
        for index, entry in enumerate(self._parser.sequence(raw, where) or []):
            self._rule_entry(entry, f"{where}[{index}]")

    def add_defaults(self, raw: Any) -> None:
        where = f"{self._where}.defaults"
        for name, value in (self._parser.mapping(raw, where) or {}).items():
            if self._param(name, where) is not None:
                local = "default_" + to_upper_snake(name).lower()
                self._default(local, name, value, where, None)

    def add_row_cap_sugar(self, raw: Any) -> None:
        if raw is None:
            return
        where = f"{self._where}.row_cap"
        body = self._parser.mapping(raw, where)
        if body is not None and self._parser.entry_keys(
            body, {"rows", "complete_with"}, {"why"}, where
        ):
            self._row_cap("row_cap", body, where)

    def check_defaults_not_required(self) -> None:
        for rule in self.rules:
            if rule.type != "required":
                continue
            for name in rule.params:
                if name in self.defaults:
                    self.error(
                        self._where,
                        f"rule {rule.id}: parameter {name!r} has a default and cannot be required",
                    )

    def _rule_entry(self, entry: Any, where: str) -> None:
        if not isinstance(entry, dict):
            self.error(where, "expected a mapping")
            return
        local = entry.get("id")
        if not isinstance(local, str) or not _LOCAL_ID.match(local):
            self.error(where, f"id {local!r} must match {_LOCAL_ID.pattern}")
            return
        handler = _ENTRY_SPECS.get(str(entry.get("type")))
        if handler is None:
            self.error(where, f"unknown rule type {entry.get('type')!r}")
            return
        required, optional = handler.required, handler.optional
        if not self._parser.entry_keys(entry, {"id", "type"} | required, {"why"} | optional, where):
            return
        why = entry.get("why", "")
        if not isinstance(why, str):
            self.error(where, "why must be a string")
            return
        builder = getattr(self, handler.method)
        builder(local, entry, where, why)

    def _register(
        self,
        local: str,
        rule_type: str,
        params: tuple[str, ...],
        why: str,
        details: dict[str, Any],
        where: str,
    ) -> None:
        rule_id = f"{self._slug}.{local}"
        if any(r.id == rule_id for r in self.rules):
            self.error(where, f"duplicate rule id {rule_id!r}")
            return
        self.rules.append(Rule(rule_id, rule_type, why, params, details))

    def _param(self, name: Any, where: str) -> str | None:
        if not isinstance(name, str) or name not in self._info.params:
            self.error(where, f"unknown parameter {name!r} on {self._info.path}")
            return None
        return name

    def _params(self, names: Any, where: str, minimum: int) -> tuple[str, ...] | None:
        if not isinstance(names, list) or len(names) < minimum:
            self.error(where, f"expected a list of at least {minimum} parameter name(s)")
            return None
        checked = [self._param(n, where) for n in names]
        if any(c is None for c in checked):
            return None
        return tuple(str(c) for c in checked)

    def _value(self, name: str, value: Any, where: str) -> bool:
        message = _value_problem(value, self._types[name], self._info.params[name].enum)
        if message:
            self.error(where, f"value for {name!r}: {message}")
        return message is None

    def _required(self, local: str, entry: Mapping[str, Any], where: str, why: str) -> None:
        params = self._params(entry["params"], where, 1)
        if params is not None:
            self._register(local, "required", params, why, {}, where)

    def _one_of(self, local: str, entry: Mapping[str, Any], where: str, why: str) -> None:
        params = self._params(entry["params"], where, 2)
        if params is not None:
            self._register(local, "one_of", params, why, {}, where)

    def _trigger(self, entry: Mapping[str, Any], where: str, details: dict[str, Any]) -> str | None:
        trigger = self._param(entry["if"], where)
        if trigger is None:
            return None
        details["if"] = trigger
        if "if_value" in entry:
            if not self._value(trigger, entry["if_value"], where):
                return None
            details["if_value"] = entry["if_value"]
        return trigger

    def _requires_any(self, local: str, entry: Mapping[str, Any], where: str, why: str) -> None:
        details: dict[str, Any] = {}
        trigger = self._trigger(entry, where, details)
        options = self._params(entry["any_of"], where, 1)
        if trigger is not None and options is not None:
            details["any_of"] = list(options)
            self._register(local, "requires_any", (trigger, *options), why, details, where)

    def _excludes(self, local: str, entry: Mapping[str, Any], where: str, why: str) -> None:
        details: dict[str, Any] = {}
        trigger = self._trigger(entry, where, details)
        clashing = self._params(entry["none_of"], where, 1)
        if trigger is not None and clashing is not None:
            details["none_of"] = list(clashing)
            self._register(local, "excludes", (trigger, *clashing), why, details, where)

    def _requires_value(self, local: str, entry: Mapping[str, Any], where: str, why: str) -> None:
        details: dict[str, Any] = {}
        trigger = self._trigger(entry, where, details)
        target = self._param(entry["then"], where)
        if trigger is None or target is None or not self._value(target, entry["then_value"], where):
            return
        details.update({"then": target, "then_value": entry["then_value"]})
        self._register(local, "requires_value", (trigger, target), why, details, where)

    def _bounds(self, local: str, entry: Mapping[str, Any], where: str, why: str) -> None:
        name = self._param(entry["param"], where)
        if name is None:
            return
        details = {k: entry[k] for k in ("min", "max") if k in entry}
        if not details:
            self.error(where, "bounds needs min, max or both")
            return
        if self._types[name] not in ("integer", "number"):
            self.error(where, f"bounds needs a numeric parameter, {name!r} is {self._types[name]}")
            return
        if not all(_is_number(v) for v in details.values()):
            self.error(where, "min and max must be numbers")
            return
        if len(details) == 2 and details["min"] > details["max"]:
            self.error(where, "min must not exceed max")
            return
        self._register(local, "bounds", (name,), why, details, where)

    def _default_entry(self, local: str, entry: Mapping[str, Any], where: str, why: str) -> None:
        name = self._param(entry["param"], where)
        if name is not None:
            self._default(local, name, entry["value"], where, why)

    def _default(self, local: str, name: str, value: Any, where: str, why: str | None) -> None:
        if name in self.defaults:
            self.error(where, f"parameter {name!r} already has a default")
            return
        if not self._value(name, value, where):
            return
        self.defaults[name] = value
        self._register(local, "default", (name,), why or "", {"value": value}, where)

    def _row_cap_entry(self, local: str, entry: Mapping[str, Any], where: str, why: str) -> None:
        self._row_cap(local, entry, where)

    def _row_cap(self, local: str, entry: Mapping[str, Any], where: str) -> None:
        rows = entry["rows"]
        if not _is_int(rows) or rows < 1:
            self.error(where, "rows must be a positive integer")
            return
        complete_with = self._params(entry["complete_with"], where, 1)
        if complete_with is None:
            return
        if self._has_row_cap:
            self.error(where, "an endpoint has at most one row_cap")
            return
        self._has_row_cap = True
        why = entry.get("why", "")
        details = {"rows": rows, "complete_with": list(complete_with)}
        self._register(local, "row_cap", complete_with, why, details, where)


@dataclass(frozen=True)
class _EntrySpec:
    method: str
    required: frozenset[str]
    optional: frozenset[str] = frozenset()


_ENTRY_SPECS: dict[str, _EntrySpec] = {
    "required": _EntrySpec("_required", frozenset({"params"})),
    "one_of": _EntrySpec("_one_of", frozenset({"params"})),
    "requires_any": _EntrySpec(
        "_requires_any", frozenset({"if", "any_of"}), frozenset({"if_value"})
    ),
    "excludes": _EntrySpec("_excludes", frozenset({"if", "none_of"}), frozenset({"if_value"})),
    "requires_value": _EntrySpec(
        "_requires_value", frozenset({"if", "if_value", "then", "then_value"})
    ),
    "bounds": _EntrySpec("_bounds", frozenset({"param"}), frozenset({"min", "max"})),
    "default": _EntrySpec("_default_entry", frozenset({"param", "value"})),
    "row_cap": _EntrySpec("_row_cap_entry", frozenset({"rows", "complete_with"})),
}


def _value_problem(value: Any, param_type: str, enum: tuple[str, ...] | None) -> str | None:
    if param_type == "integer" and not _is_int(value):
        return f"{value!r} is not an integer"
    if param_type == "number" and not _is_number(value):
        return f"{value!r} is not a number"
    if param_type == "boolean" and not isinstance(value, bool):
        return f"{value!r} is not true or false"
    if param_type == "string" and not isinstance(value, str):
        return f"{value!r} is not a string"
    if enum is not None and value not in enum:
        return f"{value!r} is not one of {', '.join(enum)}"
    return None
