# Mission: exasol-cfb-demo

> A college football analytics platform on Exasol, where the data source is a REST API and the API looks like a schema. A Virtual Schema generated from CFBD's OpenAPI spec exposes the whole CollegeFootballData API as SQL. All history (back to 1869 where it exists) is persisted once, a single SQL call refreshes each week, and an app on top shows playoff odds (Monte Carlo in parallel in-database Python UDFs), weekly score predictions, the biggest upsets and head-to-head matchups.

## Problem Statement

Exasol's strengths are hard to show with toy data:
- Federation, MPP compute, in-database UDFs and AI access over MCP usually get demoed separately on TPC-H, and audiences forget them.
- "Virtual Schema" sounds like plumbing until people see one turn SQL into REST calls.

This project shows both on data people care about:
1. **The API is a schema.** 93 REST endpoints become tables with no hand-written mapping. Ingestion is SQL. Detail nobody stores is fetched when someone asks for it.
2. **One refresh updates every page.** After a game week, one `EXECUTE SCRIPT CFB_INFRA.REFRESH_WEEK(season, week, 'regular')` call fetches through the Virtual Schema, merges, updates the model, runs the simulations, and every page in the app moves.
3. **The simulations create their own scale.** Monte Carlo runs produce hundreds of millions of rows, and every what-if click is an interactive query over them.

## Target Users

| Persona | Goal | Key Workflow |
|---------|------|--------------|
| Demo audience (data engineers, architects, analytics leads) | See an API used as a schema, in-database compute and AI on data they care about | Watch the week-N app, the live `EXECUTE SCRIPT CFB_INFRA.REFRESH_WEEK(2026, n+1, 'regular')` with the SQL↔HTTP inspector, then pages updating and what-if clicks |
| Presenter (Nico, later SEs and DevRel) | Run a reliable 20–30 min demo, live or recorded | Prepare the "Sunday after week N" state, refresh on stage, fall back to replay mode without network |
| Analysis builder | Add a new analysis on the existing model | Query `CFB_MODEL`, add a schema plus `CFB_API` views, optionally an app page |
| Exasol Labs visitor | Reproduce on Exasol Personal | Clone, create the CFBD CONNECTION with their own key, `make deploy`, `make backfill`, open the app |

## Core Capabilities

### Platform

1. **CFBD Virtual Schema.** Generated from CFBD's OpenAPI spec, it exposes every endpoint as a read-only table.
   - Query and path parameters become pushdown predicates. Required parameters become mandatory filters.
   - Year ranges and `IN` lists fan out into parallel calls under a per-query call budget.
   - Nested objects are flattened into columns; arrays become JSON columns.
   - It has three roles: ingestion source, on-demand source for per-entity detail that is never stored (game previews, matchups, advanced box scores, player lookups), and the source of the quota and status tables.
2. **SQL↔HTTP transparency.** Every Virtual Schema query shows the HTTP requests it became, plus latency, rows and calls spent, through `EXPLAIN VIRTUAL` and a fetch log the app shows in an inspector panel.
3. **Full-history persistence.** A load plan records, per endpoint, the history start date and known gaps from CFBD's data-availability page. A resumable backfill persists all history once, skipping slices already loaded: the `demo` profile is 217 calls, the optional `full` archive about 2k more. Snapshot/restore to a private gzip CSV directory rebuilds any database with zero API calls. Historical data is never re-fetched.
4. **Weekly refresh.** `EXECUTE SCRIPT CFB_INFRA.REFRESH_WEEK(season, week, season_type)` is one SQL call that runs every step (fetch → merge → model → predictions → simulations), logging time, rows and API calls per step and returning the step timeline. The app shows it as a progress timeline. Exasol has no `CALL` statement, hence `EXECUTE SCRIPT`.
5. **Analytical model.** A general model over the stored data, aware of eras:
   - conference membership and classification per season
   - coverage flags per fact
   - venue `GEOGRAPHY`
   - an "as of week" view for every page

### App (v1 pages)

6. **Playoff odds.** A Monte Carlo simulator (regular season, conference championships, CFP selection, 12-team bracket) running as parallel Python SET UDFs (numpy, vectorized across simulations) on all cores. What-if toggles filter the stored simulations, the rooting guide ranks other games by leverage, and arrows show movement since last week.
7. **This week's predictions + model scorecard.** Predicted score and win % for every game; after the refresh, last week's predictions are graded against actual results.
8. **Biggest upsets.** This week's upsets ranked against all of history: pregame win probability (from spreads) since 2013, Elo-based probability from the per-game pregame Elo that `/games` carries back to 1890.
9. **Head-to-head.** The all-time series between two teams, fetched on demand through the Virtual Schema (one call per click), the next meeting with our prediction, and a live game preview.
10. **Conversational analyst.** A chat panel (Claude + Exasol MCP, read-only) that shares app state, can drive what-if toggles, answers ad-hoc questions across the whole model, and shows its SQL.
11. **Engine benchmark.** Sims per second and wall time across sim counts, batch sizes and deployments (laptop vs. AWS).

