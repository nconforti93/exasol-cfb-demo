# Orchestrator handover for Claude Code

**Read this whole file before doing anything.** It is the single source of truth for the autonomous run and is kept current by whoever orchestrates. `ORCHESTRATOR-STATUS.md` next to it is the older, partly stale history log; when the two disagree, this file wins.

**Living document.** Update **Current state** and **Log** after each milestone and bump "Last updated." On 2026-10-30 Nico asked for everything to be committed and merged into one tree: commit locally on `cfb-demo-integrated`, never push.

Last updated: 2026-10-30 by Claude Code after merging every phase into one tree (`cfb-demo-integrated`)

---

## 0. Resume here

- **One tree now (2026-10-30).** Every phase branch (foundation, p2–p8) is committed and merged into branch `cfb-demo-integrated` in the main folder `~/Github/exasol-cfb-demo`; the sibling worktrees p2–p8 are removed (their branches remain). Venv: `UV_PROJECT_ENVIRONMENT=$HOME/.cache/cfbvenv`. The app runs from the main folder: `UV_PROJECT_ENVIRONMENT=$HOME/.cache/cfbvenv APP_LEDGER_PLAN=add-cfb-app setsid nohup make app-serve TARGET=aws`. Mentions of p4/p5/p6/p7/p8 below refer to those branches, now all in this tree.
- **Known gaps in the merged tree:** `make chat-user` and `make chat-rehearse` are declared in the Makefile but their scripts (`scripts/app.py chat-user`, `scripts/chat_rehearse.py`) belong to the unfinished chat groups C+; `make rehearse` (Phase 8 L4) does not exist yet. CLAUDE.md was rewritten on 2026-10-30 to hold durable rules only; status belongs here.
- **App:** `http://127.0.0.1:8000/` serves real AWS data. At handover its homepage and `/api/config` answered; verify health on resume. `/api/config` reported `target=aws`, `FETCH_MODE=LIVE`, next target 2026 regular week 9, 1392 of 1400 app-plan calls unspent, and 146 calls of October ledger room (after the 3-call console fan-out). The app is the p6 worktree, launched with `APP_LEDGER_PLAN=add-cfb-app setsid nohup make app-serve TARGET=aws` (log in Claude's session scratchpad). To restart, kill the `make`/`scripts.app serve` PIDs explicitly; `pkill -f` with that pattern kills the calling shell. Do not stop the AWS database.
- **Fresh data:** AWS `REFRESH_WEEK` run 18 finished `DONE` on October 27 at 11:44 UTC, used four CFBD calls, and moved `CURRENT_POINT` to **2026 after regular week 8**. `/info` usage is **250/400 for October**; the shared ledger has one Phase 6 preview call plus `REFRESH_WEEK#18` costing four. Publication/what-if run 4 is current, odds return 138 teams, and 56 week-9 picks were frozen before the first listed kickoff at 23:00 UTC. The local database is still at week 7 in replay mode.
- **Why the refresh was initially blocked:** the former 12-call Phase 6 ledger allocation had 11 left, below the four due slices' 16-call maximum with retries. Nico explicitly ordered that separate restriction removed. P6 `vs/live/ledger.yaml` now gives `add-cfb-app` and `app` caps of 1400 each (decision [46]); October 400, November 1000, the shared ledger, retry bound and `REFRESH_SPENDABLE` still apply. The main orchestration branch does not contain that YAML file; keep the p6 version when integrating.
- **Owner's current Git/permission instruction:** unrestricted filesystem and Git access for this work; do not ask for routine permission. **Local commits on `cfb-demo-integrated` are allowed (2026-10-30); never push.** Do not reset, clean or discard staged or untracked work.
- **Console done (p6 4.3).** `routes/console.py` serves listing/explain/run as `CFB_CONSOLE_WEB`; AWS verified all four plans and runs (one paid fan-out, 3 calls). Evidence in p6 `docs/app-verification.md`.
- **Phase 8 light replan done; Oct 31 items done (uncommitted).** `specs/_plans/add-cfb-demo-hardening/tasks.md` (L1–L7) overrides the heavy task list (decisions [43]–[45]). New worktree `../exasol-cfb-demo-p8` (branch `add-cfb-demo-hardening` from `main`, venv `~/.cache/p8venv`) has `scripts/release_check.py` (freeze rule, content digests of the working tree), `make demo-freeze`, `make release-check-fast`, the freeze check last in `make lint`, and CLI tests. The `CLAUDE.md` hard rules are in p8, p6 and the main tree. Snapshot `aws-2026-w08` (0 calls) is the base for recording week 9 on Nov 1.
- **Next:** Nov 1 cadence (L2.1, plan `add-cfb-demo-hardening`, ≤ 8 calls): refresh week 9 on AWS, snapshot `aws-2026-w09`, record week-9 slices against `aws-2026-w08`, then `make demo-freeze FROM=2026-11-01 UNTIL=2026-11-21` on the integrated tree (load-plan.yaml must be the p4/p5 version AWS runs). Before that, integrate p4/p5 into p6 and p8's release check into it. Then meet the Oct 31 Phase 8 freeze deadline, harden refresh and preview recovery, adopt `loader/guard.py` in the CLI, finish app verification and source integration, and resume p7 chat with the scripted model. Detailed order is in §4 below.

## 1. The mission in one paragraph

`exasol-cfb-demo` is an Exasol demo for Nico Conforti (Lead PM, Exasol core DB), due **mid-November 2026, demo window Nov 16–20**. It loads College Football Data (CFBD API) into Exasol through a virtual schema (`CFBD_VS`), persists it (`CFB_CORE`), models it with SQL views (`CFB_MODEL`), simulates the season in a Python UDF score engine (playoff odds, predictions), exposes analyses (`CFB_API`), and shows all of it in a web app (FastAPI + React) with an optional chat. Roadmap: `docs/roadmap.md`. Mission: `specs/mission.md`. Architecture: `specs/architecture.md`. Repo rules: `CLAUDE.md` (read it: the hard rules apply to you too).

## 2. Owner mandate and hard rules (non-negotiable)

Nico is mostly away. He authorized an autonomous run under these limits:

- **Light process (2026-10-23):** "this is a demo, not a full-blown product offering... we don't need a crazy amount of tests." One plan pass per phase, no separate plan review, code review only for risky code (spend limits, locks, the chat SQL guard), a few tests per feature covering the demo path. Thin end-to-end slices first, hardening later. **Priority now: a usable web app on real data**: US map home with team logos → team page → league tabs.
- **CFBD API calls:** at most **400 in October 2026** (250 used per `/info` after run 18 on October 27) and **1000 in November 2026**. The active cap configuration is in p6 `vs/live/ledger.yaml`; every live call is logged in the shared main-tree `cache/live-calls.jsonl`. Never bypass the monthly caps or the ledger. Nico lifted only the separate 12-call Phase 6 allocation; see [46] and §0.
- **AWS instance (Exasol Personal deployment `default`, 2026.1.1, 3 nodes):** deploy and run only. **Never stop, start, resize or destroy it.**
- **Never run `exakit` runtime commands or plain `exakit mcp-doctor`.** `deployments/default` is the AWS instance. Only `exakit mcp-doctor --json` is safe. The local DB is controlled with `exasol start -d local` / `exasol stop -d local`.
- **Git:** commit locally on `cfb-demo-integrated` (Nico, 2026-10-30), never push. Git reads and reversible operations are authorized; do not ask him to approve them. Preserve every worktree's staged, modified and untracked work. One branch per plan (git worktrees, below); integrate only when verified.
- **Secrets:** never read, print or log the CFBD key or any password. Tooling reads them from `~/.exasol/personal/deployments/<local|default>/secrets.json` via `vs/host/target.py`. Never extract `CONNECTION` secrets from the database (e.g. via a UDF).
- **sys owns everything** in the demo deployment (owner decision). Roles are fine; the creator/owner is sys.
- **Don't touch** schemas `CFB_DIAG`, `VECTOR_DEMO`, `VEC_SMOKE`, or anything outside `CFB_*`, `CFBD_VS`, `TEST_*`, `PERF_*`.
- **Untracked files from a parallel session are not yours:** `spikes/`, `docs/spikes/s3*`, `docs/spikes/s4*`, `specs/_plans/add-engine-spikes/`, `.serena/`. Never add, commit or delete them. You may *read* the spike write-ups (Phase 4 inputs).
- **Exasol syntax:** check reserved words and syntax with the Exasol skills or docs before writing SQL. Past reserved-word hits: `SOURCE`, `NAMES`, `RESULT`, `PROFILE`, `STATE`, `FULL`. The list is in `vs/gen/exasol_reserved_keywords.txt`.
- **Integration tests / local DB use** must hold the lock: `flock /tmp/claude-1002/-mnt-c-Users-nc-OneDrive---EXASOL-AG-Documents-Notes/d157cdb7-6061-4526-8ef3-8d5d6fabb341/scratchpad/cfb-it.lock <cmd>`. If `/tmp` was wiped, any `flock` on that path recreates it. No `pgrep` wait loops.
- **Decisions:** decide routine implementation choices yourself and log them in the plan's `decision-log.md`. Nico has authorized Git and filesystem work and the week-8 live refresh. Future CFBD calls within the monthly caps still pass the ledger and app confirmation guard; no new permission prompt is needed for ordinary refresh work.
- **Permission denials:** if your sandbox or the user denies a command, don't work around it. Use a sanctioned alternative or skip, and add it to "Blocked on Nico" below.
- **Phase 7 chat:** there is no Anthropic API key. Build and test with the scripted/stub model, but never call the real API.
- **Secrets location (owner, 2026-10-23):** app/console/chat passwords and `ANTHROPIC_API_KEY` live in `~/.cfb-demo/app.env` (0600), never in the repo `.env` (the repo is on OneDrive, which syncs to the cloud).
- **Logos:** team logos are cached locally in `~/.cfb-demo/logos/`, outside the repo and OneDrive. Never commit them and never hotlink ESPN at runtime.
- **Commit trailer:** `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## 3. Layout

| Path | Branch | Purpose |
|---|---|---|
| `~/Github/exasol-cfb-demo` (the only tree) | `cfb-demo-integrated` | All phases merged (foundation, persistence, model, score engine, analyses, app, chat groups A–B, demo hardening). Phase branches are kept for history |

`~/Github` = `/mnt/c/Users/nc/OneDrive - EXASOL AG/Github Projects`. The repo is on OneDrive (slow I/O). Python via `uv run` with `UV_PROJECT_ENVIRONMENT=$HOME/.cache/cfbvenv` (a native venv, much faster than one on OneDrive). Frontend `node_modules` live at `~/.cache/cfb-app-frontend` (symlinked, outside OneDrive).

**New work:** stay in this one tree (owner, 2026-10-30: no more sibling worktree folders).

**Databases**
- **local:** Exasol Personal nano 2026.2 at 127.0.0.1:8563. Has the full real data restored from the AWS snapshot (2026-10-23) in `CFB_CORE`, `FETCH_MODE='replay'` (no live calls). Integration tests use `TEST_*` namespaces plus a mock CFBD sidecar container started by the test harness.
- **aws:** deployment `default`, `TARGET=aws` in make commands, TLS fingerprint pinning. Holds `CFBD_VS` (live), `CFB_CORE` through week 8, `CFB_MODEL`, `CFB_SIM`, 33 `CFB_API` stored views and the live quota view. It serves the port-8000 app.
- Snapshots: `~/.cfb-demo/snapshots/aws-demo-2026-10-23`.

**speq workflow:** plans live in `specs/_plans/<plan>/` (`plan.md`, `tasks.md`, spec deltas, `decision-log.md`). The `speq` CLI is at `~/.local/bin/speq` (`speq plan validate <plan>`, `speq feature validate`). Follow Nico's light process. Task markers in `tasks.md`: `[ ]` open, `[~]` in progress, `[x]` done, `[-]` superseded. Record = merge the deltas into `specs/`, append ADRs to `specs/decision-log.md`, move the plan to `specs/_recorded/<date>-<plan>/` (see `specs/_recorded/` for the two existing examples).

## 4. Current state (update this section after every milestone)

**Authoritative snapshot, 2026-10-27 11:45 UTC.** AWS is live through 2026 regular week 8 after owner-authorized `REFRESH_WEEK` run 18. That run completed CHECK, four one-call fetches, MODEL, PREDICT and SIMULATE; it published 1M-season odds and 56 week-9 predictions before kickoff. `/info` is 250/400 for October. The port-8000 app serves the updated point and runs with the p6 ledger plan. Local remains a week-7 replay copy. The older checkpoints are in §7 and are not instructions to repeat them.

**Worktree state.** The main orchestration tree has this uncommitted handover plus parallel-session untracked material that must remain untouched. P4 has five modified files for the calibration report and its task marker; p5 has a large mix of staged, modified and untracked Phase 4/5 integration work (do not reset its index); p6 has the uncommitted live app implementation, tests, decision [46] and `vs/live/ledger.yaml`; p7 is clean at HEAD `89f21ae`, with pure chat groups A–B complete. Nico wants no commits until his final large commit. The relevant task markers are in each worktree's `specs/_plans/<plan>/tasks.md`.

**Known gaps.** Conditional what-if guide works but took 6.5 s on a 1M-season AWS run; local app strict startup refuses unrelated `PREPROC_RT` privileges inherited through `PUBLIC` (AWS startup passes). A preview ledger-write failure blocks further paid actions in the current process, but durable recovery after process death remains open. Refresh process-death and lock/busy scenarios remain untested in isolation. Do not treat the working week-8 refresh as completion of Phase 6.

| Phase | Plan | Where | State |
|---|---|---|---|
| 1 VS foundation | add-cfbd-vs-foundation | recorded | **Done.** Live on AWS |
| 2 Persistence & refresh | add-cfb-persistence-refresh | recorded | **Done.** AWS `CFB_CORE` now refreshed through 2026 regular week 8 by `REFRESH_WEEK` run 18 on October 27. That run spent four CFBD calls. The local DB remains at week 7 (snapshot restore; Oct 31 restore gate **passed**) |
| 3 Analytical model | add-cfb-analytical-model | recorded, **`main` = 2a26584** | **Done.** `CFB_MODEL` is live on AWS and local. 8 derived tables are rebuilt by refresh step 100 `CFB_INFRA.REFRESH_MODEL`, registered on both targets. Also as-of views and `MODEL_CHECKS`. ADR-031..046. Page queries run in 0.07–0.8 s on AWS. **Rule:** filter as-of views with literal `AS_OF_SEASON`/`AS_OF_WEEK_SEQ`; never join `CURRENT_AS_OF` |
| 4 Score engine | add-cfb-score-engine | **p4** (HEAD 48f3713, five modified files) | Tasks 1.1–2.3 and 3.1–3.3 delivered. Week-9 predictions, odds movement and rooting guide are live on AWS; refresh steps 200/300 ran in AWS run 18. Calibration report ran read-only and left the seed unchanged, but task 3.2 still has an unchecked marker. **Next:** align that marker, finish docs 3.4 and verification; no commit |
| 5 Analyses / CFB_API | add-cfb-analyses | **p5** (HEAD 5431d8a, Phase 4 source staged without commit) | Slices A and B 1.1–2.3 delivered; 33 stored views and one live QUOTA view deployed on AWS and local, APP/MCP grants verified. AWS column readback and seven query timings passed. `GAME_PREVIEW` view could not push down its required filter, so Phase 6 uses the direct guarded operator path. **Next:** preserve and reconcile p5's staged/unstaged integration work, record the specs after verification and integrate into p6; no commit |
| 6 Web app | add-cfb-app | **p6** (HEAD d2e632d, uncommitted) | `http://127.0.0.1:8000/` serves the map, teams, odds/what-if, predictions, upsets and head-to-head from AWS. Owner decision [46] lifted the app-specific plan cap to 1400 while retaining October 400/November 1000 monthly caps and the full retry bound. Live refresh action `7b77863fbf3e4a4c70410f9f`, database run 18, completed every step and moved the point to 2026 after week 8; it spent four ledgered calls, taking `/info` to 250/400 and this plan to 5/1400. Publication and what-if run 4 are current; odds serve 138 teams and 56 week-9 picks are frozen before kickoff. Config now targets week 9 when complete. The loopback server runs with `APP_LEDGER_PLAN=add-cfb-app`. The curated console is live (task 4.3, 3 ledgered calls for the fan-out check; plan 8/1400). Process-death/lock integration and docs remain. Local app strict startup hits unrelated `PREPROC_RT` PUBLIC grants; AWS works. No commit. |
| 7 Chat | add-cfb-chat | p7 (HEAD 89f21ae) | Groups A–B done. Group C+ is blocked until Phases 5 and 6 merge (gate 3.0). Hand-off `P7-secrets`: adopt `vs/host/appenv.py`. There is no Anthropic key, so build and test only |
| 8 Demo hardening | add-cfb-demo-hardening | **p8** (from `main` 2a26584, uncommitted) + plan in main tree | Light replan done 2026-10-27 (`tasks.md` L1–L7, [43]–[45]). L1 (freeze rule, `demo-freeze`, lint hook, CLAUDE.md rules) and L2.0 (`aws-2026-w08` snapshot) done. Next: Nov 1 capture + freeze pins (L2.1), `make weekly` by **Nov 8** (L3), rehearse/abort (L4) |

### Worktree-specific resume notes

- **p4 `../exasol-cfb-demo-p4`:** HEAD `48f3713`. The calibration report source/evidence is modified in `engine/fit.py`, `scripts/sim.py`, `docs/engine-verification.md`, `tests/unit/test_fit.py` and `tasks.md`. It already ran read-only on AWS; do not rerun it merely because task 3.2 is still marked `[ ]`. Align that marker, finish task 3.4 docs, verify, then integrate without committing.
- **p5 `../exasol-cfb-demo-p5`:** HEAD `5431d8a`. Slice A/B views are already deployed and documented. The working tree has substantial staged *and* unstaged Phase 4/5 source plus untracked SQL/docs/tests. Do not reset or clean it. `tasks.md` marks 1.1–2.3 done; cross-plan relays and recording remain. Reconcile the source with p6 before recording permanent specs.
- **p6 `../exasol-cfb-demo-p6`:** HEAD `d2e632d`. The live routes, guarded preview, refresh worker, conditional rooting guide and spend changes are uncommitted; many new source files under `app/backend`, `analyses`, `engine` and `loader/guard.py` are untracked. `docs/app-verification.md` has evidence through AWS run 18. `specs/_plans/add-cfb-app/decision-log.md` [45] protects all-slice retries; [46] records Nico's cap change. The active cap file is p6 `vs/live/ledger.yaml`, with app plans 1400 and months 400/1000. The main orchestration branch has no `vs/live/ledger.yaml` yet. `speq plan validate add-cfb-app`, focused tests, Ruff, strict mypy and `git diff --check` passed for the recent work; run the final integrated checks after remaining tasks.
- **p7 `../exasol-cfb-demo-p7`:** clean at HEAD `89f21ae`. Pure chat groups A–B are complete. Group C onward needs Phase 5/6 integration; adopt `vs/host/appenv.py` and `~/.cfb-demo/app.env` before wiring chat secrets. No Anthropic key is present, so use only the scripted model and synthetic tests.
- **Main tree:** only this handover is modified. `.serena/`, `spikes/`, `docs/spikes/s3*`, `docs/spikes/s4*` and `specs/_plans/add-engine-spikes/` are parallel-session untracked files; preserve them.

### Recommended next steps (in order)

1. ~~Finish the p6 console (task 4.3).~~ Done 2026-10-27; see log.
2. ~~Phase 8 light replan and Oct 31 items.~~ Done 2026-10-27. Next Phase 8 dates: **Nov 1** capture + `make demo-freeze` (L2.1), **Nov 8** `make weekly` (L3) and kit `2026-w08-w09` rehearsal (L4). Integrate p8's `scripts/release_check.py`/Makefile targets into p6 before Nov 1.
3. **Finish spend and refresh hardening (p6 4.2/1.9).** Isolate process-death and lock/busy behavior against local `TEST_*`/replay. Make a paid preview recover safely across app restart if its ledger append fails. Move the Phase 2 CLI onto the shared `loader/guard.py`; review spend/lock code carefully. Preserve the `estimate × MAX_ATTEMPTS` bound, the month caps and the shared ledger. Do not repeat AWS week-8 refresh; week 9 is the next target but was incomplete at handover.
4. **Integrate and record delivered source.** Align p4's stale 3.2 task marker and finish its 3.4 docs. Preserve p5's staged/unstaged work, reconcile Phase 4/5 source into p6, verify the combined build, and record permanent specs/ADRs. Keep all changes uncommitted until Nico changes his instruction; never push.
5. **Close p6 verification and docs.** Complete app access integration (6.1), perf catalog (6.3), user guide/architecture/roadmap/`CLAUDE.md` (7.1–7.4), manual map→team→odds→refresh→preview rehearsal and verification report (8.1/8.3, R.1/V.1/V.3). Local strict startup sees unrelated `PREPROC_RT` PUBLIC grants; use the isolated test namespace or AWS for app verification, and do not alter that unrelated schema. The conditional guide works but measured 6.5 s on the 1M AWS run.
6. **Resume p7 chat after integration.** Groups C onward remain open; use a scripted model only and fix its secret loader hand-off before wiring chat. The Nov 9 tier decision should account for any unfinished chat work.

**Communication with Nico.** Nico prefers short, plain status updates, and wants the app usable on real, live data as early as possible. Before each change, say which data a page shows: snapshot or live.

## 5. Key dates (orchestrator owns these)

- **Oct 31:** restore gate (already passed 2026-10-23). Phase 8 freeze rule and CLAUDE.md hard rules due.
- **Oct 27:** week-8 refresh and the 56 frozen week-9 predictions completed by 11:44 UTC, before the first listed week-9 kickoff at 23:00 UTC. October usage is 250/400.
- **Sundays Nov 1, 8, 15:** weekly cadence: AWS snapshot (catch-up until Tuesday), `REFRESH_WEEK` for the completed week, run predictions locally before Tuesday's kickoff, copy recordings to `~/.cfb-demo/recordings/`. From Nov 1 the November cap (1000) applies; a redeploy on AWS picks it up (decision [46]). Don't start a live run on 2026-10-25 between 00:00 and 01:00 UTC (DST hour).
- **Nov 3 / Nov 10:** predictions published before kickoff.
- **Nov 9:** go/no-go on demo tier (Full / B no chat / C no app / D VS+model).
- **Nov 16–20:** demo. Default mode: live proof, replay data.

## 6. Owner actions (Nico) and blocked items

Owner list U1–U11 is in `ORCHESTRATOR-STATUS.md` ("Owner actions"); some older paths there are stale. If Nico later supplies `ANTHROPIC_API_KEY`, it belongs in `~/.cfb-demo/app.env`, never the repo `.env`. Other owner items include the Claude Desktop MCP entry by Nov 13, demo date/network and licensing/CFBD terms/team marks. Chat work does not wait for a real Anthropic key.

**Blocked on Nico:** only a local-app privilege decision, if local serving is required: inherited `PUBLIC` SELECT and EXECUTE on unrelated `PREPROC_RT` cause the app's strict startup check to refuse `CFB_APP_WEB`. The AWS live app passes. Do not alter that schema or weaken the check without Nico's direction.

## 7. Log (newest first; one line per milestone)

- 2026-10-30: **All phases merged into one tree.** Committed each worktree's uncommitted work on its branch, merged foundation → p4 → p5 → p6 → p8 → p7 into `cfb-demo-integrated` (p5's staged copy of Phase 4 matched p4 48f3713; p4's newer fit work and p6's newer what-if modules won; CLAUDE.md, Makefile, pyproject and tests/conftest.py combined; uv.lock regenerated). Integration fixes: `test_fit` mypy/line length, `CFB_API` view count 33, chat rehearsal RQ08/RQ12 against the real `UPSETS`/`RANKINGS` columns, `PreviewCard` restored to the live-spend spec (0-call replay not blocked by a refresh). Ruff, strict mypy (338 files), 8331 unit+CLI tests, frontend typecheck/lint/build and 694 frontend tests pass; release check OK. Worktrees p2–p8 removed after their probe files were copied to `cache/probe/`. AWS refused connections for ~4 min at 08:19 UTC (not touched); the app now runs from the main folder with the read cache (warm requests 4–10 ms).
- 2026-10-27: **Phase 8 light replan and Oct 31 items without a commit.** `tasks.md` L1–L7 replaces the heavy 46-task list: kept captures, freeze, thin `make weekly`, merged rehearse/abort, checklist preflight, docs; cut `CFB_BENCH`/sync/scale card/dial button, bench harness, transcript replay, kit facts tooling, full release check (deferred to the Labs gate). [44] pins working-tree content digests (no commits); [45] snapshot now. `speq plan validate` passes. Worktree p8 created from `main`; `scripts/release_check.py --fast` (freeze), `make demo-freeze`, lint hook, 4 CLI tests; p8 `make lint` clean and `make test` 6930 passed. Hard rules added to p8, p6 and main-tree `CLAUDE.md`. `make snapshot TARGET=aws NAME=aws-2026-w08`: SNAPSHOT run 19, 156,004 rows, 0 CFBD calls.

