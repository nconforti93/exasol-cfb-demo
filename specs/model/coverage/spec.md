# Feature: Data coverage by season

CFBD's data is uneven across 157 seasons: games go back to 1869, Elo to 1890, pregame lines to 2013, and some datasets are not stored at all and must be fetched on demand. The model says, for every season and stored dataset, whether the data is there and whether it is complete, and for every dataset how to get it if it is not stored. An analysis then knows which seasons it can trust, and the chat can explain a gap instead of returning an empty result.

## Background

- `DATASET`: a reference table with one row per CFBD dataset that matters to the model, transcribed from `docs/cfbd-data-availability.md`.
  - Columns: code, label, CFBD endpoint, `CFB_CORE` table (if stored), `CFBD_VS` table, model object, declared first and last season, declared gap seasons, coverage population, `COMPLETE_SHARE` (the measured share from which a stored, final season is complete; 0.95 unless open question Q13 calibrates it), note.
  - `ACCESS_CLASS` follows D15:
    - `STORED`: in the `demo` load profile (`GAMES`, `SCORES`, `ELO`, `PREGAME_WP`, `SP_PLUS`, `POLLS`, `CALENDAR`, `CONFERENCE_MEMBERSHIP`, `TEAMS`, `VENUES`);
    - `ON_DEMAND`: federated, queried selectively through the virtual schema (`PLAYS`, `DRIVES`, `TEAM_BOX_SCORES`, `PLAYER_BOX_SCORES`, `BETTING_LINES`, `SRS`, `FPI`, `RECRUITING`, `TRANSFER_PORTAL`, `DRAFT`, `COACHES`, `ROSTERS`, `MATCHUP`, `GAME_PREVIEW`);
    - `UNAVAILABLE`: tier-gated on the project's key (`WEATHER`).
  - The `DATASET` row is the whole coverage statement for `ON_DEMAND` and `UNAVAILABLE` datasets: declared seasons, gaps, endpoint and virtual-schema table.
- `SEASON_COVERAGE`: one row per season (1869 to the current season) and season-scoped `STORED` dataset.
  - Load state comes from the loader's views (`CFB_META.LOAD_PLAN`, `CFB_META.LOAD_SLICES`):
    - `IS_PLANNED`: a load-plan entry of the dataset's table covers the season;
    - `IS_STORED`, `IS_FINAL`: the state of the slice that holds the season;
    - `IS_GAP`: a declared or planned gap.
  - **Measured share:** `COVERED_GAMES` or `COVERED_TEAM_SEASONS` divided by the population.

    | Dataset | Population | Covered when |
    |---|---|---|
    | `SCORES` | completed games that involve an FBS team (all completed games in a season without any FBS-recorded game) | `HAS_RESULT` |
    | `ELO` | as for `SCORES` | `HAS_ELO` |
    | `PREGAME_WP` | completed FBS-vs-FBS games, because CFBD omits games without a spread and games against FCS teams rarely have one | `HAS_PREGAME_WP` |
    | `SP_PLUS` | FBS team-seasons | an SP+ row |
    | `CONFERENCE_MEMBERSHIP` | FBS team-seasons | an affiliation-based conference |

  - `TRUST_LEVEL`, the first rule that applies:

    | Level | Rule |
    |---|---|
    | `NOT_AVAILABLE` | the season is outside the declared seasons, or a gap |
    | `ON_DEMAND` | no load-plan entry covers the season. Query it selectively through `CFBD_VS.<VS table>` |
    | `MISSING` | planned, not stored yet (the backfill has not reached it) |
    | `IN_PROGRESS` | stored, not final (the current season) |
    | `PARTIAL` | stored and final, measured share below the dataset's `COMPLETE_SHARE` |
    | `COMPLETE` | stored and final, measured share at least `COMPLETE_SHARE`, or not measured |

  - `TRUST_NOTE` explains the level in one sentence: it names the gap, the virtual-schema table, or the share.
- The examples run over the synthetic test league in the integration-test namespace. Its load plan covers `GAMES` from 1869 with the gap 1871, `CALENDAR` from 2013, `METRICS_WP_PREGAME` and `RATINGS_SP` from 2013, `RANKINGS_POLLS_RANKS` from 2014, and memberships from 1869. The current season is 2026.

## Scenarios

### Scenario: A known gap is not available

