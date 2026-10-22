# Feature: Fetch modes, retries and recordings

The fetch UDF turns one planned request into rows. Live mode calls CFBD over HTTPS with the key from the `CFBD_API` connection and handles rate limits and transient failures. Replay mode serves recorded responses from BucketFS without network or key. That makes the demo work offline with the same look as live, and lets every test run at zero API calls.

## Background

- The virtual schema property `FETCH_MODE` is `live` (default) or `replay`. A change takes effect on the next query.
- **Live mode:**
  - The fetch UDF reads the connection named by the property `CONNECTION_NAME` (default `CFBD_API`). The base URL is the connection's address and the key is its password. Both always come from the same connection object.
  - Each planned request is one HTTPS `GET <address><slice key>` with the header `Authorization: Bearer <key>`.
- **Name resolution (D21):**
  - Host names are resolved through one resolver seam.
  - The system resolver is tried first. Only on a resolution error (`gaierror`) does the seam fall back to DNS over HTTPS: an HTTPS `GET <DOH_URL>?name=<host>&type=A` with `Accept: application/dns-json`.
  - The A records are cached for the lifetime of the UDF instance, and the connection then uses one of them.
  - The virtual schema property `DOH_URL` sets the DoH endpoint. The default is `https://1.1.1.1/dns-query`; the value must be an `https` URL whose host is an IP literal. `off` disables the fallback.
  - The DoH request verifies the endpoint's TLS certificate. It carries only the host name, never the key or any request data.
  - TLS server-name indication and certificate checks of the API connection always use the connection's host name, even when DoH supplied the address.
  - Background: on Exasol Personal nano 2026.2 the UDF sandbox has an empty `/etc/resolv.conf` and `/etc/hosts`, so the system resolver fails for every host name (verified 2026-10-21). IP literals need no resolution.
- **Retries:**
  - HTTP 429, HTTP 5xx, timeouts and connection errors are retried, up to 4 attempts in total. The waits grow (about 1, 2 and 4 seconds, with jitter). A `Retry-After` header of up to 30 seconds is honored instead.
  - Other 4xx statuses and name resolution failures are not retried.
  - A request that still fails fails the whole query. Calls that sibling requests already made are not refunded.
  - The first failure stops the query's other requests at once: none of them starts a request, a retry or a retry wait afterwards. Requests already sent finish. The error raised is that first failure.
- **Replay mode:**
  - Responses come from recordings under the property `REPLAY_PATH` (a BucketFS path readable by UDFs), one file per slice key.
  - Replay never reads the connection and never opens a network connection.
  - A recording holds the slice key, HTTP status, attempts, latency, request time and the raw response body ([24]). Recordings contain CFBD data: they live only in the gitignored `cache/` and in BucketFS, never in git.
  - Replayed rows carry the recorded status, attempts, latency and request time. A recording written before attempts were stored replays with `REQ_ATTEMPTS` NULL.
- **Recording:**
  - The owner records a slice by calling the fetch UDF directly with its raw output. This emits the raw response body in ordered parts of at most 2,000,000 characters, plus status, attempts, latency and request time.
  - Readers of the virtual schema cannot do this.
  - Recording is a live call, and the recording tool budgets it.

## Scenarios

### Scenario: Live mode sends one authenticated GET per planned request

* *GIVEN* a virtual schema in live mode whose connection points to the mock API, by IP address, with a known test key
* *WHEN* the user queries `SELECT * FROM CFBD_VS.GAMES WHERE P_YEAR IN (2019, 2020)`
* *THEN* the mock API MUST receive exactly two `GET` requests, one per planned slice key, at the connection's address
* *AND* each request MUST carry the header `Authorization: Bearer <test key>`
* *AND* every row MUST have `REQ_SOURCE = 'LIVE'`

### Scenario: Rate limits and server errors are retried

* *GIVEN* the mock API answers a slice first with HTTP 429 and `Retry-After: 0`, then with 200
* *AND* answers a second slice twice with HTTP 503, then with 200
* *WHEN* the user queries both slices
* *THEN* the query MUST return the rows of both slices
* *AND* the rows of the first slice MUST have `REQ_ATTEMPTS = 2` and those of the second slice `REQ_ATTEMPTS = 3`

### Scenario: Persistent failures fail the query after four attempts

* *GIVEN* the mock API always answers a slice with HTTP 500
* *WHEN* the user queries that slice
* *THEN* the mock API MUST receive exactly 4 requests for it
* *AND* the query MUST fail with an error naming the table, the slice key and the status 500

