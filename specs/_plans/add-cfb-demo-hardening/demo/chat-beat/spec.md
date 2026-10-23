# Feature: The chat beat on a kit: bound questions, stub fallback, recorded live answers

Beat 5 has the chat set a what-if, explain a swing and answer a question outside the pages, showing its SQL. It closes with thirty seconds of the same read-only role in an external MCP client. On stage it must work with a key, without a key, and without network.

The rehearsed questions are bound to the demo kit's teams and weeks, and every bound name must exist at the kit's point before the demo starts. Without a key, the scripted stub answers them, with numbers computed live from the kit. Once a key exists, one live run of the beat on the kit can be recorded from the turn log. That transcript replays offline, but only on data that is provably the kit's: the tools run again on the same kit, and the answers are Claude's own words.

## Background

- **Stage questions** are Phase 7's must-pass questions (`chat/rehearsal`, hand-off P8-a):
  - RQ01 sets a what-if;
  - RQ04 explains a swing;
  - RQ10 answers a question outside the pages;
  - RQ15 is the guardrail aside, with RQ14 as its alternative.

  `demo/demo.yaml` lists them as `chat_questions` and names RQ05 (the team in the browser's app state) as `chat_reserve`. A reserve question goes on stage only after it has joined the must-pass list and passed its three live runs. The external-client moment asks RQ10's text again (`mcp_question`): the same question, the same role, another client. Its answers are not graded.
- **Tiers** (`demo/preflight`): the chat panel is part of tier `full` only. In tiers `B` and `C` the external MCP client is the whole of beat 5; in tier `D` there is no beat 5.
- **Bindings.** The `demo` dataset of `app/backend/chat/rehearsal/questions.yaml` is filled for the demo kit:
  - `team`: the bubble team of `demo/demo.yaml`;
  - `rival` and `whatif_game`: the bubble team's game in week N+2;
  - `whatif_week`: N+2; `prev_week`: N;
  - `history_team`: the first team of the stage pair; `history_season`: the previous season;
  - `poll`: `AP`; `sp_season`: the previous season.

  Names are bound; ids are looked up when a run starts (`chat/rehearsal`).
- **Bindings check:** `make chat-bindings-check [KIT=<kit>]`, which is also the preflight's `chat` check (`demo/preflight`).
  - It resolves every bound name and game against `CFB_API` at the kit's replay week:
    - each team in `CFB_API.TEAMS`;
    - the what-if game in `CFB_API.WHATIF_GAMES`;
    - the history season in `CFB_API.TEAM_SEASONS`.
  - It reads as the chat user and spends nothing.
- **Stub on the kit:** after the kit's replayed refresh, `CHAT_MODEL=stub` (`chat/availability`), with the rehearsed questions offered as suggestions, and `make chat-rehearse ARGS="--dataset demo --only RQ01,RQ04,RQ10,RQ15"`. The scripted answers take their numbers from the tool results, so they are the kit's numbers.
- **Demo-day caps.** Both app instances run with `CHAT_DAILY_MAX_USD=3.00` and Phase 7's `CHAT_SESSION_MAX_USD=2.00`, and share one spend file (`CHAT_SPEND_PATH`), so the daily cap covers both (`chat/budget`).
- **Recorded live transcript** (Phase 7's exit P8-c; built only once an Anthropic key exists):
  - **Recording:** `make chat-transcript KIT=<kit> CHAT=<chat id>[,<chat id>]` (`scripts/demo.py transcript`) reads the turn-log lines of those chats (`chat/turn-log`, `CHAT_LOG_PATH`) from a live run of the stage questions on the kit, in the panel or by `make chat-rehearse LIVE=yes`. It writes `~/.cfb-demo/kits/<kit>/chat-transcript.json`.
  - **Contents:** per question, the text, the app state, the tool calls in order (name and validated input), the app actions and the final answer text. It also holds the kit name, the SHA-256 of the kit's `kit.json` and the point the run saw.
  - **Refused turns:** a turn whose outcome is not `done`, or that was answered by the stub.
  - **Replay:** `CHAT_MODEL=transcript:<path>` is a model that feeds the transcript through the scripted-model seam of `chat/conversation`. The tools run again against the database, the answer text is the recorded one, and the turn log marks the turn `transcript`, with zero tokens and zero cost.
  - **Binding:** a transcript replays only on data that is provably its kit's. That holds when both are true:
    - the database is at the transcript's point, and the newest `RESTORE` run names the kit's snapshot;
    - every fetch of the newest `REFRESH_WEEK` run (`CFB_API.FETCHES`) has `REQ_SOURCE` `REPLAY`, and its slice key and request time equal a `refresh` recording of the kit's `kit.json`. A replayed fetch carries the recording's request time (`cfbd-fetch/fetch-modes`), so this proves the refresh read the kit's own recordings.

    The app checks this at start and again before each transcript turn. Otherwise the chat uses the stub and gives the reason in `/api/config`.
  - **Storage:** it holds no key and no secret, and it stays in the kit directory, never in the repository, because the answers quote stored data.
- **The chat on AWS** (App B, `demo/scale-dial`) uses the same questions and the same modes. It is the beat's fallback when the laptop's chat fails, and it runs live only (no kit on AWS).
- **External MCP client** (Phase 7's user-guide recipe; hand-off P8-b):
  - Claude Desktop on Windows, entry name `exasol-cfb`, not the starter kit's managed `exasol` entry;
  - the command is `wsl.exe`, with the arguments `-e`, the absolute path of `uvx` inside WSL, and `exasol-mcp-server@<pinned version>`. `wsl.exe -e` starts no login shell, so a bare `uvx` is not on its path. The entry's `WSLENV` lists the `EXA_*` variables with the `/u` flag, so that they reach the server inside WSL;
  - it connects as the chat user, with the role `CFB_MCP`, to `localhost:8563` inside WSL;
  - `EXA_MCP_SETTINGS` with `enable_read_query` true and the schema filter `CFB_API`;
  - on the local deployment only: the server validates TLS against a CA bundle or not at all, and on loopback `EXA_SSL_CERT_VALIDATION` is `false`.

  The owner sets it up; it is not tested here.
- Zero CFBD calls (`CFB_MCP` reads only `CFB_API`) and zero Anthropic calls in every scenario below. The live run that produces a transcript is an owner-approved action outside the tests.
- The examples use the chat test stack (`chat/availability`) on the kit `2026-w09-w10` of `demo/rehearsal-kit`, rehearsed to week 10, with `demo` bindings for the sim league's Brookvale and Ashford.

## Scenarios

### Scenario: Every bound name of the demo dataset resolves at the kit's point

* *GIVEN* the kit rehearsed to 2026 week 10 and `demo` bindings naming Brookvale, Ashford, the week-12 game Ashford against Brookvale and the season 2025
* *WHEN* the owner runs `make chat-bindings-check KIT=2026-w09-w10`
* *THEN* the check MUST pass, listing each binding with the id it resolved to
* *AND* with `team` bound to a school that is not in `CFB_API.TEAMS`, it MUST fail, naming the binding, the value and the view it searched
* *AND* the check MUST run as the chat user and the mock CFBD API MUST receive nothing

### Scenario: The stage questions pass in stub mode on the kit, offline

* *GIVEN* the kit rehearsed to 2026 week 10, with the fetch UDF's connection access revoked and no Anthropic key
* *WHEN* the stub rehearsal runs RQ01, RQ04, RQ10 and RQ15 of the `demo` dataset
* *THEN* each question MUST pass all checks of `chat/rehearsal`
* *AND* RQ01's answer MUST carry the conditional playoff odds and the effective sample that `what_if_odds_sql` returns on the kit at that moment
* *AND* no request MUST leave the process for an Anthropic endpoint or for the mock CFBD API

### Scenario: A recorded live conversation replays on its kit at no cost

* *GIVEN* fixture turn-log lines of a live run of RQ01, RQ04, RQ10 and RQ15 on the kit, all `done`, and the transcript `make chat-transcript` built from them
* *WHEN* the app runs with `CHAT_MODEL=transcript:<path>` after a replayed refresh of the kit, and the panel asks the four questions
* *THEN* each answer MUST be the recorded answer text, and each turn MUST run the recorded tool calls against the database and disclose their SQL
* *AND* the grader of `chat/rehearsal` MUST pass all four answers against the kit's data
* *AND* the turn log MUST mark the turns `transcript` with zero tokens and zero cost, and no Anthropic request MUST be made

### Scenario: A transcript is built only from finished live turns and replays only on its kit's replayed data

* *GIVEN* turn-log lines of one chat in which RQ04 ended `cancelled`, and the transcript of the previous scenario
* *WHEN* the owner builds a transcript from that chat, and the app starts with the earlier transcript while the database is at 2026 week 9 or another kit is installed
* *THEN* the build MUST be refused, naming RQ04 and its outcome, and no transcript file MUST be written
* *AND* the app MUST use the stub for the `demo` dataset, and `/api/config` MUST give the reason, naming the transcript's kit and point
* *AND* after a week-10 refresh of the right kit whose `GAMES` fetch was `LIVE`, the app MUST also use the stub, naming the live fetch as the reason
