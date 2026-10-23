"""Integration tests of model/ratings-rankings: `RATING_SP` and `RANKING`.

Read-only module over the synthetic league (marker `model_readonly`). A test that stores another
fetch of a row removes it again before it ends (`tests/it/mirror_edit.py`). The expectations are
the league's own poll bodies and the spec's rules restated in Python (`expected_visible_at`,
`expected_final`), never the SQL under test. The as-of parts (`RANKING_AS_OF`, the fetch-time
horizon) belong to `330_ranking_as_of.sql`, the SP+ checks (`SP_TEAM_UNMATCHED`,
`DUPLICATE_SOURCE_ROW`, `SP_PRIOR_NOT_FINAL`) to `500_model_checks.sql`. The `TEAM_SEASON` parts
(SP+ of the season, the prior, final ranks) compare with `RATING_SP` and `RANKING`, whose own tests
are above. The stored-twice scenario for SP+ and the not-final prior slice are in
`tests/it/test_model_variants_it.py`.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from decimal import Decimal
from typing import Any

import pytest

from tests.it import mirror_edit
from tests.it.harness import Harness
from tests.synthetic import league_teams as lt
from tests.synthetic.league import (
    AP,
    COMMITTEE,
    CURRENT_SEASON,
    FETCHED_2026_RANKINGS,
    LAST_FINAL_SEASON,
    LEAD_WEEKS,
    MYSTERY,
    RANKING_SEASONS,
    SP_NATIONAL_AVERAGES,
    SP_SEASONS,
    SP_UNMATCHED,
    League,
    fetched_at,
    is_final,
    season_key,
)

PLAN_VARIANT = "model"
pytestmark = [pytest.mark.it, pytest.mark.model_readonly]

LATER_FETCH = "2030-01-01 00:00:00"
MARKER = "marker-ranking-it"
ASHFORD = lt.team("Ashford").id
LAST_REGULAR_WEEK = 15
POSTSEASON_OFFSET = 100
DONE_SEASONS = (2024, 2025)
FINAL_BEFORE_POSTSEASON = {COMMITTEE}

RankKey = tuple[int, str, int, str, int]


def moment(value: Any) -> datetime | None:
    return None if value is None else datetime.fromisoformat(str(value))


def week_sequence(season_type: str, week: int) -> int:
    return week if season_type == "regular" else POSTSEASON_OFFSET + week


def stored_ranks(league: League) -> dict[RankKey, dict[str, Any]]:
    """Every rank of the league's poll bodies, keyed like `RANKING`'s grain."""
    result: dict[RankKey, dict[str, Any]] = {}
    for season in RANKING_SEASONS:
        slice_ = league.get("RANKINGS_POLLS_RANKS", season_key("RANKINGS_POLLS_RANKS", season))
        for entry in slice_.body:
            for poll in entry["polls"]:
                for rank in poll["ranks"]:
                    key = (season, entry["seasonType"], entry["week"], poll["poll"], rank["teamId"])
                    assert key not in result
                    result[key] = {**rank, "fetched_at": slice_.fetched_at}
    return result


def last_ranking_sequence(league: League, season: int, poll: str) -> int:
    return max(
        week_sequence(season_type, week)
        for (s, season_type, week, p, _) in stored_ranks(league)
        if (s, p) == (season, poll)
    )


def expected_visible_at(poll: str, season_type: str, week: int, *, known: bool = True) -> int:
    """The label rule of the spec: the label's sequence minus the lead; postseason polls at their
    own sequence, except the committee's, which is visible at the last regular week."""
    sequence = week_sequence(season_type, week)
    lead = LEAD_WEEKS.get(poll, 0) if known else 0
    if season_type == "postseason":
        return LAST_REGULAR_WEEK if poll in FINAL_BEFORE_POSTSEASON else sequence
    return sequence - lead


def expected_final(league: League, key: RankKey) -> bool:
    """`IS_FINAL_POLL`: postseason polls released after the postseason; the committee's last
    ranking of a season whose regular weeks are all done."""
    season, season_type, week, poll, _ = key
    if poll in FINAL_BEFORE_POSTSEASON:
        return season in DONE_SEASONS and week_sequence(season_type, week) == last_ranking_sequence(
            league, season, poll
        )
    return season_type == "postseason"


