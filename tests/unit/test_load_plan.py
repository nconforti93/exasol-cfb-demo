"""`loader/plan.py`: the load-plan schema, loader and validator, against the real catalog."""

import copy
import hashlib
import json
from collections.abc import Callable
from functools import cache
from pathlib import Path
from typing import Any

import pytest
import yaml

from loader import plan as load_plan
from loader.plan import LoadPlan, PlanConfig, PlanError
from vs.cfbd_vs.catalog import Catalog

CATALOG_PATH = Path(__file__).resolve().parents[2] / "vs" / "catalog" / "catalog.json"

PROJECT_PLAN: dict[str, Any] = {
    "version": 1,
    "config": {
        "monthly_call_budget": 1000,
        "refresh_reserve": 150,
        "quarantine_after": 2,
        "season_rollover": "08-01",
        "season_final_after": "02-15",
        "refresh_profiles": ["demo"],
    },
    "snapshot": {"schemas": ["CFB_CORE"], "meta_tables": ["LOAD_LOG", "FETCH_LOG"]},
    "refresh_steps": [],
    "entries": [
        {
            "table": "TEAMS",
            "profile": "demo",
            "priority": 1,
            "slicing": "none",
            "refresh_class": "static",
        },
        {
            "table": "CONFERENCES_AFFILIATIONS",
            "profile": "demo",
            "priority": 1,
            "slicing": "season_range",
            "seasons": {"from": 1869, "to": 2025},
            "refresh_class": "static",
        },
        {
            "table": "CONFERENCES_AFFILIATIONS",
            "profile": "demo",
            "priority": 1,
            "slicing": "season",
            "seasons": {"from": 2026},
            "refresh_class": "static",
        },
        {
            "table": "GAMES",
            "profile": "demo",
            "priority": 3,
            "slicing": "season",
            "seasons": {"from": 1869},
            "gaps": [1871],
            "refresh_class": "weekly",
            "note": "Elo and win probability per game",
        },
        {
            "table": "GAMES",
            "profile": "full",
            "priority": 9,
            "slicing": "season",
            "seasons": {"from": 1869, "to": 1869},
            "params": {"classification": "fbs"},
            "refresh_class": "weekly",
        },
    ],
}


@cache
def catalog() -> Catalog:
    return Catalog.from_json(CATALOG_PATH.read_text(encoding="utf-8"))


def parse(data: dict[str, Any]) -> LoadPlan:
    return load_plan.parse_plan(yaml.safe_dump(data, sort_keys=False), catalog())


def issues_of(data: dict[str, Any]) -> list[str]:
    with pytest.raises(PlanError) as raised:
        parse(data)
    return [str(issue) for issue in raised.value.issues]


def only_issue(data: dict[str, Any]) -> str:
    found = issues_of(data)
    assert len(found) == 1, found
    return found[0]


def valid_data() -> dict[str, Any]:
    """The project-like plan; the `full` GAMES entry covers 1869 only, so nothing overlaps."""
    data = copy.deepcopy(PROJECT_PLAN)
    data["entries"][3]["seasons"] = {"from": 1870}
    return data


def test_a_valid_plan_is_loaded_with_positional_entry_ids() -> None:
    plan = parse(valid_data())
    assert plan.version == 1
    assert [e.entry_id for e in plan.entries] == [1, 2, 3, 4, 5]
    teams, affiliations_range, affiliations_season, games, games_full = plan.entries
    assert (teams.table, teams.slicing, teams.seasons_from, teams.seasons_to) == (
        "TEAMS",
        "none",
        None,
        None,
    )
    assert (affiliations_range.seasons_from, affiliations_range.seasons_to) == (1869, 2025)
    assert (affiliations_season.seasons_from, affiliations_season.seasons_to) == (2026, None)
    assert games.gaps == (1871,)
    assert games.note == "Elo and win probability per game"
    assert games.refresh_class == "weekly"
    assert games_full.profile == "full"
    assert games_full.fixed_params() == {"classification": "fbs"}
    assert games_full.params == (("classification", "fbs"),)


def test_profile_rank_puts_demo_before_full() -> None:
    plan = parse(valid_data())
    assert plan.entries[0].profile_rank == 1
    assert plan.entries[4].profile_rank == 2


