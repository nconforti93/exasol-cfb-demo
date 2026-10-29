# Plan: add-cfb-score-engine

## Summary

Roadmap Phase 4 as a light, demo-first plan: a Python season simulator inside Exasol gives every FBS team its 2026 playoff odds, answers "if Auburn wins this weekend, how does that change their playoff odds?" by filtering stored simulated seasons, and freezes and grades weekly predictions. It is built in three thin slices: one AWS run for 2026 after week 7 with odds and what-if storage, then predictions, the rooting guide and movement, then the refresh hook and a calibration report.

Replanned on 2026-10-23 for the owner's light process ("this is a demo, not a full-blown product offering... we don't need a crazy amount of tests"). The reviewed heavy plan (9 features, 81 scenarios, 32 tasks) is commit 29fb149. `tasks.md` lists the new tasks and marks every heavy task as absorbed, kept or superseded (`[-]`) with a one-line reason. The light decisions are [44]–[57] in `decision-log.md`.

- **Score model** (`CFB_SIM` views): a team's rating blends its previous season's SP+ with its current Elo; home field off neutral sites; normal margins whose uncertainty grows with the weeks ahead. The constants come from one host fit (`make sim-fit`) committed as a seed.
- **Engine** (`CFB_INFRA.SIM_ENGINE`, Python `SET` UDF, from the S3 prototype): fixed-shape draws per batch, shared team-strength offsets, the 2026 CFP rules, and per-batch tallies for the odds and the rooting guide.
- **Storage** (D20, with layout A): sparse `SIM_TEAM` (playoff teams only), one outcome string per simulated season in `SIM_PATH`, `LEVERAGE` summed from the engine's tallies.
- **Runs** (`CFB_INFRA.SIMULATE`, Lua): one call, under Phase 2's run lock, with an input snapshot and timings.
- **Predictions, grades, movement** (slice 2), **refresh steps 200 and 300** (slice 3).

## Design

### Context

