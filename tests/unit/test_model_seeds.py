import itertools
import json
import re
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.synthetic.seed_sql import (
    MODEL_DIR,
    parse_values,
    power_conferences,
    seed_columns,
    seed_rows,
)
from vs.host.sqlobjects import MODEL_ALLOWED_REFS, lint, load, load_reserved_keywords

ROOT = Path(__file__).resolve().parents[2]
CODES = ("fbs", "fcs", "ii", "iii")
SEED_FILES = (
    "010_era.sql",
    "011_classification_label.sql",
    "012_conference_tier.sql",
    "013_conference_championship.sql",
    "014_poll.sql",
    "015_dataset.sql",
    "016_classification_transition.sql",
)


def table(file: str) -> list[dict[str, Any]]:
    names = seed_columns(file)
    return [dict(zip(names, row, strict=True)) for row in seed_rows(file)]


def assert_contiguous(ranges: list[tuple[int, int | None]]) -> None:
    ordered = sorted(ranges, key=lambda item: item[0])
    for (_, last), (first, _) in itertools.pairwise(ordered):
        assert last is not None, "an open range must be the last one"
        assert first == last + 1, f"{last} is not followed by {first}"


def assert_disjoint(ranges: list[tuple[int, int | None]]) -> None:
    ordered = sorted(ranges, key=lambda item: item[0])
    for (_, last), (first, _) in itertools.pairwise(ordered):
        assert last is not None, "an open range must be the last one"
        assert first > last, f"ranges overlap at {first}"


def by_group(rows: list[dict[str, Any]], key: str) -> dict[Any, list[tuple[int, int | None]]]:
    groups: dict[Any, list[tuple[int, int | None]]] = defaultdict(list)
    for row in rows:
        groups[row[key]].append((row["FIRST_SEASON"], row["LAST_SEASON"]))
    return groups


# --- the files ---------------------------------------------------------------------------------


def test_the_01x_files_are_exactly_the_seven_seeds() -> None:
    seeds = [path.name for path in MODEL_DIR.glob("01*.sql")]
    assert sorted(seeds) == sorted(SEED_FILES)


def test_the_seeds_pass_the_deploy_lint() -> None:
    objects = [o for o in load(MODEL_DIR) if o.file in SEED_FILES]
    problems = lint(objects, allowed_refs=MODEL_ALLOWED_REFS, reserved=load_reserved_keywords())
    assert problems == []


@pytest.mark.parametrize("file", SEED_FILES)
def test_every_row_has_one_value_per_column_and_every_seed_is_a_table(file: str) -> None:
    columns = seed_columns(file)
    assert all(len(row) == len(columns) for row in seed_rows(file))


def test_parse_values_reads_strings_numbers_nulls_and_booleans() -> None:
    assert parse_values("(1, 'it''s', NULL, TRUE),\n (2.50, 'a, b', FALSE, -3)") == [
        (1, "it's", None, True),
        (Decimal("2.50"), "a, b", False, -3),
    ]


# --- ERA ---------------------------------------------------------------------------------------


def test_era_has_four_kinds_and_nineteen_rows() -> None:
    rows = table("010_era.sql")
    assert len(rows) == 19
    assert {row["ERA_KIND"] for row in rows} == {"DIVISION", "POSTSEASON", "CHAMPION", "OVERTIME"}


@pytest.mark.parametrize("kind", ["DIVISION", "POSTSEASON", "CHAMPION", "OVERTIME"])
def test_the_eras_of_each_kind_are_contiguous_and_the_last_is_open(kind: str) -> None:
    ranges = [
        (row["FIRST_SEASON"], row["LAST_SEASON"])
        for row in table("010_era.sql")
        if row["ERA_KIND"] == kind
    ]
    assert_contiguous(ranges)
    assert min(first for first, _ in ranges) == 1869
    assert max(ranges)[1] is None


