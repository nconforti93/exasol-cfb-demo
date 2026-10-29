# Feature: Playoff selection rules

Each simulated season ends the way the real one does. Conferences crown champions, a ranking stands in for the selection committee, and a 12-team field is selected, seeded and played out under the rules of that season. The 2026 rules drive the live odds. The 2024 and 2025 rules let the backtest compare the engine with the fields that were actually selected. Tiebreakers are simplified on purpose, and documented.

## Background

- `CFB_SIM.SELECTION_RULES`: a reference table seeded from git, with one row per rule set and season range. Rule sets exist only for 12-team seasons; earlier seasons have none, and runs for them are refused (`sim-engine/sim-runs`).

  | Rule set | Seasons | Auto-bids | Byes |
  |---|---|---|---|
  | `CFP12_2024` | 2024 | the 5 best-ranked conference champions (`TOP_CHAMPIONS`, `AUTO_BIDS` 5) | the 4 best-ranked champions get seeds 1–4; the other 8 teams are seeded 5–12 by rank (`CHAMPIONS`) |
  | `CFP12_2025` | 2025 | the 5 best-ranked conference champions (`TOP_CHAMPIONS`, `AUTO_BIDS` 5) | straight seeding by rank; seeds 1–4 get byes (`STRAIGHT`) |
  | `CFP12_2026` | from 2026 | the champions of the SEC, Big Ten, Big 12 and ACC (every `P4` conference), plus the best-ranked team of a `G6` conference, champion or not (`POWER_CHAMPIONS_PLUS_G6`, `G6_BID` `TEAM`); `GUARANTEE_SCHOOL` 'Notre Dame' is selected if it is ranked within `GUARANTEE_RANK` 12 | `STRAIGHT` |

  - Every rule set has `CFP_SIZE` 12, `N_BYES` 4 and `MIN_CONFERENCE_TEAMS` 6. The remaining places go at-large to the best-ranked teams not yet selected.
  - A conference's tier comes from `CFB_MODEL.CONFERENCE_TIER` through the engine's team rows (`sim-engine/sim-runs`). From 2024 every FBS conference outside the power four is `G6`, and that includes the Pac-12.
  - Each row cites its source in a column comment: the CFP announcements of 2024 and 2025, and the January 2026 decision of the CFP management committee.
  - `G6_BID` is a column, so a published correction to `CHAMPION` changes one seed value.
- **Conference champions:**
  - A conference with at least `MIN_CONFERENCE_TEAMS` FBS teams in the season has a champion. A smaller conference has none, and its teams compete at-large, like independents.
  - A conference that holds a championship game in the season (`CFB_MODEL.CONFERENCE_CHAMPIONSHIP`) decides its title there. If the game's teams are known at the point, they play it with the game's own predicted margin and uncertainty; if it is played, its result stands. Otherwise the top two of the simulated standings meet on a neutral site (`sim-engine/engine-contract`).
  - A conference without a championship game crowns the top team of its standings.
- **Standings** (simplified, S3), best first:
  1. conference win percentage, exact;
  2. head-to-head, only for a tie of exactly two teams that met: the winner of their game goes first;
  3. conference point differential;
  4. rating;
  5. team index, ascending.
- **Resume ranking** (the committee stand-in):

  `w_win_pct × W / (W + L) + w_sos × SOS + w_pd × CAPPED_PD / (W + L) + w_rating × RATING`

  - W and L count all games so far, the remaining games and the conference championship game. `CAPPED_PD` caps each game's margin at `margin_cap`.
  - Ranks run by score descending, then rating descending, then team index. Only eligible teams are ranked.
  - The weights come from `CFB_SIM.RESUME_WEIGHTS`, fitted to the committee's final rankings (`sim-engine/calibration`).
- **Selection:**
  - Independents are selected only at-large or by the guarantee. Non-FBS teams are never selected.
  - A guaranteed team counts as an automatic bid. An automatic bid ranked outside the top 12 still gets in, and the at-large places shrink by one.
