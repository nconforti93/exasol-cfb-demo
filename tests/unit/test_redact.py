"""`vs/host/redact.py` and the places that must use it: errors never carry a password."""

from collections.abc import Mapping
from typing import Any, Never

import pytest

from scripts import grants
from vs.host import redact
from vs.host.target import Target

SECRET = "s3cret-Pa55_word-xyz"


class QueryError(Exception):
    """Stands in for `pyexasol.ExaQueryError`, whose text includes the query."""

    def __init__(self, query: str) -> None:
        super().__init__(f"syntax error\nSQL: {query}")
        self.message = "syntax error"
        self.query = query


def test_every_occurrence_of_a_secret_is_replaced() -> None:
    text = f"a {SECRET} b {SECRET}"
    assert redact.redact(text, [SECRET]) == f"a {redact.REDACTION} b {redact.REDACTION}"


def test_quote_escaped_forms_are_replaced_too() -> None:
    secret = "pa\"ss'word"
    text = "CREATE USER x IDENTIFIED BY \"pa\"\"ss'word\" / 'pa\"ss''word'"
    assert secret not in redact.redact(text, [secret])
    assert "pa" not in redact.redact(text, [secret]).replace("pa" + redact.REDACTION, "")


def test_empty_and_missing_secrets_are_ignored() -> None:
    assert redact.redact("plain text", ["", None]) == "plain text"


def test_longer_secrets_are_replaced_before_their_prefixes() -> None:
    assert redact.redact("abcdef", ["abc", "abcdef"]) == redact.REDACTION


def test_scrub_returns_the_original_error_when_no_secret_is_present() -> None:
    error = ValueError("harmless")
    assert redact.scrub(error, [SECRET]) is error


def test_scrub_replaces_an_error_that_carries_a_secret() -> None:
    scrubbed = redact.scrub(QueryError(f'CREATE USER X IDENTIFIED BY "{SECRET}"'), [SECRET])
    assert SECRET not in str(scrubbed)
    assert "QueryError" in str(scrubbed)
    assert scrubbed.__cause__ is None
    assert scrubbed.__suppress_context__


def test_scrub_looks_through_the_cause_chain() -> None:
    try:
        try:
            raise QueryError(SECRET)
        except QueryError as inner:
            raise RuntimeError("step failed") from inner
    except RuntimeError as caught:
        outer = caught
        scrubbed = redact.scrub(outer, [SECRET])
    assert scrubbed is not outer
    assert SECRET not in str(scrubbed)


class RecordingConnection:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[str, Mapping[str, Any] | None]] = []

    def execute(self, sql: str, params: Mapping[str, Any] | None = None) -> None:
        self.calls.append((sql, params))
        if self.error is not None:
            raise self.error


def test_a_failing_create_user_is_reraised_without_the_password() -> None:
    step = grants.create_user_step("CFB_TEST_OWNER", SECRET)
    query = step.sql.replace("{password!q}", f'"{SECRET}"')
    database = grants.PyexasolDatabase(RecordingConnection(QueryError(query)))
    with pytest.raises(grants.GrantsError) as error:
        database.execute(step.sql, step.params)
    assert SECRET not in str(error.value)
    assert SECRET not in repr(error.value)
    assert "CREATE USER CFB_TEST_OWNER" in str(error.value)
    assert error.value.__suppress_context__


def test_a_failing_statement_without_parameters_propagates_unchanged() -> None:
    database = grants.PyexasolDatabase(RecordingConnection(QueryError("GRANT x")))
    with pytest.raises(QueryError, match="syntax error"):
        database.execute("GRANT x")


def test_a_successful_statement_passes_the_parameters_through() -> None:
    connection = RecordingConnection()
    grants.PyexasolDatabase(connection).execute("SELECT {a}", {"a": SECRET})
    assert connection.calls == [("SELECT {a}", {"a": SECRET})]


def test_grants_main_never_prints_the_password_of_a_failing_create_user(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    query = f'CREATE USER CFB_TEST_OWNER IDENTIFIED BY "{SECRET}"'

    class Connection(RecordingConnection):
        def execute(self, sql: str, params: Mapping[str, Any] | None = None) -> Never:
            raise QueryError(query)

        def close(self) -> None:
            return None

    monkeypatch.setattr("pyexasol.connect", lambda **options: Connection())
    monkeypatch.setattr(Target, "password", lambda self: "sys-pw")
    monkeypatch.setenv("CFB_TEST_OWNER_PASSWORD", SECRET)
    monkeypatch.setenv("EXASOL_DSN", "127.0.0.1:1")
    code = grants.main(["--prefix", "TEST_"])
    captured = capsys.readouterr()
    assert code == grants.EXIT_ERROR
    assert SECRET not in captured.out + captured.err
