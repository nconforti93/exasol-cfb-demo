#!/usr/bin/env python3
"""S6 model platform probe (task 1.1, plan add-cfb-analytical-model).

Checks the Exasol platform facts (a)-(i) that the analytical model's deploy (`vs/host/sqlobjects.py`),
its views, functions, `GEO_POINT`, engine-facing `DECIMAL` casts and `REFRESH_MODEL` are built on.
Findings and the fallback the plan adopts for each negative result are in
`docs/spikes/s6-model-probe.md`.

Safety rules:
- Only schemas named `PROBE_MP_*` are created and dropped (the plan's probe prefix for this
  task). Other agents' `PROBE_*` schemas (for example `PROBE_LD_*`) are never touched. Nothing
  `TEST_*`, `CFB_*`, `CFBD_VS`, `CFB_DIAG`, `VECTOR_DEMO` or `VEC_SMOKE` is read, written or dropped.
- No user, connection, virtual schema or BucketFS object is created. Zero CFBD calls.
- The sys password comes from the Personal deployment files through `vs/host/target.py` at
  connect time and is never printed or stored.

Usage:
    uv run python docs/spikes/s6_model_probe.py [--target local|aws] [--only a,b,...] [--keep]

Writes `cache/probe/s6-model-probe[-aws].json` (gitignored). Exit code 3 when the gate (a or b)
is negative, so the fallback must be applied.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import math
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from vs.host import target  # noqa: E402

RESULTS_DIR = REPO_ROOT / "cache" / "probe"
PREFIX = "PROBE_MP_"
ITEMS = "abcdefghi"
GATE_ITEMS = ("a", "b")

TITLES = {
    "a": "Inline view and table comments, COMMENT ON a view, maximum length, quotes",
    "b": "DDL in a transaction: rollback restores views, tables, functions, rows, comments",
    "c": "GEOMETRY(4326): POINT from numbers, column type, ST_DISTANCE units, ST_WITHIN, SRID 5070",
    "d": "CREATE FUNCTION GREAT_CIRCLE_KM: results, NULLs, the / terminator, comment",
    "e": "EXA_ALL_DEPENDENCIES: view-view-table, view-meta view, view-function",
    "f": "CREATE OR REPLACE FORCE VIEW over a missing object; STATUS after table replacement",
    "g": "Filters on the point columns and on a UNION ALL view reach the fact scans",
    "h": "Nested Lua step: insert, COMMIT, error(): committed rows survive the caller's rollback",
    "i": "CAST(DOUBLE AS DECIMAL(6,2) / (5,4)) rounds; exact read-back",
}


@dataclass
class Finding:
    item: str
    title: str
    result: str = "pending"  # pass | fail | partial | info
    summary: str = ""
    fallback_needed: bool = False
    evidence: dict[str, Any] = field(default_factory=dict)


def error_text(error: BaseException) -> str:
    message = getattr(error, "message", None) or str(error)
    return " ".join(str(message).split())[:400]


def jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, (datetime,)):
        return value.isoformat()
    return value


class Session:
    """A pyexasol connection whose query errors come back as data."""

    def __init__(self, deployment: target.Target, *, autocommit: bool = True, timeout: float = 120):
        self.conn = deployment.connect(socket_timeout=timeout)
        self.conn.set_autocommit(autocommit)

    def run(self, sql: str) -> Any:
        return self.conn.execute(sql)

    def rows(self, sql: str) -> list[tuple[Any, ...]]:
        return [tuple(row) for row in self.conn.execute(sql).fetchall()]

    def value(self, sql: str) -> Any:
        return self.conn.execute(sql).fetchval()

    def attempt(self, sql: str) -> tuple[bool, Any]:
        try:
            statement = self.conn.execute(sql)
            if statement.result_type == "resultSet":
                return True, [tuple(row) for row in statement.fetchall()]
            return True, statement.rowcount()
        except Exception as error:
            return False, error_text(error)

    def commit(self) -> None:
        self.conn.commit()

    def rollback(self) -> None:
        self.conn.rollback()

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self.conn.close()


def sql_string(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


class Probe:
    def __init__(self, target_name: str, keep: bool) -> None:
        self.target_name = target_name
        self.keep = keep
        self.deployment = target.resolve(target_name)
        self.sys = Session(self.deployment)
        self.findings: dict[str, Finding] = {}
        suffix = "" if target_name == "local" else f"-{target_name}"
        self.results_file = RESULTS_DIR / f"s6-model-probe{suffix}.json"
        self.meta = {
            "target": self.deployment.describe(),
            "db_version": self.sys.value(
                "SELECT PARAM_VALUE FROM EXA_METADATA WHERE PARAM_NAME = 'databaseProductVersion'"
            ),
            "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }

    # ------------------------------------------------------------------ setup and cleanup

    def schema(self, name: str) -> str:
        """Create (replace) one `PROBE_MP_<name>` schema and return its name."""
        full = f"{PREFIX}{name}"
        self.sys.run(f"DROP SCHEMA IF EXISTS {full} CASCADE")
        self.sys.run(f"CREATE SCHEMA {full}")
        return full

    def cleanup(self) -> dict[str, Any]:
        failures = []
        names = [
            row[0]
            for row in self.sys.rows(
                f"SELECT SCHEMA_NAME FROM EXA_SCHEMAS WHERE SCHEMA_NAME LIKE '{PREFIX}%'"
            )
            if row[0].startswith(PREFIX)
        ]
        for name in names:
            ok, result = self.sys.attempt(f"DROP SCHEMA IF EXISTS {name} CASCADE")
            if not ok:
                failures.append(f"{name}: {result}")
        left = [
            row[0]
            for row in self.sys.rows(
                f"SELECT SCHEMA_NAME FROM EXA_SCHEMAS WHERE SCHEMA_NAME LIKE '{PREFIX}%'"
            )
            if row[0].startswith(PREFIX)
        ]
        return {"dropped": names, "failures": failures, "left": left}

    # ------------------------------------------------------------------ helpers

    def comments(self, schema: str) -> dict[str, Any]:
        objects = {
            row[0]: (row[1], row[2])
            for row in self.sys.rows(
                "SELECT OBJECT_NAME, OBJECT_TYPE, OBJECT_COMMENT FROM EXA_ALL_OBJECTS "
                f"WHERE ROOT_NAME = '{schema}' ORDER BY 1"
            )
        }
        columns = {
            f"{row[0]}.{row[1]}": row[2]
            for row in self.sys.rows(
                "SELECT COLUMN_TABLE, COLUMN_NAME, COLUMN_COMMENT FROM EXA_ALL_COLUMNS "
                f"WHERE COLUMN_SCHEMA = '{schema}' ORDER BY COLUMN_TABLE, COLUMN_ORDINAL_POSITION"
            )
        }
        return {"objects": objects, "columns": columns}

    # ------------------------------------------------------------------ (a)

    def probe_a(self) -> Finding:
        f = Finding("a", TITLES["a"])
        s = self.schema("A")
        sys_ = self.sys
        quoted_col = "it's a name"
        quoted_obj = "it's the view, with 'quotes'"
        sys_.run(
            f"CREATE TABLE {s}.T (ID DECIMAL(9,0) COMMENT IS 'id col', "
            f"NAME VARCHAR(50) UTF8 COMMENT IS {sql_string(quoted_col)}) COMMENT IS 'table comment'"
        )
        sys_.run(
            f"CREATE VIEW {s}.V (ID COMMENT IS 'view id', NAME COMMENT IS 'view name') "
            f"AS SELECT ID, NAME FROM {s}.T COMMENT IS {sql_string(quoted_obj)}"
        )
        read = self.comments(s)
        f.evidence["inline_readback"] = read
        inline_ok = (
            read["objects"].get("T", (None, None))[1] == "table comment"
            and read["objects"].get("V", (None, None))[1] == quoted_obj
            and read["columns"].get("T.NAME") == quoted_col
            and read["columns"].get("V.ID") == "view id"
            and read["columns"].get("V.NAME") == "view name"
        )
        f.evidence["inline_roundtrip_exact"] = inline_ok

        for label, sql in {
            "comment_on_table_view": f"COMMENT ON TABLE {s}.V IS 'v2 via COMMENT ON TABLE'",
            "comment_on_view_no_keyword": f"COMMENT ON {s}.V IS 'v3 via COMMENT ON'",
            "comment_on_column_view": f"COMMENT ON COLUMN {s}.V.ID IS 'id via COMMENT ON COLUMN'",
            "comment_on_column_table": f"COMMENT ON COLUMN {s}.T.ID IS 'tid via COMMENT ON COLUMN'",
        }.items():
            ok, result = sys_.attempt(sql)
            f.evidence[label] = "ok" if ok else f"ERROR: {result}"
        after = self.comments(s)
        f.evidence["after_comment_on"] = {
            "V": after["objects"].get("V"),
            "V.ID": after["columns"].get("V.ID"),
            "T.ID": after["columns"].get("T.ID"),
        }

        sys_.run(f"CREATE OR REPLACE VIEW {s}.V2 AS SELECT ID FROM {s}.T")
        ok, result = sys_.attempt(f"COMMENT ON TABLE {s}.V2 IS 'late view comment'")
        f.evidence["comment_on_table_view_no_inline"] = "ok" if ok else f"ERROR: {result}"

        limits: dict[str, Any] = {}
        for kind in (
            "view_comment",
            "view_column_comment",
            "table_comment",
            "table_column_comment",
        ):
            limits[kind] = self.comment_limit(s, kind)
        f.evidence["max_comment_length"] = limits

        multi = "line one\nline two, tab\t, brace {x}, percent %s, unicode éü中"
        ok, result = sys_.attempt(
            f"CREATE OR REPLACE VIEW {s}.VM (ID COMMENT IS {sql_string(multi)}) AS "
            f"SELECT ID FROM {s}.T COMMENT IS {sql_string(multi)}"
        )
        if ok:
            read = self.comments(s)
            f.evidence["special_characters_roundtrip"] = (
                read["objects"]["VM"][1] == multi and read["columns"]["VM.ID"] == multi
            )
        else:
            f.evidence["special_characters_roundtrip"] = f"ERROR: {result}"

        f.result = "pass" if inline_ok else "fail"
        f.fallback_needed = not inline_ok
        f.summary = (
            "inline comments on tables, views and their columns read back exactly from "
            "EXA_ALL_OBJECTS.OBJECT_COMMENT and EXA_ALL_COLUMNS.COLUMN_COMMENT"
            if inline_ok
            else "inline comments do not read back exactly"
        )
        return f

    def comment_limit(self, schema: str, kind: str) -> dict[str, Any]:
        def attempt(length: int) -> tuple[bool, str]:
            text = "x" * length
            if kind == "view_comment":
                sql = f"CREATE OR REPLACE VIEW {schema}.LV AS SELECT 1 AS C COMMENT IS '{text}'"
            elif kind == "view_column_comment":
                sql = f"CREATE OR REPLACE VIEW {schema}.LV (C COMMENT IS '{text}') AS SELECT 1"
            elif kind == "table_comment":
                sql = f"CREATE OR REPLACE TABLE {schema}.LT (C INT) COMMENT IS '{text}'"
            else:
                sql = f"CREATE OR REPLACE TABLE {schema}.LT (C INT COMMENT IS '{text}')"
            ok, result = self.sys.attempt(sql)
            if not ok:
                return False, result
            if kind.startswith("view_comment"):
                stored = self.sys.value(
                    f"SELECT OBJECT_COMMENT FROM EXA_ALL_OBJECTS WHERE ROOT_NAME = '{schema}' "
                    "AND OBJECT_NAME = 'LV'"
                )
            elif kind == "view_column_comment":
                stored = self.sys.value(
                    f"SELECT COLUMN_COMMENT FROM EXA_ALL_COLUMNS WHERE COLUMN_SCHEMA = '{schema}' "
                    "AND COLUMN_TABLE = 'LV'"
                )
            elif kind == "table_comment":
                stored = self.sys.value(
                    f"SELECT OBJECT_COMMENT FROM EXA_ALL_OBJECTS WHERE ROOT_NAME = '{schema}' "
                    "AND OBJECT_NAME = 'LT'"
                )
            else:
                stored = self.sys.value(
                    f"SELECT COLUMN_COMMENT FROM EXA_ALL_COLUMNS WHERE COLUMN_SCHEMA = '{schema}' "
                    "AND COLUMN_TABLE = 'LT'"
                )
            return (stored == text), f"stored length {len(stored or '')}"

        good, bad = 0, None
        first_error = ""
        for length in (100, 200, 500, 1000, 2000, 2001, 4000, 10000, 100000, 2000000):
            ok, info = attempt(length)
            if ok:
                good = length
            else:
                bad, first_error = length, info
                break
        if bad is None:
            return {"max_tested_ok": good, "limit_found": False}
        low, high = good, bad
        while high - low > 1:
            mid = (low + high) // 2
            ok, _ = attempt(mid)
            if ok:
                low = mid
            else:
                high = mid
        return {"max_ok": low, "first_failure": high, "error_at_failure": first_error[:200]}

    # ------------------------------------------------------------------ (b)

    def probe_b(self) -> Finding:
        f = Finding("b", TITLES["b"])
        s = self.schema("B")
        sys_ = self.sys
        for statement in (
            f"CREATE TABLE {s}.T1 (A DECIMAL(9,0) COMMENT IS 'old col a', B VARCHAR(20) UTF8) "
            "COMMENT IS 'old table'",
            f"INSERT INTO {s}.T1 VALUES (1, 'one'), (2, 'two')",
            f"CREATE VIEW {s}.V1 (A COMMENT IS 'old view col') AS SELECT A FROM {s}.T1 "
            "COMMENT IS 'old view'",
            f"CREATE VIEW {s}.V2 AS SELECT B FROM {s}.T1",
            f"CREATE TABLE {s}.T2 (X DECIMAL(9,0))",
            f"INSERT INTO {s}.T2 VALUES 10",
            f"CREATE FUNCTION {s}.F1 (X DOUBLE) RETURN DOUBLE IS BEGIN RETURN 1; END F1;",
            f"COMMENT ON FUNCTION {s}.F1 IS 'old function'",
        ):
            sys_.run(statement)

        def snapshot(session: Session, schema: str) -> dict[str, Any]:
            snap: dict[str, Any] = {}
            snap["objects"] = session.rows(
                "SELECT OBJECT_NAME, OBJECT_TYPE, OBJECT_COMMENT FROM EXA_ALL_OBJECTS "
                f"WHERE ROOT_NAME = '{schema}' ORDER BY 1"
            )
            snap["columns"] = session.rows(
                "SELECT COLUMN_TABLE, COLUMN_NAME, COLUMN_COMMENT FROM EXA_ALL_COLUMNS "
                f"WHERE COLUMN_SCHEMA = '{schema}' ORDER BY COLUMN_TABLE, COLUMN_ORDINAL_POSITION"
            )
            snap["t1_rows"] = session.rows(f"SELECT * FROM {schema}.T1 ORDER BY 1")
            snap["t2_rows"] = session.rows(f"SELECT * FROM {schema}.T2 ORDER BY 1")
            snap["v1_text"] = session.rows(
                f"SELECT VIEW_TEXT, VIEW_COMMENT FROM EXA_ALL_VIEWS WHERE VIEW_SCHEMA = '{schema}' "
                "AND VIEW_NAME = 'V1'"
            )
            snap["v2_exists"] = session.rows(
                f"SELECT COUNT(*) FROM EXA_ALL_VIEWS WHERE VIEW_SCHEMA = '{schema}' "
                "AND VIEW_NAME = 'V2'"
            )
            snap["f1_value"] = session.value(f"SELECT {schema}.F1(0)")
            snap["f1_comment"] = session.value(
                f"SELECT FUNCTION_COMMENT FROM EXA_ALL_FUNCTIONS WHERE FUNCTION_SCHEMA = '{schema}' "
                "AND FUNCTION_NAME = 'F1'"
            )
            snap["schemas_like"] = session.rows(
                f"SELECT SCHEMA_NAME FROM EXA_SCHEMAS WHERE SCHEMA_NAME LIKE '{PREFIX}B%' ORDER BY 1"
            )
            return snap

        before = snapshot(sys_, s)
        s2 = f"{PREFIX}B2"
        tx = Session(self.deployment, autocommit=False)
        observer = Session(self.deployment, timeout=20)
        try:
            tx.run(f"OPEN SCHEMA {s}")
            mutations = (
                f"CREATE OR REPLACE TABLE {s}.T1 (X VARCHAR(5)) COMMENT IS 'new table'",
                f"INSERT INTO {s}.T1 VALUES 'n'",
                f"CREATE OR REPLACE VIEW {s}.V1 (A COMMENT IS 'new view col') AS SELECT X AS A "
                f"FROM {s}.T1 COMMENT IS 'new view'",
                f"DROP VIEW {s}.V2",
                f"CREATE OR REPLACE FUNCTION {s}.F1 (X DOUBLE) RETURN DOUBLE IS BEGIN RETURN 2; "
                "END F1;",
                f"COMMENT ON FUNCTION {s}.F1 IS 'new function'",
                f"INSERT INTO {s}.T2 VALUES 99",
                f"CREATE TABLE {s}.T3 (Y INT)",
                f"CREATE SCHEMA {s2}",
                f"CREATE TABLE {s2}.TS (Z INT)",
            )
            statuses = []
            for sql in mutations:
                ok, result = tx.attempt(sql)
                statuses.append("ok" if ok else f"ERROR {result}")
            f.evidence["tx_statements"] = dict(
                zip([m[:60] for m in mutations], statuses, strict=True)
            )
            in_tx = snapshot(tx, s)
            f.evidence["inside_tx_sees_new_state"] = (
                in_tx["f1_value"] == 2
                and in_tx["v2_exists"] == [(0,)]
                and in_tx["t2_rows"] == [(10,), (99,)]
            )
            ok, result = observer.attempt(f"SELECT COUNT(*) FROM {s}.T1")
            f.evidence["other_session_reads_during_tx"] = (
                f"ok count={result[0][0]}" if ok else f"ERROR {result}"
            )
            ok, result = observer.attempt(f"SELECT {s}.F1(0)")
            f.evidence["other_session_function_during_tx"] = (
                f"ok value={result[0][0]}" if ok else f"ERROR {result}"
            )
            ok, result = observer.attempt(
                f"SELECT COUNT(*) FROM EXA_ALL_OBJECTS WHERE ROOT_NAME = '{s2}'"
            )
            f.evidence["other_session_sees_uncommitted_schema_objects"] = (
                result[0][0] if ok else f"ERROR {result}"
            )
            ok, result = tx.attempt(f"CREATE VIEW {s}.VBAD AS SELECT * FROM {s}.NOPE")
            f.evidence["failing_statement_in_tx"] = "ok" if ok else f"ERROR {result}"
            alive = tx.attempt(f"SELECT COUNT(*) FROM {s}.T2")
            f.evidence["tx_usable_after_failing_ddl"] = alive[0] and alive[1][0][0] == 2
            tx.rollback()
            after = snapshot(sys_, s)
            after_tx = snapshot(tx, s)
        finally:
            observer.close()
            tx.close()
        f.evidence["before"] = before
        f.evidence["after_rollback"] = after
        restored = before == after and before == after_tx
        f.evidence["restored_exactly"] = restored
        if not restored:
            f.evidence["diff"] = {
                key: {"before": before[key], "after": after[key]}
                for key in before
                if before[key] != after[key]
            }

        sc = self.schema("BC")
        tx = Session(self.deployment, autocommit=False)
        try:
            tx.run(f"CREATE TABLE {sc}.T (A INT)")
            tx.run(f"INSERT INTO {sc}.T VALUES 1")
            tx.run(f"CREATE VIEW {sc}.V AS SELECT A FROM {sc}.T")
            tx.run(f"SELECT COUNT(*) FROM {sc}.V")
            seen_before = self.sys.value(
                f"SELECT COUNT(*) FROM EXA_ALL_OBJECTS WHERE ROOT_NAME = '{sc}'"
            )
            tx.commit()
            seen_after = self.sys.value(
                f"SELECT COUNT(*) FROM EXA_ALL_OBJECTS WHERE ROOT_NAME = '{sc}'"
            )
        finally:
            tx.close()
        f.evidence["view_over_table_created_in_same_tx_then_commit"] = {
            "objects_visible_to_sys_before_commit": seen_before,
            "after_commit": seen_after,
        }
        f.result = "pass" if restored else "fail"
        f.fallback_needed = not restored
        f.summary = (
            "DDL is transactional: ROLLBACK restored the replaced and dropped objects, rows, "
            "comments and removed the created ones"
            if restored
            else "ROLLBACK did not restore the previous state (see diff)"
        )
        return f

    # ------------------------------------------------------------------ (c)

    def probe_c(self) -> Finding:
        f = Finding("c", TITLES["c"])
        s = self.schema("C")
        sys_ = self.sys
        coords = [
            (-97.1234567890123, 33.4567890123456),
            (-122.4194155, 37.7749295),
            (0.0, 0.0),
            (1e-7, -1e-7),
            (-157.8583333, 21.3069444),
            (179.999999999, -89.999999999),
            (-74.0059413, 40.7127837),
            (1.5e-5, 12345e-5),
            (-96.797, 32.7767),
        ]
        sys_.run(f"CREATE TABLE {s}.XY (ID DECIMAL(3,0), LON DOUBLE, LAT DOUBLE)")
        values = ", ".join(f"({i}, {lon!r}, {lat!r})" for i, (lon, lat) in enumerate(coords))
        sys_.run(f"INSERT INTO {s}.XY VALUES {values}")

        concat = "CAST('POINT (' || LON || ' ' || LAT || ')' AS GEOMETRY(4326))"
        formatted = (
            "CAST('POINT (' || TRIM(TO_CHAR(LON, '9999990.0000000000000')) || ' ' || "
            "TRIM(TO_CHAR(LAT, '9999990.0000000000000')) || ')' AS GEOMETRY(4326))"
        )
        wkt = {}
        for label, expr in (("concat", concat), ("to_char", formatted)):
            ok, result = sys_.attempt(
                "SELECT ID, LON, LAT, ST_X(P), ST_Y(P) FROM "
                f"(SELECT ID, LON, LAT, {expr} AS P FROM {s}.XY) ORDER BY ID"
            )
            if not ok:
                wkt[label] = {"error": result}
                continue
            worst = max(
                max(abs(float(r[3]) - float(r[1])), abs(float(r[4]) - float(r[2]))) for r in result
            )
            wkt[label] = {"max_abs_roundtrip_error": worst, "within_1e-9": worst <= 1e-9}
        ok, result = sys_.attempt(
            "SELECT CAST('POINT (' || LON || ' ' || LAT || ')' AS VARCHAR(100)) "
            f"FROM {s}.XY ORDER BY ID"
        )
        wkt["concat_text"] = [r[0] for r in result] if ok else result
        ok, result = sys_.attempt(
            "SELECT 'POINT (' || LON || ' ' || LAT || ')' FROM "
            f"(SELECT CAST(NULL AS DOUBLE) AS LON, CAST(1 AS DOUBLE) AS LAT FROM DUAL)"
        )
        wkt["null_lon_gives"] = result if ok else result
        ok, result = sys_.attempt("SELECT CAST('POINT ( 1)' AS GEOMETRY(4326)) FROM DUAL")
        wkt["cast_of_wkt_built_from_a_null_coordinate"] = "ok" if ok else f"ERROR {result}"
        ok, result = sys_.attempt(
            "SELECT CASE WHEN LON IS NULL OR LAT IS NULL THEN NULL ELSE "
            "CAST('POINT (' || LON || ' ' || LAT || ')' AS GEOMETRY(4326)) END FROM "
            "(SELECT CAST(NULL AS DOUBLE) AS LON, CAST(1 AS DOUBLE) AS LAT FROM DUAL)"
        )
        wkt["case_guarded_null_coordinate"] = (
            "ok, NULL"
            if ok and result[0][0] is None
            else (f"ERROR {result}" if not ok else f"ok, {result}")
        )
        f.evidence["wkt_from_numbers"] = wkt

        types: dict[str, Any] = {}
        sys_.run(f"CREATE VIEW {s}.V_CAST AS SELECT ID, {concat} AS GEO_POINT FROM {s}.XY")
        sys_.run(
            f"CREATE VIEW {s}.V_SETSRID AS SELECT ID, "
            "ST_SETSRID(CAST('POINT (' || LON || ' ' || LAT || ')' AS GEOMETRY), 4326) AS GEO_POINT "
            f"FROM {s}.XY"
        )
        sys_.run(
            f"CREATE VIEW {s}.V_PLAIN AS SELECT ID, "
            f"CAST('POINT (' || LON || ' ' || LAT || ')' AS GEOMETRY) AS GEO_POINT FROM {s}.XY"
        )
        sys_.run(f"CREATE TABLE {s}.T_CTAS AS SELECT ID, {concat} AS GEO_POINT FROM {s}.XY")
        sys_.run(f"CREATE TABLE {s}.T_COL (ID DECIMAL(3,0), GEO_POINT GEOMETRY(4326))")
        ok, result = sys_.attempt(f"INSERT INTO {s}.T_COL SELECT ID, {concat} FROM {s}.XY")
        types["insert_into_geometry_4326_column"] = "ok" if ok else f"ERROR {result}"
        for name in ("V_CAST", "V_SETSRID", "V_PLAIN", "T_CTAS", "T_COL"):
            types[name] = sys_.value(
                "SELECT COLUMN_TYPE FROM EXA_ALL_COLUMNS "
                f"WHERE COLUMN_SCHEMA = '{s}' AND COLUMN_TABLE = '{name}' AND COLUMN_NAME = 'GEO_POINT'"
            )
        f.evidence["column_types"] = types

        distances: dict[str, Any] = {}
        pair = (
            "SELECT ST_DISTANCE(CAST('POINT (-97 33)' AS GEOMETRY(4326)), "
            "CAST('POINT (-96 33)' AS GEOMETRY(4326)))"
        )
        distances["one_degree_longitude_at_lat_33"] = sys_.value(pair)
        dallas, nyc = (-96.797, 32.7767), (-74.0059413, 40.7127837)
        value = sys_.value(
            f"SELECT ST_DISTANCE(CAST('POINT ({dallas[0]} {dallas[1]})' AS GEOMETRY(4326)), "
            f"CAST('POINT ({nyc[0]} {nyc[1]})' AS GEOMETRY(4326)))"
        )
        distances["dallas_nyc_st_distance_4326"] = value
        distances["dallas_nyc_haversine_km"] = haversine(dallas[1], dallas[0], nyc[1], nyc[0])
        planar_degrees = math.hypot(dallas[0] - nyc[0], dallas[1] - nyc[1])
        distances["dallas_nyc_planar_degrees"] = planar_degrees
        if abs(float(value) - planar_degrees) < 1e-6:
            unit = "planar, in degrees of the coordinates"
        elif abs(float(value) / 1000 - distances["dallas_nyc_haversine_km"]) < 20:
            unit = "geodesic, metres"
        elif abs(float(value) - distances["dallas_nyc_haversine_km"]) < 20:
            unit = "geodesic, kilometres"
        else:
            unit = "unclassified"
        distances["classification"] = unit
        ok, result = sys_.attempt(
            f"SELECT ST_DISTANCE(ST_TRANSFORM(CAST('POINT ({dallas[0]} {dallas[1]})' AS GEOMETRY(4326)), 5070), "
            f"ST_TRANSFORM(CAST('POINT ({nyc[0]} {nyc[1]})' AS GEOMETRY(4326)), 5070))"
        )
        distances["dallas_nyc_after_transform_5070"] = result[0][0] if ok else f"ERROR {result}"
        f.evidence["st_distance"] = distances

        polygon = "POLYGON ((-98 32, -96 32, -96 34, -98 34, -98 32))"
        point = "CAST('POINT (-97 33)' AS GEOMETRY(4326))"
        outside = "CAST('POINT (-90 33)' AS GEOMETRY(4326))"
        within: dict[str, Any] = {}
        variants = {
            "polygon_cast_geometry_4326": f"CAST('{polygon}' AS GEOMETRY(4326))",
            "polygon_plain_string_literal": f"'{polygon}'",
            "polygon_cast_geometry_no_srid": f"CAST('{polygon}' AS GEOMETRY)",
            "polygon_st_setsrid_of_cast": f"ST_SETSRID(CAST('{polygon}' AS GEOMETRY), 4326)",
        }
        for label, expr in variants.items():
            ok_in, res_in = sys_.attempt(f"SELECT ST_WITHIN({point}, {expr}) FROM DUAL")
            ok_out, res_out = sys_.attempt(f"SELECT ST_WITHIN({outside}, {expr}) FROM DUAL")
            within[label] = (
                {"inside": res_in[0][0], "outside": res_out[0][0]}
                if ok_in and ok_out
                else f"ERROR {res_in if not ok_in else res_out}"
            )
        f.evidence["st_within"] = within

        srs = sys_.rows(
            "SELECT SRID, AUTH_NAME, AUTH_SRID, SUBSTR(SRTEXT, 1, 70), SUBSTR(PROJ4TEXT, 1, 90) "
            "FROM EXA_SPATIAL_REF_SYS WHERE SRID IN (4326, 5070) ORDER BY SRID"
        )
        f.evidence["spatial_ref_sys_rows"] = srs
        ok, result = sys_.attempt(
            f"SELECT ST_X(ST_TRANSFORM(P, 5070)), ST_Y(ST_TRANSFORM(P, 5070)) "
            f"FROM (SELECT {point} AS P FROM DUAL)"
        )
        f.evidence["st_transform_4326_to_5070_of_minus97_33"] = (
            result[0] if ok else f"ERROR {result}"
        )
        ok, result = sys_.attempt(f"SELECT ST_SRID(ST_TRANSFORM({point}, 5070)) FROM DUAL")
        f.evidence["st_srid_after_transform"] = result[0][0] if ok else f"ERROR {result}"
        ok, result = sys_.attempt(f"SELECT ST_ASTEXT({point}) FROM DUAL")
        f.evidence["st_astext"] = result[0][0] if ok else f"ERROR {result}"

        roundtrip_ok = all(
            isinstance(v, dict) and v.get("within_1e-9") for k, v in wkt.items() if k in ("concat",)
        )
        f.result = "pass" if roundtrip_ok and types.get("V_CAST") == "GEOMETRY(4326)" else "partial"
        f.summary = (
            f"concatenation round trip within 1e-9: {roundtrip_ok}; view column type with CAST: "
            f"{types.get('V_CAST')}; ST_DISTANCE on 4326: {unit}"
        )
        return f

    # ------------------------------------------------------------------ (d)

    def probe_d(self) -> Finding:
        f = Finding("d", TITLES["d"])
        s = self.schema("D")
        sys_ = self.sys
        body = (
            "(LAT1 IN DOUBLE, LON1 IN DOUBLE, LAT2 IN DOUBLE, LON2 IN DOUBLE)\n"
            "RETURN DOUBLE\n"
            "IS\n"
            "  H DOUBLE;\n"
            "BEGIN\n"
            "  H := POWER(SIN(RADIANS(LAT2 - LAT1) / 2), 2)\n"
            "     + COS(RADIANS(LAT1)) * COS(RADIANS(LAT2)) * POWER(SIN(RADIANS(LON2 - LON1) / 2), 2);\n"
            "  RETURN 2 * 6371.0088 * ASIN(LEAST(1, SQRT(H)));\n"
            "END GREAT_CIRCLE_KM;"
        )
        with_slash = f"CREATE OR REPLACE FUNCTION {s}.GREAT_CIRCLE_KM {body}\n/"
        without_slash = f"CREATE OR REPLACE FUNCTION {s}.GREAT_CIRCLE_KM {body}"
        ok1, r1 = sys_.attempt(without_slash)
        f.evidence["create_without_slash_terminator"] = "ok" if ok1 else f"ERROR {r1}"
        ok2, r2 = sys_.attempt(with_slash)
        f.evidence["create_with_slash_line"] = "ok" if ok2 else f"ERROR {r2}"
        ok3, r3 = sys_.attempt(without_slash + ";")
        f.evidence["create_with_trailing_semicolon_only"] = "ok" if ok3 else f"ERROR {r3}"
        if not (ok1 or ok2):
            f.result, f.fallback_needed = "fail", True
            f.summary = "CREATE FUNCTION per Key Interfaces failed"
            return f
        sys_.run(f"CREATE OR REPLACE FUNCTION {s}.GREAT_CIRCLE_KM {body}")
        ok, r = sys_.attempt(
            f"COMMENT ON FUNCTION {s}.GREAT_CIRCLE_KM IS 'Great-circle (haversine) distance in km'"
        )
        f.evidence["comment_on_function"] = "ok" if ok else f"ERROR {r}"
        f.evidence["function_comment_readback"] = sys_.value(
            "SELECT FUNCTION_COMMENT FROM EXA_ALL_FUNCTIONS "
            f"WHERE FUNCTION_SCHEMA = '{s}' AND FUNCTION_NAME = 'GREAT_CIRCLE_KM'"
        )

        cases = {
            "dallas_nyc": (32.7767, -96.797, 40.7127837, -74.0059413),
            "same_point": (33.0, -97.0, 33.0, -97.0),
            "antipodes_equator": (0.0, 0.0, 0.0, 180.0),
            "pole_to_pole": (90.0, 0.0, -90.0, 0.0),
            "rounding_edge_near_antipode": (0.0000001, 0.0, -0.0000001, 179.9999999),
            "across_dateline": (21.3069444, -157.8583333, 21.3069444, 179.0),
        }
        results = {}
        worst = 0.0
        for label, (a, b, c, d) in cases.items():
            ok, r = sys_.attempt(f"SELECT {s}.GREAT_CIRCLE_KM({a!r}, {b!r}, {c!r}, {d!r})")
            expected = haversine(a, b, c, d)
            if ok:
                got = float(r[0][0])
                worst = max(worst, abs(got - expected))
                results[label] = {"got": got, "python": expected}
            else:
                results[label] = f"ERROR {r}"
        f.evidence["values"] = results
        f.evidence["max_abs_diff_km_vs_python"] = worst

        nulls = {}
        for label, args in {
            "null_lat1": "NULL, -97, 33, -97",
            "null_lon2": "33, -97, 33, NULL",
            "all_null": "NULL, NULL, NULL, NULL",
        }.items():
            ok, r = sys_.attempt(f"SELECT {s}.GREAT_CIRCLE_KM({args})")
            nulls[label] = r[0][0] if ok else f"ERROR {r}"
        f.evidence["null_inputs_give"] = nulls
        ok, r = sys_.attempt(
            f"SELECT {s}.GREAT_CIRCLE_KM(CAST(32.7767 AS DECIMAL(9,4)), CAST(-96.797 AS DECIMAL(9,3)), 40, -74)"
        )
        f.evidence["decimal_and_integer_arguments"] = r[0][0] if ok else f"ERROR {r}"

        other = self.schema("D2")
        ok, r = sys_.attempt(
            f"CREATE VIEW {other}.V AS SELECT {s}.GREAT_CIRCLE_KM(LAT1, LON1, LAT2, LON2) AS KM FROM "
            "(SELECT 32.7767 AS LAT1, -96.797 AS LON1, 40.7127837 AS LAT2, -74.0059413 AS LON2 FROM DUAL)"
        )
        f.evidence["view_in_other_schema_calls_function"] = "ok" if ok else f"ERROR {r}"
        if ok:
            f.evidence["view_in_other_schema_value"] = sys_.value(f"SELECT KM FROM {other}.V")

        sys_.run(f"CREATE TABLE {other}.N AS SELECT LEVEL AS L FROM DUAL CONNECT BY LEVEL <= 20000")
        bulk = sys_.rows(
            f"SELECT COUNT(*), SUM({s}.GREAT_CIRCLE_KM(L / 1000.0, L / 500.0, 40, -74)) "
            f"FROM {other}.N"
        )
        f.evidence["20000_row_call_count"] = bulk[0][0]

        null_ok = all(v is None for v in nulls.values())
        values_ok = worst < 1e-6
        f.result = "pass" if values_ok and null_ok else "partial"
        f.fallback_needed = not values_ok
        f.summary = f"function compiles; max diff vs Python haversine {worst:.2e} km; NULL in -> NULL: {null_ok}"
        return f

    # ------------------------------------------------------------------ (e)

    def probe_e(self) -> Finding:
        f = Finding("e", TITLES["e"])
        s = self.schema("E")
        m = self.schema("EM")
        sys_ = self.sys
        for statement in (
            f"CREATE TABLE {s}.T (A INT, B DOUBLE)",
            f"CREATE VIEW {s}.V1 AS SELECT A, B FROM {s}.T",
            f"CREATE VIEW {s}.V2 AS SELECT A FROM {s}.V1",
            f"CREATE TABLE {m}.MT (K INT)",
            f"CREATE VIEW {m}.META_V AS SELECT K FROM {m}.MT",
            f"CREATE VIEW {s}.V3 AS SELECT K FROM {m}.META_V",
            f"CREATE FUNCTION {s}.F (X DOUBLE) RETURN DOUBLE IS BEGIN RETURN X * 2; END F;",
            f"CREATE VIEW {s}.V4 AS SELECT {s}.F(B) AS B2 FROM {s}.T",
            f"CREATE VIEW {s}.V5 AS SELECT V4.B2, V3.K FROM {s}.V4 JOIN {s}.V3 ON 1 = 1",
        ):
            sys_.run(statement)
        direct = sys_.rows(
            "SELECT OBJECT_SCHEMA || '.' || OBJECT_NAME, OBJECT_TYPE, REFERENCE_TYPE, "
            "REFERENCED_OBJECT_SCHEMA || '.' || REFERENCED_OBJECT_NAME, REFERENCED_OBJECT_TYPE "
            f"FROM EXA_ALL_DEPENDENCIES WHERE OBJECT_SCHEMA IN ('{s}', '{m}') ORDER BY 1, 4"
        )
        f.evidence["direct_dependencies"] = direct
        recursive = sys_.rows(
            "SELECT OBJECT_NAME, REFERENCED_OBJECT_SCHEMA || '.' || REFERENCED_OBJECT_NAME, "
            "DEPENDENCY_LEVEL FROM EXA_DBA_DEPENDENCIES_RECURSIVE "
            f"WHERE OBJECT_SCHEMA = '{s}' AND OBJECT_NAME = 'V5' ORDER BY 3, 2"
        )
        f.evidence["recursive_from_v5"] = recursive
        pairs = {(row[0], row[3]) for row in direct}
        expectations = {
            "view_to_view": (f"{s}.V2", f"{s}.V1") in pairs,
            "view_to_table": (f"{s}.V1", f"{s}.T") in pairs,
            "view_to_meta_view": (f"{s}.V3", f"{m}.META_V") in pairs,
            "view_to_function": (f"{s}.V4", f"{s}.F") in pairs,
        }
        f.evidence["expected_pairs_present"] = expectations
        rec_ok = len(recursive) > 0
        f.evidence["recursive_view_nonempty"] = rec_ok
        everything = all(expectations.values())
        f.result = "pass" if everything else "partial"
        f.fallback_needed = not everything
        f.summary = f"dependency pairs present: {expectations}"
        return f

    # ------------------------------------------------------------------ (f)

    def probe_f(self) -> Finding:
        f = Finding("f", TITLES["f"])
        s = self.schema("F")
        sys_ = self.sys

        def view_state(view: str) -> dict[str, Any]:
            return {
                "view_row": sys_.rows(
                    "SELECT VIEW_NAME, VIEW_COMMENT FROM EXA_ALL_VIEWS "
                    f"WHERE VIEW_SCHEMA = '{s}' AND VIEW_NAME = '{view}'"
                ),
                "columns": sys_.rows(
                    "SELECT COLUMN_NAME, COLUMN_TYPE, STATUS, COLUMN_COMMENT FROM EXA_ALL_COLUMNS "
                    f"WHERE COLUMN_SCHEMA = '{s}' AND COLUMN_TABLE = '{view}' "
                    "ORDER BY COLUMN_ORDINAL_POSITION"
                ),
            }

        ok, r = sys_.attempt(f"CREATE OR REPLACE VIEW {s}.PLAIN_V AS SELECT A, B FROM {s}.LATER")
        f.evidence["f1_plain_view_over_missing_table"] = "created" if ok else f"ERROR {r}"

        ok, r = sys_.attempt(
            f"CREATE OR REPLACE FORCE VIEW {s}.FV (A COMMENT IS 'comment a', B COMMENT IS 'comment b') "
            f"AS SELECT A, B FROM {s}.LATER COMMENT IS 'force view comment'"
        )
        f.evidence["f2_force_view_over_missing_table"] = "created" if ok else f"ERROR {r}"
        f.evidence["f2_state_while_missing"] = view_state("FV")
        f.evidence["f2_dependencies_while_missing"] = sys_.rows(
            "SELECT OBJECT_NAME, REFERENCED_OBJECT_SCHEMA || '.' || REFERENCED_OBJECT_NAME "
            f"FROM EXA_ALL_DEPENDENCIES WHERE OBJECT_SCHEMA = '{s}' AND OBJECT_NAME = 'FV'"
        )
        ok, r = sys_.attempt(f"SELECT * FROM {s}.FV")
        f.evidence["f3_query_while_missing"] = "worked" if ok else f"ERROR {r}"
        ok, r = sys_.attempt(f"SELECT 1 FROM {s}.FV WHERE 1 = 0")
        f.evidence["f3_query_where_false_while_missing"] = "worked" if ok else f"ERROR {r}"

        sys_.run(f"CREATE TABLE {s}.LATER (A DECIMAL(9,0), B VARCHAR(10) UTF8)")
        sys_.run(f"INSERT INTO {s}.LATER VALUES (1, 'x')")
        ok, r = sys_.attempt(f"SELECT * FROM {s}.FV")
        f.evidence["f4_query_after_table_exists_no_recreate"] = (
            f"worked rows={r}" if ok else f"ERROR {r}"
        )
        f.evidence["f4_state_after_first_query"] = view_state("FV")
        ok, r = sys_.attempt(f"SELECT 1 FROM {s}.FV WHERE 1 = 0")
        f.evidence["f4_where_false_probe_works"] = ok

        sys_.attempt(f"CREATE OR REPLACE FORCE VIEW {s}.CHAIN_A AS SELECT A FROM {s}.CHAIN_B")
        ok_b, r_b = sys_.attempt(
            f"CREATE OR REPLACE FORCE VIEW {s}.CHAIN_B AS SELECT A FROM {s}.LATER2"
        )
        sys_.run(f"CREATE TABLE {s}.LATER2 (A INT)")
        sys_.run(f"INSERT INTO {s}.LATER2 VALUES 7")
        ok, r = sys_.attempt(f"SELECT * FROM {s}.CHAIN_A")
        f.evidence["f5_chain_a_over_b_over_t_created_in_reverse_order"] = (
            f"worked rows={r}" if ok else f"ERROR {r}"
        )

        sys_.run(f"CREATE OR REPLACE TABLE {s}.LATER (A DECIMAL(9,0), B VARCHAR(10) UTF8)")
        f.evidence["f6_state_after_replace_same_columns"] = view_state("FV")
        ok, r = sys_.attempt(f"SELECT * FROM {s}.FV")
        f.evidence["f6_query_after_replace_same_columns"] = "worked" if ok else f"ERROR {r}"
        f.evidence["f6_state_after_query"] = view_state("FV")
        sys_.run(f"CREATE OR REPLACE TABLE {s}.LATER (A DECIMAL(9,0), C VARCHAR(10) UTF8)")
        f.evidence["f6_state_after_replace_dropping_B"] = view_state("FV")
        ok, r = sys_.attempt(f"SELECT * FROM {s}.FV")
        f.evidence["f6_query_after_replace_dropping_B"] = "worked" if ok else f"ERROR {r}"
        f.evidence["f6_state_after_failed_query"] = view_state("FV")
        sys_.run(f"CREATE OR REPLACE TABLE {s}.LATER (A DECIMAL(9,0), B VARCHAR(10) UTF8)")
        ok, r = sys_.attempt(f"SELECT * FROM {s}.FV")
        f.evidence["f6_query_after_restoring_B"] = "worked" if ok else f"ERROR {r}"
        f.evidence["f6_state_after_restoring_B"] = view_state("FV")

        ok, r = sys_.attempt(f"DROP TABLE {s}.LATER")
        f.evidence["f7_drop_table_referenced_by_view"] = "dropped" if ok else f"ERROR {r}"
        f.evidence["f7_state_after_drop"] = view_state("FV")
        ok, r = sys_.attempt(f"SELECT * FROM {s}.FV")
        f.evidence["f7_query_after_drop"] = "worked" if ok else f"ERROR {r}"
        sys_.run(f"CREATE TABLE {s}.LATER (A DECIMAL(9,0), B VARCHAR(10) UTF8)")
        ok, r = sys_.attempt(f"SELECT * FROM {s}.FV")
        f.evidence["f7_query_after_recreate"] = "worked" if ok else f"ERROR {r}"

        sys_.attempt(
            f"CREATE OR REPLACE FORCE VIEW {s}.FV3 (A COMMENT IS 'a', B COMMENT IS 'b', "
            f"C COMMENT IS 'c') AS SELECT A, B, C FROM {s}.NEVER"
        )
        sys_.run(f"CREATE TABLE {s}.NEVER (A INT, B INT)")
        ok, r = sys_.attempt(f"SELECT * FROM {s}.FV3")
        f.evidence["f8_declared_3_columns_but_table_has_2"] = "worked" if ok else f"ERROR {r}"
        f.evidence["f8_state"] = view_state("FV3")

        ok, r = sys_.attempt(
            f"CREATE OR REPLACE FORCE VIEW {s}.FSTAR AS SELECT * FROM {s}.MISSING_STAR"
        )
        f.evidence["f9_force_select_star_over_missing_table"] = "created" if ok else f"ERROR {r}"

        ok, r = sys_.attempt(
            f"CREATE OR REPLACE FORCE VIEW {s}.FV4 (A COMMENT IS 'a') AS SELECT A FROM {s}.NEVER "
            "COMMENT IS 'over an existing table'"
        )
        f.evidence["f10_force_view_over_existing_table"] = "created" if ok else f"ERROR {r}"
        f.evidence["f10_state"] = view_state("FV4")

        ok, r = sys_.attempt(
            f"CREATE OR REPLACE FORCE VIEW {s}.FV5 AS SELECT NO_SUCH_COLUMN FROM {s}.NEVER"
        )
        f.evidence["f11_force_view_over_existing_table_with_bad_column"] = (
            "created" if ok else f"ERROR {r}"
        )
        if ok:
            ok2, r2 = sys_.attempt(f"SELECT * FROM {s}.FV5")
            f.evidence["f11_query_of_that_view"] = "worked" if ok2 else f"ERROR {r2}"

        ok, r = sys_.attempt(
            f"CREATE OR REPLACE FORCE VIEW {s}.FW (A COMMENT IS 'ca') AS SELECT A, B FROM {s}.NEVER "
            "COMMENT IS 'declared 1 column, selects 2'"
        )
        f.evidence["f12_declared_1_column_but_select_has_2_at_creation"] = (
            "created" if ok else f"ERROR {r}"
        )
        if ok:
            ok2, r2 = sys_.attempt(f"SELECT 1 FROM {s}.FW WHERE 1 = 0")
            f.evidence["f12_compile_query"] = "worked" if ok2 else f"ERROR {r2}"
        sys_.run(
            f"CREATE OR REPLACE FORCE VIEW {s}.FX (A COMMENT IS 'ca', B COMMENT IS 'cb') AS "
            f"SELECT A, B FROM {s}.NEVER COMMENT IS 'compiled by WHERE 1 = 0'"
        )
        f.evidence["f13_columns_before_compile_query"] = view_state("FX")["columns"]
        ok, r = sys_.attempt(f"SELECT 1 FROM {s}.FX WHERE 1 = 0")
        f.evidence["f13_compile_by_where_false"] = "worked" if ok else f"ERROR {r}"
        f.evidence["f13_columns_after_compile_query"] = view_state("FX")["columns"]
        sys_.run(f"CREATE OR REPLACE VIEW {s}.PX (A COMMENT IS 'ca') AS SELECT A FROM {s}.NEVER")
        f.evidence["f14_plain_view_columns_visible_at_creation"] = view_state("PX")["columns"]

        plain_fails = "ERROR" in str(f.evidence["f1_plain_view_over_missing_table"])
        created = f.evidence["f2_force_view_over_missing_table"] == "created"
        later_works = str(f.evidence["f4_query_after_table_exists_no_recreate"]).startswith(
            "worked"
        )
        sf = self.schema("F2")
        tx = Session(self.deployment, autocommit=False)
        try:
            tx.run(
                f"CREATE OR REPLACE FORCE VIEW {sf}.V1 (A COMMENT IS 'ca1') AS SELECT A FROM {sf}.T "
                "COMMENT IS 'vc1'"
            )
            tx.run(f"CREATE TABLE {sf}.T (A INT)")
            tx.run(
                f"CREATE OR REPLACE FORCE VIEW {sf}.V2 (A COMMENT IS 'ca2') AS SELECT A FROM {sf}.T "
                "COMMENT IS 'vc2'"
            )
            tx.run(f"SELECT 1 FROM {sf}.V1 WHERE 1 = 0")
            tx.commit()
        finally:
            tx.close()
        read = self.comments(sf)
        f.evidence["f15_force_views_created_before_their_table_in_one_tx_v1_compiled_in_tx"] = {
            "columns_after_commit": read["columns"],
            "objects_after_commit": {k: v[1] for k, v in read["objects"].items()},
        }
        sys_.run(f"SELECT 1 FROM {sf}.V2 WHERE 1 = 0")
        f.evidence["f15_columns_after_compiling_v2_from_another_session"] = self.comments(sf)[
            "columns"
        ]

        view_comment_stored = bool(
            created
            and f.evidence["f2_state_while_missing"]["view_row"][0][1] == "force view comment"
        )
        columns_hidden_until_compile = f.evidence["f2_state_while_missing"]["columns"] == []
        columns_comments_after_compile = [
            col[3] for col in f.evidence["f4_state_after_first_query"]["columns"]
        ] == ["comment a", "comment b"]
        f.evidence["summary_flags"] = {
            "plain_view_over_missing_fails": plain_fails,
            "force_view_created": created,
            "force_view_works_after_table_appears": later_works,
            "view_comment_stored_while_missing": view_comment_stored,
            "column_rows_hidden_until_first_compile": columns_hidden_until_compile,
            "declared_column_comments_visible_after_compile": columns_comments_after_compile,
        }
        comments_stored = view_comment_stored and columns_comments_after_compile
        positive = plain_fails and created and later_works and comments_stored
        f.result = "pass" if positive else "partial"
        f.fallback_needed = not (created and later_works)
        f.summary = f"flags: {f.evidence['summary_flags']}"
        return f

    # ------------------------------------------------------------------ (g)

    SEASONS = range(1990, 2020)
    ROWS_PER_SEASON_GAME = 2000

    def profile(self, session: Session, marker: str, sql: str) -> dict[str, Any]:
        """Run `sql` under PROFILE ON and return the scan and join parts of its profile."""
        session.run("ALTER SESSION SET PROFILE = 'ON'")
        started = time.time()
        rows = session.rows(f"/* {marker} */ {sql}")
        elapsed = time.time() - started
        session.run("ALTER SESSION SET PROFILE = 'OFF'")
        session.run("FLUSH STATISTICS")
        parts = session.rows(
            "SELECT PART_ID, PART_NAME, PART_INFO, OBJECT_SCHEMA, OBJECT_NAME, OBJECT_ROWS, OUT_ROWS "
            "FROM EXA_USER_PROFILE_LAST_DAY WHERE SESSION_ID = CURRENT_SESSION AND STMT_ID = ("
            "SELECT MAX(STMT_ID) FROM EXA_USER_PROFILE_LAST_DAY "
            f"WHERE SESSION_ID = CURRENT_SESSION AND SQL_TEXT LIKE '%{marker}%' "
            "AND SQL_TEXT NOT LIKE '%EXA_USER_PROFILE_LAST_DAY%') ORDER BY PART_ID"
        )
        scans = [
            {"table": f"{p[3]}.{p[4]}", "table_rows": p[5], "out_rows": p[6], "part": p[1]}
            for p in parts
            if p[4] is not None and p[3] is not None
        ]
        return {
            "result": [tuple(r) for r in rows[:3]],
            "seconds": round(elapsed, 3),
            "scans": scans,
            "parts": [(p[0], p[1], (p[2] or "")[:40], p[4], p[5], p[6]) for p in parts],
        }

    def probe_g(self) -> Finding:
        f = Finding("g", TITLES["g"])
        s = self.schema("G")
        sys_ = self.sys
        sys_.run(
            f"CREATE TABLE {s}.F (SEASON DECIMAL(4,0), GAME_ID DECIMAL(9,0), WEEK_SEQ DECIMAL(3,0), "
            "HOME DECIMAL(5,0), MARGIN DECIMAL(5,0), REQ_AT TIMESTAMP)"
        )
        n_seasons = len(self.SEASONS)
        per = self.ROWS_PER_SEASON_GAME
        sys_.run(
            f"INSERT INTO {s}.F "
            f"SELECT 1990 + MOD(L, {n_seasons}), 100000 * MOD(L, {n_seasons}) + MOD(FLOOR(L / {n_seasons}), {per}), "
            f"1 + MOD(MOD(FLOOR(L / {n_seasons}), {per}), 15), "
            f"1 + MOD(MOD(FLOOR(L / {n_seasons}), {per}) * 7, 130), "
            f"MOD(L * 13, 41) - 20, "
            f"TIMESTAMP '2026-01-01 00:00:00' + FLOOR(L / ({n_seasons} * {per})) "
            f"FROM (SELECT LEVEL - 1 AS L FROM DUAL CONNECT BY LEVEL <= {n_seasons * per * 2})"
        )
        total = sys_.value(f"SELECT COUNT(*) FROM {s}.F")
        f.evidence["fact_rows"] = total
        sys_.run(f"CREATE TABLE {s}.POINTS (SEASON DECIMAL(4,0), WEEK_SEQ DECIMAL(3,0))")
        sys_.run(
            f"INSERT INTO {s}.POINTS SELECT 1990 + MOD(L, {n_seasons}), 1 + MOD(FLOOR(L / {n_seasons}), 15) "
            f"FROM (SELECT LEVEL - 1 AS L FROM DUAL CONNECT BY LEVEL <= {n_seasons * 15})"
        )
        for statement in (
            f'CREATE VIEW {s}.GAME_V AS SELECT "SEASON", "GAME_ID", "WEEK_SEQ", "HOME", "MARGIN" '
            f'FROM {s}.F QUALIFY ROW_NUMBER() OVER (PARTITION BY "SEASON", "GAME_ID" '
            'ORDER BY "REQ_AT" DESC) = 1',
            f'CREATE VIEW {s}.TG_V AS SELECT "SEASON", "WEEK_SEQ", "HOME" AS TEAM, "GAME_ID", '
            'SUM("MARGIN") OVER (PARTITION BY "SEASON", "HOME" ORDER BY "GAME_ID") AS RUN_MARGIN '
            f"FROM {s}.GAME_V",
            f"CREATE VIEW {s}.AS_OF_POINT AS SELECT SEASON, WEEK_SEQ FROM {s}.POINTS",
            f'CREATE VIEW {s}.AS_OF AS SELECT P."SEASON" AS POINT_SEASON, P."WEEK_SEQ" AS POINT_WEEK, '
            f'T.TEAM, T."GAME_ID", T.RUN_MARGIN FROM {s}.AS_OF_POINT P JOIN {s}.TG_V T '
            'ON T."SEASON" = P."SEASON" AND T."WEEK_SEQ" <= P."WEEK_SEQ"',
            f'CREATE VIEW {s}.TS_V AS SELECT "SEASON", "HOME" AS TEAM, SUM("MARGIN") AS M '
            f'FROM {s}.GAME_V GROUP BY "SEASON", "HOME"',
            f"CREATE VIEW {s}.CHECKS AS "
            f'SELECT \'C1\' AS CHECK_NAME, "SEASON", COUNT(*) AS OBSERVED FROM {s}.GAME_V GROUP BY "SEASON" '
            "UNION ALL "
            f'SELECT \'C2\', "SEASON", MAX(RUN_MARGIN) FROM {s}.TG_V GROUP BY "SEASON" '
            "UNION ALL "
            f'SELECT \'C3\', "SEASON", COUNT(*) FROM (SELECT "SEASON", "GAME_ID", ROW_NUMBER() OVER '
            f'(PARTITION BY "SEASON" ORDER BY "GAME_ID" DESC) AS RN FROM {s}.GAME_V) WHERE RN = 1 '
            'GROUP BY "SEASON"',
            f"CREATE VIEW {s}.CHECKS_INLINE_FILTER AS "
            f"SELECT 'C1' AS CHECK_NAME, \"SEASON\", COUNT(*) AS OBSERVED FROM {s}.GAME_V "
            'WHERE "SEASON" = 2010 GROUP BY "SEASON"',
        ):
            sys_.run(statement)
        sys_.run("ALTER SESSION SET QUERY_CACHE = 'OFF'")
        game_rows = f"{per} games x 2 versions"
        f.evidence["layout"] = (
            f"{n_seasons} seasons x {per} games x 2 versions = {total} fact rows; "
            f"one season holds {per * 2} fact rows ({game_rows})"
        )
        season_rows = per * 2

        def max_fact(profile: dict[str, Any]) -> list[Any]:
            return [
                (scan["table"].split(".")[-1], scan["table_rows"], scan["out_rows"])
                for scan in profile["scans"]
            ]

        g1: dict[str, Any] = {}
        base = self.profile(
            sys_, "PMG1BASE", f"SELECT COUNT(*), SUM(RUN_MARGIN) FROM {s}.TG_V WHERE TEAM >= 0"
        )
        g1["window_view_unfiltered_scans"] = max_fact(base)
        seasoned = self.profile(
            sys_,
            "PMG1SEASON",
            f'SELECT COUNT(*), SUM(RUN_MARGIN) FROM {s}.TG_V WHERE "SEASON" = 2010',
        )
        g1["window_view_season_filter_scans"] = max_fact(seasoned)
        g1["season_filter_result"] = seasoned["result"]
        g1["season_filter_result_inline_check"] = sys_.rows(
            f"SELECT COUNT(*), SUM(RUN_MARGIN) FROM (SELECT SEASON, SUM(MARGIN) OVER (PARTITION BY SEASON, HOME "
            f"ORDER BY GAME_ID) AS RUN_MARGIN FROM (SELECT * FROM {s}.F WHERE SEASON = 2010 QUALIFY "
            "ROW_NUMBER() OVER (PARTITION BY SEASON, GAME_ID ORDER BY REQ_AT DESC) = 1))"
        )
        f.evidence["g1_season_filter_through_window_view"] = g1

        g2: dict[str, Any] = {}
        point_where = "POINT_SEASON = 2010 AND POINT_WEEK = 5"
        unfiltered = self.profile(
            sys_, "PMG2NOFILTER", f"SELECT COUNT(*), SUM(RUN_MARGIN) FROM {s}.AS_OF"
        )
        g2["as_of_unfiltered_scans"] = max_fact(unfiltered)
        filtered = self.profile(
            sys_,
            "PMG2POINT",
            f"SELECT COUNT(*), SUM(RUN_MARGIN) FROM {s}.AS_OF WHERE {point_where}",
        )
        g2["as_of_point_filter_scans"] = max_fact(filtered)
        g2["as_of_point_filter_parts"] = filtered["parts"]
        g2["point_filter_result"] = filtered["result"]
        g2["inline_check"] = sys_.rows(
            f'SELECT COUNT(*), SUM(RUN_MARGIN) FROM {s}.TG_V WHERE "SEASON" = 2010 AND "WEEK_SEQ" <= 5'
        )
        g2["seconds_unfiltered_vs_point"] = (unfiltered["seconds"], filtered["seconds"])
        f.evidence["g2_point_filter_before_join"] = g2

        g3: dict[str, Any] = {}
        union_all = self.profile(
            sys_, "PMG3NOFILTER", f"SELECT COUNT(*), SUM(OBSERVED) FROM {s}.CHECKS"
        )
        g3["union_unfiltered_scans"] = max_fact(union_all)
        union_season = self.profile(
            sys_,
            "PMG3SEASON",
            f'SELECT COUNT(*), SUM(OBSERVED) FROM {s}.CHECKS WHERE "SEASON" = 2010',
        )
        g3["union_season_filter_scans"] = max_fact(union_season)
        g3["union_season_filter_parts"] = union_season["parts"]
        g3["union_season_result"] = union_season["result"]
        g3["union_season_check_inline"] = sys_.rows(
            f'SELECT COUNT(*) FROM {s}.CHECKS WHERE "SEASON" = 2010'
        )
        inlined = self.profile(
            sys_,
            "PMG3INLINE",
            f"SELECT COUNT(*), SUM(OBSERVED) FROM (SELECT 'C1' AS CHECK_NAME, \"SEASON\", COUNT(*) AS OBSERVED "
            f'FROM (SELECT * FROM {s}.F WHERE "SEASON" = 2010 QUALIFY ROW_NUMBER() OVER (PARTITION BY '
            '"SEASON", "GAME_ID" ORDER BY "REQ_AT" DESC) = 1) GROUP BY "SEASON")',
        )
        g3["manual_inline_filter_scans"] = max_fact(inlined)
        g3["seconds_unfiltered_vs_season"] = (union_all["seconds"], union_season["seconds"])
        f.evidence["g3_union_all_season_filter"] = g3

        g4: dict[str, Any] = {}
        prior = self.profile(
            sys_,
            "PMG4PRIOR",
            f"SELECT COUNT(*), SUM(a.M - COALESCE(b.M, 0)) FROM {s}.TS_V a LEFT JOIN {s}.TS_V b "
            'ON b."SEASON" = a."SEASON" - 1 AND b.TEAM = a.TEAM WHERE a."SEASON" = 2010',
        )
        g4["prior_season_join_scans"] = max_fact(prior)
        g4["prior_season_result"] = prior["result"]
        both = self.profile(
            sys_,
            "PMG4BOTH",
            f"SELECT COUNT(*), SUM(a.M - COALESCE(b.M, 0)) FROM {s}.TS_V a LEFT JOIN {s}.TS_V b "
            'ON b."SEASON" = a."SEASON" - 1 AND b.TEAM = a.TEAM '
            'WHERE a."SEASON" = 2010 AND b."SEASON" IN (2009, 2010)',
        )
        g4["prior_season_join_with_explicit_b_filter_scans"] = max_fact(both)
        g4["prior_season_join_with_explicit_b_filter_result"] = both["result"]
        g4["prior_season_join_with_explicit_b_filter_note"] = (
            "inner-join semantics differ; scans only"
        )
        inline_in = self.profile(
            sys_,
            "PMG4IN",
            f"SELECT COUNT(*), SUM(a.M - COALESCE(b.M, 0)) FROM "
            f'(SELECT * FROM {s}.TS_V WHERE "SEASON" IN (2009, 2010)) a '
            f'LEFT JOIN (SELECT * FROM {s}.TS_V WHERE "SEASON" IN (2009, 2010)) b '
            'ON b."SEASON" = a."SEASON" - 1 AND b.TEAM = a.TEAM WHERE a."SEASON" = 2010',
        )
        g4["prior_season_join_with_in_filter_on_both_sides_scans"] = max_fact(inline_in)
        g4["prior_season_join_with_in_filter_on_both_sides_result"] = inline_in["result"]
        f.evidence["g4_prior_season_join"] = g4

        f.evidence["season_rows_in_fact"] = season_rows
        g1_reach = [scan[2] for scan in g1["window_view_season_filter_scans"] if scan[0] == "F"]
        g2_reach = [scan[2] for scan in g2["as_of_point_filter_scans"] if scan[0] == "F"]
        g3_reach = [scan[2] for scan in g3["union_season_filter_scans"] if scan[0] == "F"]
        g3_unfiltered = [scan[2] for scan in g3["union_unfiltered_scans"] if scan[0] == "F"]
        pruned = lambda reach: bool(reach) and all(r <= season_rows for r in reach)  # noqa: E731
        f.evidence["fact_rows_read"] = {
            "season_rows_expected_when_pruned": season_rows,
            "g1_window_view_season_filter": g1_reach,
            "g2_point_filter_join": g2_reach,
            "g3_union_all_season_filter": g3_reach,
            "g3_union_all_unfiltered": g3_unfiltered,
            "g1_pruned": pruned(g1_reach),
            "g2_pruned": pruned(g2_reach),
            "g3_pruned": pruned(g3_reach),
        }
        f.result = "pass" if pruned(g2_reach) and pruned(g3_reach) else "partial"
        f.fallback_needed = not (pruned(g2_reach) and pruned(g3_reach))
        f.summary = (
            f"fact rows read: window view season filter {g1_reach}, as-of point filter {g2_reach}, "
            f"UNION ALL season filter {g3_reach} (a single season holds {season_rows})"
        )
        return f

    # ------------------------------------------------------------------ (h)

    def probe_h(self) -> Finding:
        f = Finding("h", TITLES["h"])
        s = self.schema("H")
        sys_ = self.sys
        sys_.run(f"CREATE TABLE {s}.LOGT (ID DECIMAL(9,0), MSG VARCHAR(200) UTF8)")
        sys_.run(f"CREATE TABLE {s}.RUNS (ID DECIMAL(9,0), STATUS VARCHAR(20) UTF8)")
        sys_.run(f"CREATE TABLE {s}.CHECK_LOG (SEASON DECIMAL(4,0), CHECK_NAME VARCHAR(40) UTF8)")
        sys_.run(
            f"CREATE VIEW {s}.CHECKS AS SELECT 2025 AS SEASON, 'C_ERR' AS CHECK_NAME FROM DUAL "
            "UNION ALL SELECT 2024, 'C_OLD' FROM DUAL"
        )
        scripts = {
            "INNER_COMMIT_THEN_ERROR": (
                f"CREATE OR REPLACE LUA SCRIPT {s}.INNER_COMMIT_THEN_ERROR () RETURNS ROWCOUNT AS\n"
                f"query([[INSERT INTO {s}.LOGT VALUES (2, 'inner before commit')]])\n"
                "query([[COMMIT]])\n"
                f"query([[INSERT INTO {s}.LOGT VALUES (3, 'inner after commit')]])\n"
                "error('inner boom', 0)\n/"
            ),
            "OUTER_PQUERY": (
                f"CREATE OR REPLACE LUA SCRIPT {s}.OUTER_PQUERY () RETURNS ROWCOUNT AS\n"
                f"query([[INSERT INTO {s}.LOGT VALUES (1, 'outer before')]])\n"
                f"local ok, res = pquery([[EXECUTE SCRIPT {s}.INNER_COMMIT_THEN_ERROR()]])\n"
                f"query([[INSERT INTO {s}.LOGT VALUES (4, 'outer after, ok=' || '' || ']] .. tostring(ok) .. [[')]])\n"
                "exit({rows_affected = 0})\n/"
            ),
            "OUTER_QUERY": (
                f"CREATE OR REPLACE LUA SCRIPT {s}.OUTER_QUERY () RETURNS ROWCOUNT AS\n"
                f"query([[INSERT INTO {s}.LOGT VALUES (1, 'outer before')]])\n"
                f"query([[EXECUTE SCRIPT {s}.INNER_COMMIT_THEN_ERROR()]])\n"
                f"query([[INSERT INTO {s}.LOGT VALUES (5, 'never reached')]])\n/"
            ),
            "STEP": (
                f"CREATE OR REPLACE LUA SCRIPT {s}.STEP (run_id, season) RETURNS ROWCOUNT AS\n"
                f"query([[INSERT INTO {s}.CHECK_LOG SELECT SEASON, CHECK_NAME FROM {s}.CHECKS "
                "WHERE SEASON = :season]], {season = season})\n"
                "query([[COMMIT]])\n"
                "error('model checks found errors', 0)\n/"
            ),
            "REFRESH_LIKE": (
                f"CREATE OR REPLACE LUA SCRIPT {s}.REFRESH_LIKE (run_id, season) RETURNS ROWCOUNT AS\n"
                f"query([[INSERT INTO {s}.RUNS VALUES (:r, 'STARTED')]], {{r = run_id}})\n"
                "query([[COMMIT]])\n"
                f"local ok, res = pquery([[EXECUTE SCRIPT {s}.STEP(:r, :s)]], {{r = run_id, s = season}})\n"
                "if not ok then\n"
                "  query([[ROLLBACK]])\n"
                f"  query([[INSERT INTO {s}.RUNS VALUES (:r, 'FAILED')]], {{r = run_id}})\n"
                "  query([[COMMIT]])\n"
                "  error(res.error_message, 0)\n"
                "end\n/"
            ),
        }
        for sql in scripts.values():
            sys_.run(sql)

        def logt(session: Session) -> list[Any]:
            return [r[0] for r in session.rows(f"SELECT ID FROM {s}.LOGT ORDER BY 1")]

        tx = Session(self.deployment, autocommit=False)
        observer = Session(self.deployment)
        scenarios: dict[str, Any] = {}
        try:
            for label, script in (("pquery_outer", "OUTER_PQUERY"), ("query_outer", "OUTER_QUERY")):
                sys_.run(f"DELETE FROM {s}.LOGT")
                ok, result = tx.attempt(f"EXECUTE SCRIPT {s}.{script}()")
                during = logt(observer)
                tx.rollback()
                scenarios[f"{label}_autocommit_off"] = {
                    "script_result": "ok" if ok else f"ERROR {result}",
                    "ids_visible_to_other_session_before_rollback": during,
                    "ids_after_caller_rollback": logt(observer),
                }
            tx_auto = Session(self.deployment, autocommit=True)
            try:
                for label, script in (
                    ("pquery_outer", "OUTER_PQUERY"),
                    ("query_outer", "OUTER_QUERY"),
                ):
                    sys_.run(f"DELETE FROM {s}.LOGT")
                    ok, result = tx_auto.attempt(f"EXECUTE SCRIPT {s}.{script}()")
                    scenarios[f"{label}_autocommit_on"] = {
                        "script_result": "ok" if ok else f"ERROR {result}",
                        "ids_after_script": logt(observer),
                    }
            finally:
                tx_auto.close()

            refresh: dict[str, Any] = {}
            for mode in ("autocommit_off", "autocommit_on"):
                sys_.run(f"DELETE FROM {s}.RUNS")
                sys_.run(f"DELETE FROM {s}.CHECK_LOG")
                session = Session(self.deployment, autocommit=(mode == "autocommit_on"))
                try:
                    ok, result = session.attempt(f"EXECUTE SCRIPT {s}.REFRESH_LIKE(7, 2025)")
                    if mode == "autocommit_off":
                        session.rollback()
                    refresh[mode] = {
                        "script_result": "ok" if ok else f"ERROR {result}",
                        "runs_rows": observer.rows(f"SELECT ID, STATUS FROM {s}.RUNS ORDER BY 2"),
                        "check_log_rows": observer.rows(
                            f"SELECT SEASON, CHECK_NAME FROM {s}.CHECK_LOG ORDER BY 1, 2"
                        ),
                    }
                finally:
                    session.close()
            scenarios["refresh_like_wrapper"] = refresh
        finally:
            observer.close()
            tx.close()
        f.evidence["scenarios"] = scenarios

        h1 = scenarios["pquery_outer_autocommit_off"]
        survivors = h1["ids_after_caller_rollback"]
        refresh_ok = all(
            r["runs_rows"] == [(7, "FAILED"), (7, "STARTED")]
            and r["check_log_rows"] == [(2025, "C_ERR")]
            for r in refresh.values()
        )
        f.evidence["committed_rows_survive_rollback"] = survivors
        f.evidence["refresh_wrapper_logs_survive"] = refresh_ok
        f.result = "pass" if 2 in survivors and 3 not in survivors and refresh_ok else "partial"
        f.fallback_needed = not refresh_ok
        f.summary = (
            f"rows committed by the nested script survive the caller's rollback: {survivors}; "
            f"wrapper logs survive: {refresh_ok}"
        )
        return f

    # ------------------------------------------------------------------ (i)

    def probe_i(self) -> Finding:
        f = Finding("i", TITLES["i"])
        s = self.schema("I")
        sys_ = self.sys
        values = [
            -7.5, 0.7, 25.149999999, 0.123449999, 0.12345, -0.6, 0.125, 0.375, 0.625, -0.125,
            -0.375, 1.005, 2.675, 0.135, 99.995, 0.00005, 0.00004999999, 0.99995, -0.12345,
            1234.565, 0.1 + 0.2, 33.333333333333336,
        ]  # fmt: skip
        sys_.run(f"CREATE TABLE {s}.D (ID DECIMAL(3,0), X DOUBLE)")
        sys_.run(
            f"INSERT INTO {s}.D VALUES " + ", ".join(f"({i}, {v!r})" for i, v in enumerate(values))
        )
        rows62 = {
            r[0]: (r[1], r[2])
            for r in sys_.rows(
                f"SELECT ID, CAST(X AS DECIMAL(6,2)), ROUND(X, 2) FROM {s}.D ORDER BY ID"
            )
        }
        ok, r = sys_.attempt(
            f"SELECT ID, CAST(X AS DECIMAL(5,4)) FROM {s}.D WHERE ABS(X) < 9.99995"
        )
        f.evidence["cast_5_4_with_where_guard_on_same_select"] = "worked" if ok else f"ERROR {r}"
        ok, r = sys_.attempt(
            f"SELECT ID, CASE WHEN ABS(X) < 9.99995 THEN CAST(X AS DECIMAL(5,4)) END FROM {s}.D"
        )
        f.evidence["cast_5_4_with_case_guard"] = "worked" if ok else f"ERROR {r}"
        sys_.run(f"CREATE TABLE {s}.D_SMALL AS SELECT * FROM {s}.D WHERE ABS(X) < 9.99995")
        rows54 = {
            r[0]: (r[1], r[2])
            for r in sys_.rows(
                f"SELECT ID, CAST(X AS DECIMAL(5,4)), ROUND(X, 4) FROM {s}.D_SMALL ORDER BY ID"
            )
        }
        ok, r = sys_.attempt(f"SELECT CAST(X AS DECIMAL(5,4)) FROM {s}.D")
        f.evidence["cast_5_4_over_all_rows_including_25_and_1234"] = (
            "worked" if ok else f"ERROR {r}"
        )
        table = []
        mismatches = []
        for i, x in enumerate(values):
            c62, r2 = rows62[i]
            c54, r4 = rows54.get(i, (None, None))
            expected62 = round_half_away(Decimal(repr(x)), 2)
            expected54 = round_half_away(Decimal(repr(x)), 4)
            table.append(
                {
                    "x": x,
                    "cast_6_2": str(c62),
                    "round_2": str(r2),
                    "decimal_text_half_away_6_2": str(expected62),
                    "cast_5_4": None if c54 is None else str(c54),
                    "round_4": None if r4 is None else str(r4),
                    "decimal_text_half_away_5_4": str(expected54),
                }
            )
            if Decimal(str(c62)) != expected62:
                mismatches.append(("6_2", x, str(c62), str(expected62)))
            if c54 is not None and Decimal(str(c54)) != expected54:
                mismatches.append(("5_4", x, str(c54), str(expected54)))
        f.evidence["table"] = table
        f.evidence["cast_differs_from_decimal_text_rounding"] = mismatches
        spec_values = [-7.5, 0.7, 25.149999999, 0.123449999, 0.12345, -0.6]
        by_x = {t["x"]: t for t in table}
        spec = {}
        for v in spec_values:
            t = by_x[v]
            spec[repr(v)] = {"DECIMAL(6,2)": t["cast_6_2"], "DECIMAL(5,4)": t["cast_5_4"]}
        f.evidence["plan_values"] = spec
        truncation_free = (
            by_x[25.149999999]["cast_6_2"] == "25.15"
            and by_x[0.123449999]["cast_5_4"] == "0.1234"
            and by_x[0.12345]["cast_5_4"] in ("0.1235", "0.1234")
        )
        f.evidence["25.149999999_to_6_2_is_rounded_not_truncated"] = by_x[25.149999999]["cast_6_2"]
        f.evidence["0.12345_to_5_4"] = by_x[0.12345]["cast_5_4"]
        f.evidence["0.123449999_to_5_4"] = by_x[0.123449999]["cast_5_4"]

        for scale, precision, step, count in ((2, 6, 200, 5000), (4, 5, 20000, 10000)):
            texts = [f"{(2 * k + 1) / step:.{scale + 1}f}" for k in range(count)]
            sys_.run(f"CREATE OR REPLACE TABLE {s}.TIES (ID DECIMAL(9,0), X DOUBLE)")
            for start in range(0, count, 1000):
                chunk = ", ".join(
                    f"({start + i}, {txt})" for i, txt in enumerate(texts[start : start + 1000])
                )
                sys_.run(f"INSERT INTO {s}.TIES VALUES {chunk}")
            swept = sys_.rows(
                f"SELECT ID, CAST(X AS DECIMAL({precision},{scale})), ROUND(X, {scale}), "
                f"CAST(ROUND(X, {scale}) AS DECIMAL({precision},{scale})) FROM {s}.TIES ORDER BY ID"
            )
            cast_bad, round_bad, worst_units = [], [], Decimal(0)
            for row_id, cast_value, round_value, cast_round_value in swept:
                expected = round_half_away(Decimal(texts[row_id]), scale)
                worst_units = max(
                    worst_units,
                    abs(Decimal(str(cast_value)) - expected) / Decimal(1).scaleb(-scale),
                )
                if Decimal(str(cast_value)) != expected:
                    cast_bad.append(texts[row_id])
                if (
                    Decimal(str(round_value)) != expected
                    or Decimal(str(cast_round_value)) != expected
                ):
                    round_bad.append(texts[row_id])
            f.evidence[f"tie_sweep_scale_{scale}"] = {
                "values": count,
                "description": f"every exact {scale + 1}-decimal tie text k.5 / 10^{scale}, "
                "as a DOUBLE; reference is half-away rounding of the decimal text",
                "cast_max_difference_in_last_places": str(worst_units),
                "cast_differs": len(cast_bad),
                "cast_differs_examples": cast_bad[:8],
                "round_or_cast_of_round_differs": len(round_bad),
                "round_differs_examples": round_bad[:8],
            }

        overflow = {}
        for label, expr in {
            "99999.0_as_6_2": "CAST(CAST(99999.0 AS DOUBLE) AS DECIMAL(6,2))",
            "9999.994_as_6_2": "CAST(CAST(9999.994 AS DOUBLE) AS DECIMAL(6,2))",
            "9999.995_as_6_2": "CAST(CAST(9999.995 AS DOUBLE) AS DECIMAL(6,2))",
            "1.0_as_5_4": "CAST(CAST(1.0 AS DOUBLE) AS DECIMAL(5,4))",
            "9.99995_as_5_4": "CAST(CAST(9.99995 AS DOUBLE) AS DECIMAL(5,4))",
            "null_as_6_2": "CAST(CAST(NULL AS DOUBLE) AS DECIMAL(6,2))",
        }.items():
            ok, r = sys_.attempt(f"SELECT {expr}")
            overflow[label] = str(r[0][0]) if ok else f"ERROR {r}"
        f.evidence["range_edges"] = overflow

        sys_.run(
            f"CREATE VIEW {s}.V AS SELECT ID, CAST(X AS DECIMAL(6,2)) AS SPREAD, "
            f"CAST(X AS DECIMAL(5,4)) AS WP FROM {s}.D_SMALL"
        )
        view_types = sys_.rows(
            "SELECT COLUMN_NAME, COLUMN_TYPE FROM EXA_ALL_COLUMNS "
            f"WHERE COLUMN_SCHEMA = '{s}' AND COLUMN_TABLE = 'V' ORDER BY COLUMN_ORDINAL_POSITION"
        )
        f.evidence["view_column_types"] = view_types
        query = f"SELECT SPREAD, WP FROM {s}.V WHERE ID IN (3, 5, 6) ORDER BY ID"
        statement = sys_.conn.execute(query)
        raw = statement.fetchall()
        f.evidence["pyexasol_default_readback"] = {
            "rows": [list(r) for r in raw],
            "python_types": [type(v).__name__ for v in raw[0]],
            "column_metadata": {k: v for k, v in statement.columns().items()},
        }
        import pyexasol

        mapped = self.deployment.connect_options()
        mapped["fetch_mapper"] = pyexasol.exasol_mapper
        conn = pyexasol.connect(**mapped)
        try:
            got = conn.execute(query).fetchall()
        finally:
            conn.close()
        f.evidence["pyexasol_exasol_mapper_readback"] = {
            "rows": [[str(v) for v in r] for r in got],
            "python_types": [type(v).__name__ for v in got[0]],
        }

        rounds = not mismatches
        f.evidence["rounds_not_truncates"] = (
            Decimal(by_x[25.149999999]["cast_6_2"]) == Decimal("25.15")
            and Decimal(by_x[0.7]["cast_6_2"]) == Decimal("0.7")
            and Decimal(by_x[0.123449999]["cast_5_4"]) == Decimal("0.1234")
            and Decimal(by_x[0.12345]["cast_5_4"]) == Decimal("0.1235")
        )
        f.result = "pass" if f.evidence["rounds_not_truncates"] else "fail"
        f.fallback_needed = not f.evidence["rounds_not_truncates"]
        f.summary = (
            f"25.149999999 -> {by_x[25.149999999]['cast_6_2']} (6,2); "
            f"0.123449999 -> {by_x[0.123449999]['cast_5_4']} (5,4); "
            f"0.12345 -> {by_x[0.12345]['cast_5_4']} (5,4); "
            f"values that differ from half-away rounding of the decimal text: {len(mismatches)} "
            f"(binary ties only); truncation_free={truncation_free}"
        )
        return f

    # ------------------------------------------------------------------ driver

    def run(self, items: str) -> int:
        cleaned = self.cleanup()
        self.meta["cleanup_before"] = cleaned
        try:
            for item in items:
                probe = getattr(self, f"probe_{item}")
                started = time.time()
                try:
                    finding = probe()
                except Exception as error:  # a probe that crashes is a finding, not a stop
                    finding = Finding(
                        item,
                        TITLES[item],
                        result="error",
                        summary=f"probe crashed: {type(error).__name__}: {error_text(error)}",
                    )
                finding.evidence["seconds"] = round(time.time() - started, 2)
                self.findings[item] = finding
                print(
                    f"({item}) {finding.result.upper():8} {finding.title}\n      {finding.summary}"
                )
        finally:
            if not self.keep:
                self.meta["cleanup_after"] = self.cleanup()
                print("cleanup:", self.meta["cleanup_after"])
            self.write_results()
            self.sys.close()
        gate_negative = [
            i for i in GATE_ITEMS if i in self.findings and self.findings[i].result != "pass"
        ]
        if gate_negative:
            print(f"GATE: negative for {gate_negative}; apply the plan's fallback")
            return 3
        return 0

    def write_results(self) -> None:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "meta": self.meta,
            "findings": {k: jsonable(asdict(v)) for k, v in self.findings.items()},
        }
        self.results_file.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        print(f"results: {self.results_file}")


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    h = (
        math.sin((p2 - p1) / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    )
    return 2 * 6371.0088 * math.asin(min(1.0, math.sqrt(h)))


def round_half_away(value: Decimal, scale: int) -> Decimal:
    quantum = Decimal(1).scaleb(-scale)
    from decimal import ROUND_HALF_UP

    return value.quantize(quantum, rounding=ROUND_HALF_UP)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--target", default="local", choices=["local", "aws"])
    parser.add_argument("--only", default="", help="comma-separated item letters, default all")
    parser.add_argument("--keep", action="store_true", help="do not drop the PROBE_MP_ schemas")
    args = parser.parse_args()
    items = "".join(sorted(i.strip() for i in args.only.split(",") if i.strip())) or ITEMS
    unknown = set(items) - set(ITEMS)
    if unknown:
        parser.error(f"unknown items {sorted(unknown)}")
    probe = Probe(args.target, args.keep)
    print(f"target {probe.meta['target']}, db {probe.meta['db_version']}, items {items}")
    return probe.run(items)


if __name__ == "__main__":
    raise SystemExit(main())
