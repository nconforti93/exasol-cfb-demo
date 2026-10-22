# Feature: Self-describing model

Analysis builders and the conversational analyst (Phase 7, over MCP) find their way through `CFB_MODEL` from the database alone. Every object and column carries a comment that says what it holds, its grain, its unit or code list, and from which season it can be trusted. Column names are plain identifiers, so SQL against the model needs no quoting. Example analyses show that the model answers the questions of the later phases without touching `CFB_CORE`.

## Background

- **Comments** are part of each object's SQL file. Views carry them inline (`CREATE OR REPLACE VIEW … ("COL" COMMENT IS '…', …) AS … COMMENT IS '…'`), because Exasol has no `COMMENT ON VIEW`. Tables carry them inline in `CREATE OR REPLACE TABLE`. The function uses `COMMENT ON FUNCTION`.
- **Rules**, checked by the lint before deploy (`model/deploy`) and on the deployed objects:
  - every table, view and function has a comment, and every column of every table and view has one;
  - an object comment starts with its grain: "One row per …";
  - a comment is at most 2,000 characters long;
  - a comment that names a code list lists every code, for example `SITE`: `HOME`, `AWAY` or `NEUTRAL`;
  - no column name is an Exasol reserved word (the list pinned in `vs/gen/exasol_reserved_keywords.txt`).
- **Data dictionary:** `docs/model-dictionary.md` is generated from `sql/model/` by `make model-docs`, with one section per object (grain, columns, comments). It is committed, and it changes only when the SQL files change.
- **Example analyses** in `sql/model/examples/`, one file per question that Phases 4–6 will ask:
  - `as_of_engine_inputs.sql`: records, conference, tier, Elo and ranks of every FBS team, plus the remaining schedule, as of a week (Phase 4 engine input);
  - `all_time_upsets.sql`: completed games ranked by the winner's pregame win probability, from spreads since 2013 and from Elo before (Phase 5 upsets);
  - `head_to_head.sql`: the all-time series between two teams with the last meeting (Phase 5 head-to-head);
  - `cfp_field_inputs.sql`: conference champions with their tier and the latest committee ranking for a completed season (Phase 4 backtest).
- The examples reference `{MODEL_SCHEMA}` only. The integration tests render them for the test namespace.

## Scenarios

### Scenario: Every object and column has a comment

* *GIVEN* a deployed model in the test namespace
* *WHEN* the comments are read from `EXA_ALL_OBJECTS` and `EXA_ALL_COLUMNS` for `TEST_CFB_MODEL`
* *THEN* every table, view and function MUST have a non-empty comment
* *AND* every column of every table and view MUST have a non-empty comment

### Scenario: Every object comment states its grain

* *GIVEN* a deployed model in the test namespace
* *WHEN* the object comments are read
* *THEN* every table and view comment MUST start with "One row per"
* *AND* the comment of `TEAM_SEASON_AS_OF` MUST name the as-of point, the team and the season as its grain

### Scenario: Model columns need no quoting

* *GIVEN* a deployed model in the test namespace
* *WHEN* a user runs `SELECT SEASON, WEEK, HOME_TEAM, AWAY_TEAM, HOME_POINTS, AWAY_POINTS FROM TEST_CFB_MODEL.GAME WHERE SEASON = 2024` without any double quotes
* *THEN* the query MUST succeed
* *AND* no column name in `TEST_CFB_MODEL` MUST be a reserved word

### Scenario: The data dictionary matches the deployed comments

* *GIVEN* a deployed model and `docs/model-dictionary.md` as committed
* *WHEN* `make model-docs` regenerates the dictionary
* *THEN* the file MUST be unchanged
* *AND* every object and column comment in the file MUST equal the comment read from the database

### Scenario: The example analyses read only the model

* *GIVEN* the four example analyses in `sql/model/examples/`
* *WHEN* they are rendered for the test namespace and run against the synthetic league
* *THEN* each MUST succeed and return the expected rows of the league
* *AND* no example MUST reference `CFB_CORE`, `CFB_META` or `CFBD_VS`
* *AND* the database's dependency lists of the objects they read MUST lead only to model objects, mirrors and the allowed loader views