def test_config_defaults_apply_when_the_section_is_missing() -> None:
    data = valid_data()
    del data["config"]
    del data["snapshot"]
    del data["refresh_steps"]
    plan = parse(data)
    assert plan.config == PlanConfig()
    assert plan.config.monthly_call_budget == 1000
    assert plan.config.refresh_reserve == 150
    assert plan.config.quarantine_after == 2
    assert plan.config.season_rollover == "08-01"
    assert plan.config.season_final_after == "02-15"
    assert plan.config.refresh_profiles == ("demo",)
    assert plan.snapshot.schemas == ("CFB_CORE",)
    assert plan.snapshot.meta_tables == ("LOAD_LOG", "FETCH_LOG")
    assert plan.refresh_steps == ()


def test_config_settings_are_the_loader_config_rows() -> None:
    settings = PlanConfig(refresh_profiles=("demo", "full")).settings()
    assert settings == {
        "MONTHLY_CALL_BUDGET": "1000",
        "REFRESH_RESERVE": "150",
        "QUARANTINE_AFTER": "2",
        "SEASON_ROLLOVER": "08-01",
        "SEASON_FINAL_AFTER": "02-15",
        "REFRESH_PROFILES": "demo,full",
    }


def test_refresh_steps_are_loaded_in_step_order() -> None:
    data = valid_data()
    data["refresh_steps"] = [
        {"step_no": 200, "name": "PREDICT", "script": "CFB_INFRA.REFRESH_PREDICT"},
        {"step_no": 100, "name": "MODEL", "script": "CFB_INFRA.REFRESH_MODEL"},
    ]
    steps = parse(data).refresh_steps
    assert [(s.step_no, s.name, s.script) for s in steps] == [
        (100, "MODEL", "CFB_INFRA.REFRESH_MODEL"),
        (200, "PREDICT", "CFB_INFRA.REFRESH_PREDICT"),
    ]


def test_the_project_plan_example_from_the_design_validates() -> None:
    data = valid_data()
    data["entries"] = [
        {
            "table": "TEAMS",
            "profile": "demo",
            "priority": 1,
            "slicing": "none",
            "refresh_class": "static",
        },
        {
            "table": "VENUES",
            "profile": "demo",
            "priority": 1,
            "slicing": "none",
            "refresh_class": "static",
        },
        {
            "table": "CONFERENCES",
            "profile": "demo",
            "priority": 1,
            "slicing": "none",
            "refresh_class": "static",
        },
        {
            "table": "CALENDAR",
            "profile": "demo",
            "priority": 2,
            "slicing": "season",
            "seasons": {"from": 2013},
            "refresh_class": "static",
        },
        {
            "table": "METRICS_WP_PREGAME",
            "profile": "demo",
            "priority": 4,
            "slicing": "season",
            "seasons": {"from": 2013},
            "refresh_class": "weekly",
        },
        {
            "table": "RATINGS_SP",
            "profile": "demo",
            "priority": 5,
            "slicing": "season",
            "seasons": {"from": 2013},
            "refresh_class": "weekly",
        },
        {
            "table": "RANKINGS_POLLS_RANKS",
            "profile": "demo",
            "priority": 6,
            "slicing": "season",
            "seasons": {"from": 2014},
            "refresh_class": "weekly",
        },
    ]
    assert len(parse(data).entries) == 7


# --- the validator -----------------------------------------------------------------------------


def with_entry(index: int, **fields: Any) -> Callable[[dict[str, Any]], None]:
    def change(data: dict[str, Any]) -> None:
        data["entries"][index].update(fields)

    return change


