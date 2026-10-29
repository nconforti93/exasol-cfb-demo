# Review: add-cfb-demo-hardening (Phase 8)

Reviewer: independent agent, 2026-10-23. I only read files: I opened no DB session and made no CFBD or Anthropic calls.

**What I read**
- This plan's `plan.md`, `decision-log.md` and all seven `demo/*/spec.md`.
- Checked against:
  - `specs/mission.md`;
  - `docs/roadmap.md` (Phase 8, the demo flow beats 1–6, the decision log and the budget);
  - `specs/_plans/ORCHESTRATOR-STATUS.md`;
  - the `review.md` of Phases 2–7, plus targeted reads of Phase 2 `loader/refresh-week`, Phase 6 `app/access` and its `plan.md`, and Phase 7 `plan.md`.

**Skills loaded first:** `exasol:exasol-database`, `exasol:exasol-bucketfs`, `exasol-mcp` and `exasol-runtime`.

`speq plan validate add-cfb-demo-hardening` → **passed** (7 delta specs). The specs hold 46 scenarios, and all 46 are mapped in Scenario Coverage.

---

## Verdict: approve with changes

The core design is right:
- a private kit (a "Sunday after week N" snapshot plus recordings of the week-N+1 refresh) under its own `REPLAY_PATH`;
- live first, with replay as a mode switch that needs no restore;
- a second app instance against AWS for the dial;
- a stdlib release check that knows the real secret values.

It reuses tested parts, adds no new data format, and keeps AWS, publishing and secrets inside the owner's rules. **No blockers.**

There are **eight major findings**. They fall into three groups:

1. **The calendar is the real risk, and the plan doesn't manage it.**
   - Phases 3–7 aren't implemented yet. The group chain makes demo-critical tasks wait for Phase 7.
   - There is no degraded demo if Phase 4, 5 or 6 slips.
   - The schedule doesn't separate the captures that can never be redone (Sunday snapshots, week recordings, predictions before kickoff) from work that can happen any later day.
   - Nobody concrete runs the Sunday cadence. (M1, M2, M3)
2. **Live on stage and the kit's numbers contradict each other.**
   - A live refresh in the demo week pulls newer data than the kit. The script's expected numbers, `kit-facts` and the chat transcript then quote numbers that are wrong on screen.
   - The live→replay switch has no rule for a hung refresh, nor for switching while a refresh still runs. (M4, M5)
3. **Correctness gaps in the kit and the release check.**
   - The week check doesn't stop a recording in which week N+2 is already complete. That moves the point past N+1.
   - The VS catalog and the `CFB_CORE` DDL must be frozen while kits exist.
   - The known-secret scan misses several real homes, among them the AWS SSH key. (M6, M7, M8)

| # | Severity | Theme | Finding |
|---|---|---|---|
| M1 | major | Calendar | Only the captures that can't be redone need to be on time; the plan doesn't separate them, and its groups block demo-critical work on Phase 7 |
| M2 | major | Calendar | No degraded demo if Phase 4, 5 or 6 isn't ready |
| M3 | major | Calendar | The Sunday cadence has no named runner, no time window and no recovery for a missed Sunday; Q17 and U2 must be settled before Nov 1 |
| M4 | major | Reliability | A live refresh in the demo week shows different numbers from the kit; the script's facts and the transcript replay then mislead |
| M5 | major | Reliability | The live→replay switch has no abort for a hung refresh and no "no refresh running" guard |
| M6 | major | Correctness | The kit's week checks don't refuse a recording where week N+2 is complete |
| M7 | major | Correctness | Kits break if the VS catalog or the `CFB_CORE` DDL changes after a snapshot; nothing freezes them |
| M8 | major | Secrets | The known-secret homes are incomplete (`node_access.pem`, `connection-instructions.txt`, the `test` deployment, `~/.exapump`, starter-kit credentials, the Claude Desktop config); the CFBD key is covered only by the `Bearer` pattern |
| m1–m12 | minor | various | Below |

---

## 1. Calendar and dependencies

### M1 (major): put the captures that can't be redone first, and unblock the demo path from Phase 7

**Evidence**
- Today is 2026-10-23.
- **Where the predecessors stand** (`ORCHESTRATOR-STATUS.md`):
  - Phase 3 is in group B;
  - Phase 4 implementation "follows Phase 3";
  - Phase 5 is a plan;
  - Phases 6 and 7 are plans in revision.
- **What Phase 8 needs from them:**
  - Groups B–F need "Phases 2–6 merged with green integration tests" (plan, Parallelization).
  - Task 10.2 needs 3.1–3.4 merged by **Nov 8**.
  - Task 3.3 (`make rehearse`) runs `make analyses` (Phase 5) and `SIMULATE`/`PUBLISH_RUN` (Phases 4–5).
- **Groups G–I wait for group F.** The chain is strictly "A → B → … → I". Group F holds 5.2/5.3, which "need Phase 7 merged". So 7.1 (G), 8.1 (H) and 9.1 (I) all wait for Phase 7, although the cut line lists 8.1 and 9.1 as **Must** and 5.2/5.3 only as **Should**.
- **Three steps have time windows that can never be reopened** (plan, Forces, "Time-bound data"):
  1. **The AWS snapshot of "Sunday after week n".** Window: Sunday until the first MACtion kickoff of week n+1 (Tuesday). It needs only Phase 2 deployed on AWS, because the snapshot scope is `CFB_CORE` + `LOAD_LOG` + `FETCH_LOG`.
  2. **The 4 refresh recordings of week n.** Window: Sunday after week n until week n+1 completes (see M6). They need only the foundation's `make record`.
  3. **The local predictions of week n+1.** Window: before week n+1's first kickoff (Tuesday). They need Phases 3–5 on local.
- Everything else (kit build, rehearse, preflight, app, benchmark, docs) can run any later day on the captured inputs.

