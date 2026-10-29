# Feature: Read-only roles for the app and the chat

The app's backend and the chat's MCP server each connect as a user that can read the serving API and nothing else. They cannot change data, start a refresh or a simulation, read the raw mirrors, or see the CFBD key. The app can also fetch the live game preview, because that view calls CFBD with its owner's rights. The chat cannot: its role has no access to the schema of the views that reach CFBD, so it can never spend quota.

## Background

- **Roles:** `make grants` (`scripts/grants.py`, extended) creates, if missing, the roles `CFB_APP` (the backend, Phase 6) and `CFB_MCP` (the MCP server, Phase 7), once the schemas exist:
  - `CFB_APP` gets `SELECT` on the schemas `CFB_API` and `CFB_API_LIVE` (`GRANT SELECT ON SCHEMA … TO CFB_APP`);
  - `CFB_MCP` gets `SELECT` on the schema `CFB_API` only.

  Nothing else is granted to them: no system privilege, no other object privilege, no connection.
  - It is idempotent: a second run changes nothing. `CREATE ROLE` has no `IF NOT EXISTS` in Exasol's grammar, so the script checks the role first, as it does for `CFB_READER`.
  - The deploy-then-grants order of `CLAUDE.md` applies: `make deploy`, then `make grants`.
  - Phase 7 can widen the chat's access later by granting it `CFB_API_LIVE` as well; the split makes that one explicit grant instead of a change to the contract.
- **Users** are not created by this plan. The owner creates the backend's and the MCP server's users (with `CREATE SESSION`) and grants them the role (documented in `docs/user-guide.md`). No password is ever written to the repository, a file or a log.
- **Views run with their owner's rights** (probe 1.1(b)), so a role reaches the analyses, the model, the logs and the virtual schema only through the serving views, and the CFBD key never (the pushdown runs as the owner, foundation probe 1.4g). In Exasol a schema's objects belong to the schema's owner, so the rights the views run with are those of the owner of `CFB_API` and `CFB_API_LIVE`. One user owns every schema of a deployment (`sys` on the real deployment, `CFB_TEST_OWNER` in the integration-test namespace), so that owner holds every underlying privilege.
- **A schema grant covers later views** (probe 1.1(c)), so the what-if views that each publication regenerates (`analyses/what-if`) need no new grant.
- **Calls through a role:** only the views of `CFB_API_LIVE` reach CFBD: `GAME_PREVIEW` (one call per query, `api/on-demand`) and `QUOTA` (CFBD's free `/info`). The app role has them; the chat role does not.
- **Integration-test namespace:** `make grants ARGS="--prefix TEST_"` creates `TEST_CFB_APP` (on `TEST_CFB_API` and `TEST_CFB_API_LIVE`) and `TEST_CFB_MCP` (on `TEST_CFB_API`). The harness creates a user `CFB_TEST_APP` with `CREATE SESSION` and one of the roles, with a random password held in process memory only, and drops roles and user with the namespace.
- The examples run in the integration-test namespace, deployed and granted, over the sim league with a published run at 2026 week 9, in live mode against the mock API.

## Scenarios

### Scenario: Grants create read-only roles for the app and the chat

* *GIVEN* a deployed namespace without the roles
* *WHEN* the owner runs `make grants ARGS="--prefix TEST_"` twice
* *THEN* `TEST_CFB_APP` MUST exist holding `SELECT` on the schemas `TEST_CFB_API` and `TEST_CFB_API_LIVE`, and `TEST_CFB_MCP` holding `SELECT` on `TEST_CFB_API` only
* *AND* neither role MUST hold any other object privilege, system privilege or connection
* *AND* the second run MUST report no change

### Scenario: The app role reads every serving view and nothing else

* *GIVEN* the user `CFB_TEST_APP` with the role `TEST_CFB_APP`
* *WHEN* it selects from every view of `TEST_CFB_API` and `TEST_CFB_API_LIVE` (`GAME_PREVIEW` with one week-10 game id), and from one object of `TEST_CFB_PRED`, `TEST_CFB_SIM`, `TEST_CFB_MODEL`, `TEST_CFB_META`, `TEST_CFB_CORE` and `TEST_CFBD_VS` each
* *THEN* every select on the two serving schemas MUST succeed
* *AND* every other select MUST fail with an insufficient-privileges error

### Scenario: The app role cannot change data or run scripts

* *GIVEN* the user `CFB_TEST_APP` with the role `TEST_CFB_APP`
* *WHEN* it tries `INSERT INTO TEST_CFB_PRED.PREDICTION …`, `CREATE TABLE TEST_CFB_API.X (A INT)`, and `EXECUTE SCRIPT` of `REFRESH_WEEK`, `PREDICT`, `PUBLISH_RUN` and `SIMULATE` in `TEST_CFB_INFRA`
* *THEN* each statement MUST fail with an insufficient-privileges error
* *AND* no table of the namespace MUST change, and `TEST_CFB_SIM.SIM_RUN` and `TEST_CFB_PRED.PUBLICATION` MUST NOT gain a row

### Scenario: The chat role reads the serving views but cannot reach CFBD

* *GIVEN* the user `CFB_TEST_APP` granted `TEST_CFB_MCP` instead of `TEST_CFB_APP`
* *WHEN* it selects from every view of `TEST_CFB_API`, from `TEST_CFB_API_LIVE.GAME_PREVIEW` with one week-10 game id and from `TEST_CFB_API_LIVE.QUOTA`, and runs the statements of the previous scenario
* *THEN* every select on `TEST_CFB_API` MUST succeed
* *AND* both selects on `TEST_CFB_API_LIVE` and every statement of the previous scenario MUST fail with an insufficient-privileges error
* *AND* the mock MUST NOT receive any request

### Scenario: Regenerated what-if views stay readable without a new grant

* *GIVEN* the user `CFB_TEST_APP` with the role `TEST_CFB_APP`, reading `TEST_CFB_API.WHATIF_OUTCOMES` of the published run
* *WHEN* the owner publishes a new manual run at 2026 week 9
* *THEN* the same user MUST read `WHATIF_OUTCOMES` of the new run without any new grant

### Scenario: The live preview works for the app role without access to the virtual schema or the key

* *GIVEN* the user `CFB_TEST_APP` with the role `TEST_CFB_APP`
* *WHEN* it selects from `TEST_CFB_API_LIVE.GAME_PREVIEW` with one week-10 game id, then from `TEST_CFBD_VS.GAMES_PREVIEW` with the same id
* *THEN* the first MUST return the mock's preview for that game
* *AND* the second MUST fail with an insufficient-privileges error
* *AND* the user's direct call of the fetch UDF `TEST_CFB_INFRA.CFBD_FETCH` MUST be denied, and the test connection's marker key MUST NOT appear in any value or error the user receives (`cfbd-fetch/key-protection`)