def broken(change: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    data = valid_data()
    change(data)
    return data


def test_an_unknown_table_names_the_entry_and_the_rule() -> None:
    message = only_issue(broken(with_entry(0, table="NO_SUCH_TABLE")))
    assert message.startswith("entry 1 (NO_SUCH_TABLE): unknown-table")
    assert "NO_SUCH_TABLE" in message


def test_a_table_without_a_mirror_is_rejected_as_volatile() -> None:
    message = only_issue(broken(with_entry(0, table="GAMES_SCHEDULE")))
    assert "entry 1 (GAMES_SCHEDULE): volatile-table" in message
    assert "no CFB_CORE mirror" in message


def test_a_gated_table_is_rejected_in_demo_and_allowed_in_full() -> None:
    gated = {
        "table": "GAMES_WEATHER",
        "priority": 7,
        "slicing": "season",
        "seasons": {"from": 2013},
        "refresh_class": "weekly",
    }

    def demo(data: dict[str, Any]) -> None:
        data["entries"].append({**gated, "profile": "demo"})

    def full(data: dict[str, Any]) -> None:
        data["entries"].append({**gated, "profile": "full"})

    message = only_issue(broken(demo))
    assert "entry 6 (GAMES_WEATHER): gated-in-demo" in message
    assert "weather" in message
    assert len(parse(broken(full)).entries) == 6


@pytest.mark.parametrize(
    ("field", "value", "rule"),
    [
        ("profile", "archive", "unknown-profile"),
        ("slicing", "season_week", "unknown-slicing"),
        ("slicing", "weekly", "unknown-slicing"),
        ("refresh_class", "archive", "unknown-refresh-class"),
    ],
)
def test_unknown_vocabulary_is_rejected(field: str, value: str, rule: str) -> None:
    message = only_issue(broken(with_entry(3, **{field: value})))
    assert f"entry 4 (GAMES): {rule}" in message
    assert value in message


def test_season_week_is_named_as_backlog() -> None:
    message = only_issue(broken(with_entry(3, slicing="season_week")))
    assert "backlog" in message


@pytest.mark.parametrize("slicing", ["none", "season_range"])
def test_none_and_season_range_must_be_static(slicing: str) -> None:
    fields: dict[str, Any] = {"slicing": slicing, "refresh_class": "weekly"}
    if slicing == "none":
        fields["seasons"] = None
    else:
        fields["seasons"] = {"from": 2000, "to": 2010}
    fields["gaps"] = None
    data = broken(with_entry(3, **fields))
    for key in [k for k, v in data["entries"][3].items() if v is None]:
        del data["entries"][3][key]
    data["entries"][3]["table"] = "CONFERENCES_AFFILIATIONS"
    message = only_issue(data)
    assert "entry 4 (CONFERENCES_AFFILIATIONS): static-required" in message


def test_a_table_without_the_year_parameter_cannot_be_sliced_by_season() -> None:
    message = only_issue(
        broken(with_entry(0, table="VENUES", slicing="season", seasons={"from": 2000}))
    )
    assert "entry 1 (VENUES): slicing-param" in message
    assert "'year'" in message


def test_a_table_without_min_and_max_year_cannot_be_a_season_range() -> None:
    message = only_issue(
        broken(
            with_entry(1, table="TEAMS", slicing="season_range", seasons={"from": 2000, "to": 2010})
        )
    )
    assert "entry 2 (TEAMS): slicing-param" in message
    assert "minYear" in message
    assert "maxYear" in message


def test_unknown_fixed_parameters_are_rejected() -> None:
    message = only_issue(broken(with_entry(3, params={"colour": "red"})))
    assert "entry 4 (GAMES): unknown-param" in message
    assert "colour" in message


@pytest.mark.parametrize(
    ("params", "needle"),
    [
        ({"classification": "pro"}, "enum"),
        ({"classification": 7}, "string"),
        ({"id": "abc"}, "integer"),
        ({"id": True}, "integer"),
        ({"id": 1.5}, "integer"),
    ],
)
def test_fixed_parameter_values_must_fit_type_and_enum(params: dict[str, Any], needle: str) -> None:
    message = only_issue(broken(with_entry(3, params=params)))
    assert "entry 4 (GAMES): bad-param-value" in message
    assert needle in message


def test_a_fixed_parameter_may_not_repeat_what_the_slicing_sets() -> None:
    message = only_issue(broken(with_entry(3, params={"year": 2019})))
    assert "entry 4 (GAMES): slice-param-fixed" in message
    assert "'year'" in message
    range_message = only_issue(broken(with_entry(1, params={"minYear": 1900})))
    assert "entry 2 (CONFERENCES_AFFILIATIONS): slice-param-fixed" in range_message


@pytest.mark.parametrize(
    ("seasons", "rule"),
    [
        ({"from": 2020, "to": 2019}, "season-order"),
        ({"from": 1868}, "season-order"),
        ({"from": 2020, "to": 1700}, "season-order"),
        ({"from": 2020, "to": 2101}, "season-order"),
        ({"to": 2020}, "bad-seasons"),
        ({"from": "2020"}, "bad-seasons"),
        ({"from": 2020, "until": 2022}, "bad-seasons"),
        ("2020", "bad-seasons"),
    ],
)
def test_seasons_must_be_in_order_and_not_before_1869(seasons: Any, rule: str) -> None:
    message = only_issue(broken(with_entry(3, seasons=seasons, gaps=[])))
    assert f"entry 4 (GAMES): {rule}" in message


def test_a_season_range_needs_a_to() -> None:
    message = only_issue(broken(with_entry(1, seasons={"from": 1869})))
    assert "entry 2 (CONFERENCES_AFFILIATIONS): season-order" in message
    assert "requires seasons.to" in message


def test_seasons_are_required_for_season_slicing_and_forbidden_for_none() -> None:
    no_seasons = broken(lambda d: d["entries"][3].pop("seasons"))
    assert "entry 4 (GAMES): missing-seasons" in only_issue(no_seasons)
    with_seasons = broken(with_entry(0, seasons={"from": 2000}))
    assert "entry 1 (TEAMS): unexpected-seasons" in only_issue(with_seasons)


def test_gaps_must_lie_inside_the_entrys_seasons() -> None:
    found = issues_of(broken(with_entry(3, seasons={"from": 1872}, gaps=[1871])))
    assert found == [
        "entry 4 (GAMES): gap-outside-seasons: gap 1871 is outside the entry's seasons 1872-current"
    ]


def test_a_bounded_entry_rejects_a_gap_after_its_end() -> None:
    data = broken(with_entry(4, gaps=[1870]))
    assert issues_of(data) == [
        "entry 5 (GAMES): gap-outside-seasons: gap 1870 is outside the entry's seasons 1869-1869"
    ]


def test_gaps_need_season_slicing_and_may_not_repeat() -> None:
    on_none = broken(with_entry(0, gaps=[1900]))
    assert "entry 1 (TEAMS): gaps-not-supported" in only_issue(on_none)
    twice = broken(with_entry(3, gaps=[1871, 1871]))
    assert "entry 4 (GAMES): gap-duplicate" in only_issue(twice)


def test_overlapping_seasons_of_equal_fixed_parameters_are_rejected() -> None:
    def overlap(data: dict[str, Any]) -> None:
        data["entries"].append(
            {
                "table": "GAMES",
                "profile": "demo",
                "priority": 4,
                "slicing": "season",
                "seasons": {"from": 2019, "to": 2019},
                "refresh_class": "weekly",
            }
        )

    message = only_issue(broken(overlap))
    assert "entry 6 (GAMES): overlap" in message
    assert "entry 4" in message
    assert "2019" in message


def test_different_fixed_parameters_do_not_overlap() -> None:
    def distinct(data: dict[str, Any]) -> None:
        data["entries"].append(
            {
                "table": "GAMES",
                "profile": "full",
                "priority": 9,
                "slicing": "season",
                "seasons": {"from": 2000},
                "params": {"classification": "fcs"},
                "refresh_class": "weekly",
            }
        )

    assert len(parse(broken(distinct)).entries) == 6


def test_a_range_and_a_season_entry_of_one_table_must_not_share_a_season() -> None:
    message = only_issue(broken(with_entry(2, seasons={"from": 2025})))
    assert "entry 3 (CONFERENCES_AFFILIATIONS): overlap" in message
    assert "2025" in message


def test_two_seasonless_entries_of_one_table_overlap() -> None:
    def twice(data: dict[str, Any]) -> None:
        data["entries"].append(copy.deepcopy(data["entries"][0]))

    message = only_issue(broken(twice))
    assert "entry 6 (TEAMS): overlap" in message


def test_a_seasonless_entry_overlaps_a_season_entry_of_the_same_table() -> None:
    def mixed(data: dict[str, Any]) -> None:
        data["entries"].append(
            {
                "table": "TEAMS",
                "profile": "demo",
                "priority": 2,
                "slicing": "season",
                "seasons": {"from": 2020},
                "refresh_class": "static",
            }
        )

    assert "entry 6 (TEAMS): overlap" in only_issue(broken(mixed))


@pytest.mark.parametrize("priority", [0, -1, "1", 1.5, True, 10000])
def test_priority_is_an_integer_from_one(priority: Any) -> None:
    message = only_issue(broken(with_entry(0, priority=priority)))
    assert "entry 1 (TEAMS): bad-entry-field" in message
    assert "priority" in message


def test_unknown_and_missing_entry_fields_are_rejected() -> None:
    message = only_issue(broken(with_entry(0, colour="red")))
    assert "entry 1 (TEAMS): bad-entry-field" in message
    assert "colour" in message

    def missing(data: dict[str, Any]) -> None:
        del data["entries"][0]["refresh_class"]

    assert "refresh_class" in only_issue(broken(missing))


@pytest.mark.parametrize(
    "steps",
    [
        [{"step_no": 99, "name": "MODEL", "script": "CFB_INFRA.M"}],
        [{"step_no": 1000, "name": "MODEL", "script": "CFB_INFRA.M"}],
        [{"step_no": "100", "name": "MODEL", "script": "CFB_INFRA.M"}],
        [{"step_no": 100, "name": "MODEL", "script": "REFRESH_MODEL"}],
        [{"step_no": 100, "name": "MODEL", "script": "cfb_infra.refresh_model"}],
        [{"step_no": 100, "name": "MODEL", "script": "A.B; DROP"}],
        [{"step_no": 100, "name": "", "script": "CFB_INFRA.M"}],
        [{"step_no": 100, "name": "X" * 65, "script": "CFB_INFRA.M"}],
        [{"step_no": 100, "name": "MODEL"}],
        [{"step_no": 100, "name": "MODEL", "script": "CFB_INFRA.M", "extra": 1}],
        "MODEL",
    ],
)
def test_invalid_refresh_steps_are_rejected(steps: Any) -> None:
    data = valid_data()
    data["refresh_steps"] = steps
    found = issues_of(data)
    assert found
    assert all("refresh-step" in message for message in found)


def test_duplicate_refresh_step_numbers_and_names_are_rejected() -> None:
    data = valid_data()
    data["refresh_steps"] = [
        {"step_no": 100, "name": "MODEL", "script": "CFB_INFRA.A"},
        {"step_no": 100, "name": "OTHER", "script": "CFB_INFRA.B"},
        {"step_no": 200, "name": "MODEL", "script": "CFB_INFRA.C"},
    ]
    found = issues_of(data)
    assert any("step_no 100 is used twice" in message for message in found)
    assert any("name 'MODEL' is used twice" in message for message in found)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("monthly_call_budget", 0),
        ("monthly_call_budget", "1000"),
        ("monthly_call_budget", True),
        ("refresh_reserve", -1),
        ("refresh_reserve", 1001),
        ("quarantine_after", 0),
        ("season_rollover", "8-1"),
        ("season_rollover", "13-01"),
        ("season_rollover", "02-30"),
        ("season_final_after", "2027-02-15"),
        ("refresh_profiles", []),
        ("refresh_profiles", ["demo", "demo"]),
        ("refresh_profiles", ["nope"]),
        ("refresh_profiles", "demo"),
        ("unknown_setting", 1),
    ],
)
def test_invalid_config_values_are_rejected(key: str, value: Any) -> None:
    def change(data: dict[str, Any]) -> None:
        data["config"][key] = value

    message = only_issue(broken(change))
    assert message.startswith("config: config")
    assert key in message


