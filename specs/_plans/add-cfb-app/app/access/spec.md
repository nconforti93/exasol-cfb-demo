# Feature: App access: the read path, the console path, the operator path and browser safety

The app reads its pages as a database user that can only read the serving schemas, so a bug in a page cannot change data, start a run or reach the CFBD key. The inspector's console queries the virtual schema as a second user that can only read it. The few actions that must write run on a separate operator path: the namespace owner, a fixed list of actions, and only when the server listens on the presenter's own machine. Every request must name the app's own host, and every action must come from the app's own origin, so a page on another site cannot reach the app, not even through DNS rebinding. The browser never sees a password.

## Background

- **Read path.** Every query a page runs goes through the app user, a database user that holds only `CREATE SESSION` and the role `CFB_APP` (`SELECT` on the schemas `CFB_API` and `CFB_API_LIVE`, `api/roles`).
  - The user's name comes from `CFB_APP_USER` (default `CFB_APP_WEB`) and its password from `CFB_APP_PASSWORD`, in the environment or in `.env`, which git ignores. The password is never committed, logged, or sent to the browser. Host, port and TLS come from `TARGET` through `vs/host/target.py`.
  - The page SQL names only objects of `CFB_API` and `CFB_API_LIVE`. The only statement that names `CFB_API_LIVE.GAME_PREVIEW` is the preview statement of the spending guard (`app/live-spend`); a lint fails on any other.
  - Read sessions use autocommit, as the page contract asks (`api/serving-contract`).
- **Console path.** The curated queries of the inspector's console (`app/inspector`) run as the console user, a database user that holds only `CREATE SESSION` and the role `CFB_READER` (`SELECT` on the virtual schema `CFBD_VS`, created by `make grants`).
  - Its name comes from `CFB_CONSOLE_USER` (default `CFB_CONSOLE_WEB`) and its password from `CFB_CONSOLE_PASSWORD`, with the same rules as the read user's password.
  - Without a console user the console is off, and `GET /api/config` says why.
  - In the integration-test namespace the console user is `CFB_TEST_READER`, which holds only `CREATE SESSION` and `SELECT` on `TEST_CFBD_VS`.
- **Operator path.** The owner of the namespace runs the actions that write. On the real deployment that owner is `sys`: `vs/host/target.py` reads its password from the deployment's secrets file when it connects and never stores it. In the integration-test namespace the owner is `CFB_TEST_OWNER`.
  - Each action opens its own connection and closes it at the end.
  - The allowlist is fixed, and nothing else runs on this path:
    - planning and running a weekly refresh (`app/refresh`);
    - writing the fetch-log rows of the app's own requests (`app/live-spend`);
    - reconciling finished loader runs with the ledger (`app/live-spend`);
    - checking the effective privileges of the read and console users, when they cannot read their own (below);
    - `make app-user`.
  - **Why it has to be the owner.** Exasol Lua scripts run with the rights of the user who calls them, not with their owner's rights. So a user holding only `EXECUTE` on `REFRESH_WEEK` cannot write any table the refresh writes, and a user that can write them all is an owner in everything but name. Views are different: they read with their owner's rights, which is why the read path needs only the serving schemas. Probe s9 (a) confirms the script rule on this deployment's version (`plan.md`).
- **When the operator path is on.** `APP_OPERATOR` is `auto` (the default), `on` or `off`:
  - `auto`: on when the server's bind address is loopback, off otherwise;
  - `on`: on, and the server refuses to start on a bind address that is not loopback;
  - `off`: never on.

  The bind address `APP_HOST` is resolved, and it counts as loopback only when **every** address it resolves to is a loopback address. A name such as `localhost` is not trusted by its spelling.

  With the operator path off, the app is read-only:
  - there is no refresh, no console, and no live CFBD request, because a live request could not be logged (`app/live-spend`);
  - replayed, stored and cached answers still work.

  `GET /api/config` reports whether the operator path is on, and why.
