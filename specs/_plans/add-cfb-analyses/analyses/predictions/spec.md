# Feature: Frozen weekly predictions

Every weekly refresh freezes a predicted score and win probability for each game of the coming week, before the game is played. A frozen prediction never changes afterwards, so the scorecard always grades predictions made in advance, even if the model is recalibrated later. When a refresh was skipped, or a deployment is new, the missing predictions are reconstructed from the leak-free as-of views and marked as made after kickoff, so nothing pretends to be earlier than it was.

## Background

- **Step script:** `EXECUTE SCRIPT CFB_INFRA.REFRESH_PREDICT(run_id, season, week, season_type)`, the contract of registered refresh steps (`loader/refresh-week`).
  - `loader/load-plan.yaml` registers it as step 200 `PREDICT`, after step 100 `MODEL` (`model/refresh-step`) and before step 300 `SIMULATE` (`sim-engine/refresh-step`). Step 400 `PUBLISH` follows the simulation (`analyses/published-odds`).
  - It runs under the refresh's run lock and takes no lock of its own. It never reads the virtual schema, so it reports 0 calls. It commits its rows and returns the number of predictions it froze.
  - A point outside the current season (`CFB_MODEL.SEASON.IS_CURRENT_SEASON` FALSE) freezes nothing: the step ends `DONE` with the detail "skipped: not the current season". The parameters `CURRENT` are fitted on 2014–2025, so predictions for those seasons would be in-sample; the calibration report covers past seasons (`sim-engine/calibration`).
- **Standalone call:** `EXECUTE SCRIPT CFB_INFRA.PREDICT(season, week, season_type)` (`make predict SEASON=… WEEK=… [SEASON_TYPE=…]`) does the same for a point of the current season outside a refresh.
  - It takes the loader's run lock as run kind `SIMULATE` (the script rule of `sim-engine/sim-runs`) with the run arguments `PREDICT <season>, <week>, <season_type>`, and is refused while another run holds the lock.
  - A point outside the current season is refused with a message and freezes nothing.
- **Point:** `(season, week, season_type)` is the row of `CFB_MODEL.AS_OF_POINT` (`model/as-of`); a NULL season type means `regular`. An unknown point is refused with a message and freezes nothing.
- **What is frozen** at point P of season S: every game of S that
  - has a week sequence of at most P's next week (`AS_OF_POINT.NEXT_WEEK_SEQ`),
  - has no row in `CFB_PRED.PREDICTION` yet,
  - and is predicted by `CFB_SIM.GAME_SCORE_AS_OF` (`sim-engine/score-model`) at the game's **freeze point**, the point whose next week is the game's week, with `PARAMS_VERSION` `CURRENT` and `HORIZON_WEEKS` 1.

  The games of P's next week are therefore frozen at P. A game of an earlier week without a prediction (a refresh that never ran, a new deployment) is reconstructed at its own freeze point, never at P. The score model covers regular-season games that involve an FBS team, conference championship games included once their teams are known; bowls and playoff games are not predicted.
- `CFB_PRED.PREDICTION`, a data table: one row per predicted game. No analysis code ever updates or deletes a row: the first freeze wins.

  | Columns | Meaning |
  |---|---|
  | `GAME_ID`, `SEASON`, `SEASON_TYPE`, `WEEK`, `WEEK_SEQ`, `START_AT` | The game, with its kickoff (UTC) when it was frozen |
  | `HOME_TEAM_ID`, `AWAY_TEAM_ID`, `IS_NEUTRAL_SITE`, `IS_CONF_CHAMPIONSHIP`, `IS_FBS_VS_FBS` | Teams and flags from `CFB_MODEL.GAME` |
  | `FREEZE_SEASON`, `FREEZE_WEEK_SEQ`, `FREEZE_LABEL` | The freeze point, for example "2026, after week 9" |
  | `PARAMS_VERSION`, `WEEK_PHASE`, `FEATURE_SET`, `PARAMS_FINGERPRINT` | The parameters used: version `CURRENT`, the freeze point's week phase, the feature set, and the fit's data fingerprint with its fit time (`SCORE_MODEL_PARAMS`) |
  | `HOME_RATING`, `AWAY_RATING`, `HOME_RATING_SOURCE`, `AWAY_RATING_SOURCE` | Both teams' ratings at the freeze point |
  | `MU`, `SIGMA`, `TOTAL`, `HOME_POINTS_PRED`, `AWAY_POINTS_PRED`, `HOME_WIN_PROB` | Copied from `GAME_SCORE_AS_OF`, with its fixed-scale `DECIMAL` types |
  | `PICK_TEAM_ID`, `PICK_WIN_PROB` | The home team when `HOME_WIN_PROB` > 0.5, the away team when it is < 0.5, NULL at exactly 0.5; `PICK_WIN_PROB` is the larger of `HOME_WIN_PROB` and 1 − `HOME_WIN_PROB` |
  | `FROZEN_AT`, `FROZEN_BEFORE_KICKOFF`, `FROZEN_BY` | UTC time from the loader's clock (`NOW_OVERRIDE` in the test namespace); TRUE when `FROZEN_AT` < `START_AT`; `REFRESH_WEEK#<run id>` or `PREDICT#<lock run id>` |