- 2026-10-27: **Phase 6 curated console live on AWS without a commit (task 4.3).** New `app/backend/routes/console.py` mounts `GET /api/console`, `POST /api/console/{id}/explain` and `/run`. EXPLAIN and the statement run as `CFB_CONSOLE_WEB`; the owner path only reads quota and writes the fetch log. `LiveExecutor` gained `run_statement(query=…)` (preview now delegates to it) with context `APP_CONSOLE#<action>`. AWS: fan-out plans 3 live slices, contradiction 0 requests, stored 157 stored slices, over-budget refused with the adapter's `BudgetError`; contradiction/stored runs 0 calls; over-budget and unconfirmed fan-out 409 before sending; one confirmed fan-out spent exactly 3 calls (84 rows, three parallel 200s), ledger 40→43 entries, plan 1392/1400, October room 146. A `REQ_AT`-as-text 500 found on AWS was fixed with a unit test. 602 app units, Ruff, strict mypy pass. Port-8000 restarted twice with the same env.

- 2026-10-27: **Handover prepared for Claude Code without a commit.** Verified the running AWS app and current cap/next target with read-only app requests, audited p4–p7 worktree status and task markers, replaced obsolete current-state checkpoints with the run-18 snapshot, and prioritized the console, Phase 8 deadline, refresh safety, source integration and scripted-only chat. Preserved all staged/untracked work and made no paid CFBD call for this handover.