def ranking_rows(harness: Harness, where: str = "1 = 1") -> list[dict[str, Any]]:
    return harness.query_dicts(
        f"SELECT * FROM {harness.model}.RANKING WHERE {where} "
        "ORDER BY SEASON, WEEK_SEQ, POLL_NAME, POLL_RANK"
    )


def poll_week(
    harness: Harness, season: int, poll_name: str, season_type: str, week: int
) -> list[dict[str, Any]]:
    return ranking_rows(
        harness,
        f"SEASON = {season} AND POLL_NAME = '{poll_name}' "
        f"AND SEASON_TYPE = '{season_type}' AND WEEK = {week}",
    )


# --- grain and values --------------------------------------------------------------------------


def test_every_stored_rank_is_one_row_with_its_values(harness: Harness, league: League) -> None:
    expected = stored_ranks(league)
    rows = ranking_rows(harness)
    assert len(rows) == len(expected)
    seen: set[RankKey] = set()
    for row in rows:
        key = (row["SEASON"], row["SEASON_TYPE"], row["WEEK"], row["POLL_NAME"], row["TEAM_ID"])
        assert key not in seen
        seen.add(key)
        rank = expected[key]
        assert row["POLL_RANK"] == rank["rank"], key
        assert row["SCHOOL"] == rank["school"], key
        assert row["CONFERENCE"] == rank["conference"], key
        assert row["FIRST_PLACE_VOTES"] == rank["firstPlaceVotes"], key
        assert row["POINTS"] == rank["points"], key
        assert moment(row["FETCHED_AT"]) == rank["fetched_at"], key
        assert row["WEEK_SEQ"] == week_sequence(key[1], key[2]), key
    assert seen == set(expected)


def test_fetch_times_are_the_slices(harness: Harness, league: League) -> None:
    for season in RANKING_SEASONS:
        distinct = harness.query(
            f"SELECT DISTINCT FETCHED_AT FROM {harness.model}.RANKING WHERE SEASON = {season}"
        )
        expected = FETCHED_2026_RANKINGS if season == CURRENT_SEASON else fetched_at(season)
        assert [moment(row[0]) for row in distinct] == [expected], season


# --- poll codes and the week order -------------------------------------------------------------


def test_polls_are_coded_and_placed_in_the_week_order(harness: Harness, league: League) -> None:
    """Scenario ratings-rankings: Polls are coded and placed in the week order."""
    ap10 = poll_week(harness, 2024, AP, "regular", 10)
    assert ap10 and {row["POLL_CODE"] for row in ap10} == {"AP"}
    assert {(row["WEEK_SEQ"], row["VISIBLE_AT_WEEK_SEQ"]) for row in ap10} == {(10, 9)}
    cfp10 = poll_week(harness, 2024, COMMITTEE, "regular", 10)
    assert cfp10 and {row["POLL_CODE"] for row in cfp10} == {"CFP"}
    assert {(row["WEEK_SEQ"], row["VISIBLE_AT_WEEK_SEQ"]) for row in cfp10} == {(10, 10)}
    ap_post = poll_week(harness, 2024, AP, "postseason", 1)
    assert {
        (row["WEEK_SEQ"], row["VISIBLE_AT_WEEK_SEQ"], row["IS_FINAL_POLL"]) for row in ap_post
    } == {(101, 101, True)}
    week1 = poll_week(harness, 2024, AP, "regular", 1)
    assert week1 and {row["WEEK_SEQ"] for row in week1} == {1}
    assert {row["VISIBLE_AT_WEEK_SEQ"] for row in week1} == {0}
    assert {row["IS_FINAL_POLL"] for row in week1} == {False}
    assert len(week1) == len(poll_week(harness, 2024, AP, "postseason", 1))


def test_poll_codes_come_from_the_poll_table(harness: Harness, league: League) -> None:
    codes = {AP: "AP", COMMITTEE: "CFP"}
    rows = ranking_rows(harness, f"POLL_NAME IN ('{AP}', '{COMMITTEE}')")
    assert {row["POLL_NAME"] for row in rows} == set(codes)
    assert all(row["POLL_CODE"] == codes[row["POLL_NAME"]] for row in rows)


def test_visibility_and_final_flags_follow_the_rules_for_every_row(
    harness: Harness, league: League
) -> None:
    for row in ranking_rows(harness, f"POLL_NAME <> '{MYSTERY}'"):
        key = (row["SEASON"], row["SEASON_TYPE"], row["WEEK"], row["POLL_NAME"], row["TEAM_ID"])
        assert row["VISIBLE_AT_WEEK_SEQ"] == expected_visible_at(key[3], key[1], key[2]), key
        assert row["IS_FINAL_POLL"] is expected_final(league, key), key