## Out of Scope

- **Live in-game data:** in-progress scores, in-game win probability, live odds during games. It may come back as a recorded game-day replay later.
- Broad scans through the API. Data the app scans broadly is stored; the API is only queried selectively (D3, D15).
- **History / dynasties page** (backlog), and other analyses beyond the v1 pages.
- Predicting the real CFP committee accurately; full conference tiebreaker rules (simplified).
- Betting picks or advice. Lines are ingested as data only.
- Writes through the Virtual Schema. Production hosting, multi-tenant use, auth, mobile apps.
- Redistributing CFBD data: no raw data, dumps or recorded responses in the repository.

## Domain Glossary

| Term | Definition |
|------|------------|
| CFBD | CollegeFootballData.com, the source REST API (v2, Bearer API key, OpenAPI 3 spec) |
| Endpoint table | A virtual table generated from one CFBD GET path |
| Pushdown parameter / required filter | A CFBD query parameter filled from a SQL predicate / one CFBD requires; a query without it is rejected |
| Fan-out / call budget | One SQL query becoming several parallel API calls / the maximum calls one query may spend |
| On-demand endpoint | An endpoint served only live through the Virtual Schema, never stored (per-game or per-entity detail) |
| Fetch log | Per-request record (endpoint, params, status, ms, rows) that powers the SQL↔HTTP inspector |
| Load plan | `loader/load-plan.yaml`: per entry the table, slicing, seasons, known gaps, refresh class (`static` / `weekly`), profile (`demo` / `full`) and priority, plus the call budget |
| Refresh | `REFRESH_WEEK(season, week, season_type)`: the weekly step chain from the API to the simulations |
| As-of (season, week) | The point in time a page or model sees; only results before it are known |
| Era / coverage flag | Classification of a team-season (FBS/FCS/historic division) / whether a fact (e.g. plays) is complete for a game |
| Sim / run / sim batch | One playout of the rest of a season / N sims for one model snapshot / the slice one UDF call handles |
| Predicted margin / total | The model's expected point difference / combined points for a game; the sims sample around them |
| Upset magnitude | `1 − pregame win probability of the winner` |
| Conditional odds / effective sample / forced re-sim | Odds among sims matching fixed results / the number of matching sims / a new run with fixed results applied |
| Leverage / rooting guide | Swing in a team's odds between a game's two outcomes / remaining games ranked by leverage |
| Resume score / auto-bid | The SQL stand-in for the committee ranking / guaranteed CFP spot (4 power conference champions + highest-ranked G6, 2026 rules) |
| Reproducibility | A run's results are fully determined by its inputs and seed (seeded per batch) |

---

## Tech Stack

| Layer | Technology | Purpose |
|-------|------------|---------|
| Database | Exasol Personal (local deployment on a laptop-class machine; AWS for the scale-out demo) | Storage, compute, Virtual Schema, UDFs |
| VS adapter | Python 3 adapter script + Python fetch UDF, generated from the pinned CFBD OpenAPI spec | API → SQL |
| Orchestration | Lua scripting programs in Exasol (`EXECUTE SCRIPT`), wrapped by host Python | Backfill, `REFRESH_WEEK`; later phases add steps and sim runs |
| Sim engine | Python 3 SET UDF (builtin SLC, numpy) | Simulation |
| Backend | Python, FastAPI, pyexasol | App API, chat orchestration |
| Frontend | React + TypeScript (Vite) | App |
| AI | Claude API + Exasol MCP server (read-only user) | Conversational analyst |
| Testing | pytest (unit + integration against local Exasol Personal) | Logic, reproducibility, VS behavior |
| Data access | CFBD free tier (1k calls/month), the design target | API quota |

## Commands

Every target runs through `uv run` from the repo root. Database targets check that the deployment is reachable first, and `TARGET=local|aws` (default `local`) selects the Exasol Personal deployment (for example `make deploy TARGET=aws`). `ARGS="…"` passes options to `deploy` and `grants`.