- 2026-10-27: **Owner lifted the app-specific plan limit; AWS week-8 refresh finished without a commit.** Nico explicitly asked to remove the 12-call Phase 6 restriction because data was a week old. P6 `vs/live/ledger.yaml` now sets `add-cfb-app` and `app` to 1400 each, the sum of authorized October and November month caps; monthly caps remain 400 and 1000. Decision [46] records the override. The restarted app's week-8 plan estimated four calls and accepted the 16-call retry bound against 153 October calls of ledger room; zero-call DNS/TLS check passed. `POST /api/refresh/run` action `7b77863fbf3e4a4c70410f9f` became `LOAD_RUN` 18, DONE after eight steps, spending exactly four calls (one per due slice) and recording `REFRESH_WEEK#18` in the shared ledger. `/info` moved 246→250; the point moved week 7→8; publication/what-if run 4 is current; odds return 138 teams and 56 week-9 picks are frozen. Next target is week 9, not yet complete. No push, commit or AWS instance lifecycle operation.

- 2026-10-27: **Phase 6 paid-preview ledger failure now blocks subsequent paid actions in the process, without a commit.** If ledger append fails after a preview query, `LiveExecutor` marks reconciliation pending and returns a charged-action failure; the next preview or refresh refuses before sending a paid query. Synthetic failure test sent one query and refused the next; live guard tests 5/5, Ruff and strict mypy passed. The conditional guide was added to stored-query inspector classification. Durable recovery after a crash before recording a preview remains open. No CFBD call; usage 246/400 and Phase 6 ledger 1/12.

