# Feature: As-of helpers

Weekly predictions are frozen before their games, the replay demo starts on "the Sunday after week N", and the score model is calibrated on past weeks. All of them need the state of the world as of a season and week, with nothing that became known later. The as-of helpers give that state for any week of any season, from the same stored data, and they are leak-free by construction.

## Background

- **As-of point:** a season and a week sequence (`model/seasons-eras`). It means "after the games of that week, before the first kickoff of the next week".
  - Sequence 0 is the preseason point of a season. A regular-season week keeps its number; postseason week `n` is `100 + n`.
  - The point `(season, week, season_type)` that `REFRESH_WEEK` receives is the row of `AS_OF_POINT` with `AS_OF_SEASON`, `AS_OF_WEEK` and `AS_OF_SEASON_TYPE`.
- `AS_OF_POINT`: one row per season and point: the preseason point and every week of the season in `SEASON_WEEK`. It has:
  - the previous and the next week sequence;
  - `CUTOFF_AT`, the first kickoff of the next week; NULL after the last week;
  - `IS_SEASON_COMPLETE`, TRUE at the last point of a season whose games are all completed;
  - `AS_OF_LABEL`, for example "2026, after week 9";
  - `IS_CURRENT`.
- `CURRENT_AS_OF`: one row, the newest point whose week has a completed game. Pages and the chat use it as their default. Any other point can be chosen from `AS_OF_POINT`.
- **Visibility rules.** A value is visible at a point only if it was known there. Event facts are known once they happen. Values that CFBD overwrites while a season runs are known only from the time they were fetched.

  | Fact | Visible at point P of season S |
  |---|---|
  | results: completed flag, points, winner, postgame Elo and win probability, attendance, excitement | games of S with `WEEK_SEQ` ≤ P |
  | pregame Elo | games of S with `WEEK_SEQ` ≤ the next week |
  | pregame line (spread, win probability) | games of S with `WEEK_SEQ` ≤ P; next-week games only if the line was fetched before `CUTOFF_AT`; never later games |
  | polls | rankings of S with `VISIBLE_AT_WEEK_SEQ` ≤ P (`model/ratings-rankings`) |
  | SP+ of S − 1 (the prior) | always, if final |
  | SP+ of S | if fetched before `CUTOFF_AT`, or at the last point of a complete season if final |
  | memberships, tiers, classification | always: they are fixed before a season starts |
  | schedule: teams, kickoff, site, venue | always, as last fetched. Later changes to the schedule are not replayed |

- **Team Elo at a point** (`ELO_AS_OF`):
  - the postgame Elo of the team's last completed game of S at or before P (`ELO_SOURCE` `POSTGAME`);
  - before its first game of S, the pregame Elo of that game (`PRESEASON`);
  - else NULL (`NONE`).

  Both depend only on games up to P.
- **Views** (earlier seasons are fully known: use `GAME`, `TEAM_GAME` and `TEAM_SEASON`):
  - `GAME_AS_OF`: one row per point and game of the point's season. `GAME_STATUS_AS_OF` is `PLAYED` (completed by P), `NOT_PLAYED` (in a week up to P but not completed), `NEXT` (the next week) or `REMAINING`. Values that are not visible are NULL.
  - `TEAM_SEASON_AS_OF`: one row per point and team-season of the point's season. It has:
    - conference, tier, classification;
    - wins, losses and ties, overall and in the conference, with point differentials, all from games completed by P;
    - games remaining and the next game;
    - `ELO_AS_OF` and `ELO_SOURCE`;
    - the latest visible AP, Coaches and committee ranks;
    - `SP_PRIOR_RATING`, plus `SP_CURRENT_RATING` and `SP_CURRENT_FETCHED_AT` when visible.
  - `RANKING_AS_OF`: one row per point, poll and ranked team, from the latest week of each poll that is visible at P.
- **Leak-free invariant:** for every point P, the rows that `GAME_AS_OF`, `TEAM_SEASON_AS_OF` and `RANKING_AS_OF` return for P do not change when any fact that is not visible at P changes.
- The examples run over the synthetic test league in the integration-test namespace:
  - The 2024 and 2025 seasons are complete and were fetched at 2026-10-20 10:00 UTC.
  - In 2026, weeks 1–9 are completed and weeks 10–15 are scheduled. The 2026 slices were fetched at 2026-10-25 12:00 UTC. The first kickoff of week 9 was 2026-10-22 23:30 UTC, and that of week 10 is 2026-10-29 23:30 UTC.

## Scenarios

### Scenario: As-of points match the refresh arguments

* *GIVEN* the stored 2024 weeks: regular weeks 1–15 and postseason week 1
* *WHEN* `CFB_MODEL.AS_OF_POINT` is queried for 2024
* *THEN* `(2024, 9, 'regular')` MUST have `AS_OF_WEEK_SEQ` 9, previous 8 and next 10
* *AND* `(2024, 1, 'postseason')` MUST have `AS_OF_WEEK_SEQ` 101, previous 15, no next week, no cutoff and `IS_SEASON_COMPLETE` TRUE
* *AND* the preseason point MUST have `AS_OF_WEEK_SEQ` 0 and next week 1, with the first kickoff of week 1 as its cutoff

