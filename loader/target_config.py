"""Per-target loader settings that `make deploy` applies: `vs/live/loader-config.yaml`.

`loader/load-plan.yaml` holds the loader settings of every deployment. This file adds, per
deployment target (`local`, `aws`; `vs/host/target.py`), the `LOADER_CONFIG` overrides that a plain
`make deploy TARGET=<target>` applies, so a redeploy (to fix a view, say) never silently lifts a
target's in-database budget (spec `loader/quota`):

    targets:
      aws:
        monthly_call_budget: ledger
        refresh_reserve: 50

A value is a whole number or text, as `LOADER_CONFIG` takes it, and the deploy validates it like
the plan's settings. `monthly_call_budget` may be `ledger`: the owner's cap in
`vs/live/ledger.yaml` for the UTC month of the deploy, so the in-database budget follows the
ledger's month cap. A month without a cap refuses the deploy, unless `LOADER_CONFIG` sets
`monthly_call_budget` itself. `now_override` is not allowed here (the test clock).

An explicit `make deploy LOADER_CONFIG="k=v,..."` wins per setting; `resolve` prints both. The
values last until the next deploy: after a month change, redeploy to follow the new month's cap.
Until then the previous month's value stays in force.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

import yaml

from loader.plan import PlanConfig

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "vs" / "live" / "loader-config.yaml"
CONFIG_HINT = "vs/live/loader-config.yaml"
LEDGER_HINT = "vs/live/ledger.yaml"
LEDGER_VALUE = "ledger"
LEDGER_SETTING = "monthly_call_budget"
SETTINGS = frozenset(f.name for f in fields(PlanConfig) if f.name != "now_override")

MonthCaps = Callable[[], Mapping[str, int]]


class TargetConfigError(ValueError):
    """The file is invalid, or a `ledger` value has no month cap; the message says what to do."""


@dataclass(frozen=True)
class TargetConfig:
    """Settings per target, as `LOADER_CONFIG` text values (`ledger` not yet resolved)."""

    targets: Mapping[str, Mapping[str, str]]


@dataclass(frozen=True)
class Overrides:
    """The `LOADER_CONFIG` overrides of one deploy and the lines that say where they came from."""

    values: dict[str, str]
    lines: tuple[str, ...]


def load(path: Path = CONFIG_PATH) -> TargetConfig:
    """The validated file; a missing or invalid file raises `TargetConfigError`."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise TargetConfigError(f"cannot read {CONFIG_HINT}: {error}") from None
    return parse(text)


def parse(text: str) -> TargetConfig:
    """Validate the YAML `text`; every problem raises `TargetConfigError` naming it."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        raise TargetConfigError(f"{CONFIG_HINT} is not valid YAML") from None
    if data is None:
        return TargetConfig({})
    if not isinstance(data, dict) or "targets" not in data:
        raise TargetConfigError(f"{CONFIG_HINT} must be a mapping with the key targets")
    extra = sorted(set(data) - {"targets"}, key=str)
    if extra:
        raise TargetConfigError(f"{CONFIG_HINT}: unknown key {extra[0]}")
    raw = data["targets"] or {}
    if not isinstance(raw, dict):
        raise TargetConfigError(f"{CONFIG_HINT}: targets must map a target name to its settings")
    return TargetConfig({str(name): _settings(str(name), value) for name, value in raw.items()})


def resolve(
    config: TargetConfig,
    target: str,
    *,
    explicit: Mapping[str, str],
    month: str,
    caps: MonthCaps,
) -> Overrides:
    """The overrides of a deploy to `target` in UTC month `month`; `explicit` wins per setting.

    `caps` returns the ledger's month caps; it is called only for a `ledger` value.
    """
    configured = config.targets.get(target, {})
    values: dict[str, str] = {}
    shown: list[str] = []
    for name, value in configured.items():
        if name in explicit:
            continue
        if value == LEDGER_VALUE:
            cap = caps().get(month)
            if cap is None:
                raise TargetConfigError(
                    f"{CONFIG_HINT}: target {target} takes {name} from the ledger, but "
                    f"{LEDGER_HINT} has no cap for {month}. The owner adds the month there, or "
                    f'deploy with LOADER_CONFIG="{name}=<calls>"'
                )
            values[name] = str(cap)
            shown.append(f"{name}={cap} (the ledger's cap for {month})")
        else:
            values[name] = value
            shown.append(f"{name}={value}")
    lines = (
        [f"loader config for target {target} ({CONFIG_HINT}): {', '.join(shown)}"] if shown else []
    )
    if explicit:
        given = ", ".join(
            f"{name}={value}" + (f" (wins over {CONFIG_HINT})" if name in configured else "")
            for name, value in explicit.items()
        )
        lines.append(f"LOADER_CONFIG: {given}")
    values.update(explicit)
    return Overrides(values, tuple(lines))


def _settings(target: str, raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise TargetConfigError(f"{CONFIG_HINT}: {target}: the settings must be a mapping")
    settings: dict[str, str] = {}
    for name, value in raw.items():
        if name not in SETTINGS:
            known = ", ".join(sorted(SETTINGS))
            raise TargetConfigError(
                f"{CONFIG_HINT}: {target}: unknown setting {name}; known: {known}"
            )
        settings[str(name)] = _text(target, str(name), value)
    return settings


def _text(target: str, name: str, value: Any) -> str:
    if value == LEDGER_VALUE and name != LEDGER_SETTING:
        raise TargetConfigError(
            f"{CONFIG_HINT}: {target}: only {LEDGER_SETTING} may be {LEDGER_VALUE!r}, not {name}"
        )
    if isinstance(value, bool) or not isinstance(value, int | str):
        raise TargetConfigError(
            f"{CONFIG_HINT}: {target}: {name} must be a whole number or text, got {value!r}"
        )
    return str(value)
