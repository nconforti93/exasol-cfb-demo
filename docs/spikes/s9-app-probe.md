# S9 app probe: early part (task 1.1, plan add-cfb-app)

Script: `docs/spikes/s9_app_probe.py --part early` (parts a, d, e). Run on 2026-10-22/23 as `sys` on
Exasol Personal **local**, version `2026.2.0-nano.3`, in a `PROBE_APP_*` namespace that the script
creates and drops (users, roles, a connection to `https://192.0.2.1` with a dummy secret that is never
contacted, two schemas, three Lua scripts, one Python sleep UDF). Zero CFBD calls. Raw output:
`cache/probe/s9-app-probe-early.json` (git-ignored). After the runs no `PROBE_APP*` user, role, schema
or connection was left. The late part (1.10: b, c) belongs to task 1.10.

## (a) Invoker rights: confirmed. Decision [2] stands.

Lua execute scripts run with the **caller's** rights. This agrees with the project memory (preprocessor
framework, 2026-06-05) and with Phase 2's probe 1.1(f).

| Run | Result |
|---|---|
| owner runs `R_READ_T` (reads `T_DATA`), `R_WRITE_T` (inserts) | both succeed |
| user with `CREATE SESSION` and `EXECUTE` on both scripts runs `R_READ_T` | `insufficient privileges: SELECT on table T_DATA` caught in script `R_READ_T` at line 1 |
| the same user runs `R_WRITE_T` | `insufficient privileges for inserting into table T_DATA` caught in script `R_WRITE_T` at line 1 |
| that user afterwards: rows in `T_DATA` | 1 before, 1 after: nothing written |
| the same user selects `V_DATA` (a view over `T_DATA`, `SELECT` on the view only), directly and inside a script | both succeed: **views read with their owner's rights** |
| the same user selects `T_DATA` directly | `insufficient privileges: SELECT on table T_DATA` |

Consequence: an `EXECUTE`-only runner user cannot refresh. The operator path stays the namespace owner
(allowlist, loopback only). No change to the plan.

## (d) Effective privileges: a user can read almost all of its own

Views and columns (all in `SYS`; `EXA_*` views other than `EXA_DBA_*` need no privilege):

| View | Columns | Shows (as the probe user) |
|---|---|---|
| `EXA_SESSION_PRIVS` | `PRIVILEGE` | the **effective** system privileges: direct, through every role (nested too) and `PUBLIC`. Complete for the system route |
| `EXA_SESSION_ROLES` | `ROLE_NAME`, `ROLE_CONSUMER_GROUP`, `ROLE_COMMENT` | every active role, nested ones included (`PROBE_APP_ROLE` and `PROBE_APP_INNER`) |
| `EXA_USER_SYS_PRIVS` | `GRANTEE`, `PRIVILEGE`, `ADMIN_OPTION` | only the grants made to the user itself |
| `EXA_ROLE_SYS_PRIVS` | same | grants to the user's roles, nested ones, and `PUBLIC` |
| `EXA_USER_ROLE_PRIVS` / `EXA_ROLE_ROLE_PRIVS` | `GRANTEE`, `GRANTED_ROLE`, `ADMIN_OPTION` | the user's direct roles / the roles of roles |
| `EXA_ALL_OBJ_PRIVS` | `OBJECT_SCHEMA`, `OBJECT_NAME`, `OBJECT_TYPE`, `PRIVILEGE`, `GRANTEE`, `GRANTOR`, `OWNER` | object privileges the user holds by any route: the user, its roles (nested) and `PUBLIC`; `GRANTEE` names the route. A schema grant has `OBJECT_SCHEMA` NULL and `OBJECT_NAME` = the schema |
| `EXA_USER_OBJ_PRIVS`, `EXA_ROLE_OBJ_PRIVS` | same | the same split as for system privileges |
| `EXA_USER_CONNECTION_PRIVS`, `EXA_ROLE_CONNECTION_PRIVS` | `GRANTEE`, `GRANTED_CONNECTION`, `ADMIN_OPTION` | `GRANT CONNECTION` to the user / to roles (nested) and `PUBLIC` |
| `EXA_USER_RESTRICTED_OBJ_PRIVS`, `EXA_ROLE_RESTRICTED_OBJ_PRIVS` | `OBJECT_SCHEMA`, `OBJECT_NAME`, `OBJECT_TYPE`, `FOR_OBJECT_SCHEMA`, `FOR_OBJECT_NAME`, `FOR_OBJECT_TYPE`, `PRIVILEGE`, `GRANTEE`, … | `GRANT ACCESS ON CONNECTION` (also the `FOR SCRIPT` form: the `FOR_OBJECT_*` columns name the script) to the user / roles / `PUBLIC` |
| `EXA_USER_IMPERSONATION_PRIVS` | `GRANTEE`, `IMPERSONATION_ON`, `GRANTOR` | **only grants made to the user itself** |
| `EXA_USER_SCHEMAS`, `EXA_USER_OBJECTS`; `EXA_ALL_SCHEMAS` (`SCHEMA_OWNER`), `EXA_ALL_OBJECTS` (`OWNER`) | | what the user owns |
| `EXA_DBA_*` | | refused for the user: `insufficient privileges for accessing view EXA_DBA_SYS_PRIVS` |