@pytest.mark.parametrize(
    ("section", "value"),
    [
        ("schemas", ["CFB_CORE", "OTHER"]),
        ("schemas", []),
        ("meta_tables", ["LOAD_RUN"]),
        ("meta_tables", "LOAD_LOG"),
        ("extra", 1),
    ],
)
def test_the_snapshot_scope_is_limited_to_what_a_restore_supports(section: str, value: Any) -> None:
    def change(data: dict[str, Any]) -> None:
        data["snapshot"][section] = value

    message = only_issue(broken(change))
    assert message.startswith("snapshot: snapshot")


def test_every_error_is_listed_not_only_the_first() -> None:
    def many(data: dict[str, Any]) -> None:
        data["entries"][0]["table"] = "NO_SUCH_TABLE"
        data["entries"][1]["table"] = "GAMES_SCHEDULE"
        data["entries"][3]["gaps"] = [1850]
        data["entries"][4]["profile"] = "archive"
        data["config"]["refresh_reserve"] = -5

    found = issues_of(broken(many))
    rules = sorted(message.split(": ")[1] for message in found)
    assert rules == [
        "config",
        "gap-outside-seasons",
        "unknown-profile",
        "unknown-table",
        "volatile-table",
    ]


def test_the_error_text_lists_one_line_per_problem() -> None:
    def many(data: dict[str, Any]) -> None:
        data["entries"][0]["table"] = "NO_SUCH_TABLE"
        data["entries"][1]["table"] = "GAMES_SCHEDULE"

    with pytest.raises(PlanError) as raised:
        parse(broken(many))
    lines = str(raised.value).splitlines()
    assert lines[0] == "the load plan has 2 problems:"
    assert len(lines) == 3
    assert all(line.startswith("  - ") for line in lines[1:])


