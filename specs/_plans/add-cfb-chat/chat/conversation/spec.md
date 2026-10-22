# Feature: Conversation: a question, visible steps, an answer

A presenter types a question. The panel shows what the analyst is doing while it works (the queries it runs, the app changes it makes), then the answer, with the SQL behind it. Follow-up questions build on the earlier ones. One question runs at a time, and a runaway turn stops at a fixed number of steps.

## Background

- **Conversations** belong to the browser session (Phase 6's anonymous session cookie, `app/access`). "New chat" starts a new conversation with its own history and its own caps (`chat/budget`). A conversation id of another browser session is unknown (404). Conversations live in the server's memory; a restart ends them, and the turn log keeps the record (`chat/turn-log`).
- **HTTP API** (every `POST` carries Phase 6's `X-CFB-Action` header and an allowed `Origin`, `app/access`):

  | Method and path | Does |
  |---|---|
  | `POST /api/chat/conversations` | starts a conversation → `{chat_id}` |
  | `GET /api/chat/conversations/{chat_id}` | the transcript: questions, answers, disclosed SQL, app changes, spend and caps |
  | `POST /api/chat/turns` `{chat_id, message, app_state}` | starts a turn in the background → 202 `{turn_id}`; 409 while a turn of the conversation runs |
  | `GET /api/chat/turns/{turn_id}?after=<seq>` | the turn's events after `seq` and its state: `running`, `done`, `failed`, `cancelled`, `stopped` |
  | `POST /api/chat/turns/{turn_id}/cancel` | asks the turn to stop |

  A message is 1 to 2,000 characters. At most 2 turns run at once in the server; a third is refused with status 409 `chat_busy`. The panel polls the turn every 400 ms while it runs.
- **Events** of a turn, numbered in order: `status` (for example "thinking", "step 2 of at most 8"), `text` (a piece of the answer as it streams), `tool_call` (tool name and validated input), `sql` (`chat/sql-disclosure`), `app_action` (`chat/app-state`), `usage` (tokens and cost so far, `chat/budget`), `answer` (the final text and the ids of the turn's statements), `error` (a code and a short message), `done`.
- **The model request.** Model `CHAT_MODEL` (default `claude-opus-5-5`, the claude-api skill's current default), `max_tokens` 16,000, adaptive thinking (the only mode this model has; its text is not shown), `output_config.effort` from `CHAT_EFFORT` (default `medium`, set explicitly because it is this model's default), `tool_choice` `auto`, streamed. The server-side refusal fallback is on (`fallbacks: "default"` with the beta `server-side-fallback-2026-07-01`) unless `CHAT_FALLBACKS=off`. Automatic prompt caching (`cache_control` `ephemeral` at the top level).
- **The tool loop** runs in the backend (`plan.md`, decision [1]). Per step it sends the conversation, streams the response, and then:
  - `end_turn`: the text is the answer; the turn is `done`;
  - `tool_use`: every tool call of the response is validated against its schema and run in order, and all results go back in one user message; a call whose input fails validation is not run and is answered with an error result that quotes the input;
  - `refusal`: the turn ends with "The model declined to answer this." and the category when the API names one; no tool of that response runs;
  - `max_tokens` with a tool call: no tool runs, and the turn fails with "the answer was cut off";
  - a turn has at most 8 model calls and 16 tool calls (`CHAT_TURN_MAX_STEPS`, `CHAT_TURN_MAX_TOOLS`).
- **Append-only history.** Within a conversation the system prompt and the tool definitions are byte-identical in every request, and each request's messages begin with the previous request's messages and the previous response, unchanged, thinking blocks included. When a turn stops with tool calls unanswered (step limit, cancel, a cap, a failure after the response), each unanswered call gets an error result ("turn stopped: <reason>") appended, so the next request is valid.
- **App state per question.** Each question is followed by a system message (`role: system` inside the conversation) that carries the app state the browser sent with it, after validation (`chat/app-state`). Earlier messages are never edited.
- **Cancel** takes effect between steps: no model call or tool starts after it, and the turn ends `cancelled`.
- **Failures of the Anthropic API** (authentication, rate limit or server errors after the SDK's 2 retries, connection errors, timeouts) end the turn `failed` with a short message and no driver or HTTP detail. They never change what a page shows.
- The examples run on the chat test stack (`chat/availability`) with the scripted model, which records each request it receives. "Ashford's 1995 game" is the history variant's meeting of Ashford and Brookvale (Phase 5 `analyses/head-to-head`).

## Scenarios

### Scenario: A question gets an answer with its steps shown

* *GIVEN* a conversation and a script that runs one `run_sql` over `TEST_CFB_API.GAMES` for Ashford's 1995 game and then answers
* *WHEN* the client posts "How did Ashford do against Brookvale in 1995?" and polls the turn until it ends
* *THEN* the events MUST arrive in this order: `status`, `tool_call`, `sql`, one or more `text`, `usage`, `answer`, `done`
* *AND* the turn MUST end `done`, and the transcript MUST hold the question, the answer and the statement

### Scenario: A follow-up question extends the conversation without editing it

* *GIVEN* a conversation in which one question was answered after one tool call
* *WHEN* the client asks a second question
* *THEN* the scripted model's second-turn request MUST carry the same system prompt and tool definitions, byte for byte, as every earlier request
* *AND* its messages MUST begin with the messages of the previous request followed by the previous response, unchanged, and then the new question and its app-state system message

### Scenario: One turn runs at a time in a conversation

* *GIVEN* a conversation with a running turn whose script waits for a test signal
* *WHEN* the client posts another question to the same conversation, and one to a new conversation
* *THEN* the first MUST be refused with status 409 naming the running turn
* *AND* the second MUST be accepted

### Scenario: A turn that keeps calling tools stops at the step limit

* *GIVEN* a script that answers every step with another `run_sql` call
* *WHEN* the client asks a question
* *THEN* the scripted model MUST receive exactly 8 requests, and the turn MUST end `stopped` with "step limit reached (8)"
* *AND* the last tool calls MUST be answered with "turn stopped: step limit" results, so that the next question's request passes the history check (every tool call answered in the message that follows it)

### Scenario: A declined request runs no tool

* *GIVEN* a script whose response has `stop_reason` `refusal`, the category `cyber`, and a `run_sql` call
* *WHEN* the client asks a question
* *THEN* the turn MUST end with "The model declined to answer this." naming the category
* *AND* no statement MUST run on the chat connection

### Scenario: A cut-off response runs no tool

* *GIVEN* a script whose response has `stop_reason` `max_tokens` and a `run_sql` call with truncated input
* *WHEN* the client asks a question
* *THEN* the turn MUST end `failed` with "the answer was cut off"
* *AND* no statement MUST run on the chat connection

### Scenario: Tool input that does not match the schema is sent back, not run

* *GIVEN* a script whose first response calls `set_whatif` with `conditions` as a string, and whose second response answers
* *WHEN* the client asks a question
* *THEN* the first tool result MUST be an error that quotes the received input and names the schema violation
* *AND* no app action and no statement MUST result from that call, and the turn MUST end `done`

### Scenario: A failing model API fails the turn, not the app

* *GIVEN* a model that raises the SDK's server error after its retries, and then one that cannot connect
* *WHEN* the client asks a question with each, and loads the odds page's data
* *THEN* both turns MUST end `failed` with a short message that contains no HTTP status line, header or driver text
* *AND* the odds data MUST be served with status 200

### Scenario: A presenter can stop a running turn

* *GIVEN* a script with three tool steps, the second of which waits for a test signal
* *WHEN* the client cancels the turn during the second step, and the signal is then released
* *THEN* the turn MUST end `cancelled`, and the scripted model MUST NOT receive a third request
* *AND* the transcript MUST show the statements that ran before the cancel
