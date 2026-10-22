# CFBD API reference findings (spec 5.33.0)

Source: `vs/openapi/cfbd-openapi-5.33.0.json`, identical to https://api.collegefootballdata.com/api/5.33.0/cfbd-openapi.json. Full read on 2026-10-21: 93 GET operations, 234 schemas, 21 tags, Bearer auth on every operation. These findings correct several assumptions made earlier from the `required` flags alone.

## Findings that change the design

1. **Conditional requirements live only in prose.** The `required` flag is set on few parameters. Most rules are in parameter descriptions: "Required unless `team` is specified", "One of `week`, `team`, or `conference` is required when filtering by year", "Cannot be combined with `minYear`", "Requires `poll=cfp`". The generator cannot derive them, so the catalog needs a reviewed **overrides file** (`vs/catalog-overrides.yaml`) of machine-readable rules per endpoint. The adapter enforces those, not just `required`.
2. **Tier-gated features.** `/info` returns `features {adjustedMetrics, weather, scoreboard, livePlayByPlay, graphQl}`.
   - `/games/weather` says "Requires Patreon".
   - `/games/{gameId}/preview/adjusted` says "Requires Patreon Tier 1".
   - `/scoreboard` and `/live/plays` map to the `scoreboard` and `livePlayByPlay` flags.
   - `/wepa/*` (tag `adjustedMetrics`) presumably maps to the `adjustedMetrics` flag.
   - **On the free tier these are expected to be unavailable.** Verify with `/info` once the key exists. Gated endpoints stay in the VS but are marked `gated` and excluded from the load profiles unless `/info` reports access.
3. **Hidden defaults:**
   - `classification` defaults to `fbs` on most stats, rushing, passing, punting and PPA endpoints and on `/scoreboard` and `/games/schedule`, so FCS data needs explicit extra calls.
   - `/roster` `year` defaults to 2025.
   - `/ratings/elo` `week` defaults to the latest week.
   - Recruiting defaults to `HighSchool`.
   - `/recruiting/groups` defaults to 2000 through the current year.
   - The `seasonType` default is not stated. `SeasonType` has a `both` value, so loads pass `seasonType=both` where accepted. S1 verifies the default.
   - **Rule:** the loader always sets classification, season type and year explicitly; it never relies on defaults.
4. **Row caps:**
   - `/plays/stats` is "limited to 2,000 records", so it is only complete per `gameId`. That's infeasible to backfill on the free tier (about 800 games per season), so it stays on-demand only.
   - `/player/search` returns up to 100 rows.
   - `/info/usage` returns at most 31 days and 50 rows.
5. **`/games` carries Elo and win probability per game:** `homePregameElo`, `homePostgameElo`, `homePostgameWinProbability` (and the away equivalents), plus line scores, excitement index and playoff metadata. Elo history back to 1890 and Elo-based upset probability come from `/games` alone; `/ratings/elo` is not needed for the v1 pages.
6. **`/teams/matchup` returns the complete series** (`team1Wins`, `team2Wins`, `ties`, `games[]` with scores) in one call, with an optional year range. It's the right on-demand source for head-to-head.
7. **`/games/schedule` returns the active or next slate in one call,** including status, odds, broadcasts, venue and a watchability score, plus the following window. It's ideal for "this week" and for the refresh.
8. **`/metrics/wp/pregame`** has no required parameters, and `year` alone gives a whole season of pregame win probability and spread in one call (data from 2013).
9. **`/info` is a budget API:** `monthlyLimit`, `remainingCalls`, `usedCalls`, `resetAt`, `tierName`, `features`. The loader reads it before spending; the app's inspector can show it.
10. **Arrays of objects inside rows are common and sometimes hold all the data.** In `/games/teams` and `/games/players` the only top-level field is `teams[]`. Others: `/rankings` `polls[] → ranks[]`, `/lines` `lines[]`, `/coaches` `seasons[]`, `/teams/matchup` `games[]`, `/playoffs/*`, `/live/plays`.
    - **Generator rule:** an array of objects becomes a child table (parent keys + array index); an array of scalars becomes a JSON string column. Very large nested objects (`/games/{id}/preview`: 1,837 leaf fields, depth 8; `/teams/season/overview`: 839) are exposed as a curated column subset plus the raw JSON, never fully flattened.
