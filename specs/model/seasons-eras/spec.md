# Feature: Seasons, weeks and eras

College football has been played since 1869, and the rules of the game around it changed many times: who governs it, how divisions are named, how a champion is decided, whether games can end in a tie. The model labels every season with its eras, so an analysis can compare like with like and the chat can explain why a 1950 tie or a 1985 "Division I-A" label is correct. It also gives every season one ordered list of weeks that the as-of helpers and the weekly refresh share.

## Background

- `SEASON`: one row per season from 1869 to the newest season with stored games, including seasons without games.
  - Era columns come from the reference table `ERA` (one row per era kind and season range):

    | Era kind | Codes and seasons |
    |---|---|
    | `DIVISION` | `PRE_NCAA` 1869–1905, `NCAA_UNDIVIDED` 1906–1955, `UNIVERSITY_COLLEGE` 1956–1972, `DIVISION_I_II_III` 1973–1977, `I_A_I_AA` 1978–2005, `FBS_FCS` 2006– |
    | `POSTSEASON` | `NO_BOWLS` 1869–1900, `BOWLS` 1901–1991, `BOWL_COALITION` 1992–1994, `BOWL_ALLIANCE` 1995–1997, `BCS` 1998–2013, `CFP_4` 2014–2023, `CFP_12` 2024– |
    | `CHAMPION` | `SELECTORS` 1869–1935, `POLLS` 1936–1997, `BCS_GAME` 1998–2013, `CFP` 2014– |
    | `OVERTIME` | `TIES` 1869–1995, `OVERTIME` 1996– (FBS level) |

  - Which conferences held a championship game in which seasons is the reference table `CONFERENCE_CHAMPIONSHIP` (`model/games`), not an era.
  - `CFP_TEAMS` is 0 before 2014, 4 in 2014–2023 and 12 from 2024. The seeding rules of the 12-team playoff (byes for the top conference champions in 2024, straight seeding from 2025) belong to Phase 4's selection configuration; the column comment says so.
  - `TIES_POSSIBLE` is TRUE in the `TIES` era. It describes the FBS level: lower divisions adopted overtime in 1981, which the column comment states.
  - Counts per season: games, completed games, FBS teams (teams classified `fbs` on the season's games), and the first and last kickoff.
  - `IS_CURRENT_SEASON` is TRUE for the newest season with stored games only.
- **Division labels** come from the reference table `CLASSIFICATION_LABEL` (one row per division era and CFBD classification). CFBD assigns today's classification codes (`fbs`, `fcs`, `ii`, `iii`) to every season. The model keeps that code and adds the name that was used at the time:

  | Division era | `fbs` | `fcs` | `ii` | `iii` |
  |---|---|---|---|---|
  | `FBS_FCS` | FBS | FCS | Division II | Division III |
  | `I_A_I_AA` | Division I-A | Division I-AA | Division II | Division III |
  | `DIVISION_I_II_III` | Division I | Division I | Division II | Division III |
  | `UNIVERSITY_COLLEGE` | University Division | College Division | College Division | College Division |
  | `NCAA_UNDIVIDED`, `PRE_NCAA` | Major college | Small college | Small college | Small college |

  `IS_CLASSIFICATION_RETROACTIVE` is TRUE when the code did not exist in that season: `fbs` and `fcs` before 1978, `ii` and `iii` before 1973.
- `SEASON_WEEK`: one row per season, season type and week that has games or a stored calendar week.
  - **Week sequence** (`WEEK_SEQ`): a regular-season week keeps its number; postseason week `n` is `100 + n`. So the postseason always follows the regular season, and `(season, week, season_type)` from `REFRESH_WEEK` maps to exactly one week.
  - Dates come from the stored calendar (`CFB_CORE.CALENDAR`, stored from 2013, deduplicated by season, season type and week with the newest fetch winning) when it has the week (`DATES_SOURCE = 'CALENDAR'`). Otherwise they are the first and last kickoff of the week's games (`'GAMES'`). `FIRST_GAME_AT` is the week's first kickoff.
  - `IS_DONE` is TRUE when every game of the week is completed, or when the season's games were last fetched at least one day after the week's end. So a postponed or cancelled game does not keep a past week open, and a week whose Thursday game is played while its Saturday games are not stays open.
  - Only the season types `regular` and `postseason` have weeks. Games of any other season type are reported by `MODEL_CHECKS`.
- All timestamps are UTC, as stored by the virtual schema.
- The examples run over the synthetic test league in the integration-test namespace.

## Scenarios

### Scenario: Early seasons carry their eras

* *GIVEN* stored games for the seasons 1885 and 1950
* *WHEN* `CFB_MODEL.SEASON` is queried for those seasons
* *THEN* 1885 MUST show `PRE_NCAA`, `NO_BOWLS`, `SELECTORS` and `TIES_POSSIBLE` TRUE
* *AND* 1950 MUST show `NCAA_UNDIVIDED`, `BOWLS`, `POLLS` and `TIES_POSSIBLE` TRUE

### Scenario: Modern seasons carry their eras

* *GIVEN* stored games for the seasons 1985, 2000, 2014 and 2024
* *WHEN* `CFB_MODEL.SEASON` is queried for those seasons
* *THEN* 1985 MUST show `I_A_I_AA` and `BOWLS`, and 2000 MUST show `I_A_I_AA` and `BCS`
* *AND* 2014 MUST show `FBS_FCS`, `CFP_4` and `CFP_TEAMS` 4
* *AND* 2024 MUST show `CFP_12`, `CFP_TEAMS` 12 and `TIES_POSSIBLE` FALSE

### Scenario: A season without games is still listed

* *GIVEN* no stored games for 1871, a known gap of the load plan
* *WHEN* `CFB_MODEL.SEASON` is queried for 1871
* *THEN* it MUST return one row with `HAS_GAMES` FALSE and `GAME_COUNT` 0
* *AND* the row MUST carry the eras of 1871

### Scenario: Division labels follow the era

* *GIVEN* Ashford's games are recorded as `fbs` in 1950, 1985 and 2024, and Lowell's as `fcs` in 1985
* *WHEN* `CFB_MODEL.TEAM_SEASON` is queried for those team-seasons
* *THEN* Ashford MUST be labeled "Major college" in 1950 with `IS_CLASSIFICATION_RETROACTIVE` TRUE
* *AND* Ashford MUST be labeled "Division I-A" and Lowell "Division I-AA" in 1985, both with `IS_CLASSIFICATION_RETROACTIVE` FALSE
* *AND* Ashford MUST be labeled "FBS" in 2024
* *AND* `CLASSIFICATION` MUST keep the CFBD code in every row

### Scenario: Weeks take their dates from the calendar when it is stored

* *GIVEN* a stored 2024 calendar with regular weeks 1–15 and postseason week 1, and no stored calendar for 1950
* *WHEN* `CFB_MODEL.SEASON_WEEK` is queried for 2024 and 1950
* *THEN* the 2024 weeks MUST take their start and end from the calendar, with `DATES_SOURCE` `CALENDAR`
* *AND* the 1950 weeks MUST take their start and end from the first and last kickoff of their games, with `DATES_SOURCE` `GAMES`

### Scenario: The postseason follows the regular season in one week order

* *GIVEN* the stored 2024 weeks
* *WHEN* `CFB_MODEL.SEASON_WEEK` is ordered by `WEEK_SEQ`
* *THEN* regular weeks 1–15 MUST have `WEEK_SEQ` 1–15
* *AND* postseason week 1 MUST have `WEEK_SEQ` 101 and come last
* *AND* no two rows of a season MUST share a `WEEK_SEQ`

### Scenario: A week is done when its games are played or its time has passed

* *GIVEN* the 2026 games of weeks 1–9 are completed and week 10 had not started when the 2026 games were fetched
* *AND* the 1950 week-5 game Brookvale against Lowell was never completed, and the 1950 games were fetched in 2026
* *WHEN* `CFB_MODEL.SEASON_WEEK` is queried for 2026 and 1950
* *THEN* 2026 weeks 1–9 MUST have `IS_DONE` TRUE and week 10 `IS_DONE` FALSE
* *AND* 1950 week 5 MUST have `IS_DONE` TRUE

### Scenario: Only the newest season is current

* *GIVEN* stored games up to the season 2026
* *WHEN* `CFB_MODEL.SEASON` is queried
* *THEN* `IS_CURRENT_SEASON` MUST be TRUE for 2026
* *AND* it MUST be FALSE for every other season
