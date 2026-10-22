# Feature: App access: the read path, the operator path and browser safety

The app reads its pages as a database user that can only read the serving schemas, so a bug in a page cannot change data, start a run or reach the CFBD key. The few actions that must write, or that query the virtual schema directly, run on a separate operator path: the namespace owner, a fixed list of actions, and only when the server listens on the presenter's own machine. The browser never sees a password.

## Background

- **Read path.** Every query a page runs goes through the app user, a database user that holds only `CREATE SESSION` and the role `CFB_APP` (`SELECT` on the schemas `CFB_API` and `CFB_API_LIVE`, `api/roles`).
  - The user's name comes from `CFB_APP_USER` (default `CFB_APP_WEB`) and its password from `CFB_APP_PASSWORD`, in the environment or in `.env`, which git ignores. The password is never committed, logged, or sent to the browser. Host, port and TLS come from `TARGET` through `vs/host/target.py`.
  - The page SQL names only objects of `CFB_API` and `CFB_API_LIVE`.
  - Read sessions use autocommit, as the page contract asks (`api/serving-contract`).
- **Operator path.** The owner of the namespace runs the actions that write or that read the virtual schema directly. On the real deployment that owner is `sys`: `vs/host/target.py` reads its password from the deployment's secrets file when it connects and never stores it. In the integration-test namespace the owner is `CFB_TEST_OWNER`.
  - Each action opens its own connection and closes it at the end.
  - The allowlist is fixed, and nothing else runs on this path:
    - planning and running a weekly refresh (`app/refresh`);
    - writing the fetch-log rows of the app's own requests (`app/live-spend`);
    - explaining and running the curated virtual-schema queries of the inspector's console (`app/inspector`).
  - **Why it has to be the owner.** Exasol Lua scripts run with the rights of the user who calls them, not with their owner's rights. So a user holding only `EXECUTE` on `REFRESH_WEEK` cannot write any table the refresh writes, and a user that can write them all is an owner in everything but name. Views are different: they read with their owner's rights, which is why the read path needs only the serving schemas. Probe s9 (a) confirms the script rule on this deployment's version (`plan.md`).
- **When the operator path is on.** `APP_OPERATOR` is `auto` (the default), `on` or `off`:
  - `auto`: on when the server binds a loopback address (`127.0.0.1`, `::1` or `localhost`), off otherwise;
  - `on`: on, and the server refuses to start on a non-loopback address;
  - `off`: never on.

  With the operator path off, the app is read-only:
  - there is no refresh, no console, and no live CFBD request, because a live request could not be logged (`app/live-spend`);
  - replayed, stored and cached answers still work.

  `GET /api/config` reports whether the operator path is on, and why.
- **Binding.** The server listens on `127.0.0.1:8000` unless `APP_HOST` and `APP_PORT` say otherwise. It runs as one worker process.
- **Browser safety.**
  - Every `POST` must carry the header `X-CFB-Action: 1` and an `Origin` that is either the app's own origin or one listed in `APP_CORS_ORIGINS`. Any other request is refused with status 403 before any database work.
  - Cross-origin requests are allowed only from the origins in `APP_CORS_ORIGINS`. It is empty by default; the development setup lists the Vite dev server. The value `*` is refused at start.
  - The app identifies a browser session by an anonymous random cookie (`HttpOnly`, `SameSite=Strict`). The cookie grants nothing; it only scopes the preview cache and the inspector's query log.
- **The read user is checked at start.** The backend connects as the read user and refuses to start, naming the user and the reason, when:
  - that user is `sys` or the namespace owner;
  - it holds any system privilege other than `CREATE SESSION`;
  - it holds any role other than the app role.
- **`make app-user`** runs on the operator path and creates the read user if it is missing:
  - `CREATE USER <name> IDENTIFIED BY <password>`, with the password read from `CFB_APP_PASSWORD`;
  - `GRANT CREATE SESSION` and `GRANT CFB_APP` to the user.

  It is idempotent, and it never prints or logs the password. `ROTATE=yes` sets a new password on an existing user. It refuses when the role is missing (run `make grants` first) or when no password is set.
- The examples run on the app test stack (`plan.md`, Key Interfaces): the integration-test namespace with the sim league, the read user `CFB_TEST_APP` with the role `TEST_CFB_APP`, `CFB_TEST_OWNER` as the operator, and the mock CFBD API.

## Scenarios

### Scenario: The backend refuses a read user that can do more than read

* *GIVEN* the app test stack
* *WHEN* the backend is started with `CFB_APP_USER` set to `CFB_TEST_OWNER`, and then with a user that holds `CREATE SESSION`, `TEST_CFB_APP` and `CREATE TABLE`
* *THEN* both starts MUST fail with a message that names the user and the reason (the namespace owner; the system privilege `CREATE TABLE`)
* *AND* the server MUST NOT accept any HTTP request

### Scenario: Every page reads through the app role

* *GIVEN* the backend on the app test stack with the read user `CFB_TEST_APP`
* *WHEN* every read endpoint of the four pages, the as-of picker and the timeline is called
* *THEN* every call MUST answer with status 200
* *AND* every statement in the inspector's query log MUST have run as `CFB_TEST_APP` and MUST name only objects of `TEST_CFB_API` and `TEST_CFB_API_LIVE`

### Scenario: The operator path follows the bind address

* *GIVEN* the app test stack
* *WHEN* the backend is started with `APP_OPERATOR=auto` on `127.0.0.1`, then with `APP_OPERATOR=auto` on `0.0.0.0`, then with `APP_OPERATOR=on` on `0.0.0.0`
* *THEN* the first MUST report the operator path on in `GET /api/config`
* *AND* the second MUST report it off with the reason "bound to a non-loopback address", and MUST answer `POST /api/refresh/plan` with status 403 naming that reason
* *AND* the third MUST refuse to start, naming `APP_OPERATOR` and the address

### Scenario: A request from another origin cannot act

* *GIVEN* the backend on the app test stack in live mode against the mock API
* *WHEN* a client posts a preview request for a week-10 game without the header `X-CFB-Action`, and then with the header but with `Origin: http://attacker.example`
* *THEN* both requests MUST be refused with status 403
* *AND* the mock MUST NOT receive any request, and the ledger and `TEST_CFB_META.FETCH_LOG` MUST NOT change

### Scenario: No secret reaches the browser

* *GIVEN* the backend on the app test stack, the passwords of `CFB_TEST_APP` and `CFB_TEST_OWNER`, the test connection's marker key, and a built frontend
* *WHEN* every API endpoint is called, including calls that fail (an unknown game id, a refused refresh, an unreachable database), and every file of the built frontend is read
* *THEN* no response body, no response header and no file MUST contain either password or the marker key

### Scenario: make app-user creates the read user once

* *GIVEN* the integration-test namespace with the role `TEST_CFB_APP`, and a password for a new user in `CFB_APP_PASSWORD`
* *WHEN* the owner runs `make app-user ARGS="--prefix TEST_ --user CFB_TEST_APP2"` twice, and once more with `CFB_APP_PASSWORD` unset for another new user
* *THEN* after the first run `CFB_TEST_APP2` MUST exist with exactly `CREATE SESSION` and the role `TEST_CFB_APP`, and MUST be able to log in with that password
* *AND* the second run MUST report no change
* *AND* the third run MUST refuse, naming `CFB_APP_PASSWORD`, and MUST NOT create a user
* *AND* the output of all three runs MUST NOT contain the password
