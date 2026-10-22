import json
from pathlib import Path
from typing import Any

import pytest

from vs.gen.schema_walk import (
    ArrayNode,
    JsonValue,
    ObjectNode,
    Scalar,
    SchemaWalker,
    SchemaWalkError,
    flatten_object,
    read_operations,
)

SPEC_PATH = Path(__file__).resolve().parents[2] / "vs" / "openapi" / "cfbd-openapi-5.33.0.json"


def ref(name: str) -> dict[str, str]:
    return {"$ref": f"#/components/schemas/{name}"}


def walker(schemas: dict[str, Any], max_depth: int = 32) -> SchemaWalker:
    return SchemaWalker({"components": {"schemas": schemas}}, max_depth=max_depth)


def walk(schema: dict[str, Any], schemas: dict[str, Any] | None = None) -> Any:
    return walker(schemas or {}).walk(schema, "test")


def obj(**properties: Any) -> dict[str, Any]:
    return {"type": "object", "properties": properties}


INT = {"type": "integer", "format": "int32"}
NUM = {"type": "number", "format": "double"}
STR = {"type": "string"}


def field_names(node: ObjectNode) -> list[str]:
    return [f.name for f in node.fields]


def test_ref_resolves_to_component_with_fields_in_spec_order() -> None:
    node = walk(ref("Game"), {"Game": obj(id=INT, homeTeam=STR, attendance=NUM)})
    assert isinstance(node, ObjectNode)
    assert field_names(node) == ["id", "homeTeam", "attendance"]
    assert node.fields[0].node == Scalar(type="integer", format="int32")


def test_single_branch_all_of_with_nullable_unwraps_to_target() -> None:
    schemas = {"Venue": obj(name=STR)}
    node = walk({"allOf": [ref("Venue")], "nullable": True}, schemas)
    assert node == walk(ref("Venue"), schemas)


def test_outer_description_wins_over_target_description() -> None:
    schemas = {"Venue": {**obj(name=STR), "description": "inner"}}
    assert walk({"allOf": [ref("Venue")], "description": "outer"}, schemas).description == "outer"
    assert walk(ref("Venue"), schemas).description == "inner"


def test_annotation_only_all_of_branches_are_ignored() -> None:
    node = walk({"allOf": [ref("Kind"), {"description": "doc"}]}, {"Kind": STR})
    assert node == Scalar(type="string", description="doc")


def test_nullable_scalar_is_the_scalar() -> None:
    assert walk({"type": "integer", "nullable": True}) == Scalar(type="integer")


def test_enum_values_are_kept_without_null() -> None:
    node = walk({"type": "string", "enum": ["a", "b", None], "nullable": True})
    assert node == Scalar(type="string", enum=("a", "b"))


def test_enum_without_type_infers_the_type() -> None:
    assert walk({"enum": ["x", "y"]}) == Scalar(type="string", enum=("x", "y"))
    assert walk({"enum": [1, 2]}) == Scalar(type="integer", enum=(1, 2))
    assert isinstance(walk({"enum": [1, "y"]}), JsonValue)


def test_nullable_one_of_and_any_of_collapse_to_the_other_branch() -> None:
    assert walk({"oneOf": [INT, {"type": "null"}]}) == Scalar(type="integer", format="int32")
    assert walk({"anyOf": [{"type": "null"}, STR]}) == Scalar(type="string")


def test_any_of_same_scalar_type_merges_enums() -> None:
    schemas = {"SeasonType": {"type": "string", "enum": ["regular", "postseason"]}}
    node = walk({"anyOf": [ref("SeasonType"), {"type": "string", "enum": ["preseason"]}]}, schemas)
    assert node == Scalar(type="string", enum=("regular", "postseason", "preseason"))


def test_any_of_same_type_without_all_enums_drops_enum() -> None:
    node = walk({"anyOf": [{"type": "string", "enum": ["a"]}, STR]})
    assert node == Scalar(type="string")


