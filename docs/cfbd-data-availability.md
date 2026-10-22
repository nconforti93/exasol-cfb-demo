# CFBD data availability

Transcribed 2026-10-21 from https://api.collegefootballdata.com/data-availability. This is the source for the load plan's per-endpoint `history_from` and known gaps (decision D5). Re-check it when bumping the pinned OpenAPI spec.

| Category | Seasons | Known gaps / notes |
|---|---|---|
| Games, scores, records, matchups | 1869– | No games for 1871; schedules can precede results |
| Season calendar | 2002– | Weeks derived from games |
| Drives | 2001– | Coverage varies by game, especially early |
| Raw plays | 2001– | Coverage and fields vary by game/source |
| Team and player box-score stats | 2004– | |
| Advanced team/game stats | 2001– | PPA from 2001, havoc components 2004, usage components 2013 |
| Havoc | 2004– | |
| Play-level player stats, success rates | 2012– | |
| Enriched passing / rushing | 2025– | CPOE FBS-vs-FBS only |
| Enriched punting | 2015– | Games without play-by-play omitted |
| PPA | 2001– | Not every play qualifies |
| Pregame win probability | 2013– | Derived from spreads; games without a spread omitted |
| In-game win probability | 2014– | Current model only from 2025; earlier not backfilled |
| Elo | 1890– | |
| SRS | 1897– | No 1918 |
| Expanded SRS | 2003– | No 2020 |
| SP+ (team and conference) | 1970– | Current season changes as games are played |
| FPI | 2005– | Third-party schedule |
| CORE ratings | 2016– | |
| Team WEPA | 2008– | |
| Player passing/rushing WEPA | 2013– | |
| Kicker PAAR | 2016– | |
| Team/conference affiliations | 1869– | Accepts a year or range |
| Coaches, seasons, tenures | 1886– | No coach-seasons for 1887 |
| Rosters | 2004– | Field completeness varies |
| Player season stats/overview | 2004– | |
| Player usage | 2013– | |
| Returning production | 2014– | Preseason dataset |
| Team talent | 2015– | |
| Transfer portal | 2021– | |
| AP Top 25 | 1936– | Other polls differ |
| Coaches Poll | 2001– | |
| BCS standings | 2001–2013 | Not every week/season |
| CFP committee rankings | 2014– | |
| HS recruits + team rankings | 2000– | Year = recruiting class |
| JUCO recruits | 2002– | |
| Prep-school recruits | 2012–2022 | |
| CFP tournaments/games | 2014– | Format changes from 2024 |
| NFL draft picks | 1967– | No 1988, 1996 |
| Betting lines, ATS | 2013– | Coverage varies |
| Weather | 2001– | Not every game |
| Media listings | 2003– | Not every game |
