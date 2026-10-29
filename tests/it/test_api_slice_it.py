"""The first serving slice answers from stored model data without a live API request."""

from __future__ import annotations

import pyexasol
import pytest

from scripts import grants
from tests.it.harness import Harness
from tests.synthetic.league import League

pytestmark = pytest.mark.model_readonly
PLAN_VARIANT = "model"


def test_serving_views_follow_the_model_and_keep_the_point_without_a_run(
    harness: Harness, league: League
) -> None:
    first = harness.deploy_api()
    second = harness.deploy_api()
    assert first.run_id > 0 and second.run_id > first.run_id

    point = harness.query(
        f'SELECT "AS_OF_SEASON", "AS_OF_WEEK_SEQ", "NEXT_WEEK_SEQ", "RUN_ID", '
        f'"HAS_PUBLICATION" FROM {harness.namespace.api_schema}."CURRENT_POINT"'
    )
    assert point == [(2026, 9, 10, None, False)]

    sample = harness.query(
        f'SELECT "TEAM_ID", "SCHOOL", "IS_FBS", "HOME_VENUE_NAME" '
        f'FROM {harness.namespace.api_schema}."TEAMS" '
        f"WHERE \"SCHOOL\" = 'Ashford'"
    )
    assert len(sample) == 1 and sample[0][1] == "Ashford"
    served = harness.query(
        f'SELECT "GAME_ID", "SEASON" FROM {harness.namespace.api_schema}."GAMES" '
        'WHERE "SEASON" = 2026 ORDER BY "GAME_ID"'
    )
    source = harness.query(
        f'SELECT "GAME_ID", "SEASON" FROM {harness.model}."GAME" '
        'WHERE "SEASON" = 2026 ORDER BY "GAME_ID"'
    )
    assert served == source


def test_app_and_chat_roles_get_only_their_serving_schemas(
    harness: Harness, league: League
) -> None:
    harness.deploy_api()
    report = grants.apply(
        grants.PyexasolDatabase(harness.sys),
        harness.namespace,
        harness.namespace.default_connection,
    )
    assert report.exit_code == grants.EXIT_OK
    rows = harness.sys.execute(
        "SELECT OBJECT_NAME, GRANTEE FROM EXA_DBA_OBJ_PRIVS "
        "WHERE OBJECT_TYPE = 'SCHEMA' AND PRIVILEGE = 'SELECT' "
        f"AND OBJECT_NAME IN ('{harness.namespace.api_schema}', "
        f"'{harness.namespace.api_live_schema}')"
    ).fetchall()
    actual = {(row[0], row[1]) for row in rows}
    assert actual == {
        (harness.namespace.api_schema, harness.namespace.app_role),
        (harness.namespace.api_schema, harness.namespace.mcp_role),
        (harness.namespace.api_live_schema, harness.namespace.app_role),
    }
    for role in (harness.namespace.app_role, harness.namespace.mcp_role):
        with harness.role_session(role) as session:
            served = session.execute(
                f'SELECT COUNT(*) FROM {harness.namespace.api_schema}."TEAMS"'
            ).fetchone()[0]
            assert served > 0
            with pytest.raises(pyexasol.ExaQueryError, match="insufficient privileges"):
                session.execute(f'SELECT COUNT(*) FROM {harness.model}."TEAM"')
