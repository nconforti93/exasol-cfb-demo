"""`engine/whatif.py`: validation and the SQL shape of the what-if over stored seasons
(sim-engine/sim-runs, layout A)."""

from __future__ import annotations

import re

import pytest

from engine import core, whatif
from engine.whatif import Condition, Names, WhatIfError


def flat(sql: str) -> str:
    return " ".join(sql.split())


def test_one_team_sql_filters_outcomes_and_counts_the_team() -> None:
    sql = flat(whatif.team_sql(42, 2, [Condition(37, True), Condition(5, False)]))
    assert sql.startswith('WITH "MATCHING" AS (SELECT o."SIM_ID" FROM "CFB_SIM"."SIM_PATH" o')
    assert (
        'WHERE o."RUN_ID" = 42 AND SUBSTR(o."OUTCOMES", 37, 1) = \'1\' '
        "AND SUBSTR(o.\"OUTCOMES\", 5, 1) = '0')" in sql
    )
    assert 'COUNT(*) AS "N_MATCHING", COUNT(t."SIM_ID") AS "N_IN_CFP"' in sql
    assert '"N_BYE"' in sql and '"N_CHAMPION"' in sql
    assert (
        'FROM "CFB_SIM"."SIM_TEAM" p WHERE p."RUN_ID" = 42 AND p."TEAM_ID" = 2) t '
        'ON t."SIM_ID" = m."SIM_ID"' in sql
    )


def test_all_teams_sql_keeps_every_eligible_roster_team() -> None:
    sql = flat(whatif.all_teams_sql(7, [Condition(1, True)]))
    assert 'FROM "CFB_SIM"."SIM_RUN_TEAM" r CROSS JOIN "MATCH_COUNT" c' in sql
    assert 'LEFT JOIN "TEAM_COUNTS" k ON k."TEAM_ID" = r."TEAM_ID"' in sql
    assert 'WHERE r."RUN_ID" = 7 AND r."IS_ELIGIBLE"' in sql
    assert 'COALESCE(k."N_IN_CFP", 0) AS "N_IN_CFP"' in sql
    assert 'JOIN "MATCHING" m ON m."SIM_ID" = p."SIM_ID" WHERE p."RUN_ID" = 7' in sql
    assert sql.endswith('ORDER BY r."TEAM_ID"')


def test_custom_names_replace_every_object() -> None:
    names = Names(
        schema="CFB_API", outcomes="WHATIF_OUTCOMES", playoff="WHATIF_PLAYOFF", roster="ROSTER"
    )
    one = whatif.team_sql(3, 9, [Condition(2, False)], names)
    every = whatif.all_teams_sql(3, [Condition(2, False)], names)
    for sql in (one, every):
        assert '"CFB_API"."WHATIF_OUTCOMES"' in sql and '"CFB_API"."WHATIF_PLAYOFF"' in sql
        assert "CFB_SIM" not in sql and "SIM_PATH" not in sql and '"SIM_TEAM"' not in sql
    assert '"CFB_API"."ROSTER"' in every


def test_no_condition_matches_every_season_of_the_run() -> None:
    sql = flat(whatif.team_sql(42, 2, []))
    assert 'WHERE o."RUN_ID" = 42)' in sql and "SUBSTR" not in sql


@pytest.mark.parametrize("position", [0, -1, whatif.MAX_POSITION + 1])
def test_an_unknown_position_is_refused(position: int) -> None:
    with pytest.raises(WhatIfError, match="position"):
        whatif.team_sql(42, 2, [Condition(position, True)])


def test_seventeen_conditions_are_refused() -> None:
    conditions = [Condition(i, True) for i in range(1, whatif.MAX_CONDITIONS + 2)]
    with pytest.raises(WhatIfError, match="at most 16 conditions"):
        whatif.all_teams_sql(42, conditions)
    assert whatif.all_teams_sql(42, conditions[: whatif.MAX_CONDITIONS])


@pytest.mark.parametrize(
    ("run_id", "team_id", "conditions"),
    [
        (True, 2, []),
        (42, True, []),
        (42, 2, [Condition(True, True)]),
        (42, 2, [Condition(3, 1)]),  # type: ignore[arg-type]
        ("42", 2, []),
        (42, 2.0, []),
    ],
)
def test_ids_must_be_plain_integers(run_id: object, team_id: object, conditions: object) -> None:
    with pytest.raises(WhatIfError):
        whatif.team_sql(run_id, team_id, conditions)  # type: ignore[arg-type]


def test_a_game_appears_in_one_condition_only() -> None:
    with pytest.raises(WhatIfError, match="twice"):
        whatif.team_sql(42, 2, [Condition(3, True), Condition(3, False)])


@pytest.mark.parametrize("bad", ['SIM"PATH', "sim_path", "1ABC", "A.B", ""])
def test_names_must_be_upper_case_identifiers(bad: str) -> None:
    with pytest.raises(WhatIfError, match="identifier"):
        whatif.team_sql(1, 2, [], Names(outcomes=bad))


def test_team_condition_maps_a_team_result_to_the_home_character() -> None:
    assert whatif.team_condition(5, 11, home_team_id=5, away_team_id=8, team_wins=True) == (
        Condition(11, True)
    )
    assert whatif.team_condition(8, 11, home_team_id=5, away_team_id=8, team_wins=True) == (
        Condition(11, False)
    )
    assert whatif.team_condition(8, 11, home_team_id=5, away_team_id=8, team_wins=False) == (
        Condition(11, True)
    )
    with pytest.raises(WhatIfError, match="does not play"):
        whatif.team_condition(9, 11, home_team_id=5, away_team_id=8, team_wins=True)


def test_the_position_limit_is_the_engines_and_the_sql_has_no_placeholders() -> None:
    assert whatif.MAX_POSITION == core.MAX_REMAINING_GAMES
    sql = whatif.all_teams_sql(1, [Condition(1, True)])
    assert not re.search(r"\{|\}|%s|\?", sql)
