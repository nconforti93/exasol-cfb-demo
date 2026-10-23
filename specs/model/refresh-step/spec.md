# Feature: Model step of the weekly refresh

The weekly refresh is one call: fetch, merge, model, predictions, simulations. The model step first rebuilds the model's derived tables from the fresh data (`model/deploy`), so pages and the engine read the refreshed week. It then checks that the freshly fetched season is fit for the steps that follow, records what it found, and stops the chain before predictions or simulations run on broken inputs. The same checks run on demand for any week.

## Background

- `MODEL_CHECKS`: a view with one row per check and season, and per week for week-scoped checks. Its columns are the check name, `SEVERITY`, season, week sequence, the number of offending rows (`OBSERVED`), `CHECK_STATUS` (`PASS` or `FAIL`), and a `DETAIL` that names up to 10 offending rows. Each check filters by season before it aggregates, so a filter on one season stays cheap. The checks:

  | Check | Severity | Fails when, in the season |
  |---|---|---|
  | `GAME_WITHOUT_WEEK` | ERROR | a game has no week, or a season type other than `regular` or `postseason` |
  | `FBS_TEAM_WITHOUT_CONFERENCE` | ERROR | a team-season classified `fbs`, with a classification that is not retroactive (from 1978), has no conference |
  | `POWER_CONFERENCE_MISSING` | ERROR | a power conference of `CONFERENCE_TIER` for the season has no team-season (the CFBD name changed) |
  | `DUPLICATE_SOURCE_ROW` | WARNING | a key of a season-scoped source (games, pregame lines, SP+, rankings, calendar) is stored in more than one fetched row; the model keeps the newest. `DETAIL` names the sources |
  | `CONF_CHAMPIONSHIP_MISSING` | WARNING | the season is not the current one, and a conference of `CONFERENCE_CHAMPIONSHIP` for the season has no detected championship game |
  | `CONF_CHAMPIONSHIP_NOTES_MISMATCH` | WARNING | an undetected regular-season game between two teams of one conference has notes that mention a championship, or a detected game has notes that do not |
  | `CONFERENCE_GAME_MISMATCH` | WARNING | a game with an FBS team, flagged as a conference game, is between teams whose team-seasons have different conferences |
  | `WEEK_NOT_COMPLETE` (per week) | WARNING | a game of the week is not completed |
  | `MEMBERSHIP_CONFLICT` | WARNING | a team-season has conflicting memberships |
  | `SP_TEAM_UNMATCHED` | WARNING | an SP+ row has no team |
  | `SP_PRIOR_NOT_FINAL` | WARNING | the season's SP+ priors come from a previous-season slice that is not final |
  | `POLL_UNKNOWN` | WARNING | a poll name is not in `POLL` |
  | `CONFERENCE_NAME_UNMATCHED` | WARNING | a conference recorded on a game is not in `CONFERENCE` |
  | `WEEK_ORDER_MISMATCH` | WARNING | a week starts before a week earlier in the week order (`WEEK_SEQ`), or a game kicks off outside its week's stored calendar dates; as-of answers follow the week order |
  | `GAME_TEAM_NOT_IN_TEAM_LIST` | INFO | a team of a game is not in the stored team list |
  | `VENUE_WITHOUT_LOCATION` | INFO | a game's venue has no coordinates |

- **Step script:** `EXECUTE SCRIPT CFB_INFRA.REFRESH_MODEL(run_id, season, week, season_type)`, the contract of registered refresh steps (`loader/refresh-week`).
  - `loader/load-plan.yaml` registers it as step 100 `MODEL`.
  - It first empties and refills every derived table in file order, in one transaction, and commits. A failed rebuild rolls back, so the tables keep their previous rows.
  - The script resolves the point from `AS_OF_POINT` and evaluates the season's checks plus the week's checks.
  - It writes one row per evaluated check to `CFB_META.MODEL_CHECK_LOG` with the run id and commits. The refresh commits its own step row before a step starts (`loader/refresh-week`), so this commit ends no uncommitted refresh state.
  - It then raises an error that names every failed ERROR check, if there is one.
  - It never reads the virtual schema, so it costs zero API calls.
  - A NULL `run_id` runs the step outside a refresh, under the loader's run lock (kind `MODEL`); the log rows then have no run id. A set `run_id` must be a running loader run of the same session, whose lock the step uses.
  - A NULL season (with NULL week and season type) only rebuilds the derived tables. `make backfill` and `make restore` call it so after their run.
