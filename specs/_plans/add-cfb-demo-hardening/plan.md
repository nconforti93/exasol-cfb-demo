# Plan: add-cfb-demo-hardening

## Summary

Roadmap Phase 8 ("Demo hardening"): turn the platform of Phases 1–7 into a ~25-minute demo that runs the same way every time, live or offline, around mid-November 2026, and make the repository ready for an Exasol Labs release.

- **Rehearsal kits.** A kit packages a private snapshot of "Sunday after week N" with the recorded CFBD responses of week N+1's refresh, the console's fan-out and the head-to-head previews. `make rehearse KIT=…` resets the laptop to it in replay mode in one command. The replayed `REFRESH_WEEK(N+1)` moves every page at zero calls, with the same numbers every time, and without network.
- **Live first, replay as the fallback.** On stage the laptop refreshes live when the preflight says it can. If a live step fails, one command switches to replay and the refresh runs again, with no restore. Below that sit per-beat fallbacks and a recorded video.
- **Season cadence.** Every Sunday in November: a live `REFRESH_WEEK` on AWS (4 calls), an AWS snapshot, and on the laptop a restore, predictions frozen before kickoff and a base publication. This gives the kits their snapshots and keeps the scorecard headline honest.
- **Scale dial.** The laptop runs 100,000 seasons inside its refresh. A second app instance on the laptop, against the AWS database over loopback, runs and publishes 1,000,000 seasons on one click. A scale card shows both deployments' runs side by side, from synced run summaries in a new `CFB_BENCH` schema, so it still works offline.
- **Benchmark.** `make bench` measures sims/s, what-if p95, refresh steps, load and on-demand times on both targets, keeps the numbers after dropping its runs, and writes `docs/benchmark.md`.
- **Demo script and preflight.** `docs/demo-script.md` covers beats 1–6, with clicks, SQL, expected numbers from the kit, timing, a fallback per beat, and reset steps. `make demo-preflight` checks every beat's prerequisites at zero calls, including the script's own SQL.
- **Labs readiness.** README for an external audience, an MIT `LICENSE`, the CFBD credit, and `make release-check`, which scans the tree and the history for secrets and raw data (a fast subset runs in `make lint`). Publishing stays an owner action.

Everything is tested at zero CFBD and zero Anthropic calls on the integration-test namespace. The live work is calendar-driven in November, capped at 70 ledgered calls under the plan `add-cfb-demo-hardening`, with 0 calls in October.

## Design

### Context

The predecessor plans are binding (interview item 1). What each one gives this phase:

- **Foundation** (recorded: `specs/cfbd-vs/`, `specs/cfbd-fetch/`, `specs/decision-log.md`, `specs/_recorded/2026-10-22-add-cfbd-vs-foundation/`):
  - **Fetch modes.** `live` and `replay`, the replay layout `<REPLAY_PATH>/<segments>/<sha256(slice)[:24]>.cfbd.json` with `requested_at`, and a missing recording failing loudly. `REPLAY_PATH` is any UDF-visible BucketFS path. A mode switch takes effect on the next query (`cfbd-fetch/fetch-modes`).
  - **The recorder.** `make record PATTERNS=… CONFIRM=yes` with `--plan`, `--rerecord`, `--query` and `--publish-local`. Recordings made on `aws` stay in `cache/replay/`, because AWS BucketFS has no host directory.
  - **Platform facts.** The BucketFS host directory is mapped into UDFs, and files appear in UDFs 0.1–0.3 s after they are written (probe 1.4k). `make dns-check` is the live gate. `vs/host/target.py` pins TLS for AWS.
  - **AWS.** The `default` Personal instance runs Exasol 2026.1.1 on 3 nodes and holds `CFBD_API`. Deploy and run only: never stop, start or resize it. There is no BucketFS replay path on AWS (`ORCHESTRATOR-STATUS.md`).
- **Phase 2** (`specs/_plans/add-cfb-persistence-refresh/`, worktree `../exasol-cfb-demo-p2`; read with its `review.md` and decision log):
  - **Snapshots.** `make snapshot` / `make restore` as pyexasol gzip CSV with a fingerprinted, atomic restore at zero calls ([31], [41], [43]). The snapshot scope is `CFB_CORE` + `LOAD_LOG` + `FETCH_LOG`. The `RESTORE` run row names the snapshot in `RUN_ARGS`. Snapshots live in `~/.cfb-demo/snapshots/` and are refused inside the repo or OneDrive. Restoring is the rehearsal reset (roadmap Phase 2).
  - **Refresh.** `REFRESH_WEEK(season, week, season_type)` re-fetches the current season's non-final season slices (4 calls with the demo plan), commits each step's log row, and keeps a table's previous data when its fetch fails. It runs in replay at 0 calls.
  - **Ledger.** `vs/live/ledger.yaml` lists the plan caps (`add-cfb-analyses` 6, `add-cfb-app` 12, `app` 60) and the month caps (2026-10: 400, 2026-11: 1000). A plan that is not listed is refused. `make refresh` charges the plan `add-cfb-persistence-refresh` (hard-coded `PLAN_NAME` in `scripts/loader_cli.py`).
  - **Demo backfill on AWS.** 217 calls in group J, by 2026-10-31. Its backfill decision [29] leaves "recording refresh slices for offline rehearsals" to this phase.
- **Phase 3** (`add-cfb-analytical-model`): `CURRENT_AS_OF` is the newest *finished* week, derived from stored data, never from the clock; a partly played week does not move it (`model/as-of`). A restored "Sunday after week N" is therefore at week N on any later date.
- **Phase 4** (`add-cfb-score-engine`):
  - **The 1M scale dial.** Per-target defaults in `SIM_CONFIG`: local 100,000 at 5,000 per batch, AWS 1,000,000 at 10,000. The `SIMULATE(season, week, season_type, n_sims, sims_per_batch, run_seed, params_version, run_label)` signature. The default seed is season × 1,000 + week sequence, so reruns are deterministic.
  - **Layout.** C, or A if its probe 1.1c says so; this phase reads the run registry only, so the choice does not matter here.
  - **Budgets.** Local 100k ≤ 30 s, AWS 1M ≤ 60 s, both as the median of 3.
  - **Commands and registry.** `make sim`, `make simulate`, `make sim-drop`, `make sim-verify`. The registry fields include `SECONDS_TOTAL`, `SECONDS_ENGINE`, `SIMS_PER_SECOND`, `NODES`, `INSTANCES`, `DB_RAM_GIB` and `DB_VERSION`, and `SIM_RUN_STEP` commits a row per step. The benchmark harness is left to this phase.
- **Phase 5** (`add-cfb-analyses`; its Phase 8 hand-offs):
  - **The rehearsal recipe.** After restoring "Sunday after week N", run `make analyses`, then `make simulate` + `make publish` at week N if the laptop has no week-N publication (0 calls), so the arrows appear after the refresh.
  - **The scorecard headline.** It is a labeled reconstruction unless predictions were frozen live. `FREEZE_KIND` and `IS_HEADLINE`: first freeze wins, and `IN_ADVANCE` needs the freeze before the game's kickoff.
  - **Publications.** `make publish` publishes a manual run (the 1M dial). Predictions and publications are deployment history, not part of snapshots (decision [10]).
  - **Performance.** `analyses/perf.py` (`make analyses-perf`) is the what-if performance protocol.
- **Phase 6** (`add-cfb-app`; Phase 8 hand-offs and Q14):
  - **AWS.** "AWS" means the app on the laptop against the AWS database, on loopback (Q14, confirmed for this plan). The operator path exists only on loopback; authentication is Phase 8's decision.
  - **The dial.** The scale dial is one more operator allowlist entry.
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
  - **P8-b, the MCP moment.** 30 seconds in beat 5: "the same read-only role, through the Exasol MCP server, in Claude Desktop". It uses the user guide's client entry `exasol-cfb` (not the kit-managed `exasol`, which `mcp-doctor` restores) with the role `CFB_MCP`, on local only, because the server has no TLS fingerprint pinning.
  - **P8-c, the exit without a key.** If the key has not arrived when Phase 8 starts, Phase 7's tasks 7.2–7.3 move here. Beat 5 then runs on the stub model. Once the key exists, Phase 8 runs 7.2–7.3 and records a live transcript of beat 5 from the turn-log lines, with `docs/chat-verification.md`.
  - **The turn log** (`chat/turn-log`) records per turn the question, the app state, each tool call with its validated input and SQL, the app actions and the final answer. Its scripted-model seam (`chat/conversation`) can therefore replay a recorded transcript, but this plan must build that replay mode (task 5.3).
- **Owner answers** (`ORCHESTRATOR-STATUS.md`): the demo is around mid-November 2026. The rehearsal uses the latest completed week at the time of the run. November is authorized for 1000 calls, October for 400. The Anthropic key will come later.

