"""Every frontend fixture parses with the pydantic model of the contract type it names."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from app.backend import models

FIXTURES = Path(__file__).resolve().parents[2] / "app" / "frontend" / "src" / "test" / "fixtures"
MANIFEST: dict[str, str] = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
SCHEMA = json.loads((FIXTURES / "contract.schema.json").read_text(encoding="utf-8"))["$defs"]


def test_the_manifest_lists_every_fixture_file() -> None:
    on_disk = {
        path.name
        for path in FIXTURES.glob("*.json")
        if path.name not in ("manifest.json", "contract.schema.json")
    }
    assert on_disk == set(MANIFEST)


@pytest.mark.parametrize("name", sorted(MANIFEST))
def test_a_fixture_parses_with_its_model(name: str) -> None:
    model = getattr(models, MANIFEST[name])
    assert isinstance(model, type) and issubclass(model, BaseModel)
    data = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    parsed = model.model_validate(data)
    assert json.loads(parsed.model_dump_json(exclude_unset=True)) == data


def test_every_object_type_of_the_contract_has_a_model() -> None:
    objects = {name for name, node in SCHEMA.items() if node.get("type") == "object"}
    assert objects <= {name for name in dir(models) if isinstance(getattr(models, name), type)}


def test_every_enum_of_the_contract_is_a_model_alias() -> None:
    enums = {name for name, node in SCHEMA.items() if "enum" in node}
    assert enums <= set(dir(models))


def test_a_model_refuses_a_field_the_contract_does_not_name() -> None:
    with pytest.raises(ValueError):
        models.Team.model_validate({"team_id": 1, "school": "A", "conference": None, "x": 1})


def test_integers_stay_integers_when_a_model_is_dumped() -> None:
    team = models.Team.model_validate({"team_id": 7, "school": "A", "conference": None})
    assert team.model_dump_json() == '{"team_id":7,"school":"A","conference":null}'
