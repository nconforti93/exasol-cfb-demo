# Feature: Score model

Every simulated season and every weekly prediction starts from one question: by how much should the home team win this game, and how sure are we? The score model answers it for every game still to be played at an as-of point, from what was known at that point only. A team's rating blends its SP+ rating from the previous season with its current Elo. The home team gets a fixed edge off neutral sites. Margins are normal around the rating difference, and the uncertainty grows with the weeks until the game. The same numbers feed the simulation and the predictions page, so a game's win probability there equals its share of home wins in the simulated seasons.

## Background

- **Inputs:** only `CFB_MODEL`'s as-of views (`model/as-of`) and the previous season's SP+, which is in the past at every point of a season:
  - `TEAM_SEASON_AS_OF`: `SP_PRIOR_RATING`, `ELO_AS_OF`, `CLASSIFICATION`;
  - `GAME_AS_OF`: the games, their status at the point and the neutral-site flag;
  - `RATING_SP` of season S − 1: `OFFENSE_RATING` and `DEFENSE_RATING`, for the predicted total.

  The current season's SP+ is not a feature: it is visible only from the point at which it was fetched, which on real data is not the demo point (2026 week 8 opened 2026-10-20 23:00 UTC, before the 2026 slice was fetched), and it cannot be calibrated leak-free on past seasons. Every query of these views filters the point with literal or bound `AS_OF_SEASON` and `AS_OF_WEEK_SEQ` values; a join to `CURRENT_AS_OF` is not pushed down and evaluates every point.
- **Constants:** `CFB_SIM.SIM_PARAMS`, a seeded reference table (`PARAM_KEY`, `PARAM_VALUE` `DECIMAL(12,4)`, `PARAM_ORIGIN` `FITTED`, `ASSUMED` or `UNFITTED`, `PARAM_NOTE`). The view `CFB_SIM.SCORE_PARAMS` turns it into one row with one column per key. The score model reads:
  - fitted: `HFA`, `B_SP`, `B_ELO`, `ELO_CENTER`, `B_ELO_ONLY`, `B_SP_ONLY`, `NON_FBS_RATING`, `SIGMA_1`, `TAU`, `TOTAL_INTERCEPT`, `TOTAL_SLOPE`, `TOTAL_DEFAULT`, `ELO_PROB_SCALE`, `ELO_PROB_HFA`;
  - assumed: `FBS_DEFAULT_RATING` (−10.00).
