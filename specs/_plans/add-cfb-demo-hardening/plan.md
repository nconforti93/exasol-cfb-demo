# Plan: add-cfb-demo-hardening

## Summary

Roadmap Phase 8 ("Demo hardening"): turn the platform of Phases 1–7 into a ~25-minute demo that runs the same way every time, live or offline, in the week of Nov 16–20, 2026, and make the repository ready for an Exasol Labs release. Revised on 2026-10-23 after the independent review; `review.md` § Review resolution maps every finding to its change.

- **Captures first.** Three inputs exist only for a few days each week, and nothing done later can recreate them:
  - the AWS snapshot of "Sunday after week N";
  - the recordings of the season slices;
  - the laptop's predictions, frozen before Tuesday's kickoff.

  They form the first group of work. That group needs only Phase 2 on AWS and the foundation's recorder, and runs every Sunday from Nov 1 (`make weekly`, 8 calls). Everything else can be built on any later day from what it captured.
- **Rehearsal kits.** A kit packages a captured snapshot with the recorded responses of the next week's refresh, the console's fan-out and the head-to-head previews. `make rehearse KIT=…` resets the laptop to it in replay mode, in one command. The replayed `REFRESH_WEEK(N+1)` then moves every page at zero calls, with the same numbers every time, and without network.
- **Live proof, replay data.** On stage the laptop refreshes in replay over the kit, so every number in the script is the kit's. The one live API call is a click in the AWS app (the console fan-out). Live data on the laptop is an owner override. In that case a hung or failed refresh is stopped (`make demo-abort`), switched to replay, and refreshed again, with no restore.
- **Demo tiers.** If a predecessor slips, the demo drops a tier instead of failing:
  - Full;
  - B: no chat, and the MCP coda is beat 5;
  - C: no app, so a SQL client and the dial from the command line;
  - D: the virtual schema and the model only.

  The tier is decided on Mon Nov 9.
- **Scale dial.** The laptop runs 100,000 seasons inside its refresh. A second app instance on the laptop, against the AWS database over loopback, runs and publishes 1,000,000 seasons on one click. The inspector's scale card shows both deployments' runs side by side. It reads synced run summaries in a new `CFB_BENCH` schema, so it still works offline.
- **Benchmark, demo script and preflight.**
  - `make bench` measures sims/s, what-if p95 and the refresh steps on both targets, and `make bench-report` writes `docs/benchmark.md`.
  - `docs/demo-script.md` covers beats 1–6 per tier.
  - `make demo-preflight` checks every beat's prerequisites at zero calls.
- **Freeze and Labs readiness.**
  - From Nov 1 until the demo, the stored-data definitions that kits depend on are frozen, and every `make lint` enforces it.
  - README, an MIT `LICENSE` and the CFBD credit.
  - `make release-check` scans the tree and the history for every known secret and for raw data.
  - Publishing stays an owner action.

Everything is tested at zero CFBD and zero Anthropic calls on the integration-test namespace. The live work is calendar-driven in November. It is capped at 70 ledgered calls under the plan `add-cfb-demo-hardening` (already listed in the ledger), with 0 calls in October.

## Design

### Context

The predecessor plans are binding (interview item 1). What each one gives this phase:

- **Foundation** (recorded: `specs/cfbd-vs/`, `specs/cfbd-fetch/`, `specs/decision-log.md`, `specs/_recorded/2026-10-22-add-cfbd-vs-foundation/`):
  - **Fetch modes.** `live` and `replay`; the replay layout `<REPLAY_PATH>/<segments>/<sha256(slice)[:24]>.cfbd.json` with `requested_at`; a missing recording fails loudly.
    - `REPLAY_PATH` is any UDF-visible BucketFS path, and a mode switch takes effect on the next query (`cfbd-fetch/fetch-modes`).
    - Replayed rows carry the recorded status, attempts, latency and request time.
  - **The recorder.** `make record PATTERNS=… CONFIRM=yes` with `--plan`, `--rerecord`, `--query` and `--publish-local`. Recordings made on `aws` stay in `cache/replay/`, because AWS BucketFS has no host directory.
  - **Platform facts.** The BucketFS host directory is mapped into UDFs, and files appear in UDFs 0.1–0.3 s after they are written (probe 1.4k). `make dns-check` is the live gate. `vs/host/target.py` pins TLS for AWS.
  - **AWS.** The `default` Personal instance runs Exasol 2026.1.1 on 3 nodes and holds `CFBD_API`. Deploy and run only: never stop, start or resize it. There is no BucketFS replay path on AWS (`ORCHESTRATOR-STATUS.md`).
- **Phase 2** (`specs/_plans/add-cfb-persistence-refresh/`, worktree `../exasol-cfb-demo-p2`; read with its `review.md` and decision log):
  - **Snapshots.** `make snapshot` / `make restore` as pyexasol gzip CSV, with a fingerprinted, atomic restore at zero calls ([31], [41], [43]).
    - The snapshot scope is `CFB_CORE` + `LOAD_LOG` + `FETCH_LOG`, so its definitions are `sql/core/`, `sql/meta/load_log.sql`, `sql/meta/fetch_log.sql` and the load plan `loader/load-plan.yaml`.
    - The `RESTORE` run row names the snapshot in `RUN_ARGS`.
    - Snapshots live in `~/.cfb-demo/snapshots/` and are refused inside the repo or OneDrive. Restoring is the rehearsal reset (roadmap Phase 2).
  - **Refresh.** `REFRESH_WEEK(season, week, season_type)` re-fetches the current season's non-final season slices (4 calls with the demo plan). It commits each step's log row and each table on its own, and keeps a table's previous data when its fetch fails. It runs in replay at 0 calls.
  - **Run lock** (`loader/run-logs`). A `RUNNING` row in `CFB_META.LOAD_RUN` is the lock. It is stale when its session is gone, when it is the caller's own session, or, for script runs, when its session is idle. A script run is one statement, so an idle session means it ended without closing its row (a failure, a `KILL STATEMENT` or an interrupt). The next run sets a stale row to `ABANDONED`.
  - **Ledger.** `vs/live/ledger.yaml` lists the plan caps (`add-cfb-analyses` 6, `add-cfb-app` 12, `app` 60, and `add-cfb-demo-hardening` 70, added by the orchestrator on 2026-10-23) and the month caps (2026-10: 400, 2026-11: 1000). A plan that is not listed is refused. `make refresh` charges the plan `add-cfb-persistence-refresh` (hard-coded `PLAN_NAME` in `scripts/loader_cli.py`).
  - **Demo backfill on AWS.** 217 calls in group J, by 2026-10-31. Its task 9.5 restores an AWS snapshot on the laptop, which is this plan's Q17 gate. Its backfill decision [29] leaves "recording refresh slices for offline rehearsals" to this phase.
- **Phase 3** (`add-cfb-analytical-model`): `CURRENT_AS_OF` is the newest *finished* week, derived from stored data, never from the clock; a partly played week does not move it (`model/as-of`). A restored "Sunday after week N" is therefore at week N on any later date.
- **Phase 4** (`add-cfb-score-engine`):
  - **The 1M scale dial.** Per-target defaults in `SIM_CONFIG`: local 100,000 at 5,000 per batch, AWS 1,000,000 at 10,000.
    - The signature is `SIMULATE(season, week, season_type, n_sims, sims_per_batch, run_seed, params_version, run_label)`.
    - The default seed is season × 1,000 + week sequence, so reruns are deterministic.
  - **Layout.** C, or A if its probe 1.1c says so; this phase reads the run registry only, so the choice does not matter here.
  - **Budgets.** Local 100k ≤ 30 s, AWS 1M ≤ 60 s, both as the median of 3.
  - **Commands and registry.** `make sim`, `make simulate`, `make sim-drop`, `make sim-verify`.
    - The registry fields include `SECONDS_TOTAL`, `SECONDS_ENGINE`, `SIMS_PER_SECOND`, `NODES`, `INSTANCES`, `DB_RAM_GIB` and `DB_VERSION`, and `SIM_RUN_STEP` commits a row per step.
    - The lock kinds `SIMULATE` and `DEPLOY`. The next lock holder sets other `RUNNING` runs to `ABANDONED` and cleans up their outputs (`sim-engine/sim-runs`).
    - The benchmark harness is left to this phase.
- **Phase 5** (`add-cfb-analyses`; its Phase 8 hand-offs):
  - **The rehearsal recipe.** After restoring "Sunday after week N", run `make analyses`, then `make simulate` + `make publish` at week N if the laptop has no week-N publication (0 calls), so the arrows appear after the refresh.
  - **The scorecard headline.** It is a labeled reconstruction unless predictions were frozen live. `FREEZE_KIND` and `IS_HEADLINE`: first freeze wins, and `IN_ADVANCE` needs the freeze before the game's kickoff.
  - **Publications.** `make publish` publishes a manual run (the 1M dial). Predictions and publications are deployment history, not part of snapshots (decision [10]).
  - **Roles.** `make grants` creates `CFB_APP` (`CFB_API`, `CFB_API_LIVE`) and `CFB_MCP` (`CFB_API` only). Users are created separately.
  - **Performance.** `analyses/perf.py` (`make analyses-perf`) is the what-if performance protocol.
- **Phase 6** (`add-cfb-app`; Phase 8 hand-offs and Q14):
  - **AWS.** "AWS" means the app on the laptop against the AWS database, on loopback (Q14, confirmed for this plan). The operator path exists only on loopback; authentication is Phase 8's decision.
  - **The dial.** The scale dial is one more operator allowlist entry.
  - **Actions.** Each action opens its own connection and closes it at the end (`app/access`). Cancelling a refresh is a `KILL STATEMENT`, and Phase 2's stale rules clean up after it (Phase 6 decision log).
  - **Recordings.** The console's `fan-out` (3 slices: `/games/teams?seasonType=both&team=Michigan&year=2022…2024`) is recorded with `make record` under the ledger. AWS is live-only; the rehearsal and the fallback are local.
  - **App behavior.** The refresh plan's week-completeness line says in advance whether the point will move. In replay the quota badge reads "replay · no calls".
  - **Cut line.** Stretch items may move here if its cut line is applied.
- **Phase 7** (`add-cfb-chat`, revised 2026-10-23 with its review resolution; hand-offs P8-a to P8-c):
  - **P8-a, the stage questions.** Fill the `demo` bindings of `questions.yaml`. Beat 5 in `docs/demo-script.md` is built from the must-pass questions:
    - RQ01 sets a what-if;
    - RQ04 explains a swing;
    - RQ10 answers outside the pages;
    - RQ14 or RQ15 is a guardrail aside.

    Must-pass is RQ01, RQ04, RQ10, RQ14 and RQ15, each passing 3 live runs out of 3. Any other question Phase 8 puts on stage joins that list and its three runs. RQ05 is now an app-state question ("odds for the team I'm looking at").
  - **P8-a, also:** the chat on AWS goes into the rehearsal recipe, and stub mode, with its suggestions, is the offline fallback for beat 5.
  - **P8-b, the MCP moment.** 30 seconds in beat 5: "the same read-only role, through the Exasol MCP server, in Claude Desktop". It uses the user guide's client entry `exasol-cfb` (not the kit-managed `exasol`, which `mcp-doctor` restores) with the role `CFB_MCP`, on local only, because the server has no TLS fingerprint pinning. It runs on the owner's Claude subscription, not the API key.
  - **P8-c, the exit without a key.** If the key has not arrived when Phase 8 starts, Phase 7's tasks 7.2–7.3 move here. Beat 5 then runs on the stub model. Once the key exists, Phase 8 runs 7.2–7.3, and may record a live transcript of beat 5 from the turn-log lines, with `docs/chat-verification.md`.
  - **Caps** (`chat/budget`): `CHAT_SESSION_MAX_USD` 2.00 and `CHAT_DAILY_MAX_USD` 20.00 by default. The daily cap spans conversations, restarts and rehearsal runs through one spend file.
  - **The turn log** (`chat/turn-log`) records per turn the question, the app state, each tool call with its validated input and SQL, the app actions and the final answer. Its scripted-model seam (`chat/conversation`) can therefore replay a recorded transcript, but this plan must build that replay mode (task 5.3, conditional on the key).
- **Owner answers** (`ORCHESTRATOR-STATUS.md`): the demo is around mid-November 2026. The rehearsal uses the latest completed week at the time of the run. November is authorized for 1000 calls, October for 400. The Anthropic key will come later.
- **Where the predecessors stand on 2026-10-23** (`ORCHESTRATOR-STATUS.md`):
  - Phase 2 has groups A–H committed in its worktree. Its docs, its review and its live group J (the AWS backfill, one refresh, and the AWS → laptop restore 9.5) must finish by Oct 31.
  - Phase 3 is in its group B.
  - Phases 4–7 are reviewed plans; Phase 4's implementation follows Phase 3.

  Only Phase 2 is certain to be on AWS by Nov 1, and any of Phases 3–7 may still be missing on Nov 9. This plan is therefore ordered by what cannot wait (review M1).
- **Orchestrator decisions on the review** (2026-10-23, the owner away): every major finding accepted or decided, the YAGNI cuts accepted, and U1, U3 and U5 taken over by the orchestrator (decision log, Interview; decisions [27]–[42]).

