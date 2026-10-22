"""Prose-rule lint: spec descriptions that state a rule must be covered by an override.

The OpenAPI spec states most requirement rules, defaults, caps and tier gates only in
descriptions. This module finds those phrases and reports every match that neither a rule of
the matching kind nor an `acknowledged` entry covers, so a spec bump cannot silently add an
unenforced rule.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from vs.gen.overrides import EndpointOverrides, Overrides

GATE = "gated_by"
_REQUIREMENT_TYPES = ("required", "one_of", "requires_any", "requires_value")
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class Phrase:
    """A rule phrase and the override kinds that can cover it (rule types or `gated_by`)."""

    name: str
    pattern: re.Pattern[str]
    covered_by: tuple[str, ...]


PHRASES: tuple[Phrase, ...] = (
    Phrase("required unless", re.compile(r"required unless"), _REQUIREMENT_TYPES),
    Phrase("required when", re.compile(r"required when"), _REQUIREMENT_TYPES),
    Phrase("is required", re.compile(r"\b(?:is|are) required\b"), _REQUIREMENT_TYPES),
    Phrase("requires", re.compile(r"\brequires?\b(?!\s+patreon)"), _REQUIREMENT_TYPES),
    Phrase("cannot be combined", re.compile(r"cannot be combined"), ("excludes",)),
    Phrase("defaults to", re.compile(r"\bdefaults?\s+to\b"), ("default", "required", "one_of")),
    Phrase("limited to", re.compile(r"\blimited to\b"), ("bounds", "row_cap")),
    Phrase("maximum", re.compile(r"\bmaximum\b"), ("bounds", "row_cap")),
    Phrase("requires patreon", re.compile(r"requires\s+patreon"), (GATE,)),
)


@dataclass(frozen=True)
class ProseFinding:
    """A description that matched rule phrases. `param` is None for the operation text."""

    endpoint: str
    param: str | None
    text: str
    phrases: tuple[str, ...]

    def describe(self) -> str:
        subject = "operation" if self.param is None else f"parameter {self.param}"
        return f"{self.endpoint} {subject} [{', '.join(self.phrases)}]: {self.text!r}"


class ProseLintError(ValueError):
    """Descriptions state rules that no override covers; `findings` lists every one."""

    def __init__(self, findings: list[ProseFinding]) -> None:
        lines = [f"  - {f.describe()}" for f in findings]
        super().__init__(
            "prose rules in the spec are covered by neither a rule nor an `acknowledged` "
            "entry:\n" + "\n".join(lines)
        )
        self.findings = tuple(findings)


def find_prose_rules(spec: Mapping[str, Any]) -> list[ProseFinding]:
    """Every operation and parameter description of a GET operation that matches a phrase."""
    return list(_scan(spec))


def find_uncovered(spec: Mapping[str, Any], overrides: Overrides) -> list[ProseFinding]:
    """The findings whose phrases no override covers; excluded endpoints are skipped."""
    excluded = {e.path for e in overrides.excluded}
    result = []
    for finding in _scan(spec):
        if finding.endpoint in excluded:
            continue
        uncovered = tuple(p for p in finding.phrases if not _is_covered(finding, p, overrides))
        if uncovered:
            result.append(ProseFinding(finding.endpoint, finding.param, finding.text, uncovered))
    return result


def check_prose(spec: Mapping[str, Any], overrides: Overrides) -> None:
    """Raise `ProseLintError` when any prose rule is uncovered."""
    uncovered = find_uncovered(spec, overrides)
    if uncovered:
        raise ProseLintError(uncovered)


def _scan(spec: Mapping[str, Any]) -> Iterator[ProseFinding]:
    components = spec.get("components", {}).get("parameters", {})
    for path in sorted(spec.get("paths", {})):
        operation = spec["paths"][path].get("get")
        if operation is None:
            continue
        yield from _match(path, None, operation.get("description"))
        for raw in operation.get("parameters", []):
            declared = _resolve_parameter(raw, components)
            yield from _match(path, declared.get("name"), declared.get("description"))


def _resolve_parameter(raw: Mapping[str, Any], components: Mapping[str, Any]) -> Mapping[str, Any]:
    ref = raw.get("$ref")
    if ref is None:
        return raw
    resolved: Mapping[str, Any] = components[ref.rsplit("/", 1)[-1]]
    return resolved


def _match(endpoint: str, param: str | None, text: Any) -> Iterator[ProseFinding]:
    if not isinstance(text, str):
        return
    normalized = _WHITESPACE.sub(" ", text).lower()
    names = tuple(p.name for p in PHRASES if p.pattern.search(normalized))
    if names:
        yield ProseFinding(endpoint, param, text, names)


def _is_covered(finding: ProseFinding, phrase: str, overrides: Overrides) -> bool:
    if any(
        a.endpoint == finding.endpoint and a.param == finding.param for a in overrides.acknowledged
    ):
        return True
    entry = overrides.endpoints.get(finding.endpoint)
    if entry is None:
        return False
    kinds = next(p.covered_by for p in PHRASES if p.name == phrase)
    return _has_cover(entry, kinds, finding.param)


def _has_cover(entry: EndpointOverrides, kinds: tuple[str, ...], param: str | None) -> bool:
    if GATE in kinds and entry.gated_by is not None:
        return True
    for rule in entry.rules:
        if rule.type in kinds and (param is None or param in rule.params):
            return True
    return False
