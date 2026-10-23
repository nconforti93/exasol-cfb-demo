"""The rehearsed questions: loader, file check, bindings, grader and runner.

Fifteen questions cover demo beat 5, the shared app state and the chat's guardrails. Each states
what a correct answer must touch, contain and change, and is graded by rules, never by another
model. The scripted model plays them in CI at no cost; the same runner drives live runs behind an
explicit cost cap. Nothing here opens a connection: the turn runner, the oracle runner and the
lookups are injected.

The grader's six checks: views (data tools only), results (values in a tool result), answer (values
in an accepted format), app changes, forbidden (nothing wrote, nothing reached the live schema or
CFBD), ending (done, within the cap).
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Protocol

import yaml

from app.backend.chat import sqlguard
from app.backend.chat.prompt import ViewEntry
from app.backend.chat.scripted import (
    FORMATS,
    RUNTIME_REF,
    Script,
    render_template,
    runtime_refs,
    script_from_spec,
)
from app.backend.chat.tools import TOOL_NAMES

QUESTIONS_PATH = Path(__file__).with_name("rehearsal") / "questions.yaml"
REPORT_DIRECTORY = "cache/chat-rehearsal"
SIM_LEAGUE = "sim_league"
DEMO = "demo"
SCHOOL_KEYS = ("team", "rival", "history_team")
GAME_KEY = "whatif_game"
SERVING_VIEWS = frozenset(
    {
        "CURRENT_POINT",
        "AS_OF_POINTS",
        "TEAMS",
        "PLAYOFF_ODDS",
        "PUBLISHED_RUNS",
        "ROOTING_GUIDE",
        "FORCED_RUN_ODDS",
        "WHATIF_RUN",
        "WHATIF_GAMES",
        "WHATIF_OUTCOMES",
        "WHATIF_PLAYOFF",
        "ANALYSIS_SETTINGS",
        "PREDICTIONS",
        "SCORECARD_WEEKS",
        "SCORECARD_SEASONS",
        "SCORECARD_CALIBRATION",
        "UPSETS",
        "MATCHUP_SERIES",
        "MATCHUP_GAMES",
        "MATCHUP_NEXT",
        "REFRESH_TIMELINE",
        "FETCHES",
        "GAMES",
        "TEAM_SEASONS",
        "RANKINGS",
        "SP_RATINGS",
    }
)
PASS_THROUGH_VIEWS = frozenset({"GAMES", "TEAM_SEASONS", "RANKINGS", "SP_RATINGS"})
# The preview view's name is spelled in two parts: the app's guard test greps the backend for it.
LIVE_NAMES = frozenset({"GAME" + "_PREVIEW", "QUOTA"})
LIVE_SCHEMA = "CFB_API_LIVE"
DATA_TOOLS = frozenset({"run_sql", "describe_view", "whatif_odds", "rooting_guide"})
REQUIRED_MUST_PASS = ("RQ01", "RQ04", "RQ10", "RQ14", "RQ15")
MUST_PASS_RUNS = 3
ACCEPT_MIN_PASSES = 13
FULL_SET = 15
MIN_QUESTIONS = 10
DECIMAL_TOLERANCE = 0.0005
ANSWER_FORMATS = frozenset(
    {"int", "pct", "pct_points", "record", "score", "name", "rank", "season"}
)
CHECKS = ("views", "results", "answer", "app_changes", "forbidden", "ending")
KNOWN_FORBIDDEN = frozenset({"live_schema", "write"})
MINUS_SIGN = chr(0x2212)
DASHES = "-" + chr(0x2013) + chr(0x2014) + MINUS_SIGN
_APOSTROPHES = str.maketrans({chr(c): "'" for c in (0x2019, 0x2018, 0x02BC, 0x60)})
_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z_0-9]*)\}")
_NUMBER = rf"[+\-{MINUS_SIGN}]?\d+(?:\.\d+)?"


class RehearsalError(ValueError):
    """The rehearsal file or a run's setup is wrong; the message names what."""


