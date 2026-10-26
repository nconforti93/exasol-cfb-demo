"""`loader/templates.py`: slice templates and fixed filters compiled with the adapter's own code."""

from functools import cache
from pathlib import Path
from typing import Any

import pytest
import yaml

from loader.plan import DEFAULT_PLAN_PATH, LoadPlan, PlanError, parse_plan
from loader.templates import SliceTemplate, compile_plan
from vs.cfbd_vs.catalog import Catalog
from vs.cfbd_vs.plan import build_slice_key

CATALOG_PATH = Path(__file__).resolve().parents[2] / "vs" / "catalog" / "catalog.json"
SAMPLE_SEASONS = (1869, 1871, 2013, 2019, 2026)


def entry(table: str, slicing: str, **fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "table": table,
        "profile": "demo",
        "priority": 1,
        "slicing": slicing,
        "refresh_class": "static",
    }
    return {**base, **fields}


DEMO_ENTRIES: list[dict[str, Any]] = [
    entry("TEAMS", "none"),
    entry("VENUES", "none"),
    entry("CONFERENCES", "none"),
    entry("CONFERENCES_AFFILIATIONS", "season_range", seasons={"from": 1869, "to": 2025}),
    entry("CONFERENCES_AFFILIATIONS", "season", seasons={"from": 2026}),
    entry("CALENDAR", "season", seasons={"from": 2013}),
    entry("GAMES", "season", seasons={"from": 1869}, gaps=[1871], refresh_class="weekly"),
    entry("METRICS_WP_PREGAME", "season", seasons={"from": 2013}, refresh_class="weekly"),
    entry("RATINGS_SP", "season", seasons={"from": 2013}, refresh_class="weekly"),
    entry("RANKINGS_POLLS_RANKS", "season", seasons={"from": 2014}, refresh_class="weekly"),
]


@cache
def catalog() -> Catalog:
    return Catalog.from_json(CATALOG_PATH.read_text(encoding="utf-8"))


def parse(entries: list[dict[str, Any]]) -> LoadPlan:
    data = {"version": 1, "entries": entries}
    return parse_plan(yaml.safe_dump(data, sort_keys=False), catalog())


def compile_entries(entries: list[dict[str, Any]]) -> tuple[SliceTemplate, ...]:
    return compile_plan(parse(entries), catalog())


def table_of(name: str) -> Any:
    return next(table for table in catalog().tables if table.name == name)


def adapter_key(template: SliceTemplate, values: dict[str, Any]) -> str:
    return build_slice_key(table_of(template.table), values)


def test_every_demo_entry_has_a_template_equal_to_the_adapters_keys() -> None:
    plan = parse(DEMO_ENTRIES)
    for template, source in zip(compile_plan(plan, catalog()), plan.entries, strict=True):
        fixed = source.fixed_params()
        if source.slicing == "none":
            assert template.slice_key() == adapter_key(template, fixed)
        elif source.slicing == "season_range":
            expected = adapter_key(
                template,
                {**fixed, "minYear": source.seasons_from, "maxYear": source.seasons_to},
            )
            assert template.slice_key() == expected
        else:
            for season in SAMPLE_SEASONS:
                values = {**fixed, "year": season}
                key = template.slice_key(season)
                defaults = {"seasonType": "both"} if "seasonType" in key else {}
                assert key == adapter_key(template, {**values, **defaults})


@pytest.mark.skipif(
    not DEFAULT_PLAN_PATH.exists(), reason="loader/load-plan.yaml is authored in 2.3"
)
def test_every_entry_of_the_project_plan_matches_the_adapters_keys() -> None:
    text = DEFAULT_PLAN_PATH.read_text(encoding="utf-8")
    plan = parse_plan(text, catalog())
    for template, source in zip(compile_plan(plan, catalog()), plan.entries, strict=True):
        table = table_of(source.table)
        defaults = {
            rule.params[0]: rule.details["value"] for rule in table.rules if rule.type == "default"
        }
        fixed = {**defaults, **source.fixed_params()}
        if source.slicing == "season":
            for season in SAMPLE_SEASONS:
                assert template.slice_key(season) == build_slice_key(
                    table, {**fixed, "year": season}
                )
        elif source.slicing == "season_range":
            values = {**fixed, "minYear": source.seasons_from, "maxYear": source.seasons_to}
            assert template.slice_key() == build_slice_key(table, values)
        else:
            assert template.slice_key() == build_slice_key(table, fixed)


def test_the_design_examples_are_the_templates() -> None:
    by_position = compile_entries(DEMO_ENTRIES)
    assert by_position[6].template == "/games?seasonType=both&year={season}"
    assert (
        by_position[3].template
        == "/conferences/affiliations?maxYear={season_to}&minYear={season_from}"
    )
    assert by_position[0].template == "/teams"


def test_the_season_range_template_fills_the_entrys_own_bounds() -> None:
    template = compile_entries(DEMO_ENTRIES)[3]
    assert template.slice_key() == "/conferences/affiliations?maxYear=2025&minYear=1869"


