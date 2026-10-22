# Feature: Analyses and serving deployment

The analyses and the serving API are installed with one command, together with everything else, on the laptop and on AWS. A redeploy never loses a frozen prediction or a publication: they are history. What the analyses and the API may read is checked before anything is sent, so no page can ever trigger a broad API scan or read the raw mirrors.

## Background

- **Command:** `make analyses [TARGET=local|aws] [ARGS="…"]`, also run by `make deploy` after `make sim` (`sim-engine/deploy`). It takes the loader's run lock as run kind `DEPLOY` (stale only when its session ends, `sim-engine/sim-runs`) and deploys, in one transaction and in this order:
  1. **`CFB_PRED`** from `sql/pred/`:
     - the reference table `ANALYSIS_SETTINGS`, replaced with its seed rows on every deploy;
     - the data tables `PREDICTION`, `PUBLICATION` and `PUBLISHED_ODDS` from `sql/pred/data/`, created when missing and never replaced;
     - 12 views: `PREDICTION_GRADED`, `SCORECARD_WEEK`, `SCORECARD_SEASON`, `SCORECARD_CALIBRATION`, `UPSET`, `POINT_PUBLICATION`, `WHATIF_PUBLICATION`, `ODDS_MOVEMENT`, `ROOTING_GUIDE`, `FORCED_RUN_ODDS`, `MATCHUP_SERIES` and `MATCHUP_NEXT`;
  2. **the generated what-if views** `WHATIF_RUN`, `WHATIF_OUTCOMES` and `WHATIF_PLAYOFF` of `CFB_API` (`analyses/what-if`), in their empty form, for each one that is missing or whose fixed columns differ from its template in `sql/api/generated/`. Existing ones are kept, so a redeploy never shows an empty what-if;
  3. **`CFB_API`**: the 23 views of `sql/api/` (`api/serving-contract`), which may read the generated views;
  4. **`CFB_API_LIVE`**: the 2 views of `sql/api_live/` (`api/on-demand`);
  5. **Lua scripts in `CFB_INFRA`**: `REFRESH_PREDICT`, `PREDICT`, `REFRESH_PUBLISH`, `PUBLISH_RUN` and `WHATIF_VIEWS`, bundled by the loader's bundler with the loader's lock library.

  It then compiles every view, reads back every comment and commits. After the commit it runs `EXECUTE SCRIPT CFB_INFRA.WHATIF_VIEWS()`, which writes the three generated views for `CFB_PRED.WHATIF_PUBLICATION`, or empty ones when there is none. The report lists the objects, the generated views with their run, the comment coverage and the refresh steps.
- **Refresh steps** 200 `PREDICT` and 400 `PUBLISH` are entries of `loader/load-plan.yaml`. The loader's deploy writes them into `CFB_META.REFRESH_STEPS` with the other entries (`loader/load-plan`); `make analyses` does not write them.
  - After its deploy, `make analyses` reports every registered step with its script, and flags a step whose script does not exist and a missing 200 or 400 row as warnings that name the step.
  - In `make deploy` the loader comes before the analyses. After a partial deploy (`make loader` alone), a refresh needs `make model`, `make sim` and `make analyses` first, because the registered steps call their scripts.
  - Integration tests deploy the loader with plan variants from `tests/it/fixtures/`: this plan's `load-plan.analyses.yaml` registers steps 100 to 400, and the variants of the earlier phases keep their own steps.
- **After a restore** (`loader/snapshot-restore`), the recipe runs `make analyses`, which regenerates the what-if views for the restored point (`analyses/what-if`).
- **Rules** shared with the model and simulation deploys (`model/deploy`, `model/documentation`, `sim-engine/deploy`):
  - SQL files in `sql/pred/`, `sql/pred/data/`, `sql/api/` and `sql/api_live/`, one object per file, rendered through the namespace's placeholders (`{PRED_SCHEMA}`, `{API_SCHEMA}`, `{API_LIVE_SCHEMA}`, `{SIM_SCHEMA}`, `{MODEL_SCHEMA}`, `{META_SCHEMA}`, `{VS_NAME}`);
  - every object and column commented, every table and view comment starting with "One row per";
  - DDL names unquoted upper case and not reserved, query identifiers double-quoted;
  - every string literal that a generator writes into SQL text (seed notes, `--setting` values, the empty generated views here, the generated what-if views in `WHATIF_VIEWS`) goes through one escaping rule, `'` doubled (`analyses/sqltext.py` on the host, `analib.sql_str` in Lua);
  - a lint before anything is sent, then every view compiled and every comment read back;
  - objects that no file defines are dropped, except the data tables and the generated views `WHATIF_RUN`, `WHATIF_OUTCOMES` and `WHATIF_PLAYOFF`;
  - a data table whose columns differ from its file stops the deploy with a message naming the table and the difference; nothing is migrated silently.
