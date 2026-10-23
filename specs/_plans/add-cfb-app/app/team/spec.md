# Feature: Team page

One page per team, at `/team/<id>`: everything a fan wants about their team as of the chosen point. The record and schedule, the ratings and polls come from stored history and need only the loaded data and the model. The playoff odds and how they moved, this week's prediction, the games to watch and the what-if toggles come from the published simulation runs, and say so plainly until a run exists. Every panel's SQL is one click away in the inspector.

## Background

- **The team** is the path's team id; the page also sets the store's focus team, so the odds board, the rooting guide and the chat see the same team (`app/shell`). An unknown id shows "No team with id <id>" and a link to the map.
- **Hero.** Logo or fallback badge, school, mascot, conference of the point's season, the record overall and in conference at the point, and the AP and CFP ranks at the point when ranked. The team's color is an accent only (`plan.md`, UI revision, Visual design).
- **Schedule and results** (`CFB_API.GAMES` of the point's season for the team): week, date, opponent with its mark and rank, site (home, away, neutral), and the result with the score. A game after the point is shown as upcoming, with the betting line and a win probability: ours (`CFB_API.PREDICTIONS`) when one exists, else CFBD's pregame probability, labeled by its source. Each row links to head-to-head for the pair.
- **Ratings and polls.** SP+ overall, offense and defense with ranks for the point's season and the season before (`CFB_API.SP_RATINGS`); the AP and CFP ranks by week of the season (`CFB_API.RANKINGS`), with a table view.
- **Outlook** (published runs, `CFB_API` views of Phase 5):
  - **Playoff odds:** `P_IN_CFP`, `P_BYE` and `P_CHAMPION` at the point with the movement since the last published point (`app/odds` rules: icon, word and percentage points), and the trajectory over the season's published points (`PLAYOFF_ODDS` filtered to the team).
  - **This week:** the team's coming game from `PREDICTIONS`: opponent, our win probability, predicted score, and the line beside it.
  - **Games to watch:** the team's `ROOTING_GUIDE` rows of the coming week and its top games of the season, with "root for", and the what-if toggles with the team as the focus team (`app/odds` rules, including where toggles are offered).
  - **Rivalries:** the team's most-played series (`MATCHUP_SERIES`), each linking to head-to-head.
  - Each outlook panel without data at the point says why in words ("No published run at 2026, after week 3", "Odds appear after the first simulation run"), never as an error.

## Scenarios

### Scenario: The team page shows record, schedule and ratings at the point

* *GIVEN* the team fixture of Brookvale at 2026 week 9: seven games played, five to come, SP+ ratings for 2025 and 2026, and AP ranks for weeks 1–9
* *WHEN* `/team/102` is opened
* *THEN* the hero MUST show Brookvale's logo or badge, its conference and its record at the point
* *AND* the schedule MUST list all twelve games, the played ones with result and score, the coming ones with the line and a win probability labeled by its source
* *AND* the ratings panel MUST show SP+ for both seasons, and the polls panel the AP rank by week with a table view

### Scenario: Outlook panels say when no run is published

* *GIVEN* a team response whose outlook has no published run at the point
* *WHEN* the team page is opened
* *THEN* the odds, this-week and games-to-watch panels MUST each state in words why they are empty
* *AND* the page MUST NOT show an error banner, and the record, schedule and ratings MUST still render

### Scenario: With a published run, the team page shows its odds, prediction and games to watch

* *GIVEN* the team outlook fixture of Brookvale at 2026 week 9 with a published run
* *WHEN* the team page is opened
* *THEN* it MUST show `P_IN_CFP` with its movement as an icon, a word and percentage points, and a trajectory with one mark per published point of the season
* *AND* it MUST show the coming game with our win probability and the line, and the coming week's games to watch with whom to root for
* *AND* setting a what-if toggle there MUST behave as on the odds board for the focus team Brookvale

### Scenario: The team page leads to head-to-head

* *GIVEN* the team page of Brookvale
* *WHEN* the head-to-head link of the game against Ashford, or of the Ashford rivalry, is followed
* *THEN* the app MUST open `/head-to-head?team=102&opponent=101` with both teams picked
* *AND* no game preview MUST be fetched until the preview button is clicked (`app/head-to-head`)