def test_a_season_template_requires_a_season_and_other_templates_refuse_one() -> None:
    templates = compile_entries(DEMO_ENTRIES)
    with pytest.raises(ValueError, match="needs a season"):
        templates[6].slice_key()
    with pytest.raises(ValueError, match="takes no season"):
        templates[0].slice_key(2020)
    with pytest.raises(ValueError, match="takes no season"):
        templates[3].slice_key(2020)


def test_a_template_carries_its_entry_identity_and_slicing() -> None:
    templates = compile_entries(DEMO_ENTRIES)
    assert [t.entry_id for t in templates] == list(range(1, len(DEMO_ENTRIES) + 1))
    assert templates[6].table == "GAMES"
    assert templates[6].slicing == "season"
    assert templates[6].gated_by is None


def test_a_gated_table_keeps_its_feature() -> None:
    compiled = compile_entries(
        [entry("GAMES_WEATHER", "season", profile="full", seasons={"from": 2020, "to": 2020})]
    )[0]
    assert compiled.gated_by == "weather"
    assert compiled.slice_key(2020) == "/games/weather?seasonType=both&year=2020"


def test_no_fixed_parameters_means_no_fixed_filter() -> None:
    assert all(t.fixed_filter is None for t in compile_entries(DEMO_ENTRIES))


def test_fixed_parameters_become_a_quoted_filter_on_the_echo_columns() -> None:
    compiled = compile_entries(
        [
            entry(
                "GAMES",
                "season",
                seasons={"from": 2020},
                params={"seasonType": "regular", "classification": "fbs"},
                refresh_class="weekly",
            )
        ]
    )[0]
    assert compiled.fixed_filter == (
        "(\"P_CLASSIFICATION\" = 'fbs') AND (\"P_SEASON_TYPE\" = 'regular')"
    )
    assert compiled.slice_key(2020) == "/games?classification=fbs&seasonType=regular&year=2020"


def test_a_fixed_value_overrides_the_explicit_default_in_the_key() -> None:
    compiled = compile_entries(
        [
            entry(
                "GAMES",
                "season",
                seasons={"from": 2020},
                params={"seasonType": "postseason"},
                refresh_class="weekly",
            )
        ]
    )[0]
    assert compiled.slice_key(2021) == "/games?seasonType=postseason&year=2021"


def test_quotes_in_a_fixed_value_are_doubled_in_the_filter() -> None:
    compiled = compile_entries(
        [
            entry(
                "GAMES",
                "season",
                seasons={"from": 2020},
                params={"team": "Texas A&M's"},
                refresh_class="weekly",
            )
        ]
    )[0]
    assert compiled.fixed_filter == "(\"P_TEAM\" = 'Texas A&M''s')"
    assert "team=Texas%20A%26M%27s" in compiled.slice_key(2020)


def test_a_boolean_fixed_parameter_renders_as_a_boolean_literal() -> None:
    compiled = compile_entries(
        [
            entry(
                "RANKINGS_POLLS_RANKS",
                "season",
                seasons={"from": 2014},
                params={"poll": "cfp", "final": True},
                refresh_class="weekly",
            )
        ]
    )[0]
    assert compiled.fixed_filter == '("P_FINAL" = TRUE) AND ("P_POLL" = \'cfp\')'
    assert "final=true" in compiled.slice_key(2020)


def test_a_plays_entry_with_season_slicing_fails_naming_the_rule() -> None:
    plan = parse([entry("PLAYS", "season", seasons={"from": 2020}, refresh_class="weekly")])
    with pytest.raises(PlanError) as raised:
        compile_plan(plan, catalog())
    [issue] = raised.value.issues
    assert issue.rule == "plays.spec_required"
    assert issue.where == "entry 1 (PLAYS)"
    assert "P_WEEK" in issue.message
    assert str(issue).startswith("entry 1 (PLAYS): plays.spec_required: ")


def test_every_broken_entry_is_listed_in_one_error() -> None:
    plan = parse(
        [
            entry("TEAMS", "none"),
            entry("PLAYS", "season", seasons={"from": 2020}, refresh_class="weekly"),
            entry(
                "RANKINGS_POLLS_RANKS",
                "season",
                seasons={"from": 2014},
                params={"final": True},
                refresh_class="weekly",
            ),
        ]
    )
    with pytest.raises(PlanError) as raised:
        compile_plan(plan, catalog())
    found = {(issue.where, issue.rule) for issue in raised.value.issues}
    assert ("entry 2 (PLAYS)", "plays.spec_required") in found
    assert ("entry 3 (RANKINGS_POLLS_RANKS)", "rankings.final_needs_cfp") in found
    assert not any(where.startswith("entry 1 ") for where, _ in found)


def test_compiling_sends_no_request_and_is_repeatable() -> None:
    plan = parse(DEMO_ENTRIES)
    assert compile_plan(plan, catalog()) == compile_plan(plan, catalog())


def test_a_template_never_contains_the_sentinels() -> None:
    for template in compile_entries(DEMO_ENTRIES):
        digits = "".join(ch for ch in template.template if ch.isdigit())
        assert "9000000" not in digits
