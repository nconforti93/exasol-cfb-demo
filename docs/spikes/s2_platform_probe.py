#!/usr/bin/env python3
"""S2 platform probe for the CFBD virtual schema (task 1.4, plan add-cfbd-vs-foundation).

Checks the platform facts (a)-(q) from plan.md as `sys` on Exasol Personal local
(127.0.0.1:8563, nano 2026.2). The pushdown SQL, fetch UDF, replay store and test
harness are built on these facts.

Safety rules:
- Only PROBE_* database objects, a probe connection with a generated dummy key
  (address 192.0.2.1, TEST-NET-1, never contacted), the BucketFS service directory
  `probe_s2`, the sub-directory `bfsdefault/default/probe_s2` and a short-lived
  sidecar container named `probe-s2-sidecar`. Everything is removed at the end
  (unless --keep).
- Zero CFBD calls. No HTTP request ever goes to api.collegefootballdata.com: (q)
  only resolves that name and TCP-connects to 1.1.1.1:443, and the UDF's HTTP
  helper refuses every host except 127.0.0.1.
- The sys password is read from the starter-kit credentials file at run time and is
  never printed or stored. Probe user passwords are random and live only in memory.

Usage:
    uv run python docs/spikes/s2_platform_probe.py [--only a,c,f] [--keep] [--crash-trigger]

Writes cache/probe/s2-platform-probe.json (gitignored) and, for (p),
vs/gen/exasol_reserved_keywords.txt (metadata only).
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import json
import random
import re
import secrets
import ssl
import statistics
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import pyexasol

DSN = "127.0.0.1:8563"
PASSWORD_FILE = Path.home() / ".exasol-starter-kit" / "credentials" / "personal_sys_password"
REPO_ROOT = Path(__file__).resolve().parents[2]
RESULTS_FILE = REPO_ROOT / "cache" / "probe" / "s2-platform-probe.json"
KEYWORDS_FILE = REPO_ROOT / "vs" / "gen" / "exasol_reserved_keywords.txt"
EXA_DIR = (
    Path.home() / ".exasol" / "personal" / "deployments" / "local" / "local" / "runtime" / "exa"
)
BUCKETFS_HOST_ROOT = EXA_DIR / "bucketfs"
BUCKETFS_CONF = EXA_DIR / "bucketfs.conf"

INFRA, META, CORE = "PROBE_INFRA", "PROBE_META", "PROBE_CORE"
VS = "PROBE_VS"
VS_COMMENTS = "PROBE_VS_COMMENTS"
VS_COMMENT_LIMIT = "PROBE_VS_COMMENT_LIMIT"
VS_COMMENT_EMOJI = "PROBE_VS_COMMENT_EMOJI"
VS_BIG = "PROBE_VS_BIG"
VS_AGG = "PROBE_VS_AGG"
ALL_VIRTUAL_SCHEMAS = (VS, VS_COMMENTS, VS_COMMENT_LIMIT, VS_COMMENT_EMOJI, VS_BIG, VS_AGG)
OWNER, READER, CONN = "PROBE_OWNER", "PROBE_READER", "PROBE_CONN"
PROBE_SERVICE, PROBE_BUCKET, PROBE_BUCKET_2 = "probe_s2", "bkt", "bkt2"
DEFAULT_SERVICE, DEFAULT_BUCKET, DEFAULT_SUBDIR = "bfsdefault", "default", "probe_s2"
SIDECAR_NAME = "probe-s2-sidecar"
SLOW_SECONDS = 2.0
CONCURRENCY_SLICES = 6
SCRIPT_TEXT_LIMIT = 2_000_000
BIG_SLICES_PER_KIND = 500
ITEMS = "abcdefghijklmnopq"

RT_LOGICAL_TABLE = "T_RT"
RT_KEYS = ["stored:a", "stored:b", "stored:empty", "nf:1", "live:1", "live:2"]
RT_EXPECTED = sorted(
    [("stored:a", 10, "STORED"), ("stored:a", 11, "STORED"), ("stored:b", 12, "STORED")]
    + [(key, row_no, "LIVE") for key in ("nf:1", "live:1", "live:2") for row_no in (1, 2)]
)
RT_EXPECTED_SUM = sum(value for _, value, _ in RT_EXPECTED)

FALLBACKS = {
    "a": "Gate: stop and escalate. Without Python adapters on nano the VS design needs another runtime.",
    "b": "Vendor a pure-Python replacement into the bundle, or point ssl at an explicit CA file; "
    "the runtime stays stdlib-only.",
    "c": "Escalate: per-table static-EMITS UDFs would each need their own script-bound key grant (D2).",
    "d": "Build the slice list as `SELECT … FROM DUAL UNION ALL …`.",
    "e": "If Exasol pushed nothing for mixed conjunctions, response-column filters would hide parameter "
    "predicates; filter in an outer query instead. Pushed predicates are not re-checked by Exasol, so "
    "the pushdown SQL re-applies every pushed predicate itself (already planned).",
    "f": "Compute the missing slices before the UDF with `(VALUES …) MINUS (SELECT SLICE_KEY FROM "
    "LOAD_LOG …)` in a derived table instead of NOT EXISTS.",
    "g": "Grant readers SELECT on CFB_CORE/CFB_META and EXECUTE on CFBD_FETCH via a role "
    "(weakens key protection, so escalate first).",
    "h": "Wrap the UNION ALL in a subquery with `ORDER BY FALSE` (workaround verified on 2026.1).",
    "i": "Keep the documentation in a CFB_META.COLUMN_DOCS table and in rule error messages.",
    "j": "Manifest as a string literal in a one-row CTE (`WITH CFBD_MANIFEST AS (SELECT '…' FROM DUAL)`).",
    "k": "Escalate: replay mode needs a UDF-readable path; Personal exposes no BucketFS HTTP port.",
    "l": "Bind the mock to the WSL host IP (eth0) and address it as http://<host ip>:<port>.",
    "m": "If scalar EMITS serializes: CFBD_FETCH becomes SET … EMITS with GROUP BY slice key.",
    "n": "Gzip+base64 the embedded catalog, or move it to BucketFS with a hash check.",
    "o": "Deploy replaces CFBD_FETCH only when its text changed; `make grants` follows every replace.",
    "p": "Use the reserved word list from the Exasol documentation.",
    "q": "D21: resolver seam with DNS-over-HTTPS fallback (task 4.2), already approved.",
}

TITLES = {
    "a": "PYTHON3 ADAPTER SCRIPT + CREATE VIRTUAL SCHEMA on nano 2026.2",
    "b": "SLC Python version and stdlib",
    "c": "Dynamic EMITS UDF inside pushdown SQL",
    "d": "FROM (VALUES …) AS R(…) syntax",
    "e": "Partial conjunct pushdown with the planned capability set",
    "f": "Rows removed by NOT EXISTS never reach the UDF",
    "g": "Pushdown runs with the VS owner's rights",
    "h": "Aggregates over a VS whose pushdown is table UNION ALL UDF",
    "i": "Column comments in VS metadata reach EXA_ALL_COLUMNS",
    "j": "Leading /*…*/ survives in EXPLAIN VIRTUAL PUSHDOWN_SQL",
    "k": "BucketFS host directory to UDF path mapping, reconcile delay, default REPLAY_PATH",
    "l": "UDF reaches a sidecar HTTP server by IP literal 127.0.0.1",
    "m": "Concurrency: scalar EMITS vs SET EMITS + GROUP BY",
    "n": "CREATE SCRIPT with a ~2 MB body",
    "o": "CREATE OR REPLACE drops the script-bound connection grant",
    "p": "Reserved keyword snapshot",
    "q": "UDF sandbox name resolution",
}


@dataclass
class Finding:
    """One probe result, with the fallback the plan adopts when the result is negative."""

    item: str
    status: str
    result: str
    details: dict[str, Any] = field(default_factory=dict)
    title: str = ""
    fallback_if_negative: str = ""
    fallback_adopted: str = ""

    def __post_init__(self) -> None:
        self.title = TITLES[self.item]
        self.fallback_if_negative = FALLBACKS[self.item]
        negative = self.status in ("fail", "error", "blocked")
        self.fallback_adopted = self.fallback_if_negative if negative else "none needed"


class Session:
    """A pyexasol connection with helpers that turn query errors into data."""

    def __init__(self, user: str, password: str) -> None:
        self.user = user
        self._password = password
        self.conn = self._connect()

    def _connect(self) -> pyexasol.ExaConnection:
        return pyexasol.connect(
            dsn=DSN,
            user=self.user,
            password=self._password,
            websocket_sslopt={"cert_reqs": ssl.CERT_NONE},
            autocommit=True,
            socket_timeout=900,
        )

    def reconnect(self) -> None:
        with contextlib.suppress(Exception):
            self.conn.close()
        self.conn = self._connect()

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

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self.conn.close()


def error_text(error: BaseException) -> str:
    message = getattr(error, "message", None) or str(error)
    return " ".join(str(message).split())[:1500]


def lit(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def timed(action: Callable[[], Any]) -> tuple[Any, float]:
    started = time.perf_counter()
    outcome = action()
    return outcome, round((time.perf_counter() - started) * 1000, 1)


# ---------------------------------------------------------------- types and metadata


def dec(precision: int = 18, scale: int = 0) -> dict[str, Any]:
    return {"type": "DECIMAL", "precision": precision, "scale": scale}


def varchar(size: int) -> dict[str, Any]:
    return {"type": "VARCHAR", "size": size, "characterSet": "UTF8"}


BOOLEAN: dict[str, Any] = {"type": "BOOLEAN"}
DOUBLE: dict[str, Any] = {"type": "DOUBLE"}
TIMESTAMP: dict[str, Any] = {"type": "TIMESTAMP", "withLocalTimeZone": False}


def sql_type(data_type: dict[str, Any]) -> str:
    kind = data_type["type"]
    if kind == "DECIMAL":
        return f"DECIMAL({data_type['precision']},{data_type['scale']})"
    if kind == "VARCHAR":
        return f"VARCHAR({data_type['size']}) UTF8"
    return str(kind)


def column(name: str, data_type: dict[str, Any], comment: str | None = None) -> dict[str, Any]:
    spec: dict[str, Any] = {"name": name, "dataType": data_type}
    if comment is not None:
        spec["comment"] = comment
    return spec


def table(
    name: str, columns: list[tuple[str, dict[str, Any]]], comment: str | None = None
) -> dict[str, Any]:
    spec: dict[str, Any] = {"name": name, "columns": [column(n, t) for n, t in columns]}
    if comment is not None:
        spec["comment"] = comment
    return spec


def emits(columns: list[tuple[str, dict[str, Any]]]) -> str:
    return "EMITS (" + ", ".join(f"{name} {sql_type(kind)}" for name, kind in columns) + ")"


PLANNED_CAPABILITIES = [
    "FILTER_EXPRESSIONS",
    "FN_PRED_AND",
    "FN_PRED_OR",
    "FN_PRED_NOT",
    "FN_PRED_EQUAL",
    "FN_PRED_NOTEQUAL",
    "FN_PRED_LESS",
    "FN_PRED_LESSEQUAL",
    "FN_PRED_BETWEEN",
    "FN_PRED_IN_CONSTLIST",
    "FN_PRED_LIKE",
    "FN_PRED_LIKE_ESCAPE",
    "FN_PRED_IS_NULL",
    "FN_PRED_IS_NOT_NULL",
    "LITERAL_NULL",
    "LITERAL_BOOL",
    "LITERAL_DATE",
    "LITERAL_TIMESTAMP",
    "LITERAL_TIMESTAMP_UTC",
    "LITERAL_DOUBLE",
    "LITERAL_EXACTNUMERIC",
    "LITERAL_STRING",
]
ADVERTISED_PREDICATE_SHAPES = {
    "P_YEAR = 2024": "predicate_equal",
    "P_YEAR <> 2020": "predicate_notequal",
    "P_YEAR < 2030": "predicate_less",
    "P_YEAR > 2018": "predicate_less",
    "P_YEAR <= 2030": "predicate_lessequal",
    "P_YEAR >= 2018": "predicate_lessequal",
    "P_YEAR BETWEEN 2019 AND 2021": "predicate_between",
    "P_WEEK IN (8, 9)": "predicate_in_constlist",
    "NOT (P_YEAR = 2020)": "predicate_not",
    "NAME LIKE 'a%'": "predicate_like",
    "NAME IS NULL": "predicate_is_null",
    "NAME IS NOT NULL": "predicate_is_not_null",
    "P_YEAR = 2024 OR P_YEAR = 2025": "predicate_or",
    "P_YEAR = 2024 AND P_WEEK = 8": "predicate_and",
}
CRASH_TRIGGER_CAPABILITIES = [
    "SELECTLIST_EXPRESSIONS",
    "AGGREGATE_SINGLE_GROUP",
    "FN_AGG_COUNT_STAR",
]

RT_COLUMNS: list[tuple[str, dict[str, Any]]] = [
    ("K", varchar(2000)),
    ("V", dec()),
    ("TS", TIMESTAMP),
    ("FLAG", BOOLEAN),
    ("D", DOUBLE),
    ("SRC", varchar(8)),
]
CONC_COLUMNS: list[tuple[str, dict[str, Any]]] = [
    ("K", varchar(100)),
    ("T0", DOUBLE),
    ("T1", DOUBLE),
    ("PID", dec(10, 0)),
    ("VM", varchar(100)),
]
ECHO_COLUMNS: list[tuple[str, dict[str, Any]]] = [
    ("P_YEAR", dec()),
    ("P_WEEK", dec()),
    ("P_FLAG", BOOLEAN),
    ("NAME", varchar(100)),
    ("FILTER_SEEN", varchar(2000000)),
    ("REQUEST_SEEN", varchar(2000000)),
]

TRICKY_COMMENT = (
    'P_YEAR: it\'s "quoted" -- not a comment /* nor this */; line1\nline2; '
    "unicode äöü é ñ €; rule games.year_required: P_YEAR is required"
)
COMMENT_2000 = ("rule text " * 200)[:2000]
COMMENT_2001 = ("rule text " * 201)[:2001]
EMOJI_COMMENT = "football \U0001f3c8 emoji outside the BMP"


def manifest_comment(keys: list[str]) -> str:
    manifest = json.dumps({"table": RT_LOGICAL_TABLE, "slices": keys}, separators=(",", ":"))
    return "/*PROBE " + manifest.replace("/", "\\/") + "*/"


def values_clause(keys: list[str], alias: str = "R(SLICE_KEY)") -> str:
    return "(VALUES " + ", ".join(f"({lit(key)})" for key in keys) + f") AS {alias}"


def not_exists_guard(logical_table: str, key_expr: str) -> str:
    return (
        f" WHERE NOT EXISTS (SELECT 1 FROM {META}.LOAD_LOG L WHERE L.TABLE_NAME = {lit(logical_table)}"
        f" AND L.SLICE_KEY = {key_expr} AND L.IS_FINAL)"
    )


def read_through_sql(
    keys: list[str], kind: str = "scalar", guard: bool = True, manifest: str = "comment"
) -> str:
    """Pushdown SQL in the plan's read-through shape: stored branch UNION ALL fetch branch.

    kind: `scalar`, `set` (SET UDF + GROUP BY slice) or `onegroup` (SET UDF without GROUP BY,
    so one instance receives every slice).
    """
    key_list = ", ".join(lit(key) for key in keys)
    stored = (
        f"SELECT K, V, TS, FLAG, D, CAST('STORED' AS VARCHAR(8) UTF8) AS SRC FROM {CORE}.T_RT"
        f" WHERE K IN ({key_list}) AND K IN (SELECT SLICE_KEY FROM {META}.LOAD_LOG"
        f" WHERE TABLE_NAME = {lit(RT_LOGICAL_TABLE)} AND IS_FINAL)"
    )
    guard_sql = not_exists_guard(RT_LOGICAL_TABLE, "R.SLICE_KEY") if guard else ""
    udf = "FETCH_SCALAR" if kind == "scalar" else "FETCH_SET"
    fetch = f"SELECT {INFRA}.{udf}(R.SLICE_KEY) {emits(RT_COLUMNS)} FROM {values_clause(keys)}{guard_sql}"
    if kind == "set":
        fetch += " GROUP BY R.SLICE_KEY"
    body = f"SELECT * FROM ({stored} UNION ALL {fetch}) WHERE V >= 0"
    if manifest == "comment":
        return manifest_comment(keys) + "\n" + body
    if manifest == "cte":
        payload = json.dumps({"table": RT_LOGICAL_TABLE, "slices": keys}, separators=(",", ":"))
        return f"WITH PROBE_MANIFEST AS (SELECT {lit(payload)} AS MANIFEST FROM DUAL) {body}"
    return body


def concurrency_keys(count: int = CONCURRENCY_SLICES) -> list[str]:
    return [f"s{i}" for i in range(1, count + 1)]


def slow_sql(kind: str, read_through: bool = False, count: int = CONCURRENCY_SLICES) -> str:
    keys = concurrency_keys(count)
    udf = {"scalar": "SLOW_SCALAR", "set": "SLOW_SET", "pool": "SLOW_POOL"}[kind]
    values = values_clause(keys, "R(K)")
    guard = not_exists_guard("T_CONC", "R.K") if read_through else ""
    fetch = f"SELECT {INFRA}.{udf}(R.K, {SLOW_SECONDS}) FROM {values}{guard}"
    if kind == "set":
        fetch += " GROUP BY R.K"
    if not read_through:
        return fetch
    key_list = ", ".join(lit(key) for key in keys)
    stored = (
        f"SELECT K, T0, T1, PID, VM FROM {CORE}.T_CONC WHERE K IN ({key_list}) AND K IN"
        f" (SELECT SLICE_KEY FROM {META}.LOAD_LOG WHERE TABLE_NAME = 'T_CONC' AND IS_FINAL)"
    )
    return f"SELECT * FROM ({stored} UNION ALL {fetch}) WHERE T1 >= T0"


def big_keys() -> list[str]:
    path = "/games?seasonType=both&week=8&year=2024"
    stored = [f"stored:big:{i:04d}:{path}" for i in range(BIG_SLICES_PER_KIND)]
    live = [f"live:big:{i:04d}:{path}" for i in range(BIG_SLICES_PER_KIND)]
    return [key for pair in zip(stored, live, strict=True) for key in pair]


def main_set() -> dict[str, Any]:
    dyn = [
        ("K", varchar(2000)),
        ("V", dec()),
        ("TS", TIMESTAMP),
        ("FLAG", BOOLEAN),
        ("D", DOUBLE),
        ("SRC", varchar(8)),
    ]
    narrow_vs = [("K", varchar(2000)), ("V", dec())]
    narrow_emits = [("A", varchar(100)), ("B", dec(9, 0))]
    wide_vs = [("K", varchar(10)), ("V", dec(9, 0))]
    wide_emits = [("K", varchar(2000)), ("V", dec())]
    renamed_emits = [("A", varchar(2000)), ("B", dec())]
    dyn_values = values_clause(["live:1", "live:2"])
    tables = [
        table("T_BASIC", [("P_YEAR", dec()), ("NAME", varchar(100))], "probe table"),
        table("T_REJECT", [("P_YEAR", dec())]),
        table("T_ADAPTER_ENV", [("PY_VERSION", varchar(100))]),
        table("T_PAD", [("PAD_LEN", dec()), ("PAD_SHA", varchar(64))]),
        table("T_ECHO", ECHO_COLUMNS),
        table("T_DYN", dyn),
        table("T_DYN_NARROW", narrow_vs),
        table("T_DYN_WIDE", wide_vs),
        table("T_DYN_RENAMED", narrow_vs),
        table("T_RT", RT_COLUMNS),
        table("T_RT_NOGUARD", RT_COLUMNS),
        table("T_RT_SET", RT_COLUMNS),
        table("T_RT_SET_NOGUARD", RT_COLUMNS),
        table("T_RT_ONEGROUP", RT_COLUMNS),
        table("T_RT_ONEGROUP_NOGUARD", RT_COLUMNS),
        table("T_RT_BIG", RT_COLUMNS),
        table("T_RT_CTE", RT_COLUMNS),
        table("T_CONN", [("CONN_STATUS", varchar(200))]),
        table("T_CONC_SCALAR", CONC_COLUMNS),
        table("T_CONC_SET", CONC_COLUMNS),
        table("T_CONC_RT", CONC_COLUMNS),
        table("T_CONC_RT_SET", CONC_COLUMNS),
        table("T_CONC_RT_SET10", CONC_COLUMNS),
        table("T_CONC_RT_POOL10", CONC_COLUMNS),
    ]
    echo_sql = (
        "SELECT CAST(Y AS DECIMAL(18,0)), CAST(W AS DECIMAL(18,0)), CAST(F AS BOOLEAN),"
        " CAST(N AS VARCHAR(100) UTF8),"
        " CAST('PROBE_FILTER:__FILTER_B64__' AS VARCHAR(2000000) UTF8),"
        " CAST('PROBE_REQUEST:__REQUEST_B64__' AS VARCHAR(2000000) UTF8)"
        " FROM (VALUES (2024, 1, TRUE, 'a'), (2024, 2, TRUE, 'B'), (2023, 3, FALSE, 'c')) AS R(Y, W, F, N)"
    )
    pushdown = {
        "T_BASIC": "SELECT CAST(2024 AS DECIMAL(18,0)), CAST('a' AS VARCHAR(100) UTF8) FROM DUAL",
        "T_REJECT": "RAISE:CFBD_VS.T_REJECT: rule probe.year_required: P_YEAR is required (probe, 0 calls)",
        "T_ADAPTER_ENV": "SELECT CAST('__PY_VERSION__' AS VARCHAR(100) UTF8) FROM DUAL",
        "T_PAD": "SELECT CAST(__PAD_LEN__ AS DECIMAL(18,0)), CAST('__PAD_SHA__' AS VARCHAR(64) UTF8) FROM DUAL",
        "T_ECHO": echo_sql,
        "T_DYN": f"SELECT {INFRA}.FETCH_SCALAR(R.SLICE_KEY) {emits(dyn)} FROM {dyn_values}",
        "T_DYN_NARROW": f"SELECT {INFRA}.FETCH_SCALAR(R.SLICE_KEY) {emits(narrow_emits)} FROM {dyn_values}",
        "T_DYN_WIDE": f"SELECT {INFRA}.FETCH_SCALAR(R.SLICE_KEY) {emits(wide_emits)} FROM {dyn_values}",
        "T_DYN_RENAMED": (
            f"SELECT {INFRA}.FETCH_SCALAR(R.SLICE_KEY) {emits(renamed_emits)} FROM {dyn_values}"
        ),
        "T_RT": read_through_sql(RT_KEYS),
        "T_RT_NOGUARD": read_through_sql(RT_KEYS, guard=False),
        "T_RT_SET": read_through_sql(RT_KEYS, kind="set"),
        "T_RT_SET_NOGUARD": read_through_sql(RT_KEYS, kind="set", guard=False),
        "T_RT_ONEGROUP": read_through_sql(RT_KEYS, kind="onegroup"),
        "T_RT_ONEGROUP_NOGUARD": read_through_sql(RT_KEYS, kind="onegroup", guard=False),
        "T_RT_BIG": read_through_sql(big_keys()),
        "T_RT_CTE": read_through_sql(RT_KEYS, manifest="cte"),
        "T_CONN": f"SELECT CAST({INFRA}.READ_CONN() AS VARCHAR(200) UTF8) FROM DUAL",
        "T_CONC_SCALAR": slow_sql("scalar"),
        "T_CONC_SET": slow_sql("set"),
        "T_CONC_RT": slow_sql("scalar", read_through=True),
        "T_CONC_RT_SET": slow_sql("set", read_through=True),
        "T_CONC_RT_SET10": slow_sql("set", read_through=True, count=10),
        "T_CONC_RT_POOL10": slow_sql("pool", read_through=True, count=10),
    }
    return {"tables": tables, "capabilities": PLANNED_CAPABILITIES, "pushdown": pushdown}


def comment_set(column_comment: str, table_comment: str) -> dict[str, Any]:
    columns = [
        column("C_TRICKY", varchar(10), TRICKY_COMMENT),
        column("C_LONG", varchar(10), column_comment),
        column("C_NONE", varchar(10)),
    ]
    return {
        "tables": [{"name": "T_COMMENTS", "comment": table_comment, "columns": columns}],
        "capabilities": PLANNED_CAPABILITIES,
        "pushdown": {"T_COMMENTS": "SELECT CAST('x' AS VARCHAR(10) UTF8), 'y', 'z' FROM DUAL"},
    }


def adapter_spec() -> dict[str, Any]:
    emoji = comment_set("plain", "plain")
    emoji["tables"][0]["columns"][0]["comment"] = EMOJI_COMMENT
    return {
        "sets": {
            "main": main_set(),
            "comments": comment_set(COMMENT_2000, "table comment: " + TRICKY_COMMENT),
            "comment_limit": comment_set(COMMENT_2001, "table comment"),
            "comment_emoji": emoji,
            "agg": {
                "tables": [table("T", [("X", dec())])],
                "capabilities": CRASH_TRIGGER_CAPABILITIES,
                "pushdown": {"T": "AGG_TRIGGER"},
            },
        }
    }


# ---------------------------------------------------------------- script bodies

ADAPTER_CODE = r"""
import base64
import hashlib
import json
import sys

