# Feature: API key protection

The CFBD key lives only in the Exasol connection `CFBD_API`. Users of the virtual schema need no rights on the connection and can never see the key: not in SQL, metadata, results, errors or logs. The repo can be public and the schema shared without exposing the key (D2, D18). On the real deployment `sys` owns everything and can read every connection, so "only the fetch UDF can use the key" holds for readers, not for the owner ([20]). The dedicated-owner model, where only the fetch UDF can read the key, is kept and tested in the `TEST_` namespace.

## Background

- A DBA creates `CFBD_API` with plain SQL (`docs/user-guide.md`). No tool handles the key, and `ACCESS ANY CONNECTION` is never granted.
- Real deployment ([20]): `sys` owns the schemas, the adapter, the fetch UDF and the virtual schema. There is no `CFB_OWNER` user and no owner password, and no script-bound grant. `make grants` creates the role `CFB_READER` with only `SELECT` on `CFBD_VS`; readers hold nothing else. Later phases expose data to apps through `CFB_API` views.
- `TEST_` namespace and any dedicated-owner namespace: `CFB_TEST_OWNER` owns the objects and the connection is bound to the fetch UDF with `GRANT ACCESS ON CONNECTION CFBD_API_MOCK FOR SCRIPT TEST_CFB_INFRA.CFBD_FETCH TO CFB_TEST_OWNER`.
- In a dedicated-owner namespace, `CREATE OR REPLACE` of the fetch UDF drops the script-bound grant. A DBA re-grants it after every redeploy that changes the fetch UDF (`make grants`). `make deploy` replaces the fetch UDF only when its body changed.
- The privilege model was verified on 2026.1.0 (S0). This feature re-verifies it on the target runtime, Exasol Personal 2026.2.
- The tests use a dedicated test connection with a marker key, never `CFBD_API`.
- Redaction ([23]): response bodies lose only the key itself, raw, JSON-escaped and percent-encoded, so `Bearer <key>` becomes `Bearer [REDACTED]` while data such as "the standard bearer Smith" is kept. Error messages also lose any `Bearer <token>`, because they can echo request headers.

## Scenarios

### Scenario: Only the fetch UDF can read the key (`TEST_` namespace)

This scenario is scoped to the `TEST_` namespace, the dedicated-owner model. On the real deployment `sys` owns every script and can read every connection, so a second `sys` script could read the key ([20]); the guarantee there is limited to readers (next scenario).

* *GIVEN* the `TEST_` namespace, owned by `CFB_TEST_OWNER`, with the script-bound grant for its fetch UDF
* *WHEN* another script owned by `CFB_TEST_OWNER` calls `exa.get_connection` for the connection
* *THEN* the call MUST fail with an access error
* *AND* the fetch UDF's live queries MUST keep working

### Scenario: Readers query without any connection privilege

* *GIVEN* a reader with only `SELECT` on `CFBD_VS`
* *WHEN* the reader queries a virtual table in live mode
* *THEN* the query MUST return rows
* *AND* the reader's direct call of `CFB_INFRA.CFBD_FETCH` MUST be denied

### Scenario: The key never appears in SQL, metadata or results

* *GIVEN* a connection whose key is a unique marker string
* *WHEN* queries, `EXPLAIN VIRTUAL` and metadata reads run as the reader and as the owner
* *THEN* the marker MUST NOT appear in the virtual schema properties, in any pushdown SQL, in any `REQ_*` value or in any result value

### Scenario: The key is redacted from errors and response bodies

* *GIVEN* the mock API answers one slice with HTTP 500 and a body that echoes the received `Authorization` header
* *AND* answers another slice with HTTP 200 and a string field that contains the key
* *WHEN* the user queries both slices separately
* *THEN* the error message of the first query MUST NOT contain the key and MUST show a redaction placeholder in its place
* *AND* the value emitted for the second slice MUST NOT contain the key

### Scenario: The key is only sent to its own connection's address

* *GIVEN* a connection whose address is the mock API
* *WHEN* the user filters a parameter with a value such as `http://other-host/` or `../../other-path`
* *THEN* every request MUST go to the connection's address, with the value percent-encoded inside the query string
* *AND* no property, filter value or slice key SHALL be able to change the host the key is sent to

### Scenario: A redeployed fetch UDF needs the grant again (`TEST_` namespace)

This scenario is scoped to the dedicated-owner model of the `TEST_` namespace. The real deployment has no script-bound grant ([20]).

* *GIVEN* the `TEST_` fetch UDF is replaced with `CREATE OR REPLACE`
* *WHEN* the user queries a virtual table in live mode before the DBA re-grants script access
* *THEN* the query MUST fail with an error naming the connection and stating that the fetch UDF has no access to it
* *AND* the error MUST NOT contain the key
* *AND* after the DBA re-grants script access, the same query MUST succeed
