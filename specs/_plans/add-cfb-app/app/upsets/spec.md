# Feature: Upsets page

The upsets part of beat 4: "the upset of the week, ranked all-time". The page shows the week's upsets with their rank since the first Division I-A season, the source of each probability, and our own frozen prediction beside them. It also lists the biggest upsets of all time and of the betting-line era.

## Background

- **The week's upsets** at point P are the rows of `CFB_API.UPSETS` with `IS_UPSET` TRUE in P's season, season type and week, ordered by `WEEK_RANK`.
  - **The upset of the week** is the row with `WEEK_RANK` 1. It shows:
    - the winner, the loser, the score and the winner's site;
    - `PROB_SOURCE`, `WINNER_PREGAME_WIN_PROB` and `UPSET_MAGNITUDE`;
    - `ALL_TIME_RANK`, read "since <`ALL_TIME_FROM_SEASON`>";
    - `LINE_ERA_RANK`;
    - `MODEL_WINNER_WIN_PROB` ("our model gave them 18 %"), or "not predicted" when it is NULL.
- **Ranks.**
  - A shared rank (`ALL_TIME_TIED`, `LINE_ERA_TIED`) reads "T-37"; a single rank reads "37".
  - The probability source ("line" or "Elo") stands next to every rank.
  - A game before the all-time window has no all-time rank. It shows only its season rank, and the page never calls it "all-time".
- **The all-time lists**, as stored now:
  - the top 25 by `ALL_TIME_RANK`, labeled "since <`ALL_TIME_FROM_SEASON`>";
  - the top 25 by `LINE_ERA_RANK`, labeled "betting-line era".
- A week without an upset says so.
- The examples run on the app test stack with the history variant of `analyses/upsets`. It holds the 1898 Elo-era upset (before the 1978 window), the 1985 and 1987 upsets, and the 1995 upset in the Ashford–Brookvale series. Where a scenario needs a 2026 week with an upset, the test picks the latest week up to 9 that has one, read from `TEST_CFB_API.UPSETS`.

## Scenarios

### Scenario: The upset of the week shows its all-time rank

* *GIVEN* the app test stack and the latest 2026 week W up to 9 that has an upset
* *WHEN* the upsets page of week W is loaded
* *THEN* the upset of the week MUST be the week-W row of `TEST_CFB_API.UPSETS` with `WEEK_RANK` 1, showing its teams, score, `PROB_SOURCE`, `UPSET_MAGNITUDE`, our model's probability, and its all-time rank read "since 1978"
* *AND* the week's other upsets MUST follow in `WEEK_RANK` order

### Scenario: A shared rank reads T-n

* *GIVEN* an upset row with `ALL_TIME_RANK` 37 and `ALL_TIME_TIED` TRUE, and another with rank 38 and `ALL_TIME_TIED` FALSE
* *WHEN* both are shown
* *THEN* the first MUST read "T-37" and the second "38"
* *AND* each MUST show its probability source next to the rank

### Scenario: Upsets before the all-time window rank only within their season

* *GIVEN* the history variant, with the 1898 upset before the 1978 window
* *WHEN* the all-time list and the 1898 season's upsets are shown
* *THEN* the all-time list MUST NOT contain the 1898 game, and MUST list the 1985 game above the 1987 game
* *AND* the 1898 game MUST show its season rank and no all-time rank

### Scenario: The all-time and line-era lists come from their ranks

* *GIVEN* the app test stack
* *WHEN* the upsets page is loaded
* *THEN* the all-time list MUST equal the rows of `TEST_CFB_API.UPSETS` with `ALL_TIME_RANK` up to 25, in rank order
* *AND* the line-era list MUST equal the rows with `LINE_ERA_RANK` up to 25, in rank order, each with `PROB_SOURCE` `LINE`

### Scenario: A week without an upset says so

* *GIVEN* a point whose week has no row with `IS_UPSET` TRUE
* *WHEN* the upsets page of that point is loaded
* *THEN* the page MUST state that the week had no upset, and MUST still show the all-time lists
* *AND* the API MUST answer with status 200