- **Rating** (`CFB_SIM.TEAM_RATING_AS_OF`, one row per as-of point and team-season of the point's season, `RATING` `DECIMAL(6,2)` in points against an average FBS team). The first rule that applies sets `RATING_SOURCE`:

  | `RATING_SOURCE` | When | `RATING` |
  |---|---|---|
  | `SP_ELO` | prior SP+ and Elo | `B_SP` × `SP_PRIOR_RATING` + `B_ELO` × (`ELO_AS_OF` − `ELO_CENTER`) |
  | `ELO` | Elo only | `B_ELO_ONLY` × (`ELO_AS_OF` − `ELO_CENTER`) |
  | `SP` | prior SP+ only | `B_SP_ONLY` × `SP_PRIOR_RATING` |
  | `DEFAULT` | neither | `FBS_DEFAULT_RATING` for an FBS team, else `NON_FBS_RATING` |

- **Game prediction** (`CFB_SIM.GAME_SCORE_AS_OF`): one row per as-of point and game of the point's season that `GAME_AS_OF` shows as `NEXT` or `REMAINING`, with both teams known and at least one FBS team. With h = `HORIZON_WEEKS` = `WEEK_SEQ` − `AS_OF_WEEK_SEQ`:
  - `MU` = `HFA` (not on a neutral site) + home `RATING` − away `RATING`, `DECIMAL(7,2)`, home minus away;
  - `SIGMA` = √(`SIGMA_1`² + `TAU`² × (h − 1)), `DECIMAL(5,2)`: one week ahead it is `SIGMA_1`;
  - `TEAM_SIGMA` = `TAU` × √(h − 1) / √2, `DECIMAL(5,2)`: the scale of the team-strength offsets that the engine shares across a team's games (`sim-engine/engine-contract`). √(`SIGMA_1`² + 2 × `TEAM_SIGMA`²) equals the game's `SIGMA`;
  - `TOTAL` = `TOTAL_INTERCEPT` + `TOTAL_SLOPE` × (both teams' prior offense rating + defense rating), else `TOTAL_DEFAULT`, `DECIMAL(5,1)`;
  - `HOME_POINTS_PRED` = max(0, (`TOTAL` + `MU`) / 2) and `AWAY_POINTS_PRED` = max(0, (`TOTAL` − `MU`) / 2), `DECIMAL(5,1)`;
  - `HOME_WIN_PROB` = ½ (Φ((`MU` − ½) / `SIGMA`) + Φ((`MU` + ½) / `SIGMA`)), `DECIMAL(5,4)`. It matches the engine, which rounds margins to whole points and sends a tied margin to an overtime coin flip. Φ is the SQL function `CFB_SIM.NORMAL_CDF` (Abramowitz–Stegun 26.2.17, error below 7.5e−8).
  - It also carries the game's keys, kickoff, teams, `IS_NEUTRAL_SITE`, `IS_CONF_CHAMPIONSHIP`, `GAME_STATUS_AS_OF`, both ratings and both rating sources.
- **Fit** (`make sim-fit [TARGET=…]`, `engine/fit.py`, host numpy 1.26.4, read-only, 0 CFBD calls). It reads completed regular-season games of 2014–2025 from `CFB_MODEL.GAME`, `TEAM_GAME`, `TEAM_SEASON` and `RATING_SP`. A game's pregame Elo is each team's Elo as of the point before the game, so the fit is leak-free at one week ahead.
  - Least squares of `MARGIN` on the home flag (not neutral), the prior SP+ difference and the pregame Elo difference, over FBS-vs-FBS games with both features, gives `HFA`, `B_SP`, `B_ELO` and `SIGMA_1` (the residual standard deviation). `ELO_CENTER` is the mean pregame Elo of the FBS teams of those games, rounded to a whole number.
  - One-feature fits give `B_ELO_ONLY` and `B_SP_ONLY`. `NON_FBS_RATING` is the mean rating that the margins of FBS-vs-non-FBS games imply for a non-FBS opponent without an Elo.
  - `TAU` is the growth of the residual variance per week ahead, measured with each team's Elo taken k games earlier (k = 1–6). When that growth is not positive, `TAU` is 1.5 with origin `ASSUMED`.
  - `TOTAL_INTERCEPT` and `TOTAL_SLOPE` by least squares of the total points on the four prior offense and defense ratings; `TOTAL_DEFAULT` is the mean total.
  - `ELO_PROB_SCALE` and `ELO_PROB_HFA` by a grid search on the log-likelihood of the Elo win probability (`sim-engine/predictions`).
  - It writes `sql/sim/010_sim_params.sql` (generated, reviewed, committed) and prints the constants, the number of games behind each and the residual standard deviation. The same input always writes the same file.

## Scenarios

### Scenario: A team's rating blends its prior SP+ and its current Elo

* *GIVEN* the synthetic league deployed with `B_SP` 0.8000, `B_ELO` 0.0400, `ELO_CENTER` 1500, `NON_FBS_RATING` −20.00
* *WHEN* `CFB_SIM.TEAM_RATING_AS_OF` is queried for the point 2026 week 9
* *THEN* Ashford's `RATING` MUST equal 0.8 × its `SP_PRIOR_RATING` + 0.04 × (its `ELO_AS_OF` − 1500), rounded to two decimals, with `RATING_SOURCE` `SP_ELO`
* *AND* every non-FBS team without an Elo at the point MUST have `RATING` −20.00 and `RATING_SOURCE` `DEFAULT`

### Scenario: Home field counts only off neutral sites

* *GIVEN* the synthetic league with one remaining 2026 game moved to a neutral site, and `HFA` 2.50
* *WHEN* `CFB_SIM.GAME_SCORE_AS_OF` is queried for the point 2026 week 9
* *THEN* `MU` − (`HOME_RATING` − `AWAY_RATING`) MUST be 2.50 for every game that is not on a neutral site
* *AND* it MUST be 0 for the neutral-site game

### Scenario: Uncertainty grows with the horizon, and the win probability follows the margin

* *GIVEN* the synthetic league deployed with `SIGMA_1` 15.00 and `TAU` 2.00
* *WHEN* `CFB_SIM.GAME_SCORE_AS_OF` is queried for the point 2026 week 9
* *THEN* every game of week 10 MUST have `HORIZON_WEEKS` 1, `SIGMA` 15.00 and `TEAM_SIGMA` 0.00
* *AND* every game of week 13 MUST have `HORIZON_WEEKS` 4, `SIGMA` 15.39 and `TEAM_SIGMA` 2.45
* *AND* every row's `HOME_WIN_PROB` MUST equal ½ (Φ((`MU` − ½) / `SIGMA`) + Φ((`MU` + ½) / `SIGMA`)) computed on the host within 0.0001
* *AND* `HOME_POINTS_PRED` − `AWAY_POINTS_PRED` MUST equal `MU` within 0.1 wherever both are above 0

### Scenario: The fit recovers known constants

* *GIVEN* 20,000 generated games whose margins follow home field 2.5, `B_SP` 0.8, `B_ELO` 0.04, a residual standard deviation of 15 and a variance growth of 4 points² per week of Elo lag
* *WHEN* the pure fit of `engine/fit.py` runs on them
* *THEN* `HFA` MUST be within 0.3 of 2.5, `B_SP` within 0.05 of 0.8 and `B_ELO` within 0.005 of 0.04
* *AND* `SIGMA_1` MUST be within 3 % of 15, and `TAU` within 25 % of 2
* *AND* rendering the seed file twice from the same games MUST give identical text
