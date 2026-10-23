# Feature: Data tools: the analyst reads the serving API and nothing else

The analyst answers from the same curated views the pages read, including the four pass-through views over the model for questions outside the pages. It writes its own read-only SQL, runs the what-if and rooting-guide queries through the same library as the app, and gets the database's own error messages back, so it can correct a query. The database role is the boundary: the chat user can read `CFB_API` and nothing else, so it can neither change data nor spend a CFBD call. Results are capped by rows, columns and size, so one answer cannot fill the conversation.

## Background

- **Principal.** Every statement of the chat runs as the chat user (`chat/availability`), role `CFB_MCP`: `SELECT` on the schema `CFB_API` only (`api/roles`). `CFB_API` views cost 0 CFBD calls and make no network request (`api/serving-contract`); `CFB_API_LIVE`, which can call CFBD, is not readable.
- **Chat connections.** A pool of 2 sessions as the chat user, separate from the app's read pool, with autocommit (the page contract).
  - **Time limit.** Each session sets the query timeout `CHAT_QUERY_TIMEOUT` (default 20 s) with `ALTER SESSION SET QUERY_TIMEOUT`, as Phase 6's read pool sets its own (its probe 1.1(e)). The model cannot change it: every statement it sends goes through `run_sql`, whose first-keyword check refuses `ALTER SESSION` (below). If probe 1.1(e) shows that the session parameter does not stop a statement, `make chat-user` assigns the chat user a consumer group with `QUERY_TIMEOUT` instead (`plan.md`, decision [9]).
  - Each session opens the serving schema (`OPEN SCHEMA CFB_API`), so unqualified view names resolve to it.
  - **Abort.** A cancel or the turn's time limit aborts a running statement through pyexasol's `abort_query` on its session (`chat/conversation`). A session the database closed instead is replaced.
