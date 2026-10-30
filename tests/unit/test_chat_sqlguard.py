"""Unit tests of `app/backend/chat/sqlguard.py`: the lexer, the static checks of `run_sql`
(`chat/data-tools`) and the identifiers the rehearsal grader reads.

The guard is a typo-catcher in front of the role boundary. Beyond the spec's cases, these tests
pin its robustness rules: lexing ambiguities are refused rather than guessed, allow checks are
strict ASCII, deny checks fold Unicode aggressively. The property tests use a seeded generator
and an independent reference classifier, so they are deterministic.
"""

from __future__ import annotations

import random
import re

import pytest

from app.backend.chat import sqlguard
from app.backend.chat.sqlguard import Accepted, LexError, Refused, Rule, check, identifiers, lex

LIVE_MESSAGE = "The chat cannot reach CFBD. A live preview is one click on the head-to-head page."
SYSTEM_MESSAGE = (
    "System tables are not part of the chat's data. describe_view gives a view's columns."
)


def accepted(sql: str) -> str:
    verdict = check(sql)
    assert isinstance(verdict, Accepted), f"refused {sql!r}: {verdict}"
    return verdict.sql


def refused(sql: str) -> Refused:
    verdict = check(sql)
    assert isinstance(verdict, Refused), f"accepted {sql!r}"
    return verdict


# --- the spec's scenario: statements other than one read are refused before the database ---


@pytest.mark.parametrize(
    ("sql", "rule"),
    [
        ('INSERT INTO "PREDICTIONS" VALUES (1)', Rule.SELECT_ONLY),
        ("SELECT 1; SELECT 2", Rule.ONE_STATEMENT),
        ('SELECT * INTO TABLE "X" FROM "TEAMS"', Rule.INTO_TABLE),
        ('DROP VIEW "TEAMS"', Rule.SELECT_ONLY),
        ("ALTER SESSION SET QUERY_TIMEOUT = 0", Rule.SELECT_ONLY),
        ('SELECT * FROM "EXA_ALL_SESSIONS"', Rule.SYSTEM_TABLES),
    ],
)
def test_spec_statements_are_refused_naming_the_rule(sql: str, rule: Rule) -> None:
    verdict = refused(sql)
    assert verdict.rule is rule
    assert verdict.message


def test_spec_commented_select_runs_without_its_trailing_semicolon() -> None:
    assert accepted('/* note */ SELECT COUNT(*) FROM "TEAMS";') == (
        '/* note */ SELECT COUNT(*) FROM "TEAMS"'
    )


def test_refusal_messages_of_the_spec() -> None:
    assert refused('SELECT * FROM "CFB_API_LIVE"."GAME_PREVIEW"').message == LIVE_MESSAGE
    assert refused('SELECT * FROM "EXA_ALL_SESSIONS"').message == SYSTEM_MESSAGE
    assert sqlguard.LIVE_SCHEMA_MESSAGE == LIVE_MESSAGE
    assert sqlguard.SYSTEM_TABLES_MESSAGE == SYSTEM_MESSAGE


# --- accepted reads, sent as written ---


