# Decision Log: add-cfb-chat

Date: 2026-10-23 (planned)

## Interview

The owner (Nico) was away and authorized the orchestrator to decide and log. The answers below are the orchestrator's, as it passed them to the planner.

**Q:** What is the scope?
**A:** Roadmap Phase 7, "Conversational analyst", in `docs/roadmap.md`: a chat panel with Claude plus the Exasol MCP (`CFB_MCP`) over `CFB_API`, with app-state tools, SQL disclosure, and a rehearsed question set that doubles as a regression check. It serves demo beat 5: "the chat sets a what-if, explains a swing, answers a question outside the pages; shows its SQL".

**Q (1) Model access:** There is no Anthropic API key. How is it built and tested?
**A:** The owner will add a key later.
- Build everything so that it runs against a stubbed model in tests.
- The live model call is gated on `ANTHROPIC_API_KEY` from the environment or `.env`. The key is never committed and never sent to the frontend.
- Without the key, the panel shows a clear "chat disabled: no API key" state.
- Use the latest Claude model id. Take model ids, the tool-use loop, streaming and prompt caching from the `claude-api` skill, not from memory.

**Q (2) Data access:** What may the chat read, and through what?
**A:** It reads only `CFB_API`, as the role `CFB_MCP`, with no access to `CFB_API_LIVE`, so it can never spend CFBD calls (Phase 5 decision). Phase 5 added four pass-through views for questions outside the pages: `GAMES`, `TEAM_SEASONS`, `RANKINGS` and `SP_RATINGS`. Read `specs/_plans/add-cfb-analyses/` (the `api/*` specs, roles, decision log).

Decide between two options and justify the choice:
- **(a)** The backend runs the tool loop and calls Exasol through pyexasol as `CFB_MCP`, with a small set of tools: for example `run_sql` (read-only, `CFB_API` only, row and time limits) and `describe`.
- **(b)** The Exasol MCP server. The `exasol-mcp` and `local-agent-ready-starter` skills describe its read-only MCP user.

Either way, every answer shows the exact SQL it ran.

**Q (3) App-state tools:** What does "the chat sets a what-if" mean?
**A:** Tools that change the app's UI state: set the what-if toggles, select a team or a page, set the as-of point.
- Phase 6 reserved a chat slot: an app-state store, a reserved region, and the path `/api/chat/`. Read `specs/_plans/add-cfb-app/`, which is being revised now, including its `review.md` resolution where present.
- These tools must not write to the database. What-ifs are the generated `CFB_API` what-if views plus the SQL of `whatif.py` (Phase 5).

**Q (4) Rehearsed question set:** How does it double as a regression check?
**A:** About 10–15 demo questions, each with its expected facts or SQL shape.
- They run against the stub model, deterministically, in CI.
- Optionally they run against the live model, gated on the key and a cost cap.
- Define the grading: the SQL touches the expected views, and the result contains the expected values.

**Q (5) Cost and safety:**
**A:**
- A token cap and a cost cap per session.
- Tool results are data, not instructions (prompt-injection hygiene; CFBD text fields reach the model).
- No secrets in prompts.
- Log every turn: the question, the SQL, the rows and the tokens.

**Q (6) Targets and tests:**
**A:** Local and AWS, as for the app. Tests follow the app's tiers:
- unit tests;
- API integration tests on the `TEST_` namespace, with the harness and synthetic data;
- frontend component tests.