11. **Wide but flat analytics endpoints:** `/passing/teams/*` (about 385 columns), `/rushing/teams/*` (about 179) and `/stats/season/advanced` (82) flatten fine. `/stats/season` is a long format (stat name/value, 5 columns). The model layer pivots as needed.
12. **The per-game box score endpoints are expensive.** `/games/teams` and `/games/players` need `year` plus one of week, team or conference, so roughly 20 calls per season (by week including postseason). They belong to the `full` profile only. `/plays` needs year + week (≈20 per season); `/drives` needs only year.
13. **Team-scoped retrieval.** Many per-season endpoints accept `team` instead of `year`, returning that team's entire history in one call (SP+, SRS, FPI, records, recruiting, returning production, team stats). That's useful for on-demand team pages and not used for backfill.
14. **`RankingPoll` enum is only `cfp`.** `/rankings?year=` without `poll` returns all polls for the season (AP, Coaches, CFP…) in one call.

## Verified with the project key (2026-10-21)

`GET /info` through the `CFBD_API` CONNECTION from a UDF on the dev DB:
- `tierName: Academic`, `monthlyLimit: 3000`, `resetAt: 2026-11-01T00:00:00Z`, `sharedPool: true` (CFB + CBB).
- `features`: `adjustedMetrics`, `weather`, `scoreboard`, `livePlayByPlay` and `graphQl` are all **false**. This confirms finding 2: those endpoints are unavailable on this key.
- **`/info` does not consume a call:** `remainingCalls` stayed 3000 after the request. The loader and inspector can read the budget freely.
- The design target stays the free tier (1k/month, D13); the academic quota is headroom.

## Revised demo profile (free tier)

| Data | Endpoint | Calls |
|---|---|---|
| Games + scores + per-game Elo/WP, **1869–2026** (all-time upsets via Elo, history) | `/games?year=Y&seasonType=both` | ~157 (1 per season, pending S1: classification behavior, response size) |
| Pregame win probability + spread, 2013– | `/metrics/wp/pregame?year=Y` | 14 |
| SP+ (offense/defense/special teams), 2013– | `/ratings/sp?year=Y` | 14 |
| Polls incl. CFP, 2014– | `/rankings?year=Y` | 13 |
| Conference membership, all years | `/conferences/affiliations?minYear&maxYear` | 1 |
| Teams, venues, calendar | `/teams`, `/venues`, `/calendar?year=2026` | 3 |
| **Backfill total** | | **~200** |
| Weekly refresh | `/games` (week), `/metrics/wp/pregame` (next week), `/rankings`, `/ratings/sp`, `/games/schedule` | ~5 |
| Head-to-head | `/teams/matchup` on demand | 1 per click |
| Game preview | `/games/{id}/preview` on demand | 1 per click |

All-time upsets therefore move into the demo profile at roughly 150 extra calls. The free tier still leaves about 700 calls/month for the `full` archive.

## Endpoint catalog (generated from the spec)

Shape = flattened leaf columns of one row (objects flattened, arrays excluded) and nesting depth. "Arrays in rows" become child tables or JSON columns.

