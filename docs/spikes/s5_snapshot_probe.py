#!/usr/bin/env python3
"""S5 snapshot format probe (task 1.2, plan add-cfb-persistence-refresh).

Checks whether `exapump export --format parquet --compression zstd` followed by `exapump upload`
into a `(LIKE …)` copy round-trips every mirror and meta column type exactly, and how exapump
behaves around it: DSN from the child environment only, certificate pinning, exit codes, an
existing non-empty table, column matching by name or position, and an empty table. Findings and
the format decision are in `docs/spikes/s5-loader-probe.md` (section "Snapshot format").

Safety rules:
- Only the schema `PROBE_S5_SNAP` with synthetic rows (no CFBD data) and a private temporary
  directory (mode 0700) for the Parquet files; both are removed at the end (unless --keep).
- Zero CFBD calls: nothing here reads a virtual schema or a connection.
- The sys password comes from `vs/host/target.py` at run time. exapump gets its DSN only through
  `EXAPUMP_DSN` in the child process's environment; argv never holds a secret, and the probe
  checks that exapump's output does not echo the DSN.

Usage:
    uv run python docs/spikes/s5_snapshot_probe.py [--target local|aws] [--keep]

Writes `cache/probe/s5-snapshot-probe[-aws].json` (gitignored).
"""

from __future__ import annotations

import argparse
import contextlib
import gzip
import json
import os
import random
import shutil
import ssl
import stat
import subprocess
import sys
import tempfile
import time
import urllib.parse
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from vs.host import target  # noqa: E402
from vs.host.metaddl import render_meta_ddl  # noqa: E402

RESULTS_DIR = REPO_ROOT / "cache" / "probe"
SCHEMA = "PROBE_S5_SNAP"
EXAPUMP = shutil.which("exapump") or str(Path.home() / ".local" / "bin" / "exapump")
EXAPUMP_TIMEOUT_SECONDS = 600
VARCHAR_MAX = 2_000_000
JSON_TARGET_CHARS = 1_000_000

TYPES_DDL = f"""CREATE TABLE {SCHEMA}.TYPES (
  ID DECIMAL(9,0) NOT NULL,
  D3 DECIMAL(3,0),
  D9 DECIMAL(9,0),
  D18 DECIMAL(18,0),
  D93 DECIMAL(9,3),
  D366 DECIMAL(36,6),
  DBL DOUBLE,
  FLAG BOOLEAN,
  DT DATE,
  TS TIMESTAMP,
  V8 VARCHAR(8) UTF8,
  V2K VARCHAR(2000) UTF8,
  V2M VARCHAR(2000000) UTF8
)"""

EXTREMES_DDL = f"""CREATE TABLE {SCHEMA}.EXTREMES (
  ID DECIMAL(9,0) NOT NULL,
  DT DATE,
  TS TIMESTAMP,
  DBL DOUBLE
)"""

COLUMN_KINDS = {
    "DECIMAL": "sum",
    "DOUBLE": "sum of values rounded to 8 decimals, summed as DECIMAL(36,8) (|x| < 1E27)",
    "BOOLEAN": "count of TRUE",
    "TIMESTAMP": "sum of seconds since 1970",
    "DATE": "sum of days since 1970 (not in the spec's table)",
    "VARCHAR": "total length",
}