def test_variant_scalars_become_json() -> None:
    node = walk({"anyOf": [STR, NUM]})
    assert node == JsonValue(reason="variant")


def test_type_list_with_null_is_nullable_scalar() -> None:
    assert walk({"type": ["integer", "null"]}) == Scalar(type="integer")
    assert walk({"type": ["integer", "string"]}) == JsonValue(reason="variant")


@pytest.mark.parametrize(
    "schema",
    [
        {},
        {"type": "object"},
        {"type": "object", "properties": {}},
        {"type": "object", "additionalProperties": INT},
        {"type": "object", "properties": {}, "additionalProperties": obj(message=STR)},
    ],
)
def test_free_form_objects_become_json(schema: dict[str, Any]) -> None:
    assert walk(schema) == JsonValue(reason="free_form")


def test_object_with_properties_and_additional_properties_keeps_properties() -> None:
    node = walk({**obj(a=INT), "additionalProperties": True})
    assert isinstance(node, ObjectNode)
    assert field_names(node) == ["a"]


def test_properties_without_type_are_an_object() -> None:
    assert isinstance(walk({"properties": {"a": INT}}), ObjectNode)


def test_arrays_of_objects_and_arrays_of_scalars_are_classified() -> None:
    objects = walk({"type": "array", "items": ref("Poll")}, {"Poll": obj(poll=STR)})
    scalars = walk({"type": "array", "items": INT})
    assert isinstance(objects, ArrayNode) and objects.of_objects
    assert isinstance(scalars, ArrayNode) and not scalars.of_objects
    assert scalars.items == Scalar(type="integer", format="int32")


def test_array_without_items_holds_free_form_values() -> None:
    node = walk({"type": "array"})
    assert node == ArrayNode(items=JsonValue(reason="free_form"))


def test_date_time_format_is_preserved() -> None:
    assert walk({"type": "string", "format": "date-time"}).format == "date-time"


def test_multi_branch_all_of_merges_object_branches() -> None:
    schemas = {"Base": obj(id=INT, name=STR), "Extra": obj(name=STR, score=NUM)}
    node = walk({"allOf": [ref("Base"), ref("Extra"), obj(rank=INT)]}, schemas)
    assert isinstance(node, ObjectNode)
    assert field_names(node) == ["id", "name", "score", "rank"]


def test_all_of_merges_sibling_properties() -> None:
    node = walk({"allOf": [ref("Base")], **obj(extra=INT)}, {"Base": obj(id=INT)})
    assert field_names(node) == ["id", "extra"]


def test_conflicting_all_of_properties_are_an_error() -> None:
    schemas = {"A": obj(id=INT), "B": obj(id=STR)}
    with pytest.raises(SchemaWalkError, match="id"):
        walk({"allOf": [ref("A"), ref("B")]}, schemas)


def test_multi_branch_all_of_of_non_objects_is_json() -> None:
    assert walk({"allOf": [INT, STR]}) == JsonValue(reason="variant")


def test_self_reference_becomes_json_at_the_cycle() -> None:
    schemas = {
        "Node": obj(name=STR, parent=ref("Node"), children={"type": "array", "items": ref("Node")})
    }
    node = walk(ref("Node"), schemas)
    assert isinstance(node, ObjectNode)
    parent = node.fields[1].node
    children = node.fields[2].node
    assert parent == JsonValue(reason="cycle")
    assert isinstance(children, ArrayNode) and not children.of_objects


def test_shared_non_recursive_refs_are_not_cycles() -> None:
    schemas = {"Team": obj(name=STR), "Game": obj(home=ref("Team"), away=ref("Team"))}
    node = walk(ref("Game"), schemas)
    assert all(isinstance(f.node, ObjectNode) for f in node.fields)


def test_pure_ref_cycle_terminates() -> None:
    assert walk(ref("A"), {"A": ref("B"), "B": ref("A")}) == JsonValue(reason="cycle")


