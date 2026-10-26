"""`make api`: install the read-only serving views for app and chat."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from typing import Any

from analyses import deploy as api_deploy
from loader.runlock import RunLockError
from vs.host import target
from vs.host.deploy import DeployError
from vs.host.namespace import Namespace, NamespaceError


def main(
    argv: list[str] | None = None,
    *,
    connector: Callable[[target.Target], Any] | None = None,
    resolve: Callable[..., target.Target] = target.resolve,
) -> int:
    """Lint the serving files, then deploy them to the selected Exasol target."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=("local", "aws"))
    parser.add_argument("--dsn")
    parser.add_argument("--prefix", default="")
    parser.add_argument("--lint-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        namespace = Namespace(args.prefix)
        api, live = api_deploy.prepare(namespace)
        if args.lint_only:
            print(f"lint ok: {len(api)} API and {len(live)} live views; nothing sent, 0 CFBD calls")
            return 0
        deployment = resolve(args.target, dsn=args.dsn)
        connection = (connector or (lambda chosen: chosen.connect()))(deployment)
        try:
            report = api_deploy.deploy_api(connection, namespace)
        finally:
            connection.close()
        for line in report.lines:
            print(line)
        return 0
    except (DeployError, NamespaceError, RunLockError, target.TargetError) as error:
        print(f"api deploy failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
