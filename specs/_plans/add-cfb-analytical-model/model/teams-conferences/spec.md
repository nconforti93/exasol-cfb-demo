# Feature: Teams, conferences and team-seasons

Conferences change over time: teams move, divisions come and go, and the set of "power" conferences that the playoff rules favor changed in 1998, 2014 and 2024. The model answers "which conference, division, classification and tier did this team have in that season" for every season back to 1869, and says where each answer came from. The simulation, the playoff selection and every season-level analysis join on this.

## Background

- `TEAM`: one row per team.
  - It contains every team in the stored team list (`CFB_CORE.TEAMS`), plus every team that appears only in stored games (historic programs), with `IS_IN_TEAM_LIST` FALSE and its name taken from its most recent game.
  - `CURRENT_CONFERENCE`, `CURRENT_DIVISION` and `CURRENT_CLASSIFICATION` are as of the last load of the team list. Their comments say so and point to `TEAM_SEASON` for any past season.
  - `HOME_VENUE_ID` is the team's current home venue.
  - `FIRST_SEASON` and `LAST_SEASON` are the first and last season with a stored game.
- `CONFERENCE`: one row per conference in `CFB_CORE.CONFERENCES`.
- `TEAM_SEASON`: one row per team and season in which the team has a stored game or a conference membership.
  - **Membership** comes from `CFB_CORE.CONFERENCES_AFFILIATIONS`. Each row there is a spell: team, conference, division and classification from `START_YEAR` to `END_YEAR` (open while NULL).
    - A stored slice speaks only for the seasons it was requested for: the season of a `year` slice, or `P_MIN_YEAR`–`P_MAX_YEAR` of a range slice.
    - When rows from several slices cover the same team-season, the row from the most recently fetched slice wins.
    - When two rows of the same slice name different conferences for one team-season, the spell with the later `START_YEAR` wins and `HAS_MEMBERSHIP_CONFLICT` is TRUE.
    - `CONFERENCE_SOURCE` is `AFFILIATION`.
  - Without a membership, the conference is the one recorded on most of the team's games in that season (`CONFERENCE_SOURCE` = `GAMES`), or none (`NONE`).
  - **Classification** is the code recorded on most of the team's games in that season (`CLASSIFICATION_SOURCE` = `GAMES`), else the membership's code (`AFFILIATION`), else none (`NONE`). Games come first because they record the team itself, while a membership records its conference. Labels and the retroactive flag follow `model/seasons-eras`.
  - **Tier** comes from the reference table `CONFERENCE_TIER`:

    | Seasons | Power tier (`IS_POWER_CONFERENCE` TRUE) | Every other FBS conference |
    |---|---|---|
    | before 1998 | none | none |
    | 1998–2013 | `AQ`: ACC, Big East (to 2012), American Athletic (2013), Big Ten, Big 12, Pac-10 (to 2010) / Pac-12 (from 2011), SEC | `NON_AQ` |
    | 2014–2023 | `P5`: ACC, Big Ten, Big 12, Pac-12, SEC | `G5` |
    | 2024– | `P4`: ACC, Big Ten, Big 12, SEC | `G6` |

    `FBS Independents` has the tier `INDEPENDENT` in every season. Conference names are CFBD's names; `MODEL_CHECKS` reports a power conference of the table that no team-season of a season uses (`model/refresh-step`).
  - Season results from completed games: wins, losses, ties, conference wins, losses and ties, points for and against.
  - Ratings and ranks: SP+ of the season and of the previous season, the first pregame Elo and the last postgame Elo of the season, the final AP and committee ranks (`model/ratings-rankings`).
- `CONFERENCE_SEASON`: one row per conference and season with member team-seasons. It holds the classification, the tier, the member count, `HAS_DIVISIONS` and the division names.
- The examples run over the synthetic test league in the integration-test namespace.

## Scenarios

### Scenario: Membership follows realignment

* *GIVEN* Carrow's memberships: Pac-12 from 1990 to 2023, and SEC from 2024 with no end
* *AND* the stored team list shows Carrow in the SEC
* *WHEN* `CFB_MODEL.TEAM_SEASON` is queried for Carrow in 2023 and 2024
* *THEN* 2023 MUST show the Pac-12 and 2024 the SEC, both with `CONFERENCE_SOURCE` `AFFILIATION`
* *AND* `CFB_MODEL.TEAM` MUST show the SEC as Carrow's `CURRENT_CONFERENCE`

