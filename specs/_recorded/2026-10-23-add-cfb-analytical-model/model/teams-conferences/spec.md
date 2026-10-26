# Feature: Teams, conferences and team-seasons

Conferences change over time: teams move, divisions come and go, and the set of "power" conferences that the playoff rules favor changed in 1998, 2014 and 2024. The model answers "which conference, division, classification and tier did this team have in that season" for every season back to 1869, and says where each answer came from. The simulation, the playoff selection and every season-level analysis join on this.

## Background

- `TEAM`: one row per team.
  - It contains every team in the stored team list (`CFB_CORE.TEAMS`, deduplicated by team id, newest fetch wins), plus every team that appears only in stored games (historic programs), with `IS_IN_TEAM_LIST` FALSE and its name taken from its most recent game.
  - `CURRENT_CONFERENCE`, `CURRENT_DIVISION` and `CURRENT_CLASSIFICATION` are as of the last load of the team list. Their comments say so and point to `TEAM_SEASON` for any past season.
  - `HOME_VENUE_ID` is the team's current home venue.
  - `COLOR`, `ALTERNATE_COLOR`, `LOGO_URL`, `LOGO_DARK_URL`, `LATITUDE` and `LONGITUDE` come from the team list, for the app's map and team pages:
    - `LOGO_URL` is the first URL of the team's logo array, where CFBD lists the light 500 px logo;
    - `LOGO_DARK_URL` is the second URL, the dark variant;
    - `LATITUDE` and `LONGITUDE` are the location of the home venue.

    All of them are NULL for a team that is not in the list, and a logo is NULL when the array has no URL at its position.
  - `FIRST_SEASON` and `LAST_SEASON` are the first and last season with a stored game.
- `CONFERENCE`: one row per conference in `CFB_CORE.CONFERENCES`.
- `TEAM_MEMBERSHIP`: one row per team and season with a stored membership whose slice speaks for the season, by the membership rules of `TEAM_SEASON` below (conference, division, classification of the winning spell, `HAS_MEMBERSHIP_CONFLICT`). It reads no games; `TEAM_SEASON` and `TEAM_SEASON_AS_OF` share it.
- `TEAM_SEASON`: one row per team and season in which the team has a stored game or a conference membership.
  - **Membership** comes from `CFB_CORE.CONFERENCES_AFFILIATIONS`. Each row there is a spell: team, conference, division and classification from `START_YEAR` to `END_YEAR` (open while NULL).
    - A stored slice speaks only for the seasons it was requested for: the season of a `year` slice, or `P_MIN_YEAR`–`P_MAX_YEAR` of a range slice.
    - When rows from several slices cover the same team-season, the row from the most recently fetched slice wins.
    - When two rows of the same slice name different conferences for one team-season, the spell with the later `START_YEAR` wins and `HAS_MEMBERSHIP_CONFLICT` is TRUE.
    - `CONFERENCE_SOURCE` is `AFFILIATION`.
  - Without a membership, the conference is the one recorded on most of the team's games with `FIXED` participants in that season (`CONFERENCE_SOURCE` = `GAMES`), or none (`NONE`). Conference championship and postseason games do not count, because their participants depend on results (`model/as-of`).
  - **Classification**, the first rule that applies:
    1. the reference table `CLASSIFICATION_TRANSITION` has a row for the team and season (`CLASSIFICATION_SOURCE` = `TRANSITION`);
    2. the code recorded on most of the team's games with `FIXED` participants in that season (`GAMES`);
    3. the membership's code (`AFFILIATION`);
    4. none (`NONE`).

    Games come before memberships because a game records the team itself, while a membership records its conference.
  - `CLASSIFICATION_TRANSITION`: a reference table with one row per team (CFBD school name) and season range whose classification differs from the code CFBD records on its games. Open question Q6 decides whether it needs rows:
    - if CFBD's games carry the code of their time (Appalachian State 2007 and Liberty 2017 recorded as `fcs`), it stays empty;
    - if they carry today's code, it lists every team and season range since 1978 whose classification differs from today's code, reviewed like the tier table.

    `GAME` applies the same table to the classification of each team on a game (`model/games`). Labels and the retroactive flag follow `model/seasons-eras`.
  - **Tier** comes from the reference table `CONFERENCE_TIER`:

    | Seasons | Power tier (`IS_POWER_CONFERENCE` TRUE) | Every other FBS conference |
    |---|---|---|
    | before 1998 | none | none |
    | 1998–2013 | `AQ`: ACC, Big East (to 2012), American Athletic (2013), Big Ten, Big 12, Pac-10 (to 2010) / Pac-12 (from 2011), SEC | `NON_AQ` |
    | 2014–2023 | `P5`: ACC, Big Ten, Big 12, Pac-12, SEC | `G5` |
    | 2024– | `P4`: ACC, Big Ten, Big 12, SEC | `G6` |

    `FBS Independents` has the tier `INDEPENDENT` in every season. Conference names are CFBD's names; `MODEL_CHECKS` reports a power conference of the table that no team-season of a season uses (`model/refresh-step`).
  - **Season records** from completed games:
    - `WINS`, `LOSSES` and `TIES` over every completed game, postseason included;
    - `CONF_WINS`, `CONF_LOSSES` and `CONF_TIES` over completed games flagged as conference games (`IS_CONFERENCE_GAME`), without conference championship games;
    - `POINTS_FOR`, `POINTS_AGAINST` and `CONF_POINT_DIFF` over the same games.
  - Ratings and ranks: SP+ of the season, the SP+ prior from the previous season with `SP_PRIOR_IS_FINAL`, the first pregame Elo and the last postgame Elo of the season, the final AP and committee ranks (`model/ratings-rankings`).