- 2026-10-27: **Phase 6 what-if run guard and local conditional-guide integration without a commit.** A two-run game cache avoids refetching 377 game rows for every toggle. Unknown-column errors and a changed run during a query become HTTP 409. Under the shared IT lock, local TestClient read run 1 and returned 55 conditional week-8 games with the set game excluded; zero CFBD calls. Seven route units, Ruff and strict mypy passed. Task 3.2 is complete. The local test override was limited to the startup check for unrelated PUBLIC privileges; AWS strict startup continues to pass.

- 2026-10-27: **Phase 6 conditional rooting guide live without a commit.** The new `POST /api/whatif/guide` counts playoff branches among stored seasons matching the selected outcomes and ranks each unset coming-week game. AWS run 2 with 1M seasons returned Auburn's 55-game conditional guide in 6.5 s, excluded the set game and used zero CFBD calls. An unknown game returned 400. Five route unit tests passed; Ruff and strict mypy passed. Latency is above Phase 5's target. Quota remains 246/400, Phase 6 ledger 1/12. Remaining for task 3.2: unknown-column run-change handling, game cache and local integration scenario.

- 2026-10-27: **Phase 6 guarded refresh completed in replay without a commit.** The runner now has an in-process single flight, separate 900 s owner connection, run-id watcher, exact operator allowlist, re-plan under the ledger lock, action polling, and charge-before-outcome handling. Local `REPLAY` run 39 directly and run 40 through FastAPI plan→run→action both finished at zero calls; the point stayed at week 7. The HTTP test overrode only the local startup privilege check, which still rejects unrelated `PREPROC_RT` PUBLIC grants. AWS plan for week 8 sees four due slices; all-slice retry bound 16 exceeds Phase 6's 11 remaining, so `POST run` refuses 409 at max_calls 0 and 4. Quota remains 246/400 and no live refresh was sent. Decision [45] records the stricter bound. Synthetic spend, live, operator, timeline and refresh tests 66 passed, strict mypy/Ruff passed. Next: process-death and lock/busy IT, CLI guard adoption, console.