### Scenario: A stored slice speaks only for the seasons it was requested for

* *GIVEN* the range slice 1869–2025 holds Dunmore's Pac-12 spell from 1990 with no end
* *AND* the 2026 slice holds Dunmore's Mountain West spell from 2026
* *WHEN* `CFB_MODEL.TEAM_SEASON` is queried for Dunmore in 2025 and 2026
* *THEN* 2025 MUST show the Pac-12 and 2026 the Mountain West
* *AND* neither row MUST be marked as a membership conflict

### Scenario: Conflicting memberships are resolved and flagged

* *GIVEN* one stored slice holds two spells of Northam that both cover 2014: Mountain West from 2010 to 2014 and Sun Belt from 2014
* *WHEN* `CFB_MODEL.TEAM_SEASON` is queried for Northam in 2014
* *THEN* it MUST return one row with the Sun Belt and `HAS_MEMBERSHIP_CONFLICT` TRUE
* *AND* `CFB_MODEL.MODEL_CHECKS` MUST report `MEMBERSHIP_CONFLICT` with 1 team-season for 2014

### Scenario: Tiers follow the era

* *GIVEN* SEC, Pac-12, Mountain West and FBS Independents team-seasons in 1985, 2013, 2014, 2023 and 2024
* *WHEN* `CFB_MODEL.TEAM_SEASON` is queried for them
* *THEN* the SEC MUST have the tier `AQ` in 2013, `P5` in 2014 and `P4` in 2024, each with `IS_POWER_CONFERENCE` TRUE
* *AND* the Pac-12 MUST have `P5` with `IS_POWER_CONFERENCE` TRUE in 2023, and `G6` with `IS_POWER_CONFERENCE` FALSE in 2024
* *AND* the Mountain West MUST have `NON_AQ` in 2013, `G5` in 2014 and `G6` in 2024
* *AND* FBS Independents MUST have `INDEPENDENT` in every one of those seasons
* *AND* no team-season of 1985 MUST have a tier

### Scenario: A team without a membership takes its conference from its games

* *GIVEN* no stored membership of Lowell in 1985
* *AND* Lowell's two 1985 games record its conference as Big Sky
* *WHEN* `CFB_MODEL.TEAM_SEASON` is queried for Lowell in 1985
* *THEN* the conference MUST be Big Sky with `CONFERENCE_SOURCE` `GAMES`

### Scenario: The games decide the classification

* *GIVEN* Fairhaven's 2013 membership is in the Mountain West, recorded as `fbs`
* *AND* all of Fairhaven's 2013 games record it as `fcs`, a transition season
* *WHEN* `CFB_MODEL.TEAM_SEASON` is queried for Fairhaven in 2013
* *THEN* `CLASSIFICATION` MUST be `fcs` with `CLASSIFICATION_SOURCE` `GAMES`
* *AND* the conference MUST still be the Mountain West

### Scenario: Teams that appear only in games are listed

* *GIVEN* Marston appears in two stored 1885 games and not in the stored team list
* *WHEN* `CFB_MODEL.TEAM` is queried for Marston
* *THEN* it MUST return one row named Marston with `IS_IN_TEAM_LIST` FALSE
* *AND* `FIRST_SEASON` and `LAST_SEASON` MUST both be 1885

### Scenario: Divisions are kept per season

* *GIVEN* Eastport's Mountain West membership has the division Mountain from 2014 to 2022 and no division from 2023
* *WHEN* `CFB_MODEL.TEAM_SEASON` and `CFB_MODEL.CONFERENCE_SEASON` are queried for 2014 and 2024
* *THEN* Eastport's `CONFERENCE_DIVISION` MUST be Mountain in 2014 and NULL in 2024
* *AND* the Mountain West MUST have `HAS_DIVISIONS` TRUE in 2014 and FALSE in 2024

### Scenario: Season records count completed games only

* *GIVEN* Ashford's stored 2024 games: 10 completed wins, 2 completed losses including one conference loss, and 7 conference wins
* *AND* Ashford and Brookvale tied 7–7 in 1950
* *WHEN* `CFB_MODEL.TEAM_SEASON` is queried for Ashford in 2024 and 1950
* *THEN* 2024 MUST show 10 wins, 2 losses, 0 ties, 7 conference wins and 1 conference loss
* *AND* 1950 MUST count the game as a tie and not as a win or a loss
