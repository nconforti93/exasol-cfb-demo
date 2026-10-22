# Feature: Virtual schema properties

The CFBD virtual schema is configured only through `ALTER VIRTUAL SCHEMA … SET` properties. Defaults are safe: the call budget is low and gating fails closed. Invalid settings are rejected up front, and no property ever holds a secret.

## Background

| Property | Default | Allowed values |
|---|---|---|
| `CONNECTION_NAME` | `CFBD_API` | Name of the connection the fetch UDF reads |
| `FETCH_MODE` | `live` | `live`, `replay` |
| `REPLAY_PATH` | `/buckets/bfsdefault/default/cfbd/replay` (probe 1.4k) | Absolute UDF-visible BucketFS path |
| `MAX_CALLS_PER_QUERY` | `10` | Integer 1–1000 |
| `MAX_PARALLEL_FETCHES` | `6` | Integer 1–16. Number of CFBD requests the fetch UDF runs at the same time ([18]) |
| `READ_THROUGH` | `TRUE` | `TRUE`, `FALSE` |
| `API_FEATURES` | empty | Comma-separated subset of `adjustedMetrics`, `weather`, `scoreboard`, `livePlayByPlay`, `graphQl` |
| `DOH_URL` | `https://1.1.1.1/dns-query` | `off`, or an `https` URL whose host is an IP literal (the DNS-over-HTTPS fallback, D21) |
| `INFRA_SCHEMA` | `CFB_INFRA` | Schema of the fetch UDF |
| `META_SCHEMA` | `CFB_META` | Schema of `LOAD_LOG` |
| `CORE_SCHEMA` | `CFB_CORE` | Schema of the mirrors |

- The schema properties let a separate test namespace run beside the real deployment.
- Property values are visible to every user who can see the virtual schema. None of them is secret.

## Scenarios

### Scenario: Defaults apply when properties are unset

* *GIVEN* a virtual schema created with no properties other than `CONNECTION_NAME`
* *WHEN* the user runs `EXPLAIN VIRTUAL` on a valid query
* *THEN* the manifest MUST show fetch mode `live`, read-through `TRUE` and budget `10`
* *AND* gated tables MUST refuse queries

### Scenario: Invalid property values are rejected

* *GIVEN* a deployed virtual schema
* *WHEN* the owner sets `MAX_CALLS_PER_QUERY` to `0` or `ten`, `FETCH_MODE` to `record`, `READ_THROUGH` to `maybe`, `API_FEATURES` to `weather,teleport`, or `DOH_URL` to `http://1.1.1.1/dns-query` or `https://cloudflare-dns.com/dns-query`
* *THEN* each `ALTER VIRTUAL SCHEMA` MUST fail with an error naming the property and its allowed values
* *AND* the property MUST keep its previous value

### Scenario: Unknown properties are rejected

* *GIVEN* a deployed virtual schema
* *WHEN* the owner runs `ALTER VIRTUAL SCHEMA CFBD_VS SET MAX_CALL = '5'`
* *THEN* the statement MUST fail with an error naming `MAX_CALL` and listing the known properties

### Scenario: Property changes apply to the next query without a refresh

* *GIVEN* `MAX_CALLS_PER_QUERY = 10` and a query that plans 12 requests
* *WHEN* the owner sets `MAX_CALLS_PER_QUERY = 12` and the user runs the query's `EXPLAIN VIRTUAL` again
* *THEN* the second plan MUST be accepted with 12 requests
* *AND* no `REFRESH` SHALL be required

### Scenario: Schema properties redirect storage and fetch objects

* *GIVEN* a second deployment whose virtual schema sets `INFRA_SCHEMA`, `META_SCHEMA` and `CORE_SCHEMA` to the `TEST_`-prefixed schemas
* *WHEN* a query is planned on that virtual schema
* *THEN* the pushdown SQL MUST reference only the `TEST_`-prefixed fetch UDF, load log and mirror tables
* *AND* stored slices of the default deployment MUST NOT be visible to it
