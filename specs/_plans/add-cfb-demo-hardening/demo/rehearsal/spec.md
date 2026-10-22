# Feature: Rehearsal: reset to a kit, replay the refresh, fall back without a restore

One command resets the laptop's database to a kit's "Sunday after week N" and puts the virtual schema into replay mode over that kit's recordings. From there the demo runs exactly as it would live: `REFRESH_WEEK` of week N+1 fetches through the virtual schema, the model, predictions, simulations and publication steps run, and every page moves, at zero CFBD calls and without network. Because the simulation seeds follow the point and the recordings are frozen, two rehearsals of one kit give the same pages. The same switch works in the other direction on stage: if a live refresh fails, switching to replay and refreshing again recovers without restoring anything.

## Background

- **Commands** (`scripts/demo.py`):
  - `make rehearse KIT=<kit> [MODE=replay|live] CONFIRM=yes` resets the local deployment to the kit. Without `CONFIRM=yes` it prints what it would do and checks the kit and the snapshot, and changes nothing.
  - `make demo-mode MODE=live|replay [KIT=<kit>] [TARGET=local|aws]` switches the fetch mode only.
- **What `make rehearse` does, in order** (each step printed with its seconds):
  1. Checks the kit: every file against its SHA-256 in `kit.json`; the snapshot exists and its `manifest.json` has the SHA-256 the kit pinned; the deployed catalog hash equals the snapshot's (`loader/snapshot-restore`).
  2. Copies the kit's recordings into the local BucketFS host directory that appears in UDFs as the kit's `replay_path`, and verifies each copy by SHA-256.
  3. Switches the virtual schema to replay over the kit: `ALTER VIRTUAL SCHEMA CFBD_VS SET FETCH_MODE = 'replay' REPLAY_PATH = '<replay_path>'`. From here no query can reach the network.
  4. Restores the snapshot (`make restore`, run lock kind `RESTORE`, zero calls).
  5. Regenerates the serving views for the restored point (`make analyses`, `analyses/what-if`).
  6. Makes sure the pages have a week-N publication: if `CFB_API.PUBLISHED_RUNS` has none at (season, N), it runs `CFB_INFRA.SIMULATE` at week N with the deployment's configuration and `CFB_INFRA.PUBLISH_RUN` of that run (zero calls).
  7. Reports the current point (week N), the publication the odds page shows, how many week-N+1 predictions were frozen in advance and how many would be reconstructed (the scorecard headline's kind, `analyses/scorecard`), the recorded completeness of week N+1, and the next commands (`make app-serve`, the refresh of week N+1).
  8. With `MODE=live`, switches to live as `make demo-mode MODE=live` does. The default mode is `replay`.
- **Mode switch rules** (`make demo-mode`):
  - `replay` needs a kit (`KIT=`, or the kit whose `replay_path` the virtual schema already has) whose recordings are installed; it sets `FETCH_MODE` and the kit's `REPLAY_PATH`.
  - `live` sets `FETCH_MODE = 'live'` and the default `REPLAY_PATH` (`/buckets/bfsdefault/default/cfbd/replay`). It is refused when the deployment has no connection with the name in the virtual schema's `CONNECTION_NAME` (`CFBD_API` on the real deployment; checked by name in `EXA_DBA_CONNECTIONS`, the key is never read) or when the DNS gate of `make dns-check` fails.
  - Replay is local only: the AWS deployment has no BucketFS host directory for recordings, so `MODE=replay` with `TARGET=aws` is refused, and `make rehearse` accepts only `TARGET=local`.
  - Only these commands change virtual-schema properties; the loader still never does (`loader/backfill`).
- **Determinism.** A replayed refresh reads the same recordings, the simulation step uses the deployment's configured sims and batch size and the default seed (season × 1,000 + week sequence, `sim-engine/sim-runs`), and predictions are frozen once per game (`analyses/predictions`). Run ids, publication ids and timestamps differ between rehearsals; values do not.
- Everything in this feature costs zero CFBD calls. The live refresh on stage is the app's (`app/refresh`) and is ledgered there.
- The examples use the integration-test namespace with the kit `2026-w09-w10` of `demo/rehearsal-kit` (sim league, base week 9, replay week 10) and the sim league's test configuration (4,000 sims, 500 per batch).

## Scenarios

### Scenario: Installing a kit resets the database to the Sunday after week N in replay mode

* *GIVEN* the integration-test namespace at 2026 week 10, with a publication at week 10 and none at week 9
* *WHEN* the owner runs `make rehearse KIT=2026-w09-w10 CONFIRM=yes`
* *THEN* `CFB_API.CURRENT_POINT` MUST return 2026 week 9, and the virtual schema MUST have `FETCH_MODE` `replay` and `REPLAY_PATH` `/buckets/bfsdefault/default/cfbd/kits/2026-w09-w10`
* *AND* `CFB_API.PUBLISHED_RUNS` MUST hold a publication at week 9, made by this command from a `DONE` run with 4,000 sims, and the odds page's what-if publication MUST be that one
* *AND* the week-10 publication MUST NOT be offered on any page, because it lies after the current point
* *AND* the report MUST state the current point, the week-9 publication, the number of week-10 predictions frozen in advance and the recorded completeness of week 10

### Scenario: The replayed refresh moves every page to week N+1 at zero calls

* *GIVEN* the kit `2026-w09-w10` installed by `make rehearse`
* *WHEN* `EXECUTE SCRIPT CFB_INFRA.REFRESH_WEEK(2026, 10, 'regular')` runs
* *THEN* every step of the returned timeline MUST be `DONE`, and the four `FETCH` steps MUST report 0 calls
* *AND* `CFB_API.FETCHES` of the run MUST show the four refresh slices with `REQ_SOURCE` `REPLAY` and the recorded status and latency
* *AND* `CFB_API.CURRENT_POINT` MUST return 2026 week 10, the odds MUST carry arrows against week 9, the scorecard MUST grade week 10, and the upsets MUST list week 10
* *AND* the mock CFBD API MUST NOT receive any request

### Scenario: Two rehearsals of one kit produce the same pages

* *GIVEN* a first rehearsal: `make rehearse KIT=2026-w09-w10 CONFIRM=yes` followed by the week-10 refresh
* *WHEN* a second rehearsal repeats both commands
* *THEN* `PLAYOFF_ODDS` at week 10, the week-10 rows of `SCORECARD_WEEKS`, the week-10 rows of `UPSETS`, the `PREDICTIONS` of week 11 and the `ROOTING_GUIDE` of the published run MUST be equal between the two rehearsals in every column except run ids, publication ids and timestamps
* *AND* the step names, statuses, slices, rows and calls of the two refresh timelines MUST be equal

### Scenario: A rehearsal runs without network or key

* *GIVEN* the kit `2026-w09-w10` and a fetch UDF whose access to the connection has been revoked, with the mock CFBD API stopped
* *WHEN* the owner runs `make rehearse KIT=2026-w09-w10 CONFIRM=yes` and then the week-10 refresh
* *THEN* both MUST succeed, and the refresh MUST end with 2026 week 10 as the current point
* *AND* no step MUST attempt a network connection

### Scenario: A failed live refresh falls back to replay without a restore

* *GIVEN* the kit `2026-w09-w10` installed with `MODE=live`, against the mock CFBD API, which answers the 2026 `GAMES` slice with HTTP 500 every time
* *WHEN* the week-10 refresh fails at its `FETCH GAMES` step, and the presenter then runs `make demo-mode MODE=replay KIT=2026-w09-w10` and the week-10 refresh again
* *THEN* after the failed refresh the current point MUST still be 2026 week 9 and the stored rows of the failed table MUST be unchanged
* *AND* the second refresh MUST end `DONE` with 2026 week 10 as the current point and 0 calls in its `FETCH` steps
* *AND* the pages MUST equal those of a rehearsal that was in replay mode from the start, in every column except run ids, publication ids and timestamps

### Scenario: Replay mode is refused where no recordings can be read

* *GIVEN* the `aws` target
* *WHEN* the owner runs `make demo-mode MODE=replay KIT=2026-w09-w10 TARGET=aws` or `make rehearse KIT=2026-w09-w10 TARGET=aws CONFIRM=yes`
* *THEN* both MUST be refused before connecting, stating that replay is local only because the AWS deployment has no BucketFS host directory for recordings
* *AND* `make demo-mode MODE=replay` without `KIT=` on a virtual schema that has no kit path MUST be refused, naming `KIT=`

### Scenario: Switching to live needs the key's connection and the DNS gate

* *GIVEN* the kit installed in replay mode on a namespace without a connection named as the virtual schema's `CONNECTION_NAME`
* *WHEN* the owner runs `make demo-mode MODE=live`
* *THEN* the command MUST be refused, naming the missing connection and the user-guide section that creates it, and the virtual schema MUST stay in replay mode
* *AND* with the connection present and the DNS gate passing, the command MUST set `FETCH_MODE` `live` and the default `REPLAY_PATH`, and print the mode in effect
