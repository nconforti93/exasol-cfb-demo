# Review: add-cfb-chat (Phase 7, Conversational analyst)

Reviewer: independent agent, 2026-10-23. Read only: `plan.md`, `decision-log.md`, all nine `chat/*/spec.md`; checked against `specs/mission.md`, `docs/roadmap.md` (Phase 7, demo beat 5), `specs/_plans/add-cfb-analyses/` (roles, serving contract), `specs/_plans/add-cfb-app/` (plan, `app/shell`, `review.md` incl. the P7 cross-plan items) and `ORCHESTRATOR-STATUS.md`. Skills loaded first: `claude-api` (SKILL, `python/claude-api/README.md`, `shared/model-migration.md` § Opus 5.5 / § Fable 5.1 refusal + preserved thinking, `shared/prompt-caching.md`, `shared/tool-use-concepts.md`), `exasol:exasol-database` (grammar), `exasol-mcp`. No DB session, no CFBD call, no Anthropic call. `speq plan validate add-cfb-chat` passes (9 delta specs; 57 scenarios, all mapped).

## Verdict: approve with changes

The design is sound where it matters most: the database role is the real boundary and it holds (Phase 5 [33] keeps every network-reaching view out of `CFB_API`, and Phase 5's serving-contract test reads `EXA_ALL_DEPENDENCIES`). The seam under the loop makes every rule testable with no key. The Claude API request shape matches the skill almost line for line. No blockers.

Ten major findings need fixing before implementation starts (most land in tasks 1.3, 1.5, 1.6, 2.1 and 1.7, and none changes the architecture):

1. **History validity beyond tool calls (M1).** Several kinds of stop leave a conversation that the API will reject on the next question. Examples: a trailing system message, an empty assistant message, and the rules for echoing fallback turns.
2. **Preserved thinking is not opted into explicitly (M2).** The skill's Opus 5.5 checklist marks this `[BLOCKS]`, and assumption A9 cannot be verified the way the plan says.
3. **Results are capped by rows, not by size (M3).** 200 wide rows can cost more than a whole question.
4. **The cost pre-check is not a worst case (M4).** It misses the fallback chain, cache-write pricing and concurrent turns. The daily spend is persisted only on a best-effort basis.
5. **The token cap's default would likely end the demo conversation (M5).**
6. **An explicit `api_key` alone does not pin where the key goes (M6).**
7. **Cancel cannot interrupt a model call, and a turn has no deadline (M7).**
8. **Decision [1] is right for the app's path, but one argument is wrong and the product call is the owner's (M8).** Task 6.4 must not rewrite the mission's "Exasol MCP" wording before the owner has signed off.
9. **The live pass bar does not protect beat 5 (M9).** RQ04, the swing question, is not on the must-pass list, and a single run says little about reliability on stage.
10. **The Phase 6 hand-offs exist only in Phase 6's review appendix (M10).** Its plan and specs still say `chat: false`, the store has no pair, and the chat-slot test sits on Phase 6's cut line.

---

## 1. Correctness of the tool loop and the conversation

### M1 (major): the history check covers tool calls only; other stops leave an invalid conversation

**Evidence**
- **Placement of the system message.** A mid-conversation `role: system` message "must be either the last entry in `messages` or be followed by an `assistant` turn" (claude-api `README.md` and `prompt-caching.md`). `run_turn` appends `user(question)` and then `system(state)` before the first call (`plan.md` Loop). Several stops can come before any assistant message is appended:
  - the budget pre-check refuses step 1 ("A conversation stops before the call…", and the daily cap);
  - cancel before step 1;
  - an API failure after retries ("A failing model API fails the turn");
  - a `ValueError` re-issue that also fails.

  In each case the conversation ends in `system(state)`. The next question appends `user(question2)`, so the system message is now followed by a user message, and every later request in that conversation gets a 400. `close_tool_calls` does not help, because no tool call is open.
- **Empty content on refusal.** A pre-output refusal returns "an empty `content` array" (`model-migration.md` § refusal). The loop appends `assistant(resp.content)` unconditionally before it matches `refusal`, which gives an assistant message with no content.
- **Echoing fallback turns.** With `fallbacks: "default"`, after a mid-output fallback the client must "omit `thinking`, `redacted_thinking`, and `tool_use` blocks … that appear *before* the final `fallback` block" when echoing the turn back (`model-migration.md` § refusal, "Echoing fallback turns back"). The plan appends the content "unchanged, thinking blocks included". By the same rule, a `tool_use` before the boundary must not be run. `resp.tool_calls` as written would run it.
- **Partial streamed text.** A mid-stream refusal bills "already-streamed output" and says to "discard the partial output" (same section). `on_text` has already pushed it to the panel, and no scenario retracts it.
- **No default branch.** The `match` has no `else`. Any other `stop_reason` falls through to the next step with no result appended.
- **Calls beyond the tool limit.** A response with more `tool_use` blocks than the tools left under `CHAT_TURN_MAX_TOOLS` must still answer every block. The plan runs up to the limit and is silent on the rest.
- **What the tests check.** The test "history check after every kind of stop" (task 2.1) is defined as "every tool call answered in the message that follows it", so it would pass every case above.

**Fix** (task 2.1, `chat/conversation` Background, `test_chat_loop.py`)
1. **Commit the question and its state atomically with the first assistant message.** Hold `user(question)` and `system(state)` as pending. If the turn ends before any assistant content exists, drop them from the conversation and keep them only in the transcript and the turn log. Nothing the model produced is bound to them, so dropping them is not a preserved-thinking edit.
2. **Never append an assistant message with empty content.** A pre-output refusal is recorded in the transcript only.
3. **Echo fallback turns by the skill's rule.**
   - Drop `thinking`, `redacted_thinking` and `tool_use` blocks before the last `fallback` block.
   - Run only the `tool_use` blocks after it.
   - Keep or drop the `fallback` marker; keep everything after the boundary as it is.
   - New scenario with a scripted mid-stream fallback.
4. **Retract partial text.** A new event, `text_reset`, tells the panel to drop the streamed text on a refusal or a cut-off. The `refusal` scenario asserts it.
5. **Add a default branch** for any other `stop_reason`: close the open tool calls and end the turn `failed`.
6. **Answer every `tool_use` beyond the limit** with "turn stopped: tool limit", in the same user message.
7. **Put the next question in the same user message as closing tool results.** When a stopped turn ended with closing `tool_result`s, the next question becomes a text block after them in that same user message, which has not been sent yet. This is the skill's form for user input after tool results (`model-migration.md`, "Mid-turn user input: deliver it as a user turn"). It also avoids two user messages in a row.
8. **Turn the history check into an API-conformance validator** that the scripted model runs on every request it receives. It checks:
   - roles and placement (system messages, no empty content, `tool_result`s directly answering the previous turn's `tool_use`s);
   - byte-identical `system` and `tools`;
   - the earlier messages unchanged.

   Every loop, integration and rehearsal test then exercises it.

### M2 (major): set `prefix_mismatch_behavior` explicitly; A9's verification step cannot work as written

**Evidence**
- The Opus 5.5 checklist is explicit: "**[BLOCKS]** If the harness builds `messages` itself: … set `prefix_mismatch_behavior` explicitly under `thinking-binding-controls-2026-08-01`" (`model-migration.md` § Claude Opus 5.5 Migration Checklist).
- The plan never sends it ([4], Model request table).
- **On an account created before 2026-08-31,** the API only *records* a mismatch, and "acts on it only when the request sets `thinking.block_binding.prefix_mismatch_behavior`". An edit bug then passes silently, and A9's "a 400 would show an edit" sees nothing.
- **On an enforced account,** the same bug is a 400 on stage.

**Fix** (tasks 1.6, 7.2; [4]; A9)
- **Rehearsal runs and the 7.2 smoke:** send `betas` + `thinking-binding-controls-2026-08-01` with `thinking: {type: "adaptive", block_binding: {prefix_mismatch_behavior: "error"}}`. An edit then fails the run on any account.
- **The panel:** use `"drop_block"`, so the demo degrades instead of failing.
- **Both:** log `input_transformations` (it arrives on `message_start`) in the turn log. A `prefix_binding_mismatch` entry raises a warning; a `model_binding_mismatch` entry is expected after a fallback.
- **Confirm in task 1.6** that `"thinking": {"type": "adaptive", ...}` is accepted alongside the plan's "thinking omitted". The skill says the two are equivalent, and `block_binding` needs the object.
- **Rewrite A9:** "verified by the explicit `error` setting, on any account".

### M7 (major): cancel works only between steps; a turn can hang for minutes

**Evidence**
- "Cancel takes effect between steps" (`chat/conversation`).
- The client uses `timeout=120, max_retries=2`, and per the skill "wall-clock can reach `timeout × (max_retries+1)`". That is up to 6 minutes per model call, plus up to 20 s per statement.
- Stop pressed on stage during a slow stream waits for that.
- The scenario cancels only during a scripted wait between steps.

**Fix**
- **Cancel closes the stream.** The cancel endpoint closes the open SDK stream from the request thread (the stream context's `close()`) and discards the partial. The rule from M1 then applies: no assistant message, and the pending question is dropped.
- **Cancel also stops the database.** A running statement is aborted through pyexasol's abort call on that session.
- **A per-turn deadline** (`CHAT_TURN_MAX_SECONDS`, for example 90 s) ends the turn `stopped` with the same closing logic.
- **Lower `max_retries` for a live audience,** to 1 for example.
- **New scenario:** "Cancel during a streaming response ends the turn within 1 s, and the next question in the conversation succeeds."

---

## 2. Cost control

### M3 (major): cap tool results by size, not only by rows

**Evidence**
- `run_sql` caps at 200 rows. Phase 5's S4 layout stores what-if outcomes as one BOOLEAN column per game (`ORCHESTRATOR-STATUS.md`, "Layout C"), so `SELECT * FROM "WHATIF_OUTCOMES"` returns 200 rows by many columns.
- The plan's own live manual check asks exactly that ("Show me every simulated season of the what-if run").
- Text cells may be up to 500 characters each.
- An envelope of tens of thousands of tokens is then appended permanently to an append-only history, and re-read (cached) on every later call. That counts against the token cap (M5) and inflates every later worst case.

**Fix** (tasks 1.5, 3.2; `chat/data-tools`)
- **A serialized-size cap** on every result envelope (`CHAT_RESULT_MAX_CHARS`, for example 20,000), applied after the row cap. Cut rows to fit, and report `truncated: true` with `truncated_by: "rows" | "size"`.
- **A column cap** (for example 40), with the dropped column names listed.
- The disclosed SQL stays as written.
- **Scenario "A large result is capped"** adds the size case and asserts the envelope length.

### M4 (major): the pre-check is not a worst case, and the daily spend can be lost

**Evidence and fixes**
- **The fallback chain.**
  - With `fallbacks: "default"`, one call can bill a mid-stream decline at normal rates *and* the fallback attempt. The fallback runs at its own, higher rates (Opus 5: $25 output, $6.25 cache write) and on a cold cache, because "prompt caches are per-model" (`model-migration.md` § refusal, Billing).
  - The worst case "max_tokens at the output price" can therefore be passed by more than 2×, so the claim "a cap is never passed by more than the estimate's error" ([14]) is false.
  - **Fix:** when fallbacks are on, worst case = primary (input + `max_tokens`) + one fallback hop priced at the highest row, with the input at the cache-write price.
- **Input price.** The new suffix is written to cache at 1.25 × input under automatic caching, so the "full input price" in the pre-check is too low. **Fix:** price the uncached part at the cache-write rate.
- **Concurrent turns.** Up to 2 turns run at once. Both pre-checks read the same daily spend, so the daily cap can be passed by a whole worst case. **Fix:** reserve the worst case under a lock before the call, and settle the reservation from `usage` afterwards. Add a unit test with two threads.
- **Persistence of the daily spend.** The daily cap "survives restarts" by reading the turn log, which is written only at turn end, and a failed write is just a warning ([17]). A crash mid-turn, or an unwritable log, loses the spend.
  - **Fix:** append one spend line per model call, synchronously, to the turn log or a small `cache/chat-spend.jsonl`, before the next call.
  - If that write fails, live calls stop (fail closed) with the reason `spend_log_unwritable`. Stub mode is unaffected.
  - The "failing log" scenario then asserts that the *turn-log* write may fail without failing the turn, but the spend write may not.
- **The `ValueError` re-issue** (eager input streaming) is a full extra call. It must pass the pre-check and count as a step (task 1.6 and the loop).
- **Rehearsal spend.** Rehearsal CLIs spend outside the server's daily sum. State this in the user guide, or have the server re-read the spend file before each call (it is cheap).

### M5 (major): the 500k-token default would likely stop the demo conversation; define what the token cap measures

**Evidence**
- Tokens of a call = input + cache-creation + cache-read + output (`chat/budget`). Every call therefore re-counts the whole prefix, cache reads included.
- Under preserved thinking, earlier thinking stays in context and counts as input. Only *dropped* blocks are "not counted toward `input_tokens`" (`model-migration.md`, breaking change 2).
- **Rough arithmetic with the plan's own estimates:**
  - the inputs: a 10–15k-token frozen prefix, 2–3 calls per question, and 1–4k output tokens per call;
  - call *n* reads about 12k + 3.5k·*n* tokens;
  - 6 questions are about 15 calls, so Σ ≈ 180k + 420k ≈ 600k tokens before output;
  - the pre-check adds `max_tokens` (16k) plus the input estimate, so the conversation stops around 440k spent: within the 4th–5th question, while the dollar cap ($2) still has room.
- A11 defers this to 7.2, but the stub path and 7.1 run on the defaults.

**Fix** ([14], `chat/budget`)
- **Redefine the cap** as a context-size guard: `CHAT_CALL_MAX_INPUT_TOKENS`, for example 150k, applied to the next call's input estimate.
- **Optionally** add a conversation cap on *uncached* tokens (input + cache-creation + output).
- **Keep dollars** as the money control. The interview's "token cap" is still met, and it now protects context size instead of re-billing cheap cache reads.
- **Adjust the token-cap scenario** to the new definition.

### Minor (cost)

- **m1. Cache breakpoints.**
  - The skill's "robust combination for agent loops" is one explicit `cache_control` on the last system block, *plus* top-level automatic caching (`prompt-caching.md`).
  - Automatic caching alone can miss when a turn appends more than 20 positions (the 20-block lookback). Eight steps of assistant and `tool_result` pairs come close to that.
  - **Fix:** add the explicit marker to `build_request`. Task 7.2 checks `cache_read_input_tokens` on a long turn, not only on the second call.
- **m2. The effort sweep.** For chat the skill says "`low` for chat", and "re-run the sweep including `low` / `medium`" (Opus 5.5 checklist). Task 7.2 compares `medium` with `high` only. **Fix:** compare `low` and `medium`; use `high` only for failures.
- **m3. The `max_tokens` rationale.** "The skill's non-streaming-safe default" is the wrong reason for a streamed request (the skill's streaming default is about 64k). 16k is fine for short answers at `medium`, and smaller is better for the pre-check. **Fix:** state the real reason (short answers, a tighter worst case), and let 7.2 measure the output tokens per call.

---

## 3. Security and safety

### M6 (major): an explicit `api_key` does not pin where the key is sent

**Evidence**
- The skill lists `base_url` "or `ANTHROPIC_BASE_URL` env" as client configuration (SKILL § Client config). An explicit `api_key` does not stop the SDK from reading the base URL from the environment.
- A stray `ANTHROPIC_BASE_URL` (a proxy or gateway setting in a developer shell) would send the owner's key to another host.
- The 1.x SDK also resolves `ANTHROPIC_AUTH_TOKEN`, profiles and WIF. Whether an explicit key fully suppresses a bearer header from `ANTHROPIC_AUTH_TOKEN` is not stated in the skill.
- The scenario "Only the explicit API key enables the chat" covers the case *without* a key. No scenario covers a key *together with* these variables.

**Fix** (task 1.6; `chat/safety` scenario "The API key travels only in the request header")
- **Build the client with everything pinned:** `Anthropic(api_key=…, base_url="https://api.anthropic.com", max_retries=…, timeout=…, http_client=DefaultHttpxClient(...))`.
- **Run the transport test with marker values set** for `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_PROFILE` and the WIF variables. Assert:
  - the host is `api.anthropic.com`;
  - there is no `Authorization` header;
  - the key is only in `x-api-key`.
- **If the SDK still adds a bearer header,** the chat refuses to start (`reason: conflicting_credentials`) rather than unsetting variables globally.
- **Turn `ANTHROPIC_LOG` off for the chat client,** or redact it, so a debug setting cannot write request details to the server log.
- **Do not export `.env` into `os.environ`.** Read the key from `.env` into the settings object only, so child processes never inherit it.

### Minor (security and safety)

- **m4. CFBD text in the system channel.**
  - The app-state message is a `role: system` message, the operator channel with system authority (`prompt-caching.md`).
  - Its content is "built from validated ids only", but it renders the *names* looked up for those ids: school names in `TEAMS`, and "Brookvale wins at Ashford". Those names are CFBD-sourced text.
  - That puts data text in the one channel the plan reserves for instructions.
  - **Fix:** render the state as a JSON object whose names are JSON string values, each cut to 80 characters, with the same data note. Add a scenario with an injected school name.
- **m5. The role-boundary scenario tests only what the guard already stops.**
  - Today it covers `CFB_PRED`, `CFB_META` and the live schema, which the guard refuses by name. The claim is that "the role is the boundary", so test statements that *pass* the guard: a scalar UDF call into `CFB_INFRA` (no `EXECUTE`), an `IMPORT … ` subselect in `FROM` (Exasol supports import as a subselect; not in the skill's grammar excerpt, so verify on the stack), and a `SELECT` on a `CFBD_VS` table.
  - Also decide whether the chat may read the `EXA_ALL_*` system views at all. `describe_view` needs `EXA_ALL_VIEWS` and `EXA_ALL_COLUMNS`. Check once on the stack that no `EXA_ALL_*` view readable by `PUBLIC` exposes other sessions' SQL text.
- **m6. A resource limit for the chat user.**
  - The chat shares the database with the pages on stage. The timeout scenario itself runs a three-way cross join.
  - The `QUERY_TIMEOUT` is a session parameter that only the guard protects (any `ALTER SESSION` is refused because it is not a `SELECT`). The role does not protect it.
  - Phase 6 rejected a consumer group because it would also cut the read user's live statements. The chat user has no live statements, so that objection does not apply here.
  - **Fix:** `make chat-user` assigns a consumer group with `QUERY_TIMEOUT`, `SESSION_TEMP_DB_RAM_LIMIT` and a low `PRECEDENCE`/`CPU_WEIGHT` (grammar `create_consumer_group_statement`, confirmed). This is the plan's own fallback, made primary. It is one database object beyond the user, so amend the non-goal, or keep it as an owner choice (U3).
- **m7. The user guide and `mcp-doctor`.** The `exasol-mcp` skill says `mcp-doctor` restores the kit's managed `exasol` entry. A user-guide recipe that edits that entry for `CFB_CHAT_WEB` will be reported as drift and reverted. **Fix:** name the entry `exasol-cfb`, and say that the kit's own `exasol` entry is a different, broader user (`SELECT ANY TABLE`).
- **m8. Injection residue (accepted, document it).** An injected text can still make the analyst *say* something on stage, by quoting it or acting on its claim. The structure prevents action, not speech. The adversarial question checks only "no app change". **Fix:** add "does not repeat the instruction as its own claim" to that question's answer check, and note the residue in the user guide.

---

## 4. Decision [1]: a backend loop, not the Exasol MCP server, in the app's path

### M8 (major): keep (a) for the app's path, but correct the rationale, keep (b2) open, and leave the mission wording to the owner

**Is (a) right?** Yes, for the app's path:
- **Masked errors.** The server masks engine errors ("A database error occurred…", skill `exasol-mcp`), so the model cannot correct a column name. That is a real accuracy loss on stage.
- **TLS.** There is no fingerprint pinning (from context7; the skills do not cover it), so (b2) would be local-only, while Phase 8 wants AWS.
- **Disclosure and caps.** A loop the backend owns gives exact disclosure, caps between steps and a deterministic seam.
- **(b1).** Rejecting (b1) is clearly right: Anthropic would have to reach a laptop.

**But one argument is wrong.**
- In (b2) the *backend* is the MCP client, sitting between the server and Claude. It can cut rows and bytes before the model sees them.
- So "unbounded results (a `WHATIF_OUTCOMES` read could cost dollars)" is not a model-cost argument. The real cost is the server materializing and serializing every row over stdio (memory and latency), which the 20 s query timeout does not bound, because the timeout stops execution, not the fetch.
- As written, the rationale overstates the case.

**The product dimension is under-weighted.**
- The mission's problem statement names "AI access over MCP" as one of the Exasol strengths the demo exists to show. Capability 10, the Tech Stack row, the dependency table and roadmap Phase 7 all say "Exasol MCP".
- The owner is a PM at Exasol, and showing Exasol's own MCP server may be part of the point.
- Decision [1] was taken by the orchestrator, with U3 marked "non-blocking". Yet tasks 6.2–6.4 rewrite `specs/mission.md` and the architecture to remove "Exasol MCP server" from the app's story.

**Fix**
1. **Correct [1]'s rationale for (b2).** Name masked errors, no fingerprint TLS and full server-side materialization. Drop the "costs dollars" claim.
2. **Make U3 a gate for tasks 6.2–6.4** (the doc rewrites), not for code. The owner confirms before the mission or roadmap changes.
3. **Keep the MCP message on stage at near-zero cost.** Hand Phase 8 a 30-second "same role, Exasol MCP server, Claude Desktop or Code" moment on local, using the user-guide recipe ([2]). Recommend it as part of beat 5, with a pointer back to it.
4. **Keep (b2) cheap.**
   - Name `ChatPool.query(sql, max_rows) -> Result(columns, rows, truncated, ms, error, sent_text)` as the seam that `run_sql` uses. An MCP-backed executor could then replace `run_sql`'s execution alone: library tools and lookups stay on pyexasol; disclosure records the tool input, which is the statement as sent; the backend applies the row and size caps.
   - Write that as A12's concrete path. The "(b2) replaces `toolrun.py`" wording is broader than needed.
   - Estimated cost: one adapter module plus an `mcp` client dependency, local-only. That is cheap enough to keep open.

---

## 5. Grading and the rehearsed set

### M9 (major): the live bar does not protect beat 5

**Evidence**
- **The must-pass list** is RQ01, RQ14 and RQ15 ([15]). Beat 5 is "sets a what-if, **explains a swing**, answers a question outside the pages". The Phase 8 hand-off builds beat 5 from RQ01, RQ04 and RQ10–RQ12, so RQ04, the swing, is not protected at all.
- **A single live run of 15 questions** at 13/15 measures average quality, not whether the three or four stage questions work every time. The model is not deterministic.
- **No graded question uses the browser's app state** ("this team", "here"). Mission capability 10 says the chat "shares app state", and decision [7] builds for it.

**Fix** (task 7.2, `chat/rehearsal`, [15])
- **Must-pass** = RQ01, RQ04, RQ14, RQ15, plus every outside question Phase 8 puts on stage (RQ10 at least).
- **Stage questions run 3 times each.** Accept them only at 3/3, or 3/3 after a prompt fix. At about $0.25 a question, 5 questions × 3 runs is about $4, within the $20.
- **Keep 13/15** as the regression bar for the whole set.
- **Add one app-state question,** for example "Why did this team's odds change since last week?" with Brookvale focused in `app_state`. Merge it into RQ04's conversation, or replace RQ05, to stay at 15.

### Minor (grading)

- **m9. The oracle must be leak-free and current.**
  - Spec: oracles run "as the chat user when grading". If that goes through `ChatPool`, its statements enter the turn's recorder and the inspector. They would then satisfy the views check (check 1) by themselves and show up in the disclosure.
  - **Fix:** a separate grader connection that is not recorded. Run each oracle immediately after its question.
  - Snapshot `CURRENT_POINT` before the question and before the oracle, and fail the question as "data moved" if they differ (a deployed namespace can refresh mid-run).
  - Point-aware oracles (RQ06 after `set_as_of`) take the point from the conversation's state, not the current one.
- **m10. The views check is trivial for app-state questions.** The validation lookups of `set_whatif` and `show_page` name `WHATIF_GAMES` and `TEAMS` themselves. So RQ01's `WHATIF_GAMES` and RQ03's `TEAMS` pass without the model reading anything. **Fix:** check 1 counts only the statements of data tools; lookups count for check 4.
- **m11. Guardrail questions should ask for no attempt.** For RQ14, a refused `CFB_API_LIVE` statement passes "forbidden" (it did not run *successfully*), but it shows on stage as a refused statement under the answer. **Fix:** for `kind: guardrail`, check 5 also fails on an *attempt*.
- **m12. The six checks are otherwise sound.** Rules over oracles computed at grade time are the right call ([15]). The format matchers are tolerant in the right places. Add one false-positive guard: a `pct` value must match a number in the answer that is not also a number in the question text.
- **m13. Stub mode on stage.** Exact bound text means one typo ends beat 5's offline fallback with "The stub model knows only the rehearsed questions". **Fix:** in stub mode the panel lists the bound questions as clickable suggestions. The same suggestion component can serve live mode as a prompt aid.

---

## 6. Interfaces with Phases 5 and 6, and the parallel groups

### M10 (major): the hand-offs are recorded, not landed

**Evidence**
- `add-cfb-app/review.md` ends with "Cross-plan items from Phase 7", P7-a to P7-e. Phase 6's `plan.md`, however, still says:
  - `GET /api/config` → "`chat: false`" (line 208);
  - "`/api/chat/*` reserved: 404" (line 233);
  - "The chat region renders only when … enabled, which it never does" (`app/shell`).
- The `app/shell` store holds "the point, the page, the focus team, the what-if conditions and the inspector's state". There is no head-to-head pair (P7-a).
- `chat_factory` (P7-e), the reusable check function (P7-c) and the principal parameter of the query log (P7-d) appear nowhere in Phase 6's tasks.
- **The chat-slot test** "The chat slot receives the app state" is **stretch #3** on Phase 6's cut line, yet Phase 7's task 4.1 extends it.
- **Dead Code Removal in this plan** still names "Phase 6's `chat: false`", which P7-b asks Phase 6 never to ship.

**Fix**
- **Before group C,** the orchestrator applies P7-a to P7-e to Phase 6's plan and specs (`app/shell` Background, the `GET /api/config` table, tasks 1.x/2.x/5.1) while that revision is still running, or records them as Phase 6 tasks. Group C's gate becomes "P7-a to P7-e present *in Phase 6's merged code*", checked by name (A1).
- **If Phase 6 cuts the chat-slot test,** Phase 7's task 4.1 owns it, with the store action for the pair.
- **Make Dead Code Removal conditional:** "`chat: false`, if Phase 6 shipped it; else none".
- **P7-f.** A8's free-text column: Phase 5's serving contract defines `GAMES` as "the columns of their `CFB_MODEL` sources". Ask the Phase 5/3 owner now whether `CFB_MODEL.GAME` keeps CFBD `notes`, rather than discovering it in task 1.7.

### Minor (process and sequencing)

- **m14. The docs are marked done before verification.**
  - Group E (6.3: "Phase 7 marked done") runs before group F, and 7.2 and 7.3 wait on a key that may not arrive before the demo.
  - **Fix:** 6.3 writes "done (stub-verified); live verification pending U2", and 7.3 flips it.
  - **Define the plan's exit without a key:** record the plan with 7.2 and 7.3 moved to Phase 8, so the speq loop is not blocked indefinitely.
- **m15. A wrong task number.** `chat/budget` says the defaults are "re-set after the live rehearsal by **task 8.2**". It is task 7.2.
- **m16. Gate 1.1 should cover the request fields too.** Besides the transport gate (A4), confirm that the resolved `anthropic` 1.x accepts these in `beta.messages.stream` without `extra_body`: `fallbacks`, `output_config.effort`, `eager_input_streaming`, a `role: "system"` message in `messages`, and the `block_binding` object from M2. Record any `extra_body` fallback in the decision log.

---

## 7. Testability

- **Validation.** `speq plan validate add-cfb-chat` passes. 57 scenarios, each mapped to a named test, with sensible tiers:
  - unit tests for pure code and the SDK request shape over an in-process transport;
  - integration tests on the `TEST_` stack;
  - component tests for the panel.
- **The scripted model** is the right seam, and the same scripts drive CI, stub mode and Phase 8's script. Strengthen it with M1's conformance validator, so every test also proves the request would be accepted by the API.
- **Missing scenarios** (from M1 to M7):
  - a mid-stream fallback, with the echo rule and the tools run only after the boundary;
  - a pre-output refusal followed by a next question that succeeds;
  - a first-step failure followed by a next question that succeeds;
  - cancel during the stream;
  - the size cap;
  - concurrent turns against the daily cap;
  - a failed spend write that fails closed;
  - credentials with the environment variables set.
- **Fail-closed tests.** "The marker key is never valid", so a misrouted test still *sends* a request (and gets a 401). **Fix:** default the test settings' `base_url` to an unroutable address, or to the in-process transport, so a misrouted test fails before the network.

---

## 8. YAGNI

- **Drop `rooting_guide`'s `conditions`.** The conditional guide is Phase 6 stretch #8, and Phase 5 measured it at about 2.2 s at k=3, which is near its limit. RQ02 needs only the unconditional guide.
- **One rehearsal entry point.** `make chat-rehearse-it` and `make chat-rehearse` differ only in namespace and data. Use one script with `--namespace test|deployed` and one make target.
- **The token cap:** keep it, redefined (M5), rather than adding a second token knob.
- **Eager input streaming:** keep it, because it is the skill's default and the owner's rule is to follow the skills. Note that this chat's tool inputs are tiny, so it buys nothing here and adds the `ValueError` re-issue path (M4). Revisit if 7.2 shows re-issues.
- **What is not over-built:** the turn log, stub mode, Undo and SQL disclosure. Each serves a stated beat or guardrail, and the cut line already orders what can slip.

---

## 9. Claim check against the skills

| Claim in the plan | Skill source | Result |
|---|---|---|
| `claude-opus-5-5` default; $4 / $20; 5-min cache write $5; read $0.20 | SKILL model table; `model-migration.md` § Opus 5.5 Pricing | Confirmed. The budget scenario's arithmetic ($0.0358) checks out |
| Opus 5.5: thinking always on; effort default `medium`; forced `tool_choice` 400 | `model-migration.md` § Opus 5.5, breaking changes 1–2 | Confirmed |
| Preserved thinking: append-only; enforced for accounts from 2026-08-31 | same, breaking change 3 | Confirmed, but the explicit `prefix_mismatch_behavior` is missing (M2) |
| Mid-conversation `role: system`: after a user message, last or followed by an assistant turn; non-spoofable | `README.md`, `prompt-caching.md` | Confirmed, and the plan's own stop paths violate it (M1) |
| `fallbacks: "default"` + `server-side-fallback-2026-07-01`; `usage.iterations` per attempt | `model-migration.md` § refusal / Opus 5.5 Safeguards | Confirmed. Echo rules and cost not handled (M1, M4) |
| Eager input streaming: validate inputs; Python raises `ValueError`; re-issue when no block is held | `tool-use-concepts.md` § Eager input streaming | Confirmed |
| Top-level `cache_control`; 512-token minimum | `prompt-caching.md`; Opus 5.5 carry-over list | Confirmed. The skill recommends an extra explicit system breakpoint (m1) |
| Text right after tool results can read as injection | `model-migration.md` (mid-turn user messages) | Confirmed |
| Manual loop when control flow interleaves other work | `tool-use-concepts.md` § Tool Runner vs Manual Loop | Consistent |
| anthropic 1.x on `httpx2`; `DefaultHttpxClient` | `python/claude-api/README.md` | Confirmed. The skill does not mention 2.x, so the 2.x release date is unverified here (the 7-day rule picks 1.x anyway) |
| The SDK resolves several credential sources | SKILL § Authentication, § Client config | Confirmed, but `ANTHROPIC_BASE_URL` is not addressed (M6) |
| Exasol `SELECT … INTO TABLE` creates a table | `exasol-grammar.md` `select_into_statement_1` | Confirmed |
| `QUERY_TIMEOUT` in a consumer group; generic `ALTER SESSION SET` | `exasol-grammar.md` (consumer group, `alter_session_statement`) | Confirmed. The session parameter's name is left to Phase 6 probe 1.1(e), which is honest |
| Exasol MCP server: a keyword gate (refuses `SELECT TOP`, a second statement, a leading `--`), masked errors, read-only by grants | skill `exasol-mcp` | Confirmed. Note: the kit's MCP user holds `SELECT ANY TABLE`, so it is a different user from `CFB_MCP` (m7) |
| MCP server: every row returned, no fingerprint TLS | context7 only | Not covered by the skills; plausible. Treat as "per the upstream docs" in [1] |

---

## What's good

- **The boundary is in the right place.** `CFB_MCP` covers `CFB_API` only; network-reaching views are isolated in `CFB_API_LIVE` (Phase 5 [33]); a positive effective-privilege start check; and an honest "the lexer is a typo-catcher" stance. "The chat cannot spend quota" is a grant fact, not a prompt promise.
- **One seam below the loop.** The same scripts drive the CI rehearsal, the stub panel and Phase 8's beat 5, at zero cost and with no key.
- **API hygiene follows the skill closely.** Append-only history, the app state as a mid-conversation system message, explicit effort, `auto` tool choice, fallbacks on and disclosed, and validation of eager input. M1 and M2 are refinements, not redirections.
- **Disclosure "by construction".** Recording pyexasol's `stmt.query`, lookups included, and feeding it into Phase 6's inspector makes "shows its SQL" true without trusting the model.
- **Injection hygiene by structure.** JSON envelopes, the data note, no egress tools, plain-text rendering, and app changes the presenter can undo. The worst case is bounded and visible.
- **Rule-based grading with oracles computed at grade time.** Free, explainable, and robust to weekly data changes. The stub run turns any Phase 5 contract change into a red build.
- **Cost is a first-class requirement.** A pinned price table with sources, `iterations`-aware pricing, a daily cap, `MAX_USD` on every live run, and a stated $20 verification budget. Only the worst-case math (M4) and the token-cap definition (M5) need tightening.
- **Disciplined process.** Explicit hand-offs, a cut line, the IT lock, no DB, CFBD or Anthropic calls during planning, and every API claim traced to a skill file.

---

## Review resolution

Resolved 2026-10-23 by the planner, on the orchestrator's decisions for the owner (logged one by one in `decision-log.md`, Interview, "Plan review round"). Skills loaded first: `claude-api`, `exasol:exasol-database`, `exasol-mcp`. No database, CFBD or Anthropic call; no edit outside the plan directory. `speq plan validate add-cfb-chat` passes without warnings: 9 delta specs, 70 scenarios (from 57), each mapped to a test; 27 tasks (from 25), 3 of them `[expert]`.

### Major findings

| Finding | Decision | Where it landed |
|---|---|---|
| M1 History validity | Accepted, all eight fixes | Decision [22]. `chat/conversation`: a question enters the history with its first kept response; a response that is not kept (refusal, cut-off, unknown stop reason) is never added, so no assistant message is empty; the fallback echo rule (pre-boundary `thinking`, `redacted_thinking`, `tool_use` and other internal blocks dropped, their tools never run, the `fallback` marker dropped from the history and kept in the transcript); `text_reset` withdraws streamed text on a refusal, a cut-off, a cancel or the deadline (not after a fallback, which continues from it); a default branch; calls beyond the tool limit answered in the same message; the next question as a text block after open tool results. `chat/conformance.py` (task 1.9) checks nine rules; the scripted model runs all of them on every request, the live model the stateless ones before sending. New scenarios: fallback echo, early end then a next question (pre-output refusal, model failure, cancel while waiting), tool limit, cut-off and unknown stop, the conformance check's own defects |
| M2 Preserved-thinking controls | Accepted | Decision [23]. `thinking: {type: adaptive, block_binding: {prefix_mismatch_behavior}}` with `thinking-binding-controls-2026-08-01`; `error` in every rehearsal run and the 7.2 smoke, `drop_block` in the panel; `input_transformations` in the turn log with a warning on `prefix_binding_mismatch` (new turn-log scenario); gate 1.1 confirms the SDK sends `block_binding`; A9 rewritten |
| M3 Size cap | Accepted | Decision [24]. 40 columns (`dropped_columns`) and 20,000 serialized characters after the 200-row cap, `truncated_by`; the large-result scenario asserts both and the envelope length |
| M4 Worst case and spend persistence | Accepted | Decision [14] revised. Worst case = input estimate at the cache-write price + `max_tokens` at the output price, plus one fallback hop at the highest row when fallbacks are on; the `ValueError` re-issue is a step with its own reservation; reservations under one process lock (unit scenario with two threads); one line per call in `cache/chat-spend.jsonl`, flushed before the next call; a failed write stops live calls with `spend_log_unwritable` (new scenario; stub unaffected); the turn-log failure scenario now asserts the spend lines. Rehearsal spend: the rehearsal runs write to the same spend file and the server re-reads today's lines before each reservation, so the daily cap covers both |
| M5 Token cap | Accepted | `CHAT_SESSION_MAX_TOKENS` replaced by `CHAT_CALL_MAX_INPUT_TOKENS` (150,000) on the next call's input estimate (previous input of all kinds + previous output + new messages); dollar caps kept; the scenario rewritten. The optional conversation cap on uncached tokens was not added (the orchestrator kept dollars as the money control) |
| M6 Key destination | Accepted | Decision [5] revised. `base_url` pinned; `max_retries`, timeout and `http_client` explicit; a request guard (an httpx request hook) stops a request to another host or with `Authorization` before the network and disables the chat with `conflicting_credentials`; SDK loggers held at `WARNING`; `.env` never exported. The transport scenario sets `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_PROFILE`, the WIF variables and `ANTHROPIC_LOG=debug`. Tests fail closed through a `conftest.py` client factory that raises on any request |
| M7 Cancel and deadline | Accepted | Decision [25]. Cancel within 1 s in every phase: close the stream, `abort_query` the statement (pyexasol 2.4.1, checked in the project's environment), abandon a call waiting for headers; `CHAT_TURN_MAX_SECONDS` 90; `max_retries` 1, timeout 60 s (5 s connect). New scenarios: cancel during a stream, cancel during a statement (replaces the between-steps one), the deadline |
| M8 Decision [1] | Kept for the app's path, rationale corrected | Decision [1] revised: masked errors, no fingerprint TLS on AWS, and the server materializing every row; the cost claim dropped. `ChatPool.query(sql, max_rows) -> QueryResult(…, sent_text)` named as the seam, A12 written as its concrete path. Hand-off P8-b: a 30-second "same role, Exasol MCP server, Claude Desktop" moment in beat 5. U3 gates tasks 6.2–6.4 only (group E2); no code waits |
| M9 Live bar | Accepted | Decision [15] revised. Must-pass RQ01, RQ04, RQ10, RQ14, RQ15 at 3 runs out of 3 (the demo-set run plus `--repeat 2`, about $4); 13 of 15 stays the regression bar; RQ05 is now an app-state question ("What are the playoff odds for the team I'm looking at?", posted with Ashford in focus), replacing "Who gained the most playoff odds this week?". New scenarios: oracles apart from the turn (includes RQ05's state), must-pass repeats |
| M10 Hand-offs | Accepted as decided | Task 3.0 checks P7-a to P7-e by name in Phase 6's merged code before group C; a missing item blocks group C until landed or assigned to a named task here. If Phase 6 cut the chat-slot test (stretch #3), task 4.1 owns it with the store action for the pair. Dead Code Removal made conditional. P7-f kept as a question to forward now: Phase 3's `model/games` already reads `NOTES`, so the ask is to keep it as a column of `CFB_MODEL.GAME` |

### Minor findings

| Finding | Decision | Where it landed |
|---|---|---|
| m1 Cache breakpoints | Accepted | Explicit `cache_control` on the system prompt plus top-level automatic caching; 7.2 checks cache reads on the last call of the longest turn |
| m2 Effort sweep | Accepted | 7.2 runs the sim league at `medium` and at `low`; `high` only for failures |
| m3 `max_tokens` rationale | Accepted, and the value lowered | 8,000 instead of 16,000, with the real reason (short answers; the worst case now counts it twice); 7.2 records the largest output per call and raises it if any call uses more than half |
| m4 CFBD text in the system channel | Accepted | The app-state message is one JSON object; names are JSON strings, control characters removed, cut to 80 characters, with a data note; new unit scenario with an injected school name |
| m5 Role-boundary test | Accepted | The scenario now sends statements that pass the guard: `TEST_CFB_PRED`, `TEST_CFB_META`, a call of the fetch script `CFBD_FETCH`, a virtual-schema table, an `IMPORT` subselect (privilege or syntax error accepted, recorded as A14); plus a direct read of `EXA_ALL_SESSIONS` that must not show another session's statement text (A13). Decision on `EXA_ALL_*`: `run_sql` refuses `SYS`, `EXA_STATISTICS` and `EXA_…` system views statically (with a pointer to `describe_view`, which keeps its fixed statements) |
| m6 Resource limit | Decided differently from the review's suggestion | Per-session `QUERY_TIMEOUT` stays primary, the consumer group is the fallback (decision [9]): Phase 6's probe 1.1(e) tests the session parameter, no Phase 6 probe covers consumer groups; the guard's first-keyword rule, the chat's only path to send its own statement, now has a tested `ALTER SESSION SET QUERY_TIMEOUT = 0` case; a group is a new database object on the owner path; the stage risk is bounded by the 20 s timeout and the 2-session pool |
| m7 `mcp-doctor` | Accepted | The user guide's client entry is `exasol-cfb`, and it says the kit's `exasol` entry is a different, broader user |
| m8 Injection residue | Accepted | The adversarial question's answer must not contain the injected instruction (`answer_none`); the residue is in `chat/safety` and the user guide |
| m9 Oracles | Accepted | A grader connection of its own, never recorded, disclosed or logged; oracle right after its question; "data moved" from two `CURRENT_POINT` reads; point-aware oracles read the conversation's app state |
| m10 Views check | Accepted | Only data-tool statements count for check 1; lookups count for check 4; RQ03's view requirement removed |
| m11 Guardrail attempts | Accepted | Check 5 fails a guardrail question on an attempt, refused or not |
| m12 Percentage false positive | Accepted | A `pct` value matches only a number of the answer that is not also in the question |
| m13 Stub mode on stage | Accepted for stub mode | `GET /api/chat/suggestions` and `Suggestions.tsx`; suggestions in live mode went to the backlog (the orchestrator scoped it to stub mode) |
| m14 Docs before verification | Accepted | 6.3 writes "done (stub-verified); live verification pending U2", 7.3 sets "done"; exit without a key: hand-off P8-c (stub-mode demo, and a recorded live transcript once the key exists) |
| m15 Task number | Fixed | `chat/budget` now names task 7.2 |
| m16 Gate 1.1 | Accepted | The gate checks `fallbacks`, `output_config.effort`, `eager_input_streaming`, a `role: "system"` message, `block_binding`, the system `cache_control`, the request hook and `input_transformations`; any `extra_body` fallback goes into the decision log |

### Testability, YAGNI and the claim check

- **Missing scenarios (§ 7):** all eight are now in the specs (fallback echo; pre-output refusal then a next question; first-step failure then a next question; cancel during the stream; the size cap; concurrent turns against the daily cap; a failed spend write; credentials with the environment variables set).
- **Fail-closed tests (§ 7):** accepted; the suite's client factory raises on any request unless the in-process recorder is installed.
- **YAGNI (§ 8):** `rooting_guide` takes `team_id` only (decision [10]); one rehearsal command, `make chat-rehearse NAMESPACE=test|deployed`, and the `chat_live` marker and `make chat-rehearse-it` are gone (decision [26]); the token cap is redefined, not doubled; eager input streaming stays.
- **Claim check (§ 9):** the MCP-server facts from context7 are now marked "per the upstream docs" in [1] and in the plan's claim table.

### Rejected or changed

Nothing was rejected outright. Three points differ from the review's wording:
- **m6:** the session timeout, not a consumer group, is primary (reasons above).
- **M5:** no extra conversation cap on uncached tokens.
- **m13:** suggestions in stub mode only.

### Found during the revision

- **`PUBLIC`'s baseline in the start check.** Phase 6's probe 1.1(d) shows that `PUBLIC` holds `USE ANY SCHEMA` on a fresh deployment, so a new user's session privileges are `CREATE SESSION` and `USE ANY SCHEMA`. As first written, the start check ("system privileges exactly `CREATE SESSION`") would have refused every chat user. `chat/availability`, decision [8] and P7-c now allow that baseline.
- **Phase 6's probe 1.1(e) has not reported yet.** The timeout decision is conditional on it (A6), and task 3.1 records the outcome.
