"""`make deploy`: build the bundle, then deploy the CFBD virtual schema, the loader and the model.

The real deployment is created and owned by `sys` ([20]); the password is read at run time from
the secrets file of the selected Exasol Personal deployment (`--target local|aws` or `TARGET`, see
`vs/host/target.py`) and never printed or written. `--prefix TEST_` (or any `UPPER_CASE_`) deploys
a separate namespace beside it, with its own schemas, scripts and virtual schema, by default
reading the mock connection `CFBD_API_MOCK`; it is refused together with the real connection.

Steps and their rules are documented in `vs/host/deploy.py` (virtual schema),
`loader/deploy.py` (loader: plan tables, views, Lua scripts), which runs second on the same
connection, and `model/deploy.py` (`CFB_MODEL`: lint, then one transaction under the loader's run
lock), which runs last. The model files are linted before any connection is made, so a lint
problem changes nothing. The one thing to remember: the fetch UDF is replaced only when its body
changed, because `CREATE OR REPLACE` drops its script-bound connection grant.

`--loader-config "monthly_call_budget=400,refresh_reserve=50"` (make: `LOADER_CONFIG=...`)
overrides loader settings of `loader/load-plan.yaml` until the next deploy.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from typing import Any

from loader import deploy as loader_deploy
from loader.runlock import RunLockError
from model import deploy as model_deploy
from vs import bundle
from vs.cfbd_vs.catalog import CatalogError
from vs.host import deploy, target
from vs.host.namespace import Namespace, NamespaceError

EXIT_OK = 0
EXIT_ERROR = 2

Connector = Callable[[target.Target], Any]
BundleWriter = Callable[[], bundle.BundleSizes]
ModelDeployer = Callable[[Any, Namespace], model_deploy.ModelReport]


def connect_sys(deployment: target.Target) -> Any:
    """A sys connection to the target; the password comes from its secrets file."""
    try:
        return deployment.connect()
    except target.TargetError as error:
        raise deploy.DeployError(str(error)) from None


def parse_property_arguments(items: list[str]) -> dict[str, str]:
    """`NAME=value` arguments as a property map."""
    properties: dict[str, str] = {}
    for item in items:
        name, separator, value = item.partition("=")
        if not separator or not name:
            raise deploy.DeployError(f"--property expects NAME=value, got {item!r}")
        properties[name.strip().upper()] = value
    return properties


def main(
    argv: list[str] | None = None,
    *,
    connector: Connector = connect_sys,
    write_bundle: BundleWriter = bundle.write_bundle,
    deploy_model: ModelDeployer | None = None,
) -> int:
    args = _parser().parse_args(argv)
    try:
        namespace = Namespace(args.prefix)
        options = deploy.DeployOptions(
            connection_name=args.connection,
            properties=parse_property_arguments(args.property),
            sync_api_features=not args.no_sync_api_features,
            force_fetch_replace=args.force_fetch_replace,
        )
        deploy.desired_properties(namespace, options)
        loader_options = loader_deploy.LoaderOptions(
            config_overrides=loader_deploy.parse_overrides(args.loader_config)
        )
        loader_deploy.validate(namespace, loader_options)
        model_deploy.prepare(namespace)
        deployment = target.resolve(args.target, dsn=args.dsn)
        sizes = write_bundle()
        print(
            f"bundle: adapter {sizes.adapter_chars:,} and fetch UDF {sizes.fetch_chars:,} "
            f"characters, written to {bundle.DEFAULT_OUT_DIR}"
        )
        next_steps = _deploy(
            connector(deployment),
            namespace,
            options,
            loader_options,
            deploy_model or model_deploy.deploy_model,
        )
    except (
        deploy.DeployError,
        bundle.BundleError,
        CatalogError,
        NamespaceError,
        target.TargetError,
    ) as error:
        print(f"deploy failed: {error}", file=sys.stderr)
        return EXIT_ERROR
    for step in next_steps:
        print(f"next: {step}")
    return EXIT_OK


def _deploy(
    connection: Any,
    namespace: Namespace,
    options: deploy.DeployOptions,
    loader_options: loader_deploy.LoaderOptions,
    deploy_model: ModelDeployer,
) -> tuple[str, ...]:
    """Deploy the virtual schema, the loader, then the model, printing each report as it ends."""
    try:
        report = deploy.deploy(connection, namespace, options)
        print("\n".join(report.lines))
        print("\n".join(loader_deploy.deploy(connection, namespace, loader_options).lines))
        print("\n".join(deploy_model(connection, namespace).lines))
        return report.next_steps
    except deploy.DeployError:
        raise
    except RunLockError as error:
        raise deploy.DeployError(str(error)) from None
    except Exception as error:
        message = str(getattr(error, "message", error)).strip()
        first = message.splitlines()[0] if message else type(error).__name__
        raise deploy.DeployError(f"Exasol rejected a statement: {first}") from None
    finally:
        connection.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", default="", help="namespace prefix, for example TEST_")
    parser.add_argument(
        "--connection",
        default=None,
        help="connection the virtual schema reads (default CFBD_API, CFBD_API_MOCK with a prefix)",
    )
    parser.add_argument(
        "--property",
        action="append",
        default=[],
        metavar="NAME=value",
        help="extra virtual schema property, repeatable (for example MAX_CALLS_PER_QUERY=5)",
    )
    parser.add_argument(
        "--no-sync-api-features",
        action="store_true",
        help="do not read /info to set API_FEATURES",
    )
    parser.add_argument(
        "--force-fetch-replace",
        action="store_true",
        help="replace the fetch UDF even if its body is unchanged (drops its connection grant)",
    )
    parser.add_argument(
        "--loader-config",
        default="",
        metavar="k=v,...",
        help="loader settings overriding the plan file, e.g. monthly_call_budget=400",
    )
    parser.add_argument(
        "--target", default=None, help="local or aws (default: $TARGET, else local)"
    )
    parser.add_argument("--dsn", default=None, help="overrides host and port of the target")
    return parser


if __name__ == "__main__":
    sys.exit(main())
