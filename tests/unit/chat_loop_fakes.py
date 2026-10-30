"""Fakes for the turn loop and the sessions: lookups and tools that behave like tasks 3.2 and 3.3
will (they disclose each statement through the tool context), and a model that waits for its
response headers without watching the cancel token."""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from app.backend.chat import appstate, tools
from app.backend.chat.appstate import AppChange, Condition, GameInfo, PointInfo
from app.backend.chat.loop import Statement, ToolContext, ToolOutcome
from app.backend.chat.model import CancelToken, ChatModel, ModelRequest, ModelResponse
from app.backend.chat.tools import ToolInput

SYSTEM = "You are the analyst of the demo app."
BROOKVALE, ASHFORD = 12, 7
GAME = 401


@dataclass
class DisclosingLookups:
    """State lookups over a tiny league; each lookup is one disclosed statement."""

    context: ToolContext
    schools: Mapping[int, str] = field(
        default_factory=lambda: {BROOKVALE: "Brookvale", ASHFORD: "Ashford"}
    )

    def point(self, season: int, week_seq: int) -> PointInfo | None:
        self.context.disclose(Statement(f'SELECT * FROM "AS_OF_POINTS" /* {season} */', rows=1))
        if (season, week_seq) == (2026, 10):
            return PointInfo(2026, 10, "2026 week 10", True)
        return None

    def teams(self, team_ids: Sequence[int]) -> Mapping[int, str]:
        self.context.disclose(Statement('SELECT "TEAM_ID", "SCHOOL" FROM "TEAMS"', rows=2))
        return {i: self.schools[i] for i in team_ids if i in self.schools}

    def whatif_games(self, game_ids: Sequence[int]) -> Mapping[int, GameInfo]:
        self.context.disclose(Statement('SELECT * FROM "WHATIF_GAMES"', rows=1))
        game = GameInfo(GAME, "Ashford", "Brookvale", 12)
        return {GAME: game} if GAME in game_ids else {}


class FailingLookups(DisclosingLookups):
    def teams(self, team_ids: Sequence[int]) -> Mapping[int, str]:
        raise RuntimeError("the chat connection is gone")


def _nothing() -> None:
    return None


class QueryTool:
    """A data tool: one disclosed statement and a one-row result. `block` makes it wait until the
    turn is cancelled (the abort hook releases it), like a slow statement."""

    def __init__(self, *, block: bool = False, fail: bool = False, stuck: bool = False) -> None:
        self.ran: list[ToolInput] = []
        self.block = block or stuck
        self.stuck = stuck
        self.fail = fail
        self.waiting = threading.Event()
        self.aborted = threading.Event()

    def __call__(self, tool_input: ToolInput, context: ToolContext) -> ToolOutcome:
        self.ran.append(tool_input)
        if self.fail:
            raise RuntimeError("driver detail: password=hunter2")
        sql = str(getattr(tool_input, "sql", f'SELECT 1 FROM "{context.tool.upper()}"'))
        if self.block:
            forget = _nothing if self.stuck else context.cancel.on_cancel(self.aborted.set)
            self.waiting.set()
            try:
                self.aborted.wait(10)
            finally:
                forget()
            sql_id = context.disclose(Statement(sql, ms=900, error="statement aborted"))
            return ToolOutcome(tools.error_result("statement aborted", sql_id=sql_id))
        purpose = getattr(tool_input, "purpose", None)
        sql_id = context.disclose(Statement(sql, rows=1, ms=3, purpose=purpose, first_rows=((1,),)))
        return ToolOutcome(tools.query_result(sql_id, ["N"], [[1]], 3))


class SetWhatIfTool:
    """An app-state tool: a disclosed lookup, then the change."""

    def __init__(self) -> None:
        self.states: list[appstate.AppState] = []

    def __call__(self, tool_input: ToolInput, context: ToolContext) -> ToolOutcome:
        assert isinstance(tool_input, tools.SetWhatIfInput)
        self.states.append(context.state)
        context.disclose(Statement('SELECT * FROM "WHATIF_GAMES"', rows=1))
        chosen = tuple(
            Condition.from_tool(c.game_id, c.home_team_wins) for c in tool_input.conditions
        )
        after = replace(context.state, conditions=chosen, page="odds")
        change = AppChange(
            tool=context.tool,
            input=tool_input.model_dump(mode="json"),
            actions=(
                {"type": "clearConditions"},
                *(
                    {"type": "setCondition", "game_id": c.game_id, "winner": c.winner}
                    for c in chosen
                ),
                {"type": "setPage", "page": "odds"},
            ),
            before=context.state,
            after=after,
            summary="Brookvale wins at Ashford",
        )
        return ToolOutcome(tools.app_change_result(change.summary, after.as_json()), change)


def tool_table(**overrides: Any) -> dict[str, Any]:
    table: dict[str, Any] = {
        name: QueryTool() for name in ("run_sql", "describe_view", "whatif_odds", "rooting_guide")
    }
    table["set_whatif"] = SetWhatIfTool()
    table.update(overrides)
    return table


class HeadersWait:
    """A model whose first call waits for its response headers and does not watch the cancel token
    (the SDK cannot close a stream it has not opened yet). Later calls go to `inner`."""

    def __init__(self, inner: ChatModel, late: ModelResponse | None = None) -> None:
        self.inner = inner
        self.late = late
        self.waiting = threading.Event()
        self.release = threading.Event()
        self.returned = threading.Event()
        self._first = True

    def respond(
        self, request: ModelRequest, on_text: Callable[[str], None], cancel: CancelToken
    ) -> ModelResponse:
        if self._first:
            self._first = False
            self.waiting.set()
            self.release.wait(10)
            try:
                if self.late is None:
                    raise ConnectionError("gave up")
                return self.late
            finally:
                self.returned.set()
        return self.inner.respond(request, on_text, cancel)
