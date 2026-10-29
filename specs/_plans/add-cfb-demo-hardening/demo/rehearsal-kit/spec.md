# Feature: Rehearsal kits: "Sunday after week N" plus the recorded week N+1

The demo tells one story: the app as of the Sunday after week N, then one call that refreshes week N+1, then every page moves. A rehearsal kit freezes that story so it can be told again and again, offline, with the same numbers. It packages three things that already exist: a private snapshot of the stored data taken on the Sunday after week N, the recorded CFBD responses of week N+1's refresh, and the recorded responses of the other requests the script makes (the console's fan-out and the head-to-head previews). Building a kit never calls CFBD: recording stays with `make record` under the live-call ledger, and the kit only checks, copies and describes.

## Background

- **Command:** `make kit NAME=<kit> SEASON=<s> WEEK=<n> SNAPSHOT=<snapshot> [SOURCE=<dir>] [FORCE=yes]` (`scripts/demo.py kit`), and `make kits` to list them. `WEEK` is the base week N; the replay week is N+1 of the same season, season type `regular`.
- **Kit name:** `<season>-w<NN>-w<NN+1>`, optionally followed by `-` and up to 12 lower-case letters or digits (for example `2026-w10-w11`, `2026-w10-w11-b`). Any other name is refused.
- **Location:** `~/.cfb-demo/kits/<kit>/`, or `$CFB_KIT_DIR/<kit>/`. Directories are created with mode 0700 and files with 0600. A location inside the repository or inside a OneDrive folder is refused, by the same rule as snapshots (`loader/snapshot-restore`). An existing kit is replaced only with `FORCE=yes`.
- **Contents:**
  - `kit.json`:
    - `kit_version` 1, `name`, `created_at` (UTC), `season`, `season_type`, `base_week`, `replay_week`;
    - the snapshot: `name`, the SHA-256 of its `manifest.json`, `catalog_hash`, `plan_hash`;
    - `replay_path` (`/buckets/bfsdefault/default/cfbd/kits/<kit>`);
    - one entry per recording: `slice_key`, `role`, `table`, `file`, `sha256`, `requested_at`, `bytes`;
    - the three week checks below, with the number of week-N+2 games already completed in the recording;
    - the demo choices of `demo/demo.yaml` that apply to the kit, and the catalog hash the kit was built against.

    It holds no response body and no credential.
  - `replay/`: a copy of each recording in the replay layout of `cfbd-fetch/fetch-modes` (`<endpoint path segments>/<sha256(slice_key)[:24]>.cfbd.json`), so the kit survives a wiped `cache/` or a reinstalled BucketFS.
  - The snapshot itself stays where `make snapshot` wrote it (`~/.cfb-demo/snapshots/<snapshot>/`); the kit names it and pins its manifest by SHA-256.
- **Required recordings**, each looked up by its canonical slice key in `SOURCE` (default `cache/replay/`):
  - `refresh`: the slice keys that `REFRESH_WEEK(season, replay_week, 'regular')` fetches. They are derived from the load plan's weekly entries for the season, with the loader's own slice templates (`loader/load-plan`). With the demo plan these are the four season slices of `GAMES`, `METRICS_WP_PREGAME`, `RATINGS_SP` and `RANKINGS_POLLS_RANKS`;
  - `console`: the slice keys of the app console's `fan-out` query, taken from the console catalog (`app/inspector`), or from `demo/demo.yaml` while that catalog is not merged;
  - `preview`: one `/games/{id}/preview` slice per game id listed for the kit in `demo/demo.yaml` (`preview_game_ids`).
- **Where the recordings come from.** The Sunday cadence (`demo/season-cadence`) copies each Sunday's refresh recordings into `~/.cfb-demo/recordings/<season>-w<NN>/` as soon as they are made. A later `make record … --rerecord` overwrites the same slice in `cache/replay/`, so a kit built days later uses `SOURCE=~/.cfb-demo/recordings/<season>-w<NN+1>`.
- **Week checks** (content only, never the clock):
  - **Replay week complete:** the recorded `GAMES` season body has every week-N+1 regular-season game involving an FBS team completed. Games listed under `allow_incomplete` in `demo/demo.yaml` (postponed or cancelled) are exempt and named in `kit.json`.
  - **The week after it not complete:** the recorded `GAMES` season body has at least one week-N+2 regular-season game involving an FBS team not completed. Otherwise the replayed refresh would move the point to N+2, not N+1 (`model/as-of`). A partly played week N+2 (weekday games) passes. `kit.json` names how many week-N+2 games were already completed, because their predictions become reconstructions on replay.
  - **Snapshot at the base week:** the snapshot's stored `GAMES` rows of the season have every week-N game involving an FBS team completed and no week-N+1 regular-season game completed. A snapshot taken after week N+1 had started, but before any of its games ended, passes.
