"""Registered-step test scripts for `REFRESH_WEEK`, rendered as SQL (no database needed here).

The integration harness creates them in the infra schema of its namespace and registers them in
the load plan variants (`refresh_steps`). Each is called as
`EXECUTE SCRIPT <script>(run_id, season, week, season_type)`:

- `TEST_STEP_RECORD` inserts its arguments into `TEST_STEP_CALLS` (in call order, `CALL_NO`), so a
  test reads which steps ran, in which order and with which season type.
- `TEST_STEP_FAIL` raises an error that names the step and the run.
- `TEST_STEP_SLEEP` calls the Python UDF `TEST_SLEEP`, which sleeps (5 seconds by default), so
  another session can observe the step as `RUNNING`.
"""

from __future__ import annotations

import re

RECORD_SCRIPT = "TEST_STEP_RECORD"
FAIL_SCRIPT = "TEST_STEP_FAIL"
SLEEP_SCRIPT = "TEST_STEP_SLEEP"
STEP_SCRIPT_NAMES = (RECORD_SCRIPT, FAIL_SCRIPT, SLEEP_SCRIPT)
CALLS_TABLE = "TEST_STEP_CALLS"
SLEEP_UDF = "TEST_SLEEP"
SLEEP_SECONDS = 5

_SCHEMA = re.compile(r"[A-Z][A-Z0-9_]*")
_HEADER = "(run_id, season, week, season_type) RETURNS ROWCOUNT AS"


def step_scripts_sql(schema: str, *, sleep_seconds: int = SLEEP_SECONDS) -> list[str]:
    """The statements that create the calls table, the sleep UDF and the three step scripts."""
    if not _SCHEMA.fullmatch(schema):
        raise ValueError(f"schema name {schema!r} must be an UPPER_CASE identifier")
    return [
        _calls_table(schema),
        _sleep_udf(schema),
        _record_script(schema),
        _fail_script(schema),
        _sleep_script(schema, sleep_seconds),
    ]


def _calls_table(schema: str) -> str:
    return (
        f"CREATE TABLE IF NOT EXISTS {schema}.{CALLS_TABLE} (\n"
        "  CALL_NO DECIMAL(18,0) IDENTITY NOT NULL,\n"
        "  RUN_ID DECIMAL(18,0),\n"
        "  SEASON DECIMAL(4,0),\n"
        "  WEEK DECIMAL(3,0),\n"
        "  SEASON_TYPE VARCHAR(16) UTF8,\n"
        "  STEP_SCRIPT VARCHAR(64) UTF8,\n"
        "  CALLED_AT TIMESTAMP\n"
        ")"
    )


def _sleep_udf(schema: str) -> str:
    return (
        f"CREATE OR REPLACE PYTHON3 SCALAR SCRIPT {schema}.{SLEEP_UDF}(SECONDS DOUBLE) "
        "RETURNS DOUBLE AS\n"
        "import time\n"
        "\n"
        "\n"
        "def run(ctx):\n"
        "    time.sleep(ctx.SECONDS)\n"
        "    return ctx.SECONDS\n"
        "/"
    )


def _record_script(schema: str) -> str:
    return (
        f"CREATE OR REPLACE LUA SCRIPT {schema}.{RECORD_SCRIPT}{_HEADER}\n"
        f'query([[INSERT INTO "{schema}"."{CALLS_TABLE}" '
        '("RUN_ID", "SEASON", "WEEK", "SEASON_TYPE", "STEP_SCRIPT", "CALLED_AT") '
        f"VALUES (:r, :s, :w, :t, '{RECORD_SCRIPT}', CURRENT_TIMESTAMP)]],\n"
        "  {r = run_id, s = season, w = week, t = season_type or null})\n"
        "/"
    )


def _fail_script(schema: str) -> str:
    return (
        f"CREATE OR REPLACE LUA SCRIPT {schema}.{FAIL_SCRIPT}{_HEADER}\n"
        f'error("{FAIL_SCRIPT}: forced failure in run " .. tostring(run_id))\n'
        "/"
    )


def _sleep_script(schema: str, seconds: int) -> str:
    return (
        f"CREATE OR REPLACE LUA SCRIPT {schema}.{SLEEP_SCRIPT}{_HEADER}\n"
        f'query([[SELECT "{schema}"."{SLEEP_UDF}"(:n)]], {{n = {seconds}}})\n'
        "/"
    )