@pytest.mark.parametrize(
    ("sql", "sent"),
    [
        ("SELECT 1", "SELECT 1"),
        ("select 1", "select 1"),
        ("SeLeCt 1;", "SeLeCt 1"),
        ("SELECT 1;  \n\t", "SELECT 1"),
        ("  \n SELECT 1", "  \n SELECT 1"),
        ("WITH x AS (SELECT 1 AS a) SELECT a FROM x", "WITH x AS (SELECT 1 AS a) SELECT a FROM x"),
        ("(SELECT 1) UNION ALL (SELECT 2)", "(SELECT 1) UNION ALL (SELECT 2)"),
        ("SELECT 1; -- done", "SELECT 1"),
        ("SELECT 1-;-- c", "SELECT 1-"),
        ("SELECT 1 /* c */;", "SELECT 1 /* c */"),
        ("SELECT 1 -- note;", "SELECT 1 -- note;"),
        ("SELECT 1 -- c\r\nFROM DUAL", "SELECT 1 -- c\r\nFROM DUAL"),
        ('SELECT "SCHOOL" FROM "TEAMS" WHERE "SEASON" = 1995', None),
    ],
)
def test_accepted_reads_are_sent_as_written(sql: str, sent: str | None) -> None:
    assert accepted(sql) == (sql if sent is None else sent)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 'a;b'",
        "SELECT 'DROP TABLE x; --'",
        "SELECT 'it''s; fine'",
        "SELECT 'a'' ; DROP TABLE t; --'",
        'SELECT 1 AS "a;b"',
        'SELECT "a""; DROP TABLE t; --" FROM "T"',
        "SELECT 1 -- a; DROP TABLE t",
        "SELECT 1 /* ; DROP TABLE t; */",
        "SELECT 1 /* -- */ FROM DUAL",
        "SELECT 1 -- /* \nFROM DUAL",
        'SELECT 1 AS "INTO TABLE"',
        'SELECT "INTO", "TABLE" FROM "T"',
        "SELECT 'INTO TABLE x' FROM DUAL",
        "SELECT 1 /* INTO TABLE x */ FROM DUAL",
        "SELECT 'CFB_API_LIVE', 'EXA_ALL_SESSIONS', 'SYS' FROM DUAL",
        "SELECT 1 -- CFB_API_LIVE EXA_ALL_SESSIONS SYS",
        "SELECT 'Se\u00f1or \U0001f600 \u202f' AS \"\u00dcn\u00efc\u00f6d\u00e9\"",
        'SELECT SYSDATE, SYS_GUID(), "SYSTEM", "EXA", EXAMPLE FROM DUAL',
        "SELECT 2*/*c*/3",
        "SELECT 1//*c*/2",
        "SELECT 1e5, .5, 1.5E-3 FROM DUAL",
        "SELECT * FROM TEST_CFB_API.TEAMS",
        "SELECT * FROM (IMPORT FROM JDBC AT CFBD_API STATEMENT 'x')",
        "SELECT TEST_CFB_INFRA.CFBD_FETCH('x') FROM DUAL",
        "SELECT * FROM TEST_CFBD_VS.GAMES",
    ],
)
def test_text_in_strings_comments_and_names_does_not_trigger(sql: str) -> None:
    accepted(sql)


def test_length_limit_applies_to_the_text_as_received() -> None:
    at_limit = "SELECT 1" + " " * (sqlguard.MAX_CHARS - len("SELECT 1"))
    assert accepted(at_limit) == "SELECT 1"
    over = refused(at_limit + " ")
    assert over.rule is Rule.LENGTH
    assert "20,000" in over.message
    assert isinstance(check("SELECT 1   ", max_chars=8), Refused)


# --- refusals: one statement, the leading keyword, INTO, live schema, system tables ---


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1;;",
        "SELECT 1; /* c */ ;",
        "SELECT 'a'; DROP TABLE t",
        "SELECT 1 /* ; */; DROP TABLE t",
        "SELECT 1 -- c\n; DROP TABLE t",
        "SELECT 1 FROM DUAL WHERE 1=1--\n; DROP TABLE t",
        'SELECT "x"; SELECT 2;',
    ],
)
def test_more_than_one_statement_is_refused(sql: str) -> None:
    assert refused(sql).rule is Rule.ONE_STATEMENT


@pytest.mark.parametrize(
    ("sql", "found"),
    [
        ("", None),
        ("   ", None),
        (";", None),
        ("-- only a comment", None),
        ("/* only */", None),
        ("-- SELECT\nDROP TABLE t", "DROP"),
        ("/* SELECT */ DROP TABLE t", "DROP"),
        ("EXPLAIN VIRTUAL SELECT * FROM T", "EXPLAIN"),
        ("EXECUTE SCRIPT CFB_INFRA.X()", "EXECUTE"),
        ("OPEN SCHEMA CFB_API_LIVE", "OPEN"),
        ("MERGE INTO t USING s ON 1=1", "MERGE"),
        ("IMPORT INTO t FROM JDBC AT c STATEMENT 'x'", "IMPORT"),
        ('"SELECT" 1', None),
        ("\u017felect 1", None),
        ("\uff33\uff25\uff2c\uff25\uff23\uff34 1", None),
        ("SELECTX 1", "SELECTX"),
        ("1 SELECT", None),
        ("((DROP TABLE t))", "DROP"),
    ],
)
def test_only_select_or_with_may_lead(sql: str, found: str | None) -> None:
    verdict = refused(sql)
    assert verdict.rule is Rule.SELECT_ONLY
    if found is not None:
        assert found in verdict.message


