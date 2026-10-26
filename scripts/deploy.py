"""`make deploy`: build the bundle, then deploy the CFBD virtual schema, the loader and the model.

The real deployment is created and owned by `sys` ([20]); the password is read at run time from
the secrets file of the selected Exasol Personal deployment (`--target local|aws` or `TARGET`, see
`vs/host/target.py`) and never printed or written. `--prefix TEST_` (or any `UPPER_CASE_`) deploys
a separate namespace beside it, with its own schemas, scripts and virtual schema, by default
reading the mock connection `CFBD_API_MOCK`; it is refused together with the real connection.

Steps and their rules are documented in `vs/host/deploy.py` (virtual schema),
`loader/deploy.py` (loader: plan tables, views, Lua scripts), which runs second on the same
connection, and `model/deploy.py` (`CFB_MODEL`: lint, then one transaction), which runs last. The
model files are linted before any connection is made, so a lint problem changes nothing. The one
thing to remember: the fetch UDF is replaced only when its body changed, because
`CREATE OR REPLACE` drops its script-bound connection grant.

Loader settings: `loader/load-plan.yaml`, then the target's overrides in
`vs/live/loader-config.yaml` (`loader/target_config.py`; on `aws` the budget follows the ledger's
month cap), then `--loader-config "k=v,..."` (make: `LOADER_CONFIG=...`), which wins per setting.
The deploy prints where each override came from; they last until the next deploy.

Run lock: once the namespace has `<infra>.RUN_LOCK` (installed by its first deploy), the deploy
holds one `DEPLOY` run lock (`loader.runlock.run_lock`) around the virtual schema, the loader and
the model deploy, so it never replaces the plan, the views or the scripts under a running backfill
or refresh. A running loader run refuses the deploy before anything changes. The first deploy
runs the virtual schema and the loader without the lock and installs it; its model step then
takes the lock itself, like `make model` (Phase 3 decision [35]).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, Protocol

from analyses import deploy as api_deploy
from loader import deploy as loader_deploy
from loader import target_config
from loader.runlock import HeldLock, RunLockError, RunLockRefused, lock_installed, run_lock
from loader.target_config import TargetConfig, TargetConfigError
from model import deploy as model_deploy
from vs import bundle
from vs.cfbd_vs.catalog import CatalogError
from vs.host import deploy, ledger, target
from vs.host.namespace import Namespace, NamespaceError

EXIT_OK = 0
EXIT_ERROR = 2
DEPLOY_KIND = "DEPLOY"
DEPLOY_ARGS = "make deploy"

Connector = Callable[[target.Target], Any]
BundleWriter = Callable[[], bundle.BundleSizes]
MonthCaps = Callable[[], Mapping[str, int]]


class ModelDeployer(Protocol):
    """`model.deploy.deploy_model`: under the caller's held lock, or its own when `lock` is None."""

    def __call__(
        self, connection: Any, namespace: Namespace, *, lock: HeldLock | None = None
    ) -> model_deploy.ModelReport: ...


class ApiDeployer(Protocol):
    """`analyses.deploy.deploy_api` under the caller's held lock when available."""

    def __call__(
        self, connection: Any, namespace: Namespace, *, lock: HeldLock | None = None
    ) -> api_deploy.ApiReport: ...


def utc_now() -> datetime:
    return datetime.now(UTC)


