import json
import textwrap
from pathlib import Path
from typing import Any

import pytest

from vs.gen.overrides import Overrides, SpecIndex, parse_overrides
from vs.gen.prose_lint import (
    ProseFinding,
    ProseLintError,
    check_prose,
    find_prose_rules,
    find_uncovered,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_SPEC = REPO_ROOT / "vs" / "openapi" / "cfbd-openapi-5.33.0.json"


def param(name: str, description: str) -> dict[str, Any]:
    return {
        "name": name,
        "in": "query",
        "required": False,
        "description": description,
        "schema": {"type": "integer"},
    }


def spec_with(
    description: str = "Plain.", params: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    return {
        "paths": {
            "/things": {
                "get": {
                    "description": description,
                    "parameters": params or [param("year", "Season year."), param("team", "Team.")],
                    "responses": {},
                }
            },
            "/other": {"get": {"description": "Plain.", "parameters": [], "responses": {}}},
        }
    }


def overrides_for(spec: dict[str, Any], yaml_text: str = "{}") -> Overrides:
    return parse_overrides(textwrap.dedent(yaml_text), SpecIndex.from_openapi(spec))


def phrases_of(findings: list[ProseFinding]) -> set[tuple[str | None, str]]:
    return {(f.param, phrase) for f in findings for phrase in f.phrases}


@pytest.mark.parametrize(
    ("text", "phrase"),
    [
        ("Season year. Required unless `team` is specified.", "required unless"),
        ("One of `week` or `team` is required when filtering by year.", "required when"),
        ("Either team or week is required.", "is required"),
        ("Requires `poll=cfp` and cannot be combined with `final`.", "requires"),
        ("Cannot be combined with `minYear`.", "cannot be combined"),
        ("Defaults to `fbs`.", "defaults to"),
        ("Returns player stats, limited to 2,000 records.", "limited to"),
        ("Trailing days to include; maximum 31.", "maximum"),
        ("Returns weather. Requires Patreon Tier 1.", "requires patreon"),
    ],
)
def test_each_rule_phrase_is_detected(text: str, phrase: str) -> None:
    findings = find_prose_rules(spec_with(params=[param("year", text)]))
    assert ("year", phrase) in phrases_of(findings)


def test_plain_descriptions_produce_no_findings() -> None:
    assert find_prose_rules(spec_with()) == []


def test_requires_patreon_is_not_also_reported_as_requires() -> None:
    findings = find_prose_rules(spec_with(description="Returns weather. Requires Patreon."))
    assert phrases_of(findings) == {(None, "requires patreon")}


def test_matching_is_case_and_whitespace_insensitive() -> None:
    findings = find_prose_rules(spec_with(params=[param("year", "REQUIRED\nUNLESS team")]))
    assert ("year", "required unless") in phrases_of(findings)


def test_operation_and_parameter_findings_carry_endpoint_and_text() -> None:
    spec = spec_with(
        description="At least one of `year` or `team` is required.",
        params=[param("year", "Season year. Required unless `team` is specified.")],
    )
    findings = find_prose_rules(spec)
    assert {(f.endpoint, f.param) for f in findings} == {("/things", None), ("/things", "year")}
    assert any("At least one of" in f.text for f in findings)


def test_uncovered_param_rule_is_reported() -> None:
    spec = spec_with(params=[param("year", "Required unless `team` is specified.")])
    findings = find_uncovered(spec, overrides_for(spec))
    assert phrases_of(findings) == {("year", "required unless")}


def test_matching_rule_covers_the_parameter() -> None:
    spec = spec_with(
        params=[param("year", "Required unless `team` is specified."), param("team", "Team.")]
    )
    overrides = overrides_for(
        spec, "endpoints: {/things: {rules: [{id: r, type: one_of, params: [year, team]}]}}"
    )
    assert find_uncovered(spec, overrides) == []


def test_rule_on_another_parameter_does_not_cover() -> None:
    spec = spec_with(
        params=[param("year", "Required unless `team` is specified."), param("team", "Team.")]
    )
    overrides = overrides_for(
        spec, "endpoints: {/things: {rules: [{id: r, type: required, params: [team]}]}}"
    )
    assert phrases_of(find_uncovered(spec, overrides)) == {("year", "required unless")}


def test_rule_of_the_wrong_kind_does_not_cover() -> None:
    spec = spec_with(
        params=[param("year", "Cannot be combined with `team`."), param("team", "Team.")]
    )
    overrides = overrides_for(
        spec, "endpoints: {/things: {rules: [{id: r, type: required, params: [year]}]}}"
    )
    assert phrases_of(find_uncovered(spec, overrides)) == {("year", "cannot be combined")}
    excludes = overrides_for(
        spec,
        "endpoints: {/things: {rules: [{id: r, type: excludes, if: year, none_of: [team]}]}}",
    )
    assert find_uncovered(spec, excludes) == []


def test_default_rule_or_requirement_covers_defaults_to() -> None:
    spec = spec_with(params=[param("year", "Season year. Defaults to 2025.")])
    as_default = overrides_for(spec, "endpoints: {/things: {defaults: {year: 2025}}}")
    as_requirement = overrides_for(
        spec, "endpoints: {/things: {rules: [{id: r, type: required, params: [year]}]}}"
    )
    assert find_uncovered(spec, as_default) == []
    assert find_uncovered(spec, as_requirement) == []
    assert find_uncovered(spec, overrides_for(spec))


def test_bounds_cover_maximum_but_not_defaults_to() -> None:
    spec = spec_with(params=[param("year", "Defaults to 7; maximum 31.")])
    overrides = overrides_for(
        spec, "endpoints: {/things: {rules: [{id: r, type: bounds, param: year, max: 31}]}}"
    )
    assert phrases_of(find_uncovered(spec, overrides)) == {("year", "defaults to")}


def test_acknowledgement_covers_parameter_text() -> None:
    spec = spec_with(params=[param("team", "Team name. Defaults to Michigan.")])
    overrides = overrides_for(
        spec, 'acknowledged: [{endpoint: /things, param: team, why: "describes semantics"}]'
    )
    assert find_uncovered(spec, overrides) == []


def test_acknowledgement_without_param_covers_only_the_operation_text() -> None:
    spec = spec_with(
        description="Limited to 10 rows.",
        params=[param("team", "Team name. Defaults to Michigan.")],
    )
    overrides = overrides_for(spec, 'acknowledged: [{endpoint: /things, why: "fine"}]')
    assert phrases_of(find_uncovered(spec, overrides)) == {("team", "defaults to")}


def test_operation_level_gating_needs_gated_by() -> None:
    spec = spec_with(description="Weather. Requires Patreon.")
    assert phrases_of(find_uncovered(spec, overrides_for(spec))) == {(None, "requires patreon")}
    gated = overrides_for(spec, "endpoints: {/things: {gated_by: weather}}")
    assert find_uncovered(spec, gated) == []


def test_operation_level_row_cap_phrase_needs_row_cap_or_bounds() -> None:
    spec = spec_with(description="Returns stats, limited to 2,000 records.")
    assert phrases_of(find_uncovered(spec, overrides_for(spec))) == {(None, "limited to")}
    capped = overrides_for(
        spec, "endpoints: {/things: {row_cap: {rows: 2000, complete_with: [year]}}}"
    )
    assert find_uncovered(spec, capped) == []


def test_operation_level_requirement_phrase_is_covered_by_any_requirement_rule() -> None:
    spec = spec_with(description="At least one of `year` or `team` is required.")
    overrides = overrides_for(
        spec, "endpoints: {/things: {rules: [{id: r, type: one_of, params: [year, team]}]}}"
    )
    assert find_uncovered(spec, overrides) == []
    assert find_uncovered(spec, overrides_for(spec))


def test_excluded_endpoints_are_not_linted() -> None:
    spec = spec_with(description="Weather. Requires Patreon.")
    overrides = overrides_for(spec, "exclude: [{path: /things, reason: paid only}]")
    assert find_uncovered(spec, overrides) == []


def test_check_prose_lists_every_uncovered_item_with_its_text() -> None:
    spec = spec_with(
        description="Weather. Requires Patreon.",
        params=[
            param("year", "Required unless `team` is specified."),
            param("team", "Defaults to Michigan."),
        ],
    )
    with pytest.raises(ProseLintError) as caught:
        check_prose(spec, overrides_for(spec))
    message = str(caught.value)
    assert "/things" in message
    assert "Weather. Requires Patreon." in message
    assert "Required unless `team` is specified." in message
    assert "Defaults to Michigan." in message
    assert "year" in message
    assert len(caught.value.findings) == 3


def test_check_prose_passes_when_everything_is_covered() -> None:
    spec = spec_with(
        params=[param("year", "Required unless `team` is specified."), param("team", "Team.")]
    )
    overrides = overrides_for(
        spec, "endpoints: {/things: {rules: [{id: r, type: one_of, params: [year, team]}]}}"
    )
    check_prose(spec, overrides)


def test_parameter_refs_are_resolved() -> None:
    spec = spec_with(params=[{"$ref": "#/components/parameters/Year"}])
    spec["components"] = {"parameters": {"Year": param("year", "Defaults to 2025.")}}
    assert ("year", "defaults to") in phrases_of(find_prose_rules(spec))


def test_real_spec_findings_include_known_prose_rules() -> None:
    spec = json.loads(REAL_SPEC.read_text())
    findings = phrases_of_by_endpoint(find_prose_rules(spec))
    assert ("/games/weather", None, "requires patreon") in findings
    assert ("/roster", "year", "defaults to") in findings
    assert ("/plays/stats", None, "limited to") in findings
    assert ("/info/usage", "days", "maximum") in findings
    assert ("/conferences/affiliations", "year", "cannot be combined") in findings
    assert ("/rankings", "latest", "requires") in findings
    assert ("/games/teams", "week", "required when") in findings
    assert ("/stats/season", "year", "required unless") in findings


def phrases_of_by_endpoint(findings: list[ProseFinding]) -> set[tuple[str, str | None, str]]:
    return {(f.endpoint, f.param, phrase) for f in findings for phrase in f.phrases}
