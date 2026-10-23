# Feature: Engine and demo benchmark

The mission promises numbers: seasons per second on a laptop and on AWS, what-if answers under a second at a million seasons, a weekly refresh including 100,000 seasons in under two minutes on the laptop, on-demand lookups under two seconds. The benchmark measures each of them the same way every time, records the machine they ran on, keeps the numbers after the runs that produced them are dropped, and writes `docs/benchmark.md` with derived numbers only. It never spends a CFBD call.

## Background

- **Commands:**
  - `make bench [TARGET=local|aws] [SUITES=sims,whatif,refresh] [ARGS="--keep --matrix <file>"]` (`scripts/bench.py run`) measures on one deployment and writes its rows to that deployment's `CFB_BENCH`.
  - `make bench-report` (`scripts/bench.py report`) writes `docs/benchmark.md`. It reads the `CFB_BENCH` rows of both deployments directly, plus two numbers straight from their logs (below). `ARGS="--peer-prefix <prefix>"` reads a namespace of the local deployment instead of AWS (tests).
- **Matrix** (`bench/matrix.yaml`, committed; `--matrix` replaces it):
  - `sims`, per target, a list of cells (`n_sims`, `sims_per_batch`):
    - local: 100,000 at 5,000, 10,000 and 1,000, and 10,000 at 1,000;
    - AWS: 1,000,000 at 10,000, 100,000 at 1,000, and 2,000,000 at 10,000 (the "1M+" cell);
  - `repeats`: 3 for simulations; `whatif_repeats`: 20.
- **Suites:**
  - `sims`: per cell and repeat, `CFB_INFRA.SIMULATE` at the current point with the label `BENCH <cell> #<repeat>`.
    - Per cell it records the median, minimum and maximum of total seconds, engine seconds and sims per second, plus the seconds per step.
    - It copies each run's summary into `CFB_BENCH.BENCH_SIM_RUN` with `RUN_PURPOSE` `BENCH`.
    - Unless `--keep` is given, each run is then dropped (`CFB_INFRA.DROP_SIM_RUN`); its numbers stay in `CFB_BENCH`.
  - `whatif`: Phase 5's timed query catalog (`analyses/perf.py`) against the deployment's what-if publication, for 1, 3 and 8 conditions and the rooting guide with and without conditions: p50, p95 and maximum over `whatif_repeats`.
  - `refresh`: the step seconds, slices, rows and calls of the newest `REFRESH_WEEK` run (`CFB_META.REFRESH_LOG`), labeled live or replay from its fetch sources.
- **Read from the logs at report time, never copied:**
  - the duration and rows of the local deployment's newest `RESTORE` run (`CFB_META.LOAD_RUN`);
  - the latency of live preview and console requests on each deployment, median and maximum: `CFB_META.FETCH_LOG` rows of `REQ_SOURCE` `LIVE` with context `APP_PREVIEW#…` or `APP_CONSOLE#…`.
- **Every measurement row** (`CFB_BENCH.BENCH_METRIC`) carries:
  - the deployment label, suite, metric, cell and sample count;
  - the median, p95, minimum, maximum and unit;
  - nodes, instances, DB RAM and database version. Sizing comes from the runs themselves (`SIM_RUN.INSTANCES`, `DB_RAM_GIB`), because Exasol Personal sizes cores and memory at every start;
  - the measurement time and the benchmark run's key.
- **Exclusive use.** The benchmark takes the loader's run lock for each simulation (through the scripts) and refuses to start while a loader or simulation run holds it. On the laptop it runs under the shared integration-test lock file, so tests and benchmarks never overlap.
- **The report** (`docs/benchmark.md`, generated, committed) has:
  - the setup: deployments, versions, sizing, dates;
  - a table per suite, and the restore and on-demand numbers;
  - the mission and Phase 4 targets, each with the measured value and `met` or `missed`: local 100,000 sims ≤ 30 s, AWS 1,000,000 ≤ 60 s, what-if p95 < 1 s at 1,000,000, `REFRESH_WEEK` with 100,000 sims < 2 min locally, on-demand lookup < 2 s, restore of the demo profile < 5 min;
  - the method and caveats.

  It holds numbers, labels and dates only: no response body, no stored row, no team-level result.
- Zero CFBD calls: no suite reads `CFB_API_LIVE` or the virtual schema, and the report reads logged latencies only.
- The examples use the integration-test namespace at 2026 week 10 with a publication, and a test matrix of 4,000 sims at 500 and 1,000 per batch, 3 repeats.

## Scenarios

### Scenario: The sims suite measures each cell three times and records the sizing

* *GIVEN* the test namespace at 2026 week 10 and the test matrix
* *WHEN* the owner runs `make bench SUITES=sims ARGS="--matrix <test matrix>"`
* *THEN* `CFB_BENCH.BENCH_METRIC` MUST hold, per cell, the median, minimum and maximum of total seconds, engine seconds and sims per second over 3 samples, with the instances, DB RAM and database version of the runs
* *AND* `CFB_BENCH.BENCH_SIM_RUN` MUST hold the 6 runs with `RUN_PURPOSE` `BENCH`, and `BENCH_SIM_STEP` their step seconds

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

### Scenario: Refresh numbers are measured from the log, restore and on-demand numbers read at report time

* *GIVEN* a replayed `REFRESH_WEEK` run, a `RESTORE` run, and logged live preview requests of 700 ms and 1,100 ms in the test namespace, with the peer namespace `TEST2_` holding its own `CFB_BENCH` rows
* *WHEN* the owner runs `make bench SUITES=refresh` and then `make bench-report ARGS="--peer-prefix TEST2_"`
* *THEN* the refresh rows MUST give the seconds, slices, rows and calls of each step, labeled replay
* *AND* `docs/benchmark.md` MUST give the restore's duration and rows, and a preview median of 900 ms and maximum of 1,100 ms, with the peer's rows in the same tables
* *AND* `CFB_BENCH.BENCH_METRIC` MUST hold no restore or on-demand row

### Scenario: The report states every target as met or missed with derived numbers only

* *GIVEN* result rows for `laptop` and `aws`, among them an AWS median of 52.0 s for 1,000,000 sims and a local median of 33.5 s for 100,000 sims
* *WHEN* the report is rendered
* *THEN* `docs/benchmark.md` MUST list the AWS 1,000,000-sims target as met (52.0 s against 60 s) and the local 100,000-sims target as missed (33.5 s against 30 s), each with its sizing and date
* *AND* the file MUST NOT contain any slice key's response body, stored row or team name
* *AND* rendering the report twice from the same rows MUST produce the same file

### Scenario: The benchmark never spends a call and waits for no one

* *GIVEN* a refresh holding the run lock in the test namespace
* *WHEN* the owner runs `make bench SUITES=sims`
* *THEN* the benchmark MUST stop before its first run, naming the running run, and write no measurement
* *AND* across every suite and report of the previous scenarios the mock CFBD API MUST have received no request and the ledger MUST be unchanged
