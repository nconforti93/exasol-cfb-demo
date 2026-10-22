import time
from dataclasses import replace
from typing import Any

import pytest

from vs.cfbd_vs.catalog import Param, Rule, Table
from vs.cfbd_vs.naming import param_column_name
from vs.cfbd_vs.rules import (
    Constraints,
    RuleViolation,
    apply_defaults,
    check_gating,
    check_rules,
    evaluate,
    parse_api_features,
)

PARAM_TYPES = {
    "year": "integer",
    "week": "integer",
    "team": "string",
    "conference": "string",
    "id": "integer",
    "gameId": "integer",
    "minYear": "integer",
    "maxYear": "integer",
    "latest": "boolean",
    "final": "boolean",
    "poll": "string",
    "days": "integer",
    "classification": "string",
}


def make_table(name: str = "T", rules: tuple[Rule, ...] = (), gated_by: str | None = None) -> Table:
    params = tuple(
        Param(
            name=api_name,
            column=param_column_name(api_name),
            location="query",
            type=spec_type,
            sql_type="X",
            enum=None,
        )
        for api_name, spec_type in PARAM_TYPES.items()
    )
    return Table(
        name=name,
        endpoint="/t",
        kind="endpoint",
        parent=None,
        array_path=(),
        hoisted=False,
        read_through=True,
        gated_by=gated_by,
        params=params,
        columns=(),
        rules=rules,
    )


def rule(rule_id: str, rule_type: str, params: tuple[str, ...], **details: Any) -> Rule:
    return Rule(id=rule_id, type=rule_type, message="", params=params, details=details)


def violation_text(table: Table, constraints: Constraints) -> str:
    with pytest.raises(RuleViolation) as caught:
        check_rules(table, constraints)
    return str(caught.value)


def test_required_missing_names_rule_table_and_column() -> None:
    table = make_table("PLAYS", (rule("plays.required_year_week", "required", ("year", "week")),))
    text = violation_text(table, {"year": [2024]})
    assert text.startswith("CFBD_VS.PLAYS: rule plays.required_year_week: ")
    assert "P_WEEK" in text
    assert "P_YEAR" not in text


def test_required_satisfied() -> None:
    table = make_table(rules=(rule("t.r", "required", ("year", "week")),))
    check_rules(table, {"year": [2024], "week": [8]})


def test_one_of_accepts_either_and_names_both_when_missing() -> None:
    table = make_table("RATINGS_SP", (rule("ratings_sp.year_or_team", "one_of", ("year", "team")),))
    check_rules(table, {"team": ["Michigan"]})
    check_rules(table, {"year": [2024]})
    text = violation_text(table, {})
    assert "rule ratings_sp.year_or_team" in text
    assert "P_YEAR" in text
    assert "P_TEAM" in text


def test_requires_any_triggers_only_when_if_param_set() -> None:
    table = make_table(
        "GAMES_TEAMS",
        (
            rule(
                "games_teams.week_team_or_conference",
                "requires_any",
                ("year", "week", "team", "conference"),
                **{"if": "year", "any_of": ["week", "team", "conference"]},
            ),
        ),
    )
    check_rules(table, {"id": [401628374]})
    check_rules(table, {"year": [2024], "week": [8]})
    text = violation_text(table, {"year": [2024]})
    for column in ("P_WEEK", "P_TEAM", "P_CONFERENCE"):
        assert column in text


def test_excludes_names_both_parameters() -> None:
    table = make_table(
        "CONFERENCES_AFFILIATIONS",
        (
            rule(
                "conferences_affiliations.year_vs_range",
                "excludes",
                ("year", "minYear", "maxYear"),
                **{"if": "year", "none_of": ["minYear", "maxYear"]},
            ),
        ),
    )
    check_rules(table, {"year": [2024]})
    check_rules(table, {"minYear": [2000], "maxYear": [2010]})
    text = violation_text(table, {"year": [2024], "minYear": [2000]})
    assert "P_YEAR" in text
    assert "P_MIN_YEAR" in text
    assert "P_MAX_YEAR" not in text


