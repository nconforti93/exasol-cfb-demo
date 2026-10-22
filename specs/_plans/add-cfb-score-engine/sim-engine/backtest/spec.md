# Feature: Backtest of the 2024 and 2025 playoff fields

Before the odds are shown for 2026, the engine is run on the two finished seasons of the 12-team playoff, as if it were standing in those weeks, and its odds are scored against the fields the committee actually selected. The inputs are only what was known at each point, and the parameters are fitted on earlier seasons, so the test is honest. On the selection weekend, with every result known, the engine's formula should pick nearly the same field as the committee did.

## Background

- **Command:** `make backtest [SEASONS=2024,2025] [SIMS=20000] [BATCH=…] [TARGET=…]`. For each season S and each k in 5, 3, 1 and 0, it runs `CFB_INFRA.SIMULATE` at the point `LAST_REGULAR_WEEK_SEQ(S) − k`, with:
  - the season's own rule set (`CFP12_2024` or `CFP12_2025`);
  - params `HOLDOUT_2023`;
  - the default seed rule;
  - the label `BACKTEST <S> k=<k>`.

  Earlier backtest runs of the same label are dropped first. At k = 0 every regular-season and championship game is played, so the field is deterministic and only the bracket varies.
- **Leak-free:** every input comes from the as-of views at the point (`sim-engine/sim-runs`). The parameters and resume weights of `HOLDOUT_2023` are fitted on seasons up to 2023 (`sim-engine/calibration`).
- `CFB_SIM.BACKTEST_FIELD`: one row per season from 2024 and team of its actual playoff field. It is taken from the stored playoff games of `CFB_MODEL.GAME` (`POSTSEASON_KIND` `CFP`, with both seeds), with the team's seed, its furthest round and whether it won the title.
- `CFB_SIM.BACKTEST_REPORT`: one row per `DONE` run labeled `BACKTEST …`. It scores the run's `SIM_TEAM_ODDS` against `BACKTEST_FIELD`:
  - `BRIER_IN_CFP` over every eligible team (actual = 1 for a team in the field);
  - `EXPECTED_OVERLAP`, the sum of `P_IN_CFP` over the actual field;
  - `MISSES`, the actual teams with `P_IN_CFP` below 0.05;
  - `P_ACTUAL_CHAMPION`.

  For runs at k = 0 it also holds:
  - `SELECTED_OVERLAP`, the actual teams with `P_IN_CFP` 1;
  - `AUTO_BID_MATCHES`;
  - `BYE_OVERLAP`;
  - `SEED_MAE` over the teams in both fields.
- **Gate:** at k = 0, `SELECTED_OVERLAP` must be at least 10 in each season. The probability metrics are reported, not gated.
- The command prints the report and writes `docs/backtest-2024-2025.md` (derived numbers only). It exits with status 3 when the gate fails; the report is kept. It costs zero CFBD calls.
- The examples run over the sim league in the integration-test namespace. Its 2024 and 2025 fields were selected by the generator's committee under each season's rules. `SIMS=2000 BATCH=500`.

## Scenarios

### Scenario: The backtest simulates each season from fixed points under its own rules

* *GIVEN* the sim league with the test parameters in both versions
* *WHEN* the owner runs `make backtest SIMS=2000 BATCH=500 ARGS="--prefix TEST_"`
* *THEN* `CFB_SIM.SIM_RUN` MUST hold 8 `DONE` runs labeled `BACKTEST 2024 k=5` … `BACKTEST 2025 k=0`, each at its point, with rule set `CFP12_2024` or `CFP12_2025` and params `HOLDOUT_2023`
* *AND* the mock API and the replay store MUST NOT receive any request

### Scenario: The actual field comes from the stored playoff games

* *GIVEN* the sim league, whose 2024 first-round games pair seeds 5 v 12, 6 v 11, 7 v 10 and 8 v 9, and whose quarterfinals add seeds 1 to 4
* *WHEN* `CFB_SIM.BACKTEST_FIELD` is queried for 2024
* *THEN* it MUST hold 12 teams with seeds 1 to 12 once each
* *AND* the winner of the final MUST be the only team marked as champion

### Scenario: The report scores the odds against the actual field

* *GIVEN* the runs of the first scenario
* *WHEN* `CFB_SIM.BACKTEST_REPORT` is queried
* *THEN* it MUST hold one row per run, with `BRIER_IN_CFP` between 0 and 1, `EXPECTED_OVERLAP` between 0 and 12, and `MISSES` and `P_ACTUAL_CHAMPION` set
* *AND* each run's `EXPECTED_OVERLAP` MUST equal the sum of `P_IN_CFP` of its actual field in `SIM_TEAM_ODDS`

### Scenario: On the selection weekend the engine picks the actual field

* *GIVEN* the sim league, whose committee ranks by the resume formula with the test resume weights
* *WHEN* the backtest runs at k = 0 for 2024 and 2025
* *THEN* every eligible team MUST have `P_IN_CFP` 0 or 1
* *AND* `SELECTED_OVERLAP` and `AUTO_BID_MATCHES` MUST equal 12 and 5 in both seasons
* *AND* the 2024 seeds 1 to 4 MUST be the four best-ranked champions

### Scenario: The backtest sees only what was known at each point

* *GIVEN* the inputs of the run `BACKTEST 2024 k=5`, recorded once
* *AND* every 2024 result after that point is then changed in `CFB_CORE`
* *WHEN* the backtest runs again
* *THEN* the new `BACKTEST 2024 k=5` run MUST have the same model digest and the same batch checksums as the recorded run

### Scenario: A failed gate fails the command and keeps the report

* *GIVEN* the sim league variant whose 2025 committee selected three at-large teams that the resume formula ranks below 20
* *WHEN* the owner runs `make backtest`
* *THEN* the command MUST exit with status 3 and name the season and its selected overlap
* *AND* the runs and `docs/backtest-2024-2025.md` MUST still be written
