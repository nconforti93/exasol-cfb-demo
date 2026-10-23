# Feature: League tabs: this week, odds, predictions, upsets and stats

The league view of college football, in tabs under `/league/`. "This week" is the slate: the coming week's games, most interesting first, with lines, win probabilities and what is at stake. The odds board, predictions with the scorecard, and upsets are the pages already built (`app/odds`, `app/predictions`, `app/upsets`), now hosted as tabs. "Stats" holds a few cheap, striking numbers that show what the database does with 150 seasons of games: travel distances in kilometres, rating movers, conference strength and win streaks, each with its SQL one click away.

## Background

- **Tabs and paths.** `/league/week` (the default), `/league/odds`, `/league/predictions`, `/league/upsets` and `/league/stats`. The old paths `/odds`, `/predictions` and `/upsets` keep working: they show the same tab and replace the URL with the new path, keeping the query.
- **This week** (`/api/league/week`): the FBS games of the point's next week (`CFB_API.GAMES`), each with both teams' marks, AP ranks at the point (`CFB_API.RANKINGS`), kickoff, venue, the betting line, and a win probability: ours (`CFB_API.PREDICTIONS`) when one exists, else CFBD's pregame probability, labeled by its source. When a published run exists, the stakes: the largest swing in any team's `P_IN_CFP` that the game decides (`CFB_API.ROOTING_GUIDE` of the point's run, `LEVERAGE`).
  - **Order:** by stakes when a run exists; otherwise games between two ranked teams first (by the better combined rank), then FBS games with a line within 7 points, then the rest by kickoff. The first game is the "Game of the week".
  - **Upset alert:** a ranked team whose win probability is below 65 %.
- **Stats** (`/api/league/stats`, the point's season to date):

  | Card | Reads | Shows |
  |---|---|---|
  | Road warriors | `CFB_API.TEAM_TRAVEL` (great-circle kilometres from the home venue, `GREAT_CIRCLE_KM`) | the FBS teams with the most kilometres travelled, and the longest single trip |
  | Biggest movers | `CFB_API.SP_RATINGS` | the five largest SP+ rises and falls against the season before |
  | Conference strength | `CFB_API.TEAM_SEASONS`, `SP_RATINGS`, `GAMES` | per FBS conference: mean SP+, the non-conference record against FBS, ranked teams |
  | Hottest streaks | `CFB_API.GAMES`, all stored seasons | the longest active win streaks, with the game they started |

- **SQL behind each panel.** Every response of these endpoints maps each panel to the query-log entry of the statement that produced it (`queries`). Each card has an "SQL" button that opens the inspector at that entry (`app/inspector`), showing the exact text, its time, and "no virtual table: 0 calls".

## Scenarios

### Scenario: This week lists the coming games, most interesting first

* *GIVEN* the week fixture at 2026 week 9 without a published run: two games between ranked teams, one FBS game with a 3-point line, and four other games
* *WHEN* `/league/week` is opened
* *THEN* the two ranked matchups MUST come first, the closer combined rank as "Game of the week", then the 3-point game, then the rest by kickoff
* *AND* each game MUST show both teams' marks, ranks, kickoff, the line and a win probability labeled "CFBD pregame"
* *AND* a ranked team below 65 % MUST carry an upset alert as an icon and a word

### Scenario: Each stats card shows its SQL on request

* *GIVEN* the stats fixture at 2026 week 9
* *WHEN* `/league/stats` is opened and the "SQL" button of "Road warriors" is clicked
* *THEN* the page MUST show the four cards, "Road warriors" with kilometres per team and the longest trip
* *AND* the inspector MUST open at the query-log entry the response names for that card

### Scenario: The built pages are league tabs, and old links still work

* *GIVEN* the app
* *WHEN* `/odds?team=102` is opened
* *THEN* the odds board MUST render as the "Odds" tab with Brookvale focused
* *AND* the URL MUST become `/league/odds?team=102` without a second history entry

### Scenario: Travel distance is great-circle kilometres from the home venue

* *GIVEN* the app test stack, where a team's home venue and the venues of its two away games have known coordinates
* *WHEN* `/api/league/stats` is called for that season
* *THEN* the team's kilometres MUST equal the sum of `GREAT_CIRCLE_KM` from its home venue to each away and neutral venue, within 1 km
* *AND* the statement MUST read only `CFB_API` views, through the app role
