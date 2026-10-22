# Feature: Load plan

The load plan decides which virtual tables are persisted, in which slices, for which seasons, when a slice is final, and in which profile it is loaded. It is one reviewed file, `loader/load-plan.yaml`. `make deploy` validates it against the endpoint catalog and installs it in `CFB_META`. Every slice the loader may fetch is derived from the plan, so the plan is also the call estimate.

## Background

- An entry names one `CFBD_VS` table and how it is loaded:

  | Field | Values | Meaning |
  |---|---|---|
  | `table` | a `CFBD_VS` table that has a `CFB_CORE` mirror | What is stored |
  | `profile` | `demo`, `full` | `demo`: the table is `stored` (D15) and the demo needs it. `full`: only the optional archive stores it |
  | `priority` | integer ≥ 1 | Load order between entries (lower first) |
  | `slicing` | `none`, `season`, `season_range` | One slice in total, one per season, or one slice for a fixed season range |
  | `seasons` | `from`, optional `to` | Covered seasons. Without `to`, up to the current season. `season_range` requires `to` |
  | `gaps` | seasons | Known holes from `docs/cfbd-data-availability.md`. They are never fetched |
  | `params` | fixed parameter values | Sent with every slice of the entry, e.g. `classification: fbs` |
  | `refresh_class` | `static`, `weekly` | When a slice is final, and whether `REFRESH_WEEK` fetches it again |

- Several entries may load the same table, as long as their seasons don't overlap for the same fixed parameters. For example, `CONFERENCES_AFFILIATIONS` can load 1869–2025 as one range slice plus one slice per season from 2026.
- The loader fetches one entry at a time: one fetch query never mixes slices of two entries (`loader/backfill`).
- Slices of `none` and `season_range` entries have no season (`SEASON` is NULL).
- Settings (`config`, with defaults):

  | Setting | Default |
  |---|---|
  | `monthly_call_budget` | 1000 |
  | `refresh_reserve` | 150 |
  | `quarantine_after` | 2 |
  | `season_rollover` | `08-01` |
  | `season_final_after` | `02-15` |
  | `refresh_profiles` | `[demo]` |

- The current season is the calendar year from its `season_rollover` day on, and the previous year before that day.
- A slice counts as **final** when it is loaded at or after:
  - `static`: any time. `none` and `season_range` entries must be `static`.
  - `weekly`: `season_final_after` of the following year (season 2026 is final from 2027-02-15).
- Who fetches a slice again:
  - `REFRESH_WEEK` re-fetches the `weekly` slices of its season that are not final, for the profiles in `refresh_profiles` (`loader/refresh-week`).
  - `BACKFILL` fetches, once more, any slice that was stored before it was final and would be final if loaded now (`loader/backfill`).
- Gated tables (`gated_by` in the catalog) are planned only while `API_FEATURES` holds their feature. The `demo` profile contains no gated tables and no volatile tables, so it works on a free key.
- Slice keys are the adapter's canonical keys (`cfbd-vs/fan-out`). `make deploy` compiles them with the adapter's own planner, so a planned slice and a virtual-table request with the same parameters have the same key.
- **Orphan slices** are `LOAD_LOG` rows that a loader run wrote (`LOADED_BY` starts with `BACKFILL#` or `REFRESH_WEEK#`, or is `LOAD_PLAN_GAP`) for a planned table, but that match no planned slice key. They appear after a catalog change, an edit of the plan, or a restore from an older catalog. Fetching their table again would pay twice for the same data, so the loader refuses to fetch a table that has orphans until the owner migrates or deletes them.
- `CFB_META.LOAD_SLICES` lists every planned slice with:
  - entry, table, profile, priority, season and slice key;
  - whether it is a gap;
  - whether it would be final if loaded now;
  - its `LOAD_LOG` state and its failure count.
- `CFB_META.LOAD_STATUS` has one row per entry with:
  - planned, stored, final, missing, quarantined and orphan slices;
  - the calls needed to complete the entry;
  - what blocks it, if anything: a missing feature, or orphans.
- The examples assume a deployment whose current date is set as stated. Integration tests set the date in the test namespace only.

## Scenarios

### Scenario: The deployed plan lists every slice the loader may fetch

* *GIVEN* a plan entry for `GAMES` with `slicing: season`, `seasons: {from: 2017}`, `refresh_class: weekly` and profile `demo`
* *AND* the current date is 2020-10-20
* *WHEN* the owner runs `make deploy` and queries `SELECT SEASON, SLICE_KEY FROM CFB_META.LOAD_SLICES WHERE TABLE_NAME = 'GAMES'`
* *THEN* the result MUST contain exactly one slice for each of the seasons 2017, 2018, 2019 and 2020
* *AND* the slice key of 2019 MUST be `/games?seasonType=both&year=2019`