def test_era_seasons_match_the_spec() -> None:
    ranges = {
        (row["ERA_KIND"], row["ERA_CODE"]): (row["FIRST_SEASON"], row["LAST_SEASON"])
        for row in table("010_era.sql")
    }
    assert ranges[("DIVISION", "I_A_I_AA")] == (1978, 2005)
    assert ranges[("DIVISION", "FBS_FCS")] == (2006, None)
    assert ranges[("POSTSEASON", "BCS")] == (1998, 2013)
    assert ranges[("POSTSEASON", "CFP_12")] == (2024, None)
    assert ranges[("CHAMPION", "POLLS")] == (1936, 1997)
    assert ranges[("OVERTIME", "TIES")] == (1869, 1995)


def test_cfp_teams_and_ties_follow_the_spec() -> None:
    rows = {(r["ERA_KIND"], r["ERA_CODE"]): r for r in table("010_era.sql")}
    teams = {code: r["CFP_TEAMS"] for (kind, code), r in rows.items() if kind == "POSTSEASON"}
    assert teams == {
        "NO_BOWLS": 0,
        "BOWLS": 0,
        "BOWL_COALITION": 0,
        "BOWL_ALLIANCE": 0,
        "BCS": 0,
        "CFP_4": 4,
        "CFP_12": 12,
    }
    assert rows[("OVERTIME", "TIES")]["TIES_POSSIBLE"] is True
    assert rows[("OVERTIME", "OVERTIME")]["TIES_POSSIBLE"] is False
    assert all(r["CFP_TEAMS"] is None for (k, _), r in rows.items() if k != "POSTSEASON")
    assert all(r["TIES_POSSIBLE"] is None for (k, _), r in rows.items() if k != "OVERTIME")


# --- CLASSIFICATION_LABEL ----------------------------------------------------------------------


def test_every_division_era_and_code_has_a_label() -> None:
    rows = table("011_classification_label.sql")
    eras = {r["ERA_CODE"] for r in table("010_era.sql") if r["ERA_KIND"] == "DIVISION"}
    assert {(r["DIVISION_ERA"], r["CLASSIFICATION"]) for r in rows} == {
        (era, code) for era in eras for code in CODES
    }
    assert len(rows) == 24


def test_labels_follow_the_spec_table() -> None:
    labels = {
        (r["DIVISION_ERA"], r["CLASSIFICATION"]): r["LABEL"]
        for r in table("011_classification_label.sql")
    }
    assert [labels[("FBS_FCS", code)] for code in CODES] == [
        "FBS",
        "FCS",
        "Division II",
        "Division III",
    ]
    assert [labels[("I_A_I_AA", code)] for code in CODES] == [
        "Division I-A",
        "Division I-AA",
        "Division II",
        "Division III",
    ]
    assert (
        labels[("DIVISION_I_II_III", "fbs")] == labels[("DIVISION_I_II_III", "fcs")] == "Division I"
    )
    assert labels[("UNIVERSITY_COLLEGE", "fbs")] == "University Division"
    assert {labels[("UNIVERSITY_COLLEGE", code)] for code in CODES[1:]} == {"College Division"}
    for era in ("NCAA_UNDIVIDED", "PRE_NCAA"):
        assert labels[(era, "fbs")] == "Major college"
        assert {labels[(era, code)] for code in CODES[1:]} == {"Small college"}


def test_the_retroactive_flag_marks_codes_that_did_not_exist_yet() -> None:
    last = {
        r["ERA_CODE"]: r["LAST_SEASON"] for r in table("010_era.sql") if r["ERA_KIND"] == "DIVISION"
    }
    for row in table("011_classification_label.sql"):
        cutoff = 1977 if row["CLASSIFICATION"] in ("fbs", "fcs") else 1972
        era_last = last[row["DIVISION_ERA"]]
        assert row["IS_RETROACTIVE"] is (era_last is not None and era_last <= cutoff), row


# --- CONFERENCE_TIER ---------------------------------------------------------------------------


def test_tier_ranges_of_a_conference_do_not_overlap() -> None:
    for name, ranges in by_group(table("012_conference_tier.sql"), "CONFERENCE_NAME").items():
        assert_disjoint(ranges)
        assert min(first for first, _ in ranges) >= 1998, name


