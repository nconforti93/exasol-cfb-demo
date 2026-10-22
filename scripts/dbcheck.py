"""Reachability check run before every Makefile target that needs the database."""

import argparse
import socket
import sys

from vs.host import target

CONNECT_TIMEOUT_SECONDS = 3.0


def unreachable_message(host: str, port: int, target_name: str = "local") -> str:
    if target_name == "local":
        return (
            f"Exasol is not reachable at {host}:{port}. "
            "Start it with `exasol start -d local` "
            "(never exakit: that targets the AWS deployment)."
        )
    return (
        f"Exasol (target {target_name}) is not reachable at {host}:{port}. "
        "Check that the deployment is running and that your IP is allowed to reach the port."
    )


def check(host: str, port: int, timeout: float = CONNECT_TIMEOUT_SECONDS) -> bool:
    """Return whether a TCP connection to the database port succeeds."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target", default=None, help="local or aws (default: $TARGET, else local)"
    )
    args = parser.parse_args(argv)
    try:
        deployment = target.resolve(args.target)
    except target.TargetError as error:
        print(f"db-check: {error}", file=sys.stderr)
        return 2
    if check(deployment.host, deployment.port):
        return 0
    print(unreachable_message(deployment.host, deployment.port, deployment.name), file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
