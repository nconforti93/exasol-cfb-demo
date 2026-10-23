# Feature: Demo preflight: one command says whether the demo will run

Before every rehearsal and before the demo itself, the presenter needs one answer: will each beat of the chosen demo tier work, in the chosen mode, on this machine, today? The preflight checks:
- the installed kit, and that the deployment has not changed since its snapshot;
- the recordings every replayed query will need, and the statements of the demo script;
- the scorecard's honesty label and the machine's sizing;
- the live prerequisites of every deployment that will make a live call on stage;
- the AWS side of the scale dial.

Each check ends PASS, WARN, FAIL or SKIP, and anything but PASS names the command that fixes it. The preflight itself never spends a CFBD or Anthropic call and never changes the database.

## Background

- **Command:** `make demo-preflight [KIT=<kit>] [MODE=replay|live] [TIER=full|B|C|D] [AWS=yes] [ARGS="--now <UTC time> --peer-prefix <prefix> --app-dist <dir> --freeze <file>"]` (`scripts/demo.py preflight`).
  - Without `KIT=` it takes the kit whose `replay_path` the virtual schema has. In live mode it takes the kit whose snapshot the newest `RESTORE` run in `CFB_META.LOAD_RUN` names (its `RUN_ARGS`).
  - `MODE` is the laptop's mode. The stage default is `MODE=replay AWS=yes`: the laptop replays the kit, and the one live call is a click in the AWS app.
  - `TIER` defaults to the `tier` of `demo/demo.yaml`, else `full`.
  - `--now` replaces the clock for the staleness check. `--peer-prefix`, `--app-dist` and `--freeze` replace the AWS deployment, the frontend build directory and `demo/freeze.yaml` (tests).
- **Output:** one line per check (`PASS`, `WARN`, `FAIL` or `SKIP`, the check id, what was found, and for anything but `PASS` the command that fixes it or the tier that skips it), then a summary. The exit code is 0 when no check failed (warnings and skips allowed), and 2 when one did.
- **Tiers** (decided at the go/no-go, `docs/demo-script.md`):

  | Tier | The demo it checks |
  |---|---|
  | `full` | every beat as scripted |
  | `B` | no chat panel; beat 5 is the external MCP client |
  | `C` | no app: a SQL client in replay mode, and the dial from the command line |
  | `D` | the virtual schema and the model only: beats 2 and 3, no odds |

  A check that a tier does not use prints `SKIP`, naming the tier. The checks below apply to every tier unless they name tiers.