### Scenario: Client errors are not retried

* *GIVEN* the mock API answers a slice with HTTP 400 and an error message in the body
* *WHEN* the user queries that slice
* *THEN* the mock API MUST receive exactly 1 request
* *AND* the query MUST fail with an error naming the slice key, the status 400 and the API's message

### Scenario: The system resolver is used when it succeeds

* *GIVEN* a virtual schema in live mode whose connection addresses the mock API by IP literal
* *AND* `DOH_URL` points to a mock DoH endpoint that logs every connection
* *WHEN* the user queries a virtual table with a valid filter
* *THEN* the query MUST return rows
* *AND* the mock DoH endpoint MUST receive no connection

### Scenario: DNS over HTTPS supplies the address when the system resolver fails

* *GIVEN* the UDF sandbox's system resolver cannot resolve `api.collegefootballdata.com`
* *AND* `DOH_URL` has its default value
* *WHEN* the fetch UDF's resolver resolves that host and opens a TLS connection to it on port 443, as `make dns-check` does, without sending an HTTP request
* *THEN* the resolver MUST obtain the A records from the DoH endpoint
* *AND* the TLS handshake MUST succeed with the certificate verified against `api.collegefootballdata.com`
* *AND* a second resolution of the same host in the same UDF instance MUST be answered from the cache without another DoH request

### Scenario: DoH failures are reported clearly

* *GIVEN* a virtual schema in live mode whose connection address uses a host name the system resolver cannot resolve
* *AND* the DoH lookup fails: its endpoint is unreachable, its TLS certificate does not verify, or it returns no A record
* *WHEN* the user queries a virtual table with a valid filter
* *THEN* the query MUST fail after a single attempt, with an error naming the host, the `DOH_URL` value and the DoH failure reason
* *AND* the error MUST point to `make dns-check`
* *AND* the error MUST NOT contain the key

### Scenario: The DoH fallback can be switched off

* *GIVEN* `DOH_URL = 'off'`
* *AND* a virtual schema in live mode whose connection address uses a host name the system resolver cannot resolve
* *WHEN* the user queries a virtual table with a valid filter
* *THEN* the query MUST fail after a single attempt with the system resolver's original error, naming the host and stating that the DoH fallback is off
* *AND* no DoH request SHALL be made

### Scenario: Replay mode serves recordings without network or key

* *GIVEN* `FETCH_MODE = 'replay'` and a recording for the 2024 games slice
* *AND* the fetch UDF's access to the connection has been revoked
* *WHEN* the user queries `SELECT * FROM CFBD_VS.GAMES WHERE P_YEAR = 2024`
* *THEN* the query MUST return the rows flattened from the recorded body, with `REQ_SOURCE = 'REPLAY'`
* *AND* `REQ_STATUS`, `REQ_MS` and `REQ_AT` MUST equal the recorded status, latency and request time
* *AND* the mock API MUST receive no request

### Scenario: A missing recording fails clearly

* *GIVEN* `FETCH_MODE = 'replay'` and no recording for the 2023 games slice
* *WHEN* the user queries `SELECT * FROM CFBD_VS.GAMES WHERE P_YEAR = 2023`
* *THEN* the query MUST fail with an error naming the slice key and the replay path
* *AND* no network request SHALL be made

### Scenario: A recorded response replays identically

* *GIVEN* the owner records the 2024 games slice from the mock API via the fetch UDF's raw output and stores it as a recording
* *WHEN* the same query runs once in live mode against the mock API and once in replay mode
* *THEN* both results MUST be identical in all response, parameter and position columns
* *AND* they MUST differ only in `REQ_SOURCE` and the request timing columns

### Scenario: Large responses are recorded in parts

* *GIVEN* the mock API returns a response body longer than 2,000,000 characters for a slice
* *WHEN* the owner records that slice through the raw output
* *THEN* the raw output MUST emit the body in ordered parts of at most 2,000,000 characters
* *AND* the recording reassembled from the parts MUST replay to the same rows as the live query

### Scenario: Switching the mode takes effect on the next query

* *GIVEN* a virtual schema in live mode
* *WHEN* the owner runs `ALTER VIRTUAL SCHEMA CFBD_VS SET FETCH_MODE = 'replay'` and the user queries again
* *THEN* the next query MUST be served from recordings, with `REQ_SOURCE = 'REPLAY'`
* *AND* the virtual schema MUST NOT need a `REFRESH`
