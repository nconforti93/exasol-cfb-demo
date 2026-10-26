# Feature: SP+ ratings and poll rankings

SP+ ratings (stored from 2013) give the score model its prior, and the polls (stored from 2014) give the resume ranking and the pages their context. Both change during a season. The model attaches them to teams, codes the polls, and records when each value became known. The as-of helpers need that to stay free of leaks.

## Background

- `RATING_SP`: one row per season and team in `CFB_CORE.RATINGS_SP`.
  - If a season and team name is stored more than once (overlapping slices), the row fetched last wins, and `MODEL_CHECKS` reports `DUPLICATE_SOURCE_ROW` as a WARNING for the season.
  - CFBD's national-averages row (team `nationalAverages`) is not a team and is left out. The view comment says so.
  - CFBD identifies the team only by name. `TEAM_ID` is the team of the stored team list whose `SCHOOL` equals that name exactly. Rows without a match keep `TEAM_ID` NULL with `IS_TEAM_MATCHED` FALSE, and `MODEL_CHECKS` reports `SP_TEAM_UNMATCHED`.
  - Columns: overall rating and ranking; offense, defense and special-teams ratings; the parts of offense and defense (success, explosiveness, rushing, passing, standard and passing downs, pace, run rate, havoc); strength of schedule; second-order wins. The overall, offense, defense and special-teams ratings are `DECIMAL(6,2)`, because the score model reads them (engine contract). The parts stay as CFBD reports them.
  - `FETCHED_AT` is when the row was fetched. `IS_FINAL` is TRUE when its slice is registered as final in the load log.
  - A season's SP+ includes that season's games, and CFBD may compute historic seasons with a later methodology. The comments say both: as a prior for season S, use the SP+ of season S − 1.
