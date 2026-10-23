"""Integration tests of model/ratings-rankings: `RANKING` (the SP+ parts come with `RATING_SP`).

Read-only module over the synthetic league (marker `model_readonly`). A test that stores another
fetch of a row removes it again before it ends (`tests/it/mirror_edit.py`). The expectations are
the league's own poll bodies and the spec's rules restated in Python (`expected_visible_at`,
`expected_final`), never the SQL under test. The as-of parts (`RANKING_AS_OF`, the fetch-time
horizon) belong to `330_ranking_as_of.sql`, `FINAL_CFP_RANK` to `210_team_season.sql`.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
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
    LEAD_WEEKS,
    MYSTERY,
    RANKING_SEASONS,
    League,
    fetched_at,
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
    rows = poll_week(harness, 2025, COMMITTEE, "regular", 14)
    assert [row["POINTS"] for row in rows] == [1500 - 50 * row["POLL_RANK"] for row in rows]