* *GIVEN* the load plan's gap 1871 for `GAMES`, registered by a backfill
* *WHEN* `CFB_MODEL.SEASON_COVERAGE` is queried for `GAMES` in 1871
* *THEN* `TRUST_LEVEL` MUST be `NOT_AVAILABLE` with `IS_GAP` TRUE
* *AND* `TRUST_NOTE` MUST name 1871 as a known gap without games

### Scenario: A finished season with full coverage is complete

* *GIVEN* the 2013 `METRICS_WP_PREGAME` slice is stored and final
* *AND* 24 of the 25 completed 2013 FBS-vs-FBS games have a pregame line
* *AND* the completed 2013 game of an FBS team against an FCS team has no line
* *WHEN* `CFB_MODEL.SEASON_COVERAGE` is queried for `PREGAME_WP` in 2013
* *THEN* `MEASURED_SHARE` MUST be 0.96 and `TRUST_LEVEL` `COMPLETE`

### Scenario: Low measured coverage is partial

* *GIVEN* the 2014 `METRICS_WP_PREGAME` slice is stored and final
* *AND* 20 of the 25 completed 2014 FBS-vs-FBS games have a pregame line
* *WHEN* `CFB_MODEL.SEASON_COVERAGE` is queried for `PREGAME_WP` in 2014
* *THEN* `MEASURED_SHARE` MUST be 0.80 and `TRUST_LEVEL` `PARTIAL`
* *AND* `TRUST_NOTE` MUST state the share

### Scenario: The current season is in progress

* *GIVEN* the 2026 slices of `GAMES`, `METRICS_WP_PREGAME`, `RATINGS_SP` and `RANKINGS_POLLS_RANKS` are stored and not final
* *WHEN* `CFB_MODEL.SEASON_COVERAGE` is queried for 2026
* *THEN* `GAMES`, `SCORES`, `ELO`, `PREGAME_WP`, `SP_PLUS` and `POLLS` MUST have `TRUST_LEVEL` `IN_PROGRESS`

### Scenario: Stored data for seasons outside the load plan is on demand

* *GIVEN* SP+ is declared from 1970 and the load plan stores it from 2013
* *WHEN* `CFB_MODEL.SEASON_COVERAGE` is queried for `SP_PLUS` in 1985 and 1960
* *THEN* `SP_PLUS` 1985 MUST be `ON_DEMAND`, and its note MUST name `CFBD_VS.RATINGS_SP`
* *AND* `SP_PLUS` 1960 MUST be `NOT_AVAILABLE`

### Scenario: Datasets that are never stored have no season rows

* *GIVEN* plays are declared from 2001 and are not in the load plan, and weather is tier-gated on the key
* *WHEN* `CFB_MODEL.SEASON_COVERAGE` and `CFB_MODEL.DATASET` are queried
* *THEN* `SEASON_COVERAGE` MUST have no rows for `PLAYS` or `WEATHER`
* *AND* `DATASET` MUST list `PLAYS` as `ON_DEMAND` from 2001 with the table `CFBD_VS.PLAYS`, and `WEATHER` as `UNAVAILABLE`

### Scenario: Planned data that is not loaded yet is missing

* *GIVEN* the load plan covers `GAMES` for 1900 and no 1900 slice is stored
* *WHEN* `CFB_MODEL.SEASON_COVERAGE` is queried for `GAMES` in 1900
* *THEN* `TRUST_LEVEL` MUST be `MISSING` with `IS_PLANNED` TRUE and `IS_STORED` FALSE

### Scenario: Elo coverage starts in 1890

* *GIVEN* stored and final games for 1885 without Elo, and for 1950 with Elo for every game
* *WHEN* `CFB_MODEL.SEASON_COVERAGE` is queried for `ELO` in 1885 and 1950
* *THEN* 1885 MUST be `NOT_AVAILABLE`
* *AND* 1950 MUST be `COMPLETE` with `MEASURED_SHARE` 1

### Scenario: The dataset list names what the model does not store

* *GIVEN* a deployed model
* *WHEN* `CFB_MODEL.DATASET` is queried
* *THEN* every `ON_DEMAND` and `UNAVAILABLE` dataset MUST name its CFBD endpoint and its `CFBD_VS` table, and MUST NOT name a `CFB_CORE` table
* *AND* every `STORED` dataset MUST name its `CFB_CORE` table and the model object that exposes it
* *AND* the declared first seasons of `GAMES` (1869), `ELO` (1890), `PREGAME_WP` (2013), `SP_PLUS` (1970) and `PLAYS` (2001) MUST match `docs/cfbd-data-availability.md`
