# S2 platform probe: findings

Task 1.4 of `add-cfbd-vs-foundation`. Probe script: `docs/spikes/s2_platform_probe.py`. Raw results: `cache/probe/s2-platform-probe.json` (gitignored).

- **Runtime:** Exasol Personal local, `2026.2.0-nano.3` (UDFs report `2026.2.0-dev.0`), podman rootless with `slirp4netns --disable-host-loopback`, 127.0.0.1:8563, 20 CPUs, 1 node.
- **Run:** 2026-10-21, as `sys`. The probe objects are owned by a probe user `PROBE_OWNER`; a second user `PROBE_READER` holds only `SELECT ON PROBE_VS`.
- **Safety:**
  - Only `PROBE_*` objects, plus a connection `PROBE_CONN` to `http://192.0.2.1:9` (TEST-NET-1, never contacted) with a generated dummy key.
  - BucketFS directories `probe_s2/` and `bfsdefault/default/probe_s2/`, and a 90-second sidecar container `probe-s2-sidecar`.
  - All of it was removed afterwards. Nothing named `PROBE%` is left in the DB, `bucketfs.conf` is back to the builtin SLC line, and no sidecar remains.
- **Zero CFBD calls.** (q) only resolves the host name and TCP-connects to `1.1.1.1:443`. The UDF's HTTP helper refuses every host except `127.0.0.1`.
- **Re-run:** `uv run python docs/spikes/s2_platform_probe.py [--only a,c,f] [--keep] [--crash-trigger]`. Item (a) always runs first and is the gate. `--only` merges results into the JSON.

## Summary