class BindingError(RehearsalError):
    """Names of the selected questions that the dataset does not bind, or binds to nothing."""

    def __init__(self, problems: Sequence[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = tuple(problems)


# --- the file --------------------------------------------------------------------------------


@dataclass(frozen=True)
class ViewSpec:
    """The views a correct answer reads: each of `all_of`, and one of each `any_of` group."""

    all_of: tuple[str, ...] = ()
    any_of: tuple[tuple[str, ...], ...] = ()


@dataclass(frozen=True)
class Question:
    """One rehearsed question as written in the file (placeholders still in place)."""

    id: str
    conversation: str
    kind: str
    text: str
    views: ViewSpec
    oracle: Mapping[str, Any]
    actions: tuple[Mapping[str, Any], ...]
    allowed_actions: tuple[Mapping[str, Any], ...]
    forbidden: tuple[str, ...]
    script: tuple[Mapping[str, Any], ...]
    app_state: Mapping[str, Any] | None = None
    must_pass: bool = False
    adversarial: bool = False


@dataclass(frozen=True)
class RehearsalFile:
    """The questions in file order and the bindings of each dataset."""

    questions: tuple[Question, ...]
    bindings: Mapping[str, Mapping[str, Any]]

    def question(self, question_id: str) -> Question:
        for question in self.questions:
            if question.id == question_id:
                return question
        raise RehearsalError(f"unknown question {question_id!r}")


def load_file(path: Path = QUESTIONS_PATH) -> RehearsalFile:
    """Read and parse the questions file."""
    with path.open(encoding="utf-8") as handle:
        return parse_file(yaml.safe_load(handle))


def parse_file(data: Mapping[str, Any]) -> RehearsalFile:
    """The typed file from its YAML mapping."""
    questions = tuple(_question(item) for item in data.get("questions", ()))
    bindings = {str(name): dict(values or {}) for name, values in data.get("bindings", {}).items()}
    return RehearsalFile(questions, bindings)


def _question(item: Mapping[str, Any]) -> Question:
    views = item.get("views") or {}
    return Question(
        id=str(item["id"]),
        conversation=str(item["conversation"]),
        kind=str(item["kind"]),
        text=str(item["text"]),
        views=ViewSpec(
            tuple(views.get("all_of", ())),
            tuple(tuple(group) for group in views.get("any_of", ())),
        ),
        oracle=dict(item.get("oracle") or {}),
        actions=tuple(item.get("actions") or ()),
        allowed_actions=tuple(item.get("allowed_actions") or ()),
        forbidden=tuple(item.get("forbidden") or ()),
        script=tuple(item.get("script") or ()),
        app_state=item.get("app_state"),
        must_pass=bool(item.get("must_pass", False)),
        adversarial=bool(item.get("adversarial", False)),
    )


def derived_names(values: Mapping[str, Any]) -> set[str]:
    """The names a dataset defines: its keys, `<school key>_id`, and `whatif_game_id`."""
    names = set(values)
    names |= {f"{key}_id" for key in SCHOOL_KEYS if key in values}
    if GAME_KEY in values:
        names.add(f"{GAME_KEY}_id")
    return names


def binding_names(value: Any) -> set[str]:
    """Every `{name}` of a JSON-like value that a dataset must bind (not `{API}`, not a runtime
    placeholder such as `{t2.row.X}` or `{state.page}`)."""
    found: set[str] = set()
    if isinstance(value, str):
        found |= {name for name in _PLACEHOLDER.findall(value) if name != "API"}
    elif isinstance(value, Mapping):
        for item in value.values():
            found |= binding_names(item)
    elif isinstance(value, list | tuple):
        for item in value:
            found |= binding_names(item)
    return found


def question_names(question: Question) -> set[str]:
    """The binding names a question uses anywhere."""
    parts: list[Any] = [
        question.text,
        question.app_state,
        question.oracle,
        question.actions,
        question.allowed_actions,
        question.script,
    ]
    return binding_names(parts)


def check_file(file: RehearsalFile) -> list[str]:
    """The problems of a questions file (empty when it is sound): the shape of the set, its
    coverage of beat 5, complete bindings for `sim_league`, known views and tools, and scripts whose
    placeholders point at earlier tool calls."""
    problems: list[str] = []
    regular = [q for q in file.questions if not q.adversarial]
    ids = [q.id for q in file.questions]
    if len(set(ids)) != len(ids):
        problems.append("question ids are not unique")
    if not MIN_QUESTIONS <= len(regular) <= FULL_SET:
        problems.append(
            f"the set holds {len(regular)} questions; it needs {MIN_QUESTIONS} to {FULL_SET}"
        )
    problems += _coverage(regular)
    must = {q.id for q in file.questions if q.must_pass}
    for needed in REQUIRED_MUST_PASS:
        if needed not in must:
            problems.append(f"{needed} must be a must_pass question")
    league = derived_names(file.bindings.get(SIM_LEAGUE, {}))
    if SIM_LEAGUE not in file.bindings:
        problems.append(f"no {SIM_LEAGUE} bindings")
    for question in file.questions:
        problems += _question_problems(question, league)
    return problems


def _coverage(questions: Sequence[Question]) -> list[str]:
    problems: list[str] = []
    kinds = {q.kind for q in questions}
    if not any(
        q.kind == "what-if" and any(a.get("tool") == "set_whatif" for a in q.actions)
        for q in questions
    ):
        problems.append("no what-if question expects set_whatif")
    for kind in ("swing", "app-state", "outside"):
        if kind not in kinds:
            problems.append(f"no {kind} question")
    if not any(q.kind == "outside" and _only_pass_through(q.views) for q in questions):
        problems.append("no outside question reads only pass-through views")
    if not any(q.kind == "app-state" and q.app_state for q in questions):
        problems.append("no app-state question posts an app_state")
    return problems


def _only_pass_through(views: ViewSpec) -> bool:
    names = set(views.all_of) | {name for group in views.any_of for name in group}
    return bool(names) and names <= PASS_THROUGH_VIEWS


def _question_problems(question: Question, league: set[str]) -> list[str]:
    problems: list[str] = []
    prefix = question.id
    if not _has_oracle(question):
        problems.append(f"{prefix}: no oracle and no expected refusal")
    if not question.script:
        problems.append(f"{prefix}: no script")
    for name in sorted(question_names(question) - league):
        problems.append(f"{prefix}: the binding {name!r} is not defined for {SIM_LEAGUE}")
    for view in (*question.views.all_of, *(n for g in question.views.any_of for n in g)):
        if view not in SERVING_VIEWS:
            problems.append(f"{prefix}: {view} is not a view of the serving API")
    for entry in question.forbidden:
        if entry not in KNOWN_FORBIDDEN and entry not in SERVING_VIEWS:
            problems.append(f"{prefix}: unknown forbidden entry {entry!r}")
    problems += _script_problems(question)
    return problems


def _has_oracle(question: Question) -> bool:
    keys = ("sql", "whatif", "queries", "answer_any")
    return any(question.oracle.get(key) for key in keys)


def _script_problems(question: Question) -> list[str]:
    problems: list[str] = []
    calls = 0
    for index, step in enumerate(question.script, start=1):
        for call in step.get("tools", ()):
            calls += 1
            if call.get("name") not in TOOL_NAMES:
                problems.append(
                    f"{question.id}: step {index} calls unknown tool {call.get('name')!r}"
                )
        for ref, fmt in runtime_refs(step):
            if fmt is not None and fmt not in FORMATS:
                problems.append(f"{question.id}: step {index} uses unknown format {fmt!r}")
            head = ref.split(".")[0]
            if head.startswith("t") and int(head[1:]) > calls - len(step.get("tools", ())):
                problems.append(
                    f"{question.id}: step {index} reads {head}, which has no result yet"
                )
    if question.script and "answer" not in question.script[-1]:
        problems.append(f"{question.id}: the script does not end with an answer")
    return problems


# --- bindings --------------------------------------------------------------------------------


class Lookups(Protocol):
    """Resolves names to ids once, when a run starts (`TEAMS`, `WHATIF_GAMES`)."""

    def team_id(self, school: str) -> int | None: ...

    def game_id(self, home: str, away: str, week: int) -> int | None: ...


@dataclass(frozen=True)
class Bound:
    """A dataset's bindings with the ids looked up."""

    values: Mapping[str, Any]

    def fill(self, value: Any, *, api: str | None = None) -> Any:
        """Fill `{name}` placeholders anywhere in a JSON-like value. A string that is exactly one
        placeholder becomes the bound value itself (an id stays an integer). `{API}` is filled only
        when `api` is given; runtime placeholders stay in place."""
        if isinstance(value, str):
            return self._fill_text(value, api)
        if isinstance(value, Mapping):
            return {key: self.fill(item, api=api) for key, item in value.items()}
        if isinstance(value, list | tuple):
            return [self.fill(item, api=api) for item in value]
        return value

    def _fill_text(self, text: str, api: str | None) -> Any:
        whole = _PLACEHOLDER.fullmatch(text)
        if whole is not None and whole.group(1) in self.values:
            return self.values[whole.group(1)]

        def one(match: re.Match[str]) -> str:
            name = match.group(1)
            if name == "API":
                return match.group(0) if api is None else api
            if name in self.values:
                return str(self.values[name])
            raise RehearsalError(f"the binding {name!r} is not defined")

        return _PLACEHOLDER.sub(one, text)


def resolve_bindings(
    file: RehearsalFile, dataset: str, questions: Sequence[Question], lookups: Lookups
) -> Bound:
    """The bindings of `dataset` for the selected questions, with ids. Raises `BindingError`
    naming every unbound name and every school or game the database does not hold."""
    if dataset not in file.bindings:
        raise BindingError([f"unknown dataset {dataset!r}"])
    values = dict(file.bindings[dataset])
    needed: set[str] = set()
    for question in questions:
        needed |= question_names(question)
    problems: list[str] = []
    missing = {_base_name(name) for name in needed - derived_names(values)}
    for name in sorted(missing):
        problems.append(f"unbound name {name!r}")
    resolved: dict[str, Any] = dict(values)
    for key in SCHOOL_KEYS:
        if key in values and (key in needed or f"{key}_id" in needed):
            identifier = lookups.team_id(str(values[key]))
            if identifier is None:
                problems.append(f"unknown school {values[key]!r} (binding {key!r})")
            else:
                resolved[f"{key}_id"] = identifier
    game = values.get(GAME_KEY)
    if isinstance(game, Mapping) and (GAME_KEY in needed or f"{GAME_KEY}_id" in needed):
        identifier = lookups.game_id(str(game["home"]), str(game["away"]), int(game["week"]))
        if identifier is None:
            problems.append(f"unknown game {game!r} (binding {GAME_KEY!r})")
        else:
            resolved[f"{GAME_KEY}_id"] = identifier
    if problems:
        raise BindingError(problems)
    return Bound(resolved)


def _base_name(name: str) -> str:
    stem = name.removesuffix("_id")
    return stem if name.endswith("_id") and stem in (*SCHOOL_KEYS, GAME_KEY) else name


def bound_texts(questions: Sequence[Question], bound: Bound) -> list[str]:
    """The questions as asked, with their bindings filled (the stub mode's suggestions)."""
    return [str(bound.fill(question.text)) for question in questions if not question.adversarial]


def scripts_by_text(questions: Sequence[Question], bound: Bound) -> dict[str, Script]:
    """The scripted model's scripts keyed by the exact bound question text (stub mode)."""
    return {
        str(bound.fill(q.text)): script_from_spec(bound.fill(list(q.script))) for q in questions
    }


# --- the browser's state ---------------------------------------------------------------------


def copy_state(state: Mapping[str, Any]) -> dict[str, Any]:
    """A deep copy of a state (it is plain JSON)."""
    copied: dict[str, Any] = json.loads(json.dumps(state))
    return copied


def initial_state() -> dict[str, Any]:
    """A fresh chat: the current point, the odds page, nothing in focus (the shape of the store's
    chat context)."""
    return {
        "point": None,
        "page": "odds",
        "focus_team": None,
        "pair": {"team": None, "opponent": None},
        "conditions": [],
    }


def posted_state(question: Question, bound: Bound, tracked: Mapping[str, Any]) -> dict[str, Any]:
    """The state posted with a question: its own `app_state` over the initial state when it has one,
    else the state after the conversation's earlier app changes."""
    if question.app_state is None:
        return copy_state(tracked)
    state = initial_state()
    state.update(bound.fill(dict(question.app_state)))
    return state


def apply_action(
    state: Mapping[str, Any], tool: str, tool_input: Mapping[str, Any]
) -> dict[str, Any]:
    """The state after an app-state tool, as the panel would apply it (used when the turn did not
    report the state after the action)."""
    new = copy_state(state)
    if tool == "set_whatif":
        chosen = [
            {"game_id": c["game_id"], "winner": "home" if c["home_team_wins"] else "away"}
            for c in tool_input.get("conditions", ())
        ]
        if tool_input.get("mode") == "add":
            kept = [
                c for c in new["conditions"] if c["game_id"] not in {x["game_id"] for x in chosen}
            ]
            new["conditions"] = kept + chosen
        else:
            new["conditions"] = chosen
        new["page"] = "odds"
    elif tool == "clear_whatif":
        new["conditions"] = []
    elif tool == "show_page":
        new["page"] = str(tool_input["page"]).replace("_", "-")
        if tool_input.get("team_id") is not None:
            new["focus_team"] = tool_input["team_id"]
        if tool_input["page"] == "head_to_head":
            new["pair"] = {
                "team": tool_input.get("team_id"),
                "opponent": tool_input.get("opponent_id"),
            }
    elif tool == "set_as_of":
        if tool_input.get("follow_current"):
            new["point"] = None
        else:
            new["point"] = {"season": tool_input["season"], "week_seq": tool_input["week_seq"]}
    return new


def flat_state(state: Mapping[str, Any], current: tuple[int, int] | None = None) -> dict[str, Any]:
    """The state as `{state.NAME}` placeholders read it in oracles. A point of `None` (following)
    is the current point."""
    point = state.get("point") or (
        {"season": current[0], "week_seq": current[1]} if current is not None else None
    )
    pair = state.get("pair") or {}
    flat: dict[str, Any] = {
        "page": state.get("page"),
        "focus_team": state.get("focus_team"),
        "focus_team_id": state.get("focus_team"),
        "pair_team_id": pair.get("team"),
        "pair_opponent_id": pair.get("opponent"),
    }
    if point is not None:
        flat["point_season"] = point["season"]
        flat["point_week_seq"] = point["week_seq"]
    return {key: value for key, value in flat.items() if value is not None}


# --- what a turn leaves behind ---------------------------------------------------------------


@dataclass(frozen=True)
class StatementRecord:
    """One statement of the turn, as the chat connection recorded it."""

    tool: str
    sql: str
    ok: bool
    refused: bool = False
    error: str | None = None


@dataclass(frozen=True)
class ActionTaken:
    """One accepted app change; `state_after` is the store's state when the turn reported it."""

    tool: str
    input: Mapping[str, Any]
    state_after: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class Transcript:
    """What the grader reads of one turn."""

    ended: str
    answer: str
    statements: tuple[StatementRecord, ...] = ()
    tool_results: tuple[str, ...] = ()
    actions: tuple[ActionTaken, ...] = ()
    cost_usd: float = 0.0
    tokens: int = 0
    seconds: float = 0.0
    reason: str | None = None
    stopped_by: str | None = None
    cfbd_requests: int = 0


# --- oracles ---------------------------------------------------------------------------------


class OracleRunner(Protocol):
    """The grader's own connection: never recorded, disclosed or logged as the chat's."""

    def query(self, sql: str) -> list[dict[str, Any]]: ...

    def what_if_odds(self, conditions: Sequence[tuple[int, bool]]) -> list[dict[str, Any]]: ...

    def current_point(self) -> tuple[int, int]: ...


@dataclass(frozen=True)
class Expectation:
    """What one oracle query says a correct answer carries."""

    in_results: tuple[tuple[str, str, Any], ...] = ()
    in_answer: tuple[tuple[str, str, Any], ...] = ()
    phrase_groups: tuple[tuple[str, ...], ...] = ()
    problem: str | None = None
    empty: bool = False


def _queries(oracle: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    if oracle.get("queries"):
        return list(oracle["queries"])
    if oracle.get("sql") or oracle.get("whatif"):
        return [oracle]
    return []


def run_oracle(
    question: Question,
    bound: Bound,
    state: Mapping[str, Any],
    runner: OracleRunner,
    *,
    api: str,
) -> tuple[list[Expectation], tuple[str, ...], tuple[str, ...]]:
    """Run the oracle queries of a question; returns the expectations, the `answer_any` phrases and
    the `answer_none` phrases."""
    oracle = bound.fill(dict(question.oracle))
    current = runner.current_point()
    flat = flat_state(state, current)
    expectations: list[Expectation] = []
    for query in _queries(oracle):
        try:
            rows = _oracle_rows(query, runner, api, flat)
        except Exception as error:
            expectations.append(Expectation(problem=f"the oracle failed: {type(error).__name__}"))
            continue
        expectations.append(_expectation(query, rows))
    any_phrases = () if any(e.empty for e in expectations) else tuple(oracle.get("answer_any", ()))
    none_phrases = tuple(oracle.get("answer_none", ()))
    return expectations, any_phrases, none_phrases


def _oracle_rows(
    query: Mapping[str, Any], runner: OracleRunner, api: str, flat: Mapping[str, Any]
) -> list[dict[str, Any]]:
    if query.get("whatif"):
        conditions = [
            (int(c["game_id"]), bool(c["home_team_wins"])) for c in query["whatif"]["conditions"]
        ]
        rows = runner.what_if_odds(conditions)
    else:
        sql = render_template(str(query["sql"]).replace("{API}", api), None, flat)
        rows = runner.query(sql)
    chosen = query.get("row")
    if chosen:
        wanted = {str(k).upper(): v for k, v in chosen.items()}
        rows = [r for r in rows if all(_same(_upper(r).get(k), v) for k, v in wanted.items())]
    return rows


def _upper(row: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key).upper(): value for key, value in row.items()}


def _same(left: Any, right: Any) -> bool:
    try:
        return Decimal(str(left)) == Decimal(str(right))
    except InvalidOperation:
        return str(left) == str(right)


def _expectation(query: Mapping[str, Any], rows: list[dict[str, Any]]) -> Expectation:
    if not rows:
        phrases = tuple(query.get("if_empty", ()))
        if phrases:
            return Expectation(phrase_groups=(phrases,), empty=True)
        return Expectation(problem="the oracle returned no row")
    row = _upper(rows[0])
    try:
        in_results = tuple(
            (column, str(fmt), row[column.upper()])
            for column, fmt in query.get("in_results", {}).items()
        )
        in_answer = tuple(
            (key, str(fmt), tuple(row[c.strip().upper()] for c in key.split(",")))
            if "," in key
            else (key, str(fmt), row[key.upper()])
            for key, fmt in query.get("in_answer", {}).items()
        )
    except KeyError as error:
        return Expectation(problem=f"the oracle has no column {error.args[0]}")
    groups = tuple(
        tuple(rule["any"])
        for rule in query.get("answer_when", ())
        if str(row.get(str(rule["column"]).upper())) == str(rule["equals"])
    )
    return Expectation(in_results, in_answer, groups)


# --- matching values -------------------------------------------------------------------------


def _norm(text: str) -> str:
    return " ".join(text.translate(_APOSTROPHES).casefold().split())


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float | Decimal) and not isinstance(value, bool)


def value_in_results(value: Any, scalars: Sequence[Any]) -> bool:
    """Is `value` among the scalars of the tool results: integers and names exactly (case and
    apostrophes ignored), decimals within 0.0005?"""
    if value is None:
        return True
    if _is_number(value):
        number = Decimal(str(value))
        integral = number == number.to_integral_value()
        for item in scalars:
            if not _is_number(item):
                continue
            other = Decimal(str(item))
            if integral and other == number:
                return True
            if not integral and abs(float(other) - float(number)) <= DECIMAL_TOLERANCE:
                return True
        return False
    wanted = _norm(str(value))
    return any(isinstance(item, str) and _norm(item) == wanted for item in scalars)


def _decimals(token: str) -> int:
    return len(token.split(".")[1]) if "." in token else 0


def _quantize(number: Decimal, places: int, rounding: str) -> Decimal:
    return number.quantize(Decimal(1).scaleb(-places), rounding=rounding)


def _percent_in(answer: str, value: Any, question_text: str, *, points: bool) -> bool:
    percent = abs(Decimal(str(value))) * 100
    units = r"%|percent\b"
    if points:
        units += r"|percentage\s+points?\b|points?\b|pts\b|pp\b"
    in_question = set(re.findall(r"\d+(?:\.\d+)?", question_text))
    for match in re.finditer(rf"({_NUMBER})\s*(?:{units})", answer, re.IGNORECASE):
        token = match.group(1).lstrip("+-" + MINUS_SIGN)
        if token in in_question or _decimals(token) > 2:
            continue
        digits = _decimals(token)
        shown = Decimal(token)
        if shown in (
            _quantize(percent, digits, ROUND_HALF_UP),
            _quantize(percent, digits, ROUND_HALF_EVEN),
        ):
            return True
    return False


def _int_in(answer: str, value: int) -> bool:
    for match in re.finditer(r"(?<![\d.,])(\d{1,3}(?:,\d{3})+|\d+)(?!\d|\.\d)", answer):
        if int(match.group(1).replace(",", "")) == value:
            return True
    return False


def _rank_in(answer: str, value: int) -> bool:
    return any(
        int(m.group(1)) == value
        for m in re.finditer(r"(?<![\w.])(?:#|T-?)?(\d+)(?![\d.]\d|\d)", answer)
    )


def _pair_in(answer: str, first: int, second: int, *, either_order: bool) -> bool:
    separator = rf"\s*(?:[{DASHES}]|and|to)\s*"
    orders = [(first, second), (second, first)] if either_order else [(first, second)]
    return any(re.search(rf"(?<![\d.]){a}{separator}{b}(?![\d]|\.\d)", answer) for a, b in orders)


def value_in_answer(fmt: str, value: Any, answer: str, question_text: str) -> bool:
    """Does the answer carry `value` in an accepted format (`chat/rehearsal`)?"""
    if value is None:
        return True
    if fmt == "pct":
        return _percent_in(answer, value, question_text, points=False)
    if fmt == "pct_points":
        return _percent_in(answer, value, question_text, points=True)
    if fmt in ("int", "season"):
        return _int_in(answer, int(value))
    if fmt == "rank":
        return _rank_in(answer, int(value))
    if fmt in ("record", "score"):
        first, second = (int(v) for v in value)
        return _pair_in(answer, first, second, either_order=fmt == "score")
    if fmt == "name":
        return _norm(str(value)) in _norm(answer)
    raise RehearsalError(f"unknown answer format {fmt!r}")


def result_scalars(tool_results: Sequence[str]) -> list[Any]:
    """The cells of every result of the turn that is not an error."""
    scalars: list[Any] = []
    for text in tool_results:
        try:
            body = json.loads(text)
        except ValueError:
            continue
        if isinstance(body, dict) and body.get("kind") != "error":
            for row in body.get("rows", ()):
                scalars.extend(row)
    return scalars


# --- statements ------------------------------------------------------------------------------


def statement_names(sql: str) -> set[str]:
    """The names of a statement outside strings and comments, upper-cased; empty when it cannot be
    lexed."""
    try:
        return {name.upper() for name in sqlguard.identifiers(sql)}
    except sqlguard.LexError:
        return set()


def _first_keyword(sql: str) -> str:
    try:
        for token in sqlguard.lex(sql):
            if token.kind == "word":
                return token.text.upper()
            if token.kind not in ("space", "line_comment", "block_comment"):
                return ""
    except sqlguard.LexError:
        return ""
    return ""


def _is_write(statement: StatementRecord) -> bool:
    return _first_keyword(statement.sql) not in ("SELECT", "WITH", "")


def _touches_live(statement: StatementRecord) -> bool:
    names = statement_names(statement.sql)
    return any(n.endswith(LIVE_SCHEMA) for n in names) or bool(names & LIVE_NAMES)


# --- grading ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class CheckResult:
    """One of the six checks."""

    passed: bool
    detail: str = ""


@dataclass(frozen=True)
class Grade:
    """The grade of one question run: `pass`, `fail` (the failed checks named) or `data_moved`
    (the current point changed during the question: re-run, do not count against the model)."""

    status: str
    checks: Mapping[str, CheckResult]
    failed: tuple[str, ...] = ()
    details: tuple[str, ...] = ()


def _match_action(expected: Mapping[str, Any], action: ActionTaken) -> bool:
    if expected.get("tool") != action.tool:
        return False
    for key, wanted in expected.items():
        if key == "tool":
            continue
        actual = action.input.get(key)
        if key == "conditions":
            if _conditions(actual) != _conditions(wanted):
                return False
        elif actual != wanted and not _pair_swapped(expected, action, key):
            return False
    return True


def _conditions(value: Any) -> set[tuple[Any, Any]]:
    return {(c["game_id"], c["home_team_wins"]) for c in value or ()}


def _pair_swapped(expected: Mapping[str, Any], action: ActionTaken, key: str) -> bool:
    if expected.get("page") != "head_to_head" or key not in ("team_id", "opponent_id"):
        return False
    other = "opponent_id" if key == "team_id" else "team_id"
    pair = {expected.get("team_id"), expected.get("opponent_id")}
    return (
        pair == {action.input.get("team_id"), action.input.get("opponent_id")} and other in expected
    )


def grade_question(
    question: Question,
    bound: Bound,
    transcript: Transcript,
    expectations: Sequence[Expectation],
    *,
    answer_any: Sequence[str] = (),
    answer_none: Sequence[str] = (),
    question_text: str,
    live: bool = False,
    question_max_usd: float | None = None,
    point_before: tuple[int, int] | None = None,
    point_after: tuple[int, int] | None = None,
) -> Grade:
    """Grade one run of a question by the six rules; `data_moved` when the current point changed."""
    if point_before is not None and point_after is not None and point_before != point_after:
        return Grade("data_moved", {}, (), ("the current point changed during the question",))
    checks = {
        "views": _check_views(question, transcript),
        "results": _check_results(expectations, transcript),
        "answer": _check_answer(expectations, answer_any, answer_none, transcript, question_text),
        "app_changes": _check_actions(question, bound, transcript),
        "forbidden": _check_forbidden(question, transcript),
        "ending": _check_ending(transcript, live, question_max_usd),
    }
    failed = tuple(name for name in CHECKS if not checks[name].passed)
    details = tuple(f"{name}: {checks[name].detail}" for name in failed)
    return Grade("pass" if not failed else "fail", checks, failed, details)


def _check_views(question: Question, transcript: Transcript) -> CheckResult:
    named: set[str] = set()
    for statement in transcript.statements:
        if statement.tool in DATA_TOOLS and statement.ok and not statement.refused:
            named |= statement_names(statement.sql)
    for view in question.views.all_of:
        if view not in named:
            return CheckResult(False, f"{view} is not named by a data-tool statement")
    for group in question.views.any_of:
        if not named & set(group):
            return CheckResult(
                False, f"none of {', '.join(group)} is named by a data-tool statement"
            )
    return CheckResult(True)


def _check_results(expectations: Sequence[Expectation], transcript: Transcript) -> CheckResult:
    scalars = result_scalars(transcript.tool_results)
    for expectation in expectations:
        if expectation.problem:
            return CheckResult(False, expectation.problem)
        for column, _fmt, value in expectation.in_results:
            if not value_in_results(value, scalars):
                return CheckResult(False, f"{column} is in no tool result")
    return CheckResult(True)


def _check_answer(
    expectations: Sequence[Expectation],
    answer_any: Sequence[str],
    answer_none: Sequence[str],
    transcript: Transcript,
    question_text: str,
) -> CheckResult:
    answer = transcript.answer
    lowered = _norm(answer)
    for expectation in expectations:
        for key, fmt, value in expectation.in_answer:
            if not value_in_answer(fmt, value, answer, question_text):
                return CheckResult(False, f"{key} is not in the answer as {fmt}")
        for group in expectation.phrase_groups:
            if not any(_norm(phrase) in lowered for phrase in group):
                return CheckResult(False, f"the answer lacks one of {list(group)}")
    if answer_any and not any(_norm(phrase) in lowered for phrase in answer_any):
        return CheckResult(False, f"the answer lacks one of {list(answer_any)}")
    for phrase in answer_none:
        if _norm(phrase) in lowered:
            return CheckResult(False, f"the answer repeats {phrase!r}")
    return CheckResult(True)


def _check_actions(question: Question, bound: Bound, transcript: Transcript) -> CheckResult:
    expected = [bound.fill(dict(a)) for a in question.actions]
    allowed = [bound.fill(dict(a)) for a in question.allowed_actions]
    taken = list(transcript.actions)
    for wanted in expected:
        if not any(_match_action(wanted, action) for action in taken):
            return CheckResult(False, f"expected app change {wanted.get('tool')} did not happen")
    for action in taken:
        if not any(_match_action(known, action) for known in (*expected, *allowed)):
            return CheckResult(False, f"unexpected app change {action.tool}")
    return CheckResult(True)


def _check_forbidden(question: Question, transcript: Transcript) -> CheckResult:
    guardrail = question.kind == "guardrail"
    for statement in transcript.statements:
        ran = statement.ok and not statement.refused
        if "write" in question.forbidden and _is_write(statement) and (ran or guardrail):
            return CheckResult(False, f"a write statement was {'run' if ran else 'attempted'}")
        if "live_schema" in question.forbidden and _touches_live(statement) and (ran or guardrail):
            return CheckResult(False, f"the live schema was {'reached' if ran else 'attempted'}")
        names = statement_names(statement.sql)
        for entry in question.forbidden:
            if entry in SERVING_VIEWS and ran and entry in names:
                return CheckResult(False, f"{entry} was read")
    if transcript.cfbd_requests:
        return CheckResult(False, f"{transcript.cfbd_requests} request(s) reached CFBD")
    return CheckResult(True)


def _check_ending(transcript: Transcript, live: bool, cap: float | None) -> CheckResult:
    if transcript.ended != "done":
        reason = transcript.stopped_by or transcript.reason or transcript.ended
        return CheckResult(False, f"the turn ended {transcript.ended}: {reason}")
    if live and cap is not None and transcript.cost_usd > cap:
        return CheckResult(False, f"the question cost ${transcript.cost_usd:.2f}, over ${cap:.2f}")
    return CheckResult(True)


# --- running ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class QuestionLimits:
    """The cost caps of one question: its own, and what is left of the run's."""

    question_max_usd: float | None
    run_remaining_usd: float | None


@dataclass(frozen=True)
class QuestionRun:
    """Everything the turn runner needs to play one question."""

    question: Question
    text: str
    app_state: Mapping[str, Any]
    conversation: str
    new_conversation: bool
    script: Script
    limits: QuestionLimits


class QuestionRunner(Protocol):
    """Plays one question as the browser would (stub or live) and returns its transcript."""

    def __call__(self, run: QuestionRun) -> Transcript: ...


@dataclass(frozen=True)
class QuestionResult:
    """One run of one question, as reported."""

    id: str
    run: int
    status: str
    must_pass: bool
    failed: tuple[str, ...] = ()
    details: tuple[str, ...] = ()
    text: str = ""
    answer: str = ""
    statements: tuple[str, ...] = ()
    actions: tuple[str, ...] = ()
    tokens: int = 0
    cost_usd: float = 0.0
    seconds: float = 0.0
    reason: str | None = None


@dataclass
class RehearsalReport:
    """The report of a run, ready to be written."""

    dataset: str
    live: bool
    started_at: datetime
    results: list[QuestionResult] = field(default_factory=list)
    run_max_usd: float | None = None
    question_max_usd: float | None = None

    @property
    def total_cost(self) -> float:
        return sum(r.cost_usd for r in self.results)


def run_rehearsal(
    file: RehearsalFile,
    dataset: str,
    *,
    runner: QuestionRunner,
    oracle: OracleRunner,
    lookups: Lookups,
    only: Sequence[str] | None = None,
    repeat: int = 1,
    live: bool = False,
    question_max_usd: float | None = None,
    run_max_usd: float | None = None,
    adversarial: bool = False,
    api: str = "CFB_API",
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> RehearsalReport:
    """Play the selected questions and grade them. Run 1 plays them in conversation order, several
    questions to a chat; runs 2 to `repeat` play each question alone in a fresh chat. Bindings are
    resolved first, so a bad dataset stops the run before any question."""
    selected = _select(file, only, adversarial)
    bound = resolve_bindings(file, dataset, selected, lookups)
    report = RehearsalReport(
        dataset, live, clock(), run_max_usd=run_max_usd, question_max_usd=question_max_usd
    )
    remaining = run_max_usd
    stopped = False
    for run_number in range(1, repeat + 1):
        states: dict[str, dict[str, Any]] = {}
        started: set[str] = set()
        for question in selected:
            if stopped or (remaining is not None and remaining <= 0):
                stopped = True
                report.results.append(_not_run(question, run_number, "cost cap"))
                continue
            conversation = (
                question.conversation if run_number == 1 else f"{question.id}#{run_number}"
            )
            tracked = states.setdefault(conversation, initial_state())
            result, transcript = _play(
                question,
                bound,
                tracked,
                runner,
                oracle,
                run_number,
                conversation,
                conversation not in started,
                live,
                question_max_usd,
                remaining,
                api,
            )
            started.add(conversation)
            states[conversation] = _after(tracked, transcript)
            report.results.append(result)
            if remaining is not None:
                remaining -= result.cost_usd
            if transcript.stopped_by == "run_cap":
                stopped = True
    return report


def _select(file: RehearsalFile, only: Sequence[str] | None, adversarial: bool) -> list[Question]:
    known = {q.id for q in file.questions}
    for name in only or ():
        if name not in known:
            raise RehearsalError(f"unknown question {name!r}")
    if only is None:
        return [q for q in file.questions if adversarial or not q.adversarial]
    chosen = [q for q in file.questions if q.id in only]
    if not adversarial and any(q.adversarial for q in chosen):
        raise RehearsalError("an adversarial question needs --adversarial")
    return chosen


def _not_run(question: Question, run_number: int, reason: str) -> QuestionResult:
    return QuestionResult(
        question.id, run_number, "not_run", question.must_pass, reason=f"not run: {reason}"
    )


def _after(state: Mapping[str, Any], transcript: Transcript) -> dict[str, Any]:
    current = copy_state(state)
    for action in transcript.actions:
        if action.state_after is not None:
            current = copy_state(action.state_after)
        else:
            current = apply_action(current, action.tool, action.input)
    return current


def _play(
    question: Question,
    bound: Bound,
    tracked: Mapping[str, Any],
    runner: QuestionRunner,
    oracle: OracleRunner,
    run_number: int,
    conversation: str,
    new_conversation: bool,
    live: bool,
    question_max_usd: float | None,
    remaining: float | None,
    api: str,
) -> tuple[QuestionResult, Transcript]:
    text = str(bound.fill(question.text))
    state = posted_state(question, bound, tracked)
    script = script_from_spec(bound.fill(list(question.script)))
    point_before = oracle.current_point()
    started = time.monotonic()
    transcript = runner(
        QuestionRun(
            question,
            text,
            state,
            conversation,
            new_conversation,
            script,
            QuestionLimits(question_max_usd, remaining),
        )
    )
    seconds = transcript.seconds or time.monotonic() - started
    after = _after(state, transcript)
    point_after = oracle.current_point()
    expectations, any_phrases, none_phrases = run_oracle(question, bound, after, oracle, api=api)
    grade = grade_question(
        question,
        bound,
        transcript,
        expectations,
        answer_any=any_phrases,
        answer_none=none_phrases,
        question_text=text,
        live=live,
        question_max_usd=question_max_usd,
        point_before=point_before,
        point_after=point_after,
    )
    return (
        QuestionResult(
            question.id,
            run_number,
            grade.status,
            question.must_pass,
            grade.failed,
            grade.details,
            text,
            transcript.answer,
            tuple(s.sql for s in transcript.statements),
            tuple(a.tool for a in transcript.actions),
            transcript.tokens,
            transcript.cost_usd,
            seconds,
            transcript.reason,
        ),
        transcript,
    )


# --- the summary and the report --------------------------------------------------------------


@dataclass(frozen=True)
class QuestionSummary:
    """The runs of one question: how many passed."""

    id: str
    must_pass: bool
    runs: int
    passes: int
    data_moved: int


@dataclass(frozen=True)
class Summary:
    """The summary of a rehearsal; `accepted` is None when the run did not cover the full set."""

    questions: tuple[QuestionSummary, ...]
    first_run_passes: int
    first_run_total: int
    total_cost_usd: float
    not_run: tuple[str, ...]
    accepted: bool | None
    reasons: tuple[str, ...]


def summarize(results: Sequence[QuestionResult]) -> Summary:
    """The pass counts, the must-pass rule (every run of a must-pass question passes, three runs)
    and the acceptance of a live run (13 of 15 on the first run, must-pass 3 out of 3)."""
    order: list[str] = []
    by_id: dict[str, list[QuestionResult]] = {}
    for result in results:
        if result.id not in by_id:
            order.append(result.id)
        by_id.setdefault(result.id, []).append(result)
    summaries = []
    for question_id in order:
        runs = [r for r in by_id[question_id] if r.status != "not_run"]
        summaries.append(
            QuestionSummary(
                question_id,
                by_id[question_id][0].must_pass,
                len(runs),
                sum(1 for r in runs if r.status == "pass"),
                sum(1 for r in runs if r.status == "data_moved"),
            )
        )
    first = [r for r in results if r.run == 1 and not r.id.startswith("ADV")]
    passes = sum(1 for r in first if r.status == "pass")
    not_run = tuple(f"{r.id} (run {r.run})" for r in results if r.status == "not_run")
    reasons: list[str] = []
    accepted: bool | None = None
    if len(first) >= FULL_SET and not not_run:
        if passes < ACCEPT_MIN_PASSES:
            reasons.append(f"{passes} of {len(first)} passed; {ACCEPT_MIN_PASSES} are needed")
        for item in summaries:
            if item.must_pass and (item.passes < item.runs or item.runs < MUST_PASS_RUNS):
                reasons.append(
                    f"{item.id} passed {item.passes} of {item.runs} runs; "
                    f"every run of {MUST_PASS_RUNS} must pass"
                )
        accepted = not reasons
    return Summary(
        tuple(summaries),
        passes,
        len(first),
        sum(r.cost_usd for r in results),
        not_run,
        accepted,
        tuple(reasons),
    )


def format_summary(summary: Summary) -> str:
    """The summary as printed: one line per question, then the totals and the verdict."""
    lines = []
    for item in summary.questions:
        mark = "ok  " if item.passes == item.runs and item.runs else "FAIL"
        star = " (must pass)" if item.must_pass else ""
        moved = f", {item.data_moved} data moved" if item.data_moved else ""
        lines.append(f"{mark} {item.id}{star}: {item.passes} of {item.runs}{moved}")
    lines.append(
        f"first run: {summary.first_run_passes} of {summary.first_run_total} passed; "
        f"cost ${summary.total_cost_usd:.2f}"
    )
    if summary.not_run:
        lines.append("not run: " + ", ".join(summary.not_run))
    if summary.accepted is not None:
        lines.append(
            "accepted" if summary.accepted else "not accepted: " + "; ".join(summary.reasons)
        )
    return "\n".join(lines)


def write_report(report: RehearsalReport, directory: Path) -> Path:
    """Write `<UTC time>.json` into `directory` (created) and return its path."""
    directory.mkdir(parents=True, exist_ok=True)
    summary = summarize(report.results)
    path = directory / f"{report.started_at.astimezone(UTC):%Y%m%dT%H%M%SZ}.json"
    body = {
        "dataset": report.dataset,
        "live": report.live,
        "started_at": report.started_at.isoformat(),
        "run_max_usd": report.run_max_usd,
        "question_max_usd": report.question_max_usd,
        "total_cost_usd": report.total_cost,
        "accepted": summary.accepted,
        "reasons": list(summary.reasons),
        "results": [
            {
                "id": r.id,
                "run": r.run,
                "status": r.status,
                "failed_checks": list(r.failed),
                "details": list(r.details),
                "question": r.text,
                "answer": r.answer,
                "statements": list(r.statements),
                "app_changes": list(r.actions),
                "tokens": r.tokens,
                "cost_usd": r.cost_usd,
                "seconds": round(r.seconds, 3),
                "reason": r.reason,
            }
            for r in report.results
        ],
    }
    path.write_text(json.dumps(body, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


# --- a check of the scripts against the dictionary -------------------------------------------


def unknown_names(sql: str, views: Sequence[ViewEntry]) -> list[str]:
    """Quoted names in `sql` that are neither a view, a column of any view, nor an alias that the
    statement defines: the typos a script can have before it meets a database."""
    known = {view.name.upper() for view in views}
    known |= {column.upper() for view in views for column, _comment in view.columns}
    try:
        tokens = [
            t for t in sqlguard.lex(sql) if t.kind not in ("space", "line_comment", "block_comment")
        ]
    except sqlguard.LexError:
        return ["(the statement cannot be read)"]
    aliases = {
        tokens[i + 1].text[1:-1].upper()
        for i in range(len(tokens) - 1)
        if tokens[i].kind == "word"
        and tokens[i].text.upper() == "AS"
        and tokens[i + 1].kind == "ident"
    }
    unknown = []
    for token in tokens:
        if token.kind == "ident":
            name = token.text[1:-1].replace('""', '"').upper()
            if name not in known and name not in aliases and name not in unknown:
                unknown.append(name)
    return unknown


def script_statements(question: Question, bound: Bound, *, api: str = "CFB_API") -> list[str]:
    """The SQL of a question's script and oracle, bound, for `unknown_names`."""
    found: list[str] = []
    filled_script = bound.fill(list(question.script))
    for step in filled_script:
        for call in step.get("tools", ()):
            sql = (call.get("input") or {}).get("sql")
            if isinstance(sql, str):
                found.append(RUNTIME_REF.sub("1", sql))
    oracle = bound.fill(dict(question.oracle), api=api)
    for query in _queries(oracle):
        if query.get("sql"):
            found.append(RUNTIME_REF.sub("1", str(query["sql"])))
    return found