- 2026-10-27: **Phase 6 zero-call refresh plan live without a commit.** `POST /api/refresh/plan` reads the shared loader estimate, stored calendar, week games, running runs, fresh free quota and ledger; it applies retry headroom and the refresh spendable limit. AWS plan for 2026 regular week 8 returned four estimated calls, 302 games (285 completed), no running run and HTTP 200. The run endpoint explicitly refuses 409 until guarded execution is implemented. Port 8000 restarted with `APP_LEDGER_PLAN=add-cfb-app`, showing 11/12 plan room; quota stayed 246/400. Operator, fixture and refresh-plan units 100 passed, strict mypy and Ruff passed. No CFBD call or commit.

- 2026-10-27: **Phase 6 refresh target integrated without a commit.** Config reads the next stored `CFB_MODEL.SEASON_WEEK` after the current point via an exact owner allowlist statement, including postseason and season rollover; the frontend uses it instead of guessing week+1. AWS port-8000 config proposed 2026 regular week 8. Operator/fixture tests 97 passed, RefreshPanel tests 20 passed, TypeScript and production build passed, Ruff/format/mypy passed. No CFBD call; quota remains 246/400.

- 2026-10-27: **Phase 6 stored-query inspector expanded without a commit.** The per-browser inspector now recognizes all zero-call CFB_API catalog reads, including odds/predictions/upsets/matchups, plus conditional what-if odds; it still excludes the free but virtual `QUOTA` read. AWS odds query produced an inspector entry whose explanation returned `no_virtual_table=true`, `live_calls=0`. Inspector/catalog checks 112 passed, Ruff and strict mypy passed. Two pre-existing ADR-field format warnings were corrected and `speq plan validate add-cfb-app` is clean. Port 8000 restarted healthy. No additional CFBD call; quota remains 246/400.