def ledger_month_caps() -> Mapping[str, int]:
    """The owner's month caps of `vs/live/ledger.yaml`."""
    return ledger.load_config().months


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
    resolve: Callable[..., target.Target] | None = None,
    clock: Callable[[], datetime] = utc_now,
    month_caps: MonthCaps = ledger_month_caps,
    target_settings: TargetConfig | None = None,
    deploy_model: ModelDeployer | None = None,
    deploy_api: ApiDeployer | None = None,
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
        explicit = loader_deploy.parse_overrides(args.loader_config)
        deployment = (resolve or target.resolve)(args.target, dsn=args.dsn)
        overrides = target_config.resolve(
            target_settings or target_config.load(),
            deployment.name,
            explicit=explicit,
            month=ledger.month_of(clock()),
            caps=month_caps,
        )
        for line in overrides.lines:
            print(line)
        loader_options = loader_deploy.LoaderOptions(config_overrides=overrides.values)
        loader_deploy.validate(namespace, loader_options)
        model_deploy.prepare(namespace)
        api_deploy.prepare(namespace)
        sizes = write_bundle()
        print(
            f"bundle: adapter {sizes.adapter_chars:,} and fetch UDF {sizes.fetch_chars:,} "
            f"characters, written to {bundle.DEFAULT_OUT_DIR}"
        )
        next_steps = deploy_namespace(
            connector(deployment), namespace, options, loader_options, deploy_model, deploy_api
        )
    except (
        deploy.DeployError,
        bundle.BundleError,
        CatalogError,
        NamespaceError,
        target.TargetError,
        TargetConfigError,
        ledger.LedgerError,
    ) as error:
        print(f"deploy failed: {error}", file=sys.stderr)
        return EXIT_ERROR
    for step in next_steps:
        print(f"next: {step}")
    return EXIT_OK


def deploy_namespace(
    connection: Any,
    namespace: Namespace,
    options: deploy.DeployOptions,
    loader_options: loader_deploy.LoaderOptions,
    deploy_model: ModelDeployer | None = None,
    deploy_api: ApiDeployer | None = None,
) -> tuple[str, ...]:
    """Deploy the virtual schema, loader, model and API under one run lock once installed.

    The model deploys under the held lock (one `DEPLOY` run); on the first deploy, which installs
    the lock, the model step takes it itself. A refusal of that step names what was deployed.
    """
    install_model = deploy_model or model_deploy.deploy_model
    install_api = deploy_api or api_deploy.deploy_api
    try:
        with _deploy_lock(connection, namespace) as lock:
            report = deploy.deploy(connection, namespace, options)
            print("\n".join(report.lines))
            loader = loader_deploy.deploy(connection, namespace, loader_options)
            print("\n".join(loader.lines))
            model = _deploy_model(install_model, connection, namespace, lock)
            print("\n".join(model.lines))
            api = install_api(connection, namespace, lock=lock)
            print("\n".join(api.lines))
            if lock is not None:
                plan = f"deployed plan {loader.plan_hash[:12]}"
                lock.detail = f"{plan}; {lock.detail}" if lock.detail else plan
        return report.next_steps
    except RunLockRefused as error:
        raise deploy.DeployError(f"{error}; nothing was deployed") from None
    except RunLockError as error:
        raise deploy.DeployError(str(error)) from None
    except deploy.DeployError:
        raise
    except Exception as error:
        message = str(getattr(error, "message", error)).strip()
        first = message.splitlines()[0] if message else type(error).__name__
        raise deploy.DeployError(f"Exasol rejected a statement: {first}") from None
    finally:
        connection.close()


def _deploy_model(
    install_model: ModelDeployer, connection: Any, namespace: Namespace, lock: HeldLock | None
) -> model_deploy.ModelReport:
    try:
        return install_model(connection, namespace, lock=lock)
    except RunLockRefused as error:
        raise deploy.DeployError(
            f"{error}; the virtual schema and the loader were deployed, the model was not: "
            "run `make model` when the run has ended"
        ) from None


@contextmanager
def _deploy_lock(connection: Any, namespace: Namespace) -> Iterator[HeldLock | None]:
    if not lock_installed(connection, namespace):
        print(
            f"run lock: {namespace.infra_schema}.RUN_LOCK is not installed yet (first deploy), "
            "so this deploy runs without the lock and installs it"
        )
        yield None
        return
    with run_lock(connection, namespace, DEPLOY_KIND, DEPLOY_ARGS) as lock:
        print(f"run lock: {DEPLOY_KIND} run {lock.run_id}")
        yield lock


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
        help="loader settings overriding the plan file and vs/live/loader-config.yaml, "
        "e.g. monthly_call_budget=400",
    )
    parser.add_argument(
        "--target", default=None, help="local or aws (default: $TARGET, else local)"
    )
    parser.add_argument("--dsn", default=None, help="overrides host and port of the target")
    return parser


if __name__ == "__main__":
    sys.exit(main())
