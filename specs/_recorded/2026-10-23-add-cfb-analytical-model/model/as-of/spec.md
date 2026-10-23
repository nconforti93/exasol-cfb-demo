# Feature: As-of helpers

Weekly predictions are frozen before their games, the replay demo starts on "the Sunday after week N", the playoff engine simulates the rest of a season, and the score model is calibrated on past weeks. All of them need the state of the world as of a season and week, with nothing that became known later. The as-of helpers give that state for any week of any season, from the same stored data, and they are leak-free by construction.

## Background

- **As-of point:** a season and a week sequence (`model/seasons-eras`). It has one information horizon for every fact: everything that was known after the games of that week and before the first kickoff of the next week (`CUTOFF_AT`). "The Sunday after week N" in the demo is a name for that point, not a second horizon.
  - Sequence 0 is the preseason point of a season. A regular-season week keeps its number; postseason week `n` is `100 + n`.
  - The point `(season, week, season_type)` that `REFRESH_WEEK` receives is the row of `AS_OF_POINT` with `AS_OF_SEASON`, `AS_OF_WEEK` and `AS_OF_SEASON_TYPE`.
- `AS_OF_POINT`: one row per season and point: the preseason point and every week of the season in `SEASON_WEEK`. It has:
  - the previous and the next week sequence, and `LAST_REGULAR_WEEK_SEQ`, the season's last regular-season week;
  - `CUTOFF_AT`, the first kickoff of the next week; NULL after the last week;
  - `AS_OF_LABEL`, for example "2026, after week 9";
  - the point metadata `IS_SEASON_COMPLETE` (TRUE at the last point of a season whose weeks are all done, `SEASON_WEEK.IS_DONE`) and `IS_CURRENT`. Point metadata changes when later games complete. It describes the point, not the world at the point, so it appears only in `AS_OF_POINT` and `CURRENT_AS_OF`, never in the three as-of views below.