What each extra grant changed (each applied, read as the probe user and as `sys`, then revoked; after
all revokes the user's views were back at the baseline):

| Grant | The user sees it in | `sys` sees it in |
|---|---|---|
| `CREATE TABLE` to the user | `EXA_USER_SYS_PRIVS`, `EXA_SESSION_PRIVS` | `EXA_DBA_SYS_PRIVS` |
| `CREATE VIEW` to a nested role | `EXA_ROLE_SYS_PRIVS`, `EXA_SESSION_PRIVS` | `EXA_DBA_SYS_PRIVS` |
| `INSERT ON SCHEMA` to the role | `EXA_ROLE_OBJ_PRIVS`, `EXA_ALL_OBJ_PRIVS` (grantee = the role) | `EXA_DBA_OBJ_PRIVS` |
| `SELECT ON SCHEMA` to `PUBLIC` | `EXA_ROLE_OBJ_PRIVS`, `EXA_ALL_OBJ_PRIVS` (grantee = `PUBLIC`), and the schema's tables in `EXA_ALL_OBJECTS` | `EXA_DBA_OBJ_PRIVS` |
| `GRANT CONNECTION` to a role, a nested role, `PUBLIC` | `EXA_ROLE_CONNECTION_PRIVS` | `EXA_DBA_CONNECTION_PRIVS` |
| `GRANT ACCESS ON CONNECTION` (plain and `FOR SCRIPT`) to a role, `PUBLIC` | `EXA_ROLE_RESTRICTED_OBJ_PRIVS` | `EXA_DBA_RESTRICTED_OBJ_PRIVS` |
| `GRANT IMPERSONATION ON owner` to the **user** | `EXA_USER_IMPERSONATION_PRIVS` | `EXA_DBA_IMPERSONATION_PRIVS` |
| `GRANT IMPERSONATION ON owner` to a **role** or to `PUBLIC` | **nowhere**: `IMPERSONATE owner` succeeds for the user (`CURRENT_USER` = owner), yet no view of the user shows it | `EXA_DBA_IMPERSONATION_PRIVS` only |
| the user creates a schema and a table (`CREATE SCHEMA`, `CREATE TABLE`) | `EXA_USER_SCHEMAS`, `EXA_USER_OBJECTS`, `EXA_ALL_SCHEMAS`, `EXA_ALL_OBJECTS` | `EXA_DBA_SCHEMAS`, `EXA_DBA_OBJECTS` |
| the `DBA` role to a nested role | `EXA_ROLE_ROLE_PRIVS`; `EXA_SESSION_PRIVS` grows by every system privilege of `DBA` | `EXA_DBA_ROLE_PRIVS` |

**`PUBLIC` on a fresh deployment:** one system privilege, `USE ANY SCHEMA`; no object, role or connection
privilege. So `EXA_SESSION_PRIVS` of a user whose only own privilege is `CREATE SESSION` reads
`CREATE SESSION` and `USE ANY SCHEMA`. The start check must tolerate `USE ANY SCHEMA` when it comes
from `PUBLIC`, and nothing else.

**Consequences for decision [25] and `app/backend/security.py` (fixes the views; Q5):**
- Own views are enough for every route except one: use `EXA_SESSION_PRIVS` (effective system
  privileges), `EXA_ALL_OBJ_PRIVS` (effective object privileges, with the route in `GRANTEE`),
  `EXA_USER_ROLE_PRIVS` (direct roles), `EXA_USER_CONNECTION_PRIVS` + `EXA_ROLE_CONNECTION_PRIVS`,
  `EXA_USER_RESTRICTED_OBJ_PRIVS` + `EXA_ROLE_RESTRICTED_OBJ_PRIVS`, `EXA_USER_IMPERSONATION_PRIVS`,
  `EXA_USER_SCHEMAS` + `EXA_USER_OBJECTS`.
- **Impersonation through a role or `PUBLIC` is invisible to the user** and works. A start check that
  proves "may impersonate no one" therefore needs `EXA_DBA_IMPERSONATION_PRIVS` (rows whose `GRANTEE` is
  the user, one of its roles, or `PUBLIC`) on the operator path. With the operator path off the
  impersonation route is unverifiable: the start is refused unless `APP_SKIP_READ_CHECK=yes` (Q5's
  fallback, now narrowed to this one route). Everything else is still checked from the user's own views.
- No privilege beyond `CREATE SESSION` is needed to read any of the own-privilege views above.

**Passwords in statement text (review m2): masked.** With `ALTER SESSION SET PROFILE = 'ON'`, `CREATE USER
… IDENTIFIED BY '<token>'` and `ALTER USER … IDENTIFIED BY '<token>'` appear as
`… IDENTIFIED BY '<SECRET>'` in `EXA_STATISTICS.EXA_DBA_AUDIT_SQL` (auditing is on on this deployment:
about 200,000 rows), `EXA_DBA_PROFILE_LAST_DAY` and `EXA_USER_PROFILE_LAST_DAY`; 0 rows contain the token.
`make app-user` may keep using `IDENTIFIED BY {password!q}`.

## (e) `ALTER SESSION SET QUERY_TIMEOUT`: accepted; two caveats for the live session

Session default: `QUERY_TIMEOUT` 0 (off), `IDLE_TIMEOUT` 86400.

| Case | Result |
|---|---|
| `= 30`, CPU-bound SQL (a non-equi cross join) | aborted at **30.0 s**: `Query terminated because timeout has been reached.` |
| `= 30`, a Python UDF that sleeps 35 s | aborted, but at **35.2 s**, after the UDF returned: the timeout does not interrupt a running UDF |
| `= 300`, the same 35 s UDF in another session | finishes (35.2 s), returns 35 |
| pyexasol `socket_timeout` 30 and 10, 35 s UDF | **not cut**: both finished at 35.2 s (pyexasol 2.4.1) |

Consequences (fixes the pool's and the live session's timeouts; Q18):
- The pool's 30 s and the live session's 300 s `QUERY_TIMEOUT` are accepted statements and work for SQL.
  For statements that run a UDF (every virtual-schema fetch) the database ends the statement only when
  the UDF returns, so the fetch function's own limits (4 attempts of 30 s, 220 s in all) are what bound a
  live statement. The 300 s timeout stays correct as the outer limit, and the plan's reasoning holds
  ("the database does not cut off a response CFBD has already given").
- On this version the client socket timeout did not fire before the statement ended, so it is not the
  limiting factor either way; keep 330 s on the live session as designed.
