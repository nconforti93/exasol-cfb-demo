# Feature: Chat availability: on with a key, a clear state without one

The chat panel sits beside the pages and answers questions about the data. It needs two things the core app does not: an Anthropic API key and a database user that can read only the serving API. When either is missing, the panel says so in one line and the rest of the app works as before. The key never leaves the server.

## Background

- **Settings** (`app/backend/settings.py`; environment first, then `.env`, which git ignores):

  | Variable | Default | Meaning |
  |---|---|---|
  | `APP_CHAT` | `auto` | `auto`: the panel is shown, and enabled when everything below is in place and the server is bound to a loopback address (as Phase 6 decides it for the operator path, `app/access`); otherwise shown disabled with the reason. `on`: the same, on any bind address. `off`: no panel at all |
  | `ANTHROPIC_API_KEY` | — | the only credential the chat uses |
  | `CHAT_MODEL` | `claude-opus-5-5` | the model id, or `stub` for the scripted model (below) |
  | `CFB_CHAT_USER` | `CFB_CHAT_WEB` | the chat's database user |
  | `CFB_CHAT_PASSWORD` | — | its password |

  The caps, the effort and the log path are in `chat/budget`, `chat/conversation` and `chat/turn-log`.
- **The key.** The backend builds the Anthropic client with the value of `ANTHROPIC_API_KEY` passed explicitly. It never builds a client without an explicit key, so no other credential source is ever used: not `ANTHROPIC_AUTH_TOKEN`, not an `ant auth login` profile, not workload identity federation. The key is never written to a response, a log, a prompt or the built frontend (`chat/safety`).
- **The chat user** has `CREATE SESSION` and the role `CFB_MCP`, which holds `SELECT` on the schema `CFB_API` only (`api/roles`, Phase 5). It cannot read `CFB_API_LIVE`, so the chat can never make a CFBD call.
  - `make chat-user [ROTATE=yes]` creates it on the operator path, as `make app-user` creates the app's read user (`app/access`): `CREATE USER … IDENTIFIED BY …` with the password from `CFB_CHAT_PASSWORD`, then `GRANT CREATE SESSION` and `GRANT CFB_MCP`. It is idempotent, never prints the password, and refuses when the role or the password is missing.
  - **The start check** reads the user's effective privileges with the same check the app uses for its read user (`app/access`) and accepts the user only when: its system privileges are exactly `CREATE SESSION`; its roles are exactly `CFB_MCP` (and `PUBLIC`); its effective object privileges are exactly `SELECT` on the schema `CFB_API`; it owns no object and has no connection access and no impersonation grant.