def mixed_text(length: int) -> str:
    unit = "äöü ÄÖÜß 😀 \"quoted\" 'single' , ; \\ tab\tline\n"
    text = (unit * (length // len(unit) + 1))[:length]
    assert len(text) == length
    return text


def json_document(chars: int) -> str:
    items = []
    total = 0
    index = 0
    while total < chars:
        item = {
            "id": index,
            "team": "Münster Bären 😀",
            "note": 'quote " and backslash \\ and slash /',
            "score": index * 0.5,
        }
        items.append(item)
        total += len(json.dumps(item, ensure_ascii=False)) + 2
        index += 1
    return json.dumps(items, ensure_ascii=False)


class Probe:
    def __init__(self, target_name: str, keep: bool) -> None:
        self.target_name = target_name
        self.keep = keep
        self.deployment = target.resolve(target_name)
        self.conn = self.deployment.connect()
        self.conn.set_autocommit(True)
        self.workdir = Path(tempfile.mkdtemp(prefix="probe-s5-snapshot-"))
        self.password = self.deployment.password()
        self.fingerprint = self._fingerprint()
        suffix = "" if target_name == "local" else f"-{target_name}"
        self.results_file = RESULTS_DIR / f"s5-snapshot-probe{suffix}.json"
        self.results: dict[str, Any] = {
            "meta": {
                "target": self.deployment.describe(),
                "db_version": self.value(
                    "SELECT PARAM_VALUE FROM EXA_METADATA WHERE PARAM_NAME = 'databaseProductVersion'"
                ),
                "exapump": subprocess.run(
                    [EXAPUMP, "--version"], capture_output=True, text=True, check=False
                ).stdout.strip(),
                "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "workdir mode": oct(stat.S_IMODE(self.workdir.stat().st_mode)),
            }
        }

    # ------------------------------------------------------------------ helpers

    def _fingerprint(self) -> str:
        """Lower-case SHA-256 of the server certificate: pinned for aws, computed for loopback."""
        if self.deployment.fingerprint:
            return self.deployment.fingerprint.lower()
        pem = ssl.get_server_certificate((self.deployment.host, self.deployment.port))
        return target.fingerprint_of(pem).lower()

    def dsn(self, password: str | None = None) -> str:
        user = urllib.parse.quote(self.deployment.user, safe="")
        secret = urllib.parse.quote(self.password if password is None else password, safe="")
        return f"exasol://{user}:{secret}@{self.deployment.host}:{self.deployment.port}"

    def exapump(
        self, *args: str, fingerprint: str | None = "", dsn: str | None = None
    ) -> dict[str, Any]:
        """Run exapump with the DSN only in the child's environment; output checked for secrets."""
        pin = self.fingerprint if fingerprint == "" else fingerprint
        argv = [EXAPUMP, *args]
        if pin:
            argv += ["--certificate-fingerprint", pin]
        child_dsn = dsn or self.dsn()
        assert child_dsn not in " ".join(argv) and self.password not in argv
        env = {**os.environ, "EXAPUMP_DSN": child_dsn}
        started = time.monotonic()
        completed = subprocess.run(
            argv,
            env=env,
            capture_output=True,
            text=True,
            timeout=EXAPUMP_TIMEOUT_SECONDS,
            check=False,
        )
        output = (completed.stdout + completed.stderr).strip()
        leaked = child_dsn in output or f":{urllib.parse.quote(self.password, safe='')}@" in output
        return {
            "argv": [part if part != pin else "<fingerprint>" for part in argv[1:]],
            "exit": completed.returncode,
            "seconds": round(time.monotonic() - started, 2),
            "output": "<withheld: contains the DSN>" if leaked else output[-1500:],
            "dsn leaked in output": leaked,
        }

    def run(self, sql: str, params: dict[str, Any] | None = None) -> Any:
        return self.conn.execute(sql, params)

    def rows(self, sql: str) -> list[tuple[Any, ...]]:
        return [tuple(row) for row in self.conn.execute(sql).fetchall()]

    def value(self, sql: str) -> Any:
        return self.conn.execute(sql).fetchval()

    def columns(self, table: str) -> list[tuple[str, str]]:
        return [
            (str(name), str(kind))
            for name, kind in self.rows(
                "SELECT COLUMN_NAME, COLUMN_TYPE FROM EXA_ALL_COLUMNS "
                f"WHERE COLUMN_SCHEMA = '{SCHEMA}' AND COLUMN_TABLE = '{table}' "
                "ORDER BY COLUMN_ORDINAL_POSITION"
            )
        ]

    def fingerprint_sql(self, table: str) -> str:
        parts = ["COUNT(*) AS ROWS_N"]
        for name, kind in self.columns(table):
            column = f'"{name}"'
            parts.append(f'COUNT({column}) AS "{name}_NN"')
            if kind.startswith("DECIMAL"):
                parts.append(f'SUM({column}) AS "{name}_AGG"')
            elif kind.startswith("DOUBLE"):
                parts.append(
                    f"SUM(CASE WHEN ABS({column}) < 1E27 THEN CAST(ROUND({column}, 8) AS DECIMAL(36,8)) END) "
                    f'AS "{name}_AGG"'
                )
                parts.append(
                    f'SUM(CASE WHEN ABS({column}) >= 1E27 THEN 1 ELSE 0 END) AS "{name}_HUGE"'
                )
            elif kind.startswith("BOOLEAN"):
                parts.append(f'SUM(CASE WHEN {column} THEN 1 ELSE 0 END) AS "{name}_AGG"')
            elif kind.startswith("TIMESTAMP"):
                parts.append(
                    f"SUM(SECONDS_BETWEEN({column}, TIMESTAMP '1970-01-01 00:00:00')) "
                    f'AS "{name}_AGG"'
                )
            elif kind.startswith("DATE"):
                parts.append(f"SUM(DAYS_BETWEEN({column}, DATE '1970-01-01')) AS \"{name}_AGG\"")
            elif kind.startswith("VARCHAR"):
                parts.append(f'SUM(LENGTH({column})) AS "{name}_AGG"')
        return f'SELECT {", ".join(parts)} FROM "{SCHEMA}"."{table}"'

    def fingerprint_of(self, table: str) -> dict[str, Any]:
        try:
            statement = self.conn.execute(self.fingerprint_sql(table))
        except Exception as error:
            message = str(getattr(error, "message", error)).split(" (Session")[0]
            return {"fingerprint error": message[:300]}
        names = list(statement.columns())
        values = statement.fetchone()
        return {name: str(value) for name, value in zip(names, values, strict=True)}

    def compare(self, source: str, copy: str) -> dict[str, Any]:
        names = ", ".join(f'"{name}"' for name, _ in self.columns(source))
        minus_one = self.value(
            f'SELECT COUNT(*) FROM (SELECT {names} FROM "{SCHEMA}"."{source}" '
            f'MINUS SELECT {names} FROM "{SCHEMA}"."{copy}")'
        )
        minus_two = self.value(
            f'SELECT COUNT(*) FROM (SELECT {names} FROM "{SCHEMA}"."{copy}" '
            f'MINUS SELECT {names} FROM "{SCHEMA}"."{source}")'
        )
        left = self.rows(f'SELECT {names} FROM "{SCHEMA}"."{source}" ORDER BY 1')
        right = self.rows(f'SELECT {names} FROM "{SCHEMA}"."{copy}" ORDER BY 1')
        differences = []
        double_drift = 0.0
        other_differences = 0
        for row_left, row_right in zip(left, right, strict=False):
            for (name, kind), a, b in zip(self.columns(source), row_left, row_right, strict=True):
                if a != b or repr(a) != repr(b):
                    if kind == "DOUBLE" and isinstance(a, float) and isinstance(b, float):
                        double_drift = max(double_drift, abs(a - b) / max(abs(a), 1e-300))
                    else:
                        other_differences += 1
                    differences.append(
                        {
                            "id": row_left[0],
                            "column": name,
                            "source": repr(a)[:120],
                            "copy": repr(b)[:120],
                        }
                    )
        fingerprint_source = self.fingerprint_of(source)
        fingerprint_copy = self.fingerprint_of(copy)
        return {
            "rows": (len(left), len(right)),
            "minus source-copy": minus_one,
            "minus copy-source": minus_two,
            "value differences (python, repr-exact)": differences,
            "differences outside DOUBLE columns": other_differences,
            "max relative DOUBLE difference": double_drift,
            "fingerprints equal": fingerprint_source == fingerprint_copy,
            "fingerprint source": fingerprint_source,
            "fingerprint copy": fingerprint_copy
            if fingerprint_source != fingerprint_copy
            else "same",
            "exact": minus_one == 0
            and minus_two == 0
            and not differences
            and len(left) == len(right)
            and fingerprint_source == fingerprint_copy,
        }

    def round_trip(self, table: str, copy: str, *, like: bool = True) -> dict[str, Any]:
        file = self.workdir / f"{SCHEMA}.{table}.parquet"
        result: dict[str, Any] = {}
        result["export"] = self.exapump(
            "export", "--table", f"{SCHEMA}.{table}", "--output", str(file),
            "--format", "parquet", "--compression", "zstd",
        )  # fmt: skip
        result["file"] = self.file_info(file)
        if like:
            self.run(f'CREATE TABLE "{SCHEMA}"."{copy}" (LIKE "{SCHEMA}"."{table}")')
        result["upload"] = self.exapump("upload", str(file), "--table", f"{SCHEMA}.{copy}")
        if result["export"]["exit"] == 0 and result["upload"]["exit"] == 0:
            result["comparison"] = self.compare(table, copy)
        return result

    def fallback_round_trip(self, table: str, copy: str) -> dict[str, Any]:
        """gzip CSV through pyexasol's HTTP transport, header line written and skipped."""
        file = self.workdir / f"{SCHEMA}.{table}.csv.gz"
        result: dict[str, Any] = {}
        started = time.monotonic()
        try:
            self.conn.export_to_file(
                str(file),
                (SCHEMA, table),
                export_params={"format": "gz", "with_column_names": True},
            )
            result["export"] = {"ok": True, "seconds": round(time.monotonic() - started, 2)}
        except Exception as error:
            result["export"] = {"ok": False, "error": str(getattr(error, "message", error))[:400]}
            return result
        result["file"] = self.file_info(file)
        with gzip.open(file, "rt", encoding="utf-8", newline="") as handle:
            result["header line"] = handle.readline().rstrip("\r\n")
        self.run(f'CREATE TABLE "{SCHEMA}"."{copy}" (LIKE "{SCHEMA}"."{table}")')
        started = time.monotonic()
        try:
            self.conn.import_from_file(
                str(file), (SCHEMA, copy), import_params={"format": "gz", "skip": 1}
            )
            result["import"] = {"ok": True, "seconds": round(time.monotonic() - started, 2)}
        except Exception as error:
            result["import"] = {"ok": False, "error": str(getattr(error, "message", error))[:400]}
            return result
        result["comparison"] = self.compare(table, copy)
        return result

    def check_special_characters(self) -> dict[str, Any]:
        """One small table per character class: which VARCHAR values survive the exapump trip."""
        cases = {
            "newline": "a\nb",
            "carriage return": "a\rb",
            "CRLF": "a\r\nb",
            "tab": "a\tb",
            "double quote": 'a"b',
            "single quote": "a'b",
            "comma": "a,b",
            "semicolon": "a;b",
            "backslash": "a\\b",
            "umlauts": "äöü",
            "emoji": "😀",
            "leading space": " a",
            "trailing space": "a ",
            "JSON array": '[{"a":1},{"b":"x, y"}]',
        }
        outcome: dict[str, Any] = {}
        for index, (label, value) in enumerate(cases.items()):
            table = f"CHARS_{index}"
            self.run(f'CREATE TABLE "{SCHEMA}"."{table}" (ID DECIMAL(9,0), V VARCHAR(100) UTF8)')
            self.run(f'INSERT INTO "{SCHEMA}"."{table}" VALUES (1, {{v}})', {"v": value})
            file = self.workdir / f"{table}.parquet"
            export = self.exapump(
                "export", "--table", f"{SCHEMA}.{table}", "--output", str(file),
                "--format", "parquet", "--compression", "zstd",
            )  # fmt: skip
            entry: dict[str, Any] = {"export exit": export["exit"]}
            if export["exit"] != 0:
                entry["error"] = export["output"][-160:]
                entry["file"] = self.file_info(file)
            else:
                self.run(f'CREATE TABLE "{SCHEMA}"."{table}_B" (LIKE "{SCHEMA}"."{table}")')
                upload = self.exapump("upload", str(file), "--table", f"{SCHEMA}.{table}_B")
                back = self.value(f'SELECT V FROM "{SCHEMA}"."{table}_B"')
                entry.update(
                    {"upload exit": upload["exit"], "back": repr(back), "exact": back == value}
                )
            outcome[label] = entry
        return outcome

    @staticmethod
    def file_info(file: Path) -> dict[str, Any]:
        if not file.exists():
            return {"exists": False}
        info = file.stat()
        return {"exists": True, "bytes": info.st_size, "mode": oct(stat.S_IMODE(info.st_mode))}

    # ------------------------------------------------------------------ data

    def setup(self) -> None:
        self.cleanup_db()
        self.run(f"CREATE SCHEMA {SCHEMA}")
        self.run(TYPES_DDL)
        self.run(EXTREMES_DDL)
        self.run(render_meta_ddl("load_log", SCHEMA))
        self.run(render_meta_ddl("fetch_log", SCHEMA))
        insert = (
            f"INSERT INTO {SCHEMA}.TYPES VALUES ({{id!d}}, {{d3!r}}, {{d9!r}}, {{d18!r}}, {{d93!r}}, "
            "{d366!r}, CAST({dbl!r} AS DOUBLE), {flag!r}, {dt!r}, {ts!r}, {v8}, {v2k}, {v2m})"
        )
        document = json_document(JSON_TARGET_CHARS)
        self.results["meta"]["json document chars"] = len(document)
        self.run(
            insert,
            {
                "id": 1, "d3": "123", "d9": "123456789", "d18": "999999999999999999",
                "d93": "123456.789", "d366": "123456789012345678901234567890.123456",
                "dbl": "0.1", "flag": "TRUE", "dt": "DATE '1869-11-06'",
                "ts": "TIMESTAMP '2026-10-22 12:34:56.789'", "v8": "äöüßÄÖÜ😀",
                "v2k": mixed_text(2000), "v2m": document,
            },
        )  # fmt: skip
        self.run(
            insert,
            {
                "id": 2, "d3": "-999", "d9": "-999999999", "d18": "-999999999999999999",
                "d93": "-999999.999", "d366": "-987654321098765432109876543210.654321",
                "dbl": "-1.7976E308", "flag": "FALSE", "dt": "DATE '2024-02-29'",
                "ts": "TIMESTAMP '1869-11-06 15:00:00.001'", "v8": "12345678",
                "v2k": "😀" * 2000, "v2m": "NULL",
            },
        )  # fmt: skip
        self.run(
            f"UPDATE {SCHEMA}.TYPES SET V2M = RPAD('x', {VARCHAR_MAX}, 'abc äöü 😀 ') WHERE ID = 2"
        )
        self.run(
            insert,
            {
                "id": 3, "d3": "0", "d9": "0", "d18": "0", "d93": "0.001", "d366": "0.000001",
                "dbl": "3.141592653589793", "flag": "TRUE", "dt": "DATE '1970-01-01'",
                "ts": "TIMESTAMP '1970-01-01 00:00:00.000'", "v8": " lead", "v2k": "trailing  ",
                "v2m": "{}",
            },
        )  # fmt: skip
        self.run(
            insert,
            {
                "id": 4, "d3": "1", "d9": "1", "d18": "1", "d93": "1", "d366": "1",
                "dbl": "2.2250738585072014E-308", "flag": "FALSE", "dt": "DATE '2026-12-31'",
                "ts": "TIMESTAMP '2262-04-11 23:47:16.854'", "v8": "x", "v2k": "x", "v2m": "x",
            },
        )  # fmt: skip
        self.run(
            f"INSERT INTO {SCHEMA}.TYPES (ID) VALUES 5"  # every other column NULL
        )
        self.run(
            f"INSERT INTO {SCHEMA}.TYPES VALUES (6, NULL, NULL, NULL, NULL, NULL, CAST(-0.0 AS DOUBLE), "
            "NULL, NULL, NULL, NULL, NULL, NULL)"
        )
        self.run(
            f"INSERT INTO {SCHEMA}.EXTREMES VALUES "
            "(1, DATE '0001-01-01', TIMESTAMP '0001-01-01 00:00:00.000', CAST(1E-300 AS DOUBLE)), "
            "(2, DATE '9999-12-31', TIMESTAMP '9999-12-31 23:59:59.999', CAST(1.7976E308 AS DOUBLE)), "
            "(3, DATE '1677-09-21', TIMESTAMP '1677-09-21 00:12:43.146', NULL)"
        )
        self.run(
            f"INSERT INTO {SCHEMA}.LOAD_LOG VALUES "
            "('GAMES', '/games?seasonType=both&year=2019', TRUE, 812, TIMESTAMP '2026-10-22 13:00:00.123', 'BACKFILL#42'), "
            "('GAMES', '/games?seasonType=both&year=1871', TRUE, 0, TIMESTAMP '2026-10-22 13:00:00.000', 'LOAD_PLAN_GAP'), "
            "('RANKINGS_POLLS_RANKS', '/rankings?seasonType=both&year=2026', FALSE, 1250, TIMESTAMP '2026-10-22 13:05:01.999', 'REFRESH_WEEK#43')"
        )
        self.run(
            f"INSERT INTO {SCHEMA}.FETCH_LOG VALUES "
            "(TIMESTAMP '2026-10-22 13:00:01.000', 'BACKFILL#42', 'GAMES', '/games?seasonType=both&year=2019', "
            "'LIVE', 200, 1, 431, 812, TIMESTAMP '2026-10-22 12:59:59.500'), "
            "(TIMESTAMP '2026-10-22 13:00:01.000', 'BACKFILL#42', 'GAMES', '/games?seasonType=both&year=2018', "
            "'LIVE', NULL, NULL, NULL, 0, NULL), "
            "(TIMESTAMP '2026-10-22 13:00:02.000', NULL, 'TEAMS', '/teams', 'REPLAY', 200, 2, 12, 900, NULL)"
        )
        self.results["meta"]["source rows"] = {
            "TYPES": self.value(f"SELECT COUNT(*) FROM {SCHEMA}.TYPES"),
            "V2M lengths": self.rows(f"SELECT ID, LENGTH(V2M) FROM {SCHEMA}.TYPES ORDER BY ID"),
        }

    def cleanup_db(self) -> None:
        with contextlib.suppress(Exception):
            self.run(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")

    def cleanup(self) -> dict[str, Any]:
        self.cleanup_db()
        shutil.rmtree(self.workdir, ignore_errors=True)
        left = self.rows("SELECT SCHEMA_NAME FROM EXA_SCHEMAS WHERE SCHEMA_NAME LIKE 'PROBE%'")
        return {
            "schemas left": [row[0] for row in left],
            "workdir removed": not self.workdir.exists(),
        }

    # ------------------------------------------------------------------ checks

    def check_dsn_and_pinning(self) -> dict[str, Any]:
        checks: dict[str, Any] = {}
        checks["pinned, lower-case fingerprint"] = self.exapump("sql", "SELECT 1")
        checks["pinned, upper-case fingerprint"] = self.exapump(
            "sql", "SELECT 1", fingerprint=self.fingerprint.upper()
        )
        checks["no fingerprint"] = self.exapump("sql", "SELECT 1", fingerprint=None)
        checks["wrong fingerprint"] = self.exapump("sql", "SELECT 1", fingerprint="00" * 32)
        checks["wrong password"] = self.exapump(
            "sql", "SELECT 1", dsn=self.dsn(password="not-the-password-123")
        )
        checks["websocket transport"] = self.exapump("sql", "--transport", "websocket", "SELECT 1")
        checks["client identification"] = self.exapump(
            "sql",
            "SELECT CLIENT, DRIVER FROM EXA_ALL_SESSIONS WHERE SESSION_ID = CURRENT_SESSION",
        )
        checks["EXAPUMP_DSN in the parent environment"] = "EXAPUMP_DSN" in os.environ
        return checks

    def check_round_trips(self) -> dict[str, Any]:
        trips: dict[str, Any] = {}
        for table in ("TYPES", "LOAD_LOG", "FETCH_LOG", "EXTREMES"):
            trips[table] = self.round_trip(table, f"{table}_BACK")
        return trips

    def check_fallback_round_trips(self) -> dict[str, Any]:
        trips: dict[str, Any] = {}
        for table in ("TYPES", "LOAD_LOG", "FETCH_LOG", "EXTREMES"):
            trips[table] = self.fallback_round_trip(table, f"{table}_CSV")
        self.run(
            f'CREATE TABLE "{SCHEMA}"."LOAD_LOG_REVERSED" ({self.reversed_columns("LOAD_LOG")})'
        )
        file = self.workdir / f"{SCHEMA}.LOAD_LOG.csv.gz"
        try:
            self.conn.import_from_file(
                str(file), (SCHEMA, "LOAD_LOG_REVERSED"), import_params={"format": "gz", "skip": 1}
            )
            trips["import into reversed column order"] = {
                "ok": True,
                "rows": self.value(f'SELECT COUNT(*) FROM "{SCHEMA}"."LOAD_LOG_REVERSED"'),
            }
        except Exception as error:
            trips["import into reversed column order"] = {
                "ok": False,
                "error": str(getattr(error, "message", error)).split(" (Session")[0][:300],
            }
        self.run(f'CREATE TABLE "{SCHEMA}"."EMPTY_CSV" (LIKE "{SCHEMA}"."TYPES")')
        empty = self.workdir / "empty.csv.gz"
        self.conn.export_to_file(
            str(empty),
            (SCHEMA, "EMPTY_CSV"),
            export_params={"format": "gz", "with_column_names": True},
        )
        self.run(f'CREATE TABLE "{SCHEMA}"."EMPTY_CSV_BACK" (LIKE "{SCHEMA}"."TYPES")')
        self.conn.import_from_file(
            str(empty), (SCHEMA, "EMPTY_CSV_BACK"), import_params={"format": "gz", "skip": 1}
        )
        trips["empty table"] = {
            "file": self.file_info(empty),
            "rows back": self.value(f'SELECT COUNT(*) FROM "{SCHEMA}"."EMPTY_CSV_BACK"'),
        }
        return trips

    def check_double_study(self) -> dict[str, Any]:
        """DOUBLE through the CSV fallback: 15-digit default vs a 17-digit format, two data shapes."""
        rng = random.Random(7)
        shapes = {
            "short decimals (JSON-like)": [
                round(rng.uniform(-50, 50), rng.choice((1, 2, 3, 6))) for _ in range(4000)
            ],
            "full-precision doubles": [
                rng.uniform(-1, 1) * 10 ** rng.randint(-6, 6) for _ in range(4000)
            ],
        }
        formats = {
            "default (15 significant digits)": {},
            "csv_cols FORMAT='9.9999999999999999EEEE' (17 digits)": {
                "csv_cols": ["1", "2 FORMAT='9.9999999999999999EEEE'"]
            },
        }
        study: dict[str, Any] = {}
        for shape_index, (shape, values) in enumerate(shapes.items()):
            source = f"DBL_SRC_{shape_index}"
            self.run(f'CREATE TABLE "{SCHEMA}"."{source}" (ID DECIMAL(9,0), X DOUBLE)')
            self.conn.import_from_iterable(list(enumerate(values)), (SCHEMA, source))
            for format_index, (label, params) in enumerate(formats.items()):
                copy = f"DBL_{shape_index}_{format_index}"
                file = self.workdir / f"{copy}.csv.gz"
                self.conn.export_to_file(
                    str(file), (SCHEMA, source),
                    export_params={"format": "gz", "with_column_names": True, **params},
                )  # fmt: skip
                self.run(f'CREATE TABLE "{SCHEMA}"."{copy}" (LIKE "{SCHEMA}"."{source}")')
                self.conn.import_from_file(
                    str(file), (SCHEMA, copy), import_params={"format": "gz", "skip": 1}
                )
                differing, drift = self.conn.execute(
                    "SELECT SUM(CASE WHEN S.X <> T.X THEN 1 ELSE 0 END), "
                    "MAX(ABS(S.X - T.X) / NULLIF(ABS(S.X), 0)) "
                    f'FROM "{SCHEMA}"."{source}" S JOIN "{SCHEMA}"."{copy}" T ON S.ID = T.ID'
                ).fetchone()
                study[f"{shape}, {label}"] = {
                    "rows": len(values),
                    "differing doubles": differing,
                    "max relative difference": drift,
                }
        det = random.Random(7)
        determinism_values = [
            round(det.uniform(0, 1), 3)
            if index % 4 == 0
            else round(det.uniform(-50, 50), 6)
            if index % 4 == 1
            else round(1500 + det.gauss(0, 200), 2)
            if index % 4 == 2
            else float(repr(det.random())[:12])
            for index in range(20000)
        ]
        self.run(f'CREATE TABLE "{SCHEMA}"."DBL_DET" (ID DECIMAL(9,0), X DOUBLE)')
        self.conn.import_from_iterable(list(enumerate(determinism_values)), (SCHEMA, "DBL_DET"))
        table = f'"{SCHEMA}"."DBL_DET"'
        float_deltas = [
            self.value(
                f"SELECT (SELECT SUM(ROUND(X, 8)) FROM {table}) - (SELECT SUM(ROUND(X, 8)) FROM {table}) "
                "FROM DUAL"
            )
            for _ in range(8)
        ]
        decimal_deltas = [
            self.value(
                f"SELECT (SELECT SUM(CAST(ROUND(X, 8) AS DECIMAL(36,8))) FROM {table}) - "
                f"(SELECT SUM(CAST(ROUND(X, 8) AS DECIMAL(36,8))) FROM {table}) FROM DUAL"
            )
            for _ in range(8)
        ]
        det_file = self.workdir / "DBL_DET.csv.gz"
        self.conn.export_to_file(
            str(det_file),
            (SCHEMA, "DBL_DET"),
            export_params={"format": "gz", "with_column_names": True},
        )
        self.run(f'CREATE TABLE "{SCHEMA}"."DBL_DET_COPY" (LIKE {table})')
        self.conn.import_from_file(
            str(det_file), (SCHEMA, "DBL_DET_COPY"), import_params={"format": "gz", "skip": 1}
        )
        copy = f'"{SCHEMA}"."DBL_DET_COPY"'
        study["fingerprint of an identical copy (CSV default, short decimals)"] = {
            "differing doubles": self.value(
                f"SELECT SUM(CASE WHEN S.X <> T.X THEN 1 ELSE 0 END) FROM {table} S "
                f"JOIN {copy} T ON S.ID = T.ID"
            ),
            "SUM(ROUND(X, 8)) source - copy": [
                repr(
                    self.value(
                        f"SELECT (SELECT SUM(ROUND(X, 8)) FROM {table}) - "
                        f"(SELECT SUM(ROUND(X, 8)) FROM {copy}) FROM DUAL"
                    )
                )
                for _ in range(4)
            ],
            "SUM(CAST(ROUND(X, 8) AS DECIMAL(36,8))) source - copy": [
                str(
                    self.value(
                        f"SELECT (SELECT SUM(CAST(ROUND(X, 8) AS DECIMAL(36,8))) FROM {table}) - "
                        f"(SELECT SUM(CAST(ROUND(X, 8) AS DECIMAL(36,8))) FROM {copy}) FROM DUAL"
                    )
                )
                for _ in range(4)
            ],
        }
        study[
            "fingerprint determinism: in-database delta of two sums over the same table, 8 runs"
        ] = {
            "SUM(ROUND(X, 8))": [repr(value) for value in float_deltas],
            "SUM(CAST(ROUND(X, 8) AS DECIMAL(36,8)))": [str(value) for value in decimal_deltas],
        }
        return study

    def check_time_zone_independence(self) -> dict[str, Any]:
        """Fingerprint expressions must not depend on the session time zone (AWS → local)."""
        expressions = (
            "SECONDS_BETWEEN(TIMESTAMP '2026-10-22 12:34:56.789', TIMESTAMP '1970-01-01 00:00:00'), "
            "POSIX_TIME(TIMESTAMP '2026-10-22 12:34:56.789')"
        )
        outcome: dict[str, Any] = {}
        session = self.deployment.connect()
        try:
            for zone in ("UTC", "Europe/Berlin", "America/New_York"):
                session.execute(f"ALTER SESSION SET TIME_ZONE = '{zone}'")
                seconds, posix = session.execute(f"SELECT {expressions} FROM DUAL").fetchone()
                outcome[zone] = {"SECONDS_BETWEEN": str(seconds), "POSIX_TIME": str(posix)}
        finally:
            session.close()
        return outcome

    def reversed_columns(self, table: str) -> str:
        return ", ".join(f'"{name}" {kind}' for name, kind in reversed(self.columns(table)))

    def check_behaviour(self) -> dict[str, Any]:
        behaviour: dict[str, Any] = {}
        file = self.workdir / f"{SCHEMA}.LOAD_LOG.parquet"
        behaviour["dry run (inferred Parquet schema)"] = self.exapump(
            "upload", str(file), "--table", f"{SCHEMA}.NOT_THERE", "--dry-run"
        )
        before = self.value(f"SELECT COUNT(*) FROM {SCHEMA}.LOAD_LOG_BACK")
        again = self.exapump("upload", str(file), "--table", f"{SCHEMA}.LOAD_LOG_BACK")
        behaviour["upload into the non-empty copy"] = {
            "exit": again["exit"],
            "rows before": before,
            "rows after": self.value(f"SELECT COUNT(*) FROM {SCHEMA}.LOAD_LOG_BACK"),
            "output": again["output"][-300:],
        }
        self.run(f'CREATE TABLE "{SCHEMA}"."LOAD_LOG_REV_PQ" ({self.reversed_columns("LOAD_LOG")})')
        upload = self.exapump("upload", str(file), "--table", f"{SCHEMA}.LOAD_LOG_REV_PQ")
        behaviour["upload into a copy with reversed column order"] = {
            "upload": upload,
            "comparison": self.compare("LOAD_LOG", "LOAD_LOG_REV_PQ")
            if upload["exit"] == 0
            else None,
        }
        self.run(
            f'CREATE TABLE "{SCHEMA}"."NARROW" (TABLE_NAME VARCHAR(128) UTF8, SLICE_KEY VARCHAR(2000) UTF8)'
        )
        behaviour["upload into a table with fewer columns"] = self.exapump(
            "upload", str(file), "--table", f"{SCHEMA}.NARROW"
        )
        behaviour["upload into a missing table"] = self.exapump(
            "upload", str(file), "--table", f"{SCHEMA}.MISSING_TABLE"
        )
        missing_created = self.rows(
            f"SELECT COLUMN_NAME, COLUMN_TYPE FROM EXA_ALL_COLUMNS WHERE COLUMN_SCHEMA = '{SCHEMA}' "
            "AND COLUMN_TABLE = 'MISSING_TABLE' ORDER BY COLUMN_ORDINAL_POSITION"
        )
        behaviour["upload into a missing table"]["created columns"] = missing_created
        self.run(f'CREATE TABLE "{SCHEMA}"."EMPTY_T" (LIKE "{SCHEMA}"."TYPES")')
        empty_file = self.workdir / f"{SCHEMA}.EMPTY_T.parquet"
        empty_export = self.exapump(
            "export", "--table", f"{SCHEMA}.EMPTY_T", "--output", str(empty_file),
            "--format", "parquet", "--compression", "zstd",
        )  # fmt: skip
        behaviour["export of an empty table"] = {
            "export": empty_export,
            "file": self.file_info(empty_file),
        }
        if empty_file.exists():
            self.run(f'CREATE TABLE "{SCHEMA}"."EMPTY_BACK" (LIKE "{SCHEMA}"."TYPES")')
            empty_upload = self.exapump(
                "upload", str(empty_file), "--table", f"{SCHEMA}.EMPTY_BACK"
            )
            behaviour["upload of the empty file"] = {
                "upload": empty_upload,
                "rows": self.value(f"SELECT COUNT(*) FROM {SCHEMA}.EMPTY_BACK"),
            }
        behaviour["export to an existing file"] = self.exapump(
            "export", "--table", f"{SCHEMA}.LOAD_LOG", "--output", str(file),
            "--format", "parquet", "--compression", "zstd",
        )  # fmt: skip
        behaviour["export to an existing file"]["file after"] = self.file_info(file)
        behaviour["export of a missing table"] = self.exapump(
            "export", "--table", f"{SCHEMA}.NO_SUCH_TABLE", "--output",
            str(self.workdir / "missing.parquet"), "--format", "parquet",
        )  # fmt: skip
        return behaviour

    def decide(self) -> dict[str, Any]:
        trips = self.results["round trips"]
        pinning = self.results["dsn and pinning"]
        mirror_types_exact = all(
            trips[table].get("comparison", {}).get("exact")
            for table in ("TYPES", "LOAD_LOG", "FETCH_LOG")
        )
        env_only = (
            pinning["pinned, lower-case fingerprint"]["exit"] == 0
            and not pinning["EXAPUMP_DSN in the parent environment"]
            and not any(
                check.get("dsn leaked in output")
                for check in pinning.values()
                if isinstance(check, dict)
            )
        )
        pin_enforced = (
            pinning["wrong fingerprint"]["exit"] != 0 and pinning["no fingerprint"]["exit"] != 0
        )
        characters = self.results["special characters"]
        broken = sorted(label for label, entry in characters.items() if not entry.get("exact"))
        fallback = self.results["fallback round trips"]
        fallback_exact = all(
            fallback[table].get("comparison", {}).get("exact")
            for table in ("TYPES", "LOAD_LOG", "FETCH_LOG", "EXTREMES")
        )
        fallback_exact_but_double = all(
            fallback[table].get("comparison", {}).get("differences outside DOUBLE columns") == 0
            and fallback[table].get("comparison", {}).get("minus source-copy") is not None
            for table in ("TYPES", "LOAD_LOG", "FETCH_LOG", "EXTREMES")
        )
        double_drift = max(
            fallback[table].get("comparison", {}).get("max relative DOUBLE difference", 0.0)
            for table in ("TYPES", "LOAD_LOG", "FETCH_LOG", "EXTREMES")
        )
        parquet = mirror_types_exact and not broken and env_only and pin_enforced
        if parquet:
            choice = "parquet-zstd via exapump"
        elif fallback_exact or fallback_exact_but_double:
            choice = "gzip CSV via pyexasol export_to_file / import_from_file (fallback, [15])"
        else:
            choice = "neither is exact: escalate"
        return {
            "exapump: mirror and meta types exact": mirror_types_exact,
            "exapump: VARCHAR values that do not survive": broken,
            "exapump: DSN from the child environment only, pinned": env_only,
            "exapump: pinning enforced": pin_enforced,
            "exapump: extreme dates exact": trips["EXTREMES"].get("comparison", {}).get("exact"),
            "fallback: every table exact": fallback_exact,
            "fallback: exact except DOUBLE": fallback_exact_but_double,
            "fallback: max relative DOUBLE difference": double_drift,
            "format": choice,
        }

    def run_all(self) -> int:
        cleanup = None
        try:
            self.setup()
            self.results["dsn and pinning"] = self.check_dsn_and_pinning()
            self.results["round trips"] = self.check_round_trips()
            self.results["special characters"] = self.check_special_characters()
            self.results["behaviour"] = self.check_behaviour()
            self.results["fallback round trips"] = self.check_fallback_round_trips()
            self.results["double study"] = self.check_double_study()
            self.results["time zone independence"] = self.check_time_zone_independence()
            self.results["decision"] = self.decide()
        finally:
            if not self.keep:
                cleanup = self.cleanup()
            self.results["cleanup"] = cleanup
            RESULTS_DIR.mkdir(parents=True, exist_ok=True)
            self.results_file.write_text(
                json.dumps(self.results, indent=2, default=str, ensure_ascii=False),
                encoding="utf-8",
            )
            with contextlib.suppress(Exception):
                self.conn.close()
        print(json.dumps(self.results.get("decision"), indent=2))
        print(f"results: {self.results_file}")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--target", choices=("local", "aws"), default="local")
    parser.add_argument("--keep", action="store_true", help="leave PROBE_S5_SNAP and the files")
    args = parser.parse_args()
    return Probe(args.target, args.keep).run_all()


if __name__ == "__main__":
    sys.exit(main())
