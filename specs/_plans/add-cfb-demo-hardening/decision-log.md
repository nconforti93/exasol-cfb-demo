# Decision Log: add-cfb-demo-hardening

Date: 2026-10-23 (planned; revised the same day after the independent review, see the last interview entry and decisions [27]–[42])

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
- P8-c is task 10.6 (task 11.6 since the review revision);
- this plan's own cross-plan changes are renamed X8-a to X8-d (decision [25]), so they do not collide with Phase 7's P8 names.

**Q (relay, 2026-10-23, after the independent review in `review.md`):** How are the review's findings resolved?
**A:** The owner is away and delegated these decisions to the orchestrator; log each one. Close to verbatim:
- **M1, accepted.**
  - Put the one-time captures in their own first group, needing only Phase 2 and the foundation recorder:
    - the Sunday AWS snapshot (window Sunday to Tuesday);
    - the 4 week recordings (until the next week completes);
    - the local `make predict` before Tuesday's kickoff.
  - Copy each Sunday's recording set to `~/.cfb-demo/recordings/<season>-wNN/` right away, because `--rerecord` overwrites `cache/replay/`.
  - Make the Phase 7–dependent group F optional, so that G, H and I (including the must tasks 8.1 and 9.1) don't wait on it.
  - An honest scorecard headline for kit w10-w11 needs Phase 5's `make predict` on local by Tue Nov 10. Make that an explicit dated task, with the degraded alternative (a labeled reconstruction) if Phase 5 isn't ready.
- **M2, accepted.**
  - Demo tiers:
    - Full;
    - B: no chat; the MCP coda replaces it;
    - C: no app; a SQL client in replay mode plus the CLI dial;
    - D: VS plus model only.
  - Go/no-go on Nov 9, with `TIER=` in the preflight, scripts per tier in `docs/demo-script.md`, and preflight checks per tier.
- **M3, decided.**
  - **Who runs the cadence:** the orchestrator, from a local Claude Code session on the laptop, which has the WSL and AWS access. Fallback: the owner runs `make weekly WEEK=n` himself, a one-command checklist.
  - **Missed Sunday:**
    - the snapshot may be taken until Tuesday, before the first kickoff of the next week;
    - recordings may be taken until week N+2 completes;
    - outside those windows that week's kit is unavailable: fall back to the previous week's kit, and document it.
  - **Q17** (AWS 2026.1.1 snapshot → laptop 2026.2 restore) is gated on Oct 31, as a 0-call test of the round trip. If it fails, the local `CFBD_API` connection becomes an owner action due Nov 1, and kit snapshots are taken on the laptop instead.
  - **Demo date:** the owner said "around mid-November", so use Nov 16–20. U6 stays an owner confirmation, non-blocking.
- **M4, decided.**
  - The default is "live proof, replay data": the stage refresh runs in replay mode against the kit, plus one live click (a preview or the fan-out) to prove it is live.
  - Live data on stage is an owner override (a U-item).
  - Transcript replay is allowed only when the newest refresh's fetches were all `REPLAY` on that kit's path.
- **M5, accepted.**
  - `make demo-mode` refuses while a refresh is `RUNNING`.
  - Add a refresh deadline and `make demo-abort`, which uses `KILL STATEMENT` on the refresh session.
  - Add a test where a later fetch fails after live `GAMES` has already committed.
  - Add a probe in 3.2 for `ALTER VIRTUAL SCHEMA … SET` against another session's open transaction.
- **M6, accepted.** The kit check also requires week N+2 to be NOT complete.
- **M7, accepted.**
  - Freeze `vs/catalog`, `sql/core` and the load plan from Nov 1 to the demo. Add a release-check rule and a `CLAUDE.md` task.
  - No `make deploy` on the real local namespace during demo week; the preflight checks the catalog hash.
- **M8, accepted.**
  - Extend the known-secret scan with the files the review lists: `node_access.pem`, `connection-instructions.txt`, every deployment's `secrets.json`, `~/.exapump/config.toml`, the starter-kit credentials, and the Claude Desktop config.
  - The CFBD key check is an optional hidden prompt, never extraction from the DB.
- **Other answers.**
  - The cadence costs 8 calls a week (refresh plus recording); fix the budget numbers.
  - Add a hard rule to the `CLAUDE.md` task: "never run exakit runtime commands or plain `exakit mcp-doctor` on this machine; `deployments/default` is the AWS instance; only `exakit mcp-doctor --json` is safe".
  - Owner actions the orchestrator takes over:
    - U1, the ledger cap, already added (`add-cfb-demo-hardening: 70`);
    - U3, the passwords: the orchestrator generates random passwords into `.env` (mode 0600, never printed) and runs `make app-user` / `make chat-user`;
    - U5, the cadence.
  - U4 uses `wsl.exe -e uvx exasol-mcp-server@<version>` as the primary Claude Desktop command.
- **Minors.** Fix all of them, specifically:
  - m1: script which app shows the scale card after the dial, or run `make bench-sync` on stage;
  - m4: disable the Refresh button in App B (the AWS app);
  - m10: name the chat daily-cap value in the pre-show checklist;
  - m12: the Wi-Fi-off drill is a hard gate.
- **YAGNI cuts, all accepted:**
  - the `ondemand` and `load` benchmark suites;
  - the two-way `BENCH_METRIC` sync;
  - the second scale-card location;
  - the `kit-pick` heuristic (use a `sql demo` query);
  - transcript replay unless an Anthropic key arrives (keep it as a conditional task).
- **Constraints:** no DB, CFBD or Anthropic calls; no edits outside the plan directory; `speq plan validate` must pass with no warnings; append a "Review resolution" section to `review.md`. Before starting, invoke `exasol:exasol-database`, `exasol:exasol-bucketfs`, `exasol-mcp` and `exasol-runtime` (all four were loaded).

