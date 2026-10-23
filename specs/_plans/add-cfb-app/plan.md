# Plan: add-cfb-app

## Summary

Roadmap Phase 6 ("App"): a FastAPI backend and a React frontend over `CFB_API` and `CFB_API_LIVE`. It has four pages (playoff odds with what-if toggles and a rooting guide, predictions with the scorecard, upsets, head-to-head with a live preview), a SQL↔HTTP inspector with a curated virtual-schema console, a refresh button with a live step timeline, an as-of week picker, and the CFBD credit in the footer. It serves demo beats 1–4.

Pages read as a read-only app user, and the console reads the virtual schema as an ordinary reader. A small operator path runs as the namespace owner, only on a loopback bind, for the refresh, the fetch log and the ledger's reconciliation. Every request must name the app's own host, and every action must come from the app's own origin. Every live CFBD call is planned at 0 calls, shown and confirmed before it is sent, never sent twice by the app, and recorded afterwards whatever its outcome. Everything is tested at zero CFBD calls on the synthetic sim league. Live verification on AWS spends at most 12 ledgered calls (8 planned) under the plan `add-cfb-app`, in November, which the owner authorized for 1000 calls.

Frontend work starts now, against validated fixtures, while Phases 2–5 are still being implemented. A demo-minimum cut line names what the mid-November demo needs and what may slip to Phase 8.

Revised on 2026-10-22/23 after the independent review (`review.md`, § Review resolution): 73 scenarios (64 + 9), 38 tasks (3 `[expert]`), 12 parallel groups.

Revised again on 2026-10-23 for the owner's new UI (§ UI revision below): the app opens on a US map of the teams, each team has its own page, and the built pages become league tabs. +3 features, +13 scenarios (86), +9 tasks (9.0–9.8, none `[expert]`), 4 more groups. Light process from now on (owner, 2026-10-23).

## UI revision (2026-10-23)

**This section supersedes the rest of the plan where they conflict:** the "four pages" of the Summary and Goals, the page tabs in the Architecture diagram, the shell's client routes and layout, the "four page tabs" of the demo-minimum cut line, and task 6.2's smoke path. Everything else stands: the three principals, browser safety, the spending guard, the refresh, the inspector, as-of and follow mode, and the behavior of the four built pages. Decisions [34]–[42] in `decision-log.md`.

**Why.** The owner looked at the UI on sample data (2026-10-23): "it isn't that 'pretty' of a UI … I would really like where the opening screen has a map of the US and then each team has their logo geographically where they are (with filter by conference probably). Then when you click the team, it opens a page dedicated to them about record, odds, predictions, etc. Then a different tab(s) has the universal CFB predictions, matchups of the week, and those kinds of cool stats/info." He also asked for a lighter process: a demo, not a product. So this revision has a few scenarios per feature, no separate review, and a thin real-data slice first.

### Information architecture

| Route | Page | Contents | Reuses | Data from |
|---|---|---|---|---|
| `/` | **Map** (home, `app/map`) | US map, FBS logos at their home stadiums; conference chips that zoom to fit; zoom buttons; hover card (record, rank); list view; "Not on the map" | as-of picker (season) | Phases 2–3 |
| `/team/<id>` | **Team** (`app/team`) | Hero (logo, colors, record, rank); schedule and results; SP+ and polls | `InlineBar`, `MoveBadge`, `ChartFrame`, `format.ts` | Phases 2–3 |
| | | Outlook: playoff odds with movement and trajectory; this week's prediction; games to watch and what-if for this team; rivalries leading to head-to-head | `RootingGuide`, `WhatIfPanel` (focus team = this team), `PredictionList` rows | Phases 4–5 |
| `/league/week` | **This week** (`app/league`) | Matchups of the week, ordered by interest; game of the week; upset alerts; stakes | prediction formatting | 2–3 (line, CFBD pregame probability); 4–5 (our pick, stakes) |
| `/league/odds`, `/league/predictions`, `/league/upsets` | Odds board, predictions with scorecard, upsets | As built (5.2–5.4), now tabs, with team marks in their rows | `OddsPage`, `PredictionsPage`, `UpsetsPage` | Phases 4–5 |
| `/league/stats` | **Stats** | Road warriors (`GREAT_CIRCLE_KM`), biggest SP+ movers, conference strength, hottest streaks; each card shows its SQL | `InlineBar`, `ChartFrame` table views | Phases 2–3 (+ P6-UI-c to serve) |
| `/head-to-head` | Head-to-head | As built (5.5); reached from team schedule rows, rivalries and matchup cards | `HeadToHeadPage` | Phase 5 |
| Header | — | Map · League · Head-to-head; team search; as-of picker; quota and ledger badges; refresh; theme; inspector | `Header`, `AsOfPicker`, `QuotaBadge`, `LedgerBadge`, `RefreshPanel`, `ThemeSwitch` | — |
| Drawer | Inspector | As built (5.6); each card's "SQL" button opens it at that card's query-log entry. The console, the refresh timeline and the spend gates are unchanged | `Inspector` | — |

- **Old paths** `/odds`, `/predictions` and `/upsets` render their tab and replace the URL with the `/league/` path, keeping the query.
- **Store.** `PageId` gains `home`, `team`, `week` and `stats`; the old ids stay, so Phase 7's app-state tools keep working. `focusTeam` doubles as the team page's team, so "focus a team" in the odds board, the chat and the team page is one state. New field: `mapConference` (`?conf=`). The default page is `home`.
- **Routing stays hand-rolled** (decision [14] holds): nine paths and one path parameter in `url.ts`.

### Map tech

| Package | Version | Released | License | Role |
|---|---|---|---|---|
| `d3-geo` | 3.1.1 | 2024-03-12 | ISC | `geoAlbersUsa` for team points; `geoPath` (null projection) for the pre-projected states. Pulls `d3-array` 3.2.4 and `internmap` |
| `topojson-client` | 3.1.0 | 2019-11-06 | ISC | `feature` and `mesh` to turn the TopoJSON into state shapes and interior borders |
| `us-atlas` | 3.0.1 | 2023-04-19 | ISC (Census cartographic boundaries, public domain) | `states-albers-10m.json` (82 kB, ≈ 30 kB gzipped): states and nation, already projected with `geoAlbersUsa().scale(1300).translate([487.5, 305])` for a 975 × 610 view box (package README). Hawaii and Alaska are insets |
| `@types/d3-geo` (dev) | 3.1.1 | 2026-07-31 | MIT | types |
| `@types/topojson-client` (dev) | 3.1.5 | 2024-09-16 | MIT | types (pulls `@types/topojson-specification` 1.0.5 and `@types/geojson`) |

Checked against the npm registry on 2026-10-23; all are older than the 7-day rule. They are pinned exactly, like the rest of `package.json`. The JSON is imported by the map module and bundled by Vite into `dist/`, so the map needs no network at the demo. No tile server, no Leaflet or MapLibre, no `d3-zoom` and no `d3-force`.

- **Points** are projected with the same `geoAlbersUsa` parameters as the atlas, so they land on the drawn states. A point the projection rejects (`null`) goes to "Not on the map".
- **Overlap.** `src/map/layout.ts` is a pure function. Markers have a constant size on screen (36 px at the national zoom, 44 px zoomed in), so at view scale `k` their radius in map units is `px / (2k)`. Starting from the projected anchors, sorted by team id, it runs at most 80 passes. Each pass pushes overlapping pairs apart along the line between their centers, pulls each marker back toward its anchor by 10 %, and clamps it to the view box. A marker that ends up more than half its size from its anchor draws a 1 px leader line to a 4 px dot at its true location. The layout is recomputed (memoized) when the filter or the zoom changes. Texas, Florida and the Southeast spread out at the national zoom, and a conference chip zooms them apart for real.
- **Zoom.** It changes the SVG `viewBox`, animated for 200 ms. A conference chip fits the box to the anchors of the conference's teams, with 60 units of padding and a minimum box of 240 × 150. The "+", "−" and "Reset" buttons zoom in steps of 1.5 around the center. There is no wheel or drag pan: it keeps keyboard use and tests simple.
- **Clustering was rejected.** Clusters hide the logos, which are the point of the page.

### Logos

