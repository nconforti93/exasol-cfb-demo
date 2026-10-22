"""One live smoke test of the mock sidecar against the local Exasol Personal DB.

Run from the repo root: `uv run python -m tests.mock_cfbd.smoke_live`. It starts the sidecar,
creates a throwaway `PROBE_MOCK` schema with tiny PYTHON3 UDFs that call the mock from inside the
DB, and always drops the schema and stops the sidecar. It makes zero CFBD calls. The sys password
comes from `vs.host.target` (the local deployment's secrets file) at run time and is never written
anywhere.
"""

import sys
from typing import Any

from tests.mock_cfbd import sidecar
from vs.host import target

SCHEMA = "PROBE_MOCK"

GET_UDF = f"""CREATE OR REPLACE PYTHON3 SCALAR SCRIPT {SCHEMA}.GET_URL(url VARCHAR(2000))
RETURNS VARCHAR(2000000) AS
import urllib.error
import urllib.request


def run(ctx):
    headers = {{"Authorization": "Bearer probe-marker-key"}}
    request = urllib.request.Request(ctx.url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return "%d %s" % (response.status, response.read(2000).decode())
    except urllib.error.HTTPError as error:
        return "%d retry-after=%s" % (error.code, error.headers.get("Retry-After"))
/"""

TLS_UDF = f"""CREATE OR REPLACE PYTHON3 SCALAR SCRIPT
{SCHEMA}.TLS_CHECK(host VARCHAR(100), port INT) RETURNS VARCHAR(2000) AS
import socket
import ssl


def run(ctx):
    try:
        with socket.create_connection((ctx.host, ctx.port), timeout=10) as raw:
            with ssl.create_default_context().wrap_socket(raw, server_hostname=ctx.host):
                return "verified"
    except ssl.SSLCertVerificationError as error:
        return "verify-failed: " + str(error.verify_message)
/"""


def scalar(connection: Any, sql: str) -> str:
    return str(connection.execute(sql).fetchval())


def main() -> int:
    connection = target.resolve("local").connect()
    handle = None
    try:
        handle = sidecar.start()
        print(f"sidecar up: {handle.address} doh={handle.doh_url}")
        connection.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        connection.execute(f"CREATE SCHEMA {SCHEMA}")
        connection.execute(GET_UDF)
        connection.execute(TLS_UDF)

        info = scalar(connection, f"SELECT {SCHEMA}.GET_URL('{handle.address}/info')")
        print("GET /info from the UDF:", info[:100])
        assert info.startswith("200 "), info

        handle.script("/games", status=429, retry_after=2, times=1)
        throttled = scalar(
            connection, f"SELECT {SCHEMA}.GET_URL('{handle.address}/games?year=2024')"
        )
        print("scripted 429:", throttled)
        assert throttled == "429 retry-after=2", throttled

        print("hits /info:", handle.hits("/info"), "hits /games:", handle.hits("/games"))
        assert handle.hits("/info") == 1 and handle.hits("/games") == 1
        keys = [entry["headers"].get("authorization") for entry in handle.log()]
        assert keys == ["Bearer probe-marker-key"] * 2, keys

        assert handle.doh_log() == []
        verdict = scalar(connection, f"SELECT {SCHEMA}.TLS_CHECK('127.0.0.1', {handle.doh_port})")
        print("DoH certificate check from the UDF:", verdict)
        assert verdict.startswith("verify-failed"), verdict
        assert next(e["kind"] for e in handle.doh_log()) == "connect"
        print("SMOKE OK")
        return 0
    finally:
        try:
            connection.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        finally:
            connection.close()
            sidecar.stop()
            if handle is not None:
                print("sidecar stopped, schema dropped")


if __name__ == "__main__":
    sys.exit(main())
