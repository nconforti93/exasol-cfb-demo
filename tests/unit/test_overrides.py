import json
import textwrap
from pathlib import Path
from typing import Any

import pytest

from vs.cfbd_vs.catalog import Catalog, Rule
from vs.gen.overrides import (
    Overrides,
    OverridesError,
    SpecIndex,
    load_overrides,
    parse_overrides,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_SPEC = REPO_ROOT / "vs" / "openapi" / "cfbd-openapi-5.33.0.json"
REAL_OVERRIDES = REPO_ROOT / "vs" / "catalog-overrides.yaml"
COMMITTED_CATALOG = REPO_ROOT / "vs" / "catalog" / "catalog.json"


def query(
    name: str, schema: dict[str, Any], description: str = "", required: bool = False
) -> dict[str, Any]:
    return {
        "name": name,
        "in": "query",
        "required": required,
        "description": description,
        "schema": schema,
    }


def ok_response(schema: dict[str, Any]) -> dict[str, Any]:
    return {"200": {"content": {"application/json": {"schema": schema}}}}


SPEC = {
    "paths": {
        "/games": {
            "get": {
                "description": "Games.",
                "parameters": [
                    query("year", {"type": "integer"}),
                    query("week", {"type": "integer"}),
                    query("team", {"type": "string"}),
                    query("id", {"type": "integer"}),
                    query("classification", {"$ref": "#/components/schemas/Division"}),
                    query("latest", {"type": "boolean"}),
                    query("poll", {"type": "string"}),
                ],
                "responses": ok_response(
                    {"type": "array", "items": {"$ref": "#/components/schemas/Game"}}
                ),
            }
        },
        "/games/teams": {
            "get": {
                "parameters": [
                    query("year", {"type": "integer"}),
                    query("id", {"type": "integer"}),
                ],
                "responses": ok_response(
                    {"type": "array", "items": {"$ref": "#/components/schemas/GameTeams"}}
                ),
            }
        },
        "/weather": {"get": {"parameters": [], "responses": ok_response({"type": "object"})}},
        "/info": {"get": {"parameters": [], "responses": ok_response({"type": "object"})}},
    },
    "components": {
        "schemas": {
            "Division": {"type": "string", "enum": ["fbs", "fcs"]},
            "Game": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "homeTeam": {"type": "string"},
                    "date": {"type": "string"},
                    "venue": {"type": "object", "properties": {"name": {"type": "string"}}},
                },
            },
            "GameTeams": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "teams": {"type": "array", "items": {"$ref": "#/components/schemas/Side"}},
                },
            },
            "Side": {
                "type": "object",
                "properties": {
                    "team": {"type": "string"},
                    "stats": {"type": "array", "items": {"$ref": "#/components/schemas/Stat"}},
                },
            },
            "Stat": {
                "type": "object",
                "properties": {
                    "category": {"type": "string"},
                    "again": {"$ref": "#/components/schemas/Side"},
                },
            },
        }
    },
}


@pytest.fixture(scope="module")
def spec() -> SpecIndex:
    return SpecIndex.from_openapi(SPEC)


def parse(yaml_text: str, spec: SpecIndex) -> Overrides:
    return parse_overrides(textwrap.dedent(yaml_text), spec)


def errors_of(yaml_text: str, spec: SpecIndex) -> list[str]:
    with pytest.raises(OverridesError) as caught:
        parse(yaml_text, spec)
    return list(caught.value.errors)


def test_spec_index_resolves_refs_enums_and_field_paths(spec: SpecIndex) -> None:
    games = spec.endpoints["/games"]
    assert games.params["classification"].enum == ("fbs", "fcs")
    assert games.params["classification"].type == "string"
    assert games.params["year"].type == "integer"
    assert ("venue", "name") in games.fields
    assert ("venue",) in games.fields
    teams = spec.endpoints["/games/teams"]
    assert ("teams", "stats", "category") in teams.fields
    assert ("teams",) in teams.arrays
    assert () in teams.arrays


def test_spec_index_handles_cyclic_schemas(spec: SpecIndex) -> None:
    fields = spec.endpoints["/games/teams"].fields
    assert ("teams", "stats", "again") in fields
    assert ("teams", "stats", "again", "team") not in fields