Applied:
- **The captures and the cadence:** group A (tasks 10.1–10.6), the new feature `demo/season-cadence`, task 3.5 due Nov 8, the recordings copy, and the dated predict task 10.4 (decisions [27], [29], [30]).
- **Tiers** (decision [28]): in `demo/preflight`, `demo/rehearsal` and `demo/chat-beat`, and task 11.2.
- **The stage default** (decision [31], revising [4]), and the transcript guard with task 5.3 made conditional (decision [32]).
- **Abort and guard** (decision [33]): in `demo/rehearsal`, plus the probe in task 3.2 (Q19, Q20).
- **The week-N+2 check** (decision [34]).
- **The freeze** (decision [35]): task 1.6 by Oct 31, and the `freeze` rule.
- **The secret homes and the CFBD prompt** (decision [36]).
- **The owner actions** (decision [37]) and the `exakit` rule (decision [38]).
- **The YAGNI cuts** (decision [39]).
- **The stage mechanics** (decision [40]): m1 as `make bench-sync` on stage with App A's card; m4 as `APP_REFRESH=off`.
- **The budget** (decision [41]) and **the placeholders-only script** (decision [42]).

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

- **Revised** by [34] (review M6): a third check.
- **Decision:** a kit is refused unless three things hold:
  - the recorded season `GAMES` body has every week-N+1 FBS game completed (exceptions are named in `demo/demo.yaml`);
  - the recorded season `GAMES` body has week N+2 not complete;
  - the snapshot's stored `GAMES` rows have week N completed and no week-N+1 game completed.

  The recordings' `requested_at` and the snapshot's `created_at` are reported, not used as rules.
- **Alternatives:** compare the snapshot's creation time and the recordings' request times with kickoff times.
- **Rationale:**
  - Content is what the story depends on.
  - Clock rules would fail the integration tests on any later date, and would refuse a snapshot taken after weekday MACtion games that is still correct, because `CURRENT_AS_OF` ignores a partly played week.
- **Promotes to ADR:** no

### [3] A Sunday cadence keeps the snapshots coming and the scorecard honest

