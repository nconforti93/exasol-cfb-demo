# Score-engine verification

## First live odds, 2026 after regular week 7 (2026-10-23)

The simulation read the stored `CFB_MODEL` point on the AWS `default` Exasol Personal deployment. `make model` and `make sim` deployed the input model and engine first; deployment runs 22 and 23 completed with zero CFBD calls. Both simulation runs finished `DONE` and each stored a 143-team, 377-remaining-game snapshot. The local model and engine deployment and a 10,000-season smoke run also passed; the local smoke took 14.879 seconds.

| AWS run | Seasons | Batch size | Batches | Inputs | Engine | Outputs | Total | Seasons/s | Engine batch ms (min/mean/max) | Execution |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 1 | 100,000 | 5,000 | 20 | 4.601 s | 5.636 s | 3.883 s | 14.496 s | 6,898 | 252 / 356 / 402 | 12 instances on 3 nodes |
| 2 | 1,000,000 | 10,000 | 100 | 4.244 s | 25.182 s | 2.835 s | 32.672 s | 30,607 | 471 / 764 / 848 | 12 instances on 3 nodes |

`SECONDS_TOTAL` includes the smaller register, batch-report and finish steps, so the three named step times do not sum exactly to the total. The batch milliseconds are measured *inside* the database's UDF instances. The host prototype took about 1.4 seconds per 10,000-season batch; the AWS 10,000-season batches reported a 0.764-second internal mean. These are different measurements and do not by themselves establish an end-to-end speedup.

### Auburn what-if

The first remaining game is Auburn against LSU. Conditional odds count the stored simulated seasons in which Auburn wins that game; the query makes no CFBD call.

| AWS run | P(CFP) now | P(CFP) if Auburn beats LSU | Matching seasons | One-team query time |
|---|---:|---:|---:|---:|
| 1, 100,000 seasons | 4.94% | 11.33% | — | 0.318 s |
| 2, 1,000,000 seasons | 4.94% | 11.20% | 366,207 | 0.155 s |

On the 1,000,000-season run, if Auburn **loses** to LSU, 633,793 seasons match (63.4%) and its P(CFP) drops to **1.31%**. Its P(bye) changes from 0.29% to 0.00%, and P(title) from 0.06% to 0.02%. This query took 0.177 seconds with the query cache off.

The AWS quota reading stayed **241 / 400** before and after the deployments, simulations and what-if queries: **zero CFBD calls** for this work.

### What-if latency and layout decision

For run 2, each cell below ran 20 times with the query cache off. The p95 is the 19th of the 20 measured wall times, including the database roundtrip. `k=3` fixes wins in Auburn's next three games (game IDs 401856728, 401856735 and 401856738; outcome positions 13, 78 and 125). The all-team query returns 138 eligible teams.

| Condition | One team p95 | All teams p95 |
|---|---:|---:|
| k = 1 | 0.163 s | 0.556 s |
| k = 3 | 0.148 s | 0.595 s |

The one-team p95 is below decision [47]'s 1-second trigger, and the all-team p95 is below its 1.5-second trigger. Layout A remains in use; the layout-C fallback in task 3.6 is not triggered by this run. These timings are for one stored 1,000,000-season run; task 3.6 remains conditional on latency growing as more runs accumulate.

### Ranking and deployment context

After week 7, **11** of the simulated top 12 were in the AP top 25 and **9** were in the AP top 12. James Madison was unranked in the AP poll. AP top-12 teams outside the simulated top 12 were UCLA (#9), Tennessee (#11) and Kentucky (#12).

The latest `DB_RAM_SIZE` event read **70.5 GiB**. The million-season run used 12 UDF instances across 3 AWS nodes, Python 3.12.3 and numpy 1.26.4, with 10,000 seasons per batch. The recorded memory event and the run's instance count are included so these timings can be compared with later runs after deployment sizing changes.


## Live predictions, movement and rooting guide (2026-10-26)

`make sim TARGET=aws` deployed 29 simulation files as DEPLOY run 10: 15 views compiled, 463/463 comments applied, and the eleven data tables kept. Existing week-8 predictions and the 1,000,000-season week-8 SIM_RUN 1 remained intact. Two prior-point runs supplied the comparison points:

| As-of point | SIM_RUN | Seasons | Teams | Remaining games | Total | Instances / nodes |
|---|---:|---:|---:|---:|---:|---:|
| Week 6 | 3 | 1,000,000 | 146 | 439 | 34.106 s | 12 / 3 |
| Week 7 | 2 | 1,000,000 | 143 | 377 | 30.686 s | 12 / 3 |
| Week 8 | 1 | 1,000,000 | 143 | 321 | 31.63 s | 12 / 3 |

`SELECT RUN_ID, AS_OF_WEEK_SEQ, PREV_RUN_ID FROM CFB_SIM.POINT_RUN WHERE AS_OF_SEASON=2026 ORDER BY AS_OF_WEEK_SEQ` returned `(3, 6, NULL)`, `(2, 7, 3)`, `(1, 8, 2)`. `CURRENT_RUN` returned week-7 run 2 with 1,000,000 seasons and `NEXT_WEEK_SEQ=8`. This is correct while `CFB_MODEL.CURRENT_AS_OF` remains at week 7: 17 incomplete week-8 games are non-FBS and the model point has not advanced. Week-8 odds and the week-9 frozen picks are available by their explicit point and week filters.

Auburn's `ODDS_MOVEMENT` rows show P(CFP) 1.0705% at week 6 (`NEW`), 4.9287% at week 7 (`UP` 3.8582 percentage points), and 1.1740% at week 8 (`DOWN` 3.7547 points). In week-8 run 1, Auburn's top `ROOTING_GUIDE` game is Ole Miss–Auburn in week 9: root for Auburn, P(CFP) 5.8078% if Auburn wins versus 0.0004% if Ole Miss wins, leverage 5.8074 percentage points, `NEXT_WEEK_RANK=1`, `LOW_SAMPLE=FALSE`. The top five guide rows and next-week rows were read by `RUN_ID=1` and Auburn's `TEAM_ID`; the next-week ranks were 1–5.

`PREDICTION_GRADED` has 56 reconstructed week-8 FBS-involving games with results: 35 correct and 21 wrong. The 2026 `FBS_INVOLVED` season scorecard has 566 graded reconstructed predictions, 434 correct, 132 wrong and Brier 0.15857. All 56 week-9 FBS-involving predictions remain `IN_ADVANCE`, frozen at 2026-10-26 09:32:38 UTC. The catalog check used `EXA_ALL_COLUMNS`: all required columns of 13 Phase 5 hand-off objects are present, and all 30 columns of `PREDICTION` are exposed by `PREDICTION_GRADED` (41 columns total).

`make quota TARGET=aws` read 245/400 used calls and 75 ledger calls of room both before and after the deploy, the two runs and the read-only checks: **zero CFBD calls**.