# --- unknown polls -----------------------------------------------------------------------------


def test_an_unknown_poll_is_kept_and_visible_at_its_own_week(
    harness: Harness, league: League
) -> None:
    """Scenario ratings-rankings: An unknown poll is kept and flagged (the `RANKING` part)."""
    rows = ranking_rows(harness, f"POLL_NAME = '{MYSTERY}'")
    assert len(rows) == 3
    assert {row["POLL_CODE"] for row in rows} == {"OTHER"}
    assert {(row["SEASON"], row["WEEK_SEQ"], row["VISIBLE_AT_WEEK_SEQ"]) for row in rows} == {
        (2024, 5, 5)
    }
    assert {row["IS_FINAL_POLL"] for row in rows} == {False}


# --- the committee's selection ranking, labeled postseason ------------------------------------


def test_committee_ranking_labeled_postseason_is_visible_after_the_regular_season(
    harness: Harness, league: League
) -> None:
    """Scenario ratings-rankings: A committee ranking labeled postseason is visible after the
    regular season."""
    selection = poll_week(harness, 2025, COMMITTEE, "postseason", 1)
    assert len(selection) == 8
    assert {
        (row["WEEK_SEQ"], row["VISIBLE_AT_WEEK_SEQ"], row["IS_FINAL_POLL"]) for row in selection
    } == {(101, 15, True)}
    others = ranking_rows(
        harness, f"SEASON = 2025 AND POLL_NAME = '{COMMITTEE}' AND SEASON_TYPE = 'regular'"
    )
    assert {row["WEEK"] for row in others} == {10, 11, 12, 13, 14}
    assert {row["IS_FINAL_POLL"] for row in others} == {False}
    assert {row["VISIBLE_AT_WEEK_SEQ"] for row in others} == {10, 11, 12, 13, 14}


def test_the_committees_last_regular_ranking_of_a_done_season_is_final(
    harness: Harness, league: League
) -> None:
    """Scenario ratings-rankings: Final ranks belong to the team-season (the `RANKING` part)."""
    last = poll_week(harness, 2024, COMMITTEE, "regular", 15)
    assert {row["IS_FINAL_POLL"] for row in last} == {True}
    first = [row for row in last if row["POLL_RANK"] == 1]
    assert [row["TEAM_ID"] for row in first] == [ASHFORD]
    earlier = ranking_rows(
        harness,
        f"SEASON = 2024 AND POLL_NAME = '{COMMITTEE}' AND WEEK < {LAST_REGULAR_WEEK}",
    )
    assert earlier and {row["IS_FINAL_POLL"] for row in earlier} == {False}
    final_ap = [
        row for row in poll_week(harness, 2024, AP, "postseason", 1) if row["POLL_RANK"] == 5
    ]
    assert [row["TEAM_ID"] for row in final_ap] == [ASHFORD]


def test_a_season_in_progress_has_no_final_committee_ranking(
    harness: Harness, league: League
) -> None:
    rows = ranking_rows(harness, f"SEASON = {CURRENT_SEASON} AND POLL_NAME = '{COMMITTEE}'")
    assert rows and {row["WEEK"] for row in rows} == {10}
    assert {row["IS_FINAL_POLL"] for row in rows} == {False}
    assert harness.query(
        f"SELECT COUNT(*) FROM {harness.model}.RANKING "
        f"WHERE SEASON = {CURRENT_SEASON} AND IS_FINAL_POLL"
    ) == [(0,)]


# --- a rank stored twice appears once ------------------------------------------------------------


@pytest.fixture
def stored_twice(harness: Harness, league: League) -> Iterator[None]:
    yield
    mirror_edit.remove_copies(harness, "RANKINGS_POLLS_RANKS", MARKER)


def ranks_of(harness: Harness, where: str) -> dict[int, tuple[int, str]]:
    return {
        row["TEAM_ID"]: (row["POLL_RANK"], str(row["FETCHED_AT"]))
        for row in ranking_rows(harness, where)
    }