- `CURRENT_AS_OF`: one row. In the current season (`SEASON.IS_CURRENT_SEASON`) it is the newest week that is done (`SEASON_WEEK.IS_DONE`: all its games are completed, or the season's games were last fetched at least one day after the week ended) while every earlier week of the season is done too. Before any week is done, it is the season's preseason point. The condition on the earlier weeks keeps the point in the regular season when CFBD labels November lower-division playoff games as postseason weeks 13 and 14. A week whose Thursday game is completed while its Saturday games are not stays open, so the current point stays at the week before. Pages and the chat use `CURRENT_AS_OF` as their default; any other point can be chosen from `AS_OF_POINT`.
- **Visibility rules.** A value is visible at a point only if it was known there. Event facts are known once they happen. Values that CFBD overwrites while a season runs are known only from the time they were fetched.

  | Fact | Visible at point P of season S |
  |---|---|
  | results: completed flag, points, margin, winner, postgame Elo and win probability, attendance, excitement | visible games of S with `WEEK_SEQ` ≤ P |
  | pregame Elo | visible games of S with `WEEK_SEQ` ≤ the next week |
  | pregame line (spread, win probability) | games of S with `WEEK_SEQ` ≤ P; next-week games only if the line was fetched before `CUTOFF_AT`; never later games |
  | polls | rankings of S with `VISIBLE_AT_WEEK_SEQ` ≤ P (the label rule, `model/ratings-rankings`), or fetched before `CUTOFF_AT` |
  | SP+ of S − 1 (the prior) | always, as last fetched, final or not |
  | SP+ of S | if fetched before `CUTOFF_AT`, or at the last point of a complete season if final |
  | memberships, tiers, classification | always: they are fixed before a season starts. The conference and classification taken from games use only games with `FIXED` participants (`model/teams-conferences`) |
  | games with `FIXED` participants: teams, kickoff, site, venue, conference flag | always, as last fetched. Later changes to the schedule are not replayed |
  | games whose participants depend on results of the season | only from the point at which their participants are known (below) |

  A slice fetched before `CUTOFF_AT` can only hold values released before it, so the fetch-time rule cannot leak.
- **Result-dependent games.** `GAME.PARTICIPANTS_RULE` (`model/games`) says when the teams of a game are known. A game is a row of `GAME_AS_OF` at P only from that point on; before, it is not a row at all, so neither its teams nor its existence nor its pregame Elo can show.

  | `PARTICIPANTS_RULE` | Games | Row of `GAME_AS_OF` at P when |
  |---|---|---|
  | `FIXED` | every other game | always |
  | `CONF_CHAMPIONSHIP` | conference championship games | its week is at most the next week of P (the point after the week before the game) |
  | `SELECTION` | bowls with an FBS team, and the games of the season's first CFP round | P ≥ `LAST_REGULAR_WEEK_SEQ` |
  | `PREVIOUS_ROUND` | later CFP rounds, and every postseason game without an FBS team (lower-division playoffs) | its week is at most P |

  - CFBD has one postseason week (open question Q3), so at P ≥ 101 the whole postseason is known at once, and the as-of helpers cannot replay "after the CFP first round". Later CFP rounds therefore never appear before P = 101, and neither does their pregame Elo, which encodes earlier-round results.
  - A game that CFBD lists before its teams are named (a placeholder, `HAS_TEAMS` FALSE) is `FIXED`: it reveals nothing but its date and venue.
  - Known limit: lower-division conference championship games are not detected (`model/games`), so their participants show at earlier points. Games that involve an FBS team are not affected.
- **Team Elo at a point** (`ELO_AS_OF`):
  - the postgame Elo of the team's last completed game of S at or before P (`ELO_SOURCE` `POSTGAME`);
  - before its first game of S, the pregame Elo of that game (`PRESEASON`), which depends only on earlier seasons;
  - else NULL (`NONE`).
- **Views** (earlier seasons are fully known: use `GAME`, `TEAM_GAME` and `TEAM_SEASON`):
  - `GAME_AS_OF`: one row per point and game of the point's season that is visible at the point. Columns: the point (`AS_OF_SEASON`, `AS_OF_WEEK_SEQ`), the game's keys and kickoff, home and away team, `HAS_TEAMS`, `IS_NEUTRAL_SITE`, `IS_CONFERENCE_GAME`, `IS_CONF_CHAMPIONSHIP`, `POSTSEASON_KIND`, `CFP_ROUND`, `PARTICIPANTS_RULE`, `GAME_STATUS_AS_OF`, and the masked values: points, `MARGIN`, winner, pregame and postgame Elo, `PREGAME_SPREAD` and `HOME_PREGAME_WIN_PROB`. `GAME_STATUS_AS_OF` is `PLAYED` (completed by P), `NOT_PLAYED` (in a week up to P but not completed), `NEXT` (the next week) or `REMAINING`. Values that are not visible are NULL.
  - `TEAM_SEASON_AS_OF`: one row per point and team-season of the point's season that has a membership or a game with `FIXED` participants. It has:
    - conference, division, tier, `IS_POWER_CONFERENCE`, classification;
    - `WINS`, `LOSSES` and `TIES` over the team's completed games in `GAME_AS_OF` (every opponent, FCS included), and `CONF_WINS`, `CONF_LOSSES` and `CONF_TIES` over its completed conference games (`IS_CONFERENCE_GAME`) without conference championship games;
    - `POINTS_FOR`, `POINTS_AGAINST` and `CONF_POINT_DIFF` over the same games;
    - `GAMES_REMAINING` and `NEXT_GAME_ID`, counted over the rows of `GAME_AS_OF` only;
    - `ELO_AS_OF` and `ELO_SOURCE`;
    - the latest visible AP, Coaches and committee ranks;
    - `SP_PRIOR_RATING`, plus `SP_CURRENT_RATING` and `SP_CURRENT_FETCHED_AT` when visible.
  - `RANKING_AS_OF`: one row per point, poll and ranked team, from the latest week of each poll that is visible at P.
- **Engine inputs.** `GAME_AS_OF` and `TEAM_SEASON_AS_OF` hold every engine input that comes from stored data (`specs/architecture.md` § Engine contract). Real values that the score model or the engine reads are fixed-scale `DECIMAL`, never `DOUBLE`: spreads `DECIMAL(6,2)`, win probabilities `DECIMAL(5,4)`, SP+ ratings `DECIMAL(6,2)`. Points, Elo and ranks are integers. The model computes no engine value: indices, predicted margins, σ, totals, ratings, strength of schedule and capped point differentials belong to Phase 4.
- **Leak-free invariant:** for every point P, the rows that `GAME_AS_OF`, `TEAM_SEASON_AS_OF` and `RANKING_AS_OF` return for P do not change when any fact that is not visible at P changes. The first kickoff of the next week defines `CUTOFF_AT` and so belongs to the point itself. The finality flag of the previous season's SP+ is not a fact of any point of the season.
- The examples run over the synthetic test league in the integration-test namespace:
  - The 2024 and 2025 seasons are complete and were fetched at 2026-10-20 10:00 UTC.
  - In 2026, weeks 1–9 are completed and weeks 10–15 are scheduled. The 2026 slices were fetched at 2026-10-25 12:00 UTC, except the rankings, which were fetched on Tuesday 2026-10-27 at 23:00 UTC. The first kickoff of week 9 was 2026-10-22 23:30 UTC, and that of week 10 is Thursday 2026-10-29 23:30 UTC.

## Scenarios

### Scenario: As-of points match the refresh arguments

* *GIVEN* the stored 2024 weeks: regular weeks 1–15 and postseason week 1
* *WHEN* `CFB_MODEL.AS_OF_POINT` is queried for 2024
* *THEN* `(2024, 9, 'regular')` MUST have `AS_OF_WEEK_SEQ` 9, previous 8, next 10 and `LAST_REGULAR_WEEK_SEQ` 15
* *AND* `(2024, 1, 'postseason')` MUST have `AS_OF_WEEK_SEQ` 101, previous 15, no next week, no cutoff and `IS_SEASON_COMPLETE` TRUE
* *AND* the preseason point MUST have `AS_OF_WEEK_SEQ` 0 and next week 1, with the first kickoff of week 1 as its cutoff

### Scenario: The current point is the newest finished week

* *GIVEN* the 2026 games of weeks 1–9 are completed and no later game is
* *WHEN* `CFB_MODEL.CURRENT_AS_OF` is queried
* *THEN* it MUST return exactly one row: 2026, regular week 9, with `CUTOFF_AT` 2026-10-29 23:30
* *AND* that point MUST be the only row of `AS_OF_POINT` with `IS_CURRENT` TRUE

### Scenario: A partly played week does not move the current point

* *GIVEN* the 2026 games were fetched again at 2026-10-30 12:00 UTC, after week 10's Thursday game was completed and before its Saturday games
* *WHEN* `CFB_MODEL.CURRENT_AS_OF` is queried
* *THEN* it MUST still return 2026, regular week 9

### Scenario: Results after the point are hidden

* *GIVEN* Ashford's completed 2026 games of weeks 5, 6 and 7
* *WHEN* `CFB_MODEL.GAME_AS_OF` is queried for the point 2026 week 5
* *THEN* the week-5 game MUST be `PLAYED` with its points, margin, winner and postgame Elo
* *AND* the week-6 game MUST be `NEXT` with its pregame Elo, and without points, margin, winner or postgame Elo
* *AND* the week-7 game MUST be `REMAINING` without points and without pregame Elo

### Scenario: Records count only games up to the point

* *GIVEN* Ashford's 2026 results: wins in weeks 1, 2, 4, 5, 6, 8 and 9, and losses in weeks 3 and 7
* *WHEN* `CFB_MODEL.TEAM_SEASON_AS_OF` is queried for Ashford at the 2026 preseason point, at week 5 and at week 9
* *THEN* the preseason point MUST show 0 wins, 0 losses and all scheduled games remaining
* *AND* week 5 MUST show 4 wins and 1 loss
* *AND* week 9 MUST show 7 wins and 2 losses, with week 10's game as the next game

### Scenario: Games whose participants depend on results are hidden until they are known

* *GIVEN* the stored 2024 SEC championship game Ashford against Carrow in week 15, the CFP first-round game Kingsley against Northam, the CFP quarterfinal Ashford against Kingsley and the Harbor Bowl Brookvale against Eastport
* *WHEN* `CFB_MODEL.GAME_AS_OF` is queried for 2024 at weeks 9, 14 and 15 and at postseason week 1
* *THEN* none of the four games MUST be a row at week 9
* *AND* at week 14 the championship game MUST be the only one of them, as `NEXT` with both teams
* *AND* at week 15 the first-round game and the Harbor Bowl MUST be rows, and the quarterfinal MUST NOT
* *AND* at postseason week 1 all four games MUST be rows

### Scenario: Remaining games count only games with known participants

* *GIVEN* Ashford's 2024 regular-season games after week 9 are in weeks 10, 12, 13 and 14, and its later games are the SEC championship game and the CFP quarterfinal
* *WHEN* `CFB_MODEL.TEAM_SEASON_AS_OF` is queried for Ashford at 2024 weeks 9 and 14
* *THEN* week 9 MUST show 4 games remaining and the week-10 game as the next game
* *AND* week 14 MUST show 1 game remaining, the championship game

### Scenario: A line is visible only once it was known

* *GIVEN* the 2026 lines of week-10 and week-11 games, fetched at 2026-10-25 12:00 UTC, before week 10's first kickoff
* *AND* the 2024 lines of week-9 and week-10 games, fetched at 2026-10-20 10:00 UTC, after the 2024 season
* *WHEN* `CFB_MODEL.GAME_AS_OF` is queried for the points 2026 week 9 and 2024 week 9
* *THEN* at 2026 week 9 the week-10 games MUST show their spread and win probability, and the week-11 games MUST NOT
* *AND* at 2024 week 9 the played week-9 games MUST show their line, and the week-10 games MUST NOT, because it was fetched after their kickoff

### Scenario: The current season's SP+ is visible only from the point when it was fetched

* *GIVEN* Ashford's 2026 SP+ fetched at 2026-10-25 12:00 UTC, its 2025 SP+, and its final 2024 SP+
* *WHEN* `CFB_MODEL.TEAM_SEASON_AS_OF` is queried for Ashford at 2026 weeks 8 and 9, and at 2024 week 9 and postseason week 1
* *THEN* `SP_PRIOR_RATING` MUST be Ashford's 2025 rating at both 2026 points
* *AND* `SP_CURRENT_RATING` MUST be set at 2026 week 9, and NULL at 2026 week 8
* *AND* `SP_CURRENT_RATING` MUST be NULL at 2024 week 9 and equal Ashford's final 2024 rating at 2024 postseason week 1

### Scenario: Polls become visible when they are released

* *GIVEN* the stored 2024 AP polls for regular weeks 1–16 and postseason week 1, and committee rankings for weeks 10–15
* *WHEN* `CFB_MODEL.RANKING_AS_OF` is queried for 2024 at the preseason point, at week 9, at week 10 and at postseason week 1
* *THEN* the preseason point MUST show the AP poll labeled week 1 and no committee ranking
* *AND* week 9 MUST show the AP poll labeled week 10 and no committee ranking
* *AND* week 10 MUST show the AP poll labeled week 11 and the committee ranking labeled week 10
* *AND* only postseason week 1 MUST show the final AP poll

### Scenario: A ranking fetched before the next kickoff is visible at that point

* *GIVEN* the 2026 committee ranking labeled week 10, released after week 9's games and fetched on Tuesday 2026-10-27 at 23:00 UTC, before week 10's first kickoff
* *WHEN* `CFB_MODEL.RANKING_AS_OF` is queried for 2026 weeks 8 and 9
* *THEN* week 9 MUST show the committee ranking labeled week 10
* *AND* week 8 MUST NOT show any committee ranking

### Scenario: Team Elo uses only completed games

* *GIVEN* Ashford played 2024 week 8 with a postgame Elo of 1702, had a bye in week 9, and opened the season with a pregame Elo of 1650
* *WHEN* `CFB_MODEL.TEAM_SEASON_AS_OF` is queried for Ashford at 2024 week 9 and at the 2024 preseason point
* *THEN* week 9 MUST show `ELO_AS_OF` 1702 with `ELO_SOURCE` `POSTGAME`
* *AND* the preseason point MUST show 1650 with `ELO_SOURCE` `PRESEASON`

### Scenario: Engine inputs are fixed-scale decimals

* *GIVEN* a deployed model and the 2024 week-14 line of Ashford against Brookvale: spread -7.5, home win probability 0.70
* *WHEN* the column types of `GAME`, `TEAM_GAME`, `GAME_AS_OF`, `TEAM_SEASON_AS_OF` and `RATING_SP` are read from `EXA_ALL_COLUMNS`
* *THEN* every spread column MUST be `DECIMAL(6,2)`, every pregame win probability column `DECIMAL(5,4)` and every SP+ rating column `DECIMAL(6,2)`
* *AND* `GAME_AS_OF` at 2024 week 14 MUST return exactly -7.50 and 0.7000 for that game

### Scenario: No fact after the point changes an as-of answer

* *GIVEN* the rows of `GAME_AS_OF`, `TEAM_SEASON_AS_OF` and `RANKING_AS_OF` for the point 2024 week 9, recorded once
* *AND* every 2024 fact that is not visible at that point is then changed in `CFB_CORE`:
  - scores, completed flags, postgame Elo and win probability, attendance and excitement of weeks 10 and later;
  - pregame Elo of weeks 11 and later;
  - every line of week 10 and later;
  - polls visible after week 9;
  - the 2024 SP+, and the finality of the 2023 SP+ slice;
  - the participants of the SEC championship game and of the CFP quarterfinal, and one added bowl game;
  - the calendar rows of weeks 11 and later
* *WHEN* the three views are queried again for that point
* *THEN* every view MUST return exactly the recorded rows
* *AND* a changed score of a week-9 game MUST change the rows of that point, which shows the comparison can detect a change
