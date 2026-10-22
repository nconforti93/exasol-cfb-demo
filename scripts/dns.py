"""`make dns-check`: can the UDF sandbox reach api.collegefootballdata.com over verified TLS?

Creates a small UDF in CFB_TOOLS as sys, runs it once and prints what it found. The UDF resolves
the host name through the resolver seam, opens a TLS connection to port 443 and verifies the
certificate against the host name. It sends no HTTP request, so no CFBD quota is used.

`--target local|aws` (or `TARGET`) selects the Exasol Personal deployment, see `vs/host/target.py`.
"""

import argparse
import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from scripts import dns_udf
from vs.cfbd_vs.resolver import DEFAULT_DOH_URL
from vs.host import target

ROOT = Path(__file__).resolve().parents[1]
BUNDLED_RESOLVER_PATH = ROOT / "vs" / "cfbd_vs" / "resolver.py"

DEFAULT_HOST = "api.collegefootballdata.com"
DEFAULT_PORT = 443
SCHEMA = "CFB_TOOLS"
SCRIPT = "DNS_CHECK"

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_ERROR = 2

SYSTEM_ONLY_RESOLVER_SOURCE = """\
import socket


def install(doh_url):
    pass


def resolve(host, port):
    infos = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
    return sorted({info[4][0] for info in infos})
"""

UDF_TEMPLATE = """\
import json
import types

RESOLVER_SOURCE = {resolver_source!r}
resolver = types.ModuleType("resolver")
exec(compile(RESOLVER_SOURCE, "resolver.py", "exec"), resolver.__dict__)

{probe_source}

def run(ctx):
    return json.dumps(probe(ctx.HOST, int(ctx.PORT), ctx.DOH_URL, resolver))
"""

UDF_SIGNATURE = (
    f"CREATE OR REPLACE PYTHON3 SCALAR SCRIPT {SCHEMA}.{SCRIPT}"
    "(host VARCHAR(255), port DECIMAL(5,0), doh_url VARCHAR(1024)) "
    "RETURNS VARCHAR(2000000) UTF8 AS\n"
)


class DnsCheckError(RuntimeError):
    """The check could not run, as opposed to running and finding a failure."""


def default_doh_url(environ: Mapping[str, str]) -> str:
    return environ.get("DOH_URL", DEFAULT_DOH_URL)


def load_resolver_source(path: Path = BUNDLED_RESOLVER_PATH) -> str:
    """Use the bundled resolver module once it exists, the system resolver before that."""
    if path.exists():
        return path.read_text(encoding="utf-8")
    return SYSTEM_ONLY_RESOLVER_SOURCE


def probe_source() -> str:
    return Path(dns_udf.__file__).read_text(encoding="utf-8")


def build_udf_source(resolver_source: str) -> str:
    return UDF_TEMPLATE.format(resolver_source=resolver_source, probe_source=probe_source())


def udf_ddl(udf_source: str) -> str:
    return UDF_SIGNATURE + udf_source


def parse_report(text: str) -> dict[str, Any]:
    try:
        report = json.loads(text)
    except json.JSONDecodeError as error:
        raise DnsCheckError(f"the UDF returned something that is not JSON: {error}") from None
    if not isinstance(report, dict):
        raise DnsCheckError("the UDF returned JSON that is not an object")
    return report


def exit_code(report: Mapping[str, Any]) -> int:
    tls = report.get("tls")
    verified = isinstance(tls, Mapping) and tls.get("verified") is True
    return EXIT_OK if report.get("ok") is True and verified else EXIT_FAILED


def format_report(report: Mapping[str, Any]) -> str:
    verdict = "OK" if exit_code(report) == EXIT_OK else "FAIL"
    tls = report.get("tls")
    lines = [
        f"dns-check {verdict}: {report['host']}:{report['port']}",
        f"  resolution path: {report.get('path') or 'none'}",
        f"  addresses:       {', '.join(report.get('addresses') or []) or 'none'}",
        f"  system lookup:   {_system_lookup(report)}",
        f"  DOH_URL:         {report.get('doh_url')}",
        f"  TLS:             {_tls_summary(tls)}",
        f"  /etc/resolv.conf: {report.get('resolv_conf')}",
        f"  /etc/hosts:      {report.get('hosts')}",
    ]
    if report.get("error"):
        lines.append(f"  failed at stage '{report.get('stage')}': {report['error']}")
    lines.append("  No HTTP request was sent: 0 CFBD calls.")
    return "\n".join(lines)


def _system_lookup(report: Mapping[str, Any]) -> str:
    if report.get("system_ok"):
        return "ok"
    return f"failed ({report.get('system_error')})"


def _tls_summary(tls: Any) -> str:
    if not isinstance(tls, Mapping):
        return "not established"
    verified = "certificate verified" if tls.get("verified") else "certificate NOT verified"
    return (
        f"{tls.get('version')}, {verified}, CN={tls.get('subject_cn')}, "
        f"expires {tls.get('not_after')}"
    )


def _first_line(error: Exception) -> str:
    message = str(getattr(error, "message", error)).strip()
    return message.splitlines()[0] if message else type(error).__name__


def run_check(host: str, port: int, doh_url: str, deployment: target.Target) -> dict[str, Any]:
    """Deploy the UDF as sys, run it once and return its report."""
    import pyexasol

    try:
        connection = deployment.connect()
    except target.TargetError as error:
        raise DnsCheckError(str(error)) from None
    try:
        connection.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
        connection.execute(udf_ddl(build_udf_source(load_resolver_source())))
        row = connection.execute(
            f"SELECT {SCHEMA}.{SCRIPT}({{host}}, {{port}}, {{doh_url}})",
            {"host": host, "port": port, "doh_url": doh_url},
        ).fetchone()
    except pyexasol.ExaError as error:
        raise DnsCheckError(f"Exasol rejected the check: {_first_line(error)}") from None
    finally:
        connection.close()
    return parse_report(row[0])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--doh-url", default=default_doh_url(os.environ))
    parser.add_argument(
        "--target", default=None, help="local or aws (default: $TARGET, else local)"
    )
    parser.add_argument("--dsn", default=None, help="overrides host and port of the target")
    args = parser.parse_args(argv)
    try:
        deployment = target.resolve(args.target, dsn=args.dsn)
        report = run_check(args.host, args.port, args.doh_url, deployment)
    except (DnsCheckError, target.TargetError) as error:
        print(f"dns-check could not run: {error}", file=sys.stderr)
        return EXIT_ERROR
    print(format_report(report))
    return exit_code(report)


if __name__ == "__main__":
    sys.exit(main())