- 2026-10-27: **Phase 6 guarded preview executed on AWS without a commit.** Startup reconciliation completed against the main tree's shared ledger without adding entries. The plan measured one paid call; `max_calls=0` returned 409 with no ledger delta. The confirmed Auburn–Ole Miss POST returned HTTP 200, `REQ_SOURCE=LIVE`, `calls_spent=1`, `fetch_log_ok=true`; a repeat in the same browser session returned cached at zero calls. `/info` moved 245→246 and the ledger gained exactly one cost-1 `add-cfb-app` entry (1/12). Port 8000 was restarted and config/quota/plan returned HTTP 200; an unconfirmed POST there returned 409. Forty focused shell/guard/operator/preview tests, strict mypy and Ruff passed. No commit.

- 2026-10-27: **Phase 6 zero-call preview plan live without a commit.** `EXPLAIN VIRTUAL` on the direct `GAMES_PREVIEW` table measured one planned live call for an Auburn–Ole Miss next meeting, without executing the fetch. `GET /api/preview/plan` serves next/latest/none basis and a disabled reason while ledger reconciliation and guarded POST are unmounted; the frontend now respects the disabled state even for a zero-call plan. AWS TestClient and port-8000 HTTP returned 200, 126 focused backend tests, strict mypy over 17 affected source files, app plan validation, frontend TypeScript/ESLint, 14 head-to-head tests and production build passed. The app uses the main tree's shared ledger path; no CFBD calls.

- 2026-10-27: **Phase 6 upsets and head-to-head live without a commit.** AWS upsets returned 19 current-week, 28 all-time (ties) and 25 line-era rows. Michigan–Ohio State returned 121 meetings; Auburn–Ole Miss returned 48 and a frozen next-game prediction. Read-only app-role TestClient, port-8000 HTTP smoke, frontend TypeScript/ESLint, 14 focused frontend tests and production build passed. Zero CFBD calls. Preview planning/execution is still open, so its card currently cannot load a plan.

- 2026-10-27: **Phase 6 predictions and scorecard live without a commit.** AWS GET predictions at current week 7 returned 56 frozen upcoming picks and 62 graded games; weekly scorecard returned two rows for each FBS population. Season totals and calibration are suppressed because stored aggregates already include later graded week 8. The port-8000 server was restarted and both GETs returned HTTP 200. Eleven focused backend tests, Ruff and strict mypy passed. Zero CFBD calls.

- 2026-10-27: **Phase 6 odds and what-if reads live without a commit.** The AWS app role served 138 odds rows, 377 selectable games, a 138-team conditional odds result (810,090 matching seasons), and Auburn's 10 top / 56 coming-week rooting guide rows. Invalid game ids return 400 and stale runs 409. Three focused what-if unit tests, Ruff and strict mypy passed. The port-8000 server was restarted and GET odds/games/guide returned HTTP 200. Only stored CFB_API views were read: zero CFBD calls. Conditional rooting guide and remaining Group C routes are open.

- 2026-10-27: **Phase 6 task 9.8 live without a commit.** New team outlook returns run-2 odds and rooting guide for Auburn; the week route returns 56 games with run-dependent pick and stakes fields. Fresh FastAPI app-role client and the rebuilt loopback server returned HTTP 200 for outlook and week. Five focused backend tests, 33 frontend tests, TypeScript, ESLint and production build passed. No CFBD calls. Next: finish guarded preview/loader guard and Group C routes.

- 2026-10-27: **Phase 5 Slice B 2.1–2.3 complete without a commit.** Local DEPLOY run 38, AWS DEPLOY run 17: 33 stored CFB_API views plus live QUOTA, 659/659 comments, zero CFBD calls. Three focused synthetic IT passed; local what-if matched the engine for all 138 teams. AWS all-column readback passed; seven query medians at 393–506 ms with cache off; app role read k=1 and k=3. The all-time top ranks have three Elo-era entries and line-era skew, documented in decision [57]. Dictionary, verification, user guide, architecture, roadmap and CLAUDE.md updated; full lint, nine units, three IT and plan validation passed. Quota stayed 245/400. Phase 6 9.8 gate is met.

- 2026-10-26: **Phase 5 Slice A task 1.5 complete without a commit.** `make api-docs` generated 21 view entries; 20 AWS stored-query medians with query cache off were all below 1 s. The current-point week-7 incomplete FBS query returned zero rows because the point has not advanced. User guide, CLAUDE.md and verification note updated; 12 dictionary/model tests and Ruff/format/mypy passed. AWS quota stayed 245/400.

- 2026-10-26: **Phase 4 calibration report task 3.2 complete without a commit.** The AWS read-only run scored 1,514 holdout games and 566 graded 2026 reconstructed picks. All bucket and bias review thresholds passed; model Brier exceeded the stored line by 0.01058, below its 0.03 trigger. Seed unchanged and zero CFBD calls. Six focused tests passed and the evidence is in `docs/engine-verification.md`.

- 2026-10-26: **Phase 5 Slice A deployed locally.** DEPLOY run 34 compiled 20 CFB_API and one CFB_API_LIVE views with 464/464 comment readback; all local role grants were already present. Zero CFBD calls. Dictionary, timings and user guide remain for 1.5. No commit made at Nico's request.