**Calendar** (2026, from Phase 4's stored schedule; Army–Navy in week 15 on Saturday 12 December; Q1):

| Week | Saturday | Sunday after it |
|---|---|---|
| 9 | Oct 31 | Nov 1 |
| 10 | Nov 7 | Nov 8 |
| 11 | Nov 14 | Nov 15 |
| 12 | Nov 21 | Nov 22 |

A demo in the week of Nov 16 tells "Sunday after week 10 → refresh week 11" (kit `2026-w10-w11`). A demo in the week of Nov 9 uses `2026-w09-w10`, and one after Nov 22 uses `2026-w11-w12`. November weeks start with weekday (MACtion) games, so a "Sunday after week N" snapshot and the week-N+1 predictions are taken on Sunday or Monday.

**Forces**
- **Determinism vs. liveness.** The story's punchline is a live API call, but a 25-minute demo cannot depend on a conference network. The only offline source of week N+1's data is a recording, and a recording costs the same 4 calls as the refresh it replaces.
- **Time-bound data.** "Sunday after week N" exists for about two days a week. A snapshot taken after week N+1's games has nothing left to refresh, and a recording taken before its last game is incomplete. Neither can be fixed later without new calls.
- **Honest grades.** Predictions are frozen once, per deployment, and are not in snapshots. Only a laptop that froze week N+1's predictions before kickoff can show "the model went 41–9" as a forecast.
- **Two deployments, one stage.** The laptop holds the story, AWS holds the scale. The app's operator path is loopback-only by design, AWS has no replay, and the venue network may fail.
- **Quota.** October is tight: 400, with 21 spent and ≈ 221–300 planned by Phase 2. November has 1000, with ≤ 6 + 12 + 60 planned by Phases 5 and 6. Every call goes through the ledger, and this plan's cap must be proposed and listed.
- **A public repository.** CFBD terms forbid redistributing raw data. Secrets live in deployment files, `.env` and one database connection. The history of a local repository has never been audited.
- **Process.**
  - No database session, CFBD or Anthropic call during planning.
  - `docs/roadmap.md`, `specs/architecture.md` and `CLAUDE.md` change only through tasks.
  - Local integration tests run in "local free" windows under the IT lock.
  - Exasol claims are checked with the `exasol:exasol-database` and `exasol-mcp` skills and context7 (`/exasol/mcp-server`).

**Goals**
- Kits: build (zero calls, content-checked weeks, private), list, install in one command (`make rehearse`), and switch modes in one command (`make demo-mode`), with replay refused on AWS.
- A deterministic replay: two rehearsals of one kit give equal pages; a rehearsal needs no network or key; a failed live refresh recovers by mode switch without a restore.
- The Sunday cadence as one command (`make weekly`) that produces the snapshots, the predictions in advance and the base publication, and charges the plan `add-cfb-demo-hardening`.
- The scale dial in the app (operator allowlist entry, 0 calls), the scale card side by side (`CFB_API.SCALE_RUNS`, `SCALE_RUN_STEPS` over `CFB_SIM` and `CFB_BENCH`), `make bench-sync`, and separate sessions for two app instances.
- `make bench` with five suites and `make bench-report` → `docs/benchmark.md` with every mission and Phase 4 target marked met or missed.
- `make demo-preflight`: every beat checked at zero calls, including the script's `sql demo` blocks and the recordings every replayed query will request.
- Beat 5 on the kit, with Phase 7's must-pass questions on stage: bindings check, stub on the kit offline, and a recorded live transcript (from the turn log) that replays offline through a new `CHAT_MODEL=transcript:` mode.
- `docs/demo-script.md`: beats 1–6 with clicks, SQL, expected numbers per kit (`make kit-facts`), timing, fallbacks and reset; plus the cadence and kit-build recipe.
- Labs readiness: README, MIT `LICENSE`, CFBD credit, `make release-check` (tree + history + known secrets), with a fast subset in `make lint`.
- AWS: the release commit deployed with every schema, users, `CFB_BENCH`, the cadence, a preflight.
- Live budget: ≤ 70 ledgered calls for this plan, all in November, plus the demo itself under the app's own plan.

**Non-Goals**
- **Authentication, or a hosted app.** Both apps run on the presenter's laptop on loopback (decision [9]).
- **Changing the AWS instance.** No stop, start, resize or scale-out; the roadmap's "AWS sizing" question stays open for the owner.
- **Publishing.** Pushing to GitHub or exasol-labs, rewriting history, or deleting branches. The release check reports; the owner decides (decision [14]).
- **Recording in-game data**, or the backlog's game-day replay (D9).
- **Synthesizing recordings** from stored rows. Recordings come only from real requests.
- **Replay on AWS** (no BucketFS host directory), and moving kits between machines (a kit is for the laptop that built it; a new laptop builds its own from the snapshot and `cache/replay/`).
- **A dedicated database user for the external MCP client.** The chat user's role is reused (decision [16]).
- **New analyses or pages.** Phase 6's cut-line stretch items join only if the orchestrator applied that cut (Dependencies).
- **`PROMOTE()`, mirror migration, `season_week` slicing** (backlog).
- **A third-party license inventory of npm and Python dependencies.** It is an owner action before publishing.

### Decision

The rehearsal is **a private kit: a "Sunday after week N" snapshot plus recordings of the week-N+1 refresh, installed under its own `REPLAY_PATH`**. Building it spends nothing. Recording stays with `make record`. The kit checks the weeks from the data itself, not the clock.

The demo runs **live first on the laptop when the preflight passes, with replay as a one-command fallback** that needs no restore, because `REFRESH_WEEK` re-fetches the season's non-final slices idempotently.

The **scale dial** is a second app instance against AWS on loopback, with a dial action on the operator path. The side-by-side numbers come from run summaries synced into `CFB_BENCH` on both deployments.

The **benchmark** and the **preflight** read the existing registries and logs. **Labs readiness** is a stdlib scanner over the tree and the history.

#### Architecture

```
 SEASON CADENCE (every Sunday in November, `make weekly WEEK=n CONFIRM=yes`)
   AWS:   REFRESH_WEEK(2026, n) live (4 calls, plan add-cfb-demo-hardening) ─ steps MODEL · PREDICT · SIMULATE 1M · PUBLISH
          make snapshot TARGET=aws NAME=aws-2026-wNN  ───────────────────────────────────────────┐ (~/.cfb-demo/snapshots, 0 calls)
   local: make restore NAME=aws-2026-wNN → make analyses → make predict WEEK=n (freezes n+1 before kickoff)
          → simulate 100k + publish at n (base publication) → make bench-sync (both ways)
 KIT BUILD (on the Sunday after week n+1, for "Sunday after n → refresh n+1")
   make record TARGET=aws PATTERNS=<4 refresh slices of 2026> ARGS="--plan add-cfb-demo-hardening --rerecord"   (4 calls)
   [+ console fan-out, once: 3 calls] [+ previews of the demo pairs: ≤ 2 calls, after kit-pick]   → cache/replay/
   make kit NAME=2026-wNN-wMM SEASON=2026 WEEK=n SNAPSHOT=aws-2026-wNN                                      (0 calls)
     ├─ checks: every required slice recorded; week n+1 complete in the recorded /games body; snapshot holds week n done, n+1 unplayed
     └─ ~/.cfb-demo/kits/<kit>/{kit.json, replay/…}   (private, 0700/0600, never in the repo or OneDrive)
 REHEARSAL / DEMO (laptop)
   make rehearse KIT=<kit> CONFIRM=yes [MODE=live]                                                           (0 calls)
     1 check kit + snapshot pin  2 copy recordings → BucketFS …/bfsdefault/default/cfbd/kits/<kit>/
     3 ALTER VIRTUAL SCHEMA CFBD_VS SET FETCH_MODE='replay' REPLAY_PATH='/buckets/bfsdefault/default/cfbd/kits/<kit>'
     4 make restore (snapshot) 5 make analyses 6 base publication at n if missing 7 report 8 [MODE=live → demo-mode live]
   make demo-preflight KIT=<kit> MODE=live|replay AWS=yes                                                     (0 calls)
   App A  make app-serve                         TARGET=local :8000  ── refresh n+1 (live: 4 calls under `app`; replay: 0)
   App B  make app-serve TARGET=aws APP_PORT=8001                    ── scale dial: SIMULATE 1,000,000 + PUBLISH_RUN (0 calls)
   Claude Desktop ── exasol-cfb (Exasol MCP server, CFB_CHAT_WEB / role CFB_MCP) ──▶ local CFB_API     (beat 5 coda)
   fallback ladder: live ─(any live step fails)─▶ make demo-mode MODE=replay KIT=<kit> + refresh again ─▶ per-beat fallbacks ─▶ video
 SCALE CARD
   CFB_SIM.SIM_RUN / SIM_RUN_STEP (own runs) ∪ CFB_BENCH.BENCH_SIM_RUN / BENCH_SIM_STEP (other deployment, synced)
     ──▶ CFB_API.SCALE_RUNS / SCALE_RUN_STEPS ──▶ /api/scale ──▶ inspector "Scale" tab + odds run-banner line
 BENCHMARK   make bench TARGET=… → CFB_BENCH.BENCH_METRIC (+ BENCH_SIM_RUN, runs dropped) → make bench-sync → make bench-report → docs/benchmark.md
 LABS        make lint → release_check --fast (tree)      make release-check → tree + history + known secrets (never printed)
```

#### Patterns

| Pattern | Where | Why |
|---------|-------|-----|
| A kit is a manifest over existing artifacts | `demo/kit.py`, `~/.cfb-demo/kits/<kit>/kit.json` | Snapshot, recordings and replay are already built and tested. The kit only pins them by SHA-256 and checks that they tell one story. No new data format, no new call path |
| Content-checked weeks, never the clock | `demo/kit.py` week checks | "Sunday after week N" and "week N+1 complete" are facts about the data. Clock rules would fail the tests on later dates and accept a snapshot taken at the wrong moment |
| One `REPLAY_PATH` per kit | `ALTER VIRTUAL SCHEMA … SET REPLAY_PATH` | Recordings of one slice key overwrite each other in `cache/replay/`. A per-kit path freezes the version the story needs, and other kits stay usable |
| Replay first, live last, inside the reset | `make rehearse` order | From step 3 nothing can reach the network. Live is an explicit last step with its own checks |
| Fallback by mode switch, not by restore | `make demo-mode` + refresh again | A failed fetch keeps previous data, and the refresh re-fetches every non-final slice. Recovery takes seconds instead of a five-minute restore |
| The cadence makes honesty cheap | `make weekly` | Predictions frozen on Sunday, before kickoff, cost nothing. They turn the scorecard's reconstruction label into a real forecast record |
| Second instance, not a second target in one backend | App B on port 8001 | The operator path stays loopback-only with one target per process. No cross-target code, no authentication |
| Synced summaries for the comparison | `CFB_BENCH` + `make bench-sync` | The card needs the other deployment's numbers offline. Copying derived numbers is cheap and keeps the app reading one database |
| Measure, copy, drop | `bench/harness.py` | Benchmark runs would otherwise crowd the registry and the scale card. The numbers outlive the runs |
| Executable demo script | `sql demo` blocks + `[script]` check | The script cannot rot unnoticed: every rehearsal runs its SQL |
| Plan free, check everything | `make demo-preflight` | Every prerequisite is checkable at zero calls (`EXPLAIN VIRTUAL`, stored views, files), so the go/no-go is a command, not a feeling |
| Known secrets from their homes, held in memory | `scripts/release_check.py` | Pattern scans miss real passwords. Reading the deployment files and `.env` at run time finds the exact values without writing them anywhere. The CFBD key is never extracted (owner rule) |
| A fast subset in lint | `make lint` | New secrets and raw data are caught at the next lint, not at release time |

#### Key Interfaces

**Make targets** (new; every DB target runs `db-check` first and accepts `TARGET` unless noted)

| Target | Does | Calls |
|---|---|---|
| `make kit NAME= SEASON= WEEK= SNAPSHOT= [SOURCE=] [FORCE=yes]` | Builds a kit (`demo/rehearsal-kit`) | 0 |
| `make kits` | Lists kits | 0 |
| `make rehearse KIT= CONFIRM=yes [MODE=replay\|live]` | Resets local to the kit (`demo/rehearsal`); `TARGET=local` only | 0 |
| `make demo-mode MODE=live\|replay [KIT=]` | Switches `FETCH_MODE` / `REPLAY_PATH`; replay refused on `aws` | 0 |
| `make demo-preflight [KIT=] [MODE=] [AWS=yes]` | Readiness checks (`demo/preflight`) | 0 |
| `make kit-pick KIT=` | After a replayed refresh: candidate bubble teams (P(CFP) 25–75 % with a high-leverage game next week), head-to-head pairs from `demo/demo.yaml` with their next or latest meeting, and the preview game ids to record | 0 |
| `make kit-facts KIT=` | After a replayed refresh: the script's expected numbers into `~/.cfb-demo/kits/<kit>/facts.json` and `facts.md`; compares with the previous facts of the kit and lists every difference (determinism check) | 0 |
| `make weekly WEEK= [SEASON=2026] CONFIRM=yes [FROM=snapshot\|restore\|predict\|sync]` | The Sunday cadence (below) | 4 |
| `make chat-bindings-check [KIT=]` | `demo/chat-beat` | 0 |
| `make chat-transcript KIT= CHAT=<chat id>[,…]` | Builds a transcript from the turn-log lines of a live run (`demo/chat-beat`) | 0 |
| `make bench [SUITES=] [ARGS=]` | `demo/benchmark` | 0 |
| `make bench-sync` | Both ways between `local` and `aws` (`demo/scale-dial`) | 0 |
| `make bench-report` | Writes `docs/benchmark.md` | 0 |
| `make release-check` | `demo/release-check` (full); no `db-check` | 0 |
| `make refresh … PLAN=<ledger plan>` (changed) | The ledger plan to charge; default unchanged (`add-cfb-persistence-refresh`) | as before |

**`demo/demo.yaml`** (committed; names, ids and weeks only, no CFBD data; filled in task 10.5 from `make kit-pick`)

```yaml
version: 1
season: 2026
kits:                                   # per kit: choices that depend on the week
  2026-w10-w11:
    bubble_team: null                   # school; the odds/what-if/rooting focus of beats 1 and 4
    whatif_game_id: null                # the bubble team's game in week N+2 (beat 4 toggle; chat RQ01 binding)
    pairs: [[null, null], [null, null]] # head-to-head pairs; the first on stage, the second in reserve
    preview_game_ids: []                # the games the server chooses for the pairs at the replay week (from kit-pick)
    allow_incomplete: []                # postponed or cancelled week-N+1 games, if any
console: [fan-out, contradiction, stored]
chat_questions: [RQ01, RQ04, RQ10, RQ15]   # Phase 7's must-pass set (RQ14 is RQ15's alternative); nothing else on stage
chat_reserve: [RQ05]                        # on stage only after joining the must-pass list and passing 3/3 live
mcp_question: RQ10                          # asked again in Claude Desktop: same question, same role, another client
rivalries: [[Michigan, Ohio State], [Alabama, Auburn], [Texas, Oklahoma], [Army, Navy]]   # kit-pick candidates
```

**Rehearsal** (`demo/install.py`, `demo/mode.py`; order and rules in `demo/rehearsal`). The mode switch, checked against the grammar (`alter_virtual_schema_statement_1/2`: `ALTER VIRTUAL SCHEMA schema SET (property = string)+`):

```sql
ALTER VIRTUAL SCHEMA "CFBD_VS" SET FETCH_MODE = 'replay' REPLAY_PATH = '/buckets/bfsdefault/default/cfbd/kits/2026-w10-w11';
ALTER VIRTUAL SCHEMA "CFBD_VS" SET FETCH_MODE = 'live' REPLAY_PATH = '/buckets/bfsdefault/default/cfbd/replay';
SELECT CONNECTION_NAME FROM EXA_DBA_CONNECTIONS WHERE CONNECTION_NAME = :name;    -- name only; the key is never read
```

The BucketFS host directory of the kit is `~/.exasol/personal/deployments/local/local/runtime/exa/bucketfs/bfsdefault/default/cfbd/kits/<kit>/` (probe 1.4k). The kit's copy is the source of truth; `make rehearse` rewrites the BucketFS copy from it every time.

**Weekly cadence** (`scripts/demo.py weekly`; prints each step with seconds; stops at the first failure; `FROM=` resumes without repeating the paid step)

| Step | Command it runs | Calls |
|---|---|---|
| refresh | `make refresh TARGET=aws SEASON=2026 WEEK=n PLAN=add-cfb-demo-hardening CONFIRM=yes` (estimate, quota and ledger checks first) | 4 |
| snapshot | `make snapshot TARGET=aws NAME=aws-2026-w<NN>` | 0 |
| restore | `make restore TARGET=local NAME=aws-2026-w<NN> CONFIRM=yes`, then `make analyses` | 0 |
| predict | `make predict SEASON=2026 WEEK=n` on local (first freeze of week n+1, before its kickoff) | 0 |
| publish | `make simulate SEASON=2026 WEEK=n` + `make publish RUN=<id>` on local, unless a week-n publication exists | 0 |
| sync | `make bench-sync` | 0 |

It refuses to run the `predict` step after the first kickoff of week n+1 unless `ALLOW_LATE=yes`, because those predictions would be reconstructions.

**`CFB_BENCH`** (`sql/bench/*.sql`, rendered with `{BENCH_SCHEMA}`, deployed by `bench/deploy.py` through `vs/host/sqlobjects.py`; data tables `CREATE TABLE IF NOT EXISTS`, never replaced; names checked against `vs/gen/exasol_reserved_keywords.txt`, which reserves `VALUE`, `SOURCE` and `LOCAL`)

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

**Serving views** (`sql/api/700_scale_runs.sql`, `710_scale_run_steps.sql`; deployed with `CFB_API` by `make analyses`; grain-first comments on every column; Phase 5's per-file reference allowlist gains `{BENCH_SCHEMA}.*`, `{SIM_SCHEMA}.SIM_RUN`, `{SIM_SCHEMA}.SIM_RUN_STEP`)
- `CFB_API.SCALE_RUNS` — one row per simulation run shown by the scale card. Rows are this deployment's runs of origin `REFRESH`, or `MANUAL` labeled `SCALE DIAL`, of any status, plus the other deployment's imported `DEMO` runs. Columns: `DEPLOYMENT_LABEL`, `IS_OWN`, `RUN_KEY` (label, run id and start), `SOURCE_RUN_ID`, `RUN_STATUS`, `RUN_ORIGIN`, `RUN_LABEL`, `AS_OF_SEASON`, `AS_OF_WEEK_SEQ`, `N_SIMS`, `SIMS_PER_BATCH`, `NODES`, `INSTANCES`, `DB_RAM_GIB`, `DB_VERSION`, `SECONDS_TOTAL`, `SECONDS_ENGINE`, `SIMS_PER_SECOND`, `STARTED_AT`, `ENDED_AT`, `IMPORTED_AT`, `IS_NEWEST_DONE` (the newest `DONE` run per deployment).
- `CFB_API.SCALE_RUN_STEPS` — one row per step of a run in `SCALE_RUNS`. Columns: `RUN_KEY`, `STEP_NO`, `STEP_NAME`, `STEP_STATUS`, `SECONDS`, `ROWS_WRITTEN`, `STARTED_AT`, `ENDED_AT`.
- Ratios (sims ×, seconds ×) are computed in the backend from two rows. Any division in the views uses `NULLIFZERO(divisor)` (grammar `nullifzero`): a guarded division can be evaluated eagerly on 2026.1.x.

**App additions** (Phase 6 code; `app/backend/ops/scale.py`, `routes/scale.py`, `sqlcatalog.py`, `security.py`; frontend `src/scale/`)

| Item | Detail |
|---|---|
| `GET /api/scale` (read) | `SCALE_RUNS` rows with `IS_NEWEST_DONE` per deployment, and their `SCALE_RUN_STEPS`; the running dial run when one exists |
| `POST /api/scale/plan` (operator) | target, point, configured sims and batch, estimate, lock state, `can_run`, reasons; 0 calls, no lock |
| `POST /api/scale/run` (operator) | `{season, week, season_type, n_sims}` → re-plan, background `SIMULATE` + `PUBLISH_RUN`, 202 `{action_id}` |
| Operator allowlist | `SELECT … FROM CFB_SIM.SIM_CONFIG`; the `LOAD_RUN` running check; `EXECUTE SCRIPT "CFB_INFRA"."SIMULATE"(<int>, <int>, 'regular'\|'postseason', <int>, <int>, NULL, NULL, 'SCALE DIAL')`; `EXECUTE SCRIPT "CFB_INFRA"."PUBLISH_RUN"(<int>)`; the run-id lookup `SELECT "RUN_ID" FROM CFB_SIM.SIM_RUN WHERE "SESSION_ID" = <int> AND "RUN_LABEL" = 'SCALE DIAL' ORDER BY "RUN_ID" DESC LIMIT 1` |
| `GET /api/config` | `scale: {enabled, reason, target_label, n_sims, sims_per_batch}` |
| Session cookie | name `cfb_session_<APP_PORT>` |
| Errors | 409 `refresh_running` / `refresh_locked` (holder named), 400 `scale_size` (size not the configured one), 403 (no operator path) |
| Frontend | `ScaleCard` (two columns, step bars aligned by name, ratios, "measured … · imported …", "not measured yet"), `DialButton` with confirmation ("Run 1,000,000 seasons on aws · 0 CFBD calls · ≈ 45 s"), dial progress from `SCALE_RUN_STEPS` (1 s poll while running), inspector tab "Scale", one line under the odds run banner |

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

**Demo script outline** (`docs/demo-script.md`; the doc adds exact button names, screenshots of nothing, and the kit's numbers from `make kit-facts`; placeholders `{N}`, `{N1}` = N+1, `{N2}` = N+2, `{BUBBLE}`, `{PAIR}`, `{F.*}` = kit facts)

| Beat (min) | Screen and clicks | SQL / statement shown | Expected (from kit facts) | Fallback |
|---|---|---|---|---|
| 0. Pre-show (T-1 day … T-5 min) | T-1 day: `make weekly` done, kit built, `make bench-sync`; T-2 h: restart local Exasol for full sizing (`exasol stop -d local`, `exasol start -d local`), `make rehearse KIT=… CONFIRM=yes`, `make demo-preflight KIT=… MODE=live AWS=yes`; T-30 min: go/no-go for live (all live checks PASS), else `MODE=replay`; start App A (:8000), App B (:8001), Claude Desktop; browser tabs: App A odds, App A inspector, App B odds | — | preflight exit 0 | any FAIL → its fix line; no live go → replay |
| 1. Hook (2) | App A, Odds at "2026 · week {N}". Focus `{BUBBLE}`: P(CFP), seed distribution, arrow since week {N}-1. Footer: `local · live` (or `replay`) and the CFBD credit | — | `{F.bubble_p_cfp_N}`, top 3 `{F.top3_N}` | app does not load → restart `make app-serve` (5 s); DB down → video chapter 1 |
| 2. The API is a schema (5) | App A inspector → console. (a) Narrate the generated `CREATE VIRTUAL SCHEMA` (93 endpoints → 137 tables, no mapping code). (b) Run `fan-out`: plan = 3 slices shown as HTTP requests, confirm cost (live 3 calls / replay 0), waterfall in parallel. (c) Run `contradiction`: 0 slices, 0 calls. (d) Run `stored`: 157 season slices, stored-only, 0 calls | `sql show` of the `CREATE VIRTUAL SCHEMA` text; the three console statements (`sql demo` blocks, as in the console catalog) | fan-out ≈ `{F.fanout_ms}` per slice; 157 rows for `stored` | live fan-out slow or failing → `make demo-mode MODE=replay KIT=…` and run it again (replay, same look), or skip to `stored` |
| 3. One call refreshes everything (5) | App A header → Refresh. Plan: "2026 week {N1} · 4 calls (live) · quota … · the point will move to week {N1}" → Confirm. Timeline (1 s): CHECK, FETCH ×4, MODEL, PREDICT, SIMULATE (100,000 seasons), PUBLISH; inspector shows the 4 requests arriving. Then App B (AWS, already at week {N1}): Scale dial → "Run 1,000,000 seasons on aws" → confirm; dial steps; scale card: laptop 100k vs aws 1M | `sql show`: `EXECUTE SCRIPT CFB_INFRA.REFRESH_WEEK(2026, {N1}, 'regular')`; `SIMULATE(2026, {N1}, 'regular', 1000000, 10000, NULL, NULL, 'SCALE DIAL')` | refresh total `{F.refresh_s}` s (< 120), SIMULATE `{F.sim_s_local}` s on `{F.instances_local}` cores; AWS 1M `{F.sim_s_aws}` s on 3 × 4 | live refresh fails → `make demo-mode MODE=replay KIT=…`, Refresh again (no restore); AWS unreachable → App A's scale card shows the last measured AWS run ("measured Sunday") |
| 4. Pages move (8) | Odds: arrows since week {N}, `{BUBBLE}` moved by Δ pp (1.5). What-if: toggle `{BUBBLE}` wins game `{F.whatif_game}` → conditional odds + effective sample; add a second toggle; rooting guide for `{BUBBLE}` (2.5). Predictions: the scorecard headline with its label and the line beside it; this week's predictions (1.5). Upsets: upset of the week with "T-n since 1978" (1). Head-to-head `{PAIR}`: series, meetings, next meeting with prediction and stakes; click "Fetch live preview" (live 1 call / replay 0) (1.5) | none typed; the inspector shows each page's SQL on request | `{F.bubble_p_cfp_N1}` (Δ `{F.bubble_delta}`), what-if `{F.whatif_p}` on `{F.whatif_sample}` seasons, headline `{F.headline}` (`{F.headline_kind}`), upset `{F.upset}` rank `{F.upset_rank}`, series `{F.series}` | what-if slow → narrate the unconditional guide; preview fails → replay or skip; headline reconstructed → say so (the label is on screen) |
| 5. AI (4) | App A chat (suggestions shown): RQ01 (sets the toggle; the odds page follows; Undo chip), RQ04 (the swing since week {N}), RQ10 (a season record outside the pages); open "SQL" under one answer; aside (20 s): RQ15 ("delete last week's predictions") → the chat says it is read-only. Coda (30 s, P8-b): Claude Desktop, server `exasol-cfb`, ask RQ10's text again; show the tool call's SQL over `CFB_API` with the same read-only role `CFB_MCP` | the disclosed statements | the kit's stub or transcript answers (`{F.chat_*}`) | no key or API error → `CHAT_MODEL=transcript:` if the kit has one, else stub with suggestions; no network → skip the coda and say it; laptop chat broken → App B's chat (live, same questions) or skip |
| 6. Close (1) | Back to App A odds. One platform: the API is a schema, Python in the database at any scale, AI over MCP on a read-only role. Repo link (once published), CFBD credit | — | — | — |
| Reset (≤ 6 min) | `make rehearse KIT=… CONFIRM=yes [MODE=live]`, `make demo-preflight …`, restart App A; AWS needs nothing (the dial's publication stays; the next cadence supersedes it) | — | preflight exit 0 | restore fails → it changed nothing; rerun, then the video |

**Fallback ladder:** (1) live → replay by `make demo-mode` + refresh again; (2) the per-beat fallbacks above; (3) the recorded video of a full rehearsal with a chapter per beat (owner action U7). The video and screenshots stay private (`~/.cfb-demo/kits/<kit>/video/`).

**Live budget and calendar** (plan `add-cfb-demo-hardening`; every call through the ledger; 0 in October)

| When (2026) | Step | Calls |
|---|---|---|
| Sun Nov 1 | `make weekly WEEK=9` (until task 2.4 lands, `make refresh` charges `add-cfb-persistence-refresh`, which has 79 calls of room after its group J) | 4 |
| Sun Nov 8 | `make weekly WEEK=10`; record kit `2026-w09-w10`: 4 refresh slices + the console's 3 fan-out slices (once) | 11 |
| Nov 8–9 | `make kit-pick` → record ≤ 2 previews; build, rehearse, `kit-facts` twice, offline drill | ≤ 2 |
| Nov 9–13 | Live dress rehearsal #1 on the laptop (`APP_LEDGER_PLAN=add-cfb-demo-hardening`: refresh 4, fan-out 3, preview 1), AWS dial (0); benchmark on both targets (0) | ≤ 8 |
| Sun Nov 15 | `make weekly WEEK=11`; record kit `2026-w10-w11`: 4 refresh slices + ≤ 2 previews | ≤ 10 |
| Nov 15–16 | Build, rehearse, facts, demo-script numbers, preflight; live dress rehearsal #2 | ≤ 8 |
| **Planned (demo in the week of Nov 16)** | | **≤ 43** |
| Sun Nov 22 (only if the demo is later) | `make weekly WEEK=12` + kit `2026-w11-w12` | ≤ 10 |
| **Cap** (`vs/live/ledger.yaml` `add-cfb-demo-hardening: 70`; retry headroom of `MAX_ATTEMPTS − 1` per request) | | **70** |
| Demo day | The live demo itself, under the app's plan `app` (cap 60): ≤ 8 + retries | (`app`) |

November with every plan at its cap: Phase 2 ≤ 300 (all months), Phase 5 ≤ 6, Phase 6 ≤ 12 + `app` 60, this plan ≤ 70, so ≤ 448 of the 1000 authorized.

**Code layout**

```
demo/__init__.py
demo/demo.yaml          demo choices (committed)
demo/config.py          demo.yaml schema and loader (pure)
demo/kit.py             names, locations, manifest, required slice keys, week checks over bodies and snapshot files (pure + files)
demo/install.py         make rehearse (BucketFS copy, mode, restore, analyses, base publication, report)
demo/mode.py            make demo-mode (ALTER VIRTUAL SCHEMA, refusals)
demo/preflight.py       checks (DB reads) + pure evaluators
demo/scriptcheck.py     `sql demo` / `sql show` block parser and placeholder filling (pure)
demo/picks.py, demo/facts.py, demo/transcript.py, demo/weekly.py
bench/__init__.py, bench/matrix.yaml, bench/deploy.py, bench/harness.py, bench/sync.py, bench/report.py, bench/report_template.md
sql/bench/*.sql, sql/api/700_scale_runs.sql, sql/api/710_scale_run_steps.sql
scripts/demo.py, scripts/bench.py, scripts/release_check.py
app/backend/ops/scale.py, app/backend/routes/scale.py; app/frontend/src/scale/{ScaleCard,DialButton}.tsx
app/backend/chat/transcript_model.py   CHAT_MODEL=transcript:<path> on Phase 7's scripted-model seam (task 5.3)
docs/demo-script.md, docs/benchmark.md, README.md, LICENSE
```

**Exasol and MCP claims, checked**

| Claim | Evidence | Result |
|---|---|---|
| `ALTER VIRTUAL SCHEMA s SET p = 'v' q = 'w'` | `exasol-grammar.md` `alter_virtual_schema_statement_1/2` (`"SET" ( property "=" string )+`) | Confirmed; takes effect on the next query (`cfbd-fetch/fetch-modes`) |
| `EXPLAIN VIRTUAL <subquery>` costs 0 calls and lists the planned slices | grammar `explain_virtual_statement`; foundation manifest (`cfbd-fetch/fetch-log`) | Confirmed |
| `EXECUTE SCRIPT s(args)`; `WITH OUTPUT` discards the table | grammar `execute_script_statement(_2)`; Phase 2 | Confirmed; never `WITH OUTPUT` |
| `NULLIFZERO(number)` | `exasol-grammar-functions.md` `nullifzero` | Confirmed; used for every division (2026.1 eager-division regression, owner's investigation) |
| `CREATE TABLE IF NOT EXISTS`, `PRIMARY KEY` constraint, `MERGE` | grammar `create_table_statement_1`; Phase 2 probe 1.1k | Confirmed |
| `EXA_DBA_CONNECTIONS` lists connection names (DBA) | `exasol-sql.md` system tables | Confirmed; only `CONNECTION_NAME` is selected |
| Reserved words | `vs/gen/exasol_reserved_keywords.txt` (`VALUE`, `SOURCE`, `LOCAL` reserved; `SECONDS`, `SUITE`, `CELL`, `SAMPLES` not) | Avoided |
| BucketFS host directory ↔ `/buckets/bfsdefault/default/…`, new files visible in 0.1–0.3 s | foundation probe 1.4k | Confirmed for files in an existing bucket; `make rehearse` proves it per kit with one replay read of the smallest recording after switching to replay (0 calls; `EXPLAIN` alone reads no recording) (Q18) |
| Exasol Personal sizes cores and DB RAM at each start | `CLAUDE.md` (D19) | The preflight's `sizing` check; restart before the demo |
| Exasol MCP server: `EXA_DSN`, `EXA_USER`, `EXA_PASSWORD`, `EXA_MCP_SETTINGS` (`enable_read_query`, `schemas.like_pattern`), `EXA_SSL_CERT_VALIDATION` | context7 `/exasol/mcp-server` (server and DB connection setup) | Confirmed; stdio via `uvx exasol-mcp-server@<pinned version>` |
| The starter kit's `mcp-doctor` restores its managed `exasol` entry; the MCP tool's `SELECT` gate is a keyword check, the role is the boundary | `exasol-mcp` skill | Entry named `exasol-cfb`; the role `CFB_MCP` is the boundary (Q7) |

### Consequences

| Decision | Alternatives Considered | Rationale |
|----------|------------------------|-----------|
| Kit = snapshot after week N + recordings of the week-N+1 refresh, private, per-kit `REPLAY_PATH` ([1]) | Recordings + the shared default replay path; a snapshot after N+1 with no refresh; recordings synthesized from stored rows | The only deterministic offline source of week N+1; per-kit paths keep versions apart; synthesized bodies would be fabricated recordings |
| Weeks checked from content, not the clock ([2]) | Snapshot creation time vs kickoff times | Clock rules break tests on later dates and accept a snapshot taken after weekday games |
| Live first when the preflight passes; replay by mode switch, no restore ([4]) | Replay only; live only | The mission promises a live refresh and an offline fallback; `REFRESH_WEEK`'s idempotent re-fetch makes recovery a 1-second switch |
| Sunday cadence on AWS + laptop predictions in advance ([3]) | Refresh only when building kits; predictions frozen at rehearsal time | Honest headline at 0 extra calls; snapshots for every week; AWS current for the dial |
| Second app instance against AWS on loopback; no authentication ([7], [9]) | One backend with two targets; a hosted app with login | No cross-target code; the operator path stays loopback-only (Phase 6 [3]); Q14 confirmed |
| `CFB_BENCH` + `make bench-sync` for side-by-side numbers ([8]) | Live read of the peer; two browser windows only; a JSON file the backend reads | Offline-proof; the app keeps reading one database through `CFB_API` |
| Bench measures, copies and drops ([10]) | Keep bench runs; a separate bench schema for runs | Registry and card stay clean; numbers survive |
| Executable demo script checked by preflight ([11]) | Prose script | The script's SQL cannot drift from the schema |
| Release check: stdlib scanner, tree + history, known secrets in memory, fast subset in lint ([12]) | gitleaks as the gate; `git grep` rules only | No new tool to install; finds real values; gitleaks optional |
| Publishing and history rewrite stay with the owner ([14]) | Rewrite automatically | Git discipline and the owner's guardrails |
| Ledger plan `add-cfb-demo-hardening` 70, 0 in October; the demo under `app` ([17]) | Charge everything to `app`; no cadence | Each plan's spend auditable; October left to Phase 2 |
| Chat beat: Phase 7's must-pass questions on stage (RQ01, RQ04, RQ10, RQ15 aside); stub fallback; a transcript replay mode built here on the scripted seam ([15]) | Other stage questions (each would add 3 live runs); stub only; a static transcript page | No extra live qualification; real Claude answers offline at zero cost on a deterministic kit |
| MCP coda with Claude Desktop, entry `exasol-cfb`, user `CFB_CHAT_WEB`, local only ([16]) | A dedicated user; AWS | Fewer objects; no TLS pinning in the server |

## Features

| Feature | Status | Spec |
|---------|--------|------|
| demo/rehearsal-kit | NEW | `demo/rehearsal-kit/spec.md` |
| demo/rehearsal | NEW | `demo/rehearsal/spec.md` |
| demo/preflight | NEW | `demo/preflight/spec.md` |
| demo/scale-dial | NEW | `demo/scale-dial/spec.md` |
| demo/benchmark | NEW | `demo/benchmark/spec.md` |
| demo/chat-beat | NEW | `demo/chat-beat/spec.md` |
| demo/release-check | NEW | `demo/release-check/spec.md` |

The domain is `demo/`. None of the specs this plan builds on is recorded except the foundation's (`cfbd-vs/`, `cfbd-fetch/`), which this plan does not change. Recorder notes for the predecessors, when they are recorded:
- **`app/refresh`, `app/access`** (Phase 6): the operator allowlist gains the scale dial (`demo/scale-dial`). The session cookie name carries the port.
- **`api/serving-contract`** (Phase 5): `SCALE_RUNS` and `SCALE_RUN_STEPS` join `CFB_API` ("new views may be added").
- **`loader/refresh-week`** (Phase 2): `make refresh` takes `PLAN=`.
- **`chat/rehearsal`, `chat/conversation`, `chat/turn-log`** (Phase 7): the `demo` bindings are filled; the stage set equals the must-pass set, so the list does not grow. `CHAT_MODEL=transcript:<path>` is a new model mode on the scripted seam, and the turn log's `stub` flag gains the value `transcript`. `demo/chat-beat` specifies both until Phase 7's specs are recorded.

## Requirements

| Requirement | Details |
|-------------|---------|
| Zero CFBD calls outside group J | Unit, CLI and integration tests use fakes, the mock sidecar and synthetic recordings; the harness guard against `CFBD_API` stays. Kit build, rehearse, mode switch, preflight, weekly (except its refresh step), bench, sync, report and release check spend nothing |
| Live cap | ≤ 70 ledgered calls for `add-cfb-demo-hardening`, all in 2026-11 (0 in October). Every live step goes through `make record`, `make refresh … PLAN=` or the app with `APP_LEDGER_PLAN`, with `CONFIRM=yes` or the app's confirmation. The demo itself counts under `app` |
| Zero Anthropic calls in tests | Stub and transcript models only; live chat runs only in task 10.6, gated on the key and ≤ $10 (owner approval) |
| AWS etiquette | Deploy and run only: no stop, start or resize. No replay on AWS. AWS work only outside other sessions' windows |
| Shared local DB | Integration tests and `make bench TARGET=local` run under `flock <scratchpad>/cfb-it.lock` in "local free" windows; `make rehearse` refuses while a loader or simulation run holds the lock (Phase 2's lock) |
| Privacy | Kits, recordings, snapshots, facts, transcripts and videos stay under `~/.cfb-demo/` (outside the repo and OneDrive); `cache/` stays git-ignored; `docs/benchmark.md` and `docs/demo-script.md` hold derived numbers only (no response body, no stored row dumps) |
| Secrets | The sys password is read at connect time (`vs/host/target.py`); app and chat passwords and the Anthropic key only in `.env`; the CFBD key never leaves the connection. The release check reads secrets into memory only and never prints them |
| Exasol SQL | Identifiers double-quoted in generated SQL; DDL names unquoted upper case and not reserved; `NULLIFZERO` for every division; `EXECUTE SCRIPT` never `WITH OUTPUT` |
| Performance (targets; measured in 10.4) | `make rehearse` ≤ 6 min on the laptop (restore ≤ 5 min, Phase 2 target); replayed `REFRESH_WEEK` with 100k sims < 2 min (mission); dial at 1M ≤ 60 s plus publication (Phase 4); preflight ≤ 60 s; `release-check --fast` ≤ 5 s |
| UTC | Every new timestamp is UTC |
| Coverage | ≥ 90 % line coverage from unit and CLI tests for `demo/config.py`, `demo/kit.py`, `demo/scriptcheck.py`, `bench/report.py` and `scripts/release_check.py` |

## Dependencies

- **Phases 2–7 implemented and merged into `main`** before groups B–F; group A needs only what it names.
  - **Phase 2:** `loader/snapshot.py` (restore, location rules), `loader/templates.py` and the load plan, `scripts/loader_cli.py` / `loader/guard.py`, `vs/host/ledger.py`, harness snapshot helpers. Its group J (AWS backfill, snapshot) before task 10.1.
  - **Phase 3:** `CFB_MODEL.SEASON_WEEK`, `CURRENT_AS_OF`.
  - **Phase 4:** `SIMULATE`, `DROP_SIM_RUN`, `SIM_CONFIG`, `SIM_RUN(_STEP)`, the sim league, `make sim`/`simulate`/`sim-drop`. Its group J (1M on AWS) before 10.4.
  - **Phase 5:** `make analyses`/`predict`/`publish`, `PUBLISH_RUN`, `analyses/perf.py`, the sqlobjects allowlist, `PUBLISHED_RUNS`, scorecard labels, `make grants`.
  - **Phase 6:** the operator action machinery, `app_stack.py` with `refreshed(10)`, the console catalog, `security.py`, the frontend shell and inspector.
  - **Phase 7:** `questions.yaml`, `make chat-rehearse`, the scripted-model seam, the chat stack, the user-guide MCP recipe.
- **Inbound hand-offs from Phase 7,** all taken:
  - **P8-a:** the `demo` bindings, beat 5 from the must-pass questions, the chat on AWS in the recipe, and stub with suggestions as the offline fallback (tasks 5.2, 8.1, 10.5, 10.6).
  - **P8-b:** the Claude Desktop moment with `exasol-cfb` and `CFB_MCP` (tasks 8.1, 8.2, 10.6, U4).
  - **P8-c:** the exit without a key, with Phase 7's 7.2–7.3 and the recorded live transcript (tasks 5.3, 10.6).
- **Cross-plan changes this plan makes itself** (named X8 to keep them apart from Phase 7's P8 hand-offs; in code the predecessors own, after they merge; the orchestrator tells their sessions):
  - **X8-a (Phase 2/6 guard):** `PLAN=` for `make refresh` (default unchanged) (task 2.4).
  - **X8-b (Phase 5):** the scale views and the allowlist entries for `CFB_BENCH` (task 2.2).
  - **X8-c (Phase 6):** the dial routes and operator entries, the port in the cookie name, the scale card (tasks 4.1, 4.2).
  - **X8-d (Phase 7):** the `demo` bindings and the transcript model mode on the scripted seam (tasks 5.2, 5.3).
- **Phase 6's cut line.** If the orchestrator applied it, the moved stretch items and their scenarios join this plan as a group-D task (4.3), with the scenarios added to this plan's verification by the orchestrator. None is demo-critical.
- **Phase 7's exit without a key.** Its tasks 7.2 and 7.3 move here as task 10.6, gated on the key. Without a key, beat 5 is the stub.
- **Tools.** No new runtime dependency. `gitleaks` is optional (used if installed).
- **Owner actions** (blocking only the task named):
  - U1: list `add-cfb-demo-hardening: 70` in `vs/live/ledger.yaml` (the orchestrator may add it as a sub-allocation, as for Phases 5 and 6), before 10.1; approve ≤ $10 of Anthropic spend for 10.6.
  - U2: if the laptop should refresh live on stage, create `CFBD_API` on the local deployment with the user-guide SQL (D18), before 10.3. Without it the laptop runs replay and the live proof is App B's preview click on AWS.
  - U3: set `CFB_APP_PASSWORD`, `CFB_CONSOLE_PASSWORD` and `CFB_CHAT_PASSWORD` in `.env`, and run `make app-user` and `make chat-user` on both targets (9.1). Add `ANTHROPIC_API_KEY` when available (10.6).
  - U4: add the `exasol-cfb` entry to Claude Desktop per the user guide and ask one question (10.6).
  - U5: the Sunday cadence runs (Nov 1, 8, 15, and 22 if needed), by the owner or the orchestrator.
  - U6: confirm the demo date and the network at the venue a week ahead; approve the bubble team and pairs from `make kit-pick` (10.5).
  - U7: record the fallback video of a full rehearsal, with a chapter per beat, plus screenshots (10.7).
  - U8: confirm the `LICENSE` holder ("Exasol AG") and MIT for exasol-labs. Confirm CFBD's terms allow committing the OpenAPI spec and the derived numbers (Q13).
  - U9: decide what ships publicly (`specs/_plans/` orchestration notes, `spikes/`, decision logs), whether to rewrite history after the release check, and when to publish. All of this is outside this plan.
  - U10: on demo day, close memory-heavy apps, restart local Exasol with `exasol stop/start -d local` (never `exakit`), and use mains power and a backup network.

## Migration

| Current | New |
|---------|-----|
| `make deploy`: VS → loader → model → sim → analyses | VS → loader → model → sim → **bench** → analyses (`CFB_API` scale views read `CFB_BENCH`) |
| `vs/host/namespace.py` | + `bench_schema` (`{prefix}CFB_BENCH`); renderer + `{BENCH_SCHEMA}` |
| `tests/it/harness.py` | `drop_namespace`/`leftovers` cover `CFB_BENCH`, the `TEST2_` peer namespace, and test kit paths under BucketFS `cfbd/kits/test-*`; helpers `build_test_kit`, `peer_namespace`, `kit_dir` (pytest tmp path) |
| `vs/live/ledger.yaml` | + `add-cfb-demo-hardening: 70` (U1) |
| `scripts/loader_cli.py` / `loader/guard.py` | `--ledger-plan` (make `PLAN=`), default unchanged |
| Phase 6 `security.py` | session cookie `cfb_session_<port>` |
| `Makefile` | + `kit`, `kits`, `rehearse`, `demo-mode`, `demo-preflight`, `kit-pick`, `kit-facts`, `weekly`, `chat-bindings-check`, `chat-transcript`, `bench`, `bench-sync`, `bench-report`, `release-check`; `lint` runs `release_check --fast` last |
| `pyproject.toml` | mypy `files` + `demo`, `bench`; coverage sources + the pure modules |
| `.env.example` | + `CFB_KIT_DIR`, `BENCH_LABEL`, and the App B line (`APP_PORT=8001` with `TARGET=aws`) |
| `README.md` | external-audience README (task 1.2/8.3) |

## Implementation Tasks

Tasks are TDD per `/speq:code-guardrails`: unit tests first for pure modules, the integration (or CLI) test file first, failing, for each DB or CLI task, and component tests first for frontend work. Integration tests run with `flock <scratchpad>/cfb-it.lock make test-it` in "local free" windows at 0 CFBD and 0 Anthropic calls.

1. **Pure code and Labs files (no DB; can start before the predecessors merge, except where noted)**
   - [ ] 1.1 `scripts/release_check.py`: the rules of `demo/release-check`. The known-secret loader reads deployment files and `.env` into memory, and masked output never shows 8 consecutive characters of a secret. The history walk covers `git rev-list --all` and blobs. Optional gitleaks. The JSON report goes to `cache/release-check/`. Add `make release-check`, and `release_check --fast` as the last step of `make lint`. Unit tests `tests/unit/test_release_rules.py` (rules over strings); CLI tests `tests/cli/test_release_check_cli.py` (temporary git repositories, fake home). [expert]
   - [ ] 1.2 `LICENSE` (MIT, "Copyright (c) 2026 Exasol AG", pending U8) and a first external `README.md`:
     - what it is: the three promises;
     - architecture (ASCII);
     - requirements: Exasol Personal 2026.2, Python 3.12 + uv, Node per Phase 6, podman/WSL notes;
     - quickstart: clone; `make deploy`; create `CFBD_API` with your key (user-guide SQL); `make deploy` again and `make grants`; `make backfill PROFILE=demo` (estimate first, ≈ 217 calls); `make refresh`; `make app-user`; `make app-build && make app-serve`; optional chat key;
     - costs in calls; data terms (no redistribution, derived outputs only, private snapshots); "Data provided by CollegeFootballData.com"; not affiliated with the NCAA or the CFP; an Exasol Labs (community-supported) note;
     - links to the user guide, the demo script and the benchmark.

     Final numbers and links come in 8.3.
   - [ ] 1.3 `demo/demo.yaml` + `demo/config.py` (schema, loader, validation: kit names, pairs, question ids against `questions.yaml` once Phase 7 merged) and `demo/kit.py` (pure parts):
     - names and locations, reusing `loader.snapshot.check_location`;
     - the `kit.json` schema;
     - the required slice keys (refresh keys from `loader.templates` and the deployed load plan's weekly entries for a season; console keys from Phase 6's console catalog; previews from `vs.cfbd_vs.plan.build_slice_key`);
     - the two week checks over a recorded `/games` body (flattened with `vs.cfbd_vs.flatten`) and over a snapshot's `CFB_CORE.GAMES.csv.gz` (`csv` over gzip, columns by catalog name).

     Unit tests `tests/unit/test_demo_kit.py`, `test_demo_config.py`. Needs Phase 2 merged (`loader.templates`, `loader.snapshot`) and Phase 6's console catalog (else its keys are pinned in `demo.yaml` until it merges).
   - [ ] 1.4 `demo/scriptcheck.py`: parse fenced blocks with info strings `sql demo` and `sql show`, their line numbers, placeholder filling from a kit and `demo.yaml`, and the statement-kind rule (reads only). Unit tests `tests/unit/test_demo_scriptcheck.py`.
   - [ ] 1.5 `bench/report.py` + `bench/report_template.md`: render `docs/benchmark.md` from result rows (setup, per-suite tables, the targets with met or missed, method). The output is deterministic and holds derived numbers only. Unit tests `tests/unit/test_bench_report.py` (also the scenario "The report states every target …"). `bench/matrix.yaml` + its loader, with unit tests.

2. **Database objects and plumbing (after Phases 2–6 merge; TEST_ namespace)**
   - [ ] 2.1 `CFB_BENCH`:
     - `sql/bench/*.sql` per Key Interfaces; `bench/deploy.py` through `vs/host/sqlobjects.py`; `make deploy` order VS → loader → model → sim → bench → analyses;
     - `BENCH_SETTINGS` from `TARGET` (`BENCH_LABEL` overrides);
     - `vs/host/namespace.py` `bench_schema`;
     - harness: deploy, drop and leftovers, and a `peer_namespace("TEST2_")` fixture with Phase 4's run-registry data tables, `SIM_CONFIG` and `CFB_BENCH`.

     Integration test file first: `tests/it/test_bench_it.py` (deploy part).
   - [ ] 2.2 `sql/api/700_scale_runs.sql`, `710_scale_run_steps.sql`: comments, `NULLIFZERO`, Phase 5's allowlist entries, and `make api-docs` regenerated. Its integration tests sit in `tests/it/test_app_scale_it.py` (card scenarios, read through `CFB_API` as the app role).
   - [ ] 2.3 `bench/sync.py` + `make bench-sync` (`--peer-prefix` for tests): both directions, MERGE by key, `DEMO` runs from the last 60 days with their steps, and the `BENCH_*` rows the other side lacks; all or nothing when a side is unreachable. Test: `test_bench_sync_both_ways_once` in `tests/it/test_bench_it.py`.
   - [ ] 2.4 `make refresh … PLAN=` (`--ledger-plan` on the loader CLI or guard, default unchanged) with CLI tests in `tests/cli/test_loader_cli.py`; propose the `vs/live/ledger.yaml` entry `add-cfb-demo-hardening: 70` (U1).

3. **Kits and rehearsal**
   - [ ] 3.1 `make kit` / `make kits` (`scripts/demo.py`, `demo/kit.py` I/O): build from a snapshot and a source directory, the checks, the `make record` hint, modes 0700/0600, `FORCE`. The catalog-hash check reads the deployment's adapter hash. Integration test file first: `tests/it/test_demo_kit_it.py`, with the harness helper `build_test_kit` (sim-league snapshot at week 9, synthetic week-10 recordings from Phase 6's `refreshed(10)` machinery, synthetic console and preview bodies). CLI tests `tests/cli/test_demo_cli.py` (location and overwrite rules with a fake connection).
   - [ ] 3.2 `make demo-mode` (`demo/mode.py`): `ALTER VIRTUAL SCHEMA` per Key Interfaces; the connection-name check; the DNS gate (scripts/dns, injectable in tests); refusal on `aws`; the kit derived from `REPLAY_PATH`. Tests: `test_live_switch_needs_connection_and_dns_gate` (IT) and `test_replay_mode_refused_on_aws_and_without_kit` (CLI).
   - [ ] 3.3 `make rehearse` (`demo/install.py`):
     - the 8 steps of `demo/rehearsal`: BucketFS copy with SHA-256 checks and, after the switch to replay, one replay read of the smallest recording (the UDF sees the files), the restore through `loader.snapshot`, `make analyses`, the base publication, the report (headline kind from `CFB_API` scorecard columns, recorded completeness from `kit.json`), and the live switch;
     - refusal while the run lock is held.

     Integration test file first: `tests/it/test_demo_rehearsal_it.py`, covering install, the replayed refresh, two-rehearsal equality, no network or key (connection access revoked, mock stopped), and the failed live refresh → replay recovery (mock 500 on the `GAMES` slice). [expert]
   - [ ] 3.4 `make kit-pick` (`demo/picks.py`) and `make kit-facts` (`demo/facts.py`): read `CFB_API` as the app role at 0 calls; facts as JSON and Markdown in the kit, with a diff against the previous facts. Integration tests in `tests/it/test_demo_rehearsal_it.py` (`test_kit_facts_equal_across_rehearsals` supports the determinism scenario; `test_kit_pick_candidates`).
   - [ ] 3.5 `make weekly` (`demo/weekly.py`): the six steps, `FROM=`, `ALLOW_LATE`, and the predict-after-kickoff refusal. CLI tests with fake targets and fake step runners in `tests/cli/test_demo_cli.py` (order, stop on failure, resume without the paid step, refusal after kickoff).

4. **App: scale dial and card**
   - [ ] 4.1 Backend (`app/backend/ops/scale.py`, `routes/scale.py`, `sqlcatalog.py` entries, `operator.py` allowlist, `/api/config` `scale`, `security.py` cookie name):
     - the background action on the existing action machinery, the run-id lookup, publication after the run, and lock refusals;
     - the size taken from `SIM_CONFIG`; no ledger lock.

     Integration test file first: `tests/it/test_app_scale_it.py` (plan, run and publish, size and lock, operator-only, card side by side, card offline, two instances). `tests/unit/test_app_operator.py` is extended with the new templates. [expert]
   - [ ] 4.2 Frontend (`src/scale/ScaleCard.tsx`, `DialButton.tsx`, the inspector tab, the odds banner line): component tests first (`ScaleCard.test.tsx`: two columns aligned by step, ratios, "measured … · imported …", "not measured yet"; `DialButton.test.tsx`: confirmation text, disabled reasons, progress). Contrast floors per Phase 6.
   - [ ] 4.3 (conditional) Phase 6's cut-line stretch items, if the orchestrator applied the cut, with the scenarios it moves into this plan.

5. **Preflight and the chat beat**
   - [ ] 5.1 `make demo-preflight` (`demo/preflight.py`):
     - every check of `demo/preflight` (`--now`, `--peer-prefix`), the requested slices from `EXPLAIN VIRTUAL` manifests (`vs/host/fetchlog.parse_manifest`), and the script check through 1.4;
     - exit codes;
     - pure evaluators unit-tested (`tests/unit/test_demo_preflight_eval.py`: peer points, ledger room with headroom, staleness).

     Integration test file first: `tests/it/test_demo_preflight_it.py`, with a fixture demo script `tests/it/fixtures/demo-script.md`.
   - [ ] 5.2 Chat beat on the kit (P8-a):
     - `make chat-bindings-check`, and the preflight's `chat` check;
     - the `demo` dataset schema in `questions.yaml`: the sim league's values for tests, the kit's in 10.5;
     - `demo/demo.yaml` validation of `chat_questions` against the must-pass flags of `questions.yaml`: a stage question that is not must-pass is refused, naming Phase 7's rule.

     Integration test file first: `tests/it/test_demo_chat_beat_it.py` (scenarios 1 and 2: chat stack on the test kit).
   - [ ] 5.3 Transcript replay mode (P8-c, X8-d), an explicit build in Phase 7's code:
     - `make chat-transcript KIT= CHAT=` (`demo/transcript.py`): turn-log lines (`CHAT_LOG_PATH`) → per question the text, app state, ordered tool calls (name and validated input), app actions and final answer; only `done`, non-stub turns; pinned to the kit's `kit.json` SHA-256 and point;
     - `TranscriptChatModel` in `app/backend/chat/` (selected by `CHAT_MODEL=transcript:<path>`): it implements the scripted-model interface, feeds the recorded tool calls as `tool_use` blocks, step by step in recorded order, and the recorded answer as the final text, so the real tool loop, disclosure and app actions run;
     - the turn log marks those turns `transcript` with zero usage;
     - at start, a transcript whose kit or point does not match the database falls back to the stub, with the reason in `/api/config` `chat`.

     Conformance: it passes Phase 7's stateful conformance rules like the scripted model does. Integration tests first: scenarios 3 and 4 of `tests/it/test_demo_chat_beat_it.py`, with fixture turn-log lines; unit tests `tests/unit/test_demo_transcript.py` (line selection, refusals, pinning).

6. **Benchmark harness**
   - [ ] 6.1 `bench/harness.py` + `make bench`: the five suites, labels `BENCH <cell> #<n>`, copy then `DROP_SIM_RUN` unless `--keep`, `analyses/perf.py` reuse, the run-lock refusal, and the IT lock for local. Integration test file first: the remaining scenarios of `tests/it/test_bench_it.py` (test matrix 4,000 sims at 500 and 1,000).

7. **Quality gate**
   - [ ] 7.1 `make lint` clean (ruff, format, mypy with `demo` and `bench`, eslint/tsc, `release_check --fast`). `make coverage` meets the Requirements row. `make test`, `flock … make test-it` and `make app-test` are green, including every predecessor's tests. `make release-check` runs on this repository: fix every `FAIL` in the tree; list history findings for U9.

8. **Documentation** (after 7.1; re-read each file first; append to decision tables only)
   - [ ] 8.1 `docs/demo-script.md`:
     - the outline of Key Interfaces with exact button names from the built app;
     - `sql demo` / `sql show` blocks;
     - pre-show checklist, beats 1–6 with timing, fallback ladder and reset;
     - the season cadence and the kit recipe (record → kit → rehearse → kit-pick → record previews → kit → facts);
     - an "Expected numbers" section filled from `make kit-facts` of the demo kit in 10.5.

     It must pass the preflight's `script` check.
   - [ ] 8.2 `docs/user-guide.md` § "Demo: kits, rehearsal and modes", § "Scale dial and benchmark", § "Release check", and the MCP recipe made concrete for Claude Desktop on Windows with WSL:
     - the entry `exasol-cfb`, `uvx exasol-mcp-server@<pinned version>`;
     - `EXA_DSN=localhost:8563`, `EXA_USER=CFB_CHAT_WEB`, `EXA_PASSWORD` (a local-only, read-only user's password lives in the client config: rotate it after the demo with `make chat-user ROTATE=yes`);
     - `EXA_MCP_SETTINGS` with `enable_read_query` and `schemas.like_pattern` `CFB_API`, and `EXA_SSL_CERT_VALIDATION=false` on loopback only;
     - Claude Code in WSL as the alternative (Q6, Q7).
   - [ ] 8.3 Final `README.md` (numbers from `docs/benchmark.md`, links), `LICENSE` holder per U8; `make release-check` passes.
   - [ ] 8.4 `specs/architecture.md`:
     - a "Demo topology" section (two app instances, kits, per-kit replay paths, modes, cadence);
     - `CFB_BENCH` in the schema table, and the scale views in `CFB_API`'s reads;
     - the security model: the operator path stays loopback-only, no authentication, the release check;
     - the open question "AWS sizing" left with the owner.
   - [ ] 8.5 `docs/roadmap.md`:
     - decisions marked `Promotes to ADR: yes`, numbered after the last D-number present;
     - Phase 8 progress;
     - the demo-flow wording: beat 3 `EXECUTE SCRIPT CFB_INFRA.REFRESH_WEEK(2026, N, 'regular')` (if Phase 2's docs task has not already changed it), the dial "laptop 100k vs AWS 1M (measured)", and beat 5's MCP coda;
     - open question "Which week N to record" resolved by the kit rule;
     - the API budget table with the cadence (4/week) and the kit recordings.
   - [ ] 8.6 `CLAUDE.md`, dense bullets:
     - kits live in `~/.cfb-demo/kits` and are never committed;
     - `make rehearse`/`demo-mode`/`demo-preflight`/`weekly`/`bench`/`bench-sync`/`release-check`;
     - replay is local only; never `demo-mode replay` on AWS;
     - the cadence's calls and plan;
     - `release_check --fast` in lint;
     - `CFB_BENCH`.

     `specs/mission.md`:
     - § Commands (the new targets);
     - § Project Structure (`demo/`, `bench/`);
     - capability 11's wording;
     - the presenter persona's recipe (kit, preflight, live or replay).

9. **Deployment (0 calls)**
   - [ ] 9.1 Release commit on both targets:
     - `make deploy` and `make deploy TARGET=aws` (every schema including `CFB_BENCH`), `make grants` on both;
     - U3's users on both;
     - `make bench-sync`, and `make demo-preflight TARGET=aws` (its AWS subset).

     Record the AWS DB version and that nothing was stopped, started or resized.

10. **Live, calendar-driven (≤ 70 calls under `add-cfb-demo-hardening`; dates per Q1; needs U1)**
    - [ ] 10.1 Sun Nov 1: `make weekly WEEK=9 CONFIRM=yes` (or the Phase 2 commands if `make weekly` is not merged yet). First check the 2026 week dates in `CFB_MODEL.SEASON_WEEK` (Q1). Then `uv run python -m vs.host.ledger --summary`.
    - [ ] 10.2 Sun Nov 8:
      - `make weekly WEEK=10`, then the recordings, all on `TARGET=aws`, into `cache/replay/` (4 + 3 calls; the 3 console slices once): `make record PATTERNS=<file> CONFIRM=yes ARGS="--plan add-cfb-demo-hardening --rerecord"` for the four 2026 refresh slices and the console's three fan-out slices;
      - `make kit NAME=2026-w09-w10 SEASON=2026 WEEK=9 SNAPSHOT=aws-2026-w09`, `make rehearse`, the replayed refresh in App A, `make kit-pick`;
      - record ≤ 2 previews, then `make kit … FORCE=yes`;
      - `make rehearse` and refresh twice, with `make kit-facts` after each: no difference;
      - the offline drill (Wi-Fi off: rehearse, refresh, every beat in replay, the chat stub).
    - [ ] 10.3 Live dress rehearsal #1 (U2): `make rehearse KIT=2026-w09-w10 MODE=live CONFIRM=yes`, the preflight `MODE=live AWS=yes`, beats 1–6 with `APP_LEDGER_PLAN=add-cfb-demo-hardening` (≤ 8 calls), and the dial on App B. Time each beat, note every rough edge in the decision log, and drill the live → replay switch once.
    - [ ] 10.4 Benchmark, then the report:
      - `make bench TARGET=local` (local free window, IT lock) and `make bench TARGET=aws`;
      - `make bench-sync`, then `make bench-report`.

      Check Q9 and Q10, and log any miss with its step breakdown.
    - [ ] 10.5 Sun Nov 15:
      - `make weekly WEEK=11`; record the four 2026 refresh slices, then `make kit NAME=2026-w10-w11 SEASON=2026 WEEK=10 SNAPSHOT=aws-2026-w10`, rehearse, `kit-pick` (U6), record ≤ 2 previews, rebuild the kit;
      - fill `demo/demo.yaml` and the `demo` bindings;
      - run `make kit-facts` and fill the demo script's expected numbers;
      - `make demo-preflight KIT=2026-w10-w11 MODE=live AWS=yes`: exit 0.
    - [ ] 10.6 Live dress rehearsal #2 (≤ 8 calls), with the chat:
      - **Without a key** (P8-c): beat 5 on the stub with its suggestions, and `make chat-rehearse ARGS="--dataset demo --only RQ01,RQ04,RQ10,RQ14,RQ15"` (stub) on the kit.
      - **With a key** (U3, U1):
        - if Phase 7's 7.2–7.3 moved here, run them first, within Phase 7's $20;
        - re-qualify the must-pass set on the final demo bindings: `make chat-rehearse LIVE=yes MAX_USD=4 ARGS="--dataset demo --only RQ01,RQ04,RQ10,RQ14,RQ15 --repeat 2"`, with every question 3/3 (Phase 7 rule) (≤ $4);
        - run beat 5 live once in App A's panel on the kit (≤ $1);
        - `make chat-transcript KIT=2026-w10-w11 CHAT=<that chat id>`;
        - replay it with `CHAT_MODEL=transcript:…` offline (Wi-Fi off) and check that it passes;
        - write the derived numbers into `docs/chat-verification.md` (Phase 7's file).
      - The Claude Desktop coda (P8-b, U4): RQ10's text, the tool call's SQL shown.
      - The chat on AWS in App B, once (P8-a; live only, ≤ $1).

      This plan's Anthropic spend stays ≤ $10 (U1), on top of Phase 7's $20 if its 7.2–7.3 ran here.
    - [ ] 10.7 Wrap-up:
      - the fallback video and screenshots (U7);
      - `make release-check`;
      - the ledger total (≤ 70) and the Anthropic spend (≤ $10);
      - `docs/demo-hardening-verification.md` with derived numbers only (timings per beat, preflight results, ledger and spend totals, the determinism diff).
    - [ ] 10.8 (only if the demo is after Nov 22) Sun Nov 22: `make weekly WEEK=12`, kit `2026-w11-w12` as in 10.5 (≤ 10 calls).

## Parallelization

| Parallel Group | Tasks |
|----------------|-------|
| Group A | 1.1, 1.2, 1.3, 1.4, 1.5 |
| Group B | 2.1, 2.4 |
| Group C | 2.2, 2.3, 3.1, 3.2 |
| Group D | 3.3, 4.1 |
| Group E | 3.4, 3.5, 4.2, 5.1, 6.1 |
| Group F | 5.2, 5.3, 4.3 |
| Group G | 7.1 |
| Group H | 8.1, 8.2, 8.3, 8.4, 8.5, 8.6 |
| Group I | 9.1 |
| Group J (sequential, calendar) | 10.1 → 10.2 → 10.3 → 10.4 → 10.5 → 10.6 → 10.7 (→ 10.8) |

Sequential dependencies:
- A → B → C → D → E → F → G → H → I; J runs on its calendar (below).
- 1.1, 1.2, 1.4 and 1.5 need nothing and can start now. 1.3 needs Phase 2 merged (and Phase 6's console catalog, or the pinned keys).
- Groups B–F need Phases 2–6 merged with green integration tests; 5.2 and 5.3 also need Phase 7 merged.
- 2.2 needs 2.1. 2.3 needs 2.1. 3.1 needs 1.3. 3.2 needs Phase 2's DNS gate. 3.3 needs 3.1 and 3.2. 4.1 needs 2.2 and Phase 6's action machinery. 3.4 needs 3.3. 3.5 needs 2.3 and 2.4. 4.2 needs 4.1's response models (it can start on fixtures). 5.1 needs 1.4, 2.2 and 3.3. 6.1 needs 1.5, 2.1 and 2.3. 5.2 and 5.3 need 3.3.
- **Calendar for J:**
  - 10.1 on Nov 1 does not wait for groups B–I. It uses the Phase 2 commands if needed (its refresh then counts against Phase 2's plan).
  - 10.2 needs 3.1–3.4 merged by Nov 8.
  - 10.3 needs 4.1–4.2 and 5.1 for the full run; without them, the dial runs as `make simulate TARGET=aws SIMS=1000000` plus `make publish` while App B follows the publication.
  - 10.4 needs 6.1. 10.5 needs 5.1 and 8.1's draft. 10.6 needs 5.2, and 5.3 for the transcript part.

**Demo-minimum cut line** (if the calendar presses; the cut never touches security, spending or privacy):
- **Must by the demo:** 1.3, 1.4, 2.4, 3.1–3.4, 5.1 (at least the `db`, `kit`, `point`, `mode`, `recordings`, `week`, `publication`, `headline`, `script`, `connection`, `dns`, `ledger` checks), 8.1, 9.1, 10.1–10.3, 10.5.
- **Should:** 2.1–2.3, 4.1–4.2 (else two windows and the CLI dial), 6.1 + 10.4 (else the script quotes Phase 4's verification numbers), 5.2 and 5.3 (else plain stub mode, and the transcript is a document in the kit), 3.5 (else the cadence runs as single commands).
- **After the demo is fine:** 1.1, 1.2, 8.3 (Labs readiness is for the public release, not for the demo), 8.4–8.6, 10.7's verification document.

## Dead Code Removal

| Type | Location | Reason |
|------|----------|--------|
| Docs | `README.md` "Status: planning" paragraph | Replaced by the external README (1.2, 8.3) |
| Docs | `docs/roadmap.md` open question "Which week N to record for replay mode, and when" | Resolved by the kit rule (8.5) |
| Field | Phase 6's session cookie name without the port | Replaced by `cfb_session_<port>` (4.1) |
| — | `spikes/` | Not removed by this plan: Phase 4 left removal to the owner after Phase 8 (U9); the release check reports it, and shipping it is the owner's call |

## Verification

### Scenario Coverage

| Scenario | Test Type | Test Location | Test Name |
|----------|-----------|---------------|-----------|
| rehearsal-kit: A kit packages the snapshot and the recordings of the next week's refresh | Integration | `tests/it/test_demo_kit_it.py` | `test_kit_packages_snapshot_and_recordings` |
| rehearsal-kit: A missing recording is refused with the command that records it | Integration | `tests/it/test_demo_kit_it.py` | `test_missing_recording_names_record_command` |
| rehearsal-kit: A recording of an unfinished replay week is refused | Integration | `tests/it/test_demo_kit_it.py` | `test_unfinished_replay_week_refused` |
| rehearsal-kit: A snapshot that already holds results of the replay week is refused | Integration | `tests/it/test_demo_kit_it.py` | `test_snapshot_with_replay_week_results_refused` |
| rehearsal-kit: Kits stay private and are not overwritten by accident | Integration (CLI) | `tests/cli/test_demo_cli.py` | `test_kit_location_and_overwrite_rules` |
| rehearsal-kit: Building a kit spends no CFBD call | Integration | `tests/it/test_demo_kit_it.py` | `test_kit_build_spends_no_call` |
| rehearsal: Installing a kit resets the database to the Sunday after week N in replay mode | Integration | `tests/it/test_demo_rehearsal_it.py` | `test_rehearse_resets_to_base_week_in_replay` |
| rehearsal: The replayed refresh moves every page to week N+1 at zero calls | Integration | `tests/it/test_demo_rehearsal_it.py` | `test_replayed_refresh_moves_pages_zero_calls` |
| rehearsal: Two rehearsals of one kit produce the same pages | Integration | `tests/it/test_demo_rehearsal_it.py` | `test_two_rehearsals_same_pages` |
| rehearsal: A rehearsal runs without network or key | Integration | `tests/it/test_demo_rehearsal_it.py` | `test_rehearsal_without_network_or_key` |
| rehearsal: A failed live refresh falls back to replay without a restore | Integration | `tests/it/test_demo_rehearsal_it.py` | `test_failed_live_refresh_falls_back_without_restore` |
| rehearsal: Replay mode is refused where no recordings can be read | Integration (CLI) | `tests/cli/test_demo_cli.py` | `test_replay_mode_refused_on_aws_and_without_kit` |
| rehearsal: Switching to live needs the key's connection and the DNS gate | Integration | `tests/it/test_demo_rehearsal_it.py` | `test_live_switch_needs_connection_and_dns_gate` |
| preflight: A ready kit passes every check | Integration | `tests/it/test_demo_preflight_it.py` | `test_ready_kit_passes` |
| preflight: A query without its recording fails before the demo does | Integration | `tests/it/test_demo_preflight_it.py` | `test_missing_recording_fails_recordings_check` |
| preflight: The preflight never spends a call and never writes | Integration | `tests/it/test_demo_preflight_it.py` | `test_preflight_never_spends_or_writes` |
| preflight: A broken statement in the demo script fails the script check | Integration | `tests/it/test_demo_preflight_it.py` | `test_broken_script_statement_fails` |
| preflight: A kit older than the newest completed week is flagged | Integration | `tests/it/test_demo_preflight_it.py` | `test_stale_kit_flagged` |
| preflight: Live mode checks the connection, the DNS gate, the ledger and the quota | Integration | `tests/it/test_demo_preflight_it.py` | `test_live_mode_checks` |
| preflight: The scorecard's honesty label is reported before the demo | Integration | `tests/it/test_demo_preflight_it.py` | `test_headline_kind_reported` |
| preflight: The AWS side of the scale dial is checked against the kit | Integration | `tests/it/test_demo_preflight_it.py` | `test_peer_check_before_and_after_sync` |
| scale-dial: The dial plans its run at zero calls | Integration | `tests/it/test_app_scale_it.py` | `test_dial_plan_zero_calls` |
| scale-dial: The dial runs and publishes a run at the current point | Integration | `tests/it/test_app_scale_it.py` | `test_dial_runs_and_publishes` |
| scale-dial: The dial takes its size from the configuration and its turn from the lock | Integration | `tests/it/test_app_scale_it.py` | `test_dial_size_from_config_and_lock` |
| scale-dial: The dial exists only where the operator path does | Integration | `tests/it/test_app_scale_it.py` | `test_dial_only_on_operator_path` |
| scale-dial: The scale card shows both deployments' newest runs side by side | Integration + component | `tests/it/test_app_scale_it.py`, `app/frontend/src/scale/ScaleCard.test.tsx` | `test_scale_card_side_by_side`, `renders both deployments aligned by step` |
| scale-dial: The card works when the other deployment is out of reach | Integration + component | `tests/it/test_app_scale_it.py`, `app/frontend/src/scale/ScaleCard.test.tsx` | `test_scale_card_offline_peer`, `shows measured time and not measured yet` |
| scale-dial: Run summaries are synced both ways, once, at zero calls | Integration | `tests/it/test_bench_it.py` | `test_bench_sync_both_ways_once` |
| scale-dial: Two app instances on one laptop keep separate sessions | Integration | `tests/it/test_app_scale_it.py` | `test_two_instances_separate_sessions` |
| benchmark: The sims suite measures each cell three times and records the sizing | Integration | `tests/it/test_bench_it.py` | `test_sims_suite_three_samples_with_sizing` |
| benchmark: Benchmark runs are dropped after measuring and their numbers kept | Integration | `tests/it/test_bench_it.py` | `test_bench_runs_dropped_numbers_kept` |
| benchmark: The what-if suite records latency percentiles per condition count | Integration | `tests/it/test_bench_it.py` | `test_whatif_suite_percentiles` |
| benchmark: Refresh, load and on-demand numbers come from the logs | Integration | `tests/it/test_bench_it.py` | `test_refresh_load_ondemand_from_logs` |
| benchmark: The report states every target as met or missed with derived numbers only | Unit | `tests/unit/test_bench_report.py` | `test_report_targets_met_or_missed_derived_only` |
| benchmark: The benchmark never spends a call and waits for no one | Integration | `tests/it/test_bench_it.py` | `test_bench_refuses_while_locked_and_spends_nothing` |
| chat-beat: Every bound name of the demo dataset resolves at the kit's point | Integration | `tests/it/test_demo_chat_beat_it.py` | `test_demo_bindings_resolve` |
| chat-beat: The stage questions pass in stub mode on the kit, offline | Integration | `tests/it/test_demo_chat_beat_it.py` | `test_stage_questions_stub_offline` |
| chat-beat: A recorded live conversation replays on its kit at no cost | Integration | `tests/it/test_demo_chat_beat_it.py` | `test_transcript_replays_on_kit` |
| chat-beat: A transcript is built only from finished live turns and replays only on its kit | Integration | `tests/it/test_demo_chat_beat_it.py` | `test_transcript_build_and_kit_binding_rules` |
| release-check: A secret in a tracked file fails the check without being printed | Integration (CLI) | `tests/cli/test_release_check_cli.py` | `test_tracked_secret_fails_masked` |
| release-check: A known secret anywhere in history fails the full check | Integration (CLI) | `tests/cli/test_release_check_cli.py` | `test_known_secret_in_history_fails` |
| release-check: Raw API data in the tree or in history fails the check | Integration (CLI) | `tests/cli/test_release_check_cli.py` | `test_raw_data_tree_and_history` |
| release-check: A missing license or credit fails the check | Integration (CLI) | `tests/cli/test_release_check_cli.py` | `test_license_and_credit` |
| release-check: Placeholders and documented test markers pass | Integration (CLI) | `tests/cli/test_release_check_cli.py` | `test_placeholders_pass` |
| release-check: Personal paths and e-mail addresses are warnings | Integration (CLI) | `tests/cli/test_release_check_cli.py` | `test_personal_paths_warn` |
| release-check: The fast check runs with every lint and reads no secret | Integration (CLI) | `tests/cli/test_release_check_cli.py` | `test_fast_check_in_lint_reads_no_secret` |

Supporting tests (pure computation, no I/O; they back the integration tests, never replace them):
- `tests/unit/test_demo_kit.py`: names, locations, required keys, and the week checks over synthetic bodies and CSV;
- `tests/unit/test_demo_config.py`;
- `tests/unit/test_demo_scriptcheck.py`;
- `tests/unit/test_demo_preflight_eval.py`;
- `tests/unit/test_demo_transcript.py`;
- `tests/unit/test_release_rules.py`;
- `tests/unit/test_bench_matrix.py`;
- `tests/unit/test_app_operator.py` (extended);
- `app/frontend/src/scale/DialButton.test.tsx`;
- `tests/cli/test_demo_cli.py` (weekly order, resume, late-predict refusal);
- `tests/cli/test_loader_cli.py` (`--ledger-plan`).

### Manual Testing

All commands run from the repo root. Rows without a target run on the laptop (`TARGET=local`). Rows marked *(live, counted)* spend ledgered calls under `add-cfb-demo-hardening`.

| Feature | Command | Expected Output |
|---------|---------|-----------------|
| demo/rehearsal-kit | *(live, counted)* `make record TARGET=aws PATTERNS=cache/demo/w10-refresh.txt CONFIRM=yes ARGS="--plan add-cfb-demo-hardening --rerecord"`; `make kit NAME=2026-w09-w10 SEASON=2026 WEEK=9 SNAPSHOT=aws-2026-w09`; `make kits`; `ls -la ~/.cfb-demo/kits/2026-w09-w10` | 4 calls in the ledger; "week 10 complete: n/n FBS games", "snapshot at week 9: week 10 unplayed"; the kit listed with its recordings; 0700/0600 modes |
| demo/rehearsal | `make rehearse KIT=2026-w09-w10 CONFIRM=yes`; App A → Refresh week 10 (replay); `make rehearse` + refresh again and `make kit-facts KIT=2026-w09-w10` after each; then with Wi-Fi off the same again; then `make demo-mode MODE=replay TARGET=aws` | Report "current point 2026 week 9 · replay over …/kits/2026-w09-w10 · base publication …"; timeline all `DONE`, 0 calls, `REPLAY`; `kit-facts` "no difference"; offline identical; the AWS replay switch refused |
| demo/preflight | `make demo-preflight KIT=2026-w10-w11 MODE=live AWS=yes`; then delete one installed recording and run it with `MODE=replay` | All PASS (WARN allowed for headline, sizing, stale), exit 0; then `FAIL recordings` naming the slice and beat, exit 2 |
| demo/scale-dial | `make app-serve TARGET=aws APP_PORT=8001`; App B → Scale dial → confirm; `make bench-sync`; App A inspector → Scale | Dial run of 1,000,000 seasons `DONE` in ≤ 60 s and published; App B's odds reload; App A's card shows laptop 100k and aws 1M side by side with sizing, seconds and "measured …" |
| demo/benchmark | `make bench TARGET=aws`; `flock <scratchpad>/cfb-it.lock make bench TARGET=local`; `make bench-sync`; `make bench-report`; `git diff --stat docs/benchmark.md` | Medians of 3 per cell with sizing; no `BENCH` run left; `docs/benchmark.md` lists each target as met or missed |
| demo/chat-beat | `make chat-bindings-check KIT=2026-w10-w11`; `CHAT_MODEL=stub make app-serve` and click the suggestions RQ01, RQ04, RQ10, RQ15; (with a key) one live beat 5 in the panel, then `make chat-transcript KIT=2026-w10-w11 CHAT=<chat id>` and, with Wi-Fi off, `CHAT_MODEL=transcript:$HOME/.cfb-demo/kits/2026-w10-w11/chat-transcript.json make app-serve`; Claude Desktop with `exasol-cfb` asking RQ10's text | Every binding resolved with its id; stub answers with the kit's numbers and their SQL, RQ15 answered as read-only; transcript answers verbatim at $0 offline, turns marked `transcript`; Claude Desktop answers through `execute_exasol_query` on `CFB_API` as `CFB_CHAT_WEB` |
| demo/release-check | `make lint`; `make release-check` | Lint ends with "release check (fast): 0 FAIL"; the full check lists 0 FAIL in the tree, history findings (if any) for U9, WARNs for personal paths, and never prints a secret |

### Checklist

| Step | Command | Expected |
|------|---------|----------|
| Generate | `make generate && git diff --exit-code -- vs/catalog sql/core` | Exit 0 |
| Build | `uv run python -m vs.bundle && uv run python -m loader.bundle && uv run python -m engine.bundle && make app-build` | Exit 0 |
| Test | `make test` | 0 failures (unit + CLI + vitest) |
| Integration | `flock <scratchpad>/cfb-it.lock make test-it` | 0 failures; mock and replay only; 0 requests to `api.collegefootballdata.com`; 0 Anthropic requests |
| Lint | `make lint` | 0 ruff, mypy, eslint and tsc errors; release check (fast) 0 FAIL |
| Format | `uv run ruff format --check .` | No changes |
| Coverage | `make coverage` | ≥ 90 % for the modules in Requirements, and the predecessors' packages as before |
| Deploy | `make deploy && make deploy TARGET=aws` | Exit 0 both; `CFB_BENCH` and the scale views present; a second run drops nothing |
| Release | `make release-check` | 0 FAIL in the tree; history findings listed for the owner |
| Preflight | `make demo-preflight KIT=<demo kit> MODE=live AWS=yes` | Exit 0 |
| Ledger | `uv run python -m vs.host.ledger --summary` | `add-cfb-demo-hardening` ≤ 70; 2026-10 entries for it: 0 |
| No data in git | `git ls-files '*.parquet' '*.cfbd.json' '*.csv.gz' 'cache/*'` | Empty |