- `make model-refresh [SEASON=… WEEK=…] [TARGET=…]` runs the step on its own.
- `make model-check SEASON=… WEEK=… [SEASON_TYPE=postseason] [TARGET=…]` prints the checks of that point from `MODEL_CHECKS`, without the step script and without writing a log.
- The examples run in the integration-test namespace in replay mode. The load plan registers `MODEL` as step 100 and a recording test step as step 200. The recordings of the 2026 weekly slices come from the synthetic league, in which no ERROR check fails for any season.

## Scenarios

### Scenario: The refresh runs the model step after the fetch

* *GIVEN* replay recordings of the league's 2026 weekly slices
* *WHEN* the presenter runs `EXECUTE SCRIPT CFB_INFRA.REFRESH_WEEK(2026, 9, 'regular')`
* *THEN* the timeline MUST list `CHECK`, the `FETCH` steps, `MODEL` and step 200 in that order, each `DONE`
* *AND* `MODEL` MUST report 0 calls
* *AND* `CFB_META.MODEL_CHECK_LOG` MUST hold one row per evaluated check with the run's id, season 2026 and, for week checks, week sequence 9

### Scenario: A failed error check stops the chain

* *GIVEN* replay recordings of the 2026 weekly slices in which the FBS team Quarry plays without any conference
* *WHEN* the presenter runs `EXECUTE SCRIPT CFB_INFRA.REFRESH_WEEK(2026, 9, 'regular')`
* *THEN* the call MUST fail with an error that names the step `MODEL`, the run and the check `FBS_TEAM_WITHOUT_CONFERENCE`
* *AND* step 200 MUST NOT run
* *AND* the 2026 slices fetched by the `FETCH` steps MUST stay stored
* *AND* `CFB_META.MODEL_CHECK_LOG` MUST keep the run's check rows, including the failed check

### Scenario: Warnings do not stop the chain

* *GIVEN* replay recordings of the 2026 weekly slices in which one week-9 game is not completed
* *WHEN* the presenter runs `EXECUTE SCRIPT CFB_INFRA.REFRESH_WEEK(2026, 9, 'regular')`
* *THEN* `MODEL` and step 200 MUST be `DONE`
* *AND* `CFB_META.MODEL_CHECK_LOG` MUST show `WEEK_NOT_COMPLETE` as failed with severity `WARNING` and 1 game

### Scenario: The checks run for any point outside a refresh

* *GIVEN* the stored synthetic league and no loader run
* *WHEN* the owner runs `EXECUTE SCRIPT CFB_INFRA.REFRESH_MODEL(NULL, 2024, 9, 'regular')`
* *THEN* the call MUST succeed
* *AND* `CFB_META.MODEL_CHECK_LOG` MUST gain the 2024 checks and the week-9 checks without a run id
* *AND* the mock API and the replay store MUST NOT receive any request

### Scenario: A renamed power conference is an error for its season

* *GIVEN* the league's 2024 SEC team-seasons are stored under the conference name "Southeastern"
* *WHEN* `CFB_MODEL.MODEL_CHECKS` is queried for 2024
* *THEN* `POWER_CONFERENCE_MISSING` MUST fail with severity `ERROR`, naming the SEC
* *AND* `EXECUTE SCRIPT CFB_INFRA.REFRESH_MODEL(NULL, 2024, 9, 'regular')` MUST fail and name that check

### Scenario: The step rebuilds the derived tables from the stored data

* *GIVEN* the stored synthetic league and the deployed model, and then a stored game whose result changes
* *WHEN* the owner runs `EXECUTE SCRIPT CFB_INFRA.REFRESH_MODEL(NULL, NULL, NULL, NULL)`
* *THEN* `GAME` and `TEAM_SEASON` MUST show the changed result
* *AND* `CFB_META.MODEL_CHECK_LOG` MUST NOT gain any row
* *AND* the call MUST be refused while another loader run holds the run lock

### Scenario: An unknown point is rejected

* *GIVEN* the stored 2024 weeks end with regular week 15 and postseason week 1
* *WHEN* the owner runs `EXECUTE SCRIPT CFB_INFRA.REFRESH_MODEL(NULL, 2024, 23, 'regular')`
* *THEN* the call MUST fail with a message that names season 2024 and regular week 23 as unknown
* *AND* `CFB_META.MODEL_CHECK_LOG` MUST NOT gain any row

### Scenario: The checks can be printed without the step

* *GIVEN* the stored synthetic league
* *WHEN* the owner runs `make model-check SEASON=2024 WEEK=9 ARGS="--prefix TEST_"`
* *THEN* the output MUST list every 2024 check and every week-9 check with severity, status and the observed count
* *AND* `CFB_META.MODEL_CHECK_LOG` MUST NOT gain any row
