# Feature: Head-to-head page

The last part of beat 4. Pick two teams and see every meeting since 1869, who leads the series, the current streak, the next meeting with our prediction and what it means for both teams' playoff odds. All of that is stored, so it costs no call. The live moment is CFBD's game preview: it is fetched only when someone clicks, it costs one call, and the inspector shows the request.

## Background

- **Pickers.** Two teams are chosen from `CFB_API.TEAMS`, searchable by school. The pair is part of the URL (`?team=<id>&opponent=<id>`).
- **The series** reads `CFB_API.MATCHUP_SERIES` from the first team's side. It shows:
  - the meetings and the record (wins, losses and ties);
  - the points for and against;
  - the first and the last season;
  - the streak;
  - the wins at home, away and on neutral sites;
  - the biggest win and the biggest loss.

  The meetings themselves come from `CFB_API.MATCHUP_GAMES`: season, week, site, score, result, `PROB_SOURCE`, the pregame probability and `UPSET_MAGNITUDE`. The series and the meetings cost 0 calls.
- **The next meeting** reads `CFB_API.MATCHUP_NEXT` at the current point. It shows:
  - the game, its week and the site;
  - our prediction: frozen or current (`PREDICTION_KIND`), with the win probability and the predicted score;
  - the stakes: both teams' `P_IN_CFP` if each wins or loses, from the run named by `STAKES_RUN_ID`.

  When the picker is on another point, the next meeting is labeled "as of <current point>" (`app/as-of`).
- **The live preview card.**
  - **The game** is the next meeting (`MATCHUP_NEXT.GAME_ID`) when there is one, otherwise the latest meeting (`MATCHUP_SERIES.LAST_GAME_ID`) (`api/on-demand`).
  - **When it is fetched:** never on page load, by a poll or in a loop. The card shows the free plan's cost label (`app/live-spend`) and a button; the click is the request.
  - **While it loads**, the card shows "fetching from CFBD" until the row arrives.
  - **What it shows:** CFBD's view of the game from `CFB_API_LIVE.GAME_PREVIEW`:
    - each team's record and its SP+, SRS, Elo and FPI ratings;
    - the odds: provider, spread, over/under and moneylines;
    - the venue and the status, and the points once the game is played;
    - `REQ_SOURCE` and `REQ_MS`.

    The preview's `RAW_JSON` is not sent to the browser.
  - **One game per request.** Each request names one game id, checked to be an integer, and the backend writes it into the query as a literal: `WHERE GAME_ID = <id>`.
- **Session cache.** A preview is kept for the browser session: keyed by game id, for 15 minutes, at most 20 previews per session.
  - A cached preview reads "cached <time> · 0 calls" and still shows when and how it was fetched.
  - "Fetch again" makes a new request, under its own cost label.
  - A different session never reads another session's cache.
- **Teams that never met** have no series and no preview, and the page says so.
- The examples run on the app test stack with the history variant: Ashford and Brookvale met in 1950, 1985, 1987, 1990 and 1995, besides the league's own games, and Ashford hosts Brookvale in 2026 week 12. The mock API answers previews with synthetic bodies. The ledger is temporary, as in `app/live-spend`.

## Scenarios

### Scenario: The series shows without a call

* *GIVEN* live mode against the mock API
* *WHEN* the head-to-head of Ashford and Brookvale is loaded
* *THEN* the series, the meetings and the next meeting MUST equal the rows of `TEST_CFB_API.MATCHUP_SERIES`, `MATCHUP_GAMES` and `MATCHUP_NEXT` for that pair, including the 1950 tie in the record
* *AND* the mock MUST NOT receive any request, and `TEST_CFB_META.FETCH_LOG` MUST NOT change

### Scenario: Opening the page never fetches a preview

* *GIVEN* the head-to-head page of Ashford and Brookvale
* *WHEN* the page is rendered, and stays open for 30 seconds
* *THEN* the page MUST NOT send a preview request
* *AND* the preview card MUST show the cost label "1 live call" and a fetch button

### Scenario: A click fetches the next meeting's preview once

* *GIVEN* the head-to-head page of Ashford and Brookvale in live mode
* *WHEN* the user clicks the preview button
* *THEN* the card MUST show "fetching from CFBD" until the row arrives
* *AND* the mock MUST receive exactly one request, for `/games/<week-12 game id>/preview`
* *AND* the card MUST show both teams from the mock's body, with `REQ_SOURCE` `LIVE` and the request's milliseconds

### Scenario: A pair without a meeting left previews its latest meeting

* *GIVEN* a pair of the sim league that has met but does not meet again in 2026
* *WHEN* the user fetches the pair's preview
* *THEN* the request MUST be for the pair's `MATCHUP_SERIES.LAST_GAME_ID`
* *AND* the card MUST show both teams' points from the mock's body

### Scenario: A second click in the same session is served from its cache

* *GIVEN* a preview of the week-12 game fetched live in one browser session
* *WHEN* the same session clicks again, then a second session clicks for the same game, and then the first session chooses "fetch again"
* *THEN* the second click of the first session MUST be answered from the cache, reading "cached" with 0 calls, and MUST NOT reach the mock
* *AND* the second session's click and the "fetch again" MUST each make exactly one request to the mock
* *AND* the ledger MUST hold exactly three entries for the game, one per request the mock received

### Scenario: Teams that never met have no series and no preview

* *GIVEN* two teams of the sim league that never met and do not meet in 2026
* *WHEN* their head-to-head is loaded
* *THEN* the page MUST state that they never met, with no series and no preview card
* *AND* the API MUST answer with status 200, and the mock MUST NOT receive any request
