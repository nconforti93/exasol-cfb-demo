"""The loader's run lock for host processes: `run_lock(connection, namespace, ...)`.

The run lock is the `RUNNING` row of `<meta>.LOAD_RUN` (`loader/run-logs`). Lua scripts take it
with `acquire_lock` of `loader/lua/loadlib.lua`; a host process takes it through the script
`<infra>.RUN_LOCK` (`loader/lua/run_lock.lua`, installed by `make deploy`), which runs the same
library in the host's own session. One implementation of the stale rules serves both.

    with run_lock(connection, namespace, "DEPLOY", "make sim") as lock:
        ...                       # the work, in the same session; lock.run_id is the LOAD_RUN row
        lock.detail = "..."       # optional: the DETAIL of the DONE row

Kinds: `DEPLOY` (`make deploy`, Phase 3's model deploy, Phase 4's `make sim` and
`make calibrate`), `SNAPSHOT` and `RESTORE`. They follow the host rule: the row is stale only when
its session is gone or is the caller's own session, never because the session is idle between
statements.

Rules for callers (the row belongs to the session of `connection`):

- Work in the session the lock was taken in, and keep it open until the block ends. A process
  that dies frees the lock with its session.
- Take the lock before your own transaction starts: acquiring commits the session's open
  transaction, so a connection with autocommit off is refused before anything is sent.
- While the lock is held, run no locking script (`BACKFILL`, `REFRESH_WEEK`, Phase 4's
  `SIMULATE`) and no second `run_lock` in that session: the own-session rule would abandon the
  row. A second `run_lock` on the same connection raises at once.
- At the end of the block the row ends `DONE` with `lock.detail`. When the block raises (also on
  Ctrl-C), the session is rolled back first, so the release cannot commit half of the work; then
  the row ends `FAILED` with the error's first line, and the error propagates.
- A refused lock raises `RunLockRefused`, naming the running run; nothing was written and the
  block does not run.
- Only the session that took the lock releases it (`RUN_LOCK` ends a row of the calling session
  only); other sessions free a row only through the stale rules.

`lock_installed(connection, namespace)` says whether the namespace has the `RUN_LOCK` script yet:
the first `make deploy` of a namespace installs it, so that deploy runs without the lock.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Protocol

from vs.cfbd_vs.sqlrender import qualified_name, string_literal
from vs.host.namespace import Namespace

__all__ = [
    "HOST_KINDS",
    "HeldLock",
    "RunLockError",
    "RunLockRefused",
    "first_line",
    "lock_installed",
    "run_lock",
]

HOST_KINDS = ("DEPLOY", "RESTORE", "SNAPSHOT")
SCRIPT_NAME = "RUN_LOCK"
DONE = "DONE"
FAILED = "FAILED"
_CAUGHT_IN_SCRIPT = " caught in script "

_held: set[int] = set()


class RunLockError(RuntimeError):
    """The lock cannot be taken or released; the message says why and what to do."""


class RunLockRefused(RunLockError):
    """Another run holds the lock; the message names it. Nothing was written."""


class Connection(Protocol):
    """The part of a pyexasol connection used here."""

    def execute(self, query: str) -> Any: ...


@dataclass
class HeldLock:
    """The lock while a block holds it: the `LOAD_RUN` row and the detail of its `DONE` end."""

    run_id: int
    kind: str
    detail: str | None = None


def first_line(error: BaseException | str) -> str:
    """The database message of an error without the script position suffix, or its type name."""
    text = str(getattr(error, "message", error)).strip()
    line = text.splitlines()[0] if text else ""
    line = line.split(_CAUGHT_IN_SCRIPT, 1)[0].strip()
    return line or type(error).__name__


@contextmanager
def run_lock(
    connection: Connection, namespace: Namespace, kind: str, args: str
) -> Iterator[HeldLock]:
    """Hold the run lock as `kind` for the block; see the module documentation for the rules."""
    if kind not in HOST_KINDS:
        raise RunLockError(
            f"run_lock takes the host kinds {', '.join(HOST_KINDS)}, not {kind!r}; script runs "
            "take the lock in their own Lua (acquire_lock)"
        )
    if _autocommit_is_off(connection):
        raise RunLockError(
            "run_lock needs autocommit on when it is taken: acquiring commits the session's open "
            "transaction. Take the lock first, then start the transaction"
        )
    if id(connection) in _held:
        raise RunLockError(
            "this connection already holds the run lock; a second lock in the same session "
            "would abandon the first (own-session rule)"
        )
    _require_script(connection, namespace)
    lock = HeldLock(_acquire(connection, namespace, kind, args), kind)
    _held.add(id(connection))
    try:
        yield lock
    except BaseException as error:
        _held.discard(id(connection))
        _end_failed(connection, namespace, lock, error)
        raise
    _held.discard(id(connection))
    if not _release(connection, namespace, lock.run_id, DONE, lock.detail):
        raise RunLockError(
            f"{kind} run {lock.run_id} was no longer RUNNING when it ended: another session "
            f"changed its row in {namespace.meta_schema}.LOAD_RUN, so the work may have "
            "overlapped another run"
        )


def _autocommit_is_off(connection: Connection) -> bool:
    attributes = getattr(connection, "attr", None)
    return isinstance(attributes, dict) and attributes.get("autocommit") is False


def _script(namespace: Namespace) -> str:
    return qualified_name(namespace.infra_schema, SCRIPT_NAME)


def lock_installed(connection: Connection, namespace: Namespace) -> bool:
    """Whether `<infra>.RUN_LOCK` exists, so a host process can take the lock."""
    rows = connection.execute(
        "SELECT COUNT(*) FROM EXA_ALL_SCRIPTS "
        f"WHERE SCRIPT_SCHEMA = {string_literal(namespace.infra_schema)} "
        f"AND SCRIPT_NAME = {string_literal(SCRIPT_NAME)}"
    ).fetchall()
    return int(rows[0][0]) > 0


def _require_script(connection: Connection, namespace: Namespace) -> None:
    if not lock_installed(connection, namespace):
        raise RunLockError(
            f"the Lua script {_script(namespace)} is not installed: run `make deploy` "
            "(the loader deploy installs it)"
        )


def _acquire(connection: Connection, namespace: Namespace, kind: str, args: str) -> int:
    statement = (
        f"EXECUTE SCRIPT {_script(namespace)}('acquire', {string_literal(kind)}, NULL, "
        f"{_text_literal(args)})"
    )
    try:
        rows = connection.execute(statement).fetchall()
    except Exception as error:
        message = first_line(error)
        if message.startswith(f"{kind} refused:"):
            raise RunLockRefused(message) from None
        raise RunLockError(f"{kind} could not take the run lock: {message}") from None
    return int(rows[0][0])


def _release(
    connection: Connection, namespace: Namespace, run_id: int, status: str, detail: str | None
) -> bool:
    statement = (
        f"EXECUTE SCRIPT {_script(namespace)}('release', {string_literal(status)}, {int(run_id)}, "
        f"{_text_literal(detail)})"
    )
    try:
        rows = connection.execute(statement).fetchall()
    except Exception as error:
        raise RunLockError(
            f"run {run_id} could not be ended {status}: {first_line(error)}"
        ) from None
    return int(rows[0][0]) == 1


def _end_failed(
    connection: Connection, namespace: Namespace, lock: HeldLock, error: BaseException
) -> None:
    try:
        connection.execute("ROLLBACK")
        _release(connection, namespace, lock.run_id, FAILED, first_line(error))
    except Exception as problem:
        error.add_note(
            f"the run lock: {lock.kind} run {lock.run_id} could not be marked FAILED "
            f"({first_line(problem)}); its row is freed when this session ends"
        )


def _text_literal(text: str | None) -> str:
    if text is None:
        return "NULL"
    return string_literal(" ".join(text.split()))
