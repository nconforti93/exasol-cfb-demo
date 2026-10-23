# Feature: The scale dial: 100,000 seasons on the laptop, a million on AWS, side by side

Beat 3 ends with the simulation count as a dial: the same model and the same week, 100,000 seasons on the laptop and 1,000,000 on the 3-node AWS instance, with the seconds of both on screen. The laptop runs its 100,000 inside the refresh. A second app instance on the laptop, connected to the AWS database over loopback, runs and publishes the million-season run on one confirmed click. A scale card in the inspector shows the newest run of each deployment side by side, with sizing and step times. The other deployment's numbers come from a sync of run summaries into the local database, so the card still shows them, labeled with their measurement time, when AWS is out of reach.

## Background

- **Two app instances, one laptop** (Phase 6 decision: "AWS" means the app on the laptop against the AWS database, on loopback):
  - App A: `make app-serve` (`TARGET=local`, port 8000).
  - App B: `make app-serve TARGET=aws APP_PORT=8001 APP_REFRESH=off`.
  - Both bind to loopback, so both have the operator path (`app/access`). No authentication is added; an app on a network address stays read-only.
  - Each instance's session cookie carries its port in its name, so two instances in one browser keep separate sessions, query logs and preview caches. Each instance accepts operator requests only from its own origin (`app/access` builds the allowed `Host` and `Origin` values from `APP_PORT`).
  - **`APP_REFRESH=off`** disables the refresh on an instance. `/api/config` reports the refresh disabled with the reason "refresh is off on this instance", and the refresh's plan and run requests are refused with 403 `refresh_off`. The dial, the console and the previews stay available. App B always runs with it, so a misclick cannot spend AWS calls or move the AWS point during the demo; the Sunday cadence refreshes AWS from the command line.
