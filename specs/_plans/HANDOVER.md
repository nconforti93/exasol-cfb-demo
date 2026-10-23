# Orchestrator handover (for Codex or any agent taking over)

**Read this whole file before doing anything.** It is the single source of truth for the autonomous run and is kept current by whoever orchestrates. `ORCHESTRATOR-STATUS.md` next to it is the older, partly stale history log; when the two disagree, this file wins.

**Living document.** Whoever is orchestrating (Claude Code until it runs out, then Codex) updates the **Current state** and **Log** sections after every milestone: a commit, a merge, a live run, a decision, or a new blocker. Bump the "Last updated" line. Commit it on the main tree branch with a message `handover: <what changed>`.

Last updated: 2026-10-23 by Codex after Phase 4 task 1.4 and AWS deploy

---

## 1. The mission in one paragraph

`exasol-cfb-demo` is an Exasol demo for Nico Conforti (Lead PM, Exasol core DB), due **mid-November 2026, demo window Nov 16–20**. It loads College Football Data (CFBD API) into Exasol through a virtual schema (`CFBD_VS`), persists it (`CFB_CORE`), models it with SQL views (`CFB_MODEL`), simulates the season in a Python UDF score engine (playoff odds, predictions), exposes analyses (`CFB_API`), and shows all of it in a web app (FastAPI + React) with an optional chat. Roadmap: `docs/roadmap.md`. Mission: `specs/mission.md`. Architecture: `specs/architecture.md`. Repo rules: `CLAUDE.md` (read it: the hard rules apply to you too).

## 2. Owner mandate and hard rules (non-negotiable)

Nico is mostly away. He authorized an autonomous run under these limits:

- **Light process (2026-10-23):** "this is a demo, not a full-blown product offering... we don't need a crazy amount of tests." One plan pass per phase, no separate plan review, code review only for risky code (spend limits, locks, the chat SQL guard), a few tests per feature covering the demo path. Thin end-to-end slices first, hardening later. **Priority now: a usable web app on real data**: US map home with team logos → team page → league tabs.
- **CFBD API calls:** at most **400 in October 2026** (240 used per `/info` as of 2026-10-23) and **1000 in November 2026**. Caps live in `vs/live/ledger.yaml`; every live call is logged in `cache/live-calls.jsonl`. The tooling enforces this. Never bypass it.
- **AWS instance (Exasol Personal deployment `default`, 2026.1.1, 3 nodes):** deploy and run only. **Never stop, start, resize or destroy it.**
- **Never run `exakit` runtime commands or plain `exakit mcp-doctor`.** `deployments/default` is the AWS instance. Only `exakit mcp-doctor --json` is safe. The local DB is controlled with `exasol start -d local` / `exasol stop -d local`.
- **Git:** commit locally only, **never push**. One branch per plan (git worktrees, below). Merge to `main` only after the plan is verified and recorded.
- **Secrets:** never read, print or log the CFBD key or any password. Tooling reads them from `~/.exasol/personal/deployments/<local|default>/secrets.json` via `vs/host/target.py`. Never extract `CONNECTION` secrets from the database (e.g. via a UDF).
- **sys owns everything** in the demo deployment (owner decision). Roles are fine; the creator/owner is sys.
- **Don't touch** schemas `CFB_DIAG`, `VECTOR_DEMO`, `VEC_SMOKE`, or anything outside `CFB_*`, `CFBD_VS`, `TEST_*`, `PERF_*`.
- **Untracked files from a parallel session are not yours:** `spikes/`, `docs/spikes/s3*`, `docs/spikes/s4*`, `specs/_plans/add-engine-spikes/`, `.serena/`. Never add, commit or delete them. You may *read* the spike write-ups (Phase 4 inputs).
- **Exasol syntax:** check reserved words and syntax with the Exasol skills or docs before writing SQL. Past reserved-word hits: `SOURCE`, `NAMES`, `RESULT`, `PROFILE`, `STATE`, `FULL`. The list is in `vs/gen/exasol_reserved_keywords.txt`.
- **Integration tests / local DB use** must hold the lock: `flock /tmp/claude-1002/-mnt-c-Users-nc-OneDrive---EXASOL-AG-Documents-Notes/d157cdb7-6061-4526-8ef3-8d5d6fabb341/scratchpad/cfb-it.lock <cmd>`. If `/tmp` was wiped, any `flock` on that path recreates it. No `pgrep` wait loops.
- **Decisions:** decide yourself and log them in the plan's `decision-log.md`. Stop and ask Nico only for irreversible, money-costing or outward-facing actions.
- **Permission denials:** if your sandbox or the user denies a command, don't work around it. Use a sanctioned alternative or skip, and add it to "Blocked on Nico" below.
- **Phase 7 chat:** there is no Anthropic API key. Build and test with the scripted/stub model, but never call the real API.
- **Secrets location (owner, 2026-10-23):** app/console/chat passwords and `ANTHROPIC_API_KEY` live in `~/.cfb-demo/app.env` (0600), never in the repo `.env` (the repo is on OneDrive, which syncs to the cloud).
- **Logos:** team logos are cached locally in `~/.cfb-demo/logos/`, outside the repo and OneDrive. Never commit them and never hotlink ESPN at runtime.
- **Commit trailer:** end commit messages with the co-author line your tool uses. Claude used `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`; Codex should use its own or none.

