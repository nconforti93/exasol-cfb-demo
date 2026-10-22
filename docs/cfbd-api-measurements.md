# CFBD API measurements (S1 + S2, 2026-10-22)

Derived numbers from the foundation's live run (`specs/_plans/add-cfbd-vs-foundation`, tasks 9.3 and 9.5). The recordings and the raw measurements stay in the git-ignored `cache/` directory (`cache/replay/`, `cache/measurements/`), because CFBD's terms forbid redistribution.

The run:
- **Key:** the project key, tier Academic, 3,000 calls per month in a shared pool (CFB + CBB). Every `/info` feature flag is false.
- **Database:** AWS Exasol Personal `default`, 2026.1.1, 3 nodes, builtin PYTHON3 3.12.3.
- **Calls:** every call went through `scripts/record.py`. S1 used the recorded slices of `vs/live/s1-patterns.yaml`; S2 used documented virtual-schema queries (`--query`). Each call was logged in `cache/live-calls.jsonl`, with `/info` read before and after.

## Calls spent

| Step | CFBD charged | Ledger cost | Notes |
|---|---|---|---|
| S1 recordings (9.3) | 10 | 14 | 11 requests. The `/wepa` 401 was not charged. One week of `/plays` was charged, but the client timed out before the response arrived, so it was fetched again |
| S2 (a) fan-out | 6 | 6 | |
| S2 (b) stored slice | 0 | 0 | |
| S2 (c) missing slice | 1 | 1 | |
| `/info` readings (one before the run and one after every call) | 0 | 0 | Free, confirmed on every reading |
| **Total** | **17** (remainingCalls 3,000 → 2,983) | **21 of 50** | The ledger never undercounts. It charged the cut-off `/plays` call 4 (attempts unknown) and the 401 refusal 1 |

No request needed a retry, and no 429 was seen.

## S1 findings

1. **`/games` returns a whole season in one call.**
   - 2024: 3,801 games (3,747 regular, 54 postseason) in 2.92 MB, with 2.0 s of HTTP time.
   - 2026, in progress: 3,678 games in 2.78 MB with 2.6 s of HTTP time. The schedule runs to the end of the regular season; 2,231 games were completed on 2026-10-22, and no postseason games were listed yet.
2. **`seasonType` omitted means regular and postseason.** `/games?year=2024` returns the same 3,801 games with the same field values as `seasonType=both`. The explicit `seasonType=both` stays, so slice keys say what they hold.
3. **`classification` omitted means every division.** This confirms Phase 2 assumption A4 and Phase 3 question Q9. The 2024 season, by the classifications of its two teams:

   | Home / away | Games |
   |---|---|
   | FBS / FBS | 799 |
   | FCS / FCS | 692 |
   | D-II / D-II | 866 |
   | D-III / D-III | 1,210 |
   | FBS / FCS | 121 |
   | FCS / D-II | 38 |
   | One or both teams without a classification (non-NCAA opponents) | 65 |

   920 games have at least one FBS team. The demo's one call per season therefore carries every division, and the Phase 2 backfill stays at one `/games` call per season.
4. **Row order is not stable between requests.** The two `/games` 2024 requests returned the same games in a different order, so `ROW_NO` identifies a row within one fetch only. Downstream keys must use `ID`.
5. **`/plays` covers FBS and FCS games only.** Week 8 of 2024 returned 19,520 plays from 112 games: 59 FBS/FBS, 52 FCS/FCS and 1 FCS/D-II. That week's 192 D-II and D-III games have no play-by-play. The response is 12.0 MB with 2.6 s of HTTP time.
6. **`/plays/stats` stays far below its row cap per game.** One game returned 208 records, against the 2,000-record cap.
7. **Tier gating is confirmed.** `/wepa/team/season` answers HTTP 401 "This endpoint requires a Patreon subscription at Tier 1 or higher" for this key, whose `adjustedMetrics` flag is false. **A refusal is not charged.**

## Payload sizes

HTTP time is the fetch UDF's measured request time (`REQ_MS`). Rows are the rows the strict flattener produced locally from the recording.

| Request | Body | HTTP | Rows |
|---|---|---|---|
| `/games?seasonType=both&year=2024` | 2.92 MB | 2.0 s | `GAMES` 3,801 |
| `/games?year=2024` (defaults check) | 2.92 MB | 1.4 s | `GAMES` 3,801 |
| `/games?seasonType=both&year=2026` | 2.78 MB | 2.6 s | `GAMES` 3,678 |
| `/rankings?seasonType=both&year=2024` | 217 KB | 0.8 s | `RANKINGS` 17, `RANKINGS_POLLS` 81, `RANKINGS_POLLS_RANKS` 2,028 (AP, Coaches, FCS Coaches, AFCA D-II, AFCA D-III every week; CFP committee in 6 weeks; one postseason week) |
| `/plays?seasonType=both&week=8&year=2024` | 12.0 MB | 2.6 s | `PLAYS` 19,520 |
| `/teams/season/overview?team=Michigan&year=2024` | 18.8 KB | 0.5 s | 1 (all 36 curated columns filled) |
| `/game/box/advanced?id=…` (one 2024 game) | 11.6 KB | 0.2 s | 1 parent row; players: PPA 18, usage 18; team tables 2 each; 4 arrays empty for that game |
| `/plays/stats?gameId=…&seasonType=both` (same game) | 75.3 KB | 0.4 s | 208 |
| `/games/{id}/preview` (a game not yet played) | 48.1 KB | 0.7 s | 1 (92 of 103 curated columns filled; the 11 NULLs are points, playoff fields and `REASON`) |

