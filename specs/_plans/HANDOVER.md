# Orchestrator handover (for Codex or any agent taking over)

**Read this whole file before doing anything.** It is the single source of truth for the autonomous run and is kept current by whoever orchestrates. `ORCHESTRATOR-STATUS.md` next to it is the older, partly stale history log; when the two disagree, this file wins.

**Living document.** Whoever is orchestrating (Claude Code until it runs out, then Codex) updates the **Current state** and **Log** sections after every milestone: a commit, a merge, a live run, a decision, or a new blocker. Bump the "Last updated" line. Commit it on the main tree branch with a message `handover: <what changed>`.

Last updated: 2026-10-23 ~10:15 UTC by Claude Code (Opus 5.5)

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
- **Logos:** team logos are cached locally in `~/.cfb-demo/logos/`, outside the repo and OneDrive. Never commit them and never hotlink ESPN at runtime.
- **Commit trailer:** end commit messages with the co-author line your tool uses. Claude used `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`; Codex should use its own or none.

## 3. Layout

| Path | Branch | Purpose |
|---|---|---|
| `~/Github/exasol-cfb-demo` (main tree) | `add-cfbd-vs-foundation` | Orchestrator branch: holds the plans for Phases 4, 5, 7(orig), 8 and this file. 10 commits ahead of `main` (plan commits only) |
| `../exasol-cfb-demo-p2` | `add-cfb-persistence-refresh` | Phase 2. **Done and recorded** (df2a647 = `main`) |
| `../exasol-cfb-demo-p3` | `add-cfb-analytical-model` | Phase 3, in progress |
| `../exasol-cfb-demo-p6` | `add-cfb-app` | Phase 6 web app, in progress (UI revision) |
| `../exasol-cfb-demo-p7` | `add-cfb-chat` (from p6) | Phase 7 chat, groups A–B done, rest blocked |

`~/Github` = `/mnt/c/Users/nc/OneDrive - EXASOL AG/Github Projects`. The repo is on OneDrive (slow I/O). Python via `uv run` (in p7 a native venv at `~/.cache/p7venv` via `UV_PROJECT_ENVIRONMENT` is much faster). Frontend `node_modules` live at `~/.cache/cfb-app-frontend` (symlinked, outside OneDrive).

**New phase:** create a worktree from `main` (`git worktree add ../exasol-cfb-demo-pN -b <plan-name> main`), then copy the latest plan folder from the main tree (`git checkout add-cfbd-vs-foundation -- specs/_plans/<plan>`). Lesson learned: always sync the latest plan into a new worktree.

**Databases**
- **local:** Exasol Personal nano 2026.2 at 127.0.0.1:8563. Has the full real data restored from the AWS snapshot (2026-10-23) in `CFB_CORE`, `FETCH_MODE='replay'` (no live calls). Integration tests use `TEST_*` namespaces plus a mock CFBD sidecar container started by the test harness.
- **aws:** deployment `default`, `TARGET=aws` in make commands, TLS fingerprint pinning. Holds `CFBD_VS` (live), `CFB_CORE` with real data, the loader scripts, and soon `CFB_MODEL`.
- Snapshots: `~/.cfb-demo/snapshots/aws-demo-2026-10-23`.

**speq workflow:** plans live in `specs/_plans/<plan>/` (`plan.md`, `tasks.md`, spec deltas, `decision-log.md`). The `speq` CLI is at `~/.local/bin/speq` (`speq plan validate <plan>`, `speq feature validate`). Codex has the speq skills in `~/.codex/skills/` (speq-plan, speq-implement, speq-record, …). Use them. Task markers in `tasks.md`: `[ ]` open, `[~]` in progress, `[x]` done, `[-]` superseded. Record = merge the deltas into `specs/`, append ADRs to `specs/decision-log.md`, move the plan to `specs/_recorded/<date>-<plan>/` (see `specs/_recorded/` for the two existing examples).

## 4. Current state (update this section after every milestone)

| Phase | Plan | Where | State |
|---|---|---|---|
| 1 VS foundation | add-cfbd-vs-foundation | recorded | **Done.** Live on AWS |
| 2 Persistence & refresh | add-cfb-persistence-refresh | recorded, `main`=df2a647 | **Done.** 221 live calls. AWS `CFB_CORE`: 155k rows, GAMES 1869–2026, SP+/WP 2013–2026, polls 2014–2026, TEAMS 1933, VENUES 852; 2026 refreshed through regular week 7. Oct 31 restore gate **passed** early |
| 3 Analytical model | add-cfb-analytical-model | p3 | Groups A–F done (through 3.8 `TEAM_SEASON`, de085a7). **Running:** merge of Phase 2 into p3 (conflicts in `scripts/deploy.py`, `tests/unit/test_deploy_cli.py`), then deploy of `CFB_MODEL` to AWS and local plus real-data sanity checks (and add COLOR/ALTERNATE_COLOR/logo/coords to `TEAM` if small). **Open:** 3.9 as-of views [expert], 3.10 coverage, 3.11 MODEL_CHECKS, 4.1 refresh_model.lua, 4.2 examples, 4.3 perf, docs 6.x, live 7.x, record |
| 4 Score engine | add-cfb-score-engine | main tree (plan only) | Plan reviewed. Starts after Phase 3 3.9 (as-of). Python UDF simulation; inputs from spikes S3/S4 (see ORCHESTRATOR-STATUS "Inputs for Phase 4"). Owner idea to consider: store a calibration file in BucketFS as an Exasol showcase beat |
| 5 Analyses / CFB_API | add-cfb-analyses | main tree (plan only) | Plan reviewed. **Thin slice first:** the views the UI needs (TEAMS with colors/coords, GAMES, TEAM_SEASONS, RANKINGS, SP_RATINGS, TEAM_TRAVEL, CURRENT_POINT, AS_OF_POINTS); see Phase 6 hand-offs P6-UI-b/c/d |
| 6 Web app | add-cfb-app | p6 | Backend core, old frontend pages, fixture preview (`make app-preview`, port 5173) done. **UI revision planned** (674ff29: map home, `/team/:id`, `/league/*`, tasks 9.0–9.8). 9.5 logos done (15779cc): all 138 FBS logos (light+dark) cached in `~/.cfb-demo/logos` from CFBD's CDN (decision [43]); all FBS teams have coords and colors in `CFB_CORE.TEAMS`. 9.0 foundation done (3342b9c: tokens, `src/ui` components, routing, contract). **Running:** 9.1 shell + 9.2 map (one agent), 9.3 team page + 9.4 league (another agent). **Next:** 9.6 real-data preview snapshot (`make app-fixtures`, `make app-preview REAL=1`) once `CFB_MODEL` is on AWS/local; then 9.7 routes (needs Phase 5 thin views), 9.8 outlook (needs Phases 4–5) |
| 7 Chat | add-cfb-chat | p7 | Groups A–B done (89f21ae). Group C+ blocked until Phases 5 and 6 merge (gate task 3.0) |
| 8 Demo hardening | add-cfb-demo-hardening | main tree (plan only) | Plan reviewed. Due items: freeze rule + CLAUDE.md hard rules by **Oct 31**; `make weekly` by **Nov 8** |

