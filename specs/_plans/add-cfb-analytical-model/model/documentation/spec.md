# Feature: Self-describing model

Analysis builders and the conversational analyst (Phase 7, over MCP) find their way through `CFB_MODEL` from the database alone. Every object and column carries a comment that says what it holds, its grain, its unit or code list, and from which season it can be trusted. Names are plain upper-case identifiers, so SQL against the model needs no quoting. Example analyses show that the model answers the questions of the later phases without touching `CFB_CORE`.

## Background

- **Comments** are part of each object's SQL file. Views carry them inline (`CREATE OR REPLACE FORCE VIEW … (COL COMMENT IS '…', …) AS … COMMENT IS '…'`): the grammar has no `COMMENT ON VIEW`, and an inline comment is replaced atomically with its view. Tables carry them inline in `CREATE OR REPLACE TABLE`. The function uses `COMMENT ON FUNCTION`.
- **Rules**, checked by the lint before deploy (`model/deploy`) and on the deployed objects:
  - every table, view and function has a comment, and every column of every table and view has one;
  - an object comment starts with its grain: "One row per …";
  - a comment is at most 2,000 characters long;
  - a comment that names a code list lists every code, for example `SITE`: `HOME`, `AWAY` or `NEUTRAL`;
  - a comment of a real-valued column that the score model or the engine reads states its fixed scale, for example "DECIMAL(6,2)";
  - no object or column name is quoted, lower case or an Exasol reserved word (the list pinned in `vs/gen/exasol_reserved_keywords.txt`).
- **Data dictionary:** `docs/model-dictionary.md` is generated from `sql/model/` by `make model-docs`, with one section per object (grain, columns, comments). It is committed, and it changes only when the SQL files change.
- **Example analyses** in `sql/model/examples/`, one file per question that Phases 4–6 will ask. Each has a header comment with the question, the consumer phase and the objects it reads.
  - `as_of_engine_inputs.sql` (Phase 4 engine input, `specs/architecture.md` § Engine contract), as of a week:
    - per FBS team: conference, tier, records and conference records so far, conference point differential, the point differential with margins capped at 24 (the engine's default `margin_cap`, a parameter Phase 4 owns), SP+ prior and Elo;
    - the remaining games with home, away, neutral site and conference flag, without the games the engine simulates (conference championships, the postseason) and without placeholders;
    - the completed conference games with their margins, for head-to-head.
  - `all_time_upsets.sql`: completed games ranked by the winner's pregame win probability, from spreads since 2013 and from Elo before (Phase 5 upsets).
  - `head_to_head.sql`: the all-time series between two teams with the last and the next meeting (Phase 5 head-to-head).
  - `cfp_field_inputs.sql`: for a completed season, the winners of the conference championship games with their tier, and the final committee ranking (Phase 4 backtest). Its header says that champions of conferences without a championship game come from the standings, which is Phase 4's job.
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
* *AND* no object or column name in `TEST_CFB_MODEL` MUST be a reserved word or contain a lower-case letter

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