- There is no conference-season view: a conference's members, divisions and counts per season are a `GROUP BY` over `TEAM_SEASON`.
- The examples run over the synthetic test league in the integration-test namespace. From 1998 on, the league has a member in every power conference of every season it stores.

## Scenarios

### Scenario: Membership follows realignment

* *GIVEN* Carrow's memberships: Pac-12 from 2011 to 2023, and SEC from 2024 with no end
* *AND* the stored team list shows Carrow in the SEC
* *WHEN* `CFB_MODEL.TEAM_SEASON` is queried for Carrow in 2023 and 2024
* *THEN* 2023 MUST show the Pac-12 and 2024 the SEC, both with `CONFERENCE_SOURCE` `AFFILIATION`
* *AND* `CFB_MODEL.TEAM` MUST show the SEC as Carrow's `CURRENT_CONFERENCE`

### Scenario: A stored slice speaks only for the seasons it was requested for

* *GIVEN* the range slice 1869–2025 holds Dunmore's Pac-12 spell from 2011 with no end
* *AND* the 2026 slice holds Dunmore's Mountain West spell from 2026
* *WHEN* `CFB_MODEL.TEAM_SEASON` is queried for Dunmore in 2025 and 2026
* *THEN* 2025 MUST show the Pac-12 and 2026 the Mountain West
* *AND* neither row MUST be marked as a membership conflict

### Scenario: Conflicting memberships are resolved and flagged

* *GIVEN* one stored slice holds two spells of Northam that both cover 2014: Mountain West from 2010 to 2014 and Sun Belt from 2014
* *WHEN* `CFB_MODEL.TEAM_SEASON` is queried for Northam in 2014
* *THEN* it MUST return one row with the Sun Belt and `HAS_MEMBERSHIP_CONFLICT` TRUE
* *AND* `CFB_MODEL.MODEL_CHECKS` MUST report `MEMBERSHIP_CONFLICT` with 1 team-season for 2014

### Scenario: Power tiers follow the era

* *GIVEN* SEC and Pac-12 team-seasons in 2013, 2014, 2023 and 2024
* *WHEN* `CFB_MODEL.TEAM_SEASON` is queried for them
* *THEN* the SEC MUST have the tier `AQ` in 2013, `P5` in 2014 and `P4` in 2024, each with `IS_POWER_CONFERENCE` TRUE
* *AND* the Pac-12 MUST have `P5` with `IS_POWER_CONFERENCE` TRUE in 2023, and `G6` with `IS_POWER_CONFERENCE` FALSE in 2024

### Scenario: Other FBS conferences take the default tier of the era