def test_the_default_tiers_cover_every_season_from_1998() -> None:
    ranges = by_group(table("012_conference_tier.sql"), "CONFERENCE_NAME")[None]
    assert_contiguous(ranges)
    assert min(first for first, _ in ranges) == 1998
    tiers = {r["TIER"] for r in table("012_conference_tier.sql") if r["CONFERENCE_NAME"] is None}
    assert tiers == {"NON_AQ", "G5", "G6"}


@pytest.mark.parametrize(
    ("season", "expected"),
    [
        (1997, set()),
        (2000, {"ACC", "Big East", "Big Ten", "Big 12", "Pac-10", "SEC"}),
        (2011, {"ACC", "Big East", "Big Ten", "Big 12", "Pac-12", "SEC"}),
        (2013, {"ACC", "American Athletic", "Big Ten", "Big 12", "Pac-12", "SEC"}),
        (2014, {"ACC", "Big Ten", "Big 12", "Pac-12", "SEC"}),
        (2023, {"ACC", "Big Ten", "Big 12", "Pac-12", "SEC"}),
        (2024, {"ACC", "Big Ten", "Big 12", "SEC"}),
        (2026, {"ACC", "Big Ten", "Big 12", "SEC"}),
    ],
)
def test_the_power_conferences_follow_the_spec(season: int, expected: set[str]) -> None:
    assert power_conferences(season) == expected


def test_only_power_tiers_are_flagged_as_power_conferences() -> None:
    for row in table("012_conference_tier.sql"):
        assert row["IS_POWER_CONFERENCE"] is (row["TIER"] in ("AQ", "P5", "P4")), row
    independents = [
        r for r in table("012_conference_tier.sql") if r["CONFERENCE_NAME"] == "FBS Independents"
    ]
    assert [r["TIER"] for r in independents] == ["INDEPENDENT"]


# --- CONFERENCE_CHAMPIONSHIP -------------------------------------------------------------------


def test_championship_seasons_have_thirteen_rows_that_start_in_1992_or_later() -> None:
    rows = table("013_conference_championship.sql")
    assert len(rows) == 13
    assert all(row["FIRST_SEASON"] >= 1992 for row in rows)
    for ranges in by_group(rows, "CONFERENCE_NAME").values():
        assert_disjoint(ranges)


def test_championship_seasons_match_the_review() -> None:
    ranges = by_group(table("013_conference_championship.sql"), "CONFERENCE_NAME")
    assert ranges["SEC"] == [(1992, None)]
    assert sorted(ranges["Big 12"]) == [(1996, 2010), (2017, None)]
    assert sorted(ranges["Pac-12"], key=lambda r: r[0]) == [(2011, 2023), (2026, None)]
    assert ranges["Western Athletic"] == [(1996, 1998)]
    assert ranges["Sun Belt"] == [(2018, None)]
    assert set(ranges) == {
        "SEC",
        "Western Athletic",
        "Big 12",
        "Mid-American",
        "ACC",
        "Conference USA",
        "Big Ten",
        "Pac-12",
        "Mountain West",
        "American Athletic",
        "Sun Belt",
    }


# --- POLL --------------------------------------------------------------------------------------


def test_polls_follow_the_spec_table() -> None:
    rows = {r["POLL_NAME"]: r for r in table("014_poll.sql")}
    assert len(rows) == 6
    assert (rows["AP Top 25"]["POLL_CODE"], rows["AP Top 25"]["DATA_FROM_SEASON"]) == ("AP", 1936)
    assert rows["Coaches Poll"]["DATA_FROM_SEASON"] == 2001
    assert rows["Playoff Committee Rankings"]["DATA_FROM_SEASON"] == 2014
    assert {r["LABEL_LEAD_WEEKS"] for n, r in rows.items() if "Committee" not in n} == {1}
    assert rows["Playoff Committee Rankings"]["LABEL_LEAD_WEEKS"] == 0
    final = [name for name, r in rows.items() if r["FINAL_BEFORE_POSTSEASON"]]
    assert final == ["Playoff Committee Rankings"]
    assert {r["POLL_CODE"] for r in rows.values()} == {
        "AP",
        "COACHES",
        "CFP",
        "FCS_COACHES",
        "D2_COACHES",
        "D3_COACHES",
    }