## 3. Layout

| Path | Branch | Purpose |
|---|---|---|
| `~/Github/exasol-cfb-demo` (main tree) | `add-cfbd-vs-foundation` | Orchestrator branch: holds the plans for Phases 4, 5, 7(orig), 8 and this file. 10 commits ahead of `main` (plan commits only) |
| `../exasol-cfb-demo-p2` | `add-cfb-persistence-refresh` | Phase 2. **Done and recorded** (df2a647 = `main`) |
| `../exasol-cfb-demo-p3` | `add-cfb-analytical-model` | Phase 3, **done and recorded** (= `main`) |
| `../exasol-cfb-demo-p5` | `add-cfb-analyses` (from main) | Phase 5 CFB_API views |
| `../exasol-cfb-demo-p6` | `add-cfb-app` | Phase 6 web app (UI revision done; live routes next) |
| `../exasol-cfb-demo-p4` | `add-cfb-score-engine` (from p3) | Phase 4 score engine |
| `../exasol-cfb-demo-p7` | `add-cfb-chat` (from p6) | Phase 7 chat, groups A–B done, rest blocked |

`~/Github` = `/mnt/c/Users/nc/OneDrive - EXASOL AG/Github Projects`. The repo is on OneDrive (slow I/O). Python via `uv run` (in p7 a native venv at `~/.cache/p7venv` via `UV_PROJECT_ENVIRONMENT` is much faster). Frontend `node_modules` live at `~/.cache/cfb-app-frontend` (symlinked, outside OneDrive).

**New phase:** create a worktree from `main` (`git worktree add ../exasol-cfb-demo-pN -b <plan-name> main`), then copy the latest plan folder from the main tree (`git checkout add-cfbd-vs-foundation -- specs/_plans/<plan>`). Lesson learned: always sync the latest plan into a new worktree.

**Databases**
- **local:** Exasol Personal nano 2026.2 at 127.0.0.1:8563. Has the full real data restored from the AWS snapshot (2026-10-23) in `CFB_CORE`, `FETCH_MODE='replay'` (no live calls). Integration tests use `TEST_*` namespaces plus a mock CFBD sidecar container started by the test harness.
- **aws:** deployment `default`, `TARGET=aws` in make commands, TLS fingerprint pinning. Holds `CFBD_VS` (live), `CFB_CORE` with real data, the loader scripts, and soon `CFB_MODEL`.
- Snapshots: `~/.cfb-demo/snapshots/aws-demo-2026-10-23`.