- **Binding.** The server listens on `127.0.0.1:8000` unless `APP_HOST` and `APP_PORT` say otherwise. It runs as one worker process.
- **Browser safety.** Each check runs before routing and before any database work.
  - **Host.** Every request, `GET` included, must carry a `Host` header from a fixed allowlist: `127.0.0.1:<port>`, `localhost:<port>` and `[::1]:<port>` for the app's own port, the Vite dev server's `127.0.0.1:5173` and `localhost:5173` in development only, and any `host:port` listed in `APP_ALLOWED_HOSTS` (empty by default; `*` is refused at start). Name and port must both match. Any other request is refused with status 400. This is the defence against DNS rebinding, where a foreign page re-resolves its own name to `127.0.0.1` and the browser then treats the app as same-origin.
  - **Origin.** Every `POST` must carry the header `X-CFB-Action: 1`, the content type `application/json`, and an `Origin` from a fixed set: `http://` plus each allowlisted loopback `host:port`, plus the origins listed in `APP_CORS_ORIGINS`. The set comes from configuration and is never derived from the request's `Host`. A `POST` without an `Origin`, or with any other, is refused with status 403.
  - **CORS.** Cross-origin requests are allowed only from the origins in `APP_CORS_ORIGINS`. It is empty by default; the development setup lists the Vite dev server. The value `*` is refused at start.
  - **The development server.** `make app` runs Vite bound to `127.0.0.1` with a fixed port (`strictPort`), its own host check left on, and an `/api` proxy that keeps the browser's `Host` (`changeOrigin: false`), so the backend's host check still applies. The launcher refuses an option that rebinds Vite to another address.
  - **Session.** The app identifies a browser session by an anonymous random cookie (`HttpOnly`, `SameSite=Strict`). The cookie grants nothing; it only scopes the preview cache, the inspector's query log and the in-flight requests.
- **The users are checked at start.** The backend reads the effective privileges of the read user and of the console user: the privileges granted to the user directly, through its roles and their roles in turn, and to `PUBLIC`; its connection privileges (`GRANT CONNECTION` and `GRANT ACCESS ON CONNECTION`); its impersonation privileges; and the objects and schemas it owns. It refuses to start, naming the user, the privilege and where it comes from, unless:
  - its system privileges are exactly `CREATE SESSION`;
  - its object privileges are exactly `SELECT` on `CFB_API` and `CFB_API_LIVE` for the read user, or exactly `SELECT` on the virtual schema for the console user;
  - it owns no object or schema, holds no connection privilege, and may impersonate no one;
  - it is neither `sys` nor the namespace owner.

  Each user reads its own privileges where it can (probe s9 (d)). Where it cannot, the check runs on the operator path against the database-wide views. With the operator path off, such a start is refused unless `APP_SKIP_READ_CHECK=yes` is set; the app then starts, and `GET /api/config` and the page header warn that the check was skipped.
- **What read-only does not mean.** The read user can run `CFB_API_LIVE.GAME_PREVIEW` from any SQL client with its password, and the console user any query of the virtual schema, and each such query can spend calls outside the ledger. The ledger guards the app's spend, not the roles'. The database's own monthly budget (`loader/quota`) caps every client. The user guide says so.
- **`make app-user`** runs on the operator path:
  - it creates the read user if it is missing: `CREATE USER <name> IDENTIFIED BY <password>` with the password read from `CFB_APP_PASSWORD`, then `GRANT CREATE SESSION` and `GRANT CFB_APP`;
  - when `CFB_CONSOLE_PASSWORD` is set, it creates the console user the same way, with `CREATE SESSION` and `CFB_READER`.

  It is idempotent, and it never prints or logs a password. `ROTATE=yes` sets a new password on an existing user. It refuses when a role is missing (run `make grants` first) or when no password is set for the read user.