- Frozen predictions are this deployment's history, like its run logs. They are not in the snapshot scope (`loader/snapshot-restore`): a restore leaves them unchanged, and a new deployment reconstructs its predictions, marked `FROZEN_BEFORE_KICKOFF` FALSE for games already kicked off.
- The examples run over the sim league (`sim-engine/sim-runs`) in the integration-test namespace with the test parameters of `sim-engine/score-model`, in replay mode, with 2026 as the current season and the loader clock at 2026-10-28 12:00 UTC, before the first kickoff of 2026 week 10. `SIM_CONFIG` sets 2,000 seasons in batches of 500.

## Scenarios

### Scenario: The refresh freezes next week's predictions before the simulation

* *GIVEN* the sim league with no prediction stored, and the load plan registering steps 100 `MODEL`, 200 `PREDICT`, 300 `SIMULATE` and 400 `PUBLISH`
* *WHEN* the presenter runs `EXECUTE SCRIPT CFB_INFRA.REFRESH_WEEK(2026, 9, 'regular')`
* *THEN* the timeline MUST list `CHECK`, the `FETCH` steps, `MODEL`, `PREDICT`, `SIMULATE` and `PUBLISH`, in that order, each `DONE`, with 0 calls for `PREDICT`
* *AND* `CFB_PRED.PREDICTION` MUST hold exactly one row for each game of `CFB_SIM.GAME_SCORE_AS_OF` at 2026 week 9 with params `CURRENT` and `HORIZON_WEEKS` 1, with `MU`, `SIGMA`, `TOTAL`, both predicted points and `HOME_WIN_PROB` equal to that view's values
* *AND* each of those rows MUST have `FREEZE_WEEK_SEQ` 9, `FROZEN_BEFORE_KICKOFF` TRUE and `FROZEN_BY` naming the refresh run, while the week-12 game Ashford against Brookvale MUST NOT have a row
* *AND* the games of weeks 1 to 9 MUST have been reconstructed at their own freeze points, with `FROZEN_BEFORE_KICKOFF` FALSE

### Scenario: A prediction names the pick and the predicted score

* *GIVEN* the sim league with no prediction stored
* *WHEN* the owner runs `EXECUTE SCRIPT CFB_INFRA.PREDICT(2026, 11, 'regular')`
* *THEN* the week-12 game Ashford against Brookvale MUST have a row with `FREEZE_WEEK_SEQ` 11, `MU` 14.50, `SIGMA` 15.00, `TOTAL` 62.5, `HOME_POINTS_PRED` 38.5, `AWAY_POINTS_PRED` 24.0 and `HOME_WIN_PROB` 0.8330
* *AND* its `PICK_TEAM_ID` MUST be Ashford's team id with `PICK_WIN_PROB` 0.8330

### Scenario: A frozen prediction never changes

* *GIVEN* the predictions frozen by the refresh of 2026 week 9
* *AND* `B_SP` is then changed to 0.6000 in every week phase of `CURRENT`, and the week-10 results are stored
* *WHEN* the owner runs `CFB_INFRA.PREDICT(2026, 9, 'regular')`, and then `CFB_INFRA.PREDICT(2026, 10, 'regular')`
* *THEN* every week-10 row of `CFB_PRED.PREDICTION` MUST be unchanged, `FROZEN_AT` included
* *AND* the second call MUST add rows only for week-11 games, with the ratings of the changed parameters

### Scenario: Missing weeks are reconstructed at their own point and marked

* *GIVEN* the sim league with no prediction stored
* *WHEN* the owner runs `CFB_INFRA.PREDICT(2026, 9, 'regular')`
* *THEN* every game of 2026 weeks 1 to 10 that `GAME_SCORE_AS_OF` predicts at its freeze point MUST have exactly one row, a game of week w with `FREEZE_WEEK_SEQ` w − 1 and that point's values
* *AND* the rows of weeks 1 to 9 MUST have `FROZEN_BEFORE_KICKOFF` FALSE, and the rows of week 10 TRUE

### Scenario: Points outside the current season freeze nothing

* *GIVEN* replay recordings of the sim league's 2025 slices
* *WHEN* the presenter runs `REFRESH_WEEK(2025, 15, 'regular')`, and then the owner runs `CFB_INFRA.PREDICT(2025, 9, 'regular')`
* *THEN* the refresh's `PREDICT` step MUST be `DONE` with the detail "skipped: not the current season"
* *AND* the standalone call MUST fail with a message naming the season
* *AND* `CFB_PRED.PREDICTION` MUST NOT gain a row

### Scenario: Games without an FBS team are not predicted

* *GIVEN* the sim league variant `with_fcs_only_game`, which adds a 2026 week-11 game between the FCS teams Lowell and Pemberton, next to the week-11 game Ashford against Lowell
* *WHEN* the owner runs `CFB_INFRA.PREDICT(2026, 10, 'regular')`
* *THEN* `CFB_PRED.PREDICTION` MUST hold a row for Ashford against Lowell
* *AND* it MUST NOT hold a row for Lowell against Pemberton, or for any other game without an FBS team

### Scenario: A standalone prediction call is refused while another run holds the lock

* *GIVEN* a refresh run of another session that is `RUNNING` and not idle
* *WHEN* the owner runs `CFB_INFRA.PREDICT(2026, 9, 'regular')`
* *THEN* the call MUST fail with a message that names the running run
* *AND* `CFB_PRED.PREDICTION` MUST NOT gain a row
