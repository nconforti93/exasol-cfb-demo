# Feature: Model deployment

`CFB_MODEL` is the analytical model on top of the stored CFBD data. One command installs it from reviewed SQL files, and running it again changes nothing. The model reads only the stored mirrors in `CFB_CORE` and the loader's plan views in `CFB_META`. It never reads the virtual schema, so building, deploying and querying it costs zero API calls.

## Background

- **Source of truth:** one SQL file per model object in `sql/model/`, deployed in file-name order:
  - reference tables (curated knowledge that CFBD does not provide: eras, division labels, conference tiers, polls, datasets);
  - one SQL function (`GREAT_CIRCLE_KM`);
  - views for every dimension, fact, coverage and as-of object.

  Every derived object is a view, so the model always reflects the stored data. There are no model tables to rebuild after a backfill, a refresh or a restore.
- **Commands** (all take `TARGET=local|aws`; `ARGS="--prefix TEST_"` deploys the integration-test namespace):
  - `make model`: lints, renders and deploys the model objects only.
  - `make deploy`: deploys the virtual schema, the loader and then the model.
- **Lint** (before any statement reaches the database). A file is rejected when:
  - an object, or one of its columns, has no comment;
  - an object comment does not start with its grain ("One row per …");
  - a column name is an Exasol reserved word;
  - it references a schema other than `CFB_MODEL`, `CFB_CORE` or the allowed `CFB_META` objects (`LOAD_PLAN`, `LOAD_PLAN_GAPS`, `LOAD_SLICES`), in particular the virtual schema `CFBD_VS`;
  - its object name differs from its file name, or two files define the same object;
  - it uses an unknown placeholder.
- **Deploy** runs in one transaction:
  1. create the schema if needed;
  2. replace the reference tables and their rows;
  3. replace the function and the views in file order;
  4. drop model objects that no file defines any more;
  5. read the comments back from the database and require one on every object and column.

  Any failure rolls the whole deploy back, so the previously deployed model stays as it was.
- The deploy reports one line per step: objects created or replaced, objects dropped, and the comment coverage.
- The examples run in the integration-test namespace (`TEST_CFB_MODEL` over `TEST_CFB_CORE` and `TEST_CFB_META`). The synthetic test league is stored in `TEST_CFB_CORE`.

## Scenarios

### Scenario: One command installs the whole model

* *GIVEN* the virtual schema and the loader are deployed in the test namespace, and `TEST_CFB_MODEL` does not exist
* *WHEN* the owner runs `make model ARGS="--prefix TEST_"`
* *THEN* `TEST_CFB_MODEL` MUST contain exactly the objects that `sql/model/` defines
* *AND* every view MUST return rows from the synthetic league without an error
* *AND* the report MUST state the number of tables, views and functions and a comment coverage of 100 %

### Scenario: A second deploy changes nothing

* *GIVEN* a deployed model
* *WHEN* the owner runs `make model ARGS="--prefix TEST_"` again
* *THEN* the same objects MUST exist with the same columns and comments
* *AND* every view MUST return the same rows as before
* *AND* the reference tables MUST hold the same rows, without duplicates

### Scenario: The full deploy includes the model

* *GIVEN* an empty test namespace
* *WHEN* the owner runs `make deploy ARGS="--prefix TEST_"`
* *THEN* the virtual schema, the loader objects and `TEST_CFB_MODEL` MUST all exist afterwards
* *AND* the model MUST be deployed after the loader

### Scenario: A file that breaks a lint rule stops the deploy before any statement

* *GIVEN* a deployed model
* *AND* a changed model directory with four problems:
  - a view column without a comment;
  - a view whose comment starts with "Games" instead of "One row per";
  - a column named `RESULT`, which is a reserved word;
  - a view that selects from `CFBD_VS.GAMES`
* *WHEN* the owner runs `make model`
* *THEN* the command MUST fail with one message per problem, each naming the file, the object and the rule
* *AND* no statement MUST be sent to the database
* *AND* the previously deployed model MUST stay unchanged

### Scenario: A failing statement rolls the whole deploy back

* *GIVEN* a deployed model
* *AND* a changed model directory in which the third view has an SQL error that only the database detects
* *WHEN* the owner runs `make model`
* *THEN* the command MUST fail and name the object and the database error
* *AND* every model object, column, comment and reference row MUST be exactly as before the command

### Scenario: An object that no file defines any more is dropped

* *GIVEN* a deployed model that contains the view `OLD_VIEW`
* *AND* `sql/model/` no longer defines `OLD_VIEW`
* *WHEN* the owner runs `make model`
* *THEN* `OLD_VIEW` MUST NOT exist afterwards
* *AND* the report MUST name it as dropped

### Scenario: The model accepts any data the virtual schema can store

* *GIVEN* every table of the `demo` load profile staged through the virtual schema from the synthetic response generator (`tests/synthetic/fake.py`), not from the league
* *WHEN* every model view is queried in full
* *THEN* no query MUST fail
* *AND* `MODEL_CHECKS` MUST return a status for every check of every stored season

### Scenario: The model never reads the virtual schema

* *GIVEN* a deployed model and the mock API in live mode
* *WHEN* every model view is queried in full
* *THEN* the mock API MUST NOT receive any request
* *AND* `TEST_CFB_META.FETCH_LOG` MUST NOT gain any row
* *AND* the database's dependency list for every model view MUST name only objects in `TEST_CFB_MODEL`, `TEST_CFB_CORE` and the allowed `TEST_CFB_META` objects
