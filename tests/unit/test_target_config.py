"""`loader/target_config.py`: the per-target loader settings of `vs/live/loader-config.yaml`."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest

from loader import target_config
from loader.target_config import TargetConfig, TargetConfigError

CAPS = {"2026-10": 400, "2026-11": 1000}
AWS = """
targets:
  aws:
    monthly_call_budget: ledger
    refresh_reserve: 50
"""


def caps() -> Mapping[str, int]:
    return CAPS


def never() -> Mapping[str, int]:
    raise AssertionError("the ledger caps must not be read")


def resolve(
    target: str, month: str = "2026-10", explicit: Mapping[str, str] | None = None
) -> target_config.Overrides:
    config = target_config.parse(AWS)
    return target_config.resolve(config, target, explicit=explicit or {}, month=month, caps=caps)


def test_the_committed_file_makes_aws_follow_the_ledger_with_a_reserve_of_50() -> None:
    config = target_config.load()
    assert config.targets == {"aws": {"monthly_call_budget": "ledger", "refresh_reserve": "50"}}


def test_aws_in_october_gets_the_ledger_cap_of_october() -> None:
    overrides = resolve("aws")
    assert overrides.values == {"monthly_call_budget": "400", "refresh_reserve": "50"}
    assert overrides.lines == (
        "loader config for target aws (vs/live/loader-config.yaml): monthly_call_budget=400 "
        "(the ledger's cap for 2026-10), refresh_reserve=50",
    )


def test_aws_in_november_follows_the_ledger_cap_of_november() -> None:
    assert resolve("aws", "2026-11").values["monthly_call_budget"] == "1000"


def test_a_target_without_an_entry_gets_no_overrides() -> None:
    config = target_config.parse(AWS)
    overrides = target_config.resolve(config, "local", explicit={}, month="2026-12", caps=never)
    assert (overrides.values, overrides.lines) == ({}, ())


def test_an_explicit_loader_config_wins_per_setting_and_is_printed() -> None:
    overrides = resolve("aws", explicit={"monthly_call_budget": "1000"})
    assert overrides.values == {"monthly_call_budget": "1000", "refresh_reserve": "50"}
    assert overrides.lines == (
        "loader config for target aws (vs/live/loader-config.yaml): refresh_reserve=50",
        "LOADER_CONFIG: monthly_call_budget=1000 (wins over vs/live/loader-config.yaml)",
    )


def test_an_explicit_setting_the_file_does_not_name_is_printed_too() -> None:
    config = target_config.parse(AWS)
    overrides = target_config.resolve(
        config, "local", explicit={"quarantine_after": "3"}, month="2026-10", caps=never
    )
    assert overrides.values == {"quarantine_after": "3"}
    assert overrides.lines == ("LOADER_CONFIG: quarantine_after=3",)


def test_a_month_without_a_ledger_cap_refuses_the_deploy() -> None:
    with pytest.raises(TargetConfigError) as refused:
        resolve("aws", "2026-12")
    message = str(refused.value)
    assert "vs/live/ledger.yaml has no cap for 2026-12" in message
    assert 'LOADER_CONFIG="monthly_call_budget=' in message


def test_an_explicit_budget_needs_no_ledger_cap() -> None:
    config = target_config.parse(AWS)
    overrides = target_config.resolve(
        config, "aws", explicit={"monthly_call_budget": "200"}, month="2026-12", caps=never
    )
    assert overrides.values == {"monthly_call_budget": "200", "refresh_reserve": "50"}


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("[]", "must be a mapping with the key targets"),
        ("targets: {aws: {monthly_call_budget: 400}}\nextra: 1", "unknown key extra"),
        ("targets: [aws]", "targets must map a target name to its settings"),
        ("targets: {aws: 400}", "aws: the settings must be a mapping"),
        ("targets: {aws: {budget: 400}}", "aws: unknown setting budget"),
        ("targets: {aws: {now_override: '2026-10-01'}}", "aws: unknown setting now_override"),
        ("targets: {aws: {refresh_reserve: ledger}}", "aws: only monthly_call_budget may be"),
        ("targets: {aws: {refresh_reserve: true}}", "aws: refresh_reserve must be"),
        ("targets: {aws: {refresh_reserve: [1]}}", "aws: refresh_reserve must be"),
        ("targets: [", "not valid YAML"),
    ],
)
def test_an_invalid_file_is_refused_naming_the_problem(text: str, message: str) -> None:
    with pytest.raises(TargetConfigError, match=message):
        target_config.parse(text)


def test_an_empty_file_has_no_targets() -> None:
    assert target_config.parse("") == TargetConfig({})
    assert target_config.parse("targets:\n") == TargetConfig({})


def test_a_missing_file_is_an_error(tmp_path: Path) -> None:
    missing = tmp_path / "nope.yaml"
    with pytest.raises(TargetConfigError, match="cannot read"):
        target_config.load(missing)