- **Revised** by [27], [29] and [41] (review M1, M3, §5): the cadence also records and copies the week's slices, checks the data before paying, has a named runner and windows, and costs 8 calls a week. Its rules are the feature `demo/season-cadence`.
- **Decision:** every Sunday in November, `make weekly WEEK=n CONFIRM=yes`:
  - on AWS: the quota check, a live `REFRESH_WEEK(2026, n)` (4 calls) with its model, prediction, 1M simulation and publication steps, the content checks, then `make snapshot TARGET=aws NAME=aws-2026-wNN`, then the recording of the 4 season slices (4 calls), copied at once to `~/.cfb-demo/recordings/2026-wNN/`;
  - on the laptop: the restore, `make analyses`, `make predict` (week n+1's first freeze, before kickoff), a week-n simulation and publication if missing, and `make bench-sync`.

  The `predict` step is refused after week n+1's first kickoff unless `ALLOW_LATE=yes`.
- **Alternatives:**
  - Refresh only when building a kit.
  - Freeze predictions at rehearsal time: every graded prediction would be a reconstruction (Phase 5 review).
  - Freeze them on AWS only: predictions do not travel in snapshots (Phase 5 [10]).
- **Rationale:**
  - It produces a "Sunday after week n" snapshot every week.
  - It makes the laptop's week-n+1 predictions `IN_ADVANCE` at zero extra calls, so the "model went 41–9" headline is a forecast.
  - It keeps AWS at the current week for the dial.
  - It costs 8 calls a week, not 4. The owner's "~4/week" covered only the refresh, and AWS cannot keep its own responses (no replay directory), so the recording repeats the 4 requests (review §5).
- **Promotes to ADR:** yes

### [4] Live first when the preflight passes; replay is a one-command fallback that needs no restore

- **Superseded as the stage default** by [31] (review M4): the laptop now refreshes in replay by default, and the live call is a click in the AWS app. The rule below applies only when the owner overrides to live data (U11), extended by [33]: a hung or failed step is stopped with `make demo-abort` before the switch.
- **Decision (original):**
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

- **Revised** by [39] and [40]: `make bench-sync` copies run summaries and steps only, never `BENCH_METRIC`. The card lives only in the inspector's "Scale" tab, and on stage `make bench-sync` runs right after the dial.
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

- **Revised** by [39]: three suites (`sims`, `whatif`, `refresh`). The `load` and `ondemand` numbers are read from the logs by `make bench-report`, which reads both deployments directly.
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

- **Revised** by [35] (the `freeze` rule in the fast subset) and [36] (every home of a known secret, and the CFBD key by an optional hidden prompt).
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

- **Revised** by [32] (review M4 and YAGNI): the transcript is built only once a key exists, and it replays only after a refresh whose fetches all replayed the kit's own recordings. Also revised by [28] (in tiers B and C the coda is the whole beat) and [41] (the demo-day caps).
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

- **Revised** by [37]: the entry's command is `wsl.exe -e <absolute uvx> exasol-mcp-server@<v>`, with `WSLENV`. The coda runs on the owner's Claude subscription, not the API key, which makes it tier B's beat 5 ([28]).
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

- **Revised** by [41]: the cadence's cost is stated as 8 calls a week, the captures run before the rehearsals, and a rehearsal starts only with room left for the remaining captures. The totals are unchanged (≤ 43 planned). The ledger entry was added by the orchestrator on 2026-10-23 ([37]).
- **Decision:**
  - The cap is 70: ≤ 43 planned for a demo in the week of Nov 16, plus ≤ 10 if it slips past Nov 22, plus retry headroom.
  - It covers the cadence refreshes (after task 2.4's `PLAN=`; before that they count against Phase 2's remaining 79), the kit recordings, the console fan-out (once) and two live dress rehearsals (`APP_LEDGER_PLAN=add-cfb-demo-hardening`).
  - The demo day's live calls (≤ 8 + retries) count under `app` (cap 60).
  - With every plan at its cap, November stays ≤ 448 of 1000. October is left to Phase 2's backfill.
- **Alternatives:** charge everything to `app`; no cadence (decision [3]).
- **Rationale:** every plan's spend is auditable by its own cap, as the ledger design intends, and October's tight budget is untouched.
- **Promotes to ADR:** no

### [18] Demo choices live in a committed `demo/demo.yaml`; `make kit-pick` proposes them

- **Revised** by [39]: `make kit-pick`'s bubble heuristic is cut. Bubble candidates come from a documented `sql demo` query in the script's kit recipe. `make kit-previews` keeps only the listing of preview game ids that feeds `make record`. The `rivalries` list is dropped. `demo/demo.yaml` gains `tier` ([28]), `stage_mode` ([31]) and `console_fanout_slices` (Q11).
- **Decision (original):**
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

  The prefix X8 keeps them apart from Phase 7's inbound hand-offs P8-a to P8-c, which this plan takes (tasks 5.2, 5.3, 8.1, 8.2, 11.6).
- **Alternatives:** hand the changes back to Phases 2, 5, 6 and 7; reuse the P8 names.
- **Rationale:**
  - Those plans are reviewed and in implementation; adding scope to them now would delay their demo-critical work.
  - The changes are small and sit behind tested seams.
  - Distinct names avoid confusing two hand-off directions.
- **Promotes to ADR:** no

### [26] A demo-minimum cut line for this plan

- **Revised** by [27] and [28]:
  - The cut line also covers the predecessors: a missing phase lowers the demo tier.
  - "Must" now starts with the captures (10.1–10.5), `make weekly` (3.5) and the freeze (1.6).
  - `make demo-abort` (3.6) is "Should", because only the live-data override needs it.
  - The transcript is conditional on a key.
- **Decision (original):**
  - **Must by the demo:** kits, rehearsal, mode switch, the core preflight checks, the demo script, deployment, the cadence and the kit weeks.
  - **Should:** the app dial and card (fallback: two windows and the CLI dial), the benchmark (fallback: Phase 4's verification numbers), the transcript, `make weekly`.
  - **After the demo:** Labs readiness (release check, README, LICENSE) and the docs tasks.
- **Alternatives:** no cut line.
- **Rationale:** Phases 3–7 are still being implemented, and the demo date is fixed. Labs readiness serves the public release, not the demo.
- **Promotes to ADR:** no


### [27] Captures first: their own group, recordings copied on capture, and the Phase 7 group optional (review M1)

- **Decision:**
  - The three time-bound inputs form group A (tasks 10.1–10.6), which runs on its calendar from Oct 31 without waiting for any other group:
    - the AWS snapshot of "Sunday after week N";
    - the week's season-slice recordings;
    - the laptop's predictions, frozen before Tuesday's kickoff.

    It needs only Phase 2 on AWS, the foundation's recorder and the ledger entry. Until `make weekly` merges (task 3.5, due Nov 8), it runs Phase 2's commands one by one.
  - The cadence copies each Sunday's recordings to `~/.cfb-demo/recordings/<season>-wNN/` at once (0700/0600, `SHA256SUMS`). Kits are built later from that copy (`make kit SOURCE=…`).
  - The stage kit's honest headline is its own dated task (10.4): the week-11 predictions are frozen on local by Tue Nov 10, or the headline is the labeled reconstruction.
  - The Phase 7 tasks (5.2, 5.3) form the optional group G, which never blocks H–K. A task removed by the cut line or by the tier decision leaves the chain. 8.1's draft and 9.1 may start before 7.1.
- **Alternatives:**
  - The strict chain A → … → I behind Phases 2–7. With it, the must tasks 8.1 and 9.1 waited on Phase 7.
  - Copying recordings only at kit build: Nov 15's `GAMES` recording overwrites Nov 8's in `cache/replay/`.
- **Rationale:** only the captures can never be redone; everything else can be built from them on any later day. A slipping predecessor then costs the demo time, not its data.
- **Promotes to ADR:** yes

### [28] Four demo tiers, with a go/no-go on Nov 9 (review M2)

- **Decision:**
  - The tiers:
    - **Full** (Phases 2–7): beats 1–6 as scripted.
    - **B** (Phases 2–6): no chat panel; the Claude Desktop coda is beat 5.
    - **C** (Phases 2–5): no app; a SQL client on local in replay, and the dial from the command line.
    - **D** (Phases 2–3): the virtual schema and the model only, as beats 2 and 3.
  - On Mon Nov 9 the orchestrator sets `tier` in `demo/demo.yaml` from what is merged and green, and logs it here. A tier may go up later only after that tier passes its preflight and a dress rehearsal by Nov 16.
  - The preflight takes `TIER=`, and a check the tier does not use prints `SKIP`. `make rehearse TIER=D` skips the analyses and the base publication.
  - `docs/demo-script.md` has a section per tier, and its `sql demo` blocks carry `tiers=`.
  - If Phase 7 has not merged, the orchestrator creates the coda's user `CFB_CHAT_WEB` with the role `CFB_MCP` (task 9.1).
- **Alternatives:** no degraded path, where the beat fallbacks assume a working app and engine; deciding the tier on demo day.
- **Rationale:** Phases 3–7 are being implemented in the same weeks. A defined step down for each phase that can slip keeps a demo. Nov 9 is the first day with a real kit to rehearse on.
- **Promotes to ADR:** yes

### [29] The cadence's runner, its windows and the missed-Sunday rule; the cadence gets its own spec (review M3)

- **Decision:**
  - **Runner:** the orchestrator, from a local Claude Code session on the laptop. Only that machine has WSL, the local Exasol and the AWS secrets under `deployments/default`; a cloud agent reaches neither. If no session has run the cadence by Monday evening, the owner runs `make weekly WEEK=n CONFIRM=yes`, a one-command checklist.
  - **Windows:**
    - the snapshot can be a kit base until Tuesday's first kickoff of the next week; the content rule is "until week n+1's first FBS game ends";
    - the recordings serve their kit until week N+2 completes (for kit `wN-wN+1`), which in cadence terms is until week n+1 completes;
    - the predictions are honest until week n+1's first kickoff.
  - **Missed Sunday:**
    - a late run still refreshes AWS;
    - its snapshot is named `-late` when it can no longer be a base;
    - its recording is skipped at zero calls when its kit would refuse it;
    - outside the windows, that week's kit is unavailable: the previous week's kit is used, and this log records it.
  - **Spec:** the feature `demo/season-cadence` specifies the cadence in 5 scenarios. It is CLI-tested with fake targets and runners.
- **Alternatives:**
  - "By the owner or the orchestrator": nobody concrete.
  - A local timer: each live run needs a `CONFIRM`.
  - Clock windows only: they would accept a recording that the kit then refuses.
- **Rationale:** it names who acts and when, checks the data before paying, and makes the fallback explicit instead of improvised.
- **Promotes to ADR:** no

### [30] Q17 is a gate on Oct 31, with a 0-call fix first and the laptop path second (review M3)

- **Decision:**
  - By Sat Oct 31, a 0-call snapshot → restore round trip from AWS (2026.1.1) to the laptop (2026.2). This is Phase 2's task 9.5, run here as 10.1 if it has not run.
  - If it fails:
    1. first, a 0-call fix of the cross-version difference in Phase 2's snapshot code (for example the DOUBLE text form of its [41]), retested by Nov 1;
    2. otherwise U2 (the local `CFBD_API`) is due Nov 1, and the laptop takes over the snapshots:
       - a local demo backfill (≈ 217 calls, on a November ledger line the orchestrator adds, outside this plan's 70);
       - each Sunday a local live refresh (4) and `make snapshot TARGET=local`, next to the AWS refresh for the dial. That makes 12 calls a week, ≤ 55 of 70 for a demo on Nov 16–20.
- **Alternatives:**
  - Checking it "before 10.1": too late to get U2 for Nov 1.
  - Taking kit snapshots on the laptop from the start: it needs U2 and ≈ 217 more calls for nothing if the restore works.
- **Rationale:** Nov 1 is the first capture, and its fallback needs the owner's connection the same day. The local backfill is stated, not hidden: it is the real price of the fallback.
- **Promotes to ADR:** no

### [31] On stage: live proof, replay data; live data on the laptop is the owner's override (review M4; revises [4])

- **Decision:**
  - The laptop (App A) refreshes in replay over the stage kit, so every scripted number is the kit's.
  - The one live call is the console `fan-out` in the AWS app (App B, beat 2; 3 calls under `app`). The reserve is App B's head-to-head preview (1 call).
  - Live data on the laptop is the owner's override (U11: `stage_mode: live`, needs U2). The preflight then runs with `MODE=live`, and the abort rules of [33] apply.
  - The script carries two expected columns: "replay" (kit facts) and "live" (read on screen, never a fixed number).
  - The preflight's live checks run for each deployment that goes live: App B with `AWS=yes`, the laptop with `MODE=live`.
- **Why App B:** the virtual schema's mode is global to a deployment, so a live click on the laptop would need a mode switch on stage. App B is always live ([6]). As a side effect, U2 is no longer needed for the default.
- **Alternatives:**
  - **Live first when the preflight passes** (the old [4]). A live refresh in the demo week fetches newer slices than the kit: weekday results, Tuesday's CFP rankings, maybe new SP+. The script's numbers, `kit-facts` and a transcript would then contradict the screen.
  - **Replay only:** it loses the live call the story promises.
  - **A live click on the laptop:** it needs a mode switch on stage.
- **Rationale:** the mission's promise is "refresh on stage, fall back without network". A replayed refresh of real recorded week-N+1 data, with the badge saying "replay", is honest. One real call in beat 2 proves the virtual schema is live.
- **Promotes to ADR:** yes

### [32] A transcript only once a key exists, and only on its kit's replayed data (review M4, YAGNI)

- **Decision:**
  - Task 5.3 is built only once an Anthropic key has arrived (Q16).
  - Its guard holds when both are true:
    - the point and the restored snapshot match the transcript's;
    - every fetch of the newest `REFRESH_WEEK` run is `REPLAY`, with the slice key and the request time of one of the kit's `refresh` recordings. A replayed fetch carries the recording's request time (`cfbd-fetch/fetch-modes`), so this proves the refresh read the kit's own path.

    The app checks it at start and before each transcript turn; otherwise it uses the stub and gives the reason.
  - If no key ever arrives, the recorder drops `demo/chat-beat`'s two transcript scenarios.
- **Alternatives:**
  - A binding by the kit's SHA-256 and point only. After a live refresh the point is the same, so the transcript would quote the kit's numbers over live data.
  - Reading `REPLAY_PATH` from the virtual schema's properties. The app's roles cannot read them, and the property may have changed after the refresh.
- **Rationale:** it proves "on that kit's path" from data the app role can read, and it spends the expert-grade effort only when it can pay off.
- **Promotes to ADR:** no

### [33] No mode switch during a run; a refresh deadline; `make demo-abort` (review M5)

- **Decision:**
  - **The guard.** `make demo-mode` refuses while a non-stale `RUNNING` row of any kind is in `CFB_META.LOAD_RUN`, naming it. Staleness follows Phase 2's rules: session gone, own session, or an idle script session. The `ALTER` runs on its own connection with a 10-second limit.
  - **The deadline.** The app's `APP_REFRESH_DEADLINE_S` (default 45) applies to the `FETCH` steps. When they have not all ended by then, the refresh progress shows `make demo-abort` and `make demo-mode MODE=replay KIT=<kit>`.
  - **The abort.** `make demo-abort` runs `KILL STATEMENT IN SESSION <id> WITH MESSAGE 'demo-abort'` (grammar `kill_statement`) on the newest `RUNNING` `REFRESH_WEEK` or `SIMULATE` run.
    - It waits up to 15 seconds for the session to be gone or idle; the next run sets the row `ABANDONED` (Phase 2 `loader/run-logs`, Phase 4 `sim-runs`).
    - A killed statement keeps its transaction (owner's verified note on 2026.1.0). The app's action closes its own connection (`app/access`), which ends that transaction.
    - If the session still runs after 15 seconds, the command exits with 2 and prints the `KILL SESSION` line.
  - **New scenarios:** a failure on the third fetch after a changed live `GAMES` committed, repaired by the replayed refresh; and a hung refresh, refused by the switch, aborted, then replayed.
  - **The probe in 3.2:** the `ALTER` against another session's open transaction, the mode's reach into other sessions, and the killed run's transaction (Q19, Q20).
- **Alternatives:**
  - A total refresh deadline, such as the review's 60 s: it would also fire on a slow but healthy simulation step.
  - The app killing its own run: a second stop path on stage, with no gain over one pasted terminal line.
  - `KILL SESSION` as the default: it rolls back, but it also ends a SQL client's session in tier C without warning.
- **Rationale:** it reuses Phase 2's stale rules, Phase 4's cleanup and Phase 6's per-action connections, adds no cleanup code, and makes a mixed live/replay run impossible.
- **Promotes to ADR:** no

### [34] A third week check: week N+2 is not complete in the recording (review M6)

- **Decision:**
  - `make kit` refuses a recorded `GAMES` body in which every week-N+2 FBS game is completed, naming the week and the recordings copy to use instead.
  - `kit.json` counts the week-N+2 games already completed (weekday games), because their predictions become reconstructions on replay.
  - The cadence applies the same rule before it records ([29]).
- **Alternatives:** trusting the recording date; refusing any week-N+2 result, which would reject every recording made after a Tuesday MACtion game.
- **Rationale:** `CURRENT_AS_OF` is the newest finished week, so a recording made after week N+2 ended moves the replayed point to N+2. This is a content rule, like the other two.
- **Promotes to ADR:** no

### [35] Freeze the snapshot scope's definitions from Nov 1 until the demo (review M7)

- **Decision:**
  - **Frozen paths:** `vs/catalog/`, `sql/core/`, `sql/meta/load_log.sql`, `sql/meta/fetch_log.sql` and `loader/load-plan.yaml`. They are frozen from Nov 1, after the cadence's snapshot, until the demo (`UNTIL` 2026-11-21, moved with the date).
  - **Enforcement:**
    - `make demo-freeze` pins their git tree ids in `demo/freeze.yaml`;
    - `release_check --fast`, part of every `make lint`, fails on a change (the `freeze` rule);
    - the preflight's `kit` check compares the deployed catalog hash with the kit snapshot's, and the trees with the pins.
  - **The rule in words:** `CLAUDE.md` states it (task 1.6, by Oct 31), together with "no `make deploy` on the real local namespace in demo week except through `make rehearse`". The orchestrator tells the Phase 2–7 sessions.
  - **A change** needs the owner's decision and a migration that re-fingerprints the existing snapshots.
- **Alternatives:**
  - `make generate && git diff` inside the preflight: the preflight would then write files. That check stays in the Checklist and in lint.
  - Re-taking snapshots after a change: impossible, because they are time-bound.
  - No rule.
- **Rationale:** the kits of Nov 8 and Nov 15 cannot be retaken. A regenerated catalog or a changed `CFB_CORE` table after them would silently break the stage kit.
- **Promotes to ADR:** yes

### [36] Known secrets from every home; the CFBD key only by a hidden prompt (review M8; extends [12])

- **Decision:**
  - **The homes.** `known-secret` reads, into memory only:
    - for every deployment under `~/.exasol/personal/deployments/` (today `local`, `default` and `test`): its `secrets.json` (every string value of 8 or more characters), `deployment.json`, `connection-instructions.txt` (values after a password label), and `node_access.pem` and any `*.pem` (key bodies compared with whitespace removed, any 64-character run);
    - `~/.exapump/config.toml`, parsed with `tomllib` and never printed, as the `exasol-database` skill's "never `cat`" rule requires;
    - `~/.exasol-starter-kit/credentials/*`;
    - the `exasol-cfb` entry's `EXA_PASSWORD` in the Claude Desktop configuration;
    - `.env`.

    A missing home is a `WARN` naming it.
  - **The secret-file rule** also names a tracked `.exapump/config.toml` and `claude_desktop_config.json`.
  - **The CFBD key:** `--cfbd-key-prompt` (owner only) asks for it at a prompt that does not echo, for one run, in memory only. Without the flag, the report says "pattern-checked only".
- **Alternatives:** reading only `local` and `default` `secrets.json`; extracting the key from the connection (owner rule: no secret extraction); patterns only.
- **Rationale:** the AWS node's SSH key, the exapump passwords and the starter kit's MCP user are real secrets on this machine, and a pasted key body passes every pattern rule.
- **Promotes to ADR:** no

### [37] Owner actions taken over by the orchestrator; U4 through `wsl.exe`

- **Decision:**
  - **U1, the ledger entry, is done:** `add-cfb-demo-hardening: 70` (ledger commit `12cbbf1`). The ≤ $10 Anthropic approval stays with the owner.
  - **U3:** the orchestrator generates random app and chat passwords into `.env` (mode 0600, never printed) and runs `make app-user` and `make chat-user` on both targets. The API key stays with the owner.
  - **U5:** the cadence runner of [29].
  - **U4's primary entry** is `wsl.exe` with `-e`, the absolute path of `uvx` in WSL and `exasol-mcp-server@<v>`.
    - Its `env` sets the `EXA_*` values and `WSLENV` with `/u` for each of them.
    - `wsl.exe -e` starts no login shell, and on this machine `uvx` is `~/.local/bin/uvx`, which only `.profile` and `.bashrc` put on the path. A bare `uvx` would not be found, and without `WSLENV` the variables would not reach the server.
    - The DSN is WSL-local `localhost:8563`.
  - **The alternatives for U4:** Windows-native `uvx` with WSL localhost forwarding, or Claude Code in WSL.
- **Alternatives:** keep U1, U3 and U5 with the owner (he is away); Windows-native `uvx` as the primary command.
- **Rationale:** these actions need no judgment only the owner can make. uv, Python and the database are already in WSL.
- **Promotes to ADR:** no

### [38] Never an `exakit` runtime command on this machine

- **Decision:** a hard rule in `CLAUDE.md` (task 1.6): "never run exakit runtime commands or plain `exakit mcp-doctor` on this machine; `deployments/default` is the AWS instance; only `exakit mcp-doctor --json` is safe". Local Exasol is driven only with `exasol stop/start -d local`.
- **Alternatives:** use the kit's commands as its skills recommend.
- **Rationale:**
  - The `exasol-runtime` skill keeps the kit's state in `deployments/default`, which on this machine is the AWS instance.
  - `exakit start/stop/status/repair-runtime` would therefore act on AWS and break the mandate.
  - The plain `mcp-doctor` repairs and may provision on that database (`exasol-mcp` skill).
- **Promotes to ADR:** yes

### [39] Cuts that do not serve the demo (review §10)

- **Decision:**
  - **Benchmark:** the `load` and `ondemand` suites are cut; `make bench-report` reads those numbers from `CFB_META.LOAD_RUN` and `FETCH_LOG` directly.
  - **Sync:** `BENCH_METRIC` is no longer synced; the report reads both deployments.
  - **The scale card** is only in the inspector's "Scale" tab, with no odds-banner line.
  - **`make kit-pick`'s bubble heuristic** becomes a documented `sql demo` query, and `make kit-previews` keeps the preview listing.
  - **Transcript replay** is conditional on a key ([32]).
  - **Kept:** `sims`, `whatif` and `refresh`, and the 2M cell.
- **Alternatives:** keep everything.
- **Rationale:** each cut item copied numbers the logs already hold, or added code that a query or a later decision covers. Fewer tasks before Nov 9.
- **Promotes to ADR:** no

### [40] Stage mechanics: the card after the dial, App B's refresh off, the dial's estimate, the terminal tab, the build, the offline gate (review m1–m4, m6, m12, §9)

- **Decision:**
  - **m1:** after the dial, `make bench-sync` runs in the open terminal tab (seconds, 0 calls). App A's inspector "Scale" tab then shows the laptop's refresh (read live) and the AWS dial run, both just watched. If the sync fails, the card shows the last synced run, "measured <date>".
  - **m2:** the pre-show list has one terminal tab with `make bench-sync`, `make demo-abort` and `make demo-mode MODE=replay KIT=…` pasted and ready.
  - **m3:** `make app-build` runs at T-2 h, and the preflight's `app` check fails without a current build.
  - **m4:** App B runs with `APP_REFRESH=off`. Its refresh button is disabled with a reason, and the requests answer 403 `refresh_off`, so no misclick spends AWS calls or moves AWS.
  - **m6:** the dial's confirmation quotes the plan's estimate, or "about a minute".
  - **m12:** the Wi-Fi-off drill is a hard gate in 11.1 and 11.5; no dress rehearsal counts before it passes.
  - **§9:** the two-instance test also posts an operator request to each instance with its own origin and with the other's.
- **Alternatives:**
  - m1: App B's card, whose laptop column would be the last synced run, not the one just watched.
  - m4: a script line "never press Refresh in App B".
- **Rationale:** the punchline shows both numbers the audience just saw. A flag cannot be forgotten on stage; a script line can.
- **Promotes to ADR:** no

### [41] Budget: 8 calls a week, captures before rehearsals, quota first, the demo-day chat cap (review §5, m8–m10)

- **Decision:**
  - **The cadence** costs 8 calls a week (refresh 4 + recording 4). Nov 1 costs 4, plus 4 only if an `aws-2026-w08` base exists. The planned total is unchanged: ≤ 43 for a demo on Nov 16–20 (≤ 47 with the Nov 1 recording), ≤ 53 with Nov 22, capped at 70.
  - **m8:**
    - each Sunday's capture runs before that week's rehearsals;
    - a dress rehearsal starts only when the plan's room after it still covers every remaining capture (8 each) plus headroom.
  - **m9:** `make weekly`'s first step is `quota`, because the key's pool is shared (Academic, `sharedPool`).
  - **m10:**
    - on demo day both apps run with `CHAT_DAILY_MAX_USD=3.00` and `CHAT_SESSION_MAX_USD=2.00` and share one spend file;
    - the pre-show checklist names both values, and the preflight's `chat` check prints them and warns above 3.00;
    - this plan's Anthropic spend, demo day included, is ≤ $9 of the approved ≤ $10;
    - the MCP coda uses the owner's Claude subscription, not the API key.
- **Alternatives:** a ledger sub-cap of ≤ 20 for rehearsals (review m8). It would need a second ledger plan name for one rule that the order of the calendar already enforces.
- **Rationale:** a must-have capture can never lose its calls to a rehearsal, and the chat on stage is capped by a number the presenter can see.
- **Promotes to ADR:** no

### [42] The committed demo script keeps placeholders; the filled numbers stay in the kit (review m7)

- **Decision:**
  - `docs/demo-script.md` holds placeholders only (`{BUBBLE}`, `{PAIR}`, `{F.*}`), with no team name and no kit number.
  - `make kit-facts` writes the filled copy `demo-script.filled.md` into the private kit.
  - U8 also covers the team names and marks in the private screenshots and video, and the third-party dependency inventory.
- **Alternatives:** an "Expected numbers" section filled in the committed file (the original 8.1).
- **Rationale:** the same rule as `docs/benchmark.md`, which holds no team-level result. The repository is meant to go public.
- **Promotes to ADR:** no

## Assumptions to verify

| # | Assumption | Verify | If wrong |
|---|---|---|---|
| Q1 | CFBD's 2026 regular weeks end on Saturdays Oct 31 (w9), Nov 7 (w10), Nov 14 (w11) and Nov 21 (w12), and each next week's first kickoff is on the Tuesday after. Derived from Phase 4's stored schedule, with Army–Navy in week 15 on Dec 12 | Task 10.2: the 2026 rows of `CFB_MODEL.SEASON_WEEK` (or `CFB_CORE.CALENDAR`) after Phase 2's backfill | Shift the cadence, the windows and the kit names by the offset; the rules (latest completed week) are unchanged |
| Q2 | The demo is in the week of Nov 16–20 (the orchestrator's reading of "around mid-November"), so the stage kit is `2026-w10-w11` | U6 (non-blocking) | Use the newest kit whose replay week is the latest completed week on demo day: `2026-w09-w10` before Nov 15, `2026-w11-w12` (10.6, 11.9) after Nov 22; move the freeze's `UNTIL` |
| Q3 | Phase 6's session cookie has one name for every port | Read the merged `app/backend/security.py` in task 4.1 | Nothing to change if it already carries the port; the scenario still pins it |
| Q4 | Phase 6's background-action machinery (action ids, follow key, run-id lookup) generalizes to a second action kind | Task 4.1, on the merged code | The dial stays CLI (`make simulate TARGET=aws` + `make publish`), with App B following the publication; the card is unaffected |
| Q5 | Phase 7's turn-log lines (`chat/turn-log`: tool calls with validated inputs, app actions, answer) are enough to replay a turn, and tool calls can be grouped into model steps by their order within the line's `calls` | Task 5.3 (only with a key), on the merged code | Replay one tool call per step (the answer text is verbatim either way); if a field is missing, add it to the turn-log writer in 5.3 (X8-d) |
| Q6 | Claude Desktop on Windows runs `wsl.exe -e <absolute uvx> exasol-mcp-server@<version>`, and `WSLENV` passes the `EXA_*` variables into WSL, where the server reaches `localhost:8563` with `EXA_SSL_CERT_VALIDATION=false` | Task 11.6 with U4 (about 5 minutes) | Windows-native `uvx` with WSL localhost forwarding, or Claude Code in WSL (`claude mcp add exasol-cfb …`) as the client |
| Q7 | The starter kit's `exakit mcp-doctor` leaves a non-kit entry named `exasol-cfb` alone | After U4: `exakit mcp-doctor --json` (report only; never the plain command, [38]) | Keep the entry in Claude Code's user scope instead, or re-add it after any `mcp-doctor` repair |
| Q8 | Local Exasol starts with at least 8 instances after a restart with free memory | The preflight's `sizing` check at T-2 h | Close more applications and restart; else quote the measured seconds with the instance count (the script states sizing anyway) |
| Q9 | AWS 1,000,000 sims run in ≤ 60 s (median) at the demo point, which has fewer remaining games than Phase 4's week-9 measurement, and 2,000,000 in ≤ 120 s | Task 11.4 | Show the 1M run anyway, with its time; drop the 2M cell from the report |
| Q10 | `make rehearse` takes ≤ 6 min on the laptop (restore ≤ 5 min, Phase 2 target) | Measured in task 11.1 and recorded in the verification document | Reset only between rehearsals and before the show; the stage never restores ([31], [33]) |
| Q11 | Phase 6's console `fan-out` slices are `/games/teams?seasonType=both&team=Michigan&year=2022`, `2023` and `2024` | `make kit` reads them from the merged console catalog (1.3); until it merges, from `console_fanout_slices` in `demo/demo.yaml` | Record whatever the catalog names; the kit takes the keys from code |
| Q12 | Repeated rehearsals of one kit keep the graded predictions of week N+1 (frozen by the cadence) and freeze identical week-N+2 values (first freeze wins, same inputs). Week-N+2 games already played in the recording ([34] allows a partly played week) become reconstructions on replay, and `kit.json` counts them | `test_two_rehearsals_same_pages` and task 11.1's `kit-facts` diff | Report the difference in `kit-facts`, and freeze week N+2 in the cadence too (`make predict WEEK=N+1` on local before kickoff) |
| Q13 | CFBD's terms allow publishing the pinned OpenAPI spec (`vs/openapi/cfbd-openapi-5.33.0.json`) and derived numbers in `docs/` | U8 (CFBD terms or a question to CFBD) | `make generate` downloads the pinned spec by URL and checks its SHA-256 instead of committing it (the generated catalog stays) |
| Q14 | The flattened `GAMES` table carries the week, season type, completion flag and both teams' classification, so the week checks can run on the recorded body, on the snapshot CSV and on AWS's stored rows | Task 1.3, against `vs/catalog/catalog.json` | Use the columns that exist (for example, FBS membership from `CONFERENCES_AFFILIATIONS` in the snapshot); the rule stays "every FBS game" |
| Q15 | The laptop needs no `CFBD_API` connection for the default stage mode ([31]) | — | It is needed only for the override (U11) or the Q17 fallback (U2) |
| Q16 | An Anthropic key arrives before Nov 15 | U3 | The stub-mode beat with suggestions (decision [15]), or the coda alone in tier B. Phase 7's 7.2/7.3 stay open in task 11.6 and run once the key exists (P8-c). Task 5.3 is not built, and the recorder drops `demo/chat-beat`'s two transcript scenarios |
| Q17 | A snapshot taken on AWS (2026.1.1) restores exactly on the laptop (2026.2): Phase 2's fingerprints, including the DOUBLE text-form rule [41], match across versions | Gate on Sat Oct 31: Phase 2 task 9.5, or task 10.1 | Decision [30]: a 0-call fix first; then U2 by Nov 1 and the laptop takes the snapshots (local backfill ≈ 217 calls on its own ledger line; 12 calls a week here) |
| Q18 | Files copied into a new subdirectory `cfbd/kits/<kit>/` of the existing `default` bucket are readable by UDFs within a second (probe 1.4k covered files in an existing bucket; `exasol-bucketfs`: a fresh upload is usable once synchronized) | Task 3.3: `make rehearse` does one replay read of the smallest recording after the switch | Wait and retry for up to 10 s; if still unreadable, install kits under the default `cfbd/replay` path with a kit-specific subdirectory created at deploy time |
| Q19 | `ALTER VIRTUAL SCHEMA … SET` neither hangs nor breaks another session's open transaction that read the virtual schema, and the new mode applies to every session's next query | Probe in task 3.2 (a), on the `TEST_` namespace, 0 calls | If it waits: `make demo-mode`'s 10 s limit fails with the reason, and the script switches modes only between beats with App A idle. If it rolls back the reader: the same rule. If the mode does not reach other sessions: `make demo-mode` also restarts App A's read pool (the app reconnects per action anyway) |
| Q20 | After `KILL STATEMENT` of a running `REFRESH_WEEK`, the session is idle with its transaction open. The app's action then closes its connection, which ends it, and a refresh from another session neither waits nor collides once that session is closed | Probe in task 3.2 (b); test of task 3.6 | If a refresh collides while the killed session is still open: `make demo-abort` ends an app-started run's session with `KILL SESSION` after its wait. In tier C the presenter runs `ROLLBACK` in the SQL client |
| Q21 | Phase 6's refresh progress can carry a deadline field without breaking its follow rules | Task 3.6, on the merged code | The frontend shows the notice from its own timer, based on the timeline's `FETCH` steps |
| Q22 | The Claude Desktop configuration is readable from WSL under `/mnt/c/Users/*/AppData/Roaming/Claude/` | Task 1.1 on this machine | `CLAUDE_DESKTOP_CONFIG` names it; otherwise a `WARN` "not checked" (its password equals `.env`'s `CFB_CHAT_PASSWORD`, which is checked) |

## Review Findings

<!-- Populated by speq-implement after code review. -->

### [43] Light replan: keep the dated captures and the stage path, cut the measuring machinery (2026-10-27)

- **Decision:** `tasks.md` replaces the task list. Kept: the freeze and the hard rules (Oct 31), the Sunday captures, a thin `make weekly`, a merged `make rehearse` with `make demo-abort`, a checklist preflight, the tier decision, the docs and the rehearsals. Cut or deferred: `CFB_BENCH`, bench sync, the scale card and dial button, the benchmark harness, transcript replay, kit facts/previews tooling, a separate `demo-mode`, and the release check beyond the freeze.
- **Alternatives:** the reviewed heavy plan (46 tasks); dropping the kits and rehearsing live.
- **Rationale:** the owner switched to the light process on 2026-10-23 and asked for a usable app on real data first. What cannot be redone later (the Sunday captures, the freeze before the first kit) stays; what only measures or polishes is done by hand or after the demo. The release check guards the Labs publication, an owner decision after the demo.
- **Promotes to ADR:** no

### [44] The freeze pins content digests of the working tree

- **Decision:** `demo/freeze.yaml` pins, per frozen path, a SHA-256 over the sorted `(path, git blob id)` pairs of the files that git would track there (`git ls-files -co --exclude-standard`), read from the working tree. The rule compares the same digest. The spec's "git tree id" is amended accordingly.
- **Alternatives:** commit tree ids (`git rev-parse HEAD:<path>`), as the spec said.
- **Rationale:** the owner asked for no commits until his final one. A commit tree id would not see an uncommitted change to a frozen path, which is exactly what the freeze must catch. Blob ids keep the digest independent of line-ending settings and file modes.
- **Promotes to ADR:** no

### [45] Snapshot "after week 8" now, so the first recording builds a kit

- **Decision:** take `aws-2026-w08` on 2026-10-27 at 0 calls, while AWS's point is "after week 8" (run 18) and before week 9's refresh. Nov 1's cadence then records the week-9 slices against it, and kit `2026-w08-w09` becomes the first rehearsal kit, a week earlier than planned.
- **Alternatives:** wait for Nov 1 and record nothing that day (the heavy plan's rule without a w08 base).
- **Rationale:** the base exists only until the week-9 refresh, and the snapshot costs no call. An earlier kit gives one more week to find rehearsal problems before Nov 9.
- **Promotes to ADR:** no

