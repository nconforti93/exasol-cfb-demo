"""OpenAPI 3.0 schema walker: turns response and parameter schemas into a typed shape tree.

The tree keeps only what table derivation needs:

- `ObjectNode`: an object with declared properties, fields in spec order.
- `ArrayNode`: an array; `of_objects` tells child-table arrays from JSON-column arrays.
- `Scalar`: integer, number, boolean or string, with `format` (`date-time` → TIMESTAMP) and enum.
- `JsonValue`: anything without a fixed relational shape, stored as JSON text. Reasons:
  `free_form` (no declared properties, or an empty schema), `variant` (a union of different
  types), `cycle` (a `$ref` back into its own ancestry).

Resolution rules: local `$ref` JSON pointers; `allOf` (one branch unwraps, several object
branches merge, annotation-only branches are ignored); `oneOf`/`anyOf` drop null branches,
collapse to the single remaining branch or to one scalar type, and become a JSON variant
otherwise; `nullable` and type lists with `null` are nullable (every column is nullable).
A cycle becomes JSON at the point where it closes, and a nesting depth above `max_depth` is an
error. Malformed input (dangling or external `$ref`, unknown type) is an error naming the
location.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

MAX_DEPTH = 32
SCALAR_TYPES = ("integer", "number", "boolean", "string")
PARAMETER_LOCATIONS = ("query", "path")
JSON_SPEC_TYPE = "json"

_STRUCTURAL_KEYS = frozenset(
    {
        "$ref",
        "type",
        "properties",
        "items",
        "allOf",
        "oneOf",
        "anyOf",
        "enum",
        "additionalProperties",
    }
)
_LOCAL_REF_PREFIX = "#/"
_JSON_MEDIA_TYPE = "application/json"
_OK_STATUS = "200"


class SchemaWalkError(ValueError):
    """The spec cannot be walked: malformed schema, dangling reference or excessive depth."""


@dataclass(frozen=True)
class Scalar:
    """A scalar value; `enum` excludes `null`."""

    type: str
    format: str | None = None
    enum: tuple[Any, ...] | None = None
    description: str | None = None


@dataclass(frozen=True)
class JsonValue:
    """A value without a fixed relational shape, kept as JSON text."""

    reason: str
    description: str | None = None


@dataclass(frozen=True)
class Field:
    """One declared property of an object."""

    name: str
    node: Node


@dataclass(frozen=True)
class ObjectNode:
    """An object with declared properties, in spec order."""

    fields: tuple[Field, ...]
    description: str | None = None


@dataclass(frozen=True)
class ArrayNode:
    """An array. Arrays of objects become child tables; all other arrays become JSON text."""

    items: Node
    description: str | None = None

    @property
    def of_objects(self) -> bool:
        """True if the elements are objects with declared properties."""
        return isinstance(self.items, ObjectNode)


Node = Scalar | JsonValue | ObjectNode | ArrayNode


@dataclass(frozen=True)
class Leaf:
    """A flattened field that becomes one column: a scalar or a JSON value."""

    path: tuple[str, ...]
    node: Scalar | JsonValue | ArrayNode

    @property
    def spec_type(self) -> str:
        """The scalar type, or `json` for JSON values and arrays that are not child tables."""
        return self.node.type if isinstance(self.node, Scalar) else JSON_SPEC_TYPE

    @property
    def format(self) -> str | None:
        return self.node.format if isinstance(self.node, Scalar) else None

    @property
    def description(self) -> str | None:
        return self.node.description


@dataclass(frozen=True)
class ChildArray:
    """An array of objects inside an object: the source of a child table."""

    path: tuple[str, ...]
    items: ObjectNode
    description: str | None = None


@dataclass(frozen=True)
class Flattened:
    """The leaves of an object (nested objects prefixed) and its arrays of objects."""

    leaves: tuple[Leaf, ...]
    arrays: tuple[ChildArray, ...]


@dataclass(frozen=True)
class ApiParameter:
    """A query or path parameter of an operation."""

    name: str
    location: str
    required: bool
    node: Node
    description: str


@dataclass(frozen=True)
class Operation:
    """A GET operation with its parameters and the walked 200 JSON response schema."""

    path: str
    description: str
    parameters: tuple[ApiParameter, ...]
    response: Node


class SchemaWalker:
    """Walks schemas of one OpenAPI document, resolving local references."""

    def __init__(self, document: Mapping[str, Any], max_depth: int = MAX_DEPTH) -> None:
        self._document = document
        self._max_depth = max_depth

    def walk(self, schema: Any, location: str) -> Node:
        """Walk `schema`; `location` prefixes error messages."""
        return self._walk(schema, location, (), 0)

    def resolve(self, ref: Any, location: str) -> Any:
        """Return the target of a local `$ref` JSON pointer."""
        if not isinstance(ref, str) or not ref.startswith(_LOCAL_REF_PREFIX):
            raise SchemaWalkError(f"{location}: only local references are supported, got {ref!r}")
        target: Any = self._document
        for token in ref[len(_LOCAL_REF_PREFIX) :].split("/"):
            key = token.replace("~1", "/").replace("~0", "~")
            if not isinstance(target, Mapping) or key not in target:
                raise SchemaWalkError(f"{location}: reference {ref} does not resolve")
            target = target[key]
        return target

    def _walk(self, schema: Any, location: str, stack: tuple[str, ...], depth: int) -> Node:
        if depth > self._max_depth:
            raise SchemaWalkError(f"{location}: schema nesting is deeper than {self._max_depth}")
        if not isinstance(schema, Mapping):
            raise SchemaWalkError(f"{location}: a schema must be a JSON object")
        description = _description(schema)
        if "$ref" in schema:
            node = self._walk_ref(schema["$ref"], location, stack, depth)
        elif "allOf" in schema:
            node = self._walk_all_of(schema, location, stack, depth)
        elif "oneOf" in schema or "anyOf" in schema:
            node = self._walk_union(schema, location, stack, depth)
        else:
            node = self._walk_typed(schema, location, stack, depth)
        return _with_description(node, description)

    def _walk_ref(self, ref: Any, location: str, stack: tuple[str, ...], depth: int) -> Node:
        if ref in stack:
            return JsonValue(reason="cycle")
        target = self.resolve(ref, location)
        return self._walk(target, str(ref), (*stack, str(ref)), depth)

    def _walk_all_of(
        self, schema: Mapping[str, Any], location: str, stack: tuple[str, ...], depth: int
    ) -> Node:
        branches = _list_of(schema["allOf"], f"{location}/allOf")
        if "properties" in schema or schema.get("type") == "object":
            branches = [*branches, {k: v for k, v in schema.items() if k != "allOf"}]
        nodes = [
            self._walk(branch, f"{location}/allOf/{index}", stack, depth)
            for index, branch in enumerate(branches)
            if _is_structural(branch)
        ]
        notes = [_description(b) for b in branches if not _is_structural(b)]
        if len(nodes) == 1:
            return _with_description(nodes[0], next((n for n in notes if n), None))
        if nodes and all(isinstance(n, ObjectNode) for n in nodes):
            return _merge_objects([n for n in nodes if isinstance(n, ObjectNode)], location)
        return JsonValue(reason="variant")

    def _walk_union(
        self, schema: Mapping[str, Any], location: str, stack: tuple[str, ...], depth: int
    ) -> Node:
        keyword = "oneOf" if "oneOf" in schema else "anyOf"
        nodes = [
            self._walk(branch, f"{location}/{keyword}/{index}", stack, depth)
            for index, branch in enumerate(_list_of(schema[keyword], f"{location}/{keyword}"))
            if not _is_null_schema(branch)
        ]
        if len(nodes) == 1:
            return nodes[0]
        return _merge_scalars(nodes) or JsonValue(reason="variant")

    def _walk_typed(
        self, schema: Mapping[str, Any], location: str, stack: tuple[str, ...], depth: int
    ) -> Node:
        spec_type = _effective_type(schema)
        if spec_type is None:
            return JsonValue(reason="free_form")
        if spec_type == "variant":
            return JsonValue(reason="variant")
        if spec_type == "object":
            return self._walk_object(schema, location, stack, depth)
        if spec_type == "array":
            if "items" not in schema:
                return ArrayNode(items=JsonValue(reason="free_form"))
            items = self._walk(schema["items"], f"{location}/items", stack, depth + 1)
            return ArrayNode(items=items)
        if spec_type in SCALAR_TYPES:
            return Scalar(type=spec_type, format=schema.get("format"), enum=_enum_of(schema))
        raise SchemaWalkError(f"{location}: unknown schema type {spec_type!r}")

    def _walk_object(
        self, schema: Mapping[str, Any], location: str, stack: tuple[str, ...], depth: int
    ) -> Node:
        properties = schema.get("properties", {})
        if not isinstance(properties, Mapping):
            raise SchemaWalkError(f"{location}: properties must be a JSON object")
        if not properties:
            return JsonValue(reason="free_form")
        fields = tuple(
            Field(name, self._walk(sub, f"{location}/properties/{name}", stack, depth + 1))
            for name, sub in properties.items()
        )
        return ObjectNode(fields=fields)


def flatten_object(node: ObjectNode) -> Flattened:
    """Flatten nested objects into prefixed leaves; arrays of objects are returned separately."""
    leaves: list[Leaf] = []
    arrays: list[ChildArray] = []
    for path, child in _walk_fields(node, ()):
        if isinstance(child, ArrayNode) and isinstance(child.items, ObjectNode):
            arrays.append(ChildArray(path, child.items, child.description))
        elif not isinstance(child, ObjectNode):
            leaves.append(Leaf(path, child))
    return Flattened(tuple(leaves), tuple(arrays))


def iter_fields(node: ObjectNode) -> Iterator[tuple[tuple[str, ...], Node]]:
    """Yield every (path, node) of an object in spec order, descending into nested objects only."""
    yield from _walk_fields(node, ())


def read_operations(
    document: Mapping[str, Any], max_depth: int = MAX_DEPTH
) -> tuple[Operation, ...]:
    """Walk every GET operation of the document, sorted by path."""
    walker = SchemaWalker(document, max_depth)
    paths = document.get("paths") or {}
    operations = []
    for path in sorted(paths):
        item = paths[path]
        if isinstance(item, Mapping) and isinstance(item.get("get"), Mapping):
            operations.append(_read_operation(walker, path, item))
    return tuple(operations)


def _read_operation(walker: SchemaWalker, path: str, item: Mapping[str, Any]) -> Operation:
    operation = item["get"]
    merged: dict[tuple[str, str], Mapping[str, Any]] = {}
    for raw in [*(item.get("parameters") or []), *(operation.get("parameters") or [])]:
        parameter = _resolve_object(walker, raw, f"{path} parameter")
        merged[(str(parameter.get("in")), str(parameter.get("name")))] = parameter
    parameters = tuple(_read_parameter(walker, path, p) for p in merged.values())
    response = walker.walk(_response_schema(walker, path, operation), f"{path} 200 response")
    description = operation.get("description") or operation.get("summary") or ""
    return Operation(path, str(description), parameters, response)


def _read_parameter(walker: SchemaWalker, path: str, parameter: Mapping[str, Any]) -> ApiParameter:
    name = parameter.get("name")
    location = parameter.get("in")
    where = f"{path} parameter {name!r}"
    if not isinstance(name, str) or not name:
        raise SchemaWalkError(f"{path}: a parameter has no name")
    if location not in PARAMETER_LOCATIONS:
        raise SchemaWalkError(f"{where}: location {location!r} is not supported (query or path)")
    node = walker.walk(parameter.get("schema", {}), where)
    required = bool(parameter.get("required", False)) or location == "path"
    return ApiParameter(name, location, required, node, str(parameter.get("description") or ""))


def _response_schema(walker: SchemaWalker, path: str, operation: Mapping[str, Any]) -> Any:
    responses = operation.get("responses") or {}
    if _OK_STATUS not in responses:
        raise SchemaWalkError(f"{path}: the GET operation has no 200 response")
    response = _resolve_object(walker, responses[_OK_STATUS], f"{path} 200 response")
    content = response.get("content") or {}
    media = content.get(_JSON_MEDIA_TYPE) if isinstance(content, Mapping) else None
    if not isinstance(media, Mapping) or "schema" not in media:
        raise SchemaWalkError(f"{path}: the 200 response has no {_JSON_MEDIA_TYPE} schema")
    return media["schema"]


def _resolve_object(walker: SchemaWalker, raw: Any, location: str) -> Mapping[str, Any]:
    seen: set[str] = set()
    while isinstance(raw, Mapping) and "$ref" in raw:
        ref = raw["$ref"]
        if ref in seen:
            raise SchemaWalkError(f"{location}: reference cycle at {ref}")
        seen.add(ref)
        raw = walker.resolve(ref, location)
    if not isinstance(raw, Mapping):
        raise SchemaWalkError(f"{location}: expected a JSON object")
    return raw


def _walk_fields(
    node: ObjectNode, prefix: tuple[str, ...]
) -> Iterator[tuple[tuple[str, ...], Node]]:
    for field in node.fields:
        path = (*prefix, field.name)
        yield path, field.node
        if isinstance(field.node, ObjectNode):
            yield from _walk_fields(field.node, path)


def _description(schema: Mapping[str, Any]) -> str | None:
    text = schema.get("description")
    return text if isinstance(text, str) and text.strip() else None


def _with_description(node: Node, description: str | None) -> Node:
    return node if description is None else replace(node, description=description)


def _is_structural(schema: Any) -> bool:
    return not isinstance(schema, Mapping) or bool(_STRUCTURAL_KEYS & schema.keys())


def _is_null_schema(schema: Any) -> bool:
    if not isinstance(schema, Mapping):
        return False
    return schema.get("type") == "null" or schema.get("enum") == [None]


def _list_of(value: Any, location: str) -> list[Any]:
    if not isinstance(value, list):
        raise SchemaWalkError(f"{location}: expected a JSON array")
    return value


def _effective_type(schema: Mapping[str, Any]) -> str | None:
    declared = schema.get("type")
    if isinstance(declared, list):
        non_null = [t for t in declared if t != "null"]
        if len(non_null) != 1:
            return "variant" if non_null else None
        declared = non_null[0]
    if declared is not None:
        return str(declared)
    if "properties" in schema:
        return "object"
    if "items" in schema:
        return "array"
    if "enum" in schema:
        return _enum_value_type(schema["enum"])
    return None


def _enum_value_type(values: Any) -> str:
    kinds = (
        {_json_scalar_type(v) for v in values if v is not None}
        if isinstance(values, list)
        else set()
    )
    return kinds.pop() if len(kinds) == 1 else "variant"


def _json_scalar_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    return "variant"


def _enum_of(schema: Mapping[str, Any]) -> tuple[Any, ...] | None:
    values = schema.get("enum")
    if not isinstance(values, list):
        return None
    return tuple(v for v in values if v is not None)


def _merge_scalars(nodes: Sequence[Node]) -> Scalar | None:
    scalars = [n for n in nodes if isinstance(n, Scalar)]
    if len(scalars) != len(nodes) or len({(s.type, s.format) for s in scalars}) != 1:
        return None
    enum: tuple[Any, ...] | None = None
    if all(s.enum is not None for s in scalars):
        enum = tuple(dict.fromkeys(v for s in scalars for v in (s.enum or ())))
    return Scalar(type=scalars[0].type, format=scalars[0].format, enum=enum)


def _merge_objects(nodes: Sequence[ObjectNode], location: str) -> ObjectNode:
    merged: dict[str, Field] = {}
    for node in nodes:
        for field in node.fields:
            existing = merged.get(field.name)
            if existing is not None and existing.node != field.node:
                raise SchemaWalkError(
                    f"{location}: allOf branches define property {field.name!r} differently"
                )
            merged.setdefault(field.name, field)
    return ObjectNode(fields=tuple(merged.values()))
