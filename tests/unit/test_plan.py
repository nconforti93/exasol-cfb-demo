import json
import random
from typing import Any

import pytest

from vs.cfbd_vs import plan
from vs.cfbd_vs.catalog import Column, Param, Table
from vs.cfbd_vs.plan import BudgetError, PlanError, RequestPlan


def make_param(name: str, type_: str = "integer", location: str = "query") -> Param:
    return Param(
        name=name,
        column="P_" + name.upper(),
        location=location,
        type=type_,
        sql_type="DECIMAL(18,0)",
        enum=None,
    )


def make_table(endpoint: str = "/games", params: tuple[Param, ...] | None = None) -> Table:
    if params is None:
        params = (
            make_param("year"),
            make_param("week"),
            make_param("seasonType", "string"),
        )
    return Table(
        name="GAMES",
        endpoint=endpoint,
        kind="endpoint",
        parent=None,
        array_path=(),
        hoisted=False,
        read_through=True,
        gated_by=None,
        params=params,
        columns=(Column(name="ROW_NO", kind="position"),),
        rules=(),
    )


def test_range_and_in_list_fan_out_into_their_product() -> None:
    values: dict[str, Any] = {"year": [2019, 2020, 2021], "week": [8, 9], "seasonType": ["both"]}
    result = plan.plan_requests(make_table(), values, budget=10)
    assert result.slice_keys == (
        "/games?seasonType=both&week=8&year=2019",
        "/games?seasonType=both&week=9&year=2019",
        "/games?seasonType=both&week=8&year=2020",
        "/games?seasonType=both&week=9&year=2020",
        "/games?seasonType=both&week=8&year=2021",
        "/games?seasonType=both&week=9&year=2021",
    )


def test_expansion_order_is_independent_of_the_input_mapping_order() -> None:
    forward = plan.plan_requests(make_table(), {"year": [1, 2], "week": [3, 4]}, budget=None)
    backward = plan.plan_requests(make_table(), {"week": [3, 4], "year": [1, 2]}, budget=None)
    assert forward.slice_keys == backward.slice_keys


def test_duplicate_values_are_requested_once() -> None:
    values = {"year": [2020, 2020], "week": [8, 8]}
    result = plan.plan_requests(make_table(), values, budget=10)
    assert result.slice_keys == ("/games?week=8&year=2020",)


def test_integral_float_and_integer_collapse_to_one_value() -> None:
    result = plan.plan_requests(make_table(), {"year": [2020, 2020.0]}, budget=10)
    assert result.slice_keys == ("/games?year=2020",)


def test_params_without_values_are_not_sent() -> None:
    result = plan.plan_requests(make_table(), {"year": [2024]}, budget=10)
    assert result.slice_keys == ("/games?year=2024",)


def test_no_parameters_gives_the_bare_path() -> None:
    result = plan.plan_requests(make_table("/teams", params=()), {}, budget=10)
    assert result.slice_keys == ("/teams",)


def test_none_values_are_treated_as_not_sent() -> None:
    result = plan.plan_requests(make_table(), {"year": [2024], "week": None}, budget=10)
    assert result.slice_keys == ("/games?year=2024",)


def test_over_budget_names_planned_budget_and_remedy_before_expanding() -> None:
    years = list(range(2000, 2025))
    with pytest.raises(BudgetError) as error:
        plan.plan_requests(make_table(), {"year": years}, budget=10)
    message = str(error.value)
    assert message.startswith("CFBD_VS.GAMES: ")
    assert "25 requests are planned" in message
    assert "MAX_CALLS_PER_QUERY is 10" in message
    assert "narrower filter" in message
    assert "raise MAX_CALLS_PER_QUERY" in message


def test_budget_is_checked_from_counts_so_huge_products_never_materialize() -> None:
    huge = {"year": list(range(100_000)), "week": list(range(100_000))}
    with pytest.raises(BudgetError) as error:
        plan.plan_requests(make_table(), huge, budget=10)
    assert "10000000000 requests are planned" in str(error.value)


def test_exactly_at_budget_is_allowed() -> None:
    result = plan.plan_requests(make_table(), {"year": list(range(2015, 2025))}, budget=10)
    assert len(result.slice_keys) == 10
    assert result.budget == 10


def test_unlimited_budget_skips_the_check() -> None:
    result = plan.plan_requests(make_table(), {"year": list(range(2000, 2025))}, budget=None)
    assert len(result.slice_keys) == 25
    assert result.budget is None


def test_budget_counts_distinct_requests_not_input_values() -> None:
    result = plan.plan_requests(make_table(), {"year": [2020] * 50}, budget=1)
    assert result.slice_keys == ("/games?year=2020",)