- **States** reported by `GET /api/config` as `chat: {available, enabled, reason, model}`:

  | `reason` | When | `available` | `enabled` |
  |---|---|---|---|
  | `ok` | key set, chat user accepted | true | true |
  | `stub` | `CHAT_MODEL=stub`, chat user accepted | true | true |
  | `no_api_key` | `ANTHROPIC_API_KEY` empty or unset (and not `stub`) | true | false |
  | `chat_user_missing` | `CFB_CHAT_PASSWORD` unset, or the login fails | true | false |
  | `chat_user_not_read_only` | the start check refused the user; the message names the privilege | true | false |
  | `network_bind` | `APP_CHAT=auto` and a non-loopback bind: every chat question spends money, so a network-exposed app does not offer it unless switched `on`; the stub model costs nothing and is not affected | true | false |
  | `off` | `APP_CHAT=off` | false | false |

  `model` is the configured model id when enabled, otherwise absent. The panel renders when `available` is true. A disabled panel shows "Chat disabled: no API key" (or the reason's message) and a one-line hint, and has no input field.
- **Stub mode.** `CHAT_MODEL=stub` runs the scripted model of the rehearsed question set (`chat/rehearsal`) instead of Claude, with no key and no network request. It matches a question to a script by its exact text after binding, with the bindings of `CHAT_STUB_DATASET` (default `demo`; the tests use `sim_league`). It answers the rehearsed questions with their scripts against the live database and answers anything else with "The stub model knows only the rehearsed questions." The panel shows a "stub model" badge on every answer.
- **The core app is independent of the chat.** A disabled chat, a refused chat user or an unreachable Anthropic API never changes what a page shows or blocks the server's start.
- While the chat is not enabled, every `/api/chat/*` request answers 503 with the reason, before any model call or database statement. With `APP_CHAT=off` they answer 404.
- The examples run on the chat test stack (`plan.md`, Key Interfaces): Phase 6's app test stack plus the chat user `CFB_TEST_CHAT` with the role `TEST_CFB_MCP`, the scripted model, and a marker key that no API accepts.

## Scenarios

### Scenario: Without an API key the panel says so and the app works

* *GIVEN* the chat test stack with `ANTHROPIC_API_KEY` unset and the chat user configured
* *WHEN* a client reads `GET /api/config`, loads the odds page's data, and posts a chat question
* *THEN* the config MUST report `chat` with `available` true, `enabled` false and `reason` `no_api_key`
* *AND* the odds data MUST be served with status 200
* *AND* the chat question MUST be refused with status 503 naming the reason, and the scripted model MUST NOT receive any request
* *AND* the rendered panel MUST show "Chat disabled: no API key" and MUST NOT show an input field

### Scenario: With a key and an accepted chat user the chat is enabled

* *GIVEN* the chat test stack with the marker key in `ANTHROPIC_API_KEY` and the chat user `CFB_TEST_CHAT`
* *WHEN* a client reads `GET /api/config`
* *THEN* `chat` MUST report `available` and `enabled` true, `reason` `ok` and `model` `claude-opus-5-5`
* *AND* no part of the response MUST contain the marker key

### Scenario: Only the explicit API key enables the chat

* *GIVEN* the chat test stack with `ANTHROPIC_API_KEY` unset, `ANTHROPIC_AUTH_TOKEN` set to a marker value, and `ANTHROPIC_PROFILE` naming a profile
* *WHEN* the backend starts and a client reads `GET /api/config`
* *THEN* `chat` MUST report `reason` `no_api_key`
* *AND* the backend MUST NOT build an Anthropic client

### Scenario: A chat user that can do more than read the serving API is refused

* *GIVEN* the chat test stack with the marker key
* *WHEN* the backend starts with `CFB_CHAT_USER` set to a user holding `TEST_CFB_APP` (which reads `TEST_CFB_API_LIVE`), and then to a user holding `TEST_CFB_MCP` and `CREATE TABLE`
* *THEN* both starts MUST serve the pages, and `chat` MUST report `reason` `chat_user_not_read_only` with a message naming the user and the privilege (`SELECT` on `TEST_CFB_API_LIVE`; the system privilege `CREATE TABLE`)
* *AND* every `/api/chat/*` request MUST answer 503

### Scenario: The stub model answers the rehearsed questions without a key

* *GIVEN* the chat test stack with `CHAT_MODEL=stub`, `CHAT_STUB_DATASET=sim_league` and no `ANTHROPIC_API_KEY`
* *WHEN* a client asks the first rehearsed question, and then "What is the weather in Ashford?"
* *THEN* `chat` MUST report `reason` `stub`, and the first answer MUST follow the question's script against the database and carry the stub badge
* *AND* the second answer MUST be "The stub model knows only the rehearsed questions."
* *AND* no request MUST leave the process for an Anthropic endpoint

### Scenario: On a network bind the chat is off unless switched on

* *GIVEN* the chat test stack with the marker key and the chat user
* *WHEN* the backend starts on `0.0.0.0` with `APP_CHAT=auto`, and then with `APP_CHAT=on`
* *THEN* the first MUST report `chat` with `enabled` false and `reason` `network_bind`, and MUST answer chat questions with status 503
* *AND* the second MUST report `enabled` true

### Scenario: The chat can be switched off entirely

* *GIVEN* the chat test stack with the marker key and `APP_CHAT=off`
* *WHEN* a client reads `GET /api/config` and posts a chat question
* *THEN* `chat` MUST report `available` false and `reason` `off`, and the app MUST NOT render the chat region
* *AND* the chat question MUST answer 404

### Scenario: make chat-user creates the chat user once

* *GIVEN* the integration-test namespace with the role `TEST_CFB_MCP`, and a password in `CFB_CHAT_PASSWORD`
* *WHEN* the owner runs `make chat-user ARGS="--prefix TEST_ --user CFB_TEST_CHAT2"` twice, and once more with `CFB_CHAT_PASSWORD` unset for another new user
* *THEN* after the first run `CFB_TEST_CHAT2` MUST exist with exactly `CREATE SESSION` and the role `TEST_CFB_MCP`, and MUST log in with that password
* *AND* the second run MUST report no change
* *AND* the third run MUST refuse, naming `CFB_CHAT_PASSWORD`, and MUST NOT create a user
* *AND* the output of all three runs MUST NOT contain the password
