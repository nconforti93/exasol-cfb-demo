# CFB_MODEL verification (plan `add-cfb-analytical-model`, 2026-10-23)

Derived numbers only; no CFBD data. Light process (the owner's "this is a demo" switch): the live runs of task group K were done in two passes on the same day, a pre-run on the model through `TEAM_SEASON` and a final pass on the finished model. Evidence sources: decision log [35] to [40], the commits `8c62f8a` and `8d4d739`, `docs/spikes/s6-model-probe.md` (platform probe, performance smoke, full-model timings).

## Targets

| | AWS `default` | local `local` |
|---|---|---|
| Exasol | 2026.1.1, 3 nodes | 2026.2.0-nano.3, 1 node (WSL) |
| `make deploy` (2026-10-23, final model) | exit 0, DEPLOY run 18 | exit 0, DEPLOY run 14 |
| Model objects | 15 tables (7 reference, 8 derived), 11 views, 1 function | same |
| Comments | 454 of 454 (100 %), 11 views compiled | same |
| `REFRESH_STEPS` | `(100, MODEL, CFB_INFRA.REFRESH_MODEL)` | same |

## Stored data (identical on both targets)

`GAME` 112,674 rows over 158 seasons; `TEAM_SEASON` 41,578; `TEAM` 1,933 (138 FBS in 2026); `RANKING` 20,994; `SEASON_WEEK` 2,218; `AS_OF_POINT` 2,375. `CURRENT_AS_OF` is 2026, regular week 7.

## Zero-call proof (task 7.5)

| Measure | Before the final deploys | After |
|---|---|---|
| `CFB_META.FETCH_LOG` rows, AWS | 221 | 221 |
| `CFB_META.FETCH_LOG` rows, local | 221 | 221 |
| Latest `FETCH_LOG.LOGGED_AT` | 2026-10-23 06:44:22 | 2026-10-23 06:44:22 |
| Live ledger lines (`cache/live-calls.jsonl`) | 37 | 37 |
| Ledger, October 2026 | 242 of 400 | 242 of 400 |

`make deploy TARGET=aws` reads only the free `/info`. `make quota TARGET=aws` after the deploys: `USED_CALLS` 241 of 3,000 (the key's counter, shared with other tools), `BACKFILL_CALLS` 217, `REFRESH_CALLS` 4. No model object references `CFBD_VS` (lint and the dependency test).

## Checks (`MODEL_CHECKS`, all seasons, both targets identical)

Failed checks over all seasons (`WEEK_SEQ IS NULL`, one row per check and season):

| Check | Severity | Seasons failing | Offenders |
|---|---|---|---|
| `GAME_WITHOUT_WEEK` | ERROR | 1 (2020) | 532 games of the season types `spring_regular` and `spring_postseason`, 2 with an FBS team; the as-of views leave them out |
| `CONFERENCE_GAME_MISMATCH` | WARNING | 89 | 1,689 games with an FBS team carry a conference flag across CFBD memberships (1,475 before 1990, 209 in 2001-2010, 5 in 2021, none since) |
| `CONF_CHAMPIONSHIP_MISSING` | WARNING | 13 | 25 conference-seasons (SEC 1992-2000, Big 12 1996-2000, MAC and WAC 1997-1998, Pac-12 2018 and 2021, four conferences in 2020, MAC 2022) |
| `CONF_CHAMPIONSHIP_NOTES_MISMATCH` | WARNING | 1 | 1 |
| `DUPLICATE_SOURCE_ROW` | WARNING | 1 (2022) | 100 ranking keys |
| `WEEK_ORDER_MISMATCH` | WARNING | 19 | 146 weeks or games out of calendar order |
| `VENUE_WITHOUT_LOCATION` | INFO | 9 | 78 venues |

The only ERROR is the 2020 spring games; no other season stops a refresh.

### `MODEL_CHECKS` warnings for 2026 (the refresh's own point, season 2026, week 7)

16 checks run, 13 pass, 3 fail, none an ERROR:

| Check | Severity | Observed | What it is |
|---|---|---|---|
| `WEEK_ORDER_MISMATCH` | WARNING | 62 | Regular week 1: 61 lower-division games kick off before the calendar's week-1 start; regular week 15: Army-Navy (12 December) kicks off after its calendar week |
| `WEEK_NOT_COMPLETE` | WARNING | 1 | One lower-division game of week 7 (Texas Wesleyan vs Mary Hardin-Baylor) is not completed in CFBD's data |
| `VENUE_WITHOUT_LOCATION` | INFO | 19 | Venues of small schools without coordinates in CFBD |

All three are known and none involves an FBS result. `CONF_CHAMPIONSHIP_MISSING` passes for 2026 (it skips the current season), and the 2026 Army-Navy game is never taken for a championship game (decision [32]).

## Assumptions Q1 to Q13

| # | Outcome |
|---|---|
| Q1 | Holds: AP 2024 week 1 is the preseason order (Georgia, Ohio State, Oregon); week 2 reflects week-1 results (Texas third). Lead 1 stays |
| Q2 | (a) The first 2024 committee ranking is labeled week 11 (released after the week-10 games), so with lead 0 every committee ranking of a past season is visible one week after its release (the live demo is not affected: a ranking fetched before the cutoff is visible at once). (b) The 2024 and 2025 selection rankings are labeled regular week 16 and flagged final. (c) `DUPLICATE_SOURCE_ROW` 2022: 100 ranking keys, resolved by the fetch time and `POLLS_NO` |
| Q3 | Holds: one postseason week (`WEEK_SEQ` 101); 2024 has 35 bowls and 11 CFP games; CFBD labels the 2025 lower-division playoff as postseason weeks 13 and 14 (`WEEK_SEQ` 113 and 114, not chronological; `WEEK_ORDER_MISMATCH`) |
| Q4 | Holds: Texas Big 12 (2023, P5) then SEC (2024, P4); Alabama SEC in both |
| Q5 | Holds: `POWER_CONFERENCE_MISSING` and `CONFERENCE_NAME_UNMATCHED` pass in every season |
| Q6 | Holds: CFBD's classification is the code of its time (App State 2007 `fcs`, Liberty 2017 `fcs`), so `CLASSIFICATION_TRANSITION` stays empty (0 rows) |
| Q7 | Holds with fallbacks: 167 championship games over 28 seasons (1996-2025), 9 or 10 per season since 2021 (2020: 6). CFBD stores the 2025 title games without the conference flag; the unflagged fallback adds exactly those nine ([36]). The 13 seasons of `CONF_CHAMPIONSHIP_MISSING` above are the exceptions |
| Q8 | Holds: `CORR(PREGAME_SPREAD, HOME_PREGAME_WIN_PROB)` for 2024 is -0.96 |
| Q9 | Holds: 2024 games include `fbs`, `fcs`, `ii` and `iii` |
| Q10, Q11 | Platform probe 1.1 (local, 2026.2): `ST_DISTANCE` on 4326 is planar, DDL is transactional, inline comments read back, `FORCE` views work, `DOUBLE` to `DECIMAL` rounds. Not re-run on AWS (the deploys, which depend on all of it, succeeded there) |
| Q12 | Holds: `SP_TEAM_UNMATCHED` passes in every season |
| Q13 | Pregame lines: `COMPLETE` in 10 seasons, `PARTIAL` in 3. Elo is `PARTIAL` in 135 seasons (about 0.87 to 0.92 of completed FBS games); the threshold was left at 0.95, so the trust level says "partial" |

## Coverage (`SEASON_COVERAGE`, trust levels per season and dataset)

| Dataset | COMPLETE | PARTIAL | IN_PROGRESS | ON_DEMAND | NOT_AVAILABLE |
|---|---|---|---|---|---|
| `GAMES`, `SCORES` | 156 | 0 | 1 | 0 | 1 (1871) |
| `ELO` | 1 | 135 | 1 | 0 | 21 |
| `PREGAME_WP` | 10 | 3 | 1 | 0 | 144 |
| `SP_PLUS` | 13 | 0 | 1 | 43 | 101 |
| `POLLS` | 12 | 0 | 1 | 78 | 67 |
| `CALENDAR` | 14 | 0 | 0 | 11 | 133 |
| `CONFERENCE_MEMBERSHIP` | 158 | 0 | 0 | 0 | 0 |

## Timings (warm, query cache off, 1 warm-up and 3 runs, median; decision [40], `s6-model-probe.md`)

| Query | AWS s | Local s | Target s |
|---|---|---|---|
| `TEAM` current FBS teams | 0.07 | 0.03 | 1 |
| `TEAM_SEASON` one team, all seasons | 0.07 | 0.04 | 1 |
| `TEAM_SEASON` one season | 0.09 | 0.05 | 1 |
| `TEAM_GAME` one team and season | 0.08 | 0.07 | 1 |
| `GAME` one season (3,831 rows) | 0.49 | 0.17 | 1 |
| `GAME_AS_OF` one point | 0.44 | 0.16 | 1 |
| `RANKING_AS_OF` one point | 0.10 | | 1 |
| `TEAM_SEASON_AS_OF` one point | 0.81 | 0.57 | 1 |
| `MODEL_CHECKS` one point | 1.45 | 0.83 | 3 |
| `SEASON_COVERAGE` in full | 1.00 | 0.85 | 3 |
| `GAME` grouped by season | 0.06 | 0.03 | 2 |
| `REFRESH_MODEL` rebuild of 217,643 rows | 6.3 | about 11 | 5 (checks only) |

Every target is met. Before the eight objects became tables the same queries took 0.77 to 6.4 s on AWS (compile time of the deep view stack); see [40].

## Not verified (light process)

- The four example analyses beyond `head_to_head.sql` (task 4.2 deferred); `docs/model-verification.md` therefore has no example timings.
- Probe 1.1 on AWS (task 7.1's re-run); the deploys and the real-data checks cover the same ground.
- A `MATERIALIZED_STALE` check for the derived tables (not built); the rule is documented in the user guide.