def test_spec_index_on_real_spec_finds_known_paths() -> None:
    index = SpecIndex.from_openapi(json.loads(REAL_SPEC.read_text()))
    assert len(index.endpoints) == 93
    rankings = index.endpoints["/rankings"]
    assert rankings.params["week"].type == "number"
    assert rankings.params["seasonType"].enum is not None
    assert ("polls", "ranks", "team") in rankings.fields or any(
        path[:2] == ("polls", "ranks") for path in rankings.fields
    )


RANKS_PARENT_KEYS = (("season",), ("seasonType",), ("week",), ("polls", "poll"))


def test_real_overrides_give_the_ranks_their_own_week_season_and_poll() -> None:
    index = SpecIndex.from_openapi(json.loads(REAL_SPEC.read_text()))
    real = parse_overrides(REAL_OVERRIDES.read_text(encoding="utf-8"), index)
    assert real.children["RANKINGS_POLLS_RANKS"].parent_keys == RANKS_PARENT_KEYS


def test_committed_ranks_table_loads_standalone() -> None:
    ranks = Catalog.from_json(COMMITTED_CATALOG.read_text(encoding="utf-8")).table(
        "RANKINGS_POLLS_RANKS"
    )
    keys = [(c.name, c.path) for c in ranks.columns if c.kind == "parent_key"]
    assert keys == [
        ("SEASON", ("season",)),
        ("SEASON_TYPE", ("seasonType",)),
        ("WEEK", ("week",)),
        ("POLL", ("polls", "poll")),
    ]


def test_empty_document_is_valid(spec: SpecIndex) -> None:
    result = parse("{}", spec)
    assert result.endpoints == {}
    assert result.excluded == ()
    assert result.acknowledged == ()


def test_full_example_loads(spec: SpecIndex) -> None:
    result = parse(
        """
        endpoints:
          /games:
            defaults: {classification: fbs}
            verify: [classification-omission-means-all]
            read_through: false
            rules:
              - {id: year_unless_id, type: one_of, params: [year, id]}
              - id: week_needs_year
                type: requires_any
                if: week
                any_of: [year]
                why: week alone is ambiguous
          /games/teams:
            hoist: {array: teams, carry: {id: GAME_ID}}
          /weather: {gated_by: weather}
        exclude:
          - {path: /info, reason: not a data endpoint}
        acknowledged:
          - {endpoint: /games, param: team, why: "describes semantics, not a rule"}
        """,
        spec,
    )
    games = result.endpoints["/games"]
    assert games.read_through is False
    assert games.verify == ("classification-omission-means-all",)
    assert games.defaults == {"classification": "fbs"}
    ids = [r.id for r in games.rules]
    assert ids == ["games.year_unless_id", "games.week_needs_year", "games.default_classification"]
    assert games.rules[1].message == "week alone is ambiguous"
    assert result.endpoints["/games/teams"].hoist is not None
    assert result.endpoints["/games/teams"].hoist.array == ("teams",)
    assert result.endpoints["/games/teams"].hoist.carry == {("id",): "GAME_ID"}
    assert result.endpoints["/weather"].gated_by == "weather"
    assert [e.path for e in result.excluded] == ["/info"]
    assert result.acknowledged[0].param == "team"


def rule_of(yaml_rule: str, spec: SpecIndex) -> Rule:
    result = parse(
        f"""
        endpoints:
          /games:
            rules:
              - {yaml_rule}
        """,
        spec,
    )
    return result.endpoints["/games"].rules[0]


def test_required_rule(spec: SpecIndex) -> None:
    rule = rule_of("{id: r, type: required, params: [year, week]}", spec)
    assert (rule.id, rule.type, rule.params) == ("games.r", "required", ("year", "week"))


def test_requires_any_rule_with_if_value(spec: SpecIndex) -> None:
    rule = rule_of("{id: r, type: requires_any, if: latest, if_value: true, any_of: [poll]}", spec)
    assert rule.details == {"if": "latest", "if_value": True, "any_of": ["poll"]}
    assert set(rule.params) == {"latest", "poll"}


def test_excludes_rule(spec: SpecIndex) -> None:
    rule = rule_of("{id: r, type: excludes, if: year, none_of: [id, week]}", spec)
    assert rule.details == {"if": "year", "none_of": ["id", "week"]}


