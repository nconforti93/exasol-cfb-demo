"""Game-id adapter to the stored-season what-if engine."""

from __future__ import annotations

from typing import cast

import pytest

from analyses import whatif
from engine import whatif as engine_whatif


def test_adapter_uses_serving_views_and_the_engine_position_contract() -> None:
    actual = whatif.what_if_odds_sql(
        7, [whatif.Condition(401, False), whatif.Condition(403, True)], {401: 3, 403: 9}
    )
    expected = engine_whatif.all_teams_sql(
        7,
        [engine_whatif.Condition(3, False), engine_whatif.Condition(9, True)],
        engine_whatif.Names(
            schema="CFB_API",
            outcomes="WHATIF_OUTCOMES",
            playoff="WHATIF_PLAYOFF",
            roster="WHATIF_TEAMS",
        ),
    )
    assert actual == expected
    assert "SUBSTR(o.\"OUTCOMES\", 3, 1) = '0'" in actual
    assert '"CFB_SIM"' not in actual


@pytest.mark.parametrize(
    ("run", "conditions", "games"),
    [
        (True, [], {}),
        (7, [whatif.Condition(True, True)], {True: 3}),
        (7, [whatif.Condition(402, True)], {401: 3}),
        (7, [whatif.Condition(401, True), whatif.Condition(401, False)], {401: 3}),
        (7, [whatif.Condition(401, cast(bool, 1))], {401: 3}),
        (7, [whatif.Condition(n, True) for n in range(17)], {n: n + 1 for n in range(17)}),
        (7, [whatif.Condition(401, True)], {401: 0}),
    ],
)
def test_invalid_request_is_refused_before_sql(
    run: int, conditions: list[whatif.Condition], games: dict[int, int]
) -> None:
    with pytest.raises(whatif.ConditionError):
        whatif.what_if_odds_sql(run, conditions, games)
