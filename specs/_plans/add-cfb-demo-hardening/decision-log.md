# Decision Log: add-cfb-demo-hardening

Date: 2026-10-23 (planned)

## Interview

The owner (Nico) was away and authorized the orchestrator to decide and log. The answers below are the orchestrator's, as it passed them to the planner, close to verbatim.

**Q:** What is the scope?
**A:** Roadmap Phase 8, "Demo hardening", in `docs/roadmap.md`:
- **AWS deployment.** Simulations are the scale dial: local 100k vs AWS 1M+.
- **Replay-mode rehearsal.** Record "Sunday after week N", provide a recorded fallback, and write `docs/demo-script.md` and `docs/benchmark.md`.
- **Labs readiness.** README, LICENSE (MIT), secret scan, and no raw data in git.

The goal is a reliable ~25-minute demo following the roadmap's "Demo flow", beats 1–6, around mid-November 2026. The owner confirmed this date; the rehearsal uses the latest completed week at the time of the run.

**Q (1) Inputs from earlier plans:** What must the plan build on?
**A:** Read them all, including their `review.md` files and Phase 8 hand-offs.
- **Phase 2 loader** (`specs/_plans/add-cfb-persistence-refresh/`, worktree `../exasol-cfb-demo-p2`):
  - snapshot and restore as gzip CSV;
  - the restore is the rehearsal reset;
  - the 217-call demo backfill runs on AWS.
- **Phase 3 model.**
- **Phase 4 engine:** the 1M scale dial, layout decision C vs A, and `make sim` / `make publish`.
- **Phase 5 analyses.** Its Phase 8 notes:
  - after restoring "Sunday after week N", run `make simulate` + `make publish` at week N (0 calls) so the arrows appear after the live refresh;
  - the scorecard headline is a labeled reconstruction unless predictions were frozen live.
- **Phase 6 app.** Its Phase 8 notes:
  - authentication, if the operator path should work on a network address;
  - the 1M scale dial as one more allowed action;
  - replay recordings for the 3 fan-out slices;
  - Q14: in Phase 8, "AWS" means the app on the laptop against the AWS database, on loopback.
- **Phase 7 chat.** Its Phase 8 notes:
  - fill the `demo` bindings for the rehearsed questions; beat 5 is built from RQ01, RQ04 and RQ10–RQ12;
  - a short demo moment, "same read-only role via the Exasol MCP server in Claude Desktop", which keeps the mission's "AI access over MCP" showcase;
  - the chat runs against a stub until the owner provides an Anthropic key. If the key never comes, the exit is a stub-mode demo plus a recorded live transcript once a key exists.
- **The foundation's recorded specs** (`specs/cfbd-vs/`, `specs/cfbd-fetch/`), `specs/decision-log.md` and `specs/_plans/ORCHESTRATOR-STATUS.md`:
  - AWS is the `default` Personal instance, Exasol 2026.1.1, 3 nodes;
  - deploy and run only; never stop, start or resize it;
  - there is no BucketFS replay path on AWS.

**Q (2) Rehearsal:** What must the rehearsal be?
**A:** A deterministic replay of "Sunday after week N, then the live refresh of week N+1".
- Decide how replay works without live CFBD. The options are replay recordings plus a snapshot restore, or a snapshot taken after week N plus recordings of the week N+1 refresh slices.
- The recorded fallback covers a total network loss: everything must work offline in replay mode.

**Q (3) Scale dial:** How is scale shown?
**A:**
- Local 100k sims and AWS 1M sims, using Phase 4's performance budgets.
- The app and the inspector show the run times side by side.
- `docs/benchmark.md` records the measured numbers: sims/s, what-if p95, refresh step times, load times.

**Q (4) Demo script:** What goes into `docs/demo-script.md`?
**A:** Beat by beat: exact clicks and SQL, expected numbers, timing, fallbacks per beat, and reset steps.

**Q (5) Labs readiness:** What is needed?
**A:**
- A README for an external audience and an MIT LICENSE.
- A secret scan (a tool or `git grep` rules), wired into `make lint` or a `make release-check`.
- A check that no raw CFBD data is in git, and the CFBD attribution.
- A clean-history check. The repo is local only; publishing is NOT in scope without explicit owner approval, so list it as an owner action.

**Q (6) Budget:** How are live calls handled?
**A:** Live calls for the rehearsal recording come through the ledger, with a plan cap you propose, within the owner's monthly caps (October 400, November 1000). Already planned: about 21 + 217 + ~4/week refresh + ≤ 6 + ≤ 12 + app 60.

