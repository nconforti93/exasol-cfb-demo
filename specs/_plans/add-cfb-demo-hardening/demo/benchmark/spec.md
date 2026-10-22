# Feature: Engine and demo benchmark

The mission promises numbers: seasons per second on a laptop and on AWS, what-if answers under a second at a million seasons, a weekly refresh including 100,000 seasons in under two minutes on the laptop, on-demand lookups under two seconds. The benchmark measures each of them the same way every time, records the machine they ran on, keeps the numbers after the runs that produced them are dropped, and writes `docs/benchmark.md` with derived numbers only. It never spends a CFBD call.

## Background

- **Command:** `make bench [TARGET=local|aws] [SUITES=sims,whatif,refresh,load,ondemand] [ARGS="--keep --matrix <file>"]` (`scripts/bench.py run`), then `make bench-report` (`scripts/bench.py report`), which writes `docs/benchmark.md` from the `CFB_BENCH` rows of both deployments (after `make bench-sync`, `demo/scale-dial`).
- **Matrix** (`bench/matrix.yaml`, committed; `--matrix` replaces it):
  - `sims`, per target, a list of cells (`n_sims`, `sims_per_batch`): local 100,000 at 5,000, 10,000 and 1,000, and 10,000 at 1,000; AWS 1,000,000 at 10,000, 100,000 at 1,000, and 2,000,000 at 10,000 (the "1M+" cell);
  - `repeats`: 3 for simulations; `whatif_repeats`: 20.
- **Suites:**
  - `sims`: per cell and repeat, `CFB_INFRA.SIMULATE` at the current point with the label `BENCH <cell> #<repeat>`. Per cell it records the median, minimum and maximum of total seconds, engine seconds and sims per second, plus the seconds per step, and copies each run's summary into `CFB_BENCH.BENCH_SIM_RUN` with `RUN_PURPOSE` `BENCH`. Unless `--keep` is given, each run is then dropped (`CFB_INFRA.DROP_SIM_RUN`); its numbers stay in `CFB_BENCH`.
  - `whatif`: Phase 5's timed query catalog (`analyses/perf.py`) against the deployment's what-if publication, for 1, 3 and 8 conditions and the rooting guide with and without conditions: p50, p95 and maximum over `whatif_repeats`.
  - `refresh`: the step seconds, slices, rows and calls of the newest `REFRESH_WEEK` run (`CFB_META.REFRESH_LOG`), labeled live or replay from its fetch sources.
  - `load`: the duration and rows of the newest `BACKFILL`, `SNAPSHOT` and `RESTORE` runs (`CFB_META.LOAD_RUN`).
  - `ondemand`: the latency of live preview and console requests (`CFB_META.FETCH_LOG` rows of `REQ_SOURCE` `LIVE` with context `APP_PREVIEW#…` or `APP_CONSOLE#…`), median and maximum.
- **Every measurement row** (`CFB_BENCH.BENCH_METRIC`) carries the deployment label, suite, metric, cell, sample count, median, p95, minimum, maximum, unit, nodes, instances, DB RAM, database version, measurement time and the benchmark run's key. Sizing comes from the runs themselves (`SIM_RUN.INSTANCES`, `DB_RAM_GIB`; Exasol Personal sizes cores and memory at every start).
- **Exclusive use.** The benchmark takes the loader's run lock for each simulation (through the scripts) and refuses to start while a loader or simulation run holds it. On the laptop it runs under the shared integration-test lock file, so tests and benchmarks never overlap.
- **The report** (`docs/benchmark.md`, generated, committed) has: the setup (deployments, versions, sizing, dates); a table per suite; the mission and Phase 4 targets with measured value and `met` or `missed` (local 100,000 sims ≤ 30 s, AWS 1,000,000 ≤ 60 s, what-if p95 < 1 s at 1,000,000, `REFRESH_WEEK` with 100,000 sims < 2 min locally, on-demand lookup < 2 s, restore of the demo profile < 5 min); and the method and caveats. It holds numbers, labels and dates only: no response body, no stored row, no team-level result.
- Zero CFBD calls: no suite reads `CFB_API_LIVE` or the virtual schema; the `ondemand` suite reads logged latencies only.
- The examples use the integration-test namespace at 2026 week 10 with a publication, and a test matrix of 4,000 sims at 500 and 1,000 per batch, 3 repeats.

## Scenarios

### Scenario: The sims suite measures each cell three times and records the sizing

* *GIVEN* the test namespace at 2026 week 10 and the test matrix
* *WHEN* the owner runs `make bench SUITES=sims ARGS="--matrix <test matrix>"`
* *THEN* `CFB_BENCH.BENCH_METRIC` MUST hold, per cell, the median, minimum and maximum of total seconds, engine seconds and sims per second over 3 samples, with the instances, DB RAM and database version of the runs
* *AND* `CFB_BENCH.BENCH_SIM_RUN` MUST hold the 6 runs with `RUN_PURPOSE` `BENCH` and their step seconds in `BENCH_SIM_STEP`

### Scenario: Benchmark runs are dropped after measuring and their numbers kept

* *GIVEN* the sims suite of the previous scenario without `--keep`
* *WHEN* it finishes
* *THEN* none of its 6 runs MUST remain in `CFB_SIM.SIM_RUN` as `DONE`, and their outcome tables MUST be gone
* *AND* the publication shown by the pages MUST be unchanged
* *AND* the measurements in `CFB_BENCH` MUST remain
* *AND* with `--keep` the runs MUST remain, labeled `BENCH`

### Scenario: The what-if suite records latency percentiles per condition count

* *GIVEN* the test namespace with its week-10 publication
* *WHEN* the owner runs `make bench SUITES=whatif`
* *THEN* `CFB_BENCH.BENCH_METRIC` MUST hold p50, p95 and maximum seconds for 1, 3 and 8 conditions and for the rooting guide with and without conditions, each over 20 samples, naming the run of the publication
* *AND* no measured statement MUST name `CFB_API_LIVE` or the virtual schema

### Scenario: Refresh, load and on-demand numbers come from the logs

* *GIVEN* a replayed `REFRESH_WEEK` run, a `RESTORE` run and logged live preview requests of 700 ms and 1,100 ms in the test namespace
* *WHEN* the owner runs `make bench SUITES=refresh,load,ondemand`
* *THEN* the refresh rows MUST give the seconds, slices, rows and calls of each step, labeled replay
* *AND* the load rows MUST give the restore's duration and rows
* *AND* the on-demand rows MUST give a median of 900 ms and a maximum of 1,100 ms for previews

### Scenario: The report states every target as met or missed with derived numbers only

* *GIVEN* `CFB_BENCH` rows for `laptop` and `aws`, among them an AWS median of 52.0 s for 1,000,000 sims and a local median of 33.5 s for 100,000 sims
* *WHEN* the owner runs `make bench-report`
* *THEN* `docs/benchmark.md` MUST list the AWS 1,000,000-sims target as met (52.0 s against 60 s) and the local 100,000-sims target as missed (33.5 s against 30 s), each with its sizing and date
* *AND* the file MUST NOT contain any slice key's response body, stored row or team name
* *AND* running the report twice on the same rows MUST produce the same file

### Scenario: The benchmark never spends a call and waits for no one

* *GIVEN* a refresh holding the run lock in the test namespace
* *WHEN* the owner runs `make bench SUITES=sims`
* *THEN* the benchmark MUST stop before its first run, naming the running run, and write no measurement
* *AND* across every suite of the previous scenarios the mock CFBD API MUST have received no request and the ledger MUST be unchanged
