# Feature: Endpoint catalog

Turns the pinned CFBD OpenAPI spec plus a reviewed overrides file into the catalog that defines every table of the CFBD Virtual Schema. The whole API becomes a schema without hand-written mapping code, and a new API version means regenerate, review the diff, commit.

## Background

- Inputs: the pinned spec `vs/openapi/cfbd-openapi-5.33.0.json` (93 GET operations) and the reviewed overrides file `vs/catalog-overrides.yaml`. `make generate` writes the committed catalog `vs/catalog/catalog.json` and the CFB_CORE mirror DDL `sql/core/cfb_core_mirrors.sql`. Both are derived from API metadata only and contain no CFBD data.
- The virtual schema is `CFBD_VS`. Each non-excluded GET operation becomes one endpoint table. Its name comes from the path: drop the leading `/` and the path parameters, join the segments with `_`, split camelCase, uppercase (`/games/{gameId}/preview` → `GAMES_PREVIEW`, `/metrics/wp/pregame` → `METRICS_WP_PREGAME`). `cfbd-vs/flattening` specifies the child tables derived from arrays.
- Every table has three column groups:
  - response columns (`cfbd-vs/flattening`);
  - one parameter column `P_<PARAM>` per query or path parameter of the endpoint (`cfbd-vs/pushdown`);
  - the request metadata columns `REQ_*` (`cfbd-fetch/fetch-log`).
- Parameter column types: integer → `DECIMAL(18,0)`, number → `DOUBLE`, boolean → `BOOLEAN`, string and enum → `VARCHAR(2000) UTF8`. An override may correct a parameter's type, for example `week` on `/rankings`, which the spec declares as `number`.
- The overrides file holds everything the spec states only in prose:
  - conditional requirement rules and defaults to make explicit;
  - row caps and tier gating;
  - exclusions with reasons;
  - curated column subsets for very large objects, hoisting and parent keys;
  - column type, size and name fixes;
  - read-through eligibility.

  Every entry is reviewed like code.
- The catalog has a version hash computed from the generated catalog content (the canonical JSON with the hash field blanked), so any change to the generated tables, columns, types or rules changes it, and a change that leaves the content alone (a comment in the overrides) does not. The adapter script and the fetch UDF are both deployed from one catalog version.

## Scenarios

### Scenario: Every GET operation becomes a virtual table

* *GIVEN* the pinned spec with 93 GET operations
* *AND* an overrides file that excludes a set of endpoints, each with a reason
* *WHEN* the catalog is generated and `CFBD_VS` is created from it
* *THEN* `CFBD_VS` MUST contain exactly one endpoint table for every non-excluded GET operation
* *AND* the catalog MUST list every excluded endpoint with its exclusion reason
* *AND* the number of endpoint tables plus the number of exclusions MUST equal 93

### Scenario: Invalid overrides stop generation

* *GIVEN* an overrides file with an entry that names an endpoint, parameter or response field that does not exist in the pinned spec, or an exclusion without a reason
* *WHEN* the catalog is generated
* *THEN* generation MUST fail with a non-zero exit status
* *AND* the error MUST name every offending overrides entry and the reason it is invalid
* *AND* the generator MUST NOT write or modify any output file

### Scenario: Prose-only rules must be covered by an override

* *GIVEN* an operation or parameter whose description in the spec states a rule in prose, such as "Required unless", "Requires", "required when", "Cannot be combined", "Defaults to", "limited to", "maximum" or "Requires Patreon"
* *AND* neither a rule in the overrides file nor an explicit acknowledgement entry covers it
* *WHEN* the catalog is generated
* *THEN* generation MUST fail
* *AND* the error MUST list every uncovered operation or parameter together with its description text

### Scenario: Generation is deterministic and the committed catalog is current

* *GIVEN* an unchanged spec and overrides file
* *WHEN* the catalog is generated twice
* *THEN* both runs MUST produce byte-identical `vs/catalog/catalog.json` and `sql/core/cfb_core_mirrors.sql`
* *AND* the committed files MUST be identical to a fresh generation

### Scenario: Table names derive from the endpoint path

* *GIVEN* the endpoints `/games/{gameId}/preview`, `/metrics/wp/pregame`, `/player/search` and `/stats/season/advanced`
* *WHEN* the virtual schema is created
* *THEN* the tables `GAMES_PREVIEW`, `METRICS_WP_PREGAME`, `PLAYER_SEARCH` and `STATS_SEASON_ADVANCED` MUST exist in `CFBD_VS`
* *AND* if two endpoints mapped to the same table name, generation MUST fail and name both paths

### Scenario: Every API parameter becomes a typed parameter column

* *GIVEN* the endpoint `/games` with the query parameters `year`, `week`, `seasonType`, `classification`, `team`, `home`, `away`, `conference`, `id`, `competition` and `round`
* *AND* the endpoint `/games/{gameId}/preview` with the path parameter `gameId`
* *WHEN* the virtual schema is created
* *THEN* `CFBD_VS.GAMES` MUST have the columns `P_YEAR`, `P_WEEK`, `P_SEASON_TYPE`, `P_CLASSIFICATION`, `P_TEAM`, `P_HOME`, `P_AWAY`, `P_CONFERENCE`, `P_ID`, `P_COMPETITION` and `P_ROUND`
* *AND* `P_YEAR`, `P_WEEK` and `P_ID` MUST be `DECIMAL(18,0)`, and `P_SEASON_TYPE` and `P_TEAM` MUST be `VARCHAR(2000) UTF8`
* *AND* `CFBD_VS.GAMES_PREVIEW` MUST have the column `P_GAME_ID` of type `DECIMAL(18,0)`

### Scenario: Columns carry the API documentation and the rules

* *GIVEN* the generated catalog
* *WHEN* a user reads the column metadata of a virtual table from `EXA_ALL_COLUMNS`
* *THEN* each response column comment SHALL contain the field description from the spec, if the spec has one
* *AND* each parameter column comment MUST contain the parameter description and every rule that constrains the parameter (required, conditional requirement, explicit default, allowed values, bounds, required feature)

### Scenario: Column name collisions stop generation

* *GIVEN* two response fields of one table that flatten to the same column name, or a response column whose name equals a parameter, request metadata or position column
* *AND* no overrides entry renames one of them
* *WHEN* the catalog is generated
* *THEN* generation MUST fail and name the table, the column name and both source fields

### Scenario: Reserved words never become column names

* *GIVEN* a response field whose flattened name is an Exasol reserved keyword, such as `date` in the games of `/teams/matchup`
* *AND* no overrides entry renames it
* *WHEN* the virtual schema is created
* *THEN* the column MUST be named with a trailing underscore (`DATE_`), so that users can reference it without quoting
* *AND* the column comment MUST name the original API field

### Scenario: Refreshing the virtual schema reproduces the same tables

* *GIVEN* a deployed `CFBD_VS`
* *WHEN* the owner runs `ALTER VIRTUAL SCHEMA CFBD_VS REFRESH`
* *THEN* the set of tables, their columns, column order, data types and comments MUST be unchanged

### Scenario: Adapter and fetch UDF from different catalog versions are refused

* *GIVEN* an adapter script deployed from one catalog version and a fetch UDF deployed from another
* *WHEN* a query needs to fetch a slice
* *THEN* the query MUST fail with an error stating that the adapter and the fetch UDF were deployed from different catalog versions and that a redeploy is required
* *AND* no request SHALL be sent to the API