- Recording slices costs CFBD calls and is done beforehand, by the cadence or with `make record PATTERNS=<file> CONFIRM=yes ARGS="--plan add-cfb-demo-hardening --rerecord"`. `--rerecord` is needed because an older version of the season slices may already be in `cache/replay/`. Building, listing and checking a kit costs zero calls.
- The examples use the integration-test namespace:
  - the sim league, with 2026 weeks 1–9 completed;
  - a snapshot of it taken by the harness at week 9;
  - the harness's synthetic recordings: the week-10 refresh (week 10 completed, week 11 unplayed), the console's fan-out, and one game preview.

## Scenarios

### Scenario: A kit packages the snapshot and the recordings of the next week's refresh

* *GIVEN* the harness snapshot `test-w09` of the sim league at 2026 week 9, and `demo/demo.yaml` listing game 9101 under `preview_game_ids` for the kit
* *AND* synthetic recordings of the four 2026 refresh slices with week 10 completed and week 11 unplayed, of the three console fan-out slices and of the preview of game 9101
* *WHEN* the owner runs `make kit NAME=2026-w09-w10 SEASON=2026 WEEK=9 SNAPSHOT=test-w09 SOURCE=<harness recordings>`
* *THEN* the directory `<kit dir>/2026-w09-w10/` MUST exist with mode 0700, and `kit.json` and every recording copy MUST have mode 0600
* *AND* `kit.json` MUST list exactly eight recordings, four with role `refresh`, three with role `console` and one with role `preview`, each with the SHA-256 of its copy, and MUST name the snapshot `test-w09` with the SHA-256 of its manifest, `base_week` 9, `replay_week` 10 and `replay_path` `/buckets/bfsdefault/default/cfbd/kits/2026-w09-w10`
* *AND* the command MUST print all three week checks as passed: week 10 complete and week 11 not complete in the recording, and week 9 complete and week 10 unplayed in the snapshot

### Scenario: A missing recording is refused with the command that records it

* *GIVEN* the inputs of the first scenario without the recording of the `RATINGS_SP` 2026 slice
* *WHEN* the owner runs `make kit` for `2026-w09-w10`
* *THEN* the command MUST fail without creating the kit directory
* *AND* the error MUST name the missing slice key and its role
* *AND* it MUST print the `make record` line, with the plan `add-cfb-demo-hardening`, that would record the missing slices

### Scenario: A recording of an unfinished replay week is refused

* *GIVEN* the inputs of the first scenario, except that the recorded 2026 `GAMES` body still has three week-10 FBS games not completed
* *WHEN* the owner runs `make kit` for `2026-w09-w10`
* *THEN* the command MUST fail, naming the three games and the week, and stating that week 10 needs a new recording after its last game
* *AND* with the three game ids listed under `allow_incomplete` in `demo/demo.yaml` the kit MUST be built, and `kit.json` MUST name them as exceptions

### Scenario: A recording taken after the following week ended is refused

* *GIVEN* the inputs of the first scenario, except that the recorded 2026 `GAMES` body also has every week-11 FBS game completed
* *WHEN* the owner runs `make kit` for `2026-w09-w10`
* *THEN* the command MUST fail without creating the kit directory, stating that week 11 is complete in the recording, so the replayed refresh would move the point to week 11 instead of week 10
* *AND* the error MUST name the recordings copy of the cadence (`~/.cfb-demo/recordings/2026-w10/`) as the source to use, if it exists
* *AND* with only two week-11 FBS games completed and the rest unplayed, the kit MUST be built, and `kit.json` MUST state that 2 week-11 games were already completed

### Scenario: A snapshot that already holds results of the replay week is refused

* *GIVEN* a harness snapshot `test-w10` taken after the replay refresh of week 10, so week-10 games are completed in it
* *WHEN* the owner runs `make kit NAME=2026-w09-w10 SEASON=2026 WEEK=9 SNAPSHOT=test-w10`
* *THEN* the command MUST fail, stating that the snapshot holds completed week-10 games and is not "Sunday after week 9"
* *AND* no kit directory MUST be created

### Scenario: Kits stay private and are not overwritten by accident

* *GIVEN* an existing kit `2026-w09-w10`
* *WHEN* the owner builds `2026-w09-w10` again without `FORCE=yes`, or with `CFB_KIT_DIR` pointing into the repository or into a OneDrive folder
* *THEN* each command MUST be refused with a message naming the rule, and the existing kit MUST be unchanged
* *AND* with `FORCE=yes` and a valid location the kit MUST be rebuilt, and `make kits` MUST list it once, with its season, weeks, snapshot, recording count and creation time

### Scenario: Building a kit spends no CFBD call

* *GIVEN* the inputs of the first scenario and the live-call ledger
* *WHEN* the owner builds and lists the kit
* *THEN* the mock CFBD API MUST receive no request
* *AND* the ledger MUST be unchanged
* *AND* the command MUST NOT open a database session other than the reads it needs to check the snapshot's catalog hash against the deployment