**Agents running at last update (Claude subagents; they die with Claude's session):** p3 merge+AWS model deploy; p6 tasks 9.1+9.2 and 9.3+9.4 (two agents, same worktree, disjoint dirs). If you take over, assume they may have stopped mid-task. Check `git status` and `git log` in each worktree and finish or redo the task from what is on disk.

### Recommended next steps (in order)
1. **p3:** finish the merge if it is still unresolved (keep both sides; Phase 2 added the `DEPLOY` lock and `deploy_namespace`, Phase 3 added the model deploy). Deploy `CFB_MODEL` on AWS and local; sanity-check on real data; commit.
2. **p6:** finish 9.0 and 9.5, then 9.1–9.4 and 9.6 (`make app-fixtures TARGET=aws`, `make logos`, `make app-preview REAL=1`). This gives Nico a clickable map and team page on real data. **Tell Nico when it is clickable** (URL http://127.0.0.1:5173/).
3. **Phase 5 thin slice:** new worktree p5 from `main` (after merging Phase 3 if done, else branch from p3). Build only the `CFB_API` views the UI needs, then 9.7 routes in p6 so the app serves real data instead of a snapshot.
4. **Phase 3 rest (light):** 3.9 as-of, 3.11 checks, refresh_model; skip heavy perf work; record and merge to main.
5. **Phase 4 engine (light):** worktree p4; minimal engine for odds and predictions; then 9.8 in p6.
6. **Phase 5 rest, Phase 6 rest, Phase 7 group C+, Phase 8.**
7. Merge each phase to `main` once verified (fast-forward or merge; `main` is not checked out anywhere, so `git branch -f main <commit>` works for a fast-forward).

## 5. Key dates (orchestrator owns these)

- **Oct 31:** restore gate (already passed 2026-10-23). Phase 8 freeze rule and CLAUDE.md hard rules due.
- **Sundays Nov 1, 8, 15:** weekly cadence: AWS snapshot (catch-up until Tuesday), `REFRESH_WEEK` for the completed week, run predictions locally before Tuesday's kickoff, copy recordings to `~/.cfb-demo/recordings/`. From Nov 1 the November cap (1000) applies; a redeploy on AWS picks it up (decision [46]). Don't start a live run on 2026-10-25 between 00:00 and 01:00 UTC (DST hour).
- **Nov 3 / Nov 10:** predictions published before kickoff.
- **Nov 9:** go/no-go on demo tier (Full / B no chat / C no app / D VS+model).
- **Nov 16–20:** demo. Default mode: live proof, replay data.

## 6. Owner actions (Nico) and blocked items

Owner list U1–U11 is in `ORCHESTRATOR-STATUS.md` ("Owner actions"). Most relevant: U3 `ANTHROPIC_API_KEY` in `.env` when he has it; U4 Claude Desktop MCP entry by Nov 13; U6 confirm demo date and venue network; U8 license/CFBD terms/team marks.

**Blocked on Nico:** none right now.

## 7. Log (newest first; one line per milestone)

- 2026-10-23: 9.0 UI foundation committed (3342b9c); 9.1–9.4 started.

- 2026-10-23: 9.5 logos committed; 276 logo files fetched from cdn.collegefootballdata.com (not ESPN; [43]). Route `make_router` not yet mounted (no app.py yet).

- 2026-10-23: HANDOVER.md created for a possible Codex takeover (Claude at ~80 % usage).
- 2026-10-23: Phase 6 UI revision planned (674ff29); 9.0 and 9.5 started.
- 2026-10-23: Phase 2 recorded (df2a647), `main` fast-forwarded. ADR-013..030.
- 2026-10-23: Phase 2 live group J on AWS: 221 calls, restore gate passed.
- 2026-10-23: Owner switched to the light process; asked for map-first UI.
- 2026-10-23: Phase 3 3.8 TEAM_SEASON (de085a7); Phase 7 2.1 turn loop (89f21ae); fixture preview (64f983a).