**Spike evidence (binding, read from the main tree; the files are another session's and stay untracked):**
- **S3 → D19** (`docs/spikes/s3-python-engine-throughput.md`, `spikes/s3/results/`): Python engine GO.
  - Compute-only 100k: 7.06 s local (8 cores, 10k per batch; 7.11 s at 5k) and 1.54–1.65 s on AWS (3 × 4 cores, 1k–10k per batch), fixed per-statement cost included (local 3.2 s, AWS 0.8 s).
  - Dense full mode: AWS 12.4 s, local 40–76 s; emission is 75 % of the wall, `SIM_TEAM` rows alone 60–69 %. Rows emit at about 0.35M/s locally and 1.84M/s on AWS.
  - Use 1k–10k sims per batch, at least 2 batches per core slot, explicit `EMITS`, one DataFrame per batch, one engine pass feeding every output.
  - The prototype's draw order breaks common random numbers (review M2): Phase 4 uses fixed-shape draws.
- **S4 → D20** (`docs/spikes/s4-sim-storage.md`, `spikes/s4/results/`): sparse `SIM_TEAM` plus per-run `LEVERAGE` plus a roster; no per-sim margins; outcome layout C with A as the near-tie fallback.
  - 1M on AWS, layout C: all-team what-if p95 0.52 s (k = 1), 0.21 s (k = 3); one-team what-if below 0.2 s; `LEVERAGE` lookup 0.078 s (4.75 s to build in SQL from C).
  - 100k locally: A and C within 2–19 ms of each other on every what-if cell; A smallest and fastest to load (AWS 1M: A 7.2 s, C 30.6 s).
  - At 1M the cost sits in `SIM_TEAM`, not in the outcome layout (local A vs A+). The conditional rooting guide (k = 3) is 2.2 s on either.

**Phase 3 as built** (475c534, merged into this branch as 42b2121; coordinator note of 2026-10-23):
- `CFB_MODEL.AS_OF_POINT` and `CURRENT_AS_OF` (`AS_OF_SEASON`, `AS_OF_WEEK`, `AS_OF_SEASON_TYPE`, `AS_OF_WEEK_SEQ`, `AS_OF_LABEL`, `PREVIOUS_WEEK_SEQ`, `NEXT_WEEK_SEQ`, `LAST_REGULAR_WEEK_SEQ`, `CUTOFF_AT`, `IS_SEASON_COMPLETE`, `IS_CURRENT`). The current point is 2026, after week 7.
- `GAME_AS_OF` (`GAME_ID`, `SEASON_TYPE`, `WEEK`, `WEEK_SEQ`, `START_AT`, team ids, `HAS_TEAMS`, `INVOLVES_FBS`, `IS_FBS_GAME`, `IS_NEUTRAL_SITE`, `IS_CONFERENCE_GAME`, `IS_CONF_CHAMPIONSHIP`, `POSTSEASON_KIND`, `CFP_ROUND`, `PARTICIPANTS_RULE`, `GAME_STATUS_AS_OF`, masked `MARGIN`, `WINNER_TEAM_ID`, `PREGAME_SPREAD`, `HOME_PREGAME_WIN_PROB`, Elo). Title games appear from the point before their week.
- `TEAM_SEASON_AS_OF` (`TEAM_ID`, `SCHOOL`, `CONFERENCE_NAME`, `CONFERENCE_TIER`, `IS_POWER_CONFERENCE`, `CLASSIFICATION`, `WINS`, `LOSSES`, `CONF_WINS`, `CONF_LOSSES`, `CONF_POINT_DIFF`, `POINTS_FOR`, `POINTS_AGAINST`, `GAMES_PLAYED`, `GAMES_REMAINING`, `ELO_AS_OF`, `AP_RANK`, `CFP_RANK`, `SP_PRIOR_RATING`, `SP_CURRENT_RATING`). At 2026 week 7 `SP_CURRENT_RATING` is NULL: week 8 opened 2026-10-20 23:00 UTC, before the 2026 SP+ slice was fetched.
- `RATING_SP` (read season − 1 directly), `GAME`, `TEAM_GAME`, `TEAM_SEASON`, `CONFERENCE_CHAMPIONSHIP`, `CONFERENCE_TIER`.
- **Usage rule:** filter the as-of views with literal or bound `AS_OF_SEASON` and `AS_OF_WEEK_SEQ`. A join to `CURRENT_AS_OF` is not pushed down and evaluates every point (about 21 s).
- **AWS timings per point:** `GAME_AS_OF` 1.75 s, `TEAM_SEASON_AS_OF` 3.2 s, `RANKING_AS_OF` 0.64 s. About 87–92 % of completed FBS games carry Elo.

**Phase 2 as recorded** (`specs/loader/`): `REFRESH_WEEK` runs registered steps ≥ 100 as `(run_id, season, week, season_type)`; the run lock in `CFB_META.LOAD_RUN` already has kinds `SIMULATE` (script rule) and `DEPLOY` (host rule), with `acquire_lock`/`release_lock` in `loader/lua/loadlib.lua` and `loader/runlock.py` (hand-off H1 delivered); the Lua bundler (`loader.bundle.render_constants`); the test clock; the IT harness.

**Real 2026 schedule** (stored recording of `/games?seasonType=both&year=2026`, requested 2026-10-22, read at zero calls): regular weeks 1–13; week 8 runs 2026-10-20 to 10-25; week 13 ends 2026-11-29; no week-14 rows yet (the championship games of 5 December are listed once paired); Army–Navy alone in week 15 on 12 December.

**CFP rules for 2026** (decision [9], web research of 2026-10-22, confirmed by the review): 12 teams; the champions of the four power conferences plus the best-ranked Group of Six team (champion or not, the Pac-12 counted as Group of Six); Notre Dame in when ranked within 12; seven at-large; straight seeding with byes for the top four since 2025. The brief's wording ("5 highest-ranked conference champions … top 4 champions get byes") mixes the 2024 bye rule with the 2024–2025 bid rule; the bid rule stays one seed value away ([50]).

Forces:
- **The demo, not a product.** The pages need odds with movement, a what-if on one team, the games to watch, and frozen, graded predictions, on real 2026 data, by the November demo window (16–20 November). Each slice must end in something the owner can look at.
- **Emission dominates.** Every emitted row costs more than the engine's arithmetic, so outputs are tallied in the engine where possible.
- **Reproducibility** (mission): runs repeat from inputs and seed, so each run snapshots its inputs and records seed, batch layout and versions.
- **Leak-free inputs.** Only as-of views at a literal point; the current season's SP+ is not a feature.
- **Process:** no DB session and no CFBD call while planning; local DB work in "local free" windows under the IT lock; AWS is deploy and run only; zero CFBD calls in every task.

**Goals**
- Slice 1: `make simulate TARGET=aws SEASON=2026 WEEK=7 SIMS=1000000` stores the 2026 odds and every simulated season's outcomes, and `make what-if TEAM=Auburn WIN=next` answers the owner's question.
- Slice 2: frozen and graded predictions with a scorecard and calibration buckets, the rooting guide, and movement between points.
- Slice 3: the weekly refresh freezes and simulates (steps 200 and 300); a calibration report; the cfp.org check; docs.
- The tables and views that Phase 5 serves and Phase 6's outlook reads (§ Cross-plan hand-offs).

**Non-Goals** (superseded heavy scope, see `tasks.md`)
- Week phases, two params versions, calibration gates, the resume-weight fit, F2.
- The 2024/2025 rule sets and the backtest; runs for any season but 2026.
- Forced re-sims (`SIMULATE_FROM`), `make sim-verify`, `make sim-drop`, the step table, the batch table.
- Known championship pairings, the selection-week view, `FIXED_HOME_WIN`.
- The engine platform probe, the synthetic sim league, the loop-reference oracle, the local performance smoke with median-of-3 gating.
- The conditional rooting guide (with toggles) and the serving layer (`CFB_API`, grants): Phase 5.
- In-simulation rating updates, per-sim margins, formats other than 12 teams.

### Decision

The score model is SQL views over the as-of views, with constants from a committed seed that a host fit writes. The engine is the S3 prototype changed to fixed-shape draws, shared team offsets, the 2026 rules and a tagged stage output that carries per-batch tallies. A Lua script resolves the point, takes the run lock, snapshots the inputs with literal point filters, fans the engine out over the batches, splits the stage into the output tables with plain SQL, and records the timings. Outcomes are stored as one string per simulated season (layout A), so the what-if needs no per-run DDL.

#### Architecture

```
 CFB_MODEL (Phase 3)  AS_OF_POINT · CURRENT_AS_OF · GAME_AS_OF · TEAM_SEASON_AS_OF · RATING_SP · GAME · TEAM_GAME · CONFERENCE_CHAMPIONSHIP
        │   (as-of views always filtered with literal AS_OF_SEASON, AS_OF_WEEK_SEQ)
        ▼  CFB_SIM (sql/sim/, deployed by engine/deploy.py through vs/host/sqlobjects.py)
 SIM_PARAMS ──▶ SCORE_PARAMS ──▶ TEAM_RATING_AS_OF ──▶ GAME_SCORE_AS_OF (MU, SIGMA, TEAM_SIGMA, TOTAL, points, HOME_WIN_PROB)
 SEASON_RULES ─┐                         │                        │
               └──────────▶ ENGINE_TEAM_AS_OF · ENGINE_GAME_AS_OF ◀┘
        ▲
 make sim-fit (host numpy, read-only) ─▶ sql/sim/010_sim_params.sql (generated, reviewed, committed)

 EXECUTE SCRIPT CFB_INFRA.SIMULATE(2026, 7, 'regular', 1000000, 10000, NULL, 'demo')
   lock (LOAD_RUN kind SIMULATE) · resolve the point (AS_OF_POINT) · refuse (rules, SELECTION_AT, batch sizes)
   1 REGISTER   SIM_RUN (RUNNING), commit
   2 FREEZE     PREDICTION ◀ GAME_SCORE_AS_OF @Q for each unfrozen point Q ≤ P (slice 2)
   3 INPUTS     SIM_RUN_TEAM ◀ ENGINE_TEAM_AS_OF @P ; SIM_RUN_GAME ◀ ENGINE_GAME_AS_OF @P ⋈ SIM_RUN_TEAM ; SIM_RUN_PARAM
   4 ENGINE     INSERT INTO SIM_STAGE SELECT CFB_INFRA.SIM_ENGINE(run constants, model row)
                  FROM SIM_RUN_MODEL_ROWS CROSS JOIN (VALUES BETWEEN 0 AND 99) AS b("BATCH_ID") GROUP BY batch
                ┌ per batch: SeedSequence([seed, batch]).spawn(2) → season (z, u, team offsets d), postseason (z, u)
                │ season → standings → championship games → resume → selection (2026 rules) → bracket
                └ one DataFrame: T playoff rows · P outcome string · W wins tally · C title tally · G home wins · L in-field × home-win counts · B info
   5 OUTPUTS    SIM_TEAM ◀ T · SIM_PATH ◀ P · TEAM_WINS ◀ W · CONF_CHAMP ◀ C · SIM_RUN_GAME.N_HOME_WINS ◀ G · LEVERAGE ◀ L + G + T
   6 FINISH     TRUNCATE SIM_STAGE · SIM_RUN DONE, seconds, instances, nodes, versions · commit · release the lock
   any error ─▶ rollback · delete the run's outputs · truncate the stage · FAILED · release · raise
        │
        ▼  views over DONE runs
 SIM_TEAM_ODDS ─▶ POINT_RUN ─▶ CURRENT_RUN · ODDS_MOVEMENT        LEVERAGE ─▶ ROOTING_GUIDE
 PREDICTION ─▶ PREDICTION_GRADED ─▶ PREDICTION_SCORECARD · PREDICTION_CALIBRATION       GAME ─▶ GAME_WIN_PROB
 what-if: engine/whatif.py ─▶ SUBSTR(OUTCOMES, PATH_POS, 1) filters on SIM_PATH, counts in SIM_TEAM
        │
        ▼  Phase 5: thin CFB_API views (read-only roles) ─▶ Phase 6 pages and 9.8 outlook; Phase 7 chat
 REFRESH_WEEK: … ─▶ 100 MODEL ─▶ 200 PREDICT (REFRESH_PREDICT) ─▶ 300 SIMULATE (REFRESH_SIMULATE)      (slice 3)
```

#### Patterns

| Pattern | Where | Why |
|---------|-------|-----|
| Constants as a generated, reviewed seed | `sql/sim/010_sim_params.sql` written by `make sim-fit` | A fit is a deliberate, diffable act; local and AWS runs use the same constants because they travel in git |
| One formula, in SQL | `TEAM_RATING_AS_OF` → `GAME_SCORE_AS_OF`, read by the snapshot and by the freeze | Predictions and simulations cannot drift apart; the engine receives `MU` and `TEAM_SIGMA`, not ratings and formulas |
| Literal point filters | the Lua scripts resolve the point first, then filter every as-of view with its numbers | Phase 3's rule: a join to `CURRENT_AS_OF` is not pushed down (21 s) |
| Input snapshot per run | `SIM_RUN_TEAM`, `SIM_RUN_GAME`, `SIM_RUN_PARAM` | The engine reads only the snapshot; a run stays repeatable after any refresh, and the snapshot doubles as the roster and the game map of the what-if |
| One engine pass, one tagged stage shape, SQL splits | `SIM_ENGINE … EMITS (<SIM_STAGE columns>)` → `SIM_STAGE` | S3: every extra output statement costs a full engine pass |
| Tally in the engine, not over stored seasons | `W`, `C`, `G`, `L` rows | Leverage is one matrix product per batch in numpy; recounting it from 1M stored strings would unpivot hundreds of columns |
| Fixed-shape draws from two child streams | `SeedSequence([seed, batch]).spawn(2)` | A change to one game never shifts another game's draws; repeatable regardless of scheduling |
| Shared team offsets with unchanged marginals | `TEAM_SIGMA` per game, `game_sigma` = `SIGMA_1` | Season odds get realistic spread (review M1) while every game keeps the score model's probability |
| Fixed outcome table (layout A) | `SIM_PATH (RUN_ID, SIM_ID, OUTCOMES)` | No per-run DDL, no generated views, the same SQL every week; S4 measured A level with C at 100k |
| Sentinels instead of NULLs in the emitted frame | `SIM_STAGE` | Avoids the untested NaN/NULL path of DataFrame emission |
| Readers see only `DONE` runs; failure cleans up; the next lock holder cleans up kills | views, `SIMULATE`, slice 3 | A killed script cannot log its end (Phase 2 probe 1.1j) |
| Thin Lua over declarative SQL | `engine/lua/simlib.lua` + views | Same split as Phases 2 and 3; the SQL is testable without Lua |

#### Key Interfaces

**Objects of `CFB_SIM`** (files `sql/sim/NNN_<object>.sql`, data tables `sql/sim/data/<object>.sql`; every comment starts with "One row per <grain>"; DDL names unquoted upper case; allowed references `{SIM_SCHEMA}.*` and `{MODEL_SCHEMA}.*`)

| File | Object | Kind | Grain | Slice |
|---|---|---|---|---|
| 010 | `SIM_PARAMS` | reference, generated by `make sim-fit`; `REFRESH_*` rows per target from `engine/sim-config.yaml` | parameter | 1 |
| 011 | `SEASON_RULES` | reference | season that may be simulated (2026) | 1 |
| 012 | `SCORE_PARAMS` | view (pivot of `SIM_PARAMS`) | the parameter set (1 row) | 1 |
| 020 | `NORMAL_CDF(X)` | function, A&S 26.2.17 | — | 1 |
| 100 | `TEAM_RATING_AS_OF` | view | as-of point and team-season | 1 |
| 110 | `GAME_SCORE_AS_OF` | view | as-of point and `NEXT`/`REMAINING` game with both teams known and an FBS team | 1 |
| 200 | `ENGINE_TEAM_AS_OF` | view | as-of point and roster team: FBS team-seasons plus non-FBS opponents of remaining games | 1 |
| 210 | `ENGINE_GAME_AS_OF` | view | as-of point and engine game (`REMAINING`, `PLAYED_CONF`), with team ids | 1 |
| 230 | `SIM_RUN_MODEL_ROWS` | view | run and tagged model row | 1 |
| 300 | `SIM_TEAM_ODDS` | view | `DONE` run and eligible roster team | 1 |
| 310 | `POINT_RUN` | view | as-of point with a `DONE` run | 2 |
| 315 | `CURRENT_RUN` | view | the run of the newest point at or before the current point (≤ 1 row) | 2 |
| 320 | `ODDS_MOVEMENT` | view | `POINT_RUN` and eligible team | 2 |
| 330 | `ROOTING_GUIDE` | view | `DONE` run, team in `LEVERAGE`, remaining game | 2 |
| 400 | `PREDICTION_GRADED` | view | prediction | 2 |
| 410 | `PREDICTION_SCORECARD` | view | season, played week (NULL = season to date), population, freeze kind | 2 |
| 420 | `PREDICTION_CALIBRATION` | view | season, population, freeze kind, pick-probability bucket | 2 |
| 430 | `GAME_WIN_PROB` | view | completed game with a winner, an FBS team and a pregame probability | 2 |
| data | `SIM_RUN`, `SIM_RUN_TEAM`, `SIM_RUN_GAME`, `SIM_RUN_PARAM`, `SIM_STAGE`, `SIM_TEAM`, `SIM_PATH`, `TEAM_WINS`, `CONF_CHAMP`, `LEVERAGE` | data tables (created if missing, never replaced or dropped) | below | 1 |
| data | `PREDICTION` | data table | predicted game (frozen once) | 2 |

**Scripts in `CFB_INFRA`:** `SIM_ENGINE` (`PYTHON3 SET`, explicit `EMITS`, body = `engine/core.py` + `engine/udf.py`, comment names `ENGINE_VERSION`); Lua `SIMULATE` (slice 1), `PREDICT` (slice 2), `REFRESH_PREDICT` and `REFRESH_SIMULATE` (slice 3), each bundled as `render_constants` + `loader/lua/loadlib.lua` + `engine/lua/simlib.lua` + its entry file.

**`SIM_PARAMS`** (`PARAM_KEY`, `PARAM_VALUE` `DECIMAL(12,4)`, `PARAM_ORIGIN`, `PARAM_NOTE`)

| Keys | Origin | Used by |
|---|---|---|
| `HFA`, `B_SP`, `B_ELO`, `ELO_CENTER`, `B_ELO_ONLY`, `B_SP_ONLY`, `NON_FBS_RATING`, `SIGMA_1`, `TAU`, `TOTAL_INTERCEPT`, `TOTAL_SLOPE`, `TOTAL_DEFAULT` | `FITTED` by `make sim-fit` (`UNFITTED` placeholders until 1.3: the S3 defaults `HFA` 2.5, `B_SP` 1, `B_ELO` 0, `SIGMA_1` 15, `TAU` 0) | score model |
| `ELO_PROB_SCALE`, `ELO_PROB_HFA` | `FITTED` (grid search) | `GAME_WIN_PROB` |
| `FBS_DEFAULT_RATING` −10, `OT_MARGIN` 3, `MARGIN_CAP` 24, `W_WIN_PCT` 100, `W_SOS` 2.0, `W_PD` 0.5, `W_RATING` 0 | `ASSUMED` (S3 prototype values) | score model, engine |
| `MOVE_THRESHOLD` 0.0070, `MIN_BRANCH_SAMPLE` 100 | `ASSUMED` (3 SE of a difference of two 100k runs) | `ODDS_MOVEMENT`, `ROOTING_GUIDE` |
| `REFRESH_N_SIMS`, `REFRESH_SIMS_PER_BATCH` | per target (`engine/sim-config.yaml`: local 100,000 / 5,000, aws 1,000,000 / 10,000) | `REFRESH_SIMULATE` (slice 3) |

**Data tables** (`sql/sim/data/*.sql`; every column commented in the files; the readers' contract is § Cross-plan hand-offs)

```sql
CREATE TABLE IF NOT EXISTS {SIM_SCHEMA}.SIM_RUN (RUN_ID DECIMAL(18,0) IDENTITY NOT NULL, RUN_STATUS VARCHAR(10) UTF8 NOT NULL,
  RUN_ORIGIN VARCHAR(8) UTF8 NOT NULL, RUN_LABEL VARCHAR(200) UTF8, LOAD_RUN_ID DECIMAL(18,0),
  AS_OF_SEASON DECIMAL(4,0) NOT NULL, AS_OF_WEEK DECIMAL(3,0) NOT NULL, AS_OF_SEASON_TYPE VARCHAR(10) UTF8 NOT NULL,
  AS_OF_WEEK_SEQ DECIMAL(3,0) NOT NULL, AS_OF_LABEL VARCHAR(100) UTF8,
  RUN_SEED DECIMAL(18,0) NOT NULL, N_SIMS DECIMAL(18,0) NOT NULL, SIMS_PER_BATCH DECIMAL(9,0) NOT NULL, N_BATCHES DECIMAL(9,0) NOT NULL,
  N_TEAMS DECIMAL(5,0), N_GAMES DECIMAL(5,0), N_LEFT_OUT_GAMES DECIMAL(5,0), MODEL_DIGEST VARCHAR(64) ASCII,
  ENGINE_VERSION VARCHAR(16) ASCII, NUMPY_VERSION VARCHAR(20) ASCII, PYTHON_VERSION VARCHAR(20) ASCII,
  INSTANCES DECIMAL(5,0), NODES DECIMAL(4,0), STARTED_AT TIMESTAMP NOT NULL, ENDED_AT TIMESTAMP,
  SECONDS_INPUTS DECIMAL(9,3), SECONDS_ENGINE DECIMAL(9,3), SECONDS_OUTPUTS DECIMAL(9,3), SECONDS_TOTAL DECIMAL(9,3),
  SIMS_PER_SECOND DECIMAL(12,1), DETAIL VARCHAR(2000) UTF8);
CREATE TABLE IF NOT EXISTS {SIM_SCHEMA}.SIM_RUN_TEAM (RUN_ID DECIMAL(18,0) NOT NULL, TEAM_IDX DECIMAL(5,0) NOT NULL,
  TEAM_ID DECIMAL(18,0) NOT NULL, SCHOOL VARCHAR(200) UTF8, CONFERENCE VARCHAR(100) UTF8, TIER VARCHAR(8) UTF8 NOT NULL,
  HAS_CCG BOOLEAN NOT NULL, IS_ELIGIBLE BOOLEAN NOT NULL, RATING DECIMAL(6,2) NOT NULL, RATING_SOURCE VARCHAR(8) UTF8 NOT NULL,
  SOS DECIMAL(6,2) NOT NULL, WINS DECIMAL(2,0) NOT NULL, LOSSES DECIMAL(2,0) NOT NULL, CONF_WINS DECIMAL(2,0) NOT NULL,
  CONF_LOSSES DECIMAL(2,0) NOT NULL, CONF_PD DECIMAL(5,0) NOT NULL, CAPPED_PD DECIMAL(5,0) NOT NULL);
CREATE TABLE IF NOT EXISTS {SIM_SCHEMA}.SIM_RUN_GAME (RUN_ID DECIMAL(18,0) NOT NULL, GAME_ROLE VARCHAR(12) UTF8 NOT NULL,
  GAME_IDX DECIMAL(5,0) NOT NULL, PATH_POS DECIMAL(5,0), GAME_ID DECIMAL(18,0) NOT NULL, WEEK_SEQ DECIMAL(3,0), START_AT TIMESTAMP,
  IS_NEXT_WEEK BOOLEAN, HOME_TEAM_ID DECIMAL(18,0) NOT NULL, AWAY_TEAM_ID DECIMAL(18,0) NOT NULL, HOME_IDX DECIMAL(5,0) NOT NULL,
  AWAY_IDX DECIMAL(5,0) NOT NULL, IS_NEUTRAL_SITE BOOLEAN, CONF_GAME BOOLEAN, MU DECIMAL(7,2), SIGMA DECIMAL(5,2),
  TEAM_SIGMA DECIMAL(5,2), HOME_WIN_PROB DECIMAL(5,4), MARGIN DECIMAL(4,0), N_HOME_WINS DECIMAL(18,0));
  -- REMAINING rows: PATH_POS = GAME_IDX + 1, MU … HOME_WIN_PROB, N_HOME_WINS (after the engine); PLAYED_CONF rows: MARGIN
CREATE TABLE IF NOT EXISTS {SIM_SCHEMA}.SIM_RUN_PARAM (RUN_ID DECIMAL(18,0) NOT NULL, PARAM_KEY VARCHAR(40) ASCII NOT NULL,
  PARAM_VALUE DECIMAL(12,4) NOT NULL);          -- the engine's lower-case keys (sim-engine/engine-contract)
CREATE TABLE IF NOT EXISTS {SIM_SCHEMA}.SIM_STAGE (RUN_ID DECIMAL(18,0) NOT NULL, BATCH_ID DECIMAL(9,0) NOT NULL,
  ROW_KIND CHAR(1) ASCII NOT NULL, SIM_ID DECIMAL(18,0), TEAM_ID DECIMAL(18,0), GAME_IDX DECIMAL(5,0), SEED DECIMAL(2,0),
  BYE BOOLEAN, REACHED_ROUND DECIMAL(1,0), CHAMPION BOOLEAN, WINS DECIMAL(2,0), N DECIMAL(18,0),
  OUTCOMES VARCHAR(2000) ASCII, INFO VARCHAR(2000) UTF8);   -- SIM_ENGINE EMITS exactly these columns
CREATE TABLE IF NOT EXISTS {SIM_SCHEMA}.SIM_TEAM (RUN_ID DECIMAL(18,0) NOT NULL, SIM_ID DECIMAL(18,0) NOT NULL,
  TEAM_ID DECIMAL(18,0) NOT NULL, SEED DECIMAL(2,0) NOT NULL, BYE BOOLEAN NOT NULL, REACHED_ROUND DECIMAL(1,0) NOT NULL,
  CHAMPION BOOLEAN NOT NULL);
CREATE TABLE IF NOT EXISTS {SIM_SCHEMA}.SIM_PATH (RUN_ID DECIMAL(18,0) NOT NULL, SIM_ID DECIMAL(18,0) NOT NULL,
  OUTCOMES VARCHAR(2000) ASCII);                 -- character PATH_POS: 1 = home win, 0 = away win; NULL when no game remains
CREATE TABLE IF NOT EXISTS {SIM_SCHEMA}.TEAM_WINS (RUN_ID DECIMAL(18,0) NOT NULL, TEAM_ID DECIMAL(18,0) NOT NULL,
  WINS DECIMAL(2,0) NOT NULL, N DECIMAL(18,0) NOT NULL);
CREATE TABLE IF NOT EXISTS {SIM_SCHEMA}.CONF_CHAMP (RUN_ID DECIMAL(18,0) NOT NULL, TEAM_ID DECIMAL(18,0) NOT NULL,
  N DECIMAL(18,0) NOT NULL);
CREATE TABLE IF NOT EXISTS {SIM_SCHEMA}.LEVERAGE (RUN_ID DECIMAL(18,0) NOT NULL, TEAM_ID DECIMAL(18,0) NOT NULL,
  GAME_IDX DECIMAL(5,0) NOT NULL, N_HOME DECIMAL(18,0) NOT NULL, N_HOME_IN_CFP DECIMAL(18,0) NOT NULL,
  N_AWAY DECIMAL(18,0) NOT NULL, N_AWAY_IN_CFP DECIMAL(18,0) NOT NULL);
-- PREDICTION (slice 2): the columns of sim-engine/predictions, MU DECIMAL(7,2), SIGMA DECIMAL(5,2), TOTAL and points
--   DECIMAL(5,1), probabilities DECIMAL(5,4), FREEZE_KIND VARCHAR(16), FROZEN_BY VARCHAR(64)
```

Reserved words (`vs/gen/exasol_reserved_keywords.txt`): every planned name was checked; `SOURCE`, `VALUE`, `POSITION`, `SESSION`, `ROWS`, `CURRENT` and `NEXT` are reserved and appear only inside compound names (`RATING_SOURCE`, `PARAM_VALUE`, `PATH_POS`, `IS_NEXT_WEEK`, `CURRENT_RUN`). The task that writes a file re-runs the lint.

**Engine inputs** (`ENGINE_TEAM_AS_OF`, `ENGINE_GAME_AS_OF` at point P of season S; the snapshot assigns `TEAM_IDX` by `TEAM_ID` and `GAME_IDX` by `START_AT`, `GAME_ID`)

| Input | Rule |
|---|---|
| roster | FBS team-seasons of `TEAM_SEASON_AS_OF` plus the non-FBS opponents of `REMAINING` games; `IS_ELIGIBLE` = FBS with at least one game in S |
| `CONFERENCE`, `TIER` | `CONFERENCE_NAME`; `P4` / `G6` from `CONFERENCE_TIER`, `IND` for FBS independents, `NON_FBS` (conference NULL) otherwise |
| `HAS_CCG` | the conference has a `CONFERENCE_CHAMPIONSHIP` row covering S |
| `RATING` | `TEAM_RATING_AS_OF` |
| `SOS` | mean `RATING` of the opponents of the team's regular-season games before `SELECTION_AT` (played and remaining), championship games excluded |
| `WINS`, `LOSSES`, `CONF_WINS`, `CONF_LOSSES`, `CONF_PD` | `TEAM_SEASON_AS_OF` (no championship game is played before a valid point) |
| `CAPPED_PD` | Σ of the team's margins of `PLAYED` regular-season games, each capped at ±`MARGIN_CAP` |
| `REMAINING` games | `GAME_SCORE_AS_OF` rows that are regular season, not `IS_CONF_CHAMPIONSHIP`, and start before `SELECTION_AT`; `CONF_GAME` = `IS_CONFERENCE_GAME` and both teams in one conference |
| `PLAYED_CONF` games | `GAME_AS_OF` `PLAYED` conference games between two teams of one conference, with `MARGIN` |
| left out | `NOT_PLAYED` games, games after `SELECTION_AT`; counted in `SIM_RUN.N_LEFT_OUT_GAMES` |
| `PARAM` rows | `hfa` = `HFA`; `game_sigma` = `SIGMA_1`; `post_team_sigma` = `TAU` × √(h − 1) / √2 with h = the last remaining week − P + 1; `ot_margin`, `margin_cap`, `w_*` from `SIM_PARAMS`; `auto_bid_rule`, `auto_bids`, `g6_bid`, `guarantee_rank`, `min_conference_teams` from `SEASON_RULES`; `guarantee_team_idx` = the roster index of `GUARANTEE_SCHOOL`, else −1 |

**Run SQL** (written by `simlib.lua`; names are the namespace's constants, quoted; values bound, except whole numbers that the script checked)

```sql
-- 4 ENGINE: one SET group per batch; the last batch id and the run constants are whole numbers in the text
INSERT INTO "CFB_SIM"."SIM_STAGE"
SELECT "CFB_INFRA"."SIM_ENGINE"(42, 2026007, 10000, b."BATCH_ID", m."ROW_KIND", m."GAME_ROLE", m."TEAM_IDX", … , m."PARAM_VALUE")
  FROM "CFB_SIM"."SIM_RUN_MODEL_ROWS" m
 CROSS JOIN (VALUES BETWEEN 0 AND 99) AS b("BATCH_ID")
 WHERE m."RUN_ID" = 42
 GROUP BY b."BATCH_ID";
-- 5 OUTPUTS (the stage holds one run: the lock makes the script its only writer)
INSERT INTO "CFB_SIM"."SIM_TEAM" SELECT "RUN_ID", "SIM_ID", "TEAM_ID", "SEED", "BYE", "REACHED_ROUND", "CHAMPION" FROM "CFB_SIM"."SIM_STAGE" WHERE "ROW_KIND" = 'T';
INSERT INTO "CFB_SIM"."SIM_PATH" SELECT "RUN_ID", "SIM_ID", "OUTCOMES" FROM "CFB_SIM"."SIM_STAGE" WHERE "ROW_KIND" = 'P';
-- TEAM_WINS, CONF_CHAMP: SUM("N") by team (and wins); SIM_RUN_GAME.N_HOME_WINS: MERGE of SUM("N") of 'G' by GAME_IDX
INSERT INTO "CFB_SIM"."LEVERAGE"
SELECT 42, t."TEAM_ID", g."GAME_IDX", g."N_HOME_WINS", COALESCE(l."N", 0), 1000000 - g."N_HOME_WINS", t."N_IN_CFP" - COALESCE(l."N", 0)
  FROM (SELECT "TEAM_ID", COUNT(*) AS "N_IN_CFP" FROM "CFB_SIM"."SIM_STAGE" WHERE "ROW_KIND" = 'T' GROUP BY "TEAM_ID") t
 CROSS JOIN (SELECT "GAME_IDX", "N_HOME_WINS" FROM "CFB_SIM"."SIM_RUN_GAME" WHERE "RUN_ID" = 42 AND "GAME_ROLE" = 'REMAINING') g
  LEFT JOIN (SELECT "TEAM_ID", "GAME_IDX", SUM("N") AS "N" FROM "CFB_SIM"."SIM_STAGE" WHERE "ROW_KIND" = 'L' GROUP BY "TEAM_ID", "GAME_IDX") l
    ON l."TEAM_ID" = t."TEAM_ID" AND l."GAME_IDX" = g."GAME_IDX";
-- B rows (one per batch, ≤ 10,000) are read by the script and parsed in Lua: every batch present, one model digest,
--   INSTANCES = distinct vm ids, NODES = distinct node ids, versions
TRUNCATE TABLE "CFB_SIM"."SIM_STAGE";
```

**What-if SQL** (`engine/whatif.py`; S4's measured sparse shapes, with layout A's conditions; schema and table names are parameters, so Phase 5 can point them at its `CFB_API` views)

```sql
-- one team (S4 Q2 shape): "if team 2 wins game g (home, PATH_POS 37)"
WITH "M" AS (SELECT "SIM_ID" FROM "CFB_SIM"."SIM_PATH" WHERE "RUN_ID" = 42 AND SUBSTR("OUTCOMES", 37, 1) = '1')
SELECT COUNT(*) AS "N_MATCHING", COUNT(t."SIM_ID") AS "N_IN_CFP",
       SUM(CASE WHEN t."BYE" THEN 1 ELSE 0 END) AS "N_BYE", SUM(CASE WHEN t."CHAMPION" THEN 1 ELSE 0 END) AS "N_CHAMPION"
  FROM "M" LEFT JOIN (SELECT "SIM_ID", "BYE", "CHAMPION" FROM "CFB_SIM"."SIM_TEAM" WHERE "RUN_ID" = 42 AND "TEAM_ID" = 2) t
    ON t."SIM_ID" = "M"."SIM_ID";
-- all teams (S4 Q1 shape): the eligible roster of SIM_RUN_TEAM cross-joined with COUNT(*) of "M", left-joined with
--   per-team counts of SIM_TEAM JOIN "M" ON "SIM_ID" WHERE "RUN_ID" = 42 GROUP BY "TEAM_ID"; zero counts, never no rows
```

At most 16 conditions; every number is a validated `int` (run, team, positions), the winner is `'1'` or `'0'`, identifiers are double-quoted.

**Code layout**

```
engine/__init__.py, core.py (pure numpy, from the S3 prototype), udf.py (UDF wrapper), bundle.py (script text, ENGINE_VERSION),
       fit.py (pure fits, seed renderer, DB read), whatif.py (pure SQL builder), deploy.py (make sim), sim-config.yaml (slice 3)
engine/lua/simlib.lua, simulate.lua, predict.lua, refresh_predict.lua, refresh_simulate.lua
scripts/sim.py            deploy | fit | simulate | what-if | predict
sql/sim/*.sql, sql/sim/data/*.sql
tests/engine/models.py    crafted models
tests/unit/test_engine_core.py, test_selection_rules.py, test_engine_bundle.py, test_fit.py, test_whatif.py
tests/it/test_sim_deploy_it.py, test_score_model_it.py, test_sim_runs_it.py, test_sim_engine_it.py, test_predictions_it.py,
         test_sim_refresh_step_it.py
```

**Expected run time** (estimates from the spike CSVs and Phase 3's AWS timings; measured in 1.4, 1.5 and 2.3; about 400 remaining games at 2026 week 7)

| Step | Local 100k (8 cores, 5k/batch) | AWS 100k (3 × 4, 5k/batch) | AWS 1M (3 × 4, 10k/batch) | Basis |
|---|---|---|---|---|
| Lock, register, checks | 0.2 s | 0.2 s | 0.2 s | Phase 2 small statements |
| Freeze (slice 2; one point once frozen) | 2–6 s | 2–6 s | 2–6 s | `GAME_SCORE_AS_OF` reads both as-of views |
| Inputs snapshot | 4–8 s | 5–10 s | 5–10 s | `TEAM_SEASON_AS_OF` 3.2 s + `GAME_AS_OF` 1.75 s per point on AWS, each read once or twice |
| Engine compute | ≈ 7 s | ≈ 1.6 s | ≈ 6–8 s | S3 compute-only; 1M ≈ 8.3 waves × 0.66 s + 0.8 s, plus the offsets and the leverage product |
| Playoff rows (12 per season) | ≈ 3.4 s | ≈ 0.7 s | ≈ 6.5 s | S3 emission rates |
| Tally rows (`L` ≈ batches × 100 teams × 400 games) | ≈ 2.5 s | ≈ 0.5 s | ≈ 2.5 s | 0.8M rows locally, 4.4M at 1M |
| Outcome strings | ≈ 3.8 s | ≈ 1.9 s | ≈ 7 s | S3 path emission; S4 A load 7.2 s at 1M |
| Split, truncate, finish | ≈ 1.5 s | ≈ 0.7 s | ≈ 3 s | S4: 12M sparse rows by `INSERT … SELECT` in 0.41 s on AWS |
| **Expected total** | **≈ 25–35 s** (laptop drift up to ≈ 50 s) | **≈ 12–20 s** | **≈ 30–45 s** | |
| **Target** | ≤ 60 s | ≤ 30 s | ≤ 60 s | mission: `REFRESH_WEEK` incl. 100k < 2 min locally |

What-if at 1M on AWS (layout A, sparse `SIM_TEAM`): one team ≈ 0.1–0.3 s, all teams ≈ 0.5–1.0 s at k = 1 (S4 C: 0.52 s p95, plus a scan of 1M strings); rooting guide lookup ≈ 0.1 s. Measured in 1.5; the trigger for the fallback is in [47].

**Exasol claims** (checked against the skills and the spikes by the heavy plan; the light design adds none that is unproven):

| Claim | Evidence |
|---|---|
| `PYTHON3 SET SCRIPT … EMITS (…)`, `ctx.get_dataframe(num_rows=…)`, `ctx.emit(DataFrame)`; `GROUP BY batch` fans out to `nrOfCores` per node; `exa.meta.vm_id` | S3 measured; `exasol-udfs` |
| `(VALUES BETWEEN 0 AND n) AS b("BATCH_ID")` as a cross-joined source | `exasol-grammar.md` `value_range_table`, `from_item`; n is written as a whole number |
| `CREATE TABLE IF NOT EXISTS`, `TRUNCATE TABLE` (DML), `MERGE` | `exasol-grammar.md` |
| No built-in normal CDF; `EXP`, `SQRT`; `CREATE FUNCTION … RETURN DOUBLE` | `exasol-grammar-functions.md`; Phase 3's `GREAT_CIRCLE_KM` |
| Lua `pquery`, `exit(rows, columns)`, nested `EXECUTE SCRIPT`, the lock recipe | Phase 2 probe 1.1, `loadlib.lua` |
| As-of views need literal point filters | Phase 3 measurement (coordinator, 2026-10-23) |

### Consequences

| Decision | Alternatives Considered | Rationale |
|----------|------------------------|-----------|
| Light, demo-first slices; heavy items superseded in `tasks.md` ([44]) | Implement the reviewed heavy plan | Owner's light process; the demo window is mid-November |
| Rating = prior SP+ blend with current Elo; one host fit, no phases, versions or gates ([45]) | Current-season SP+; the heavy fit with phases, holdout and gates; Elo only | Current SP+ is not visible at the demo point; Elo carries the in-season signal; the owner asked for "SP+-based"; one fit explains in one sentence |
| `TAU` fitted from lagged Elo, shared team offsets kept ([46]) | Independent per-game noise; an assumed `TAU` only | Season odds need the spread (review M1); the lag fit is cheap and honest |
| Layout A: one outcome string per season in a fixed table ([47]) | D20's layout C with per-run DDL and generated views | No DDL per run, no derivation step, no generated serving views; S4 measured A level with C at 100k and the 1M cost in `SIM_TEAM` |
| `LEVERAGE` from engine tallies ([48]); batch size ≥ 5,000 | SQL build from the stored outcomes (S4: 4.75 s with C) | One matrix product per batch; layout-independent; tally rows grow with the batch count |
| Lua `SIMULATE` from slice 1, timings on `SIM_RUN`, no step or batch tables ([49]) | Host orchestration first; the heavy registry | Step 300 becomes a thin wrapper; the demo beat runs in-database |
| 2026 rules per [9] in one `SEASON_RULES` row with switches; `SELECTION_AT` instead of a selection-week view; simulated title games only ([50]) | The brief's "five best champions, top-four champions get byes"; 2024/2025 rule sets; known pairings | [9] is the researched 2026 rule; one value flips the bid rule; nothing in the demo window needs known pairings |
| Predictions, grades and movement live in `CFB_SIM` ([51]) | Phase 5's `CFB_PRED` with publications | One phase owns the numbers it computes; Phase 5 becomes thin views |
| Tests on Phase 3's league and crafted models ([52]) | A dedicated sim league; a loop-reference oracle | 30 scenarios cover the demo path; selection is pure numpy on crafted models |
| Deploy through a generalized `sqlobjects` with a keep list ([53]) | A new deployer; the heavy data-table kind with column migration | Small change to proven code |
| No partitioning, no run drop ([54]) | `PARTITION BY RUN_ID`, `make sim-drop` | A season stores few runs; a trigger and a fallback task exist |
| BucketFS parameter file optional ([55]) | Make it the source of the constants | The seed stays the single source; the file is a showcase |
| `GAME_WIN_PROB` with fitted Elo constants stays here; Phase 5 ranks upsets ([56]) | Move it to Phase 5; the heavy Elo fit | Phase 5's upset view already reads it |
| Sentinels in the stage frame ([57]) | NULLs in emitted DataFrames | The NULL path of DataFrame emission is unprobed; sentinels need no probe |

## Features

| Feature | Status | Spec |
|---------|--------|------|
| sim-engine/score-model | NEW | `sim-engine/score-model/spec.md` |
| sim-engine/engine-contract | NEW | `sim-engine/engine-contract/spec.md` |
| sim-engine/selection-rules | NEW | `sim-engine/selection-rules/spec.md` |
| sim-engine/sim-runs | NEW | `sim-engine/sim-runs/spec.md` |
| sim-engine/predictions | NEW | `sim-engine/predictions/spec.md` |
| sim-engine/refresh-step | NEW | `sim-engine/refresh-step/spec.md` |
| sim-engine/deploy | NEW | `sim-engine/deploy/spec.md` |

The heavy plan's `sim-engine/calibration`, `sim-engine/win-probability` and `sim-engine/backtest` deltas are removed: the fit moved into `score-model`, the pregame probability into `predictions`, and the backtest is superseded. No Phase 2 or Phase 3 spec changes; recorder note: `loader/load-plan`'s registered steps gain 200 `PREDICT` and 300 `SIMULATE` (slice 3).

## Requirements

| Requirement | Details |
|-------------|---------|
| Zero CFBD calls | No `CFB_SIM` object or Lua script reads `CFBD_VS` (lint). `make quota TARGET=aws` before and after every live task: used calls unchanged |
| Shared databases | Local DB work only in "local free" windows, `make test-it` under `flock <scratchpad>/cfb-it.lock`. AWS `default`: deploy and run only, never stop, start or resize |
| Layering | `CFB_SIM` reads only `CFB_MODEL` and itself; the Lua scripts also `CFB_META` (lock) and `CFB_INFRA` (engine). No grants (Phase 5) |
| Leak-free | Inputs only from as-of views at a literal point plus the previous season's SP+; labels only from completed games |
| Reproducibility | Per run: seed, batch size, sims, input snapshot, model digest, engine, numpy and Python versions |
| Runtime | Builtin `PYTHON3` SLC (Python 3.12.3, numpy 1.26.4; pandas inside the UDF only), Exasol Lua and SQL; host Python 3.12 with numpy 1.26.4 |
| Self-description | Every `CFB_SIM` object and column commented, grain first |
| Performance | AWS 1M ≤ 60 s, AWS 100k ≤ 30 s, local 100k ≤ 60 s; one-team what-if at 1M on AWS ≤ 1 s. Measured once and recorded; a miss is logged with its step seconds, not a gate |
| Data in git | Seeds hold constants and published rules only; docs hold derived numbers |

## Dependencies

- **G0 (met):** Phase 3's 3.9–3.11 (as-of views, coverage, checks) merged into this branch (42b2121).
- **Phase 2 (met):** run lock kinds `SIMULATE` and `DEPLOY`, `acquire_lock`/`release_lock`, `loader/runlock.py`, the Lua bundler, `REFRESH_WEEK` registered steps, the IT harness. Hand-off H1 is fulfilled.
- **Phase 3 (shared files):** `vs/host/sqlobjects.py` (extended by 1.2, additive), `loader/load-plan.yaml` `refresh_steps` (Phase 3's 4.1 adds 100 `MODEL`, task 3.1 adds 200 and 300; merge by appending). Phase 3's 4.3 may materialize `GAME`; nothing here depends on it.
- **Main tree (read only):** `../exasol-cfb-demo/spikes/s3/` (`engine_proto.py`, `engine_loop_ref.py`, `engine_udf.py`, `synth_season.py`, `tests/test_engine_proto.py`) and `spikes/s4/` (`queries.py`); untracked, another session's: read them, adapt code into `engine/`, never `git add` anything under `spikes/`.
- **Tools:** `numpy==1.26.4` joins the dev group (`uv lock`).
- **Owner:** none for slices 1–2; the cfp.org check (3.3) before the first committee ranking (about 2026-11-03).

## Cross-plan hand-offs

**Phase 5 (`CFB_API`, thin views; `CFB_APP`/`CFB_MCP` read only `CFB_API`).** Phase 4 now owns the frozen predictions, grades, odds movement and the shown run, so Phase 5's light replan can drop `CFB_PRED.PREDICTION`, `PUBLICATION`, `PUBLISHED_ODDS`, steps 200 `PREDICT` and 400 `PUBLISH`, `WHATIF_PUBLICATION`, the three generated what-if views with `analib.publish`, `whatif_views.lua` and `analyses/whatif_ddl.py`, and the review items P5-M3 (apostrophes in generated DDL: none is generated) and P5-m13 (no run is ever dropped). It keeps upset ranking, head-to-head, `CFB_API_LIVE`, the grants and the decision on a conditional rooting guide.

| `CFB_API` view (Phase 5 name) | Reads | Columns Phase 4 provides |
|---|---|---|
| `PLAYOFF_ODDS` | `CFB_SIM.ODDS_MOVEMENT` | `AS_OF_SEASON`, `AS_OF_WEEK_SEQ`, `AS_OF_LABEL`, `RUN_ID`, `TEAM_ID`, `SCHOOL`, `CONFERENCE`, `TIER`, `ODDS_RANK`, `P_IN_CFP`, `P_BYE`, `P_QUARTERFINAL`, `P_SEMIFINAL`, `P_FINAL`, `P_CHAMPION`, `P_CONF_CHAMPION`, `EXPECTED_WINS`, `MEAN_SEED`, `PREV_AS_OF_LABEL`, `PREV_P_IN_CFP`, `DELTA_P_IN_CFP`, `DELTA_P_CHAMPION`, `RANK_CHANGE`, `MOVE` |
| `PUBLISHED_RUNS` | `CFB_SIM.POINT_RUN` ⋈ `SIM_RUN` | `AS_OF_SEASON`, `AS_OF_WEEK_SEQ`, `AS_OF_LABEL`, `RUN_ID`, `PREV_RUN_ID`, `RUN_ORIGIN`, `N_SIMS`, `SECONDS_TOTAL`, `SIMS_PER_SECOND`, `INSTANCES`, `NODES`, `ENDED_AT` (Phase 6's `publication_id` is the `RUN_ID`) |
| what-if run (in `CURRENT_POINT` or its own view) | `CFB_SIM.CURRENT_RUN` | `RUN_ID`, `AS_OF_SEASON`, `AS_OF_WEEK_SEQ`, `AS_OF_LABEL`, `N_SIMS`, `NEXT_WEEK_SEQ` |
| `WHATIF_GAMES` | `CFB_SIM.SIM_RUN_GAME` (`GAME_ROLE` `REMAINING`) of `CURRENT_RUN` | `RUN_ID`, `GAME_ID`, `PATH_POS`, `WEEK_SEQ`, `IS_NEXT_WEEK`, `START_AT`, `HOME_TEAM_ID`, `AWAY_TEAM_ID`, `HOME_WIN_PROB`, `N_HOME_WINS` |
| `WHATIF_OUTCOMES` (static view) | `CFB_SIM.SIM_PATH` | `RUN_ID`, `SIM_ID`, `OUTCOMES` |
| `WHATIF_PLAYOFF` (static view) | `CFB_SIM.SIM_TEAM` | `RUN_ID`, `SIM_ID`, `TEAM_ID`, `SEED`, `BYE`, `REACHED_ROUND`, `CHAMPION` |
| roster for zero rows | `CFB_SIM.SIM_RUN_TEAM` | `RUN_ID`, `TEAM_ID`, `SCHOOL`, `CONFERENCE`, `TIER`, `IS_ELIGIBLE` |
| `ROOTING_GUIDE` | `CFB_SIM.ROOTING_GUIDE` | `RUN_ID`, `TEAM_ID`, `GAME_ID`, `SEASON`, `WEEK_SEQ`, `IS_NEXT_WEEK`, `HOME_TEAM_ID`, `HOME_SCHOOL`, `AWAY_TEAM_ID`, `AWAY_SCHOOL`, `ROOT_FOR_TEAM_ID`, `ROOT_FOR_SCHOOL`, `P_IF_HOME_WIN`, `P_IF_AWAY_WIN`, `LEVERAGE`, `GUIDE_RANK`, `NEXT_WEEK_RANK`, `LOW_SAMPLE` |
| `PREDICTIONS` | `CFB_SIM.PREDICTION_GRADED` | the `PREDICTION` columns plus `HOME_POINTS`, `AWAY_POINTS`, `IS_COMPLETED`, `PLAYED_WEEK_SEQ`, `IS_RESCHEDULED`, `GRADE`, `BRIER`, `MARGIN_ERROR`, `LINE_HOME_WIN_PROB`, `LINE_PICK_CORRECT`, `LINE_BRIER` |
| current predictions (team page "this week" before a freeze) | `CFB_SIM.GAME_SCORE_AS_OF` | filtered with the literal current point read first from `CURRENT_POINT`, never joined |
| `SCORECARD_WEEKS`, `SCORECARD_SEASONS` | `CFB_SIM.PREDICTION_SCORECARD` (`WEEK_SEQ` NULL = season to date) | `SEASON`, `WEEK_SEQ`, `POPULATION`, `FREEZE_KIND`, `IS_HEADLINE`, `GAMES_GRADED`, `CORRECT`, `WRONG`, `TIES`, `RECORD_LABEL`, `PICK_ACCURACY`, `BRIER`, `MAE_MARGIN`, `LINE_GAMES`, `LINE_CORRECT`, `LINE_WRONG`, `LINE_BRIER`, `MODEL_BRIER_ON_LINE_GAMES` |
| `SCORECARD_CALIBRATION` | `CFB_SIM.PREDICTION_CALIBRATION` | `SEASON`, `POPULATION`, `FREEZE_KIND`, `BUCKET`, `GAMES_GRADED`, `MEAN_PICK_WIN_PROB`, `PICK_WIN_SHARE` |
| `UPSETS` (Phase 5 ranks) | `CFB_SIM.GAME_WIN_PROB` (+ `PREDICTION` for our own probability) | `GAME_ID`, `SEASON`, `SEASON_TYPE`, `WEEK`, `WEEK_SEQ`, `HOME_TEAM_ID`, `AWAY_TEAM_ID`, `WINNER_TEAM_ID`, `LOSER_TEAM_ID`, `PROB_SOURCE`, `HOME_PREGAME_WIN_PROB`, `WINNER_PREGAME_WIN_PROB`, `UPSET_MAGNITUDE` |

What-if SQL: reuse `engine/whatif.py` (pure, stdlib) with `schema="CFB_API"`, `outcomes="WHATIF_OUTCOMES"`, `playoff="WHATIF_PLAYOFF"`, `roster=…`; a condition is a game's `PATH_POS` from `WHATIF_GAMES` and `'1'` (home wins) or `'0'`.

**Phase 6 (9.8 `GET /api/team/{id}/outlook`, through Phase 5's views):**
- `run_id` = `CURRENT_RUN.RUN_ID`; without a row every panel is `available: false`, reason `no_run`.
- `odds.trajectory`: the team's `PLAYOFF_ODDS` rows of the season by `AS_OF_WEEK_SEQ` → `{season: AS_OF_SEASON, week_seq: AS_OF_WEEK_SEQ, label: AS_OF_LABEL, p_in_cfp, p_bye, p_champion}`; `odds.current` is the current point's row; `move` and `delta_p_in_cfp` are its `MOVE` and `DELTA_P_IN_CFP`.
- `this_week.game`: the team's next game from `PREDICTIONS` (`prediction_kind` `FROZEN`), else from `GAME_SCORE_AS_OF` at the literal current point (`CURRENT`); `team_win_prob` is `HOME_WIN_PROB` or 1 − it, `pred_team_points`/`pred_opponent_points` from `HOME_POINTS_PRED`/`AWAY_POINTS_PRED` by side, `spread` from the game's stored line.
- `watch.guide`: `ROOTING_GUIDE` of the run for the team: `top` = `GUIDE_RANK` ≤ 5, `coming_week` = `IS_NEXT_WEEK` rows by `NEXT_WEEK_RANK`, `conditional` FALSE.
- League week `stakes` = the largest `LEVERAGE` of a game over all teams in `ROOTING_GUIDE` of `CURRENT_RUN`.
- Field mapping: `OddsRow`, `GuideRow`, `ScorecardRow` (`week` = rows with a `WEEK_SEQ`, `season` = the row with `WEEK_SEQ` NULL) and `CalibrationBucket` fields are the lower-cased column names above. `PredictionRow`: `pred_home_points`/`pred_away_points` = `HOME_POINTS_PRED`/`AWAY_POINTS_PRED`, the school names from `CFB_API.TEAMS`, `grade` never `NO_PICK` (every prediction has a pick). `RunBanner.published_at` = `ENDED_AT`. `WhatIfOddsRow.base_*` from `PLAYOFF_ODDS` of the same run; `low_sample` against Phase 5's threshold. `app-fixtures` may read `CFB_SIM` directly as the owner for the preview snapshot.

**Phase 8:** the weekly cadence becomes "after week N is done: `REFRESH_WEEK` (steps 200 and 300 from slice 3), or by hand `make predict` then `make simulate`, before the first kickoff of week N + 1 (week 9: Tuesday 2026-10-27 23:00 UTC)". The demo's scale dial is a 1M run on AWS.

## Migration

No data migration: `CFB_SIM` is new.

| Current | New |
|---------|-----|
| `vs/host/namespace.py`, `metaddl.py` | + `sim_schema`, `{SIM_SCHEMA}` |
| `vs/host/sqlobjects.py` | lint takes the schema placeholder; `deploy(…, keep=…)` names tables never dropped as orphans |
| `tests/it/harness.py` | `deploy_sim`, `set_sim_params`; `drop_namespace`/`leftovers` cover `{prefix}CFB_SIM` |
| `pyproject.toml` | dev `numpy==1.26.4`; mypy `files` + `engine` |
| `Makefile` | `sim`, `sim-fit`, `simulate`, `what-if` (slice 1), `predict` (slice 2) |
| `loader/load-plan.yaml` `refresh_steps` | + 200 `PREDICT`, 300 `SIMULATE` (slice 3) |
| `make deploy` | VS → loader → model → sim (slice 3) |

## Implementation Tasks

Light process: tests first for each task, a few per feature on the demo path (the scenario table below); unit tests for pure Python, integration tests for SQL and Lua. Every DB task holds the IT lock locally and makes zero CFBD calls.

### Slice 1: 2026 odds and what-if on AWS (first usable result)

- [ ] 1.1 Engine core `engine/core.py` [expert]
  - **Start** from a copy of `../exasol-cfb-demo/spikes/s3/engine_proto.py` (read from the main tree), then change it per `sim-engine/engine-contract` and `selection-rules`: two child streams with fixed shapes and the `(S, T)` team offsets; margins with `TEAM_SIGMA` and `game_sigma`, postseason with `post_team_sigma` (no `post_sigma`); `NON_FBS` teams, `IS_ELIGIBLE`, `HAS_CCG`, `min_conference_teams`; the bid rules (`auto_bid_rule`, `g6_bid`, guarantee) and straight seeding; a pair that met twice decides head-to-head by the later game; the tagged stage frame with sentinels; `L` counts as one float32 product over the teams in the field; the validations; G = 0. Keep the vectorized style and the prototype's tiebreakers, resume and bracket.
  - **Files:** `engine/__init__.py`, `engine/core.py`, `tests/engine/__init__.py`, `tests/engine/models.py` (crafted models, ideas from `spikes/s3/synth_season.py`), `tests/unit/test_engine_core.py`, `tests/unit/test_selection_rules.py`, `pyproject.toml`, `uv.lock`.
  - **Tests:** engine-contract 1–5 and selection-rules 1–5 (unit; S3's invariant test ported as engine-contract 3).
  - **Done:** the 10 tests pass; a 10,000-season batch of a 136-team, 400-game crafted model runs in under 3 s on the host; `make lint` clean.
- [ ] 1.2 Simulation schema, deploy and the engine script
  - **Files:** `vs/host/namespace.py`, `vs/host/metaddl.py`, `vs/host/sqlobjects.py` (schema placeholder parameter, `keep`), `engine/udf.py` (chunked `get_dataframe(num_rows=4096)`, parse, `simulate_batch`, one DataFrame emit, `INFO` from `exa.meta`), `engine/bundle.py` (`CREATE OR REPLACE PYTHON3 SET SCRIPT {INFRA_SCHEMA}.SIM_ENGINE(…) EMITS (<SIM_STAGE columns>) AS …`, `ENGINE_VERSION`, fails at 1,990,000 characters), `engine/deploy.py` (run lock `DEPLOY` via `loader/runlock.py`, `sqlobjects.deploy` with the data tables as `extra_statements` and in `keep`, the engine script), `scripts/sim.py` (`deploy`), `Makefile` (`sim`), `sql/sim/010_sim_params.sql` (`UNFITTED` placeholders), `011_season_rules.sql`, `012_score_params.sql`, `sql/sim/data/*.sql` (the ten slice-1 tables), `tests/it/harness.py`, `tests/it/test_sim_deploy_it.py`, `tests/unit/test_engine_bundle.py`, `tests/unit/test_namespace.py`, `tests/unit/test_sqlobjects.py`.
  - **Tests:** deploy 1–2 (IT; for 2, insert the rows directly); unit: the bundle's `EMITS` list equals the `SIM_STAGE` DDL columns in order, `ENGINE_VERSION` is stable, the keep list survives the orphan drop.
  - **Done:** `make test` green, the two ITs green, `make sim` on the local real namespace (local free window) twice: the second run drops nothing.
- [ ] 1.3 Score model, engine inputs and the fit
  - **Files:** `sql/sim/020_normal_cdf.sql`, `100_team_rating_as_of.sql`, `110_game_score_as_of.sql`, `200_engine_team_as_of.sql`, `210_engine_game_as_of.sql` (per § Engine inputs), `engine/fit.py`, `scripts/sim.py` (`fit`), `Makefile` (`sim-fit`), `sql/sim/010_sim_params.sql` (regenerated), `tests/unit/test_fit.py`, `tests/it/test_score_model_it.py`, `tests/it/harness.py` (`set_sim_params`).
  - **Tests:** score-model 1–3 (IT), 4 (unit).
  - **Done:** tests green; `make sim-fit TARGET=aws` run once (read-only, 0 calls), the generated seed reviewed and committed with the fit's printout in the commit message; on AWS `SELECT … FROM CFB_SIM.GAME_SCORE_AS_OF WHERE AS_OF_SEASON = 2026 AND AS_OF_WEEK_SEQ = 7 ORDER BY START_AT` returns the week-8 to week-13 games with |`MU`| < 50 and probabilities strictly between 0 and 1, in seconds, not tens of seconds.
- [ ] 1.4 `SIMULATE`, outputs, odds and what-if
  - **Files:** `engine/lua/simlib.lua`, `engine/lua/simulate.lua`, `engine/deploy.py` (bundle and install the script), `sql/sim/230_sim_run_model_rows.sql`, `300_sim_team_odds.sql`, `engine/whatif.py`, `scripts/sim.py` (`simulate`, `what-if`), `Makefile` (`simulate`, `what-if`), `tests/it/test_sim_runs_it.py`, `tests/it/test_sim_engine_it.py`, `tests/unit/test_whatif.py`.
  - **Rules:** Phase 2's Lua recipe (`pquery` everywhere, `ROLLBACK` before logging a failure, `R0003` = cannot log); `acquire_lock('SIMULATE', …)` and `release_lock`; resolve the point from `AS_OF_POINT` first, then literal filters; whole numbers checked before they enter the text; the `B` rows parsed in Lua. `make what-if` resolves `WIN=next` to the team's first remaining game of the run.
  - **Tests:** sim-runs 1, 2, 3, 6, 7 and engine-contract 6 (IT); unit: `whatif.py` validation (unknown position, 17 conditions, a `bool` id) and its SQL shape for both queries and custom names.
  - **Done:** tests green; locally (local free window) `make simulate SEASON=2026 WEEK=7 SIMS=100000 BATCH=5000` prints the top 25 and the seconds; `make what-if RUN_ID=<id> TEAM=Auburn WIN=next` prints before and after.
- [ ] 1.5 Live on AWS: the first usable result
  - **Steps:** `make quota TARGET=aws`; `make sim TARGET=aws`; `make simulate TARGET=aws SEASON=2026 WEEK=7 SIMS=100000 BATCH=5000`, then `SIMS=1000000 BATCH=10000`; `make what-if TARGET=aws RUN_ID=<1M run> TEAM=Auburn WIN=next`, and `LOSE=next`; time the what-if SQL for one team and for all teams at k = 1 and k = 3, 20 runs each with the query cache off, p95; compare the top 12 with the AP top 25 at the point; `make quota TARGET=aws` again.
  - **Files:** `docs/engine-verification.md` (derived numbers only: seconds per step, sizing, sims per second, what-if p95s, Auburn's odds before, after a win and after a loss); `decision-log.md` (the [47] check outcome).
  - **Done:** the numbers are recorded and the orchestrator can tell the owner: "Auburn: X % to make the CFP now, Y % if they win this weekend, Z % if they lose (1,000,000 seasons in N s on AWS)". Quota unchanged.

### Slice 2: predictions, the rooting guide and movement

- [ ] 2.1 Predictions: freeze, grades, scorecard, calibration, pregame probability
  - **Files:** `sql/sim/data/prediction.sql`, `sql/sim/400_prediction_graded.sql`, `410_prediction_scorecard.sql`, `420_prediction_calibration.sql`, `430_game_win_prob.sql`, `engine/lua/simlib.lua` (freeze: points oldest first, literal filters, skip points without unfrozen games), `engine/lua/predict.lua`, `engine/lua/simulate.lua` (step 2), `engine/deploy.py`, `scripts/sim.py` (`predict`), `Makefile` (`predict`), `tests/it/test_predictions_it.py`.
  - **Tests:** predictions 1–4 (IT).
  - **Done:** tests green; `make predict SEASON=2026 WEEK=7` locally.
- [ ] 2.2 Rooting guide, shown runs and movement
  - **Files:** `sql/sim/310_point_run.sql`, `315_current_run.sql`, `320_odds_movement.sql`, `330_rooting_guide.sql`, `tests/it/test_sim_runs_it.py` (two more tests).
  - **Tests:** sim-runs 4 and 5 (IT).
  - **Done:** tests green.
- [ ] 2.3 Live slice 2 on AWS and the hand-off check
  - **Steps:** `make sim TARGET=aws`; `make predict TARGET=aws SEASON=2026 WEEK=7` (reconstructs weeks 1–8; record the seconds); `make simulate TARGET=aws SEASON=2026 WEEK=6 SIMS=1000000 BATCH=10000`, so week 7 has movement; for Auburn read `ODDS_MOVEMENT`, the top 5 and next-week rows of `ROOTING_GUIDE`, the week-8 `PREDICTION_GRADED` rows once played, and the season row of `PREDICTION_SCORECARD`; compare the columns of every hand-off object (`EXA_ALL_COLUMNS`) with § Cross-plan hand-offs; quota before and after.
  - **Files:** `docs/engine-verification.md` (append).
  - **Done:** the hand-off columns match, and the orchestrator can relay the contract to Phases 5 and 6. Weekly from here, by hand until 3.1: after week N is done and refreshed, `make predict` and `make simulate` for week N before week N + 1's first kickoff.

### Slice 3: hardening, only as far as the demo needs

- [ ] 3.1 Refresh steps, per-target defaults and killed-run cleanup
  - **Files:** `engine/lua/refresh_predict.lua`, `engine/lua/refresh_simulate.lua`, `engine/lua/simlib.lua` (cleanup of other `RUNNING` runs after the lock and at the start of `REFRESH_SIMULATE`), `engine/sim-config.yaml`, `engine/deploy.py` (`REFRESH_*` rows per target), `loader/load-plan.yaml` (append 200 and 300), `scripts/deploy.py` (sim after the model), `tests/it/test_sim_refresh_step_it.py`.
  - **Tests:** refresh-step 1–2 (IT).
  - **Done:** tests green; a local replay `make refresh` (local free window, 0 calls) shows `PREDICT` and `SIMULATE`; `make deploy TARGET=aws` registers both steps. The live weekly refresh stays the orchestrator's cadence.
- [ ] 3.2 Calibration report
  - **Files:** `engine/fit.py` (`--report`: refit on 2014–2023 and score 2024–2025 one week ahead: MAE and Brier next to the stored line, bias, ten probability buckets with a 3-SE tolerance, the lag table behind `TAU`), `scripts/sim.py`, `tests/unit/test_fit.py` (metric functions on hand-computed arrays), `docs/engine-verification.md` (section, with the 2026 scorecard and calibration rows).
  - **Done:** the report is written. A bucket outside 3 SE, |bias| above 1.5 points or a Brier score more than 0.03 above the line's is logged as a decision (refit, raise `TAU`, or accept), never a gate.
- [ ] 3.3 CFP rules check on cfp.org, before the first committee ranking (about 2026-11-03), zero CFBD calls: the bid rule, the `G6` bid, the Notre Dame guarantee and selection day. Record the source in `SEASON_RULES.RULE_NOTE`; a different reading changes one value (logged).
- [ ] 3.4 Docs (re-read each file first; other agents edit them): `docs/user-guide.md` § "Score model and simulations" (the model in one paragraph, the rules and their simplifications, `make sim`/`sim-fit`/`simulate`/`what-if`/`predict`, reading the odds, the guide and the scorecard, zero calls); `CLAUDE.md` dense bullets (`engine/` and `sim-engine/`, numpy pin, literal point filters, layout A, batch sizes ≥ 5,000); `specs/architecture.md` § Engine contract (layout A, tallies, parameters, 2026 rules); `docs/roadmap.md` (Phase 4 progress; ADR numbers for the decisions marked `Promotes to ADR: yes`; D20 amended by [47]/[48]).
- [ ] 3.5 (optional) BucketFS parameter file, a showcase beat only
  - `make sim-fit` also writes `build/sim/sim-params.json`; `make sim-bfs [TARGET=…]` uploads it to BucketFS (`exapump bucketfs`, the `bfs_*` profile, credentials read by the tooling, never printed); `SIM_ENGINE` reads `/buckets/bfsdefault/default/cfb/sim-params.json` when present and records the file's digest in `INFO` and `SIM_RUN.DETAIL` (`params=bucketfs:<digest>`); the snapshot's `PARAM` rows stay the source.
  - **Cost:** about half a day: one host command, about 20 lines in the UDF, one IT. **Risks:** BucketFS write credentials and port reachability on the AWS deployment are unverified; the file is a second copy of the constants. Never a dependency.
- [ ] 3.6 (only if triggered by 1.5 or 2.3) What-if storage fallback per [47] and [54]: `ALTER TABLE … PARTITION BY RUN_ID` on `SIM_TEAM` and `SIM_PATH` if latency grows with stored runs; layout C only if the one-team what-if misses 1 s p95 at 1M on AWS.

## Parallelization

| Parallel Group | Tasks |
|----------------|-------|
| S1-A | 1.1, 1.2 (and the pure part of 1.3: `engine/fit.py` with its unit test) |
| S1-B | 1.3 |
| S1-C | 1.4 |
| S1-D | 1.5 |
| S2-A | 2.1, 2.2 |
| S2-B | 2.3 |
| S3-A | 3.1, 3.2, 3.3, 3.5 (optional), 3.6 (only if triggered) |
| S3-B | 3.4 |

Sequential dependencies:
- S1-A → S1-B → S1-C → S1-D: 1.3 needs 1.2's deploy; 1.4 needs 1.1, 1.2 and 1.3.
- S1-D → S2-A → S2-B; 2.1 and 2.2 touch disjoint files except `tests/it/test_sim_runs_it.py` (2.2 only) and `simlib.lua`/`simulate.lua` (2.1 only).
- S2-A → S3-A (3.1 and 3.2 need 2.1); 3.3 has only its date; S3-A → S3-B.
- Local DB work in "local free" windows; AWS live tasks (1.5, 2.3) need no window.

## Dead Code Removal

| Type | Location | Reason |
|------|----------|--------|
| Spec deltas | `sim-engine/calibration/`, `sim-engine/win-probability/`, `sim-engine/backtest/` (this plan) | Superseded by the light replan; content moved into `score-model` and `predictions`, or dropped |
| — | code | This plan adds new code only. `spikes/` stays untouched in the main tree as the evidence of D19/D20 |

## Verification

### Scenario Coverage

| Scenario | Test Type | Test Location | Test Name |
|----------|-----------|---------------|-----------|
| score-model: A team's rating blends its prior SP+ and its current Elo | Integration | `tests/it/test_score_model_it.py` | `test_rating_blends_prior_sp_and_elo` |
| score-model: Home field counts only off neutral sites | Integration | `tests/it/test_score_model_it.py` | `test_home_field_only_off_neutral_sites` |
| score-model: Uncertainty grows with the horizon, and the win probability follows the margin | Integration | `tests/it/test_score_model_it.py` | `test_sigma_by_horizon_and_win_probability` |
| score-model: The fit recovers known constants | Unit | `tests/unit/test_fit.py` | `test_fit_recovers_known_constants` |
| engine-contract: A batch is determined by its seed, its id and its size | Unit | `tests/unit/test_engine_core.py` | `test_batch_determined_by_seed_id_and_size` |
| engine-contract: Shared team strength widens win totals but keeps each game's probability | Unit | `tests/unit/test_engine_core.py` | `test_team_offsets_widen_win_totals` |
| engine-contract: Every simulated season produces a complete playoff | Unit | `tests/unit/test_engine_core.py` | `test_every_season_complete_playoff` |
| engine-contract: The tallies agree with the simulated seasons | Unit | `tests/unit/test_engine_core.py` | `test_tallies_agree_with_seasons` |
| engine-contract: An invalid model is rejected, not repaired | Unit | `tests/unit/test_engine_core.py` | `test_invalid_model_rejected` |
| engine-contract: The database computes what the host computes | Integration | `tests/it/test_sim_engine_it.py` | `test_database_equals_host_engine` |
| selection-rules: The four power champions and the best Group of Six team get in, even outside the top 12 | Unit | `tests/unit/test_selection_rules.py` | `test_power_champions_and_best_g6_team` |
| selection-rules: The guaranteed school gets in within the top 12, and only there | Unit | `tests/unit/test_selection_rules.py` | `test_guarantee_within_top12` |
| selection-rules: Seeding is straight by rank, and the top four get byes | Unit | `tests/unit/test_selection_rules.py` | `test_straight_seeding_and_byes` |
| selection-rules: With the five-best-champions rule a Group of Six champion can take a power champion's place | Unit | `tests/unit/test_selection_rules.py` | `test_top_champions_rule` |
| selection-rules: Small conferences crown no champion, and conferences without a title game crown their leader | Unit | `tests/unit/test_selection_rules.py` | `test_small_and_no_ccg_conferences` |
| sim-runs: One call simulates the rest of the season and registers the run | Integration | `tests/it/test_sim_runs_it.py` | `test_simulate_registers_run` |
| sim-runs: Only the playoff teams and one outcome string per season are stored | Integration | `tests/it/test_sim_runs_it.py` | `test_sparse_playoff_rows_and_outcome_strings` |
| sim-runs: A what-if filters the stored seasons | Integration | `tests/it/test_sim_runs_it.py` | `test_what_if_filters_stored_seasons` |
| sim-runs: The rooting guide's counts are precomputed | Integration | `tests/it/test_sim_runs_it.py` | `test_leverage_equals_recount` |
| sim-runs: Odds move between points | Integration | `tests/it/test_sim_runs_it.py` | `test_odds_movement_between_points` |
| sim-runs: A failed run leaves no outputs and frees the lock | Integration | `tests/it/test_sim_runs_it.py` | `test_failed_run_leaves_no_outputs` |
| sim-runs: Runs outside the rules or under another lock are refused | Integration | `tests/it/test_sim_runs_it.py` | `test_refusals` |
| predictions: Next week's games are frozen once, before kickoff | Integration | `tests/it/test_predictions_it.py` | `test_freeze_once_before_kickoff` |
| predictions: Missed weeks are reconstructed at their own point and flagged | Integration | `tests/it/test_predictions_it.py` | `test_reconstructed_weeks_flagged` |
| predictions: Graded predictions add up to the scorecard | Integration | `tests/it/test_predictions_it.py` | `test_grades_scorecard_calibration` |
| predictions: A game's pregame probability comes from its line, else from Elo | Integration | `tests/it/test_predictions_it.py` | `test_game_win_prob_line_else_elo` |
| refresh-step: The weekly refresh freezes predictions and simulates the season | Integration | `tests/it/test_sim_refresh_step_it.py` | `test_refresh_predicts_and_simulates` |
| refresh-step: A killed run is cleaned up by the next run | Integration | `tests/it/test_sim_refresh_step_it.py` | `test_killed_run_cleaned_up` |
| deploy: One command installs the simulation schema | Integration | `tests/it/test_sim_deploy_it.py` | `test_make_sim_installs_schema` |
| deploy: A redeploy keeps every run and prediction | Integration | `tests/it/test_sim_deploy_it.py` | `test_redeploy_keeps_runs_and_predictions` |

Supporting unit tests: `tests/unit/test_engine_bundle.py` (`EMITS` = stage columns, `ENGINE_VERSION`), `tests/unit/test_whatif.py` (validation and SQL shape), `tests/unit/test_sqlobjects.py` (keep list, schema placeholder), `tests/unit/test_namespace.py`.

### Manual Testing

All commands from the repo root; local ones in a "local free" window; none spends a CFBD call.

| Feature | Command | Expected Output |
|---------|---------|-----------------|
| sim-engine/deploy | `make sim`, again; `make sim TARGET=aws` | Report: reference tables, function, views, data tables, scripts, comments 100 %; the second run drops nothing |
| sim-engine/score-model | `make sim-fit TARGET=aws`; then `SELECT G.HOME_TEAM, G.AWAY_TEAM, S.MU, S.SIGMA, S.HOME_POINTS_PRED, S.AWAY_POINTS_PRED, S.HOME_WIN_PROB FROM CFB_SIM.GAME_SCORE_AS_OF S JOIN CFB_MODEL.GAME G ON G.GAME_ID = S.GAME_ID WHERE S.AS_OF_SEASON = 2026 AND S.AS_OF_WEEK_SEQ = 7 AND S.HORIZON_WEEKS = 1 ORDER BY S.HOME_WIN_PROB DESC LIMIT 10` | The fit's constants and game counts; `git diff sql/sim/010_sim_params.sql` shows them; week 8's most lopsided games with plausible margins and points |
| sim-engine/engine-contract | `uv run pytest tests/unit/test_engine_core.py -q` | All pass in seconds |
| sim-engine/selection-rules | `SELECT * FROM CFB_SIM.SEASON_RULES` | One 2026 row as in the spec, with its source note |
| sim-engine/sim-runs | `make simulate TARGET=aws SEASON=2026 WEEK=7 SIMS=1000000 BATCH=10000`; `make what-if TARGET=aws RUN_ID=<id> TEAM=Auburn WIN=next` | Result row `DONE`, 1,000,000 seasons, seconds ≤ 60, `INSTANCES` 12; top 25 odds; Auburn's P(CFP), P(bye), P(champion) before and after, with the matching-season count |
| sim-engine/sim-runs (movement, guide) | `SELECT TEAM_ID, P_IN_CFP, PREV_P_IN_CFP, MOVE FROM CFB_SIM.ODDS_MOVEMENT WHERE AS_OF_SEASON = 2026 AND AS_OF_WEEK_SEQ = 7 ORDER BY ODDS_RANK LIMIT 15`; `SELECT * FROM CFB_SIM.ROOTING_GUIDE WHERE RUN_ID = <id> AND TEAM_ID = <Auburn> ORDER BY GUIDE_RANK LIMIT 5` | Arrows against week 6; Auburn's five games to watch with whom to root for |
| sim-engine/predictions | `make predict TARGET=aws SEASON=2026 WEEK=7`; `SELECT * FROM CFB_SIM.PREDICTION_SCORECARD WHERE SEASON = 2026 AND WEEK_SEQ IS NULL` | Weeks 1–8 frozen (reconstructed, plus week-8 games not yet kicked off as in advance); the season record and Brier next to the line's |
| sim-engine/refresh-step | `make refresh SEASON=2026 WEEK=<current>` in replay on local | Timeline with `PREDICT` and `SIMULATE` `DONE`, 0 calls; a `REFRESH` run in `SIM_RUN` |

### Checklist

| Step | Command | Expected |
|------|---------|----------|
| Generate | `make generate && git diff --exit-code -- vs/catalog sql/core` | Exit 0 |
| Build | `make bundle && uv run python -m engine.bundle` | Exit 0; the engine script below 1,990,000 characters |
| Test | `make test` | 0 failures (unit + CLI, no DB) |
| Integration | `flock <scratchpad>/cfb-it.lock make test-it` | 0 failures; mock and replay only; 0 requests to `api.collegefootballdata.com` |
| Lint | `make lint` | 0 ruff errors, 0 mypy errors (`engine` included) |
| Format | `uv run ruff format --check .` | No changes |
| Deploy | `make sim && make sim TARGET=aws` | Exit 0 both; a second run drops nothing |
| Layering | `git grep -n -i -e 'CFBD_VS' -e 'CFB_CORE' -e 'CFB_META' -e '{VS_NAME}' -- sql/sim` | No match |
| No stale runs | `SELECT COUNT(*) FROM CFB_SIM.SIM_RUN WHERE RUN_STATUS = 'RUNNING'` after a live task | 0 |
| Zero calls | `make quota TARGET=aws` before and after each live task | `/info` used calls unchanged |
| No data in git | `git ls-files '*.parquet' '*.cfbd.json' 'spikes/*'` | Empty |