## Flattening on real payloads

- **Flattening:** every recorded response flattens with **zero errors**, and no response field is left uncovered by the catalog (spec 5.33.0, catalog `b6389f65c48c`). So 9.4 changed no generated content.
- **String lengths:** the longest observed values are 416 characters (`PLAYS.PLAY_TEXT`), 89 (`GAMES.NOTES`), 64 (`GAMES.PLAYOFF_BOWL_NAME`) and 55 (`GAMES.VENUE`), against the declared `VARCHAR(2000)`. The largest `RAW_JSON` is 48,082 characters. The sizes stay as generated (decision [26]).
- **Box score `teams.rushing`:** its entries carry no team name, so those rows are identified by position (`TEAMS_RUSHING_NO`) only.
- **Replay sweep (9.4), local 2026.2, 0 calls:**
  - **Setup:** the recordings were published into the local BucketFS (`--publish-local`), then queried through a `TEST_` virtual schema in replay mode.
  - **Coverage:** 24 virtual tables over the 8 canonical slices, including every table of the advanced box score.
  - **Result:** each returned exactly the recorded row count, with `REQ_SOURCE = 'REPLAY'`. A full `SELECT *` of `GAMES` 2024 returned 3,801 rows × 60 columns.

## S2 live verification (AWS)

- **(a) Fan-out.**
  - **Query:** `SELECT * FROM CFBD_VS.GAMES WHERE P_YEAR BETWEEN 2019 AND 2021 AND P_WEEK IN (8, 9)`.
  - **Calls:** 6 slices, 6 calls; remainingCalls dropped by exactly 6.
  - **Concurrency:** all six requests started in the same millisecond (`MAX_PARALLEL_FETCHES` 6, peak 6). Each took 310–424 ms, so the fan-out finished 424 ms after it started, against 2,256 ms sequentially.
  - **Result:** 859 rows; the query took 2.06 s end to end.
- **(b) Read-through.**
  - **Setup:** the recorded 2024 `GAMES` slice was staged as a final stored slice (3,801 rows plus its `LOAD_LOG` row, at 0 calls), then queried in live mode.
  - **Result:** every row came back `STORED` with `REQ_ATTEMPTS` 0. remainingCalls was unchanged, and the query took 1.26 s.
  - **Cleanup:** the staged rows were removed afterwards.
- **(c) Missing slice.** `SELECT * FROM CFBD_VS.TEAMS_MATCHUP WHERE P_TEAM1 = 'Michigan' AND P_TEAM2 = 'Ohio State'` made 1 call; remainingCalls dropped by exactly 1. CFBD needed 4.26 s, and the query took 4.87 s end to end.
- **Zero-call probes on 2026.1.1 (task 9.2), in replay mode with one staged slice:**
  - Aggregates over the read-through `UNION ALL` work (GROUP BY, COUNT/SUM).
  - `NOT EXISTS` keeps stored slices away from the fetch UDF on 3 nodes.

## On-demand latency against the 2 s target

The database side of a single-slice live lookup takes about **0.6 s**: in (c), 4.87 s end to end minus 4.26 s at CFBD.

- **Where it holds:** endpoints that answer in under about 1.4 s. In S1 that was the box score (0.2 s), team overview (0.5 s), game preview (0.7 s) and play stats (0.4 s).
- **Where it fails:** `/teams/matchup`, CFBD's head-to-head endpoint, took 4.3 s at CFBD in the one measurement, so the end-to-end time was 4.9 s.
- **Stored slices:** they answer without CFBD; 3,801 rows took 1.3 s.

The demo's head-to-head beat should therefore read pairs that are already stored, or show a "fetching from CFBD" state.

## Demo budget, re-confirmed

- **Key:** Academic, 3,000 calls per month, shared pool, resets on the 1st. The owner's cap for October 2026 is **400 calls across all plans**, and this plan's cap was 50.
- **Phase 2 demo profile:** **217 calls once, then 4 per weekly refresh**. S1 confirmed the profile's main assumption (A4): one `/games` call per season returns every division, so no per-classification calls are needed.
- **October:** 21 (ledger, this plan) + 217 (backfill) + 4 per refresh comes to about 250 calls, within the 400-call October cap and far below the key's 3,000.
- **Free tier:** the profile still fits on the free 1,000-call tier that the design targets (D13).
