"""The UDF of the DoH end-to-end test (`make test-network`).

It is the dns-check probe of `scripts/dns.py`, wrapped to count the resolver's DoH requests, with
one difference: the system resolver is replaced by one that fails for every host name. A sandbox
that resolves names itself (a fixed Personal nano) therefore still takes the DoH path, so the test
asserts that path instead of skipping. IP literals still resolve, because the resolver uses them
to connect to the address the DoH answer gave.
"""

from __future__ import annotations

from scripts import dns

PROBE_TEMPLATE = """\
import ipaddress
import json
import socket
import types

RESOLVER_SOURCE = __RESOLVER_SOURCE__
resolver = types.ModuleType("resolver")
exec(compile(RESOLVER_SOURCE, "resolver.py", "exec"), resolver.__dict__)

__PROBE_SOURCE__

real_getaddrinfo = socket.getaddrinfo


def failing_system_resolver(host, *arguments, **keywords):
    try:
        ipaddress.ip_address(host)
    except ValueError:
        raise socket.gaierror(socket.EAI_NONAME, "injected: the system resolver fails") from None
    return real_getaddrinfo(host, *arguments, **keywords)


def run(ctx):
    host, port = ctx.HOST, int(ctx.PORT)
    socket.getaddrinfo = failing_system_resolver
    try:
        return json.dumps(_report(ctx, host, port))
    finally:
        resolver.uninstall()
        socket.getaddrinfo = real_getaddrinfo


def _report(ctx, host, port):
    resolver.install(ctx.DOH_URL)
    calls = []

    def count(active):
        inner = active.transport

        def counting(url, headers, timeout):
            calls.append(url)
            return inner(url, headers, timeout)

        active.transport = counting

    count(resolver._active)
    try:
        first = resolver.resolve(host, port)
    except Exception as error:
        failure = {"ok": False, "stage": "resolve", "system_ok": False, "doh_requests": len(calls)}
        failure["error"] = type(error).__name__ + ": " + str(error)
        return failure
    second = resolver.resolve(host, port)
    report = probe(host, port, ctx.DOH_URL, resolver, getaddrinfo=failing_system_resolver)
    count(resolver._active)
    third = resolver.resolve(host, port)
    report.update(doh_requests=len(calls), doh_urls=calls)
    report.update(first=first, second=second, third=third)
    return report
"""


def probe_body() -> str:
    """The script body (`run(ctx)` and helpers), runnable as a module."""
    body = PROBE_TEMPLATE.replace("__RESOLVER_SOURCE__", repr(dns.load_resolver_source()))
    return body.replace("__PROBE_SOURCE__", dns.probe_source())


def probe_ddl(schema: str, script: str) -> str:
    """`CREATE OR REPLACE` statement for the probe UDF `schema.script`."""
    header = (
        f"CREATE OR REPLACE PYTHON3 SCALAR SCRIPT {schema}.{script}"
        "(HOST VARCHAR(255), PORT DECIMAL(5,0), DOH_URL VARCHAR(1024)) "
        "RETURNS VARCHAR(2000000) UTF8 AS\n"
    )
    return header + probe_body()