* *GIVEN* Mountain West and FBS Independents team-seasons in 2013, 2014 and 2024, and team-seasons in 1985
* *WHEN* `CFB_MODEL.TEAM_SEASON` is queried for them
* *THEN* the Mountain West MUST have `NON_AQ` in 2013, `G5` in 2014 and `G6` in 2024
* *AND* FBS Independents MUST have `INDEPENDENT` in every one of those seasons
* *AND* no team-season of 1985 MUST have a tier

### Scenario: A team without a membership takes its conference from its games

* *GIVEN* no stored membership of Lowell in 1985
* *AND* Lowell's two 1985 games record its conference as Big Sky
* *WHEN* `CFB_MODEL.TEAM_SEASON` is queried for Lowell in 1985
* *THEN* the conference MUST be Big Sky with `CONFERENCE_SOURCE` `GAMES`

### Scenario: The games decide the classification when no transition is seeded

* *GIVEN* Fairhaven's 2013 membership is in the Mountain West, recorded as `fbs`
* *AND* all of Fairhaven's 2013 games record it as `fcs`, a transition season, and `CLASSIFICATION_TRANSITION` has no row for it
* *WHEN* `CFB_MODEL.TEAM_SEASON` is queried for Fairhaven in 2013
* *THEN* `CLASSIFICATION` MUST be `fcs` with `CLASSIFICATION_SOURCE` `GAMES`
* *AND* the conference MUST still be the Mountain West

### Scenario: A seeded transition overrides the recorded classification

* *GIVEN* Northam's 2014 games record it as `fbs`
* *AND* a `CLASSIFICATION_TRANSITION` row classifies Northam as `fcs` for 2010–2014
* *WHEN* `CFB_MODEL.TEAM_SEASON` and `CFB_MODEL.GAME` are queried for Northam in 2014
* *THEN* `TEAM_SEASON` MUST show `CLASSIFICATION` `fcs` with `CLASSIFICATION_SOURCE` `TRANSITION`
* *AND* every 2014 game of Northam MUST classify Northam as `fcs`

### Scenario: Teams that appear only in games are listed

* *GIVEN* Marston appears in two stored 1885 games and not in the stored team list
* *WHEN* `CFB_MODEL.TEAM` is queried for Marston
* *THEN* it MUST return one row named Marston with `IS_IN_TEAM_LIST` FALSE
* *AND* `FIRST_SEASON` and `LAST_SEASON` MUST both be 1885

### Scenario: A team carries its colors, logos and coordinates from the team list

* *GIVEN* Carrow's newest team-list row has the logo array `["https://example.invalid/500/1.png","https://example.invalid/500-dark/1.png"]`, and Dunmore's has an empty array and no color
* *WHEN* `CFB_MODEL.TEAM` is queried
* *THEN* Carrow MUST have `LOGO_URL` `https://example.invalid/500/1.png` and `LOGO_DARK_URL` `https://example.invalid/500-dark/1.png`
* *AND* Dunmore MUST have `LOGO_URL`, `LOGO_DARK_URL` and `COLOR` NULL and keep its `ALTERNATE_COLOR`
* *AND* every listed team MUST have the color, alternate color and home-venue latitude and longitude of its team-list row, and Marston MUST have all six columns NULL

### Scenario: Divisions are kept per season

* *GIVEN* Eastport's Mountain West membership has the division Mountain from 2014 to 2022 and no division from 2023
* *WHEN* `CFB_MODEL.TEAM_SEASON` is queried for Eastport in 2014 and 2024
* *THEN* `CONFERENCE_DIVISION` MUST be Mountain in 2014
* *AND* `CONFERENCE_DIVISION` MUST be NULL in 2024

### Scenario: Season records count completed games, and conference records leave out the championship game

* *GIVEN* Ashford's 2024 regular season: 12 completed games with 10 wins and 2 losses, of which 8 SEC games with 7 wins and 1 loss
* *AND* Ashford then won the SEC championship game and lost the CFP quarterfinal
* *AND* Ashford and Brookvale tied 7–7 in 1950
* *WHEN* `CFB_MODEL.TEAM_SEASON` is queried for Ashford in 2024 and 1950
* *THEN* 2024 MUST show 11 wins, 3 losses, 0 ties, 7 conference wins and 1 conference loss
* *AND* 1950 MUST count the game as a tie and not as a win or a loss
