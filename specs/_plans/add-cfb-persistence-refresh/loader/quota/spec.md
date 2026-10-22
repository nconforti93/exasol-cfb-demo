# Feature: Quota view

`CFB_META.QUOTA` shows, in one row, how many CFBD calls the key has left this month, how many of them the project may spend, how much is reserved for weekly refreshes, and where this period's calls went. Reading it costs no call: it reads `/info` through the virtual schema, and the API does not count `/info`. Backfill and refresh use the same numbers to stop before the budget. Because the numbers come from the key's own counters, the cap holds even for calls made outside `make`, from any SQL client.

## Background

- Columns:

  | Column | Meaning |
  |---|---|
  | `TIER_NAME`, `MONTHLY_LIMIT`, `USED_CALLS`, `REMAINING_CALLS`, `RESET_AT` | From `CFBD_VS.INFO` |
  | `PERIOD_START` | One month before `RESET_AT` |
  | `MONTHLY_CALL_BUDGET`, `REFRESH_RESERVE` | From the deployed loader settings |
  | `PROJECT_BUDGET` | `LEAST(MONTHLY_LIMIT, MONTHLY_CALL_BUDGET)` |
  | `REFRESH_SPENDABLE` | `GREATEST(0, LEAST(REMAINING_CALLS, PROJECT_BUDGET − USED_CALLS))`, what a refresh may spend |
  | `BACKFILL_SPENDABLE` | `GREATEST(0, REFRESH_SPENDABLE − REFRESH_RESERVE)`, what a backfill may spend |
  | `BACKFILL_CALLS`, `REFRESH_CALLS` | Live `FETCH_LOG` requests since `PERIOD_START` whose context is a backfill or refresh run |
  | `OTHER_CALLS` | `GREATEST(0, USED_CALLS − BACKFILL_CALLS − REFRESH_CALLS)`: on-demand queries and any other client of the key's shared pool |
  | `DEMO_CALLS_TO_COMPLETE`, `FULL_CALLS_TO_COMPLETE` | Calls the backfill queue still needs for each profile; gaps and quarantined slices excluded |

- `USED_CALLS` counts every call of the key in the current month, including other plans, other tools and the CBBD shared pool. `PROJECT_BUDGET` is therefore a cap on the key's whole monthly use, not only on the loader's.
- The design target is the free tier: the default `monthly_call_budget` is 1000 (D13), even when the key allows more. The owner changes it in `loader/load-plan.yaml` and runs `make deploy`.
- A deploy can override settings for one deployment: `make deploy LOADER_CONFIG="monthly_call_budget=400,refresh_reserve=50"`. The next deploy without the override restores the plan's values. This is how a month with a smaller authorization is enforced in the database.
- `make quota` prints the view.
- The examples use live mode against the mock API, whose `/info` answers are scripted per scenario.

## Scenarios

### Scenario: The quota view shows the key's quota without spending a call

* *GIVEN* the mock API's `/info` reports tier `Free`, `monthlyLimit` 1000, `usedCalls` 120, `remainingCalls` 880 and `resetAt` `2020-11-01T00:00:00Z`
* *WHEN* the owner queries `SELECT * FROM CFB_META.QUOTA`
* *THEN* the row MUST show those values and `PERIOD_START` 2020-10-01 00:00:00
* *AND* the mock API MUST NOT receive any request other than `/info`

### Scenario: The backfill allowance keeps the reserve

* *GIVEN* `/info` reports `monthlyLimit` 1000, `usedCalls` 700 and `remainingCalls` 300
* *AND* `monthly_call_budget` 1000 and `refresh_reserve` 150
* *WHEN* the owner queries `CFB_META.QUOTA`
* *THEN* `REFRESH_SPENDABLE` MUST be 300
* *AND* `BACKFILL_SPENDABLE` MUST be 150

### Scenario: The project budget caps a larger key quota

* *GIVEN* `/info` reports `monthlyLimit` 3000, `usedCalls` 500 and `remainingCalls` 2500
* *AND* `monthly_call_budget` 1000 and `refresh_reserve` 150
* *WHEN* the owner queries `CFB_META.QUOTA`
* *THEN* `PROJECT_BUDGET` MUST be 1000 and `REFRESH_SPENDABLE` 500
* *AND* `BACKFILL_SPENDABLE` MUST be 350

### Scenario: A deploy-time override caps one deployment and is undone by the next deploy

* *GIVEN* `loader/load-plan.yaml` sets `monthly_call_budget` 1000 and `refresh_reserve` 150
* *AND* `/info` reports `monthlyLimit` 3000, `usedCalls` 50 and `remainingCalls` 2950
* *WHEN* the owner runs `make deploy LOADER_CONFIG="monthly_call_budget=400,refresh_reserve=50"`, queries `CFB_META.QUOTA`, then runs `make deploy` and queries it again
* *THEN* the first query MUST show `PROJECT_BUDGET` 400, `REFRESH_SPENDABLE` 350 and `BACKFILL_SPENDABLE` 300
* *AND* the second query MUST show `PROJECT_BUDGET` 1000 and `BACKFILL_SPENDABLE` 800

### Scenario: Calls are attributed to backfill, refresh and other use

* *GIVEN* in the current period a backfill made 12 live requests, a refresh made 2, and `/info` reports `usedCalls` 20
* *AND* the fetch log also holds replay rows and live rows from before `PERIOD_START`
* *WHEN* the owner queries `CFB_META.QUOTA`
* *THEN* `BACKFILL_CALLS` MUST be 12, `REFRESH_CALLS` 2 and `OTHER_CALLS` 6
* *AND* replay rows and rows from earlier periods MUST NOT be counted

### Scenario: The view shows what completing each profile costs

* *GIVEN* 4 missing `demo` slices, 7 missing `full`-only slices, one quarantined `demo` slice and one unregistered gap
* *WHEN* the owner queries `CFB_META.QUOTA`
* *THEN* `DEMO_CALLS_TO_COMPLETE` MUST be 4 and `FULL_CALLS_TO_COMPLETE` 11
* *AND* the gap and the quarantined slice MUST NOT count

### Scenario: A budget change in the plan takes effect after deploy

* *GIVEN* `monthly_call_budget` is changed to 800 in `loader/load-plan.yaml`
* *WHEN* the owner runs `make deploy` and queries `CFB_META.QUOTA` with `/info` reporting `monthlyLimit` 1000
* *THEN* `MONTHLY_CALL_BUDGET` and `PROJECT_BUDGET` MUST be 800