@pytest.mark.parametrize(
    ("text", "needle"),
    [
        ("entries: [", "not valid YAML"),
        ("- a\n- b\n", "mapping"),
        ("", "mapping"),
        ("version: 2\nentries: []\n", "version"),
        ("entries: []\n", "version"),
        ("version: 1\n", "entries"),
        ("version: 1\nentries: {}\n", "entries"),
        ("version: 1\nentries: [1]\n", "entry 1"),
        ("version: 1\nentries: []\nextra: 1\n", "extra"),
    ],
)
def test_structural_problems_are_reported_as_plan_issues(text: str, needle: str) -> None:
    with pytest.raises(PlanError) as raised:
        load_plan.parse_plan(text, catalog())
    assert needle in str(raised.value)


def test_an_empty_entry_list_is_valid() -> None:
    plan = load_plan.parse_plan("version: 1\nentries: []\n", catalog())
    assert plan.entries == ()


# --- the plan hash -----------------------------------------------------------------------------


def test_the_hash_is_a_sha256_of_the_normalized_plan() -> None:
    first = parse(valid_data())
    assert len(first.plan_hash) == 64
    int(first.plan_hash, 16)
    assert parse(valid_data()).plan_hash == first.plan_hash


def test_the_hash_ignores_formatting_comments_key_order_and_notes() -> None:
    data = valid_data()
    reordered = {key: data[key] for key in reversed(list(data))}
    for entry in reordered["entries"]:
        entry.pop("note", None)
    text = "# a comment\n" + yaml.safe_dump(reordered, sort_keys=False, default_flow_style=True)
    assert load_plan.parse_plan(text, catalog()).plan_hash == parse(valid_data()).plan_hash


