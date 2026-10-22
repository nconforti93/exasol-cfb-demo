# Feature: SQL disclosure: every answer shows the SQL it ran

The audience should never have to trust the chat. Under every answer the panel lists each statement that ran for it, exactly as the database received it, with its rows and time, and offers to copy it into a SQL client. The same statements appear in the inspector next to the pages' own queries.

## Background

- **What is disclosed.** Every statement that the chat connection sends during a turn, from any tool: `run_sql`, `describe_view`, `whatif_odds`, `rooting_guide`, and the lookups that validate app-state changes (`chat/app-state`). Statements that the static checks refused are disclosed too, marked "refused before the database, not run" (`chat/data-tools`).
- **Each entry** has a `sql_id` (numbered per conversation: `S1`, `S2`, …), the tool, the model's stated purpose for `run_sql`, the SQL text exactly as the driver sent it (pyexasol's formatted statement text, as in Phase 6's query log), the row count (or "more than 200"), the milliseconds, and the error message if any. The text is never reformatted or shortened.
- **Under the answer.** The `answer` event lists the turn's `sql_id`s. The panel shows "SQL (n)" under the answer; opening it lists the entries in order, each with a "copy SQL" control. An answer that ran no statement shows "No SQL ran for this answer."
- **In the inspector.** Each entry is also added to the browser session's query log (`app/inspector`) with the principal `chat` and the chat user's name, so "plan on demand" works for it as for a page query. A statement that reads only `CFB_API` shows "no virtual table: 0 calls".
- **Numbers come from results.** The system prompt tells the model to state no number that is not in a tool result of the conversation, and to name the method with every what-if number (`chat/data-tools`). The rehearsed question set checks this (`chat/rehearsal`).
- The examples run on the chat test stack with the scripted model and the statement recorder of `chat/data-tools`.

## Scenarios

### Scenario: The answer lists exactly the statements that ran, as sent

* *GIVEN* a script that calls `set_whatif` for "Brookvale wins at Ashford", runs `whatif_odds` for Brookvale, and runs `run_sql` with a statement containing a comment, mixed case and irregular spacing, before answering
* *WHEN* the client asks a question and reads the turn's events and the transcript
* *THEN* the answer's `sql_id`s MUST name one entry for every statement in the statement recorder for that turn, in the same order, and no other
* *AND* each entry's text MUST equal, byte for byte, the text the statement recorder received, and the `run_sql` entry MUST equal the model's input apart from a removed trailing `;`
* *AND* the validation lookups of `set_whatif` MUST be listed with the tool name `set_whatif`

### Scenario: Refused and failed statements are disclosed with their reason

* *GIVEN* a script whose `run_sql` calls are `DELETE FROM "TEAMS"` and a `SELECT` of an unknown column, followed by an answer
* *WHEN* the client asks a question
* *THEN* the answer's disclosure MUST list the `DELETE` marked "refused before the database, not run" with the rule, and the `SELECT` with the database's error message
* *AND* the statement recorder MUST hold only the `SELECT`

### Scenario: An answer without SQL says so

* *GIVEN* a script that answers "Hello" without calling a tool
* *WHEN* the client asks "Hi"
* *THEN* the `answer` event MUST list no `sql_id`, and the panel MUST show "No SQL ran for this answer."

### Scenario: The chat's statements appear in the inspector

* *GIVEN* a turn whose script ran one `run_sql` over `TEST_CFB_API.PLAYOFF_ODDS`
* *WHEN* the same browser session reads `GET /api/inspector` and asks for the plan of that entry
* *THEN* the query log MUST hold the statement with the principal `chat` and the user `CFB_TEST_CHAT`
* *AND* the plan MUST read "no virtual table: 0 calls", and the mock CFBD API MUST NOT receive any request

### Scenario: The panel shows the SQL under each answer and copies it

* *GIVEN* the chat panel rendered with a synthetic transcript of two answers, the first with two statements (one with an error) and the second with none
* *WHEN* the user opens "SQL (2)" under the first answer and presses "copy SQL" on its second entry
* *THEN* the panel MUST list both statements in order with their row counts, milliseconds and the error, in a monospaced block without reformatting
* *AND* the clipboard MUST receive the second statement's text exactly
* *AND* the second answer MUST show "No SQL ran for this answer."