# --- CLASSIFICATION_TRANSITION -----------------------------------------------------------------


def transition_problems(rows: list[dict[str, Any]]) -> list[str]:
    problems = [f"code {r['CLASSIFICATION']}" for r in rows if r["CLASSIFICATION"] not in CODES]
    grouped = by_group(rows, "TEAM_NAME")
    for team, ranges in grouped.items():
        try:
            assert_disjoint(ranges)
        except AssertionError:
            problems.append(f"overlap {team}")
    problems += [f"before 1978 {r['TEAM_NAME']}" for r in rows if r["FIRST_SEASON"] < 1978]
    return problems


def test_the_classification_transition_seed_is_empty_and_valid() -> None:
    assert seed_rows("016_classification_transition.sql") == []
    assert transition_problems(table("016_classification_transition.sql")) == []


def test_the_transition_validator_catches_bad_rows() -> None:
    rows = [
        {"TEAM_NAME": "A", "FIRST_SEASON": 2000, "LAST_SEASON": 2005, "CLASSIFICATION": "fbs"},
        {"TEAM_NAME": "A", "FIRST_SEASON": 2005, "LAST_SEASON": 2006, "CLASSIFICATION": "fcs"},
        {"TEAM_NAME": "B", "FIRST_SEASON": 1970, "LAST_SEASON": 1975, "CLASSIFICATION": "i-aa"},
    ]
    assert transition_problems(rows) == ["code i-aa", "overlap A", "before 1978 B"]


# --- DATASET -----------------------------------------------------------------------------------

AVAILABILITY_ROWS = {
    "GAMES": "Games, scores, records, matchups",
    "SCORES": "Games, scores, records, matchups",
    "MATCHUP": "Games, scores, records, matchups",
    "CALENDAR": "Season calendar",
    "DRIVES": "Drives",
    "PLAYS": "Raw plays",
    "TEAM_BOX_SCORES": "Team and player box-score stats",
    "PLAYER_BOX_SCORES": "Team and player box-score stats",
    "PREGAME_WP": "Pregame win probability",
    "ELO": "Elo",
    "SRS": "SRS",
    "SP_PLUS": "SP+ (team and conference)",
    "FPI": "FPI",
    "CONFERENCE_MEMBERSHIP": "Team/conference affiliations",
    "COACHES": "Coaches, seasons, tenures",
    "ROSTERS": "Rosters",
    "TRANSFER_PORTAL": "Transfer portal",
    "POLLS": "AP Top 25",
    "RECRUITING": "HS recruits + team rankings",
    "DRAFT": "NFL draft picks",
    "BETTING_LINES": "Betting lines, ATS",
    "WEATHER": "Weather",
}
DATASETS_WITHOUT_AVAILABILITY_ROW = {"TEAMS", "VENUES", "GAME_PREVIEW"}


def availability() -> dict[str, tuple[int, set[int]]]:
    """Category -> (first season, gap seasons) from the transcribed availability table."""
    result: dict[str, tuple[int, set[int]]] = {}
    for line in (ROOT / "docs" / "cfbd-data-availability.md").read_text().splitlines():
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) != 3 or not re.match(r"\d{4}", cells[1]):
            continue
        first = int(cells[1][:4])
        gaps = {int(y) for y in re.findall(r"\b\d{4}\b", cells[2])} if "No " in cells[2] else set()
        result[cells[0]] = (first, gaps)
    return result


