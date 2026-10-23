"""`vs/host/sqlobjects.py` `deploy` over a fake connection: the statement sequence, the rollbacks,
the orphan drop, the compile pass and the read-back. The behaviour on the engine is covered by
`tests/it/test_model_deploy_it.py` (fixture directories) and `tests/it/test_model_harness_it.py`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest

from loader.runlock import HeldLock
from vs.host.deploy import DeployError
from vs.host.namespace import Namespace
from vs.host.sqlobjects import SqlObject, deploy, load, render

SCHEMA = "TEST_CFB_MODEL"
NAMESPACE = Namespace("TEST_")
FIXTURES = Path(__file__).parent / "fixtures" / "sqlobjects" / "valid"


class FakeQueryError(Exception):
    """Like `pyexasol.ExaQueryError`: the database text is in `message`."""

    def __init__(self, message: str) -> None:
        super().__init__(f"query failed: {message}")
        self.message = message


class Result:
    def __init__(self, rows: Sequence[tuple[Any, ...]] = ()) -> None:
        self._rows = list(rows)

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._rows)


class FakeConnection:
    """Records every statement; answers the two catalog reads from `catalog` and `columns`.

    `catalog` rows are `(name, type, comment)` of `EXA_ALL_OBJECTS`, `columns` rows are
    `(table, name, comment, position, status)` of `EXA_ALL_COLUMNS`. `fail` maps a statement
    prefix to the error text the database would raise.
    """

    def __init__(self) -> None:
        self.statements: list[str] = []
        self.catalog: list[tuple[str, str, str | None]] = []
        self.columns: list[tuple[str, str, str | None, int, str | None]] = []
        self.fail: dict[str, str] = {}
        self.autocommit_calls: list[bool] = []
        self.attr: dict[str, Any] = {"autocommit": True}
        self.on_read: Callable[[FakeConnection], None] | None = None

    def set_autocommit(self, value: bool) -> None:
        self.autocommit_calls.append(value)
        self.attr["autocommit"] = value

    def execute(self, query: str) -> Result:
        self.statements.append(query)
        for prefix, message in self.fail.items():
            if query.startswith(prefix):
                raise FakeQueryError(message)
        if "FROM EXA_ALL_OBJECTS" in query:
            if self.on_read is not None:
                self.on_read(self)
            return Result(self.catalog)
        if "FROM EXA_ALL_COLUMNS" in query:
            return Result(self.columns)
        return Result()


def objects_of(directory: Path) -> list[SqlObject]:
    return render(load(directory), NAMESPACE)


def expected_catalog(
    objects: Sequence[SqlObject],
) -> tuple[list[tuple[str, str, str | None]], list[tuple[str, str, str | None, int, str | None]]]:
    """What the database holds after the objects were created: comments and columns as declared."""
    catalog = [(o.name, o.kind, o.comment) for o in objects]
    columns: list[tuple[str, str, str | None, int, str | None]] = [
        (o.name, column.name, column.comment, position, None)
        for o in objects
        if o.kind in ("TABLE", "VIEW")
        for position, column in enumerate(o.columns, start=1)
    ]
    return catalog, columns


def deployed(objects: Sequence[SqlObject]) -> tuple[FakeConnection, HeldLock]:
    """A fake that already holds what a deploy of `objects` leaves (so this is a second deploy)."""
    connection = FakeConnection()
    connection.catalog, connection.columns = expected_catalog(objects)
    return connection, HeldLock(7, "DEPLOY")


def run(
    connection: FakeConnection,
    objects: Sequence[SqlObject],
    lock: HeldLock,
    extra: Sequence[str] = (),
) -> Any:
    return deploy(connection, SCHEMA, objects, extra_statements=extra, lock=lock)


def test_the_transaction_runs_in_file_order_then_compiles_every_view_then_commits() -> None:
    objects = objects_of(FIXTURES)
    connection, lock = deployed(objects)
    report = run(
        connection, objects, lock, extra=["CREATE OR REPLACE LUA SCRIPT X.Y() AS return 1"]
    )
    statements = connection.statements
    assert statements[0] == f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"
    created = [s for s in statements if s.startswith(("CREATE OR REPLACE", "INSERT", "COMMENT ON"))]
    assert [s.split()[0] for s in created] == [
        "CREATE",
        "INSERT",
        "CREATE",
        "COMMENT",
        "CREATE",
        "CREATE",
        "CREATE",
    ]
    assert created[-1].startswith("CREATE OR REPLACE LUA SCRIPT X.Y")
    compiles = [s for s in statements if s.startswith("SELECT 1 FROM")]
    assert compiles == [
        f'SELECT 1 FROM "{SCHEMA}"."GAME" WHERE 1 = 0',
        f'SELECT 1 FROM "{SCHEMA}"."SEASON" WHERE 1 = 0',
    ]
    assert statements[-1] == "COMMIT"
    assert statements.index(compiles[0]) > statements.index(created[-1])
    assert report.created == ("ERA", "GREAT_CIRCLE_KM", "GAME", "SEASON")


def test_the_read_back_comes_after_the_compile_pass() -> None:
    objects = objects_of(FIXTURES)
    connection, lock = deployed(objects)
    run(connection, objects, lock)
    last_compile = max(
        i for i, s in enumerate(connection.statements) if s.startswith("SELECT 1 FROM")
    )
    reads = [i for i, s in enumerate(connection.statements) if "FROM EXA_ALL_COLUMNS" in s]
    assert reads
    assert min(reads) > last_compile


def test_autocommit_is_off_during_the_transaction_and_back_on_after_it() -> None:
    objects = objects_of(FIXTURES)
    connection, lock = deployed(objects)
    run(connection, objects, lock)
    assert connection.autocommit_calls == [False, True]


def test_a_connection_that_is_not_in_autocommit_mode_is_refused_before_any_statement() -> None:
    objects = objects_of(FIXTURES)
    connection, lock = deployed(objects)
    connection.attr["autocommit"] = False
    with pytest.raises(DeployError, match="autocommit"):
        run(connection, objects, lock)
    assert connection.statements == []


def test_a_failing_statement_rolls_back_and_names_the_file_object_and_database_error() -> None:
    objects = objects_of(FIXTURES)
    connection, lock = deployed(objects)
    connection.fail["CREATE OR REPLACE FORCE VIEW TEST_CFB_MODEL.SEASON"] = (
        "syntax error, unexpected IDENTIFIER_LIST_ caught in script X at line 3"
    )
    with pytest.raises(DeployError) as raised:
        run(connection, objects, lock)
    message = str(raised.value)
    assert "110_season.sql" in message
    assert "SEASON" in message
    assert "syntax error, unexpected IDENTIFIER_LIST_" in message
    assert "caught in script" not in message
    assert connection.statements[-1] == "ROLLBACK"
    assert "COMMIT" not in connection.statements
    assert connection.autocommit_calls == [False, True]


def test_a_view_that_does_not_compile_fails_the_deploy_and_rolls_back() -> None:
    objects = objects_of(FIXTURES)
    connection, lock = deployed(objects)
    connection.fail[f'SELECT 1 FROM "{SCHEMA}"."SEASON"'] = (
        "view SEASON is invalid: object X not found"
    )
    with pytest.raises(DeployError) as raised:
        run(connection, objects, lock)
    assert "compile" in str(raised.value)
    assert "SEASON" in str(raised.value)
    assert "object X not found" in str(raised.value)
    assert connection.statements[-1] == "ROLLBACK"
    assert "COMMIT" not in connection.statements


def test_a_missing_comment_in_the_database_fails_the_deploy() -> None:
    objects = objects_of(FIXTURES)
    connection, lock = deployed(objects)
    connection.catalog = [
        (name, kind, None if name == "GAME" else comment)
        for name, kind, comment in connection.catalog
    ]
    with pytest.raises(DeployError) as raised:
        run(connection, objects, lock)
    assert "GAME" in str(raised.value)
    assert "comment" in str(raised.value)
    assert connection.statements[-1] == "ROLLBACK"


def test_every_read_back_problem_is_listed_not_only_the_first() -> None:
    objects = objects_of(FIXTURES)
    connection, lock = deployed(objects)
    connection.columns = [
        (table, name, "changed" if (table, name) == ("GAME", "SEASON") else comment, pos, status)
        for table, name, comment, pos, status in connection.columns
        if (table, name) != ("SEASON", "GAME_KM")
    ]
    with pytest.raises(DeployError) as raised:
        run(connection, objects, lock)
    message = str(raised.value)
    assert "GAME.SEASON" in message
    assert "SEASON.GAME_KM" in message


def test_an_outdated_view_column_fails_the_deploy() -> None:
    objects = objects_of(FIXTURES)
    connection, lock = deployed(objects)
    connection.columns = [
        (table, name, comment, pos, "OUTDATED" if table == "GAME" else status)
        for table, name, comment, pos, status in connection.columns
    ]
    with pytest.raises(DeployError, match="OUTDATED"):
        run(connection, objects, lock)


def test_an_object_that_is_missing_after_the_statements_fails_the_deploy() -> None:
    objects = objects_of(FIXTURES)
    connection, lock = deployed(objects)
    connection.catalog = [row for row in connection.catalog if row[0] != "ERA"]
    with pytest.raises(DeployError, match=r"ERA.*does not exist"):
        run(connection, objects, lock)


def test_objects_that_no_file_defines_are_dropped_inside_the_transaction() -> None:
    objects = objects_of(FIXTURES)
    connection, lock = deployed(objects)
    connection.catalog = [
        *connection.catalog,
        ("OLD_VIEW", "VIEW", "One row per old"),
        ("OLD_TABLE", "TABLE", "One row per old"),
        ("OLD_FN", "FUNCTION", "old"),
        ("OLD_SCRIPT", "SCRIPT", "old"),
    ]
    report = run(connection, objects, lock)
    drops = [s for s in connection.statements if s.startswith("DROP")]
    assert sorted(drops) == sorted(
        [
            f'DROP VIEW "{SCHEMA}"."OLD_VIEW"',
            f'DROP TABLE "{SCHEMA}"."OLD_TABLE"',
            f'DROP FUNCTION "{SCHEMA}"."OLD_FN"',
            f'DROP SCRIPT "{SCHEMA}"."OLD_SCRIPT"',
        ]
    )
    commit = connection.statements.index("COMMIT")
    assert all(connection.statements.index(d) < commit for d in drops)
    first_compile = min(
        i for i, s in enumerate(connection.statements) if s.startswith("SELECT 1 FROM")
    )
    assert all(connection.statements.index(d) < first_compile for d in drops)
    assert set(report.dropped) == {
        "VIEW OLD_VIEW",
        "TABLE OLD_TABLE",
        "FUNCTION OLD_FN",
        "SCRIPT OLD_SCRIPT",
    }
    assert any("OLD_VIEW" in line for line in report.lines)


def test_only_the_target_schema_is_asked_for_orphans() -> None:
    objects = objects_of(FIXTURES)
    connection, lock = deployed(objects)
    run(connection, objects, lock)
    reads = [s for s in connection.statements if "FROM EXA_ALL_OBJECTS" in s]
    assert reads
    assert all(f"ROOT_NAME = '{SCHEMA}'" in s for s in reads)


def test_an_object_whose_kind_changed_is_dropped_before_it_is_created_again() -> None:
    objects = objects_of(FIXTURES)
    connection, lock = deployed(objects)
    existing = [("GAME", "TABLE", "One row per game"), ("ERA", "TABLE", None)]
    before_catalog = list(connection.catalog)

    def first_read_sees_old_state(fake: FakeConnection) -> None:
        reads = [s for s in fake.statements if "FROM EXA_ALL_OBJECTS" in s]
        fake.catalog = existing if len(reads) == 1 else before_catalog

    connection.on_read = first_read_sees_old_state
    report = run(connection, objects, lock)
    drop = f'DROP TABLE "{SCHEMA}"."GAME"'
    create = next(
        s
        for s in connection.statements
        if s.startswith("CREATE OR REPLACE FORCE VIEW TEST_CFB_MODEL.GAME")
    )
    assert connection.statements.index(drop) < connection.statements.index(create)
    assert f'DROP TABLE "{SCHEMA}"."ERA"' not in connection.statements
    assert report.retyped == ("GAME (was TABLE)",)


def test_the_report_counts_objects_comments_and_names_what_it_did() -> None:
    objects = objects_of(FIXTURES)
    connection, lock = deployed(objects)
    report = run(connection, objects, lock)
    assert (report.tables, report.views, report.functions, report.scripts) == (1, 2, 1, 0)
    assert report.comments_found == report.comments_expected
    assert report.comments_expected == 4 + (3 + 2 + 2)
    text = "\n".join(report.lines)
    assert "1 tables, 2 views, 1 functions" in text
    assert "100 %" in text
    assert "dropped: none" in text


def test_the_lock_detail_summarizes_the_deploy() -> None:
    objects = objects_of(FIXTURES)
    connection, lock = deployed(objects)
    run(connection, objects, lock)
    assert lock.detail == f"{SCHEMA}: 1 tables, 2 views, 1 functions; dropped 0; comments 100 %"


def test_extra_statements_run_after_the_objects_and_roll_back_with_them() -> None:
    objects = objects_of(FIXTURES)
    connection, lock = deployed(objects)
    connection.fail["CREATE OR REPLACE LUA"] = "script rejected"
    with pytest.raises(DeployError, match=r"extra statement 1.*script rejected"):
        run(connection, objects, lock, extra=["CREATE OR REPLACE LUA SCRIPT X.Y() AS return 1"])
    assert connection.statements[-1] == "ROLLBACK"


def test_an_object_for_another_schema_is_refused_before_any_statement() -> None:
    objects = render(load(FIXTURES), Namespace(""))
    connection, lock = deployed(objects)
    with pytest.raises(DeployError, match="CFB_MODEL"):
        run(connection, objects, lock)
    assert connection.statements == []


def test_a_file_without_an_object_is_refused_before_any_statement(tmp_path: Path) -> None:
    (tmp_path / "100_x.sql").write_text("SELECT 1;\n")
    objects = objects_of(tmp_path)
    connection, lock = deployed([])
    with pytest.raises(DeployError, match=r"100_x\.sql"):
        run(connection, objects, lock)
    assert connection.statements == []


def test_an_interrupt_rolls_back_too() -> None:
    objects = objects_of(FIXTURES)
    connection, lock = deployed(objects)

    def interrupt(fake: FakeConnection) -> None:
        raise KeyboardInterrupt

    connection.on_read = interrupt
    with pytest.raises(KeyboardInterrupt):
        run(connection, objects, lock)
    assert connection.statements[-1] == "ROLLBACK"
    assert connection.autocommit_calls == [False, True]
