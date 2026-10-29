"""The curated console: four fixed statements over the virtual schema, explained and run as the
console user (an ordinary reader). The owner path only reads the quota and writes the fetch log."""

from __future__ import annotations

import re
import secrets
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pyexasol import ExaQueryError

from app.backend import console_catalog, db, explain, live, models, spend
from app.backend.explain import Explained
from app.backend.sessions import ConsoleBusy, QueryLog, SessionStore
from app.backend.settings import Settings
from vs.host import fetchlog, ledger

Row = dict[str, Any]
PAGE = "console"
BUDGET_MARKER = "MAX_CALLS_PER_QUERY"
# "VM error: F-UDF-CL-LIB-1126: F-UDF-CL-SL-PYTHON-1004: BudgetError: ..." -> "BudgetError: ..."
_UDF_PREFIX = re.compile(r"^(?:VM error:\s*)?(?:F-UDF-[A-Z0-9-]+:\s*)*")


class _Statement:
    """A `db.Result` with the two methods `LiveExecutor` reads from a driver statement."""

    def __init__(self, result: db.Result) -> None:
        self._result = result

    def column_names(self) -> list[str]:
        return list(self._result.columns)

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._result.rows


class _ConsoleConnection:
    """The console session as a connection; every statement goes to the browser's query log."""

    def __init__(self, session: db.DbSession, log: QueryLog) -> None:
        self._session = session
        self._log = log

    def execute(self, sql: str) -> _Statement:
        return _Statement(self._session.execute(sql, log=self._log, page=PAGE))


class UnknownQuery(LookupError):
    """The id names none of the four curated queries (404)."""


def _sql(query_id: str, vs_schema: str) -> str:
    try:
        query = console_catalog.get(query_id)
    except KeyError:
        raise UnknownQuery(query_id) from None
    return console_catalog.sql(query, vs_schema)


def _error(code: str, message: str, status: int, **extra: Any) -> JSONResponse:
    return JSONResponse({"error": code, "message": message, **extra}, status_code=status)


def _refusal_text(error: ExaQueryError) -> str:
    message = explain.refusal_message(getattr(error, "message", error))
    return _UDF_PREFIX.sub("", message) or message


def _plan_refusal(error: ExaQueryError) -> JSONResponse:
    message = _refusal_text(error)
    if BUDGET_MARKER in message:
        return _error("max_calls_exceeded", message, 409)
    return _error("condition_error", message, 400)


def _manifest(manifest: fetchlog.Manifest) -> Row:
    described = explain.describe_manifest(manifest)
    described.pop("requests")
    return described


def _iso(value: object) -> str | None:
    # REQ_AT arrives from the result as text; the fetch-log row keeps whatever it was given.
    if value is None:
        return None
    return value.isoformat() if isinstance(value, datetime) else str(value)


def _request_lines(rows: Sequence[fetchlog.FetchLogRow]) -> list[Row]:
    return [
        {
            "slice": row.slice_key,
            "source": row.source,
            "status": row.http_status,
            "attempts": row.attempts,
            "ms": row.ms,
            "rows": row.rows_returned,
            "requested_at": _iso(row.requested_at),
        }
        for row in rows
    ]