```bash
make generate     # catalog + mirror DDL from the pinned OpenAPI spec and vs/catalog-overrides.yaml
make bundle       # adapter + fetch UDF script bodies (build/sql/), size-checked
make test         # unit + cli, no DB, no network
make test-it      # integration against local Exasol Personal: mock CFBD + replay, TEST_ namespace, 0 CFBD calls
make test-network # the one network-marked test: DoH + TLS handshake from the UDF sandbox, 0 CFBD calls
make lint         # ruff check, ruff format --check, mypy --strict (vs, scripts, tests)
make coverage     # unit + cli, >= 90 % line coverage for vs/cfbd_vs and vs/gen
make deploy       # build the bundle; create/refresh schemas, adapter, fetch UDF, CFBD_VS (as sys)
make grants       # create the reader role CFB_READER with SELECT ON CFBD_VS
make dns-check    # resolve the API host from a UDF + verified TLS handshake, 0 calls (gate for live steps)
make record PATTERNS=… CONFIRM=yes   # record live responses for replay (capped by cache/live-calls.jsonl)
make db-check     # is the selected database reachable
```

Loader targets (Phase 2); all take `TARGET=local|aws`, and the live ones print the call estimate and the quota first and need `CONFIRM=yes`:

```bash
make deploy [LOADER_CONFIG="k=v,..."]     # also deploys the loader with vs/live/loader-config.yaml's target settings; LOADER_CONFIG wins, until the next deploy
make backfill PROFILE=demo|full [FROM=year] [MAX_CALLS=n] [CONFIRM=yes]   # CFB_INFRA.BACKFILL; MAX_CALLS=0 is a dry run
make refresh SEASON=y WEEK=w [SEASON_TYPE=postseason] [CONFIRM=yes]        # CFB_INFRA.REFRESH_WEEK; prints the timeline
make quota                                                                  # CFB_META.QUOTA and the ledger room, 0 calls
make snapshot NAME=n                       # private gzip CSV copy of CFB_CORE + load log + fetch log
make restore NAME=n CONFIRM=yes [FORCE=yes]  # atomic restore, 0 calls; also the rehearsal reset
make snapshots                             # list snapshots
```

`LEDGER=off` turns the call ledger off for people who run their own key. Planned with later phases (not in the Makefile yet): `make build` (frontend bundle), the model, simulation and app targets. Frontend lint (eslint) joins `make lint` with the app.

## Project Structure

Planned:

```
exasol-cfb-demo/
├── specs/              # speq mission, architecture, feature specs, plans
├── docs/               # roadmap + decision log, data availability, demo script, benchmark, spikes
├── vs/                 # OpenAPI spec (pinned), generator, Python adapter + fetch UDF
├── sql/                # DDL: infra, core, model, sim, predictions, api views, grants
├── loader/             # load plan, backfill, REFRESH_WEEK
├── engine/           # Python sim engine (UDF + pure-Python core for unit tests)
├── app/{backend,frontend}/
├── bench/
└── Makefile
```

## Architecture

See `specs/architecture.md`.

## Constraints

- **Secrets:** the CFBD key exists only in the Exasol CONNECTION `CFBD_API`. Only `CFB_INFRA.CFBD_FETCH` can read it (script-bound `ACCESS`). The app, the MCP user, the repo, logs, VS properties and pushdown SQL never contain it.
- **CFBD terms:** no redistribution of raw API data (derived outputs are fine). Credit "Data provided by CollegeFootballData.com". Never present the data as official NCAA/CFP data. Public release under exasol-labs (MIT).
- **Quota:** the demo MUST run on CFBD's free tier (1k calls/month, D13). A `demo` load profile (217 calls once, including all games back to 1869; 4 per weekly refresh) makes the app fully working. The `full` profile (all endpoints, all history, about 2k calls) fills in from leftover monthly budget, resumably and newest first. Rebuilt databases restore from a private snapshot with zero calls (D11, D45). The in-database `PROJECT_BUDGET` and the call ledger enforce the budget (D42).
- **Engine:** Python only (D16). Runs MUST be reproducible from inputs + seed.
- **Runs on a laptop:** everything in the demo profile, including 100k-sim runs, MUST work on Exasol Personal local on a laptop-class machine (D17).
- **Demo:** MUST run offline in replay mode with the same look as live.
- **Performance** (proposed, validated in the benchmark): a what-if query under 1 s p95 at 1M sims; `REFRESH_WEEK` including 100k sims under 2 min locally; an on-demand VS lookup under 2 s.

## External Dependencies

| Service | Purpose | Failure Impact |
|---------|---------|----------------|
| CollegeFootballData API v2 | All source data | No refresh or on-demand lookups. Stored data and replay mode keep everything else working |
| Exasol MCP server | Chat tool access | Chat disabled, core app unaffected |
| Claude API | Chat reasoning | Chat disabled, core app unaffected |
| Python 3 SLC (builtin) | UDF runtime | Simulations and VS unavailable |
