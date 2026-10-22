"""Table derivation: walked GET operations + reviewed overrides → catalog tables (host-side).

Every non-excluded operation becomes one endpoint table; every array of objects becomes a child
table per level (`RANKINGS` → `RANKINGS_POLLS` → `RANKINGS_POLLS_RANKS`). Columns per table, in
order: position columns, parent keys, response columns (spec order), `RAW_JSON` (curated only),
`P_*` (spec order), `REQ_*`. Every column carries its SQL type.

Path conventions (see `catalog.Column`): value paths are relative to the table's own element,
parent-key paths start at the endpoint's row element and cross array names, position columns
carry the array they number. `ROW_NO` has the path `()` for an array response and none for a
single-object response.

Overrides (`vs.gen.overrides`) are applied here: hoisting (lossless only: every other row field
must be carried and no other array may exist), curated subsets (+ `RAW_JSON`, no children),
parent keys of child tables (from any ancestor level), field type/size/rename (`type: json`
also collapses an object or an array of objects into one JSON column), parameter types, and
rules. The spec's hard-required parameters become a `<slug>.spec_required` rule. All problems
are collected and raised together as `TablesError`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from vs.cfbd_vs import naming
from vs.cfbd_vs.catalog import Column, Param, Rule, Table
from vs.gen.overrides import (
    ChildOverrides,
    EndpointOverrides,
    FieldOverride,
    Overrides,
    rule_slug,
)
from vs.gen.schema_walk import (
    ApiParameter,
    ArrayNode,
    JsonValue,
    Leaf,
    Node,
    ObjectNode,
    Operation,
    Scalar,
    flatten_object,
    iter_fields,
)

MAX_FLATTENED_FIELDS = 500
MAX_COMMENT_LENGTH = 2000
SCALAR_ROOT_COLUMN = "VALUE"
SPEC_REQUIRED_RULE = "spec_required"
SPEC_REQUIRED_MESSAGE = "the API spec marks it as required"
FEATURES_PROPERTY = "API_FEATURES"
JSON_TYPE_NAME = "json"
STRING_TYPE_NAME = "string"

_ELLIPSIS = "…"
_SEPARATOR = " | "
META_COMMENTS: Mapping[str, str] = {
    "REQ_SLICE": "Canonical slice key: the path and query string exactly as sent",
    "REQ_SOURCE": "Where the row came from: LIVE, REPLAY or STORED",
    "REQ_STATUS": "Final HTTP status; NULL for STORED",
    "REQ_ATTEMPTS": "HTTP attempts including retries; 0 for STORED",
    "REQ_MS": "Request wall time in ms including retries; NULL for STORED",
    "REQ_ROWS": "Rows the response produced for this table",
    "REQ_AT": "UTC start time of the request (of the original request for STORED)",
}


class TablesError(ValueError):
    """Table derivation failed; `errors` lists every problem found."""

    def __init__(self, errors: Sequence[str]) -> None:
        super().__init__("table derivation failed:\n" + "\n".join(f"  - {e}" for e in errors))
        self.errors = tuple(errors)


def derive_tables(
    operations: Sequence[Operation], overrides: Overrides, reserved: frozenset[str]
) -> tuple[Table, ...]:
    """Derive every table of the non-excluded operations; raise `TablesError` listing all errors."""
    excluded = {e.path for e in overrides.excluded}
    errors: list[str] = []
    tables: list[Table] = []
    owners: dict[str, str] = {}
    for operation in operations:
        if operation.path in excluded:
            continue
        options = overrides.endpoints.get(operation.path) or EndpointOverrides(operation.path)
        deriver = _EndpointDeriver(operation, options, overrides.children, reserved)
        for table, source in deriver.derive():
            if table.name in owners:
                errors.append(
                    f"table name {table.name} is produced by both {owners[table.name]} and {source}"
                )
                continue
            owners[table.name] = source
            tables.append(table)
        errors.extend(deriver.errors)
    child_tables = {t.name for t in tables if t.kind == "child"}
    for name in sorted(set(overrides.children) - child_tables):
        errors.append(f"children[{name!r}]: no derived child table has this name")
    if errors:
        raise TablesError(errors)
    return tuple(tables)


@dataclass
class _Level:
    """One array level of a table tree: the element schema and the value columns it produced."""

    array: tuple[str, ...] | None
    prefix: tuple[str, ...]
    table: str | None
    segment: str | None = None
    columns: dict[tuple[str, ...], Column] = field(default_factory=dict)


@dataclass(frozen=True)
class _Shared:
    """Everything an endpoint table and its child tables have in common."""

    endpoint: str
    read_through: bool
    gated_by: str | None
    params: tuple[Param, ...]
    rules: tuple[Rule, ...]
    param_columns: tuple[Column, ...]


class _EndpointDeriver:
    def __init__(
        self,
        operation: Operation,
        options: EndpointOverrides,
        children: Mapping[str, ChildOverrides],
        reserved: frozenset[str],
    ) -> None:
        self.operation = operation
        self.options = options
        self.children = children
        self.reserved = reserved
        self.errors: list[str] = []
        self._used_fields: set[tuple[str, ...]] = set()
        self.shared = self._shared()

    def error(self, message: str) -> None:
        self.errors.append(f"{self.operation.path}: {message}")

    def derive(self) -> list[tuple[Table, str]]:
        try:
            name = naming.table_name_from_path(self.operation.path)
        except naming.NamingError as error:
            self.error(str(error))
            return []
        root_array, element = self._root()
        if self.options.hoist is not None and self.options.curated is not None:
            self.error("hoist and curated cannot be combined")
            return []
        count = len(self.errors)
        if self.options.curated is not None:
            tables = self._curated(name, root_array, element)
        elif self.options.hoist is not None:
            tables = self._hoisted(name, root_array, element)
        else:
            tables = self._tree([_Level(() if root_array else None, (), name)], element)
        if len(self.errors) == count:
            self._check_unused_fields()
        return [(t, self._source(t)) for t in tables]

    def _source(self, table: Table) -> str:
        if table.kind == "endpoint":
            return self.operation.path
        return f"{self.operation.path} (array {_dotted(table.array_path)})"

    def _shared(self) -> _Shared:
        pairs = [(a, self._param(a)) for a in self.operation.parameters]
        found = [(api, param) for api, param in pairs if param is not None]
        params = tuple(param for _, param in found)
        rules = self._rules()
        param_columns = tuple(
            Column(
                param.column,
                "param",
                sql_type=param.sql_type,
                comment=self._param_comment(api, param, rules),
            )
            for api, param in found
        )
        read_through = True if self.options.read_through is None else self.options.read_through
        return _Shared(
            self.operation.path,
            read_through,
            self.options.gated_by,
            params,
            rules,
            param_columns,
        )

    def _param(self, api: ApiParameter) -> Param | None:
        node = api.node
        if not isinstance(node, Scalar):
            self.error(f"parameter {api.name!r}: only scalar parameters are supported")
            return None
        override = self.options.params.get(api.name)
        spec_type = override.type if override is not None else node.type
        spec_format = node.format if spec_type == node.type else None
        enum = node.enum
        if enum is not None and not all(isinstance(v, str) for v in enum):
            self.error(f"parameter {api.name!r}: only string enums are supported")
            enum = None
        try:
            sql_type = naming.param_sql_type(spec_type, spec_format)
            column = naming.param_column_name(api.name)
        except naming.NamingError as error:
            self.error(f"parameter {api.name!r}: {error}")
            return None
        return Param(
            name=api.name,
            column=column,
            location=api.location,
            type=spec_type,
            sql_type=sql_type,
            enum=None if enum is None else tuple(enum),
        )

    def _rules(self) -> tuple[Rule, ...]:
        own = tuple(self.options.rules)
        covered = {n for r in own if r.type == "required" for n in r.params}
        missing = tuple(
            p.name for p in self.operation.parameters if p.required and p.name not in covered
        )
        if not missing:
            return own
        rule_id = f"{rule_slug(self.operation.path)}.{SPEC_REQUIRED_RULE}"
        if any(r.id == rule_id for r in own):
            self.error(f"rule id {rule_id} is reserved for the spec's required parameters")
        spec_rule = Rule(rule_id, "required", SPEC_REQUIRED_MESSAGE, missing)
        return (spec_rule, *own)

    def _param_comment(self, api: ApiParameter, param: Param, rules: tuple[Rule, ...]) -> str:
        parts = [api.description] if api.description.strip() else []
        parts.append(f"API {param.location} parameter {param.name}")
        parts.extend(_rule_text(r) for r in rules if param.name in r.params)
        if param.enum:
            parts.append("allowed values: " + ", ".join(param.enum))
        if self.options.gated_by:
            parts.append(
                f"requires CFBD feature {self.options.gated_by} "
                f"(virtual schema property {FEATURES_PROPERTY})"
            )
        return _clamp(_SEPARATOR.join(parts))

    def _root(self) -> tuple[bool, Node]:
        response = self.operation.response
        if isinstance(response, ArrayNode):
            return True, self._collapse(response.items, ())
        return False, self._collapse(response, ())

    def _collapse(self, node: Node, prefix: tuple[str, ...]) -> Node:
        if not isinstance(node, ObjectNode):
            return node
        fields = []
        for child in node.fields:
            path = (*prefix, child.name)
            inner = child.node
            override = self.options.fields.get(path)
            if override is not None and override.type == JSON_TYPE_NAME and _is_structured(inner):
                self._used_fields.add(path)
                inner = JsonValue(reason="override", description=inner.description)
            elif isinstance(inner, ArrayNode) and isinstance(inner.items, ObjectNode):
                inner = replace(inner, items=self._collapse(inner.items, path))
            else:
                inner = self._collapse(inner, path)
            fields.append(replace(child, node=inner))
        return replace(node, fields=tuple(fields))

    def _tree(
        self, levels: list[_Level], element: Node, carried: tuple[Column, ...] = ()
    ) -> list[Table]:
        current = levels[-1]
        name = current.table
        assert name is not None
        if isinstance(element, ObjectNode):
            flat = flatten_object(element)
            leaves, arrays = flat.leaves, flat.arrays
        else:
            leaves, arrays = (Leaf((), _leaf_node(element)),), ()
        if not self._fits(name, len(leaves)):
            return []
        values = self._value_columns(current.prefix, leaves, current)
        is_child = _parent_of(levels) is not None
        parent_keys = self._declared_parent_keys(name, levels[:-1]) if is_child else carried
        tables = [self._table(levels, parent_keys, values, ())]
        for array in arrays:
            full = (*current.prefix, *array.path)
            segment = self._array_segment(full, array.path)
            if segment is not None:
                level = _Level(array.path, full, f"{name}_{segment}", segment)
                tables += self._tree([*levels, level], array.items)
        return tables

    def _array_segment(self, full: tuple[str, ...], relative: tuple[str, ...]) -> str | None:
        override = self.options.fields.get(full)
        if override is not None and override.rename:
            self._used_fields.add(full)
            return override.rename
        try:
            return naming.column_name_from_path(relative)
        except naming.NamingError as error:
            self.error(f"array {_dotted(full)}: {error}; add a rename")
            return None

    def _hoisted(self, name: str, root_array: bool, element: Node) -> list[Table]:
        hoist = self.options.hoist
        assert hoist is not None
        if not isinstance(element, ObjectNode):
            self.error("hoist needs a response row object")
            return []
        flat = flatten_object(element)
        target = next((a for a in flat.arrays if a.path == hoist.array), None)
        if target is None:
            self.error(f"hoist array {_dotted(hoist.array)} is not an array of objects")
            return []
        for array in flat.arrays:
            if array.path != hoist.array:
                self.error(
                    f"hoisting drops the array {_dotted(array.path)}; it has no parent table"
                )
        root = _Level(() if root_array else None, (), None)
        for leaf in flat.leaves:
            if leaf.path in hoist.carry:
                root.columns[leaf.path] = self._carried_column(leaf, hoist.carry[leaf.path])
            else:
                self.error(f"hoisting drops the field {_dotted(leaf.path)}; add it to hoist.carry")
        for path in hoist.carry:
            if path not in root.columns:
                self.error(f"hoist.carry field {_dotted(path)} is not a scalar field of the row")
        segment = self._array_segment(hoist.array, hoist.array)
        if segment is None:
            return []
        level = _Level(hoist.array, hoist.array, name, segment)
        return self._tree([root, level], target.items, tuple(root.columns.values()))

    def _carried_column(self, leaf: Leaf, name: str) -> Column:
        if name in self.reserved:
            self.error(f"hoist.carry column {name} is a reserved word")
        sql_type, is_json = self._column_type(leaf.path, leaf)
        comment = _clamp(
            _SEPARATOR.join(
                [*_description(leaf), f"API field {_dotted(leaf.path)} of the response row"]
            )
        )
        return Column(name, "parent_key", leaf.path, sql_type, comment, is_json)

    def _curated(self, name: str, root_array: bool, element: Node) -> list[Table]:
        curated = self.options.curated or ()
        if not isinstance(element, ObjectNode):
            self.error("curated needs a response row object")
            return []
        index = {path: (order, node) for order, (path, node) in enumerate(iter_fields(element))}
        chosen: dict[tuple[str, ...], tuple[int, Node]] = {}
        for path in curated:
            if path not in index:
                self.error(
                    f"curated field {_dotted(path)} is not a field of the response row "
                    "outside arrays"
                )
                continue
            for full in _expand(path, index[path][1]):
                if full in chosen:
                    self.error(f"curated field {_dotted(full)} is listed twice")
                chosen[full] = index[full]
        ordered = sorted(chosen.items(), key=lambda item: item[1][0])
        leaves = tuple(Leaf(path, _leaf_node(node)) for path, (_, node) in ordered)
        if not self._fits(name, len(leaves)):
            return []
        root = _Level(() if root_array else None, (), name)
        values = self._value_columns((), leaves, root)
        raw = (_raw_json_column(),) if self.options.raw_json else ()
        return [self._table([root], (), values, raw)]

    def _fits(self, name: str, count: int) -> bool:
        if count <= MAX_FLATTENED_FIELDS:
            return True
        advice = (
            "the curated subset is too large"
            if self.options.curated is not None
            else "declare a curated subset (curated:) for this endpoint"
        )
        self.error(
            f"table {name} has {count} flattened fields, more than {MAX_FLATTENED_FIELDS}; {advice}"
        )
        return False

    def _value_columns(
        self, prefix: tuple[str, ...], leaves: Iterable[Leaf], level: _Level
    ) -> tuple[Column, ...]:
        columns = []
        for leaf in leaves:
            full = (*prefix, *leaf.path)
            column = self._value_column(full, leaf)
            if column is not None:
                columns.append(column)
                level.columns[full] = column
        return tuple(columns)

    def _value_column(self, full: tuple[str, ...], leaf: Leaf) -> Column | None:
        override = self.options.fields.get(full)
        name: str | None
        if override is not None and override.rename:
            self._used_fields.add(full)
            name = override.rename
            if name in self.reserved:
                self.error(f"field {_dotted(full)}: rename {name} is a reserved word")
        else:
            name = self._derived_name(full, leaf.path)
        if name is None:
            return None
        sql_type, is_json = self._column_type(full, leaf)
        api = f"API field {_dotted(full)}" if full else "API response element"
        comment = _clamp(_SEPARATOR.join([*_description(leaf), api]))
        return Column(name, "value", leaf.path, sql_type, comment, is_json)

    def _derived_name(self, full: tuple[str, ...], relative: tuple[str, ...]) -> str | None:
        try:
            base = naming.column_name_from_path(relative) if relative else SCALAR_ROOT_COLUMN
        except naming.NamingError as error:
            self.error(f"field {_dotted(full)}: {error}; add a rename")
            return None
        return naming.safe_column_name(base, self.reserved)

    def _column_type(self, full: tuple[str, ...], leaf: Leaf) -> tuple[str, bool]:
        override: FieldOverride | None = self.options.fields.get(full)
        spec_type, spec_format = leaf.spec_type, leaf.format
        size = None
        if override is not None and (override.type or override.size):
            self._used_fields.add(full)
            if override.type:
                spec_type, spec_format = override.type, None
            size = override.size
        if size is not None and (
            spec_type != STRING_TYPE_NAME or spec_format == naming.DATE_TIME_FORMAT
        ):
            self.error(f"field {_dotted(full)}: size applies only to VARCHAR string columns")
            size = None
        try:
            sql_type = naming.column_sql_type(spec_type, spec_format, size)
        except naming.NamingError as error:
            self.error(f"field {_dotted(full)}: {error}")
            sql_type = naming.JSON_TYPE
        return sql_type, spec_type == JSON_TYPE_NAME

    def _declared_parent_keys(self, name: str, ancestors: list[_Level]) -> tuple[Column, ...]:
        entry = self.children.get(name)
        if entry is None:
            return ()
        columns = []
        for path in entry.parent_keys:
            column = _ancestor_column(path, ancestors)
            if column is None:
                self.error(
                    f"children[{name!r}]: parent key {_dotted(path)} is not a scalar field of an "
                    f"ancestor of {name}"
                )
            else:
                columns.append(column)
        return tuple(columns)

    def _table(
        self,
        levels: list[_Level],
        parent_keys: tuple[Column, ...],
        values: tuple[Column, ...],
        raw: tuple[Column, ...],
    ) -> Table:
        current = levels[-1]
        assert current.table is not None
        parent = _parent_of(levels)
        positions = tuple(_position_column(index, level) for index, level in enumerate(levels))
        meta = tuple(
            Column(n, "meta", sql_type=t, comment=META_COMMENTS[n])
            for n, t in naming.META_COLUMNS.items()
        )
        columns = (*positions, *parent_keys, *values, *raw, *self.shared.param_columns, *meta)
        self._check_collisions(current.table, columns)
        return Table(
            name=current.table,
            endpoint=self.shared.endpoint,
            kind="endpoint" if parent is None else "child",
            parent=parent,
            array_path=current.prefix,
            hoisted=parent is None and len(levels) > 1,
            read_through=self.shared.read_through,
            gated_by=self.shared.gated_by,
            params=self.shared.params,
            columns=columns,
            rules=self.shared.rules,
        )

    def _check_collisions(self, table: str, columns: Iterable[Column]) -> None:
        seen: dict[str, str] = {}
        params = {p.column: p.name for p in self.shared.params}
        for column in columns:
            source = _column_source(column, params)
            if column.name in seen:
                self.error(
                    f"table {table}: column {column.name} is produced by both {seen[column.name]} "
                    f"and {source}; rename one of them"
                )
            else:
                seen[column.name] = source

    def _check_unused_fields(self) -> None:
        for path in self.options.fields:
            if path not in self._used_fields:
                self.error(
                    f"fields override {_dotted(path)} is not used by any column (name a scalar "
                    "field, or set type json on an object or array)"
                )


def _parent_of(levels: list[_Level]) -> str | None:
    return levels[-2].table if len(levels) > 1 else None


def _ancestor_column(path: tuple[str, ...], ancestors: list[_Level]) -> Column | None:
    for level in reversed(ancestors):
        source = level.columns.get(path)
        if source is None:
            continue
        origin = f"copied from {level.table}.{source.name}" if level.table else "carried"
        comment = _clamp(_SEPARATOR.join([f"Parent key {origin}", source.comment or ""]))
        return Column(source.name, "parent_key", path, source.sql_type, comment, source.json)
    return None


def _position_column(index: int, level: _Level) -> Column:
    if index == 0:
        comment = (
            "1-based position of the element in the response array"
            if level.array is not None
            else "Always 1: the response is a single object"
        )
        return Column(naming.ROW_NO, "position", level.array, naming.DECIMAL_TYPE, comment)
    assert level.array is not None
    assert level.segment is not None
    comment = f"1-based position within the array {_dotted(level.prefix)}"
    name = level.segment + naming.POSITION_SUFFIX
    return Column(name, "position", level.array, naming.DECIMAL_TYPE, comment)


def _raw_json_column() -> Column:
    comment = "The complete response row object as JSON text"
    return Column(naming.RAW_JSON_COLUMN, "raw_json", None, naming.JSON_TYPE, comment, True)


def _column_source(column: Column, params: Mapping[str, str]) -> str:
    if column.kind == "param":
        return f"parameter {params.get(column.name, column.name)}"
    if column.kind == "meta":
        return "request metadata"
    if column.kind == "raw_json":
        return "the raw JSON column"
    if column.kind == "position":
        return (
            f"the position of array {_dotted(column.path)}" if column.path else "the row position"
        )
    if column.kind == "parent_key":
        return f"parent key {_dotted(column.path or ())}"
    return f"field {_dotted(column.path or ())}"


def _expand(path: tuple[str, ...], node: Node) -> list[tuple[str, ...]]:
    if not isinstance(node, ObjectNode):
        return [path]
    return [(*path, *sub) for sub, inner in iter_fields(node) if not isinstance(inner, ObjectNode)]


def _leaf_node(node: Node) -> Scalar | JsonValue | ArrayNode:
    if isinstance(node, ObjectNode):
        return JsonValue(reason="free_form", description=node.description)
    return node


def _is_structured(node: Node) -> bool:
    return isinstance(node, ObjectNode | ArrayNode)


def _description(leaf: Leaf) -> list[str]:
    return [leaf.description] if leaf.description else []


def _rule_text(rule: Rule) -> str:
    details = " ".join(f"{key}={_render(value)}" for key, value in sorted(rule.details.items()))
    shape = " ".join(part for part in (rule.type, ", ".join(rule.params), details) if part)
    text = f"rule {rule.id} [{shape}]"
    return f"{text}: {rule.message}" if rule.message else text


def _render(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return ",".join(_render(v) for v in value)
    return str(value)


def _clamp(text: str) -> str:
    if len(text) <= MAX_COMMENT_LENGTH:
        return text
    return text[: MAX_COMMENT_LENGTH - len(_ELLIPSIS)] + _ELLIPSIS


def _dotted(path: Sequence[str]) -> str:
    return ".".join(path)