- **Checks** (ids in brackets):
  - [db] the local deployment answers, with its version.
  - [kit] the kit and the deployment still tell one story:
    - the kit's files match `kit.json`, and its snapshot's manifest has the pinned SHA-256;
    - the newest `RESTORE` run names that snapshot, with no `BACKFILL` run after it;
    - the deployed catalog hash equals the kit's snapshot's;
    - every path frozen by `demo/freeze.yaml` (`demo/release-check`) has its pinned git tree id.
  - [point] the current point is the kit's base week N (before the refresh) or its replay week N+1 (after a refresh); anything else fails.
  - [mode] the virtual schema's `FETCH_MODE` is the requested mode, and in replay its `REPLAY_PATH` is the kit's.
  - [recordings] in replay:
    - every installed recording file exists in the BucketFS host directory with its SHA-256;
    - every slice that the tier's replayed queries will request has a recording. The requested slices come from `EXPLAIN VIRTUAL` of the queries themselves (zero calls): the refresh's fetch of week N+1 (the loader's batch query per weekly table of the season), the console's `fan-out` and `contradiction` queries, and the preview query of each pair in `demo/demo.yaml`.
  - [week] the recorded week N+1 is complete and week N+2 is not (from `kit.json`), so the refresh will move the point to N+1.
  - [publication] (`full`, `B`, `C`) a publication exists at week N; after the refresh, one at week N+1.
  - [headline] (`full`, `B`, `C`) how many graded week-N+1 predictions were frozen in advance. WARN when the headline will be the labeled reconstruction (`analyses/scorecard`).
  - [script] every statement of the demo script that the tier checks runs on the kit (below).
  - [sizing] (`full`, `B`, `C`) the instances and DB RAM of the newest local simulation run (`CFB_SIM.SIM_RUN.INSTANCES`, `DB_RAM_GIB`). WARN below 8 instances, the sizing the 100k timing assumes.
  - [stale] WARN when a regular-season week after the kit's replay week has ended by `--now` (or the clock), from the restored `CFB_MODEL.SEASON_WEEK`: the demo should use the newest completed week.
  - [app] (`full`, `B`) the frontend build exists (`app/frontend/dist/index.html`) and no file under `app/frontend/src/` is newer than it; otherwise FAIL with `make app-build`.
  - [chat] (`full`) the chat:
    - its availability (`chat/availability`: live with a key, stub without one);
    - every name and game of the `demo` bindings resolves at the kit's replay week (`demo/chat-beat`);
    - the caps in effect, `CHAT_SESSION_MAX_USD` and `CHAT_DAILY_MAX_USD`. WARN when the daily cap is above the demo-day value of 3.00.
  - [users] the users the tier needs exist: the app's read and console users (`full`, `B`), and the chat user with the role `CFB_MCP` (`full`, `B`, `C`; it is also the external MCP client's user).
  - **Live checks**, for each deployment that makes a live call on stage: the laptop with `MODE=live`, and AWS with `AWS=yes` (the AWS app's live click: the console `fan-out`, 3 calls, and one preview, 1 call).
    - [connection] a connection with the virtual schema's `CONNECTION_NAME` exists (by name);
    - [dns] the DNS gate of `make dns-check` passes;
    - [ledger] the ledger plan `app` and the month have room for every planned live call, plus the retry headroom of `app/live-spend`. With `MODE=live` the laptop plans 4 refresh slices, 3 fan-out slices and 1 preview; with `AWS=yes` the AWS app plans 3 fan-out slices and 1 preview;
    - [quota] `CFB_META.QUOTA` of that deployment covers its planned calls (from the free `/info`).
  - [aws] with `AWS=yes` and tiers `full`, `B` and `C`:
    - the AWS deployment answers with its pinned certificate;
    - its newest `DONE` refresh simulation run (`CFB_SIM.SIM_RUN`, origin `REFRESH`) is at the kit's replay week;
    - it has a `DONE` run at that point with the sims its `CFB_SIM.SIM_CONFIG` names (the dial's size);
    - the local `CFB_BENCH` holds its newest such run (`demo/scale-dial`).
- **Checked script statements.** In `docs/demo-script.md`, fenced blocks whose info string is `sql demo` are checked, and `sql show` blocks are shown on stage and never run (for example the `REFRESH_WEEK` call).
  - A block whose info string adds `tiers=<list>` (for example `sql demo tiers=C,D`) belongs to those tiers only; any other `sql demo` block belongs to every tier.
  - Placeholders `{SEASON}`, `{N}`, `{N1}`, `{BUBBLE_TEAM}`, `{RIVAL}` and `{PAIR_A}`/`{PAIR_B}` are filled from the kit and `demo/demo.yaml`.
  - A statement that reads the virtual schema is only explained (`EXPLAIN VIRTUAL`) in live mode, and executed only in replay mode.
  - A statement on a serving view (`CFB_API`) is executed as the app's read user in tiers `full` and `B`, and as the chat user in tier `C`. In tier `D`, which has no serving views, a read of the model's views is executed as the owner.
  - Any statement that is not a `SELECT` or an `EXPLAIN VIRTUAL` fails the check.
- **Never spends.** The preflight makes no CFBD call: `EXPLAIN VIRTUAL` and stored-only statements cost none, and virtual-schema statements execute only in replay mode. It reads `/info` only through `CFB_META.QUOTA` for a deployment that goes live, makes no Anthropic call, and writes nothing.
- The examples use the integration-test namespace with the kit `2026-w09-w10` installed by `make rehearse` (`demo/rehearsal`), a fixture demo script and a fixture frontend build directory.

## Scenarios

### Scenario: A ready kit passes every check

* *GIVEN* the kit `2026-w09-w10` installed in replay mode, the app users and the chat user created, the chat caps at their demo-day values, the fixture frontend build, and the fixture demo script whose `sql demo` blocks are valid
* *WHEN* the owner runs `make demo-preflight KIT=2026-w09-w10 MODE=replay`
* *THEN* every check MUST print `PASS`, except `headline`, `sizing` and `stale`, which MAY print `WARN`, and the live and `aws` checks, which MUST print `SKIP` because nothing goes live and `AWS=yes` is not set
* *AND* the exit code MUST be 0
* *AND* the summary MUST name the kit, the mode, the tier, the current point and the beat each `WARN` affects

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

### Scenario: The AWS side of the scale dial and its live click are checked against the kit

* *GIVEN* a peer namespace `TEST2_` standing in for AWS, whose newest `DONE` refresh run is at 2026 week 10 with 8,000 sims, the size its `SIM_CONFIG` names
* *WHEN* the owner runs the preflight with `MODE=replay AWS=yes ARGS="--peer-prefix TEST2_"`, before and after `make bench-sync`
* *THEN* before the sync the `aws` check MUST print `WARN`, stating that the local scale card does not yet hold the peer's newest run
* *AND* after the sync it MUST print `PASS`, naming the peer's point, its run's sims and seconds
* *AND* the live checks MUST run for the peer only, and the `ledger` check MUST count the peer's 4 planned live calls under `app`
* *AND* when the peer's newest refresh run is at another week than the kit's replay week, the `aws` check MUST print `WARN`, naming both points

### Scenario: A tier skips the checks of the parts it does not use

* *GIVEN* the installed kit with `demo` bindings that name a school missing from `CFB_API.TEAMS`, and no frontend build
* *WHEN* the owner runs the preflight with `TIER=full`, then with `TIER=C`
* *THEN* with `TIER=full` the `chat` and `app` checks MUST fail and the exit code MUST be 2
* *AND* with `TIER=C` the `chat` and `app` checks MUST print `SKIP`, naming tier C, the script check MUST run only the blocks of tier C, and the exit code MUST be 0
* *AND* with `TIER=D` the `publication`, `headline`, `sizing` and `aws` checks MUST print `SKIP` as well

### Scenario: A deployment changed since the kit's snapshot fails the kit check

* *GIVEN* the installed kit, and then the test namespace's virtual schema redeployed from a catalog fixture with one changed table, so the deployed catalog hash differs from the snapshot's
* *WHEN* the owner runs the preflight, and again with `ARGS="--freeze <fixture>"` whose pin for `vs/catalog` differs from the repository's tree id
* *THEN* the `kit` check MUST fail, naming both catalog hashes and the freeze rule, with the fix "redeploy the frozen commit, then `make rehearse`"
* *AND* with the fixture freeze file the `kit` check MUST also fail, naming the path `vs/catalog` and both tree ids
* *AND* the exit code MUST be 2 in both runs