- 2026-10-26: **Phase 4 source integrated into p5 without committing.** Seven overlap files resolved; combined deploy order is VS → loader → model → simulation → API under the DEPLOY lock. Focused deploy tests 38/38 plus an ordering regression passed; Ruff clean. Changes staged, merge state cleared. Nico requested no more commits until he makes one large final commit. No DB or CFBD calls.

- 2026-10-26: **Phase 4 refresh integration committed and registered on AWS** (`48f3713`). AWS DEPLOY run 16 installed three refresh steps, sim defaults 1M/10k and 100% comment readback with zero CFBD calls; quota stayed 245/400. Real local replay run 33 on 2025 week 7 executed CHECK/MODEL/PREDICT/SIMULATE with zero calls, the simulation correctly skipped without 2025 rules. The isolated 2026 TEST_ replay had run a full 2,000-season simulation and stale-run cleanup. Task 3.1 is complete with the missing real-local-2026 recording caveat.

- 2026-10-26: **Phase 4 real local deploy passed, replay data gap found.** `make deploy TARGET=local ARGS=--no-sync-api-features` registered MODEL/PREDICT/SIMULATE steps as DEPLOY run 31, with 0 CFBD calls. Local replay `make refresh SEASON=2026 WEEK=7` became run 32 and failed in FETCH because `/metrics/wp/pregame?seasonType=both&year=2026` has no replay recording; no API call and no new step execution. Two isolated TEST_ IT had already passed fetch→model→predict→simulate and abandoned-run cleanup with zero mock live requests. The SIM_RUN comment changed in the draft but the retained table kept its old comment (99% readback); the file was adjusted to preserve exact readback for the next deploy.

- 2026-10-26: **Phase 5 task 1.4 committed and live on AWS** (`5431d8a`). Local focused IT verified stored timeline/FETCHES, app-only QUOTA and MCP denial; 4 units, Ruff and mypy passed. AWS `make api` DEPLOY run 15 compiled 20 `CFB_API` views and 1 `CFB_API_LIVE` view, all comments checked; grants already in place. Used calls stayed 245 before and after. The port-8000 app server was restarted with normal AWS startup checks; `GET /api/timeline` returned HTTP 200 with refresh run 5, six steps and eight stored fetches. Zero CFBD calls.

- 2026-10-26: **Phase 6 read-only timeline route committed** (`d2e632d`). The fixed catalog reads `CFB_API.REFRESH_TIMELINE` and `FETCHES`; response includes the latest or selected run and a follow key from `CURRENT_POINT`. Fake-DB tests (106 focused), Ruff and strict mypy passed. The running app needs restart after Phase 5 deploy; zero DB and CFBD calls in this code milestone.

- 2026-10-26: **Phase 5 N1 preview-view assumption disproved.** In a local mock test, `CREATE FORCE VIEW` plus deferred compile/readback installed `GAME_PREVIEW`, but `SELECT ... WHERE P_GAME_ID = 2026016` as the app role failed with `required parameter not set: P_GAME_ID`; no mock CFBD hit. The outer view filter does not reach the virtual-table adapter. Phase 5 is removing the unsupported view and keeping `QUOTA`, `REFRESH_TIMELINE` and `FETCHES`; Phase 6's guarded operator path will query the virtual table directly. Zero CFBD calls.

- 2026-10-26: **Verified CFP source note deployed on AWS** as SIM DEPLOY run 14. Lint of 29 SQL files, 15 view compiles and all 463 comment checks passed; eleven run data tables were kept. Zero CFBD calls; no AWS lifecycle action.

- 2026-10-26: **Phase 4 official CFP rules check committed** (`000e3c3`). CFP's 2026-27 bracket confirms the G6 automatic bid is for the highest-ranked team, Notre Dame gets a bid in the final top 12, and Selection Day is December 6. The seed values stand; `RULE_NOTE` cites the official pages and labels the 12:00 UTC cutoff as a model boundary. No CFBD calls or AWS changes.

- 2026-10-26: **Phase 6 task 9.7 committed and live on AWS** (`ee1eba3`). Map, team, points/search, week and stats read `CFB_API` only; 138 FBS teams, 56 games at the current point, all AWS routes HTTP 200. The normal AWS privilege check passed; `make app-user TARGET=aws` provisioned app/console users without exposing passwords. The loopback server at port 8000 serves the built SPA and a per-browser stored-query inspector; real HTTP smoke confirmed the map SQL button and zero-call plan. Focused route/inspector tests, Ruff and strict mypy passed. No CFBD calls. Local DB was started and local users provisioned, but normal local app startup refuses inherited `PUBLIC` grants on unrelated `PREPROC_RT`; no outside schema changed.

- 2026-10-26: **Phase 6 task 2.2 committed** (`606b295`). FastAPI shell, one-worker serve/dev CLI, user provisioning, security middleware and cached logo route are in place. Eight focused CLI/shell tests passed; Ruff and strict mypy clean. Corrected dev child-process monitoring, validated static-directory serving, and the user lookup's bound literal. No DB or CFBD call. App still shows the snapshot; 9.7 live read routes in progress.

- 2026-10-26: **Runtime slowdown checkpoint.** Simple commands, Python startup, P5 focused IT, and p6 frontend build stalled on the OneDrive worktrees despite free memory; interrupted checks have no pass result. P5 task 1.4 is uncommitted/paused (20 stored and 2 live views statically linted; unit generator 2/2; IT pending). P4 task 3.1 is paused with only a task marker changed and drafts in `/tmp/p4-31/`. Local IT lock is free. Phase 6 backend/live map routes have priority while the other agents pause worktree activity.

- 2026-10-26: **Phase 4 task 2.3 committed** (`f9060df`). AWS view/movement/rooting/grade evidence and task marker recorded in `docs/engine-verification.md`; required columns for all 13 handoff objects present, no CFBD calls. Task 3.1 refresh integration started; no AWS refresh authorized for this coding step.

- 2026-10-26: **Phase 4 AWS movement runs verified.** SIM views deployed as DEPLOY run 10. Week-7 SIM_RUN 2 (1M, 30.686 s) and week-6 SIM_RUN 3 (1M, 34.106 s) DONE. POINT_RUN links weeks 6→7→8 as runs 3→2→1; CURRENT_RUN is week-7 run 2 until the week-8 point advances. Auburn odds moved 1.0705%→4.9287%→1.1740%; week-8 rooting guide leads with Ole Miss–Auburn at 5.8074 pp leverage. Reconstructed week-8 grades 35 correct/21 wrong; all 56 week-9 in-advance picks intact. Quota 245/400 before and after: zero CFBD calls. Task 2.3 evidence/commit pending.