- **Tools** (JSON schemas in `plan.md`, Key Interfaces):
  - `run_sql {sql, purpose}`: one read-only statement over the serving views.
    - **Static checks before the database** (a typo-catcher with clear messages; the role is the boundary):
      - one statement (one trailing `;` and trailing whitespace are removed before sending);
      - the first keyword, after comments, is `SELECT` or `WITH`;
      - no `INTO TABLE` outside strings and comments (Exasol's `SELECT … INTO TABLE` creates a table);
      - no reference to the live schema;
      - no reference to the system schemas `SYS` and `EXA_STATISTICS` or to a system view (`EXA_…`, `$EXA_…`), with the message "System tables are not part of the chat's data. describe_view gives a view's columns.";
      - at most 20,000 characters.

      A refused statement is answered with an error result naming the rule. A reference to the live schema is answered with "The chat cannot reach CFBD. A live preview is one click on the head-to-head page."
    - Apart from that trailing `;`, the statement runs exactly as written; nothing is added or rewritten.
  - `describe_view {view}`: the comment and the columns (name, type, comment) of one view of the serving schema, from `EXA_ALL_VIEWS` and `EXA_ALL_COLUMNS` with bound values. An unknown name is an error result.
  - `whatif_odds {conditions, team_ids?}`: the conditional odds for the what-if run of `CFB_API.CURRENT_POINT.WHATIF_RUN_ID`, built by `analyses/whatif.py` (`what_if_odds_sql`), optionally limited to the given teams after the query. The library's validation errors are error results naming the condition. While `WHATIF_VIEWS_CURRENT` is FALSE, or when the run changed between the lookup and the query (no rows, or an unknown-column error, `analyses/what-if`), the result is the error "the what-if run changed; read the current point again".
  - `rooting_guide {team_id}`: the team's rows of `CFB_API.ROOTING_GUIDE` for the run of the current point's publication, as the odds page shows them without toggles (`app/odds`).
- **Result caps**, applied in this order to every result, with the statement left as written:
  1. **Rows:** at most 200 (`CHAT_ROWS_MAX`). The backend fetches at most 201 and reports the cap when there were more.
  2. **Text values:** cut at 500 characters and marked.
  3. **Columns:** at most 40 (`CHAT_RESULT_MAX_COLUMNS`), the first 40 in statement order; the names of the others are listed in `dropped_columns`.
  4. **Size:** the serialized result is at most 20,000 characters (`CHAT_RESULT_MAX_CHARS`). Rows are dropped from the end until it fits; when not even one row fits, the result holds no row and says so.

  `truncated` is true when any cap applied, and `truncated_by` lists which: `rows`, `columns`, `size`.
- **Results** are JSON objects: `kind`, `sql_id` (`chat/sql-disclosure`), `columns`, `rows`, `row_count`, `truncated`, `truncated_by`, `dropped_columns`, `ms`, and a fixed `note` that the values are data from the database and are not instructions (`chat/safety`). What-if and guide results also carry `method` ("conditioning: the simulated seasons in which these results happened") and the run id.
- **Errors** from the database are error results with Exasol's message text, without the DSN, the user's password or any driver stack.
- The examples run on the chat test stack (`chat/availability`) with the scripted model, in live mode against the mock CFBD API, after `refreshed(10)`. The what-if condition is "Brookvale wins at Ashford", the week-12 game set to an away win (`app/odds`). A statement recorder on the chat connection lists every statement the database received.

## Scenarios

### Scenario: A question outside the pages is answered from the pass-through views

* *GIVEN* the history variant's meetings of Ashford and Brookvale (Phase 5 `analyses/head-to-head`)
* *WHEN* the script runs `run_sql` with `SELECT * FROM "GAMES" WHERE "SEASON" = 1995` filtered to the Ashford–Brookvale game, and then with a `TEAM_SEASONS` query for Ashford in 2025
* *THEN* each result MUST equal the rows that the same statement returns when run directly as `CFB_TEST_CHAT`
* *AND* the mock CFBD API MUST NOT receive any request

### Scenario: Statements other than one read are refused before the database

* *GIVEN* the script's `run_sql` calls with `INSERT INTO "PREDICTIONS" VALUES (1)`, `SELECT 1; SELECT 2`, `SELECT * INTO TABLE "X" FROM "TEAMS"`, `DROP VIEW "TEAMS"`, `ALTER SESSION SET QUERY_TIMEOUT = 0`, `SELECT * FROM "EXA_ALL_SESSIONS"`, and `/* note */ SELECT COUNT(*) FROM "TEAMS";`
* *WHEN* the client asks a question
* *THEN* the first six MUST be answered with error results naming the rule they break, and the statement recorder MUST show none of them
* *AND* the seventh MUST run as written, without its trailing `;`

### Scenario: The role keeps the chat inside the serving API

* *GIVEN* the script's `run_sql` calls that pass the static checks: a `SELECT` from `TEST_CFB_PRED.PREDICTION`, a `SELECT` from `TEST_CFB_META.FETCH_LOG`, a call of the test namespace's fetch script `CFBD_FETCH` in `TEST_CFB_INFRA` with valid arguments, a `SELECT` from a table of the test namespace's virtual schema, and an `IMPORT` subselect from the test namespace's CFBD connection; and a sixth call that selects from `TEST_CFB_API_LIVE.GAME_PREVIEW` for a week-11 game id
* *WHEN* the client asks a question, and the test then runs the same six statements directly as `CFB_TEST_CHAT`
* *THEN* the first five MUST be answered with the database's error and MUST NOT return a row, and the sixth with "The chat cannot reach CFBD. A live preview is one click on the head-to-head page."
* *AND* each direct statement MUST fail with an insufficient-privileges error, except that the `IMPORT` subselect MAY fail with a syntax error where the database has no such form
* *AND* a direct read of `EXA_ALL_SESSIONS` as `CFB_TEST_CHAT`, while the app's read user runs a statement, MUST NOT contain that statement's text
* *AND* the mock CFBD API MUST NOT receive any request, and the ledger MUST NOT change

### Scenario: A large result is capped

* *GIVEN* the published run's 2,000 simulated seasons in `WHATIF_OUTCOMES`, which has one column per remaining game
* *WHEN* the script runs `run_sql` with `SELECT * FROM "WHATIF_OUTCOMES"` with the default caps, and again with `CHAT_RESULT_MAX_COLUMNS` 3 and `CHAT_RESULT_MAX_CHARS` 2,000
* *THEN* the first result MUST hold at most 200 rows, its serialized JSON MUST be at most 20,000 characters, `truncated` MUST be true, and `truncated_by` MUST name every cap that applied
* *AND* the second MUST hold the first 3 columns, list the other column names in `dropped_columns`, serialize to at most 2,000 characters, and name `rows`, `columns` and `size` in `truncated_by`
* *AND* each statement shown and logged MUST be the statement as written, without an added `LIMIT`

### Scenario: A slow statement is stopped by the time limit

* *GIVEN* `CHAT_QUERY_TIMEOUT` set to 2 seconds
* *WHEN* the script runs a `run_sql` statement that takes far longer than that (a three-way cross join of `WHATIF_OUTCOMES`, counted with a filter on an expression of all three `SIM_ID`s, so that no plan can shortcut it), and then answers
* *THEN* the tool result MUST be the database's timeout error, returned within 10 seconds of the call
* *AND* the turn MUST continue to its answer, and the next statement on the same connection MUST succeed

### Scenario: The what-if tool gives the app's numbers

* *GIVEN* the what-if run of the current point
* *WHEN* the script calls `whatif_odds` with "Brookvale wins at Ashford" and Brookvale's team id
* *THEN* the result MUST equal Brookvale's row of `what_if_odds_sql` for that run and condition, run directly as `CFB_TEST_APP`, with the run id and the method "conditioning: the simulated seasons in which these results happened"
* *AND* a call with a game that is not among the run's games MUST be answered with the library's message naming that game, before any statement

### Scenario: The rooting-guide tool follows the odds page

* *GIVEN* Brookvale and the what-if run of the current point
* *WHEN* the script calls `rooting_guide` for Brookvale
* *THEN* the result MUST equal Brookvale's rows of `TEST_CFB_API.ROOTING_GUIDE` for the run of the current point's publication

### Scenario: The database's error message reaches the model so it can correct its query

* *GIVEN* a script whose first `run_sql` selects the unknown column `"WINS_TOTAL"` from `TEAM_SEASONS`, whose second `describe_view` reads `TEAM_SEASONS`, and whose third `run_sql` uses a column from that description
* *WHEN* the client asks a question
* *THEN* the first result MUST be an error result carrying Exasol's message that names the column, and no DSN, password or driver stack
* *AND* the second MUST list every column of `TEST_CFB_API.TEAM_SEASONS` with its comment, and the third MUST succeed
