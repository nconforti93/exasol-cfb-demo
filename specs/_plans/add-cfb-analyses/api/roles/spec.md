# Feature: Read-only roles for the app and the chat

The app's backend and the chat's MCP server each connect as a user that can read the serving API and nothing else. They cannot change data, start a refresh or a simulation, read the raw mirrors, or see the CFBD key. The live game preview still works for them, because the view calls CFBD with its owner's rights.

## Background

- **Roles:** `make grants` (`scripts/grants.py`, extended) creates, if missing, the roles `CFB_APP` (the backend, Phase 6) and `CFB_MCP` (the MCP server, Phase 7), and grants each `SELECT` on the schema `CFB_API` (`GRANT SELECT ON SCHEMA CFB_API TO …`) once the schema exists. Nothing else is granted to them: no system privilege, no other object privilege, no connection.
  - It is idempotent: a second run changes nothing. `CREATE ROLE` has no `IF NOT EXISTS` in Exasol's grammar, so the script checks the role first, as it does for `CFB_READER`.
  - The deploy-then-grants order of `CLAUDE.md` applies: `make deploy`, then `make grants`.
- **Users** are not created by this plan. The owner creates the backend's and the MCP server's users (with `CREATE SESSION`) and grants them the role (documented in `docs/user-guide.md`). No password is ever written to the repository, a file or a log.
- **Views run with their owner's rights** (probe 1.1(b)), so a role reaches the analyses, the model, the logs and the virtual schema only through `CFB_API` views, and the CFBD key never (the pushdown runs as the owner, foundation probe 1.4g).
- **A schema grant covers later views** (probe 1.1(c)), so the what-if views that each publication regenerates (`analyses/what-if`) need no new grant.
- **Calls through a role:** the only views that reach CFBD are `GAME_PREVIEW` (one call per query, `api/on-demand`) and `QUOTA` (CFBD's free `/info`). Both roles have them; whether the chat may use `GAME_PREVIEW` freely is Phase 7's decision.
- **Integration-test namespace:** `make grants ARGS="--prefix TEST_"` creates `TEST_CFB_APP` and `TEST_CFB_MCP` with `SELECT` on `TEST_CFB_API`. The harness creates a user `CFB_TEST_APP` with `CREATE SESSION` and one of the roles, with a random password held in process memory only, and drops roles and user with the namespace.
- The examples run in the integration-test namespace, deployed and granted, over the sim league with a published run at 2026 week 9, in live mode against the mock API.

## Scenarios

### Scenario: Grants create read-only roles for the app and the chat

* *GIVEN* a deployed namespace without the roles
* *WHEN* the owner runs `make grants ARGS="--prefix TEST_"` twice
* *THEN* `TEST_CFB_APP` and `TEST_CFB_MCP` MUST exist, each holding `SELECT` on the schema `TEST_CFB_API` and no other object privilege, system privilege or connection
* *AND* the second run MUST report no change

### Scenario: The app role reads every serving view and nothing else

* *GIVEN* the user `CFB_TEST_APP` with the role `TEST_CFB_APP`
* *WHEN* it selects from every `TEST_CFB_API` view (`GAME_PREVIEW` with one week-10 game id), and from one object of `TEST_CFB_PRED`, `TEST_CFB_SIM`, `TEST_CFB_MODEL`, `TEST_CFB_META`, `TEST_CFB_CORE` and `TEST_CFBD_VS` each
* *THEN* every select on `TEST_CFB_API` MUST succeed
* *AND* every other select MUST fail with an insufficient-privileges error

### Scenario: The app role cannot change data or run scripts

* *GIVEN* the user `CFB_TEST_APP` with the role `TEST_CFB_APP`
* *WHEN* it tries `INSERT INTO TEST_CFB_PRED.PREDICTION …`, `CREATE TABLE TEST_CFB_API.X (A INT)`, and `EXECUTE SCRIPT` of `REFRESH_WEEK`, `PREDICT`, `PUBLISH_RUN` and `SIMULATE` in `TEST_CFB_INFRA`
* *THEN* each statement MUST fail with an insufficient-privileges error
* *AND* no table of the namespace MUST change, and `TEST_CFB_SIM.SIM_RUN` and `TEST_CFB_PRED.PUBLICATION` MUST NOT gain a row

### Scenario: The chat role has the same access as the app role

* *GIVEN* the user `CFB_TEST_APP` granted `TEST_CFB_MCP` instead of `TEST_CFB_APP`
* *WHEN* it runs the statements of the two previous scenarios
* *THEN* every result MUST be the same as with `TEST_CFB_APP`

### Scenario: Regenerated what-if views stay readable without a new grant

* *GIVEN* the user `CFB_TEST_APP` with the role `TEST_CFB_APP`, reading `TEST_CFB_API.WHATIF_OUTCOMES` of the published run
* *WHEN* the owner publishes a new manual run at 2026 week 9
* *THEN* the same user MUST read `WHATIF_OUTCOMES` of the new run without any new grant

### Scenario: The live preview works for the app role without access to the virtual schema or the key

* *GIVEN* the user `CFB_TEST_APP` with the role `TEST_CFB_APP`
* *WHEN* it selects from `TEST_CFB_API.GAME_PREVIEW` with one week-10 game id, then from `TEST_CFBD_VS.GAMES_PREVIEW` with the same id
* *THEN* the first MUST return the mock's preview for that game
* *AND* the second MUST fail with an insufficient-privileges error
* *AND* the user's direct call of the fetch UDF `TEST_CFB_INFRA.CFBD_FETCH` MUST be denied, and the test connection's marker key MUST NOT appear in any value or error the user receives (`cfbd-fetch/key-protection`)
