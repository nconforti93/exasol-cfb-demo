# Feature: Read-only roles for the app and the chat

The app and the chat read the serving schemas and nothing else: they cannot write, run scripts, read the model or the raw data, or reach the API key. The chat cannot spend CFBD quota at all, because it cannot read the one schema whose views call CFBD. Both guarantees are grant facts that one query can show.

## Background

- `make grants [TARGET=…]` (code `scripts/grants.py`) creates, idempotently and in addition to the foundation's `CFB_READER`:
  - the role `CFB_APP` with `SELECT ON SCHEMA CFB_API` and `SELECT ON SCHEMA CFB_API_LIVE`;
  - the role `CFB_MCP` with `SELECT ON SCHEMA CFB_API` only.

  A schema that does not exist yet is skipped with a note; run `make grants` again after `make api`. With `--prefix TEST_` the roles are `TEST_CFB_APP` and `TEST_CFB_MCP` on the `TEST_` schemas.
- Neither role gets a privilege on `CFB_MODEL`, `CFB_SIM`, `CFB_META`, `CFB_CORE`, `CFB_INFRA` or `CFBD_VS`, any system privilege, or any connection. Views read with their owner's rights (confirmed by Phase 6's probe S9 (a)), and one user owns every schema of a deployment (`sys`; `CFB_TEST_OWNER` in `TEST_`), so the schema grants are enough.
- Users are created by the owner with a password typed into a SQL client and given `CREATE SESSION` plus one role (`docs/user-guide.md`); no password is stored in a file of the repository. The integration harness creates `CFB_TEST_APP` (role `TEST_CFB_APP`) and `CFB_TEST_MCP` (role `TEST_CFB_MCP`) with random passwords kept in process memory, and drops them with the namespace.

## Scenarios

### Scenario: The app role reads the serving views and nothing else

* *GIVEN* the deployed serving schemas, `make grants` run, and then `CFB_API` deployed again
* *WHEN* the user `CFB_TEST_APP` queries every view of `CFB_API`, then `CFB_MODEL.TEAM`, then tries an `INSERT` into a `CFB_META` table, a `CREATE TABLE` and an `EXECUTE SCRIPT` of `CFB_INFRA.REFRESH_WEEK`
* *THEN* every `CFB_API` view MUST answer
* *AND* every other statement MUST fail with insufficient privileges
* *AND* running `make grants` again MUST succeed and change no grant

### Scenario: The chat role reads the serving views but cannot reach CFBD

* *GIVEN* the deployed serving schemas with `CFB_API_LIVE`, `make grants` run, and the mock CFBD server armed
* *WHEN* the user `CFB_TEST_MCP` queries `CFB_API.TEAMS`, then `CFB_API_LIVE.GAME_PREVIEW` with a literal game id, then `CFB_API_LIVE.QUOTA`
* *THEN* the first query MUST answer
* *AND* the second and third MUST fail with insufficient privileges
* *AND* the mock MUST NOT receive any request