**Q (context):** Which constraints apply to planning?
**A:**
- No DB sessions, no CFBD calls and no Anthropic calls.
- Do not edit `docs/roadmap.md`, `specs/architecture.md` or `CLAUDE.md`; list the needed changes as tasks.
- Invoke `exasol:exasol-database` (and `exasol:exasol-mcp` for the Claude Desktop moment) before making Exasol claims; this is the owner's standing rule. Both skills were loaded: `exasol:exasol-database`, and `exasol-mcp`, the MCP skill available in this session. The MCP server's settings were checked with context7 `/exasol/mcp-server`.

**Q (relay, 2026-10-23, during planning):** Phase 7 was just revised. What changes for this plan?
**A:** Re-read its `plan.md`, `decision-log.md` and `review.md` § Review resolution.
1. The must-pass rehearsed questions are now RQ01, RQ04, RQ10, RQ14 and RQ15, each passing 3/3 live. RQ05 is now an app-state question ("odds for the team I'm looking at"). Any other question on stage (RQ11, for example) joins the must-pass list under Phase 7's hand-off P8-a. Fix the draft's "RQ01, RQ04 and RQ10–RQ12".
2. A `CHAT_MODEL=transcript:` replay mode fits Phase 7's scripted seam, but Phase 8 must build it. Make that an explicit task here.
3. Hand-off P8-b: an MCP moment in Claude Desktop with the entry name `exasol-cfb`, using the same read-only `CFB_MCP` role.
4. Exit if no key ever arrives (P8-c): a stub-mode demo, plus a recorded live transcript once a key exists.

Applied:
- the stage set is exactly the must-pass set (decision [15]);
- the transcript mode is task 5.3, built from the turn log;
- P8-b is decision [16];
- P8-c is task 10.6;
- this plan's own cross-plan changes are renamed X8-a to X8-d (decision [25]), so they do not collide with Phase 7's P8 names.

## Design Decisions

### [1] The rehearsal is a private kit: a "Sunday after week N" snapshot plus recordings of the week-N+1 refresh, under its own `REPLAY_PATH`

- **Decision:**
  - A kit (`~/.cfb-demo/kits/<season>-wNN-wMM/`) pins by SHA-256 a Phase 2 snapshot taken on the Sunday after week N. It copies the recordings that replay needs:
    - the season slices that `REFRESH_WEEK(season, N+1)` fetches (4 with the demo plan), recorded after week N+1's last game;
    - the console's 3 fan-out slices;
    - the previews of the demo pairs.
  - `make rehearse` restores the snapshot, installs the recordings under `/buckets/bfsdefault/default/cfbd/kits/<kit>`, and points the virtual schema's `REPLAY_PATH` there.
  - This is the interview's second option, in the shape of its first: the snapshot is the state, and the recordings are only what the one refresh and the clicks request.
- **Alternatives:**
  - **Recordings in the shared default replay path:** one slice key has one file, so a newer recording of the 2026 games overwrites the one the story needs, and other users of `cache/replay/` change the demo.
  - **A snapshot after week N+1 with no refresh:** nothing would move on stage.
  - **Recordings synthesized from stored rows:** fabricated responses, a lossy inverse of the flattener, and against the "no fabricated records" rule.
  - **Recording the backfill:** pointless; the snapshot already holds history at 0 calls.
- **Rationale:** it is the only deterministic offline source of week N+1's data. It reuses tested parts (snapshot, restore, replay, recorder), adds no data format, and per-kit paths keep several kits usable at once.
- **Promotes to ADR:** yes

### [2] Kits judge the weeks from the data, never from the clock

- **Decision:** a kit is refused unless two things hold:
  - the recorded season `GAMES` body has every week-N+1 FBS game completed (exceptions are named in `demo/demo.yaml`);
  - the snapshot's stored `GAMES` rows have week N completed and no week-N+1 game completed.

  The recordings' `requested_at` and the snapshot's `created_at` are reported, not used as rules.
- **Alternatives:** compare the snapshot's creation time and the recordings' request times with kickoff times.
- **Rationale:**
  - Content is what the story depends on.
  - Clock rules would fail the integration tests on any later date, and would refuse a snapshot taken after weekday MACtion games that is still correct, because `CURRENT_AS_OF` ignores a partly played week.
