# exasol-cfb-demo — Project Rules

A college football analytics platform on Exasol: an OpenAPI-generated Virtual Schema over the whole CFBD API, all history persisted, one `REFRESH_WEEK` call per week, Monte Carlo playoff odds in Python SET UDFs, a FastAPI + React app and an MCP chat. Product scope: `specs/mission.md`. Architecture: `specs/architecture.md`. Phases and decisions (D…): `docs/roadmap.md`. **Current status, open work and dates live in `specs/_plans/HANDOVER.md`, not here**: this file holds only rules that stay true.

## Repository and Git

- One working folder, branch `main`, pushed to `github.com/nconforti93/exasol-cfb-demo` (**public**). No sibling worktree folders per phase.
- Commit locally when work is verified; push only when the owner asks. Before a push, check what is being published: no secrets, no raw CFBD data, no machine-specific hosts or certificate pins.
- Untracked `spikes/`, `docs/spikes/s3*`, `docs/spikes/s4*`, `specs/_plans/add-engine-spikes/` and `.serena/` belong to a parallel session: never add, commit or delete them.
- Commit trailer: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Spec-driven development (speq)

- Every feature goes through `/speq:plan` → `/speq:implement` → `/speq:record`. `specs/` defines only user-visible behavior (domains `cfbd-vs/`, `loader/`, `model/`, `sim-engine/`, `api/`, `app/`, `chat/`); plans in progress are in `specs/_plans/`.
- Testing tiers, toolchain, CI, deployment and licensing are not specs; they live here.
- Decide routine implementation choices yourself and record them in the plan's `decision-log.md`.

## Layering

- `CFB_MODEL` reads only stored data (`CFB_CORE`, `CFB_META` plan views). `CFB_SIM`/`CFB_PRED` read `CFB_MODEL`. `CFB_API` reads `CFB_MODEL` and `CFB_SIM`. The app and the MCP/chat user read only `CFB_API`; `CFB_API_LIVE.QUOTA` is app-only.
- **Current-season data is read from `CFB_CORE`, never through `CFBD_VS`:** current-season slices are never final, so a VS query on them is a live call. Only the loader reads the current season through the VS.
- Endpoint classes (D15): `stored` (scanned broadly; in `CFB_CORE`), `federated` (selective on-demand queries; read-through serves stored slices at 0 calls), `unavailable` (tier-gated). Never route a broad scan through the API: reclassify the endpoint as stored instead.
- A paid game preview uses the app's guarded operator path with a literal `P_GAME_ID`. Do not add a `GAME_PREVIEW` view: Exasol does not push the required filter through a view.

## Data, secrets and quota

