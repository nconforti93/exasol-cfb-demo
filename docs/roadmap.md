# Roadmap & Build Plan

Each phase is one speq plan (`/speq:plan`), and each leaves a demoable state.

## Decision log

| # | Date | Decision | Why |
|---|------|----------|-----|
| D1 | 2026-10-21 | Python VS adapter; Python **and** Rust sim engines *(engine part superseded by D16)* | Adapter language doesn't show (HTTP dominates); engines compared head to head |
| D2 | 2026-10-21 | CFBD key only in CONNECTION `CFBD_API`, script-bound `ACCESS` for `CFBD_FETCH` | Verified on 2026.1.0: readers need no connection grant; replace drops the grant |
| D3 | 2026-10-21 | **Persist all history; no hot/cold tiering through the API** *(refined by D15)* | History is small (few GB), never changes, and is expensive to query through the API (calls + seconds). The API is a source, not a storage tier |
| D4 | 2026-10-21 | CFBD Patreon tier 2 ($5/month, 30k calls) | Full backfill about 2k calls in one go; room for on-demand lookups and development |
| D5 | 2026-10-21 | Per-endpoint history start dates from CFBD's data-availability page (games back to 1869) | "All data" means everything each endpoint has (`docs/cfbd-data-availability.md`) |
| D6 | 2026-10-21 | The VS is featured in three roles: ingestion as SQL, on-demand long-tail detail, transparency (SQL↔HTTP inspector) | Shows "the API is a schema" without a contrived tiering story |
| D7 | 2026-10-21 | Demo narrative: "Sunday after week N" → live `REFRESH_WEEK(N+1)` → every page updates | One call drives all use cases; replay mode keeps it safe offline |
| D8 | 2026-10-21 | v1 pages: playoff odds, weekly predictions + scorecard, biggest upsets, head-to-head | They carry the refresh story; history/dynasties go to the backlog |
| D9 | 2026-10-21 | Live in-game data out of scope | Only demoable on Saturdays; maybe later as a recorded replay |
| D10 | 2026-10-21 | The engine samples scores (margin + total), not just winners | Powers predictions, point-differential tiebreakers and upsets from one model |
| D11 | 2026-10-21 | Backfill runs once per data owner. A rebuilt DB is restored from a Parquet snapshot (zero calls); `BACKFILL()` resumes and skips slices already in the load log | Rebuilds, new machines and AWS must not re-spend quota. Snapshots stay private (CFBD allows private retention, forbids redistribution) |
| D13 | 2026-10-21 | **The demo runs on the free 1k/month tier.** The load plan has a `demo` profile (v1 pages only: games, SP+, rankings, pregame WP, conferences, lookups, 2013–2026, about 100–150 calls) and a `full` profile (all 93 endpoints, all history) that fills in from the leftover monthly budget. Head-to-head uses on-demand `/teams/matchup` (one call per click). Ratings are computed from games in SQL. The demo refresh is about 8 calls | A labs visitor with a free key reaches a working app with about 150 calls; the full archive completes in about 3–4 months without touching the demo reserve |
| D14 | 2026-10-21 | Design from the **full spec read** (`docs/cfbd-api-reference.md`): conditional requirements come from a reviewed overrides file; arrays of objects become child tables; the loader never relies on API defaults; gated endpoints (weather, adjusted metrics, scoreboard, live) are excluded from profiles unless `/info` reports access; per-game Elo/WP from `/games` replaces `/ratings/elo` for v1 | Most requirement rules are prose-only; several endpoints are tier-gated or capped; `/games` already carries the Elo history back to 1890 |
| D15 | 2026-10-21 | **Stored vs. federated by access pattern, with read-through.** Endpoints are classed `stored` (scanned broadly/repeatedly by the demo: games since 1869, pregame WP, SP+, rankings, conferences, teams, venues), `federated` (all other free-tier endpoints, queried selectively on demand through the VS) or `unavailable` (tier-gated on the key). The VS pushdown serves already-stored slices from `CFB_CORE` (0 calls) and fetches only missing slices (`UNION ALL … WHERE NOT EXISTS` against the load log); a nightly `PROMOTE()` persists slices fetched on demand. `PROFILE=full` (~3,300 calls) becomes optional | The demo must work on a free key (1k/month); selective federated queries cost 1–2 calls, broad ones are blocked by the call budget; the read-through avoids paying twice for the same slice. Refines D3: D3 rejected tiering *by age* (broad historical scans through the API); D15 tiers *by access pattern* |
| D16 | 2026-10-21 | **Python is the only sim engine** (numpy, vectorized across sims; SET UDFs fanned out with `GROUP BY batch_id`). Rust moves to the backlog | Python is built into Exasol Personal; Rust adds toolchain/SDK pinning (fast-moving, one breaking release in 5 weeks) and a cross-language bit-identical RNG burden for every user |
| D17 | 2026-10-21 | **Runtime: Exasol Personal local** (laptop-class), not the docker-db container; the demo profile and 100k-sim runs must work on a laptop; AWS only for the 1M+ scale beat | Matches how users will run it; Personal local here has 11.4 GiB DB RAM, Python 3.12 + numpy, 20 cores |
| D18 | 2026-10-21 | **No key tool:** the user creates `CFBD_API` with plain `CREATE CONNECTION` SQL; the user guide documents the SQL (create, grant, rotate, verify) | User preference; fewer moving parts; the key never passes through tooling |
| D19 | 2026-10-22 | **Python engine: GO** (S3, `docs/spikes/s3-python-engine-throughput.md`). `GROUP BY batch_id` fans out to **`nrOfCores` instances per node** (not host CPUs); Personal **auto-sizes `nrOfCores` and DB RAM at every start** from free host memory (12 cores / 11.4 GiB, 6 / 5.5, 8 / 7.6 seen on this laptop), so record the sizing with every run. Recommended `sims_per_batch` 1,000–10,000 (at least 2 batches per slot): **10,000** on the laptop and as the stored default, 1,000 for a 100k run on AWS 3×4. Emit with `ctx.emit(DataFrame)`, run the engine once per batch (single pass), one UDF per output shape with explicit `EMITS`. 100k wall time, target < 60 s: **local 55.4 s median [40–76 s] (8 cores)**, **AWS 12.4 s** (3 nodes × 4 cores). Emission is about 75 % of the wall; compute-only 100k is about 7 s local / 1.5 s AWS; no stage is loop-bound (top: `records`, `standings`, `sample`, `resume`) | "100k seasons in N seconds" holds on AWS with margin and on the laptop only with reduced emission (D20: est. 17–20 s). Corrects the D17 sizing: Personal does not keep 11.4 GiB / 20 cores, it sizes both at start, and UDF concurrency is `nrOfCores` (D17's row is left as written). Seeding is per `(run_seed, batch_id)`, so `sims_per_batch` is part of the reproducibility key |
| D20 | 2026-10-22 | **Sim storage** (S4, `docs/spikes/s4-sim-storage.md`): `SIM_PATH` is **layout C** (one `BOOLEAN` column per remaining game, DDL generated per run; A, a `VARCHAR` vector, is the near-tie fallback; B rejected). `SIM_TEAM` is **sparse** (in-CFP rows only, about 12 per sim) plus a `TEAMS` roster for the zero rows, plus a per-run **`LEVERAGE`** table built by the loader. **No per-sim margins**; the per-run histogram M0 is optional and off by default | 1M sims on AWS (3 nodes): dense `SIM_TEAM` Q1 k=1 p95 9.47 s vs sparse 0.52 s (k=3 0.21 s, k=8 0.17 s, Q2 < 0.2 s); 717 → 55.6 MB; unconditional rooting guide 0.078 s with `LEVERAGE` (vs 1.35–2.5 s). At 100k every layout answers what-if in < 120 ms p95, C fastest in 6 of 8 cells, B is 15× A in memory. No v1 page reads per-sim margins (+38 to +78 MB at 100k, +379 MB at 1M). **Unmet:** the conditional rooting guide (k=3) is about 2.2 s p95 at 1M; deferred to Phase 5 (accept, precompute common condition sets, or `DISTRIBUTE BY`) |
| D21 | 2026-10-21 | **UDF DNS workaround:** the fetch UDF resolves hostnames via the system resolver, falling back to DNS over HTTPS (1.1.1.1, configurable) when the sandbox can't resolve | Exasol Personal nano 2026.2.0-nano.3 UDF sandboxes have empty resolv.conf/hosts (verified, even `localhost` fails); TCP egress works; Nico approved the workaround. To be reported to the nano team; remove when fixed |
| D22 | 2026-10-21 | **Parameters are separate `P_` echo columns** (`P_YEAR`, `P_WEEK`, …): each row carries the parameter values of the request that produced it (NULL if not sent); response fields keep their own columns | Uniform for 93 endpoints with no per-pair binding review (about 150 parameter/field pairs, several semantic mismatches, and a wrong binding drops rows silently); rows always satisfy their parameter predicates; avoids reserved words. The S2 query reads `P_YEAR BETWEEN 2019 AND 2021 AND P_WEEK IN (8,9)` |
| D23 | 2026-10-21 | **One dynamic-output fetch UDF** (`CFB_INFRA.CFBD_FETCH … EMITS (...)`) serves every table; the catalog is embedded in the adapter and the UDF with a hash handshake; deploy replaces the UDF only when its text changed | The connection grant is per script, so one script keeps the single key reader; stored and live types come from one catalog; the pushdown SQL stays readable |
| D24 | 2026-10-21 | **Advertise more predicates than are pushed** (`<>`, `LIKE`, `OR`, `NOT`, `IS NULL` …): translate only `=`/`IN`/ranges/`AND`/same-parameter `OR` on `P_` columns, reject every other parameter predicate with a message, re-apply all other filters as a residual; no aggregate, projection, `LIMIT` or join pushdown | Unadvertised predicates on `P_` columns would be evaluated by Exasol against NULL echo values and return silently empty results. Accepted by Nico |
| D25 | 2026-10-21 | **The call budget counts planned requests per virtual-table access**, stored slices included (`MAX_CALLS_PER_QUERY`, default 10); stored-only queries (`REQ_SOURCE = 'STORED'`) are exempt | The adapter can't see the load log; a plan-time ceiling is safe and visible in `EXPLAIN VIRTUAL` |
| D26 | 2026-10-21 | **Read-through by exact slice key:** a slice (`TABLE_NAME`, canonical request path) is served from `CFB_CORE` iff `CFB_META.LOAD_LOG` holds a final row for it; mirrors have the virtual table's columns | Simple, provable, exactly once per slice; verified live in S2 (stored slice 0 calls, missing slice exactly 1). Subsumption (a stored season serving a week) is deferred |
| D27 | 2026-10-21 | **Fetch log:** every table carries `REQ_*` request columns and the pushdown SQL starts with a JSON manifest of the planned requests; consumers persist `CFB_META.FETCH_LOG` (`vs/host/fetchlog.py`) | UDFs cannot write tables; actuals travel with the rows, the plan with `EXPLAIN`, zero-row requests stay visible |
| D28 | 2026-10-21 | **Child tables** for arrays of objects: 1-based position columns, reviewed parent keys, hoisting only where declared, a curated subset plus `RAW_JSON` for objects over 500 fields | A positional join always works; heuristic keys would fail silently |
| D29 | 2026-10-21 | **Integration tests make zero CFBD calls:** `TEST_` namespace, mock CFBD API addressed by IP with a marker key, synthetic recordings; a guard refuses `CFBD_API` | CFBD terms (the repo goes public), quota, and no risk to stored data or the real key |
| D30 | 2026-10-21 | **No reliance on hidden defaults:** the overrides turn each one into an explicit sent-and-echoed default, a requirement, or a verified "omission means all" (S1); `/plays/stats` requires `gameId` (2,000-row cap); a prose lint fails generation on uncovered rule-like descriptions | Implements D14 mechanically and keeps it from drifting on spec bumps |
| D31 | 2026-10-21 | **Fan-out runs as one SET group with a bounded thread pool** (`MAX_PARALLEL_FETCHES`, default 6) | Speed doesn't depend on DB load and CFBD concurrency stays under control; S2: 6 slices fetched concurrently in 424 ms against 2.3 s sequentially. Chosen by Nico |
| D32 | 2026-10-21 | **`sys` owns the real deployment;** readers get the role `CFB_READER` with `SELECT` on `CFBD_VS`; the `TEST_` namespace keeps a dedicated owner, so the script-bound grant model stays tested | On a Personal DB a separate owner adds a password and a step without demo value. Trade-off: `sys` can read every connection, so "only `CFBD_FETCH` reads the key" holds for readers. Chosen by Nico |
| D12 | 2026-10-21 | Start on CFBD's free tier (1k/month; academic 3k if eligible); upgrade to tier 2 ($5) only if needed | With D11 the backfill can spread over 2–3 months, newest seasons first; the demo works on recent seasons from week one. Alternatives (ESPN undocumented API, SportsDataverse bulk files, API-Sports) lack a spec, clear terms or breadth |

## API call budget (designed for the free tier: 1k calls/month, D13/D14)

| What | Calls |
|---|---|
| `make backfill PROFILE=demo` (= the `stored` class): `/games` 1869–2026 (with per-game Elo/WP), pregame WP + SP+ 2013–, polls 2014–, affiliations (1 range call), teams, venues, conferences, calendar | 217, once (S1: one `/games` call per season covers every division) |
| Demo `REFRESH_WEEK`: current-season games, pregame WP, rankings, SP+ (`/games/schedule` is volatile and not stored) | 4 per week |
| Federated on-demand queries (head-to-head, previews, plays, box scores, chat) | 1–2 per selective query and 0 once stored (read-through + nightly `PROMOTE()`); ~100–200/month |
| Rehearsals (restore snapshot + replay) and tests | 0 |
| **Demo total** | **~330 in month 1, ~120/month after** |
| *Optional* `PROFILE=full` archive (stores every federated endpoint too, all history, newest first; not needed for the demo, D15) | **~3,300 total**: ~2,150 from year×week endpoints (`/plays` 442, `/games/teams` 391, `/games/players` 391, player-game success/PPA 255 each, punting plays 204) + ~1,200 per-season/static. Free tier: ~4 months after the demo profile; academic tier: ~1–2 months |

Full details: `docs/cfbd-api-reference.md`. S1 (2026-10-22) verified the per-season assumption for `/games`: one call returns a whole season of every division, about 2.9 MB, and an omitted `seasonType` means regular plus postseason ([`docs/cfbd-api-measurements.md`](cfbd-api-measurements.md)). The project key is Academic (3,000 calls/month, shared pool); the owner caps October 2026 at 400 calls across all plans. `/info` and refused (401) calls are free.

## Phase 0: Spikes

| # | Spike | Done when |
|---|-------|-----------|
| ✅ S0 | CONNECTION privilege model | Verified (D2) |
| ✅ S1 | Endpoint catalog + real sizes | Generator prototype over the spec + `vs/catalog-overrides.yaml` (conditional rules, defaults, caps, gating, child tables) seeded from `docs/cfbd-api-reference.md`; `/info` features of the actual key; one real call per access pattern (≈25 calls) for row counts, payload sizes, the `seasonType`/`classification` defaults; demo budget re-confirmed. **Done 2026-10-22: catalog of all 93 GET operations (137 virtual tables, `vs/catalog/catalog.json`); the key is Academic with every feature false; 10 calls: `/games` returns a season of every division in one call (2.9 MB), an omitted `seasonType` means both, `/plays` covers FBS and FCS, every real payload flattens strictly; demo budget 217 + 4/week re-confirmed; write-up [`docs/cfbd-api-measurements.md`](cfbd-api-measurements.md)** |
| ✅ S2 | VS pushdown, fan-out, read-through, fetch log | `EXPLAIN VIRTUAL … WHERE P_YEAR BETWEEN 2019 AND 2021 AND P_WEEK IN (8,9)` → 6 parallel requests; contradiction → 0 calls; missing required/conditional filter → clear error; call budget enforced; **read-through: a stored slice costs 0 calls and Exasol does not invoke the fetch UDF on the empty `NOT EXISTS` branch (verified, not assumed)**; stored vs. live columns identical; no UNION/aggregate crash on 2026.1.0; fetch-log mechanism chosen. **Done 2026-10-22: live on AWS 2026.1.1 (7 calls): 6 parallel requests in 424 ms (2.3 s sequential), a stored slice 0 calls, a missing slice exactly 1; aggregates over the read-through `UNION ALL` don't crash on 2026.1.1 or 2026.2; contradictions, rules and the budget verified at 0 calls by the integration tests; fetch log = `REQ_*` columns plus the `EXPLAIN` manifest (D27); details in [`docs/cfbd-api-measurements.md`](cfbd-api-measurements.md)** |
| ✅ S3 | Python engine throughput | Python SET UDF fan-out via `GROUP BY batch_id` across all cores on Personal; a numpy-vectorized prototype of season + selection + bracket; sims/second vs. batch size; 100k-sim run time on the laptop. **Done 2026-10-22: GO, 100k local 55.4 s / AWS 12.4 s (D19); write-up [`docs/spikes/s3-python-engine-throughput.md`](spikes/s3-python-engine-throughput.md)** |
| ✅ S4 | Sim storage layout | On Personal: layouts A (VARCHAR vector), B (long table), C (one BOOLEAN column per game); measured at 100k, then 1M if it fits; what-if p95 for 1/3/8 conditions + rooting guide; per-game score storage decided. **Done 2026-10-22: layout C, sparse `SIM_TEAM` + `LEVERAGE`, no per-sim margins (D20); write-up [`docs/spikes/s4-sim-storage.md`](spikes/s4-sim-storage.md)** |

## Phase 1: CFBD Virtual Schema
- Generator, adapter, fetch UDF for all 93 endpoints; required-filter semantics; range/IN fan-out; contradiction short-circuit; call budget; replay mode.
- `make deploy`, `make grants`; `docs/user-guide.md` covers the `CREATE CONNECTION` SQL (D18).
- **Demo beat:** "93 REST endpoints, one `CREATE VIRTUAL SCHEMA`, no mapping code. Here's the SQL, here's the HTTP."

## Phase 2: Persistence + refresh
- Load plan (history start, gaps, refresh class, **profile `demo`/`full` + priority**), `REFRESH_WEEK()`, load/refresh/fetch logs, quota view.
- `BACKFILL([from])`: resumable and idempotent. Fetches only (endpoint, season, week) slices missing from the load log, newest first, within a monthly call budget that keeps a reserve for refreshes.
- `make snapshot` / `make restore`: export/import `CFB_CORE` (+ load log) as Parquet in a private location outside the repo (local dir or private S3 for AWS). Zero API calls on a rebuild.
- Dev container data on a named Docker volume.
- `PROFILE=demo` backfill first; `PROFILE=full` trickles in from the leftover monthly budget.
- Restoring the "Sunday after week N" snapshot is also the rehearsal reset.

## Phase 3: Analytical model
- `CFB_MODEL`: era-aware dims and facts, coverage flags, `GEOGRAPHY` venues, as-of helpers, `COMMENT ON` throughout.
- **Done when:** an analysis can be written without touching `CFB_CORE`.

## Phase 4: Score model + engine
- Margin/total model calibrated on 2014–2025 (MAE, Brier, calibration); Elo-based probability for pre-2013 upsets.
- Selection config (2026 rules), simplified tiebreakers.
- Python engine (vectorized, seeded per batch); run registry; backtest of 2024/2025 CFP fields.
- **Demo beat:** "100k seasons in N seconds, in parallel on every core, in plain Python inside the database."

## Phase 5: Analyses
- `CFB_SIM` what-if (conditional odds, rooting guide, forced re-sim), week-over-week movement.
- `CFB_PRED`: frozen weekly predictions, scorecard, upsets with all-time rank.
- Head-to-head: all-time series on demand via `/teams/matchup` (one call per click), next meeting + our prediction, on-demand preview through `CFB_API`.
- `CFB_API` serving views and functions, all commented.

## Phase 6: App
- FastAPI + React:
  - Pages: odds, predictions + scorecard, upsets, head-to-head.
  - Extras: SQL↔HTTP inspector, refresh button + step timeline, as-of week picker.
  - CFBD credit in the footer.

## Phase 7: Conversational analyst
- Chat panel: Claude + Exasol MCP (`CFB_MCP`) over `CFB_API`; app-state tools; SQL disclosure; rehearsed question set as a regression check.

## Phase 8: Demo hardening
- AWS deployment; sims as the scale dial (local 100k vs. AWS 1M+).
- Replay mode rehearsal ("Sunday after week N" recorded), recorded fallback, `docs/demo-script.md`, `docs/benchmark.md`.
- Labs readiness: README, LICENSE (MIT), secret scan, no raw data in git.

## Demo flow (~25 min)
1. **Hook (2):** the app as of Sunday before week N+1's refresh. "Your team, its playoff odds."
2. **The API is a schema (5):** `CREATE VIRTUAL SCHEMA` over CFBD; `SELECT` from an endpoint; inspector shows the HTTP; a year range fans out in parallel; a contradiction costs 0 calls; 157 years of games already persisted.
3. **One call refreshes everything (5):** `CALL REFRESH_WEEK(2026, N)`; the timeline shows fetch → merge → model → predictions → 100k sims with seconds and calls per step; then the sim count as the dial (laptop 100k, AWS 1M).
4. **Pages move (8):** scorecard ("model went 41–9"), upset of the week ranked all-time, playoff odds with arrows, what-if toggles + rooting guide on a bubble team, head-to-head with a live preview fetched on click.
5. **AI (4):** the chat sets a what-if, explains a swing, answers a question outside the pages; shows its SQL.
6. **Close (1):** one platform, any analysis; repo link.

## Backlog
- History / dynasties (peak Elo since 1890, streaks).
- Recorded game-day replay with live win probability.
- Live-spec VS refresh (`SPEC_SOURCE=live`).
- 4th-down "Go-For-It Index"; recruiting geography; coach performance vs. talent; transfer-portal flows.

## Open questions
- AWS sizing: can Exasol Personal on AWS scale out, or only up?
- Which week N to record for replay mode, and when (the season runs until the 2027-01-25 title game).