def test_the_newest_fetch_of_a_rank_wins(
    harness: Harness, league: League, stored_twice: None
) -> None:
    """Scenario ratings-rankings: A rating or a ranking stored twice appears once (the
    `RANKING` part, as copies of the stored week-9 AP poll)."""
    where = (
        f"SEASON = {CURRENT_SEASON} AND POLL_NAME = '{AP}' AND SEASON_TYPE = 'regular' AND WEEK = 9"
    )
    before = ranks_of(harness, where)
    stored = mirror_edit.store_changed_copy(
        harness,
        "RANKINGS_POLLS_RANKS",
        where.replace("POLL_NAME", "POLL"),
        marker=MARKER,
        fetched_at=LATER_FETCH,
        row_offset=1000,
        changes={"POINTS": "POINTS + 1", "RANK": "RANK"},
    )
    assert stored == len(before) > 0
    after = ranking_rows(harness, where)
    assert len(after) == len(before)
    assert {row["TEAM_ID"] for row in after} == set(before)
    assert {moment(row["FETCHED_AT"]) for row in after} == {datetime(2030, 1, 1)}
    assert [row["POINTS"] for row in after] == [1500 - 50 * row["POLL_RANK"] + 1 for row in after]
    assert [row["POLL_RANK"] for row in after] == sorted(row["POLL_RANK"] for row in after)


def test_an_older_fetch_never_replaces_a_newer_one(
    harness: Harness, league: League, stored_twice: None
) -> None:
    where = "SEASON = 2024 AND POLL = 'AP Top 25' AND SEASON_TYPE = 'regular' AND WEEK = 3"
    mirror_edit.store_changed_copy(
        harness,
        "RANKINGS_POLLS_RANKS",
        where,
        marker=MARKER,
        fetched_at="2000-01-01 00:00:00",
        row_offset=1000,
        changes={"POINTS": "0"},
    )
    rows = poll_week(harness, 2024, AP, "regular", 3)
    assert len(rows) == 10 and all(row["POINTS"] > 0 for row in rows)


def test_rows_of_one_fetch_with_the_same_key_take_the_later_poll_position(
    harness: Harness, league: League
) -> None:
    """Scenario ratings-rankings: more than one committee snapshot of a week in one fetch is
    resolved by the later position of the poll in the response (`POLLS_NO`), not by the row."""
    where = f"SEASON = 2025 AND POLL = '{COMMITTEE}' AND SEASON_TYPE = 'regular' AND WEEK = 14"
    assert len(poll_week(harness, 2025, COMMITTEE, "regular", 14)) == 8
    slice_key, fetch = harness.query(
        f"SELECT DISTINCT REQ_SLICE, REQ_AT FROM {harness.core}.RANKINGS_POLLS_RANKS WHERE {where}"
    )[0]
    mirror_edit.store_changed_copy(
        harness,
        "RANKINGS_POLLS_RANKS",
        where,
        marker=slice_key,
        fetched_at=str(fetch),
        row_offset=-100,
        changes={"POLLS_NO": "POLLS_NO + 1", "POINTS": "POINTS + 7"},
    )
    try:
        rows = poll_week(harness, 2025, COMMITTEE, "regular", 14)
        assert len(rows) == 8
        assert [row["POINTS"] for row in rows] == [1500 - 50 * row["POLL_RANK"] + 7 for row in rows]
    finally:
        harness.owner.execute(
            f"DELETE FROM {harness.core}.RANKINGS_POLLS_RANKS WHERE {where} AND ROW_NO < 0"
        )
        harness.rebuild_model()
    rows = poll_week(harness, 2025, COMMITTEE, "regular", 14)
    assert [row["POINTS"] for row in rows] == [1500 - 50 * row["POLL_RANK"] for row in rows]


# --- SP+ ratings (RATING_SP) ---------------------------------------------------------------------