SPEC = json.loads(base64.b64decode("__SPEC_B64__").decode("utf-8"))


def _set(properties):
    return SPEC["sets"][properties.get("PROBE_SET", "main")]


def _b64(text):
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _pushdown(request, raw, properties):
    pushdown = request["pushdownRequest"]
    name = pushdown["from"]["name"]
    template = _set(properties)["pushdown"][name]
    if template.startswith("RAISE:"):
        raise ValueError(template[len("RAISE:"):])
    if template == "AGG_TRIGGER":
        count = len(pushdown.get("selectListDataTypes") or []) or 1
        return "SELECT " + ", ".join(["CAST(1 AS DECIMAL(18,0))"] * count) + " FROM DUAL"
    return (
        template.replace("__FILTER_B64__", _b64(json.dumps(pushdown.get("filter"), sort_keys=True)))
        .replace("__REQUEST_B64__", _b64(raw))
        .replace("__PY_VERSION__", sys.version.split()[0])
        .replace("__PAD_LEN__", str(len(PAD)))
        .replace("__PAD_SHA__", hashlib.sha256(PAD.encode("utf-8")).hexdigest())
    )


def adapter_call(raw):
    request = json.loads(raw)
    kind = request["type"]
    properties = (request.get("schemaMetadataInfo") or {}).get("properties") or {}
    if kind in ("createVirtualSchema", "refresh", "setProperties"):
        return json.dumps({"type": kind, "schemaMetadata": {"tables": _set(properties)["tables"]}})
    if kind == "dropVirtualSchema":
        return json.dumps({"type": kind})
    if kind == "getCapabilities":
        return json.dumps({"type": kind, "capabilities": _set(properties)["capabilities"]})
    if kind == "pushdown":
        return json.dumps({"type": kind, "sql": _pushdown(request, raw, properties)})
    raise ValueError("PROBE adapter: unsupported request type " + kind)