### Scenario: Planned slice keys match the virtual schema's requests

* *GIVEN* the project's `loader/load-plan.yaml`, compiled by `make deploy`
* *WHEN* the key of one planned slice of every entry is compared with the request that `EXPLAIN VIRTUAL` plans for the same table and parameter values
* *THEN* both keys MUST be identical for every entry
* *AND* the comparison MUST NOT send any request to the API

### Scenario: An invalid plan stops the deploy

* *GIVEN* a deployed plan
* *AND* a changed plan with five problems:
  - an entry for an unknown table;
  - an entry for the volatile table `GAMES_SCHEDULE`;
  - a `PLAYS` entry with `slicing: season` (the API requires `week` together with `year`);
  - two `GAMES` entries that both cover 2019;
  - a gap outside its entry's seasons
* *WHEN* the owner runs `make deploy`
* *THEN* the deploy MUST fail with one message per problem, each naming the entry and the broken rule
* *AND* the previously deployed plan MUST stay in `CFB_META` unchanged

### Scenario: A new season enters the plan on the rollover date

* *GIVEN* a `GAMES` entry from 2017 without `to`
* *WHEN* `CFB_META.LOAD_SLICES` is queried with the current date 2021-07-31, and again with 2021-08-01
* *THEN* the newest `GAMES` season in the first result MUST be 2020
* *AND* the second result MUST also contain the 2021 slice

### Scenario: Finality follows the refresh class

* *GIVEN* the current date is 2020-10-20
* *AND* entries for `GAMES` (`weekly`, `season`) and `CALENDAR` (`static`, `season`)
* *WHEN* `CFB_META.LOAD_SLICES` is queried
* *THEN* the 2020 `GAMES` slice MUST NOT be final if loaded now, and the 2019 `GAMES` slice MUST be
* *AND* the 2020 `CALENDAR` slice MUST be final if loaded now

### Scenario: Range and seasonless slices have no season

* *GIVEN* a `TEAMS` entry with `slicing: none` and a `CONFERENCES_AFFILIATIONS` entry with `slicing: season_range` and seasons 1869–2019
* *WHEN* `CFB_META.LOAD_SLICES` is queried
* *THEN* each entry MUST have exactly one slice, with `SEASON` NULL
* *AND* the range slice key MUST be `/conferences/affiliations?maxYear=2019&minYear=1869`

### Scenario: The demo profile is part of the full profile and comes first

* *GIVEN* a plan with entries in both profiles
* *WHEN* the slices of profile `full` are listed in backfill order (`CFB_META.BACKFILL_QUEUE`)
* *THEN* every slice of profile `demo` MUST be included
* *AND* every slice of profile `demo` MUST come before every slice that only profile `full` contains

### Scenario: Gated entries wait for their feature

* *GIVEN* a `full` entry for the gated table `GAMES_WEATHER`
* *AND* `API_FEATURES` does not contain `weather`
* *WHEN* `CFB_META.LOAD_SLICES` and `CFB_META.LOAD_STATUS` are queried
* *THEN* `LOAD_SLICES` MUST NOT list any slice of that entry
* *AND* `LOAD_STATUS` MUST show that entry as blocked by the feature `weather`

### Scenario: The plan shows what completing it costs

* *GIVEN* a deployed plan where some slices are stored, one slice is quarantined and one entry has a gap
* *WHEN* the owner queries `CFB_META.LOAD_STATUS`
* *THEN* each entry MUST show its planned, stored, final, missing, quarantined and orphan slices and the calls needed to complete it
* *AND* gap and quarantined slices MUST NOT count as calls

### Scenario: Orphan slices are reported and block fetching their table

* *GIVEN* `GAMES` slices for 2017–2020 stored by a backfill
* *AND* a plan edit that starts the `GAMES` entry in 2019, so the stored 2017 and 2018 slices match no planned key
* *WHEN* the owner runs `make deploy`, then queries `CFB_META.LOAD_STATUS`, then runs a backfill and a refresh
* *THEN* the deploy MUST warn that 2 stored `GAMES` slices no longer match the plan
* *AND* `LOAD_STATUS` MUST show 2 orphan slices for `GAMES` and the entry as blocked by orphans
* *AND* the backfill and the refresh MUST NOT request any `GAMES` slice, and both MUST name the orphans and the remedy (migrate or delete them)