def test_excludes_with_if_value_ignores_other_trigger_values() -> None:
    table = make_table(
        rules=(
            rule(
                "t.latest_vs_final",
                "excludes",
                ("latest", "final"),
                **{"if": "latest", "if_value": True, "none_of": ["final"]},
            ),
        )
    )
    check_rules(table, {"latest": [False], "final": [True]})
    assert "t.latest_vs_final" in violation_text(table, {"latest": [True], "final": [True]})


def rankings_rules() -> tuple[Rule, ...]:
    return (
        rule(
            "rankings.latest_needs_cfp",
            "requires_value",
            ("latest", "poll"),
            **{"if": "latest", "if_value": True, "then": "poll", "then_value": "cfp"},
        ),
        rule(
            "rankings.latest_vs_final",
            "excludes",
            ("latest", "final"),
            **{"if": "latest", "none_of": ["final"]},
        ),
    )


def test_requires_value_rejects_missing_and_wrong_value() -> None:
    table = make_table("RANKINGS", rankings_rules())
    assert "rankings.latest_needs_cfp" in violation_text(table, {"year": [2024], "latest": [True]})
    wrong = violation_text(table, {"latest": [True], "poll": ["ap"]})
    assert "rankings.latest_needs_cfp" in wrong
    assert "P_POLL" in wrong
    check_rules(table, {"latest": [True], "poll": ["cfp"]})


def test_requires_value_checks_every_combination_of_a_value_set() -> None:
    table = make_table(rules=rankings_rules()[:1])
    assert "latest_needs_cfp" in violation_text(
        table, {"latest": [True, False], "poll": ["cfp", "ap"]}
    )
    check_rules(table, {"latest": [False], "poll": ["ap"]})
    check_rules(table, {"poll": ["ap"]})


def test_value_dependent_rules_combined() -> None:
    table = make_table("RANKINGS", rankings_rules())
    text = violation_text(
        table, {"latest": [True], "poll": ["cfp"], "final": [True], "year": [2024]}
    )
    assert "rankings.latest_vs_final" in text
    assert "latest_needs_cfp" not in text


def test_bounds_max_names_rule_column_and_limit() -> None:
    table = make_table(
        "INFO_USAGE", (rule("info_usage.days_max", "bounds", ("days",), **{"max": 31}),)
    )
    check_rules(table, {"days": [31]})
    text = violation_text(table, {"days": [60]})
    assert "info_usage.days_max" in text
    assert "P_DAYS" in text
    assert "31" in text


def test_bounds_min_and_range_object_without_enumeration() -> None:
    table = make_table(rules=(rule("t.years", "bounds", ("year",), min=1869, max=2100),))
    check_rules(table, {"year": range(1869, 2101)})
    assert "t.years" in violation_text(table, {"year": range(1800, 2000)})
    assert "t.years" in violation_text(table, {"year": range(2000, 10**12)})


def test_row_cap_requires_a_completeness_parameter() -> None:
    table = make_table(
        "PLAYS_STATS",
        (rule("plays_stats.row_cap", "row_cap", ("gameId",), rows=2000, complete_with=["gameId"]),),
    )
    check_rules(table, {"gameId": [401628374]})
    text = violation_text(table, {"year": [2024], "week": [8]})
    assert "plays_stats.row_cap" in text
    assert "2,000" in text
    assert "P_GAME_ID" in text


def test_message_is_appended_when_present() -> None:
    table = make_table(
        "ROSTER",
        (
            replace(
                rule("roster.year_required", "required", ("year",)),
                message="API silently defaults year to 2025",
            ),
        ),
    )
    text = violation_text(table, {"team": ["Michigan"]})
    assert "P_YEAR" in text
    assert "API silently defaults year to 2025" in text


