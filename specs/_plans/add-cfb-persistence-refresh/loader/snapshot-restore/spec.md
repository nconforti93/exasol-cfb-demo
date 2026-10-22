# Feature: Snapshot and restore

Data paid for once is never fetched again (D11). `make snapshot` copies the stored data and its load log into a private directory as Parquet. `make restore` rebuilds any deployment from that copy with zero API calls: a new laptop, a fresh Exasol Personal, the AWS instance. Restoring the "Sunday after week N" snapshot is also the rehearsal reset of the demo.

## Background

- Commands:
  - `make snapshot NAME=<name> [TARGET=local|aws]`;
  - `make restore NAME=<name> [TARGET=local|aws] CONFIRM=yes [FORCE=yes]`;
  - `make snapshots` lists the snapshots with their creation time, source and size.
- **Location:** `~/.cfb-demo/snapshots/<name>/`, or the directory in `CFB_SNAPSHOT_DIR`.
  - The directory has mode 0700 and its files mode 0600.
  - A location inside the repository or inside a OneDrive folder is refused.
  - An existing snapshot is never overwritten.
  - Snapshots stay private: CFBD allows private retention, not redistribution.
- **Scope:** every `CFB_CORE` mirror, plus `CFB_META.LOAD_LOG` and `CFB_META.FETCH_LOG`. The load plan's `snapshot` section lists the scope, so later phases can add their schemas.
  - Not in the scope: the run and step logs (`LOAD_RUN`, `REFRESH_LOG`), the failure list, and the configuration tables. Each deployment keeps its own run history, and `make deploy` recreates the configuration from the YAML.
  - Restored `LOADED_BY` and `CONTEXT` values refer to the runs of the deployment the snapshot came from.
  - The restored fetch log changes the quota view's attribution of past calls. The budget guard is unaffected, because it uses `/info`.
- **Content:** one Parquet file per non-empty scope table, and `manifest.json` with:
  - the name and creation time;
  - the source target and host, without credentials;
  - the database version, the catalog hash and the load plan hash;
  - per table: row count, column names and types, file name, file SHA-256 and a fingerprint.
- **Fingerprint:** the row count plus, per column, the non-NULL count and one type-specific aggregate. The same generated SQL computes it on both sides.

  | Column type | Aggregate |
  |---|---|
  | `DECIMAL` | Sum |
  | `DOUBLE` | Sum of values rounded to 8 decimals |
  | `BOOLEAN` | Count of TRUE |
  | `TIMESTAMP` | Sum of seconds since 1970 |
  | `VARCHAR` | Total length |

- **Restore:**
  - It refuses before changing anything when:
    - a scope table's columns differ from the manifest;
    - the manifest's catalog hash differs from the deployed catalog, unless `FORCE=yes`. Slice keys may have changed, and restored slices would become orphans (`loader/load-plan`).
  - It loads the files into a temporary landing schema and checks every fingerprint against the manifest.
  - In one transaction it replaces every scope table: tables without a file become empty.
  - It then drops the landing schema and records itself as a `RESTORE` row in `LOAD_RUN`.
  - A restore never queries `CFBD_VS`, so it makes zero API calls.
- Snapshot and restore take the loader's run lock (`loader/run-logs`), so no load changes the data while they run.
- The integration examples run in the test namespace on local Exasol Personal. Restoring from AWS into local is checked in the live verification.

## Scenarios

### Scenario: A snapshot writes a private, self-describing copy

* *GIVEN* a deployment with stored `GAMES`, `RATINGS_SP` and `TEAMS` slices
* *WHEN* the owner runs `make snapshot NAME=week7`
* *THEN* `~/.cfb-demo/snapshots/week7/` MUST contain one Parquet file per non-empty scope table and `manifest.json`
* *AND* the manifest MUST list every scope table with its row count, columns, file checksum and fingerprint, plus the catalog hash
* *AND* the directory MUST have mode 0700, every file mode 0600, and no file MUST contain a credential

### Scenario: Snapshots never land in the repository or a synced folder

* *GIVEN* `CFB_SNAPSHOT_DIR` points into the repository, or into a path that contains a OneDrive folder
* *WHEN* the owner runs `make snapshot NAME=week7`
* *THEN* the command MUST fail with a message naming the directory and the rule
* *AND* the command MUST NOT write any file

### Scenario: A snapshot is never overwritten

* *GIVEN* a snapshot named `week7` exists
* *WHEN* the owner runs `make snapshot NAME=week7` again
* *THEN* the command MUST fail and name the existing snapshot
* *AND* the existing snapshot MUST be unchanged

### Scenario: A rebuilt deployment is restored exactly, with zero calls

* *GIVEN* a snapshot of a deployment with stored slices, including an empty gap slice
* *AND* the deployment is dropped and deployed again empty
* *WHEN* the owner runs `make restore NAME=week7 CONFIRM=yes`
* *THEN* every scope table MUST hold exactly the rows it held when the snapshot was taken, value for value
* *AND* the mock API and the replay store MUST NOT receive any request
* *AND* a following `SELECT * FROM CFBD_VS.GAMES WHERE P_YEAR = 2019` MUST be served from storage without a request

### Scenario: A restore resets newer data to the snapshot

* *GIVEN* a snapshot `week7` taken before a refresh
* *AND* the refresh has since replaced the 2020 slices, added slices, and written log rows
* *WHEN* the owner runs `make restore NAME=week7 CONFIRM=yes`
* *THEN* `CFB_CORE`, `LOAD_LOG` and `FETCH_LOG` MUST equal their state at the snapshot
* *AND* the slices added after the snapshot MUST NOT be stored or registered any more
* *AND* `LOAD_RUN` and `REFRESH_LOG` MUST keep their rows, plus one `RESTORE` row with status `DONE`

### Scenario: A restore into a deployment with different columns is refused

* *GIVEN* a snapshot whose manifest lists `GAMES` with a column the target's mirror does not have
* *WHEN* the owner runs `make restore NAME=week7 CONFIRM=yes`
* *THEN* the command MUST fail before changing any table, naming the table and the column difference
* *AND* the target's data MUST be unchanged

### Scenario: A snapshot from another catalog needs FORCE

* *GIVEN* a snapshot whose manifest's catalog hash differs from the deployed catalog, with identical columns
* *WHEN* the owner runs `make restore NAME=week7 CONFIRM=yes`, and then the same with `FORCE=yes`
* *THEN* the first command MUST fail before changing any table, naming both catalog hashes and the orphan risk
* *AND* the second command MUST restore the snapshot

### Scenario: A failed restore changes nothing

* *GIVEN* a snapshot whose `GAMES` file was altered after the snapshot
* *WHEN* the owner runs `make restore NAME=week7 CONFIRM=yes`
* *THEN* the command MUST fail, naming the file whose checksum or fingerprint differs
* *AND* every scope table MUST keep its previous content
* *AND* the command MUST NOT leave a landing schema behind

### Scenario: Snapshot and restore are refused while a loader run is active

* *GIVEN* a backfill or refresh is running
* *WHEN* the owner runs `make snapshot NAME=week8` or `make restore NAME=week7 CONFIRM=yes`
* *THEN* the command MUST fail with a message naming the running run
* *AND* the command MUST NOT write any file or change any table