- **Seeding:** by the rule set's bye rule. Each selected team's seed comes from its rank among the selected teams; under `CHAMPIONS`, the four best-ranked champions take seeds 1–4 first.
- **Bracket:** fixed, with no reseeding.
  - First round: 5 v 12, 6 v 11, 7 v 10 and 8 v 9, at the better seed's home, with home field `hfa`.
  - Quarterfinals: 1 v the winner of 8/9, 2 v 7/10, 3 v 6/11, 4 v 5/12.
  - Semifinals: the 1-quadrant against the 4-quadrant, and the 2-quadrant against the 3-quadrant. Then the final.
  - From the quarterfinals on, games are neutral and the better seed is listed first. The predicted margin is the rating difference.
- **Simplifications** (documented in the user guide):
  - divisions, multi-team head-to-head and conference-specific tiebreakers are not modeled;
  - simulated championship games are neutral;
  - the committee is a formula;
  - transitional FBS teams count as eligible.
- The examples use crafted models whose regular season and championship games are all played, so every simulated season selects the same field, and only the bracket varies.

## Scenarios

### Scenario: In 2026 the four power champions and the best-ranked Group of Six team get in

* *GIVEN* a crafted 2026 season whose champions are ranked: SEC 1, Big Ten 3, Big 12 9, ACC 11, Mountain West 14
* *AND* the American Athletic team Ivel, which lost its championship game, is ranked 13 as the best-ranked team of a G6 conference
* *WHEN* the season is simulated under `CFP12_2026`
* *THEN* the field MUST be the four power champions, Ivel and the 7 best-ranked other eligible teams
* *AND* the Mountain West champion MUST NOT be selected

### Scenario: A power champion ranked outside the top 12 still gets in

* *GIVEN* a crafted 2026 season in which the SEC, Big Ten and Big 12 champions are ranked 1 to 3, the ACC champion 18, and the best-ranked G6 team 10
* *WHEN* the season is simulated under `CFP12_2026`
* *THEN* the ACC champion MUST be selected
* *AND* the team ranked 12 MUST NOT be selected, because the 7 at-large places go to the teams ranked 4 to 9 and 11

### Scenario: The guaranteed team gets in within the top 12

* *GIVEN* a crafted 2026 season in which every power champion and the best-ranked G6 team are ranked outside the top 12, and the independent Notre Dame is ranked 12
* *WHEN* the season is simulated under `CFP12_2026`
* *THEN* Notre Dame MUST be selected, and the 6 at-large places MUST go to the teams ranked 1 to 6
* *AND* in the same season with Notre Dame ranked 13, Notre Dame MUST NOT be selected and the team ranked 7 MUST be

### Scenario: In 2025 the five best-ranked champions get in, whatever their conference

* *GIVEN* a crafted 2025 season whose champions are ranked: Big Ten 1, SEC 3, Big 12 4, American Athletic 20, Sun Belt 24, ACC 26
* *WHEN* the season is simulated under `CFP12_2025`
* *THEN* the American Athletic and Sun Belt champions MUST be selected
* *AND* the ACC champion MUST NOT be selected

### Scenario: In 2024 the four best-ranked champions get the byes

* *GIVEN* a crafted 2024 season whose champions are ranked 1, 2, 9 and 12, the fifth champion 16, and non-champions ranked 3 to 8
* *WHEN* the season is simulated under `CFP12_2024`
* *THEN* seeds 1 to 4 MUST be the champions ranked 1, 2, 9 and 12, in that order
* *AND* the team ranked 3 MUST be seeded 5

### Scenario: From 2025 on, seeding is straight by rank