### Scenario: The current point is the newest week with a completed game

* *GIVEN* the 2026 games of weeks 1–9 are completed and no later game is
* *WHEN* `CFB_MODEL.CURRENT_AS_OF` is queried
* *THEN* it MUST return exactly one row: 2026, regular week 9, with `CUTOFF_AT` 2026-10-29 23:30
* *AND* that point MUST be the only row of `AS_OF_POINT` with `IS_CURRENT` TRUE

### Scenario: Results after the point are hidden

* *GIVEN* Ashford's completed 2026 games of weeks 5, 6 and 7
* *WHEN* `CFB_MODEL.GAME_AS_OF` is queried for the point 2026 week 5
* *THEN* the week-5 game MUST be `PLAYED` with its points, winner and postgame Elo
* *AND* the week-6 game MUST be `NEXT` with its pregame Elo, and without points, winner or postgame Elo
* *AND* the week-7 game MUST be `REMAINING` without points and without pregame Elo

### Scenario: Records count only games up to the point

* *GIVEN* Ashford's 2026 results: wins in weeks 1, 2, 4, 5, 6, 8 and 9, and losses in weeks 3 and 7
* *WHEN* `CFB_MODEL.TEAM_SEASON_AS_OF` is queried for Ashford at the 2026 preseason point, at week 5 and at week 9
* *THEN* the preseason point MUST show 0 wins, 0 losses and all scheduled games remaining
* *AND* week 5 MUST show 4 wins and 1 loss
* *AND* week 9 MUST show 7 wins and 2 losses, with week 10's game as the next game

### Scenario: A line is visible only once it was known

* *GIVEN* the 2026 lines of week-10 and week-11 games, fetched at 2026-10-25 12:00 UTC, before week 10's first kickoff
* *AND* the 2024 lines of week-9 and week-10 games, fetched at 2026-10-20 10:00 UTC, after the 2024 season
* *WHEN* `CFB_MODEL.GAME_AS_OF` is queried for the points 2026 week 9 and 2024 week 9
* *THEN* at 2026 week 9 the week-10 games MUST show their spread and win probability, and the week-11 games MUST NOT
* *AND* at 2024 week 9 the played week-9 games MUST show their line
* *AND* at 2024 week 9 the week-10 games MUST NOT show a line, because it was fetched after their kickoff

### Scenario: The current season's SP+ is visible only from the point when it was fetched

* *GIVEN* Ashford's 2026 SP+ fetched at 2026-10-25 12:00 UTC, its final 2025 SP+, and its final 2024 SP+
* *WHEN* `CFB_MODEL.TEAM_SEASON_AS_OF` is queried for Ashford at 2026 weeks 8 and 9, and at 2024 week 9 and postseason week 1
* *THEN* `SP_PRIOR_RATING` MUST be Ashford's 2025 rating at both 2026 points
* *AND* `SP_CURRENT_RATING` MUST be set at 2026 week 9, and NULL at 2026 week 8
* *AND* `SP_CURRENT_RATING` MUST be NULL at 2024 week 9 and equal Ashford's final 2024 rating at 2024 postseason week 1

### Scenario: Polls become visible when they are released

* *GIVEN* the stored 2024 AP polls for regular weeks 1–16 and postseason week 1, and committee rankings for weeks 10–16
* *WHEN* `CFB_MODEL.RANKING_AS_OF` is queried for 2024 at the preseason point, at week 9, at week 10 and at postseason week 1
* *THEN* the preseason point MUST show the AP poll labeled week 1 and no committee ranking
* *AND* week 9 MUST show the AP poll labeled week 10 and no committee ranking
* *AND* week 10 MUST show the AP poll labeled week 11 and the committee ranking labeled week 10
* *AND* only postseason week 1 MUST show the final AP poll

### Scenario: Team Elo uses only completed games

* *GIVEN* Ashford played 2024 week 8 with a postgame Elo of 1702, had a bye in week 9, and opened the season with a pregame Elo of 1650
* *WHEN* `CFB_MODEL.TEAM_SEASON_AS_OF` is queried for Ashford at 2024 week 9 and at the 2024 preseason point
* *THEN* week 9 MUST show `ELO_AS_OF` 1702 with `ELO_SOURCE` `POSTGAME`
* *AND* the preseason point MUST show 1650 with `ELO_SOURCE` `PRESEASON`

### Scenario: No fact after the point changes an as-of answer

* *GIVEN* the rows of `GAME_AS_OF`, `TEAM_SEASON_AS_OF` and `RANKING_AS_OF` for the point 2024 week 9, recorded once
* *AND* every 2024 fact that is not visible at that point is then changed in `CFB_CORE`:
  - scores, completed flags and postgame Elo of weeks 10 and later;
  - pregame Elo of weeks 11 and later;
  - every line of week 10 and later;
  - polls visible after week 9;
  - the 2024 SP+
* *WHEN* the three views are queried again for that point
* *THEN* every view MUST return exactly the recorded rows
* *AND* a changed score of a week-9 game MUST change the rows of that point, which shows the comparison can detect a change
