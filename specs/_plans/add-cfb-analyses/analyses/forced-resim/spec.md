# Feature: Forced re-simulation results

A what-if filters stored seasons, so every toggle shrinks the sample. A forced re-sim answers the same question with the full sample: the published run is simulated again with the chosen results fixed, the same seed and the same draws, so the difference to the base run is the forced results alone (`sim-engine/sim-runs`). It is a deliberate act of the owner, because it writes a run; the pages and the chat read its results next to the base run's.

## Background

- **Starting one:** `EXECUTE SCRIPT CFB_INFRA.SIMULATE_FROM(base_run_id, fixed_results, run_label)`, or `make simulate BASE=… FIX="<game id>=H|A,…" [LABEL=…]` (`sim-engine/sim-runs`). It takes the run lock and writes a run, so the read-only app and chat roles (`api/roles`) cannot start one; Phase 6 decides whether the backend gets a separate owner path (`plan.md`, Dependencies).
- `CFB_PRED.FORCED_RUN_ODDS` (served as `CFB_API.FORCED_RUN_ODDS`): one row per `DONE` run with `RUN_ORIGIN` `FORCED` and eligible team of its roster:
  - `RUN_ID`, `BASE_RUN_ID`, `RUN_LABEL`, `N_SIMS`;
  - `FIXED_RESULTS`: the forced games of the run's inputs (`SIM_RUN_GAME.FIXED_HOME_WIN`), as `<game id>=H` or `<game id>=A`, comma-separated in game index order;
  - the forced run's `P_IN_CFP`, `P_BYE` and `P_CHAMPION` and the base run's (`BASE_P_IN_CFP`, `BASE_P_BYE`, `BASE_P_CHAMPION`) from `CFB_SIM.SIM_TEAM_ODDS`, and `DELTA_P_IN_CFP` and `DELTA_P_CHAMPION`.
  - A base run that was dropped (`DROPPED`) has no odds any more; its columns are then NULL.
- The forced run's full-season rooting guide is `CFB_API.ROOTING_GUIDE` for its run id (`analyses/rooting-guide`). The forced game is still one of its remaining games, with every season in the forced winner's branch.
- A forced run is never published (`analyses/published-odds`), so it never replaces the odds page; it is shown as a scenario next to it.
- The examples run in the integration-test namespace over the sim league with a published run of 4,000 seasons in 8 batches of 500 at 2026 week 9, forced with "Brookvale wins at Ashford" (the week-12 game, `A`). The test app role is a user granted `TEST_CFB_APP` (`api/roles`).

## Scenarios

### Scenario: A forced re-sim's odds are shown next to its base run's

* *GIVEN* the published run
* *WHEN* the owner runs `EXECUTE SCRIPT CFB_INFRA.SIMULATE_FROM(<published run>, '<week-12 game id>=A', 'Brookvale wins at Ashford')`
* *THEN* `CFB_API.FORCED_RUN_ODDS` MUST hold one row per eligible team of the new run, with the published run as `BASE_RUN_ID` and `FIXED_RESULTS` `'<week-12 game id>=A'`
* *AND* `P_IN_CFP` and `BASE_P_IN_CFP` MUST equal `CFB_SIM.SIM_TEAM_ODDS` of the forced and the base run
* *AND* Brookvale's `DELTA_P_IN_CFP` MUST be above 0

### Scenario: A forced run has its own full-season rooting guide

* *GIVEN* the forced run of the previous scenario
* *WHEN* `CFB_API.ROOTING_GUIDE` is queried for the forced run and Brookvale
* *THEN* the forced game's row MUST have `N_HOME` 0, `P_IF_HOME_WIN` NULL and `LOW_SAMPLE` TRUE
* *AND* every other remaining game's counts MUST equal the forced run's `CFB_SIM.LEVERAGE` for Brookvale

### Scenario: The read-only roles cannot start a forced re-sim

* *GIVEN* the published run and a user granted the app role
* *WHEN* that user runs `EXECUTE SCRIPT CFB_INFRA.SIMULATE_FROM(<published run>, '<week-12 game id>=A', NULL)`
* *THEN* the call MUST fail with an insufficient-privileges error
* *AND* `CFB_SIM.SIM_RUN` MUST NOT gain a row