**Fix**
1. Add a "Captures" row group at the top of the Live table and task 10.x. Each Sunday it does: AWS refresh + snapshot (Phase 2 commands), plus `make record` of the 4 slices for the kit that ends that week. Its only dependencies are Phase 2 on AWS, the foundation recorder, U1, and the console keys pinned in `demo.yaml` (Q11).
   - Building the kit can then happen days later, as soon as 3.1 exists.
   - State that recordings already in `cache/replay/` are enough. `make kit SOURCE=` can take them later, but `--rerecord` overwrites a slice in `cache/replay/`, so copy each Sunday's set into `~/.cfb-demo/recordings/2026-wNN/` right away. Otherwise Nov 15's GAMES recording overwrites Nov 8's (decision [1] names this problem but solves it only at kit build).
2. Split `make weekly`'s `predict` step deadline from Sunday: "before week n+1's first kickoff (Tuesday)". Say that the honest headline for kit `2026-w10-w11` needs Phase 5's `make predict` on local by **Tue Nov 10**. Otherwise the headline is the labeled reconstruction (the preflight WARN already covers that).
3. Change the group chain so that group F is **optional** for G–I: "G needs A–E; F may run after I." Also allow 8.1 (draft) and 9.1 before 7.1 when the cut line is applied.

### M2 (major): there is no degraded demo if a predecessor isn't ready

**Evidence**
- Decision [26] and the cut line cover only Phase 8's own tasks ("the cut never touches security …").
- The beat fallbacks assume a working app (`make app-serve`), `CFB_API` and the engine. If Phase 4, 5 or 6 isn't merged by about Nov 13, beats 1, 3 (dial), 4 and 5 have no defined path.

**Fix:** add a short "Demo tiers" section to decision [26] and `docs/demo-script.md`.

| Tier | Needs | Beats |
|---|---|---|
| Full | Phases 2–7 | 1–6 as scripted |
| B, no chat | Phases 2–6 | 1–4 and 6; beat 5 replaced by the MCP coda in Claude Desktop on `CFB_API` (no API key needed, local) or dropped |
| C, no app | Phases 2–5 | SQL client (exapump or DBeaver) in replay. Beat 2 runs the three console statements as `sql demo`; beat 3 runs `EXECUTE SCRIPT CFB_INFRA.REFRESH_WEEK` and shows `CFB_API` timeline views; beat 4 queries `CFB_API.PLAYOFF_ODDS`/`SCORECARD_*`; dial via `make simulate TARGET=aws` |
| D, VS only | Phases 2–3 | Beats 2 and 3 (refresh + model views), no odds; positioned as "the API is a schema" |