- `TEAM_SEASON.SP_PRIOR_RATING` is the SP+ rating of the team's previous season as last fetched, whether its slice is final or not, with `HAS_SP_PRIOR` and `SP_PRIOR_IS_FINAL`. A previous season is in the past at every point of a season, so the prior never leaks. `MODEL_CHECKS` reports `SP_PRIOR_NOT_FINAL` as a WARNING when a season's priors come from a slice that is not final, for example when no load re-fetched the previous season after 15 February.
- `RANKING`: one row per poll, season, season type, week and ranked team in `CFB_CORE.RANKINGS_POLLS_RANKS`.
  - If that key is stored more than once, the row fetched last wins. Rows of one fetch with the same key (more than one committee snapshot for a week, open question Q2) are resolved by the later position of the poll in the response (`POLLS_NO`). `MODEL_CHECKS` reports `DUPLICATE_SOURCE_ROW` for fetched duplicates.
  - `POLL_CODE` comes from the reference table `POLL`:

    | CFBD poll name | Code | CFBD data from | Label lead (weeks) | Final ranking before the postseason |
    |---|---|---|---|---|
    | AP Top 25 | `AP` | 1936 | 1 | no |
    | Coaches Poll | `COACHES` | 2001 | 1 | no |
    | Playoff Committee Rankings | `CFP` | 2014 | 0 | yes |
    | FCS Coaches Poll | `FCS_COACHES` | not stated | 1 | no |
    | AFCA Division II Coaches Poll | `D2_COACHES` | not stated | 1 | no |
    | AFCA Division III Coaches Poll | `D3_COACHES` | not stated | 1 | no |

    A poll name that is not in the table keeps its rows, with code `OTHER` and a lead of 0, and `MODEL_CHECKS` reports `POLL_UNKNOWN`.
  - `WEEK_SEQ` follows `model/seasons-eras`. `FETCHED_AT` is when the row was fetched.
  - **Visibility (the label rule):** the week label of a regular-season poll runs ahead of the games it reflects: an AP poll labeled week 10 is released after the games of week 9. `VISIBLE_AT_WEEK_SEQ` is the label's `WEEK_SEQ` minus the poll's label lead.
    - Postseason polls are released after the postseason and are visible at their own `WEEK_SEQ`.
    - A poll whose final ranking comes before the postseason (the committee's selection ranking) is visible at the season's last regular-season week, even if CFBD labels it postseason.
    - The committee's lead is 0 until open question Q2 shows how CFBD labels it. A lead that is too small only delays visibility; a lead that is too large would leak.
    - The as-of helpers also show any ranking fetched before the point's cutoff (`model/as-of`). So a committee ranking released on a Tuesday and fetched before the next kickoff is visible at the current point at once.
  - `IS_FINAL_POLL` is TRUE for postseason-labeled polls that are released after the postseason, and for the committee's last ranking of a season whose regular-season weeks are all done (`SEASON_WEEK.IS_DONE`). The stored ranks do not carry CFBD's `isFinal` flag, so the model derives it.
- `TEAM_SEASON.FINAL_AP_RANK` is the team's rank in the season's final AP poll. `FINAL_CFP_RANK` is its rank in the committee ranking with `IS_FINAL_POLL` TRUE.
- The examples run over the synthetic test league in the integration-test namespace.

## Scenarios

### Scenario: SP+ ratings attach to teams by name

* *GIVEN* stored 2024 SP+ rows for "Ashford" with rating 25.1, for "Ashford St.", a name that is not in the team list, and for "nationalAverages"
* *WHEN* `CFB_MODEL.RATING_SP` is queried for 2024
* *THEN* the "Ashford" row MUST carry Ashford's `TEAM_ID`, `IS_TEAM_MATCHED` TRUE and `SP_RATING` 25.10
* *AND* the "Ashford St." row MUST be kept with `TEAM_ID` NULL and `IS_TEAM_MATCHED` FALSE
* *AND* there MUST be no "nationalAverages" row
* *AND* `CFB_MODEL.MODEL_CHECKS` MUST report `SP_TEAM_UNMATCHED` with 1 row for 2024

### Scenario: A rating or a ranking stored twice appears once

* *GIVEN* Ashford's 2026 SP+ is stored in the season slice fetched at 2026-10-25 12:00 UTC and in a team slice fetched at 2026-10-26 08:00 UTC with the rating 24.0
* *AND* the 2026 AP poll labeled week 9 is stored in the season slice and in a later week slice
* *WHEN* `CFB_MODEL.RATING_SP` and `CFB_MODEL.RANKING` are queried for 2026
* *THEN* Ashford MUST have one 2026 SP+ row, with rating 24.00 and `FETCHED_AT` 2026-10-26 08:00
* *AND* every team ranked in that AP poll MUST have one row
* *AND* `CFB_MODEL.MODEL_CHECKS` MUST report `DUPLICATE_SOURCE_ROW` for 2026 with severity `WARNING`, naming both sources

### Scenario: The previous season's SP+ is the prior

* *GIVEN* Carrow's final 2023 SP+ rating of 20.0
* *AND* no stored SP+ for 2012
* *WHEN* `CFB_MODEL.TEAM_SEASON` is queried for Carrow in 2024 and for Ashford in 2013
* *THEN* Carrow's 2024 `SP_PRIOR_RATING` MUST be 20.00 with `HAS_SP_PRIOR` and `SP_PRIOR_IS_FINAL` TRUE
* *AND* Ashford's 2013 `SP_PRIOR_RATING` MUST be NULL with `HAS_SP_PRIOR` FALSE

### Scenario: A prior that is not final is used and reported

* *GIVEN* the 2025 SP+ slice is registered as not final
* *WHEN* `CFB_MODEL.TEAM_SEASON` and `CFB_MODEL.MODEL_CHECKS` are queried for 2026
* *THEN* Ashford's 2026 `SP_PRIOR_RATING` MUST be its stored 2025 rating with `SP_PRIOR_IS_FINAL` FALSE
* *AND* `MODEL_CHECKS` MUST report `SP_PRIOR_NOT_FINAL` as failed for 2026 with severity `WARNING`

### Scenario: The current season's SP+ is marked as not final

* *GIVEN* the 2026 SP+ slice is stored as not final and was fetched at 2026-10-25 12:00 UTC
* *AND* the 2025 SP+ slice is stored as final
* *WHEN* `CFB_MODEL.RATING_SP` is queried for both seasons
* *THEN* the 2026 rows MUST have `IS_FINAL` FALSE and `FETCHED_AT` 2026-10-25 12:00
* *AND* the 2025 rows MUST have `IS_FINAL` TRUE

### Scenario: Polls are coded and placed in the week order

* *GIVEN* stored 2024 rankings: AP Top 25 for regular weeks 1–16 and postseason week 1, and Playoff Committee Rankings for regular weeks 10–15
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

### Scenario: A committee ranking labeled postseason is visible after the regular season

* *GIVEN* the 2025 committee rankings labeled regular weeks 10–14, and the selection ranking labeled postseason week 1
* *AND* 2025's last regular-season week is week 15
* *WHEN* `CFB_MODEL.RANKING` is queried for the 2025 committee rankings
* *THEN* the selection ranking MUST have `WEEK_SEQ` 101, `VISIBLE_AT_WEEK_SEQ` 15 and `IS_FINAL_POLL` TRUE
* *AND* no other 2025 committee ranking MUST have `IS_FINAL_POLL` TRUE

### Scenario: Final ranks belong to the team-season

* *GIVEN* Ashford is ranked 5th in the 2024 final AP poll and 1st in the last 2024 committee ranking (week 15)
* *AND* a 2026 committee ranking labeled week 10 is stored while 2026 weeks 10–15 are not done
* *WHEN* `CFB_MODEL.TEAM_SEASON` is queried for Ashford in 2024 and 2026
* *THEN* 2024 MUST show `FINAL_AP_RANK` 5 and `FINAL_CFP_RANK` 1
* *AND* 2026 MUST show no `FINAL_CFP_RANK`
