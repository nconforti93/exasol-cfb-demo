# Feature: Demo preflight: one command says whether the demo will run

Before every rehearsal and before the demo itself, the presenter needs one answer: will each beat work, in the mode chosen, on this machine, today? The preflight checks the installed kit, the recordings every replayed query will need, the statements of the demo script, the scorecard's honesty label, the machine's sizing, the live prerequisites when the demo runs live, and the AWS side of the scale dial. Each check ends PASS, WARN or FAIL with the command that fixes it. The preflight itself never spends a CFBD or Anthropic call and never changes the database.

## Background

- **Command:** `make demo-preflight [KIT=<kit>] [MODE=replay|live] [AWS=yes] [ARGS="--now <UTC time>"]` (`scripts/demo.py preflight`). Without `KIT=` it takes the kit whose `replay_path` the virtual schema has, or, in live mode, the kit whose snapshot the newest `RESTORE` run in `CFB_META.LOAD_RUN` names (its `RUN_ARGS`). `--now` replaces the clock for the staleness check (tests).
- **Output:** one line per check (`PASS`, `WARN` or `FAIL`, the check id, what was found, and for anything but `PASS` the command that fixes it), then a summary. The exit code is 0 when no check failed (warnings allowed) and 2 when one did.
- **Checks** (ids in brackets):
  - [db] the local deployment answers, with its version.
  - [kit] the kit's files match `kit.json`, its snapshot's manifest has the pinned SHA-256, and the newest `RESTORE` run names that snapshot with no `BACKFILL` run after it.
  - [point] the current point is the kit's base week N (before the refresh) or its replay week N+1 (after a refresh); anything else fails.
  - [mode] the virtual schema's `FETCH_MODE` is the requested mode, and in replay its `REPLAY_PATH` is the kit's.
  - [recordings] in replay: every installed recording file exists in the BucketFS host directory with its SHA-256, and every slice that the script's replayed queries will request has a recording. The requested slices come from `EXPLAIN VIRTUAL` of the queries themselves (zero calls): the refresh's fetch of week N+1 (the loader's batch query per weekly table of the season), the console's `fan-out` and `contradiction` queries, and the preview query of each pair in `demo/demo.yaml`.
  - [week] the recorded week N+1 is complete (from `kit.json`), so the refresh will move the point.
  - [publication] a publication exists at week N; after the refresh, one at week N+1.
  - [headline] how many graded week-N+1 predictions were frozen in advance; WARN when the headline will be the labeled reconstruction (`analyses/scorecard`).
  - [script] every statement of the demo script marked for checking runs on the kit (below).
  - [sizing] the instances and DB RAM of the newest local simulation run (`CFB_SIM.SIM_RUN.INSTANCES`, `DB_RAM_GIB`); WARN below 8 instances, the sizing the 100k timing assumes.
  - [stale] WARN when a regular-season week after the kit's replay week has ended by `--now` (or the clock), from the restored `CFB_MODEL.SEASON_WEEK`: the demo should use the newest completed week.
  - [chat] the chat's availability (`chat/availability`: live with a key, stub without one), and that every name and game of the `demo` bindings resolves at the kit's replay week (`demo/chat-beat`).
  - [users] the app's read and console users and the chat user exist.
  - Live mode only: [connection] a connection with the virtual schema's `CONNECTION_NAME` exists (by name); [dns] the DNS gate of `make dns-check` passes; [ledger] the ledger plan `app` and the month have room for the demo's planned live calls (4 refresh slices, 3 fan-out slices and 1 preview) plus the retry headroom of `app/live-spend`; [quota] `CFB_META.QUOTA.REFRESH_SPENDABLE` covers the refresh (from free `/info`).
  - With `AWS=yes`: [aws] the AWS deployment answers with its pinned certificate; its newest `DONE` refresh simulation run (`CFB_SIM.SIM_RUN`, origin `REFRESH`) is at the kit's replay week; it has a `DONE` run at that point with the sims its `CFB_SIM.SIM_CONFIG` names (the dial's size); and the local `CFB_BENCH` holds its newest such run (`demo/scale-dial`). `ARGS="--peer-prefix <prefix>"` checks a namespace of the local deployment instead (tests).
- **Checked script statements.** In `docs/demo-script.md`, fenced blocks whose info string is `sql demo` are checked; `sql show` blocks are shown on stage and never run by the preflight (for example the `REFRESH_WEEK` call). Placeholders `{SEASON}`, `{N}`, `{N1}`, `{BUBBLE_TEAM}`, `{RIVAL}` and `{PAIR_A}`/`{PAIR_B}` are filled from the kit and `demo/demo.yaml`. A statement that reads the virtual schema is only explained (`EXPLAIN VIRTUAL`) in live mode, and executed only in replay mode; a statement on a serving view (`CFB_API`) is executed as the app's read user. Any other statement in a `sql demo` block fails the check.
- **Never spends.** The preflight makes no CFBD call (`EXPLAIN VIRTUAL` and stored-only statements cost none, and virtual-schema statements execute only in replay mode), reads `/info` only through `CFB_META.QUOTA` in live mode, makes no Anthropic call, and writes nothing.
- The examples use the integration-test namespace with the kit `2026-w09-w10` installed by `make rehearse` (`demo/rehearsal`) and a fixture demo script.