class ConsoleService:
    def __init__(self, request: Request, settings: Settings, store: SessionStore) -> None:
        self.request = request
        self.settings = settings
        self.session = store.session(request.state.session_id)

    def listing(self) -> Row:
        self._require()
        vs = self.settings.namespace.vs_name
        return {
            "principal": db.CONSOLE_PRINCIPAL,
            "user": self.settings.console_user,
            "help": console_catalog.HELP_TEXT,
            "queries": [
                {
                    "id": query.id,
                    "title": query.title,
                    "summary": query.summary,
                    "sql": console_catalog.sql(query, vs),
                }
                for query in console_catalog.CONSOLE_QUERIES
            ],
        }

    def explain(self, query_id: str) -> Row:
        """`EXPLAIN VIRTUAL` as the console user: 0 calls. A refusal at plan time is the answer."""
        self._require()
        sql = _sql(query_id, self.settings.namespace.vs_name)
        statement = f"EXPLAIN VIRTUAL {sql}"
        with self.request.app.state.console.session() as opened:
            try:
                result = opened.execute(statement, log=self.session.log, page=PAGE)
            except ExaQueryError as error:
                return self._explained(statement, None, _refusal_text(error))
        explained = explain.parse_explain(result.columns, result.rows)
        return self._explained(statement, explained, None)

    def run(self, query_id: str, max_calls: int, book: ledger.Ledger) -> Row:
        self._require()
        if not self.request.app.state.reconciled:
            raise live.LiveRefused("reconcile_pending", "ledger reconciliation has not finished")
        sql = _sql(query_id, self.settings.namespace.vs_name)
        action_id = secrets.token_hex(12)
        with self.session.console_run(), self.request.app.state.console.session() as opened:
            result = self._executor(book).run_statement(
                sql,
                max_calls=max_calls,
                action_id=action_id,
                label=f"console {query_id}",
                context="APP_CONSOLE",
                query=_ConsoleConnection(opened, self.session.log),
            )
        return {
            "id": query_id,
            "action_id": action_id,
            "columns": list(result.columns),
            "rows": [list(row) for row in result.rows],
            "row_count": len(result.rows),
            "calls_spent": result.calls_charged,
            "source": result.plan.source,
            "requests": _request_lines(result.logged),
            "fetch_log_ok": result.fetch_log_ok,
        }

    def _require(self) -> None:
        availability = self.request.app.state.console.availability
        if not availability.enabled:
            raise db.ConsoleDisabled(availability.reason)

    def _explained(self, statement: str, explained: Explained | None, refused: str | None) -> Row:
        entry = next(
            (item for item in reversed(self.session.log.entries()) if item.sql == statement), None
        )
        manifests = () if explained is None else explained.manifests
        plan = spend.plan_from_manifests(manifests)
        no_virtual = explained is not None and explained.no_virtual_table
        return {
            "entry_id": "" if entry is None else str(entry.id),
            "principal": db.CONSOLE_PRINCIPAL,
            "no_virtual_table": no_virtual,
            "message": explain.NO_VIRTUAL_TABLE_TEXT if no_virtual else None,
            "manifests": [_manifest(item) for item in manifests],
            "spend": {
                "source": plan.source,
                "live_calls": plan.live_calls,
                "fetch_mode": plan.fetch_mode,
            },
            "refused": refused,
        }

    def _executor(self, book: ledger.Ledger) -> live.LiveExecutor:
        state = self.request.app.state
        operator = state.operator

        def busy() -> str | None:
            running = operator.running_runs(self.settings.namespace)
            return None if not running else f"{running[0][1]} run {running[0][0]}"

        return live.LiveExecutor(
            self.settings.namespace,
            operator,
            book,
            target=self.settings.target,
            ledger_plan=self.settings.ledger_plan,
            gate=getattr(state, "live_gate", None),
            busy=busy,
            reconcile_pending=lambda: not state.reconciled,
            on_record_failure=lambda: setattr(state, "reconciled", False),
        )


def make_router(settings: Settings, store: SessionStore, book: ledger.Ledger) -> APIRouter:
    router = APIRouter()

    def guarded(action: Any) -> Any:
        try:
            return action()
        except UnknownQuery:
            return _error("not_found", "no such console query", 404)
        except db.ConsoleDisabled as error:
            return _error("console_disabled", str(error), 403)
        except ConsoleBusy as error:
            return _error("live_busy", str(error), 409)
        except ValueError as error:
            return _error("invalid_argument", str(error), 400)
        except ExaQueryError as error:
            # Raised by the plan before anything is sent, e.g. the per-query budget.
            return _plan_refusal(error)
        except live.LiveRefused as error:
            return _error(error.code, str(error), error.status)
        except live.LiveFailed as error:
            return _error("cfbd_failed", str(error), 502, calls_charged=error.calls_charged)

    @router.get("/api/console", response_model=models.ConsoleResponse)
    def listing(request: Request) -> Any:
        return guarded(lambda: ConsoleService(request, settings, store).listing())

    @router.post("/api/console/{query_id}/explain", response_model=models.ExplainResponse)
    def explain_query(request: Request, query_id: str) -> Any:
        return guarded(lambda: ConsoleService(request, settings, store).explain(query_id))

    @router.post("/api/console/{query_id}/run", response_model=models.ConsoleRunResponse)
    def run(request: Request, query_id: str, body: dict[str, Any]) -> Any:
        def action() -> Row:
            max_calls = body.get("max_calls")
            if type(max_calls) is not int or max_calls < 0 or set(body) != {"max_calls"}:
                raise ValueError("max_calls must be a non-negative integer")
            return ConsoleService(request, settings, store).run(query_id, max_calls, book)

        return guarded(action)

    return router
