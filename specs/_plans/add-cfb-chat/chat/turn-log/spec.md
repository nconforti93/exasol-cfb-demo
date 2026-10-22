# Feature: Turn log: every question, its SQL, its rows and its tokens

After a demo, or when an answer looked wrong, the presenter needs to see exactly what happened: what was asked, what ran, what came back, and what it cost. Every turn writes one line to a local log that never leaves the machine and never holds a secret.

## Background

- **File:** `CHAT_LOG_PATH`, default `cache/chat-log.jsonl` (`cache/` is git-ignored). One JSON object per line, appended under a lock when the turn ends, whatever the outcome.
- **Fields of a line:**
  - `logged_at`, `started_at`, `ended_at` (UTC), `chat_id`, `turn_id` (random ids, nothing that identifies a person), `target` (`local` or `aws`), the namespace prefix;
  - `model` (requested), `effort`, `fallbacks`, `prompt_sha256` (`chat/safety`), `stub` true for the stub model;
  - `question`, and the validated `app_state` as the model received it (`chat/app-state`);
  - `calls`: per model call its `stop_reason`, the model that served it, `usage`, the cost in US dollars and the API request id;
  - `tools`: per tool call its name, the validated input, the `sql_id`, the exact SQL text, the row count, `truncated`, the first 20 rows, the milliseconds and the error; refused statements with the rule;
  - `app_actions` with the state before and after;
  - `answer` (the final text), `outcome` (`done`, `failed`, `cancelled`, `stopped`, `refused` when the model declined, `rejected` when the input check refused the question before any model call, `chat/safety`), the reason for an outcome other than `done`, and the totals: tokens, cost, model calls, tool calls.
- **Redaction.** Before a line is written, every string is checked for the Anthropic key and the database passwords held in memory; a match is replaced with `[REDACTED]` and counted in `redactions`. The key and passwords are never added in the first place; this is the second line of defence.
- **Failure to write** the log does not fail the turn: the answer is shown, and the server's log gets one warning per failure (without the line's content).
- **Rows** in the log are local data like `cache/`; the log is never committed or sent anywhere (CFBD terms).
- The daily cap reads the log back (`chat/budget`).
- The examples run on the chat test stack with the scripted model and `CHAT_LOG_PATH` in a temporary directory.

## Scenarios

### Scenario: A finished turn is logged with its question, SQL, rows and tokens

* *GIVEN* a script that calls `set_whatif` and `whatif_odds` for "Brookvale wins at Ashford" and answers, with fixed usage
* *WHEN* the client asks "What if Brookvale wins at Ashford?" and the turn ends
* *THEN* the log MUST gain exactly one line, with the question, the app state, two model calls with their usage and cost, and the tool calls with the exact SQL texts of every disclosed statement, their row counts and their first rows
* *AND* the line's totals MUST equal the sums of its calls, and its `outcome` MUST be `done`

### Scenario: Turns that do not finish are logged with their outcome

* *GIVEN* four turns: one cancelled, one stopped by the step limit, one failed by a model error, and one declined by the model
* *WHEN* each turn ends
* *THEN* the log MUST hold one line per turn with `outcome` `cancelled`, `stopped`, `failed` and `refused` and the reason
* *AND* each line MUST hold the statements that ran before the turn ended

### Scenario: A failing log does not fail the turn, and the log never leaks

* *GIVEN* `CHAT_LOG_PATH` in a directory that is not writable, and then a writable one, and a chat connection test double whose error message for one statement echoes the chat user's password
* *WHEN* the client asks a question whose script runs that statement, once with each path
* *THEN* the first turn MUST end `done` with its answer, and the server's log MUST hold one warning without the line's content
* *AND* the line in the writable log MUST show `[REDACTED]` in place of the password, with `redactions` 1