| Item | Result | What the plan does with it |
|---|---|---|
| (a) Python adapter + VS on nano 2026.2 | **Pass.** The adapter and VS are created in under 1 s, and adapter exceptions reach the client verbatim (also in `EXPLAIN VIRTUAL`) | Gate passed. Rule errors raised in the adapter are user-visible as written |
| (b) SLC Python and stdlib | **Pass.** Python 3.12.3, OpenSSL 3.0.13, CA bundle `/usr/lib/ssl/cert.pem` (121 CAs). `urllib`, `http.client`, `ssl`, `json`, `hashlib`, `zoneinfo`, `concurrent.futures` all import. `fromisoformat('…Z')` works. 20 CPUs, UDF uid 65534 | Stdlib-only runtime confirmed. No fallback needed |
| (c) Dynamic `EMITS` inside pushdown SQL | **Pass.** Works alone and inside the read-through `UNION ALL`. **Exasol requires the pushdown result types to equal the VS column types exactly**: narrower or wider `VARCHAR`/`DECIMAL` is rejected ("Adapter generated invalid pushdown query … Data type mismatch"). Column names may differ | 3.6: render `EMITS` and the stored branch from the same catalog types, and `CAST` every literal in the stored branch (e.g. `CAST('STORED' AS VARCHAR(8) UTF8)`). Use `TIMESTAMP`, which shows as `TIMESTAMP(3)` |
| (d) `FROM (VALUES …) AS R(…)` | **Pass.** Works directly and in pushdown SQL, up to 1,000 rows. String `VALUES` become `VARCHAR(max len) UTF8`, so there is no `CHAR` padding and slice keys reach the UDF unpadded | 3.6 uses `VALUES` as planned. No fallback needed |
| (e) Partial conjunct pushdown | **Pass, after a fix.** Exasol pushes only the supported conjuncts and evaluates the rest locally. A mixed `OR` is not pushed at all. **Exasol does NOT re-check pushed predicates**: rows that violate them come back. **The capability is `FN_PRED_LESSEQUAL`; the planned `…LESSEQUALS` was silently ignored**, so `<=`/`>=` were not pushed until it was corrected | 3.7 advertises `FN_PRED_LESSEQUAL`, and a unit test pins the capability list. 3.6 re-applies every pushed predicate in the outer `WHERE` (already planned; now proven mandatory). Shapes for 3.1 are listed below |
| (f) `NOT EXISTS` keeps stored slices away from the UDF | **Pass.** Over 9/9 runs (scalar, SET + `GROUP BY`, one-group SET) the marker UDF never saw a stored slice. Controls without `NOT EXISTS` raised the marker. Also true at 1,000 slices (500 stored) | Read-through shape confirmed. The `MINUS` fallback is not needed |
| (g) Pushdown runs with the owner's rights | **Pass.** A reader with only `SELECT ON PROBE_VS` ran the read-through query (stored table, `LOAD_LOG`, fetch UDF) and read the script-bound connection through the VS. Direct access to the core table, `LOAD_LOG`, the UDF or the connection script is denied. The reader may run `EXPLAIN VIRTUAL` | Key-protection and reader model confirmed on 2026.2. No role grants |
| (h) Aggregates over table `UNION ALL` UDF | **Pass.** `COUNT`, `SUM`, `GROUP BY` and `COUNT DISTINCT` over the read-through VS (scalar, SET, one-group SET), and a user-side `UNION ALL` with a VS branch: 8/8 correct, no crash. The 2026.1 crash trigger (aggregate capabilities) was not run (shared DB, see below) | Keep the no-aggregate-pushdown rule. The `ORDER BY FALSE` fallback is not needed |
| (i) Column comments in VS metadata | **Pass.** Comments appear verbatim in `EXA_ALL_COLUMNS.COLUMN_COMMENT` (quotes, newline, `/* */`, umlauts, an emoji outside the BMP). No comment gives NULL. Table comments are verbatim too. **Maximum 2,000 characters per comment: 2,001 is rejected** ("Maximum column comment length exceeded (2000)") and fails `CREATE VIRTUAL SCHEMA` | 2.3: truncate generated comments to at most 2,000 characters (description + rules + gating) and test the cap |
| (j) Leading `/*…*/` in `PUSHDOWN_SQL` | **Pass.** `/*PROBE {json}*/` survives verbatim, including a 1,000-slice manifest of 58 KB. The JSON round-trips once `/` is escaped as `\/`. `EXPLAIN VIRTUAL` columns are `PUSHDOWN_ID, PUSHDOWN_SQL, PUSHDOWN_JSON, PUSHDOWN_INVOLVED_TABLES`. `PUSHDOWN_JSON` holds the adapter request/response exchange. The CTE fallback also works | 3.6 keeps the comment manifest. Escape `/` as `\/` so `*/` can never occur inside it |
| (k) BucketFS on Personal | **Pass.** Host `…/runtime/exa/bucketfs/<service>/<bucket>/<path>` becomes UDF path `/buckets/<service>/<bucket>/<path>`. A new bucket is visible after about 1.0 s. New, changed or deleted files and nested directories in an existing bucket after 0.1–0.3 s. A 5 MB file after 0.1 s. Mode-0600 files are readable. Files directly under `<service>/` are invisible. `/buckets` is read-only for UDFs (EROFS) | **Default `REPLAY_PATH` = `/buckets/bfsdefault/default/cfbd/replay`**, host dir `~/.exasol/personal/deployments/local/local/runtime/exa/bucketfs/bfsdefault/default/cfbd/replay/` (the standard Exasol default-bucket name). 6.3 polls from a UDF after writing recordings (a new bucket appears in about 1 s, files in 0.1–0.3 s) |
| (l) UDF reaches a mock HTTP server by IP | **Pass (sidecar, Nico's decision).** A sidecar with `--network container:<db container>` bound to `127.0.0.1:<port>` answered the UDF with HTTP 200 in 42–53 ms and is not reachable from the host. The UDF sandbox shares the DB container's network namespace (`127.0.0.1:8563` connects). `10.0.2.2` (host loopback) is unreachable. Host IPs are reachable only for non-loopback listeners | 6.2: run the mock as a sidecar in the DB container's network namespace, and set `CFBD_API_MOCK` = `http://127.0.0.1:<port>`. Look up the DB container by image `exasol/nano` and port 8563 (its ID changes on restart; the name stayed `exasol-db-32903a10`). The fallback (bind to the WSL host IP) is not needed |
| (m) Scalar vs SET concurrency | **Negative for scalar.** Scalar `EMITS` over `VALUES` always runs in 1 VM, serially: 6 × 2 s gives 12 s, directly, via the VS and in the read-through shape. SET + `GROUP BY` slice runs in parallel but **depends on load**: 6 VMs (2.0 s) on an idle DB, 2 VMs (6.0 s) while the parallel spike plan was running. With 10 slices, at most 6 VMs. **One SET group with an in-UDF thread pool ran 10 slices fully in parallel in 2.01 s on 1 VM**, regardless of load | **Fallback adopted: `CFBD_FETCH` becomes `SET … EMITS`.** For the parallelism mechanism, the evidence favours one SET group (no `GROUP BY`) plus a bounded thread pool over `GROUP BY` slice. See "Decision for 3.6/4.4" |
| (n) `CREATE SCRIPT` with a body of about 2 MB | **Pass, with a hard limit.** **The script text is capped at 2,000,000 characters** ("size of script text exceeds VARCHAR(2000000)"). A UDF with a 1,990,157-byte body is created in 206 ms; first call 447 ms, then 247 ms; content intact. A 1.99 MB adapter works; `EXPLAIN VIRTUAL` median is 222 ms vs 156 ms for the 0.3 MB probe adapter | 5.1: the bundler fails if a script body is 1,990,000 characters or more. Monitor bundle size; if the catalog grows, use the planned gzip+base64 fallback |
| (o) `CREATE OR REPLACE` drops the script grant | **Confirmed on 2026.2.** The grant is script-bound (a second script by the same owner is denied). It is dropped by `CREATE OR REPLACE` even with **identical** text (the row disappears from `EXA_DBA_RESTRICTED_OBJ_PRIVS`). `SCRIPT_TEXT` is stored normalized: header `CREATE PYTHON3 SCALAR SCRIPT "READ_CONN" () …` without `OR REPLACE` or schema, trailing `/` removed. The body after `AS\n` is byte-identical | 5.3: compare the stored body (after the first ` AS\n`) with the generated body (without the trailing `/`), and replace only when they differ. `make grants` after every replace (already planned) |
| (p) Reserved keywords | **Pass.** 468 `EXA_SQL_KEYWORDS WHERE RESERVED` written to `vs/gen/exasol_reserved_keywords.txt` (one uppercase keyword per line, sorted, no header). Includes `DATE`, `ORDER`, `RESULT`, `LIMIT`, `YEAR`, `VALUE`; excludes `WEEK`, `SEASON`, `TEAM`, `NAME`. One entry contains a hyphen (`END-EXEC`) | Input for `naming.safe_column_name`. `P_YEAR` and the other `P_` names are safe |
| (q) Sandbox name resolution | **Confirmed.** `/etc/resolv.conf` and `/etc/hosts` exist but are empty (0 bytes). `nsswitch.conf` is present. `getaddrinfo('api.collegefootballdata.com')` and `getaddrinfo('localhost')` both fail with `gaierror -3`. IP literals resolve. TCP `1.1.1.1:443` connects in 17 ms | D21 resolver seam with the DoH fallback (task 4.2) as approved |

## Details for the implementation tasks

### (e) Filter JSON shapes on 2026.2 (for 3.1/3.2)

These shapes were observed with the corrected capability set below.

- `P_YEAR = 2024` gives `predicate_equal{left: column, right: literal_exactnumeric("2024")}`. Literal values are strings.
- Range and set operators:
  - `P_YEAR > 2018` arrives as `predicate_less` with the **operands swapped**: literal on the left, column on the right.
  - `>=` arrives as `predicate_lessequal`, swapped the same way.
  - `<` and `<=` arrive as-is.
  - `BETWEEN` gives `predicate_between{expression, left, right}`.
  - `IN (8, 9)` gives `predicate_in_constlist{expression, arguments}`. **`IN (8)` with one value becomes `predicate_equal`.**
- Negation and pattern operators:
  - `<>` gives `predicate_notequal`.
  - `NOT (…)` gives `predicate_not{expression}`.
  - `LIKE` gives `predicate_like{expression, pattern}`, plus `escapeChar` with `ESCAPE`.
  - `IS NULL` and `IS NOT NULL` give `predicate_is_null` and `predicate_is_not_null`.
  - `OR` gives `predicate_or{expressions}` and `AND` gives `predicate_and{expressions}`.
- Exasol normalizes some literals and expressions:
  - `2024.0`, `2.024E3` and `2020 + 4` all arrive as `literal_exactnumeric("2024")`.
  - `P_YEAR + 0 = 2024` arrives as `P_YEAR = 2024`.
  - `'2024'` compared with a DECIMAL column stays `literal_string("2024")`, which is a type mismatch the adapter must reject.
- **Contradictions are not folded:**
  - `P_YEAR = 2024 AND P_YEAR = 2025` and `BETWEEN 2021 AND 2019` arrive unchanged.
  - `P_YEAR = NULL` arrives as `literal_null`.
  - The adapter's contradiction check is needed as planned; treat `= NULL` as a contradiction.
- Boolean column forms:
  - A bare boolean column (`WHERE P_FLAG`) arrives as a `column` node used as the whole filter.
  - `NOT P_FLAG` arrives as `predicate_not{column}`.
- What the adapter can and cannot see:
  - `P_YEAR = P_WEEK` arrives with two `column` operands, so the adapter can reject it as non-literal.
  - `P_YEAR = (SELECT 2024 FROM DUAL)`, `UPPER(NAME) = 'B'`, and join conditions are **not pushed**: the filter is absent and Exasol evaluates them locally.
- **Gap to note (spec owner):** a non-pushed value predicate on an *optional* parameter, e.g. `P_WEEK = (SELECT 8 FROM DUAL)` together with `P_YEAR = 2024`:
  - The adapter never sees it, so it fetches without `week`.
  - The echo column is NULL for the unsent parameter, and Exasol's local filter returns 0 rows without any error.
  - Required parameters are safe: the rule rejects the missing value, which matches the spec's join scenario.
  - The adapter cannot detect this case.
- Request envelope:
  - `pushdownRequest` has `type: select`, `from: {type: table, name}`, `filter`, `selectListDataTypes` and `filter_expr_string_for_debug`. With no projection capability there is no `selectList`.
  - `involvedTables[].columns[]` carry `name` and `dataType`; `VARCHAR` has no `characterSet` there.
  - `schemaMetadataInfo` carries `{name, properties}`.
- **Unknown capability names are silently ignored** instead of raising an error. The only symptom is predicates evaluated locally, which with echo columns means silently empty results. 3.7 needs a unit test pinning the exact capability list, and 7.2 should assert that each advertised predicate arrives.

Corrected capability set as probed:
- Filters: `FILTER_EXPRESSIONS`.
- Predicates: `FN_PRED_AND`, `OR`, `NOT`, `EQUAL`, `NOTEQUAL`, `LESS`, `LESSEQUAL`, `BETWEEN`, `IN_CONSTLIST`, `LIKE`, `LIKE_ESCAPE`, `IS_NULL`, `IS_NOT_NULL`.
- Literals: `LITERAL_NULL`, `BOOL`, `DATE`, `TIMESTAMP`, `TIMESTAMP_UTC`, `DOUBLE`, `EXACTNUMERIC`, `STRING`.

### (m) Decision for 3.6/4.4: fetch concurrency

Measured with 6 slices × 2 s sleep, unless stated. Each variant ran 1–3 times; together the runs span idle and loaded DB states.

| Shape | Wall (span of UDF intervals) | Parallel VMs |
|---|---|---|
| scalar `EMITS` (direct, via VS, read-through) | 12.0 s every run | 1 |
| SET `EMITS` + `GROUP BY` slice (direct, via VS, read-through) | 2.0 s idle; 2.2–6.1 s under load | 6 idle; 2–6 under load, mostly 2 |
| SET + `GROUP BY` slice, 10 slices, read-through via VS | 4.0 s idle; 9.2–10.0 s under load | 6 at most; 2–4 under load |
| one SET group (no `GROUP BY`) + `ThreadPoolExecutor(10)`, 10 slices, read-through via VS | 2.01 s | 1 VM, 10 threads |

**Adopted (plan fallback):** `CFBD_FETCH` changes from a scalar to a `SET … EMITS` script, because scalar `EMITS` serializes.

**Recommendation, for the 3.6/4.4 owner to confirm:** call it with no `GROUP BY`, so one instance receives every planned slice, and fetch them with a bounded thread pool (for example `max_workers = min(slices, 10)`). Emit from the main thread as futures complete, with `REQ_AT`/`REQ_MS` measured per thread.

- **For:** concurrency is deterministic and bounded (useful for rate limits), it is independent of Exasol's load-adaptive VM count, and it is cheaper (one VM start).
- **Why not `GROUP BY` slice:** under the parallel spike's load it reached only 2 VMs. Six slices then took 6.0 s, exactly half of the 12 s summed latency, so the fan-out SHOULD ("less than half") would fail.
- **Cost:** all responses of one query sit in one VM's memory (the budget caps this at `MAX_CALLS_PER_QUERY` slices). Retry/backoff runs per thread.
- **Safe for read-through:** (f) and (h) pass for the one-group form, so stored slices never reach it and aggregates over it are correct.

### (k) BucketFS layout (for 4.3, 6.3, 9.1)

- `bucketfs.conf` lines are written by the reconciler (about 1 s): `/exa/bucketfs/<svc>/<bkt> <svc> <bkt> /buckets/<svc>/<bkt> <password> P`. Removing the directory removes the line within about 1 s.
- Only directories at depth 2 (`<service>/<bucket>`) become buckets. Recordings go below a bucket, for example `bfsdefault/default/cfbd/replay/<endpoint segments>/<sha256(slice)[:24]>.cfbd.json`.
- `bfsdefault/default` did not exist on this Personal before the probe. Deploy, or the first recording, creates it.

### (n) Script size (for 5.1)

- The limit is on the stored script text: header plus body, at most 2,000,000 characters. pyexasol sends the statement fine.
- The adapter parses its embedded spec on every call. With a 1.99 MB body, one `EXPLAIN VIRTUAL` adds about 65 ms.

### (o) Script text normalization (for 5.3)

- Sent: `CREATE OR REPLACE PYTHON3 SCALAR SCRIPT PROBE_INFRA.READ_CONN() RETURNS VARCHAR(200) UTF8 AS\n<body>\n/`
- Stored: `CREATE PYTHON3 SCALAR SCRIPT "READ_CONN" () RETURNS VARCHAR(200) UTF8 AS\n<body>\n`
- Deploy compares the body only: the stored header is normalized (quoted name, no schema, no `OR REPLACE`, reformatted parameter list), so comparing it to the sent header always differs.
- The `CFBD_FETCH` signature is fixed by the plan, so changing it is a deliberate redeploy (for example a `--force` replace followed by `make grants`).

### (h) Crash trigger not run

The 2026.1 regression (aggregate pushed into a `UNION ALL` branch; trigger capabilities `SELECTLIST_EXPRESSIONS` + `AGGREGATE_SINGLE_GROUP` + `FN_AGG_COUNT_STAR`) is available behind `--crash-trigger`.

- It was not run: a session SIGABRT on the shared DB could disturb the parallel `add-engine-spikes` timed windows.
- With the planned filter-only capabilities, nothing crashes on 2026.2.
- Run it on an idle DB to learn whether 2026.2 fixed the regression (useful for Nico's bug report, not for this plan).

### (l) Sidecar notes (for 6.2)

- Probed image: the local `script-language-container:…python-3.12…` image (`--entrypoint python3`), so no pull was needed. Any image with Python 3 works.
- `python3` as PID 1 ignores SIGTERM, so `podman stop` waits 10 s. Use `--init`, handle SIGTERM, or stop with `podman rm -f`.
- `--network container:<db>` shares the network namespace of the *running* container. After `exasol stop/start -d local` the sidecar must be restarted.
- The DB's own ports are in the same namespace. Pick a port outside Exasol's range (the probe used 40000–49999).
