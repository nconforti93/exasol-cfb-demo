# Feature: Conversation: a question, visible steps, an answer

A presenter types a question. The panel shows what the analyst is doing while it works (the queries it runs, the app changes it makes), then the answer, with the SQL behind it. Follow-up questions build on the earlier ones. One question runs at a time, a runaway turn stops at a fixed number of steps or seconds, and Stop works at any moment. Whatever happens to one question, the next one in the conversation still works.

## Background

- **Conversations** belong to the browser session (Phase 6's anonymous session cookie, `app/access`). "New chat" starts a new conversation with its own history and its own caps (`chat/budget`). A conversation id of another browser session is unknown (404). Conversations live in the server's memory; a restart ends them, and the turn log keeps the record (`chat/turn-log`).
- **HTTP API** (every `POST` carries Phase 6's `X-CFB-Action` header and an allowed `Origin`, `app/access`):

  | Method and path | Does |
  |---|---|
  | `POST /api/chat/conversations` | starts a conversation → `{chat_id}` |
  | `GET /api/chat/conversations/{chat_id}` | the transcript: questions (also those that never reached the history), answers, disclosed SQL, app changes, spend and caps |
  | `POST /api/chat/turns` `{chat_id, message, app_state}` | starts a turn in the background → 202 `{turn_id}`; 409 while a turn of the conversation runs |
  | `GET /api/chat/turns/{turn_id}?after=<seq>` | the turn's events after `seq` and its state: `running`, `done`, `failed`, `refused`, `cancelled`, `stopped` |
  | `POST /api/chat/turns/{turn_id}/cancel` | stops the turn (below) |
  | `GET /api/chat/suggestions` | in stub mode the rehearsed questions, bound (`chat/availability`); otherwise an empty list |

  A message is 1 to 2,000 characters. At most 2 turns run at once in the server; a third is refused with status 409 `chat_busy`. The panel polls the turn every 400 ms while it runs.
- **Events** of a turn, numbered in order: `status` (for example "thinking", "step 2 of at most 8", "answer continued by claude-opus-5"), `text` (a piece of the answer as it streams), `text_reset` (drop the text streamed so far for this turn, with the reason), `tool_call` (tool name and validated input), `sql` (`chat/sql-disclosure`), `app_action` (`chat/app-state`), `usage` (tokens and cost so far, `chat/budget`), `answer` (the final text and the ids of the turn's statements), `error` (a code and a short message), `done`.
- **The model request.** Model `CHAT_MODEL` (default `claude-opus-5-5`, the claude-api skill's current default), `max_tokens` from `CHAT_MAX_TOKENS` (default 8,000: short answers, and a tighter worst case for `chat/budget`), streamed.
  - **Thinking:** `{"type": "adaptive", "block_binding": {"prefix_mismatch_behavior": CHAT_PREFIX_MISMATCH}}` with the beta `thinking-binding-controls-2026-08-01`. Adaptive is the only mode this model has, and its text is not shown. `CHAT_PREFIX_MISMATCH` is `drop_block` in the panel, so a history bug degrades the answer instead of failing it on stage, and `error` in every rehearsal run (`chat/rehearsal`), so the same bug fails the run on any account.
  - `output_config.effort` from `CHAT_EFFORT` (default `medium`, set explicitly because it is this model's default); `tool_choice` `auto`.
  - The server-side refusal fallback is on (`fallbacks: "default"` with the beta `server-side-fallback-2026-07-01`) unless `CHAT_FALLBACKS=off`.
  - **Caching:** one explicit `cache_control` breakpoint on the system prompt, plus automatic caching at the top level for the growing conversation (both with the 5-minute lifetime).
  - Every response's `input_transformations` (a list of thinking blocks the API dropped) is kept for the turn log (`chat/turn-log`).
- **The tool loop** runs in the backend (`plan.md`, decision [1]). Per step it checks the caps, sends the conversation, streams the response, and then acts on the stop reason:
  - `end_turn`: the text is the answer; the turn is `done`.
  - `tool_use`: every tool call of the response is validated against its schema and run in order, and all results go back in one user message. A call whose input fails validation is not run and is answered with an error result that quotes the input. Calls beyond the turn's tool limit are not run and are answered "turn stopped: tool limit" in that same message, and the turn ends `stopped`.
  - `refusal`: no tool of that response runs, the text streamed so far is withdrawn (`text_reset`), and the turn ends `refused` with "The model declined to answer this." and the category when the API names one.
  - `max_tokens`: no tool runs, the streamed text is withdrawn, and the turn ends `failed` with "the answer was cut off".
  - **Any other stop reason:** the same as `max_tokens`, with the message "the answer stopped unexpectedly (<stop reason>)".
  - A turn has at most 8 model calls, 16 tool calls and 90 seconds (`CHAT_TURN_MAX_STEPS`, `CHAT_TURN_MAX_TOOLS`, `CHAT_TURN_MAX_SECONDS`). At the step limit the last response's tool calls are answered "turn stopped: step limit" without running; at the time limit the turn stops as a cancel would (below) and ends `stopped`.
- **The answer after a fallback.** When the server-side fallback switched models during a response, the response holds a `fallback` block at each switch. Only the tool calls after the last `fallback` block run. The response enters the history in the form the API requires: the `thinking`, `redacted_thinking` and `tool_use` blocks before the last `fallback` block, and any other model-internal block before it, are left out; text blocks before it and every block after it are kept as they are; the `fallback` block itself is left out (the transcript and the turn log keep it). The streamed text stays on screen, because the fallback model continues from it, and the turn emits a `status` naming the model that finished the answer.
- **The history** is what the next request sends. It is append-only and it is always valid for the API:
  - **A question enters the history with its first kept response.** A response is kept when it ends `end_turn` or `tool_use`. The question, its app-state system message (`chat/app-state`) and the first kept response are added together. A turn that ends before its first kept response (a refusal, a cut-off or unexpected response, a model failure, a cancel, the time limit, a budget stop) adds nothing to the history; its question stays in the transcript and the turn log.
  - **A response that is not kept is never added**, whatever step it came from, so no assistant message is ever empty.
  - **Every tool call is answered.** A kept response with tool calls is always followed by one user message that holds, in order, one result per call: its result, its input error, or "turn stopped: <reason>" for a call that did not run.
  - **The open tail.** When the history ends with tool results that no kept response has answered yet (the turn stopped after them, or its next response was not kept), the next question is added as a text block after those results in that same user message, and its app-state system message follows. This is the API's form for user input after tool results, and it avoids two user messages in a row.
  - **Nothing earlier changes.** Within a conversation the system prompt and the tool definitions are byte-identical in every request, and every kept response, and every message before it, stays exactly as it was in the request that produced it. Kept responses are added unchanged, thinking blocks included, apart from the fallback rule above.
- **The conformance check.** `chat/conformance.py` checks a request against the API's rules for the history (`plan.md`, Key Interfaces): roles and the placement of system messages, no empty message or text block, every tool call answered in the next message and nowhere else, no assistant message at the end, the system prompt and tools unchanged, and every kept response with every message before it unchanged. The scripted model runs the whole check on every request it receives, so every test also shows that the API would accept the request. The live model runs the rules that need no earlier request before every request it sends, and fails the turn with `internal_history_error` instead of sending a request the API would reject.
- **Cancel** stops the turn within 1 second, whatever it is doing: an open response stream is closed and its text withdrawn (`text_reset`); a running statement is aborted on its session (pyexasol's `abort_query`); a model call still waiting for its response headers is abandoned, and its late result is discarded. No model call or tool starts after it. Tool calls of a kept response that did not run are answered "turn stopped: cancelled". The turn ends `cancelled`, and the conversation takes the next question at once.
- **Failures of the Anthropic API** (authentication, rate limit or server errors after the SDK's 1 retry, connection errors, timeouts of `CHAT_API_TIMEOUT`, default 60 s) end the turn `failed` with a short message and no driver or HTTP detail. They never change what a page shows.
- The examples run on the chat test stack (`chat/availability`) with the scripted model, which records each request it receives and runs the conformance check on it; a failed check fails the test. "Ashford's 1995 game" is the history variant's meeting of Ashford and Brookvale (Phase 5 `analyses/head-to-head`).

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
* *AND* every request of the conversation MUST pass the conformance check

### Scenario: One turn runs at a time in a conversation

* *GIVEN* a conversation with a running turn whose script waits for a test signal
* *WHEN* the client posts another question to the same conversation, and one to a new conversation
* *THEN* the first MUST be refused with status 409 naming the running turn
* *AND* the second MUST be accepted

### Scenario: A turn that keeps calling tools stops at the step limit

* *GIVEN* a script that answers every step with another `run_sql` call
* *WHEN* the client asks a question, and then a second question in the same conversation
* *THEN* the scripted model MUST receive exactly 8 requests for the first, and the turn MUST end `stopped` with "step limit reached (8)"
* *AND* the eighth response's tool call MUST be answered with a "turn stopped: step limit" result without running
* *AND* the second question MUST be added as a text block after that result in the same user message, followed by its app-state system message, and its request MUST pass the conformance check

### Scenario: A response with more tool calls than the turn allows is answered in full

* *GIVEN* `CHAT_TURN_MAX_TOOLS` set to 3 and a script whose first response holds 5 `run_sql` calls
* *WHEN* the client asks a question
* *THEN* the first 3 statements MUST run, and the 4th and 5th calls MUST be answered with "turn stopped: tool limit" results in the same user message as the first 3 results
* *AND* the turn MUST end `stopped` with "tool limit reached (3)", and the next question's request MUST pass the conformance check

### Scenario: A declined request runs no tool and withdraws its text

* *GIVEN* a script whose first response streams the text "Brookvale's chances are", then ends with `stop_reason` `refusal`, the category `cyber`, and a `run_sql` call
* *WHEN* the client asks a question
* *THEN* the turn MUST end `refused` with "The model declined to answer this." naming the category, and a `text_reset` event MUST follow the streamed text
* *AND* no statement MUST run on the chat connection
* *AND* the next question's request MUST hold neither the declined question nor the declined response

### Scenario: A turn that ends before its first kept response leaves the history unchanged

* *GIVEN* three conversations, each with one answered question, whose second turns end before any kept response: a refusal with an empty `content`, a model that raises the SDK's server error after its retry, and a cancel while the first model call still waits for its response
* *WHEN* the client asks a third question in each conversation
* *THEN* each third request MUST pass the conformance check, and MUST hold neither the second question, nor its app-state message, nor an assistant message with empty content
* *AND* each third turn MUST end `done`, and each transcript MUST still show the second question with its outcome

### Scenario: A response finished by the fallback model keeps only what came after the switch

* *GIVEN* a script whose first response holds a text block, a `thinking` block and a `run_sql` call, then a `fallback` block from `claude-opus-5-5` to `claude-opus-5`, then a `describe_view` call, and ends `tool_use`, and whose second response answers
* *WHEN* the client asks a question
* *THEN* only `describe_view` MUST run, and the chat connection MUST NOT receive the `run_sql` statement
* *AND* in the second request that response MUST hold the text block and the `describe_view` call, and no `thinking` block, `run_sql` call or `fallback` block from before the switch
* *AND* the turn MUST emit a `status` naming `claude-opus-5`, MUST NOT emit a `text_reset`, and MUST end `done`

### Scenario: A cut-off or unexpected response runs no tool

* *GIVEN* one script whose response ends with `stop_reason` `max_tokens` and a `run_sql` call with truncated input, and another whose response ends with the stop reason `something_new` and a `run_sql` call
* *WHEN* the client asks a question with each script
* *THEN* the first turn MUST end `failed` with "the answer was cut off", and the second `failed` with "the answer stopped unexpectedly (something_new)"
* *AND* each MUST emit a `text_reset`, and no statement MUST run on the chat connection
* *AND* the next question's request in each conversation MUST hold neither response and MUST pass the conformance check

### Scenario: Tool input that does not match the schema is sent back, not run

* *GIVEN* a script whose first response calls `set_whatif` with `conditions` as a string, and whose second response answers
* *WHEN* the client asks a question
* *THEN* the first tool result MUST be an error that quotes the received input and names the schema violation
* *AND* no app action and no statement MUST result from that call, and the turn MUST end `done`

### Scenario: A failing model API fails the turn, not the app

* *GIVEN* a model that raises the SDK's server error after its retry, and then one that cannot connect
* *WHEN* the client asks a question with each, and loads the odds page's data
* *THEN* both turns MUST end `failed` with a short message that contains no HTTP status line, header or driver text
* *AND* the odds data MUST be served with status 200

### Scenario: A presenter can stop a turn while a statement runs

* *GIVEN* a script with three tool steps whose second step runs the slow statement of `chat/data-tools` (a three-way cross join of `WHATIF_OUTCOMES`)
* *WHEN* the client cancels the turn while that statement runs
* *THEN* the turn MUST end `cancelled` within 2 seconds of the cancel, and the scripted model MUST NOT receive a third request
* *AND* the transcript MUST show the statements that ran before the cancel and the aborted statement with its error
* *AND* a next question in the conversation MUST run a statement on the chat pool successfully and end `done`

### Scenario: Cancel during a streaming response ends the turn within a second

* *GIVEN* a script whose first response streams text and then holds the stream open until a test signal
* *WHEN* the client cancels the turn while the stream is open
* *THEN* the turn MUST end `cancelled` within 1 second of the cancel, with a `text_reset` after the streamed text, and the scripted model MUST record that the stream was closed
* *AND* a next question in the conversation MUST end `done`, and its request MUST pass the conformance check and MUST NOT hold the cancelled question

### Scenario: A turn stops at its time limit

* *GIVEN* `CHAT_TURN_MAX_SECONDS` set to 2 and a script whose first response holds its stream open until a test signal that never comes
* *WHEN* the client asks a question
* *THEN* the turn MUST end `stopped` with "time limit reached (2 s)" within 3 seconds of being posted, with a `text_reset`
* *AND* a next question in the conversation MUST end `done`

### Scenario: The conformance check rejects requests the API would reject

* *GIVEN* a valid two-turn request history and copies of it with one defect each: an app-state system message followed by a user message, an assistant message with empty content, a tool call without its result in the next message, a `tool_result` placed after a text block, a changed earlier message, a changed system prompt, and an assistant message as the last message
* *WHEN* the conformance check runs on each
* *THEN* the valid history MUST pass
* *AND* each copy MUST fail, naming the rule it breaks and the position of the message