**Q (context):** Which constraints apply to planning?
**A:**
- No database sessions, no CFBD calls and no Anthropic calls; there is no key.
- `docs/roadmap.md`, `specs/architecture.md` and `CLAUDE.md` change only through tasks.
- Load `exasol:exasol-database` and `exasol-mcp` before making Exasol or MCP claims (the owner's rule), and `claude-api` before making Claude API claims.

## Design Decisions

### [1] The tool loop runs in the backend over pyexasol as the `CFB_MCP` chat user; the Exasol MCP server is not in the app's path

- **Decision:** option (a).
  - The FastAPI backend runs a manual tool loop against the Claude API with the Anthropic Python SDK.
  - It executes the chat's tools on its own pyexasol pool as the user `CFB_CHAT_WEB`, whose only role is `CFB_MCP` (`SELECT` on `CFB_API`).
  - The role is the boundary.
- **Alternatives:**
  - **(b1) The Claude API's MCP connector to an Exasol MCP server.** The connector reaches only servers at a URL that Anthropic's side can call (`mcp_servers` with `type: url`, claude-api skill). That means exposing a database-backed MCP endpoint of the presenter's laptop to the internet, and the chat could not work offline.
  - **(b2) The backend as an MCP client of a local `exasol-mcp-server` (2.2.0) over stdio.** Rejected for four reasons:
    - `execute_exasol_query` returns every row of a result as a list of dicts, with no cap (context7 `/exasol/mcp-server`). One read of `WHATIF_OUTCOMES` (up to 1M rows on AWS) could cost more than a demo.
    - The server masks engine errors as "A database error occurred. Please try again later…" (skill `exasol-mcp`), so the model cannot correct a wrong column name.
    - TLS is checked by CA bundle or switched off, with no fingerprint pinning, while the AWS target pins the fingerprint (`vs/host/target.py`).
    - It adds a subprocess, a runtime download, and about 20 generic tools (UDFs, BucketFS, preprocessors) that would have to be filtered.
- **Rationale:**
  - Exact SQL disclosure, row and time limits, caps between steps, real error messages, and one deterministic seam for the scripted model.
  - The read-only and no-CFBD guarantees come from the role in both designs, so (a) loses none of them.
  - The roadmap's "Exasol MCP" message survives through [2].
- **Promotes to ADR:** yes

### [2] The role `CFB_MCP` keeps its name and also serves external MCP clients, documented in the user guide

- **Decision:**
  - `CFB_MCP` (Phase 5) stays the chat's role.
  - The user guide shows how to point `exasol-mcp-server` (`EXA_DSN`, `EXA_USER=CFB_CHAT_WEB`, `EXA_PASSWORD`, `EXA_MCP_SETTINGS` with `enable_read_query` and a `CFB_API` schema filter) at the same user, for Claude Desktop or Claude Code. It works on local only, because of TLS.
- **Alternatives:** rename the role (breaks Phase 5's contract); drop MCP from the story; ship the MCP setup as code.
- **Rationale:** one boundary, two clients. "The same read-only role powers the in-app chat and an MCP client" can be shown without the app depending on the server's current limits.
- **Promotes to ADR:** no

### [3] A manual tool loop instead of the SDK's tool runner

- **Decision:** `chat/loop.py` owns the loop. The seam for the model is `ChatModel.respond(request, on_text)`, below the loop.
- **Alternatives:** `client.beta.messages.tool_runner` with its per-turn hooks, which is the claude-api skill's default recommendation.
- **Rationale:**
  - **Work between steps.** Budget pre-checks, cancel checks, event emission, the step and tool limits, and closing unanswered tool calls on every kind of stop all run between steps. The skill names "control flow interleaving other work" as the reason for a manual loop.
  - **Testability.** With the seam below the loop, every loop rule is tested with the scripted model without the SDK. The SDK layer is tested on its own (decision [6]).
  - **Size.** The loop is about 80 lines.
- **Promotes to ADR:** no

### [4] Request settings

- **Decision:**
  - model `claude-opus-5-5`, `max_tokens` 16,000;
  - `thinking` omitted (always adaptive on this model);
  - `output_config.effort` `medium`, sent explicitly;
  - `tool_choice` `auto`;
  - top-level automatic caching;
  - streaming with `eager_input_streaming: true` on every tool, with pydantic validation of every input before it runs;
  - `fallbacks: "default"` with the beta `server-side-fallback-2026-07-01`.
- **Alternatives:**
  - `high` effort (more thinking, more latency and cost on stage; measured in task 7.2);
  - non-streaming (the answer would appear in one piece after 10–20 s);
  - no fallbacks (a classifier refusal would end the answer);
  - `strict: true` tools without streaming.
- **Rationale:**
  - Every value follows the claude-api skill for this model:
    - the default model;
    - effort defaults to `medium` and should be set explicitly;
    - forced `tool_choice` is a 400;
    - eager input streaming plus client validation for streamed client tools;
    - fallbacks on by default for `claude-opus-5-5` code.
  - A fallback runs without Opus 5.5's thinking blocks and is billed at its own model's prices, so `chat/budget` prices each `usage.iterations` entry by its model.
  - The owner is told the fallback is on and can set `CHAT_FALLBACKS=off`.
- **Promotes to ADR:** no

### [5] Only an explicit `ANTHROPIC_API_KEY` turns the chat on; the client is never built without it

- **Decision:**
  - The backend reads `ANTHROPIC_API_KEY` (environment, then `.env`) and builds `anthropic.Anthropic(api_key=…)` only when it is set.
  - It never calls the zero-argument constructor.
- **Alternatives:** the SDK's default credential chain (`ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, an `ant auth login` profile, workload identity variables).
- **Rationale:**
  - The interview gates the chat on that variable.
  - A developer's `ant` profile or a stray token must not switch the chat on, or bill an account the owner did not intend, on a demo machine.
- **Promotes to ADR:** yes

### [6] Two test seams: a scripted model, and an in-process HTTP transport for the SDK layer

- **Decision:**
  - **`ScriptedChatModel`** (`chat/scripted.py`) drives every loop, tool, budget, app-state and rehearsal test, and stub mode. It plays scripted steps with fixed usage.
  - **The SDK request.** `tests/unit/test_chat_model.py` runs the real SDK client over an in-process transport (`httpx2.MockTransport` through `DefaultHttpxClient`). It pins the request body and checks that the key travels only in the `x-api-key` header.
- **Alternatives:**
  - a loopback fake API server for everything (slower; socket I/O in unit tests);
  - only Python fakes (the real request shape would go untested until a key exists).
- **Rationale:**
  - Determinism, zero cost, and no network.
  - The marker key is never valid, so a misrouted test fails instead of spending.
  - Gate 1.1 checks that the SDK accepts the transport; the fallback is a loopback server.
- **Promotes to ADR:** no

### [7] Append-only conversations; the app state reaches the model as a system message after each question

- **Decision:**
  - Within a conversation the system prompt and the tools are byte-identical in every request.
  - Each request extends the previous one with the previous response, unchanged and with its thinking blocks.
  - The browser's validated app state follows each question as a mid-conversation `role: system` message.
  - Stops close every unanswered tool call with an error result.
- **Alternatives:**
  - rewrite the system prompt with the state each turn;
  - prepend the state to the user's text;
  - a `get_app_state` tool;
  - trim old tool results.
- **Rationale:**
  - **Preserved thinking.** On `claude-opus-5-5` it rejects a replayed thinking block after any edit of the system prompt, the tools or an earlier message. This is enforced by default for accounts created from 2026-08-31, which the owner's new key will likely be. The same rule keeps the prompt cache valid.
  - **Placement.** The system role is the non-spoofable operator channel. Placed after the question, never after tool results, it is not mistaken for an injection (claude-api skill).
  - **Cost.** A `get_app_state` tool would cost a round trip per question.
- **Promotes to ADR:** yes

### [8] A dedicated chat user with a positive privilege check; a refused user disables the chat, not the app

- **Decision:**
  - `make chat-user` creates `CFB_CHAT_WEB` with `CREATE SESSION` and `CFB_MCP`.
  - At start, Phase 6's read-user check (revised per review M2; hand-off P7-c) must find exactly that and `SELECT` on `CFB_API`, and nothing else.
  - Otherwise the chat is disabled with the reason, and the pages work.
- **Alternatives:**
  - reuse the app's read user `CFB_APP_WEB`. Its role reads `CFB_API_LIVE`, which spends CFBD calls.
  - refuse the server's start, as Phase 6 does for its read user.
- **Rationale:**
  - The no-CFBD guarantee must hold by grant.
  - The mission's dependency table says "Chat disabled, core app unaffected".
- **Promotes to ADR:** no

### [9] `run_sql` runs verbatim, behind static typo-catchers, a 201-row fetch and a session timeout

- **Decision:**
  - **Static checks:** one statement; `SELECT` or `WITH` first; no `INTO TABLE` (grammar `select_into_statement_1`: such a `SELECT` creates a table); no reference to the live schema; at most 20,000 characters.
  - Then the text runs as written, except that one trailing `;` is removed.
  - At most 201 rows are fetched and 200 returned.
  - `QUERY_TIMEOUT` is 20 s per chat session.
- **Alternatives:**
  - wrap the statement in `SELECT * FROM (…) LIMIT 200` (the disclosed SQL would no longer be the model's);
  - a full SQL parser (more code to be wrong in);
  - no free SQL (questions outside the pages need it);
  - a consumer group for the timeout (kept as the fallback if Phase 6's probe finds the session parameter unusable).
- **Rationale:**
  - The role is the boundary. The checks give the model clear messages and keep refused statements away from the database.
  - Disclosure stays exact.
- **Promotes to ADR:** no

### [10] Eight tools: four data tools, four app-state tools; what-ifs through `analyses/whatif.py`

- **Decision:**
  - Data tools: `run_sql`, `describe_view`, `whatif_odds` and `rooting_guide`.
  - App-state tools: `set_whatif`, `clear_whatif`, `show_page` and `set_as_of`.
  - The what-if and guide tools call Phase 5's library for the what-if run of `CURRENT_POINT`.
- **Alternatives:**
  - only `run_sql` (the model would rebuild the conditional-odds SQL and its run guard each time);
  - a tool per view;
  - one combined set-and-read what-if tool.
- **Rationale:**
  - The library's validation, run guard and method label come for free, and the chat's numbers equal the app's.
  - The set stays small, as the skill advises.
  - Separating "show it" from "compute it" keeps app-state tools free of data reads beyond validation.
- **Promotes to ADR:** no

### [11] App-state changes are validated on the server, applied by the panel like clicks, and undoable

- **Decision:**
  - The tools run the pages' own rules through `SELECT` lookups: the what-if run's games while its views are current, the what-if point compared by season and week sequence (Phase 6 review m14), teams in `TEAMS`, and points without `IS_AFTER_CURRENT`.
  - They emit `app_action` events with the state before and after.
  - The panel dispatches them to Phase 6's store and shows a chip with Undo.
  - `set_whatif` moves the app to the odds page at the what-if point when needed, and says so.
- **Alternatives:**
  - the backend pushes state to the browser;
  - the model writes the URL;
  - changes without Undo.
- **Rationale:**
  - The store stays the single source of truth (Phase 6 [19]), and the model can ask only for what a click could do.
  - A wrong change costs one click on stage.
  - No write path exists in the chat code.
- **Promotes to ADR:** no

### [12] Every statement on the chat connection is disclosed, exactly as sent, and also enters the inspector

- **Decision:**
  - The pool records pyexasol's statement text, rows, milliseconds and error for every statement of a turn: the model's SQL, the library's SQL, and validation lookups. Statements refused by the checks are recorded too, marked "not run".
  - The answer lists them; the panel shows "SQL (n)" with copy.
  - The inspector's query log gains them with the principal `chat` (hand-off P7-d).
- **Alternatives:** disclose only `run_sql`; reconstruct SQL from tool inputs.
- **Rationale:**
  - "Every answer shows the exact SQL it ran" holds by construction, with the same "text the database received" rule as Phase 6's inspector.
- **Promotes to ADR:** no

### [13] Turns run in the background, and the panel polls their events

- **Decision:** `POST /api/chat/turns` returns 202 with a turn id. A thread runs the loop and appends numbered events. The panel polls `GET /api/chat/turns/{id}?after=n` every 400 ms. At most 2 turns run per process.
- **Alternatives:** server-sent events; a WebSocket; a synchronous request that holds for the whole answer.
- **Rationale:**
  - This is Phase 6's "follow, don't push" pattern (the refresh timeline): one worker, no proxy configuration, simple tests with `TestClient`.
  - Streamed text arrives in the events with at most 400 ms of delay.
- **Promotes to ADR:** no

### [14] Budget: a worst-case check before each call, caps per conversation, day and turn, and a pinned price table

- **Decision:**
  - **Prices.** Every call is priced from `usage`, per `iterations` entry when the fallback ran. Opus 5.5: $4 / $20, cache write $5, cache read $0.20 per MTok; prices are re-checked against the pricing page in task 1.3.
  - **Pre-check.** Before a call, spent plus its worst case (input estimate at the full input price, plus `max_tokens` at the output price) must stay within the caps.
  - **Proposed defaults:** $2.00 and 500,000 tokens per conversation, $20.00 per UTC day (read back from the turn log at start), 8 model calls and 16 tool calls per turn.
  - Live rehearsals need an explicit `MAX_USD`.
- **Alternatives:**
  - caps checked after each call (they can be passed by a whole call);
  - the Usage and Cost Admin API (needs an admin key);
  - no daily cap.
- **Rationale:**
  - A cap is never passed by more than the estimate's error.
  - Spend is as explicit as the CFBD ledger makes calls.
  - The defaults are re-set from task 7.2's measurements and are the owner's to change (U3).
- **Promotes to ADR:** no

### [15] The rehearsed set: 15 questions graded by rules, with oracles computed at grade time; stub in CI, live behind `MAX_USD`

- **Decision:**
  - **The set.** `questions.yaml` holds 15 questions in five conversations:
    - a what-if set from the chat;
    - clear and rooting guide;
    - navigation;
    - two swings and an as-of move;
    - three page facts;
    - four questions outside the pages;
    - two guardrails (no live CFBD; no writes).
  - **Extra.** An adversarial question runs only live on the test namespace.
  - **Contents per question.** Expected views, an oracle statement with the values that must appear in the results and the answer, expected app actions, forbidden objects, and a script for the scripted model.
  - **The six checks:** views, results, answer, app changes, forbidden, ending and cap.
  - **Pass bars.**
    - Stub run: 15 of 15, in `make test-it`.
    - Live on the demo dataset: at least 13 of 15, with RQ01, RQ14 and RQ15 passing.
- **Alternatives:**
  - an LLM judge (costs money, is not deterministic, and grades itself);
  - fixed expected numbers (break on every weekly refresh of real data);
  - free-text expectations.
- **Rationale:**
  - The checks are free and explain each failure.
  - The same file runs on the sim league and on real data.
  - The stub run turns every Phase 5 contract change (a renamed view or column) into a failing test.
  - The scripts double as stub mode for the panel ([19]) and as Phase 8's beat-5 script.
- **Promotes to ADR:** yes

### [16] Injection hygiene by structure, not by filtering

- **Decision:**
  - Tool results are JSON envelopes: values as strings and numbers in `rows`, text cut at 500 characters, and a fixed data note.
  - The system prompt says that only it and the user instruct, and that the rules hold for the whole conversation (the skill's wording).
  - There are no egress tools.
  - The panel renders plain text, and Phase 6's CSP still applies.
  - Questions or app state containing a held secret are refused before the model.
- **Alternatives:** pattern filters for "ignore previous instructions" (bypassable); trusting the model alone.
- **Rationale:**
  - The worst a successful injection can do is propose an app change that the presenter sees and can undo.
  - It cannot write, spend CFBD calls, exfiltrate (nothing to send and nowhere to send it) or read beyond `CFB_API`.
- **Promotes to ADR:** no

### [17] A JSONL turn log in `cache/`, with redaction

- **Decision:**
  - `cache/chat-log.jsonl` gets one line per turn, whatever the outcome.
  - Each line holds the question, the app state, the calls with usage and cost, the tools with exact SQL, row counts and the first 20 rows, the app actions, the answer and the outcome.
  - Held secrets are redacted as a second line of defence.
  - A failed write is a warning, not a failed turn.
- **Alternatives:** a database table (the chat role cannot write, and the owner path should not grow for this); no rows in the log.
- **Rationale:**
  - The interview asks for the question, the SQL, the rows and the tokens.
  - `cache/` is git-ignored and local, as CFBD's terms require for raw-ish data.
  - The daily cap reads the log back.
- **Promotes to ADR:** no

### [18] `anthropic` 1.x, not 2.x

- **Decision:** `anthropic>=1.15,<2` in a new group `chat`. Task 1.1 takes the newest 1.x that is at least 7 days old.
- **Alternatives:** 2.1.0 (released 2026-10-21, one day before planning).
- **Rationale:** Phase 6's version policy; the claude-api skill documents the 1.x surface (`httpx2`, `DefaultHttpxClient`); the 2.0 migration is unreviewed. Moving to 2.x goes on the backlog.
- **Promotes to ADR:** no

### [19] Stub mode: `CHAT_MODEL=stub` serves the rehearsed scripts in the panel, without a key

- **Decision:**
  - The panel works with the scripted model against the live database, with a "stub model" badge.
  - Questions are matched to rehearsal scripts by their exact bound text; any other question gets a fixed "only the rehearsed questions" answer.
- **Alternatives:** nothing until a key exists.
- **Rationale:**
  - The owner and Phase 8 can rehearse beat 5's flow, the disclosure and the app changes before a key exists.
  - It is the offline fallback for beat 5.
- **Promotes to ADR:** no

### [20] The panel renders disabled with a reason; on a network bind the chat is off unless `APP_CHAT=on`

- **Decision:**
  - `GET /api/config` reports `chat: {available, enabled, reason, model}`.
  - The panel renders whenever `available` is true. A disabled panel shows its reason, for example "Chat disabled: no API key", and has no input.
  - With `APP_CHAT=auto`, the chat is enabled only on a loopback bind.
- **Alternatives:**
  - hide the panel when disabled, which is Phase 6's current slot rule (superseded; recorder note);
  - enable it on any bind.
- **Rationale:**
  - A missing key should look intentional on stage.
  - Every question spends money, so Phase 6's loopback principle for the operator path applies. The stub costs nothing and is exempt.
- **Promotes to ADR:** no

### [21] Live verification: at most $20 of Anthropic spend and 0 CFBD calls

- **Decision:** after U2 (the key), task 7.2 runs:
  - a one-question smoke: model id, fallback accepted, cache reads, no preserved-thinking 400;
  - the live rehearsal on the test namespace at `medium` with the adversarial question;
  - the failures again at `high`;
  - the demo dataset.

  Task 7.3 then runs beat 5 by hand, locally and on AWS. Costs are summed from the turn log and the reports.
- **Alternatives:** no live check before Phase 8; a full sweep of effort levels.
- **Rationale:** each API assumption ([4], [7]) needs one cheap proof before the demo. The caps' defaults need real numbers.
- **Promotes to ADR:** no

## Assumptions to verify

| # | Assumption | Verify step |
|---|---|---|
| A1 | Phase 6 lands the store, sessions, the query log, the security middleware (with review M1's `Host` and `Origin` fixes) and the read-user check (review M2) as its revised plan describes | Before group C: re-read `specs/_plans/add-cfb-app/` and its review resolution; confirm hand-offs P7-a to P7-e; adjust tasks 3.x if a name moved |
| A2 | `claude-opus-5-5` and its prices ($4 / $20, cache read $0.20, cache write 1.25 ×) as cached in the claude-api skill on 2026-10-06 | Task 1.3: WebFetch the pricing page from the skill's `live-sources.md`; task 7.2: the served model id and `usage` of the smoke |
| A3 | `fallbacks: "default"` with the beta header works on the streaming beta endpoint together with client tools and `eager_input_streaming` | Task 1.6 pins the request body; task 7.2's smoke shows it accepted; else set `CHAT_FALLBACKS=off` and log it |
| A4 | `DefaultHttpxClient(transport=httpx2.MockTransport(…))` is accepted by the 1.x SDK | Gate of task 1.1; fallback: a loopback fake server in the same unit test |
| A5 | `anthropic` 1.x stays the right major for the demo | Task 1.1 applies the 7-day rule; the 2.x changelog is reviewed before Phase 8 (backlog) |
| A6 | `ALTER SESSION SET QUERY_TIMEOUT = 20` bounds a chat statement | Phase 6 probe 1.1(e); scenario "A slow statement is stopped by the time limit"; fallback: a consumer group with `QUERY_TIMEOUT` assigned by `make chat-user` |
| A7 | Fetching 201 rows of a large result (for example `WHATIF_OUTCOMES` at 1M rows on AWS) is fast enough and frees the result on close | Task 3.1 times it locally at 100k; task 7.3 once on AWS; if slow, the system prompt's "aggregate in SQL" stays the mitigation, and the 20 s timeout bounds it |
| A8 | `CFB_API.GAMES` carries a free-text column (CFBD `notes`) for the adversarial question | Read `docs/api-dictionary.md` after Phase 5; fallback: a synthetic historic team name in the history variant |
| A9 | The owner's key belongs to an account that enforces preserved thinking (created after 2026-08-31), so append-only history is required, not just recommended | Task 7.2's smoke asks two questions with tool calls; a "bound to a different conversation" 400 would show an edit. The design is append-only either way |
| A10 | A one- or two-tool answer at `medium` takes under 20 s | Task 7.2 measures the latencies; if too slow, `low` effort for navigation-only turns (per-message effort, backlog) or a shorter prompt |
| A11 | The cap defaults ($2 per conversation, 500k tokens, $20 per day, $5 per live rehearsal) fit a demo | Task 7.2 measures the costs and re-sets the defaults; the owner may override (U3) |
| A12 | The owner accepts that the in-app chat does not use the Exasol MCP server ([1]), with the server documented for external clients ([2]) | Owner review (U3); non-blocking. If rejected, (b2) can replace `toolrun.py` behind the same tools, keeping disclosure and caps, once the server offers a row cap and error passthrough |

## Review Findings

<!-- Populated by speq-implement after code review. -->
