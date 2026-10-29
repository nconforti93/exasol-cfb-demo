# Feature: As-of week picker

Every page shows the world as of one point: a season and a week, after that week's games and before the next week's first kickoff. The app opens at the current point, the "Sunday after week N" of the demo's hook. The picker moves every page to an earlier week together, and a link reproduces the same view. After a refresh, the pages follow the new current point, but only once the refresh has published, so a page never shows a week whose odds do not exist yet.

## Background

- **Points.**
  - The default is `CFB_API.CURRENT_POINT`.
  - The choices are the rows of `CFB_API.AS_OF_POINTS` whose `IS_AFTER_CURRENT` is FALSE (`api/serving-contract`). Each is labeled with its `AS_OF_LABEL`, for example "2026, after week 9".
  - Each choice carries the hints `HAS_PUBLICATION`, `HAS_PREDICTIONS` and `HAS_GRADES`.
  - A point is addressed by its season and week sequence: 0 is the preseason, a regular week keeps its number, and postseason week `n` is `100 + n` (`model/as-of`).
- **In the URL.** The chosen point is part of the URL (`?season=2026&week=9`, where `week` is the week sequence), so a link shows the same pages.
- **One validation for every page.** Every endpoint that takes a point (points, odds, predictions, scorecard, upsets) validates it the same way: an unknown point, or one after the current point, is answered with the current point's data plus a notice, never with an error and never with data after the current point.
- **What each page reads at point P:**

  | Page | At P |
  |---|---|
  | Odds | the latest publication of P (`PLAYOFF_ODDS`, `PUBLISHED_RUNS`); what-if toggles only at the what-if run's point (`app/odds`) |
  | Predictions | the predictions of P's next week; the grades of P's week; the season to date at P; the calibration of P's season |
  | Upsets | the upsets of P's week; the all-time lists as stored now |
  | Head-to-head | the series and meetings as stored now; the next meeting as of the current point, labeled so when P is another point |
  | Map | the FBS teams and conferences of P's season; records at P; locations as stored now (`app/map`) |
  | Team | record, schedule results and ranks at P; games after P as upcoming; the outlook of P's latest publication (`app/team`) |
  | League: this week, stats | the games of P's next week; the season to date at P (`app/league`) |

- **Follow mode.**
  - Without an explicit choice, the app follows the current point. The timeline poll (`app/refresh`) also returns the **follow key**: `AS_OF_SEASON`, `AS_OF_WEEK_SEQ`, `PUBLICATION_ID`, `WHATIF_RUN_ID` and `WHATIF_VIEWS_CURRENT` of `CURRENT_POINT`. When any of them changes, every page reloads.
  - **Reloads wait for the refresh.** A refresh merges the new games before it models, simulates and publishes, so `CURRENT_POINT` moves about a minute before the publication of the new point exists. While the newest refresh run is `RUNNING`, the app therefore holds its reloads and says "refresh running: pages update when it publishes". When the run ends, it reloads once if the key changed. This holds for a run started in the app and for one started in a SQL client alike.
  - A run that stays `RUNNING` with no step change for 10 minutes no longer holds the reloads, and the page says the run looks stuck.
  - After an explicit choice, the point stays, and a notice offers the new current point.
- **Empty states.** A page that has no data at a point says so in words, for example "no published run at 2026, after week 3". This is never shown as an error.
- The examples run on the app test stack: the current point is 2026 week 9; weeks 8 and 9 each have a publication; and the predictions frozen by the week-9 refresh include reconstructions for the earlier weeks.

## Scenarios

### Scenario: The app opens at the current point

* *GIVEN* the app test stack
* *WHEN* the app is opened without a point in the URL
* *THEN* the picker MUST show "2026, after week 9" as the current point
* *AND* every page MUST load its data at season 2026 and week sequence 9

### Scenario: Choosing an earlier week moves every page

* *GIVEN* the app open at the current point
* *WHEN* the user picks 2026 week 8
* *THEN* the URL MUST hold `season=2026&week=8`
* *AND* the odds MUST be those of the week-8 publication, the predictions those of week 9 frozen at point 8, the grades those of week 8, and the upsets those of week 8
* *AND* the head-to-head next meeting MUST be labeled "as of 2026, after week 9"

### Scenario: Points after the current one are never offered

* *GIVEN* the stored data restored to a snapshot in which 2026 week 8 is the current point, with the week-9 publication still in `CFB_PRED` (harness `snapshot_restore_at(8)`)
* *WHEN* the app is opened, then opened with `?season=2026&week=9`, and the odds and the predictions endpoints are called for 2026 week 9
* *THEN* the picker MUST NOT offer week 9
* *AND* the link MUST show the pages at week 8 with a notice that week 9 is after the current point
* *AND* both endpoints MUST answer with the week-8 data and the same notice

### Scenario: A page without data at a point says so

* *GIVEN* the app test stack, in which 2026 week 3 has no publication
* *WHEN* the user picks 2026 week 3 and opens the odds page
* *THEN* the page MUST state that there is no published run at "2026, after week 3"
* *AND* the API MUST answer with status 200 and no rows, not with an error

### Scenario: The pages follow a refresh only after it has published

* *GIVEN* the app test stack in replay mode
* *WHEN* the owner runs `EXECUTE SCRIPT TEST_CFB_INFRA.REFRESH_WEEK(2026, 10, 'regular')` in another session, and `GET /api/timeline` is read during step `WAIT` and after the run
* *THEN* the reading during `WAIT` MUST report the run as holding reloads, and its follow key MUST still name the week-9 publication
* *AND* the reading after the run MUST report no hold, and its follow key MUST name 2026 week 10 and the publication the run made

### Scenario: After a refresh the pages follow the new current point

* *GIVEN* one browser that follows the current point, and another that has picked 2026 week 8
* *WHEN* a refresh runs, first moving the current point while the run is still `RUNNING`, and then publishing week 10 and ending
* *THEN* while the run is `RUNNING` the following browser MUST NOT reload, and MUST say that the pages update when the refresh publishes
* *AND* after the run ends the following browser MUST reload every page once at week 10 without a user action
* *AND* the other browser MUST stay at week 8 and show a notice that offers week 10