def test_string_values_are_percent_encoded_per_rfc3986() -> None:
    table = make_table("/teams/matchup", (make_param("team1", "string"),))
    key = plan.plan_requests(table, {"team1": ["Ohio State"]}, budget=1).slice_keys[0]
    assert key == "/teams/matchup?team1=Ohio%20State"


def test_unreserved_characters_stay_and_everything_else_is_escaped() -> None:
    table = make_table("/teams", (make_param("team", "string"),))
    key = plan.plan_requests(table, {"team": ["a-b_c.d~e&f=g+h/é"]}, budget=1).slice_keys[0]
    assert key == "/teams?team=a-b_c.d~e%26f%3Dg%2Bh%2F%C3%A9"


def test_foreign_host_values_stay_inside_the_query_string() -> None:
    table = make_table("/teams", (make_param("team", "string"),))
    key = plan.plan_requests(table, {"team": ["http://other-host/"]}, budget=1).slice_keys[0]
    assert key == "/teams?team=http%3A%2F%2Fother-host%2F"
    assert "://" not in key


def test_boolean_values_render_as_true_and_false() -> None:
    table = make_table("/x", (make_param("flag", "boolean"),))
    result = plan.plan_requests(table, {"flag": [True, False]}, budget=2)
    assert result.slice_keys == ("/x?flag=true", "/x?flag=false")


def test_booleans_are_not_accepted_for_integer_parameters() -> None:
    with pytest.raises(PlanError, match="year"):
        plan.plan_requests(make_table(), {"year": [True]}, budget=1)


def test_non_integral_values_are_rejected_for_integer_parameters() -> None:
    with pytest.raises(PlanError, match="year"):
        plan.plan_requests(make_table(), {"year": [2020.5]}, budget=1)


def test_strings_are_rejected_for_integer_parameters() -> None:
    with pytest.raises(PlanError, match="year"):
        plan.plan_requests(make_table(), {"year": ["2020"]}, budget=1)


def test_integers_are_rejected_for_string_parameters() -> None:
    with pytest.raises(PlanError, match="seasonType"):
        plan.plan_requests(make_table(), {"seasonType": [1]}, budget=1)


def test_number_parameters_render_plainly_and_reject_non_finite_values() -> None:
    table = make_table("/x", (make_param("ratio", "number"),))
    assert plan.plan_requests(table, {"ratio": [1.5, 2]}, budget=2).slice_keys == (
        "/x?ratio=1.5",
        "/x?ratio=2",
    )
    with pytest.raises(PlanError, match="ratio"):
        plan.plan_requests(table, {"ratio": [float("nan")]}, budget=1)


def test_path_parameters_are_substituted_and_not_repeated_in_the_query() -> None:
    table = make_table(
        "/games/{gameId}/preview", (make_param("gameId", location="path"), make_param("year"))
    )
    key = plan.plan_requests(table, {"gameId": [401628374], "year": [2024]}, budget=1)
    assert key.slice_keys == ("/games/401628374/preview?year=2024",)


def test_path_values_are_percent_encoded_and_cannot_add_segments() -> None:
    table = make_table("/x/{name}", (make_param("name", "string", "path"),))
    result = plan.plan_requests(table, {"name": ["../../other"]}, budget=1)
    assert result.slice_keys == ("/x/..%2F..%2Fother",)


@pytest.mark.parametrize("value", [".", ".."])
def test_dot_segments_are_rejected_as_path_values(value: str) -> None:
    table = make_table("/x/{name}", (make_param("name", "string", "path"),))
    with pytest.raises(PlanError, match="name"):
        plan.plan_requests(table, {"name": [value]}, budget=1)


def test_missing_path_value_is_an_error() -> None:
    table = make_table("/games/{gameId}", (make_param("gameId", location="path"),))
    with pytest.raises(PlanError, match="gameId"):
        plan.plan_requests(table, {}, budget=1)


def test_unknown_parameter_is_an_error() -> None:
    with pytest.raises(PlanError, match="bogus"):
        plan.plan_requests(make_table(), {"bogus": [1]}, budget=1)


def test_empty_value_list_is_an_error_not_a_silent_zero_plan() -> None:
    with pytest.raises(PlanError, match="year"):
        plan.plan_requests(make_table(), {"year": []}, budget=1)


def test_path_template_without_a_matching_path_parameter_is_an_error() -> None:
    table = make_table("/games/{gameId}", ())
    with pytest.raises(PlanError, match="gameId"):
        plan.plan_requests(table, {}, budget=1)