SP_MARKER = "marker-sp-it"
SP_LATER_FETCH = "2030-01-01 00:00:00"
SP_DECIMAL_COLUMNS = ("SP_RATING", "OFFENSE_RATING", "DEFENSE_RATING", "SPECIAL_TEAMS_RATING")
SP_PARTS = {
    "OFFENSE_SUCCESS": ("offense", "success"),
    "OFFENSE_EXPLOSIVENESS": ("offense", "explosiveness"),
    "OFFENSE_RUSHING": ("offense", "rushing"),
    "OFFENSE_PASSING": ("offense", "passing"),
    "OFFENSE_STANDARD_DOWNS": ("offense", "standardDowns"),
    "OFFENSE_PASSING_DOWNS": ("offense", "passingDowns"),
    "OFFENSE_PACE": ("offense", "pace"),
    "OFFENSE_RUN_RATE": ("offense", "runRate"),
    "DEFENSE_SUCCESS": ("defense", "success"),
    "DEFENSE_EXPLOSIVENESS": ("defense", "explosiveness"),
    "DEFENSE_RUSHING": ("defense", "rushing"),
    "DEFENSE_PASSING": ("defense", "passing"),
    "DEFENSE_STANDARD_DOWNS": ("defense", "standardDowns"),
    "DEFENSE_PASSING_DOWNS": ("defense", "passingDowns"),
    "DEFENSE_HAVOC_TOTAL": ("defense", "havoc", "total"),
    "DEFENSE_HAVOC_FRONT_SEVEN": ("defense", "havoc", "frontSeven"),
    "DEFENSE_HAVOC_DB": ("defense", "havoc", "db"),
}
SpKey = tuple[int, str]