def test_requires_value_rule(spec: SpecIndex) -> None:
    rule = rule_of(
        "{id: r, type: requires_value, if: latest, if_value: true, then: poll, then_value: cfp}",
        spec,
    )
    assert rule.details == {
        "if": "latest",
        "if_value": True,
        "then": "poll",
        "then_value": "cfp",
    }


def test_bounds_rule(spec: SpecIndex) -> None:
    rule = rule_of("{id: r, type: bounds, param: year, min: 1869, max: 2100}", spec)
    assert rule.params == ("year",)
    assert rule.details == {"min": 1869, "max": 2100}


def test_default_rule_form_and_sugar_are_equivalent(spec: SpecIndex) -> None:
    sugar = parse("endpoints: {/games: {defaults: {classification: fbs}}}", spec)
    explicit = rule_of(
        "{id: default_classification, type: default, param: classification, value: fbs}", spec
    )
    assert sugar.endpoints["/games"].rules[0] == explicit


def test_row_cap_sugar_creates_the_rule(spec: SpecIndex) -> None:
    result = parse(
        "endpoints: {/games: {row_cap: {rows: 2000, complete_with: [id]}}}",
        spec,
    )
    games = result.endpoints["/games"]
    rule = games.rules[0]
    assert (rule.id, rule.type) == ("games.row_cap", "row_cap")
    assert rule.details == {"rows": 2000, "complete_with": ["id"]}
    assert rule.params == ("id",)


def test_rules_are_acceptable_to_the_rules_engine(spec: SpecIndex) -> None:
    from vs.cfbd_vs.catalog import Param, Table
    from vs.cfbd_vs.rules import RuleViolation, check_rules

    result = parse(
        """
        endpoints:
          /games:
            rules:
              - {id: r, type: requires_any, if: year, any_of: [week, team]}
        """,
        spec,
    )
    params = tuple(
        Param(n, "P_" + n.upper(), "query", "integer", "X", None) for n in ("year", "week", "team")
    )
    table = Table(
        "GAMES",
        "/games",
        "endpoint",
        None,
        (),
        False,
        True,
        None,
        params,
        (),
        result.endpoints["/games"].rules,
    )
    check_rules(table, {"year": [2024], "week": [1]})
    with pytest.raises(RuleViolation):
        check_rules(table, {"year": [2024]})


def test_curated_and_raw_json(spec: SpecIndex) -> None:
    result = parse(
        "endpoints: {/games: {curated: [id, homeTeam, venue.name], raw_json: true}}", spec
    )
    games = result.endpoints["/games"]
    assert games.curated == (("id",), ("homeTeam",), ("venue", "name"))
    assert games.raw_json is True


def test_param_and_field_overrides(spec: SpecIndex) -> None:
    result = parse(
        """
        endpoints:
          /games:
            params: {week: {type: number}}
            fields:
              date: {rename: DATE_, size: 40}
              homeTeam: {type: string, size: 100}
              id: {type: integer}
        """,
        spec,
    )
    games = result.endpoints["/games"]
    assert games.params["week"].type == "number"
    assert games.fields[("date",)].rename == "DATE_"
    assert games.fields[("date",)].size == 40
    assert games.fields[("id",)].type == "integer"


def test_children_parent_keys(spec: SpecIndex) -> None:
    result = parse("children: {GAMES_TEAMS_TEAMS: {parent_keys: [id]}}", spec)
    assert result.children["GAMES_TEAMS_TEAMS"].parent_keys == (("id",),)


def test_every_error_is_listed_not_just_the_first(spec: SpecIndex) -> None:
    errors = errors_of(
        """
        endpoints:
          /nope: {read_through: false}
          /games:
            rules:
              - {id: a, type: required, params: [bogus]}
            curated: [missing.field]
            gated_by: not-a-feature
        """,
        spec,
    )
    assert len(errors) == 4
    assert any("/nope" in e and "unknown endpoint" in e for e in errors)
    assert any("bogus" in e and "unknown parameter" in e for e in errors)
    assert any("missing.field" in e and "unknown response field" in e for e in errors)
    assert any("not-a-feature" in e for e in errors)


def test_error_text_names_the_location(spec: SpecIndex) -> None:
    errors = errors_of(
        "endpoints: {/games: {rules: [{id: a, type: required, params: [bogus]}]}}", spec
    )
    assert errors[0].startswith("endpoints['/games'].rules[0]")