- **The CFBD key lives only in the Exasol CONNECTION `CFBD_API`**, created by the owner with plain SQL (D18, `docs/user-guide.md`). Claude never handles it. Only `CFB_INFRA.CFBD_FETCH` reads it; readers reach it only through the virtual schema. It never goes in `.env`, SQL files, VS properties, logs, fixtures or frontend code. Never grant `ACCESS ANY CONNECTION`, or `ACCESS` on `CFBD_API` to anything else.
- `sys` owns the real deployment ([20]) and needs no connection grant. The `TEST_` namespace has its own owner with the script-bound `GRANT ACCESS ON CONNECTION … FOR SCRIPT CFB_INFRA.CFBD_FETCH`; `CREATE OR REPLACE` of `CFBD_FETCH` drops that grant even for identical text, so deploy replaces the UDF only when its body changed, and `make grants ARGS="--prefix TEST_"` must follow a deploy that replaced it.
- **Deploy order:** `make deploy` → create `CFBD_API` → `make deploy` again (syncs `API_FEATURES` from the free `/info`) → `make grants` (`CFB_READER` on `CFBD_VS`, `CFB_APP` on `CFB_API` and `CFB_API_LIVE`, `CFB_MCP` on `CFB_API` only).
- **Other secrets** (app, console and chat passwords, `ANTHROPIC_API_KEY`) live in `~/.cfb-demo/app.env` (0600, `make app-env`, read through `vs/host/appenv.py`), never in the repo or OneDrive. A `*_PASSWORD` or `*_API_KEY` in the repo `.env` stops the app. Database passwords are read at run time from `~/.exasol/personal/deployments/<local|default>/secrets.json`. Never print or log a value, and never extract a CONNECTION secret from the database.
- **Never commit or share CFBD data** (terms forbid redistribution): no raw responses, dumps, replay recordings, snapshots or logos. They stay in `cache/` (gitignored), BucketFS, `~/.cfb-demo/snapshots/` and `~/.cfb-demo/logos/`. Test fixtures are small and synthetic. Credit "Data provided by CollegeFootballData.com" in the app footer and README.
- **Quota:** free tier 1,000 calls/month. Never re-backfill a rebuilt database: `make restore` from a private snapshot (D11, D45). Live calls only from `make backfill` / `make refresh` (estimate first, `CONFIRM=yes`), the app's guarded refresh and preview, and the recorder. Integration tests make zero calls. Pinned spec: `vs/openapi/cfbd-openapi-5.33.0.json`.
- **Live ledger:** every live call is recorded in `cache/live-calls.jsonl` (`LEDGER_PATH` overrides). Caps are in `vs/live/ledger.yaml`: `plans` per plan over all months, `months` per UTC month across plans, checked against `/info` used calls plus the estimate; a month without an entry refuses live calls. **The owner edits that file, never Claude.** `/info` is free. Runs record `<KIND>#<run id>` with the larger of measured calls and the `/info` delta, also after Ctrl-C or a timeout ([47]); a mock connection (`CFBD_API_MOCK`) is not ledgered ([49]). `LEDGER=off` is only for people with their own key.
- **Two budget guards (D42):** `CFB_META.QUOTA` in the database (`PROJECT_BUDGET = LEAST(MONTHLY_LIMIT, monthly_call_budget)`; `BACKFILL_SPENDABLE = REFRESH_SPENDABLE - refresh_reserve`), and the ledger. Per-target settings are in `vs/live/loader-config.yaml` and applied by every `make deploy TARGET=<t>` (`aws`: budget = the ledger's cap for the deploy's month), so a redeploy never lifts the AWS budget; redeploy once after a month change. A live action may start only when `estimate × MAX_ATTEMPTS` (4) fits both the ledger plan and `REFRESH_SPENDABLE`.

## Toolchain and make targets