"""

FETCH_COMMON = r"""
import datetime
import decimal

ROWS_PER_SLICE = 2


def _value(column, slice_key, row_no):
    if column.name.upper() == "SRC":
        return "LIVE"
    if column.type is str:
        return slice_key
    if column.type in (int, decimal.Decimal):
        return row_no
    if column.type is datetime.datetime:
        return datetime.datetime(2024, 8, 24, 16, 0, 0)
    if column.type is bool:
        return True
    if column.type is float:
        return 1.5
    return None


def _emit_slice(ctx, slice_key):
    if slice_key.startswith("stored:"):
        raise RuntimeError("PROBE_MARKER: stored slice " + slice_key + " reached the fetch UDF")
    columns = exa.meta.output_columns
    for row_no in range(1, ROWS_PER_SLICE + 1):
        ctx.emit(*[_value(column, slice_key, row_no) for column in columns])
"""

SLOW_COMMON = r"""
import os
import time


def _one(ctx):
    started = time.time()
    time.sleep(float(ctx.WAIT_S))
    ctx.emit(ctx.K, started, time.time(), os.getpid(), str(exa.meta.vm_id))
"""

SLOW_POOL_CODE = r"""
import concurrent.futures
import os
import time

POOL_SIZE = 10


def _sleep(key, seconds):
    started = time.time()
    time.sleep(seconds)
    return key, started, time.time()


def run(ctx):
    jobs = []
    while True:
        jobs.append((ctx.K, float(ctx.WAIT_S)))
        if not ctx.next():
            break
    with concurrent.futures.ThreadPoolExecutor(max_workers=POOL_SIZE) as pool:
        results = list(pool.map(lambda job: _sleep(*job), jobs))
    for key, started, ended in results:
        ctx.emit(key, started, ended, os.getpid(), str(exa.meta.vm_id))
"""

SCALAR_RUN = "\n\ndef run(ctx):\n    {call}\n"
SET_RUN = "\n\ndef run(ctx):\n    while True:\n        {call}\n        if not ctx.next():\n            break\n"

READ_CONN_CODE = r"""
def run(ctx):
    connection = exa.get_connection("PROBE_CONN")
    return "readable key_len=%d" % len(connection.password or "")
"""

DIAG_CODE = r"""
import datetime
import hashlib
import ipaddress
import json
import os
import socket
import sys
import time
import urllib.parse
import urllib.request


def _emit(ctx, item, value):
    ctx.emit(item, value if isinstance(value, str) else json.dumps(value, sort_keys=True, default=str))


def _read(path):
    try:
        with open(path, "rb") as handle:
            return handle.read().decode("utf-8", "replace")
    except Exception as error:
        return "ERR %s: %s" % (type(error).__name__, error)


def _tcp(host, port):
    started = time.time()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(3)
    try:
        sock.connect((host, port))
        return "CONNECTED %.0fms" % ((time.time() - started) * 1000)
    except Exception as error:
        return "ERR %s: %s %.0fms" % (type(error).__name__, error, (time.time() - started) * 1000)
    finally:
        sock.close()


def _http(url):
    host = urllib.parse.urlsplit(url).hostname
    if host is None or ipaddress.ip_address(host) != ipaddress.ip_address("127.0.0.1"):
        return "REFUSED: probe HTTP only to 127.0.0.1"
    started = time.time()
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            body = response.read(200).decode("utf-8", "replace")
            return "HTTP %d body=%r %.0fms" % (response.status, body, (time.time() - started) * 1000)
    except Exception as error:
        return "ERR %s: %s %.0fms" % (type(error).__name__, error, (time.time() - started) * 1000)


