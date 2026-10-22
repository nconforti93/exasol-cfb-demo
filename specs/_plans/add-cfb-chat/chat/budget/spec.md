# Feature: Budget: cost and token caps per conversation and per day

Every model call costs money, and a demo laptop must never run up a bill by accident. Each call is priced from the usage the API reports. A conversation stops before the call that could take it over its cap, a day stops at its cap across conversations and restarts, and the panel shows the spend as it grows.

## Background

- **Prices** (`app/backend/chat/prices.py`; US dollars per million tokens; source: the claude-api skill's model table, cached 2026-10-06, re-checked against the pricing page by task 1.3):

  | Model | Input | Output | Cache write (5 min) | Cache read |
  |---|---|---|---|---|
  | `claude-opus-5-5` | 4.00 | 20.00 | 5.00 | 0.20 |
  | `claude-opus-5` | 5.00 | 25.00 | 6.25 | 0.50 |
  | `claude-opus-4-8` | 5.00 | 25.00 | 6.25 | 0.50 |

  Cache writes cost 1.25 × input for the 5-minute lifetime. A model that is not in the table is priced at the highest row, and the turn log records a warning.
- **Cost of a call** = input × input price + cache-creation input × cache-write price + cache-read input × cache-read price + output × output price, from `usage`. When the refusal fallback ran (`chat/conversation`), each entry of `usage.iterations` is priced at its own model; otherwise the whole call at the model that served it. Thinking tokens are output tokens.
- **Tokens of a call** = input + cache-creation input + cache-read input + output.
- **Caps** (settings; proposed defaults, re-set after the live rehearsal by task 8.2):

  | Setting | Default | Scope |
  |---|---|---|
  | `CHAT_SESSION_MAX_USD` | 2.00 | one conversation |
  | `CHAT_SESSION_MAX_TOKENS` | 500,000 | one conversation |
  | `CHAT_DAILY_MAX_USD` | 20.00 | all conversations of the server, per UTC day |
  | `CHAT_TURN_MAX_STEPS` / `CHAT_TURN_MAX_TOOLS` | 8 / 16 | one turn (`chat/conversation`) |

- **Before each model call** the loop computes its worst case: the previous call's input tokens (all kinds) plus the new messages' estimated tokens (characters / 2, on the safe side), all at the full input price, plus `max_tokens` at the output price; the token worst case is the same input estimate plus `max_tokens`. If spent plus the worst case would pass a conversation cap, or the day's spend plus the worst case would pass the daily cap, the call is not made: unanswered tool calls get "turn stopped: budget" results, and the turn ends `stopped` naming the cap. A new question in that conversation is refused with status 409 `chat_budget_exhausted`; "New chat" starts a fresh conversation unless the daily cap is reached.
- **The day's spend** survives restarts: at start the backend sums the costs of today's turns from the turn log (`chat/turn-log`).
- **The panel** shows, under the input, the conversation's spend and tokens against its caps ("$0.42 of $2.00 · 61k of 500k tokens"), from the `usage` events, and a notice when a cap stopped a turn.
- The examples use the scripted model with fixed usage per step, so the costs are exact.

## Scenarios

### Scenario: Each call is priced from its usage

* *GIVEN* usage of 1,200 input, 15,000 cache-read, 2,000 cache-creation and 900 output tokens served by `claude-opus-5-5`, a second usage whose `iterations` hold one `claude-opus-5-5` entry and one fallback entry served by `claude-opus-4-8`, and a third usage served by an unknown model
* *WHEN* each is priced
* *THEN* the first MUST cost $0.0048 + $0.0030 + $0.0100 + $0.0180 = $0.0358
* *AND* the second MUST price each iteration at its own model's row
* *AND* the third MUST be priced at the highest row and MUST be flagged for a warning

### Scenario: A conversation stops before the call that could pass its cost cap

* *GIVEN* `CHAT_SESSION_MAX_USD` 1.00, a conversation that has spent $0.20 in one call of 10,000 input tokens, and a script whose every step calls a tool and reports 10,000 input and 3,000 output tokens served by `claude-opus-5-5` ($0.10 per call; worst case about $0.36 with `max_tokens` 16,000)
* *WHEN* the client asks a question
* *THEN* the scripted model MUST receive exactly 5 requests, the turn MUST end `stopped` naming the conversation's cost cap, and the conversation's spend MUST be $0.70
* *AND* a new question in the conversation MUST be refused with status 409 `chat_budget_exhausted`, and a new conversation MUST be accepted

### Scenario: A conversation stops at its token cap

* *GIVEN* `CHAT_SESSION_MAX_TOKENS` 50,000, a conversation whose one earlier call reported 12,000 input and 3,000 output tokens, and a script whose every step calls a tool and reports the same usage
* *WHEN* the client asks a question
* *THEN* the scripted model MUST receive exactly 1 request: the first call's worst case (15,000 spent, about 12,300 input, 16,000 output) stays within 50,000, and the second call's (30,000 spent, at least 12,000 input, 16,000 output) does not
* *AND* the turn MUST end `stopped` naming the conversation's token cap, and the conversation's tokens MUST be 30,000

### Scenario: The daily cap spans conversations and restarts

* *GIVEN* `CHAT_DAILY_MAX_USD` 1.00 and a turn log holding turns of today costing $0.98 and turns of yesterday costing $5.00
* *WHEN* the backend starts and a client asks a question in a new conversation, and the test clock then moves to the next UTC day
* *THEN* the first question MUST be refused with status 409 `chat_daily_budget_exhausted` before any model call
* *AND* after the day changes, a question MUST be accepted

### Scenario: The panel shows the spend against the caps

* *GIVEN* the chat panel rendered with a conversation whose `usage` events report $0.42 and 61,000 tokens against caps of $2.00 and 500,000
* *WHEN* the panel renders, and then receives a turn that ended `stopped` on the cost cap
* *THEN* it MUST show "$0.42 of $2.00 · 61k of 500k tokens"
* *AND* after the stopped turn it MUST show a notice naming the cap and a "New chat" button, and MUST disable the input of that conversation