def test_slice_keys_never_contain_a_scheme_or_host() -> None:
    result = plan.plan_requests(make_table(), {"year": [2019, 2020]}, budget=10)
    assert all(key.startswith("/") for key in result.slice_keys)


def build_plan() -> RequestPlan:
    return plan.plan_requests(make_table(), {"year": [2019, 2020], "week": [8]}, budget=10)


def test_manifest_lists_table_mode_flag_budget_count_and_slices() -> None:
    manifest = plan.build_manifest(build_plan(), mode="live", read_through=True)
    assert manifest == {
        "table": "GAMES",
        "fetch_mode": "live",
        "read_through": True,
        "budget": 10,
        "planned_requests": 2,
        "slice_keys": ["/games?week=8&year=2019", "/games?week=8&year=2020"],
    }


def test_manifest_json_round_trips() -> None:
    manifest = plan.build_manifest(build_plan(), mode="replay", read_through=False)
    assert json.loads(plan.manifest_json(manifest)) == manifest


def test_manifest_json_cannot_close_an_sql_comment() -> None:
    table = make_table("/teams", (make_param("team", "string"),))
    built = plan.plan_requests(table, {"team": ["*/ DROP"]}, budget=1)
    text = plan.manifest_json(plan.build_manifest(built, mode="live", read_through=True))
    assert "*/" not in text
    assert json.loads(text)["slice_keys"] == list(built.slice_keys)


def test_manifest_json_is_compact_and_deterministic() -> None:
    manifest = plan.build_manifest(build_plan(), mode="live", read_through=True)
    assert plan.manifest_json(manifest) == plan.manifest_json(dict(reversed(manifest.items())))
    assert "\n" not in plan.manifest_json(manifest)


def test_unlimited_budget_appears_as_null_in_the_manifest() -> None:
    built = plan.plan_requests(make_table(), {"year": [2020]}, budget=None)
    assert plan.build_manifest(built, mode="live", read_through=True)["budget"] is None


def test_manifest_comment_wraps_the_json_with_the_cfbd_tag() -> None:
    manifest = plan.build_manifest(build_plan(), mode="live", read_through=True)
    comment = plan.manifest_comment(manifest)
    assert comment.startswith("/*CFBD {")
    assert comment.endswith("}*/")
    assert json.loads(comment[len("/*CFBD ") : -len("*/")]) == manifest


HOSTILE_PIECES = [
    "*/",
    "/*",
    "*",
    "/",
    "**/",
    "*//*",
    "\\",
    '"',
    "'",
    "\n",
    "\x00",
    "\ud800",
    "\u2028",
    "a",
]


def hostile(rng: random.Random) -> str:
    return "".join(rng.choice(HOSTILE_PIECES) for _ in range(rng.randint(0, 8)))


def hostile_manifest(seed: int) -> dict[str, Any]:
    rng = random.Random(seed)
    return {
        hostile(rng): [hostile(rng) + "*/", rng.randint(-5, 5), None, True],
        "slice_keys": [hostile(rng) for _ in range(rng.randint(0, 4))],
    }


@pytest.mark.parametrize("seed", range(100))
def test_hostile_manifests_cannot_close_the_comment_early(seed: int) -> None:
    manifest = hostile_manifest(seed)
    comment = plan.manifest_comment(manifest)
    assert comment.count("*/") == 1
    assert comment.endswith("}*/")
    assert comment.isascii()
    body = comment[len("/*CFBD ") : -len("*/")]
    assert json.loads(body) == manifest


def test_a_range_is_expanded_only_after_the_budget_check() -> None:
    with pytest.raises(BudgetError, match="1000000000000 requests are planned"):
        plan.plan_requests(make_table(), {"year": range(10**12)}, budget=10)


def test_a_range_of_integers_expands_like_a_list() -> None:
    result = plan.plan_requests(make_table(), {"year": range(2019, 2022), "week": [8]}, budget=10)
    assert result.slice_keys == tuple(f"/games?week=8&year={y}" for y in (2019, 2020, 2021))


def test_ranges_are_type_checked_against_the_parameter() -> None:
    table = make_table("/x", (make_param("name", "string"),))
    with pytest.raises(PlanError, match="name"):
        plan.plan_requests(table, {"name": range(3)}, budget=10)


def test_sets_expand_in_sorted_order() -> None:
    result = plan.plan_requests(make_table(), {"year": {2021, 2019, 2020}}, budget=10)
    assert result.slice_keys == tuple(f"/games?year={y}" for y in (2019, 2020, 2021))


def test_an_empty_range_is_an_error() -> None:
    with pytest.raises(PlanError, match="year"):
        plan.plan_requests(make_table(), {"year": range(0)}, budget=10)