def test_exclusion_without_reason_is_an_error(spec: SpecIndex) -> None:
    assert any("reason" in e for e in errors_of("exclude: [{path: /info}]", spec))
    assert any("reason" in e for e in errors_of("exclude: [{path: /info, reason: ''}]", spec))


def test_exclusion_of_unknown_endpoint_is_an_error(spec: SpecIndex) -> None:
    errors = errors_of("exclude: [{path: /nope, reason: why}]", spec)
    assert any("/nope" in e and "unknown endpoint" in e for e in errors)


def test_excluded_endpoint_cannot_have_other_entries(spec: SpecIndex) -> None:
    errors = errors_of(
        """
        endpoints: {/info: {read_through: false}}
        exclude: [{path: /info, reason: gone}]
        """,
        spec,
    )
    assert any("/info" in e and "excluded" in e for e in errors)


@pytest.mark.parametrize(
    "rule",
    [
        "{id: a, type: nonsense}",
        "{id: a, type: required}",
        "{id: a, type: required, params: []}",
        "{id: a, type: one_of, params: [year]}",
        "{id: a, type: requires_any, if: year}",
        "{id: a, type: requires_any, if: year, any_of: []}",
        "{id: a, type: excludes, if: year}",
        "{id: a, type: requires_value, if: latest, then: poll, then_value: cfp}",
        "{id: a, type: bounds, param: year}",
        "{id: a, type: bounds, param: year, min: 5, max: 1}",
        "{id: a, type: bounds, param: team, max: 3}",
        "{id: a, type: bounds, param: year, max: abc}",
        "{id: a, type: default, param: year}",
        "{id: a, type: required, params: [year], extra: 1}",
        "{type: required, params: [year]}",
        "{id: Bad-Id, type: required, params: [year]}",
        "{id: a, type: requires_any, if: latest, if_value: 3, any_of: [year]}",
        "{id: a, type: requires_value, if: latest, if_value: true, then: poll, then_value: 5}",
        "{id: a, type: default, param: classification, value: d3}",
        "{id: a, type: row_cap, rows: 0, complete_with: [id]}",
        "{id: a, type: row_cap, rows: 5, complete_with: []}",
    ],
)
def test_invalid_rule_entries_are_errors(spec: SpecIndex, rule: str) -> None:
    errors = errors_of(f"endpoints: {{/games: {{rules: [{rule}]}}}}", spec)
    assert errors


def test_duplicate_rule_ids_are_errors(spec: SpecIndex) -> None:
    errors = errors_of(
        """
        endpoints:
          /games:
            rules:
              - {id: a, type: required, params: [year]}
              - {id: a, type: required, params: [week]}
        """,
        spec,
    )
    assert any("duplicate rule id" in e for e in errors)


def test_two_defaults_for_one_parameter_are_errors(spec: SpecIndex) -> None:
    errors = errors_of(
        """
        endpoints:
          /games:
            defaults: {classification: fbs}
            rules: [{id: other, type: default, param: classification, value: fcs}]
        """,
        spec,
    )
    assert any("default" in e and "classification" in e for e in errors)


def test_default_value_must_match_parameter_type_and_enum(spec: SpecIndex) -> None:
    assert errors_of("endpoints: {/games: {defaults: {year: abc}}}", spec)
    assert errors_of("endpoints: {/games: {defaults: {classification: d3}}}", spec)
    assert errors_of("endpoints: {/games: {defaults: {bogus: 1}}}", spec)


def test_param_with_default_cannot_also_be_required(spec: SpecIndex) -> None:
    errors = errors_of(
        """
        endpoints:
          /games:
            defaults: {year: 2025}
            rules: [{id: r, type: required, params: [year]}]
        """,
        spec,
    )
    assert any("default" in e and "year" in e for e in errors)


def test_param_type_override_applies_to_rule_value_checks(spec: SpecIndex) -> None:
    result = parse(
        """
        endpoints:
          /games:
            params: {week: {type: number}}
            rules: [{id: b, type: bounds, param: week, max: 2.5}]
        """,
        spec,
    )
    assert result.endpoints["/games"].rules[0].details == {"max": 2.5}


def test_unknown_keys_are_errors(spec: SpecIndex) -> None:
    assert errors_of("surprise: 1", spec)
    assert errors_of("endpoints: {/games: {surprise: 1}}", spec)
    assert errors_of("exclude: [{path: /info, reason: x, extra: 1}]", spec)


