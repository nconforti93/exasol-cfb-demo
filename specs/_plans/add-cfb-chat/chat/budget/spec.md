# Feature: Budget: cost caps per conversation and per day, and a context cap per call

Every model call costs money, and a demo laptop must never run up a bill by accident. Each call is priced from the usage the API reports. Before a call, its true worst case is reserved against the caps, so neither one conversation nor two at once can pass them. Every call's cost is written down before the next call starts, and if it cannot be written, live calls stop. The panel shows the spend as it grows.

## Background

- **Prices** (`app/backend/chat/prices.py`; US dollars per million tokens; source: the claude-api skill's model table, cached 2026-10-06, re-checked against the pricing page by task 1.3):

  | Model | Input | Output | Cache write (5 min) | Cache read |
  |---|---|---|---|---|
  | `claude-opus-5-5` | 4.00 | 20.00 | 5.00 | 0.20 |
  | `claude-opus-5` | 5.00 | 25.00 | 6.25 | 0.50 |
  | `claude-opus-4-8` | 5.00 | 25.00 | 6.25 | 0.50 |

  Cache writes cost 1.25 × input for the 5-minute lifetime. The two lower rows are the targets of the refusal fallback for `claude-opus-5-5`. A model that is not in the table is priced at the highest row, and the turn log records a warning.
- **Cost of a call** = input × input price + cache-creation input × cache-write price + cache-read input × cache-read price + output × output price, from `usage`. When the refusal fallback ran (`chat/conversation`), each entry of `usage.iterations` is priced at its own model; otherwise the whole call at the model that served it. Thinking tokens are output tokens.
- **Input estimate of the next call** = the previous call's input tokens of all kinds (input, cache creation, cache read) + its output tokens (earlier thinking stays in the context) + the new messages since its response, at one token per 2 characters (on the safe side). For a conversation's first call, the whole request at one token per 2 characters.
- **Worst case of a call:**
  - the input estimate at the model's cache-write price (an expired cache writes everything again) + `max_tokens` at its output price;
  - and, when the refusal fallback is on, one fallback hop on top: the input estimate at the highest cache-write price of the table + `max_tokens` at the highest output price (a mid-answer decline is billed, and the fallback model starts with a cold cache).

  A re-issued call (after unparseable tool input, `chat/conversation`) is a call like any other: it counts as a step and needs its own reservation.
- **Caps** (settings; proposed defaults, re-set after the live rehearsal by task 7.2):

  | Setting | Default | Scope |
  |---|---|---|
  | `CHAT_SESSION_MAX_USD` | 2.00 | one conversation's spend |
  | `CHAT_DAILY_MAX_USD` | 20.00 | all model calls of the day (UTC), across conversations, restarts and rehearsal runs |
  | `CHAT_CALL_MAX_INPUT_TOKENS` | 150,000 | the input estimate of one call: the conversation's context size |
  | `CHAT_TURN_MAX_STEPS` / `CHAT_TURN_MAX_TOOLS` / `CHAT_TURN_MAX_SECONDS` | 8 / 16 / 90 | one turn (`chat/conversation`) |

- **Reservations.** Before each call, under one lock for the process, the loop reserves the call's worst case. The call is made only when:
  - the conversation's spend, plus its open reservations, plus the worst case stays within `CHAT_SESSION_MAX_USD`;
  - the day's spend in the spend file, plus every open reservation of the process, plus the worst case stays within `CHAT_DAILY_MAX_USD`;
  - the input estimate stays within `CHAT_CALL_MAX_INPUT_TOKENS`.

  Otherwise the call is not made and the turn ends `stopped`, naming the cap. After the call, the reservation is settled at the call's cost. A call that ends without a final `usage` (a closed stream, an abandoned call, a broken connection) is settled at its worst case.
- **The spend file** (`CHAT_SPEND_PATH`, default `cache/chat-spend.jsonl`) gets one line per settled call: UTC time, process, conversation, model, usage, cost, and `estimated` true when the cost is a worst case. The line is appended and flushed under a file lock before the next call starts. The server and the rehearsal runs (`chat/rehearsal`) write to the same file, and the server reads today's lines again before each reservation, so the daily cap covers both.
  - **If a line cannot be written,** the call's cost stays counted in memory, no further live call is made in that process, the running turn ends `failed` with `spend_log_unwritable`, and `GET /api/config` reports the chat disabled with that reason (`chat/availability`). Stub mode spends nothing and is not affected.
- **A spent conversation.** A new question in a conversation is refused with status 409 `chat_budget_exhausted` when its first call's input estimate passes the context cap, or when the smallest worst case of that call would pass the conversation's cost cap. "New chat" starts a fresh conversation unless the daily cap is reached, in which case questions are refused with 409 `chat_daily_budget_exhausted`.
- **The panel** shows, under the input, the conversation's spend against its cap and its context size against the context cap ("$0.42 of $2.00 · context 61k of 150k tokens"), from the `usage` events, and a notice when a cap stopped a turn.
- The examples use the scripted model with fixed usage per step, so the costs are exact. Their tool steps run `run_sql` with a one-row count over `TEAMS`, so the new messages of a step add only a few hundred tokens to the estimate.

## Scenarios

### Scenario: Each call is priced from its usage

* *GIVEN* usage of 1,200 input, 15,000 cache-read, 2,000 cache-creation and 900 output tokens served by `claude-opus-5-5`, a second usage whose `iterations` hold one `claude-opus-5-5` entry and one fallback entry served by `claude-opus-4-8`, and a third usage served by an unknown model
* *WHEN* each is priced
* *THEN* the first MUST cost $0.0048 + $0.0030 + $0.0100 + $0.0180 = $0.0358
* *AND* the second MUST price each iteration at its own model's row
* *AND* the third MUST be priced at the highest row and MUST be flagged for a warning

### Scenario: The worst case counts the cache-write price and one fallback hop

* *GIVEN* an input estimate of 10,000 tokens, `max_tokens` 8,000 and the model `claude-opus-5-5`
* *WHEN* the worst case is computed with the refusal fallback off, and then on
* *THEN* the first MUST be $0.0500 + $0.1600 = $0.2100
* *AND* the second MUST be $0.2100 + $0.0625 + $0.2000 = $0.4725

### Scenario: A conversation stops before the call that could pass its cost cap

* *GIVEN* `CHAT_SESSION_MAX_USD` 0.80, `CHAT_MAX_TOKENS` 8,000 and `CHAT_FALLBACKS=off`, a conversation that has spent $0.10 in one call of 10,000 input and 3,000 output tokens, and a script whose every step calls a tool and reports 10,000 input and 3,000 output tokens served by `claude-opus-5-5` ($0.10 per call; worst case about $0.23: about 13,300 input tokens at $5.00 and 8,000 output tokens at $20.00 per million)
* *WHEN* the client asks a question
* *THEN* the scripted model MUST receive exactly 5 requests, the turn MUST end `stopped` naming the conversation's cost cap, and the conversation's spend MUST be $0.60
* *AND* a new question in the conversation MUST be refused with status 409 `chat_budget_exhausted`, and a new conversation MUST be accepted

### Scenario: A conversation stops at its context cap

* *GIVEN* `CHAT_CALL_MAX_INPUT_TOKENS` 18,000, a conversation whose one earlier call reported 12,000 input and 3,000 output tokens, and a script whose every step calls a tool and reports 16,000 input and 3,000 output tokens
* *WHEN* the client asks a question
* *THEN* the scripted model MUST receive exactly 1 request: the first call's estimate (about 15,300 tokens) stays within 18,000, and the second call's (at least 19,000) does not
* *AND* the turn MUST end `stopped` naming the context cap, and a new question in the conversation MUST be refused with status 409 `chat_budget_exhausted`

### Scenario: The daily cap spans conversations and restarts

* *GIVEN* `CHAT_DAILY_MAX_USD` 1.00 and a spend file holding calls of today costing $0.98 and calls of yesterday costing $5.00
* *WHEN* the backend starts and a client asks a question in a new conversation, and the test clock then moves to the next UTC day
* *THEN* the first question MUST be refused with status 409 `chat_daily_budget_exhausted` before any model call
* *AND* after the day changes, a question MUST be accepted

### Scenario: Concurrent turns cannot pass the daily cap together

* *GIVEN* `CHAT_DAILY_MAX_USD` 1.00, a spend file holding $0.60 of today, and two conversations whose next calls each have a worst case of $0.30
* *WHEN* two threads reserve those calls at the same moment
* *THEN* exactly one reservation MUST be granted, and the other MUST be refused naming the daily cap
* *AND* after the granted call settles at $0.10, the day's spend MUST be $0.70, and a new reservation of $0.30 MUST be granted

### Scenario: A failed spend write stops live calls

* *GIVEN* the marker key, the scripted model standing in for Claude, a script with two tool steps, and `CHAT_SPEND_PATH` in a directory that the test makes unwritable after the first model call
* *WHEN* the client asks a question
* *THEN* the scripted model MUST receive exactly 1 request, the first response's tool call MUST be answered "turn stopped: spend_log_unwritable" without running, and the turn MUST end `failed` naming `spend_log_unwritable`
* *AND* `GET /api/config` MUST then report `chat` with `enabled` false and `reason` `spend_log_unwritable`
* *AND* a server in stub mode with the same path MUST still answer a rehearsed question

### Scenario: The panel shows the spend against the caps

* *GIVEN* the chat panel rendered with a conversation whose `usage` events report $0.42 and a context of 61,000 tokens against caps of $2.00 and 150,000
* *WHEN* the panel renders, and then receives a turn that ended `stopped` on the cost cap
* *THEN* it MUST show "$0.42 of $2.00 · context 61k of 150k tokens"
* *AND* after the stopped turn it MUST show a notice naming the cap and a "New chat" button, and MUST disable the input of that conversation