def test_depth_guard_names_the_location() -> None:
    deep: dict[str, Any] = INT
    for _ in range(5):
        deep = obj(inner=deep)
    with pytest.raises(SchemaWalkError, match="deeper than 3"):
        walker({}, max_depth=3).walk(deep, "root")
    assert isinstance(walker({}, max_depth=5).walk(deep, "root"), ObjectNode)


@pytest.mark.parametrize(
    ("schema", "message"),
    [
        (ref("Missing"), "Missing"),
        ({"$ref": "other.json#/X"}, "other.json"),
        ({"type": "file"}, "file"),
        ({"type": "object", "properties": []}, "properties"),
        ("not a schema", "schema"),
    ],
)
def test_malformed_schemas_are_errors(schema: Any, message: str) -> None:
    with pytest.raises(SchemaWalkError, match=message):
        walk(schema)


def test_ref_pointer_unescapes_json_pointer_tokens() -> None:
    document = {"components": {"schemas": {"a/b": INT, "c~d": STR}}}
    node = SchemaWalker(document).walk({"$ref": "#/components/schemas/a~1b"}, "x")
    assert node == Scalar(type="integer", format="int32")
    node = SchemaWalker(document).walk({"$ref": "#/components/schemas/c~0d"}, "x")
    assert node == Scalar(type="string")


def test_flatten_object_prefixes_nested_leaves_and_separates_child_arrays() -> None:
    schemas = {
        "TeamSP": obj(
            team=STR,
            offense=obj(rating=NUM, passing=NUM),
            defense=obj(havoc=obj(total=NUM)),
            homeLineScores={"type": "array", "items": INT},
            polls={"type": "array", "items": obj(poll=STR)},
            meta={"type": "object"},
        )
    }
    flat = flatten_object(walk(ref("TeamSP"), schemas))
    assert [leaf.path for leaf in flat.leaves] == [
        ("team",),
        ("offense", "rating"),
        ("offense", "passing"),
        ("defense", "havoc", "total"),
        ("homeLineScores",),
        ("meta",),
    ]
    assert [leaf.spec_type for leaf in flat.leaves] == [
        "string",
        "number",
        "number",
        "number",
        "json",
        "json",
    ]
    assert [a.path for a in flat.arrays] == [("polls",)]
    assert field_names(flat.arrays[0].items) == ["poll"]


def test_flatten_object_finds_arrays_inside_nested_objects() -> None:
    node = walk(obj(stats=obj(items={"type": "array", "items": obj(v=INT)})))
    flat = flatten_object(node)
    assert flat.leaves == ()
    assert [a.path for a in flat.arrays] == [("stats", "items")]


def test_leaf_carries_format_and_description() -> None:
    node = walk(obj(start={"type": "string", "format": "date-time", "description": "Kickoff"}))
    leaf = flatten_object(node).leaves[0]
    assert (leaf.spec_type, leaf.format, leaf.description) == ("string", "date-time", "Kickoff")


def spec_doc(paths: dict[str, Any], **components: Any) -> dict[str, Any]:
    return {"paths": paths, "components": components}


def get_op(schema: Any, parameters: list[Any] | None = None, **extra: Any) -> dict[str, Any]:
    return {
        "get": {
            "description": "Does things.",
            "parameters": parameters or [],
            "responses": {"200": {"content": {"application/json": {"schema": schema}}}},
            **extra,
        }
    }


def test_read_operations_returns_get_operations_sorted_with_params_and_response() -> None:
    document = spec_doc(
        {
            "/b": get_op({"type": "array", "items": INT}),
            "/a/{id}": get_op(
                obj(x=INT),
                [
                    {"name": "id", "in": "path", "required": True, "schema": INT},
                    {"$ref": "#/components/parameters/Year"},
                ],
            ),
            "/post-only": {"post": {}},
        },
        parameters={
            "Year": {"name": "year", "in": "query", "description": "Season", "schema": INT}
        },
    )
    ops = read_operations(document)
    assert [op.path for op in ops] == ["/a/{id}", "/b"]
    first = ops[0]
    assert first.description == "Does things."
    assert [(p.name, p.location, p.required) for p in first.parameters] == [
        ("id", "path", True),
        ("year", "query", False),
    ]
    assert first.parameters[1].description == "Season"
    assert first.parameters[1].node == Scalar(type="integer", format="int32")
    assert isinstance(first.response, ObjectNode)
    assert isinstance(ops[1].response, ArrayNode)