def test_all_violations_are_reported_one_per_line() -> None:
    table = make_table(
        "T",
        (
            rule("t.a", "required", ("year",)),
            rule("t.b", "required", ("week",)),
        ),
    )
    with pytest.raises(RuleViolation) as caught:
        check_rules(table, {})
    lines = str(caught.value).splitlines()
    assert len(lines) == 2
    assert lines[0].startswith("CFBD_VS.T: rule t.a: ")
    assert lines[1].startswith("CFBD_VS.T: rule t.b: ")
    assert [v.rule_id for v in caught.value.violations] == ["t.a", "t.b"]


def test_rule_naming_unknown_parameter_is_a_catalog_error() -> None:
    table = make_table(rules=(rule("t.bad", "required", ("nope",)),))
    with pytest.raises(ValueError, match="nope"):
        check_rules(table, {})


def test_empty_value_set_is_a_programming_error() -> None:
    table = make_table(rules=(rule("t.r", "required", ("year",)),))
    with pytest.raises(ValueError, match="empty"):
        check_rules(table, {"year": []})


def test_defaults_fill_unset_parameters_only() -> None:
    table = make_table(
        rules=(rule("t.default_classification", "default", ("classification",), value="fbs"),)
    )
    filled = apply_defaults(table, {"year": [2024]})
    assert list(filled.constraints["classification"]) == ["fbs"]
    assert filled.defaulted == ("classification",)
    assert list(filled.constraints["year"]) == [2024]
    replaced = apply_defaults(table, {"classification": ["fcs"]})
    assert list(replaced.constraints["classification"]) == ["fcs"]
    assert replaced.defaulted == ()


def test_apply_defaults_does_not_mutate_input() -> None:
    table = make_table(rules=(rule("t.d", "default", ("classification",), value="fbs"),))
    original = {"year": [2024]}
    apply_defaults(table, original)
    assert original == {"year": [2024]}


def test_rules_see_defaulted_values() -> None:
    table = make_table(
        rules=(
            rule("t.default_poll", "default", ("poll",), value="cfp"),
            rule(
                "t.needs_cfp",
                "requires_value",
                ("latest", "poll"),
                **{"if": "latest", "if_value": True, "then": "poll", "then_value": "cfp"},
            ),
        )
    )
    result = evaluate(table, {"latest": [True]}, frozenset())
    assert list(result.constraints["poll"]) == ["cfp"]


def test_gating_names_feature_property_and_refresh_hint() -> None:
    table = make_table("GAMES_WEATHER", gated_by="weather")
    with pytest.raises(RuleViolation) as caught:
        check_gating(table, frozenset({"scoreboard"}))
    text = str(caught.value)
    assert text.startswith("CFBD_VS.GAMES_WEATHER: rule gating.weather: ")
    assert "weather" in text
    assert "API_FEATURES" in text
    assert "ALTER VIRTUAL SCHEMA" in text


def test_gating_passes_with_feature_and_for_ungated_tables() -> None:
    check_gating(make_table(gated_by="weather"), frozenset({"weather", "scoreboard"}))
    check_gating(make_table(gated_by=None), frozenset())


def test_gating_fails_closed_when_features_unknown() -> None:
    with pytest.raises(RuleViolation):
        check_gating(make_table(gated_by="weather"), parse_api_features(None))
    with pytest.raises(RuleViolation):
        check_gating(make_table(gated_by="weather"), parse_api_features(""))


def test_parse_api_features() -> None:
    assert parse_api_features("weather, scoreboard ,") == frozenset({"weather", "scoreboard"})
    assert parse_api_features(None) == frozenset()
    assert parse_api_features("  ") == frozenset()


def test_evaluate_checks_gating_before_rules() -> None:
    table = make_table(
        "GAMES_WEATHER", (rule("w.year", "required", ("year",)),), gated_by="weather"
    )
    with pytest.raises(RuleViolation) as caught:
        evaluate(table, {}, frozenset())
    assert [v.rule_id for v in caught.value.violations] == ["gating.weather"]
    with pytest.raises(RuleViolation) as caught:
        evaluate(table, {}, frozenset({"weather"}))
    assert [v.rule_id for v in caught.value.violations] == ["w.year"]