@pytest.mark.parametrize(
    "sql",
    [
        "select * into table x from t",
        "SELECT * INTO /* c */ TABLE x FROM t",
        "SELECT * INTO\n-- c\nTABLE x FROM t",
        "SELECT 1INTO TABLE x FROM t",
        "SELECT 1.INTO TABLE x FROM t",
        "SELECT 'a'INTO TABLE x FROM t",
        'SELECT "a"INTO TABLE x FROM t',
        "SELECT * \uff29\uff2e\uff34\uff2f TABLE x FROM t",
        'SELECT * INTO "TABLE" FROM t',
        "WITH a AS (SELECT 1) SELECT * INTO TABLE x FROM a",
    ],
)
def test_into_is_refused_outside_strings_and_comments(sql: str) -> None:
    assert refused(sql).rule is Rule.INTO_TABLE


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM CFB_API_LIVE.GAME_PREVIEW",
        'SELECT * FROM "CFB_API_LIVE"."QUOTA"',
        "SELECT * FROM cfb_api_live.quota",
        "SELECT * FROM TEST_CFB_API_LIVE.GAME_PREVIEW",
        'SELECT * FROM "TEST_CFB_API_LIVE"."GAME_PREVIEW" WHERE "GAME_ID" = 4011',
        "SELECT * FROM PROBE_CFB_API_LIVE.QUOTA",
        "SELECT * FROM \uff23\uff26\uff22_\uff21\uff30\uff29_\uff2c\uff29\uff36\uff25.QUOTA",
        "SELECT * FROM cfb_api_l\u0131ve.quota",
        'SELECT * FROM "cfb_api_live"."quota"',
        "SELECT * FROM CFB_API_LIV\u00c9.QUOTA",
        "SELECT * FROM CFB_API_LIVE\u0301.QUOTA",
        "SELECT * FROM X.CFB_API_LIVE",
    ],
)
def test_the_live_schema_is_refused_in_any_namespace_and_spelling(sql: str) -> None:
    verdict = refused(sql)
    assert verdict.rule is Rule.LIVE_SCHEMA
    assert verdict.message == LIVE_MESSAGE


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM SYS.EXA_ALL_USERS",
        'SELECT * FROM "SYS"."DUAL"',
        "SELECT * FROM sys.dual",
        "SELECT * FROM EXA_STATISTICS.EXA_DB_SIZE_LAST_DAY",
        'SELECT * FROM "EXA_STATISTICS"."X"',
        'SELECT * FROM "$EXA_STATS_AUDIT_SQL"',
        "SELECT * FROM exa_all_sessions",
        "SELECT * FROM EXA_USER_PROFILE_LAST_DAY",
        "SELECT * FROM \uff25\uff38\uff21_ALL_SESSIONS",
        'SELECT * FROM "EXA_ALL_SESSIONS"',
        "SELECT * FROM EXA_ALL_COLUMNS WHERE COLUMN_SCHEMA = 'CFB_API'",
    ],
)
def test_system_schemas_and_views_are_refused(sql: str) -> None:
    verdict = refused(sql)
    assert verdict.rule is Rule.SYSTEM_TABLES
    assert verdict.message == SYSTEM_MESSAGE


def test_live_schema_takes_precedence_over_system_tables() -> None:
    assert refused("SELECT * FROM CFB_API_LIVE.QUOTA, SYS.DUAL").rule is Rule.LIVE_SCHEMA


# --- lexing ambiguities are refused rather than guessed ---