- **The dial** is one more operator action (`app/refresh` lists the others):
  - `POST /api/scale/plan` (0 calls, no lock) returns:
    - the target, the current point, and the configured sims and batch size of `CFB_SIM.SIM_CONFIG`;
    - the estimated seconds: those of the newest `DONE` run of that size on this deployment, if any;
    - whether a loader or simulation run holds the lock, and `can_run` with reasons.
  - `POST /api/scale/run` `{season, week, season_type, n_sims}` re-plans and refuses an `n_sims` that differs from the plan's. It then runs in a background thread on the owner's connection, in order:
    1. `EXECUTE SCRIPT CFB_INFRA.SIMULATE(<season>, <week>, '<season_type>', <n_sims>, <sims_per_batch>, NULL, NULL, 'SCALE DIAL')`;
    2. `EXECUTE SCRIPT CFB_INFRA.PUBLISH_RUN(<run id>)`.

    It answers 202 with an action id (`GET /api/actions/{id}`).
  - The point is always `CFB_API.CURRENT_POINT`, and the sims and batch size always come from the deployment's configuration (`sim-engine/deploy`: 100,000 at 5,000 on the laptop, 1,000,000 at 10,000 on AWS). The dial never takes free numbers from the browser.
  - The dial's confirmation quotes the plan: "Run <sims> seasons on <target> · 0 CFBD calls · ≈ <estimate> s", or "about a minute" when there is no estimate.
  - The dial spends no CFBD call and takes no ledger lock. It takes the loader's run lock through the scripts (kind `SIMULATE`), so it is refused while a refresh, a backfill, a restore or another simulation runs, and a refresh is refused while it runs.
  - Its progress is polled from `CFB_API.SCALE_RUN_STEPS` (one row per step, committed at each step's start and end, `sim-engine/sim-runs`). When the publication exists, the pages follow it by the current point's follow key (`app/as-of`).
- **The scale card** (the inspector's "Scale" tab) reads `CFB_API.SCALE_RUNS` and `CFB_API.SCALE_RUN_STEPS`:
  - one column per deployment: the newest `DONE` run whose origin is `REFRESH`, or `MANUAL` with the label `SCALE DIAL`;
  - per run: the deployment label (`laptop` or `aws`), sims, batch size, nodes, instances, DB RAM, database version, total seconds, engine seconds, sims per second, the point, and when it ended;
  - per step: a bar of its seconds, aligned across the columns by step name;
  - the ratio "×n sims in ×m time" between the two columns. Every division in the views uses `NULLIFZERO` on its divisor, because the 2026.1 optimizer can evaluate a guarded division eagerly.
  - A column from another deployment carries "measured <time>" and "imported <time>".
  - On stage the card is shown in App A right after the dial, once `make bench-sync` has imported the dial's run (seconds, zero calls). App A's own column is always read live, so it shows the refresh the audience just watched.
- **`CFB_BENCH`** (new schema, owned by `sys`; data tables created if missing and never replaced):
  - `BENCH_SETTINGS`: the deployment's own label, written by `make deploy` from `TARGET` (`laptop` for `local`, `aws` for `aws`; `BENCH_LABEL` overrides).
  - `BENCH_SIM_RUN` and `BENCH_SIM_STEP`: run summaries and step seconds copied from a deployment's `CFB_SIM.SIM_RUN` and `SIM_RUN_STEP`. They are keyed by (deployment label, source run id, start time), with `RUN_PURPOSE` `DEMO` or `BENCH` and the import time.
  - `BENCH_METRIC`: the benchmark's other measurements, written on the deployment they were measured on (`demo/benchmark`).
- **`make bench-sync`** (`scripts/bench.py sync`) copies run summaries in both directions between `local` and `aws`:
  - what it copies: the `DONE` runs of origin `REFRESH` or labeled `SCALE DIAL` from the last 60 days, with their steps. It never copies `BENCH_METRIC` rows; the benchmark report reads both deployments directly;
  - it merges by key, so a repeated sync adds nothing;
  - it reads and writes derived numbers only, as `sys` on both deployments, and makes no CFBD call;
  - if either side cannot be reached, it changes nothing and says which;
  - `ARGS="--peer-prefix <prefix>"` syncs with a namespace of the local deployment instead of AWS (tests).
- `CFB_API.SCALE_RUNS` and `SCALE_RUN_STEPS` join the serving schema (`api/serving-contract`: new views may be added), so `CFB_APP` and `CFB_MCP` read them through their existing schema grants.
- The examples use the app test stack (`tests/it/app_stack.py`) after `refreshed(10)`, with the sim league's test configuration (4,000 sims at 500). A second namespace `TEST2_`, with the simulation registry and `CFB_BENCH` deployed, stands in for the other deployment.

## Scenarios

### Scenario: The dial plans its run at zero calls

* *GIVEN* the app on the test stack at 2026 week 10 with the operator path on
* *WHEN* the browser posts `/api/scale/plan`
* *THEN* the answer MUST name the target, the point 2026 week 10, 4,000 sims at 500 per batch from the configuration, and `can_run` true
* *AND* it MUST include the estimated seconds of the newest `DONE` run with 4,000 sims, or none when there is no such run
* *AND* the dial's confirmation MUST quote that estimate in seconds, or "about a minute" when there is none
* *AND* no run lock MUST be taken, and the mock CFBD API MUST receive nothing

### Scenario: The dial runs and publishes a run at the current point

* *GIVEN* the plan of the previous scenario
* *WHEN* the browser posts `/api/scale/run` with 4,000 sims and polls the action and `CFB_API.SCALE_RUN_STEPS`
* *THEN* the action MUST end `done`, naming a new `DONE` run with origin `MANUAL`, label `SCALE DIAL`, 4,000 sims, and the point 2026 week 10
* *AND* the polled steps MUST have shown each step of the run `RUNNING` before it was `DONE`
* *AND* `CFB_API.PUBLISHED_RUNS` MUST show a new publication of that run at week 10, and the odds page MUST reload to it by the follow key
* *AND* the ledger and the mock CFBD API MUST be untouched

### Scenario: The dial takes its size from the configuration and its turn from the lock

* *GIVEN* a refresh holding the run lock
* *WHEN* the browser posts `/api/scale/run`, and later, with the lock free, posts it with 1,000,000 sims on a deployment configured for 4,000
* *THEN* the first MUST be refused with 409 `refresh_running`, naming the running run
* *AND* the second MUST be refused with 400, naming the configured size, and no run MUST start

### Scenario: The dial exists only where the operator path does

* *GIVEN* the app bound to a non-loopback address, read-only
* *WHEN* the browser loads `/api/config` and posts `/api/scale/run`
* *THEN* `/api/config` MUST report the dial disabled with the reason "operator path off"
* *AND* the post MUST be refused with 403

### Scenario: An instance with the refresh off cannot refresh by a click

* *GIVEN* the app on the test stack on loopback, started with `APP_REFRESH=off`
* *WHEN* the browser loads `/api/config`, then posts the refresh's plan and run requests, then posts `/api/scale/plan`
* *THEN* `/api/config` MUST report the refresh disabled with the reason "refresh is off on this instance"
* *AND* both refresh requests MUST be refused with 403 `refresh_off`, no run MUST start, and the mock CFBD API MUST receive nothing
* *AND* the dial's plan MUST still be answered with `can_run` true

### Scenario: The scale card shows both deployments' newest runs side by side

* *GIVEN* the refresh run of week 10 on the test stack (4,000 sims) and, in the local `CFB_BENCH`, an imported `aws` run of 8,000 sims at week 10 with its steps
* *WHEN* the browser opens the inspector's "Scale" tab
* *THEN* it MUST show two columns, `laptop` with the local refresh run and `aws` with the imported run, each with sims, batch size, nodes, instances, DB RAM, version, total and engine seconds, sims per second and the point
* *AND* the step bars MUST be aligned by step name, and the `aws` column MUST carry its measurement and import times
* *AND* the card MUST state the ratio of sims and of seconds between the columns
* *AND* a run labeled `BENCH` or of origin `FORCED` MUST NOT appear on the card

### Scenario: The card works when the other deployment is out of reach

* *GIVEN* the local `CFB_BENCH` holding an imported `aws` run, and no connection to any other deployment
* *WHEN* the browser opens the inspector's "Scale" tab
* *THEN* the card MUST show the imported run with "measured <time>", read from the local database alone
* *AND* when no run of the other deployment was ever imported, its column MUST say "not measured yet" and name `make bench-sync`

### Scenario: Run summaries are synced both ways, once, at zero calls

* *GIVEN* `DONE` runs on the local namespace (a refresh run and a dial run) and on `TEST2_` (a refresh run of 8,000 sims), with their steps, and a `BENCH_METRIC` row on each side
* *WHEN* the owner runs `make bench-sync` twice, with `TEST2_` as the other deployment
* *THEN* after the first run each side's `CFB_BENCH.BENCH_SIM_RUN` MUST hold the other side's runs with their step seconds, under the other side's label
* *AND* neither side's `BENCH_METRIC` MUST hold the other side's row
* *AND* the second run MUST add no row and change no row
* *AND* neither side's `CFB_SIM` tables MUST change, and the mock CFBD API MUST receive nothing

### Scenario: Two app instances on one laptop keep separate sessions

* *GIVEN* two app instances of the test stack on ports 8000 and 8001
* *WHEN* one browser runs a console query in the first instance and then opens the inspector of the second, and an operator request is posted to each instance with its own origin and with the other instance's origin
* *THEN* each instance MUST set a session cookie whose name carries its port
* *AND* the second instance's query log MUST NOT show the first instance's query, and the first instance's log MUST still show it
* *AND* each instance MUST accept the operator request with its own origin and refuse the one with the other instance's origin with 403