def test_gated_by_must_be_a_known_feature(spec: SpecIndex) -> None:
    assert parse("endpoints: {/weather: {gated_by: weather}}", spec)
    assert errors_of("endpoints: {/weather: {gated_by: Weather}}", spec)


def test_hoist_validation(spec: SpecIndex) -> None:
    assert errors_of(
        "endpoints: {/games/teams: {hoist: {array: nope, carry: {id: GAME_ID}}}}", spec
    )
    assert errors_of("endpoints: {/games/teams: {hoist: {array: id, carry: {id: GAME_ID}}}}", spec)
    assert errors_of(
        "endpoints: {/games/teams: {hoist: {array: teams, carry: {nope: GAME_ID}}}}", spec
    )
    assert errors_of(
        "endpoints: {/games/teams: {hoist: {array: teams, carry: {id: bad name}}}}", spec
    )


def test_raw_json_requires_curated(spec: SpecIndex) -> None:
    assert errors_of("endpoints: {/games: {raw_json: true}}", spec)


def test_field_override_validation(spec: SpecIndex) -> None:
    assert errors_of("endpoints: {/games: {fields: {nope: {rename: X}}}}", spec)
    assert errors_of("endpoints: {/games: {fields: {date: {size: 0}}}}", spec)
    assert errors_of("endpoints: {/games: {fields: {date: {size: 40, type: integer}}}}", spec)
    assert errors_of("endpoints: {/games: {fields: {date: {type: blob}}}}", spec)
    assert errors_of("endpoints: {/games: {fields: {date: {rename: lower}}}}", spec)
    assert errors_of("endpoints: {/games: {fields: {date: {}}}}", spec)


def test_param_override_validation(spec: SpecIndex) -> None:
    assert errors_of("endpoints: {/games: {params: {nope: {type: integer}}}}", spec)
    assert errors_of("endpoints: {/games: {params: {week: {type: blob}}}}", spec)


def test_children_validation(spec: SpecIndex) -> None:
    assert errors_of("children: {lowercase: {parent_keys: [id]}}", spec)
    assert errors_of("children: {UNRELATED_TABLE: {parent_keys: [id]}}", spec)
    assert errors_of("children: {GAMES_TEAMS_TEAMS: {parent_keys: [nope]}}", spec)
    assert errors_of("children: {GAMES_TEAMS_TEAMS: {parent_keys: []}}", spec)


def test_acknowledged_validation(spec: SpecIndex) -> None:
    assert errors_of("acknowledged: [{endpoint: /nope, why: x}]", spec)
    assert errors_of("acknowledged: [{endpoint: /games, param: nope, why: x}]", spec)
    assert errors_of("acknowledged: [{endpoint: /games, param: team}]", spec)
    assert errors_of("acknowledged: [{endpoint: /games, param: team, why: ''}]", spec)
    assert parse("acknowledged: [{endpoint: /games, why: op level}]", spec).acknowledged


def test_verify_must_be_list_of_strings(spec: SpecIndex) -> None:
    assert errors_of("endpoints: {/games: {verify: [1]}}", spec)
    assert errors_of("endpoints: {/games: {verify: x}}", spec)


def test_invalid_yaml_and_wrong_shape_are_errors(spec: SpecIndex) -> None:
    assert errors_of("endpoints: [", spec)
    assert errors_of("- a\n- b", spec)
    assert errors_of("endpoints: [1]", spec)


def test_load_overrides_reads_a_file(spec: SpecIndex, tmp_path: Path) -> None:
    path = tmp_path / "o.yaml"
    path.write_text("endpoints: {/games: {read_through: false}}\n")
    assert load_overrides(path, spec).endpoints["/games"].read_through is False


def test_rule_id_slug_drops_path_parameters() -> None:
    index = SpecIndex.from_openapi(
        {
            "paths": {
                "/games/{gameId}/preview": {
                    "get": {
                        "parameters": [
                            {
                                "name": "gameId",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "integer"},
                            }
                        ],
                        "responses": ok_response({"type": "object"}),
                    }
                }
            }
        }
    )
    result = parse_overrides(
        "endpoints:\n"
        "  '/games/{gameId}/preview':\n"
        "    rules: [{id: r, type: required, params: [gameId]}]\n",
        index,
    )
    assert result.endpoints["/games/{gameId}/preview"].rules[0].id == "games_preview.r"