@pytest.mark.parametrize(
    "sql",
    [
        "/* a /* b */ */ SELECT 1",
        "SELECT 1 /* /* */",
        "/* a /* b */ DROP TABLE t; */ SELECT 1",
        "SELECT 'abc",
        "SELECT 'a''; DROP TABLE t",
        'SELECT "abc',
        "SELECT 1 /* abc",
        "SELECT 1 -- c\rDROP TABLE t",
        "SELECT 1 -- c\u2028DROP TABLE t",
        "SELECT 1 -- c\u2029DROP TABLE t",
    ],
)
def test_ambiguous_or_unterminated_text_is_refused(sql: str) -> None:
    assert refused(sql).rule is Rule.SYNTAX


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1\x00",
        "SELECT '\x07'",
        "SELECT 1 -- \x0b DROP",
        "SELECT 1\x85",
        "SELECT 1 /* \u202e */",
        "SELECT '\u2066x\u2069'",
        "SEL\u200bECT 1",
        "SELECT * FROM CFB_API\u200b_LIVE.QUOTA",
        "\ufeffSELECT 1",
    ],
)
def test_control_direction_and_invisible_characters_are_refused(sql: str) -> None:
    assert refused(sql).rule is Rule.CHARACTERS


def test_invisible_characters_inside_strings_and_quoted_names_are_data() -> None:
    assert accepted("SELECT 'a\u200bb' AS \"x\u200dy\"") == "SELECT 'a\u200bb' AS \"x\u200dy\""


def test_unicode_whitespace_in_code_separates_tokens() -> None:
    assert refused("SELECT *\u3000INTO\u00a0TABLE x FROM t").rule is Rule.INTO_TABLE


def test_quoted_live_schema_with_invisible_characters_is_refused() -> None:
    assert refused('SELECT * FROM "CFB_API\u200b_LIVE"."QUOTA"').rule is Rule.LIVE_SCHEMA


# --- the lexer and identifiers for grading ---


def test_lex_is_lossless_and_typed() -> None:
    sql = "SELECT \"a\"\"b\", 'c''d' -- e\n/* f */ FROM x1;"
    tokens = lex(sql)
    assert "".join(token.text for token in tokens) == sql
    kinds = [token.kind for token in tokens if token.kind != "space"]
    assert kinds == [
        "word",
        "ident",
        "symbol",
        "string",
        "line_comment",
        "block_comment",
        "word",
        "word",
        "semicolon",
    ]
    assert [token.start for token in tokens][:3] == [0, 6, 7]


def test_lex_splits_numbers_from_following_words() -> None:
    assert [(token.kind, token.text) for token in lex("1INTO 1e5x 1ex .5a")] == [
        ("number", "1"),
        ("word", "INTO"),
        ("space", " "),
        ("number", "1e5"),
        ("word", "x"),
        ("space", " "),
        ("number", "1"),
        ("word", "ex"),
        ("space", " "),
        ("number", ".5"),
        ("word", "a"),
    ]


def test_lex_raises_on_unterminated_text() -> None:
    with pytest.raises(LexError, match="character 8"):
        lex("SELECT 'abc")


def test_identifiers_for_grading() -> None:
    sql = (
        'SELECT "SCHOOL", p_in_cfp FROM "CFB_API"."PLAYOFF_ODDS" '
        'WHERE x = \'GAMES\' AND "a""b" = 1 -- TEAMS\n/* RANKINGS */'
    )
    assert identifiers(sql) == (
        "SELECT",
        "SCHOOL",
        "P_IN_CFP",
        "FROM",
        "CFB_API",
        "PLAYOFF_ODDS",
        "WHERE",
        "X",
        "AND",
        'a"b',
    )


def test_identifiers_raise_on_text_that_does_not_lex() -> None:
    with pytest.raises(LexError):
        identifiers("SELECT 'abc")


# --- property tests (seeded, deterministic) ---

# fmt: off
FRAGMENTS = (
    "SELECT", "select", "WITH", "with", "INTO", "into", "TABLE", "CFB_API_LIVE",
    "EXA_ALL_SESSIONS", "SYS", "DROP", "x", "y1", "1", "1e5", ".", ",", "(", ")", "$", "=",
    "'", "''", '"', '""', "--", "-", "/*", "*/", "/", "*", ";",
    " ", " ", "\n", "\r\n", "\t", "\u00e9", "\U0001f600", "\u2028",
)
# fmt: on
NUMBER = r"[0-9]+(?:\.[0-9]*)?(?:[eE][+-]?[0-9]+)?|\.[0-9]+(?:[eE][+-]?[0-9]+)?"
WORD = re.compile(NUMBER + r"|(?:[^\W\d]|\$)[\w$]*")