def decimal(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def two_places(value: float | None) -> Decimal | None:
    return None if value is None else Decimal(str(value)).quantize(Decimal("0.01"))


def stored_ratings(league: League) -> dict[SpKey, dict[str, Any]]:
    """Every SP+ row of the league's bodies except the national-averages row, by season and name."""
    result: dict[SpKey, dict[str, Any]] = {}
    for season in SP_SEASONS:
        slice_ = league.get("RATINGS_SP", season_key("RATINGS_SP", season))
        for body in slice_.body:
            if body["team"] == SP_NATIONAL_AVERAGES:
                continue
            assert (season, body["team"]) not in result
            result[(season, body["team"])] = {**body, "fetched_at": slice_.fetched_at}
    return result


def rating_rows(harness: Harness, where: str = "1 = 1") -> list[dict[str, Any]]:
    return harness.query_dicts(
        f"SELECT * FROM {harness.model}.RATING_SP WHERE {where} ORDER BY SEASON, SCHOOL"
    )


def rating_row(harness: Harness, season: int, school: str) -> dict[str, Any]:
    rows = harness.query_dicts(
        f"SELECT * FROM {harness.model}.RATING_SP WHERE SEASON = {season} AND SCHOOL = {{name}}",
        {"name": school},
    )
    assert len(rows) == 1, (season, school)
    return rows[0]


def test_every_stored_rating_is_one_row_with_its_values(harness: Harness, league: League) -> None:
    expected = stored_ratings(league)
    rows = rating_rows(harness)
    assert len(rows) == len(expected)
    seen: set[SpKey] = set()
    for row in rows:
        key = (row["SEASON"], row["SCHOOL"])
        assert key not in seen
        seen.add(key)
        body = expected[key]
        assert row["CONFERENCE"] == body["conference"], key
        assert decimal(row["SP_RATING"]) == two_places(body["rating"]), key
        assert row["SP_RANKING"] == body["ranking"], key
        assert decimal(row["OFFENSE_RATING"]) == two_places(body["offense"]["rating"]), key
        assert row["OFFENSE_RANKING"] == body["offense"]["ranking"], key
        assert decimal(row["DEFENSE_RATING"]) == two_places(body["defense"]["rating"]), key
        assert row["DEFENSE_RANKING"] == body["defense"]["ranking"], key
        assert decimal(row["SPECIAL_TEAMS_RATING"]) == two_places(body["specialTeams"]["rating"])
        assert row["STRENGTH_OF_SCHEDULE"] == pytest.approx(body["sos"]), key
        assert row["SECOND_ORDER_WINS"] == pytest.approx(body["secondOrderWins"]), key
        assert moment(row["FETCHED_AT"]) == body["fetched_at"], key
        for column, path in SP_PARTS.items():
            value: Any = body
            for step in path:
                value = value[step]
            assert row[column] == pytest.approx(value), (key, column)
    assert seen == set(expected)


def test_ratings_are_fixed_scale_decimals(harness: Harness, league: League) -> None:
    """Scenario as-of: Engine inputs are fixed-scale decimals (the `RATING_SP` columns)."""
    types = dict(
        harness.query(
            "SELECT COLUMN_NAME, COLUMN_TYPE FROM EXA_ALL_COLUMNS "
            f"WHERE COLUMN_SCHEMA = '{harness.model}' AND COLUMN_TABLE = 'RATING_SP'"
        )
    )
    assert {types[column] for column in SP_DECIMAL_COLUMNS} == {"DECIMAL(6,2)"}
    assert types["SEASON"] == "DECIMAL(4,0)"
    assert all(types[column] == "DOUBLE" for column in SP_PARTS)


def test_sp_team_matching(harness: Harness, league: League) -> None:
    """Scenario ratings-rankings: SP+ ratings attach to teams by name (the `RATING_SP` part)."""
    ashford = rating_row(harness, 2024, "Ashford")
    assert ashford["TEAM_ID"] == ASHFORD
    assert ashford["IS_TEAM_MATCHED"] is True
    assert decimal(ashford["SP_RATING"]) == Decimal("25.10")
    unmatched = rating_row(harness, 2024, SP_UNMATCHED)
    assert unmatched["TEAM_ID"] is None
    assert unmatched["IS_TEAM_MATCHED"] is False
    assert harness.query(
        f"SELECT COUNT(*) FROM {harness.model}.RATING_SP WHERE SCHOOL = '{SP_NATIONAL_AVERAGES}'"
    ) == [(0,)]
    stored = harness.query(
        f"SELECT COUNT(*) FROM {harness.core}.RATINGS_SP "
        f"WHERE YEAR_ = 2024 AND TEAM = '{SP_NATIONAL_AVERAGES}'"
    )
    assert stored == [(1,)]


def test_only_the_unmatched_name_has_no_team(harness: Harness, league: League) -> None:
    for row in rating_rows(harness):
        if (row["SEASON"], row["SCHOOL"]) == (2024, SP_UNMATCHED):
            continue
        assert row["IS_TEAM_MATCHED"] is True, (row["SEASON"], row["SCHOOL"])
        assert row["TEAM_ID"] == lt.team(row["SCHOOL"]).id, (row["SEASON"], row["SCHOOL"])
    assert harness.query(
        f"SELECT COUNT(*) FROM {harness.model}.RATING_SP WHERE NOT IS_TEAM_MATCHED"
    ) == [(1,)]
    assert harness.query(
        f"SELECT COUNT(*) FROM {harness.model}.RATING_SP "
        "WHERE (TEAM_ID IS NULL) <> (NOT IS_TEAM_MATCHED)"
    ) == [(0,)]


def test_current_sp_not_final(harness: Harness, league: League) -> None:
    """Scenario ratings-rankings: The current season's SP+ is marked as not final."""
    current = rating_rows(harness, f"SEASON = {CURRENT_SEASON}")
    assert current and {row["IS_FINAL"] for row in current} == {False}
    assert {moment(row["FETCHED_AT"]) for row in current} == {datetime(2026, 10, 25, 12, 0)}
    previous = rating_rows(harness, f"SEASON = {LAST_FINAL_SEASON}")
    assert previous and {row["IS_FINAL"] for row in previous} == {True}


def test_finality_follows_the_slice_of_every_row(harness: Harness, league: League) -> None:
    for row in rating_rows(harness):
        assert row["IS_FINAL"] is is_final(row["SEASON"]), (row["SEASON"], row["SCHOOL"])
        assert moment(row["FETCHED_AT"]) == fetched_at(row["SEASON"])


@pytest.fixture
def sp_stored_twice(harness: Harness, league: League) -> Iterator[None]:
    yield
    mirror_edit.remove_copies(harness, "RATINGS_SP", SP_MARKER)


def test_the_newest_fetch_of_a_rating_wins(
    harness: Harness, league: League, sp_stored_twice: None
) -> None:
    """Scenario ratings-rankings: A rating or a ranking stored twice appears once (the
    `RATING_SP` part, as a later copy of Ashford's stored 2024 row)."""
    stored = mirror_edit.store_changed_copy(
        harness,
        "RATINGS_SP",
        "YEAR_ = 2024 AND TEAM = 'Ashford'",
        marker=SP_MARKER,
        fetched_at=SP_LATER_FETCH,
        row_offset=1000,
        changes={"RATING": "24.0", "RANKING": "7"},
    )
    assert stored == 1
    row = rating_row(harness, 2024, "Ashford")
    assert decimal(row["SP_RATING"]) == Decimal("24.00") and row["SP_RANKING"] == 7
    assert moment(row["FETCHED_AT"]) == datetime(2030, 1, 1)
    assert row["IS_FINAL"] is False
    assert len(rating_rows(harness, "SEASON = 2024")) == len(
        [key for key in stored_ratings(league) if key[0] == 2024]
    )


def test_an_older_rating_fetch_never_replaces_a_newer_one(
    harness: Harness, league: League, sp_stored_twice: None
) -> None:
    mirror_edit.store_changed_copy(
        harness,
        "RATINGS_SP",
        "YEAR_ = 2024 AND TEAM = 'Ashford'",
        marker=SP_MARKER,
        fetched_at="2000-01-01 00:00:00",
        row_offset=1000,
        changes={"RATING": "0.0"},
    )
    row = rating_row(harness, 2024, "Ashford")
    assert decimal(row["SP_RATING"]) == Decimal("25.10")
    assert row["IS_FINAL"] is True


def test_ties_of_fetch_time_take_the_later_slice_key_then_the_later_row(
    harness: Harness, league: League, sp_stored_twice: None
) -> None:
    where = "YEAR_ = 2024 AND TEAM = 'Ashford'"
    slice_key, fetch = harness.query(
        f"SELECT DISTINCT REQ_SLICE, REQ_AT FROM {harness.core}.RATINGS_SP WHERE {where}"
    )[0]
    mirror_edit.store_changed_copy(
        harness,
        "RATINGS_SP",
        where,
        marker=SP_MARKER,
        fetched_at=str(fetch),
        row_offset=1000,
        changes={"RATING": "11.0"},
    )
    assert decimal(rating_row(harness, 2024, "Ashford")["SP_RATING"]) == Decimal("11.00")
    mirror_edit.remove_copies(harness, "RATINGS_SP", SP_MARKER)
    mirror_edit.store_changed_copy(
        harness,
        "RATINGS_SP",
        where,
        marker=slice_key,
        fetched_at=str(fetch),
        row_offset=1000,
        changes={"RATING": "12.0"},
    )
    try:
        assert decimal(rating_row(harness, 2024, "Ashford")["SP_RATING"]) == Decimal("12.00")
    finally:
        harness.owner.execute(
            f"DELETE FROM {harness.core}.RATINGS_SP WHERE {where} AND ROW_NO >= 1000"
        )
        harness.rebuild_model()
    assert decimal(rating_row(harness, 2024, "Ashford")["SP_RATING"]) == Decimal("25.10")


def test_a_team_name_matches_only_exactly(
    harness: Harness, league: League, sp_stored_twice: None
) -> None:
    mirror_edit.store_changed_copy(
        harness,
        "RATINGS_SP",
        "YEAR_ = 2024 AND TEAM = 'Ashford'",
        marker=SP_MARKER,
        fetched_at=SP_LATER_FETCH,
        row_offset=1000,
        changes={"TEAM": "'ashford'"},
    )
    lower = rating_row(harness, 2024, "ashford")
    assert (lower["TEAM_ID"], lower["IS_TEAM_MATCHED"]) == (None, False)
    assert rating_row(harness, 2024, "Ashford")["TEAM_ID"] == ASHFORD


def test_a_name_of_a_team_outside_the_team_list_stays_unmatched(
    harness: Harness, league: League, sp_stored_twice: None
) -> None:
    """Scenario ratings-rankings: SP+ ratings attach to teams by name: TEAM_ID is the team of the
    stored team list with that name, so a team seen only in games (Marston) does not match."""
    mirror_edit.store_changed_copy(
        harness,
        "RATINGS_SP",
        "YEAR_ = 2024 AND TEAM = 'Ashford'",
        marker=SP_MARKER,
        fetched_at=SP_LATER_FETCH,
        row_offset=1000,
        changes={"TEAM": "'Marston'"},
    )
    assert harness.query(
        f"SELECT IS_IN_TEAM_LIST FROM {harness.model}.TEAM WHERE SCHOOL = 'Marston'"
    ) == [(False,)]
    marston = rating_row(harness, 2024, "Marston")
    assert (marston["TEAM_ID"], marston["IS_TEAM_MATCHED"]) == (None, False)


# --- TEAM_SEASON: SP+ of the season, the prior, final ranks ------------------------------------


def team_season(harness: Harness, name: str, season: int) -> dict[str, Any]:
    rows = harness.query_dicts(
        f"SELECT * FROM {harness.model}.TEAM_SEASON "
        f"WHERE TEAM_ID = {lt.team(name).id} AND SEASON = {season}"
    )
    assert len(rows) == 1, (name, season)
    return rows[0]


def test_sp_prior(harness: Harness, league: League) -> None:
    """Scenario ratings-rankings: The previous season's SP+ is the prior."""
    carrow = team_season(harness, "Carrow", 2024)
    assert decimal(carrow["SP_PRIOR_RATING"]) == Decimal("20.00")
    assert (carrow["HAS_SP_PRIOR"], carrow["SP_PRIOR_IS_FINAL"]) == (True, True)
    ashford = team_season(harness, "Ashford", 2013)
    assert ashford["SP_PRIOR_RATING"] is None
    assert (ashford["HAS_SP_PRIOR"], ashford["SP_PRIOR_IS_FINAL"]) == (False, None)
    stored_2013 = rating_row(harness, 2013, "Ashford")["SP_RATING"]
    assert decimal(ashford["SP_RATING"]) == decimal(stored_2013)
    assert decimal(team_season(harness, "Ashford", 2025)["SP_PRIOR_RATING"]) == Decimal("25.10")


def test_sp_columns_come_from_rating_sp_of_the_season_and_the_one_before(
    harness: Harness, league: League
) -> None:
    ratings = {
        (row["SEASON"], row["TEAM_ID"]): row for row in rating_rows(harness, "TEAM_ID IS NOT NULL")
    }
    rows = harness.query_dicts(f"SELECT * FROM {harness.model}.TEAM_SEASON")
    with_prior = 0
    for row in rows:
        key = (row["SEASON"], row["TEAM_ID"])
        current = ratings.get(key)
        prior = ratings.get((row["SEASON"] - 1, row["TEAM_ID"]))
        assert decimal(row["SP_RATING"]) == (current and decimal(current["SP_RATING"])), key
        assert decimal(row["SP_PRIOR_RATING"]) == (prior and decimal(prior["SP_RATING"])), key
        assert row["HAS_SP_PRIOR"] is (prior is not None), key
        assert row["SP_PRIOR_IS_FINAL"] == (prior and prior["IS_FINAL"]), key
        with_prior += prior is not None
    assert with_prior > 0
    types = dict(
        harness.query(
            "SELECT COLUMN_NAME, COLUMN_TYPE FROM EXA_ALL_COLUMNS "
            f"WHERE COLUMN_SCHEMA = '{harness.model}' AND COLUMN_TABLE = 'TEAM_SEASON'"
        )
    )
    assert (types["SP_RATING"], types["SP_PRIOR_RATING"]) == ("DECIMAL(6,2)", "DECIMAL(6,2)")


def test_final_ranks(harness: Harness, league: League) -> None:
    """Scenario ratings-rankings: Final ranks belong to the team-season."""
    done = team_season(harness, "Ashford", 2024)
    assert (done["FINAL_AP_RANK"], done["FINAL_CFP_RANK"]) == (5, 1)
    current = team_season(harness, "Ashford", CURRENT_SEASON)
    assert (current["FINAL_AP_RANK"], current["FINAL_CFP_RANK"]) == (None, None)
    assert harness.query(
        f"SELECT COUNT(*) FROM {harness.model}.TEAM_SEASON "
        f"WHERE SEASON = {CURRENT_SEASON} AND (FINAL_AP_RANK IS NOT NULL "
        "OR FINAL_CFP_RANK IS NOT NULL)"
    ) == [(0,)]


def test_final_ranks_come_from_the_final_polls(harness: Harness, league: League) -> None:
    finals: dict[tuple[int, int, str], int] = {}
    for row in ranking_rows(harness, "IS_FINAL_POLL AND POLL_CODE IN ('AP', 'CFP')"):
        key = (row["SEASON"], row["TEAM_ID"], row["POLL_CODE"])
        assert key not in finals, key
        finals[key] = row["POLL_RANK"]
    rows = harness.query_dicts(
        f"SELECT SEASON, TEAM_ID, FINAL_AP_RANK, FINAL_CFP_RANK FROM {harness.model}.TEAM_SEASON"
    )
    ranked = 0
    for row in rows:
        season, team_id = row["SEASON"], row["TEAM_ID"]
        assert row["FINAL_AP_RANK"] == finals.get((season, team_id, "AP")), (season, team_id)
        assert row["FINAL_CFP_RANK"] == finals.get((season, team_id, "CFP")), (season, team_id)
        ranked += row["FINAL_AP_RANK"] is not None
    assert ranked == len([key for key in finals if key[2] == "AP"]) > 0