**speq workflow:** plans live in `specs/_plans/<plan>/` (`plan.md`, `tasks.md`, spec deltas, `decision-log.md`). The `speq` CLI is at `~/.local/bin/speq` (`speq plan validate <plan>`, `speq feature validate`). Codex has the speq skills in `~/.codex/skills/` (speq-plan, speq-implement, speq-record, …). Use them. Task markers in `tasks.md`: `[ ]` open, `[~]` in progress, `[x]` done, `[-]` superseded. Record = merge the deltas into `specs/`, append ADRs to `specs/decision-log.md`, move the plan to `specs/_recorded/<date>-<plan>/` (see `specs/_recorded/` for the two existing examples).

## 4. Current state (update this section after every milestone)

**Codex took over on 2026-10-23.** Phase 4 task 1.4 is committed as `3fa2fbc` after 2 focused IT tests passed (190.40 s), focused unit tests, mypy and Ruff. Local `make model` and `make sim` passed; the first 10k simulation of 2026 after week 7 is `DONE` (run 1, 14.879 s, 143 teams, 377 games, zero CFBD calls). Local Auburn what-if: P(CFP) 4.85% now, 11.37% if it beats LSU in week 8 (3,651 matching seasons, query 0.163 s). The model correction and simulation objects are deployed on AWS (DEPLOY runs 22 and 23, zero CFBD calls). Task 1.5's AWS 100k/1M runs are next. AWS quota baseline is 241 of the October project cap of 400. The p5 uncommitted task set at handover is listed below. If `CFB_META.LOAD_RUN` still shows a `RUNNING` row from the stopped Claude session, the run lock treats it as stale.