def random_text(rng: random.Random) -> str:
    return "".join(rng.choice(FRAGMENTS) for _ in range(rng.randint(0, 24)))


def reference_classes(text: str) -> list[str] | None:
    """An independent character classifier: code, string, ident or comment per character; None
    where the text is unterminated or ambiguous (nested block comment, odd line break)."""
    classes: list[str] = []
    i = 0
    while i < len(text):
        if text.startswith("--", i):
            end = text.find("\n", i)
            end = len(text) if end < 0 else end
            body = text[i:end]
            if "\u2028" in body or "\u2029" in body or "\r" in body.rstrip("\r"):
                return None
            if body.endswith("\r") and end == len(text):
                return None
            classes += ["comment"] * (end - i)
            i = end
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            if end < 0 or "/*" in text[i + 2 : end + 1]:
                return None
            classes += ["comment"] * (end + 2 - i)
            i = end + 2
        elif text[i] in "'\"":
            quote = text[i]
            j = i + 1
            while True:
                j = text.find(quote, j)
                if j < 0:
                    return None
                if text.startswith(quote * 2, j):
                    j += 2
                    continue
                break
            kind = "string" if quote == "'" else "ident"
            classes += [kind] * (j + 1 - i)
            i = j + 1
        else:
            classes.append("code")
            i += 1
    return classes


def lexer_classes(text: str) -> list[str]:
    mapping = {"string": "string", "ident": "ident", "line_comment": "comment"}
    mapping["block_comment"] = "comment"
    return [mapping.get(token.kind, "code") for token in lex(text) for _ in token.text]


def reference_rule(text: str) -> Rule | None:
    """The verdict of the checks, computed from the reference classifier: blank every
    non-code character, then look at words, semicolons and quoted names."""
    classes = reference_classes(text)
    assert classes is not None
    code = "".join(
        c if k == "code" else " " if k == "comment" else "#"
        for c, k in zip(text, classes, strict=True)
    )
    names = [
        text[m.start() + 1 : m.end() - 1].replace('""', '"')
        for m in re.finditer(r'"(?:[^"]|"")*"', "".join(
            c if k == "ident" else " " for c, k in zip(text, classes, strict=True)
        ))
    ]  # fmt: skip
    semis = [i for i, c in enumerate(code) if c == ";"]
    if semis and not code[semis[-1] + 1 :].strip():
        code = code[: semis[-1]] + " " + code[semis[-1] + 1 :]
    if ";" in code:
        return Rule.ONE_STATEMENT
    words = [m.group() for m in WORD.finditer(code) if m.group()[0] not in "0123456789."]
    first = code.lstrip(" \t\r\n(\u2028")
    if not words or not any(
        first.upper().startswith(w) and not _continues_word(first[len(w) : len(w) + 1])
        for w in ("SELECT", "WITH")
    ):
        return Rule.SELECT_ONLY
    upper = [w.upper() for w in words]
    if "INTO" in upper:
        return Rule.INTO_TABLE
    folded = upper + [n.upper() for n in names]
    if any("CFB_API_LIVE" in n for n in folded):
        return Rule.LIVE_SCHEMA
    for n in folded:
        for part in n.split("$"):
            if part in ("SYS", "EXA_STATISTICS") or part.startswith("EXA_"):
                return Rule.SYSTEM_TABLES
    return None


def _continues_word(char: str) -> bool:
    return bool(char) and (char.isalnum() or char in "_$")


def test_property_lexer_matches_the_reference_classifier() -> None:
    rng = random.Random(20261023)
    for _ in range(4000):
        text = random_text(rng)
        expected = reference_classes(text)
        if expected is None:
            with pytest.raises(LexError):
                lex(text)
            continue
        assert "".join(token.text for token in lex(text)) == text
        assert lexer_classes(text) == expected, text


def test_property_check_matches_the_reference_verdict() -> None:
    rng = random.Random(7)
    for _ in range(4000):
        text = random_text(rng)
        verdict = check(text)
        if reference_classes(text) is None:
            assert isinstance(verdict, Refused) and verdict.rule is Rule.SYNTAX, text
            continue
        rule = reference_rule(text)
        if rule is None:
            assert isinstance(verdict, Accepted), (text, verdict)
        else:
            assert isinstance(verdict, Refused) and verdict.rule is rule, (text, verdict, rule)