def test_evaluate_returns_defaulted_constraints_when_valid() -> None:
    table = make_table(rules=(rule("t.d", "default", ("classification",), value="fbs"),))
    result = evaluate(table, {"year": [2024]}, frozenset())
    assert list(result.constraints["classification"]) == ["fbs"]
    assert result.defaulted == ("classification",)


def test_child_table_with_same_rules_gives_same_rule_id() -> None:
    shared = (rule("rankings.required_year", "required", ("year",)),)
    parent = make_table("RANKINGS", shared)
    child = make_table("RANKINGS_POLLS_RANKS", shared)
    assert violation_text(parent, {}).split(": ")[1:3] == violation_text(child, {}).split(": ")[1:3]


def test_bool_does_not_match_integer_values() -> None:
    table = make_table(
        rules=(
            rule(
                "t.vv",
                "requires_value",
                ("latest", "year"),
                **{"if": "latest", "if_value": True, "then": "year", "then_value": 1},
            ),
        )
    )
    assert "t.vv" in violation_text(table, {"latest": [True], "year": [True]})
    check_rules(table, {"latest": [True], "year": [1]})


# --- hardening: value rules never enumerate an integer range, messages stay bounded ---

HUGE = range(0, 10**15)


def value_rule(if_value: Any, then_value: Any, rule_type: str = "requires_value") -> Rule:
    details = {"if": "week", "if_value": if_value}
    if rule_type == "requires_value":
        details.update({"then": "year", "then_value": then_value})
    else:
        details["none_of"] = ["team"]
    return rule("t.huge", rule_type, ("week", "year"), **details)


def test_requires_value_on_huge_ranges_answers_without_enumerating() -> None:
    table = make_table(rules=(value_rule(5, 2024),))
    started = time.monotonic()

    text = violation_text(table, {"week": HUGE, "year": HUGE})

    assert time.monotonic() - started < 1
    assert "t.huge" in text
    assert "got: 0, 1, 2, 3, 4, ..." in text


def test_excludes_trigger_on_a_huge_range_is_a_membership_test() -> None:
    table = make_table(rules=(value_rule(10**14, None, "excludes"),))
    started = time.monotonic()

    assert "t.huge" in violation_text(table, {"week": HUGE, "team": ["a"]})
    check_rules(table, {"week": range(0, 10), "team": ["a"]})

    assert time.monotonic() - started < 1


@pytest.mark.parametrize(
    ("if_value", "triggered"),
    [(5, True), (5.0, True), (5.5, False), (True, False), ("5", False), (-1, False)],
)
def test_range_membership_keeps_value_semantics_in_constant_time(
    if_value: Any, triggered: bool
) -> None:
    table = make_table(rules=(value_rule(if_value, None, "excludes"),))
    started = time.monotonic()

    if triggered:
        assert "t.huge" in violation_text(table, {"week": HUGE, "team": ["a"]})
    else:
        check_rules(table, {"week": HUGE, "team": ["a"]})

    assert time.monotonic() - started < 1


def test_bool_trigger_never_matches_an_integer_range() -> None:
    table = make_table(rules=(value_rule(True, None, "excludes"),))

    check_rules(table, {"week": range(0, 2), "team": ["a"]})


def test_offending_values_are_listed_at_most_five_and_truncated() -> None:
    table = make_table(
        rules=(
            rule(
                "t.poll",
                "requires_value",
                ("latest", "poll"),
                **{"if": "latest", "if_value": True, "then": "poll", "then_value": "cfp"},
            ),
        )
    )
    polls = [f"poll{i}-" + "x" * 500 for i in range(50)]

    text = violation_text(table, {"latest": [True], "poll": polls})

    assert len(text) < 800
    assert text.count("poll") <= 2 + 5
    assert text.rstrip(")").endswith("...")