def test_dataset_lists_25_datasets_in_three_access_classes() -> None:
    rows = table("015_dataset.sql")
    assert len(rows) == 25
    assert len({r["DATASET_CODE"] for r in rows}) == 25
    classes = {
        c: [r["DATASET_CODE"] for r in rows if r["ACCESS_CLASS"] == c]
        for c in ("STORED", "ON_DEMAND", "UNAVAILABLE")
    }
    assert sorted(classes["STORED"]) == sorted(
        [
            "GAMES",
            "SCORES",
            "ELO",
            "PREGAME_WP",
            "SP_PLUS",
            "POLLS",
            "CALENDAR",
            "CONFERENCE_MEMBERSHIP",
            "TEAMS",
            "VENUES",
        ]
    )
    assert len(classes["ON_DEMAND"]) == 14
    assert classes["UNAVAILABLE"] == ["WEATHER"]


def test_declared_seasons_and_gaps_equal_the_availability_page() -> None:
    page = availability()
    rows = {r["DATASET_CODE"]: r for r in table("015_dataset.sql")}
    for code, label in AVAILABILITY_ROWS.items():
        first, gaps = page[label]
        row = rows[code]
        assert row["FIRST_SEASON"] == first, code
        declared_gaps = {int(g) for g in (row["GAP_SEASONS"] or "").split(",") if g}
        assert declared_gaps == gaps, code
    for code in DATASETS_WITHOUT_AVAILABILITY_ROW:
        assert rows[code]["FIRST_SEASON"] is None
    spec = {"GAMES": 1869, "ELO": 1890, "PREGAME_WP": 2013, "SP_PLUS": 1970, "PLAYS": 2001}
    assert {code: rows[code]["FIRST_SEASON"] for code in spec} == spec


def test_complete_share_is_a_fraction() -> None:
    for row in table("015_dataset.sql"):
        assert Decimal(0) <= row["COMPLETE_SHARE"] <= Decimal(1)
        assert row["COMPLETE_SHARE"] == Decimal("0.95")


def test_every_virtual_table_and_endpoint_exists_in_the_catalog() -> None:
    catalog = {
        t["name"]: t for t in json.loads((ROOT / "vs/catalog/catalog.json").read_text())["tables"]
    }
    for row in table("015_dataset.sql"):
        assert row["VS_TABLE"] in catalog, row["DATASET_CODE"]
        assert catalog[row["VS_TABLE"]]["endpoint"] == row["CFBD_ENDPOINT"], row["DATASET_CODE"]


def test_every_core_table_exists_as_a_mirror() -> None:
    mirrors = set(
        re.findall(
            r"CREATE TABLE CFB_CORE\.(\w+) \(", (ROOT / "sql/core/cfb_core_mirrors.sql").read_text()
        )
    )
    for row in table("015_dataset.sql"):
        if row["CORE_TABLE"] is not None:
            assert row["CORE_TABLE"] in mirrors, row["DATASET_CODE"]


def test_only_stored_datasets_name_a_core_table_and_a_model_object() -> None:
    for row in table("015_dataset.sql"):
        stored = row["ACCESS_CLASS"] == "STORED"
        assert (row["CORE_TABLE"] is not None) is stored, row["DATASET_CODE"]
        assert (row["MODEL_OBJECT"] is not None) is stored, row["DATASET_CODE"]


def test_stored_datasets_are_the_demo_entries_of_the_load_plan() -> None:
    plan = yaml.safe_load((ROOT / "loader/load-plan.yaml").read_text())
    demo = {e["table"] for e in plan["entries"] if e["profile"] == "demo"}
    stored = {r["CORE_TABLE"] for r in table("015_dataset.sql") if r["ACCESS_CLASS"] == "STORED"}
    assert demo - stored == {"CONFERENCES"}
    assert stored <= demo


def test_the_measured_populations_follow_the_coverage_spec() -> None:
    rows = {r["DATASET_CODE"]: r["COVERAGE_POPULATION"] for r in table("015_dataset.sql")}
    assert rows["SCORES"] == rows["ELO"] == "COMPLETED_FBS_GAMES"
    assert rows["PREGAME_WP"] == "COMPLETED_FBS_VS_FBS_GAMES"
    assert rows["SP_PLUS"] == rows["CONFERENCE_MEMBERSHIP"] == "FBS_TEAM_SEASONS"