## Scenarios

### Scenario: A ready kit passes every check

* *GIVEN* the kit `2026-w09-w10` installed in replay mode, the app users and the chat user created, and the fixture demo script whose `sql demo` blocks are valid
* *WHEN* the owner runs `make demo-preflight KIT=2026-w09-w10 MODE=replay`
* *THEN* every check MUST print `PASS`, except `headline`, `sizing` and `stale`, which MAY print `WARN`
* *AND* the exit code MUST be 0
* *AND* the summary MUST name the kit, the mode, the current point and the beat each `WARN` affects

### Scenario: A query without its recording fails before the demo does

* *GIVEN* the installed kit with the recording of the console's 2023 fan-out slice deleted from the BucketFS host directory
* *WHEN* the owner runs the preflight in replay mode
* *THEN* the `recordings` check MUST fail, naming the slice key, the query that needs it (the console's `fan-out`) and the demo beat (2)
* *AND* it MUST print the fix: `make rehearse KIT=2026-w09-w10 CONFIRM=yes` to reinstall, or the `make record` line when the kit itself lacks the recording
* *AND* the exit code MUST be 2

### Scenario: The preflight never spends a call and never writes

* *GIVEN* the installed kit switched to live mode against the mock CFBD API, and the ledger
* *WHEN* the owner runs `make demo-preflight MODE=live`
* *THEN* the mock CFBD API MUST receive no data request
* *AND* the virtual-schema statements of the script MUST have been explained and not executed
* *AND* the ledger, `CFB_META.LOAD_RUN`, `CFB_META.FETCH_LOG` and every `CFB_SIM` and `CFB_PRED` table MUST be unchanged

### Scenario: A broken statement in the demo script fails the script check

* *GIVEN* the fixture demo script with one `sql demo` block that names a column `CFB_API.PLAYOFF_ODDS` does not have, and one `sql demo` block holding an `UPDATE`
* *WHEN* the owner runs the preflight
* *THEN* the `script` check MUST fail, naming the line of each block and, for the first, the database's error text
* *AND* for the second it MUST state that only reads are allowed in `sql demo` blocks
* *AND* the `sql show` block holding `EXECUTE SCRIPT CFB_INFRA.REFRESH_WEEK(...)` MUST NOT be run

### Scenario: A kit older than the newest completed week is flagged

* *GIVEN* the installed kit with replay week 10
* *WHEN* the owner runs the preflight with `--now` after the end of week 11 in the restored calendar
* *THEN* the `stale` check MUST print `WARN`, naming week 11 as the newest completed week and the kit name that a new kit would have (`2026-w10-w11`)
* *AND* with `--now` before the end of week 11 the check MUST print `PASS`

### Scenario: Live mode checks the connection, the DNS gate, the ledger and the quota

* *GIVEN* the installed kit in live mode on a namespace without a connection named in the virtual schema's `CONNECTION_NAME`, and a ledger whose plan `app` has 5 calls of room
* *WHEN* the owner runs the preflight with `MODE=live`
* *THEN* the `connection` check MUST fail, naming the connection and the user-guide section that creates it
* *AND* the `ledger` check MUST fail, stating the 8 planned live calls plus the retry headroom against the 5 calls of room
* *AND* with the connection present, a ledger with room and a quota covering the refresh, those checks MUST pass

### Scenario: The scorecard's honesty label is reported before the demo

* *GIVEN* the installed kit, where 6 of the 9 graded week-10 predictions were frozen before kickoff and 3 would be reconstructed
* *WHEN* the owner runs the preflight
* *THEN* the `headline` check MUST report 6 predictions frozen in advance and 3 to be reconstructed, and which row the scorecard's headline will use
* *AND* when no graded prediction was frozen in advance, it MUST print `WARN`, stating that the headline will carry the reconstruction label

### Scenario: The AWS side of the scale dial is checked against the kit

* *GIVEN* a peer namespace `TEST2_` standing in for AWS, whose newest `DONE` refresh run is at 2026 week 10 with 8,000 sims, the size its `SIM_CONFIG` names
* *WHEN* the owner runs the preflight with `AWS=yes ARGS="--peer-prefix TEST2_"`, before and after `make bench-sync`
* *THEN* before the sync the `aws` check MUST print `WARN`, stating that the local scale card does not yet hold the peer's newest run
* *AND* after the sync it MUST print `PASS`, naming the peer's point, its run's sims and seconds
* *AND* when the peer's newest refresh run is at another week than the kit's replay week, the check MUST print `WARN`, naming both points