| Phase | Plan | Where | State |
|---|---|---|---|
| 1 VS foundation | add-cfbd-vs-foundation | recorded | **Done.** Live on AWS |
| 2 Persistence & refresh | add-cfb-persistence-refresh | recorded | **Done.** 221 live calls. AWS `CFB_CORE`: 155k rows (GAMES 1869–2026, SP+/WP 2013–2026, polls 2014–2026, TEAMS 1933, VENUES 852). 2026 is refreshed through regular week 7. The local DB has an identical copy (snapshot restore; Oct 31 restore gate **passed**) |
| 3 Analytical model | add-cfb-analytical-model | recorded, **`main` = 2a26584** | **Done.** `CFB_MODEL` is live on AWS and local. 8 derived tables are rebuilt by refresh step 100 `CFB_INFRA.REFRESH_MODEL`, registered on both targets. Also as-of views and `MODEL_CHECKS`. ADR-031..046. Page queries run in 0.07–0.8 s on AWS. **Rule:** filter as-of views with literal `AS_OF_SEASON`/`AS_OF_WEEK_SEQ`; never join `CURRENT_AS_OF` |
| 4 Score engine | add-cfb-score-engine | **p4** (HEAD 3fa2fbc) | Light plan 513455b: 14 tasks in 3 slices; see `tasks.md`. **Done:** 1.1 engine core (a70c7fc), 1.2 `CFB_SIM` schema + `CFB_INFRA.SIM_ENGINE` UDF (74af62c), 1.3 fitted params + engine input views (5395e21; HFA 3.14, SIGMA_1 16.6, TAU 2.83; margin RMSE 16.9 vs the CFBD line's 15.7), 1.4 stored runs and what-if (3fa2fbc; local 10k smoke and focused IT passed; model and sim deployed on AWS). **Next:** 1.5: 100k/1M on AWS plus Auburn what-if. Then slice 2: 2.1 predictions freeze/grade (**deadline below**), 2.2 movement + rooting guide, 2.3 live. Then slice 3 |
| 5 Analyses / CFB_API | add-cfb-analyses | **p5** (HEAD 1af6214) | Light plan 1af6214: 8 tasks. Slice A 1.1→1.5 is 20 engine-free `CFB_API` views, roles `CFB_APP`/`CFB_MCP`, `make api`. Slice B 2.1–2.3 is thin views over Phase 4 objects, gated on Phase 4 1.4/2.1/2.2 being merged into p5. **1.1 is in progress, uncommitted (see below)** |
| 6 Web app | add-cfb-app | **p6** (HEAD b900208) | UI revision done:<br>• 9.0 foundation 3342b9c<br>• 9.1 shell + 9.2 US map 3545207<br>• 9.3 team page + 9.4 league week/stats 7003bcb<br>• 9.5 logos 15779cc (138 FBS logos in `~/.cfb-demo/logos`, from cdn.collegefootballdata.com, [43])<br>• 9.6 real-data snapshot preview a307279<br>• secrets loader bc401d0 ([44])<br>**The preview runs at http://127.0.0.1:5173/** (`make app-preview REAL=1` with `CHOKIDAR_USEPOLLING=1`; it serves JSON from `~/.cfb-demo/fixtures/latest`, a snapshot taken 2026-10-23 08:01 UTC, **not live DB**). Real: map, team pages, this week, stats, search. Sample: odds, predictions, upsets, H2H, outlooks. **Next:** 2.2 `app/backend/main.py` + `scripts/app.py` (serve/user; users `CFB_APP_WEB`/`CFB_CONSOLE_WEB`, passwords from `~/.cfb-demo/app.env`), 9.7 live routes over `CFB_API` (needs Phase 5 slice A), 9.8 outlook (needs Phase 4 slice 1–2 + Phase 5 slice B). The logo route `make_router` is not mounted yet |
| 7 Chat | add-cfb-chat | p7 (HEAD 89f21ae) | Groups A–B done. Group C+ is blocked until Phases 5 and 6 merge (gate 3.0). Hand-off `P7-secrets`: adopt `vs/host/appenv.py`. There is no Anthropic key, so build and test only |
| 8 Demo hardening | add-cfb-demo-hardening | main tree (plan only) | Plan reviewed under the heavy process; do a light replan first. Due: the freeze rule and CLAUDE.md hard rules by **Oct 31**; `make weekly` by **Nov 8** |

### Handover work sets (p4 completed; p5 still uncommitted)

**p4 task 1.4 (completed in 3fa2fbc; retained here as handover history)** (`SIMULATE` Lua, output split, `SIM_TEAM_ODDS`, `engine/whatif.py`, `make simulate`/`make what-if`):
- **Modified:** `Makefile`, `engine/deploy.py`, `loader/bundle.py`, `scripts/sim.py`, `tests/it/harness.py`, `tests/it/sim_support.py`, `tests/unit/test_model_seeds.py`, and **`sql/model/013_conference_championship.sql`**. That last edit adds a Pac-12 championship game from 2026: the rebuilt 8-team Pac-12 plays one, but the seed ended the Pac-12 in 2023, so its teams had `HAS_CCG` FALSE. Redeploy the model (`make model`) for it to take effect, and log the decision in the p4 decision-log.
- **New:** `engine/lua/`, `engine/whatif.py`, `sql/sim/230_sim_run_model_rows.sql`, `sql/sim/300_sim_team_odds.sql`, `tests/unit/test_simulate_bundle.py`, `tests/unit/test_whatif.py`.
- **At handover:** it was about to deploy locally (`make model`, then `make sim`) and run a 10k smoke under the IT lock. The local work and commit are now done.
- **Original completion checklist (local items done; AWS items in task 1.5):**
  1. Review the diff and run `make test` / `make lint`.
  2. Local smoke at 10k.
  3. `make sim TARGET=aws`.
  4. One AWS run of 2026 after week 7 at 100k (literal point: `AS_OF_SEASON=2026`, the week-7 `AS_OF_WEEK_SEQ`).
  5. Check the in-database ms per batch against the host's ~1.4 s per 10k batch (the BLAS pin).
  6. The Auburn what-if: P(CFP) now vs "Auburn wins its next game".
  7. Commit.
- **Recipe for `SIM_RUN_MODEL_ROWS`:** it is in the 1.3 notes, in `plan.md` Key Interfaces and `sql/sim/2*_engine_*_as_of.sql`.
  - `TEAM_IDX = ROW_NUMBER() OVER (ORDER BY TEAM_ID)-1`.
  - `GAME_IDX` per `GAME_ROLE` ordered by `START_AT, GAME_ID`.
  - `PATH_POS = GAME_IDX+1` for `REMAINING`.
  - Lower-case param keys.
  - Columns exactly `core.RUN_COLUMNS + core.MODEL_COLUMNS`.

**p5 task 1.1** (`make api`, first `CFB_API` views):
- **Modified:** `vs/host/sqlobjects.py`, `vs/host/namespace.py`, `vs/host/metaddl.py` and their unit tests.
- **Where it stopped:** it had just started taking Phase 4's generalized deployer text, which plan decision [56] says to copy identically from p4. Compare these three files with p4's HEAD versions; if p4's are a superset, take p4's text.
- **Then:** build the 1.1 views, deploy, and continue 1.2→1.5.
- **Source for the view SQL:** reuse `../exasol-cfb-demo-p6/scripts/app_fixtures.py`, which has working real-data SQL for the map, team, week and stats.

### Recommended next steps (in order)
1. **p4: finish 1.4 and 1.5.** This gives the first real odds and the Auburn what-if, which Nico asked about directly. Report the numbers to Nico.
2. **p4: 2.1 predictions freeze/grade, plus refresh week 8, before Tue 2026-10-27 23:00 UTC**, so week-9 predictions count as made in advance. Week 8 completes Sat Oct 24; refresh it Sun Oct 25 or Mon Oct 26 with `make refresh SEASON=2026 WEEK=8 TARGET=aws CONFIRM=yes` (about 4 calls).
3. **p5: slice A (1.1→1.5)**, deployed on both targets.
4. **p6: 2.2 backend main + users, then 9.7 live routes** over `CFB_API`, then retire the snapshot as the default. This is the point where the app reads the DB live. Nico explicitly wants this.
5. **p4: 2.2/2.3, then p5 slice B, then p6 9.8**, so the outlook, odds tab and what-if are live.
6. **Merge order to `main`:** p4 when slice 1–2 is verified, then p5, then p6. Merge `main` into p6 and p7 before their gated groups. Record each with the speq record workflow (light, as for Phases 2–3).
7. **Phase 7 group C+, Phase 8 light replan,** and the weekly cadence from Nov 1.

**Communication with Nico.** Nico prefers short, plain status updates, and wants the app usable on real, live data as early as possible. Before each change, say which data a page shows: snapshot or live.

## 5. Key dates (orchestrator owns these)

- **Oct 31:** restore gate (already passed 2026-10-23). Phase 8 freeze rule and CLAUDE.md hard rules due.
- **Sun Oct 25 / Mon Oct 26:** refresh week 8 (it completes Sat Oct 24; Oct budget room ~158 calls), then freeze week-9 predictions before **Oct 27 23:00 UTC**. Avoid live runs 2026-10-25 00:00–01:00 UTC (DST).
- **Sundays Nov 1, 8, 15:** weekly cadence: AWS snapshot (catch-up until Tuesday), `REFRESH_WEEK` for the completed week, run predictions locally before Tuesday's kickoff, copy recordings to `~/.cfb-demo/recordings/`. From Nov 1 the November cap (1000) applies; a redeploy on AWS picks it up (decision [46]). Don't start a live run on 2026-10-25 between 00:00 and 01:00 UTC (DST hour).
- **Nov 3 / Nov 10:** predictions published before kickoff.
- **Nov 9:** go/no-go on demo tier (Full / B no chat / C no app / D VS+model).
- **Nov 16–20:** demo. Default mode: live proof, replay data.

## 6. Owner actions (Nico) and blocked items

Owner list U1–U11 is in `ORCHESTRATOR-STATUS.md` ("Owner actions"). Most relevant: U3 `ANTHROPIC_API_KEY` in `.env` when he has it; U4 Claude Desktop MCP entry by Nov 13; U6 confirm demo date and venue network; U8 license/CFBD terms/team marks.

**Blocked on Nico:** none.

## 7. Log (newest first; one line per milestone)

- 2026-10-23: **Phase 4 task 1.4 committed** (3fa2fbc). Two focused IT tests passed; AWS model and simulation deploys passed as DEPLOY runs 22 and 23, zero CFBD calls. Task 1.5 AWS runs next.

- 2026-10-23: **Local Auburn what-if passed.** From 10k run 1, P(CFP) rises from 4.85% to 11.37% if Auburn beats LSU in week 8; query 0.163 s. The smoke exposed and p4 fixed a Makefile `RUN` variable collision and duplicate `SCHOOL` columns. Pac-12 CCG seed decision logged as p4 [58].

- 2026-10-23: **Phase 4 first local simulation.** `make model` and `make sim` passed locally; 10k 2026-after-week-7 seasons stored as `DONE` run 1 in 14.879 s (engine 7.097 s), 143 teams and 377 games; zero CFBD calls. AWS quota baseline: 241/400.

- 2026-10-23: **Codex took over as orchestrator.** Read `CLAUDE.md` and this handover, checked worktree state, and started Phase 4 task 1.4 with an implementation agent. No database or live API call yet.

- 2026-10-23: **Handover to Codex.** Claude stopped p4 1.4 and p5 1.1 mid-task (uncommitted WIP listed in §4). Nothing running.

- 2026-10-23: Phase 5 light replan committed (1af6214); slice A started.

- 2026-10-23: Phase 4 1.3 fit done on AWS (5395e21); main merged into p4; 1.4 started.

- 2026-10-23: p5 worktree created; Phase 5 light replan started.

- 2026-10-23: Phase 3 recorded (2a26584), `main` fast-forwarded. p4 and p6 still need `main` merged in (p4 after 1.3 commits).

- 2026-10-23: Phase 4 1.1 engine core committed (a70c7fc).

- 2026-10-23: Phase 3 materialization (8d4d739): page queries 0.07–0.8 s on AWS. Phase 3 finish+record started.

- 2026-10-23: Phase 4 1.2 CFB_SIM schema + UDF deployed (74af62c); 1.3 started.

- 2026-10-23: Phase 4 light replan committed (513455b); 1.1 + 1.2 started.

- 2026-10-23: Phase 3 as-of views + checks done (475c534), merged into p4 (42b2121); 4.1+4.3 started.

- 2026-10-23: p4 worktree created; Phase 4 light replan started.

- 2026-10-23: App secrets moved to ~/.cfb-demo/app.env (bc401d0, [44]); CLAUDE.md hard rule added in p6.

- 2026-10-23: Nico approved moving app secrets to ~/.cfb-demo/app.env; change running in p6.

- 2026-10-23: 9.6 real-data preview live at http://127.0.0.1:5173/ (a307279).

- 2026-10-23: 9.1 shell + 9.2 US map home committed (3545207).

- 2026-10-23: 9.3 team page + 9.4 league week/stats committed (7003bcb).

- 2026-10-23: CFB_MODEL live on AWS+local (8c62f8a, merge ebb877f); 9.6 and 3.9–3.11 started.

- 2026-10-23: 9.0 UI foundation committed (3342b9c); 9.1–9.4 started.

- 2026-10-23: 9.5 logos committed; 276 logo files fetched from cdn.collegefootballdata.com (not ESPN; [43]). Route `make_router` not yet mounted (no app.py yet).

- 2026-10-23: HANDOVER.md created for a possible Codex takeover (Claude at ~80 % usage).
- 2026-10-23: Phase 6 UI revision planned (674ff29); 9.0 and 9.5 started.
- 2026-10-23: Phase 2 recorded (df2a647), `main` fast-forwarded. ADR-013..030.
- 2026-10-23: Phase 2 live group J on AWS: 221 calls, restore gate passed.
- 2026-10-23: Owner switched to the light process; asked for map-first UI.
- 2026-10-23: Phase 3 3.8 TEAM_SEASON (de085a7); Phase 7 2.1 turn loop (89f21ae); fixture preview (64f983a).