* *GIVEN* the crafted season of the previous scenario, whose champions ranked 1, 2, 9 and 12 lead the SEC, the Big Ten, the Big 12 and the ACC, whose fifth champion, ranked 16, leads the Mountain West and is the best-ranked team of a G6 conference, and in which Notre Dame is not ranked
* *WHEN* it is simulated under `CFP12_2025`, and then under `CFP12_2026`
* *THEN* both MUST select the same 12 teams as `CFP12_2024`
* *AND* both MUST seed the four best-ranked selected teams 1 to 4, the team ranked 3 included

### Scenario: Ties in the standings use the simplified tiebreakers

* *GIVEN* a crafted conference in which A and B tie at 7–1, and A beat B; and a second conference in which C, D and E tie at 6–2 in a cycle, with conference point differentials 40, 55 and 31
* *WHEN* the season is simulated
* *THEN* A MUST rank above B
* *AND* D, C and E MUST rank in that order, by point differential, skipping head-to-head

### Scenario: A Pac-12 team can take the 2026 Group of Six bid

* *GIVEN* a crafted 2026 season whose Pac-12 has 6 FBS teams of tier `G6`
* *AND* Dunmore, second in the Pac-12 standings and not its champion, is ranked 11 as the best-ranked team of a G6 conference, while the Mountain West champion is ranked 14
* *WHEN* the season is simulated under `CFP12_2026`
* *THEN* Dunmore MUST be selected as the Group of Six bid
* *AND* the Mountain West champion MUST NOT be selected

### Scenario: In 2026 the Pac-12 is a Group of Six conference

* *GIVEN* the sim league, whose Pac-12 has 2 FBS teams in 2025 and 6 from 2026
* *WHEN* `CFB_SIM.ENGINE_TEAM_AS_OF` is queried at 2025 week 9 and at 2026 week 9
* *THEN* every Pac-12 team MUST have tier `G6` and be eligible in both seasons
* *AND* no 2026 team of tier `P4` MUST belong to the Pac-12

### Scenario: A known championship pairing that is not played yet is simulated between those teams

* *GIVEN* a crafted 2026 season at the point before championship week, in which the SEC championship game between the second and the third team of the standings is known but not played, with the second team listed first, `MU` 3.0, `SIGMA` 15.00 and `TEAM_SIGMA` 0, and `game_sigma` 15.00
* *WHEN* 4,000 seasons are simulated
* *THEN* the SEC champion MUST be one of these two teams in every season, never the leader of the standings
* *AND* the second team MUST win the title in a share of seasons within 0.03 of 0.5792, the game's `MARGIN_WIN_PROB`

### Scenario: Championship games use the known pairing, and small conferences crown no champion

* *GIVEN* a crafted 2026 season in which the SEC championship game between the standings' second and third teams is known and played, with the third team winning by 3
* *AND* a conference with no championship game whose standings are led by Eastport, and the Pac-12 with 2 FBS teams
* *WHEN* the season is simulated
* *THEN* the SEC champion MUST be the third team in every season
* *AND* Eastport MUST be its conference's champion in every season
* *AND* the Pac-12 teams MUST NOT be champions in any season

### Scenario: The bracket pairs the seeds and gives the first round a home field

* *GIVEN* a crafted season with a fixed field and seeds
* *WHEN* 2,000 seasons are simulated
* *THEN* every first-round game MUST pair seeds 5 v 12, 6 v 11, 7 v 10 and 8 v 9, and every quarterfinal seed 1 with the winner of 8/9, 2 with 7/10, 3 with 6/11 and 4 with 5/12
* *AND* in first-round games between teams of equal rating, the better seed MUST win more than half the time

### Scenario: The seeded rule sets match the published rules

* *GIVEN* a deployed simulation schema
* *WHEN* `CFB_SIM.SELECTION_RULES` is read
* *THEN* it MUST hold `CFP12_2024` for 2024, `CFP12_2025` for 2025 and `CFP12_2026` from 2026, with the values of the table above
* *AND* no rule set MUST cover a season before 2024