def test_path_level_parameters_are_merged_and_operation_level_wins() -> None:
    item = get_op(STR, [{"name": "year", "in": "query", "description": "op", "schema": INT}])
    item["parameters"] = [
        {"name": "year", "in": "query", "description": "path", "schema": STR},
        {"name": "team", "in": "query", "schema": STR},
    ]
    op = read_operations(spec_doc({"/x": item}))[0]
    assert [(p.name, p.description) for p in op.parameters] == [("year", "op"), ("team", "")]


def test_response_refs_are_resolved() -> None:
    item = get_op(STR)
    item["get"]["responses"]["200"] = {"$ref": "#/components/responses/Ok"}
    document = spec_doc(
        {"/x": item},
        responses={"Ok": {"content": {"application/json": {"schema": obj(a=INT)}}}},
    )
    assert isinstance(read_operations(document)[0].response, ObjectNode)


def test_operation_without_json_200_response_is_an_error() -> None:
    item: dict[str, Any] = {"get": {"responses": {"404": {}}}}
    with pytest.raises(SchemaWalkError, match="/x"):
        read_operations(spec_doc({"/x": item}))


def test_header_parameters_are_rejected() -> None:
    item = get_op(STR, [{"name": "X-Trace", "in": "header", "schema": STR}])
    with pytest.raises(SchemaWalkError, match="X-Trace"):
        read_operations(spec_doc({"/x": item}))


@pytest.fixture(scope="module")
def real_operations() -> dict[str, Any]:
    document = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    return {op.path: op for op in read_operations(document)}


def row_object(response: Any) -> ObjectNode:
    node = response.items if isinstance(response, ArrayNode) else response
    assert isinstance(node, ObjectNode)
    return node


def test_real_spec_has_93_walkable_get_operations(real_operations: dict[str, Any]) -> None:
    assert len(real_operations) == 93


def test_real_spec_rankings_nest_polls_and_ranks(real_operations: dict[str, Any]) -> None:
    week = row_object(real_operations["/rankings"].response)
    flat = flatten_object(week)
    assert [leaf.path for leaf in flat.leaves] == [("season",), ("seasonType",), ("week",)]
    assert [a.path for a in flat.arrays] == [("polls",)]
    ranks = flatten_object(flat.arrays[0].items).arrays
    assert [a.path for a in ranks] == [("ranks",)]


def test_real_spec_root_shapes(real_operations: dict[str, Any]) -> None:
    categories = real_operations["/stats/categories"].response
    assert isinstance(categories, ArrayNode) and categories.items == Scalar(type="string")
    assert isinstance(real_operations["/teams/matchup"].response, ObjectNode)
    assert isinstance(real_operations["/info"].response, ObjectNode)


def test_real_spec_variant_stat_value_is_json(real_operations: dict[str, Any]) -> None:
    stat = row_object(real_operations["/stats/season"].response)
    assert stat.fields[-1].name == "statValue"
    assert stat.fields[-1].node == JsonValue(reason="variant")


def test_real_spec_leaf_counts_match_the_reference(real_operations: dict[str, Any]) -> None:
    preview = flatten_object(row_object(real_operations["/games/{gameId}/preview"].response))
    overview = flatten_object(row_object(real_operations["/teams/season/overview"].response))
    assert len(preview.leaves) == 1837
    assert len(overview.leaves) == 839


def test_real_spec_path_parameter(real_operations: dict[str, Any]) -> None:
    params = real_operations["/games/{gameId}/preview"].parameters
    assert [(p.name, p.location, p.required) for p in params] == [("gameId", "path", True)]