- Python 3.12 with uv (`uv.lock`, `package = false`; groups `dev`, `app`, `chat`). Use a native venv: `UV_PROJECT_ENVIRONMENT=$HOME/.cache/cfbvenv` (a venv on OneDrive is very slow). Every target runs through `uv run` except `db-check`. Ruff line length 100; mypy strict over `vs`, `loader`, `model`, `engine`, `analyses`, `scripts`, `tests`, `app/backend`.
- `vs/cfbd_vs` is stdlib-only (embedded in the adapter and fetch UDF). numpy is pinned to 1.26.4, the version in the builtin SLC (D19).
- Frontend: `app/frontend`, Node ≥ 22.12, exact pins, `package-lock.json` is the source of truth. `make app-deps` installs into `~/.cache/cfb-app-frontend` and links `node_modules` (OneDrive is too slow).
- **Targets** (details in `specs/mission.md` § Commands; database targets run `db-check` first and take `TARGET=local|aws`, default `local`; `ARGS` passes options through):
  - VS: `generate`, `bundle` (fails from 1,990,000 characters; Exasol's script cap is 2,000,000), `deploy`, `grants`, `dns-check`, `record`.
  - Loader: `backfill`, `refresh`, `quota`, `snapshot`, `restore`, `snapshots`.
  - Model and engine: `model`, `model-check`, `model-refresh`, `model-docs`, `model-perf`, `sim`, `sim-fit`, `simulate`, `what-if`, `predict`.
  - Serving: `api`, `api-docs`.
  - App: `app` (dev pair), `app-serve`, `app-preview`, `app-fixtures`, `app-env`, `app-user`, `app-build`, `app-test`, `app-lint`, `app-e2e`, `app-perf`, `logos`.
  - Quality: `test`, `test-it`, `test-network`, `lint`, `coverage`, `release-check-fast`, `demo-freeze`.

## Testing

- Tiers: `tests/unit` (pure, no DB, no network), `tests/cli` (entry points with fakes), `tests/it` (marker `it`, Exasol Personal local only). `make test` runs unit + cli + frontend; `make lint` runs ruff, mypy, the frontend lint and the freeze check last; `make coverage` needs ≥ 90 % lines for `vs/cfbd_vs`, `vs/gen` and `loader`.
- Fixtures are synthetic (`tests/synthetic/`). Tests never read the real `~/.cfb-demo/app.env` and never reach Anthropic (both enforced in `tests/conftest.py`).
- **Integration tests** deploy a throwaway `TEST_` namespace (owner `CFB_TEST_OWNER`, random passwords in memory) plus a mock CFBD sidecar in the DB container's network namespace, and drop it all at the end. They must never reference the real `CFBD_API`. **Zero CFBD calls.** The mock must be restarted after `exasol stop/start -d local` (the harness does it). `LOADER_CONFIG.NOW_OVERRIDE` (test clock) exists only in `TEST_`.
- **One integration run at a time:** sessions sharing the local DB serialize with `flock <lock file> make test-it`, and never run while a benchmark has the database.
- `make test-network` is the only network test (DoH from the UDF sandbox plus a TLS handshake, no HTTP request, 0 calls); `make dns-check` is the same check as a live gate.

## Loader (`loader/`)

- `loader/load-plan.yaml` decides everything that costs calls. **Run `make deploy` after every edit**: it validates against the catalog and rewrites plan tables, views and scripts (an invalid plan changes nothing). `uv run python -m loader.plan --check --estimate` prints the estimate at 0 calls. Never reorder entries without a redeploy: the position is `ENTRY_ID`.
- Entry points are Lua: `EXECUTE SCRIPT CFB_INFRA.BACKFILL(run_profile, from_season, max_calls)` and `CFB_INFRA.REFRESH_WEEK(season, week, season_type)`. Exasol has no `CALL`, the argument count must match, and **never use `WITH OUTPUT`** (it discards the result table).
- **Lua rules:** every statement through `pquery`, every entry function through `pcall`, no bare `query(`; on error `ROLLBACK`, log, `COMMIT`, then `error(msg, 0)`. Cut strings at a character boundary before `exit()` or a VARCHAR column. Result column names must not be reserved words. The bundler double-quotes identifiers; `::name` is for deploy-time constants only, values are bound as `:name`. Lua runs with the caller's rights.
- The loader never switches `READ_THROUGH` and never issues `ALTER VIRTUAL SCHEMA` (properties are global). Batches keep `REQ_SOURCE <> 'STORED'`.
- **Time:** every loader timestamp uses `CONVERT_TZ(CURRENT_TIMESTAMP, SESSIONTIMEZONE, 'UTC')` (Personal runs in `CET`); not `SYSTIMESTAMP` or `POSIX_TIME`.
- **Run lock:** a `RUNNING` row in `CFB_META.LOAD_RUN`. Host processes take it with `loader.runlock.run_lock`; only the holder's session releases it. `make deploy`, `make sim` and `make api` hold `DEPLOY`. A registered refresh step declares exactly `(run_id, season, week, season_type)` and must not take the lock.
- **Failures:** a failed batch is retried slice by slice; a slice failing alone is quarantined after `quarantine_after` runs (clear with `DELETE FROM CFB_META.LOAD_FAILURES`); a backfill stops after 2 consecutive failed batches; a run overshoots `max_calls` by at most 3. Orphan slices (keys the plan no longer has) block their table until migrated or deleted. To re-fetch a slice, set `LOAD_LOG.IS_FINAL = FALSE` and run a backfill.
- **Snapshots** hold CFBD data: `~/.cfb-demo/snapshots/<name>/` (gzip CSV), refused inside the repo or OneDrive, never shared. `make restore` needs `CONFIRM=yes`, refuses a column difference always and a catalog-hash difference unless `FORCE=yes`, and makes 0 calls. A DOUBLE with more than 15 significant digits comes back rounded to its text form.

## Model (`sql/model/`)

- Stored data only, zero calls; one SQL file per object, deployed in one transaction by `vs/host/sqlobjects.py`. Lint: every object and column has a comment, object comments start "One row per ...", DDL names are unquoted upper case and not reserved, query bodies quote identifiers. `make model-docs` regenerates `docs/model-dictionary.md`.
- `GAME`, `SEASON_WEEK`, `TEAM`, `RATING_SP`, `RANKING`, `TEAM_MEMBERSHIP`, `TEAM_SEASON` and `AS_OF_POINT` are tables rebuilt by `CFB_INFRA.REFRESH_MODEL` (refresh step `MODEL`, after backfill and restore, `make model`, `make model-refresh`). After a direct `BACKFILL`, a manual `CFB_CORE` edit or a test that edits stored rows, run `make model-refresh`. Everything else is a view.
- **As-of points** are `(AS_OF_SEASON, AS_OF_WEEK_SEQ)` (0 preseason, n regular, 100 + n postseason), horizon "known before the next week's first kickoff". **Filter as-of views with literal points** (read `CURRENT_AS_OF` first): joining to it evaluates every point. Read `GAME` at most once per query.
- Venues are `GEOMETRY(4326)` (no `GEOGRAPHY`); engine-facing values are fixed-scale `DECIMAL`. ERROR-level `MODEL_CHECKS` stop the refresh chain; warnings go to `CFB_META.MODEL_CHECK_LOG`.

## Simulation and predictions (`engine/`, `sql/sim/`)

- One engine: Python, numpy-vectorized across the sims of a batch (D16), one SET UDF call per batch with `GROUP BY batch_id`. The contract (inputs, RNG, outputs) is the `sim-engine/` spec: inputs are fixed-scale `DECIMAL`, RNG is `Philox(SeedSequence([run_seed, batch_id]))`, and a run is reproducible only for the same `sims_per_batch` and numpy version.
- `make sim` deploys `CFB_SIM` and the Lua scripts `SIMULATE`, `PREDICT`, `REFRESH_SIMULATE` and `REFRESH_PREDICT` under the `DEPLOY` lock; the refresh scripts are registered steps of `REFRESH_WEEK`. Refresh defaults per target are in `engine/sim-config.yaml`. `make simulate`, `what-if` and `predict` are manual runs (0 calls).
- **Predictions are frozen before kickoff**; the scorecard only grades picks made in advance. Never re-freeze a week whose games have started.

## Serving API (`analyses/`, `sql/api/`)

- `make api` lints and deploys `CFB_API` and `CFB_API_LIVE`; `make deploy` installs them after the model and simulation. Every view and column has a comment and view comments begin with the row grain; `make api-docs` regenerates `docs/api-dictionary.md` without a database.
- `CFB_API` is a thin stored-data layer with no own tables or refresh step. Point-grain views carry `AS_OF_SEASON` and `AS_OF_WEEK_SEQ`; consumers read `CURRENT_POINT` first and filter with those values as literals. The chat role has no `CFB_API_LIVE` grant.

## App (`app/`)

- FastAPI backend (`app/backend`) serving the built React frontend (`app/frontend/dist`) on `127.0.0.1:8000`. The app never calls CFBD directly and never holds the key.
- Database users (`make app-user`): `CFB_APP_WEB` (role `CFB_APP`) for reads, `CFB_CONSOLE_WEB` (role `CFB_READER`) for the SQL console. The owner **operator path** (refresh, previews, fetch log) is on only while the server listens on loopback and runs only allowlisted statements.
- Every live action goes through the ledger and the spend guard above, confirms the estimate, and refuses rather than queue when a refresh or another process holds the ledger lock. A 0-call replay preview is served even while a refresh runs. **Spend, lock and ledger code gets a careful review on every change.**
- Stored reads are cached in-process (`app/backend/readcache.py`); the cache is cleared when the timeline poll sees the latest loader run change. Refresh planning, the timeline and the quota always read the database. The SQL↔HTTP inspector marks cached entries.
- To restart, kill the `make`/`scripts.app serve` PIDs explicitly (`pkill -f` with that pattern kills the calling shell).

## Chat (`app/backend/chat/`)

- A tool loop over `CFB_API` for a read-only user with role `CFB_MCP` (no `CFB_API_LIVE`). `run_sql` statements pass `chat/sqlguard.py` before the database.
- Build and test with the scripted model (`chat/scripted.py`): 0 Anthropic calls. Call the real Claude API only when the owner asks, with `ANTHROPIC_API_KEY` from `~/.cfb-demo/app.env` and an explicit spend cap.
- Rehearsal questions (`chat/rehearsal/questions.yaml`) must use the real `CFB_API` column names (`docs/api-dictionary.md`); a unit test checks them.

## Demo rules

- **Demo freeze:** while `demo/freeze.yaml` is in force, `vs/catalog/`, `sql/core/`, `sql/meta/load_log.sql`, `sql/meta/fetch_log.sql` and `loader/load-plan.yaml` must keep their pinned content digests (the rehearsal kits' snapshots depend on them). `make lint` fails on any change. The only way out is an owner-approved migration that re-fingerprints the snapshots, then `make demo-freeze FROM= UNTIL= FORCE=yes`. Pins are taken from the tree deployed on AWS.
- **Demo week** (dates in the handover): no `make deploy` on the real local namespace outside the rehearsal procedure.

## Environment

- The repo path contains spaces (OneDrive): quote paths, and pass `CURDIR` explicitly to make if a target breaks.
- **Runtime (D17):** Exasol Personal 2026.x, local deployment `local` (nano, podman, 127.0.0.1:8563) and the AWS deployment `default` (3 nodes), selected with `TARGET=local|aws` (`vs/host/target.py`). `aws` pins the TLS fingerprint from `deployment.json` and refuses to connect unverified. `EXASOL_DSN` overrides host and port only.
- **AWS: deploy and run only. Never stop, start, resize or destroy it.** Drive the local database only with `exasol start/stop -d local`. Never run `exakit` runtime commands or plain `exakit mcp-doctor` (`deployments/default` is the AWS instance); only `exakit mcp-doctor --json` is safe.
- Touch only `CFB_*`, `CFBD_VS`, `TEST_*` and `PERF_*` schemas; leave everything else (e.g. `CFB_DIAG`, `VECTOR_DEMO`, `VEC_SMOKE`, `PREPROC_RT`) alone.
- **Check Exasol reserved words** (`vs/gen/exasol_reserved_keywords.txt`) before naming anything; past hits: `SOURCE`, `NAMES`, `RESULT`, `PROFILE`, `STATE`, `FULL`, `LOG`.
- **UDF DNS on Personal nano:** the UDF sandbox has empty `/etc/resolv.conf` and `/etc/hosts`. The fetch UDF tries the system resolver first and falls back to DNS over HTTPS (`DOH_URL`, default `https://1.1.1.1/dns-query`) only on `gaierror` (D21). Only name lookup changes; TLS still verifies the real host name. Remove the fallback once nano fixes the sandbox.
- **UDF facts:** concurrency is `nrOfCores` per node, which Personal sizes from free host memory at each start, so record the sizing (`EXA_SYSTEM_EVENTS`, `podman logs`) with any benchmark. `os.getpid()` is 1 in every UDF instance; identify instances with `exa.meta.vm_id`. Exasol's string-to-DOUBLE parse is not correctly rounded, so pass model values to UDFs as `DECIMAL`. No Rust in v1 (D16).
