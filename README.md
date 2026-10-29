<div align="center">

# exasol-cfb-demo

**College football analytics on Exasol, where the data source is a REST API.**

[![Exasol](https://img.shields.io/badge/Exasol-Personal%202026.x-1ba0e2.svg)](https://github.com/exasol/exasol-personal)
[![Python](https://img.shields.io/badge/python-3.12-3776AB.svg)](https://www.python.org/)
[![spec|driven](https://img.shields.io/badge/spec-driven-blue)](./specs/)

</div>

## What it solves

Most "analytics on an API" projects start with an ingestion pipeline: a script
that pages through the API, writes files, and loads them into a warehouse. The
pipeline becomes the product, and every new endpoint is more pipeline.

`exasol-cfb-demo` skips that step. A **Virtual Schema generated from the
[CollegeFootballData](https://collegefootballdata.com) OpenAPI spec** exposes
the whole CFBD API as SQL tables in Exasol. You `SELECT` from an endpoint, and
the adapter turns your `WHERE` clause into HTTP requests, runs them in parallel
from a Python UDF, and returns rows:

```sql
SELECT HOME_TEAM, AWAY_TEAM, HOME_POINTS, AWAY_POINTS
FROM   CFBD_VS.GAMES
WHERE  P_YEAR = 2026 AND P_WEEK = 9 AND P_TEAM = 'Auburn';
```

Everything else is built on top of that one idea, inside the database:

- **All history persisted.** A loader stores every season since 1869 in
  `CFB_CORE`. Stored slices are then served by the same Virtual Schema at
  **zero API calls** (read-through).
- **One call per week.** `EXECUTE SCRIPT CFB_INFRA.REFRESH_WEEK(2026, 9, 'regular')`
  refetches the open week (4 API calls), rebuilds the model, reruns the
  simulation and freezes next week's predictions.
- **Monte Carlo in the database.** Playoff odds come from up to a million
  simulated seasons in parallel, numpy-vectorized Python SET UDFs, spread
  across every core and node.
- **A web app on top**, reading only a curated `CFB_API` schema: a US map of
  every FBS team, team pages, playoff odds with what-if and rooting guide,
  predictions with a scorecard graded against picks made in advance, the
  biggest upsets, head-to-head history, and a SQL↔HTTP inspector that shows
  what each query cost.

```
CFBD API ◀── HTTPS ── CFBD_FETCH (Python UDF) ◀── CFBD_ADAPTER (Virtual Schema)  ◀── SELECT … FROM CFBD_VS.*
                                                        │
                    BACKFILL once · REFRESH_WEEK weekly │ on-demand (previews, box scores)
                                                        ▼
         CFB_CORE (all history) ─▶ CFB_MODEL (as-of model) ─▶ CFB_SIM · CFB_PRED ─▶ CFB_API
                                                                                     │
                                                                  FastAPI + React app ┘
```

It runs on [**Exasol Personal**](https://github.com/exasol/exasol-personal),
the free Exasol edition: a single-node local deployment on a laptop, or a
multi-node deployment in your own cloud account.

## Quick Start

### Prerequisites

- **[Exasol Personal](https://github.com/exasol/exasol-personal)** 2026.x
  (local deployment needs podman), or any Exasol 2026.x database.
- **A free CFBD API key** from <https://collegefootballdata.com/key>. The demo
  is designed for the free tier (1,000 calls per month).
- **[uv](https://docs.astral.sh/uv/)** (Python 3.12 is installed for you) and
  **Node.js ≥ 22.12** for the frontend.

### 1. Start Exasol Personal

```bash
exasol install local -d local     # first time: installs the local deployment named "local"
exasol start -d local             # every time after that
exasol status -d local
```

The tooling reads the `sys` password at run time from
`~/.exasol/personal/deployments/local/secrets.json`. You never copy it
anywhere. For an Exasol Personal deployment on AWS, add `TARGET=aws` to any
`make` command below (deployment `default`, TLS certificate pinned).

### 2. Deploy the Virtual Schema and the loader

```bash
make deploy        # adapter, fetch UDF, CFBD_VS, CFB_META, CFB_STAGE, loader scripts
```

Then store your API key in Exasol. Type this into a SQL client (DBeaver, the
Exasol VS Code extension, `exapump sql`), never on a shell command line:

```sql
CREATE CONNECTION CFBD_API
  TO 'https://api.collegefootballdata.com'
  USER ''
  IDENTIFIED BY '<your CFBD API key>';
```

The key lives **only** in that connection. No config file, environment
variable, log or app process ever holds it. Finish the deploy:

```bash
make deploy        # again: syncs your tier's features from the free /info endpoint
make grants        # role CFB_READER with SELECT on CFBD_VS
make dns-check     # name resolution and TLS from inside the UDF sandbox (0 API calls)
```

Check it from SQL. `INFO` is free:

```sql
SELECT * FROM CFBD_VS.INFO;   -- your tier and remaining calls this month
```

### 3. Load the history

```bash
make backfill PROFILE=demo                  # prints the call estimate and the quota, then stops
make backfill PROFILE=demo CONFIRM=yes      # ~217 calls, once; resumable
```

Every command that can spend calls prints its estimate first and refuses
without `CONFIRM=yes`. If you already have a snapshot, `make restore NAME=<snapshot> CONFIRM=yes`
rebuilds the database at **zero** calls instead.

### 4. Build the model, simulate, predict

```bash
make model                                     # CFB_MODEL: era-aware dims, facts, as-of views
make sim                                       # CFB_SIM / CFB_PRED objects and the engine UDFs
make simulate SEASON=2026 WEEK=8 SIMS=100000   # playoff odds from the state after week 8
make predict  SEASON=2026 WEEK=8               # freeze week 9's picks before kickoff
make api                                       # CFB_API serving views and the app/MCP roles
```

All five run entirely on stored data: **0 API calls**.

### 5. Start the app

```bash
make app-env       # ~/.cfb-demo/app.env (0600) with random app passwords, never in the repo
make app-user      # read-only database users for the app and the SQL console
make logos         # cache team logos in ~/.cfb-demo/logos (never committed, never hotlinked)
make app-build     # build the React frontend
make app-serve     # API + frontend in one process
```

Open **<http://127.0.0.1:8000/>**. Start at the map, click a team, then use
the league tabs: odds, predictions, upsets, head-to-head.

`make app` runs a development setup instead (uvicorn `--reload` plus Vite on
`:5173`). `make app-preview` shows the frontend on sample fixtures with no
database at all.

### 6. Every week

```bash
make refresh SEASON=2026 WEEK=9               # estimate first
make refresh SEASON=2026 WEEK=9 CONFIRM=yes   # 4 calls: games, win probabilities, SP+, polls
```

or, from SQL:

```sql
EXECUTE SCRIPT CFB_INFRA.REFRESH_WEEK(2026, 9, 'regular');
```

The result is a step timeline (check, one fetch per table, model, simulation,
predictions) with seconds, rows and calls per step. The app shows the same
timeline and can start the refresh itself.

## How it works

### The Virtual Schema

`make generate` turns the pinned OpenAPI spec (`vs/openapi/cfbd-openapi-5.33.0.json`)
and a reviewed overrides file into a catalog: one table per endpoint, nested
objects flattened, arrays of objects as child tables, every query parameter as a
`P_<PARAM>` column. The adapter (`CFBD_ADAPTER`, a Python adapter script)
handles each pushdown like this:

1. Intersects the predicates per parameter. A contradiction returns zero rows
   with no request.
2. Enforces the API's required-parameter rules and tier gating at planning
   time, with an error that names the rule.
3. Expands year ranges and `IN` lists into requests, and refuses anything over
   the per-query call budget (`MAX_CALLS_PER_QUERY`).
4. Emits SQL that **unions stored slices from `CFB_CORE` with live fetches for
   the missing ones**, so stored data costs nothing.

`CFBD_FETCH`, a Python SET UDF, makes the requests through a bounded thread
pool, retries 429s and 5xxs, and adds `REQ_*` columns to every row: slice,
source (`STORED`/`LIVE`/`REPLAY`), status, attempts, milliseconds. `EXPLAIN VIRTUAL`
shows every planned request before you spend a call.

### Persistence and refresh

The loader is a set of Lua scripts in `CFB_INFRA` over declarative SQL views.
`loader/load-plan.yaml` decides which endpoints are stored, how they are sliced,
and when a slice is final. Each batch is one fan-out query through `CFBD_VS`,
staged in `CFB_STAGE`, then swapped into `CFB_CORE` together with its load and
fetch log rows in one transaction. `REFRESH_WEEK` runs that for the open week
and then the registered steps (model, simulation, predictions).

Spend is guarded in three places: the per-query budget in the adapter, the
monthly budget and refresh reserve in the loader, and a host-side call ledger
(`cache/live-calls.jsonl`) with monthly caps that every live command checks
before it runs.

### Model, simulation and predictions

- **`CFB_MODEL`** turns raw mirrors into an era-aware model with *as-of* views,
  so any past week can be replayed exactly as it looked then.
- **The score model** predicts a margin and total per remaining game from
  in-SQL ratings with an SP+ prior. Its σ is calibrated on 2014–2025.
- **The engine** is one Python SET UDF per output, numpy-vectorized across the
  simulations of a batch. Batches fan out with `GROUP BY batch_id` across
  cores and nodes. Each simulated season plays the remaining schedule,
  standings, conference championships, rankings, the 12-team selection and the
  bracket. A run is reproducible from its seed and batch size.
- **`CFB_PRED`** freezes each week's predictions before kickoff, so the
  scorecard only ever grades picks made in advance.

### The app

FastAPI backend, React frontend. The backend connects as a read-only user that
sees only `CFB_API`. On-demand data (game previews, the console's live
queries) goes through guarded `CFB_API_LIVE` views and the same call ledger.
The app never calls CFBD itself, and never holds the key.

## Rules worth knowing

- **Never redistribute CFBD data.** The API terms forbid it. Raw responses,
  snapshots and logos stay on your machine (`~/.cfb-demo/`, `cache/`) and are
  never committed. Test fixtures are synthetic.
- **The key lives only in the `CFBD_API` connection.** Readers reach it only
  through the Virtual Schema. Never grant `ACCESS ANY CONNECTION`.
- **Exasol Personal nano UDFs have no DNS.** The fetch UDF falls back to DNS
  over HTTPS (`DOH_URL`, default `https://1.1.1.1/dns-query`) only when the
  system resolver fails. TLS still verifies the real host name.

## Documentation

- [docs/user-guide.md](docs/user-guide.md): deploying, the key, querying the Virtual Schema, read-through, replay, the loader, snapshots
- [docs/model-dictionary.md](docs/model-dictionary.md): every `CFB_MODEL` object and column
- [specs/architecture.md](specs/architecture.md): layers, schemas, adapter, loader and engine contracts
- [specs/mission.md](specs/mission.md): scope and commands
- [docs/roadmap.md](docs/roadmap.md): phases and decisions
- [docs/cfbd-api-reference.md](docs/cfbd-api-reference.md): findings about the CFBD API

## Development

```bash
make test          # unit + CLI tests, no database, no network
make lint          # ruff, ruff format --check, mypy --strict
make coverage      # ≥ 90 % line coverage for vs/cfbd_vs and vs/gen
make test-it       # integration tests against the local DB (0 CFBD calls, mock sidecar)
make app-test      # frontend tests
```

Integration tests deploy into a throwaway `TEST_` namespace with its own users
and a mock CFBD server, and drop everything at the end. They never touch the
real `CFBD_API` connection.

---

Data provided by [CollegeFootballData.com](https://collegefootballdata.com).
This project is not affiliated with the NCAA or the College Football Playoff.