- **`make logos`** runs `scripts/logos.py` (`TARGET=local|aws`, `FORCE=1`, `ALL=1` adds FCS):
  - It reads the logo URLs of the stored team list as the owner (`vs/host/target.py`): `CFB_CORE.TEAMS`, newest fetch per id, FBS by default. It reads stored data only, never `CFBD_VS`, so it costs 0 CFBD calls.
  - It downloads `LOGOS[0]` to `<id>.png` and, when present, `LOGOS[1]` (ESPN's "500-dark") to `<id>-dark.png`, in `CFB_LOGO_DIR` (default `~/.cfb-demo/logos/`, outside the repository and outside OneDrive).
  - **Fetch rules:** `http` is upgraded to `https`; only hosts under `espncdn.com` are fetched, others are skipped and reported; 10 s timeout; 4 in parallel; at most 1 MB; `Content-Type: image/png` and the PNG signature are checked; an existing file is skipped unless `FORCE=1`.
  - It writes `manifest.json` (team id → files, host, bytes, fetched at) and prints "N teams, M files, K skipped, 0 CFBD calls, M external fetches to a.espncdn.com".
- **External fetch.** This is not a CFBD call and touches no quota or ledger, but it does reach outside the machine. It runs only by hand, once, and never from the app or during the demo. The user guide says so (task 7.1).
- **Serving.** `GET /api/logos/{team_id}.png[?variant=dark]` (`routes/logos.py`, read path, no database):
  - The id must be an integer, else 400 before any file access. The file is served from `APP_LOGO_DIR` (default `~/.cfb-demo/logos`), and a missing dark file falls back to the light one. A missing logo answers 404 with JSON.
  - It sends `Cache-Control: public, max-age=86400`. The CSP is unchanged (`img-src 'self' data:`), so a hotlink to ESPN could not load even by mistake.
  - Team responses carry `has_logo`, a check that the file exists, so the browser never asks for a logo that is missing. `make app-preview` serves the same path from the same directory.
- **Fallback badge.** A disc in `COLOR`, with `ABBREVIATION` (else the first four letters of the school) in white or near-black, whichever reaches 4.5:1 on that color, ringed in `ALTERNATE_COLOR`. It is used when `has_logo` is false, when the image fails, and for every team under `APP_LOGOS=off` (recordings that leave private media, owner action U8).
- **Never committed.** The repository holds no team marks (checklist row "No team marks in git").

### Visual design (binding for 9.0–9.4)

The look is a clean sports dashboard: a neutral page, near-white or near-black cards, big tabular numbers, logos as the main imagery, and team colors as accents only. No gradients behind text and no textures. All values are tokens in `src/theme/tokens.ts`, emitted to `theme.css` by `npm run theme:css`. Component CSS uses the tokens and holds no raw sizes or colors.

- **Spacing** `--space-1` to `--space-8`: 4, 8, 12, 16, 24, 32, 48, 64 px.
- **Type scale** (system font stack; every number `tabular-nums`):

  | Token | Size / weight / line height | Use |
  |---|---|---|
  | `--text-label` | 16 px / 600 / 1.3, uppercase, 0.04 em tracking | chips, eyebrow labels, axis ticks |
  | `--text-small` | 16 px / 400 / 1.4 | meta lines, table captions |
  | `--text-body` | 18 px / 400 / 1.45 | body, table cells (unchanged floor) |
  | `--text-title` | 22 px / 700 / 1.25 | card titles |
  | `--text-page` | 30 px / 700 / 1.15 | page titles |
  | `--text-hero` | 40 px / 800 / 1.1 | the team's name |
  | `--text-kpi` | 48 px / 700 / 1 | KPI numbers (P(CFP), record) |

- **Shape and depth:**
  - radii: `--radius-card` 14 px, `--radius-control` 10 px, `--radius-pill` 999 px;
  - `--shadow-card`: in light, `0 1px 2px rgb(0 0 0 / 6%), 0 8px 24px rgb(0 0 0 / 6%)`; in dark, `none`, and the border carries the edge;
  - `--shadow-raised`: for a hovered marker;
  - new colors `--surface-raised` (light `#ffffff`, dark `#222220`) and `--border-subtle` (the grid color). Both are added to the contrast pairs of `tokens.test.ts`.
- **Layout.**
  - Content is at most 1320 px wide and centered, with 24 px of page padding (32 px from 1440 px).
  - A 12-column grid with 24 px gaps; cards span 4, 6, 8 or 12 columns, and everything stacks at 1100 px or less.
  - Design for a 1280 × 720 projector first and a 1920 × 1080 laptop second.
- **Cards** (`src/ui/Card.tsx`):
  - a `--surface` background, a 1 px `--border-subtle` border, `--radius-card` and `--shadow-card`, with 20 px of padding (24 px from 1200 px);
  - the header holds an optional eyebrow label and the title on the left, and the actions on the right: an "SQL" ghost button with a code icon (`SqlButton`), then info;
  - the body has a 12 px gap;
  - the footer is a meta line in `--text-small` muted, for example "0 calls · 38 ms · CFB_API.TEAM_TRAVEL".
- **Team colors** (`src/theme/teamColor.ts`, pure, unit-tested):
  - **Choosing the accent.** `teamAccent(team, theme)` returns `COLOR` if it reaches 3:1 against `--surface` in that theme, else `ALTERNATE_COLOR` if that does, else `--accent`.
  - **Where it appears:** the 6 px top stripe of the team hero and of team-scoped cards, the marker ring, the team's mark in charts and the focus highlight.
  - **Never for text, never as a fill behind text,** except the badge, whose text color is computed as above.
  - **Validation.** A value that is not `#RRGGBB` (CFBD sends it with or without the `#`) counts as missing.
  - **Applying it.** Through SVG presentation attributes or a CSS custom property set by React's `style` (CSSOM, which the CSP allows), never through a `style` attribute in `index.html`.
- **Shared components** (`src/ui/`): `Card`, `Chip`, `TeamMark` (sizes 24, 32, 48, 96 and 112; logo or badge; the dark variant in the dark theme), `RankTag` ("#6"), `RecordLine` ("7–1 · 4–1 Big Ten"), `StatTile` (a label, a KPI number and a `MoveBadge` delta) and `SqlButton`.
- **Map style:**
  - states in `--surface` with `--axis` 1 px borders (`vector-effect: non-scaling-stroke`) and the nation outline in `--border`;
  - each marker is a `--surface-raised` disc with a 2.5 px ring in the team accent and the logo inset by 4 px;
  - hover and focus scale the marker to 1.2 and add `--shadow-raised`;
  - leader lines are `--ink-muted`.
- **Motion.** 200 ms ease-out for zoom and hover, and none under `prefers-reduced-motion`.
- **The existing rules hold:** contrast floors in both themes, states shown as an icon plus a word, and a table view for every chart.

### API additions

All are on the read path, with fixed SQL in `sqlcatalog.py` over `CFB_API` only (the existing lint applies), and are validated like every point route. Each response carries `queries: Record<string, string>`, which maps a panel to this session's query-log entry; it feeds the cards' "SQL" buttons.

| Method and path | Reads | Needs |
|---|---|---|
| `GET /api/teams/map?season=&week=` | `TEAMS` (+ P6-UI-b columns), `TEAM_SEASONS` (FBS of the season, conference, record), `RANKINGS` (AP at P); `has_logo` | Phases 2–3 + P6-UI-b, d |
| `GET /api/team/{id}?season=&week=` | `TEAMS`, `TEAM_SEASONS`, `GAMES` (schedule, results, `PREGAME_SPREAD`, `HOME_PREGAME_WIN_PROB`), `RANKINGS`, `SP_RATINGS` (P's season and the one before) | same |
| `GET /api/team/{id}/outlook?season=&week=` | `PLAYOFF_ODDS` (the team at every published point of the season), `PUBLISHED_RUNS`, `PREDICTIONS` (the team's next game), `ROOTING_GUIDE` (the team), `MATCHUP_SERIES` (the team's 5 most-played series), `CURRENT_POINT`; each panel `{available, reason}` | Phases 4–5 |
| `GET /api/league/week?season=&week=` | `GAMES` (P's next week, FBS), `RANKINGS`; with a run, `PREDICTIONS` and `ROOTING_GUIDE` (max `LEVERAGE` per game) | Phases 2–3; 4–5 enrich |
| `GET /api/league/stats?season=&week=` | `TEAM_TRAVEL` (P6-UI-c), `SP_RATINGS`, `TEAM_SEASONS`, `GAMES` | Phases 2–3 + P6-UI-c, d |
| `GET /api/logos/{team_id}.png?variant=dark` | a file in `APP_LOGO_DIR`; no database | — |

New settings: `APP_LOGO_DIR` (default `~/.cfb-demo/logos`) and `APP_LOGOS` (`on` by default, or `off`). The existing `/api/teams` feeds the header search. The routes of the built pages are unchanged (3.1–3.5).

### A real-data snapshot for the preview

Component tests keep the synthetic, committed fixtures; they are the contract. To look at real data before the backend serves it, `make app-fixtures [TARGET=aws] [SEASON=2026]` runs `scripts/app_fixtures.py`:
- **What it reads.** As the owner, it reads the stored model and team list: `CFB_MODEL` `TEAM`, `VENUE`, `GAME`, `TEAM_GAME`, `RANKING`, `RATING_SP`, `SEASON_WEEK` and `GREAT_CIRCLE_KM`, plus `CFB_CORE.TEAMS` for colors until P6-UI-a lands. A unit test asserts that no statement names `CFBD_VS`, so it costs 0 calls.
- **What it writes.** The response shapes of the thin slice (`points`, `teams`, `teams-map`, one `team.<id>` per FBS team, `league-week`, `league-stats`), each validated by `app/backend/models.py` before it is written. They go to `~/.cfb-demo/fixtures/<target>-<season>/`, outside the repository (CFBD terms: no raw data in git). The current point is the season's last week whose `SEASON_WEEK.IS_DONE` is TRUE; `publication_id` is null.
- **How it is served.** `make app-preview REAL=1` (or `REAL=<dir>`) serves that directory first and the synthetic fixtures for everything else, such as the odds. The banner reads "PREVIEW · real data snapshot of <target>, <exported at>; read-only". This SQL is a throwaway twin of 9.7's: once `make app-serve` serves the same pages, the snapshot is only a fallback for offline design work.

### Sequencing: what is usable when

| Slice | What works | Needs | Tasks |
|---|---|---|---|
| **S0, today** | The whole new UI on synthetic fixtures (`make app-preview`) | nothing | 9.0–9.4, 9.5 |
| **S1, today or tomorrow** | Map, team page (hero, schedule and results, SP+, polls), this week (line, CFBD pregame probability) and stats on **real data** through the preview, with real logos | The AWS backfill done (`CFB_CORE`); Phase 3's views deployed on that target (deploy only); `make logos` and `make app-fixtures` run once | 9.5, 9.6 |
| **S2** | The same pages served by `make app-serve` from the real database | Phase 2 merged; P6-UI-b, c, d deployed; 2.2 (its gate moves up to this) | 2.2, 9.7 |
| **S3** | Team outlook (odds, trajectory, this week, games to watch, what-if, rivalries), the odds, predictions and upsets tabs, our picks and stakes in this week, head-to-head | Phase 4 publishing runs; Phase 5 merged | 3.1–3.5, 9.8 |
| **S4** | Refresh with its timeline, the inspector console, the live preview | Unchanged: Phase 2 merged, then 1.9, 2.4, 4.1–4.3 | unchanged |

Blocked on Phase 4 and 5: every number from a simulation (odds, movement, trajectory, what-if, rooting guide, stakes), our predictions and the scorecard, and upsets (Phase 4's Elo-based magnitude). Blocked on Phase 5 alone: rivalries and head-to-head (`MATCHUP_*`), and serving the thin slice (P6-UI-b, c, d). Blocked on nothing but data: the map, the team's record, schedule, SP+ and polls, this week's slate with lines, and the stats.

### Hand-offs

- **P6-UI-a (Phase 3):** `CFB_MODEL.TEAM` adds `COLOR` and `ALTERNATE_COLOR` from the stored team list (`CFB_CORE.TEAMS`, newest fetch, as CFBD reports them; NULL for a team not in the list). Coordinates need nothing new: `TEAM.HOME_VENUE_ID` → `VENUE.LATITUDE`, `LONGITUDE`. The logo URLs stay out of the model; only the owner's `make logos` reads them.
- **P6-UI-b (Phase 5):** `CFB_API.TEAMS` adds `MASCOT`, `ABBREVIATION`, `COLOR`, `ALTERNATE_COLOR`, `HOME_VENUE_NAME`, `HOME_CITY`, `HOME_STATE`, `LATITUDE` and `LONGITUDE`, from `TEAM` and from `VENUE` on `HOME_VENUE_ID`.
- **P6-UI-c (Phase 5):** a new view, `CFB_API.TEAM_TRAVEL`, with one row per stored team game: `TEAM_ID`, `SEASON`, `WEEK_SEQ`, `GAME_ID`, `SITE`, `VENUE_ID` and `KM_FROM_HOME`.
  - `KM_FROM_HOME` is `GREAT_CIRCLE_KM` from the team's current home venue to the game's venue: 0 at home, NULL when a venue lacks coordinates.
  - As a view it runs the function with the owner's rights, so `CFB_APP` needs no `EXECUTE`.
  - `CFB_API` grows from 26 to 27 views. The chat's `CFB_MCP` sees it too, at 0 calls.
- **P6-UI-d (Phase 5, order):** deploy the thin-slice views first, before Phase 4 has published anything: `TEAMS` (b), `GAMES`, `TEAM_SEASONS`, `RANKINGS`, `SP_RATINGS`, `TEAM_TRAVEL` (c), `CURRENT_POINT` and `AS_OF_POINTS`. They must answer then, with the publication and what-if columns NULL and `HAS_PUBLICATION` FALSE.
- **Phase 4:** none. The team page reads Phase 4 only through Phase 5's views. The trajectory needs one publication per week, which the weekly refresh already makes.
- **Phase 7 (update to P7-a):** `PageId` gains `home`, `team`, `week` and `stats`, and the old ids are unchanged. The team page's team is `focusTeam`, and the new store field is `mapConference`. No tool signature changes; "set the focus team" now also drives the team page.
- **Phase 8:**
  - The demo opens on the map. Beat 1 becomes "click the bubble team on the map" → its page with odds, movement and games to watch. Beat 4's pages are the league tabs and the team page.
  - Copy `~/.cfb-demo/logos` with the rehearsal snapshot.
  - Use `APP_LOGOS=off` for any recording that leaves private media.

### Tests (light process)

A few component tests per page, on synthetic fixtures in `StrictMode`, one per new scenario (Scenario Coverage); unit tests for the pure parts (`layout.ts`, `teamColor.ts`, `url.ts`, the logo fetch rules, the snapshot mapping); one integration file for the served thin slice (`tests/it/test_app_team_it.py`, 9.7/9.8). There is no new E2E: the light process replaces task 6.2's Playwright smoke with a manual walk in V.3 (map → team → league → head-to-head → inspector).

## Design

### Context

The predecessor plans are binding (interview item 1):

- **Phase 5** (`specs/_plans/add-cfb-analyses/`, revised 2026-10-22) is the contract the app consumes.
  - **Serving schemas.** `CFB_API` has 26 views (3 of them generated per publication) and costs 0 calls. `CFB_API_LIVE` holds `GAME_PREVIEW` (1 call per query) and `QUOTA` (`/info`, free; the row of `CFB_META.QUOTA`).
  - **Role.** `CFB_APP` has `SELECT` on both schemas.
  - **Page contract.** Consumers use autocommit; pages start from `CURRENT_POINT` and never offer `IS_AFTER_CURRENT` points; the `IS_HEADLINE` and `FREEZE_KIND` labels; "T-n" ties with `PROB_SOURCE` beside every rank; the what-if run guard and `WHATIF_VIEWS_CURRENT`; the forced-run labels.
  - **`CURRENT_POINT`** carries `AS_OF_SEASON`, `AS_OF_WEEK_SEQ`, `NEXT_WEEK_SEQ`, `PUBLICATION_ID`, `WHATIF_RUN_ID`, `WHATIF_AS_OF_WEEK_SEQ` and `WHATIF_VIEWS_CURRENT`, but no what-if season.
  - **Preview UX contract.** Never on page load; one literal id; a fetching state; the next meeting, else the latest one.
  - **What-if library.** `analyses/whatif.py`: `what_if_odds_sql` and `rooting_guide_sql`, both with the run guard.
  - **Thresholds.** The 0.0070 arrow threshold and the sample thresholds in `ANALYSIS_SETTINGS`.
  - **Pass-through views** for the chat, among them `CFB_API.GAMES` over `CFB_MODEL.GAME`.
  - **Open Phase 6 decisions it hands over:**
    - the backend's fetch-log writes for previews;
    - an owner path for refresh, forced re-sims and publication;
    - preview caching per session.
- **Phase 2** (`specs/_plans/add-cfb-persistence-refresh/`, worktree `../exasol-cfb-demo-p2`) provides:
  - `REFRESH_WEEK(season, week, season_type)`, whose steps commit their `REFRESH_LOG` rows at start and end; another session sees the progress (its scenario "Other sessions see the progress while the refresh runs"). Its live `CHECK` compares the estimate with `QUOTA.REFRESH_SPENDABLE`, and it checks again before each table. It reads `/info` only for live runs with due slices. After a kill or a client abort nothing more can be logged; the next run frees the `RUNNING` row (`refresh_week.lua` header);
  - the run lock, which refuses concurrent runs and names the running run;
  - `LOAD_RUN` (`RUN_ID`, `RUN_KIND`, `SESSION_ID`, `RUN_STATUS`, `CALLS`, `REMAINING_BEFORE`, …) and `FETCH_LOG` with `CONTEXT = '<RUN_KIND>#<RUN_ID>'`;
  - **`CFB_META.QUOTA`** (`loader/quota`): `REFRESH_SPENDABLE = GREATEST(0, LEAST(REMAINING_CALLS, PROJECT_BUDGET − USED_CALLS))` and `BACKFILL_SPENDABLE = GREATEST(0, REFRESH_SPENDABLE − REFRESH_RESERVE)`;
  - **`vs/host/ledger.py`** with the caps file `vs/live/ledger.yaml` (owner-edited). On the Phase 2 branch (commits `bb34528`, `ac151ab`) it holds the plans `add-cfb-app: 12` and `app: 60` and the months `2026-10: 400` and `2026-11: 1000`. `Ledger.check(plan, month, estimate, used_calls, headroom=)` refuses a plan or month without a cap. **`Ledger.exclusive()` does not wait** (verified in the source): it opens the lock file beside the ledger on every call and takes `flock(LOCK_EX | LOCK_NB)`. When any other holder has it, another process or another thread of the same process, it raises `LedgerError` at once. A disabled ledger takes no lock. `LedgerRefused` is a subclass of `LedgerError`;
  - the guarded host wrapper `scripts/loader_cli.py`: estimate, live check, quota, `Ledger.check`/`room`/`record_run`, the `LEDGER=off` rule, and `--prefix` without a ledger. It takes the ledger lock for every run of the real namespace, replay included, and records a run whatever its outcome, but only while its own process lives;
  - the harness, with `TEST_STEP_SLEEP`, `TEST_STEP_FAIL`, `set_now`, `refresh` and recordings.
- **Phase 4** provides the run registry fields behind `PUBLISHED_RUNS` and the harness's `hold_deploy_lock`.
- **Phase 3** provides the as-of points (`AS_OF_POINT`, `CURRENT_AS_OF`, week sequences): a week is done when all its games are completed.
- **The foundation** (recorded) provides:
  - `vs/host/target.py`: `TARGET`, with the sys password read at connect time and TLS pinned on AWS;
  - `vs/host/fetchlog.py`: `parse_manifest`, `build_fetch_log_rows` (a planned fetched slice without staged rows is logged with NULL status and 0 rows) and `insert_fetch_log`;
  - `scripts/record.py --query`, the guarded virtual-schema query, with `planned_live`, `slice_calls`, `query_costs` and `charged`;
  - the fetch function's retry rules (`vs/cfbd_vs/http.py`): `MAX_ATTEMPTS` 4, 30 s per attempt, backoff 1 s × 2ⁿ with ±20 % jitter, `Retry-After` honored up to 30 s; name resolution over DoH with a 10 s timeout. A slice can therefore take about 220 s at worst;
  - stored-only queries: with `REQ_SOURCE = 'STORED'` the plan contains no fetch and no call budget applies (`cfbd-fetch/read-through`, "Stored-only queries never call the API");
  - the role `CFB_READER` (`SELECT` on `CFBD_VS`, `make grants`) and, in `TEST_`, the user `CFB_TEST_READER`;
  - the mock sidecar, with `script(path, status=, delay_ms=, times=)`;
  - the security-model facts: readers may run `EXPLAIN VIRTUAL` (probe 1.4g), and the key never appears in it.
- **Roadmap demo flow, beats 1–4.**
  1. The hook (the app as of Sunday before week N+1).
  2. The inspector shows the HTTP behind SQL, the fan-out and the 0-call contradiction.
  3. The `REFRESH_WEEK` timeline.
  4. The pages move: the scorecard, the upset of the week ranked all-time, the odds with arrows, what-if toggles and a rooting guide on a bubble team, and head-to-head with a live preview on click.

Forces:
- **A read-only app and a refresh button.** `CFB_APP` can only `SELECT`, but the refresh writes `CFB_CORE`, `CFB_META`, `CFB_SIM`, `CFB_PRED` and DDL in `CFB_API`, and the fetch log needs `INSERT`. Exasol Lua scripts run with the caller's rights. The project memory has this as verified on 2026-06-05 in the preprocessor framework; the skills do not cover it; probe 1.1(a) re-checks it. So "a runner user with only `EXECUTE` on `REFRESH_WEEK`", the interview's example, cannot write anything the refresh writes.
- **Beat 2 needs the virtual schema itself.** The app role cannot read `CFBD_VS`, and the only virtual-table view it can read is `GAME_PREVIEW`, which has no year parameter. Showing a year range fanning out needs another principal, and a reader of the virtual schema is enough.
- **Loopback alone is not a boundary.** With DNS rebinding a foreign page reaches `127.0.0.1` as if it were same-origin; only the `Host` header still differs. A Vite dev server started with `--host`, or a proxy that rewrites `Host`, opens the owner path to the network.
- **Quota and honesty.**
  - CFBD calls are scarce: 400 in October, 1000 in November (authorized by the owner on 2026-10-22). The plan caps `add-cfb-app: 12` and `app: 60` exist on the Phase 2 branch.
  - The ledger is the only record of live calls, and "live CFBD spend must always be explicit" (interview item 3).
  - A statement can fail or time out after CFBD has answered; a process can die between spending and recording.
  - The ledger lock does not wait, and a refresh holds it for its whole run.
  - Reading `/info` around every click would roughly double the preview's latency against the 2 s target.
- **Drivers.** A pyexasol connection is one Exasol session, runs one statement at a time, and cannot be shared between threads (threadsafety level 1; pyexasol docs via context7).
- **Demo-grade UI.** It must be legible on a projector, offer light and dark themes, credit CFBD, and look the same offline in replay mode.
- **Phase 8 on AWS.** The `aws` target pins TLS (`vs/host/target.py`), the app must run as a single process, and AWS has no BucketFS replay host directory, so the AWS demo is live-only.
- **Minimal, current frontend dependencies.** TypeScript 7.0.2 is current, but typescript-eslint 8.72.0 requires TypeScript below 6.1.0. FastAPI shipped 23 releases since 2026-10-19, 8 of them in 29 hours.
- **Schedule.** The demo is around mid-November. On 2026-10-22 Phase 2 is mid-way through its tasks, Phase 3 is in group A, Phase 4 has not started and Phase 5 is reviewed but not implemented. Work that needs none of them cannot wait for all of them.
- **Process.**
  - No database session during planning, and zero CFBD calls.
  - `docs/roadmap.md`, `specs/architecture.md` and `CLAUDE.md` are edited only by the docs tasks.
  - Local tests run in "local free" windows under the IT lock. On AWS: deploy and run only.

**Goals**
- A backend that reads every page through the app user and names only `CFB_API` and `CFB_API_LIVE` objects; a console path as a reader of the virtual schema; and an operator path with a fixed allowlist that is on only when every bind address is loopback.
- **Browser safety:** a `Host` allowlist on every request, a configured `Origin` set for every `POST`, a pinned Vite dev server, and a start check that proves the users' effective privileges.
- **Four pages:**
  - odds, with arrows, a run banner, what-if toggles and the rooting guide;
  - predictions, with the scorecard and calibration;
  - upsets, with all-time and line-era ranks;
  - head-to-head, with a click-only live preview of a server-chosen game and a per-session cache.
- **The inspector:**
  - a per-session query log with the exact SQL;
  - `EXPLAIN VIRTUAL` on demand, by the same principal, with each slice shown as its HTTP request;
  - the `REQ_*` columns and a waterfall;
  - the refresh's requests, as they arrive;
  - a four-query console for beat 2, including 157 stored seasons at 0 calls.
- **Refresh:** a plan at 0 calls that also says whether the point will move, confirmation of any live spend, a background run on the operator path, a timeline that follows any refresh run, and clean refusals.
- **The as-of picker,** addressable by URL, following the current point by default, and reloading only after a refresh has published.
- **Explicit spending:** a 0-call plan, a cost label or confirmation, a server-side re-plan against `max_calls`, ledger and quota guards, one live request at a time, never a retried live statement, a ledger entry per live request even on failure, fetch-log rows, and reconciliation of loader runs that died.
- **The shell:** one process serving the API and the built frontend; dev with Vite; a strict CSP with nothing it would block; loopback by default; light and dark themes legible on a projector; the CFBD credit; a clear outage state; the ledger state in the header; a reserved chat slot.
- **Make targets:** `make app`, `make app-build`, `make app-serve`, `make app-user`, `make app-test`, `make app-e2e` and `make app-perf`, with frontend lint in `make lint` and vitest in `make test`.
- **Tests:** API integration tests on the `TEST_` stack, component tests with vitest and Testing Library, a Playwright smoke (local or in the podman image) or its API-level fallback, and zero CFBD calls.
- **A demo-minimum cut line** that the orchestrator can apply if a predecessor lands late.

**Non-Goals**
- The chat panel and its tools (Phase 7). Only the slot, the state store and the reserved `/api/chat/` path exist here.
- Demo hardening, the AWS scale dial, the replay rehearsal recipe and `docs/demo-script.md` (Phase 8). The app's support for them is listed under Dependencies.
- **Owner actions other than the refresh.** Forced re-sims (`make simulate BASE=`), manual publications (`make publish`), predictions (`make predict`) and drops stay CLI owner actions. The forced runs' odds are not shown (they serve no beat; the chat can read `FORCED_RUN_ODDS`).
- Free-text SQL in the app. The console runs four curated queries; ad-hoc SQL belongs in a SQL client.
- **The operator path on a non-loopback bind.** It would need authentication, which is a Phase 8 decision. The app can still be served on a network address read-only, with that address in `APP_ALLOWED_HOSTS`.
- **A per-start operator token** (review M1, item 6): after DNS rebinding an attacker can read `index.html` too, so the token would add little. Real authentication is Phase 8's.
- Users, logins, multi-tenant use, mobile layouts (mission out of scope).
- `PROMOTE()` of previews, and any preview cache beyond the browser session.
- New database objects, apart from the two users that `make app-user` creates: the read user and the console user.
- Server-side caching of page data. The database answers within budget, and freshness after a refresh matters more.
- A coverage gate on the frontend.
- Changing `Ledger.exclusive()` or the CLI's lock and record behavior. The app designs around them.

### Decision

The backend has three principals:
- **The read path,** the app user `CFB_APP_WEB` with the role `CFB_APP`. Every page, the what-if queries, the preview fetch and the timeline run on it, from fixed SQL over the serving schemas.
- **The console path,** the console user `CFB_CONSOLE_WEB` with the role `CFB_READER`. It explains and runs the four curated console queries over `CFBD_VS`, so the inspector shows what any reader with `SELECT` gets.
- **The operator path,** the namespace owner, read at connect time through `vs/host/target.py`. It runs only an allowlist (the refresh, the fetch-log rows of the app's own requests, the reconciliation of loader runs with the ledger, and the privilege check's fallback), and only when every address the server binds to is loopback.

Every request passes a `Host` allowlist, and every `POST` a configured `Origin` set, before routing. At start the backend proves the read and console users' effective privileges.

Every action that can call CFBD goes through one spending guard: plan at 0 calls, label or confirmation, re-plan against `max_calls`, ledger and quota, then act behind an in-process live gate and, for live plans only, the ledger lock. A live statement runs once, on a session whose timeout outlives the fetch function, and a `finally` writes a ledger entry per live request, charging `MAX_ATTEMPTS` per planned slice when it failed, before the fetch-log rows. Reconciliation records any loader run that spent calls without a ledger entry. The refresh reuses Phase 2's own guard, extracted from the CLI.

The frontend is a dependency-light React app: no router, state, data-fetching or chart library. It is served by the same FastAPI process in production and by a pinned Vite in development. It follows the current point by a key that changes with the publication, and holds its reloads while a refresh runs.

#### Architecture

```
 Browser: React 19 app (built by Vite)                        AppState store ─▶ chat slot (Phase 7; not rendered)
   pages  Odds · Predictions · Upsets · Head-to-head     header  as-of picker · quota + ledger badge · refresh · theme · inspector
     │  fetch /api/*, same origin; one AbortController per page load; every POST: X-CFB-Action: 1, JSON, never retried
     ▼
 FastAPI  app/backend   one uvicorn worker, 127.0.0.1:8000; serves app/frontend/dist (SPA fallback, CSP) in production
   ├── security     Host ∈ allowlist (name and port) on every request, else 400 · POST: X-CFB-Action, JSON,
   │                Origin ∈ configured set (never derived from Host), else 403 · CORS only for APP_CORS_ORIGINS
   ├── read path    ReadPool: 4 sessions as CFB_APP_WEB (role CFB_APP), autocommit, QUERY_TIMEOUT 30 s, checkout ≤ 5 s
   │     │          (503 busy), broken sessions replaced; 1 retry on a conflict, for 0-call catalog statements only
   │     routes ── sqlcatalog (fixed SQL over CFB_API / CFB_API_LIVE) ── analyses/whatif.py (what-if, conditional guide)
   │     per browser session: query log (stmt.query), preview cache (15 min, 20), single flight   process: quota reading (5 min)
   ├── live.py      the only module that names GAME_PREVIEW. Live plans: live gate (in-process, wait ≤ 5 s) → busy checks
   │                (own refresh; newest refresh run RUNNING; reconciliation pending) → Ledger.exclusive() (held → 409 live_busy)
   │                → re-plan, ledger, quota → run ONCE on the live session (CFB_APP_WEB, QUERY_TIMEOUT 300 s, socket 330 s)
   │                or the console session → finally: ledger entries (failure: MAX_ATTEMPTS per planned slice), then fetch log
   ├── console path CFB_CONSOLE_WEB (role CFB_READER): 4 curated statements over CFBD_VS; EXPLAIN VIRTUAL; guarded run via live.py
   └── operator path owner (sys via vs/host/target.py; CFB_TEST_OWNER in TEST_); on only if every bind address is loopback
         ops/refresh   ── loader/guard.py (extracted from scripts/loader_cli.py) ── EXECUTE SCRIPT CFB_INFRA.REFRESH_WEEK(…)
                          background thread, own connection (socket 900 s); session id noted → run id looked up early;
                          live runs hold the ledger lock for the whole run; disabled for live under make app (--reload)
         ops/fetchlog  ── INSERT INTO CFB_META.FETCH_LOG …  CONTEXT APP_PREVIEW#<action> / APP_CONSOLE#<action>
         ops/reconcile ── loader/guard.reconcile: LOAD_RUN ⟷ ledger under the ledger lock (start; before a live refresh;
                          when the timeline sees a run end); records REFRESH_WEEK#/BACKFILL# runs that spent without an entry
     ▼
 Exasol   CFB_API (26 views) · CFB_API_LIVE (GAME_PREVIEW, QUOTA)                     ◀── read path
          CFBD_VS ──▶ CFBD API v2 (live) or BucketFS recordings (replay)               ◀── console path; read path via GAME_PREVIEW
          CFB_INFRA.REFRESH_WEEK: 1 CHECK · FETCH … · 100 MODEL · 200 PREDICT · 300 SIMULATE · 400 PUBLISH
            (a REFRESH_LOG row committed at each step's start and end) ──▶ CFB_API.REFRESH_TIMELINE / FETCHES
            (polled every 1 s while a refresh runs, else every 5 s, with CURRENT_POINT's follow key; follows runs started anywhere)
 Host     cache/live-calls.jsonl (+ .lock; plans app and add-cfb-app) · vs/live/ledger.yaml (caps; owner-edited)
          .env (CFB_APP_PASSWORD, CFB_CONSOLE_PASSWORD; git-ignored)
          ~/.exasol/personal/deployments/<local|default>/secrets.json (sys; read at connect, never stored)
```

#### Patterns

| Pattern | Where | Why |
|---------|-------|-----|
| Three principals: a read-only path for everything a page shows, a reader of the virtual schema for the console, an owner path for an allowlist | `db.py`, `console.py`, `operator.py`, `ops/*` | Lua scripts run with the caller's rights, so only an owner can refresh; views run with the owner's rights, so pages need only the serving schemas. A bug in a page cannot write, the console shows what any reader gets, and the owner path cannot run arbitrary SQL |
| Capability tied to the bind address | `settings.py` | The owner path exists only where the presenter's machine is the only client. Every resolved bind address must be loopback; a name is not trusted by its spelling. A network-exposed app is read-only by construction, until Phase 8 adds authentication |
| `Host` allowlist and a configured `Origin` set | `security.py` | After DNS rebinding a foreign page is same-origin with the app; only `Host` still tells them apart, so an origin derived from `Host` would pass. Starlette's `TrustedHostMiddleware` compares names only (the port is dropped), so the app checks name and port itself |
| Positive privilege check | `db.py` | A name check misses what a role, `PUBLIC`, a connection grant, an impersonation grant or ownership adds. Comparing the effective set with the exact expected set catches every route |
| Plan free, act guarded, record always | `spend.py`, `live.py`, `loader/guard.py` | `EXPLAIN VIRTUAL` and the refresh estimate cost 0 calls, so every button can state its cost. The server re-plans against `max_calls`, so a mode switch between showing and clicking can never spend unseen calls. The ledger and the fetch log are written by the same code that spent |
| Charge in `finally`, never retry | `live.py` | A failed or timed-out statement may already have called CFBD up to 4 times per slice. Over-counting is the safe direction; a retried live statement would be a second, unconfirmed spend |
| Lock only what spends; one flight per question | `live.py`, `sessions.py` | `Ledger.exclusive()` does not wait. Taking it for 0-call plans would fail every replay click during a refresh, and two threads of the app would fail each other. An in-process gate queues the app's own live requests, and a single flight turns a double click into one fetch plus a cache hit |
| Reconcile against the database's run log | `loader/guard.py`, `ops/reconcile.py` | The ledger record is written by the process that spent, and a process that dies cannot write it. `LOAD_RUN` and the committed step rows survive, so the next start records what is missing |
| One guard for CLI and app | `loader/guard.py` | `make refresh` and the refresh button check and record the same way; only the ledger plan, the lock policy and the retry headroom differ |
| Fixed SQL catalog with a serving-schema lint | `sqlcatalog.py` | Pages cannot drift into `CFB_SIM` or `CFB_META`, and only `live.py` names `GAME_PREVIEW`. The inspector shows texts that a reviewer has seen |
| Curated console instead of free text | `console_catalog.py` | Beat 2's three promises (fan-out, contradiction, stored) plus the budget refusal, with exact expected plans and bounded spend, and no injection surface |
| Same-principal `EXPLAIN` | `explain.py`, inspector | The plan describes exactly what that principal's query does. Readers may explain (probe 1.4g), so no owner rights are needed for page, preview or console plans |
| Follow the publication, not the point | timeline poll, `store.ts` | Every refresh step commits its row (Phase 2), so polling follows refreshes started anywhere. The point moves when the games merge, a minute before the publication exists, so the key includes the publication and reloads wait for the run to end |
| Server-recorded statement text | query log | pyexasol's `stmt.query` is the text the database received. The client only references log entries by id, so it can never submit SQL |
| Per-session state, never global | `sessions.py` | The preview cache, the query log and the in-flight requests belong to the browser that spent or ran them. An anonymous cookie grants nothing |
| Dependency-light frontend | `app/frontend` | React, React DOM and Vite only at runtime and build. Charts are a few SVG forms after the dataviz method. Fewer moving parts for a public Labs repo |
| Contract fixtures validated on both sides | `src/test/fixtures`, `tests/unit/test_app_fixtures.py` | The component tests use synthetic API responses that the backend's response models must parse, so the frontend can be built before the backend and its tests cannot drift from the API |

#### Key Interfaces

**Pages and the views they read** (all on the read path; columns per `api/serving-contract` and the Phase 5 specs)

| Page / element | Views | Filter |
|---|---|---|
| Header: point, picker | `CFB_API.CURRENT_POINT`, `CFB_API.AS_OF_POINTS` | points with `IS_AFTER_CURRENT` FALSE, one season at a time |
| Header: quota badge | `CFB_API_LIVE.QUOTA` | live mode only: one reading per 5 min, or on request (free `/info`); in replay "replay · no calls" and no query |
| Header: ledger badge | `GET /api/config` | "ledger off", or the reason live actions are disabled |
| Odds | `PLAYOFF_ODDS`, `PUBLISHED_RUNS`, `ANALYSIS_SETTINGS` | the point (`AS_OF_SEASON`, `AS_OF_WEEK_SEQ`) |
| Odds: what-if | `WHATIF_RUN`, `WHATIF_GAMES`; `what_if_odds_sql` (reads `WHATIF_OUTCOMES`, `WHATIF_PLAYOFF`, `PLAYOFF_ODDS`, `WHATIF_RUN`, `ANALYSIS_SETTINGS`) | the what-if run only (`CURRENT_POINT.WHATIF_RUN_ID`), while `WHATIF_VIEWS_CURRENT`, at (season of `WHATIF_RUN`, `WHATIF_AS_OF_WEEK_SEQ`) |
| Odds: rooting guide | `ROOTING_GUIDE`; `rooting_guide_sql` over the coming week | the run of the point's publication; the focus team |
| Predictions | `PREDICTIONS` (next week of P; games played in P's week), `SCORECARD_WEEKS`, `SCORECARD_SEASONS`, `SCORECARD_CALIBRATION` | point, population |
| Upsets | `UPSETS` | P's week; `ALL_TIME_RANK` ≤ 25; `LINE_ERA_RANK` ≤ 25 |
| Head-to-head | `TEAMS`, `MATCHUP_SERIES`, `MATCHUP_GAMES`, `MATCHUP_NEXT` | the ordered pair; next meeting at the current point |
| Head-to-head: preview | `CFB_API_LIVE.GAME_PREVIEW` (in `live.py` only) | `GAME_ID = <literal integer>` the server took from `MATCHUP_NEXT`, else `MATCHUP_SERIES.LAST_GAME_ID`; on click only |
| Refresh plan: week completeness | `CFB_API.GAMES` | the target season, week and season type: games, completed, not yet kicked off |
| Timeline, inspector | `REFRESH_TIMELINE`, `FETCHES`, `CURRENT_POINT` (follow key) | the newest refresh run, or a chosen one; `CONTEXT = 'REFRESH_WEEK#<run>'` |

**HTTP API** (JSON; snake_case keys are the view columns in lower case; every request needs an allowed `Host`; every `POST` needs `X-CFB-Action: 1`, `Content-Type: application/json` and an allowed `Origin`)

| Method and path | Principal | Reads or does | Calls |
|---|---|---|---|
| `GET /api/config` | — | target; operator `{enabled, reason}`; console `{enabled, reason}`; ledger `{on, plan, plan_cap, plan_room, month, month_cap, month_room}` (file totals); live `{enabled, reasons}`; `read_check_skipped`; `dev`; `fetch_mode`; `chat: false`; version | 0 |
| `GET /api/health` | read | `SELECT 1`; 503 when unreachable | 0 |
| `GET /api/points?season=` | read | `CURRENT_POINT`; `AS_OF_POINTS` of the season (default: the current one); the list of seasons | 0 |
| `GET /api/teams` | read | `TEAMS` | 0 |
| `GET /api/odds?season=&week=` | read | odds rows, run banner, settings, what-if availability `{available, reason, run_id}` | 0 |
| `GET /api/whatif/games` | read | `WHATIF_RUN`, `WHATIF_GAMES` | 0 |
| `POST /api/whatif/odds` `{run_id, conditions[]}` | read | `what_if_odds_sql`; 400 on `ConditionError`; 409 `whatif_run_changed` | 0 |
| `GET /api/guide?season=&week=&team=` | read | `ROOTING_GUIDE` of the point's run: top 10 by `GUIDE_RANK` and the coming week | 0 |
| `POST /api/whatif/guide` `{run_id, team_id, conditions[]}` | read | `rooting_guide_sql` over the coming week's `WHATIF_GAMES` | 0 |
| `GET /api/predictions?season=&week=` | read | this week's and last week's predictions | 0 |
| `GET /api/scorecard?season=&week=&population=` | read | week, season to date, calibration | 0 |
| `GET /api/upsets?season=&week=` | read | the week's upsets, all-time and line-era top 25 | 0 |
| `GET /api/matchup?team=&opponent=` | read | series, meetings, next meeting | 0 |
| `GET /api/preview/plan?team=&opponent=` | read | the server's choice of game; `EXPLAIN VIRTUAL` of the preview query; the session cache state; busy or disabled reasons → cost label | 0 |
| `POST /api/preview` `{team, opponent, max_calls, force}` | read (+ operator for the fetch log) | the guarded fetch of the game the server chose for the pair | ≤ 1 (+ retries) |
| `GET /api/quota?fresh=` | read | live: `CFB_API_LIVE.QUOTA` (cached 5 min); replay: `{mode: "replay"}` without a query | 0 (free `/info`) |
| `GET /api/timeline?run=` | read | `REFRESH_TIMELINE` (the newest refresh run by default), `FETCHES` of the run, and `follow {key, hold, stuck}` from `CURRENT_POINT` | 0 |
| `POST /api/refresh/plan` `{season, week, season_type}` | operator (+ read for the week's games) | the guard's plan: estimate, mode, fresh quota, `REFRESH_SPENDABLE`, ledger room with headroom, running runs, week completeness, `can_run`, reasons | 0 (live: free `/info`) |
| `POST /api/refresh/run` `{season, week, season_type, max_calls}` | operator | re-plan, guard, background `REFRESH_WEEK`; 202 `{action_id}` | ≤ the estimate, plus retries |
| `GET /api/actions/{id}` | — (operator for the run-id lookup) | the action's state, run id once known, outcome, point before and after | 0 |
| `GET /api/inspector` | — | this session's query log | 0 |
| `POST /api/inspector/{entry}/explain` | the entry's principal | `EXPLAIN VIRTUAL` of the logged text | 0 |
| `GET /api/console` | — (requires operator and console) | the four curated queries | 0 |
| `POST /api/console/{id}/explain` | console | the plan | 0 |
| `POST /api/console/{id}/run` `{max_calls}` | console (+ operator for the fetch log) | the guarded run | ≤ 3 (+ retries) |
| `/api/chat/*` | — | reserved for Phase 7: 404 | — |

Errors carry `{error, message}` and never driver text: 400 (foreign `Host`; invalid argument; `ConditionError`), 403 (a `POST` check; no operator path; no console), 409 (`max_calls_exceeded`, `ledger_refused`, `quota_refused`, `live_busy`, `live_disabled`, `reconcile_pending`, `refresh_locked`, `refresh_running`, `whatif_run_changed`), 502 `cfbd_failed` (a live statement failed and was charged), 503 (`database_unreachable`, `busy`).

**Settings** (`app/backend/settings.py`; environment first, then `.env`; nothing secret is ever logged)

| Variable | Default | Meaning |
|---|---|---|
| `TARGET`, `EXASOL_DSN` | `local`, — | deployment and address override (`vs/host/target.py`) |
| `CFB_APP_USER` | `CFB_APP_WEB` | the read user |
| `CFB_APP_PASSWORD` | — (required) | its password; `.env` is git-ignored |
| `CFB_CONSOLE_USER` | `CFB_CONSOLE_WEB` | the console user (role `CFB_READER`) |
| `CFB_CONSOLE_PASSWORD` | — | its password; without it the console is off |
| `APP_HOST`, `APP_PORT` | `127.0.0.1`, `8000` | bind; `APP_HOST` is resolved, and it is loopback only if every address is |
| `APP_OPERATOR` | `auto` | `auto` (on iff loopback), `on` (refuses a non-loopback bind), `off` |
| `APP_ALLOWED_HOSTS` | empty | extra `host:port` values for the `Host` allowlist (a network bind, read-only); `*` is refused |
| `APP_CORS_ORIGINS` | empty | comma-separated origins; `*` is refused; `make app` sets the Vite origins |
| `APP_DEV` | off | set by `make app`: adds the Vite hosts to the allowlist and disables the live refresh |
| `APP_SKIP_READ_CHECK` | no | `yes` starts without the privilege check when neither user nor operator can read the privileges; warned in the header |
| `APP_LEDGER_PLAN` | `app` | the ledger plan the app's spend counts against (`add-cfb-app` for task 8.2) |
| `APP_LEDGER_PATH` | `cache/live-calls.jsonl` | ledger file; a prefixed namespace requires an explicit path |
| `LEDGER` | on | `off` skips the ledger (ledger module rule); shown in the header |
| `APP_PREFIX` | empty | namespace prefix (integration tests); refused with the real `CFBD_API` connection, like `--prefix` |
| `CFB_TEST_OWNER_PASSWORD` | — | operator password for a prefixed namespace (precedent: `scripts/grants.py`) |
| `APP_STATIC_DIR` | `app/frontend/dist` | the built frontend |

**Browser safety** (`app/backend/security.py`, middleware order: `Host`, then the `POST` checks, then CORS)
- **`Host` allowlist,** every request: `127.0.0.1:<APP_PORT>`, `localhost:<APP_PORT>`, `[::1]:<APP_PORT>`; with `APP_DEV`, also `127.0.0.1:5173` and `localhost:5173`; plus `APP_ALLOWED_HOSTS`. Name (case-insensitive) and port must match. Otherwise 400, before routing.
- **`POST`:** `X-CFB-Action: 1`; `Content-Type: application/json`; `Origin` in `{"http://" + h for h in the loopback allowlist} ∪ APP_CORS_ORIGINS`. The set is built once from configuration and never from the request. A missing `Origin` fails. Otherwise 403.
- **Vite** (`vite.config.ts`): `server.host: '127.0.0.1'`, `server.port: 5173`, `server.strictPort: true`, `server.allowedHosts` left at its default (on), proxy `/api` → `http://127.0.0.1:8000` with `changeOrigin: false`. `scripts/app.py dev` refuses `--host` (or a `server.host` override) in its passthrough arguments. `src/test/viteConfig.test.ts` pins these values.
- **WSL2:** a `127.0.0.1` bind is forwarded to Windows `localhost`, where the browser runs; it is not exposed to the LAN in NAT or mirrored mode (user guide).

**Operator allowlist** (`app/backend/operator.py`; the wrapper records every statement, and `tests/unit/test_app_operator.py` asserts that each matches one of these templates)

| Action | Statements | Module |
|---|---|---|
| refresh plan | `loader/guard.py`'s estimate queries over `CFB_META.LOAD_SLICES`, `LOADER_SETTINGS`, `LOAD_ORPHANS`, `LOAD_STATUS`; the `FETCH_MODE` property; `SELECT * FROM CFB_META.QUOTA` (fresh for every plan); `SELECT … FROM CFB_META.LOAD_RUN WHERE "RUN_STATUS" = 'RUNNING'` | `ops/refresh.py` |
| refresh run | `EXECUTE SCRIPT "CFB_INFRA"."REFRESH_WEEK"(<int>, <int>, 'regular'\|'postseason')`; on a second connection `SELECT "RUN_ID" FROM CFB_META.LOAD_RUN WHERE "SESSION_ID" = <int> AND "RUN_KIND" = 'REFRESH_WEEK' ORDER BY "RUN_ID" DESC LIMIT 1`; then the `LOAD_RUN` row and `QUOTA` for the ledger record | `ops/refresh.py` |
| fetch-log write | `INSERT INTO "CFB_META".FETCH_LOG (…) VALUES …` (`fetchlog.insert_fetch_log`, bound values) | `ops/fetchlog.py` |
| reconciliation | `loader/guard.reconcile`'s reads: `LOAD_RUN` rows of kind `REFRESH_WEEK` or `BACKFILL` of the current UTC month; their `REFRESH_LOG` step rows; the session check of Phase 2's stale rule; the estimate query for a step left unfinished | `ops/reconcile.py` |
| privilege check, fallback | the database-wide privilege views named by probe 1.1(d), for the read and console users | `db.py` |
| `make app-user` (CLI, not HTTP) | `CREATE USER … IDENTIFIED BY {password!q}`, `ALTER USER … IDENTIFIED BY …` (`ROTATE=yes`), `GRANT CREATE SESSION`, `GRANT CFB_APP`; with `CFB_CONSOLE_PASSWORD` the same for the console user with `GRANT CFB_READER` | `scripts/app.py` |

**Console catalog** (`app/backend/console_catalog.py`; run on the console path; `{VS}` is `CFBD_VS`, or `TEST_CFBD_VS` in the tests; the expected manifests are pinned by task 1.7 against the catalog's canonical slice-key and planning code)

| Id | SQL | Expected plan |
|---|---|---|
| `fan-out` | `SELECT * FROM {VS}."GAMES_TEAMS" WHERE "P_YEAR" BETWEEN 2022 AND 2024 AND "P_TEAM" = 'Michigan'` | 3 slices, `/games/teams?seasonType=both&team=Michigan&year=2022` … `2024`; federated, so up to 3 live calls (0 in replay) |
| `contradiction` | the same with `"P_YEAR" = 2023 AND "P_YEAR" = 2024` instead of the range | 0 slices, `sources` `[]` |
| `stored` | `SELECT "REQ_SLICE", "REQ_SOURCE", "REQ_STATUS", "REQ_ATTEMPTS", "REQ_MS", "REQ_AT", COUNT(*) AS "REQ_ROWS" FROM {VS}."GAMES" WHERE "P_YEAR" BETWEEN 1869 AND 2025 AND "REQ_SOURCE" = 'STORED' GROUP BY "REQ_SLICE", "REQ_SOURCE", "REQ_STATUS", "REQ_ATTEMPTS", "REQ_MS", "REQ_AT" ORDER BY "REQ_SLICE"` | stored-only: no fetch, `budget` null, `sources` `["STORED"]` (`cfbd-fetch/read-through`); one row per stored season slice, 157 on the real deployment, 0 calls. `REQ_ROWS` feeds the fetch log's rows per slice |
| `over-budget` | the `fan-out` query over `BETWEEN 2010 AND 2024` | 15 slices > `MAX_CALLS_PER_QUERY` 10: refused at plan time |

`GAMES_TEAMS` needs `year` plus one of `week`, `team` or `conference` (catalog rule `games_teams.week_team_or_conference_with_year`), so a team filter keeps it selective. The console's help text also points at the pages' stored history, such as the head-to-head series since 1897, whose plan reads "no virtual table: 0 calls".

**Spending guard** (`spend.py` is pure, `live.py` executes and is the only module that names `GAME_PREVIEW`)

```
plan(statement, principal)                       # 0 calls; never takes a lock
  manifests = EXPLAIN VIRTUAL statement as principal   (0..n pushdowns, vs/host/fetchlog.parse_manifest)
  live = Σ len(slice_keys) over manifests whose sources hold LIVE ; source = LIVE | REPLAY | STORED | NONE

act(statement, principal, max_calls, flight)     # flight: (session, game) for a preview, (session) for the console
  single flight on `flight`: a preview waiter re-checks the session cache inside it and is served "cached · 0 calls";
                             a second console run of the session → 409 live_busy
  p = plan(...)
  if p.live == 0:
      run on the principal's session (read pool, or the console session); any LIVE rows in the result
      (a mode switch since the plan) are charged like a live request; fetch-log rows (operator); return
  live gate (in-process lock; wait ≤ 5 s, else 409 live_busy)
    409 live_busy        if the app's own refresh action runs, or the newest refresh run is RUNNING
                            (unless the operator path finds its session gone: then it is reconciled first)
    409 reconcile_pending if a finished loader run is still unrecorded
    with Ledger.exclusive():                      # does not wait; held → 409 live_busy, "another process holds the ledger lock"
      p = plan again;  409 max_calls_exceeded if p.live > max_calls
      403 if the operator path is off;  409 live_disabled if LEDGER is on and the plan or the month has no cap
      409 ledger_refused  unless Ledger.check(APP_LEDGER_PLAN, month, p.live, quota.used_calls, headroom = MAX_ATTEMPTS − 1)
      409 quota_refused   unless p.live + MAX_ATTEMPTS − 1 ≤ quota.BACKFILL_SPENDABLE   (reading ≤ 5 min old, else read now)
      try:     rows = run(statement) ONCE on the live session (read user; QUERY_TIMEOUT 300 s, socket 330 s)
                      or the console session (same timeouts); never through the pool's retry
      except:  error = first line of the exception
      finally: ledger first — per LIVE slice of rows: (plan, target, run=<action id>, slice_key, status, attempts,
                                                       cost = attempts or MAX_ATTEMPTS, ms, remaining_* = null)
                              — on error, per planned live slice: status = attempts = null, cost = MAX_ATTEMPTS, error
  then operator: insert_fetch_log(build_fetch_log_rows(manifest, rows or [], context='APP_PREVIEW#<action id>'))
       a failed insert is reported in the response and logged; it never removes a ledger entry
  response: rows, calls spent, source — or 502 cfbd_failed naming the error and the calls charged
```

`LIVE_QUERY_TIMEOUT_SECONDS` = 300 lies above the fetch function's worst case per slice, computed in a unit test from `vs/cfbd_vs/http.py` and the resolver: 4 × 30 s attempts + 3 × 30 s waits + 10 s DoH = 220 s; slices of one statement run in parallel. The socket timeout of 330 s lets the database end the statement first, so the driver sees an error instead of a cut connection.

**Reconciliation** (`loader/guard.reconcile`, under `Ledger.exclusive()`; the app runs it at start, before every live refresh, and when the timeline sees a refresh run leave `RUNNING`)

```
for each LOAD_RUN row of kind REFRESH_WEEK or BACKFILL started in the current UTC month:
  skip unless it is no longer running (status ≠ RUNNING, or RUNNING with its session gone: Phase 2's stale rule)
  skip unless it was live (REMAINING_BEFORE is set: Phase 2 reads /info only for live runs with due slices)
  skip if the ledger has an entry with slice_key '<RUN_KIND>#<RUN_ID>'
  calls = CALLS if set, else Σ CALLS of its committed step rows
          + MAX_ATTEMPTS × the slices of a FETCH step left RUNNING (its row's SLICES, else that table's due slices now)
  ledger.record_run(APP_LEDGER_PLAN, target, kind, run_id, calls, error = "recorded by reconciliation: <reason>")
lock held → the pass is retried later, and live actions answer 409 reconcile_pending while a run is known to be unrecorded
```

A run started from a SQL client in live mode (beat 3's "one SQL call") has no ledger entry by design, and is recorded this way under the app's plan. A run is never recorded twice: the CLI and the app's own refresh record inside the same lock.

**Refresh flow**

```
POST /api/refresh/plan → ops/refresh → loader/guard.plan_refresh(conn, ns, season, week, type, ledger, plan=APP_LEDGER_PLAN,
                                                                   headroom=MAX_ATTEMPTS − 1, fresh_quota=True)
                          + week completeness (CFB_API.GAMES on the read path) + dev-mode rule (APP_DEV: no live run)
                          → {estimate per table, total, mode, quota, REFRESH_SPENDABLE, ledger room, running runs,
                             week {games, completed, not_kicked_off, expectation}, can_run, reasons}
POST /api/refresh/run  → in-process single flight (409 naming the active action) → reconcile → re-plan; refuse if total > max_calls
                          → thread: note owner session id → guard.run(...) = EXECUTE SCRIPT … REFRESH_WEEK(...) on its own
                            owner connection (live: under Ledger.exclusive() for the whole run; replay: no lock)
                          → second connection: run id by session id, as soon as the LOAD_RUN row exists
                          → outcome rows or first error line → ledger record REFRESH_WEEK#<run> (live)
                          → action DONE | FAILED, with the point before and after
                       ← 202 {action_id}
page: GET /api/timeline every 1 s while the newest refresh run is RUNNING, else every 5 s; GET /api/actions/{id};
      the follow key (season, week_seq, publication_id, whatif_run_id, whatif_views_current) changes →
      follow mode: hold while the run is RUNNING (stuck after 10 min without a step change), then reload once;
      pinned point: a notice
```

**App test stack** (`tests/it/app_stack.py`, task 2.3; the `TEST_` namespace; IT lock; 0 calls)
1. The harness deploys the virtual schema, the loader with `tests/it/fixtures/load-plan.app.yaml`, the model, the simulation schema and the analyses, then runs the grants with the prefix. The load-plan variant is Phase 5's `load-plan.analyses.yaml` plus step 350 `WAIT`, which is `TEST_STEP_SLEEP` (5 s). The users are `CFB_TEST_APP` with `TEST_CFB_APP` (Phase 5's helper), `CFB_TEST_READER` (foundation; `SELECT` on `TEST_CFBD_VS`) as the console user, and `CFB_TEST_OWNER`.
2. It seeds Phase 4's sim league and Phase 5's history variant, writes replay recordings of the league's 2026 weekly slices, sets `SIM_CONFIG` to 2,000 seasons in batches of 500, and sets the clock to the end of 2026 week 9.
3. It publishes a manual run at 2026 week 8 (4,000 seasons, 8 × 500), then runs `REFRESH_WEEK(2026, 9, 'regular')` as the owner, which freezes, simulates and publishes.
4. `reset()` brings the stack back to this base state after a mutating test. `refreshed(10)` stores the week-10 results (`complete_week(10)`) and runs the app's replay refresh of week 10.
5. `app_client(**overrides)` is a FastAPI `TestClient` with the read user `CFB_TEST_APP`, the console user `CFB_TEST_READER`, the operator `CFB_TEST_OWNER`, `APP_PREFIX=TEST_`, a bind of `127.0.0.1`, the `Host` `127.0.0.1:8000`, and a temporary ledger: `Ledger(path, config_path=…)` over a temporary file and a temporary caps file in which plan `app` has a cap of 20 and the month is authorized for 20. Overrides cover `APP_DEV`, a caps file without `app`, and `LEDGER=off`.
6. `served_app()` runs uvicorn as a child process on a free loopback port. The credentials reach it only through the child's environment. It can be killed and started again with the same temporary ledger. It is used by the E2E smoke and the process-death scenario.
7. Helpers: `mock_delay(path, ms)` and `mock_fail(path, status)` over the sidecar's `script`; `hold_ledger_lock()`, a child process that holds `Ledger.exclusive()` on the temporary ledger; and a harness accessor for the passwords of `CFB_TEST_APP` (hand-off P6-b) and `CFB_TEST_READER`.

**Frontend** (`app/frontend`)

```
package.json (exact pins), package-lock.json, index.html (no inline script), vite.config.ts (127.0.0.1:5173, strictPort,
proxy /api → 127.0.0.1:8000, changeOrigin false), tsconfig.json (strict), eslint.config.js (typescript-eslint, react-hooks),
vitest.setup.ts (jsdom, jest-dom)
public/theme-init.js (sets data-theme before first paint from storage, in try/catch, else prefers-color-scheme; CSP-safe)
src/main.tsx (StrictMode), App.tsx (path routing without a router library: /odds /predictions /upsets /head-to-head)
src/api/client.ts (same-origin fetch; one AbortController per page load; POST adds X-CFB-Action and JSON, never retried;
                   typed errors 400/403/409/502/503), types.ts
src/state/store.ts (AppState: point, page, team, conditions, inspector, follow key and hold; actions), url.ts
src/theme/tokens.ts (validated light/dark palette and text tokens → CSS variables), theme.css, ThemeSwitch.tsx
src/format/format.ts (percent, "< 0.1 %", "T-37", record labels, "N seasons in S s", spend labels, HTTP request lines)
src/shell/AppShell.tsx Header.tsx Footer.tsx OutageBanner.tsx AsOfPicker.tsx QuotaBadge.tsx LedgerBadge.tsx ChatSlot.tsx
src/spend/SpendButton.tsx ConfirmSpend.tsx SpendNotice.tsx
src/odds/OddsPage.tsx OddsTable.tsx RunBanner.tsx WhatIfPanel.tsx RootingGuide.tsx
src/predictions/PredictionsPage.tsx ScorecardHeadline.tsx CalibrationChart.tsx PredictionList.tsx
src/upsets/UpsetsPage.tsx    src/headtohead/HeadToHeadPage.tsx TeamPicker.tsx SeriesSummary.tsx NextMeeting.tsx PreviewCard.tsx
src/refresh/RefreshPanel.tsx Timeline.tsx    src/inspector/Inspector.tsx QueryLog.tsx PlanView.tsx Waterfall.tsx Console.tsx
src/charts/ (SVG: inline bars, dot plot with reference line, timeline bars, waterfall; each with a table view)
src/test/fixtures/*.json (synthetic, sim-league names), src/test/render.tsx (StrictMode), src/test/viteConfig.test.ts;
*.test.ts(x) beside their components
e2e/smoke.spec.ts, playwright.config.ts (local Playwright or the podman image, per task 1.2's gate)
```

**Charts and color (the dataviz method).**
- The odds bars use one sequential hue.
- Movement is polarity: two diverging hues, always with an arrow icon and a word.
- Timeline states use the status palette, with an icon and a label.
- Calibration is a single series of dots on a gray diagonal, with no legend (the title names it).
- The waterfall colors bars by source, with a direct label.
- One axis per chart; a table view for every chart; text in text tokens, never in a series color.

Task 1.8 runs the dataviz validator (`validate_palette.js`) on the chosen hexes for `--mode light` and `--mode dark` against the two surfaces. `tokens.test.ts` pins the validated values and the WCAG contrast floors.

**Make targets** (the repo path contains spaces: every recipe quotes `$(ROOT)`; `scripts/app.py` launches the processes)

| Target | Does |
|---|---|
| `make app` | `db-check`, `app-deps`, then `python -m scripts.app dev`: `APP_DEV=1`, uvicorn with `--reload` on 127.0.0.1:8000 with `APP_CORS_ORIGINS` set to the Vite origins, plus `npm run dev` (127.0.0.1:5173, `strictPort`, proxy `/api`; a `--host` override is refused); Ctrl-C stops both. No live refresh in this mode |
| `make app-build` | `app-deps`, then `npm run build` (`tsc -b && vite build`) → `app/frontend/dist/` (git-ignored) |
| `make app-serve` | `db-check`, then `python -m scripts.app serve`: one process, one worker; refuses without `dist/`; `TARGET=aws` works |
| `make app-user [ROTATE=yes] [ARGS=…]` | `db-check`, then `python -m scripts.app user`: create or rotate the read user, and the console user when `CFB_CONSOLE_PASSWORD` is set (`app/access`) |
| `make app-deps` | `npm ci` in `app/frontend` when `package-lock.json` is newer than `node_modules` |
| `make app-test` | `app-deps`, then `npm test` (vitest run) |
| `make app-e2e` | `db-check`, `app-build`, then `pytest tests/it -m e2e` (run under the IT lock); Playwright locally or in `mcr.microsoft.com/playwright:v1.64.1-noble` through podman with `--network host`, per task 1.2's gate |
| `make app-perf` | `db-check`, then `python -m scripts.app perf`: the performance budget's read rows (never a live endpoint) |
| `make test` / `make lint` / `make test-it` / `make coverage` | + `app-test`; + `npm run lint` and `npm run typecheck`; `-m "it and not network and not e2e"`; + a report for the backend's pure modules |

**Versions** (re-checked 2026-10-22 21:46 UTC against PyPI and the npm registry; pre-releases excluded)

| Package | Latest | Released | Newest ≥ 7 days on 2026-10-22 | Note |
|---|---|---|---|---|
| fastapi | 0.161.0 | 2026-10-22 18:30 | **0.143.2** (2026-10-15) | 23 releases since 2026-10-19 (0.150.0 → 0.161.0), 8 of them in 29 h (0.157.0 on 2026-10-21 13:56 → 0.161.0). Requires `starlette>=0.46.0` with no upper bound, `pydantic>=2.9.0`, and `opentelemetry-api>=1.44.0` (0.143.2 and 0.161.0 alike) |
| starlette | 1.9.0 | 2026-10-18 | 1.8.0 (2026-10-13) | pinned exactly in group `app`, since fastapi bounds it only from below |
| uvicorn | 0.54.0 | 2026-09-25 | 0.54.0 | no `[standard]` extra; `watchfiles` only for `make app` reload if needed |
| pydantic | 2.14.1 | 2026-10-11 | 2.14.1 | through fastapi |
| httpx | 0.28.1 | 2024-12-06 | 0.28.1 | dev group, for `TestClient`; 1.0.dev6 is a pre-release |
| react, react-dom | 19.3.1 | 2026-10-14 | 19.3.1 | runtime |
| vite | 8.3.5 | 2026-10-21 | 8.3.4 (2026-10-08) | engines Node `^20.19 \|\| >=22.12` (local Node 24.15.0, npm 11.12.1) |
| @vitejs/plugin-react | 6.1.2 | 2026-10-05 | 6.1.2 | peer `vite ^8` |
| typescript | **6.0.3** | 2026-04-16 | 6.0.3 | not 7.0.2 (2026-07-08): typescript-eslint 8.71.2 and 8.72.0 both declare the peer `typescript >=4.8.4 <6.1.0` |
| vitest | 5.0.5 | 2026-10-15 | 5.0.5 | peers `vite ^6.4 \|\| ^7 \|\| ^8`, `@types/node ^22 \|\| >=24` |
| @types/node | 24.19.2 | 2026-10-09 | 24.19.2 | matches local Node 24 |
| jsdom | 30.1.3 | 2026-10-15 | 30.1.3 | vitest environment |
| @testing-library/react / dom / jest-dom / user-event | 16.3.3 / 10.4.2 / 7.0.1 / 14.6.8 | 2026-08-27 / 09-13 / 08-09 / 10-13 | same | react peer `^18 \|\| ^19`; jest-dom peer `vitest >= 0.32` |
| eslint / typescript-eslint / eslint-plugin-react-hooks | 10.13.0 / 8.72.0 / 7.1.1 | 2026-10-16 / 10-19 / 04-17 | 10.12.0 / 8.71.2 / 7.1.1 | flat config; react-hooks accepts eslint 10 |
| @types/react, @types/react-dom | 19.3.0 | 2026-09-09 | 19.3.0 | (the earlier date 10-04 was wrong) |
| @playwright/test | 1.64.1 | 2026-10-14 | 1.64.1 | the image `mcr.microsoft.com/playwright:v1.64.1-noble` exists (MCR tag list); podman 4.9.3 is on this host |

Policy (task 1.2): within these majors, take the newest release that is at least 7 days old **on the day of task 1.2**, and pin it exactly on both sides: npm packages in `package.json` with `package-lock.json` committed; the Python group `app` with `==` pins for fastapi, starlette and uvicorn, resolved in the committed `uv.lock`. On 2026-10-22 that means fastapi 0.143.2, not 0.161.0. Record the resolved set in `docs/app-verification.md`. No other runtime dependency: no router, state, data-fetching or chart library.

**Exasol claims checked against the skills** (`exasol:exasol-database` loaded; grammar and keyword references), **the predecessor probes and the project memory**

| Claim | Evidence | Result |
|---|---|---|
| `EXPLAIN VIRTUAL <subquery>` returns the pushdowns, with the manifest in `PUSHDOWN_SQL` | `exasol-grammar.md` `explain_virtual_statement`; `vs/host/fetchlog.explain_pushdown_sql`; foundation | Confirmed |
| Readers may run `EXPLAIN VIRTUAL`; the key never appears in it | Foundation probe 1.4g; architecture § Security model | Confirmed for virtual-schema readers (the console path). Through a serving view with only schema `SELECT`: Phase 5 probe 1.1(b), re-checked here by probe 1.10(b) |
| `EXPLAIN VIRTUAL` of a query without virtual tables returns no pushdown rows rather than an error | Not stated | Probe 1.10(b). Fallback: label "no virtual table" from the serving-contract lint (Q3) |
| A stored-only query (`REQ_SOURCE = 'STORED'`) contains no fetch and no budget applies | Recorded `cfbd-fetch/read-through` scenario "Stored-only queries never call the API" | Confirmed for the plain query. With the `GROUP BY` around it: task 1.7 and probe 1.10(b) (Q21) |
| Lua scripting programs run with the caller's rights; views read with their owner's rights | Project memory (preprocessor framework, verified 2026-06-05); the skills are silent | Decisive for [2]. Re-checked by probe 1.1(a) with a toy script in a `PROBE_` namespace. If scripts ran with definer rights, the operator path would become an `EXECUTE`-only runner user (Q1) |
| `GRANT EXECUTE ON SCRIPT`, `GRANT IMPERSONATION ON user`, `IMPERSONATE user` exist | `exasol-grammar.md` `grant_objprivs_statement_1`, `grant_impersonation_statement`, `impersonate_statement` | Confirmed; considered and rejected ([2]); impersonation is part of the privilege check ([25]) |
| Two connection grants exist: `GRANT CONNECTION <c> TO …` and `GRANT ACCESS ON CONNECTION <c> [FOR SCRIPT …] TO …`; object grants take `ON SCHEMA` | `grant_connection_statement_1`, `grant_connection_restricted_statement_1/2`, `grant_objprivs_statement_1` | Confirmed: the privilege check covers both connection forms; the scenario grants `INSERT ON SCHEMA` |
| `CREATE USER <u> IDENTIFIED BY <password>`; `ALTER USER` | `create_user_statement`; `scripts/grants.py` `create_user_step` (`{password!q}`) | Confirmed; reused. Whether the password is masked in audit or profiling text: probe 1.1(d) (review m2) |
| `ALTER SESSION SET <param> = <value>` for a per-session query timeout | `alter_session_statement` (generic parameter) | Parameter name and effect checked in probe 1.1(e), for 30 s (pool) and 300 s (live session). Fallback: socket timeouts only (Q18) |
| `CREATE CONSUMER GROUP … QUERY_TIMEOUT`, `ALTER USER … SET CONSUMER_GROUP` | `create_consumer_group_statement`, `alter_user_set_consumer_group` | Confirmed; not used: a group applies to the whole read user, live statements included, and is a new database object ([26]) |
| Autocommit is pyexasol's default; `stmt.query` is the sent text; one connection is one session, runs one statement, and is not shared between threads | pyexasol docs (context7 `/exasol/pyexasol`) | Confirmed: a 4-session read pool, a reserved live session, a console session per action; the refresh gets its own connection |
| Each refresh step commits its row at start and end, and another session sees the progress | Phase 2 `loader/refresh-week` scenario | Taken. Re-checked through `REFRESH_TIMELINE` as the app role during `SIMULATE` and `PUBLISH` (probe 1.10(c)) |
| A client abort ends the run; committed steps stay; the next run frees the `RUNNING` row | Phase 2 `refresh_week.lua` header, `loader/runlock.py` ("a process that dies frees the lock with its session") | Taken; whether the server aborts at once or lets the script finish is observed by the process-death scenario (Q16). Reconciliation handles both |
| The run lock refuses concurrent runs, naming the running run | Phase 2 `loader/run-logs` | Taken; mapped to 409. `hold_deploy_lock` takes the same lock: task 4.2 verifies (Q15) |
| A schema grant covers views replaced later (the regenerated what-if views) | Phase 5 probe 1.1(c); project memory (preprocessor framework) | Taken |
| Privilege dictionary views (`EXA_USER_*`, `EXA_ROLE_*`, `EXA_DBA_*`): names, columns, and whether a user sees role-inherited and `PUBLIC` grants | Not in the skills' references | Probe 1.1(d). Fallback: the operator path reads the database-wide views; with the operator off, refuse unless `APP_SKIP_READ_CHECK=yes` (Q5) |
| Reserved words | `exasol-reserved-keywords.md` | The new identifiers (`CFB_APP_WEB`, `CFB_CONSOLE_WEB`, `CFB_TEST_APP2`, the alias `REQ_ROWS`) are not reserved; `CONNECTION` is, and appears only as a keyword. `CONTEXT` values are strings. The console SQL quotes every identifier |

Outside Exasol: Starlette's `TrustedHostMiddleware` (source on GitHub, read 2026-10-22) lowercases the host name, drops the port, and answers 400. The app therefore uses its own check of name and port.

**Performance budget** (targets; local = laptop at 100k seasons on restored real data; AWS = 1M; measured by task 6.3 locally and task 8.2 on AWS; protocol after S4: 3 discarded warm-ups, 25 samples, p95 is the second largest, an exclusive window, sizing recorded)

| Request | Local | AWS | Basis |
|---|---|---|---|
| Odds page data (points, odds, banner, settings) | < 0.6 s p95 | < 0.8 s p95 | Phase 5 `PLAYOFF_ODDS` < 0.3 s, plus 3 small views |
| What-if toggle → conditional odds | < 0.5 s p95 | < 1.2 s p95 | Phase 5: < 0.3 s / < 1 s, plus ≤ 50 ms backend |
| Conditional guide (coming week) | < 0.7 s p95 | < 1.2 s p95 | Phase 5: < 0.5 s / < 1 s (or its F1/F2 outcome) |
| Predictions or scorecard page | < 1 s | < 1 s | small views |
| Upsets page | < 1.5 s | < 1.5 s | Phase 5 `UPSETS` < 1 s per week, plus two top-25 lists |
| Head-to-head (series, meetings, next) | < 1.2 s | < 1.2 s | Phase 5 < 1 s |
| Preview click, replay | < 1.5 s | — | `EXPLAIN` plus a replayed fetch, at 0 calls |
| Preview click, live | — | < 2 s, median of the live calls | mission on-demand target; S1: 0.7 s at CFBD plus ≈ 0.6 s database, plus `EXPLAIN`, the busy check and the ledger (Q7) |
| Timeline poll (with the follow key) | < 0.2 s | < 0.3 s | `LOAD_RUN` × `REFRESH_LOG`, plus `CURRENT_POINT` |
| A step's change visible on the page | ≤ 1.5 s after its commit | ≤ 2 s | 1 s poll |
| Backend overhead per request | ≤ 50 ms p95 | — | measured against direct SQL |

`scripts/app.py perf` times a fixed catalog of read endpoints. `tests/unit/test_app_perf.py` asserts that the catalog names no preview, quota, console or refresh endpoint, the same rule as Phase 5's [31]. Live latency comes only from task 8.2's ledgered calls.

**Code layout**

```
app/__init__.py  app/backend/__init__.py
app/backend/main.py            create_app(settings, read_factory, console_factory, owner_factory, ledger); static + SPA fallback;
                               headers; errors; startup: privilege check, ledger state, reconciliation
app/backend/settings.py        Settings.from_env (environment, .env); bind resolution; operator decision; host and origin sets (pure)
app/backend/security.py        Host allowlist; X-CFB-Action, JSON and Origin check; CSP and headers; session cookie (pure + middleware)
app/backend/db.py              ReadPool; LiveSession; console session factory; effective-privilege check; query recorder;
                               0-call conflict retry; 503 mapping
app/backend/operator.py        OperatorPath: enabled and reason; owner connection per action; statement allowlist
app/backend/live.py            the only module naming GAME_PREVIEW: preview plan and fetch; fetch mode; live gate; busy checks;
                               ledger lock for live plans; run once; finally: ledger, then fetch log
app/backend/console.py         the console path: explain and run of console_catalog statements through live.py
app/backend/ops/refresh.py · ops/fetchlog.py · ops/reconcile.py
app/backend/sqlcatalog.py      every read statement (pyexasol placeholders) + the serving-schema and GAME_PREVIEW lints (pure)
app/backend/explain.py         EXPLAIN VIRTUAL rows → 0..n manifests                                          (pure)
app/backend/spend.py           plans, guard decisions, ledger entries (success and failure), fetch-log rows, timeouts (pure)
app/backend/sessions.py        session ids; preview cache; single flight; console run per session; query log (pure, thread-safe)
app/backend/console_catalog.py the four statements and their expected plans                                   (pure)
app/backend/models.py          pydantic response models (also validate the frontend fixtures)
app/backend/routes/            config points odds whatif predictions upsets matchup preview quota timeline refresh actions
                               inspector console; deps.py (the shared point validation)
loader/guard.py                plan_refresh / plan_backfill / run / record / reconcile, extracted from scripts/loader_cli.py
vs/host/livecalls.py           planned_live, slice_calls, query_costs, charged (moved from scripts/record.py)
scripts/app.py                 serve | dev | user | perf
app/frontend/                  see above
tests/it/app_stack.py, tests/it/fixtures/load-plan.app.yaml, tests/it/test_app_*_it.py
tests/unit/test_app_*.py, test_loader_guard.py, test_livecalls.py; tests/cli/test_app_cli.py
docs/spikes/s9_app_probe.py, docs/spikes/s9-app-probe.md, docs/app-verification.md
```

### Consequences

| Decision | Alternatives Considered | Rationale |
|----------|------------------------|-----------|
| Read path as `CFB_APP_WEB` (role `CFB_APP`); console path as `CFB_CONSOLE_WEB` (role `CFB_READER`); an operator path as the owner with a fixed allowlist ([2], [10]) | A runner user with only `EXECUTE` on `REFRESH_WEEK` (the interview's example); a runner user granted every write and `CREATE ANY` privilege; `GRANT IMPERSONATION` to the app user; a request-queue table plus an owner-side worker; a separate runner process; the console as the owner | Scripts run with the caller's rights, so an `EXECUTE`-only user fails at its first write. A fully privileged runner is an owner with one more password. Impersonation lets any page bug become `sys`. A queue gives the app a write path and adds a process. A separate process gives no real separation on a laptop. The console needs only `SELECT` on the virtual schema, and showing a reader's principal makes beat 2's point |
| The operator path only when every bind address is loopback; `on` with a network bind refuses to start ([3]) | Always on; a token header; authentication in Phase 6; trusting the name `localhost` | The owner can do anything. On loopback the presenter's machine is the only client. A name can resolve elsewhere. Authentication is Phase 8's decision; until then a network-exposed app is read-only |
| Allowlist = refresh, fetch-log rows, reconciliation, the privilege check's fallback; forced re-sims and publication stay CLI ([4]) | Buttons for `SIMULATE_FROM`, `PUBLISH_RUN`, `PREDICT` | They serve no beat (Phase 5 [8]), take ≈ 40 s at 1M and write. The scale dial is Phase 8 and adds one allowlist entry then |
| Fetch log of the app's own requests written by the operator path from the result's `REQ_*` columns, after the ledger ([5]) | `INSERT` on `FETCH_LOG` for `CFB_APP`; a separate logger user; not logging previews | Keeps Phase 5's "the app role cannot change data" true. One principal for writes. Without the operator path, live requests are refused rather than left unlogged |
| One spending guard for every live action ([6]) | Labels only; a per-feature check | "Live spend always explicit" holds by construction. The re-plan against `max_calls` closes the label-to-click gap. The ledger stays the single record |
| Per-click ledger entries without `/info` readings; quota cached for 5 min for previews and the console, fresh for every refresh plan ([7]) | `/info` before and after each click, like the recorder | Two extra virtual-table reads would push the preview past 2 s. The refresh plan reads `/info` anyway, and other clients spend in between |
| Preview cache per browser session, 15 min, 20 entries ([8]) | A process-wide cache; no cache; `PROMOTE()`; a longer life for played games | Phase 5's hand-off. The viewer who spent owns the result. A played game's preview never changes, but a longer life is not worth its complexity now |
| The inspector logs `stmt.query` per session, explains on demand as the same principal, and shows each slice as its HTTP request ([9]) | Explain every query eagerly; reconstruct the SQL client-side; explain as the owner | 0 calls and no owner rights; the text shown is the text sent; no client SQL ever reaches the server |
| A curated four-query console as a `CFB_READER` user for beat 2; the stored query proves 157 seasons at 0 calls ([10]) | Free-text SQL; the console as the owner; a `CFB_API_LIVE` view per demo query; a stored query of one season | Exact expected plans, bounded spend (≤ 3 calls), no injection surface. "Any reader with `SELECT`" is shown by a reader. Stored slices count toward the budget unless the query is stored-only |
| Refresh: plan, confirm, background thread, poll the serving timeline for any run, map refusals to 409, no cancel ([11]) | Server-sent events; run synchronously in the request; a cancel button | Each step commits its row, so polling is correct and simple. A 2-minute statement must not hold an HTTP request. Cancelling is a `KILL`, an owner action |
| Extract Phase 2's CLI guard into `loader/guard.py` with the ledger, plan, lock policy and headroom as parameters, plus `reconcile` ([12]) | Call `scripts.loader_cli` from the app; re-implement it | One implementation of estimate, quota, ledger verdict, record and reconciliation; the CLI's tests pin the behavior, unchanged |
| One worker; a 4-session read pool with checkout timeout and replacement; a reserved live session; autocommit; retries only for 0-call statements ([13]) | Several workers; a connection per request; autocommit off; one pool for all | The in-process state needs one process. pyexasol sessions cannot be shared. A live wait must not hold a page session. Autocommit is the page contract |
| React 19 + Vite 8 + TypeScript 6.0, no router, state, data or chart library; exact pins on both sides after a 7-day wait ([14]) | TypeScript 7; react-router, TanStack Query, a chart library; bounds in `uv.lock` | TypeScript 7 breaks typescript-eslint. Four routes, one store and four SVG forms do not justify libraries. FastAPI's churn makes exact pins and the wait necessary |
| FastAPI serves `dist` with an SPA fallback and a CSP with nothing it would block; `make app` runs a pinned Vite with a proxy ([15]) | nginx in front; hash routing; an inline theme script; Google-hosted fonts | "A single process" (interview). `StaticFiles(html=True)` has no SPA fallback. `default-src 'self'` blocks inline scripts and other hosts' fonts |
| Passwords from the environment or `.env`; `make app-user` creates both users; the start proves their effective privileges ([16], [25]) | Tooling writes the password; the read path as `sys` in development; a name check | No tool writes a secret; "read-only pages" cannot be misconfigured silently by any route a privilege can take |
| As-of in the URL; follow mode by a key with the publication, holding reloads while a refresh runs ([17], [30]) | Session state only; always follow; reload on a `CURRENT_POINT` change | Links reproduce a view; beat 4's "pages move" happens without a click and never to an unpublished week |
| A chat slot as a store, a region and a reserved path ([19]) | Build nothing; a placeholder panel | Phase 7 plugs in without a layout or state rework; nothing visible changes now |
| Tests: API integration tests on the `TEST_` stack, component tests on validated fixtures, Playwright locally or through podman, else API-level ([20]) | Mocked-DB API tests only; E2E for everything; API-level at the first install problem | The database behavior is the risk; fixtures cannot drift; the browser wiring (polling, follow mode, the click-only preview) needs a browser when one can be had |
| Live verification ≤ 12 calls under `add-cfb-app`, 8 planned, in November, after Phase 2's group J and Phase 5's 7.3 ([21]) | 0 calls; a full demo run live | Each live mechanism is proven once; November is authorized (1000) |
| A `Host` allowlist on every request and a configured `Origin` set for every `POST` ([24]) | Starlette's `TrustedHostMiddleware`; an origin derived from `Host`; a per-start operator token; loopback alone | Rebinding makes the attacker same-origin; only `Host` differs. Starlette's check ignores the port. A token in `index.html` is readable after rebinding too |
| Live statements charged in a `finally`, never retried, on a session whose timeout outlives the fetch function ([26]) | Charge only on success; the pool's retry; a consumer group with `QUERY_TIMEOUT` | A failed statement may have spent 4 calls per slice. A retry is a second unconfirmed spend. A consumer group would cut live statements of the same user and adds a database object |
| The ledger lock only for live plans, behind an in-process gate; one flight per question; 409 `live_busy` ([27]) | Lock every act, as the CLI does; wait for the lock; change `Ledger.exclusive()` | The lock does not wait and a refresh holds it for minutes; replay needs no lock; changing Phase 2's semantics would ripple into its CLI |
| Reconciliation of loader runs with the ledger; the run id learned early; no live refresh under `make app` ([28]) | Trust the thread; a claims file; excluding source directories from `--reload` | A dead process cannot record, while `LOAD_RUN` survives. A claims file is a second ledger format. Reload exclusion still restarts on other edits |
| Plans `app` (runtime) and `add-cfb-app` (verification); on-demand spend checked against `BACKFILL_SPENDABLE`; the ledger state shown at start ([29]) | The raw `REMAINING_CALLS`; one plan for both; a refusal on the first click | Previews on stage must not eat the weekly refresh's reserve. The proof stays separately capped. A missing cap must show before the demo, not during it |
| Frontend first, a split probe, and a demo-minimum cut line ([31]) | Keep every task behind the merges of Phases 2–5 | The demo is about 3.5 weeks away, and the frontend and the pure backend need no other phase |
| The server picks the preview's game from the pair ([32]) | The client sends a game id | One game per pair; a meaningful cache key; exactly Phase 5's UX contract |
| Minor hardening: replay-safe quota badge, pool limits, request cancellation, StrictMode-safe effects, shared point validation ([33]) | Leave them to implementation | Each closes a failure seen in review (an error banner in replay, unbounded waits, stale responses, double fetches, a route without validation) |

## Features

| Feature | Status | Spec |
|---------|--------|------|
| app/access | NEW | `app/access/spec.md` |
| app/live-spend | NEW | `app/live-spend/spec.md` |
| app/refresh | NEW | `app/refresh/spec.md` |
| app/inspector | NEW | `app/inspector/spec.md` |
| app/as-of | NEW | `app/as-of/spec.md` |
| app/odds | NEW | `app/odds/spec.md` |
| app/predictions | NEW | `app/predictions/spec.md` |
| app/upsets | NEW | `app/upsets/spec.md` |
| app/head-to-head | NEW | `app/head-to-head/spec.md` |
| app/shell | NEW | `app/shell/spec.md` |
| app/map | NEW (UI revision) | `app/map/spec.md` |
| app/team | NEW (UI revision) | `app/team/spec.md` |
| app/league | NEW (UI revision) | `app/league/spec.md` |

Domain `app/`, as `CLAUDE.md` plans it. The Phase 2–5 specs are not changed by delta files; none of them is recorded yet. This plan uses their contracts and leaves these recorder notes:
- **`loader/refresh-week`, `loader/quota`:** unchanged. Recorder note: `make refresh` and the app share `loader/guard.py`; the app's spend counts under its own ledger plan; the app reconciles loader runs that have no ledger entry.
- **`api/on-demand`:** unchanged. Hand-off P6-a below adds `REQ_ROWS` to `GAME_PREVIEW` if Phase 5 accepts it.
- **`api/roles`:** unchanged. The read user `CFB_APP_WEB` and the console user `CFB_CONSOLE_WEB` are created by `make app-user` (`app/access`). Phase 5 left user creation to the owner, and that is what this target is.
- **`cfbd-fetch/fetch-log`** (recorded): unchanged. Its Background already names "the app backend (Phase 6)" as a consumer; the contexts `APP_PREVIEW#…` and `APP_CONSOLE#…` are new values, not a contract change.
- **`cfbd-vs/key-protection`** (recorded): unchanged. `CFB_READER` gains a user, as its spec foresees ("anyone who queries the virtual schema").

## Requirements

| Requirement | Details |
|-------------|---------|
| Zero CFBD calls in tests | Integration, component and E2E tests use the mock, replay and synthetic data; the harness guard against `CFBD_API` stays. `scripts/app.py perf` never touches a live endpoint (unit-tested). Live verification: ≤ **12** ledgered calls under plan `add-cfb-app` (8 planned, task 8.2), in November (authorized for 1000); the October total stays ≤ 400 |
| Explicit live spend | Every action that can call CFBD has a 0-call plan, a cost label or confirmation, a server re-plan against `max_calls`, ledger and quota guards (`BACKFILL_SPENDABLE` for previews and the console), one live request at a time, no retry, one ledger entry per live request even when it fails, fetch-log rows, and reconciliation of loader runs (`app/live-spend`) |
| Read-only pages | Pages read only `CFB_API` and `CFB_API_LIVE`, as `CFB_APP_WEB` (lint plus integration test); only `live.py` names `GAME_PREVIEW` (lint). The start proves the effective privileges of the read and console users |
| Operator path | The owner, through `vs/host/target.py` (the password read at connect time, never stored); the allowlist only (unit-tested statement templates); on only when every bind address is loopback; one connection per action |
| Secrets | No password, key or DSN secret in the repository, logs, API responses or the built frontend (`app/access` scenario). `.env` stays git-ignored. The CFBD key stays in `CFBD_API` only (CLAUDE.md) |
| CFBD terms | Credit in every page's footer; "not official NCAA or CFP data"; no raw response in git; the preview's `RAW_JSON` is never sent to the browser; fixtures are synthetic (sim-league names) |
| Browser safety | Loopback by default; a `Host` allowlist (name and port) on every request; `X-CFB-Action`, JSON and a configured `Origin` on every `POST`; CORS only for listed origins (never `*`); a pinned Vite dev server; CSP `default-src 'self'` with no inline script and no foreign host; nosniff; `frame-ancestors 'none'` |
| Accessibility and design | Base text ≥ 18 px; contrast ≥ 4.5:1 for text and ≥ 3:1 for controls and marks, in both themes (unit-tested); keyboard reachable; states as icon plus word; a table view for every chart; dataviz palette validated in both themes |
| Performance | The budget above; misses recorded with a breakdown and a logged decision |
| Runtime | Python 3.12 (uv; group `app`: fastapi, starlette, uvicorn, exact pins); Node ≥ 22.12 (local 24.15.0); one process; `TARGET=local\|aws` |
| Ownership | `sys` owns every schema; the app adds no database object except its two users |
| Shared database etiquette | Integration tests, the probes, the E2E smoke and `make app-perf` run in "local free" windows under `flock <scratchpad>/cfb-it.lock`; AWS `default`: deploy and run only; no database session during planning |

## Dependencies

- **Gates by group** (Parallelization):
  - Groups A, A-FE1, A-FE2 and A3 need no other phase. Probe 1.1 needs only a "local free" window.
  - Group P2 (1.9, 2.4) needs **Phase 2 merged into `main`**: `scripts/loader_cli.py`, the Phase 2 `vs/host/ledger.py` and `vs/live/ledger.yaml` with the plan caps `add-cfb-app: 12` and `app: 60`.
  - Group B onward needs **Phases 2–5 implemented and merged into `main`** with green integration tests. This plan uses:
    - Phase 5's serving schemas and roles, `analyses/whatif.py`, the harness helpers (`deploy_analyses`, `seed_analyses_history`, `complete_week`, `publish`, `app_connection(role)`, `snapshot_restore_at`, the user `CFB_TEST_APP`) and the `load-plan.analyses.yaml` variant;
    - Phase 4's sim league, `SIM_CONFIG` and `hold_deploy_lock`;
    - Phase 3's as-of points and `CFB_MODEL.GAME` (through `CFB_API.GAMES`);
    - Phase 2's `REFRESH_WEEK`, `TEST_STEP_SLEEP`, `TEST_STEP_FAIL`, `scripts/loader_cli.py`, the ledger configuration and the harness.
  - Group G needs the live groups of Phase 2 (J) and Phase 5 (7.3) done, in the same window protocol, and the live group of Phase 4 (J) before it.
- **Hand-offs** (for the orchestrator to record in the plans named):
  - **P6-a (Phase 5):** add `REQ_ROWS` to `CFB_API_LIVE.GAME_PREVIEW`, so the fetch log takes the rows per slice from the view. Fallback: the backend counts the result rows per slice. For a single-object preview that count is 1, and it is equal by construction while the view adds no row filter.
  - **P6-b (Phase 5):** the harness keeps the password of `CFB_TEST_APP` in process memory, available to `app_stack.py`, so the E2E child process can be given it through its environment. (The foundation harness already keeps `CFB_TEST_READER`'s; task 2.3 adds an accessor.)
  - **P6-c (Phase 2):** the guard extraction (task 1.9) happens here, after Phase 2 has merged, and keeps its CLI tests green unchanged. The orchestrator tells the Phase 2 session that `scripts/loader_cli.py` will be refactored, that the app's live refresh holds the ledger lock for its whole run, and that the CLI's lock during replay runs makes app live requests answer busy. Optional for Phase 2: the CLI may call `guard.reconcile` before a live run; this plan does not require it.
  - **Phase 7:** the chat slot contract (`app/shell`): the AppState store and its actions, the region that `GET /api/config` `chat` turns on, and `/api/chat/`. The app adds no MCP or Claude code.
  - **Phase 8:**
    - the operator path on a non-loopback bind needs authentication; a hosted read-only app needs its address in `APP_ALLOWED_HOSTS`;
    - the scale dial (a manual 1M run published from the app) is one more allowlist entry;
    - the replay recordings for the console's `fan-out` (3 slices) are made with `make record` under the ledger;
    - AWS has no BucketFS replay host directory (`ORCHESTRATOR-STATUS.md`), so the AWS demo is live-only; the rehearsal and the fallback are local, in replay;
    - the rehearsal recipe ends with `make app-serve` and opens the app at the restored "Sunday after week N"; the refresh plan's week-completeness line shows in advance whether beat 4's "pages move" will happen;
    - the stretch items of the demo-minimum cut line, if the orchestrator applies it.
- **`add-engine-spikes`:** none.
- **User prerequisites:**
  - U1: "local free" windows.
  - U2 (resolved 2026-10-22): the owner authorized 1000 calls for 2026-11, and the orchestrator added the plan caps `add-cfb-app: 12` and `app: 60` to `vs/live/ledger.yaml` on the Phase 2 branch. No action left; the app warns at start if a cap is missing.
  - U3: the owner reviews decisions [2], [3], [10] and [24] (the backend process holds the `sys` connection on loopback; a curated console as a reader). This does not block any task.
  - U4: the owner sets `CFB_APP_PASSWORD` and `CFB_CONSOLE_PASSWORD` in `.env` and runs `make app-user` once per deployment (user guide, task 7.1).

## Migration

No data migration: the app adds no database object except its two users.

| Current | New |
|---------|-----|
| `make test`: unit + CLI | + `app-test` (vitest) |
| `make lint`: ruff, mypy | + `npm run lint` (eslint) and `npm run typecheck` (`tsc --noEmit`); mypy `files` + `app` |
| `make test-it`: `-m "it and not network"` | `-m "it and not network and not e2e"`; `make app-e2e` runs the `e2e` marker |
| `make coverage` | + a report for `app/backend/{settings,security,spend,sessions,sqlcatalog,explain,console_catalog}.py` at ≥ 90 % |
| `pyproject.toml` | + group `app` (fastapi, starlette, uvicorn, `==` pins) and `[tool.uv] default-groups = ["dev", "app"]`; dev + httpx; pytest marker `e2e`; coverage source + `app/backend` |
| `scripts/loader_cli.py` | delegates to `loader/guard.py`; output, exit codes, lock and record behavior unchanged |
| `scripts/record.py` | imports `planned_live`, `slice_calls`, `query_costs`, `charged` from `vs/host/livecalls.py` |
| `vs/live/ledger.yaml` | no change by this plan: the Phase 2 branch already holds `add-cfb-app: 12`, `app: 60`, `2026-10: 400` and `2026-11: 1000` |
| `.gitignore` | + `app/frontend/test-results/`, `app/frontend/playwright-report/` (`node_modules/`, `dist/`, `cache/` and `.env` are already ignored) |
| `tests/it/harness.py` | `leftovers` and `drop_namespace` also drop test app users (`CFB_TEST_APP2`); an accessor for `CFB_TEST_READER`'s password; the app stack lives in `tests/it/app_stack.py` |
| Local `.env` (not committed) | + `CFB_APP_PASSWORD`, `CFB_CONSOLE_PASSWORD` |

## Implementation Tasks

Tasks are TDD per `/speq:code-guardrails`. Host code writes its unit tests first; each backend task writes its integration test file first (failing), then the code; each frontend task writes its component tests first. Integration tests run with `flock <scratchpad>/cfb-it.lock make test-it` in "local free" windows at 0 CFBD calls. Items marked *(stretch)* are above the demo-minimum cut line (Parallelization) and may move to Phase 8.

**Who starts and who finishes each integration test file.** A scenario is written by the task that builds the last piece it needs, never earlier. Tasks in the same group never share a test file.

| File | Started by | Scenarios added later by | Green at the end of |
|---|---|---|---|
| `tests/it/test_app_shell_it.py` | 2.2 | — | 2.3 |
| `tests/it/test_app_access_it.py` | 2.2: refuses read user, privilege through role/PUBLIC/connection, operator follows bind, foreign `Host`, cross-origin `POST`, `make app-user` | 6.1: pages through the role, no secret | 6.1 |
| `tests/it/test_app_as_of_it.py` | 3.1: opens at current, after-current (odds), no data | 4.2: follow after publish; 6.1: earlier week | 6.1 |
| `tests/it/test_app_odds_it.py` | 3.1: odds and arrows | 3.2: what-if, guide, invalid conditions | 3.2 |
| `tests/it/test_app_predictions_it.py` | 3.3 (with the after-current fallback of a non-odds route) | — | 3.3 |
| `tests/it/test_app_upsets_it.py` | 3.4 | — | 3.4 |
| `tests/it/test_app_head_to_head_it.py` | 3.5: series, page load, never met | 4.1: click, latest meeting, cache | 4.1 |
| `tests/it/test_app_live_spend_it.py` | 4.1 | — | 4.1 |
| `tests/it/test_app_refresh_it.py` | 4.2 (also the live-spend scenario "busy during a refresh") | — | 4.2 |
| `tests/it/test_app_inspector_it.py` | 4.3 | — | 4.3 |
| `tests/it/test_app_e2e_it.py` | 6.2 | — | 6.2 |

1. **Probes, toolchain and pure code** (no database except the probes)
   - [ ] 1.1 Probe, early part: `docs/spikes/s9_app_probe.py --part early`, with its findings in `docs/spikes/s9-app-probe.md`. It runs in a `PROBE_` namespace that it creates and drops (an owner user, a schema, a role, users, a connection to a dummy address with no secret), under the IT lock, at 0 calls. **Gate:** a "local free" window; no other phase.
     - (a) **Invoker rights (decisive).** The probe owner owns a table and two one-line Lua scripts, one that reads the table and one that inserts into it. A probe user with only `CREATE SESSION` and `EXECUTE` on both scripts runs them. Expected: insufficient privileges at the read and at the insert, with nothing written. Record the messages and the version.
     - (d) **Effective privileges.** As a probe user with `CREATE SESSION` and a probe role with `SELECT` on a probe schema: which views and columns show its system, object and role privileges, recursively through roles, and those of `PUBLIC`; its connection privileges (`GRANT CONNECTION` and `GRANT ACCESS ON CONNECTION`); impersonation (`GRANT IMPERSONATION ON`); and the objects and schemas it owns. Repeat after each extra grant: `CREATE TABLE` to the user, `INSERT ON SCHEMA` to the role, `SELECT ON SCHEMA` to `PUBLIC`, both connection grants to the role, impersonation, and an owned object. Record what the user sees itself and what only the database-wide views show, and `PUBLIC`'s grants on a fresh deployment. Also (review m2): after `CREATE USER … IDENTIFIED BY` and `ALTER USER … IDENTIFIED BY`, read the statement text in `EXA_DBA_AUDIT_SQL` (when auditing is on) and in the profiling views: is the password masked? If auditing is off, record that.
     - (e) `ALTER SESSION SET QUERY_TIMEOUT = 30`: accepted, and it aborts a 35-second statement; `= 300` in another session lets the same statement finish.

     **Gates:** (a) fixes [2]: on a definer-rights result, switch the operator's refresh to an `EXECUTE`-only runner user and log it; (d) fixes [25]'s views and the fallback (Q5); (e) fixes the pool's and the live session's timeouts (Q18).
   - [ ] 1.2 Toolchain and scaffolding. Per the version policy, on the day of the task:
     - `app/frontend/package.json` (exact pins), `package-lock.json`, `vite.config.ts` (127.0.0.1:5173, `strictPort`, proxy `/api` with `changeOrigin: false`), `tsconfig.json` (strict), `eslint.config.js`, the vitest configuration (jsdom, jest-dom setup), `src/test/viteConfig.test.ts` (pins the server and proxy values), and one placeholder component test;
     - `pyproject.toml` (group `app` with `==` pins, httpx, mypy and coverage entries, marker `e2e`), `uv.lock`, `.gitignore`, and the Makefile targets of Key Interfaces;
     - `app/backend/__init__.py`.

     **Gates:**
     - `npm ci && npm run build && npm test && npm run lint && npm run typecheck` pass (Q9);
     - **Playwright, in order** (Q8, Q19): (1) `npx playwright install chromium` without `sudo`, then a trivial Playwright test against `vite preview`; (2) else the same test inside `mcr.microsoft.com/playwright:v1.64.1-noble` through podman with `--network host`; (3) else drop Playwright from `package.json` and task 6.2 takes the API-level fallback. Record the path taken in `docs/app-verification.md`.
   - [ ] 1.3 `app/backend/settings.py` and `security.py`: the settings table; bind resolution (every resolved address loopback); the operator decision (auto, on or off × loopback); the `Host` allowlist (name and port; loopback names with the port, the Vite hosts with `APP_DEV`, `APP_ALLOWED_HOSTS`; `*` refused); the configured `Origin` set (never from the request); the CORS parse (refuse `*`); the `POST` check (header, JSON, `Origin`); the security headers and the session cookie. Unit tests first: `tests/unit/test_app_settings.py` and `test_app_security.py`, with a fake resolver (`localhost` resolving to a non-loopback address is not loopback) and the rebinding case (`Host: evil.example:8000` with a matching `Origin` → 400).
   - [ ] 1.4 Move `planned_live`, `slice_calls`, `query_costs` and `charged` from `scripts/record.py` to `vs/host/livecalls.py`; `record.py` imports them, its tests move to `tests/unit/test_livecalls.py`, and the CLI tests stay green. Then `app/backend/spend.py` (pure):
     - plans from manifests;
     - the guard decision for (plan, `max_calls`, ledger state and room with the headroom `MAX_ATTEMPTS − 1`, a missing plan or month cap, `LEDGER=off`, the quota's `BACKFILL_SPENDABLE` with the same headroom, operator state), with the refusal codes and messages of `app/live-spend`;
     - ledger entries from `REQ_*` rows; failure entries (one per planned live slice: status and attempts null, cost `MAX_ATTEMPTS`, the error's first line); entries for `LIVE` rows of a 0-call plan;
     - fetch-log rows through `fetchlog.build_fetch_log_rows`, with a `REQ_ROWS` fallback that counts the rows, and the rows of a failed statement;
     - `LIVE_QUERY_TIMEOUT_SECONDS` and the socket timeout, with a test that computes the fetch function's worst case from `vs/cfbd_vs/http.py` and the resolver and asserts the margin.

     Unit tests first: `tests/unit/test_app_spend.py`.
   - [ ] 1.5 `app/backend/sessions.py`: session ids; the preview cache (15 min, 20 entries, per session, `force`); the single flight per (session, game), inside which a waiter re-checks the cache; one console run per session; the query log (50 entries, ids). It is thread-safe under concurrent requests. Unit tests first: `tests/unit/test_app_sessions.py`, with a fake clock and real threads (two waiters, one fetch).
   - [ ] 1.6 `app/backend/sqlcatalog.py` and `explain.py`:
     - every read statement of Key Interfaces with pyexasol placeholders, `CFB_API.GAMES` for the week's completeness included;
     - the lints: only `{API_SCHEMA}` and `{API_LIVE_SCHEMA}` objects, every identifier quoted, no string interpolation of request values; no virtual schema; and a repository scan in which `GAME_PREVIEW` appears in `app/backend/live.py` only;
     - parsing `EXPLAIN VIRTUAL` results into 0..n manifests.

     Unit tests first: `tests/unit/test_app_sqlcatalog.py` (including `test_game_preview_only_in_live`) and `test_app_explain.py`.
   - [ ] 1.7 `app/backend/console_catalog.py`: the four statements and their expected plans, with the stored-only query over 1869–2025. Unit test first: `tests/unit/test_app_console_catalog.py`, which builds the expected slice keys with the catalog's canonical slice-key code (`vs/cfbd_vs`), checks the `games_teams` requirement rule against `vs/catalog/catalog.json`, and pins the stored query's manifest (no fetch, `budget` null, `sources` `["STORED"]`) from the adapter's planning code.
   - [ ] 1.8 Frontend foundations:
     - `src/theme/tokens.ts`, from a palette run through the dataviz validator for both modes, with the output pasted into `docs/app-verification.md`;
     - `theme.css`, `public/theme-init.js` (CSP-safe theme before first paint) and `src/format/format.ts`;
     - `src/api/types.ts` and `app/backend/models.py`, the pydantic response models, including `config` (ledger, live, console), `timeline.follow` and the error codes;
     - the synthetic fixtures `src/test/fixtures/*.json`.

     Tests first: `src/theme/tokens.test.ts` (contrast floors, base size, pinned palette), `src/format/format.test.ts` ("T-37", "< 0.1 %", records, spend labels, "N seasons in S s", HTTP request lines without a key), and `tests/unit/test_app_fixtures.py` (every fixture parses with its response model).
   - [ ] 1.9 Extract `loader/guard.py` from `scripts/loader_cli.py`: `plan_refresh`, `plan_backfill`, `run` and `record`, with structured results (estimate, mode, quota, verdict, room, outcome) and as parameters: the ledger (the CLI passes a disabled one for `--prefix`; the guard never derives it from the prefix), the ledger plan, the lock policy (the CLI's "every run of the real namespace"; the app's "live runs only"), the retry headroom of the ledger check (CLI 0, app `MAX_ATTEMPTS − 1`) and a fresh-quota flag. Add `reconcile(conn, ns, ledger, plan, clock)` per Key Interfaces. `scripts/loader_cli.py` keeps its output, exit codes, lock and record behavior by delegating. Tests first: `tests/unit/test_loader_guard.py` (fake connection: estimate, live check, verdicts, headroom, ledger record, `LEDGER=off`, prefixed namespace; reconcile: an ended run with `CALLS`, one without (step sum), a stale `RUNNING` run with an unfinished fetch step, a replay run (`REMAINING_BEFORE` null) skipped, an already recorded run skipped, the lock held → pending, the month window). Verify `REMAINING_BEFORE` and the step rows' `SLICES` against `refresh_week.lua` and `backfill.lua`. Phase 2's `tests/cli/test_loader_cli.py` stays green unchanged. **Gate:** Phase 2 merged. [expert]
   - [ ] 1.10 Probe, late part: `docs/spikes/s9_app_probe.py --part late` in the `TEST_` namespace with the mock, under the IT lock, at 0 calls. **Gate:** Phases 2–5 merged and their integration tests green.
     - (b) As `CFB_TEST_APP`: `EXPLAIN VIRTUAL` of the preview statement (the manifest and its `fetch_mode`); of a `TEST_CFB_API.PLAYOFF_ODDS` query (no rows, or an error, and which); of a contradiction through the view (`GAME_ID = 1 AND GAME_ID = 2`: 0 planned). As `CFB_TEST_READER`: `EXPLAIN VIRTUAL` of the console's `stored` query, plain and with its `GROUP BY` (no fetch, no budget, over 1869–2025).
     - (c) **Reads during `SIMULATE` and `PUBLISH`** (review M7). With `SIM_CONFIG` raised so that `SIMULATE` takes at least 10 s, an owner session runs `REFRESH_WEEK(2026, 10, 'regular')`. A reader thread as `CFB_TEST_APP` reads `REFRESH_TIMELINE`, the odds, `what_if_odds_sql` and the guide in a loop. Each read is tagged with the step `RUNNING` at that moment. Record latencies, waits, transaction conflicts and errors per step, above all during `PUBLISH`, which regenerates the what-if views.

     **Gates:** (b) fixes the inspector's no-pushdown handling (Q2, Q3), the fetch-mode source (Q20) and the stored query's form (Q21); (c) fixes the poll design (Q4) and the pool's retry and timeout policy for 0-call statements, and confirms the follow-mode hold.

2. **Backend core**
   - [ ] 2.1 `app/backend/db.py` and `operator.py`:
     - **ReadPool:** 4 sessions as the read user; autocommit; the timeout per 1.1(e); checkout waits at most 5 s, then 503 `busy`; a broken session is closed and replaced on the next checkout; one retry on a transaction-conflict error for 0-call catalog statements only (a statement marked live is refused by the pool); recording `stmt.query`, the principal, the milliseconds and the rows to the session's query log; mapping an unreachable database to 503 with `TargetError`'s message.
     - **LiveSession and the console session factory:** one read-user session reserved for live statements, and a console-user session per console action, both with the live timeouts; replaced when broken.
     - **The effective-privilege check** per 1.1(d), for the read and console users: the positive rule of `app/access`; the operator fallback; `APP_SKIP_READ_CHECK`.
     - **OperatorPath:** enabled and reason; an owner connection per action through `vs/host/target.py`, or the test owner for a prefixed namespace; a statement recorder for the allowlist test.

     Unit tests first: `tests/unit/test_app_db.py` and `test_app_operator.py` (fake connections; allowlist templates; the positive rule for each privilege route; no password in any message). **Gate:** 1.1(d, e), 1.3, 1.5, 1.6; no other phase.
   - [ ] 2.2 `app/backend/main.py`, `scripts/app.py` (`serve`, `dev`, `user`) and the Makefile targets:
     - `create_app` with injected factories and ledger; startup: the privilege check, the ledger state, the first reconciliation;
     - `/api/config` (operator, console, ledger, live reasons, skipped read check, dev, fetch mode) and `/api/health`;
     - static serving with the SPA catch-all, the cache headers and the security headers;
     - the middleware order `Host`, `POST` check, CORS;
     - the error handlers (400, 403, 409, 502, 503, with JSON bodies and no driver text);
     - one worker; `scripts/app.py dev` sets `APP_DEV` and refuses a Vite host override; `user` creates the read and console users.

     Integration test files first: `tests/it/test_app_shell_it.py` (one process serves, with no inline script in `index.html`; outage) and the first scenarios of `tests/it/test_app_access_it.py` (per the table). CLI tests: `tests/cli/test_app_cli.py` (argument checks, refusal without `dist/`, `user` without a password, the console user, a Vite `--host` refused, no secret in the output).
   - [ ] 2.3 `tests/it/app_stack.py` and `tests/it/fixtures/load-plan.app.yaml`, per Key Interfaces: the session fixture, `reset()`, `refreshed(10)`, `app_client()` with its overrides, `served_app()` (restartable, killable), the temporary-ledger helper, `hold_ledger_lock()`, `mock_delay(path, ms)`, `mock_fail(path, status)` and the harness accessor for `CFB_TEST_READER`'s password. All app helpers are written here, so later tasks do not edit them in parallel. A smoke test `tests/it/test_app_stack_smoke.py`: the base state (two publications, the current point at 2026 week 9) and `reset()` round trips.
   - [ ] 2.4 `app/backend/live.py`, the guarded execution of Key Interfaces: the preview plan and fetch statements (the only place that names `GAME_PREVIEW`); the fetch mode from the preview's 0-call `EXPLAIN`; the live gate (in-process, wait ≤ 5 s); the busy checks (own refresh action, newest refresh run `RUNNING` with the operator's stale check, reconciliation pending); `Ledger.exclusive()` for live plans only, held → `live_busy`; the re-plan and the guard through `spend.py`; one execution on the live or console session; a `finally` that writes the ledger entries before the fetch-log rows; the charging of `LIVE` rows from a 0-call plan; the single-flight hook. Unit tests first: `tests/unit/test_app_live.py` with fake connections, a fake ledger and real threads: two live requests serialize; a 0-call plan never touches the lock; a held lock answers `live_busy` naming the holder; an exception charges `MAX_ATTEMPTS` per planned slice and the statement is executed exactly once; a failing fetch-log insert leaves the ledger entry; a reconcile-pending state refuses. **Gate:** Phase 2 merged, 1.4, 1.5, 2.1. [expert]

3. **Read endpoints**
   - [ ] 3.1 `routes/config`, `points`, `odds` (odds rows, run banner, settings, what-if availability) and `teams`, with `routes/deps.py`: the one point validation for every route that takes a point (unknown or after-current → the current point plus a notice). Integration tests first: `tests/it/test_app_as_of_it.py` (scenarios 1, 3 for the odds endpoint, 4) and `tests/it/test_app_odds_it.py` (scenario 1).
   - [ ] 3.2 `routes/whatif`: games, conditional odds and the conditional guide *(stretch)* through `analyses/whatif.py`; the unconditional guide; the what-if point compared as (season of `WHATIF_RUN`, `WHATIF_AS_OF_WEEK_SEQ`); 400 on `ConditionError`; 409 `whatif_run_changed` on an empty result or an unknown-column error; the what-if games cached per run id. Integration tests: the remaining scenarios of `tests/it/test_app_odds_it.py`.
   - [ ] 3.3 `routes/predictions`: predictions, scorecard, the season to date *(stretch)* and calibration *(stretch)*, with the population *(stretch)*, through `routes/deps.py`. Integration tests first: `tests/it/test_app_predictions_it.py`, including the after-current fallback of this non-odds route (as-of scenario 3).
   - [ ] 3.4 `routes/upsets`, with the all-time and line-era lists *(stretch)*. Integration tests first: `tests/it/test_app_upsets_it.py`.
   - [ ] 3.5 `routes/matchup`: series, meetings, next meeting, never met; and the function that picks the pair's preview game (next meeting at the current point, else the latest meeting), used by 4.1. Integration tests first: `tests/it/test_app_head_to_head_it.py` (series, page load without a request, never met).

4. **Live, operator and inspector**
   - [ ] 4.1 `routes/preview`, `routes/quota` and `ops/fetchlog.py` over `live.py`:
     - the preview plan by pair, with the cost label, the cache state and the busy or disabled reason;
     - the guarded fetch of the server-chosen game, the session cache with `force`, the single flight, the response without `RAW_JSON`, and 502 `cfbd_failed` with the calls charged;
     - the quota: live, cached 5 min, with `fresh`; replay, `{mode: "replay"}` without a query.

     Integration tests first: `tests/it/test_app_live_spend_it.py` (every live-spend scenario except "busy during a refresh"), plus the click, latest-meeting and cache scenarios in `tests/it/test_app_head_to_head_it.py`.
   - [ ] 4.2 `routes/refresh`, `routes/timeline`, `routes/actions`, `ops/refresh.py` and `ops/reconcile.py`, over `loader/guard.py`. [expert]
     - the plan: fresh quota, `REFRESH_SPENDABLE`, the ledger room with the headroom, "plus retries", the running runs, the week's completeness from `CFB_API.GAMES`, and the dev-mode rule;
     - the run: the argument checks, a single flight per process, reconciliation first, the re-plan against `max_calls`, a background thread with its own owner connection (socket timeout 900 s) that notes its session id, the early run-id lookup, the ledger lock for the whole of a live run, the action's state and outcome with the point before and after, and the ledger record under the app plan;
     - reconciliation at startup, before each live refresh, and when the timeline sees a refresh run leave `RUNNING`; the pending state;
     - the timeline of the newest refresh run, or a chosen run, with its `FETCHES` and the follow key with `hold` and `stuck`;
     - the mapping of the lock refusal and the `CHECK` refusal to 409 with the database's message. Verify that `hold_deploy_lock` takes the lock `REFRESH_WEEK`'s `CHECK` refuses on (Q15); if not, start a run with `TEST_STEP_SLEEP` in another session instead.

     Integration tests first: `tests/it/test_app_refresh_it.py` (the ten refresh scenarios and live-spend "busy during a refresh", using `TEST_STEP_SLEEP`, `TEST_FAIL_PUBLISH`, `hold_deploy_lock`, `hold_ledger_lock()` and `served_app()`), plus the follow-after-publish scenario in `tests/it/test_app_as_of_it.py`. Record in `docs/app-verification.md` whether the killed process's run was aborted or finished (Q16).
   - [ ] 4.3 `routes/inspector`, `routes/console` and `console.py`: the query log; explain on demand as the entry's principal ("no virtual table: 0 calls" per 1.10(b)); each slice key as its HTTP request; the console on the console path, its explain and its guarded run through `live.py` with `APP_CONSOLE#<action>`, available only with the operator path and a console user; the refresh requests per run, polled during the run. Integration tests first: `tests/it/test_app_inspector_it.py`, using `mock_delay("/games/teams", 400)` and `register_slice` for 1997.

5. **Frontend** (component tests first, against the fixtures of 1.8 and in `StrictMode`; no database and no other phase; starts after 1.2 and 1.8; each task owns its own directory)
   - [ ] 5.1 Shell (`src/shell`, `src/state`, `src/api`, `App.tsx`):
     - path routing; the AppState store with its actions; follow mode by the follow key, holding reloads while `hold`, reloading once after, and the "stuck" notice;
     - the header: as-of picker, quota badge (replay-safe), ledger badge ("ledger off", disabled reasons, skipped read check), theme switch, inspector switch;
     - the footer, the outage banner and the chat slot;
     - the API client: `X-CFB-Action` and JSON on every `POST`, never retried; one `AbortController` per page load, aborted on a point or page change; typed errors including `live_busy`, 502 and 503 `busy`; polling hooks that start one interval per mount and clear it, so `StrictMode` double effects do not double the polls.

     Tests: `AppShell.test.tsx`, `Header.test.tsx`, `QuotaBadge.test.tsx`, `Footer.test.tsx`, `ThemeSwitch.test.tsx`, `AsOfPicker.test.tsx`, `src/state/followCurrent.test.tsx`, `src/api/client.test.ts` and `ChatSlot.test.tsx` *(stretch)*.
   - [ ] 5.2 Odds (`src/odds`, `src/charts` bars): the table with movement in percentage points, the run banner, the what-if panel (coming week first, ≤ 16, debounce 250 ms, sample and flags, method label, 409 reload, toggles only at the what-if point by season and week sequence), the rooting guide (unconditional; conditional *(stretch)*), and the focus team from the URL. Tests: `OddsTable.test.tsx`, `WhatIfPanel.test.tsx` and `OddsPage.test.tsx`.
   - [ ] 5.3 Predictions (`src/predictions`, `src/charts` dot plot): the headline with the reconstruction label, the line beside it, the two prediction lists; the season to date, the calibration chart with its table view and the population switch *(stretch)*. Tests: `ScorecardHeadline.test.tsx`, `CalibrationChart.test.tsx` and `PredictionsPage.test.tsx`.
   - [ ] 5.4 Upsets (`src/upsets`): the upset of the week, "T-n" with the source, the week's other upsets, the empty week; the two top-25 lists and the pre-window games *(stretch)*. Tests: `UpsetsPage.test.tsx`.
   - [ ] 5.5 Head-to-head and spending (`src/headtohead`, `src/spend`): the team pickers, the series, the meetings, the next meeting with the "as of" label, `PreviewCard` (pair-based plan label, click only, disabled while fetching, a fetching state, cached state and "fetch again", busy and disabled reasons, the failed-and-charged notice), `SpendButton`, `ConfirmSpend` and `SpendNotice`. Tests: `PreviewCard.test.tsx` and `SpendButton.test.tsx` (the live-spend UI scenario).
   - [ ] 5.6 Refresh and inspector (`src/refresh`, `src/inspector`, `src/charts` timeline and waterfall):
     - the refresh panel: plan with "plus retries" and the week's completeness, confirmation, run, the action state with the point before and after, the refusal notice, the dev-mode and operator reasons;
     - the timeline, polled every 1 s or 5 s, with an elapsed counter;
     - the inspector drawer: the query log, the plan view with HTTP request lines, "copy SQL" *(stretch)*, the `REQ_*` lines, the waterfall, the console (labeled with the reader principal; `over-budget` *(stretch)*) and the refresh runs with their requests during the run.

     Tests: `RefreshPanel.test.tsx`, `Timeline.test.tsx`, `Inspector.test.tsx` and `Waterfall.test.tsx`.

6. **End to end, access and performance**
   - [ ] 6.1 Complete `tests/it/test_app_access_it.py` (every page through the app role; no secret reaches the browser, with a built frontend) and `tests/it/test_app_as_of_it.py` (an earlier week moves every page *(stretch)*).
   - [-] 6.2 E2E smoke. *Superseded on 2026-10-23 (UI revision, light process): the app now opens on the map, and the owner asked for few tests. The flows below become a manual walk in V.3 (map → team page → league odds → refresh in replay → head-to-head preview click), recorded in the verification report; a Playwright script is optional, not a gate.*
     - **With Playwright** (local or in the podman image, per gate 1.2): `app/frontend/e2e/smoke.spec.ts`, driven by `tests/it/test_app_e2e_it.py` (markers `it` and `e2e`) against `served_app()` in replay. It opens the app at 2026 week 9 and sees the odds with arrows; runs the refresh of week 10, sees `WAIT` running and the "pages update when it publishes" notice while the odds page keeps week 9, then every step done and the pages at week 10; and on head-to-head checks that no preview request was sent on load, clicks the preview, and sees "fetching", then `REPLAY`, with no request to the mock.
     - **Fallback:** the same flows as API-level steps in `tests/it/test_app_e2e_it.py` over `served_app()` with httpx, and the reason logged in this plan's decision log.
   - [ ] 6.3 `scripts/app.py perf` (`make app-perf`), with the fixed catalog of read endpoints. Unit test first: `tests/unit/test_app_perf.py`, which asserts that no preview, quota, console or refresh endpoint is in the catalog.
     - A local run in a "local free" window on the restored real data with a 100k published run (Phase 5's task 7.4 state): every local row of the budget, plus the replay preview click and the timeline visibility, with sizing recorded *(stretch: else measured on the 8.1 rehearsal)*.
     - A projector and accessibility pass: a keyboard-only walk through the map (list view), a team page, the league tabs, head-to-head, the inspector and the refresh; a review at 1280×720 in both themes *(stretch beyond the contrast tests)*.

     Results go to `docs/app-verification.md`.

7. **Documentation** (each file re-read first; `docs/roadmap.md`, `specs/architecture.md` and `CLAUDE.md` are edited only here)
   - [ ] 7.1 `docs/user-guide.md`, section "The app":
     - creating the users (`CFB_APP_PASSWORD` and `CFB_CONSOLE_PASSWORD` in `.env`, `make app-user`, `ROTATE=yes`);
     - `make app`, `make app-build` and `make app-serve` (local and `TARGET=aws`); no live refresh under `make app`;
     - the operator path and the loopback rule; the `Host` allowlist and `APP_ALLOWED_HOSTS`; WSL2's forwarding of `127.0.0.1` to Windows `localhost`, not to the LAN;
     - what read-only does not mean: the read user can spend preview calls, and the console user any virtual-schema query, from a SQL client; the ledger guards the app's spend, not the roles'; probe 1.1(d)'s finding on passwords in statement text;
     - explicit spending, the ledger plans `app` and `add-cfb-app`, the "ledger off" badge (`LEDGER=off` for one's own key), busy refusals and reconciliation;
     - replay mode in the app; the inspector and the console; what each page reads.
     - (UI revision) the map, team page and league tabs; `make logos` (an external fetch to the logo host, not a CFBD call; the cache in `~/.cfb-demo/logos`; never committed; `APP_LOGOS=off`); `make app-fixtures` and `make app-preview REAL=1`.
   - [ ] 7.2 `specs/architecture.md`:
     - § Components, Backend and Frontend: the three principals, the routes, the inspector and the console;
     - § Security model: the users `CFB_APP_WEB` and `CFB_CONSOLE_WEB`, the operator path, the `Host` and `Origin` rules, the positive privilege check, and what the roles can still spend outside the ledger; the fact that Lua scripts run with the caller's rights, with probe 1.1(a); "+ execute on its functions" removed;
     - § Fetch log: the app writes through the operator path with `APP_PREVIEW#` and `APP_CONSOLE#`, after the ledger; reconciliation of loader runs;
     - the overview diagram's app line.
   - [ ] 7.3 `docs/roadmap.md`:
     - the decisions marked `Promotes to ADR: yes`, numbered after the last D-number present;
     - Phase 6 marked done with its bullets: the console; the operator path; the refresh button over `REFRESH_WEEK`, with the wording `EXECUTE SCRIPT` instead of `CALL` in demo beat 3;
     - the backlog: authentication for a network-bound operator path; app-triggered forced re-sims and publications; `PROMOTE()` of previews; a free-text console; a cancel button; the stretch items left over, if the cut was applied.
   - [ ] 7.4 `CLAUDE.md`, dense bullets:
     - the domain `app/`, the code in `app/backend` and `app/frontend`, and the make targets;
     - the frontend toolchain (Node ≥ 22.12, `npm ci`, the lockfile, exact pins, TypeScript 6.0 because of typescript-eslint), with eslint in `make lint` and vitest in `make test`;
     - the live-call sources gain the app (preview, console, refresh), ledgered under `app`; `Ledger.exclusive()` does not wait; the app reconciles loader runs;
     - the operator path rule (loopback only, allowlist) and the browser-safety rules;
     - the `.env` keys `CFB_APP_USER`, `CFB_APP_PASSWORD`, `CFB_CONSOLE_USER` and `CFB_CONSOLE_PASSWORD`;
     - this plan's ledger cap.

     `specs/mission.md`:
     - § Commands (the `app-*` targets, replacing the planned `make build`);
     - § Project Structure (`app/backend`, `app/frontend`);
     - § Tech Stack (versions);
     - glossary entries "Operator path", "Read path", "Console path" and "Live spend".

8. **Live verification** (last; AWS `default` deploy and run only; local in a "local free" window)
   - [ ] 8.1 Local rehearsal at 0 calls:
     - restore the "Sunday after week N" snapshot (Phase 2), `make deploy`, `make grants` and `make app-user`;
     - `make app-build && make app-serve`;
     - click through beats 1–4 in replay: the hook, the inspector's console (contradiction and the 157-season stored query; the fan-out only if its recordings exist, else its plan), the refresh of week N+1 with the timeline and the week-completeness line, and the pages moving after the publication;
     - record the timings (and the budget rows that 6.3 left as stretch) and check that the ledger and `/info` are unchanged.
   - [ ] 8.2 AWS live, at most 12 calls for this plan with **8 planned**, under `APP_LEDGER_PLAN=add-cfb-app`, in November (authorized for 1000). It runs after Phase 2's live group J and Phase 5's task 7.3, in a window agreed with the orchestrator in which no other session spends calls; `make dns-check TARGET=aws` first. Then `make app-serve TARGET=aws` on loopback, and:
     - **1 preview** of a coming-week game (median latency with the replayed and cached clicks as context);
     - the **`fan-out` console query** (3 calls; check the parallel waterfall);
     - **1 refresh** of the current week from the button, only if its plan shows an estimate of at most 4; otherwise refresh in replay and log why.

     The ledger checks pass in sequence: 0 + 1 + 3, 1 + 3 + 3 and 4 + 4 + 3 are all ≤ 12. Read `make quota TARGET=aws` before and after.
   - [ ] 8.3 Zero-call proof and write-up:
     - the ledger entries of `add-cfb-app` are ≤ 12 and match one to one the `FETCH_LOG` rows with `APP_PREVIEW#`, `APP_CONSOLE#` and `REFRESH_WEEK#` contexts of the window;
     - the `/info` drop over the window equals the sum of every ledger entry in it, each listed with its plan;
     - `FETCH_LOG` has no row from tests;
     - `docs/app-verification.md` is complete, with derived numbers only (latencies, counts, resolved versions, the palette report, the Playwright path, the probe findings).

9. **UI revision (2026-10-23)** (§ UI revision; light process: a few component tests per task in `StrictMode` on synthetic fixtures, tests first; real data is for looking, not for tests). Each task follows the Visual design rules.
   - [ ] 9.0 Foundation for the new pages (`src/api`, `src/state`, `src/theme`, `src/ui`, `App.tsx`, `preview/`, `app/backend/models.py`):
     - **Contract:** the types and pydantic models `TeamsMapResponse`, `TeamResponse`, `TeamOutlookResponse`, `LeagueWeekResponse` and `LeagueStatsResponse` (each with `queries`, and `has_logo` on every team), `endpoints.ts`, and synthetic fixtures with sim-league names: `teams-map.json` (coordinates across the 48 states, one team in Honolulu, one without coordinates, a 12-team cluster), `team.json`, `team-outlook.json`, `team-outlook.empty.json`, `league-week.json` and `league-stats.json`; the manifest and `npm run schema`. `preview/fixtureApi.ts` gains `/api/team/<id>`, `/api/team/<id>/outlook`, the league routes, and `/api/logos/<id>.png` served from `CFB_LOGO_DIR` when the file exists (else 404).
     - **Routing:** the page ids `home`, `team`, `week` and `stats`; `url.ts` for `/`, `/team/<id>`, `/league/<tab>` and the old paths (`replaceState`); `mapConference` in the store and as `?conf=`; `home` as the default; `App.tsx` registers placeholder pages in `src/map`, `src/team` and `src/league` for 9.1–9.4 to fill.
     - **Design system:** the tokens (spacing, type, radii, shadows, `--surface-raised`, `--border-subtle`) with their contrast pairs; `src/theme/teamColor.ts`; `src/ui/` `Card`, `Chip`, `TeamMark`, `RankTag`, `RecordLine`, `StatTile` and `SqlButton`.
     - Tests: `url.test.ts` (new paths, old-path redirect), `teamColor.test.ts`, `TeamMark.test.tsx`, `tokens.test.ts`; the existing frontend tests and `test_app_fixtures.py` stay green.
   - [ ] 9.1 Shell and league host (`src/shell`, `src/league/LeaguePage.tsx`, one prop each in the built pages):
     - the header navigation (Map · League · Head-to-head) and a team search over `/api/teams` (a combobox: keyboard, FBS first);
     - the League page, whose tabs host the built Odds, Predictions and Upsets pages and the Week and Stats pages;
     - `TeamMark` in the rows of `OddsTable`, `UpsetTable`, `PredictionList` and the head-to-head pickers, with no change in behavior;
     - the shell restyled to the card and grid rules.

     Tests: `Header.test.tsx` (navigation, search), `LeaguePage.test.tsx` (league: "The built pages are league tabs, and old links still work").
   - [ ] 9.2 Map home (`src/map`):
     - `d3-geo` 3.1.1, `topojson-client` 3.1.0, `us-atlas` 3.0.1, `@types/d3-geo` 3.1.1 and `@types/topojson-client` 3.1.5, pinned exactly, with the lockfile;
     - `geo.ts`: the projection, and the state and nation paths from `states-albers-10m.json`;
     - `layout.ts`: the relaxation and the leader lines;
     - `UsMap.tsx` and `MapPage.tsx`: chips, zoom to fit, zoom buttons, the hover and focus card, the list view and "Not on the map".

     Tests: `layout.test.ts` (Ann Arbor inside Michigan's bounds, Honolulu inside the Hawaii inset, map: "Crowded logos are pushed apart and keep a leader line"), `MapPage.test.tsx` (the other three map scenarios). The shell's logo scenario, frontend half, goes in `TeamMark.test.tsx`.
   - [ ] 9.3 Team page (`src/team`):
     - the hero, schedule and results, ratings and polls (`/api/team/{id}`);
     - the outlook panels (`/api/team/{id}/outlook`): odds with movement, and a trajectory as a small SVG line with a table view; this week; games to watch with `RootingGuide` and `WhatIfPanel` for the focus team; rivalries;
     - the empty states.

     Tests: `TeamPage.test.tsx` (the four team scenarios).
   - [ ] 9.4 League "This week" and "Stats" (`src/league/WeekPage.tsx`, `StatsPage.tsx`): matchup cards with the order and upset-alert rules; four stat cards with `InlineBar` bars and table views; the "SQL" buttons. Tests: `WeekPage.test.tsx`, `StatsPage.test.tsx` (the first two league scenarios).
   - [ ] 9.5 Logos:
     - `scripts/logos.py` and `make logos`, per § Logos;
     - `app/backend/routes/logos.py` (an `APIRouter`, no database) with `APP_LOGO_DIR` and `APP_LOGOS` in `settings.py`;
     - `has_logo` as a helper for the routes.

     Tests first: `tests/unit/test_logos.py`, with a fake fetcher and no network (https upgrade, host allowlist, size and type refusals, the PNG signature, skip unless `FORCE`, the manifest, no `CFBD_VS` in the SQL), and `tests/unit/test_app_logos_route.py`, on a bare FastAPI app with the router (200, 404 JSON, 400 for a non-integer before file access, the dark fallback, no path traversal, `APP_LOGOS=off`).
   - [ ] 9.6 Real-data snapshot: `scripts/app_fixtures.py`, `make app-fixtures`, and `REAL=` in `make app-preview` and `preview/fixtureApi.ts`, per § A real-data snapshot. Unit test first: `tests/unit/test_app_fixtures_export.py` (synthetic rows → models; no statement names `CFBD_VS`; the output directory is outside the repository). Then run it once against the target that holds the backfill (`TARGET=aws`, deploy and run only; 0 CFBD calls) after `make logos`, and record the counts (teams, on the map, with logos) in the task notes.
   - [ ] 9.7 Thin-slice routes (`app/backend/routes/teams_map.py`, `team.py`, `league.py`; the statements in `sqlcatalog.py`; `queries`; `has_logo`). **Gate:** 2.2, Phase 2 merged, and P6-UI-b, c and d deployed. Integration test first: `tests/it/test_app_team_it.py` (map, team, this week and stats through the app role; the league scenario "Travel distance is great-circle kilometres from the home venue"; the shell's logo scenario, backend half).
   - [ ] 9.8 Outlook routes: `GET /api/team/{id}/outlook`, and the run-dependent parts of `/api/league/week` (our pick, stakes). **Gate:** Phases 4–5 merged; 3.1. Extends `tests/it/test_app_team_it.py` (team: "With a published run …" through the API).

## Parallelization

| Parallel Group | Tasks |
|----------------|-------|
| Group A | 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8 |
| Group A-FE1 | 5.1 |
| Group A-FE2 | 5.2, 5.3, 5.4, 5.5, 5.6 |
| Group A3 | 2.1 |
| Group P2 (gate: Phase 2 merged) | 1.9, 2.4 |
| Group B (gate: Phases 2–5 merged) | 1.10, 2.2 → 2.3 |
| Group C | 3.1, 3.3, 3.4, 3.5 |
| Group C2 | 3.2, 4.1, 4.2 |
| Group D | 4.3 |
| Group E | 6.1, 6.2, 6.3 |
| Group F | 7.1, 7.2, 7.3, 7.4 |
| Group G (sequential, gated) | 8.1 → 8.2 → 8.3 |
| Group UI-A (UI revision; no other phase) | 9.0, 9.5 |
| Group UI-B (after 9.0) | 9.1, 9.2, 9.3, 9.4, 9.6 |
| Group UI-R (gate: 2.2, Phase 2 merged, P6-UI-b/c/d) | 9.7 |
| Group UI-O (gate: Phases 4–5 merged, 3.1) | 9.8 |

Sequential dependencies:
- **Start now, with no other phase:** A; then A-FE1 → A-FE2 as soon as 1.2 and 1.8 are done; A3 as soon as 1.1(d, e), 1.3, 1.5 and 1.6 are done. The frontend therefore runs while Phases 3–5 implement.
- **P2** starts when Phase 2 merges: 1.9 needs nothing else; 2.4 needs 1.4, 1.5 and 2.1.
- **B → C → C2 → D → E → F → G** once Phases 2–5 have merged:
  - 1.10 runs at the head of B, beside 2.2; 2.3 needs 2.2's `create_app`. 2.2 also needs 2.1, 1.2 and 1.8.
  - C touches disjoint files: `routes/points`, `odds`, `teams`, `deps.py` with their two test files; `routes/predictions`; `routes/upsets`; `routes/matchup`.
  - C2: 3.2 needs 3.1 and extends `test_app_odds_it.py`; 4.1 needs 3.5, 2.4 and 1.4, and extends `test_app_head_to_head_it.py`; 4.2 needs 1.9, 2.4 and 3.1, and extends `test_app_as_of_it.py`. Their files are disjoint.
  - D: 4.3 needs 4.1 and 4.2.
  - E: 6.1 needs every route and 5.1–5.6 (a built frontend); 6.2 needs 6.1's build and 1.2's gate; 6.3 needs every route and a "local free" window with restored real data.
  - F needs green A–E. G needs F, the live groups of Phases 2, 4 and 5, and U1.
- **UI revision:** UI-A starts now (9.0 and 9.5 touch disjoint files). UI-B starts when 9.0 is done: 9.1–9.4 each own their directory (`src/shell` with `src/league/LeaguePage.tsx`; `src/map`; `src/team`; `src/league/WeekPage.tsx` and `StatsPage.tsx`), and 9.6 owns `scripts/app_fixtures.py` and the `REAL=` part of `preview/`. Suggested staffing, 3 implementers: FE-1 does 9.0, then 9.1 and 9.2; FE-2 does 9.3 and 9.4 after 9.0; BE-1 does 9.5, then 9.6. 9.7 and 9.8 go to whoever does group C. **2.2's gate moves up:** Phase 2 merged and P6-UI-b, c and d deployed are enough for 2.2 and 9.7 (slice S2); the rest of group B keeps its gate.
- **1.1's gates** fix 2.1 (timeouts, privilege check); **1.10's gates** fix 2.1's retry policy (a follow-up inside 2.2), 4.2 (poll, hold) and 4.3 (no-pushdown handling, stored query).

### Demo-minimum cut line

For the orchestrator to apply if a predecessor (most likely Phase 5) lands late. Below the line is what the mid-November demo needs; above it is stretch, which may move to Phase 8 with its scenarios and tests. The cut never touches security or spending.

**Must (the demo cannot run without it):**
- **Shell:** `make app-serve` as one process; the map home, the team page (record, schedule, SP+; the outlook once Phases 4–5 publish), the league tabs and head-to-head (UI revision; the map's zoom buttons and the stats tab are the first UI items to cut); the footer credit; the outage banner; both themes with the tested contrast floors; the CSP-safe theme script; the ledger and quota badges.
- **Security and spending, all of review M1–M7:** the `Host` allowlist, the `Origin` set, the pinned Vite, the bind resolution; the positive privilege check for both users; the spending guard with the ledger written in a `finally`, no retry, the live timeouts, the live gate, the single flight, `live_busy`, reconciliation, no live refresh under `make app`, the ledger state at start, the `BACKFILL_SPENDABLE` rule, and the replay-safe quota badge.
- **As-of:** opening at `CURRENT_POINT`; follow mode by the follow key with the hold until the run ends; the shared point validation.
- **Odds (beats 1 and 4):** the table with arrows (icon and word), the run banner, the focus team, the what-if toggles with sample and flags, and the unconditional rooting guide.
- **Predictions (beat 4):** the scorecard headline with its reconstruction label and the line beside it; last week graded; this week's predictions.
- **Upsets (beat 4):** the upset of the week with "since 1978", "T-n" and the source; the week's other upsets; the empty week.
- **Head-to-head (beat 4):** pickers, series, meetings, the next meeting with stakes, and the preview card (server-chosen game, click only, fetching, cached, fetch again).
- **Inspector (beat 2):** the query log with exact SQL, the plan on demand with HTTP request lines, the `REQ_*` lines, the waterfall, the console's `fan-out`, `contradiction` and `stored` (157 seasons); the refresh's requests as they arrive.
- **Refresh (beat 3):** the plan (cost, mode, quota, ledger room, week completeness), the confirmation, the background run, the 1 s timeline, following external runs, refusals as notices, and the outcome naming the point.
- **Tests and proof:** every integration scenario of the items above; the E2E smoke (Playwright, local or podman, else API-level); 8.1–8.3.

**Stretch (may slip to Phase 8), in the order the orchestrator should cut:**

| # | Item | Tasks touched | Scenarios that move |
|---|---|---|---|
| 1 | The local `make app-perf` timing run (keep the budget table; measure on the 8.1 rehearsal) | 6.3 | — |
| 2 | The keyboard and projector pass beyond the contrast tests | 6.3 | — |
| 3 | The chat-slot component test (the store, the hidden region and the `/api/chat/` 404 stay) | 5.1 | shell: "The chat slot receives the app state" (to Phase 7) |
| 4 | Predictions: the population switch, the calibration chart, the season to date | 3.3, 5.3 | predictions: "The calibration chart compares claimed and actual win shares", "The population switch changes every number"; the season-to-date line of "The scorecard headline is the marked row" |
| 5 | Upsets: the all-time and line-era top-25 lists | 3.4, 5.4 | upsets: "The all-time and line-era lists come from their ranks", "Upsets before the all-time window rank only within their season" |
| 6 | As-of: the URL-addressable point and the picker for earlier weeks (follow mode and the server fallback stay) | 5.1, 6.1 | as-of: "Choosing an earlier week moves every page"; the link step of "Points after the current one are never offered" |
| 7 | Inspector: the `over-budget` console entry and "copy SQL" | 4.3, 5.6 | inspector: "A query over the budget is refused at plan time" |
| 8 | Odds: the conditional rooting guide (with toggles); Phase 5 measured it near its latency limit | 3.2, 5.2 | the "with toggles" half of odds: "The rooting guide of a team, without and with toggles" |

If the cut is applied, the moved scenarios leave this plan's delta specs before recording, and the orchestrator lists them in Phase 8's interview.

## Dead Code Removal

| Type | Location | Reason |
|------|----------|--------|
| Duplicated logic | `scripts/loader_cli.py` `_Session` (estimate, quota, verdict, room, record) | Moved to `loader/guard.py` (task 1.9); the CLI keeps only argument parsing and printing |
| Functions | `scripts/record.py` `planned_live`, `slice_calls`, `query_costs`, `charged` | Moved to `vs/host/livecalls.py` (task 1.4); `record.py` imports them |
| Tests | the unit tests of those four functions | Moved to `tests/unit/test_livecalls.py` |
| Docs | `specs/mission.md` "Planned … `make build` (frontend bundle)" | Replaced by `make app-build` (task 7.4) |

## Verification

### Scenario Coverage

| Scenario | Test Type | Test Location | Test Name |
|----------|-----------|---------------|-----------|
| access: The backend refuses a read user that can do more than read | Integration | `tests/it/test_app_access_it.py` | `test_refuses_read_user_that_can_write` |
| access: A privilege that reaches the read user through its role, PUBLIC or a connection is refused | Integration | `tests/it/test_app_access_it.py` | `test_refuses_privilege_through_role_public_or_connection` |
| access: Every page reads through the app role | Integration | `tests/it/test_app_access_it.py` | `test_pages_read_through_app_role` |
| access: The operator path follows the bind address | Integration | `tests/it/test_app_access_it.py` | `test_operator_path_follows_bind` |
| access: A rebinding attempt with a foreign Host is refused | Integration | `tests/it/test_app_access_it.py` | `test_foreign_host_refused` |
| access: A cross-origin POST cannot act | Integration | `tests/it/test_app_access_it.py` | `test_cross_origin_post_cannot_act` |
| access: No secret reaches the browser | Integration | `tests/it/test_app_access_it.py` | `test_no_secret_reaches_browser` |
| access: make app-user creates the read user once | Integration (CLI + DB) | `tests/it/test_app_access_it.py` | `test_make_app_user_idempotent` |
| live-spend: The plan states the cost before any call | Integration | `tests/it/test_app_live_spend_it.py` | `test_plan_states_cost_without_call` |
| live-spend: A live request is recorded in the ledger and the fetch log | Integration | `tests/it/test_app_live_spend_it.py` | `test_live_request_ledgered_and_logged` |
| live-spend: The server refuses to spend more than the user confirmed | Integration | `tests/it/test_app_live_spend_it.py` | `test_refuses_above_confirmed` |
| live-spend: A ledger or quota without room refuses before any call | Integration | `tests/it/test_app_live_spend_it.py` | `test_ledger_and_quota_refuse` |
| live-spend: Without the operator path a live request is refused and a replayed one is served | Integration | `tests/it/test_app_live_spend_it.py` | `test_operator_off_live_refused_replay_served` |
| live-spend: Replayed answers cost nothing and are still logged | Integration + Component | `tests/it/test_app_live_spend_it.py`, `app/frontend/src/shell/QuotaBadge.test.tsx` | `test_replay_costs_nothing_and_is_logged`, `shows replay without querying the quota` |
| live-spend: Every spending control states its cost | Component | `app/frontend/src/spend/SpendButton.test.tsx` | `states the cost on every spending control` |
| live-spend: A live request that fails is still charged | Integration | `tests/it/test_app_live_spend_it.py` | `test_failed_live_request_charged` |
| live-spend: Two concurrent clicks spend once | Integration | `tests/it/test_app_live_spend_it.py` | `test_concurrent_clicks_spend_once` |
| live-spend: A live request during a refresh is refused as busy, and a replayed one is served | Integration | `tests/it/test_app_refresh_it.py` | `test_live_busy_during_refresh_or_held_lock` |
| live-spend: The ledger state is reported before the first click | Integration + Component | `tests/it/test_app_live_spend_it.py`, `app/frontend/src/shell/Header.test.tsx` | `test_ledger_state_reported_before_click`, `shows ledger off and why live actions are disabled` |
| refresh: The refresh plan names its cost before anything runs | Integration | `tests/it/test_app_refresh_it.py` | `test_plan_names_cost` |
| refresh: A confirmed refresh runs the chain and the timeline follows it live | Integration | `tests/it/test_app_refresh_it.py` | `test_refresh_runs_chain_timeline_live` |
| refresh: A refresh started elsewhere is followed too | Integration | `tests/it/test_app_refresh_it.py` | `test_follows_refresh_started_elsewhere` |
| refresh: A refresh while another run holds the lock is refused cleanly | Integration + Component | `tests/it/test_app_refresh_it.py`, `app/frontend/src/refresh/RefreshPanel.test.tsx` | `test_refused_while_locked`, `shows a lock refusal as a notice` |
| refresh: A refresh the budget cannot cover is refused before any call | Integration | `tests/it/test_app_refresh_it.py` | `test_budget_refusal_before_call` |
| refresh: Only one app-started refresh runs at a time | Integration | `tests/it/test_app_refresh_it.py` | `test_single_app_refresh` |
| refresh: A failed step shows where the chain stopped | Integration | `tests/it/test_app_refresh_it.py` | `test_failed_step_shown` |
| refresh: Without the operator path there is no refresh | Integration + Component | `tests/it/test_app_refresh_it.py`, `app/frontend/src/refresh/RefreshPanel.test.tsx` | `test_no_refresh_without_operator`, `hides the button without the operator path` |
| refresh: A refresh whose app process died is still recorded | Integration | `tests/it/test_app_refresh_it.py` | `test_dead_process_refresh_reconciled` |
| refresh: The development server offers no live refresh | Integration | `tests/it/test_app_refresh_it.py` | `test_dev_server_no_live_refresh` |
| inspector: Every query of a page is listed with its exact SQL | Integration | `tests/it/test_app_inspector_it.py` | `test_page_queries_logged_with_exact_sql` |
| inspector: A serving-view query plans no request | Integration | `tests/it/test_app_inspector_it.py` | `test_serving_query_plans_no_request` |
| inspector: A preview shows its planned request and its request columns | Integration | `tests/it/test_app_inspector_it.py` | `test_preview_plan_and_request_columns` |
| inspector: A year range fans out in parallel | Integration + Component | `tests/it/test_app_inspector_it.py`, `app/frontend/src/inspector/Waterfall.test.tsx` | `test_console_fan_out_parallel`, `draws overlapping bars for parallel slices` |
| inspector: A contradiction costs no call | Integration | `tests/it/test_app_inspector_it.py` | `test_console_contradiction_zero_calls` |
| inspector: Stored history costs no call | Integration | `tests/it/test_app_inspector_it.py` | `test_console_stored_history_zero_calls` |
| inspector: A query over the budget is refused at plan time | Integration | `tests/it/test_app_inspector_it.py` | `test_console_over_budget_refused` |
| inspector: The refresh's requests are listed per run | Integration | `tests/it/test_app_inspector_it.py` | `test_refresh_requests_per_run` |
| inspector: The console exists only with the operator path | Integration + Component | `tests/it/test_app_inspector_it.py`, `app/frontend/src/inspector/Inspector.test.tsx` | `test_console_requires_operator`, `hides the console without the operator path` |
| as-of: The app opens at the current point | Integration + Component | `tests/it/test_app_as_of_it.py`, `app/frontend/src/shell/AsOfPicker.test.tsx` | `test_opens_at_current_point`, `opens at the current point` |
| as-of: Choosing an earlier week moves every page | Integration | `tests/it/test_app_as_of_it.py` | `test_earlier_week_moves_every_page` |
| as-of: Points after the current one are never offered | Integration | `tests/it/test_app_as_of_it.py`, `tests/it/test_app_predictions_it.py` | `test_after_current_points_never_offered`, `test_after_current_point_falls_back` |
| as-of: A page without data at a point says so | Integration + Component | `tests/it/test_app_as_of_it.py`, `app/frontend/src/odds/OddsPage.test.tsx` | `test_point_without_data_is_empty`, `states when a point has no published run` |
| as-of: The pages follow a refresh only after it has published | Integration | `tests/it/test_app_as_of_it.py` | `test_follow_key_changes_only_after_publish` |
| as-of: After a refresh the pages follow the new current point | Component | `app/frontend/src/state/followCurrent.test.tsx` | `holds reloads while the refresh runs, then follows unless a point is pinned` |
| odds: The odds show the published run with arrows since the last published week | Integration | `tests/it/test_app_odds_it.py` | `test_odds_with_movement_and_banner` |
| odds: Movement is shown with an icon and a word, never by color alone | Component | `app/frontend/src/odds/OddsTable.test.tsx` | `shows movement with an icon and a word` |
| odds: A toggle recomputes the odds over the matching seasons | Integration + Component | `tests/it/test_app_odds_it.py`, `app/frontend/src/odds/WhatIfPanel.test.tsx` | `test_toggle_matches_library`, `shows the effective sample and the low-sample flag` |
| odds: A what-if for a superseded run is reloaded, not shown | Integration + Component | `tests/it/test_app_odds_it.py`, `app/frontend/src/odds/WhatIfPanel.test.tsx` | `test_superseded_run_conflict`, `reloads and clears the toggles when the run changed` |
| odds: Toggles are offered only at the what-if run's point | Integration + Component | `tests/it/test_app_odds_it.py`, `app/frontend/src/odds/WhatIfPanel.test.tsx` | `test_toggles_only_at_whatif_point`, `compares the what-if point by season and week` |
| odds: The rooting guide of a team, without and with toggles | Integration | `tests/it/test_app_odds_it.py` | `test_rooting_guide_with_and_without_toggles` |
| odds: Invalid conditions are refused before any query | Integration | `tests/it/test_app_odds_it.py` | `test_invalid_conditions_refused` |
| predictions: The scorecard headline is the marked row | Integration | `tests/it/test_app_predictions_it.py` | `test_headline_is_marked_row` |
| predictions: A reconstructed headline says so | Integration + Component | `tests/it/test_app_predictions_it.py`, `app/frontend/src/predictions/ScorecardHeadline.test.tsx` | `test_reconstructed_headline_labeled`, `labels a reconstructed headline` |
| predictions: This week's predictions are the ones frozen for the next week | Integration | `tests/it/test_app_predictions_it.py` | `test_this_week_predictions` |
| predictions: Last week's games are graded with their results | Integration | `tests/it/test_app_predictions_it.py` | `test_last_week_graded` |
| predictions: The calibration chart compares claimed and actual win shares | Component | `app/frontend/src/predictions/CalibrationChart.test.tsx` | `draws one dot per bucket and a matching table` |
| predictions: The population switch changes every number | Integration | `tests/it/test_app_predictions_it.py` | `test_population_switch` |
| upsets: The upset of the week shows its all-time rank | Integration | `tests/it/test_app_upsets_it.py` | `test_upset_of_the_week` |
| upsets: A shared rank reads T-n | Unit + Component | `app/frontend/src/format/format.test.ts`, `app/frontend/src/upsets/UpsetsPage.test.tsx` | `formats shared ranks as T-n`, `shows the source next to every rank` |
| upsets: Upsets before the all-time window rank only within their season | Integration | `tests/it/test_app_upsets_it.py` | `test_pre_window_upset_season_rank_only` |
| upsets: The all-time and line-era lists come from their ranks | Integration | `tests/it/test_app_upsets_it.py` | `test_all_time_and_line_era_lists` |
| upsets: A week without an upset says so | Integration | `tests/it/test_app_upsets_it.py` | `test_week_without_upset` |
| head-to-head: The series shows without a call | Integration | `tests/it/test_app_head_to_head_it.py` | `test_series_without_call` |
| head-to-head: Opening the page never fetches a preview | Integration + Component | `tests/it/test_app_head_to_head_it.py`, `app/frontend/src/headtohead/PreviewCard.test.tsx` | `test_page_load_makes_no_request`, `never fetches on load, also in StrictMode` |
| head-to-head: A click fetches the next meeting's preview once | Integration + Component | `tests/it/test_app_head_to_head_it.py`, `app/frontend/src/headtohead/PreviewCard.test.tsx` | `test_click_fetches_next_meeting_once`, `shows fetching until the row arrives` |
| head-to-head: A pair without a meeting left previews its latest meeting | Integration | `tests/it/test_app_head_to_head_it.py` | `test_latest_meeting_preview` |
| head-to-head: A second click in the same session is served from its cache | Integration | `tests/it/test_app_head_to_head_it.py` | `test_session_cache` |
| head-to-head: Teams that never met have no series and no preview | Integration | `tests/it/test_app_head_to_head_it.py` | `test_never_met` |
| shell: One process serves the app and its API | Integration | `tests/it/test_app_shell_it.py` | `test_one_process_serves_app_and_api` |
| shell: The footer credits CFBD on every page | Component | `app/frontend/src/shell/Footer.test.tsx` | `credits CFBD on every page` |
| shell: Both themes keep text legible on a projector | Unit + Component | `app/frontend/src/theme/tokens.test.ts`, `app/frontend/src/shell/ThemeSwitch.test.tsx` | `meets the contrast and size floors`, `switches, persists and survives storage errors` |
| shell: A database outage shows a clear state, not a crash | Integration + Component | `tests/it/test_app_shell_it.py`, `app/frontend/src/shell/AppShell.test.tsx` | `test_database_outage_clear_state`, `shows an outage banner with retry` |
| shell: The chat slot receives the app state | Component | `app/frontend/src/shell/ChatSlot.test.tsx` | `receives the app state and drives it` |
| shell: Team logos come from the app, with a badge when one is missing | Unit + Component + Integration | `tests/unit/test_app_logos_route.py`, `app/frontend/src/ui/TeamMark.test.tsx`, `tests/it/test_app_team_it.py` | `test_logo_route_statuses`, `draws the logo or a badge and never asks for a missing logo`, `test_has_logo_matches_cache` |
| map: The map shows every FBS team at its home stadium | Unit + Component | `app/frontend/src/map/layout.test.ts`, `app/frontend/src/map/MapPage.test.tsx` | `projects Ann Arbor into Michigan and Honolulu into the Hawaii inset`, `draws one linked marker per located team and lists the rest` |
| map: Crowded logos are pushed apart and keep a leader line | Unit | `app/frontend/src/map/layout.test.ts` | `separates a 12-team cluster deterministically with leader lines` |
| map: A conference filter narrows and zooms the map | Component | `app/frontend/src/map/MapPage.test.tsx` | `filters, zooms and writes conf to the URL` |
| map: The list view is the map's table view | Component | `app/frontend/src/map/MapPage.test.tsx` | `lists the same teams by conference` |
| team: The team page shows record, schedule and ratings at the point | Component + Integration | `app/frontend/src/team/TeamPage.test.tsx`, `tests/it/test_app_team_it.py` | `shows hero, schedule and ratings`, `test_team_page_reads_through_app_role` |
| team: Outlook panels say when no run is published | Component | `app/frontend/src/team/TeamPage.test.tsx` | `explains empty outlook panels without an error` |
| team: With a published run, the team page shows its odds, prediction and games to watch | Component + Integration | `app/frontend/src/team/TeamPage.test.tsx`, `tests/it/test_app_team_it.py` | `shows odds, trajectory, this week and games to watch`, `test_team_outlook_with_run` |
| team: The team page leads to head-to-head | Component | `app/frontend/src/team/TeamPage.test.tsx` | `links schedule rows and rivalries to head-to-head` |
| league: This week lists the coming games, most interesting first | Component + Integration | `app/frontend/src/league/WeekPage.test.tsx`, `tests/it/test_app_team_it.py` | `orders by interest with source labels and upset alerts`, `test_league_week_reads_through_app_role` |
| league: Each stats card shows its SQL on request | Component | `app/frontend/src/league/StatsPage.test.tsx` | `opens the inspector at the card's entry` |
| league: The built pages are league tabs, and old links still work | Unit + Component | `app/frontend/src/state/url.test.ts`, `app/frontend/src/league/LeaguePage.test.tsx` | `replaces old paths with league paths`, `hosts the built pages as tabs` |
| league: Travel distance is great-circle kilometres from the home venue | Integration | `tests/it/test_app_team_it.py` | `test_travel_km_matches_great_circle` |

86 scenarios (73 + 13 of the UI revision), each mapped to at least one test. Component tests render real components in `StrictMode` against synthetic API fixtures that the backend's response models validate (`tests/unit/test_app_fixtures.py`). Unit tests cover only pure computation: the contrast tokens and the rank format.

Supporting tests back the integration tests; they do not replace them:
- **Unit** (pure, or with fakes; no database):
  - `tests/unit/test_app_settings.py`, `test_app_security.py` (bind resolution, `Host` and `Origin` rules, rebinding), `test_app_spend.py` (guard decisions, failure charging, timeout margin), `test_app_sessions.py` (single flight with threads), `test_app_sqlcatalog.py` (including `test_game_preview_only_in_live`), `test_app_explain.py`, `test_app_console_catalog.py`, `test_app_fixtures.py`, `test_app_perf.py`;
  - `test_app_db.py` (pool limits, 0-call-only retry, positive privilege rule), `test_app_operator.py` and `test_app_live.py` (lock only for live plans, one execution, ledger before fetch log), with fake connections;
  - `test_loader_guard.py` (including reconciliation) and `test_livecalls.py`;
  - frontend: `format.test.ts`, `tokens.test.ts`, `client.test.ts` (no POST retry, abort on change) and `viteConfig.test.ts`.
- **CLI:** `tests/cli/test_app_cli.py`; Phase 2's `tests/cli/test_loader_cli.py` unchanged.
- **Smoke:** `tests/it/test_app_stack_smoke.py`.
- **E2E:** `tests/it/test_app_e2e_it.py` with `app/frontend/e2e/smoke.spec.ts` (local or podman), or its API-level fallback.

### Manual Testing

All commands run from the repo root against Exasol Personal local (`TARGET=local`) after `make deploy`, `make grants`, `make app-user` and `make app-build`, with `make app-serve` running, unless a row names AWS or `make app`. None spends a CFBD call, except the rows marked live (task 8.2, ledgered).

| Feature | Command | Expected Output |
|---------|---------|-----------------|
| app/access | `curl -s http://127.0.0.1:8000/api/config`; `curl -s -o /dev/null -w '%{http_code}\n' -H 'Host: evil.example:8000' http://127.0.0.1:8000/api/config`; `curl -s -X POST -H 'X-CFB-Action: 1' -H 'Content-Type: application/json' http://127.0.0.1:8000/api/refresh/plan -d '{}'`; `APP_HOST=0.0.0.0 APP_ALLOWED_HOSTS=<lan ip>:8000 make app-serve` and `curl -s http://<lan ip>:8000/api/config`; `APP_OPERATOR=on APP_HOST=0.0.0.0 make app-serve` | Operator and console on (loopback); `400` for the foreign `Host`; `403` for the `POST` without `Origin`; operator off with the reason "bound to a non-loopback address"; the last refuses to start, naming `APP_OPERATOR` and the address |
| app/live-spend | Head-to-head, a pair with a coming-week meeting: read the preview button; in replay, click it; then click again; restart with `LEDGER=off make app-serve` | "replay · 0 calls", then a row with `REPLAY` and "0 calls spent", then "cached · 0 calls"; the quota badge reads "replay · no calls"; `make record ARGS="--ledger"` unchanged; with `LEDGER=off` the header shows "ledger off". *(live, AWS, task 8.2)* the label reads "1 live call", and after the click "1 live call spent" and one ledger entry under `add-cfb-app` |
| app/refresh | In replay: refresh, pick the next week, run; then `EXECUTE SCRIPT CFB_INFRA.REFRESH_WEEK(...)` in a SQL client; then `make app` and open the refresh in live mode | The plan reads "0 calls · replay" and names the week's games and how many have not kicked off; the timeline shows `CHECK`, `FETCH …`, `MODEL`, `PREDICT`, `SIMULATE` and `PUBLISH` turning `RUNNING` then `DONE` with seconds, rows and calls; the outcome names the point before and after. The SQL-client run is followed without a click. Under `make app` the live plan reads "live refresh needs make app-serve" |
| app/inspector | Open the inspector on the odds page → plan of `PLAYOFF_ODDS`; console `contradiction` → explain, run; `stored` → explain, run; `over-budget` → explain | "no virtual table: 0 calls"; 0 slices and 0 calls; a plan with no fetch and no budget, then one `STORED` row per season (157), 0 calls, principal `reader (CFB_CONSOLE_WEB)`; refused with "15 requests planned, budget 10". *(live, AWS)* `fan-out` → 3 overlapping bars, 3 `GET https://api.collegefootballdata.com/games/teams?…` lines without a key, and 3 calls |
| app/as-of | Pick last week in the picker; copy the URL into a new tab; edit the URL to a later week; then run a refresh of the next week in a SQL client while the app follows the current point | Every page moves to last week; the new tab shows the same view; the later week falls back to the current point with a notice. During the refresh the header reads "refresh running: pages update when it publishes" and the pages keep the old week; after the run they reload once at the new week |
| app/odds | Odds page; focus a bubble team; set two toggles on the coming week's games | Arrows with words and percentage-point changes since last week; "100,000 seasons in N s on …"; conditional odds with "n of 100,000 seasons" and the method label; the team's coming-week guide labeled conditional |
| app/predictions | Predictions page at the current point; switch the population | The headline record (for example "41–9") with the line beside it, labeled "reconstructed …" when the freeze kind is `ALL`; this week's predictions; the calibration dots and table; every number changes with the population |
| app/upsets | Upsets page at the current point | The upset of the week with "since 1978", "T-n" where tied, the source beside every rank; the all-time and line-era top 25 |
| app/head-to-head | Michigan vs Ohio State | The series since 1897 at 0 calls in < 1.2 s; the next meeting with our prediction and stakes, or the latest meeting as the preview's game; the preview only after a click, with the button disabled while it runs |
| app/map, app/team, app/league (S1, real data, today) | After the backfill and Phase 3's views on the target: `make logos TARGET=aws`, then `make app-fixtures TARGET=aws SEASON=2026`, then `make app-preview REAL=1`; open `http://127.0.0.1:5173/`, pick "SEC", click a team, open League → This week and Stats; `ls ~/.cfb-demo/logos \| wc -l` | The summary says "0 CFBD calls" and counts the external logo fetches; the banner reads "real data snapshot of aws"; about 136 FBS logos on the map with Hawaii in its inset and leader lines in Texas, Florida and the Southeast; "SEC" zooms to the Southeast; the team page shows the real record, schedule with scores and SP+; this week lists the real slate with lines; about 270 files in the cache |
| app/map, app/team, app/league (S2/S3, served) | With `make app-serve`: `curl -s 'http://127.0.0.1:8000/api/teams/map' \| head -c 300`; `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/api/logos/130.png`; `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/api/logos/abc.png`; open `/`, a team page, every league tab; click a card's "SQL"; `APP_LOGOS=off make app-serve` | JSON with `has_logo` per team; `200` (or `404` JSON without a cached logo); `400`; the inspector opens at that card's statement with "no virtual table: 0 calls"; with logos off, only badges, and no request to `/api/logos/` |
| app/shell | `curl -sI http://127.0.0.1:8000/odds`; `curl -s http://127.0.0.1:8000/ \| grep -c '<script>'`; switch the theme and reload; stop the database briefly (`exasol stop -d local` in a local free window), then start it | `200`, `text/html`, the CSP header; `0` inline scripts; the theme is kept without a flash; an outage banner with retry, then the data again without restarting the server; the footer "Data provided by CollegeFootballData.com" on every page |

### Checklist

| Step | Command | Expected |
|------|---------|----------|
| Generate | `make generate && git diff --exit-code -- vs/catalog sql/core` | Exit 0 |
| Build | `uv run python -m vs.bundle && uv run python -m loader.bundle && make app-build` | Exit 0; `app/frontend/dist/index.html` exists |
| Test | `make test` | 0 failures (Python unit + CLI, vitest) |
| Integration | `flock <scratchpad>/cfb-it.lock make test-it` | 0 failures; mock and replay only; 0 requests to `api.collegefootballdata.com` |
| E2E | `flock <scratchpad>/cfb-it.lock make app-e2e` | 0 failures (Playwright local or podman, or the recorded API-level fallback) |
| Lint | `make lint` | 0 ruff, mypy, eslint and `tsc` errors |
| Format | `uv run ruff format --check .` | No changes |
| Coverage | `make coverage` | ≥ 90 % for `vs/cfbd_vs`, `vs/gen`, `loader`, `model`, `engine`, `analyses`, and the backend's pure modules |
| Page SQL | `uv run pytest tests/unit/test_app_sqlcatalog.py` | Every read statement names only `{API_SCHEMA}` and `{API_LIVE_SCHEMA}` objects; `GAME_PREVIEW` appears only in `app/backend/live.py` |
| Operator allowlist | `uv run pytest tests/unit/test_app_operator.py` | Every operator statement matches an allowlist template |
| Browser safety | `uv run pytest tests/unit/test_app_security.py`; `npm --prefix app/frontend test -- viteConfig`; with `make app-serve` running: `curl -s -o /dev/null -w '%{http_code}\n' -H 'Host: evil.example:8000' http://127.0.0.1:8000/api/config` | Pass; pass; `400` |
| Live spending | `uv run pytest tests/unit/test_app_live.py tests/unit/test_app_spend.py tests/unit/test_loader_guard.py` | Pass: one execution per live statement, ledger before fetch log, failure charging, lock only for live plans, reconciliation |
| Secrets | `git grep -n -E '(CFB_APP\|CFB_CONSOLE)_PASSWORD[[:space:]]*=[[:space:]]*[^[:space:]]' -- . ':!docs' ':!specs'`; `flock <scratchpad>/cfb-it.lock uv run pytest tests/it/test_app_access_it.py -k no_secret` | No password value in a tracked file outside documentation (`.env` is ignored); the scan of every response and of `dist/` for the test passwords and the marker key passes (a plain search for the word "password" would match react-dom's input-type table) |
| Performance | `make app-perf` (task 6.3); task 8.2 on AWS | Budget met, or a logged decision with the breakdown; no live endpoint timed |
| Deploy | `make deploy && make grants && make app-user && make app-build && make app-serve` | The server starts on 127.0.0.1:8000 with the operator and console paths on, the privilege check passed, and no missing ledger cap; `/api/health` 200 |
| Zero calls | `make record ARGS="--ledger"`; `make quota TARGET=aws` before and after task 8.2 | Ledger entries for `add-cfb-app` ≤ 12 (8 planned), one to one with the `FETCH_LOG` rows of the window; the `/info` drop equals the sum of every ledger entry in the window |
| No data in git | `git ls-files '*.parquet' '*.cfbd.json' 'cache/*' 'app/frontend/dist/*'` | Empty; the fixtures are synthetic |
| No team marks in git, no hotlinking | `git ls-files \| grep -i -E 'logo.*\.(png\|svg\|jpe?g)$\|teamlogos'`; `git grep -n -i 'espncdn' -- app/frontend app/backend`; after `make app-build`: `grep -rl -i espncdn app/frontend/dist \| wc -l` | Empty; empty (only `scripts/logos.py` names the logo host); `0` |
| Map bundle offline | `make app-build`; `grep -ho 'https://[a-z0-9.-]*' app/frontend/dist/assets/*.js \| sort -u` | No map, tile, font or logo host: at most the documentation links React's error messages carry; the atlas is inside the bundle |