def test_the_hash_ignores_the_order_of_gap_lists() -> None:
    def gaps(order: list[int]) -> str:
        return parse(broken(with_entry(3, gaps=order))).plan_hash

    assert gaps([1871, 1873]) == gaps([1873, 1871])


def test_the_hash_changes_with_what_decides_the_slices() -> None:
    base = parse(valid_data()).plan_hash
    assert parse(broken(with_entry(3, seasons={"from": 1870, "to": 2025}))).plan_hash != base
    assert parse(broken(with_entry(3, priority=4))).plan_hash != base
    assert parse(broken(with_entry(3, gaps=[1871, 1872]))).plan_hash != base
    assert parse(broken(with_entry(4, params={"classification": "fcs"}))).plan_hash != base

    def budget(data: dict[str, Any]) -> None:
        data["config"]["monthly_call_budget"] = 800

    assert parse(broken(budget)).plan_hash != base


def test_the_hash_depends_on_the_entry_order_because_entry_ids_are_positional() -> None:
    def swap(data: dict[str, Any]) -> None:
        data["entries"][0], data["entries"][1] = data["entries"][1], data["entries"][0]

    assert parse(broken(swap)).plan_hash != parse(valid_data()).plan_hash


def test_the_hash_matches_its_documented_definition() -> None:
    plan = parse(valid_data())
    assert plan.plan_hash == hashlib.sha256(load_plan.normalized_json(plan).encode()).hexdigest()
    document = json.loads(load_plan.normalized_json(plan))
    assert set(document) == {"version", "config", "snapshot", "refresh_steps", "entries"}
    assert all("note" not in entry and "entry_id" not in entry for entry in document["entries"])