**Calendar** (2026, from Phase 4's stored schedule; Army–Navy in week 15 on Saturday 12 December; Q1):

| Week | Saturday | Sunday after it | First kickoff of the next week |
|---|---|---|---|
| 9 | Oct 31 | Nov 1 | Tue Nov 3 |
| 10 | Nov 7 | Nov 8 | Tue Nov 10 |
| 11 | Nov 14 | Nov 15 | Tue Nov 17 |
| 12 | Nov 21 | Nov 22 | Tue Nov 24 |

The demo is assumed in the week of Nov 16–20 (U6 confirms; non-blocking). It tells "Sunday after week 10 → refresh week 11" with the stage kit `2026-w10-w11`. A demo before Nov 15 would use `2026-w09-w10`, and one after Nov 22 would use `2026-w11-w12`. November weeks start with weekday (MACtion) games, so a "Sunday after week N" snapshot and the week-N+1 predictions must be taken before Tuesday's first kickoff.

**Dated schedule** (Oct 31 → demo; "capture" rows cannot be redone later):

| Date (2026) | What | Calls (this plan) | Kind |
|---|---|---|---|
| Fri Oct 23 | Plan revised; group B (pure code, `make weekly`, `PLAN=`) may start | 0 | |
| Sat Oct 31 | **Q17 gate**: an AWS snapshot restores exactly on the laptop (Phase 2 9.5). The freeze rule and the `CLAUDE.md` hard rules are in place (1.6) | 0 | gate |
| Sun Nov 1 | **Freeze begins** (`make demo-freeze` after the snapshot; the sessions are told). Cadence week 9: AWS refresh, snapshot `aws-2026-w09` (base of `2026-w09-w10`); recording only if an `aws-2026-w08` base exists | 4 (≤ 8) | capture |
| Tue Nov 3, before kickoff | Week-10 predictions on local, if Phase 5 is deployed (the rehearsal kit's headline only) | 0 | capture |
| Sun Nov 8 | Cadence week 10: AWS refresh, snapshot `aws-2026-w10` (base of the stage kit), record and copy the week-10 slices (for `2026-w09-w10`), the console's fan-out once | 11 | capture |
| Mon Nov 9 | **Go/no-go on the demo tier** (Full, B, C or D) | 0 | decision |
| Tue Nov 10, before kickoff | **Week-11 predictions on local** (Phase 5 `make predict`): the stage kit's honest headline; otherwise the labeled reconstruction | 0 | capture |
| Nov 8–10 | Kit `2026-w09-w10`: build, previews, rehearse, facts twice, offline drill (hard gate) | ≤ 2 | |
| Nov 9–13 | Dress rehearsal #1; benchmark and report; U4 | ≤ 8 | |
| Sun Nov 15 | Cadence week 11: AWS refresh, snapshot, record and copy the week-11 slices (for the stage kit) | 8 | capture |
| Nov 15–16 | Stage kit `2026-w10-w11`: build, previews, facts, preflight, offline drill (hard gate); dress rehearsal #2 | ≤ 10 | |
| Nov 16–20 | **Demo week**: no `make deploy` on the real local namespace; the demo itself (under `app`) | (`app`) | demo |
| Sun Nov 22 | Only if the demo moved later: cadence week 12, kit `2026-w11-w12` | ≤ 10 | capture |
| Sat Nov 21, or the day after a later demo | Freeze ends (`UNTIL` of `demo/freeze.yaml`) | 0 | |

**Forces**
- **Determinism vs. liveness.** The story's punchline is a live API call, but a 25-minute demo cannot depend on a conference network. A live refresh in the demo week would also fetch newer data than the kit (weekday results, Tuesday's CFP rankings), so the script's numbers would be wrong on screen. The only offline source of week N+1's data is a recording, and a recording costs the same 4 calls as the refresh it replaces.
- **Time-bound data.** "Sunday after week N" exists for about two days a week. A snapshot taken after week N+1's games has nothing left to refresh. A recording taken before week N+1's last game is incomplete, and one taken after week N+2 ended moves the point too far. None of these can be fixed later without new calls.
- **Honest grades.** Predictions are frozen once, per deployment, and are not in snapshots. Only a laptop that froze week N+1's predictions before kickoff can show "the model went 41–9" as a forecast.
- **Predecessors in flight.** Phases 3–7 are implemented in the same weeks as the captures. A kit breaks if the definitions under its snapshot change, and a demo breaks if it assumes a phase that is not merged.
- **Two deployments, one stage.** The laptop holds the story, AWS holds the scale. The app's operator path is loopback-only by design, AWS has no replay, and the venue network may fail.
- **Quota.** October is tight: 400, with 21 spent and ≈ 221–300 planned by Phase 2. November has 1000, with ≤ 6 + 12 + 60 planned by Phases 5 and 6. The CFBD key's monthly pool is shared (Academic tier). Every call goes through the ledger.
- **A public repository.** CFBD terms forbid redistributing raw data. Secrets live in deployment files, `.env`, `~/.exapump`, the starter kit's credentials, the Claude Desktop configuration and one database connection. The history of a local repository has never been audited.
- **This machine.** `~/.exasol/personal/deployments/default` is the AWS instance, so any `exakit` runtime command would act on AWS. Local Exasol is driven only with `exasol stop/start -d local`.
- **Process.**
  - No database session, CFBD or Anthropic call during planning.
  - `docs/roadmap.md`, `specs/architecture.md` and `CLAUDE.md` change only through tasks.
  - Local integration tests run in "local free" windows under the IT lock.
  - Exasol claims are checked with the `exasol:exasol-database`, `exasol:exasol-bucketfs`, `exasol-mcp` and `exasol-runtime` skills and context7 (`/exasol/mcp-server`).

**Goals**
- **Captures** (group A): every Sunday from Nov 1, the AWS snapshot, the recordings (copied at once to `~/.cfb-demo/recordings/`) and the local predictions before kickoff, through one command (`make weekly`, `demo/season-cadence`) that checks the data before it pays.
- **Kits:** build (zero calls, three content-checked weeks, private), list, install in one command (`make rehearse`), and switch modes in one command (`make demo-mode`), with replay refused on AWS and a switch refused during a run.
- **A deterministic replay:**
  - two rehearsals of one kit give equal pages;
  - a rehearsal needs no network or key;
  - a failed, hung or half-done live refresh recovers by abort and mode switch, without a restore.
- **Stage mode** "live proof, replay data", with live data as an owner override.
- **Demo tiers** Full, B, C and D, each with its script and preflight, and a go/no-go on Nov 9.
- **The scale dial in the app:**
  - an operator allowlist entry, at 0 calls;
  - the scale card side by side, in the inspector (`CFB_API.SCALE_RUNS`, `SCALE_RUN_STEPS` over `CFB_SIM` and `CFB_BENCH`);
  - `make bench-sync` of run summaries;
  - separate sessions for two app instances, and the AWS instance's refresh off.
- `make bench` with three suites, and `make bench-report` → `docs/benchmark.md`, with every mission and Phase 4 target marked met or missed.
- `make demo-preflight`: every beat of the chosen tier checked at zero calls, including the script's `sql demo` blocks, the recordings every replayed query will request, and the deployment against the kit's snapshot.
- **Beat 5** on the kit, with Phase 7's must-pass questions on stage: the bindings check and the stub on the kit offline. Once a key exists, a recorded live transcript that replays only on its kit's replayed data.
- **A freeze** of the snapshot scope's definitions from Nov 1 until the demo, enforced in every `make lint` and by the preflight.
- **`docs/demo-script.md`:** beats 1–6 per tier, with clicks, SQL, the expected numbers as placeholders filled per kit, timing, fallbacks and reset; plus the cadence and the kit recipe.
- **Labs readiness:** README, MIT `LICENSE`, the CFBD credit, and `make release-check` (tree, history, known secrets from every home), with a fast subset in `make lint`.
- **AWS:** the release commit deployed with every schema, users, `CFB_BENCH`, the cadence, a preflight.
- **Live budget:** ≤ 70 ledgered calls for this plan, all in November, plus the demo itself under the app's own plan.

**Non-Goals**
- **Authentication, or a hosted app.** Both apps run on the presenter's laptop on loopback (decision [9]).
- **Changing the AWS instance.** No stop, start, resize or scale-out; the roadmap's "AWS sizing" question stays open for the owner.
- **Live CFBD data on the laptop by default.** It is the owner's override (U11).
- **Publishing.** Pushing to GitHub or exasol-labs, rewriting history, or deleting branches. The release check reports; the owner decides (decision [14]).
- **Recording in-game data**, or the backlog's game-day replay (D9).
- **Synthesizing recordings** from stored rows. Recordings come only from real requests.
- **Replay on AWS** (no BucketFS host directory), and moving kits between machines. A kit is for the laptop that built it; a new laptop builds its own from the snapshot and the recordings copy.
- **A dedicated database user for the external MCP client.** The chat user's role is reused (decision [16]).
- **New analyses or pages.** Phase 6's cut-line stretch items join only if the orchestrator applied that cut (Dependencies).
- **Cut as not worth their cost** (decision [39]):
  - the `load` and `ondemand` benchmark suites, because the report reads those numbers from the logs;
  - syncing `BENCH_METRIC`;
  - a second place for the scale card;
  - the bubble-team heuristic of a `make kit-pick`;
  - a transcript replay before an Anthropic key exists.
- **`PROMOTE()`, mirror migration, `season_week` slicing** (backlog).
- **A third-party license inventory of npm and Python dependencies.** It is an owner action before publishing.

### Decision

The work starts with **captures**. Every Sunday, one command takes the AWS snapshot, the recordings and the laptop's predictions while they can still be taken, and copies the recordings out of reach of later overwrites. Everything built from them can happen on any later day.

The rehearsal is **a private kit: a "Sunday after week N" snapshot plus recordings of the week-N+1 refresh, installed under its own `REPLAY_PATH`**. Building it spends nothing, and the kit checks three weeks from the data itself, not the clock.

On stage the default is **live proof, replay data**. The laptop replays the kit, and the live call is the AWS app's console fan-out. Live data on the laptop is the owner's override. There, a failed or hung refresh is stopped by `make demo-abort` (a `KILL STATEMENT`; Phase 2's stale rules clean up), switched by `make demo-mode`, and refreshed again with no restore. The switch is refused while any run holds the lock.

The demo has **four tiers**, chosen on Nov 9, and every script and preflight check knows which tier it serves.

The **scale dial** is a second app instance against AWS on loopback, with a dial action on the operator path and its refresh off. The side-by-side numbers come from run summaries synced into `CFB_BENCH` on both deployments.

The **benchmark** and the **preflight** read the existing registries and logs. **Labs readiness** is a stdlib scanner over the tree and the history. Its fast subset also enforces the **freeze** that keeps every kit's snapshot restorable.

#### Architecture

```
 CAPTURES (group A; every Sunday in November; `make weekly WEEK=n CONFIRM=yes`, 8 calls, plan add-cfb-demo-hardening)
   runner: the orchestrator's local Claude Code session on the laptop (WSL + AWS secrets); fallback: the owner runs the same command
   AWS:   quota ─▶ REFRESH_WEEK(2026, n) live (4) ─▶ content checks on stored GAMES (0) ─▶ make snapshot NAME=aws-2026-wNN (0)
          ─▶ make record <the 4 season slices> --rerecord (4, only if a kit can use it) ─▶ copy → ~/.cfb-demo/recordings/2026-wNN/
   local: make restore NAME=aws-2026-wNN ─▶ make analyses ─▶ make predict WEEK=n (week n+1 frozen before Tuesday's kickoff)
          ─▶ simulate 100k + publish at n (base publication) ─▶ make bench-sync
 KIT BUILD (any later day; 0 calls, plus ≤ 2 preview recordings per kit)
   make kit NAME=2026-wNN-wMM SEASON=2026 WEEK=n SNAPSHOT=aws-2026-wNN SOURCE=~/.cfb-demo/recordings/2026-wMM
     ├─ checks: every slice recorded; in the recorded /games body week n+1 complete and n+2 not; snapshot has week n done, n+1 unplayed
     └─ ~/.cfb-demo/kits/<kit>/{kit.json, replay/…}   (private, 0700/0600, never in the repo or OneDrive)
 REHEARSAL / DEMO (laptop)
   make rehearse KIT=<kit> CONFIRM=yes [TIER=…]                                                              (0 calls)
     1 check kit, snapshot pin, catalog hash   2 copy recordings → BucketFS …/bfsdefault/default/cfbd/kits/<kit>/
     3 ALTER VIRTUAL SCHEMA CFBD_VS SET FETCH_MODE='replay' REPLAY_PATH='/buckets/bfsdefault/default/cfbd/kits/<kit>'
     4 make restore   5 make analyses   6 base publication at n if missing   7 report   [8 MODE=live: owner override only]
   make demo-preflight KIT=<kit> MODE=replay AWS=yes [TIER=…]                                                 (0 calls)
   App A  make app-serve                                        local :8000 ─ refresh n+1 in replay (0 calls), the kit's numbers
   App B  make app-serve TARGET=aws APP_PORT=8001 APP_REFRESH=off      :8001 ─ live proof: console fan-out (3 calls under `app`)
                                                                             ─ scale dial: SIMULATE 1,000,000 + PUBLISH_RUN (0 calls)
   terminal  make bench-sync after the dial ─▶ App A inspector "Scale": laptop 100k vs aws 1M, both just watched
   Claude Desktop ─ exasol-cfb: wsl.exe -e <uvx> exasol-mcp-server@<v> (CFB_CHAT_WEB, role CFB_MCP) ─▶ local CFB_API (beat 5 coda)
   override (U11): MODE=live ─(fetch past APP_REFRESH_DEADLINE_S, or a failure)─▶ make demo-abort ─▶ make demo-mode MODE=replay ─▶ Refresh
   ladder: per-beat fallbacks ─▶ the lower tier's script for that part ─▶ video
 SCALE CARD   CFB_SIM.SIM_RUN / SIM_RUN_STEP (own runs) ∪ CFB_BENCH.BENCH_SIM_RUN / BENCH_SIM_STEP (other deployment, synced)
              ──▶ CFB_API.SCALE_RUNS / SCALE_RUN_STEPS ──▶ /api/scale ──▶ inspector "Scale" tab
 BENCHMARK    make bench TARGET=… (sims, whatif, refresh) → that deployment's CFB_BENCH → make bench-report (both deployments + their logs) → docs/benchmark.md
 LABS         make lint → release_check --fast (tree + freeze)    make release-check → tree + history + known secrets from every home (never printed)
```

#### Patterns

| Pattern | Where | Why |
|---------|-------|-----|
| Capture first, build later | Group A, `make weekly` | Only the Sunday snapshot, the recordings and the pre-kickoff predictions are time-bound. Separating them means a slipping predecessor costs the demo time, not its data |
| Copy recordings on capture | `~/.cfb-demo/recordings/<season>-wNN/` | `--rerecord` overwrites a slice in `cache/replay/`. The copy keeps each week's version for a kit built days later |
| Check before paying | `make weekly` content checks | A recording a kit would refuse costs 4 calls for nothing. The stored data says so at zero calls |
| A kit is a manifest over existing artifacts | `demo/kit.py`, `~/.cfb-demo/kits/<kit>/kit.json` | Snapshot, recordings and replay are already built and tested. The kit only pins them by SHA-256 and checks that they tell one story. No new data format, no new call path |
| Content-checked weeks, never the clock | `demo/kit.py`, `demo/weekly.py` | "Sunday after week N", "week N+1 complete" and "week N+2 not complete" are facts about the data. Clock rules would fail the tests on later dates and accept a snapshot taken at the wrong moment |
| One `REPLAY_PATH` per kit | `ALTER VIRTUAL SCHEMA … SET REPLAY_PATH` | Recordings of one slice key overwrite each other in `cache/replay/`. A per-kit path freezes the version the story needs, and other kits stay usable |
| Live proof, replay data | App A in replay, App B's fan-out live | Every scripted number stays true on screen, and the live call is still real |
| Tiers instead of hope | `TIER=` in the preflight, the rehearsal and the script | A predecessor that slips lowers the demo by a defined step instead of breaking it |
| Abort by kill, clean up by the next lock holder | `make demo-abort` | Reuses Phase 2's and Phase 4's stale-lock rules and Phase 6's action connections; no new cleanup path |
| Fallback by mode switch, not by restore | `make demo-mode` + refresh again | A failed fetch keeps the previous data, and the refresh re-fetches every non-final slice. Recovery takes seconds instead of a five-minute restore |
| Freeze what snapshots depend on | `freeze` rule in `make lint`; preflight `[kit]` | A kit's snapshot cannot be retaken, so its definitions must not move while it is in use |
| The cadence makes honesty cheap | `make weekly` | Predictions frozen before kickoff cost nothing. They turn the scorecard's reconstruction label into a real forecast record |
| Second instance, not a second target in one backend | App B on port 8001 | The operator path stays loopback-only with one target per process. No cross-target code, no authentication |
| Synced summaries for the comparison | `CFB_BENCH` + `make bench-sync` | The card needs the other deployment's numbers offline. Copying derived numbers is cheap and keeps the app reading one database |
| Measure, copy, drop | `bench/harness.py` | Benchmark runs would otherwise crowd the registry and the scale card. The numbers outlive the runs |
| Executable demo script | `sql demo` blocks + `[script]` check | The script cannot rot unnoticed: every rehearsal runs its SQL |
| Plan free, check everything | `make demo-preflight` | Every prerequisite is checkable at zero calls (`EXPLAIN VIRTUAL`, stored views, files, git trees), so the go/no-go is a command, not a feeling |
| Known secrets from their homes, held in memory | `scripts/release_check.py` | Pattern scans miss real passwords. Reading every home at run time finds the exact values without writing them anywhere. The CFBD key is never extracted (owner rule); the owner may type it at a hidden prompt |
| A fast subset in lint | `make lint` | New secrets, raw data and frozen-path changes are caught at the next lint in any session, not at release time |

#### Key Interfaces

**Make targets** (new; every DB target runs `db-check` first and accepts `TARGET` unless noted)

| Target | Does | Calls |
|---|---|---|
| `make weekly WEEK= [SEASON=2026] CONFIRM=yes [FROM=] [ALLOW_LATE=yes] [FORCE=yes]` | The Sunday cadence (`demo/season-cadence`) | 8 |
| `make kit NAME= SEASON= WEEK= SNAPSHOT= [SOURCE=] [FORCE=yes]` | Builds a kit (`demo/rehearsal-kit`) | 0 |
| `make kits` | Lists kits | 0 |
| `make rehearse KIT= CONFIRM=yes [MODE=replay\|live] [TIER=]` | Resets local to the kit (`demo/rehearsal`); `TARGET=local` only | 0 |
| `make demo-mode MODE=live\|replay [KIT=]` | Switches `FETCH_MODE` / `REPLAY_PATH`; refused during a run; replay refused on `aws` | 0 |
| `make demo-abort [TARGET=]` | `KILL STATEMENT` on the running refresh or simulation run (`demo/rehearsal`) | 0 |
| `make demo-preflight [KIT=] [MODE=] [TIER=] [AWS=yes]` | Readiness checks (`demo/preflight`) | 0 |
| `make kit-previews KIT=` | After a replayed refresh: the preview game ids the server chooses for the pairs of `demo/demo.yaml`, and a patterns file for `make record` | 0 |
| `make kit-facts KIT=` | After a replayed refresh: the script's expected numbers into `~/.cfb-demo/kits/<kit>/facts.json` and `facts.md`, and the filled script `demo-script.filled.md` there; compares with the kit's previous facts and lists every difference (determinism check) | 0 |
| `make demo-freeze FROM= UNTIL=` | Writes `demo/freeze.yaml`: the dates and the git tree id of each frozen path (`demo/release-check`) | 0 |
| `make chat-bindings-check [KIT=]` | `demo/chat-beat` | 0 |
| `make chat-transcript KIT= CHAT=<chat id>[,…]` | Only once a key exists: builds a transcript from the turn-log lines of a live run (`demo/chat-beat`) | 0 |
| `make bench [SUITES=sims,whatif,refresh] [ARGS=]` | `demo/benchmark` | 0 |
| `make bench-sync` | Run summaries both ways between `local` and `aws` (`demo/scale-dial`) | 0 |
| `make bench-report` | Writes `docs/benchmark.md` from both deployments | 0 |
| `make release-check [ARGS="--cfbd-key-prompt"]` | `demo/release-check` (full); no `db-check` | 0 |
| `make refresh … PLAN=` and `make backfill … PLAN=` (changed) | The ledger plan to charge; default unchanged (`add-cfb-persistence-refresh`) | as before |

**`demo/demo.yaml`** (committed; names, ids, weeks and choices only, no CFBD data; filled in tasks 11.2 and 11.5)

```yaml
version: 1
season: 2026
tier: full                              # set at the go/no-go on Nov 9: full | B | C | D (demo/preflight)
stage_mode: replay                      # "live proof, replay data"; live only as the owner's override (U11)
kits:                                   # per kit: choices that depend on the week
  2026-w10-w11:
    bubble_team: null                   # school; the odds/what-if/rooting focus of beats 1 and 4
    whatif_game_id: null                # the bubble team's game in week N+2 (beat 4 toggle; chat RQ01 binding)
    pairs: [[null, null], [null, null]] # head-to-head pairs; the first on stage, the second in reserve
    preview_game_ids: []                # from make kit-previews
    allow_incomplete: []                # postponed or cancelled week-N+1 games, if any
console: [fan-out, contradiction, stored]
console_fanout_slices: []               # pinned slice keys while Phase 6's console catalog is not merged (Q11)
chat_questions: [RQ01, RQ04, RQ10, RQ15]   # Phase 7's must-pass set (RQ14 is RQ15's alternative); nothing else on stage
chat_reserve: [RQ05]                        # on stage only after joining the must-pass list and passing 3/3 live
mcp_question: RQ10                          # asked again in Claude Desktop: same question, same role, another client
```

**Rehearsal, mode and abort** (`demo/install.py`, `demo/mode.py`, `demo/abort.py`; order and rules in `demo/rehearsal`). The statements were checked against the grammar: `alter_virtual_schema_statement_1/2` is `ALTER VIRTUAL SCHEMA schema SET (property = string)+`, and `kill_statement` / `msg_clause` is `KILL STATEMENT [stmt_id] IN SESSION session_id [WITH MESSAGE string]`.

```sql
ALTER VIRTUAL SCHEMA "CFBD_VS" SET FETCH_MODE = 'replay' REPLAY_PATH = '/buckets/bfsdefault/default/cfbd/kits/2026-w10-w11';
ALTER VIRTUAL SCHEMA "CFBD_VS" SET FETCH_MODE = 'live' REPLAY_PATH = '/buckets/bfsdefault/default/cfbd/replay';
SELECT CONNECTION_NAME FROM EXA_DBA_CONNECTIONS WHERE CONNECTION_NAME = :name;    -- name only; the key is never read
SELECT "RUN_ID", "RUN_KIND", "SESSION_ID" FROM "CFB_META"."LOAD_RUN"
 WHERE "RUN_STATUS" = 'RUNNING' ORDER BY "RUN_ID" DESC;                           -- the lock (loader/run-logs)
KILL STATEMENT IN SESSION 4711 WITH MESSAGE 'demo-abort';                         -- the session id is formatted as an integer literal
```

The BucketFS host directory of the kit is `~/.exasol/personal/deployments/local/local/runtime/exa/bucketfs/bfsdefault/default/cfbd/kits/<kit>/` (probe 1.4k). The kit's copy is the source of truth; `make rehearse` rewrites the BucketFS copy from it every time.

**Weekly cadence** (`scripts/demo.py weekly`, `demo/weekly.py`; rules in `demo/season-cadence`; prints each step with seconds; stops at the first failure; `FROM=` resumes without repeating a paid step)

| Step | Command it runs | Calls |
|---|---|---|
| quota | `make quota TARGET=aws`: the shared pool's remaining calls and this plan's ledger room cover 8 + headroom | 0 |
| refresh | `make refresh TARGET=aws SEASON=2026 WEEK=n PLAN=add-cfb-demo-hardening CONFIRM=yes` | 4 |
| checks | AWS stored `GAMES`: base (week n done, no week-n+1 game done) and recording (week n done, n+1 not complete) | 0 |
| snapshot | `make snapshot TARGET=aws NAME=aws-2026-w<NN>` (`-late` when the base check failed) | 0 |
| record | `make record TARGET=aws PATTERNS=<4 season slices> CONFIRM=yes ARGS="--plan add-cfb-demo-hardening --rerecord"`; skipped when its check failed or no week-(n-1) base exists | 4 |
| copy | the recorded files → `~/.cfb-demo/recordings/2026-w<NN>/` (0700/0600, `SHA256SUMS`) | 0 |
| restore | `make restore TARGET=local NAME=aws-2026-w<NN> CONFIRM=yes`, then `make analyses` | 0 |
| predict | `make predict SEASON=2026 WEEK=n` on local (first freeze of week n+1); refused after its first kickoff unless `ALLOW_LATE=yes` | 0 |
| publish | `make simulate SEASON=2026 WEEK=n` + `make publish RUN=<id>` on local, unless a week-n publication exists | 0 |
| sync | `make bench-sync` | 0 |

A step whose command is not deployed yet (`predict`, `publish`, `sync`) is skipped with that reason.

**Missed Sunday** (decision [29]):
- The snapshot can be a kit base until week n+1's first FBS game ends (in practice, Tuesday's kickoff).
- The recording can serve its kit until week n+1 completes.
- A run outside both windows still refreshes AWS. That week's kit is then unavailable, the previous week's kit stays the stage kit, and the decision log records it.

**`CFB_BENCH`** (`sql/bench/*.sql`, rendered with `{BENCH_SCHEMA}`, deployed by `bench/deploy.py` through `vs/host/sqlobjects.py`). Data tables are `CREATE TABLE IF NOT EXISTS`, never replaced. Names are checked against `vs/gen/exasol_reserved_keywords.txt`, which reserves `VALUE`, `SOURCE` and `LOCAL`.

```sql
CREATE TABLE IF NOT EXISTS {BENCH_SCHEMA}.BENCH_SETTINGS (SETTING_NAME VARCHAR(64) UTF8 NOT NULL,
  SETTING_TEXT VARCHAR(2000) UTF8);                       -- DEPLOYMENT_LABEL: laptop | aws (BENCH_LABEL overrides)
CREATE TABLE IF NOT EXISTS {BENCH_SCHEMA}.BENCH_SIM_RUN (DEPLOYMENT_LABEL VARCHAR(40) UTF8 NOT NULL,
  SOURCE_RUN_ID DECIMAL(18,0) NOT NULL, STARTED_AT TIMESTAMP NOT NULL, RUN_PURPOSE VARCHAR(10) UTF8 NOT NULL,  -- DEMO | BENCH
  RUN_ORIGIN VARCHAR(8) UTF8, RUN_LABEL VARCHAR(200) UTF8, AS_OF_SEASON DECIMAL(4,0), AS_OF_WEEK_SEQ DECIMAL(3,0),
  N_SIMS DECIMAL(18,0) NOT NULL, SIMS_PER_BATCH DECIMAL(9,0) NOT NULL, N_BATCHES DECIMAL(9,0), NODES DECIMAL(4,0),
  INSTANCES DECIMAL(5,0), DB_RAM_GIB DECIMAL(7,1), DB_VERSION VARCHAR(40) UTF8, SECONDS_TOTAL DECIMAL(9,3),
  SECONDS_ENGINE DECIMAL(9,3), SIMS_PER_SECOND DECIMAL(12,1), ENDED_AT TIMESTAMP, IMPORTED_AT TIMESTAMP NOT NULL,
  CONSTRAINT BENCH_SIM_RUN_PK PRIMARY KEY (DEPLOYMENT_LABEL, SOURCE_RUN_ID, STARTED_AT));
CREATE TABLE IF NOT EXISTS {BENCH_SCHEMA}.BENCH_SIM_STEP (DEPLOYMENT_LABEL VARCHAR(40) UTF8 NOT NULL,
  SOURCE_RUN_ID DECIMAL(18,0) NOT NULL, STARTED_AT TIMESTAMP NOT NULL, STEP_NO DECIMAL(3,0) NOT NULL,
  STEP_NAME VARCHAR(64) UTF8 NOT NULL, SECONDS DECIMAL(9,3), ROWS_WRITTEN DECIMAL(18,0));
CREATE TABLE IF NOT EXISTS {BENCH_SCHEMA}.BENCH_METRIC (DEPLOYMENT_LABEL VARCHAR(40) UTF8 NOT NULL,
  BENCH_RUN_KEY VARCHAR(40) UTF8 NOT NULL, SUITE VARCHAR(20) UTF8 NOT NULL, METRIC_NAME VARCHAR(80) UTF8 NOT NULL,
  CELL VARCHAR(200) UTF8 NOT NULL, SAMPLES DECIMAL(5,0) NOT NULL, MEDIAN_NUMBER DECIMAL(18,4), P95_NUMBER DECIMAL(18,4),
  MIN_NUMBER DECIMAL(18,4), MAX_NUMBER DECIMAL(18,4), UNIT_NAME VARCHAR(20) UTF8 NOT NULL, NODES DECIMAL(4,0),
  INSTANCES DECIMAL(5,0), DB_RAM_GIB DECIMAL(7,1), DB_VERSION VARCHAR(40) UTF8, MEASURED_AT TIMESTAMP NOT NULL,
  DETAIL VARCHAR(2000) UTF8);
```

**Serving views** (`sql/api/700_scale_runs.sql`, `710_scale_run_steps.sql`). They are deployed with `CFB_API` by `make analyses`, with grain-first comments on every column. Phase 5's per-file reference allowlist gains `{BENCH_SCHEMA}.BENCH_SIM_RUN`, `{BENCH_SCHEMA}.BENCH_SIM_STEP`, `{SIM_SCHEMA}.SIM_RUN` and `{SIM_SCHEMA}.SIM_RUN_STEP`.
- `CFB_API.SCALE_RUNS` has one row per simulation run shown by the scale card:
  - the rows: this deployment's runs of origin `REFRESH`, or `MANUAL` labeled `SCALE DIAL`, of any status, plus the other deployment's imported `DEMO` runs;
  - the columns: `DEPLOYMENT_LABEL`, `IS_OWN`, `RUN_KEY` (label, run id and start), `SOURCE_RUN_ID`, `RUN_STATUS`, `RUN_ORIGIN`, `RUN_LABEL`, `AS_OF_SEASON`, `AS_OF_WEEK_SEQ`, `N_SIMS`, `SIMS_PER_BATCH`, `NODES`, `INSTANCES`, `DB_RAM_GIB`, `DB_VERSION`, `SECONDS_TOTAL`, `SECONDS_ENGINE`, `SIMS_PER_SECOND`, `STARTED_AT`, `ENDED_AT`, `IMPORTED_AT`, and `IS_NEWEST_DONE` (the newest `DONE` run per deployment).
- `CFB_API.SCALE_RUN_STEPS` has one row per step of a run in `SCALE_RUNS`. Columns: `RUN_KEY`, `STEP_NO`, `STEP_NAME`, `STEP_STATUS`, `SECONDS`, `ROWS_WRITTEN`, `STARTED_AT`, `ENDED_AT`.
- Ratios (sims ×, seconds ×) are computed in the backend from two rows. Any division in the views uses `NULLIFZERO(divisor)` (grammar `nullifzero`), because a guarded division can be evaluated eagerly on 2026.1.x.

**App additions** (Phase 6 code; `app/backend/ops/scale.py`, `routes/scale.py`, `sqlcatalog.py`, `security.py`, `settings.py`; frontend `src/scale/`, the refresh timeline)

| Item | Detail |
|---|---|
| `GET /api/scale` (read) | `SCALE_RUNS` rows with `IS_NEWEST_DONE` per deployment, and their `SCALE_RUN_STEPS`; the running dial run when one exists |
| `POST /api/scale/plan` (operator) | Target, point, configured sims and batch, estimate, lock state, `can_run`, reasons; 0 calls, no lock |
| `POST /api/scale/run` (operator) | `{season, week, season_type, n_sims}` → re-plan, background `SIMULATE` + `PUBLISH_RUN`, 202 `{action_id}` |
| Operator allowlist | `SELECT … FROM CFB_SIM.SIM_CONFIG`; the `LOAD_RUN` running check; `EXECUTE SCRIPT "CFB_INFRA"."SIMULATE"(<int>, <int>, 'regular'\|'postseason', <int>, <int>, NULL, NULL, 'SCALE DIAL')`; `EXECUTE SCRIPT "CFB_INFRA"."PUBLISH_RUN"(<int>)`; the run-id lookup `SELECT "RUN_ID" FROM CFB_SIM.SIM_RUN WHERE "SESSION_ID" = <int> AND "RUN_LABEL" = 'SCALE DIAL' ORDER BY "RUN_ID" DESC LIMIT 1` |
| `GET /api/config` | `scale: {enabled, reason, target_label, n_sims, sims_per_batch}`; `refresh.enabled` and its reason |
| `APP_REFRESH=off` | Refresh disabled with "refresh is off on this instance"; its plan and run requests answer 403 `refresh_off`. App B always runs with it |
| `APP_REFRESH_DEADLINE_S` (default 45) | When the refresh's `FETCH` steps have not all ended by then, the refresh progress carries the notice with `make demo-abort` and `make demo-mode MODE=replay KIT=<kit>` |
| Session cookie | Name `cfb_session_<APP_PORT>` |
| Errors | 409 `refresh_running` / `refresh_locked` (holder named), 400 `scale_size` (size not the configured one), 403 (no operator path, or `refresh_off`) |
| Frontend | `ScaleCard` in the inspector's "Scale" tab: two columns, step bars aligned by name, ratios, "measured … · imported …", "not measured yet". `DialButton` with the confirmation from the plan ("Run 1,000,000 seasons on aws · 0 CFBD calls · ≈ <estimate> s", or "about a minute"), disabled reasons, and progress from `SCALE_RUN_STEPS` (1 s poll while running). The refresh button's off state, and the deadline notice in the timeline |

**Benchmark matrix** (`bench/matrix.yaml`)

```yaml
repeats: 3
whatif_repeats: 20
sims:
  local: [{n_sims: 100000, sims_per_batch: 5000}, {n_sims: 100000, sims_per_batch: 10000},
          {n_sims: 100000, sims_per_batch: 1000}, {n_sims: 10000, sims_per_batch: 1000}]
  aws:   [{n_sims: 1000000, sims_per_batch: 10000}, {n_sims: 100000, sims_per_batch: 1000},
          {n_sims: 2000000, sims_per_batch: 10000}]       # the "1M+" cell; dropped from the report if > 120 s (Q9)
```

**Demo script outline** (`docs/demo-script.md`, tier Full). The committed file holds placeholders only: `{N}`, `{N1}` = N+1, `{N2}` = N+2, `{BUBBLE}`, `{PAIR}`, and `{F.*}` for kit facts. `make kit-facts` writes the filled copy `demo-script.filled.md` into the private kit (review m7). Its column "replay" holds the kit's numbers; the column "live" says what is read on screen and never quotes a fixed number.

| Beat (min) | Screen and clicks | SQL / statement shown | Expected: replay (kit facts) | Expected: live (read on screen) | Fallback |
|---|---|---|---|---|---|
| 0. Pre-show (T-1 day … T-5 min) | **T-1 day:** the newest cadence done, the stage kit built, `make bench-sync`. **T-2 h:** restart local Exasol for full sizing (`exasol stop -d local`, `exasol start -d local`; never `exakit`), `make app-build`, `make rehearse KIT=… CONFIRM=yes`, `make demo-preflight KIT=… MODE=replay AWS=yes`. **T-30 min:** the preflight again (exit 0) and the tier from `demo/demo.yaml`; `.env` holds `CHAT_DAILY_MAX_USD=3.00` and `CHAT_SESSION_MAX_USD=2.00`; start App A (:8000), App B (:8001, `APP_REFRESH=off`) and Claude Desktop. Browser tabs: App A odds, App A inspector, App B console, App B odds. One terminal tab in the repo with three lines pasted and ready: `make bench-sync`, `make demo-abort`, `make demo-mode MODE=replay KIT=…` | — | preflight exit 0 | — | any FAIL → its fix line; a missing part → that part from the lower tier's script |
| 1. Hook (2) | App A, Odds at "2026 · week {N}". Focus `{BUBBLE}`: P(CFP), seed distribution, arrow since week {N}-1. Footer: `local · replay` and the CFBD credit | — | `{F.bubble_p_cfp_N}`, top 3 `{F.top3_N}` | the same (nothing has refreshed yet) | app does not load → `make app-serve` again (5 s; built at T-2 h); DB down → video chapter 1 |
| 2. The API is a schema (5) | **App B (aws) console.** (a) Narrate the generated `CREATE VIRTUAL SCHEMA` (93 endpoints → 137 tables, no mapping code). (b) Run `fan-out` **live**: plan = 3 slices shown as HTTP requests, confirm the cost (3 calls), waterfall in parallel. This is the demo's live proof. (c) Run `contradiction`: 0 slices, 0 calls. (d) Run `stored`: the season slices, stored-only, 0 calls | `sql show` of the `CREATE VIRTUAL SCHEMA` text; the three console statements (`sql demo` blocks, as in the console catalog) | — | the fan-out's milliseconds and rows per slice; the `stored` row count | App B's fan-out slow or failing → the same query in App A's console (replay, same look, "replay · no calls"), and say so; AWS unreachable → App A only |
| 3. One call refreshes everything (5) | **App A** header → Refresh. Plan: "2026 week {N1} · 0 calls (replay) · the point will move to week {N1}" → Confirm. Timeline (1 s): CHECK, FETCH ×4 (`REPLAY`, recorded Sunday), MODEL, PREDICT, SIMULATE (100,000 seasons), PUBLISH; the inspector shows the 4 replayed requests with their recorded latency. **App B** (aws, already at week {N1}): Scale dial → confirm ("≈ … s"); the dial's steps. **Terminal:** `make bench-sync`. **App A** inspector → Scale: laptop 100k vs aws 1M, both just watched | `sql show`: `EXECUTE SCRIPT CFB_INFRA.REFRESH_WEEK(2026, {N1}, 'regular')`; `SIMULATE(2026, {N1}, 'regular', 1000000, 10000, NULL, NULL, 'SCALE DIAL')` | refresh total `{F.refresh_s}` s (< 120), SIMULATE `{F.sim_s_local}` s on `{F.instances_local}` cores | AWS 1M seconds on 3 × 4, the ratio | a replayed refresh cannot reach the network: on a failure, Refresh again once, else video chapter 3. Dial fails or AWS unreachable → App A's Scale tab shows the last synced AWS run ("measured <date>"). `bench-sync` fails → the same, say "measured Sunday". Override (live data): the deadline notice → the two pasted lines, then Refresh again |
| 4. Pages move (8) | Odds: arrows since week {N}, `{BUBBLE}` moved by Δ pp (1.5). What-if: toggle `{BUBBLE}` wins game `{F.whatif_game}` → conditional odds + effective sample; add a second toggle; rooting guide for `{BUBBLE}` (2.5). Predictions: the scorecard headline with its label and the line beside it; this week's predictions (1.5). Upsets: upset of the week with "T-n since 1978" (1). Head-to-head `{PAIR}`: series, meetings, next meeting with prediction and stakes; "Fetch live preview" (replay, 0 calls) (1.5) | none typed; the inspector shows each page's SQL on request | `{F.bubble_p_cfp_N1}` (Δ `{F.bubble_delta}`), what-if `{F.whatif_p}` on `{F.whatif_sample}` seasons, headline `{F.headline}` (`{F.headline_kind}`), upset `{F.upset}` rank `{F.upset_rank}`, series `{F.series}` | override only: every number read on screen; reserve live click: the same preview in App B (1 call) | what-if slow → narrate the unconditional guide; preview fails → skip it; headline reconstructed → say so (the label is on screen) |
| 5. AI (4) | App A chat (suggestions shown): RQ01 (sets the toggle; the odds page follows; Undo chip), RQ04 (the swing since week {N}), RQ10 (a season record outside the pages); open "SQL" under one answer; aside (20 s): RQ15 ("delete last week's predictions") → the chat says it is read-only. Coda (30 s, P8-b): Claude Desktop, server `exasol-cfb`, ask RQ10's text again; show the tool call's SQL over `CFB_API` with the same read-only role `CFB_MCP` | the disclosed statements | the stub's answers (`{F.chat_*}`), or Claude's answers over the kit's data | — | no key or API error → the transcript (only if one was built and its guard holds), else the stub with suggestions; daily cap reached → stub; no network → skip the coda and say so; laptop chat broken → App B's chat (live), or the coda alone (tier B) |
| 6. Close (1) | Back to App A odds. One platform: the API is a schema, Python in the database at any scale, AI over MCP on a read-only role. Repo link (once published), CFBD credit | — | — | — | — |
| Reset (≤ 6 min, measured in 11.1) | `make rehearse KIT=… CONFIRM=yes`, `make demo-preflight …`, restart App A; AWS needs nothing (the dial's publication stays; the next cadence supersedes it) | — | preflight exit 0 | — | restore fails → it changed nothing; rerun, then the video |

**Tier scripts** (`docs/demo-script.md` has one section per tier; their `sql demo` blocks carry `tiers=`; decided at the go/no-go on Nov 9, decision [28]):

| Tier | Needs | Beats |
|---|---|---|
| Full | Phases 2–7 | 1–6 as above |
| B, no chat | Phases 2–6 | 1–4 and 6 as above. Beat 5 is the Claude Desktop coda alone: no API key, local, on the owner's subscription. Without U4 it is dropped |
| C, no app | Phases 2–5 | A SQL client (the exapump shell or DBeaver) on local, as the owner, in replay. Beat 1: `CFB_API.PLAYOFF_ODDS` for `{BUBBLE}`. Beat 2: the three console statements as `sql demo tiers=C,D` blocks (the fan-out replays; the live proof is dropped). Beat 3: `EXECUTE SCRIPT CFB_INFRA.REFRESH_WEEK(2026, {N1}, 'regular')` in the client and its returned timeline, then the dial in the terminal (`make simulate TARGET=aws SIMS=1000000` + `make publish TARGET=aws RUN=<id>`, with its printed step times). Beat 4: `PLAYOFF_ODDS`, `SCORECARD_WEEKS`, `UPSETS`. Beat 5: the coda. After a `make demo-abort` (override only), `ROLLBACK` in the client before the next refresh |
| D, VS and model | Phases 2–3 | Beats 2 and 3 in the SQL client: the console statements, the replayed refresh, then the model's views. No odds, no dial, no beat 5. It is told as "the API is a schema" |

**Fallback ladder:**
1. With the override only: live → `make demo-abort` → `make demo-mode MODE=replay` → refresh again.
2. The per-beat fallbacks above.
3. The lower tier's script for the part that broke.
4. The recorded video of a full rehearsal, with a chapter per beat (owner action U7).

The video and the screenshots stay private (`~/.cfb-demo/kits/<kit>/video/`).

**Live budget and calendar** (plan `add-cfb-demo-hardening`; every call through the ledger; 0 in October). The cadence costs 8 calls a week, 4 for the AWS refresh and 4 for the recording, because AWS cannot keep its own responses (no replay directory).

| When (2026) | Step | Calls |
|---|---|---|
| Sun Nov 1 | Cadence week 9: refresh 4. The recording (4) only if an `aws-2026-w08` base exists. Until task 2.4 lands, `make refresh` charges `add-cfb-persistence-refresh` (79 calls of room after its group J) | 4 (≤ 8) |
| Sun Nov 8 | Cadence week 10 (8) + the console's 3 fan-out slices (once) | 11 |
| Nov 8–10 | Kit `2026-w09-w10`: ≤ 2 previews | ≤ 2 |
| Nov 9–13 | Dress rehearsal #1 (`APP_LEDGER_PLAN=add-cfb-demo-hardening`): App B fan-out 3 + preview 1; with U2, the override drill on the laptop: refresh 4 | ≤ 8 |
| Sun Nov 15 | Cadence week 11 (8) | 8 |
| Nov 15–16 | Stage kit: ≤ 2 previews; dress rehearsal #2 ≤ 8 | ≤ 10 |
| **Planned (demo Nov 16–20)** | | **≤ 43** (≤ 47 with the Nov 1 recording) |
| Sun Nov 22 (only if the demo is later) | Cadence week 12 (8) + ≤ 2 previews | ≤ 10 |
| **Cap** (`vs/live/ledger.yaml` `add-cfb-demo-hardening: 70`; retry headroom of `MAX_ATTEMPTS − 1` per request) | | **70** |
| Demo day | App B's fan-out (3) and a reserve preview (1); with the override, the laptop's refresh (4) and preview (1). Under the app's plan `app` (cap 60), plus retries | (`app`) |

**Rules for the order of spending** (review m8):
- Each Sunday's capture runs before that week's rehearsals.
- A dress rehearsal starts only when this plan's ledger room after it still covers every remaining Sunday's capture (8 each) plus headroom (`uv run python -m vs.host.ledger --summary`).

November with every plan at its cap: Phase 2 ≤ 300 (all months), Phase 5 ≤ 6, Phase 6 ≤ 12 + `app` 60, this plan ≤ 70, so ≤ 448 of the 1000 authorized. The Q17 fallback would add a local backfill (≈ 217 plus retries, on its own ledger line), for about 670 of 1000.

**Anthropic spend** (≤ $10 for this plan, owner approval U1):
- re-qualifying the must-pass set ≤ $4;
- one live run of beat 5 ≤ $1;
- the AWS chat once ≤ $1;
- demo day ≤ $3, enforced by `CHAT_DAILY_MAX_USD=3.00`, with one spend file shared by both apps.

That is ≤ $9 in total. Phase 7's $20 comes on top if its 7.2–7.3 run here. The MCP coda uses the owner's Claude subscription, not the API key.

**Code layout**

```
demo/__init__.py
demo/demo.yaml          demo choices (committed)
demo/freeze.yaml        freeze dates and tree ids (committed, written by make demo-freeze)
demo/config.py          demo.yaml schema and loader (pure)
demo/kit.py             names, locations, manifest, required slice keys, the three week checks over bodies and snapshot files (pure + files)
demo/weekly.py          make weekly: steps, content checks (shared with kit.py), windows, resume (pure planning + runners)
demo/install.py         make rehearse (BucketFS copy, mode, restore, analyses, base publication, report)
demo/mode.py            make demo-mode (run-lock guard, ALTER VIRTUAL SCHEMA with a 10 s limit, refusals)
demo/abort.py           make demo-abort (KILL STATEMENT, wait for a stale lock)
demo/preflight.py       checks (DB reads, git trees) + pure evaluators, tiers
demo/scriptcheck.py     `sql demo` / `sql show` block parser, `tiers=`, placeholder filling (pure)
demo/previews.py, demo/facts.py, demo/transcript.py
bench/__init__.py, bench/matrix.yaml, bench/deploy.py, bench/harness.py, bench/sync.py, bench/report.py, bench/report_template.md
sql/bench/*.sql, sql/api/700_scale_runs.sql, sql/api/710_scale_run_steps.sql
scripts/demo.py, scripts/bench.py, scripts/release_check.py
app/backend/ops/scale.py, app/backend/routes/scale.py; app/frontend/src/scale/{ScaleCard,DialButton}.tsx
app/backend/chat/transcript_model.py   CHAT_MODEL=transcript:<path> on Phase 7's scripted-model seam (task 5.3, conditional)
docs/demo-script.md, docs/benchmark.md, README.md, LICENSE
```

**Exasol, MCP and machine claims, checked**

| Claim | Evidence | Result |
|---|---|---|
| `ALTER VIRTUAL SCHEMA s SET p = 'v' q = 'w'` | `exasol-grammar.md` `alter_virtual_schema_statement_1/2` (`"SET" ( property "=" string )+`) | Confirmed; takes effect on the next query (`cfbd-fetch/fetch-modes`) |
| How that `ALTER` behaves against another session's open transaction that read the virtual schema, and that the mode reaches every session | Not covered by the skills | Unverified: probe in task 3.2 (Q19). `make demo-mode` refuses during a run and limits the statement to 10 s. The global effect is harmless: the chat and the MCP coda read only `CFB_API`, which has no network-reaching view (Phase 5 [33]), and App B is another deployment |
| `KILL STATEMENT [stmt_id] IN SESSION id [WITH MESSAGE 'm']` | grammar `kill_statement`, `msg_clause` | Confirmed (syntax) |
| A killed statement keeps its session's transaction; a killed script cannot clean up; the next lock holder sets the run `ABANDONED` | Owner's verified note on 2026.1.0 (`KILL STATEMENT` does not roll back); Phase 2 `loader/run-logs` stale rules and probe 1.1j; Phase 4 `sim-runs` (error `R0003` after a kill) | Consistent. The app's action closes its own connection (`app/access`), which ends the transaction. Probe in 3.2 (Q20) |
| `EXPLAIN VIRTUAL <subquery>` costs 0 calls and lists the planned slices | grammar `explain_virtual_statement`; foundation manifest (`cfbd-fetch/fetch-log`) | Confirmed |
| A replayed fetch row carries the recording's request time | foundation `cfbd-fetch/fetch-modes` (recorded spec) | Confirmed; the transcript guard relies on it |
| `EXECUTE SCRIPT s(args)`; `WITH OUTPUT` discards the table | grammar `execute_script_statement(_2)`; Phase 2 | Confirmed; never `WITH OUTPUT` |
| `NULLIFZERO(number)` | `exasol-grammar-functions.md` `nullifzero` | Confirmed; used for every division (2026.1 eager-division regression, owner's investigation) |
| `CREATE TABLE IF NOT EXISTS`, `PRIMARY KEY` constraint, `MERGE` | grammar `create_table_statement_1`; Phase 2 probe 1.1k | Confirmed |
| `EXA_DBA_CONNECTIONS` lists connection names (DBA) | `exasol-sql.md` system tables | Confirmed; only `CONNECTION_NAME` is selected |
| Reserved words | `vs/gen/exasol_reserved_keywords.txt` (`VALUE`, `SOURCE` and `LOCAL` reserved; `SECONDS`, `SUITE`, `CELL` and `SAMPLES` not) | Avoided |
| BucketFS host directory ↔ `/buckets/bfsdefault/default/…`, new files visible in 0.1–0.3 s | foundation probe 1.4k; `exasol-bucketfs` (a bucket is mounted read-only at `/buckets/<service>/<bucket>/<path>`, usable once synchronized) | Confirmed for files in an existing bucket. `make rehearse` proves it per kit with one replay read of the smallest recording after switching to replay (0 calls; `EXPLAIN` alone reads no recording) (Q18) |
| Exasol Personal sizes cores and DB RAM at each start | `CLAUDE.md` (D19) | The preflight's `sizing` check; restart before the demo |
| `exakit` runtime commands act on `deployments/default`, which is the AWS instance on this machine | `exasol-runtime` (the kit's state lives in `deployments/default`; "never drive the runtime underneath the kit"); the directory listing (review §7) | Hard rule for this project (decision [38]): no `exakit start/stop/status/repair-runtime`; local Exasol only with `exasol stop/start -d local` |
| Plain `exakit mcp-doctor` repairs and may provision on the `default` database; `--json` only reports | `exasol-mcp` skill | Confirmed; only `exakit mcp-doctor --json` (Q7) |
| Exasol MCP server: `EXA_DSN`, `EXA_USER`, `EXA_PASSWORD`, `EXA_MCP_SETTINGS` (`enable_read_query`, `schemas.like_pattern`), `EXA_SSL_CERT_VALIDATION` | context7 `/exasol/mcp-server` (server and DB connection setup) | Confirmed; stdio via `uvx exasol-mcp-server@<pinned version>` |
| The starter kit's `mcp-doctor` restores its managed `exasol` entry; the MCP tool's `SELECT` gate is a keyword check, and the role is the boundary | `exasol-mcp` skill | Entry named `exasol-cfb`; the role `CFB_MCP` is the boundary (Q7) |
| `wsl.exe -e` starts no login shell; `uvx` is `~/.local/bin/uvx`, which only `.profile`/`.bashrc` put on the path; `WSLENV` carries variables from Windows into WSL | Checked on this machine (`command -v uvx`, `.profile`, `.bashrc`); `WSLENV` is Windows/WSL behaviour outside the skills | The entry uses the absolute `uvx` path and `WSLENV` with `/u`; verified with U4 (Q6) |

### Consequences

| Decision | Alternatives Considered | Rationale |
|----------|------------------------|-----------|
| Captures first, as their own group with only Phase 2 and the recorder as inputs; recordings copied on capture; the Phase 7 group optional ([27]) | One chain A → … → I behind Phases 2–7; copying recordings at kit build | Only the captures are time-bound; a slipping predecessor must cost time, not data |
| Kit = snapshot after week N + recordings of the week-N+1 refresh, private, per-kit `REPLAY_PATH` ([1]) | Recordings + the shared default replay path; a snapshot after N+1 with no refresh; recordings synthesized from stored rows | The only deterministic offline source of week N+1; per-kit paths keep versions apart; synthesized bodies would be fabricated recordings |
| Weeks checked from content, not the clock, including "week N+2 not complete" ([2], [34]) | Snapshot creation time vs kickoff times | Clock rules break tests on later dates and accept a snapshot taken after weekday games; a late recording would move the point to N+2 |
| Live proof, replay data; live data as the owner's override ([31], revising [4]) | Live first when the preflight passes; replay only | A live refresh in the demo week contradicts the kit's numbers on screen; one real call still proves "live" |
| Abort by `KILL STATEMENT`, cleanup by the next lock holder; no switch during a run ([33]) | Wait for retries to time out; restore | Seconds instead of minutes; reuses Phase 2/4/6 rules; no mixed live/replay run |
| Four demo tiers with a go/no-go on Nov 9 ([28]) | One demo, hope the predecessors land | A defined degraded demo for every predecessor that can slip |
| Sunday cadence on AWS + laptop predictions in advance, 8 calls, run by the orchestrator ([3], [29]) | Refresh only when building kits; predictions frozen at rehearsal time | Honest headline at 0 extra calls; snapshots for every week; AWS current for the dial |
| Freeze the snapshot scope's definitions from Nov 1 until the demo, in lint and preflight ([35]) | Re-take snapshots after a change; no rule | Snapshots cannot be retaken; a change would silently break the stage kit |
| Second app instance against AWS on loopback with its refresh off; no authentication ([7], [9], [40]) | One backend with two targets; a hosted app with login | No cross-target code; the operator path stays loopback-only (Phase 6 [3]); a misclick cannot move AWS |
| `CFB_BENCH` + `make bench-sync` of run summaries for side-by-side numbers; `bench-sync` on stage after the dial ([8], [40]) | Live read of the peer; two browser windows only; a JSON file the backend reads | Offline-proof; the app keeps reading one database through `CFB_API`; both numbers on the card are the ones just watched |
| Bench measures, copies and drops; three suites ([10], [39]) | Keep bench runs; five suites with copies of logged numbers | Registry and card stay clean; numbers survive; the logs already hold the rest |
| Executable demo script checked by the preflight, per tier, with placeholders only ([11], [42]) | Prose script; filled numbers in the committed file | The script's SQL cannot drift from the schema; no team-level numbers in the public repo |
| Release check: stdlib scanner, tree + history, known secrets from every home in memory, fast subset in lint ([12], [36]) | gitleaks as the gate; `git grep` rules only | No new tool to install; finds the real values, the AWS node key included |
| Publishing and history rewrite stay with the owner ([14]) | Rewrite automatically | Git discipline and the owner's guardrails |
| Ledger plan `add-cfb-demo-hardening` 70, 0 in October; the demo under `app`; captures before rehearsals ([17], [41]) | Charge everything to `app`; no cadence | Each plan's spend auditable; October left to Phase 2; a rehearsal cannot drain a capture |
| Chat beat: Phase 7's must-pass questions on stage; stub fallback; a transcript only after a key, and only on its kit's replayed data ([15], [32]) | Other stage questions; a transcript bound only to the kit and point | No extra live qualification; a transcript never quotes numbers that differ from the screen |
| MCP coda with Claude Desktop through `wsl.exe`, entry `exasol-cfb`, user `CFB_CHAT_WEB`, local only ([16], [37]) | A dedicated user; AWS; Windows-native `uvx` with localhost forwarding | Fewer objects; no TLS pinning in the server; uv and the DSN are already in WSL |
| No `exakit` runtime command on this machine ([38]) | Use the kit's commands | `deployments/default` is AWS here; one wrong command would break the AWS mandate |

## Features

| Feature | Status | Spec |
|---------|--------|------|
| demo/season-cadence | NEW | `demo/season-cadence/spec.md` |
| demo/rehearsal-kit | NEW | `demo/rehearsal-kit/spec.md` |
| demo/rehearsal | NEW | `demo/rehearsal/spec.md` |
| demo/preflight | NEW | `demo/preflight/spec.md` |
| demo/scale-dial | NEW | `demo/scale-dial/spec.md` |
| demo/benchmark | NEW | `demo/benchmark/spec.md` |
| demo/chat-beat | NEW | `demo/chat-beat/spec.md` |
| demo/release-check | NEW | `demo/release-check/spec.md` |

The domain is `demo/`. None of the specs this plan builds on is recorded except the foundation's (`cfbd-vs/`, `cfbd-fetch/`), which this plan does not change. Recorder notes for the predecessors, when they are recorded:
- **`app/refresh`, `app/access`** (Phase 6):
  - the operator allowlist gains the scale dial (`demo/scale-dial`);
  - the session cookie name carries the port;
  - `APP_REFRESH=off` and `APP_REFRESH_DEADLINE_S` join the refresh (`demo/scale-dial`, `demo/rehearsal`).
- **`api/serving-contract`** (Phase 5): `SCALE_RUNS` and `SCALE_RUN_STEPS` join `CFB_API` ("new views may be added").
- **`loader/refresh-week`, `loader/backfill`** (Phase 2): `make refresh` and `make backfill` take `PLAN=`.
- **`chat/rehearsal`, `chat/conversation`, `chat/turn-log`** (Phase 7):
  - the `demo` bindings are filled; the stage set equals the must-pass set, so the list does not grow;
  - once a key exists, `CHAT_MODEL=transcript:<path>` is a new model mode on the scripted seam, and the turn log's `stub` flag gains the value `transcript`. `demo/chat-beat` specifies both until Phase 7's specs are recorded;
  - if no key ever arrives, the recorder drops the two transcript scenarios of `demo/chat-beat` (task 5.3 was not built).

## Requirements

| Requirement | Details |
|-------------|---------|
| Zero CFBD calls outside groups A and K | Unit, CLI and integration tests use fakes, the mock sidecar and synthetic recordings; the harness guard against `CFBD_API` stays. Kit build, rehearse, mode switch, abort, preflight, the cadence's free steps, bench, sync, report and release check spend nothing |
| Live cap | ≤ 70 ledgered calls for `add-cfb-demo-hardening`, all in 2026-11 (0 in October). Every live step goes through `make weekly`, `make record`, `make refresh … PLAN=` or the app with `APP_LEDGER_PLAN`, with `CONFIRM=yes` or the app's confirmation. The demo itself counts under `app` |
| Captures before rehearsals | Each Sunday's capture runs before that week's rehearsals; a rehearsal starts only with room left for the remaining captures |
| Zero Anthropic calls in tests | Stub and transcript models only; live chat runs only in task 11.6, gated on the key and ≤ $10 (owner approval) |
| AWS etiquette | Deploy and run only: no stop, start or resize. No replay on AWS. App B always runs with `APP_REFRESH=off`. `make demo-abort TARGET=aws` stops only this project's own statement. AWS work only outside other sessions' windows |
| This machine | Never an `exakit` runtime command or a plain `exakit mcp-doctor`; local Exasol only with `exasol stop/start -d local` |
| Shared local DB | Integration tests, the cadence's local steps and `make bench TARGET=local` run under `flock <scratchpad>/cfb-it.lock` in "local free" windows. `make rehearse` and `make demo-mode` refuse while a run holds the lock (Phase 2's lock) |
| Freeze | From Nov 1 until the demo, `vs/catalog/`, `sql/core/`, `sql/meta/load_log.sql`, `sql/meta/fetch_log.sql` and `loader/load-plan.yaml` keep their pinned tree ids (`demo/freeze.yaml`). In demo week, no `make deploy` on the real local namespace except through `make rehearse` |
| Privacy | Kits, recordings, recordings copies, snapshots, facts, filled scripts, transcripts and videos stay under `~/.cfb-demo/` (outside the repo and OneDrive). `cache/` stays git-ignored. `docs/benchmark.md` and `docs/demo-script.md` hold derived numbers and placeholders only (no response body, no stored row dumps, no team-level results) |
| Secrets | The sys password is read at connect time (`vs/host/target.py`). App and chat passwords are generated by the orchestrator into `.env` (mode 0600, never printed); the Anthropic key is only in `.env`; the CFBD key never leaves the connection. The release check reads secrets into memory only and never prints them |
| Exasol SQL | Identifiers double-quoted in generated SQL; DDL names unquoted upper case and not reserved; `NULLIFZERO` for every division; `EXECUTE SCRIPT` never `WITH OUTPUT` |
| Performance (targets; measured in 11.1 and 11.4) | `make rehearse` ≤ 6 min on the laptop (restore ≤ 5 min, Phase 2 target); replayed `REFRESH_WEEK` with 100k sims < 2 min (mission); dial at 1M ≤ 60 s plus publication (Phase 4); preflight ≤ 60 s; `release-check --fast` ≤ 5 s; `make demo-abort` ≤ 15 s |
| UTC | Every new timestamp is UTC |
| Coverage | ≥ 90 % line coverage from unit and CLI tests for `demo/config.py`, `demo/kit.py`, `demo/weekly.py`, `demo/scriptcheck.py`, `bench/report.py` and `scripts/release_check.py` |

## Dependencies

- **What each group needs from the predecessors** (details in Parallelization):
  - **Group A (captures)** needs only Phase 2 deployed on AWS (its group J), the foundation's recorder, and the ledger entry (done). Until task 3.5 merges, it runs Phase 2's commands one by one. Its local steps use whatever of Phases 3–5 is deployed, and skip the rest with a note.
  - **Group B** needs nothing, except Phase 2 merged for 1.3, 2.4 and 3.5.
  - **Groups C–F** need the predecessors each task names: Phases 2–4 for the kit and bench objects, Phase 5 for the views, the rehearsal's analyses and the preflight, and Phase 6 for the app tasks.
  - **Group G** needs Phase 7 merged; it is optional and never blocks H–K.
  - A task removed by the cut line or by the tier decision (11.2) leaves the chain; the next group waits only for the tasks that remain.
- **The predecessor parts used:**
  - **Phase 2:** `loader/snapshot.py` (restore, location rules), `loader/templates.py`, `loader/load-plan.yaml`, `loader/runlock.py`, `scripts/loader_cli.py` / `loader/guard.py`, `vs/host/ledger.py`, the harness snapshot helpers. Its group J (AWS backfill, snapshot, 9.5) before task 10.1.
  - **Phase 3:** `CFB_MODEL.SEASON_WEEK`, `CURRENT_AS_OF`.
  - **Phase 4:** `SIMULATE`, `DROP_SIM_RUN`, `SIM_CONFIG`, `SIM_RUN(_STEP)`, the sim league, `make sim`/`simulate`/`sim-drop`. Its group J (1M on AWS) before 11.4.
  - **Phase 5:** `make analyses`/`predict`/`publish`, `PUBLISH_RUN`, `analyses/perf.py`, the sqlobjects allowlist, `PUBLISHED_RUNS`, scorecard labels, `make grants`.
  - **Phase 6:** the operator action machinery, `app_stack.py` with `refreshed(10)`, the console catalog, `security.py`, the frontend shell and inspector.
  - **Phase 7:** `questions.yaml`, `make chat-rehearse`, the scripted-model seam, the chat stack, `make chat-user`, the user-guide MCP recipe.
- **Inbound hand-offs from Phase 7,** all taken:
  - **P8-a:** the `demo` bindings, beat 5 from the must-pass questions, the chat on AWS in the recipe, and stub with suggestions as the offline fallback (tasks 5.2, 8.1, 11.5, 11.6).
  - **P8-b:** the Claude Desktop moment with `exasol-cfb` and `CFB_MCP` (tasks 8.1, 8.2, 11.6, U4).
  - **P8-c:** the exit without a key, with Phase 7's 7.2–7.3 and, once a key exists, the recorded live transcript (tasks 5.3, 11.6).
- **Cross-plan changes this plan makes itself** (named X8 to keep them apart from Phase 7's P8 hand-offs; in code the predecessors own, after they merge; the orchestrator tells their sessions):
  - **X8-a (Phase 2/6 guard):** `PLAN=` for `make refresh` and `make backfill` (default unchanged) (task 2.4).
  - **X8-b (Phase 5):** the scale views and the allowlist entries for `CFB_BENCH` (task 2.2).
  - **X8-c (Phase 6):** the dial routes and operator entries, the port in the cookie name, `APP_REFRESH`, `APP_REFRESH_DEADLINE_S`, the scale card (tasks 3.6, 4.1, 4.2).
  - **X8-d (Phase 7):** the `demo` bindings and, once a key exists, the transcript model mode on the scripted seam (tasks 5.2, 5.3).
- **The freeze applies to the predecessors too.** From Nov 1, no Phase 2–7 change may touch the frozen paths without the owner's migration decision; their `make lint` fails otherwise (task 1.6), and the orchestrator tells their sessions (10.2).
- **Phase 6's cut line.** If the orchestrator applied it, the moved stretch items and their scenarios join this plan as a group-F task (4.3), with the scenarios added to this plan's verification by the orchestrator. None is demo-critical.
- **Phase 7's exit without a key.** Its tasks 7.2 and 7.3 move here as part of task 11.6, gated on the key. Without a key, beat 5 is the stub, or the coda alone in tier B.
- **Tools.** No new runtime dependency. `gitleaks` is optional (used if installed).
- **Taken over by the orchestrator** (decision [37]):
  - **U1, the ledger entry:** `add-cfb-demo-hardening: 70` is listed (ledger commit `12cbbf1` on the Phase 2 branch).
  - **U3, the passwords and users:** the orchestrator generates random `CFB_APP_PASSWORD`, `CFB_CONSOLE_PASSWORD` and `CFB_CHAT_PASSWORD` into `.env` (mode 0600, never printed), and runs `make app-user` and `make chat-user` on both targets (9.1).
  - **U5, the Sunday cadence:** the orchestrator, from a local Claude Code session on the laptop, which has the WSL and AWS access. If no session has run it by Monday evening, the owner runs `make weekly WEEK=n CONFIRM=yes` himself, a one-command checklist (`docs/demo-script.md` § Cadence).
- **Owner actions** (blocking only the task named; final list):
  - **U1:** approve ≤ $10 of Anthropic spend for this plan (11.6).
  - **U2 (conditional):** create `CFBD_API` on the local deployment with the user-guide SQL (D18). It is due **Sun Nov 1** only if the Q17 gate fails. Otherwise it is needed only for the live-data override (U11), before dress rehearsal #2.
  - **U3:** add `ANTHROPIC_API_KEY` to `.env` when available (5.3, 11.6).
  - **U4:** add the `exasol-cfb` entry to Claude Desktop (`wsl.exe -e <absolute uvx> exasol-mcp-server@<v>`, `WSLENV`, per the user guide), and ask one question, by **Fri Nov 13**.
  - **U6:** confirm the demo date (assumed Nov 16–20; non-blocking) and the network at the venue. Approve the bubble team and the pairs on **Sun Nov 15** (11.5).
  - **U7:** record the fallback video of a full rehearsal, with a chapter per beat, plus screenshots. The first take is at dress rehearsal #1 (by Nov 13), refreshed at #2 if time allows.
  - **U8:** before publishing:
    - confirm the `LICENSE` holder ("Exasol AG") and MIT for exasol-labs;
    - confirm that CFBD's terms allow committing the OpenAPI spec and the derived numbers (Q13);
    - accept the team names and marks in the private screenshots and video;
    - own the third-party dependency inventory.
  - **U9:** decide what ships publicly (`specs/_plans/` orchestration notes, `spikes/`, decision logs), whether to rewrite history after the release check, and when to publish. All of this is outside this plan.
  - **U10:** before the show, one click-through rehearsal in the browser. On demo day:
    - close memory-heavy apps;
    - restart local Exasol with `exasol stop/start -d local` (never `exakit`);
    - use mains power and a backup network.
  - **U11 (optional):** override the stage default to live data on the laptop (`stage_mode: live`; needs U2).

## Migration

| Current | New |
|---------|-----|
| `make deploy`: VS → loader → model → sim → analyses | VS → loader → model → sim → **bench** → analyses (`CFB_API` scale views read `CFB_BENCH`) |
| `vs/host/namespace.py` | + `bench_schema` (`{prefix}CFB_BENCH`); renderer + `{BENCH_SCHEMA}` |
| `tests/it/harness.py` | `drop_namespace`/`leftovers` cover `CFB_BENCH`, the `TEST2_` peer namespace, and test kit paths under BucketFS `cfbd/kits/test-*`; helpers `build_test_kit`, `peer_namespace`, `kit_dir` (pytest tmp path), `recordings_dir` |
| `vs/live/ledger.yaml` | `add-cfb-demo-hardening: 70` (done by the orchestrator) |
| `scripts/loader_cli.py` / `loader/guard.py` | `--ledger-plan` (make `PLAN=`) for refresh and backfill, default unchanged |
| Phase 6 `security.py`, `settings.py` | session cookie `cfb_session_<port>`; `APP_REFRESH`, `APP_REFRESH_DEADLINE_S` |
| `Makefile` | + `weekly`, `kit`, `kits`, `rehearse`, `demo-mode`, `demo-abort`, `demo-preflight`, `kit-previews`, `kit-facts`, `demo-freeze`, `chat-bindings-check`, `chat-transcript`, `bench`, `bench-sync`, `bench-report`, `release-check`; `lint` runs `release_check --fast` last |
| `demo/freeze.yaml` | New, committed on Nov 1 by `make demo-freeze` |
| `CLAUDE.md` | Hard rules: the freeze, no `make deploy` on the real local namespace in demo week, no `exakit` runtime command or plain `mcp-doctor` (1.6); the rest in 8.6 |
| `pyproject.toml` | mypy `files` + `demo`, `bench`; coverage sources + the pure modules |
| `.env.example` | + `CFB_KIT_DIR`, `BENCH_LABEL`, `APP_REFRESH`, `APP_REFRESH_DEADLINE_S`, the demo-day chat caps, and the App B line (`APP_PORT=8001` with `TARGET=aws` and `APP_REFRESH=off`) |
| `README.md` | External-audience README (tasks 1.2, 8.3) |

## Implementation Tasks

Tasks are TDD per `/speq:code-guardrails`: unit tests first for pure modules, the integration (or CLI) test file first, failing, for each DB or CLI task, and component tests first for frontend work. Integration tests run with `flock <scratchpad>/cfb-it.lock make test-it` in "local free" windows at 0 CFBD and 0 Anthropic calls.

1. **Pure code and Labs files (no DB; can start now, except where noted)**
   - [ ] 1.1 `scripts/release_check.py`, the rules of `demo/release-check` beyond the freeze rule of 1.6:
     - the secret, raw-data, release and hygiene rules;
     - the known-secret loader for every home, reading into memory only: every deployment's `secrets.json`, `deployment.json`, `connection-instructions.txt` and `*.pem` (key bodies compared without whitespace), `~/.exapump/config.toml` (`tomllib`, never printed), the starter kit's credentials, the Claude Desktop configuration and `.env`. A missing home is a `WARN`;
     - `--cfbd-key-prompt` (`getpass`, memory only);
     - masked output that never shows 8 consecutive characters of a secret;
     - the history walk over `git rev-list --all` and blobs; optional gitleaks; the JSON report in `cache/release-check/`; `make release-check`.

     Unit tests `tests/unit/test_release_rules.py` (rules over strings, PEM normalization); CLI tests `tests/cli/test_release_check_cli.py` (temporary git repositories, fake home). [expert]
   - [ ] 1.2 `LICENSE` (MIT, "Copyright (c) 2026 Exasol AG", pending U8) and a first external `README.md`:
     - what it is: the three promises;
     - architecture (ASCII);
     - requirements: Exasol Personal 2026.2, Python 3.12 + uv, Node per Phase 6, podman/WSL notes;
     - quickstart: clone; `make deploy`; create `CFBD_API` with your key (user-guide SQL); `make deploy` again and `make grants`; `make backfill PROFILE=demo` (estimate first, ≈ 217 calls); `make refresh`; `make app-user`; `make app-build && make app-serve`; optional chat key;
     - costs in calls; data terms (no redistribution, derived outputs only, private snapshots); "Data provided by CollegeFootballData.com"; not affiliated with the NCAA or the CFP; an Exasol Labs (community-supported) note;
     - links to the user guide, the demo script and the benchmark.

     Final numbers and links come in 8.3.
   - [ ] 1.3 `demo/demo.yaml` + `demo/config.py` (schema, loader, validation: kit names, `tier`, `stage_mode`, pairs, question ids against `questions.yaml` once Phase 7 merged) and `demo/kit.py` (pure parts):
     - names and locations, reusing `loader.snapshot.check_location`;
     - the `kit.json` schema;
     - the required slice keys: refresh keys from `loader.templates` and the deployed load plan's weekly entries for a season; console keys from Phase 6's console catalog, or `console_fanout_slices` until it merges; previews from `vs.cfbd_vs.plan.build_slice_key`;
     - the three week checks over a recorded `/games` body (flattened with `vs.cfbd_vs.flatten`) and over a snapshot's `CFB_CORE.GAMES.csv.gz` (`csv` over gzip, columns by catalog name). The same functions serve the cadence's checks over stored rows.

     Unit tests `tests/unit/test_demo_kit.py`, `test_demo_config.py`. Needs Phase 2 merged (`loader.templates`, `loader.snapshot`).
   - [ ] 1.4 `demo/scriptcheck.py`: parse fenced blocks with info strings `sql demo` and `sql show`, the `tiers=` attribute, their line numbers, placeholder filling from a kit and `demo.yaml`, and the statement-kind rule (reads only). Unit tests `tests/unit/test_demo_scriptcheck.py`.
   - [ ] 1.5 `bench/report.py` + `bench/report_template.md`: render `docs/benchmark.md` from result rows (setup, the suite tables, the restore and on-demand rows read from the logs, the targets with met or missed, method). The output is deterministic and holds derived numbers only. Unit tests `tests/unit/test_bench_report.py` (also the scenario "The report states every target …"). `bench/matrix.yaml` + its loader, with unit tests.
   - [ ] 1.6 **By Sat Oct 31:** the freeze, as the first piece of `scripts/release_check.py`:
     - the `freeze` rule in `--fast` (git tree ids of the frozen paths against `demo/freeze.yaml`, with `--now`);
     - `make demo-freeze FROM= UNTIL=`;
     - `release_check --fast` as the last step of `make lint`.

     Also the `CLAUDE.md` hard rules, as dense bullets in their own commit:
     - the freeze and its paths;
     - no `make deploy` on the real local namespace in demo week except through `make rehearse`;
     - "never run exakit runtime commands or plain `exakit mcp-doctor` on this machine; `deployments/default` is the AWS instance; only `exakit mcp-doctor --json` is safe".

     CLI tests in `tests/cli/test_release_check_cli.py` (the freeze scenario; the fast check reads no secret).

2. **Database objects and plumbing (TEST_ namespace)**
   - [ ] 2.1 `CFB_BENCH` (after Phases 2–4 merge):
     - `sql/bench/*.sql` per Key Interfaces; `bench/deploy.py` through `vs/host/sqlobjects.py`; `make deploy` order VS → loader → model → sim → bench → analyses;
     - `BENCH_SETTINGS` from `TARGET` (`BENCH_LABEL` overrides);
     - `vs/host/namespace.py` `bench_schema`;
     - harness: deploy, drop and leftovers, and a `peer_namespace("TEST2_")` fixture with Phase 4's run-registry data tables, `SIM_CONFIG` and `CFB_BENCH`.

     Integration test file first: `tests/it/test_bench_it.py` (deploy part).
   - [ ] 2.2 `sql/api/700_scale_runs.sql`, `710_scale_run_steps.sql` (after Phase 5 merges): comments, `NULLIFZERO`, Phase 5's allowlist entries, and `make api-docs` regenerated. Its integration tests sit in `tests/it/test_app_scale_it.py` (card scenarios, read through `CFB_API` as the app role).
   - [ ] 2.3 `bench/sync.py` + `make bench-sync` (`--peer-prefix` for tests): both directions, MERGE by key, the `DEMO` runs from the last 60 days with their steps, and the `BENCH_SIM_RUN`/`BENCH_SIM_STEP` rows the other side lacks; never `BENCH_METRIC`; all or nothing when a side is unreachable. Test: `test_bench_sync_runs_both_ways_once` in `tests/it/test_bench_it.py`.
   - [ ] 2.4 `make refresh … PLAN=` and `make backfill … PLAN=` (`--ledger-plan` on the loader CLI or guard, default unchanged), with CLI tests in `tests/cli/test_loader_cli.py`. Needs Phase 2 merged only.

3. **Kits, cadence and rehearsal**
   - [ ] 3.1 `make kit` / `make kits` (`scripts/demo.py`, `demo/kit.py` I/O):
     - build from a snapshot and a source directory (default `cache/replay/`, or a recordings copy), with the three checks;
     - the `make record` hint, modes 0700/0600, `FORCE`;
     - the catalog-hash check reads the deployment's adapter hash.

     Integration test file first: `tests/it/test_demo_kit_it.py`, with the harness helper `build_test_kit` (sim-league snapshot at week 9; synthetic week-10 recordings from Phase 6's `refreshed(10)` machinery, with week 11 unplayed, partly or fully played as the scenario needs; synthetic console and preview bodies). CLI tests `tests/cli/test_demo_cli.py` (location and overwrite rules with a fake connection).
   - [ ] 3.2 `make demo-mode` (`demo/mode.py`):
     - the run-lock guard (Phase 2's stale rules, read through `loader/runlock.py`);
     - `ALTER VIRTUAL SCHEMA` per Key Interfaces on its own connection with a 10 s limit;
     - the connection-name check; the DNS gate (scripts/dns, injectable in tests); the refusal on `aws`; the kit derived from `REPLAY_PATH`.

     **Probe first** (0 calls, local, `TEST_` namespace, under the IT lock), with its results recorded in the decision log (Q19, Q20):
     - (a) Session A, with autocommit off, reads a table of the test virtual schema in replay and keeps its transaction open. Session B runs `ALTER VIRTUAL SCHEMA … SET FETCH_MODE …`. Record:
       - whether B waits, succeeds or fails;
       - whether A's next statement sees a rollback or a collision;
       - whether A's next read and a third session's first read use the new mode.
     - (b) A `REFRESH_WEEK` with a test-hook sleep in a `FETCH` step, started from a pyexasol session, then `KILL STATEMENT IN SESSION`. Record:
       - the session's state (idle, open transaction);
       - whether a refresh from another session waits on or collides with the killed run's uncommitted step, while the killed session stays open and after it closes.

     Tests: `test_live_switch_needs_connection_and_dns_gate` (IT); `test_replay_mode_refused_on_aws_and_without_kit` (CLI); the guard is covered by `test_hung_refresh_aborted_before_switch` (3.6).
   - [ ] 3.3 `make rehearse` (`demo/install.py`):
     - the 8 steps of `demo/rehearsal`, and `TIER=D` skipping steps 5–6;
     - the BucketFS copy with SHA-256 checks, and, after the switch to replay, one replay read of the smallest recording (the UDF sees the files);
     - the restore through `loader.snapshot`, `make analyses`, and the base publication;
     - the report: the headline kind from `CFB_API` scorecard columns, and the recorded completeness from `kit.json`;
     - the live switch, and the refusal while the run lock is held.

     Integration test file first: `tests/it/test_demo_rehearsal_it.py`. It covers:
     - the install, the replayed refresh, and two-rehearsal equality;
     - no network or key (connection access revoked, mock stopped);
     - the failed live refresh → replay recovery (mock 500 on `GAMES`);
     - the failure after a live table committed (mock: changed `GAMES`, then a 500 on `RATINGS_SP`).

     The tier step selection is CLI-tested. [expert]
   - [ ] 3.4 `make kit-facts` (`demo/facts.py`) and `make kit-previews` (`demo/previews.py`). Both read `CFB_API` as the app role at 0 calls.
     - Facts are written as JSON and Markdown in the kit, with a diff against the previous facts, plus the filled demo script `demo-script.filled.md`.
     - The previews are the game ids the server chooses for the pairs in `demo/demo.yaml`, with a patterns file for `make record`.

     The bubble-team choice is a documented `sql demo` query in the script's kit recipe, not code. Integration tests in `tests/it/test_demo_rehearsal_it.py`: `test_kit_facts_equal_across_rehearsals` supports the determinism scenario, and `test_kit_previews_listing`.
   - [ ] 3.5 **By Sun Nov 8:** `make weekly` (`demo/weekly.py`, `scripts/demo.py weekly`) per `demo/season-cadence`:
     - the ten steps, the content checks (shared with 1.3), the recordings copy, the `-late` snapshot name and the skipped recording;
     - `FROM=`, `ALLOW_LATE`, `FORCE`, the skip of undeployed steps, and the summary.

     Needs Phase 2 merged (and 2.4 for `PLAN=`; until then the refresh step uses Phase 2's plan). Integration (CLI) test file first: `tests/cli/test_demo_weekly_cli.py`, with fake targets and fake step runners; unit tests for the step planning and windows in `tests/unit/test_demo_weekly.py`.
   - [ ] 3.6 `make demo-abort` (`demo/abort.py`) and the app's refresh deadline (`APP_REFRESH_DEADLINE_S` in the refresh progress, and the timeline notice; X8-c):
     - the newest `RUNNING` `REFRESH_WEEK` or `SIMULATE` row, then `KILL STATEMENT … WITH MESSAGE 'demo-abort'`;
     - the wait of up to 15 s for a stale lock, the exit codes, and the printed `KILL SESSION` line;
     - the app's action closes its connection on the failed statement (assert it).

     Integration test first: `test_hung_refresh_aborted_before_switch` in `tests/it/test_demo_rehearsal_it.py`, using the app test stack, a mock delay on `GAMES`, and `APP_REFRESH_DEADLINE_S=5`. It adapts to the probe of 3.2. Needs 3.2, 3.3 and Phase 6's refresh action. [expert]

4. **App: scale dial and card (after Phase 6 merges)**
   - [ ] 4.1 Backend (`app/backend/ops/scale.py`, `routes/scale.py`, `sqlcatalog.py` entries, `operator.py` allowlist, `/api/config` `scale` and `refresh`, `security.py` cookie name, `settings.py` `APP_REFRESH`):
     - the background action on the existing action machinery, the run-id lookup, publication after the run, and lock refusals;
     - the size taken from `SIM_CONFIG`; no ledger lock;
     - `APP_REFRESH=off` refusals (403 `refresh_off`).

     Integration test file first: `tests/it/test_app_scale_it.py`. It covers plan, run and publish, size and lock, operator-only, the refresh off, the card side by side, the card offline, and two instances with their origins. `tests/unit/test_app_operator.py` is extended with the new templates. [expert]
   - [ ] 4.2 Frontend (`src/scale/ScaleCard.tsx`, `DialButton.tsx`, the inspector's "Scale" tab, the refresh button's off state, the timeline's deadline notice). Component tests first:
     - `ScaleCard.test.tsx`: two columns aligned by step, ratios, "measured … · imported …", "not measured yet";
     - `DialButton.test.tsx`: the confirmation text from the estimate or "about a minute", disabled reasons, progress.

     Contrast floors per Phase 6.
   - [ ] 4.3 (conditional) Phase 6's cut-line stretch items, if the orchestrator applied the cut, with the scenarios it moves into this plan.

5. **Preflight and the chat beat**
   - [ ] 5.1 `make demo-preflight` (`demo/preflight.py`):
     - every check of `demo/preflight` (`--now`, `--peer-prefix`, `--app-dist`, `--freeze`), with tiers and `SKIP`;
     - the requested slices from `EXPLAIN VIRTUAL` manifests (`vs/host/fetchlog.parse_manifest`), and the script check through 1.4;
     - the kit check's catalog hash and freeze pins; the `app` check; the live checks per deployment; the chat caps;
     - exit codes;
     - pure evaluators unit-tested (`tests/unit/test_demo_preflight_eval.py`: peer points, ledger room with headroom per deployment, staleness, the tier matrix).

     Integration test file first: `tests/it/test_demo_preflight_it.py`, with a fixture demo script `tests/it/fixtures/demo-script.md`, a fixture frontend build and a fixture freeze file.
   - [ ] 5.2 Chat beat on the kit (P8-a; optional group G, needs Phase 7 merged):
     - `make chat-bindings-check`, and the preflight's `chat` check;
     - the `demo` dataset schema in `questions.yaml`: the sim league's values for tests, the kit's in 11.5;
     - `demo/demo.yaml` validation of `chat_questions` against the must-pass flags of `questions.yaml`: a stage question that is not must-pass is refused, naming Phase 7's rule.

     Integration test file first: `tests/it/test_demo_chat_beat_it.py` (scenarios 1 and 2: chat stack on the test kit).
   - [ ] 5.3 (conditional: only once an Anthropic key has arrived, Q16) Transcript replay mode (P8-c, X8-d), an explicit build in Phase 7's code:
     - `make chat-transcript KIT= CHAT=` (`demo/transcript.py`): turn-log lines (`CHAT_LOG_PATH`) → per question the text, app state, ordered tool calls (name and validated input), app actions and final answer. It takes only `done`, non-stub turns, and is pinned to the kit's `kit.json` SHA-256 and point;
     - `TranscriptChatModel` in `app/backend/chat/` (selected by `CHAT_MODEL=transcript:<path>`). It implements the scripted-model interface and feeds the recorded tool calls as `tool_use` blocks, step by step in recorded order, then the recorded answer as the final text, so the real tool loop, disclosure and app actions run;
     - the turn log marks those turns `transcript` with zero usage;
     - the guard, at start and before each transcript turn: the point and the restored snapshot match, and every fetch of the newest `REFRESH_WEEK` run is `REPLAY` with a slice key and request time of the kit's refresh recordings. Otherwise the stub, with the reason in `/api/config` `chat`.

     Conformance: it passes Phase 7's stateful conformance rules like the scripted model does. Integration tests first: scenarios 3 and 4 of `tests/it/test_demo_chat_beat_it.py`, with fixture turn-log lines; unit tests `tests/unit/test_demo_transcript.py` (line selection, refusals, pinning, the guard over fetch rows). [expert]

6. **Benchmark harness**
   - [ ] 6.1 `bench/harness.py` + `make bench`:
     - the `sims`, `whatif` and `refresh` suites, with labels `BENCH <cell> #<n>`;
     - copy, then `DROP_SIM_RUN` unless `--keep`;
     - `analyses/perf.py` reused, the run-lock refusal, and the IT lock for local;
     - `make bench-report` reading both deployments' `CFB_BENCH` and their `LOAD_RUN`/`FETCH_LOG` for the restore and on-demand rows (`--peer-prefix` for tests).

     Integration test file first: the remaining scenarios of `tests/it/test_bench_it.py` (test matrix 4,000 sims at 500 and 1,000).

7. **Quality gate**
   - [ ] 7.1 `make lint` clean (ruff, format, mypy with `demo` and `bench`, eslint/tsc, `release_check --fast` with the freeze rule). `make coverage` meets the Requirements row. `make test`, `flock … make test-it` and `make app-test` are green, including every predecessor's tests. `make release-check` runs on this repository: fix every `FAIL` in the tree, and list history findings for U9.

8. **Documentation** (after 7.1; 8.1's draft may start earlier; re-read each file first; append to decision tables only)
   - [ ] 8.1 `docs/demo-script.md` (placeholders only, no team names or kit numbers in the committed text):
     - the outline of Key Interfaces with exact button names from the built app, and the two expected columns (replay, live);
     - `sql demo` / `sql show` blocks, with `tiers=`;
     - the pre-show checklist: the T-2 h `make app-build`, the terminal tab with its three lines, App B's `APP_REFRESH=off`, and the chat caps `CHAT_DAILY_MAX_USD=3.00` and `CHAT_SESSION_MAX_USD=2.00`;
     - beats 1–6 with timing and the scripted transitions between App A, App B, the terminal and Claude Desktop; the fallback ladder; the reset;
     - one section per tier (B, C, D) with its own script;
     - § Cadence: the one-command checklist `make weekly WEEK=n CONFIRM=yes`, the windows and the missed-Sunday rule;
     - § Kit recipe: capture → `make kit SOURCE=~/.cfb-demo/recordings/…` → rehearse → the bubble-candidates `sql demo` query → `make kit-previews` → record previews → kit → facts.

     It must pass the preflight's `script` check. The filled copy is `make kit-facts`'s output in the kit.
   - [ ] 8.2 `docs/user-guide.md`:
     - § "Demo: captures, kits, rehearsal, modes and abort", § "Scale dial and benchmark", § "Release check and freeze";
     - the MCP recipe, made concrete for Claude Desktop on Windows with WSL:
       - the entry `exasol-cfb`, with command `wsl.exe` and arguments `-e`, the absolute `uvx` path in WSL (`command -v uvx`) and `exasol-mcp-server@<pinned version>`;
       - `EXA_DSN=localhost:8563`, `EXA_USER=CFB_CHAT_WEB` and `EXA_PASSWORD`. A local-only, read-only user's password lives in the client config: rotate it after the demo with `make chat-user ROTATE=yes`;
       - `EXA_MCP_SETTINGS` with `enable_read_query` and `schemas.like_pattern` `CFB_API`, and `EXA_SSL_CERT_VALIDATION=false` on loopback only;
       - `WSLENV=EXA_DSN/u:EXA_USER/u:EXA_PASSWORD/u:EXA_MCP_SETTINGS/u:EXA_SSL_CERT_VALIDATION/u`;
       - the alternatives: Windows-native `uvx` with WSL localhost forwarding, or Claude Code in WSL (Q6, Q7);
       - "never run plain `exakit mcp-doctor` on this machine".
   - [ ] 8.3 Final `README.md` (numbers from `docs/benchmark.md`, links), `LICENSE` holder per U8; `make release-check` passes.
   - [ ] 8.4 `specs/architecture.md`:
     - a "Demo topology" section: the captures, two app instances, kits, per-kit replay paths, the stage mode and the override, the tiers, the freeze;
     - `CFB_BENCH` in the schema table, and the scale views in `CFB_API`'s reads;
     - the security model: the operator path stays loopback-only, no authentication, the release check;
     - the open question "AWS sizing" left with the owner.
   - [ ] 8.5 `docs/roadmap.md`:
     - decisions marked `Promotes to ADR: yes`, numbered after the last D-number present;
     - Phase 8 progress;
     - the demo-flow wording: beat 2's live fan-out on AWS; beat 3 `EXECUTE SCRIPT CFB_INFRA.REFRESH_WEEK(2026, N, 'regular')` (if Phase 2's docs task has not already changed it); the dial "laptop 100k vs AWS 1M (measured)"; beat 5's MCP coda; the tiers;
     - the open question "Which week N to record" resolved by the cadence and kit rules;
     - the API budget table: the cadence at 8 calls a week (refresh 4 + recording 4) and the kit recordings.
   - [ ] 8.6 `CLAUDE.md`, dense bullets, beyond 1.6's hard rules:
     - kits and recordings copies live in `~/.cfb-demo/` and are never committed;
     - the commands `make weekly`/`rehearse`/`demo-mode`/`demo-abort`/`demo-preflight`/`bench`/`bench-sync`/`release-check`;
     - replay is local only: never `demo-mode replay` on AWS; App B always runs with `APP_REFRESH=off`;
     - the cadence's calls, plan and runner;
     - `release_check --fast` in lint; `CFB_BENCH`.

     `specs/mission.md`:
     - § Commands (the new targets);
     - § Project Structure (`demo/`, `bench/`);
     - capability 11's wording;
     - the presenter persona's recipe (kit, preflight, tier, stage mode).

9. **Deployment (0 calls)**
   - [ ] 9.1 Release commit on both targets:
     - `make deploy` and `make deploy TARGET=aws` (every schema the merged phases provide, including `CFB_BENCH`), and `make grants` on both;
     - U3, done by the orchestrator: random passwords generated into `.env` (mode 0600, never printed), then `make app-user` and `make chat-user` on both targets. If Phase 7 has not merged (tiers B and C), the orchestrator creates `CFB_CHAT_WEB` with `CREATE SESSION` and the role `CFB_MCP` (Phase 5's `make grants`), using the statements `make chat-user` would run (Phase 7 decision log), so the MCP coda has its user;
     - `make bench-sync`, and `make demo-preflight TARGET=aws` (its AWS subset).

     Record the AWS DB version, and that nothing was stopped, started or resized. In demo week this task does not redeploy the real local namespace (the freeze).

10. **Captures (group A; calendar-bound; cannot be redone; ≤ 8 calls per Sunday under `add-cfb-demo-hardening`)**. Runner: the orchestrator from a local session on the laptop; the owner runs `make weekly WEEK=n CONFIRM=yes` if no session has run by Monday evening. Until 3.5 merges, each step is the matching Phase 2 or foundation command, and the recordings copy is `install -d -m 0700 ~/.cfb-demo/recordings/2026-wNN` plus `install -m 0600` of the files `make record` reports, with `sha256sum` into `SHA256SUMS`.
    - [ ] 10.1 **Sat Oct 31, the Q17 gate** (0 calls): Phase 2's task 9.5 restores an AWS snapshot exactly on the laptop. If it has not run, run it: `make snapshot TARGET=aws NAME=aws-q17-<date>`, then in a "local free" window `make restore TARGET=local NAME=… CONFIRM=yes`. If it fails:
      1. first, a 0-call fix of the cross-version difference in Phase 2's snapshot code (for example the DOUBLE text form of its [41]), retested by Nov 1;
      2. otherwise U2 is due Sun Nov 1, and the laptop takes over the snapshots:
         - the demo backfill on local (`make backfill PROFILE=demo TARGET=local PLAN=<a November ledger line the orchestrator adds>`, ≈ 217 calls, outside this plan's cap);
         - each Sunday `make refresh TARGET=local PLAN=add-cfb-demo-hardening` (4) and `make snapshot TARGET=local NAME=local-2026-wNN`, next to the AWS refresh for the dial. That makes 12 calls a week, ≤ 55 of 70 for a demo on Nov 16–20.

      Record the outcome in the decision log (Q17).
    - [ ] 10.2 **Sun Nov 1** (snapshot window until Tue Nov 3's first kickoff): first check the 2026 week dates in `CFB_MODEL.SEASON_WEEK` or `CFB_CORE.CALENDAR` (Q1).
      - Run cadence week 9: the quota, the AWS refresh (4), the snapshot `aws-2026-w09`, and the recording (4) only if an `aws-2026-w08` base exists. Then the local restore, the analyses and `make predict WEEK=9` before Tue Nov 3's first kickoff if Phase 5 is deployed, else skipped (the rehearsal kit's headline becomes a reconstruction).
      - Then `make demo-freeze FROM=2026-11-01 UNTIL=2026-11-21` on the commit deployed on AWS, committed. Tell the Phase 2–7 sessions that the frozen paths are closed.
      - `uv run python -m vs.host.ledger --summary`.
    - [ ] 10.3 **Sun Nov 8** (snapshot window until Tue Nov 10's first kickoff; recording window until week 11 completes): cadence week 10 (8): snapshot `aws-2026-w10`, the base of the stage kit; recordings copied to `~/.cfb-demo/recordings/2026-w10/` for `2026-w09-w10`. Then the console's 3 fan-out slices, once: `make record TARGET=aws PATTERNS=<fan-out file> CONFIRM=yes ARGS="--plan add-cfb-demo-hardening"`, copied to `~/.cfb-demo/recordings/console/`.
    - [ ] 10.4 **By Tue Nov 10, before week 11's first kickoff:** the week-11 predictions are frozen on local (`make predict SEASON=2026 WEEK=10` after the restore of `aws-2026-w10`; Phase 5). This makes the stage kit's scorecard headline a forecast. If Phase 5 is not deployed on local by then, the headline is the labeled reconstruction: the preflight's `headline` check warns, the script says so on stage, and the decision log records it.
    - [ ] 10.5 **Sun Nov 15** (snapshot window until Tue Nov 17; recording window until week 12 completes, Sat Nov 21): cadence week 11 (8). The recordings are copied to `~/.cfb-demo/recordings/2026-w11/` for the stage kit `2026-w10-w11`.
    - [ ] 10.6 (only if the demo moves after Nov 22) **Sun Nov 22:** cadence week 12 (8); `UNTIL` of the freeze moved with the demo date.

11. **Kits, go/no-go, rehearsals and the demo (group K; any day after their captures)**
    - [ ] 11.1 **Nov 8–10, kit `2026-w09-w10`** (needs 3.1, 3.3, 3.4):
      - `make kit NAME=2026-w09-w10 SEASON=2026 WEEK=9 SNAPSHOT=aws-2026-w09 SOURCE=~/.cfb-demo/recordings/2026-w10`, then `make rehearse` (time it: Q10) and the replayed refresh;
      - `make kit-previews`, record ≤ 2 previews, then `make kit … FORCE=yes`;
      - `make rehearse` and refresh twice, with `make kit-facts` after each: no difference;
      - **hard gate**, the offline drill: with Wi-Fi off, rehearse, refresh, every beat of the rehearsed tier in replay, and the chat stub. No dress rehearsal counts before it passes.
    - [ ] 11.2 **Mon Nov 9, go/no-go on the tier:** one line per tier, Full, B, C or D, from what is merged and green. Set `tier` in `demo/demo.yaml` and record the decision in the decision log. The tier may go up later only after that tier passes its preflight and a dress rehearsal by Nov 16.
    - [ ] 11.3 **Nov 9–13, dress rehearsal #1** on `2026-w09-w10` (≤ 8 calls; the ledger room rule first):
      - `make rehearse`, then the preflight `MODE=replay AWS=yes`;
      - beats 1–6 of the rehearsed tier, with App B's live fan-out under `APP_LEDGER_PLAN=add-cfb-demo-hardening`, the dial, and `make bench-sync` with the card;
      - with U2, the override drill on the laptop: a live refresh, then `make demo-abort` and the switch to replay once.

      The orchestrator drives the beats through the app's HTTP API and the CLI and times each one; U7's first video take is recorded here. Note every rough edge in the decision log. Without 4.1–4.2 the dial runs as `make simulate TARGET=aws SIMS=1000000` plus `make publish`, while App B follows the publication.
    - [ ] 11.4 Benchmark, then the report (needs 6.1):
      - `make bench TARGET=local` (local free window, IT lock) and `make bench TARGET=aws`;
      - `make bench-sync`, then `make bench-report`.

      Check Q9 and Q10, and log any miss with its step breakdown.
    - [ ] 11.5 **Nov 15–16, the stage kit `2026-w10-w11`** (needs 10.5, 5.1, 8.1's draft):
      - `make kit NAME=2026-w10-w11 SEASON=2026 WEEK=10 SNAPSHOT=aws-2026-w10 SOURCE=~/.cfb-demo/recordings/2026-w11`, then rehearse;
      - the bubble-candidates query (U6 approves), `make kit-previews`, record ≤ 2 previews, rebuild the kit;
      - fill `demo/demo.yaml` and the `demo` bindings;
      - `make kit-facts`: the facts and the filled script in the kit;
      - `make demo-preflight KIT=2026-w10-w11 MODE=replay AWS=yes`: exit 0;
      - **hard gate**, the offline drill on this kit.
    - [ ] 11.6 **Nov 15–16, dress rehearsal #2** (≤ 8 calls), with the chat:
      - **Without a key** (P8-c): beat 5 on the stub with its suggestions, and `make chat-rehearse ARGS="--dataset demo --only RQ01,RQ04,RQ10,RQ14,RQ15"` (stub) on the kit; in tier B, the coda alone.
      - **With a key** (U3, U1):
        - if Phase 7's 7.2–7.3 moved here, run them first, within Phase 7's $20;
        - re-qualify the must-pass set on the final demo bindings: `make chat-rehearse LIVE=yes MAX_USD=4 ARGS="--dataset demo --only RQ01,RQ04,RQ10,RQ14,RQ15 --repeat 2"`, with every question 3/3 (Phase 7 rule) (≤ $4);
        - run beat 5 live once in App A's panel on the kit (≤ $1);
        - only if 5.3 was built: `make chat-transcript KIT=2026-w10-w11 CHAT=<that chat id>`, and replay it with `CHAT_MODEL=transcript:…` offline (Wi-Fi off) to check that it passes;
        - write the derived numbers into `docs/chat-verification.md` (Phase 7's file).
      - The Claude Desktop coda (P8-b, U4): RQ10's text, with the tool call's SQL shown.
      - The chat on AWS in App B, once (P8-a; live only, ≤ $1).

      This plan's Anthropic spend stays ≤ $10 (U1), demo day included, on top of Phase 7's $20 if its 7.2–7.3 ran here.
    - [ ] 11.7 **Demo week (Nov 16–20):**
      - no `make deploy` on the real local namespace (the freeze);
      - the pre-show checklist of the script, with the chat caps in `.env`;
      - the demo itself under `app`; U10.
    - [ ] 11.8 Wrap-up:
      - the fallback video and screenshots (U7);
      - `make release-check`;
      - the ledger total (≤ 70) and the Anthropic spend (≤ $10);
      - `docs/demo-hardening-verification.md` with derived numbers only: timings per beat, preflight results, the probe results, the ledger and spend totals, the determinism diff, and the tier used.
    - [ ] 11.9 (only if the demo is after Nov 22) Kit `2026-w11-w12` as in 11.5, from 10.6's captures.

## Parallelization

| Parallel Group | Tasks |
|----------------|-------|
| Group A (captures; calendar; sequential) | 10.1 → 10.2 → 10.3 → 10.4 → 10.5 (→ 10.6) |
| Group B | 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 2.4, 3.5 |
| Group C | 2.1, 3.1, 3.2 |
| Group D | 2.2, 2.3, 3.3 |
| Group E | 3.4, 3.6, 4.1, 5.1, 6.1 |
| Group F | 4.2, 4.3 |
| Group G (optional; Phase 7) | 5.2, 5.3 |
| Group H | 7.1 |
| Group I | 8.1, 8.2, 8.3, 8.4, 8.5, 8.6 |
| Group J | 9.1 |
| Group K (calendar) | 11.1, 11.2, 11.3, 11.4, 11.5, 11.6, 11.7, 11.8 (11.9) |

Sequential dependencies:
- **Group A** runs on its calendar from Oct 31 and never waits for B–K. It needs only Phase 2 on AWS, the foundation's recorder and the ledger entry. It uses `make weekly` once 3.5 has merged.
- **B → C → D → E → F → H → I → J.** Group G is optional: it runs when Phase 7 has merged (and 5.3 only with a key), in parallel with H–K, and never blocks them. 8.1's draft and 9.1 may start before 7.1 when the cut line is applied.
- **Within B:** 1.1, 1.2, 1.4 and 1.5 need nothing and can start now. 1.6 is due Sat Oct 31 and comes before 1.1 (same file). 1.3, 2.4 and 3.5 need Phase 2 merged; 3.5 is due Sun Nov 8.
- **Within C–F:**
  - 2.1 needs Phases 2–4 merged; 2.2 needs 2.1 and Phase 5; 2.3 needs 2.1.
  - 3.1 needs 1.3 and the harness snapshot helpers; 3.2 needs Phase 2's DNS gate; 3.3 needs 3.1, 3.2 and Phases 3–5 (Phases 2–3 for tier D).
  - 3.4 needs 3.3; 3.6 needs 3.2, 3.3 and Phase 6's refresh action.
  - 4.1 needs 2.2 and Phase 6's action machinery; 4.2 needs 4.1's response models (it can start on fixtures).
  - 5.1 needs 1.4, 2.2 and 3.3; 6.1 needs 1.5, 2.1 and 2.3.
  - 5.2 and 5.3 need 3.3.
- **A task cut** by the cut line or by the tier decision (11.2) leaves the chain.
- **Calendar for K:**
  - 11.1 needs 10.3 and 3.1, 3.3, 3.4, by Nov 8–10. If they are late, the kit is built as soon as they merge: the recordings wait safely in `~/.cfb-demo/recordings/2026-w10/`.
  - 11.2 is on Mon Nov 9.
  - 11.3 needs 11.1, 11.2 and 5.1 (4.1–4.2 for the app dial, else the CLI dial).
  - 11.4 needs 6.1.
  - 11.5 needs 10.5, 5.1 and 8.1's draft; 11.6 needs 11.5 (5.2 for the chat; 5.3 for the transcript part).
  - 11.7 is the demo; 11.8 comes after it.

**Demo-minimum cut line** (if the calendar presses; the cut never touches security, spending, privacy or the captures):
- **Must by the demo:** 10.1–10.5; 1.3, 1.4, 1.6, 2.4, 3.1–3.5; 5.1 (at least the `db`, `kit`, `point`, `mode`, `recordings`, `week`, `publication`, `headline`, `script`, `connection`, `dns`, `ledger` and `aws` checks, and tiers); 8.1; 9.1; 11.1, 11.2, 11.3, 11.5, 11.6 (without its chat parts), 11.7.
- **Should:**
  - 2.1–2.3, and 4.1–4.2 (else two windows and the CLI dial);
  - 3.6 (needed only for the live-data override);
  - 6.1 + 11.4 (else the script quotes Phase 4's verification numbers);
  - 5.2 (else the coda alone, tier B);
  - 5.3, only with a key.
- **After the demo is fine:** 1.1, 1.2, 8.3 (Labs readiness is for the public release, not for the demo), 8.4–8.6, and 11.8's verification document.

## Dead Code Removal

| Type | Location | Reason |
|------|----------|--------|
| Docs | `README.md` "Status: planning" paragraph | Replaced by the external README (1.2, 8.3) |
| Docs | `docs/roadmap.md` open question "Which week N to record for replay mode, and when" | Resolved by the cadence and kit rules (8.5) |
| Field | Phase 6's session cookie name without the port | Replaced by `cfb_session_<port>` (4.1) |
| Config | `demo/freeze.yaml` | Removed by the owner after the demo; the `freeze` rule warns once `UNTIL` has passed |
| — | `spikes/` | Not removed by this plan: Phase 4 left removal to the owner after Phase 8 (U9); the release check reports it, and shipping it is the owner's call |

## Verification

### Scenario Coverage

| Scenario | Test Type | Test Location | Test Name |
|----------|-----------|---------------|-----------|
| season-cadence: A Sunday cadence captures the week in order at eight calls | Integration (CLI) | `tests/cli/test_demo_weekly_cli.py` | `test_weekly_captures_in_order_eight_calls` |
| season-cadence: A failed step stops the cadence and a resume does not pay twice | Integration (CLI) | `tests/cli/test_demo_weekly_cli.py` | `test_weekly_stops_and_resumes_without_paying_twice` |
| season-cadence: A cadence run after the next week began keeps the recording and gives up the kit base | Integration (CLI) | `tests/cli/test_demo_weekly_cli.py` | `test_weekly_late_run_keeps_recording_drops_base` |
| season-cadence: A recording that its kit would refuse is not paid for | Integration (CLI) | `tests/cli/test_demo_weekly_cli.py` | `test_weekly_skips_recording_a_kit_would_refuse` |
| season-cadence: The cadence refuses before spending when the quota or the ledger cannot cover it | Integration (CLI) | `tests/cli/test_demo_weekly_cli.py` | `test_weekly_refuses_without_quota_or_ledger_room` |
| rehearsal-kit: A kit packages the snapshot and the recordings of the next week's refresh | Integration | `tests/it/test_demo_kit_it.py` | `test_kit_packages_snapshot_and_recordings` |
| rehearsal-kit: A missing recording is refused with the command that records it | Integration | `tests/it/test_demo_kit_it.py` | `test_missing_recording_names_record_command` |
| rehearsal-kit: A recording of an unfinished replay week is refused | Integration | `tests/it/test_demo_kit_it.py` | `test_unfinished_replay_week_refused` |
| rehearsal-kit: A recording taken after the following week ended is refused | Integration | `tests/it/test_demo_kit_it.py` | `test_recording_past_following_week_refused` |
| rehearsal-kit: A snapshot that already holds results of the replay week is refused | Integration | `tests/it/test_demo_kit_it.py` | `test_snapshot_with_replay_week_results_refused` |
| rehearsal-kit: Kits stay private and are not overwritten by accident | Integration (CLI) | `tests/cli/test_demo_cli.py` | `test_kit_location_and_overwrite_rules` |
| rehearsal-kit: Building a kit spends no CFBD call | Integration | `tests/it/test_demo_kit_it.py` | `test_kit_build_spends_no_call` |
| rehearsal: Installing a kit resets the database to the Sunday after week N in replay mode | Integration | `tests/it/test_demo_rehearsal_it.py` | `test_rehearse_resets_to_base_week_in_replay` |
| rehearsal: The replayed refresh moves every page to week N+1 at zero calls | Integration | `tests/it/test_demo_rehearsal_it.py` | `test_replayed_refresh_moves_pages_zero_calls` |
| rehearsal: Two rehearsals of one kit produce the same pages | Integration | `tests/it/test_demo_rehearsal_it.py` | `test_two_rehearsals_same_pages` |
| rehearsal: A rehearsal runs without network or key | Integration | `tests/it/test_demo_rehearsal_it.py` | `test_rehearsal_without_network_or_key` |
| rehearsal: A failed live refresh falls back to replay without a restore | Integration | `tests/it/test_demo_rehearsal_it.py` | `test_failed_live_refresh_falls_back_without_restore` |
| rehearsal: A failure after a live table was committed is repaired by the replayed refresh | Integration | `tests/it/test_demo_rehearsal_it.py` | `test_mid_chain_failure_repaired_by_replay` |
| rehearsal: A hung live refresh is stopped before the mode switch | Integration | `tests/it/test_demo_rehearsal_it.py` | `test_hung_refresh_aborted_before_switch` |
| rehearsal: Replay mode is refused where no recordings can be read | Integration (CLI) | `tests/cli/test_demo_cli.py` | `test_replay_mode_refused_on_aws_and_without_kit` |
| rehearsal: Switching to live needs the key's connection and the DNS gate | Integration | `tests/it/test_demo_rehearsal_it.py` | `test_live_switch_needs_connection_and_dns_gate` |
| preflight: A ready kit passes every check | Integration | `tests/it/test_demo_preflight_it.py` | `test_ready_kit_passes` |
| preflight: A query without its recording fails before the demo does | Integration | `tests/it/test_demo_preflight_it.py` | `test_missing_recording_fails_recordings_check` |
| preflight: The preflight never spends a call and never writes | Integration | `tests/it/test_demo_preflight_it.py` | `test_preflight_never_spends_or_writes` |
| preflight: A broken statement in the demo script fails the script check | Integration | `tests/it/test_demo_preflight_it.py` | `test_broken_script_statement_fails` |
| preflight: A kit older than the newest completed week is flagged | Integration | `tests/it/test_demo_preflight_it.py` | `test_stale_kit_flagged` |
| preflight: Live mode checks the connection, the DNS gate, the ledger and the quota | Integration | `tests/it/test_demo_preflight_it.py` | `test_live_mode_checks` |
| preflight: The scorecard's honesty label is reported before the demo | Integration | `tests/it/test_demo_preflight_it.py` | `test_headline_kind_reported` |
| preflight: The AWS side of the scale dial and its live click are checked against the kit | Integration | `tests/it/test_demo_preflight_it.py` | `test_peer_check_and_live_click` |
| preflight: A tier skips the checks of the parts it does not use | Integration | `tests/it/test_demo_preflight_it.py` | `test_tier_skips_unused_checks` |
| preflight: A deployment changed since the kit's snapshot fails the kit check | Integration | `tests/it/test_demo_preflight_it.py` | `test_changed_deployment_fails_kit_check` |
| scale-dial: The dial plans its run at zero calls | Integration + component | `tests/it/test_app_scale_it.py`, `app/frontend/src/scale/DialButton.test.tsx` | `test_dial_plan_zero_calls`, `quotes the estimate or about a minute` |
| scale-dial: The dial runs and publishes a run at the current point | Integration | `tests/it/test_app_scale_it.py` | `test_dial_runs_and_publishes` |
| scale-dial: The dial takes its size from the configuration and its turn from the lock | Integration | `tests/it/test_app_scale_it.py` | `test_dial_size_from_config_and_lock` |
| scale-dial: The dial exists only where the operator path does | Integration | `tests/it/test_app_scale_it.py` | `test_dial_only_on_operator_path` |
| scale-dial: An instance with the refresh off cannot refresh by a click | Integration | `tests/it/test_app_scale_it.py` | `test_refresh_off_flag` |
| scale-dial: The scale card shows both deployments' newest runs side by side | Integration + component | `tests/it/test_app_scale_it.py`, `app/frontend/src/scale/ScaleCard.test.tsx` | `test_scale_card_side_by_side`, `renders both deployments aligned by step` |
| scale-dial: The card works when the other deployment is out of reach | Integration + component | `tests/it/test_app_scale_it.py`, `app/frontend/src/scale/ScaleCard.test.tsx` | `test_scale_card_offline_peer`, `shows measured time and not measured yet` |
| scale-dial: Run summaries are synced both ways, once, at zero calls | Integration | `tests/it/test_bench_it.py` | `test_bench_sync_runs_both_ways_once` |
| scale-dial: Two app instances on one laptop keep separate sessions | Integration | `tests/it/test_app_scale_it.py` | `test_two_instances_separate_sessions` |
| benchmark: The sims suite measures each cell three times and records the sizing | Integration | `tests/it/test_bench_it.py` | `test_sims_suite_three_samples_with_sizing` |
| benchmark: Benchmark runs are dropped after measuring and their numbers kept | Integration | `tests/it/test_bench_it.py` | `test_bench_runs_dropped_numbers_kept` |
| benchmark: The what-if suite records latency percentiles per condition count | Integration | `tests/it/test_bench_it.py` | `test_whatif_suite_percentiles` |
| benchmark: Refresh numbers are measured from the log, restore and on-demand numbers read at report time | Integration | `tests/it/test_bench_it.py` | `test_refresh_suite_and_report_read_logs` |
| benchmark: The report states every target as met or missed with derived numbers only | Unit | `tests/unit/test_bench_report.py` | `test_report_targets_met_or_missed_derived_only` |
| benchmark: The benchmark never spends a call and waits for no one | Integration | `tests/it/test_bench_it.py` | `test_bench_refuses_while_locked_and_spends_nothing` |
| chat-beat: Every bound name of the demo dataset resolves at the kit's point | Integration | `tests/it/test_demo_chat_beat_it.py` | `test_demo_bindings_resolve` |
| chat-beat: The stage questions pass in stub mode on the kit, offline | Integration | `tests/it/test_demo_chat_beat_it.py` | `test_stage_questions_stub_offline` |
| chat-beat: A recorded live conversation replays on its kit at no cost | Integration (5.3, conditional on a key) | `tests/it/test_demo_chat_beat_it.py` | `test_transcript_replays_on_kit` |
| chat-beat: A transcript is built only from finished live turns and replays only on its kit's replayed data | Integration (5.3, conditional on a key) | `tests/it/test_demo_chat_beat_it.py` | `test_transcript_build_and_binding_rules` |
| release-check: A secret in a tracked file fails the check without being printed | Integration (CLI) | `tests/cli/test_release_check_cli.py` | `test_tracked_secret_fails_masked` |
| release-check: A known secret anywhere in history fails the full check | Integration (CLI) | `tests/cli/test_release_check_cli.py` | `test_known_secret_in_history_fails` |
| release-check: Every home of a known secret is read, and a pasted node key is found | Integration (CLI) | `tests/cli/test_release_check_cli.py` | `test_every_secret_home_read_pasted_key_found` |
| release-check: The CFBD key is checked only when the owner types it | Integration (CLI) | `tests/cli/test_release_check_cli.py` | `test_cfbd_key_only_by_prompt` |
| release-check: Raw API data in the tree or in history fails the check | Integration (CLI) | `tests/cli/test_release_check_cli.py` | `test_raw_data_tree_and_history` |
| release-check: A missing license or credit fails the check | Integration (CLI) | `tests/cli/test_release_check_cli.py` | `test_license_and_credit` |
| release-check: Placeholders and documented test markers pass | Integration (CLI) | `tests/cli/test_release_check_cli.py` | `test_placeholders_pass` |
| release-check: Personal paths and e-mail addresses are warnings | Integration (CLI) | `tests/cli/test_release_check_cli.py` | `test_personal_paths_warn` |
| release-check: A change to a frozen path fails the check during the freeze | Integration (CLI) | `tests/cli/test_release_check_cli.py` | `test_freeze_rule_during_and_after` |
| release-check: The fast check runs with every lint and reads no secret | Integration (CLI) | `tests/cli/test_release_check_cli.py` | `test_fast_check_in_lint_reads_no_secret` |

Supporting tests (pure computation, no I/O; they back the integration tests, never replace them):
- `tests/unit/test_demo_kit.py`: names, locations, required keys, and the three week checks over synthetic bodies and CSV;
- `tests/unit/test_demo_weekly.py`: step planning, windows, resume points, the skip rules;
- `tests/unit/test_demo_config.py`;
- `tests/unit/test_demo_scriptcheck.py` (including `tiers=`);
- `tests/unit/test_demo_preflight_eval.py` (including the tier matrix and the per-deployment ledger room);
- `tests/unit/test_demo_transcript.py` (5.3, conditional);
- `tests/unit/test_release_rules.py` (including PEM normalization and the freeze comparison);
- `tests/unit/test_bench_matrix.py`;
- `tests/unit/test_app_operator.py` (extended);
- `app/frontend/src/scale/DialButton.test.tsx` (disabled reasons, progress);
- `tests/cli/test_demo_cli.py` (rehearse tier step selection, demo-abort exit codes with a fake connection);
- `tests/cli/test_loader_cli.py` (`--ledger-plan` for refresh and backfill).

### Manual Testing

All commands run from the repo root. Rows without a target run on the laptop (`TARGET=local`). Rows marked *(live, counted)* spend ledgered calls under `add-cfb-demo-hardening`.

| Feature | Command | Expected Output |
|---------|---------|-----------------|
| demo/season-cadence | `make weekly WEEK=10` (dry run); *(live, counted)* `make weekly WEEK=10 CONFIRM=yes`; `ls -la ~/.cfb-demo/recordings/2026-w10`; `sha256sum -c ~/.cfb-demo/recordings/2026-w10/SHA256SUMS` | The dry run lists the ten steps, 8 calls and the windows; the live run prints each step with seconds and a summary naming `aws-2026-w10` as the base of `2026-w10-w11` and the recordings as serving `2026-w09-w10`; 8 calls in the ledger; 0700/0600 modes; every checksum OK |
| demo/rehearsal-kit | `make kit NAME=2026-w09-w10 SEASON=2026 WEEK=9 SNAPSHOT=aws-2026-w09 SOURCE=$HOME/.cfb-demo/recordings/2026-w10`; `make kits`; `ls -la ~/.cfb-demo/kits/2026-w09-w10` | "week 10 complete: n/n FBS games", "week 11 not complete (k of m already played)", "snapshot at week 9: week 10 unplayed"; the kit listed with its recordings; 0700/0600 modes; 0 calls |
| demo/rehearsal | `make rehearse KIT=2026-w09-w10 CONFIRM=yes`; App A → Refresh week 10 (replay); `make rehearse` + refresh again and `make kit-facts KIT=2026-w09-w10` after each; then with Wi-Fi off the same again; then `make demo-mode MODE=replay TARGET=aws`; with U2: `make rehearse … MODE=live`, start a refresh, `make demo-mode MODE=replay KIT=2026-w09-w10` while it runs, `make demo-abort`, the switch again and Refresh | Report "current point 2026 week 9 · replay over …/kits/2026-w09-w10 · base publication …"; timeline all `DONE`, 0 calls, `REPLAY`; `kit-facts` "no difference"; offline identical; the AWS replay switch refused; the switch during the run refused naming the run; the abort ends in ≤ 15 s; the next refresh `DONE` in replay |
| demo/preflight | `make demo-preflight KIT=2026-w10-w11 MODE=replay AWS=yes`; then `TIER=C`; then delete one installed recording and run it again | All PASS (WARN allowed for headline, sizing, stale; SKIP for the laptop's live checks), exit 0; with `TIER=C` the chat and app checks SKIP; then `FAIL recordings` naming the slice and beat, exit 2 |
| demo/scale-dial | `make app-serve TARGET=aws APP_PORT=8001 APP_REFRESH=off`; App B → Refresh (disabled) → Scale dial → confirm; `make bench-sync`; App A inspector → Scale | The refresh button disabled with its reason; the confirmation quotes the estimate; a dial run of 1,000,000 seasons `DONE` in ≤ 60 s and published; App B's odds reload; App A's card shows laptop 100k and aws 1M side by side with sizing, seconds and "measured …" |
| demo/benchmark | `make bench TARGET=aws`; `flock <scratchpad>/cfb-it.lock make bench TARGET=local`; `make bench-report`; `git diff --stat docs/benchmark.md` | Medians of 3 per cell with sizing; no `BENCH` run left; `docs/benchmark.md` lists each target as met or missed, with the restore and on-demand rows from the logs |
| demo/chat-beat | `make chat-bindings-check KIT=2026-w10-w11`; `CHAT_MODEL=stub make app-serve` and click the suggestions RQ01, RQ04, RQ10, RQ15; Claude Desktop with `exasol-cfb` asking RQ10's text; (only with a key and 5.3) one live beat 5 in the panel, then `make chat-transcript KIT=2026-w10-w11 CHAT=<chat id>` and, with Wi-Fi off, `CHAT_MODEL=transcript:$HOME/.cfb-demo/kits/2026-w10-w11/chat-transcript.json make app-serve` | Every binding resolved with its id; stub answers with the kit's numbers and their SQL, RQ15 answered as read-only; Claude Desktop answers through `execute_exasol_query` on `CFB_API` as `CFB_CHAT_WEB`; transcript answers verbatim at $0 offline, turns marked `transcript` |
| demo/release-check | `make lint`; `make release-check`; `make release-check ARGS="--cfbd-key-prompt"` (owner, on a terminal) | Lint ends with "release check (fast): 0 FAIL" and the freeze rule passing; the full check lists 0 FAIL in the tree, history findings (if any) for U9, WARNs for personal paths and missing homes, "CFBD key: pattern-checked only" without the flag, and never prints a secret |

### Checklist

| Step | Command | Expected |
|------|---------|----------|
| Generate | `make generate && git diff --exit-code -- vs/catalog sql/core` | Exit 0 |
| Build | `uv run python -m vs.bundle && uv run python -m loader.bundle && uv run python -m engine.bundle && make app-build` | Exit 0 |
| Test | `make test` | 0 failures (unit + CLI + vitest) |
| Integration | `flock <scratchpad>/cfb-it.lock make test-it` | 0 failures; mock and replay only; 0 requests to `api.collegefootballdata.com`; 0 Anthropic requests |
| Lint | `make lint` | 0 ruff, mypy, eslint and tsc errors; release check (fast) 0 FAIL, the freeze rule included |
| Format | `uv run ruff format --check .` | No changes |
| Coverage | `make coverage` | ≥ 90 % for the modules in Requirements, and the predecessors' packages as before |
| Deploy | `make deploy && make deploy TARGET=aws` | Exit 0 both; `CFB_BENCH` and the scale views present; a second run drops nothing |
| Release | `make release-check` | 0 FAIL in the tree; history findings listed for the owner |
| Preflight | `make demo-preflight KIT=<demo kit> MODE=replay AWS=yes` | Exit 0 |
| Freeze | `uv run python scripts/release_check.py --fast` during the freeze | `freeze` passes: every frozen path has its pinned tree id |
| Ledger | `uv run python -m vs.host.ledger --summary` | `add-cfb-demo-hardening` ≤ 70; 2026-10 entries for it: 0 |
| No data in git | `git ls-files '*.parquet' '*.cfbd.json' '*.csv.gz' 'cache/*'` | Empty |