def test_property_accepted_text_is_a_prefix_of_the_input_and_idempotent() -> None:
    rng = random.Random(42)
    seen = 0
    for _ in range(6000):
        text = "SELECT " + random_text(rng)
        verdict = check(text)
        if not isinstance(verdict, Accepted):
            continue
        seen += 1
        candidates = {text.rstrip()} | {text[:i].rstrip() for i, c in enumerate(text) if c == ";"}
        assert verdict.sql in candidates, text
        assert check(verdict.sql) == verdict, text
    assert seen > 300


DANGEROUS = (
    "DROP TABLE t",
    "SELECT * INTO TABLE x FROM t",
    "SELECT * FROM CFB_API_LIVE.QUOTA",
    "SELECT * FROM SYS.EXA_ALL_SESSIONS",
    "ALTER SESSION SET QUERY_TIMEOUT = 0",
    'DELETE FROM "PREDICTIONS"',
    "x'; DROP TABLE t; --",
    'a"; DROP TABLE t; --',
)
BENIGN = (
    "SELECT {} FROM DUAL",
    'SELECT "SCHOOL" FROM "TEAMS" WHERE "SCHOOL" = {}',
    "WITH a AS (SELECT {} AS v) SELECT v FROM a",
)


def test_property_payloads_in_strings_and_comments_are_inert() -> None:
    rng = random.Random(99)
    for _ in range(600):
        payload = rng.choice(DANGEROUS) + rng.choice(("", ";", " -- x", "\n"))
        template = rng.choice(BENIGN)
        as_string = "'" + payload.replace("'", "''") + "'"
        accepted(template.format(as_string))
        comment_safe = payload.replace("/*", "").replace("*/", "")
        accepted(template.format("1 /* " + comment_safe + " */"))
        line_safe = comment_safe.replace("\n", " ")
        accepted(template.format("1 -- " + line_safe + "\n"))
        assert refused(template.format("1") + "; " + rng.choice(DANGEROUS)).rule in (
            Rule.ONE_STATEMENT,
            Rule.SYNTAX,
        )


BASES = (
    ('SELECT "SCHOOL" FROM "TEAMS" WHERE "SEASON" = 2025', None),
    ("WITH a AS (SELECT 1 AS v) SELECT v FROM a", None),
    ("DROP TABLE x", Rule.SELECT_ONLY),
    ("SELECT * INTO TABLE x FROM t", Rule.INTO_TABLE),
    ("SELECT * FROM CFB_API_LIVE.QUOTA", Rule.LIVE_SCHEMA),
    ("SELECT * FROM SYS.EXA_ALL_SESSIONS", Rule.SYSTEM_TABLES),
    ("SELECT 1 FROM DUAL; SELECT 2 FROM DUAL", Rule.ONE_STATEMENT),
)
NOISE = (" ", "  ", "\n", "\t", "/* c */", "/**/", "-- c\n", "\r\n")


def test_property_case_and_comment_noise_keep_the_verdict() -> None:
    rng = random.Random(5)
    for _ in range(800):
        base, rule = rng.choice(BASES)
        parts = re.split(r"( )", base)
        noisy = "".join(
            rng.choice(NOISE) if part == " " else _random_case(rng, part) for part in parts
        )
        verdict = check(noisy)
        if rule is None:
            assert isinstance(verdict, Accepted), noisy
        else:
            assert isinstance(verdict, Refused) and verdict.rule is rule, (noisy, verdict)


def _random_case(rng: random.Random, part: str) -> str:
    if '"' in part:
        return part
    return "".join(c.upper() if rng.random() < 0.5 else c.lower() for c in part)


def test_property_check_never_raises_on_arbitrary_text() -> None:
    rng = random.Random(1)
    alphabet = "SELCTWHINOAB;'\"-/*\n\r\t ()$_1\u00e9\x00\u2028\u202e\u200b"
    for _ in range(3000):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 40)))
        assert isinstance(check(text), Accepted | Refused)