- **Layering**, checked by the lint and, after the deploy, through `EXA_ALL_DEPENDENCIES`:
  - `CFB_PRED` may reference only `CFB_PRED`, `CFB_SIM` and `CFB_MODEL`;
  - `CFB_API` may reference `CFB_API`, `CFB_PRED`, `CFB_SIM`, `CFB_MODEL` and the `CFB_META` objects `REFRESH_LOG`, `LOAD_RUN` and `FETCH_LOG`. It references no virtual table and nothing that reaches the network;
  - `CFB_API_LIVE` may reference only the virtual table `GAMES_PREVIEW` (in its preview file) and `CFB_META.QUOTA` (in its quota file);
  - never `CFB_CORE`;
  - the Lua scripts may also use `CFB_META` (lock, clock, step log) and `CFB_INFRA`.
- **Test hooks:** `ARGS="--setting TEST_FAIL_PUBLISH=1"` sets a row of `ANALYSIS_SETTINGS` that makes the what-if regeneration fail. It is accepted only in the integration-test namespace; a deploy elsewhere removes it.
- The deploy costs zero CFBD calls. The examples run in the integration-test namespace.

## Scenarios

### Scenario: One command installs the analyses and the serving schemas

* *GIVEN* a deployed simulation schema, a loader deployed with the plan variant `load-plan.analyses.yaml`, and no analyses
* *WHEN* the owner runs `make analyses ARGS="--prefix TEST_"`
* *THEN* the report MUST list in `CFB_PRED` 1 reference table, 3 data tables and 12 views, in `CFB_API` 23 views plus the 3 generated what-if views, in `CFB_API_LIVE` 2 views, and 5 scripts, with 100 % comments
* *AND* every view MUST have compiled with readable columns before the commit, the file views over the generated views' empty forms included
* *AND* the report MUST list steps 100 `MODEL`, 200 `PREDICT`, 300 `SIMULATE` and 400 `PUBLISH` of `CFB_META.REFRESH_STEPS`, each with an existing script and no warning

### Scenario: A redeploy keeps predictions and publications

* *GIVEN* a deployed namespace with frozen predictions and a publication of a `DONE` run
* *WHEN* the owner runs `make analyses ARGS="--prefix TEST_"` again
* *THEN* `CFB_PRED.PREDICTION`, `PUBLICATION` and `PUBLISHED_ODDS` MUST be unchanged
* *AND* `CFB_API.WHATIF_RUN` MUST still name the published run
* *AND* the report MUST drop nothing

### Scenario: The full deploy includes the analyses after the simulation schema

* *GIVEN* an empty integration-test namespace
* *WHEN* the harness runs the full deploy
* *THEN* the virtual schema, the loader, the model, the simulation schema and the analyses MUST be deployed, in that order

### Scenario: The analyses and the API read only what they may

* *GIVEN* a file in `sql/pred/` whose view selects from `CFB_CORE.GAMES`, and, separately, a file in `sql/api/` whose view selects from the virtual table `GAMES_PREVIEW`
* *WHEN* the owner runs `make analyses` for each
* *THEN* the lint MUST name the file, the view and the reference, and nothing MUST be sent to the database
* *AND* for the deployed schemas, `EXA_ALL_DEPENDENCIES` MUST show references only to the schemas and objects listed in the layering rule

### Scenario: A changed data table stops the deploy

* *GIVEN* a deployed `CFB_PRED.PREDICTION` and a file that adds a column to it
* *WHEN* the owner runs `make analyses ARGS="--prefix TEST_"`
* *THEN* the deploy MUST fail with a message that names `PREDICTION` and the added column
* *AND* no object of `CFB_PRED`, `CFB_API` or `CFB_API_LIVE` MUST change

### Scenario: A deploy is refused while a run holds the lock

* *GIVEN* a refresh run of another session that is `RUNNING` and not idle
* *WHEN* the owner runs `make analyses ARGS="--prefix TEST_"`
* *THEN* the deploy MUST fail with a message that names the running run
* *AND* nothing MUST be sent to `CFB_PRED`, `CFB_API` or `CFB_API_LIVE`
