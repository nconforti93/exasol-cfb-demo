# Serving API verification (2026-10-27)

`CFB_API` serves stored model data to the app and chat. `CFB_API_LIVE.QUOTA` is a separate app-only view over the free `/info` endpoint. The unsupported `GAME_PREVIEW` view was removed after Exasol failed to push its required `P_GAME_ID` filter through a view; Phase 6 uses a guarded direct query.

## Deployments and quota

| Target | Result | Comments | CFBD calls |
|---|---|---:|---:|
| AWS `default` | DEPLOY run 15: 20 stored views, one live quota view; APP/MCP grants in place | 464/464 | 0 |
| Local `local` | DEPLOY run 34: same views; APP/MCP grants already in place | 464/464 | 0 |

AWS `make quota` reported 245 used calls and 75 calls of project-ledger room before and after the stored-query timing probe. The project October budget is 400 calls. The probe selected only `CFB_API` views and never queried `CFB_API_LIVE.QUOTA` or `CFBD_VS`.

## AWS stored-query timings

The probe ran on the AWS 3-node Exasol Personal deployment with `QUERY_CACHE = OFF`. Each query ran five times; the table shows the median wall time including the database round trip. The point was 2026 regular week 7 (`AS_OF_WEEK_SEQ = 7`); team queries used Michigan (`TEAM_ID = 130`) and the matchup used Ohio State (`TEAM_ID = 194`). Query text is in the app route catalog and the view definitions in `sql/api/`.

| Query | Rows returned | Median ms |
|---|---:|---:|
| Points for 2026 | 17 | 64.5 |
| FBS team search | 138 | 121.1 |
| FBS map teams | 138 | 117.4 |
| Map records | 714 | 79.0 |
| Map rankings | 200 | 48.4 |
| Michigan schedule | 12 | 73.8 |
| Michigan polls | 3 | 45.3 |
| Michigan SP+ | 2 | 48.0 |
| Incomplete next-week FBS games | 0 | 126.7 |
| Road warriors | 5 | 264.0 |
| SP+ movers | 10 | 75.6 |
| Conference strength | 11 | 237.7 |
| Win streaks | 10 | 345.8 |
| Michigan–Ohio State series | 1 | 122.4 |
| Michigan–Ohio State games | 20 | 73.0 |
| Michigan–Ohio State next meeting | 1 | 149.7 |
| Week-7 upsets | 19 | 120.5 |
| All-time top upsets | 21 | 95.7 |
| Refresh timeline | 6 | 75.0 |
| Stored fetches | 20 | 52.4 |

All 20 medians were below the plan's one-second target. The next-week game query returned zero rows because `CURRENT_POINT` still names week 7 while all FBS games of week 8 are complete; incomplete non-FBS games prevent the point from advancing. The query's latency passed, but its empty result does not validate the populated week card. The existing app live smoke showed the week card before those games completed; a fresh games fetch after the week-8 completion window is needed to advance `CURRENT_POINT` for the next live card.

The Slice A dictionary at this checkpoint listed 21 views from their SQL comments.

## Slice B: odds, predictions and history

The engine-facing serving files are deployed as 33 stored `CFB_API` views and one `CFB_API_LIVE.QUOTA` view: local DEPLOY run 38 and AWS DEPLOY run 17. Each deploy compiled every view and read back all 659 object and column comments (642 stored, 17 live). Both reported zero CFBD calls. The current dictionary, regenerated with `make api-docs`, lists all 34 views. The AWS `EXA_ALL_COLUMNS` readback matched the declared column names and order of every stored view; this includes all Phase 4 hand-off columns in the odds, what-if, prediction and scorecard sources.

On local, a one-game `CFB_API` what-if matched Phase 4's engine query for all 138 teams in the current run. Three focused synthetic integration tests passed for run visibility, the adapter, prediction and scorecard serving, matchup values and line/Elo upsets. Unit checks for the what-if adapter and dictionary passed after regeneration. The AWS app user `CFB_APP_WEB` queried both one-condition and three-condition what-ifs successfully; each returned 138 teams. Those queries read only stored serving views.

The AWS timing probe used `QUERY_CACHE = OFF`, five executions per query, and measured wall time including the round trip. The current API point remained 2026 after week 7 and its what-if run was 2. Query text and samples are in the local probe artifact `/tmp/p5_slice_b_aws_probe.jsonl` (outside this repository).

| Stored query | Rows | Median ms |
|---|---:|---:|
| `PLAYOFF_ODDS` for run 2 | 138 | 505.7 |
| `WEEK_GAMES` for the point | 56 | 392.6 |
| Michigan–Ohio State `MATCHUP_NEXT` | 1 | 460.0 |
| Week-7 `UPSETS` top 20 | 19 | 413.1 |
| All-time `UPSETS` through rank 20 | 24 | 409.3 |
| What-if, one condition | 138 | 501.8 |
| What-if, three conditions | 138 | 419.4 |

All seven medians are below one second. The timing probe connected as the deployment owner; the separate `CFB_APP_WEB` smoke confirmed that the app role can run both what-if shapes. Quota usage stayed at 245 of the project's 400 October calls.

The all-time top ranks contain 24 games because rank 20 is shared by five games. Three of the 24 are pre-line Elo games (1985, 1991 and 1993), so the Elo extension appears without filling the top ranks with older extreme estimates. The line era supplies 21 of 24 rows, and 11 rows share a magnitude with another ranked row. The latter is expected because stored line probabilities have four decimal places; the page must display the `ALL_TIME_TIED` marker. The line-era top 20 has 21 rows after ties, spanning 2013–2026; 11 carry `LINE_ERA_TIED`. All 8,695 pre-1978 upsets retain season ranks and have no all-time rank. The list includes recognizable results such as Kansas–Texas (2021), Northern Illinois–Notre Dame (2024), and Jacksonville State–Florida State (2021). This is a plausibility check, not a calibration claim for historic Elo probabilities.