def _stat(path):
    try:
        if not os.path.exists(path):
            return {"exists": False}
        if os.path.isdir(path):
            return {"exists": True, "dir": True, "entries": sorted(os.listdir(path))[:50]}
        with open(path, "rb") as handle:
            data = handle.read()
        return {"exists": True, "dir": False, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    except Exception as error:
        return {"exists": "unknown", "error": "%s: %s" % (type(error).__name__, error)}


def _write(path):
    try:
        with open(path, "w") as handle:
            handle.write("probe")
        return "WROTE"
    except Exception as error:
        return "ERR %s: %s" % (type(error).__name__, error)


def _slc(ctx):
    import platform
    import ssl

    modules = {}
    for name in ["urllib.request", "urllib.parse", "http.client", "ssl", "json", "hashlib", "socket",
                 "datetime", "zoneinfo", "base64", "gzip", "zlib", "decimal", "re", "dataclasses",
                 "typing", "concurrent.futures", "threading", "email.utils", "random", "ipaddress"]:
        try:
            __import__(name)
            modules[name] = "ok"
        except Exception as error:
            modules[name] = "ERR %s" % error
    paths = ssl.get_default_verify_paths()
    context = ssl.create_default_context()
    meta = {}
    for key in ["script_language", "script_name", "node_count", "node_id", "vm_id", "memory_limit",
                "database_version"]:
        meta[key] = str(getattr(exa.meta, key, None))
    _emit(ctx, "sys.version", sys.version)
    _emit(ctx, "executable", sys.executable)
    _emit(ctx, "platform", platform.platform())
    _emit(ctx, "openssl", ssl.OPENSSL_VERSION)
    _emit(ctx, "verify_paths", {"cafile": paths.cafile, "capath": paths.capath,
                                "openssl_cafile": paths.openssl_cafile,
                                "openssl_cafile_exists": os.path.exists(paths.openssl_cafile or ""),
                                "openssl_capath": paths.openssl_capath,
                                "openssl_capath_exists": os.path.isdir(paths.openssl_capath or "")})
    _emit(ctx, "ca_cert_store_stats", context.cert_store_stats())
    _emit(ctx, "modules", modules)
    _emit(ctx, "sha256", hashlib.sha256(b"probe").hexdigest())
    _emit(ctx, "fromisoformat_Z", str(datetime.datetime.fromisoformat("2024-08-24T16:00:00.000Z")))
    _emit(ctx, "cpu_count", os.cpu_count())
    _emit(ctx, "uid_gid", [os.getuid(), os.getgid()])
    _emit(ctx, "exa.meta", meta)


def _dns(ctx):
    for path in ["/etc/resolv.conf", "/etc/hosts", "/etc/nsswitch.conf", "/etc/host.conf"]:
        content = _read(path) if os.path.exists(path) else None
        _emit(ctx, "file " + path, "MISSING" if content is None else "size=%d %r" % (len(content), content[:300]))
    for host in ["api.collegefootballdata.com", "localhost", "1.1.1.1"]:
        try:
            infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
            _emit(ctx, "getaddrinfo " + host, sorted({info[4][0] for info in infos}))
        except Exception as error:
            _emit(ctx, "getaddrinfo " + host,
                  "ERR %s(errno=%s): %s" % (type(error).__name__, getattr(error, "errno", None), error))
    _emit(ctx, "tcp 1.1.1.1:443", _tcp("1.1.1.1", 443))


def run(ctx):
    command = ctx.CMD
    if command == "slc":
        _slc(ctx)
    elif command == "dns":
        _dns(ctx)
    elif command.startswith("tcp:"):
        host, port = command[4:].rsplit(":", 1)
        _emit(ctx, command, _tcp(host, int(port)))
    elif command.startswith("http:"):
        _emit(ctx, command, _http(command[5:]))
    elif command.startswith("stat:"):
        _emit(ctx, command, _stat(command[5:]))
    elif command.startswith("write:"):
        _emit(ctx, command, _write(command[6:]))
    else:
        raise ValueError("unknown DIAG command " + command)
"""

SIDECAR_SERVER = r"""
import http.server, sys, threading
port, lifetime = int(sys.argv[1]), float(sys.argv[2])
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"probe-sidecar-ok"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        print("HIT", self.client_address[0], self.path, flush=True)
    def log_message(self, *args):
        pass
server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
print("LISTENING", port, flush=True)
threading.Timer(lifetime, server.shutdown).start()
server.serve_forever()
"""


def script_sql(header: str, body: str) -> str:
    return f"{header} AS\n{body.strip()}\n/"


def adapter_sql(name: str, pad: str) -> str:
    spec = b64(json.dumps(adapter_spec(), separators=(",", ":")))
    pad_line = f'PAD = """{pad}"""' if pad else 'PAD = ""'
    body = pad_line + "\n" + ADAPTER_CODE.replace("__SPEC_B64__", spec)
    return script_sql(f"CREATE OR REPLACE PYTHON3 ADAPTER SCRIPT {INFRA}.{name}", body)


def pad_text(size: int) -> str:
    lines = []
    total = 0
    counter = 0
    while total < size:
        line = hashlib.sha256(str(counter).encode()).hexdigest()
        lines.append(line)
        total += len(line) + 1
        counter += 1
    return "\n".join(lines)


def udf_scripts() -> dict[str, str]:
    dynamic_header = "CREATE OR REPLACE PYTHON3 {kind} SCRIPT {schema}.{name}(SLICE_KEY VARCHAR(2000)) EMITS (...)"
    slow_header = (
        "CREATE OR REPLACE PYTHON3 {kind} SCRIPT {schema}.{name}(K VARCHAR(100), WAIT_S DOUBLE) "
        + emits(CONC_COLUMNS)
    )
    return {
        "FETCH_SCALAR": script_sql(
            dynamic_header.format(kind="SCALAR", schema=INFRA, name="FETCH_SCALAR"),
            FETCH_COMMON + SCALAR_RUN.format(call="_emit_slice(ctx, ctx.SLICE_KEY)"),
        ),
        "FETCH_SET": script_sql(
            dynamic_header.format(kind="SET", schema=INFRA, name="FETCH_SET"),
            FETCH_COMMON + SET_RUN.format(call="_emit_slice(ctx, ctx.SLICE_KEY)"),
        ),
        "SLOW_SCALAR": script_sql(
            slow_header.format(kind="SCALAR", schema=INFRA, name="SLOW_SCALAR"),
            SLOW_COMMON + SCALAR_RUN.format(call="_one(ctx)"),
        ),
        "SLOW_SET": script_sql(
            slow_header.format(kind="SET", schema=INFRA, name="SLOW_SET"),
            SLOW_COMMON + SET_RUN.format(call="_one(ctx)"),
        ),
        "SLOW_POOL": script_sql(
            slow_header.format(kind="SET", schema=INFRA, name="SLOW_POOL"), SLOW_POOL_CODE
        ),
        "READ_CONN": read_conn_sql("READ_CONN"),
        "READ_CONN_OTHER": read_conn_sql("READ_CONN_OTHER"),
        "DIAG": script_sql(
            f"CREATE OR REPLACE PYTHON3 SCALAR SCRIPT {INFRA}.DIAG(CMD VARCHAR(2000)) "
            "EMITS (ITEM VARCHAR(2000) UTF8, VAL VARCHAR(2000000) UTF8)",
            DIAG_CODE,
        ),
    }


def read_conn_sql(name: str, extra: str = "") -> str:
    return script_sql(
        f"CREATE OR REPLACE PYTHON3 SCALAR SCRIPT {INFRA}.{name}() RETURNS VARCHAR(200) UTF8",
        READ_CONN_CODE + extra,
    )


# ---------------------------------------------------------------- the probe


class Probe:
    """Runs the probe items against Personal local and records findings."""

    def __init__(self, keep: bool, crash_trigger: bool) -> None:
        self.keep = keep
        self.crash_trigger = crash_trigger
        self.findings: dict[str, Finding] = {}
        self.meta: dict[str, Any] = {}
        self.sys = Session("SYS", read_sys_password())
        self.owner_password = secrets.token_urlsafe(24)
        self.reader_password = secrets.token_urlsafe(24)
        self.owner: Session | None = None
        self.reader: Session | None = None
        self.created_default_dirs: list[Path] = []
        self.udfs_ready = False
        self.previous_findings: dict[str, Any] = load_previous_findings()

    # ------------------------------------------------------------ plumbing

    def record(self, finding: Finding) -> None:
        self.findings[finding.item] = finding
        print(f"({finding.item}) {finding.status.upper()}: {finding.result}", flush=True)
        self.save()

    def save(self) -> None:
        RESULTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        findings = dict(self.previous_findings)
        findings.update({item: asdict(found) for item, found in self.findings.items()})
        payload = {"meta": self.meta, "findings": dict(sorted(findings.items()))}
        RESULTS_FILE.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n")

    def as_owner(self) -> Session:
        assert self.owner is not None
        return self.owner

    def as_reader(self) -> Session:
        assert self.reader is not None
        return self.reader

    def diag(self, command: str, session: Session | None = None) -> dict[str, Any]:
        runner = session or self.as_owner()
        rows = runner.rows(f"SELECT {INFRA}.DIAG({lit(command)})")
        result: dict[str, Any] = {}
        for item, value in rows:
            try:
                result[item] = json.loads(value)
            except (TypeError, ValueError):
                result[item] = value
        return result

    def explain(self, session: Session, query: str) -> dict[str, Any]:
        statement = session.run("EXPLAIN VIRTUAL " + query)
        names = statement.column_names()
        rows = [dict(zip(names, row, strict=True)) for row in statement.fetchall()]
        return {"columns": names, "rows": rows}

    @staticmethod
    def pushdown_sql(explained: dict[str, Any]) -> str:
        for row in explained["rows"]:
            for name, value in row.items():
                if name.upper() == "PUSHDOWN_SQL":
                    return str(value)
        return ""

    def other_active_sessions(self) -> list[tuple[Any, ...]]:
        return self.sys.rows(
            "SELECT SESSION_ID, USER_NAME, STATUS, COMMAND_NAME, CLIENT FROM EXA_ALL_SESSIONS"
            " WHERE SESSION_ID <> CURRENT_SESSION AND STATUS <> 'IDLE'"
        )

    # ------------------------------------------------------------ setup and cleanup

    def drop_probe_objects(self) -> list[str]:
        problems = []
        statements = [
            f"DROP VIRTUAL SCHEMA IF EXISTS {name} CASCADE" for name in ALL_VIRTUAL_SCHEMAS
        ]
        statements += [f"DROP SCHEMA IF EXISTS {name} CASCADE" for name in (INFRA, META, CORE)]
        statements += [f"DROP USER IF EXISTS {name} CASCADE" for name in (OWNER, READER)]
        statements += [f"DROP CONNECTION IF EXISTS {CONN}"]
        for statement in statements:
            ok, outcome = self.sys.attempt(statement)
            if not ok:
                problems.append(f"{statement}: {outcome}")
        return problems

    def setup_database(self) -> None:
        self.meta["db_version"] = self.sys.value(
            "SELECT PARAM_VALUE FROM EXA_METADATA WHERE PARAM_NAME = 'databaseProductVersion'"
        )
        self.meta["script_languages"] = self.sys.value(
            "SELECT SYSTEM_VALUE FROM EXA_PARAMETERS WHERE PARAMETER_NAME = 'SCRIPT_LANGUAGES'"
        )
        self.meta["other_active_sessions_at_start"] = self.other_active_sessions()
        self.meta["started_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        self.meta["preclean_problems"] = self.drop_probe_objects()
        dummy_key = "probe-dummy-" + secrets.token_hex(12)
        for statement in [
            f'CREATE USER {OWNER} IDENTIFIED BY "{self.owner_password}"',
            f"GRANT CREATE SESSION, CREATE SCHEMA, CREATE TABLE, CREATE VIEW, CREATE SCRIPT,"
            f" CREATE VIRTUAL SCHEMA TO {OWNER}",
            f'CREATE USER {READER} IDENTIFIED BY "{self.reader_password}"',
            f"GRANT CREATE SESSION TO {READER}",
            f"CREATE CONNECTION {CONN} TO 'http://192.0.2.1:9' USER 'probe' IDENTIFIED BY {lit(dummy_key)}",
        ]:
            self.sys.run(statement)
        self.owner = Session(OWNER, self.owner_password)
        self.reader = Session(READER, self.reader_password)
        owner = self.as_owner()
        for name in (INFRA, META, CORE):
            owner.run(f"CREATE SCHEMA {name}")
        owner.run(
            f"CREATE TABLE {META}.LOAD_LOG (TABLE_NAME VARCHAR(128) UTF8 NOT NULL,"
            " SLICE_KEY VARCHAR(2000) UTF8 NOT NULL, IS_FINAL BOOLEAN NOT NULL)"
        )
        owner.run(
            f"CREATE TABLE {CORE}.T_RT ("
            + ", ".join(f"{n} {sql_type(t)}" for n, t in RT_COLUMNS if n != "SRC")
            + ")"
        )
        owner.run(
            f"CREATE TABLE {CORE}.T_CONC ("
            + ", ".join(f"{n} {sql_type(t)}" for n, t in CONC_COLUMNS)
            + ")"
        )
        log_rows = [
            ("stored:a", True),
            ("stored:b", True),
            ("stored:empty", True),
            ("nf:1", False),
        ] + [(key, True) for key in big_keys() if key.startswith("stored:")]
        for start in range(0, len(log_rows), 200):
            chunk = log_rows[start : start + 200]
            owner.run(
                f"INSERT INTO {META}.LOAD_LOG VALUES "
                + ", ".join(
                    f"({lit(RT_LOGICAL_TABLE)}, {lit(key)}, {str(final).upper()})"
                    for key, final in chunk
                )
            )
        owner.run(
            f"INSERT INTO {CORE}.T_RT VALUES"
            " ('stored:a', 10, TIMESTAMP '2024-08-24 16:00:00', TRUE, 2.5),"
            " ('stored:a', 11, TIMESTAMP '2024-08-24 16:00:00', TRUE, 2.5),"
            " ('stored:b', 12, TIMESTAMP '2024-08-24 16:00:00', FALSE, 2.5),"
            " ('nf:1', 13, TIMESTAMP '2024-08-24 16:00:00', FALSE, 2.5),"
            " ('stored:zzz', 14, TIMESTAMP '2024-08-24 16:00:00', FALSE, 2.5)"
        )

    def setup_udfs(self) -> None:
        owner = self.as_owner()
        for sql in udf_scripts().values():
            owner.run(sql)
        owner.run(f"GRANT SELECT ON {VS} TO {READER}")
        self.udfs_ready = True

    def cleanup(self) -> dict[str, Any]:
        report: dict[str, Any] = {}
        report["sidecar"] = remove_sidecar()
        for session in (self.owner, self.reader):
            if session is not None:
                session.close()
        report["drop_problems"] = self.drop_probe_objects()
        report["bucketfs"] = self.cleanup_bucketfs()
        report["leftovers"] = {
            "schemas": self.sys.rows(
                "SELECT SCHEMA_NAME FROM EXA_SCHEMAS WHERE SCHEMA_NAME LIKE 'PROBE%'"
            ),
            "users": self.sys.rows(
                "SELECT USER_NAME FROM EXA_DBA_USERS WHERE USER_NAME LIKE 'PROBE%'"
            ),
            "connections": self.sys.rows(
                "SELECT CONNECTION_NAME FROM EXA_DBA_CONNECTIONS WHERE CONNECTION_NAME LIKE 'PROBE%'"
            ),
        }
        return report

    def cleanup_bucketfs(self) -> dict[str, Any]:
        removed = []
        for path in (
            BUCKETFS_HOST_ROOT / PROBE_SERVICE,
            BUCKETFS_HOST_ROOT / DEFAULT_SERVICE / DEFAULT_BUCKET / DEFAULT_SUBDIR,
        ):
            if path.exists():
                remove_tree(path)
                removed.append(str(path))
        for path in sorted(self.created_default_dirs, key=lambda p: len(p.parts), reverse=True):
            if path.exists() and not any(path.iterdir()):
                path.rmdir()
                removed.append(str(path))
        include_default = bool(self.created_default_dirs)
        started = time.time()
        while time.time() - started < 15 and probe_lines_in_bucketfs_conf(include_default):
            time.sleep(0.25)
        return {
            "removed": removed,
            "conf_lines_left": probe_lines_in_bucketfs_conf(include_default),
            "conf_removal_s": round(time.time() - started, 2),
        }

    # ------------------------------------------------------------ (a)

    def probe_a(self) -> Finding:
        owner = self.as_owner()
        _, create_ms = timed(lambda: owner.run(adapter_sql("PROBE_ADAPTER", "")))
        _, vs_ms = timed(
            lambda: owner.run(
                f"CREATE VIRTUAL SCHEMA {VS} USING {INFRA}.PROBE_ADAPTER WITH PROBE_SET = 'main'"
            )
        )
        basic = owner.rows(f"SELECT P_YEAR, NAME FROM {VS}.T_BASIC")
        table_count = owner.value(
            f"SELECT COUNT(*) FROM EXA_ALL_TABLES WHERE TABLE_SCHEMA = {lit(VS)}"
        )
        expected_tables = len(main_set()["tables"])
        env = owner.rows(f"SELECT PY_VERSION FROM {VS}.T_ADAPTER_ENV")
        reject_ok, reject = owner.attempt(f"SELECT * FROM {VS}.T_REJECT")
        explain_ok, explain_reject = owner.attempt(f"EXPLAIN VIRTUAL SELECT * FROM {VS}.T_REJECT")
        rule_text = (
            "CFBD_VS.T_REJECT: rule probe.year_required: P_YEAR is required (probe, 0 calls)"
        )
        passed = basic == [(2024, "a")] and table_count == expected_tables
        return Finding(
            "a",
            "pass" if passed else "fail",
            f"adapter + VS created ({create_ms} ms / {vs_ms} ms); T_BASIC returned {basic}; "
            f"{table_count}/{expected_tables} virtual tables; adapter Python {env[0][0] if env else '?'}; "
            f"adapter exception text reaches the client verbatim: {(not reject_ok) and rule_text in str(reject)}",
            {
                "basic_rows": basic,
                "table_count": table_count,
                "adapter_python": env,
                "adapter_error_query": reject,
                "adapter_error_explain": explain_reject
                if not explain_ok
                else "EXPLAIN did not fail",
            },
        )

    # ------------------------------------------------------------ (b)

    def probe_b(self) -> Finding:
        info = self.diag("slc")
        modules = info.get("modules", {})
        missing = sorted(name for name, state in modules.items() if state != "ok")
        version = str(info.get("sys.version", ""))
        paths = info.get("verify_paths", {})
        stats = info.get("ca_cert_store_stats", {})
        ca_ok = bool(paths.get("openssl_cafile_exists")) or bool(paths.get("openssl_capath_exists"))
        passed = version.startswith("3.12") and not missing and ca_ok
        return Finding(
            "b",
            "pass" if passed else "fail",
            f"Python {version.split()[0] if version else '?'}, {info.get('openssl')}; stdlib modules missing: "
            f"{missing or 'none'}; CA store {paths.get('openssl_cafile')} exists={paths.get('openssl_cafile_exists')} "
            f"(stats {stats}); fromisoformat('…Z') -> {info.get('fromisoformat_Z')}; cpu_count {info.get('cpu_count')}",
            info,
        )

    # ------------------------------------------------------------ (c), (d)

    def probe_c(self) -> Finding:
        owner = self.as_owner()
        dyn_ok, dyn = owner.attempt(f"SELECT K, V, TS, FLAG, D, SRC FROM {VS}.T_DYN ORDER BY K, V")
        rt_ok, rt = owner.attempt(f"SELECT K, V, SRC FROM {VS}.T_RT ORDER BY K, V")
        narrow_ok, narrow = owner.attempt(f"SELECT * FROM {VS}.T_DYN_NARROW ORDER BY 1, 2")
        wide_ok, wide = owner.attempt(f"SELECT * FROM {VS}.T_DYN_WIDE ORDER BY 1, 2")
        renamed_ok, renamed = owner.attempt(f"SELECT * FROM {VS}.T_DYN_RENAMED ORDER BY 1, 2")
        types = owner.rows(
            f"SELECT COLUMN_TABLE, COLUMN_NAME, COLUMN_TYPE FROM EXA_ALL_COLUMNS WHERE COLUMN_SCHEMA = {lit(VS)}"
            " AND COLUMN_TABLE LIKE 'T\\_DYN%' ESCAPE '\\' ORDER BY COLUMN_TABLE, COLUMN_ORDINAL_POSITION"
        )
        passed = dyn_ok and isinstance(dyn, list) and len(dyn) == 4 and rt_ok
        return Finding(
            "c",
            "pass" if passed else "fail",
            f"dynamic EMITS inside pushdown: T_DYN {len(dyn) if dyn_ok else 'ERR'} rows, read-through T_RT "
            f"{'ok' if rt_ok else 'ERR'}; EMITS types narrower than VS types: {'ok' if narrow_ok else 'rejected'}; "
            f"wider: {'ok' if wide_ok else 'rejected'}; same types, other names: "
            f"{'ok' if renamed_ok else 'rejected'}",
            {
                "t_dyn": dyn,
                "t_rt": rt,
                "narrow": narrow,
                "wide": wide,
                "renamed": renamed,
                "vs_column_types": types,
            },
        )

    def probe_d(self) -> Finding:
        owner = self.as_owner()
        plain = owner.rows("SELECT X, Y FROM (VALUES (1, 'a'), (2, 'bbb')) AS R(X, Y) ORDER BY X")
        owner.run(
            f"CREATE OR REPLACE TABLE {CORE}.VALUES_TYPES AS SELECT * FROM (VALUES ('a'), ('bbbb')) AS R(K)"
        )
        value_type = owner.rows(
            f"SELECT COLUMN_NAME, COLUMN_TYPE FROM EXA_ALL_COLUMNS WHERE COLUMN_SCHEMA = {lit(CORE)}"
            " AND COLUMN_TABLE = 'VALUES_TYPES'"
        )
        padded = owner.rows(
            "SELECT '[' || K || ']', LENGTH(K) FROM (VALUES ('a'), ('bbbb')) AS R(K) ORDER BY 2"
        )
        returned = owner.rows(f"SELECT DISTINCT '[' || K || ']', SRC FROM {VS}.T_RT ORDER BY 1")
        udf_keys = [key for key, source in returned if source == "LIVE"]
        big_ok, big = owner.attempt(f"SELECT COUNT(*), COUNT(DISTINCT K) FROM {VS}.T_RT_BIG")
        passed = plain == [(1, "a"), (2, "bbb")] and big_ok
        return Finding(
            "d",
            "pass" if passed else "fail",
            f"VALUES works directly and inside pushdown SQL (6 rows; {2 * BIG_SLICES_PER_KIND} rows in T_RT_BIG: "
            f"{big}); string VALUES column type {value_type} (VARCHAR, not CHAR: no blank padding); values seen "
            f"by SQL {padded}; slice keys echoed by the UDF {udf_keys}",
            {
                "plain": plain,
                "value_type": value_type,
                "padded": padded,
                "udf_keys": udf_keys,
                "big": big,
            },
        )

    # ------------------------------------------------------------ (e)

    def echo_filter(self, predicate: str) -> dict[str, Any]:
        owner = self.as_owner()
        query = f"SELECT * FROM {VS}.T_ECHO WHERE {predicate}"
        try:
            explained = self.explain(owner, query)
        except Exception as error:
            return {"predicate": predicate, "error": error_text(error)}
        sql = self.pushdown_sql(explained)
        match = re.search(r"PROBE_FILTER:([A-Za-z0-9+/=]*)", sql)
        pushed = json.loads(base64.b64decode(match.group(1))) if match else "NO PUSHDOWN SQL"
        return {"predicate": predicate, "pushed": pushed, "explain_rows": len(explained["rows"])}

    def probe_e(self) -> Finding:
        owner = self.as_owner()
        partial = self.echo_filter("P_YEAR = 2024 AND UPPER(NAME) = 'B'")
        partial_rows = owner.rows(
            f"SELECT NAME FROM {VS}.T_ECHO WHERE P_YEAR = 2024 AND UPPER(NAME) = 'B'"
        )
        trust_rows = owner.rows(
            f"SELECT P_YEAR, NAME FROM {VS}.T_ECHO WHERE P_YEAR = 2024 ORDER BY NAME"
        )
        mixed_or = self.echo_filter("P_YEAR = 2024 OR UPPER(NAME) = 'B'")
        mixed_or_rows = owner.rows(
            f"SELECT NAME FROM {VS}.T_ECHO WHERE P_YEAR = 2024 OR UPPER(NAME) = 'B' ORDER BY NAME"
        )
        forms = [
            "P_YEAR = 2024",
            "P_YEAR > 2018",
            "P_YEAR >= 2018",
            "P_YEAR < 2030",
            "P_YEAR <= 2030",
            "P_YEAR BETWEEN 2019 AND 2021",
            "P_WEEK IN (8, 9)",
            "P_WEEK IN (8)",
            "P_YEAR <> 2020",
            "NOT (P_YEAR = 2020)",
            "NAME LIKE 'a%'",
            "NAME LIKE 'a!%%' ESCAPE '!'",
            "NAME IS NULL",
            "NAME IS NOT NULL",
            "P_YEAR = 2024 OR P_YEAR = 2025",
            "P_YEAR = 2024 AND P_WEEK = 8",
            "P_YEAR BETWEEN 2019 AND 2021 AND P_WEEK IN (8, 9)",
            "P_YEAR = 2024 AND P_YEAR = 2025",
            "P_YEAR BETWEEN 2021 AND 2019",
            "P_YEAR = '2024'",
            "P_YEAR = 2020 + 4",
            "P_YEAR = 2024.0",
            "P_YEAR = 2.024E3",
            "P_YEAR = (SELECT 2024 FROM DUAL)",
            "P_YEAR = P_WEEK",
            "P_YEAR + 0 = 2024",
            "NAME = 'O''Brien'",
            "P_FLAG = TRUE",
            "P_FLAG",
            "NOT P_FLAG",
            "UPPER(NAME) = 'B'",
            "P_YEAR = NULL",
            "P_YEAR >= 2018 AND P_YEAR <= 2020",
        ]
        shapes = [self.echo_filter(form) for form in forms]
        arrived = {shape["predicate"]: shape.get("pushed") for shape in shapes}
        capability_check = {
            form: isinstance(arrived.get(form), dict) and arrived[form].get("type") == expected_type
            for form, expected_type in ADVERTISED_PREDICATE_SHAPES.items()
        }
        request_sql = self.pushdown_sql(
            self.explain(owner, f"SELECT * FROM {VS}.T_ECHO WHERE P_YEAR = 2024")
        )
        request_match = re.search(r"PROBE_REQUEST:([A-Za-z0-9+/=]*)", request_sql)
        sample_request = (
            json.loads(base64.b64decode(request_match.group(1))) if request_match else None
        )
        pushed_partial = partial.get("pushed")
        split_ok = (
            isinstance(pushed_partial, dict) and pushed_partial.get("type") == "predicate_equal"
        )
        local_ok = partial_rows == [("B",)]
        trusts = len(trust_rows) == 3
        missing = sorted(form for form, ok in capability_check.items() if not ok)
        return Finding(
            "e",
            "pass" if split_ok and local_ok and not missing else "fail",
            f"`P_YEAR = 2024 AND UPPER(NAME) = 'B'` pushes only {json.dumps(pushed_partial)[:120]} and Exasol "
            f"evaluates UPPER locally ({partial_rows}); pushed predicates are "
            f"{'NOT re-checked (adapter rows with P_YEAR=2023 come back)' if trusts else 're-checked by Exasol'}; "
            f"a mixed OR pushes {json.dumps(mixed_or.get('pushed'))[:60]} and is evaluated locally "
            f"({mixed_or_rows}); advertised predicates that did not arrive: {missing or 'none'}",
            {
                "capability_check": capability_check,
                "partial": partial,
                "partial_rows": partial_rows,
                "trust_rows": trust_rows,
                "mixed_or": mixed_or,
                "mixed_or_rows": mixed_or_rows,
                "shapes": shapes,
                "sample_pushdown_request": sample_request,
            },
        )

    # ------------------------------------------------------------ (f)

    def probe_f(self) -> Finding:
        owner = self.as_owner()
        runs = []
        for table_name in ("T_RT", "T_RT_SET", "T_RT_ONEGROUP"):
            for _ in range(3):
                ok, rows = owner.attempt(f"SELECT K, V, SRC FROM {VS}.{table_name} ORDER BY K, V")
                runs.append(
                    {
                        "table": table_name,
                        "ok": ok,
                        "matches": ok and sorted(rows) == RT_EXPECTED,
                        "rows": rows,
                    }
                )
        controls = {}
        for table_name in ("T_RT_NOGUARD", "T_RT_SET_NOGUARD", "T_RT_ONEGROUP_NOGUARD"):
            ok, outcome = owner.attempt(f"SELECT K, V, SRC FROM {VS}.{table_name}")
            controls[table_name] = {
                "ok": ok,
                "marker_raised": (not ok) and "PROBE_MARKER" in str(outcome),
                "outcome": outcome,
            }
        big_ok, big = owner.attempt(
            f"SELECT COUNT(*), COUNT(DISTINCT K), SUM(CASE WHEN SRC = 'LIVE' THEN 1 ELSE 0 END) FROM {VS}.T_RT_BIG"
        )
        direct_ok, direct = owner.attempt(read_through_sql(RT_KEYS) + " ORDER BY K, V")
        all_runs_ok = all(run["matches"] for run in runs)
        controls_ok = all(control["marker_raised"] for control in controls.values())
        big_expected = [(2 * BIG_SLICES_PER_KIND, BIG_SLICES_PER_KIND, 2 * BIG_SLICES_PER_KIND)]
        passed = all_runs_ok and controls_ok and big_ok and big == big_expected and direct_ok
        return Finding(
            "f",
            "pass" if passed else "fail",
            f"read-through shape returned exactly the expected 9 rows in {sum(r['matches'] for r in runs)}/{len(runs)} "
            f"runs (scalar, SET + GROUP BY, one-group SET) with the marker UDF never raising; controls without NOT EXISTS raised the "
            f"marker: {controls_ok}; 1000-slice variant {big} (expected {big_expected[0]})",
            {
                "runs": runs,
                "controls": controls,
                "big": big,
                "direct": direct if not direct_ok else len(direct),
            },
        )

    # ------------------------------------------------------------ (g)

    def probe_g(self) -> Finding:
        reader = self.as_reader()
        positives = {
            "vs_read_through": reader.attempt(f"SELECT K, V, SRC FROM {VS}.T_RT ORDER BY K, V"),
            "vs_dynamic_udf": reader.attempt(f"SELECT COUNT(*) FROM {VS}.T_DYN"),
            "explain_virtual": reader.attempt(f"EXPLAIN VIRTUAL SELECT * FROM {VS}.T_RT"),
        }
        negatives = {
            "core_table": reader.attempt(f"SELECT * FROM {CORE}.T_RT"),
            "load_log": reader.attempt(f"SELECT * FROM {META}.LOAD_LOG"),
            "fetch_udf_direct": reader.attempt(
                f"SELECT {INFRA}.FETCH_SCALAR('live:x') EMITS (K VARCHAR(100) UTF8) FROM DUAL"
            ),
            "read_conn_direct": reader.attempt(f"SELECT {INFRA}.READ_CONN()"),
        }
        privileges = self.sys.rows(
            "SELECT PRIVILEGE, OBJECT_SCHEMA, OBJECT_NAME, OBJECT_TYPE FROM EXA_DBA_OBJ_PRIVS"
            f" WHERE GRANTEE = {lit(READER)} ORDER BY 1, 2, 3"
        )
        rt_ok, rt_rows = positives["vs_read_through"]
        positive_ok = rt_ok and sorted(rt_rows) == RT_EXPECTED and positives["vs_dynamic_udf"][0]
        negative_ok = all(not ok for ok, _ in negatives.values())
        return Finding(
            "g",
            "pass" if positive_ok and negative_ok else "fail",
            f"reader with only SELECT ON {VS} ran the read-through query (stored table, LOAD_LOG, fetch UDF): "
            f"{positive_ok}; direct access to CORE/META/UDFs denied: {negative_ok}; reader EXPLAIN VIRTUAL "
            f"{'allowed' if positives['explain_virtual'][0] else 'denied'}",
            {
                "positives": {k: (ok, v if not ok else len(v)) for k, (ok, v) in positives.items()},
                "negatives": negatives,
                "reader_object_privileges": privileges,
            },
        )

    # ------------------------------------------------------------ (h)

    def aggregate(self, session: Session, sql: str) -> dict[str, Any]:
        ok, outcome = session.attempt(sql)
        if not ok and ("connection" in str(outcome).lower() or "closed" in str(outcome).lower()):
            session.reconnect()
            return {"sql": sql, "ok": False, "crashed_session": True, "outcome": outcome}
        return {"sql": sql, "ok": ok, "outcome": outcome}

    def probe_h(self) -> Finding:
        owner = self.as_owner()
        checks = {
            "count_sum": (
                f"SELECT COUNT(*), SUM(V) FROM {VS}.T_RT",
                [(len(RT_EXPECTED), RT_EXPECTED_SUM)],
            ),
            "group_by": (
                f"SELECT SRC, COUNT(*), SUM(V) FROM {VS}.T_RT GROUP BY SRC ORDER BY SRC",
                [("LIVE", 6, 9), ("STORED", 3, 33)],
            ),
            "count_distinct": (f"SELECT COUNT(DISTINCT K) FROM {VS}.T_RT", [(5,)]),
            "set_form": (
                f"SELECT COUNT(*), SUM(V) FROM {VS}.T_RT_SET",
                [(len(RT_EXPECTED), RT_EXPECTED_SUM)],
            ),
            "onegroup_form": (
                f"SELECT SRC, COUNT(*), SUM(V) FROM {VS}.T_RT_ONEGROUP GROUP BY SRC ORDER BY SRC",
                [("LIVE", 6, 9), ("STORED", 3, 33)],
            ),
            "user_union_all": (
                f"SELECT COUNT(*) FROM (SELECT K FROM {VS}.T_RT UNION ALL SELECT K FROM {CORE}.T_RT)",
                [(len(RT_EXPECTED) + 5,)],
            ),
            "user_union_all_group": (
                f"SELECT SRC, COUNT(*) FROM (SELECT SRC FROM {VS}.T_RT UNION ALL"
                f" SELECT 'CORE' FROM {CORE}.T_RT) GROUP BY SRC ORDER BY SRC",
                [("CORE", 5), ("LIVE", 6), ("STORED", 3)],
            ),
            "reader_count_sum": (
                f"SELECT COUNT(*), SUM(V) FROM {VS}.T_RT",
                [(len(RT_EXPECTED), RT_EXPECTED_SUM)],
            ),
        }
        results = {}
        for name, (sql, expected) in checks.items():
            session = self.as_reader() if name.startswith("reader") else owner
            outcome = self.aggregate(session, sql)
            outcome["expected"] = expected
            outcome["correct"] = outcome["ok"] and normalized(outcome["outcome"]) == normalized(
                expected
            )
            results[name] = outcome
        all_ok = all(result["correct"] for result in results.values())
        return Finding(
            "h",
            "pass" if all_ok else "fail",
            f"COUNT/SUM/GROUP BY/COUNT DISTINCT over the read-through VS (scalar and SET forms) and a user-side "
            f"UNION ALL with a VS branch: {sum(r['correct'] for r in results.values())}/{len(results)} correct, "
            f"no crash on {self.meta.get('db_version')} with the planned (filter-only) capabilities",
            {"checks": results},
        )

    def probe_h_crash_trigger(self) -> dict[str, Any]:
        owner = self.as_owner()
        owner.run(
            f"CREATE VIRTUAL SCHEMA {VS_AGG} USING {INFRA}.PROBE_ADAPTER WITH PROBE_SET = 'agg'"
        )
        session = Session(OWNER, self.owner_password)
        try:
            sql = f"SELECT COUNT(*) FROM (SELECT X FROM {VS_AGG}.T UNION ALL SELECT V FROM {CORE}.T_RT)"
            outcome = self.aggregate(session, sql)
        finally:
            session.close()
        alive = self.sys.value("SELECT 1 FROM DUAL")
        return {
            "capabilities": CRASH_TRIGGER_CAPABILITIES,
            "outcome": outcome,
            "db_alive_after": alive == 1,
        }

    # ------------------------------------------------------------ (i)

    def probe_i(self) -> Finding:
        owner = self.as_owner()
        owner.run(
            f"CREATE VIRTUAL SCHEMA {VS_COMMENTS} USING {INFRA}.PROBE_ADAPTER WITH PROBE_SET = 'comments'"
        )
        columns = dict(
            owner.rows(
                f"SELECT COLUMN_NAME, COLUMN_COMMENT FROM EXA_ALL_COLUMNS WHERE COLUMN_SCHEMA = {lit(VS_COMMENTS)}"
            )
        )
        table_comment = owner.value(
            f"SELECT TABLE_COMMENT FROM EXA_ALL_TABLES WHERE TABLE_SCHEMA = {lit(VS_COMMENTS)} AND TABLE_NAME = 'T_COMMENTS'"
        )
        tricky_ok = columns.get("C_TRICKY") == TRICKY_COMMENT
        long_ok = columns.get("C_LONG") == COMMENT_2000
        none_ok = columns.get("C_NONE") is None
        limit_ok, limit = owner.attempt(
            f"CREATE VIRTUAL SCHEMA {VS_COMMENT_LIMIT} USING {INFRA}.PROBE_ADAPTER WITH PROBE_SET = 'comment_limit'"
        )
        limit_len = None
        if limit_ok:
            limit_len = owner.value(
                f"SELECT LENGTH(COLUMN_COMMENT) FROM EXA_ALL_COLUMNS WHERE COLUMN_SCHEMA = {lit(VS_COMMENT_LIMIT)}"
                " AND COLUMN_NAME = 'C_LONG'"
            )
        emoji_ok, emoji = owner.attempt(
            f"CREATE VIRTUAL SCHEMA {VS_COMMENT_EMOJI} USING {INFRA}.PROBE_ADAPTER WITH PROBE_SET = 'comment_emoji'"
        )
        emoji_stored = None
        if emoji_ok:
            emoji_stored = owner.value(
                f"SELECT COLUMN_COMMENT FROM EXA_ALL_COLUMNS WHERE COLUMN_SCHEMA = {lit(VS_COMMENT_EMOJI)}"
                " AND COLUMN_NAME = 'C_TRICKY'"
            )
        passed = tricky_ok and long_ok and none_ok
        return Finding(
            "i",
            "pass" if passed else "fail",
            f"column comments appear verbatim in EXA_ALL_COLUMNS (quotes, newline, /* */, umlauts: {tricky_ok}; "
            f"2000 chars: {long_ok}; none -> NULL: {none_ok}); table comment verbatim: "
            f"{table_comment == 'table comment: ' + TRICKY_COMMENT}; 2001-char comment: "
            f"{'accepted, stored length ' + str(limit_len) if limit_ok else 'rejected: ' + str(limit)[:160]}; "
            f"non-BMP emoji: {'stored as ' + repr(emoji_stored) if emoji_ok else 'rejected: ' + str(emoji)[:160]}",
            {
                "columns": columns,
                "table_comment": table_comment,
                "limit": {"ok": limit_ok, "outcome": limit, "stored_length": limit_len},
                "emoji": {"ok": emoji_ok, "outcome": emoji, "stored": emoji_stored},
            },
        )

    # ------------------------------------------------------------ (j)

    def probe_j(self) -> Finding:
        owner = self.as_owner()
        results = {}
        for table_name, keys in (("T_RT", RT_KEYS), ("T_RT_BIG", big_keys())):
            explained = self.explain(owner, f"SELECT * FROM {VS}.{table_name}")
            sql = self.pushdown_sql(explained)
            survives = sql.startswith("/*PROBE ")
            parsed = None
            if survives:
                parsed = json.loads(sql[len("/*PROBE ") : sql.index("*/")])
            results[table_name] = {
                "columns": explained["columns"],
                "leading_comment_survives": survives,
                "manifest_roundtrip": parsed == {"table": RT_LOGICAL_TABLE, "slices": keys},
                "comment_chars": len(manifest_comment(keys)),
                "sql_chars": len(sql),
                "sql_head": sql[:160],
                "pushdown_json_head": str(explained["rows"][0].get("PUSHDOWN_JSON", ""))[:400],
                "involved_tables": explained["rows"][0].get("PUSHDOWN_INVOLVED_TABLES"),
            }
        cte_ok, cte_rows = owner.attempt(f"SELECT K, V, SRC FROM {VS}.T_RT_CTE ORDER BY K, V")
        cte_sql = self.pushdown_sql(self.explain(owner, f"SELECT * FROM {VS}.T_RT_CTE"))
        results["cte_fallback"] = {
            "query_ok": cte_ok and sorted(cte_rows) == RT_EXPECTED,
            "explain_shows_cte": cte_sql.startswith("WITH PROBE_MANIFEST"),
        }
        passed = all(
            results[name]["leading_comment_survives"] and results[name]["manifest_roundtrip"]
            for name in ("T_RT", "T_RT_BIG")
        )
        return Finding(
            "j",
            "pass" if passed else "fail",
            f"leading /*PROBE {{json}}*/ survives verbatim in PUSHDOWN_SQL and round-trips (6 slices and "
            f"{len(big_keys())} slices, {results['T_RT_BIG']['comment_chars']} chars): {passed}; EXPLAIN VIRTUAL columns "
            f"{results['T_RT']['columns']}; CTE fallback also works: {results['cte_fallback']}",
            results,
        )

    # ------------------------------------------------------------ (k)

    def bucket_visible(self, udf_path: str) -> bool:
        return bool(self.diag("stat:" + udf_path)["stat:" + udf_path].get("exists") is True)

    def wait_for(self, check: Callable[[], bool], timeout: float = 20.0) -> float | None:
        started = time.time()
        while time.time() - started < timeout:
            if check():
                return round(time.time() - started, 2)
            time.sleep(0.2)
        return None

    def udf_sha(self, udf_path: str) -> str | None:
        stat = self.diag("stat:" + udf_path)["stat:" + udf_path]
        return stat.get("sha256")

    def probe_k(self) -> Finding:
        service = BUCKETFS_HOST_ROOT / PROBE_SERVICE
        bucket = service / PROBE_BUCKET
        udf_bucket = f"/buckets/{PROBE_SERVICE}/{PROBE_BUCKET}"
        timings: dict[str, Any] = {}
        bucket.mkdir(parents=True, exist_ok=True)
        (bucket / "hello.txt").write_text("hello\n")
        timings["new_bucket_s"] = self.wait_for(
            lambda: self.bucket_visible(f"{udf_bucket}/hello.txt")
        )
        bucket_2 = service / PROBE_BUCKET_2
        bucket_2.mkdir(parents=True, exist_ok=True)
        (bucket_2 / "hello.txt").write_text("hello\n")
        timings["new_bucket_2_s"] = self.wait_for(
            lambda: self.bucket_visible(f"/buckets/{PROBE_SERVICE}/{PROBE_BUCKET_2}/hello.txt")
        )
        conf_lines = probe_lines_in_bucketfs_conf()
        (bucket / "second.txt").write_text("second\n")
        timings["new_file_in_bucket_s"] = self.wait_for(
            lambda: self.bucket_visible(f"{udf_bucket}/second.txt")
        )
        nested = bucket / "games" / "teams" / "0123456789abcdef01234567.cfbd.json"
        nested.parent.mkdir(parents=True, exist_ok=True)
        nested.write_text(
            json.dumps({"slice_key": "/games/teams?year=2024", "status": 200, "body": "[]"})
        )
        timings["new_nested_file_s"] = self.wait_for(
            lambda: self.bucket_visible(
                f"{udf_bucket}/games/teams/0123456789abcdef01234567.cfbd.json"
            )
        )
        (bucket / "hello.txt").write_text("changed\n")
        changed_sha = hashlib.sha256(b"changed\n").hexdigest()
        timings["overwrite_visible_s"] = self.wait_for(
            lambda: self.udf_sha(f"{udf_bucket}/hello.txt") == changed_sha
        )
        (bucket / "second.txt").unlink()
        timings["delete_visible_s"] = self.wait_for(
            lambda: not self.bucket_visible(f"{udf_bucket}/second.txt")
        )
        big = bucket / "big.cfbd.json"
        big_bytes = pad_text(5_000_000).encode()
        big.write_bytes(big_bytes)
        big_sha = hashlib.sha256(big_bytes).hexdigest()
        timings["big_file_s"] = self.wait_for(
            lambda: self.udf_sha(f"{udf_bucket}/big.cfbd.json") == big_sha
        )
        private = bucket / "private.cfbd.json"
        private.write_text("private\n")
        private.chmod(0o600)
        time.sleep(0.5)
        private_stat = self.diag(f"stat:{udf_bucket}/private.cfbd.json")
        loose = service / "loose.txt"
        loose.write_text("loose\n")
        time.sleep(3)
        loose_visible = self.bucket_visible(f"/buckets/{PROBE_SERVICE}/loose.txt")
        write_attempt = self.diag(f"write:{udf_bucket}/udf_write.txt")
        default_bucket = BUCKETFS_HOST_ROOT / DEFAULT_SERVICE / DEFAULT_BUCKET
        default_preexisting = default_bucket.exists()
        for path in (BUCKETFS_HOST_ROOT / DEFAULT_SERVICE, default_bucket):
            if not path.exists():
                self.created_default_dirs.append(path)
        replay_dir = default_bucket / DEFAULT_SUBDIR / "replay" / "games"
        replay_dir.mkdir(parents=True, exist_ok=True)
        (replay_dir / "abcdef0123456789abcdef01.cfbd.json").write_text("{}")
        default_udf = f"/buckets/{DEFAULT_SERVICE}/{DEFAULT_BUCKET}/{DEFAULT_SUBDIR}/replay/games/abcdef0123456789abcdef01.cfbd.json"
        timings["default_bucket_s"] = self.wait_for(lambda: self.bucket_visible(default_udf))
        listing = self.diag("stat:/buckets")
        passed = timings["new_bucket_s"] is not None and timings["default_bucket_s"] is not None
        return Finding(
            "k",
            "pass" if passed else "fail",
            f"host {BUCKETFS_HOST_ROOT}/<service>/<bucket>/<path> -> UDF /buckets/<service>/<bucket>/<path>; new "
            f"bucket visible after {timings['new_bucket_s']} s / {timings['new_bucket_2_s']} s, new file in an "
            f"existing bucket after {timings['new_file_in_bucket_s']} s, nested {timings['new_nested_file_s']} s, "
            f"overwrite {timings['overwrite_visible_s']} s, delete {timings['delete_visible_s']} s, 5 MB file "
            f"{timings['big_file_s']} s; mode-0600 file readable: "
            f"{private_stat.get(f'stat:{udf_bucket}/private.cfbd.json')}; files at service level visible: "
            f"{loose_visible}; UDF write: {write_attempt.get(f'write:{udf_bucket}/udf_write.txt')}; "
            f"/buckets/bfsdefault/default works (pre-existing: {default_preexisting})",
            {
                "timings_s": timings,
                "bucketfs_conf_probe_lines": conf_lines,
                "private_file": private_stat,
                "loose_file_visible": loose_visible,
                "udf_write": write_attempt,
                "default_bucket_preexisting": default_preexisting,
                "buckets_listing": listing,
                "proposed_default_replay_path": f"/buckets/{DEFAULT_SERVICE}/{DEFAULT_BUCKET}/cfbd/replay",
                "proposed_host_dir": str(default_bucket / "cfbd" / "replay"),
            },
        )

    # ------------------------------------------------------------ (l)

    def probe_l(self) -> Finding:
        container = db_container()
        network = (
            podman(["inspect", container, "--format", "{{.HostConfig.NetworkMode}}"])
            if container
            else None
        )
        host_ip = primary_host_ip()
        targets = [
            ("127.0.0.1", 8563, "DB port on the container loopback (same netns as the UDF?)"),
            ("10.0.2.2", 8563, "slirp4netns gateway -> host loopback"),
            ("10.255.255.254", 53, "WSL DNS listener on a non-loopback host address"),
            (host_ip, 8563, "host eth0 IP, DB port bound to host 127.0.0.1 only"),
        ]
        tcp = {
            f"{ip}:{port}": {"what": what, **self.diag(f"tcp:{ip}:{port}")}
            for ip, port, what in targets
        }
        port = random.randint(40000, 49999)
        sidecar: dict[str, Any] = {"container": container, "port": port}
        status = "blocked"
        if container is None:
            sidecar["error"] = "DB container not found"
            status = "error"
        else:
            image = slc_python_image()
            sidecar["image"] = image
            started = run_command(
                [
                    "podman",
                    "run",
                    "-d",
                    "--rm",
                    "--name",
                    SIDECAR_NAME,
                    "--network",
                    f"container:{container}",
                    "--entrypoint",
                    "python3",
                    image,
                    "-c",
                    SIDECAR_SERVER,
                    str(port),
                    "90",
                ]
            )
            sidecar["start"] = started
            if started["returncode"] == 0:
                listening = self.wait_for(
                    lambda: "LISTENING" in podman(["logs", SIDECAR_NAME]), timeout=30
                )
                sidecar["listening_after_s"] = listening
                url = f"http://127.0.0.1:{port}/probe"
                sidecar["udf_http"] = self.diag("http:" + url).get("http:" + url)
                sidecar["host_http_127"] = host_http_probe(port)
                sidecar["logs"] = podman(["logs", SIDECAR_NAME])
                sidecar["stop"] = remove_sidecar()
                ok = "HTTP 200" in str(sidecar["udf_http"]) and "probe-sidecar-ok" in str(
                    sidecar["udf_http"]
                )
                status = "pass" if ok else "fail"
            else:
                status = "blocked" if "denied" in started["stderr"].lower() else "error"
        return Finding(
            "l",
            status,
            f"DB container {container} network {network}; UDF -> sidecar on 127.0.0.1:{port} in the DB container's "
            f"netns: {sidecar.get('udf_http', sidecar.get('start', {}).get('stderr', ''))!s:.120}; TCP from the UDF: "
            + "; ".join(
                f"{target} {str(info.get('tcp:' + target, ''))[:40]}"
                for target, info in tcp.items()
            ),
            {"tcp": tcp, "sidecar": sidecar, "host_ip": host_ip, "slirp4netns": slirp_flags()},
        )

    # ------------------------------------------------------------ (m)

    def concurrency(self, label: str, sql: str, count: int = CONCURRENCY_SLICES) -> dict[str, Any]:
        owner = self.as_owner()
        rows, wall_ms = timed(lambda: owner.rows(sql))
        intervals = [(float(t0), float(t1)) for _, t0, t1, _, _ in rows]
        starts = sorted(t0 for t0, _ in intervals)
        span = max(t1 for _, t1 in intervals) - min(t0 for t0, _ in intervals) if intervals else 0.0
        return {
            "label": label,
            "rows": len(rows),
            "client_wall_ms": wall_ms,
            "udf_span_s": round(span, 2),
            "serial_s": count * SLOW_SECONDS,
            "max_overlap": max_overlap(intervals),
            "distinct_pids": len({row[3] for row in rows}),
            "distinct_vms": len({row[4] for row in rows}),
            "start_offsets_s": [round(start - starts[0], 2) for start in starts],
        }

    def probe_m(self) -> Finding:
        sessions_before = self.other_active_sessions()
        variants = [
            ("scalar direct", slow_sql("scalar")),
            ("set + GROUP BY direct", slow_sql("set")),
            ("scalar via VS", f"SELECT * FROM {VS}.T_CONC_SCALAR"),
            ("set + GROUP BY via VS", f"SELECT * FROM {VS}.T_CONC_SET"),
            ("scalar read-through via VS", f"SELECT * FROM {VS}.T_CONC_RT"),
            ("set read-through via VS", f"SELECT * FROM {VS}.T_CONC_RT_SET"),
            ("set + GROUP BY via VS (repeat)", f"SELECT * FROM {VS}.T_CONC_SET"),
            ("set read-through via VS (repeat)", f"SELECT * FROM {VS}.T_CONC_RT_SET"),
        ]
        results = [self.concurrency(label, sql) for label, sql in variants]
        results.append(
            self.concurrency(
                "set read-through via VS, 10 slices", f"SELECT * FROM {VS}.T_CONC_RT_SET10", 10
            )
        )
        results.append(
            self.concurrency(
                "one SET group + thread pool, read-through via VS, 10 slices",
                f"SELECT * FROM {VS}.T_CONC_RT_POOL10",
                10,
            )
        )
        by_label = {result["label"]: result for result in results}
        scalar_parallel = (
            by_label["scalar read-through via VS"]["max_overlap"] >= CONCURRENCY_SLICES // 2
        )
        set_parallel = by_label["set read-through via VS"]["max_overlap"] >= CONCURRENCY_SLICES // 2
        summary = "; ".join(
            f"{r['label']}: span {r['udf_span_s']} s, overlap {r['max_overlap']}, VMs {r['distinct_vms']}"
            for r in results
        )
        status = "pass" if scalar_parallel else "fail"
        return Finding(
            "m",
            status,
            f"{CONCURRENCY_SLICES} slices x {SLOW_SECONDS} s sleep: {summary}",
            {
                "variants": results,
                "scalar_parallel": scalar_parallel,
                "set_parallel": set_parallel,
                "other_active_sessions_before": sessions_before,
                "other_active_sessions_after": self.other_active_sessions(),
            },
        )

    # ------------------------------------------------------------ (n)

    def big_udf(self, size: int) -> dict[str, Any]:
        owner = self.as_owner()
        pad = pad_text(size)
        body = f'import hashlib\nPAD = """{pad}"""\n\n\ndef run(ctx):\n    return "%d:%s" % (len(PAD), hashlib.sha256(PAD.encode()).hexdigest())\n'
        sql = script_sql(
            f"CREATE OR REPLACE PYTHON3 SCALAR SCRIPT {INFRA}.BIG_UDF() RETURNS VARCHAR(200) UTF8",
            body,
        )
        expected = f"{len(pad)}:{hashlib.sha256(pad.encode()).hexdigest()}"
        (created, create_ms) = timed(lambda: owner.attempt(sql))
        result: dict[str, Any] = {
            "body_bytes": len(body.encode()),
            "statement_bytes": len(sql.encode()),
            "create_ms": create_ms,
        }
        if not created[0]:
            result["create_error"] = created[1]
            return result
        first, first_ms = timed(lambda: owner.attempt(f"SELECT {INFRA}.BIG_UDF()"))
        _second, second_ms = timed(lambda: owner.attempt(f"SELECT {INFRA}.BIG_UDF()"))
        stored = owner.value(
            f"SELECT LENGTH(SCRIPT_TEXT) FROM EXA_ALL_SCRIPTS WHERE SCRIPT_SCHEMA = {lit(INFRA)} AND SCRIPT_NAME = 'BIG_UDF'"
        )
        result.update(
            {
                "first_call_ms": first_ms,
                "second_call_ms": second_ms,
                "intact": first[0] and first[1] == [(expected,)],
                "call_error": None if first[0] else first[1],
                "stored_script_text_chars": stored,
            }
        )
        return result

    def explain_latency(self, schema: str) -> float:
        owner = self.as_owner()
        samples = []
        for _ in range(5):
            _, elapsed = timed(lambda: self.explain(owner, f"SELECT * FROM {schema}.T_BASIC"))
            samples.append(elapsed)
        return statistics.median(samples)

    def probe_n(self) -> Finding:
        owner = self.as_owner()
        udf_2mb = self.big_udf(SCRIPT_TEXT_LIMIT - 10_000)
        udf_8mb = self.big_udf(SCRIPT_TEXT_LIMIT + 1_000)
        adapter_overhead = len(adapter_sql("PROBE_ADAPTER_BIG", "x"))
        pad = pad_text(SCRIPT_TEXT_LIMIT - adapter_overhead - 10_000)
        adapter = adapter_sql("PROBE_ADAPTER_BIG", pad)
        created, create_ms = timed(lambda: owner.attempt(adapter))
        adapter_result: dict[str, Any] = {
            "statement_bytes": len(adapter.encode()),
            "create_ms": create_ms,
            "ok": created[0],
        }
        if created[0]:
            owner.run(
                f"CREATE VIRTUAL SCHEMA {VS_BIG} USING {INFRA}.PROBE_ADAPTER_BIG WITH PROBE_SET = 'main'"
            )
            pad_row = owner.rows(f"SELECT PAD_LEN, PAD_SHA FROM {VS_BIG}.T_PAD")
            adapter_result["intact"] = pad_row == [
                (len(pad), hashlib.sha256(pad.encode()).hexdigest())
            ]
            adapter_result["explain_median_ms_big"] = self.explain_latency(VS_BIG)
            adapter_result["explain_median_ms_small"] = self.explain_latency(VS)
        else:
            adapter_result["error"] = created[1]
        passed = bool(udf_2mb.get("intact")) and bool(adapter_result.get("intact"))
        return Finding(
            "n",
            "pass" if passed else "fail",
            f"UDF with a {udf_2mb.get('body_bytes')}-byte body: create {udf_2mb.get('create_ms')} ms, first "
            f"call {udf_2mb.get('first_call_ms')} ms, second {udf_2mb.get('second_call_ms')} ms, intact "
            f"{udf_2mb.get('intact')}; {udf_8mb.get('body_bytes')}-byte body: "
            f"{'intact' if udf_8mb.get('intact') else udf_8mb.get('create_error') or udf_8mb.get('call_error')}; "
            f"adapter of {adapter_result['statement_bytes']} bytes: intact {adapter_result.get('intact')}, "
            "EXPLAIN VIRTUAL median "
            f"{adapter_result.get('explain_median_ms_big')} ms vs {adapter_result.get('explain_median_ms_small')} ms "
            f"for the small adapter",
            {"udf_2mb": udf_2mb, "udf_8mb": udf_8mb, "adapter_2mb": adapter_result},
        )

    # ------------------------------------------------------------ (o)

    def read_conn(self, session: Session, name: str = "READ_CONN") -> tuple[bool, Any]:
        return session.attempt(f"SELECT {INFRA}.{name}()")

    def restricted_grants(self) -> Any:
        ok, outcome = self.sys.attempt(
            f"SELECT * FROM EXA_DBA_RESTRICTED_OBJ_PRIVS WHERE OBJECT_NAME = {lit(CONN)}"
        )
        return outcome if ok else f"view not readable: {outcome}"

    def grant(self) -> None:
        self.sys.run(f"GRANT ACCESS ON CONNECTION {CONN} FOR SCRIPT {INFRA}.READ_CONN TO {OWNER}")

    def probe_o(self) -> Finding:
        owner = self.as_owner()
        reader = self.as_reader()
        steps: dict[str, Any] = {}
        steps["1_no_grant"] = self.read_conn(owner)
        self.grant()
        steps["2_grant_rows"] = self.restricted_grants()
        steps["3_owner_after_grant"] = self.read_conn(owner)
        steps["4_other_script"] = self.read_conn(owner, "READ_CONN_OTHER")
        steps["5_reader_via_vs"] = reader.attempt(f"SELECT CONN_STATUS FROM {VS}.T_CONN")
        steps["6_reader_direct"] = self.read_conn(reader)
        owner.run(read_conn_sql("READ_CONN"))
        steps["7_rows_after_identical_replace"] = self.restricted_grants()
        steps["7_owner_after_identical_replace"] = self.read_conn(owner)
        self.grant()
        owner.run(read_conn_sql("READ_CONN", extra="\n# changed\n"))
        steps["8_rows_after_changed_replace"] = self.restricted_grants()
        steps["8_owner_after_changed_replace"] = self.read_conn(owner)
        sent = read_conn_sql("READ_CONN", extra="\n# changed\n")
        stored = owner.value(
            f"SELECT SCRIPT_TEXT FROM EXA_ALL_SCRIPTS WHERE SCRIPT_SCHEMA = {lit(INFRA)} AND SCRIPT_NAME = 'READ_CONN'"
        )
        steps["9_script_text"] = script_text_comparison(sent, stored)
        self.grant()
        dropped_identical = not steps["7_owner_after_identical_replace"][0]
        dropped_changed = not steps["8_owner_after_changed_replace"][0]
        script_bound = steps["3_owner_after_grant"][0] and not steps["4_other_script"][0]
        reader_ok = steps["5_reader_via_vs"][0] and not steps["6_reader_direct"][0]
        return Finding(
            "o",
            "pass" if script_bound and reader_ok else "fail",
            f"grant is script-bound: {script_bound}; reader without any connection privilege reads it through the VS: "
            f"{reader_ok}; CREATE OR REPLACE drops the grant with identical text: {dropped_identical}, with changed "
            f"text: {dropped_changed}; EXA_ALL_SCRIPTS.SCRIPT_TEXT vs sent: {steps['9_script_text']['summary']}",
            steps,
        )

    # ------------------------------------------------------------ (p)

    def probe_p(self) -> Finding:
        rows = self.sys.rows("SELECT KEYWORD FROM EXA_SQL_KEYWORDS WHERE RESERVED")
        keywords = sorted({str(keyword).upper() for (keyword,) in rows})
        KEYWORDS_FILE.parent.mkdir(parents=True, exist_ok=True)
        KEYWORDS_FILE.write_text("\n".join(keywords) + "\n")
        samples = {
            word: word in keywords
            for word in (
                "DATE",
                "ORDER",
                "RESULT",
                "LIMIT",
                "YEAR",
                "WEEK",
                "SEASON",
                "TEAM",
                "NAME",
                "VALUE",
            )
        }
        return Finding(
            "p",
            "pass" if keywords else "fail",
            f"{len(keywords)} reserved keywords written to {KEYWORDS_FILE.relative_to(REPO_ROOT)}; samples {samples}",
            {"count": len(keywords), "samples": samples},
        )

    # ------------------------------------------------------------ (q)

    def probe_q(self) -> Finding:
        info = self.diag("dns")
        cfbd = str(info.get("getaddrinfo api.collegefootballdata.com"))
        tcp = str(info.get("tcp 1.1.1.1:443"))
        resolv = str(info.get("file /etc/resolv.conf"))
        hosts = str(info.get("file /etc/hosts"))
        confirmed = cfbd.startswith("ERR") and tcp.startswith("CONNECTED")
        return Finding(
            "q",
            "pass" if confirmed else "info",
            f"/etc/resolv.conf {resolv!r:.60}, /etc/hosts {hosts!r:.60}; getaddrinfo(api.collegefootballdata.com) -> "
            f"{cfbd[:90]}; localhost -> {str(info.get('getaddrinfo localhost'))[:60]}; TCP 1.1.1.1:443 -> {tcp}",
            info,
        )


# ---------------------------------------------------------------- host helpers


def load_previous_findings() -> dict[str, Any]:
    """Findings of earlier runs, so a partial run (--only) updates the results file in place."""
    try:
        return dict(json.loads(RESULTS_FILE.read_text()).get("findings", {}))
    except (OSError, ValueError):
        return {}


def read_sys_password() -> str:
    password = PASSWORD_FILE.read_text().strip()
    if not password:
        raise SystemExit(f"empty credentials file: {PASSWORD_FILE}")
    return password


def remove_tree(path: Path) -> None:
    for child in sorted(path.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if child.is_dir():
            child.rmdir()
        else:
            child.chmod(0o644)
            child.unlink()
    path.rmdir()


def probe_lines_in_bucketfs_conf(include_default: bool = True) -> list[str]:
    lines = []
    for line in BUCKETFS_CONF.read_text().splitlines():
        fields = line.split()
        default_line = include_default and fields[1:3] == [DEFAULT_SERVICE, DEFAULT_BUCKET]
        if len(fields) >= 6 and (fields[1] == PROBE_SERVICE or default_line):
            lines.append(" ".join([*fields[:4], "<password>", *fields[5:]]))
    return lines


def run_command(args: list[str], timeout: float = 120) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout, check=False
        )
        return {
            "returncode": completed.returncode,
            "stdout": completed.stdout.strip()[:2000],
            "stderr": completed.stderr.strip()[:2000],
        }
    except Exception as error:
        return {"returncode": -1, "stdout": "", "stderr": error_text(error)}


def podman(args: list[str]) -> str:
    outcome = run_command(["podman", *args])
    return outcome["stdout"] + (("\n" + outcome["stderr"]) if outcome["stderr"] else "")


def db_container() -> str | None:
    for line in podman(["ps", "--format", "{{.Names}}|{{.Image}}|{{.Ports}}"]).splitlines():
        parts = line.split("|")
        if len(parts) == 3 and "exasol/nano" in parts[1] and "8563" in parts[2]:
            return parts[0]
    return None


def slc_python_image() -> str:
    for line in podman(["images", "--format", "{{.Repository}}:{{.Tag}}"]).splitlines():
        if "script-language-container" in line and "python-3.12" in line:
            return line.strip()
    return "docker.io/library/python:3.12-slim"


def remove_sidecar() -> dict[str, Any]:
    return run_command(["podman", "rm", "-f", "--ignore", SIDECAR_NAME])


def primary_host_ip() -> str:
    outcome = run_command(["ip", "-4", "route", "get", "1.1.1.1"])
    match = re.search(r"\bsrc (\d+\.\d+\.\d+\.\d+)", outcome["stdout"])
    return match.group(1) if match else "0.0.0.0"


def slirp_flags() -> str:
    outcome = run_command(["pgrep", "-a", "slirp4netns"])
    return " ".join(sorted({flag for flag in outcome["stdout"].split() if flag.startswith("--")}))


def host_http_probe(port: int) -> str:
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/host", timeout=3) as response:
            return f"HTTP {response.status} (sidecar port reachable from the host)"
    except Exception as error:
        return f"not reachable from the host ({type(error).__name__})"


def max_overlap(intervals: list[tuple[float, float]]) -> int:
    events = sorted(
        [(start, 1) for start, _ in intervals] + [(end, -1) for _, end in intervals],
        key=lambda e: (e[0], e[1]),
    )
    current = best = 0
    for _, delta in events:
        current += delta
        best = max(best, current)
    return best


def normalized(rows: Any) -> list[tuple[str, ...]]:
    return [tuple(str(value) for value in row) for row in rows]


def script_text_comparison(sent: str, stored: str | None) -> dict[str, Any]:
    stored = stored or ""
    sent_body = sent.split(" AS\n", 1)[1].removesuffix("/")
    stored_body = stored.split(" AS\n", 1)[1] if " AS\n" in stored else stored
    identical = stored == sent
    return {
        "identical": identical,
        "body_identical": sent_body == stored_body,
        "sent_head": sent[:90],
        "stored_head": stored[:90],
        "sent_tail": sent[-20:],
        "stored_tail": stored[-20:],
        "summary": "identical"
        if identical
        else (
            "body identical, header differs"
            if sent_body == stored_body
            else "differs (see details)"
        ),
    }


# ---------------------------------------------------------------- main


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--only", default=ITEMS, help="items to run, e.g. 'a,c,f' (a always runs)")
    parser.add_argument(
        "--keep", action="store_true", help="keep the PROBE_* objects for inspection"
    )
    parser.add_argument(
        "--crash-trigger",
        action="store_true",
        help="also try the 2026.1 UNION ALL aggregate crash trigger",
    )
    args = parser.parse_args()
    selected = {item for item in args.only.replace(",", "") if item in ITEMS} | {"a"}
    probe = Probe(keep=args.keep, crash_trigger=args.crash_trigger)
    exit_code = 0
    try:
        probe.setup_database()
        try:
            probe.record(probe.probe_a())
        except Exception as error:
            probe.record(Finding("a", "error", error_text(error)))
        if probe.findings["a"].status != "pass":
            print("GATE (a) failed: stopping.", flush=True)
            return 2
        probe.setup_udfs()
        order = "bpqidcejfghokmln"
        for item in order:
            if item not in selected:
                continue
            try:
                probe.record(getattr(probe, f"probe_{item}")())
            except Exception as error:
                probe.record(Finding(item, "error", error_text(error)))
        if args.crash_trigger and "h" in selected:
            probe.meta["h_crash_trigger"] = probe.probe_h_crash_trigger()
            print(f"(h) crash trigger: {probe.meta['h_crash_trigger']}", flush=True)
    except Exception as error:
        probe.meta["fatal"] = error_text(error)
        print(f"FATAL: {probe.meta['fatal']}", flush=True)
        exit_code = 1
    finally:
        if not args.keep:
            probe.meta["cleanup"] = probe.cleanup()
            print(f"cleanup: {probe.meta['cleanup']}", flush=True)
        probe.meta["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        probe.save()
        probe.sys.close()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