- **Promotes to ADR:** no

### [3] A Sunday cadence keeps the snapshots coming and the scorecard honest

- **Decision:** every Sunday in November, `make weekly WEEK=n CONFIRM=yes`:
  - on AWS, a live `REFRESH_WEEK(2026, n)` (4 calls), with its model, prediction, 1M simulation and publication steps, then `make snapshot TARGET=aws NAME=aws-2026-wNN`;
  - on the laptop, the restore, `make analyses`, `make predict` (week n+1's first freeze, before kickoff), a week-n simulation and publication if missing, and `make bench-sync`.

  The `predict` step is refused after week n+1's first kickoff unless `ALLOW_LATE=yes`.
- **Alternatives:**
  - Refresh only when building a kit.
  - Freeze predictions at rehearsal time: every graded prediction would be a reconstruction (Phase 5 review).
  - Freeze them on AWS only: predictions do not travel in snapshots (Phase 5 [10]).
- **Rationale:**
  - It produces a "Sunday after week n" snapshot every week.
  - It makes the laptop's week-n+1 predictions `IN_ADVANCE` at zero extra calls, so the "model went 41–9" headline is a forecast.
  - It keeps AWS at the current week for the dial.
  - 4 calls a week fits the owner's "~4/week refresh".
- **Promotes to ADR:** yes

### [4] Live first when the preflight passes; replay is a one-command fallback that needs no restore

- **Decision:**
  - The laptop refreshes live on stage when `make demo-preflight MODE=live` passes at T-30 min (the local `CFBD_API` connection, the DNS gate, ledger room under `app`, quota); otherwise the demo runs in replay.
  - If a live step fails during the demo, `make demo-mode MODE=replay KIT=<kit>` and running the step again recovers. `REFRESH_WEEK` re-fetches every non-final season slice, and a failed fetch keeps the previous data, so no restore is needed.
- **Alternatives:**
  - **Replay only:** safe, but drops the promise of a live API call (mission, D7).
  - **Live only:** a conference network decides the demo.
  - **A restore as the fallback:** minutes on stage instead of seconds.
- **Rationale:** the mission asks for "refresh on stage, fall back to replay mode without network", and the idempotent refresh makes the fallback instant. Without U2 (no key on the laptop), replay is primary and the live proof moves to a preview click on App B (AWS).
- **Promotes to ADR:** yes

### [5] The fallback ladder: replay, then per-beat fallbacks, then a video

- **Decision:** three levels:
  1. live → replay (decision [4]);
  2. a fallback per beat in `docs/demo-script.md`, for example "AWS unreachable → the scale card's measured run" or "no key → stub suggestions";
  3. a private video of a full rehearsal with a chapter per beat, plus screenshots (owner action U7).
- **Alternatives:** a slide deck only; no video.
- **Rationale:** each level covers a failure the level above cannot: the network, one component, the laptop itself.
- **Promotes to ADR:** no

### [6] Replay is local only

- **Decision:**
  - `make demo-mode MODE=replay` and `make rehearse` refuse `TARGET=aws` before connecting.
  - The AWS app (App B) is always live, and is used only for the dial and as a live proof.
- **Alternatives:** upload recordings to AWS BucketFS with credentials.
- **Rationale:** AWS BucketFS has no host directory and the tools have no credentials for it (foundation, `ORCHESTRATOR-STATUS.md`). The AWS side of the demo spends no CFBD call anyway: the dial is a simulation.
- **Promotes to ADR:** no

### [7] The scale dial is a second app instance against AWS on loopback, with a dial action on the operator path

- **Decision:**
  - App B is `make app-serve TARGET=aws APP_PORT=8001` on the laptop.
  - Its operator path gains one action: `SIMULATE` at the current point with the deployment's configured size (1,000,000 at 10,000 on AWS), then `PUBLISH_RUN`, as a background action with progress from `CFB_API.SCALE_RUN_STEPS`. The size is never taken from the browser.
- **Alternatives:**
  - One backend with a second target.
  - A CLI-only dial (`make simulate TARGET=aws` + `make publish`). This stays the fallback if the app work slips.
- **Rationale:**
  - It follows Phase 6 Q14 and its hand-off ("one more allowlist entry").
  - There is no cross-target code, and the operator path stays loopback-only.
  - The publication makes App B's pages, what-if included, show a million seasons.
- **Promotes to ADR:** yes

### [8] The side-by-side numbers come from run summaries synced into `CFB_BENCH`

- **Decision:**
  - `CFB_BENCH` (new schema) holds imported run summaries and steps (`BENCH_SIM_RUN`, `BENCH_SIM_STEP`), the benchmark's measurements (`BENCH_METRIC`) and the deployment's own label.
  - `make bench-sync` copies both ways between `local` and `aws`, merging by key.
  - `CFB_API.SCALE_RUNS` / `SCALE_RUN_STEPS` combine the deployment's own refresh and dial runs (live, any status) with the other deployment's imported runs.
  - The scale card shows the newest `DONE` run per deployment, side by side, aligned by step, with "measured … · imported …".
- **Alternatives:**
  - The backend reads the other deployment live: it fails offline, and adds a second target and its secrets to the app.
  - Two browser windows only: no comparison on one screen and no offline fallback.
  - A JSON file the backend reads: breaks "apps read only `CFB_API`".
- **Rationale:** it works offline (the AWS fallback of beat 3), keeps the app on one database, and copies derived numbers only.
- **Promotes to ADR:** yes

### [9] No authentication: the operator path stays loopback-only, and there is no hosted app

- **Decision:** Phase 8 adds no login. Both apps run on the presenter's laptop on loopback. An app on a network address stays read-only (Phase 6 [3]).
- **Alternatives:** authentication for a hosted app on AWS (Phase 6's open hand-off).
- **Rationale:**
  - Q14 is confirmed for this plan: "AWS" is the laptop app against the AWS database.
  - Authentication would add a security surface, secrets and tests without a demo benefit.
  - The mission lists auth as out of scope.
- **Promotes to ADR:** yes

### [10] The benchmark measures, copies and drops

- **Decision:**
  - `make bench` runs five suites:
    - `sims`: a matrix per target, median of 3, sizing from the runs;
    - `whatif`: Phase 5's `analyses/perf.py`;
    - `refresh`, `load` and `ondemand`: from the logs.
  - It copies each run's numbers into `CFB_BENCH` and then drops the benchmark's simulation runs unless `--keep`.
  - `make bench-report` renders `docs/benchmark.md` with every mission and Phase 4 target marked met or missed.
  - The "1M+" of the roadmap is a 2,000,000-sims cell on AWS, reported only if it finishes within 120 s. The dial itself stays at the configured 1,000,000.
- **Alternatives:** keep benchmark runs, which would crowd the registry, the card and the retention rules; a dial at 2M, which would double the stage time with no story gain.
- **Rationale:** reproducible numbers that survive the runs, a clean registry, and an honest "+".
- **Promotes to ADR:** no

### [11] The demo script is executable and checked by the preflight

- **Decision:** `docs/demo-script.md` marks SQL as `sql demo` (run by the preflight's `script` check) or `sql show` (shown on stage, never run by the check, such as `REFRESH_WEEK`). Placeholders are filled from the kit and `demo/demo.yaml`. Only reads are allowed in `sql demo`. Virtual-schema statements are executed only in replay mode and explained in live mode.
- **Alternatives:** prose with SQL that nobody runs until the demo.
- **Rationale:** a script that is run at every rehearsal cannot drift from the schema, and checking it costs zero calls.
- **Promotes to ADR:** no

### [12] The release check is a stdlib scanner over the tree and the history, with known secrets held in memory; a fast subset runs in `make lint`

- **Decision:** `scripts/release_check.py` runs several rule groups:
  - secret patterns: PEM keys, AWS keys, Anthropic keys, bearer tokens, `IDENTIFIED BY` literals, tracked secret files;
  - known secrets, read at run time from the deployments' `secrets.json` and `deployment.json` and from `.env` and searched in every tracked file and history blob;
  - raw-data rules: `cache/`, recordings, snapshot files, big files, JSON or CSV files with many CFBD game records outside `tests/synthetic/`;
  - release rules: an MIT `LICENSE`, the CFBD credit, the NCAA/CFP statement;
  - hygiene warnings: personal paths, scratchpad paths, e-mail addresses.

  Output masks every secret. `--fast` (tracked files, no history, no secrets read) runs at the end of `make lint`. gitleaks is used when installed, as warnings. The CFBD key is never read, because it lives only in the database connection (owner rule: no secret extraction).
- **Alternatives:**
  - gitleaks as the gate: a new tool to install, and it does not know this project's real values.
  - `git grep` rules only: miss history and real passwords.
- **Rationale:** it finds the exact values that matter without writing them anywhere, covers what will actually be published (history included), and runs offline.
- **Promotes to ADR:** yes

### [13] An MIT LICENSE held by Exasol AG, and an external README

- **Decision:**
  - `LICENSE` is the MIT text with "Copyright (c) 2026 Exasol AG", pending the owner (U8).
  - The README addresses a Labs visitor: what it is, the architecture, requirements, a quickstart with call costs, the data terms and the CFBD credit, "not affiliated with the NCAA or the CFP", the Labs support note, and links.
- **Alternatives:** the author as copyright holder; a short README.
- **Rationale:** Exasol Labs projects are company projects. The quickstart is the visitor's path in the mission ("Clone, create the CFBD CONNECTION with their own key, make deploy, make backfill, open the app").
- **Promotes to ADR:** no

### [14] Publishing, history rewrites and the public file set stay with the owner

- **Decision:** the plan never pushes, publishes, rewrites history or deletes branches. The release check reports history findings, and the owner decides (U9):
  - whether to publish, and when;
  - whether to squash or rewrite;
  - whether `specs/_plans/` (orchestration notes with local paths), `spikes/` and the decision logs ship.
- **Alternatives:** clean up automatically before publishing.
- **Rationale:** git discipline (read-only git), the owner's guardrails, and the interview ("publishing is NOT in scope without explicit owner approval").
- **Promotes to ADR:** yes

### [15] The chat beat: Phase 7's must-pass questions on stage; stub as the fallback; a transcript replay mode built here (revised after the Phase 7 relay)

- **Decision:**
  - **On stage:** RQ01 (sets a what-if), RQ04 (explains a swing), RQ10 (outside the pages) and RQ15 as a 20-second guardrail aside (RQ14 its alternative). This is exactly Phase 7's must-pass set, each 3/3 live.
  - **Reserve:** RQ05, the team in the browser's app state. It goes on stage only after it joins the must-pass list and passes three live runs (Phase 7 P8-a).
  - **Bindings:** filled for the demo kit and checked by `make chat-bindings-check` and the preflight. `demo/demo.yaml` refuses a stage question that is not must-pass.
  - **No key:** `CHAT_MODEL=stub` with suggestions; its answers take their numbers from tool results on the kit.
  - **With a key:**
    - re-qualify the must-pass set on the final demo bindings (3/3, ≤ $4);
    - run beat 5 live once in the panel;
    - `make chat-transcript` turns that chat's turn-log lines into a transcript bound to the kit's SHA-256 and point;
    - `CHAT_MODEL=transcript:<path>`, a new model mode built here on Phase 7's scripted-model seam (task 5.3), replays it: the tools run again, and the answers are Claude's recorded text.
  - **Without a key at all,** the exit is the stub-mode beat; a live transcript is recorded once a key exists (Phase 7 P8-c).
- **Alternatives:**
  - RQ11 or RQ12 on stage: each adds a must-pass question and its three live runs, for no gain over RQ10.
  - A transcript from the rehearsal report: the turn log already holds tool inputs and answers (`chat/turn-log`), and it is Phase 7's named source for P8-c.
  - Stub only.
  - A static transcript page.
- **Rationale:**
  - The stage set equals the must-pass set, so no extra qualification is needed and Phase 7's guarantees apply as they are.
  - The kit is deterministic, so a recorded transcript is exactly right on replay.
  - Building the replay mode as its own task keeps Phase 7's scope unchanged (relay item 2).
- **Promotes to ADR:** no

### [16] The MCP coda (Phase 7 P8-b): Claude Desktop, entry `exasol-cfb`, the chat user with the role `CFB_MCP`, local only

- **Decision:**
  - The last 30 seconds of beat 5 use Claude Desktop with the Exasol MCP server under the entry `exasol-cfb`. That is not the starter kit's managed `exasol` entry, which `mcp-doctor` restores and which belongs to a broader user.
  - The server runs as `CFB_CHAT_WEB` (role `CFB_MCP`, `SELECT` on `CFB_API` only), with `EXA_MCP_SETTINGS` `{"enable_read_query": true, "schemas": {"like_pattern": "CFB_API"}}` and `EXA_SSL_CERT_VALIDATION=false` on loopback.
  - It asks RQ10's text again: the same question, the same role, another client.
  - The chat user's password sits in the client config, so the user guide says to rotate it after the demo.
  - Claude Code in WSL is the alternative client. The coda's answer is not graded, so RQ10 needs no further qualification.
- **Alternatives:**
  - A dedicated database user: one more object and password, for the same role.
  - AWS: the server pins no TLS fingerprint.
  - Dropping the moment: it would lose the mission's "AI access over MCP" showcase.
- **Rationale:** the same read-only role in Exasol's own MCP server proves that the boundary is the database, at near-zero cost (Phase 7 review M8.3, hand-off P8-b).
- **Promotes to ADR:** no

### [17] Ledger plan `add-cfb-demo-hardening` capped at 70, 0 in October; the demo itself under `app`

- **Decision:**
  - The cap is 70: ≤ 43 planned for a demo in the week of Nov 16, plus ≤ 10 if it slips past Nov 22, plus retry headroom.
  - It covers the cadence refreshes (after task 2.4's `PLAN=`; before that they count against Phase 2's remaining 79), the kit recordings, the console fan-out (once) and two live dress rehearsals (`APP_LEDGER_PLAN=add-cfb-demo-hardening`).
  - The demo day's live calls (≤ 8 + retries) count under `app` (cap 60).
  - With every plan at its cap, November stays ≤ 448 of 1000. October is left to Phase 2's backfill.
- **Alternatives:** charge everything to `app`; no cadence (decision [3]).
- **Rationale:** every plan's spend is auditable by its own cap, as the ledger design intends, and October's tight budget is untouched.
- **Promotes to ADR:** no

### [18] Demo choices live in a committed `demo/demo.yaml`; `make kit-pick` proposes them

- **Decision:**
  - Per kit, the file holds the bubble team, the what-if game, the head-to-head pairs, the preview game ids and allowed incomplete games. It also holds the console queries, the chat questions and the candidate rivalries.
  - `make kit-pick` reads `CFB_API` after a replayed refresh and proposes:
    - bubble teams: P(CFP) between 25 and 75 % with a high-leverage game the next week;
    - pairs: from the rivalry list, with their next or latest meeting;
    - the preview games to record.

    The owner approves (U6).
- **Alternatives:** choices only in the kit; automatic choice.
- **Rationale:** names, ids and weeks are not raw data, the script and the chat bindings need one reviewed source, and the right bubble team is a judgment call.
- **Promotes to ADR:** no

### [19] Two app instances keep separate sessions: the cookie name carries the port

- **Decision:** the session cookie is `cfb_session_<APP_PORT>`.
- **Alternatives:** open App B at `localhost` and App A at `127.0.0.1`.
- **Rationale:** cookies are shared across ports of one host, so the two apps would overwrite each other's session (query log, preview cache) in one browser. A naming trick that depends on the presenter typing the right host is fragile on stage.
- **Promotes to ADR:** no

### [20] Kits are private and re-installed from their own copy

- **Decision:**
  - Kits live in `~/.cfb-demo/kits/` (`CFB_KIT_DIR`), with modes 0700/0600, refused inside the repo or OneDrive (the snapshot rule).
  - The kit's own copy of the recordings is the source. `make rehearse` rewrites the BucketFS copy every time and checks it by SHA-256.
- **Alternatives:** keep recordings only in BucketFS or `cache/`.
- **Rationale:** CFBD terms (private retention only), and resilience: a wiped `cache/` or a reinstalled Personal deployment, which removes the BucketFS host directory, does not lose the demo.
- **Promotes to ADR:** no

### [21] The preflight's go/no-go is a command; every check is zero-call

- **Decision:** the checks are those of `demo/preflight`, including:
  - the recordings that `EXPLAIN VIRTUAL` of every replayed query will request;
  - the script's SQL;
  - the headline kind, staleness against the restored calendar, and sizing;
  - live prerequisites;
  - the AWS side.

  The exit code is 2 on any FAIL. It writes nothing and calls nothing.
- **Alternatives:** a manual checklist.
- **Rationale:** a missing recording or a stale kit is cheap to find before the demo and fatal during it.
- **Promotes to ADR:** no

### [22] Demo-day sizing: restart local Exasol before the demo, and warn below 8 instances

- **Decision:** the pre-show checklist restarts the local deployment with free memory (`exasol stop -d local`, `exasol start -d local`; never `exakit`, which targets AWS). The preflight warns when the newest local run used fewer than 8 instances.
- **Alternatives:** take whatever sizing the laptop gives.
- **Rationale:** Exasol Personal sizes cores and DB RAM at every start (D19: 12, 6 and 8 seen). Phase 4's 30 s budget assumes 8.
- **Promotes to ADR:** no

### [23] Beat 2 runs in the app's curated console; `CREATE VIRTUAL SCHEMA` is shown, not run

- **Decision:**
  - Beat 2 uses the inspector console's `fan-out`, `contradiction` and `stored` queries (Phase 6's catalog), with the SQL visible.
  - The generated `CREATE VIRTUAL SCHEMA` text is shown as a `sql show` block, never executed on stage.
- **Alternatives:** a SQL client; re-creating the schema live.
- **Rationale:**
  - The console is tested, budget-guarded and runs as an ordinary reader.
  - Re-creating the virtual schema on stage would replace a working schema mid-demo for no extra insight.
  - Phase 6 review m8: "157 years" comes from the stored-only query.
- **Promotes to ADR:** no

### [24] The dial's size comes from the deployment's configuration only

- **Decision:** `POST /api/scale/run` must name the configured `n_sims`. Any other value is refused with 400 `scale_size`. The batch size and the point always come from the server.
- **Alternatives:** free sizes from the browser.
- **Rationale:** the same server-side discipline as Phase 6's refresh: a click cannot start an unplanned run, and the card's comparison stays between the two configured sizes.
- **Promotes to ADR:** no

### [25] Phase 8's changes in predecessors' code are made here, after they merge, and named X8-a to X8-d

- **Decision:** this plan implements its cross-plan changes itself, after those plans merge, with their tests kept green; the orchestrator tells their sessions.
  - **X8-a:** `make refresh PLAN=`.
  - **X8-b:** the scale views in `sql/api/`, with the allowlist.
  - **X8-c:** the dial routes, the operator entries, the cookie and the card.
  - **X8-d:** the `demo` bindings and the transcript model mode.

  The prefix X8 keeps them apart from Phase 7's inbound hand-offs P8-a to P8-c, which this plan takes (tasks 5.2, 5.3, 8.1, 8.2, 10.6).
- **Alternatives:** hand the changes back to Phases 2, 5, 6 and 7; reuse the P8 names.
- **Rationale:**
  - Those plans are reviewed and in implementation; adding scope to them now would delay their demo-critical work.
  - The changes are small and sit behind tested seams.
  - Distinct names avoid confusing two hand-off directions.
- **Promotes to ADR:** no

### [26] A demo-minimum cut line for this plan

- **Decision:**
  - **Must by the demo:** kits, rehearsal, mode switch, the core preflight checks, the demo script, deployment, the cadence and the kit weeks.
  - **Should:** the app dial and card (fallback: two windows and the CLI dial), the benchmark (fallback: Phase 4's verification numbers), the transcript, `make weekly`.
  - **After the demo:** Labs readiness (release check, README, LICENSE) and the docs tasks.
- **Alternatives:** no cut line.
- **Rationale:** Phases 3–7 are still being implemented, and the demo date is fixed. Labs readiness serves the public release, not the demo.
- **Promotes to ADR:** no

## Assumptions to verify

| # | Assumption | Verify | If wrong |
|---|---|---|---|
| Q1 | CFBD's 2026 regular weeks end on Saturdays Oct 31 (w9), Nov 7 (w10), Nov 14 (w11), Nov 21 (w12); derived from Phase 4's stored schedule, with Army–Navy in week 15 on Dec 12 | Task 10.1: the 2026 rows of `CFB_MODEL.SEASON_WEEK` (or `CFB_CORE.CALENDAR`) after Phase 2's backfill | Shift the cadence and kit names by the offset; the rules (latest completed week) are unchanged |
| Q2 | The demo is in the week of Nov 16, so the demo kit is `2026-w10-w11` | U6, a week ahead | Use the newest kit whose replay week is the latest completed week on demo day: `2026-w09-w10` before Nov 15, `2026-w11-w12` (task 10.8) after Nov 22 |
| Q3 | Phase 6's session cookie has one name for every port | Read the merged `app/backend/security.py` in task 4.1 | Nothing to change if it already carries the port; the scenario still pins it |
| Q4 | Phase 6's background-action machinery (action ids, follow key, run-id lookup) generalizes to a second action kind | Task 4.1, on the merged code | The dial stays CLI (`make simulate TARGET=aws` + `make publish`), with App B following the publication; the card is unaffected |
| Q5 | Phase 7's turn-log lines (`chat/turn-log`: tool calls with validated inputs, app actions, answer) are enough to replay a turn, and tool calls can be grouped into model steps by their order within the line's `calls` | Task 5.3, on the merged code | Replay one tool call per step (the answer text is verbatim either way); if a field is missing, add it to the turn-log writer in 5.3 (X8-d) |
| Q6 | Claude Desktop on Windows can run `uvx exasol-mcp-server@<version>` and reach the WSL local deployment at `localhost:8563` with `EXA_SSL_CERT_VALIDATION=false` | Task 10.6 with U4 (about 5 minutes) | Use `wsl.exe` as the entry's command, or Claude Code in WSL (`claude mcp add exasol-cfb …`) as the client |
| Q7 | The starter kit's `exakit mcp-doctor` leaves a non-kit entry named `exasol-cfb` alone | After U4: `exakit mcp-doctor --json` (report only) | Keep the entry in Claude Code's user scope instead, or re-add it after any `mcp-doctor` repair |
| Q8 | Local Exasol starts with at least 8 instances after a restart with free memory | The preflight's `sizing` check at T-2 h | Close more applications and restart; else quote the measured seconds with the instance count (the script states sizing anyway) |
| Q9 | AWS 1,000,000 sims run in ≤ 60 s (median) at the demo point, which has fewer remaining games than Phase 4's week-9 measurement, and 2,000,000 in ≤ 120 s | Task 10.4 | Show the 1M run anyway, with its time; drop the 2M cell from the report |
| Q10 | `make rehearse` takes ≤ 6 min on the laptop (restore ≤ 5 min, Phase 2 target) | Task 10.2 | Reset only between rehearsals and before the show; the stage fallback never restores (decision [4]) |
| Q11 | Phase 6's console `fan-out` slices are `/games/teams?seasonType=both&team=Michigan&year=2022`, `2023` and `2024` | `make kit` reads them from the merged console catalog (1.3) | Record whatever the catalog names; the kit takes the keys from code |
| Q12 | Repeated rehearsals of one kit keep the graded predictions of week N+1 (frozen by the cadence) and freeze identical week-N+2 values (first freeze wins, same inputs) | `test_two_rehearsals_same_pages` and task 10.2's `kit-facts` diff | Report the difference in `kit-facts`, and freeze week N+2 in the cadence too (`make predict WEEK=N+1` on local before kickoff) |
| Q13 | CFBD's terms allow publishing the pinned OpenAPI spec (`vs/openapi/cfbd-openapi-5.33.0.json`) and derived numbers in `docs/` | U8 (CFBD terms or a question to CFBD) | `make generate` downloads the pinned spec by URL and checks its SHA-256 instead of committing it (the generated catalog stays) |
| Q14 | The flattened `GAMES` table carries the week, season type, completion flag and both teams' classification, so the week checks can run on the recorded body and on the snapshot CSV | Task 1.3, against `vs/catalog/catalog.json` | Use the columns that exist (for example, FBS membership from `CONFERENCES_AFFILIATIONS` in the snapshot); the rule stays "every FBS game" |
| Q15 | The laptop gets its own `CFBD_API` connection for a live refresh on stage | U2 | Replay is primary on the laptop; the live proof is App B's preview click on AWS (1 call under `app`) |
| Q16 | An Anthropic key arrives before Nov 15 | U3 | The stub-mode beat with suggestions (decision [15]); Phase 7's 7.2/7.3 and the transcript recording stay open in task 10.6 and run once the key exists (P8-c) |
| Q17 | A snapshot taken on AWS (2026.1.1) restores exactly on the laptop (2026.2): Phase 2's fingerprints, including the DOUBLE text-form rule [41], match across versions | Phase 2 task 9.5 (before 10.1) | Take the kit snapshots from the laptop: a live local refresh each Sunday (needs U2, 4 calls) or a replayed refresh of the newest kit, followed by `make snapshot TARGET=local` |
| Q18 | Files copied into a new subdirectory `cfbd/kits/<kit>/` of the existing `default` bucket are readable by UDFs within a second (probe 1.4k covered files in an existing bucket) | Task 3.3: `make rehearse` does one replay read of the smallest recording after the switch | Wait and retry for up to 10 s; if still unreadable, install kits under the default `cfbd/replay` path with a kit-specific subdirectory created at deploy time |

## Review Findings

<!-- Populated by speq-implement after code review. -->