- 2026-10-26: **Phase 5 task 1.3 committed and deployed on AWS** (`5834c4b`, DEPLOY run 12). Head-to-head series/games, next matchup and upsets extend CFB_API to 18 views; 2 focused local IT, lint and real-data checks passed. Michigan–Ohio State has 121 games; initial cold series query 1.077 s, warm median 0.018 s. AWS deployment compiled all 18 views and 419 comments, zero CFBD calls. Task 1.4 started.

- 2026-10-26: **Phase 6 main merge committed** (`a2cc67f`). Makefile, pyproject.toml and uv.lock conflicts resolved; staged diff check, TOML parse, offline uv lock check and Make target parsing passed. Backend 2.2 resumed. No DB or CFBD calls.

- 2026-10-26: **Phase 4 task 2.2 committed** (`ba97006`). Four simulation presentation views (point/current run, odds movement, rooting guide) compiled locally with two focused IT tests and lint passing. Phase 5 task 1.3 also compiled 18 API views locally; Michigan–Ohio State series, games and 2025 upset checks passed. AWS deployment and latency check remain.

- 2026-10-26: **Corrected AWS API deployment verified.** Target fix committed in p5 (6674dd5); 14 map/team/week/stats views deployed as AWS DEPLOY run 9. APP/MCP CFB_API SELECT and APP CFB_API_LIVE SELECT verified in AWS catalog. CFB_API returns 138 FBS teams. Phase 6 backend 2.2 started; app remains snapshot preview until routes are live.

- 2026-10-26: **API target-selection bug found.** `make api TARGET=aws` deployed to local because `scripts/api.py` used a literal `local` argparse default; AWS CFB_API schemas are absent and APP/MCP grants are pending. Phase 5 task 1.2 committed (e561745), focused IT/lint and local deploy passed. Fix and regression test underway.

- 2026-10-26: **Fresh week-8 AWS odds stored.** 1M-season SIM_RUN 1 `DONE` in 31.63 s; 143 teams, 321 games. Auburn P(CFP) 1.17% now vs 5.81% if it beats Ole Miss (week 9, 0.612 s query). Post-run quota unchanged 245/400: zero CFBD calls. Phase 5 1.2 local deploy and focused IT passed; commit pending.

- 2026-10-26: **Week-9 predictions frozen in advance on AWS.** Phase 4 2.1 committed (e3a401e) after clock-race fix and 3 focused TEST_ IT. AWS sim deploy run 6 and `make predict` froze 622 rows; 56 week-9 FBS games are all `IN_ADVANCE` from week-8 point at 09:32 UTC, before first kickoff Oct 27 23:00 UTC. Quota unchanged 245/400, zero calls.

- 2026-10-26: **Phase 5 task 1.1 committed** (e065b98). Eight CFB_API views for map/team data, `make api`, APP/MCP roles; local real-data deploy/grants, 2 focused TEST_ IT and lint passed, zero CFBD calls. Task 1.2 week/stats views started; app still uses the snapshot preview.

- 2026-10-26: **Week-8 AWS refresh completed.** `REFRESH_WEEK` run 5 fetched GAMES, WP pregame, SP ratings and polls; step 100 rebuilt the model. Four calls recorded; `/info` and ledger now 245/400, 75 ledger calls left. Prediction freeze waits for the reviewed clock-race fix and retest.

- 2026-10-26: **Phase 4 prediction TEST_ integration passed.** Two tests in 59.25 s after local redeploy; independent review then found a fresh-clock-per-insert race to fix before AWS.

- 2026-10-26: **AWS stored data restored.** `make deploy TARGET=aws` recreated the project schemas and loader/model; private snapshot `aws-demo-2026-10-23` restored 155,521 rows as RESTORE run 2 and rebuilt 8 model tables. Orphans 0, core games 112,674, zero CFBD calls. `CFBD_API` is absent after reset; Nico is recreating it.

- 2026-10-26: **AWS reset discovered.** AWS is `database_ready` but has no CFB schemas or `CFBD_API` connection. The 2026-10-23 private snapshot is intact (155,521 rows); prepare schema deploy and documented restore, then Nico must recreate the key connection before week-8 refresh. No live call or AWS lifecycle action taken.

- 2026-10-26: **Phase 4 local prediction smoke.** `make sim` deployed the prediction table/views/Lua as DEPLOY run 20; `make predict` froze 566 rows, zero CFBD calls. A test-clock seam fix is pending before focused IT.

- 2026-10-26: **Resumed after shutdown.** Local deployment restarted under the shared IT lock and is ready. AWS is unreachable while the launcher reports a separate `start` operation in progress; no AWS lifecycle action taken. Phase 4 2.1 focused checks and Phase 5 1.1 reconstruction resumed.

- 2026-10-23: **Paused at Nico's shutdown request.** No agent or DB statement running. Phase 4 2.1 has uncommitted p4 code; focused tests and local DB verification next. Phase 5 1.1 has one new test in p5 and unapplied draft files in `/tmp/p5-stage/`.

- 2026-10-23: **Phase 4 task 1.5 committed** (61708de). `docs/engine-verification.md` records AWS 100k/1M timings, Auburn win/loss, 20-run what-if p95, AP comparison and sizing; decision [47] keeps layout A. Quota unchanged at 241/400.

- 2026-10-23: **AWS 1M simulation and Auburn what-if passed.** Run 2 `DONE` in 32.672 s, engine 25.182 s, 12 instances on 3 nodes; Auburn P(CFP) 4.94% now vs 11.20% if it beats LSU (366,207 matching seasons, 0.155 s query). Post-run quota unchanged at 241/400: zero CFBD calls.

- 2026-10-23: **AWS 100k simulation and Auburn what-if passed.** Run 1 `DONE` in 14.496 s, engine 5.636 s, 12 instances on 3 nodes, 143 teams and 377 games; P(CFP) 4.94% now vs 11.33% if Auburn beats LSU (0.318 s query). Zero CFBD calls.

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