# --- LOADER_CONFIG overrides -------------------------------------------------------------------


def test_overrides_parse_key_value_pairs() -> None:
    assert load_plan.parse_config_overrides("monthly_call_budget=400,refresh_reserve=50") == {
        "monthly_call_budget": "400",
        "refresh_reserve": "50",
    }
    assert load_plan.parse_config_overrides("") == {}
    assert load_plan.parse_config_overrides(" Refresh_Reserve = 5 ") == {"refresh_reserve": "5"}


@pytest.mark.parametrize("text", ["monthly_call_budget", "=4", "a=1,,b=2", "a=1,a=2"])
def test_malformed_overrides_are_rejected(text: str) -> None:
    with pytest.raises(PlanError, match="LOADER_CONFIG"):
        load_plan.parse_config_overrides(text)


def test_overrides_replace_settings_and_are_validated_like_the_yaml() -> None:
    config = PlanConfig()
    changed = config.with_overrides({"monthly_call_budget": "400", "refresh_reserve": "50"})
    assert (changed.monthly_call_budget, changed.refresh_reserve) == (400, 50)
    assert config.monthly_call_budget == 1000
    assert changed.quarantine_after == config.quarantine_after


def test_override_profiles_use_plus_or_semicolon() -> None:
    assert PlanConfig().with_overrides({"refresh_profiles": "demo+full"}).refresh_profiles == (
        "demo",
        "full",
    )
    assert PlanConfig().with_overrides({"refresh_profiles": "full;demo"}).refresh_profiles == (
        "full",
        "demo",
    )


@pytest.mark.parametrize(
    ("overrides", "needle"),
    [
        ({"monthly_call_budget": "abc"}, "monthly_call_budget"),
        ({"monthly_call_budget": "0"}, "monthly_call_budget"),
        ({"refresh_reserve": "5000"}, "refresh_reserve"),
        ({"season_rollover": "13-40"}, "season_rollover"),
        ({"refresh_profiles": "nope"}, "refresh_profiles"),
        ({"now_override": "yesterday"}, "now_override"),
        ({"bogus": "1"}, "bogus"),
    ],
)
def test_invalid_overrides_are_rejected(overrides: dict[str, str], needle: str) -> None:
    with pytest.raises(PlanError) as raised:
        PlanConfig().with_overrides(overrides)
    assert needle in str(raised.value)


def test_an_override_may_set_the_test_clock_but_the_yaml_may_not() -> None:
    config = PlanConfig().with_overrides({"now_override": "2020-10-20"})
    assert config.now_override == "2020-10-20"
    assert config.settings()["NOW_OVERRIDE"] == "2020-10-20"
    assert "NOW_OVERRIDE" not in PlanConfig().settings()
    assert PlanConfig().with_overrides({"now_override": "2020-10-20 06:30:00"}).now_override == (
        "2020-10-20 06:30:00"
    )

    def clock(data: dict[str, Any]) -> None:
        data["config"]["now_override"] = "2020-10-20"

    assert "now_override" in only_issue(broken(clock))


def test_the_budget_and_reserve_are_checked_together_after_overrides() -> None:
    with pytest.raises(PlanError, match="refresh_reserve"):
        PlanConfig().with_overrides({"monthly_call_budget": "100"})


# --- files -------------------------------------------------------------------------------------


def test_load_plan_reads_a_file(tmp_path: Path) -> None:
    path = tmp_path / "plan.yaml"
    path.write_text(yaml.safe_dump(valid_data(), sort_keys=False), encoding="utf-8")
    assert len(load_plan.load_plan(path, catalog()).entries) == 5


def test_load_plan_reports_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(PlanError, match="not found"):
        load_plan.load_plan(tmp_path / "missing.yaml", catalog())


def test_load_plan_defaults_to_the_project_plan_and_catalog() -> None:
    assert load_plan.DEFAULT_PLAN_PATH.name == "load-plan.yaml"
    assert load_plan.DEFAULT_PLAN_PATH.parent.name == "loader"