- The examples run on the app test stack (`plan.md`, Key Interfaces): the integration-test namespace with the sim league, the read user `CFB_TEST_APP` with the role `TEST_CFB_APP`, the console user `CFB_TEST_READER`, `CFB_TEST_OWNER` as the operator, and the mock CFBD API.

## Scenarios

### Scenario: The backend refuses a read user that can do more than read

* *GIVEN* the app test stack
* *WHEN* the backend is started with `CFB_APP_USER` set to `CFB_TEST_OWNER`, and then with a user that holds `CREATE SESSION`, `TEST_CFB_APP` and `CREATE TABLE`
* *THEN* both starts MUST fail with a message that names the user and the reason (the namespace owner; the system privilege `CREATE TABLE`)
* *AND* the server MUST NOT accept any HTTP request

### Scenario: A privilege that reaches the read user through its role, PUBLIC or a connection is refused

* *GIVEN* the app test stack, whose read user `CFB_TEST_APP` holds only `CREATE SESSION` and the role `TEST_CFB_APP`
* *WHEN* the backend is started three times, each time after one grant that is revoked again afterwards: `GRANT INSERT ON SCHEMA TEST_CFB_META TO TEST_CFB_APP`, then `GRANT SELECT ON SCHEMA TEST_CFB_SIM TO PUBLIC`, then `GRANT ACCESS ON CONNECTION` on the test namespace's CFBD connection `TO TEST_CFB_APP`
* *THEN* each start MUST fail with a message that names `CFB_TEST_APP`, the privilege, and where it comes from (the role `TEST_CFB_APP`, `PUBLIC`, the connection)
* *AND* after the last revoke the backend MUST start

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

### Scenario: A rebinding attempt with a foreign Host is refused

* *GIVEN* the backend on the app test stack on `127.0.0.1`, in live mode against the mock API
* *WHEN* a client sends `GET /api/config`, and then a valid preview request with `X-CFB-Action: 1`, both with `Host: evil.example:8000` and `Origin: http://evil.example:8000`
* *THEN* both requests MUST be refused with status 400
* *AND* the mock MUST NOT receive any request, and the ledger, the inspector's query log and `TEST_CFB_META.FETCH_LOG` MUST NOT change

### Scenario: A cross-origin POST cannot act

* *GIVEN* the backend on the app test stack in live mode against the mock API
* *WHEN* a client posts a preview request with an allowed `Host` three times: without the header `X-CFB-Action`, then with the header and `Origin: http://attacker.example`, then with the header and no `Origin` at all
* *THEN* all three requests MUST be refused with status 403
* *AND* the mock MUST NOT receive any request, and the ledger and `TEST_CFB_META.FETCH_LOG` MUST NOT change

### Scenario: No secret reaches the browser

* *GIVEN* the backend on the app test stack, the passwords of `CFB_TEST_APP`, `CFB_TEST_READER` and `CFB_TEST_OWNER`, the test connection's marker key, and a built frontend
* *WHEN* every API endpoint is called, including calls that fail (an unknown pair, a refused refresh, an unreachable database), and every file of the built frontend is read
* *THEN* no response body, no response header and no file MUST contain any of the passwords or the marker key

### Scenario: make app-user creates the read user once

* *GIVEN* the integration-test namespace with the role `TEST_CFB_APP`, and a password for a new user in `CFB_APP_PASSWORD`
* *WHEN* the owner runs `make app-user ARGS="--prefix TEST_ --user CFB_TEST_APP2"` twice, and once more with `CFB_APP_PASSWORD` unset for another new user
* *THEN* after the first run `CFB_TEST_APP2` MUST exist with exactly `CREATE SESSION` and the role `TEST_CFB_APP`, and MUST be able to log in with that password
* *AND* the second run MUST report no change
* *AND* the third run MUST refuse, naming `CFB_APP_PASSWORD`, and MUST NOT create a user
* *AND* the output of all three runs MUST NOT contain the password