| Endpoint | Tag | Hard-required | Conditional rules / defaults / caps (from descriptions) | Shape | Arrays in rows | Gating |
|---|---|---|---|---|---|---|
| `/wepa/team/season` | adjustedMetrics | — | — | array, 26 cols, depth 1 | — | feature flag `adjustedMetrics` (likely) |
| `/wepa/players/passing` | adjustedMetrics | — | — | array, 8 cols, depth 0 | — | feature flag `adjustedMetrics` (likely) |
| `/wepa/players/rushing` | adjustedMetrics | — | — | array, 8 cols, depth 0 | — | feature flag `adjustedMetrics` (likely) |
| `/wepa/players/kicking` | adjustedMetrics | — | — | array, 7 cols, depth 0 | — | feature flag `adjustedMetrics` (likely) |
| `/teams/season/overview` | teams | year, team | — | object, 839 cols, depth 4 | players_usage, players_ppa | — |
| `/teams` | teams | — | — | array, 24 cols, depth 1 | alternateNames, logos | — |
| `/teams/fbs` | teams | — | — | array, 24 cols, depth 1 | alternateNames, logos | — |
| `/teams/matchup` | teams | team1, team2 | — | object, 7 cols, depth 0 | games | — |
| `/teams/ats` | teams | year | — | array, 9 cols, depth 0 | — | — |
| `/roster` | teams | — | `year`: Season year. Defaults to 2025. | array, 15 cols, depth 0 | recruitIds | — |
| `/conferences` | conferences | — | — | array, 6 cols, depth 0 | — | — |
| `/conferences/changes` | conferences | year | — | array, 11 cols, depth 0 | — | — |
| `/conferences/affiliations` | conferences | — | `year`: Season year. Cannot be combined with `minYear` or `maxYear`. | array, 9 cols, depth 0 | — | — |
| `/talent` | teams | year | — | array, 3 cols, depth 0 | — | — |
| `/venues` | venues | — | — | array, 14 cols, depth 0 | — | — |
| `/stats/player/season` | stats | year | — | array, 9 cols, depth 0 | — | — |
| `/stats/player/success` | stats | — | `year`: Season year. Required unless `playerId` is specified.<br>`playerId`: Player ID. Required unless `year` is specified. | array, 12 cols, depth 1 | — | — |
| `/stats/player/success/game` | stats | year | `week`: Week number. Required unless `team` or `playerId` is specified. | array, 16 cols, depth 1 | — | — |
| `/stats/season` | stats | — | `year`: Season year. Required unless `team` is specified.<br>`team`: Team name. Required unless `year` is specified.<br>`classification`: Division classification. Defaults to `fbs`. | array, 5 cols, depth 0 | — | — |
| `/stats/categories` | stats | — | — | array, 1 cols, depth 0 | — | — |
| `/stats/season/advanced` | stats | — | `year`: Season year. Required unless `team` is specified.<br>`team`: Team name. Required unless `year` is specified.<br>`excludeGarbageTime`: Excludes garbage-time plays when `true`. Defaults to `false`.<br>`classification`: Division classification. Defaults to `fbs`. | array, 82 cols, depth 2 | — | — |
| `/stats/game/advanced` | stats | — | `year`: Season year. Required unless `team` is specified.<br>`team`: Team name. Required unless `year` is specified.<br>`excludeGarbageTime`: Excludes garbage-time plays when `true`. Defaults to `false`. | array, 62 cols, depth 2 | — | — |
| `/stats/game/havoc` | stats | — | `year`: Season year. Required unless `team` is specified.<br>`team`: Team name. Required unless `year` is specified. | array, 22 cols, depth 1 | — | — |
| `/rushing/plays` | rushing | — | `year`: Season year. Requires team or week.<br>`week`: Week number. Requires year; either team or week is required with year.<br>`team`: Team name on either side of the rush; either team or week is required with year.<br>`conference`: Conference name or abbreviation on either side of the rush.<br>`classification`: Division classification. Defaults to `fbs`. | array, 34 cols, depth 1 | — | — |
| `/rushing/players/season` | rushing | — | `year`: Season year. Required unless rusherId is specified.<br>`rusherId`: Rusher athlete ID. Required unless year is specified.<br>`classification`: Division classification. Defaults to `fbs`. | array, 89 cols, depth 2 | — | — |
| `/rushing/players/games` | rushing | — | `year`: Season year. Requires team or week; optional when rusherId is specified alone.<br>`week`: Week number. Requires year; either team or week is required with year.<br>`team`: Team name; either team or week is required with year.<br>`rusherId`: Rusher athlete ID. Required unless year is specified.<br>`classification`: Division classification. Defaults to `fbs`. | array, 93 cols, depth 2 | — | — |
| `/rushing/teams/season` | rushing | — | `year`: Season year. Required unless team is specified.<br>`team`: Team name. Required unless year is specified.<br>`classification`: Division classification. Defaults to `fbs`. | array, 175 cols, depth 3 | — | — |
| `/rushing/teams/games` | rushing | year | `week`: Week number. Either team or week is required.<br>`team`: Team name. Either team or week is required.<br>`classification`: Division classification. Defaults to `fbs`. | array, 179 cols, depth 3 | — | — |
| `/recruiting/players` | recruiting | — | `year`: Recruiting class year. Required unless `team` is specified.<br>`team`: Team name. Required unless `year` is specified.<br>`classification`: Recruit classification. Defaults to `HighSchool`. | array, 19 cols, depth 1 | — | — |
| `/recruiting/teams` | recruiting | — | — | array, 4 cols, depth 0 | — | — |
| `/recruiting/groups` | recruiting | — | `recruitType`: Recruit classification. Defaults to `HighSchool`.<br>`startYear`: Earliest recruiting class year. Defaults to 2000.<br>`endYear`: Latest recruiting class year. Defaults to the current year. | array, 7 cols, depth 0 | — | — |
| `/ratings/core` | ratings | — | **Returns Context & Opponent-Relative Efficiency (CORE) ratings. At least one of `year` or `team` is required.**<br>`year`: Season year. Required unless `team` is specified.<br>`team`: Exact team name. Required unless `year` is specified. | array, 11 cols, depth 0 | — | — |
| `/ratings/sp` | ratings | — | `year`: Season year. Required unless `team` is specified.<br>`team`: Team name. Required unless `year` is specified. | array, 29 cols, depth 2 | — | — |
| `/ratings/sp/conferences` | ratings | — | `classification`: Division classification. Defaults to `fbs`. | array, 25 cols, depth 2 | — | — |
| `/ratings/srs` | ratings | — | `year`: Season year. Required unless `team` is specified.<br>`team`: Team name. Required unless `year` is specified. | array, 6 cols, depth 0 | — | — |
| `/ratings/srs/expanded` | ratings | — | `year`: Season year. Required unless `team` is specified.<br>`team`: Team name. Required unless `year` is specified. | array, 7 cols, depth 0 | — | — |
| `/ratings/elo` | ratings | — | `week`: Week number. Defaults to the latest available week in the season.<br>`preseason`: Return initial ratings from each team's opening regular-season game. Missing opening ratings are omitted. Cannot be combined with week; seasonType must be regular or both when specified. Defaults to false. | array, 4 cols, depth 0 | — | — |
| `/ratings/fpi` | ratings | — | `year`: Season year. Required unless `team` is specified.<br>`team`: Team name. Required unless `year` is specified. | array, 14 cols, depth 1 | — | — |
| `/rankings` | rankings | year | `latest`: Returns the latest CFP snapshot when `true`, preferring the snapshot marked as final. Requires `poll=cfp` and cannot be combined with `final`.<br>`final`: Returns the CFP snapshot marked as final when `true`. Requires `poll=cfp` and cannot be combined with `latest`. | array, 3 cols, depth 0 | polls | — |
| `/punting/plays` | punting | — | `year`: Season year. Required unless gameId is specified.<br>`week`: Week number. Either week, a team filter, punterId, or returnerId is required with year.<br>`team`: Team name on either side of the punt.<br>`conference`: Conference name or abbreviation on either side of the punt.<br>`classification`: Division classification. Defaults to `fbs`. | array, 46 cols, depth 1 | — | — |
| `/punting/players/season` | punting | year | `classification`: Division classification. Defaults to `fbs`. | array, 30 cols, depth 1 | — | — |
| `/punting/players/games` | punting | year | `classification`: Division classification. Defaults to `fbs`. | array, 34 cols, depth 1 | — | — |
| `/punting/teams/season` | punting | year | `classification`: Division classification. Defaults to `fbs`. | array, 51 cols, depth 1 | — | — |
| `/punting/teams/games` | punting | year | `classification`: Division classification. Defaults to `fbs`. | array, 55 cols, depth 1 | — | — |
| `/plays` | plays | year, week | `team`: Team name on either side of the play.<br>`conference`: Conference of either team.<br>`classification`: Division classification of either team. | array, 28 cols, depth 1 | — | — |
| `/plays/types` | plays | — | — | array, 3 cols, depth 0 | — | — |
| `/plays/stats` | plays | — | **Returns player and play-stat associations, limited to 2,000 records.** | array, 20 cols, depth 1 | — | — |
| `/plays/stats/types` | plays | — | — | array, 2 cols, depth 0 | — | — |
| `/playoffs/cfp` | playoffs | year | — | object, 8 cols, depth 1 | participants, rounds | — |
| `/playoffs/cfp/participants` | playoffs | year | — | array, 12 cols, depth 1 | — | — |
| `/playoffs/cfp/games` | playoffs | year | — | array, 24 cols, depth 2 | slots | — |
| `/player/search` | players | searchTerm | **Returns up to 100 players whose names match the search term.** | array, 14 cols, depth 0 | teamStints | — |
| `/player/usage` | players | year | `excludeGarbageTime`: Excludes garbage-time plays when `true`. Defaults to `false`. | array, 14 cols, depth 1 | — | — |
| `/player/season/overview` | players | year, playerId | — | object, 31 cols, depth 2 | boxScoreStats_categories | — |
| `/player/returning` | players | — | `year`: Season year. Required unless `team` is specified.<br>`team`: Team name. Required unless `year` is specified. | array, 15 cols, depth 0 | — | — |
| `/player/portal` | players | year | — | array, 10 cols, depth 0 | — | — |
| `/passing/plays` | passing | year | `team`: Team name on either side of the pass. Either team or week is required.<br>`week`: Week number. Either team or week is required.<br>`conference`: Conference name or abbreviation on either side of the pass.<br>`classification`: Division classification. Defaults to `fbs`. | array, 41 cols, depth 1 | — | — |
| `/passing/players/season` | passing | — | `year`: Season year. Required unless passerId is specified.<br>`passerId`: Passer athlete ID. Required unless year is specified.<br>`classification`: Division classification. Defaults to `fbs`. | array, 194 cols, depth 2 | — | — |
| `/passing/players/games` | passing | year | `week`: Week number. Either passerId, team, or week is required.<br>`team`: Team name. Either passerId, team, or week is required.<br>`passerId`: Passer athlete ID. Either passerId, team, or week is required.<br>`classification`: Division classification. Defaults to `fbs`. | array, 198 cols, depth 2 | — | — |
| `/passing/teams/season` | passing | — | `year`: Season year. Required unless team is specified.<br>`team`: Team name. Required unless year is specified.<br>`classification`: Division classification. Defaults to `fbs`. | array, 381 cols, depth 3 | — | — |
| `/passing/teams/games` | passing | year | `week`: Week number. Either team or week is required.<br>`team`: Team name. Either team or week is required.<br>`classification`: Division classification. Defaults to `fbs`. | array, 385 cols, depth 3 | — | — |
| `/ppa/predicted` | metrics | down, distance | — | array, 2 cols, depth 0 | — | — |
| `/ppa/teams` | metrics | — | `year`: Season year. Required unless `team` is specified.<br>`team`: Team name. Required unless `year` is specified.<br>`classification`: Division classification. Defaults to `fbs`. | array, 21 cols, depth 2 | — | — |
| `/ppa/games` | metrics | year | `classification`: Division classification. Defaults to `fbs`. | array, 19 cols, depth 1 | — | — |
| `/ppa/players/games` | metrics | year | `week`: Week number. Required unless `team` is specified.<br>`team`: Team name. Required unless `week` is specified. | array, 11 cols, depth 1 | — | — |
| `/ppa/players/season` | metrics | — | `year`: Season year. Required unless `playerId` is specified.<br>`playerId`: Player ID. Required unless `year` is specified. | array, 22 cols, depth 1 | — | — |
| `/metrics/wp` | metrics | gameId | — | array, 16 cols, depth 0 | — | — |
| `/metrics/wp/pregame` | metrics | — | — | array, 8 cols, depth 0 | — | — |
| `/metrics/fg/ep` | metrics | — | — | array, 3 cols, depth 0 | — | — |
| `/live/plays` | plays | gameId | **Returns live play-by-play data and advanced metrics for a game. Results may be cached for up to five seconds after calculation.** | object, 8 cols, depth 0 | teams, drives | feature flag `livePlayByPlay` |
| `/lines` | betting | — | `year`: Season year. Required unless `gameId` is specified.<br>`team`: Team name on either side of the game.<br>`conference`: Conference of either team. | array, 15 cols, depth 0 | lines | — |
| `/info` | info | — | — | object, 12 cols, depth 1 | products | — |
| `/info/usage` | info | — | `days`: Trailing days to include. Defaults to 7; maximum 31.<br>`limit`: Maximum endpoint and request rows to return. Defaults to 10; maximum 50. | object, 7 cols, depth 1 | topEndpoints, recentRequests | — |
| `/games` | games | — | `year`: Season year. Required unless `id` is specified.<br>`team`: Team name on either side of the game.<br>`conference`: Conference of either team.<br>`round`: Playoff round. Requires `competition`. | array, 39 cols, depth 1 | homeLineScores, awayLineScores | — |
| `/games/teams` | games | — | `year`: Season year. Required unless `id` is specified.<br>`week`: Week number. One of `week`, `team`, or `conference` is required when filtering by year.<br>`team`: Team name. One of `week`, `team`, or `conference` is required when filtering by year.<br>`conference`: Conference name or abbreviation. One of `week`, `team`, or `conference` is required when filtering by year. | array, 1 cols, depth 0 | teams | — |
| `/games/players` | games | — | `year`: Season year. Required unless `id` is specified.<br>`week`: Week number. One of `week`, `team`, or `conference` is required when filtering by year.<br>`team`: Team name. One of `week`, `team`, or `conference` is required when filtering by year.<br>`conference`: Conference name or abbreviation. One of `week`, `team`, or `conference` is required when filtering by year. | array, 1 cols, depth 0 | teams | — |
| `/games/media` | games | year | — | array, 12 cols, depth 0 | — | — |
| `/games/weather` | games | — | **Returns historical and forecast weather data for games. Requires Patreon.**<br>`year`: Season year. Required unless `gameId` is specified. | array, 22 cols, depth 0 | — | Patreon (weather) |
| `/games/schedule` | games | — | `classification`: Division of either participant. Defaults to fbs.<br>`conference`: Conference abbreviation of either participant. | object, 14 cols, depth 1 | games | — |
| `/games/{gameId}/preview` | games | gameId | — | object, 1837 cols, depth 8 | analysis_broadcasts_data, analysis_home_keyPlayers_passing_data, analysis_home_keyPlayers_rushing_data, analysis_home_keyPlayers_receiving_data, analysis_home_recentResults_data, analysis_away_keyPlayers_passing_data, analysis_away_keyPlayers_rushing_data, analysis_away_keyPlayers_receiving_data, analysis_away_recentResults_data, analysis_series_data_recentMeetings | — |
| `/games/{gameId}/preview/adjusted` | games | gameId | **Returns stored adjusted team and player metrics until game completion. Requires Patreon Tier 1. Team metrics may use the previous season; players remain current-season.** | object, 129 cols, depth 6 | analysis_home_passing_data, analysis_home_rushing_data, analysis_home_kicking_data, analysis_away_passing_data, analysis_away_rushing_data, analysis_away_kicking_data | Patreon Tier 1 |
| `/records` | games | — | `year`: Season year. Required unless `team` is specified.<br>`team`: Team name. Required unless `year` is specified. | array, 35 cols, depth 1 | — | — |
| `/calendar` | games | year | — | array, 7 cols, depth 0 | — | — |
| `/scoreboard` | games | — | `classification`: Division classification. Defaults to `fbs`. | array, 35 cols, depth 1 | homeTeam_lineScores, awayTeam_lineScores | feature flag `scoreboard` |
| `/drives` | drives | year | `team`: Team name on either side of the drive.<br>`conference`: Conference of either team.<br>`classification`: Division classification of either team. | array, 28 cols, depth 1 | — | — |
| `/draft/teams` | draft | — | — | array, 4 cols, depth 0 | — | — |
| `/draft/positions` | draft | — | — | array, 2 cols, depth 0 | — | — |
| `/draft/picks` | draft | — | — | array, 24 cols, depth 1 | — | — |
| `/coaches` | coaches | — | — | array, 4 cols, depth 0 | seasons | — |
| `/coaches/profile` | coaches | coachId | — | object, 22 cols, depth 1 | — | — |
| `/coaches/seasons` | coaches | — | — | array, 68 cols, depth 2 | — | — |
| `/coaches/tenures` | coaches | — | **Returns continuous head-coaching tenures and their attributed records. Requires coachId or team; year and active only filter the results.** | array, 20 cols, depth 1 | — | — |
| `/game/box/advanced` | games | id | — | object, 8 cols, depth 1 | teams_rushingAdvanced, teams_passing, teams_fieldPosition, teams_scoringOpportunities, teams_havoc, teams_rushing, teams_explosiveness, teams_successRates, teams_cumulativePpa, teams_ppa, players_ppa, players_usage, players_rushing, players_passing | — |
