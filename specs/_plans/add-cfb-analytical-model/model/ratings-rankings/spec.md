# Feature: SP+ ratings and poll rankings

SP+ ratings (stored from 2013) give the score model its prior, and the polls (stored from 2014) give the resume ranking and the pages their context. Both change during a season. The model attaches them to teams, codes the polls, and records when each value became known. The as-of helpers need that to stay free of leaks.

## Background

- `RATING_SP`: one row per season and team in `CFB_CORE.RATINGS_SP`.
  - CFBD identifies the team only by name. `TEAM_ID` is the team of the stored team list whose `SCHOOL` equals that name exactly. Rows without a match keep `TEAM_ID` NULL with `IS_TEAM_MATCHED` FALSE, and `MODEL_CHECKS` reports `SP_TEAM_UNMATCHED`.
  - Columns: overall rating and ranking; offense, defense and special-teams ratings and their parts (success, explosiveness, rushing, passing, standard and passing downs, pace, run rate, havoc); strength of schedule; second-order wins.
  - `FETCHED_AT` is when the row was fetched. `IS_FINAL` is TRUE when its slice is registered as final in the load log.
  - A season's SP+ includes that season's games. The comments say so: as a prior for season S, use the final SP+ of season S − 1.
- `TEAM_SEASON.SP_PRIOR_RATING` is the final SP+ rating of the team's previous season, with `HAS_SP_PRIOR`.
- `RANKING`: one row per poll, season, season type, week and ranked team in `CFB_CORE.RANKINGS_POLLS_RANKS`.
  - `POLL_CODE` comes from the reference table `POLL`:

    | CFBD poll name | Code | CFBD data from | Label lead (weeks) |
    |---|---|---|---|
    | AP Top 25 | `AP` | 1936 | 1 |
    | Coaches Poll | `COACHES` | 2001 | 1 |
    | Playoff Committee Rankings | `CFP` | 2014 | 0 |
    | FCS Coaches Poll | `FCS_COACHES` | not stated | 1 |
    | AFCA Division II Coaches Poll | `D2_COACHES` | not stated | 1 |
    | AFCA Division III Coaches Poll | `D3_COACHES` | not stated | 1 |

    A poll name that is not in the table keeps its rows, with code `OTHER` and a lead of 0, and `MODEL_CHECKS` reports `POLL_UNKNOWN`.
  - `WEEK_SEQ` follows `model/seasons-eras`.
  - **Visibility:** the week label of a regular-season poll runs ahead of the games it reflects. A poll labeled week 10 is released after the games of week 9. `VISIBLE_AT_WEEK_SEQ` is the label's `WEEK_SEQ` minus the poll's label lead. Postseason polls are the final polls of the season, released after the postseason; they are visible at their own `WEEK_SEQ`. The committee's lead is 0 because its rankings are released on Tuesdays, after the as-of point "Sunday after the week" has passed.
  - `IS_FINAL_POLL` is TRUE for postseason polls.
- `TEAM_SEASON.FINAL_AP_RANK` is the team's rank in the season's final AP poll. `FINAL_CFP_RANK` is its rank in the season's last committee ranking.
- The examples run over the synthetic test league in the integration-test namespace.

## Scenarios

### Scenario: SP+ ratings attach to teams by name

* *GIVEN* stored 2024 SP+ rows for "Ashford" with rating 25.1 and for "Ashford St.", a name that is not in the team list
* *WHEN* `CFB_MODEL.RATING_SP` is queried for 2024
* *THEN* the "Ashford" row MUST carry Ashford's `TEAM_ID` and `IS_TEAM_MATCHED` TRUE
* *AND* the "Ashford St." row MUST be kept with `TEAM_ID` NULL and `IS_TEAM_MATCHED` FALSE
* *AND* `CFB_MODEL.MODEL_CHECKS` MUST report `SP_TEAM_UNMATCHED` with 1 row for 2024

### Scenario: The previous season's final SP+ is the prior

* *GIVEN* Carrow's final 2023 SP+ rating of 20.0
* *AND* no stored SP+ for 2012
* *WHEN* `CFB_MODEL.TEAM_SEASON` is queried for Carrow in 2024 and for Ashford in 2013
* *THEN* Carrow's 2024 `SP_PRIOR_RATING` MUST be 20.0 with `HAS_SP_PRIOR` TRUE
* *AND* Ashford's 2013 `SP_PRIOR_RATING` MUST be NULL with `HAS_SP_PRIOR` FALSE

### Scenario: The current season's SP+ is marked as not final

* *GIVEN* the 2026 SP+ slice is stored as not final and was fetched at 2026-10-25 12:00 UTC
* *AND* the 2025 SP+ slice is stored as final
* *WHEN* `CFB_MODEL.RATING_SP` is queried for both seasons
* *THEN* the 2026 rows MUST have `IS_FINAL` FALSE and `FETCHED_AT` 2026-10-25 12:00
* *AND* the 2025 rows MUST have `IS_FINAL` TRUE

### Scenario: Polls are coded and placed in the week order

* *GIVEN* stored 2024 rankings: AP Top 25 for regular weeks 1–16 and postseason week 1, and Playoff Committee Rankings for regular weeks 10–16
* *WHEN* `CFB_MODEL.RANKING` is queried for 2024
* *THEN* the AP poll labeled regular week 10 MUST have `POLL_CODE` `AP`, `WEEK_SEQ` 10 and `VISIBLE_AT_WEEK_SEQ` 9
* *AND* the committee ranking labeled week 10 MUST have `POLL_CODE` `CFP` and `VISIBLE_AT_WEEK_SEQ` 10
* *AND* the AP poll of postseason week 1 MUST have `WEEK_SEQ` 101, `VISIBLE_AT_WEEK_SEQ` 101 and `IS_FINAL_POLL` TRUE
* *AND* the AP polls of regular week 1 and of postseason week 1 MUST stay separate rows

### Scenario: An unknown poll is kept and flagged

* *GIVEN* stored 2024 rankings of a poll named "Mystery Poll"
* *WHEN* `CFB_MODEL.RANKING` and `CFB_MODEL.MODEL_CHECKS` are queried
* *THEN* its rows MUST have `POLL_CODE` `OTHER` and be visible at their own `WEEK_SEQ`
* *AND* `MODEL_CHECKS` MUST report `POLL_UNKNOWN` for 2024 naming "Mystery Poll"

### Scenario: Final ranks belong to the team-season

* *GIVEN* Ashford is ranked 2nd in the 2024 final AP poll and 1st in the last 2024 committee ranking (week 16)
* *WHEN* `CFB_MODEL.TEAM_SEASON` is queried for Ashford in 2024
* *THEN* `FINAL_AP_RANK` MUST be 2 and `FINAL_CFP_RANK` MUST be 1