The preflight takes `TIER=` and skips the checks of missing parts. A one-line go/no-go per tier at **Nov 9** (the date of dress rehearsal #1) decides which tier is rehearsed.

### M3 (major): who runs the cadence, and the two decisions due before Nov 1

**Evidence**
- U5 says the cadence is run "by the owner or the orchestrator".
- **The orchestrator can't just "run" on a Sunday.** It is a local Claude Code session on this laptop: AWS secrets under `~/.exasol/personal/deployments/default/`, local Exasol in WSL. A cloud scheduled agent can't reach either. Someone must start a session in the window, or the laptop must run a local timer, which needs an owner's `CONFIRM=yes` decision for live calls.
- **Q17** (AWS 2026.1.1 snapshot → laptop 2026.2 restore) is checked only by Phase 2 task 9.5 "before 10.1". If it fails, the fallback needs **U2** (a local `CFBD_API` connection) by **Nov 1**, but U2 is scheduled "before 10.3".
- **U6** (the demo date) is asked "a week ahead". The date decides whether the Nov 22 kit (≤ 10 calls) is needed and which kit is the stage kit.

**Fix**
- Name the runner per Sunday: the orchestrator session, started by Nico on Sunday afternoon, or Nico himself. Give each step a window (snapshot Sun–Mon, recording Sun–Fri, predict Sun until the Tuesday kickoff).
- Add a "missed Sunday" rule:
  - a snapshot taken Monday still passes the content check;
  - a recording taken later passes only until week n+1 completes (M6);
  - a missed predict means a labeled reconstruction.
- Make Q17 a **gate on Oct 31** (Phase 2 9.5). If it fails, U2 is due Nov 1, and the cadence snapshots the laptop instead.
- Ask U6 (the date, ± 2 days) **now**.

---

## 2. Demo reliability

### M4 (major): a live refresh in the demo week doesn't produce the kit's numbers

**Evidence**
- **Live data is newer than the kit.** Kit `2026-w10-w11` is the Nov 8 snapshot plus Nov 15 recordings. A live `REFRESH_WEEK(2026, 11)` during the week of Nov 16 fetches the **current** 2026 season slices:
  - week-12 weekday (MACtion) results from Tue and Wed;
  - the CFP rankings released Tuesday;
  - possibly updated SP+.

  `CURRENT_AS_OF` still moves to 11, because week 12 is only partly played. But odds, the what-if, the scorecard rows and the upsets differ from the recorded data.
- **The script assumes kit numbers.** Its "Expected (from kit facts)" column (beats 1, 3, 4, 5) and `make kit-facts` are valid only for a replayed refresh.
- **Decision [15]'s transcript binding is too weak.** It binds a transcript to "the kit's SHA-256 and point". After a **live** refresh the point is still the kit's replay week, so the transcript would replay. Its recorded answer text then quotes kit numbers, while the tools it re-runs return different live numbers. That is a visible contradiction on stage, presented as Claude's answer.
- `chat-beat` spec: "Binding: a transcript replays only on its kit at its point."

**Fix**
- **Transcript replay** (`chat-beat`, task 5.3) needs both of these, otherwise it uses the stub and gives the reason:
  - the newest `REFRESH_WEEK` run's fetches are all `REQ_SOURCE` `REPLAY`;
  - the VS `REPLAY_PATH` is the kit's.

  Add this as a THEN to the scenario "A transcript is built only … replays only on its kit".
- **The demo script** carries two columns: "replay (kit facts)" and "live (read on screen)". Live mode never quotes a fixed number.
- **Make the trade-off explicit in decision [4].** Live buys one real API call in beat 3 at the cost of the scripted numbers. A cleaner middle is **"live proof, replay data"**: run the refresh in replay and show the single live call through the head-to-head preview click or the console `fan-out` (≤ 3 calls, under `app`). The owner should choose. My recommendation is replay for the refresh and live for one click, because the mission's promise is "refresh on stage, fall back without network", and a recorded refresh of real week-N+1 data honestly says "replayed" in the badge.

### M5 (major): the live→replay switch needs a "no refresh running" guard and an abort for a hung refresh

**Evidence**
- **How a refresh fails** (Phase 2 `loader/refresh-week`):
  - "Each table commits on its own … A failed step stops the chain";
  - a failure needs `MAX_ATTEMPTS` (4) with backoff per slice;
  - on a degraded conference network, the likely failure is a **hang or a slow timeout**, not a clean 500.
- **While it runs:**
  - it holds the run lock, so the replay rerun is refused with 409 `refresh_running` until it ends;
  - the DNS gate in the preflight doesn't detect a slow link.
- **`make demo-mode` doesn't check whether a refresh is running.** The foundation says a mode switch "takes effect on the next query" (`cfbd-fetch/fetch-modes`). A switch in the middle of a refresh can therefore give a run whose later tables come from replay and whose earlier tables come from live: a mixed run.
- **The skills don't cover two Exasol behaviours.** They don't say how `ALTER VIRTUAL SCHEMA … SET` (grammar `alter_virtual_schema_statement_2` confirmed in `exasol-grammar.md:215-219`) behaves against another session's open transaction that read the virtual schema: whether it waits or causes a rollback. Nor do they say that `FETCH_MODE` is visible to every session of the deployment. Treat both as unverified.
- **A failure on a later table leaves the point moved.** The rehearsal spec tests only a 500 on `GAMES`, the first fetch. A failure on the third table leaves live `GAMES` committed, so `CURRENT_AS_OF` already sits at N+1 with no publication.

**Fix**
- `make demo-mode` refuses while a `REFRESH_WEEK`, `SIMULATE` or `RESTORE` run is `RUNNING` (Phase 2's lock), naming it.
- Add `make demo-abort` (operator): `KILL STATEMENT` on the holder's session. The owner's notes say `KILL STATEMENT` keeps the transaction, and the next lock holder marks the run `ABANDONED` (Phase 4 M5).
- Add a total refresh deadline for live on stage (for example `APP_REFRESH_DEADLINE_S=60`, after which the app shows "switch to replay").
- Add a scenario: the mock fails `RATINGS_SP` (the third fetch) after `GAMES` succeeded live with **different** content from the recording. The replay rerun must then give pages equal to pure replay.
- Probe the `ALTER` on a second session's open read transaction once in 3.2 (0 calls, local), and record the result.
- **The global effect is acceptable:** on the laptop only App A's console, preview and refresh read `CFBD_VS`. The chat and the MCP coda read `CFB_API`, which holds no network-reaching views (Phase 5 [33]), and App B's AWS VS is a different deployment. Say so in decision [4].

### The fallback ladder, beat by beat (minor items)

- **m1. Beat 3, the card after the dial.** Neither app shows both just-run numbers. App A's card shows the AWS column from the last pre-show sync. App B's card shows the laptop's last imported run, not the refresh the audience just watched. **Fix:** the script shows the card in App B and says "laptop: measured <time>". Alternatively, run `make bench-sync` (seconds, 0 calls) in the already-open terminal after the dial. Pick one and script it.
- **m2. Beat 3, the presenter's terminal.** Recovery is a terminal command (`make demo-mode`). State in the pre-show list that a terminal tab is open in the repo, with the exact line pasted and ready.
- **m3. Beats 2/4 and the "app does not load" fallback.** The fallback ("restart `make app-serve` (5 s)") ignores the frontend build. State that `make app-build` ran at T-2 h, and that the preflight checks for `dist/`.
- **m4. A misclick on App B's refresh button** spends AWS calls under `app` and moves AWS mid-demo. For App B (AWS), hide or disable the refresh button with an env flag (`APP_REFRESH=off`), or the script says "never press Refresh in App B".
- **m5. The ≤ 6 min reset** is realistic, but it isn't measured until 10.2. Restore ≤ 5 min is Phase 2's target. Step 6 (SIMULATE 100k + publish) runs only on the first rehearsal, because the week-N publication survives restores as deployment history. The reset isn't on stage anyway (decision [4]). Keep Q10, and make the measured number a 10.2 output.

**Unscripted parts of the 25 minutes:** the transitions between App A and App B, which browser tab shows the card (m1), and the narration for the scorecard label. The roadmap's beat 2 "`SELECT` from an endpoint" is covered by `fan-out`. Beyond m1–m4 there are no gaps.

---

## 3. Scale dial

**Two instances on loopback: sound.**
- Phase 6's `Host` allowlist and `Origin` set are built from `APP_PORT` (`add-cfb-app/plan.md:260-261`), so App B on 8001 passes its own checks.
- Cookies ignore ports, so decision [19]'s `cfb_session_<port>` is needed and sufficient. `localStorage` is already per-origin.

**`bench-sync` both ways: sound.** It runs as `sys` on both sides, merges by key and is all-or-nothing. Writes to AWS `CFB_BENCH` are "run", not "change the instance".

**The targets are plausible but unmeasured.**
- Phase 4's revised budget is ≈ 38 s AWS plus a layout-C gate of ≤ 10 s at 1M (Phase 4 review M4), so 1M in ≤ 60 s holds on paper. Linear scaling makes 2M in ≤ 120 s plausible, and the "drop if > 120 s" rule is honest.
- S3's full-mode 12.4 s per 100k on AWS (≈ 124 s per 1M) only holds with the sparse `SIM_TEAM`, which is Phase 4's design.
- The 1M number is first measured in Phase 4's group J. Q9's fallback is right.

**m6. The dial's confirmation text hard-codes "≈ 45 s".** Take it from `/api/scale/plan`'s estimate, which the plan already returns; with no estimate, say "about a minute".

---

## 4. Release check

**What is right**
- It holds known values in memory and masks them (rule name and length, never 8 consecutive characters).
- It never reads the CFBD key.
- It scans every blob of `rev-list --all` (the worktree branches share the repository, so p2, p3 and p6 are included).
- It applies raw-data rules to the tree and the history, with `tests/synthetic/` allowlisted.
- The `--fast` subset in lint reads no secret.
- It never publishes: decision [14], Non-Goals, and U9. **I confirm that no task pushes, publishes, rewrites history or deletes branches.** The report goes to the git-ignored `cache/release-check/`.

### M8 (major): known-secret homes are incomplete

**Evidence**
- `~/.exasol/personal/deployments/` holds `default`, `local` **and `test`**. I listed names only.
- `default/` holds `secrets.json`, `deployment.json`, **`connection-instructions.txt`** and **`node_access.pem`** (the AWS node SSH key).
- The spec reads only `local` and `default` `secrets.json` and `default/deployment.json`.
- Not covered at all:
  - **`~/.exapump/config.toml`:** clear-text profile passwords, per the `exasol-database` and `exasol-bucketfs` skills;
  - **`~/.exasol-starter-kit/credentials/`:** the kit's MCP user (`exasol-mcp` skill);
  - **the Claude Desktop config:** holds `CFB_CHAT_WEB`'s password after U4 (decision [16]).
- The `secret-file` rule catches only a *tracked* `*.pem`. The pasted body of `node_access.pem` in a note would pass.

**Fix**
- Extend `known-secret` to:
  - every `deployments/*/secrets.json`;
  - the body of `node_access.pem` (and any `*.pem` under `deployments/*/`), compared after whitespace normalization;
  - passwords in `connection-instructions.txt`;
  - the passwords of `~/.exapump/config.toml`, parsed and never printed (the skill's rule is "never `cat`", and in-memory parsing honours it);
  - `~/.exasol-starter-kit/credentials/*`;
  - optionally the `exasol-cfb` entry's `EXA_PASSWORD` in the Claude Desktop config.
- Reading each into memory and masking it follows the plan's own pattern.
- **The CFBD key:** add an optional owner-only `--cfbd-key-prompt`. It reads the key from a hidden TTY prompt for one run and keeps it in memory only. This is not extraction from the DB, so it respects the "no secret extraction" rule. Without it, the report says "CFBD key: pattern-checked only (Bearer)".

**m7. License and terms.** Correct as owner actions (U8, Q13). Add two things to U8:
- the team names and marks in screenshots and the video (private, so fine);
- the third-party dependency inventory, already a Non-Goal.

`docs/benchmark.md` forbids team names; `docs/demo-script.md` should say the same for its committed text, or keep `{BUBBLE}` placeholders only, with the filled values in the private `facts.md`.

---

## 5. Budget

- **CFBD calls.**
  - Planned: Nov 1 4, Nov 8 11, Nov 8–9 2, Nov 9–13 8, Nov 15 10, Nov 15–16 8 = **43**. The arithmetic is right; ≤ 53 with Nov 22, and the cap is 70.
  - At every cap, November is ≤ 448 of 1000. That stays well inside even if Phase 5's and Phase 6's live groups slip into November.
  - The cadence costs 8 calls a week, not 4 (refresh 4 + recording 4), because AWS has no replay directory to record the refresh's own responses. This is unavoidable. Say so in [3] and [17], since the owner's "~4/week" covers only the refresh.
- **m8. Retry headroom vs the cap.** A worst-case retry storm (`MAX_ATTEMPTS` 4 per request) on 43 requests exceeds 70. The ledger then refuses, which is the right failure. Make sure no *must-have* capture (M1) falls after a rehearsal that could drain the cap: order the Live table so the captures come before the dress rehearsals each week, or give the rehearsals their own sub-cap (for example ≤ 20).
- **m9. The CFBD key's shared pool.** The key is Academic with `sharedPool true`, so the monthly 3000 isn't Nico's alone. The preflight's `quota` check covers the demo. Add a `quota` line to `make weekly`'s first step as well (Phase 2's `CHECK` already refuses).
- **Anthropic spend: capped.**
  - Phase 8's spend is ≤ $10, plus Phase 7's $20 if its 7.2–7.3 run here.
  - Every live run has `MAX_USD`.
  - The panel on demo day and dress rehearsals fall under Phase 7's per-conversation and **daily** caps (`chat/budget`; spend file across restarts).
  - **m10.** State that demo-day chat spend counts under Phase 7's daily cap and name its value in the pre-show checklist. Otherwise, $10 isn't uncapped in practice.
  - The MCP coda in Claude Desktop uses Nico's Claude subscription, not the API key. It works without a key, so it isn't blocked by U3. Say so; it also makes the coda the natural beat-5 fallback (M2, tier B).

---

## 6. Owner actions U1–U10

| Action | Owner only? | Comment |
|---|---|---|
| U1 ledger entry | **No**: the orchestrator can add `add-cfb-demo-hardening: 70` as a sub-allocation (as done for Phases 5 and 6) | The Anthropic ≤ $10 approval stays with the owner. Due **before Nov 8** (the first charge under this plan) |
| U2 local `CFBD_API` | Yes: the key must not pass through tooling (D18, no extraction) | Due **Nov 1** if Q17 fails (M3), else before 10.3 |
| U3 `.env` passwords + users | **No**: the orchestrator can generate random passwords into `.env` (0600) and run `make app-user`/`chat-user`, as `ORCHESTRATOR-STATUS.md` already offers | The API key stays with the owner |
| U4 Claude Desktop entry | Yes: a Windows GUI app and a password in its config | Make `wsl.exe -e uvx exasol-mcp-server@<v>` the **primary** command, because uv and Python are already in WSL and the DSN is then WSL-local `localhost:8563`. Windows→WSL localhost forwarding becomes the alternative, not the plan. Never run plain `exakit mcp-doctor` or `mcp-setup` (see §7) |
| U5 Sunday cadence | **No**, but it needs a running local session (M3) | The orchestrator, in a session Nico starts Sunday afternoon |
| U6 demo date, venue network, picks | Yes | Ask for the date **now** (M3); the picks stay "a week ahead" |
| U7 video | Yes | Schedule it right after 10.6 |
| U8, U9 license, terms, what ships | Yes | Not demo-blocking |
| U10 demo day | Yes | Fine |

**Net:** U1 (ledger), U3 and U5 can move to the orchestrator. That leaves the owner with U1's dollar approval, U2, U4, U6–U10. Add dates to each: U2 Nov 1 (conditional), U6 date now, U4 by Nov 13 (dress rehearsal #1), U7 by Nov 17.

---

## 7. Exasol, BucketFS, runtime and MCP claims (re-checked against the skills)

| Claim | Skill evidence | Verdict |
|---|---|---|
| `ALTER VIRTUAL SCHEMA s SET p='v' q='w'` | `exasol-grammar.md:218-219` `( "SET" ( property "=" string )+ )` | Confirmed |
| The mode switch affects all sessions and is safe against open transactions | Not covered by the skills | Unverified: probe it in 3.2 (M5). The global effect is harmless for the chat and MCP, which read only `CFB_API` |
| A new subdirectory `cfbd/kits/<kit>/` is visible to UDFs | `exasol-bucketfs`: the bucket is synchronized to every node and mounted read-only at `/buckets/<service>/<bucket>/<path>`; a fresh upload is usable once synchronization finishes | Subdirectories are part of the path, so expected to hold. Q18's read-back after the switch and the 10 s retry are the right check. The kit's host-directory copy bypasses the BucketFS service, which probe 1.4k validated only for an existing bucket. Keep the probe |
| Personal restart with `exasol stop/start -d local`, never `exakit` | `exasol-runtime` says "never drive the runtime underneath the kit" and puts the kit's state in `deployments/default`. **On this machine `default` is the AWS instance** (directory listing: `default/` has `node_access.pem` and `deployment.json`) | The plan is right *for this machine*, and stronger than it says: any `exakit start/stop/status/repair-runtime` targets AWS and would break the mandate. **m11:** state it in `CLAUDE.md` (task 8.6) as a hard rule, and include `exakit mcp-doctor` without `--json` (which repairs and may provision the kit's MCP user on the `default` DB, i.e. AWS). Q7's check must be `exakit mcp-doctor --json` only, as the plan says |
| `mcp-doctor` restores the kit's `exasol` entry; the SELECT gate is a keyword check; the role is the boundary | `exasol-mcp` skill | Confirmed. The entry name `exasol-cfb` is right. Q7 stays an assumption: the skill says the kit "owns" the client configs, but doesn't say how it treats foreign entries |
| Claude Desktop (Windows) reaches WSL `localhost:8563` | Not covered | Unverified. Q6's fallbacks are right; promote `wsl.exe` to primary (§6) |
| `NULLIFZERO`, `EXECUTE SCRIPT` without `WITH OUTPUT`, `CREATE TABLE IF NOT EXISTS`, `PRIMARY KEY`, `EXA_DBA_CONNECTIONS` name-only | Plan's citations; grammar | Consistent; not re-derived |

---

## 8. Correctness of kits

### M6 (major): the kit's week check doesn't stop a recording past week N+1

**Evidence**
- `REFRESH_WEEK` re-fetches the whole **season** slices (Phase 2), and `CURRENT_AS_OF` is "the newest finished week" from stored data (Phase 3).
- A `GAMES` recording made after week N+2 completed (for example Nov 22 for kit `w10-w11`, or simply a late re-record) therefore moves the point to **N+2** on the replayed refresh, not N+1.
- The kit checks only "week N+1 complete" (`rehearsal-kit` Background, decision [2]).
- Partial week-N+2 results (MACtion) are harmless to the point. They do make those games' week-N+2 predictions reconstructions on replay, which decision [12]/Q12's "identical week-N+2 freezes" doesn't mention.

**Fix**
- Add a third check: the recorded `GAMES` body has week N+2 **not complete**. It fails otherwise, naming the week.
- Report the count of week-N+2 games already completed as a note in `kit.json`.
- Add a refusal scenario to `demo/rehearsal-kit`.

### M7 (major): freeze the catalog and the `CFB_CORE` DDL while kits are in use

**Evidence**
- `make rehearse` step 1 requires "the deployed catalog hash equals the snapshot's".
- Phase 2 restore refuses column differences, and a different catalog hash without `FORCE` (Phase 2 M6).
- Kit snapshots are taken Nov 1/8/15 and **can't be retaken** (time-bound). Phases 3–7 are being implemented over the same weeks.
- A regenerated catalog (a foundation fix or a spec bump) or a `CFB_CORE` DDL change after Nov 8 makes kit `2026-w09-w10` unusable. One after Nov 15 makes the stage kit unusable.

**Fix**
- Add a **freeze rule** from Nov 1 until the demo:
  - no change to `vs/catalog/`, `sql/core` or the load plan's stored tables without a migration that also re-fingerprints existing snapshots;
  - `make generate && git diff --exit-code -- vs/catalog sql/core` becomes part of `make demo-preflight`'s `kit` check.
- The orchestrator tells the Phase 2–7 sessions.
- Also: no `make deploy` on the **real local namespace** during the demo week except through `make rehearse`. A redeploy can reset VS properties; the preflight `mode` check catches it.

---

## 9. Testability

- **Validation passes.** Scenario→test mapping is complete (46/46); file and test names are consistent with the tasks.
- Integration tests run on `TEST_` with a `TEST2_` peer, at zero calls. The examples are concrete (sim league, 4,000 sims), and the frontend has component tests first.
- **Gaps**
  - M5's mid-chain failure scenario and M4's transcript live-guard (above).
  - **m12.** `test_rehearsal_without_network_or_key` revokes the connection and stops the mock. That proves "no fetch". It doesn't prove the laptop works **offline**: the app's static assets, fonts and the WSL DNS. The manual offline drill in 10.2 covers that; keep it a hard gate in 10.2's Done.
  - `test_two_instances_separate_sessions` should also post one operator action to each instance, to cover the port-scoped `Origin` set.
- **Groups:** see M1 (group F must not block G–I).

---

## 10. YAGNI

These can be cut or deferred without hurting the demo:
- **`ondemand` and `load` benchmark suites.** They copy numbers that the logs already hold. `bench-report` can read `FETCH_LOG`/`LOAD_RUN` directly. Keep `sims` and `whatif`, which need a harness, and `refresh`.
- **Syncing `BENCH_METRIC` both ways.** Only `bench-report` uses the other side's metrics, and it can read both deployments. Sync only `BENCH_SIM_RUN`/`STEP`, which the offline card needs.
- **Scale card in two places** (inspector tab and odds banner line). The demo uses one; keep the inspector tab.
- **`make kit-pick`'s bubble heuristic.** A documented `sql demo` query in the script does the same at zero code. Keep the preview-id listing, which feeds `make record`.
- **Transcript replay (5.3).** It is the most expensive item in the chat beat (`[expert]`-grade work in Phase 7's code) and is worthless until a key exists. Make it explicitly conditional on Q16. It is already "Should"; also mark it "only after the key arrives". The stub with suggestions and the MCP coda cover beat 5 offline.
- **The 2M benchmark cell.** It is fine as specified (one cell, honest drop rule). Keep it.

---

## What's good

- **The kit is a manifest, not a format.** It pins snapshot and recordings by SHA-256, keeps a per-kit `REPLAY_PATH`, keeps its own copy as the source of truth, and is private (0700/0600, outside the repo and OneDrive). It reuses every tested part.
- **Content-checked weeks instead of clock rules.** This is correct for MACtion weeks and stable in tests. M6 adds one more check of the same kind.
- **A fallback without restore.** It is grounded in Phase 2's per-table semantics, and it is the right instinct.
- **An executable demo script with `sql demo`/`sql show`, and a zero-call preflight with fix lines and exit codes.** It turns the go/no-go into a command, and the `recordings` check through `EXPLAIN VIRTUAL` is particularly good.
- **The Sunday cadence for honest predictions** makes "the model went 41–9" a real forecast at zero extra calls.
- **The scale dial's server-side discipline** (configured size only, run lock, loopback operator path) and **`CFB_BENCH`** for an offline-proof comparison.
- **The release check finds real values without writing them anywhere.** It covers history and leaves every publishing decision with the owner.
- **The budget is auditable per plan,** with 0 calls in October and ≤ 448 of 1000 in November at every cap.
- **The Exasol claims are cited to the grammar and skills,** the runtime caveat (`exakit` → AWS) is respected, and the MCP entry avoids `mcp-doctor` drift.

---

## Review resolution

Resolved on 2026-10-23 by the planner, from the orchestrator's decisions. The owner is away and delegated them; they are logged in `decision-log.md` (the last interview entry and decisions [27]–[42]). No DB session, CFBD call or Anthropic call was made, and nothing outside the plan directory was edited. The skills loaded first were `exasol:exasol-database`, `exasol:exasol-bucketfs`, `exasol-mcp` and `exasol-runtime`.

`speq plan validate add-cfb-demo-hardening` → **passed, no warnings**: 8 specs, 60 scenarios (46 + 14 new), all 60 mapped in Scenario Coverage. 46 tasks, 5 of them `[expert]`, in 11 parallel groups.

**Status words:**
- **Accepted** means the review's fix as proposed.
- **Decided** means the orchestrator chose between the options the review named.
- **Varied** means the intent was kept with a different mechanism; the reason is given below.

### Major findings

| # | Status | Resolution | Where |
|---|---|---|---|
| M1 | Accepted | The captures are their own **group A** (tasks 10.1–10.6). Their inputs are only Phase 2 on AWS, the recorder and the ledger entry, and they never wait for another group. `make weekly` (3.5) is due Nov 8; until then the group runs Phase 2's commands one by one. Each Sunday's recordings are copied at once to `~/.cfb-demo/recordings/<season>-wNN/`, and kits build from that copy (`SOURCE=`). The stage kit's honest headline is the dated task 10.4 (week-11 predictions on local by Tue Nov 10), with the labeled reconstruction as the degraded path. The Phase 7 tasks form the optional group G, which never blocks H–K; 8.1's draft and 9.1 may start before 7.1. The new feature `demo/season-cadence` (5 scenarios) specifies the cadence | plan (Summary, dated schedule, Tasks 10.x and 3.5, Parallelization); `demo/season-cadence`; `demo/rehearsal-kit` Background; [27] |
| M2 | Accepted | Tiers Full, B (no chat; the coda is beat 5), C (no app; SQL client in replay and the CLI dial) and D (VS and model only). The go/no-go is on **Mon Nov 9** (task 11.2) and sets `tier` in `demo/demo.yaml`. The preflight takes `TIER=`, and unused checks print `SKIP`. `make rehearse TIER=D` skips the analyses and the publication. The script has a section per tier and `sql demo tiers=`. If Phase 7 is missing, the orchestrator creates the coda's user | plan (Tier scripts, 11.2, 9.1); `demo/preflight` (new scenario "A tier skips…"); `demo/rehearsal`; `demo/chat-beat`; [28] |
| M3 | Decided | **Runner:** the orchestrator from a local Claude Code session on the laptop; the owner's fallback is `make weekly WEEK=n CONFIRM=yes`. **Windows:** the snapshot until Tuesday's first kickoff (content rule: until week n+1's first FBS game ends); the recordings until week N+2 completes; the predictions until kickoff. **Missed Sunday:** a `-late` snapshot, a recording skipped at zero calls when its kit would refuse it, and the previous kit used and logged. **Q17** is a gate on **Sat Oct 31** (10.1): a 0-call fix first, then U2 by Nov 1 and laptop snapshots. **Demo date:** Nov 16–20 assumed; U6 is non-blocking | plan (dated schedule, Missed Sunday, 10.1); `demo/season-cadence`; [29], [30]; Q1, Q2, Q17 |
| M4 | Decided | The default is **"live proof, replay data"**. App A refreshes in replay over the kit, and the live call is App B's console `fan-out` in beat 2 (3 calls under `app`), with App B's preview as the reserve. Live data on the laptop is the owner's override (U11). The script has two expected columns, "replay (kit facts)" and "live (read on screen)". A transcript replays only after a refresh whose fetches were all `REPLAY`, with the slice keys and request times of the kit's own recordings, checked at start and before each turn | plan (Decision, demo-script outline); `demo/rehearsal` Background; `demo/chat-beat` (binding + a THEN in the transcript scenario); `demo/preflight` live checks per deployment; [31], [32] |
| M5 | Accepted | `make demo-mode` refuses while any non-stale run holds the lock, naming it and `make demo-abort`. `APP_REFRESH_DEADLINE_S` (default 45) applies to the fetch steps. `make demo-abort` runs `KILL STATEMENT … WITH MESSAGE 'demo-abort'` and waits ≤ 15 s for a stale lock; the next run sets the row `ABANDONED`. Two new scenarios: a third-fetch failure after a changed live `GAMES` committed, and a hung refresh that is aborted and then switched. The probe in 3.2 covers the `ALTER` against an open reader, the mode's reach, and the killed run's transaction (Q19, Q20) | `demo/rehearsal` (2 new scenarios); tasks 3.2, 3.6; [33] |
| M6 | Accepted | A third week check: the recorded `GAMES` body must have week N+2 not complete. `kit.json` counts the week-N+2 games already played. The cadence applies the same rule before paying for a recording | `demo/rehearsal-kit` (new scenario); `demo/season-cadence`; `demo/preflight` `[week]`; [34]; Q12 |
| M7 | Accepted | The freeze of `vs/catalog/`, `sql/core/`, `sql/meta/load_log.sql`, `sql/meta/fetch_log.sql` and `loader/load-plan.yaml` runs from Nov 1 to the demo (`UNTIL` Nov 21). `make demo-freeze` pins the tree ids, and the `freeze` rule in `release_check --fast` fails every `make lint` on a change. The preflight's `kit` check compares the deployed catalog hash and the pins. The `CLAUDE.md` rules ship by Oct 31 (task 1.6), together with "no `make deploy` on the real local namespace in demo week". The orchestrator tells the Phase 2–7 sessions (10.2) | `demo/release-check` (new scenario); `demo/preflight` (new scenario); tasks 1.6, 10.2; [35] |
| M8 | Accepted | `known-secret` reads every deployment's `secrets.json`, `deployment.json`, `connection-instructions.txt` and `*.pem` (key bodies compared without whitespace), `~/.exapump/config.toml` (parsed, never printed), `~/.exasol-starter-kit/credentials/*`, the Claude Desktop `exasol-cfb` entry and `.env`. A missing home is a `WARN`. The CFBD key is checked only with `--cfbd-key-prompt` (hidden prompt, memory only); otherwise the report says "pattern-checked only" | `demo/release-check` (2 new scenarios); task 1.1; [36] |

### Minor findings and other sections

| # | Status | Resolution |
|---|---|---|
| m1 | Decided | `make bench-sync` in the open terminal right after the dial; App A's inspector "Scale" tab then shows both just-watched runs. If the sync fails, it shows the last synced run, "measured <date>" ([40]) |
| m2 | Accepted | A terminal tab with `make bench-sync`, `make demo-abort` and `make demo-mode MODE=replay KIT=…` pasted, in the pre-show list (8.1) |
| m3 | Accepted | `make app-build` at T-2 h, and a new preflight `app` check (tiers Full and B) |
| m4 | Varied | Not a script line but a flag: App B runs with `APP_REFRESH=off` (button disabled, 403 `refresh_off`); new scenario in `demo/scale-dial`. A flag cannot be forgotten on stage |
| m5 | Accepted | Q10 is kept, and the reset time is measured in 11.1 |
| m6 | Accepted | The dial's confirmation quotes the plan's estimate, or "about a minute"; it is part of the plan scenario and the component test |
| m7 | Accepted | The committed `docs/demo-script.md` holds placeholders only; `make kit-facts` writes the filled copy into the private kit. U8 also covers the team names and marks in the screenshots and video, and the dependency inventory ([42]) |
| m8 | Varied | Captures before rehearsals each week, and a rehearsal starts only with room for the remaining captures. There is no rehearsal sub-cap: it would need a second ledger plan name for a rule the calendar order already enforces ([41]) |
| m9 | Accepted | `quota` is `make weekly`'s first step and refuses before spending (new scenario) |
| m10 | Accepted | Demo day runs with `CHAT_DAILY_MAX_USD=3.00` and `CHAT_SESSION_MAX_USD=2.00` on one shared spend file. The values are named in the pre-show list and printed by the preflight's `chat` check. Spend is ≤ $9 of the approved ≤ $10 ([41]) |
| m11 | Accepted | The `CLAUDE.md` hard rule ships by Oct 31 (1.6), word for word as decided: no `exakit` runtime command or plain `mcp-doctor` on this machine, only `exakit mcp-doctor --json` ([38]) |
| m12 | Accepted | The Wi-Fi-off drill is a hard gate in 11.1 and in 11.5; no dress rehearsal counts before it passes |
| §5 budget | Accepted | The cadence is stated as 8 calls a week (refresh 4 + recording 4) in [3], [17], [41], the plan's budget and 8.5. The planned total stays ≤ 43 (≤ 47 with the conditional Nov 1 recording) |
| §6 owner actions | Accepted | U1 (the ledger entry, done), U3 (passwords and users) and U5 (the cadence) move to the orchestrator. The owner keeps the dated list below |
| §7 runtime and MCP | Accepted | U4's primary command is `wsl.exe -e <absolute uvx> exasol-mcp-server@<v>`. Two details were added, both checked on this machine: `wsl.exe -e` starts no login shell and `uvx` is `~/.local/bin/uvx` (on the path only through `.profile`/`.bashrc`), so the path must be absolute; and `WSLENV` with `/u` must carry the `EXA_*` variables (Q6) |
| §9 tests | Accepted | The two-instance test also posts an operator request with each origin |
| §10 YAGNI | Accepted, all | The `load` and `ondemand` suites are cut (the report reads the logs), and so are the `BENCH_METRIC` sync and the odds-banner card line. `kit-pick`'s heuristic becomes a `sql demo` query, with `make kit-previews` for the preview ids. Transcript replay (5.3) is conditional on a key. The 2M cell is kept ([39]) |

### Varied or rejected, with reasons

- **M5's total refresh deadline (60 s example): varied.** The deadline covers only the `FETCH` steps, default 45 s. A total deadline would also fire on a slow but healthy simulation step, and a network hang happens in the fetches.
- **M7's `make generate && git diff` inside the preflight: varied.** The preflight writes nothing, and `make generate` writes files. The preflight compares the deployed catalog hash and the git tree ids against `demo/freeze.yaml` instead. The generate-and-diff check stays in the Checklist and in lint.
- **M3's "ask U6 now": varied** by the orchestrator. Nov 16–20 is assumed, and U6 is a non-blocking confirmation that moves the freeze's `UNTIL` and the stage kit if the date changes (Q2).
- **M3's "snapshot Sun–Mon" window: varied** by the orchestrator to "until Tuesday's first kickoff". The kit's content rule is the hard limit.
- **m4 and m8:** varied as stated in the table.
- **Rejected outright:** nothing.

### Additions beyond the review (flagged for the owner)

- **The cost of the Q17 fallback.** If the cross-version restore cannot be fixed at zero calls, the laptop needs its own demo backfill: ≈ 217 calls on a new November ledger line the orchestrator would add, outside this plan's 70, which brings November to ≈ 670 of 1000. Each Sunday would then cost 12 calls here (≤ 55 of 70 in total) ([30]).
- **A `-late` snapshot name and a zero-call skip of a recording a kit would refuse,** so a late cadence never pays for an unusable capture ([29]).
- **The transcript guard proves "the kit's path" from `CFB_API.FETCHES` request times** (`cfbd-fetch/fetch-modes`), not from virtual-schema properties, which the app's roles cannot read ([32]).

### Dated schedule (Oct 31 → demo)

| Date (2026) | What | Calls |
|---|---|---|
| Sat Oct 31 | Q17 gate (0-call AWS → laptop restore); the freeze rule and the `CLAUDE.md` hard rules in place (1.6) | 0 |
| Sun Nov 1 | Freeze begins; cadence week 9: AWS refresh, snapshot `aws-2026-w09` (the recording only if an `aws-2026-w08` base exists) | 4 (≤ 8) |
| Tue Nov 3 | Week-10 predictions on local before kickoff, if Phase 5 is deployed | 0 |
| Sun Nov 8 | Cadence week 10 (`aws-2026-w10`, the stage kit's base; recordings for `2026-w09-w10`) + the console fan-out once | 11 |
| Nov 8–10 | Kit `2026-w09-w10`, previews, determinism check, offline drill (hard gate) | ≤ 2 |
| Mon Nov 9 | Go/no-go on the demo tier | 0 |
| Tue Nov 10 | Week-11 predictions on local before kickoff (the stage kit's honest headline) | 0 |
| Nov 9–13 | Dress rehearsal #1; benchmark and report; U4 by Fri Nov 13 | ≤ 8 |
| Sun Nov 15 | Cadence week 11 (recordings for the stage kit `2026-w10-w11`) | 8 |
| Nov 15–16 | Stage kit, previews, facts, preflight, offline drill (hard gate); dress rehearsal #2 | ≤ 10 |
| Nov 16–20 | Demo week: no `make deploy` on the real local namespace; the demo (under `app`) | (`app`) |
| Sun Nov 22 | Only if the demo moves later: cadence week 12, kit `2026-w11-w12` | ≤ 10 |
| Sat Nov 21 | Freeze ends | 0 |

### Owner actions after the revision

- **U1:** approve ≤ $10 of Anthropic spend.
- **U2 (conditional):** the local `CFBD_API` connection. It is due Sun Nov 1 only if the Q17 gate fails; otherwise it is needed only for the live-data override.
- **U3:** `ANTHROPIC_API_KEY` in `.env` when available.
- **U4:** the Claude Desktop entry `exasol-cfb` through `wsl.exe`, by Fri Nov 13.
- **U5 (fallback only):** run `make weekly WEEK=n CONFIRM=yes` if no orchestrator session has run the cadence by Monday evening.
- **U6:** confirm the demo date (Nov 16–20 assumed; non-blocking) and the venue network; approve the bubble team and the pairs on Sun Nov 15.
- **U7:** the fallback video and screenshots: a first take at dress rehearsal #1, a refresh at #2.
- **U8:** the LICENSE holder and MIT; the CFBD terms (Q13); the team names and marks in the private media; the dependency inventory before publishing.
- **U9:** what ships, any history rewrite, and when to publish.
- **U10:** one click-through rehearsal before the show. On demo day: free memory, `exasol stop/start -d local` (never `exakit`), mains power and a backup network.
- **U11 (optional):** override the stage default to live data on the laptop (needs U2).
