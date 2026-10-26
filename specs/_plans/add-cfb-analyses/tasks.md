# Tasks: add-cfb-analyses

Worktree: ../exasol-cfb-demo-p5 (branch add-cfb-analyses, from `main` = recorded Phases 1–3)

Light, demo-first replan of 2026-10-23 (decision-log [41]). Details, files, tests and definitions of done per task: plan.md § Implementation Tasks. Markers: `[ ]` open, `[~]` in progress, `[x]` done, `[-]` superseded.

## Slice A: the serving layer without the engine (now)

### Group A-1
- [x] 1.1 Serving deploy (`make api`), roles `CFB_APP`/`CFB_MCP`, harness users, and the thin slice: `CURRENT_POINT`, `AS_OF_POINTS`, `TEAMS`, `GAMES`, `TEAM_GAMES`, `TEAM_SEASONS`, `RANKINGS`, `SP_RATINGS` (P6-UI-b, P6-UI-d)

### Group A-2
- [ ] 1.2 `TEAM_TRAVEL`, `WEEK_GAMES` and the stats views `ROAD_WARRIORS`, `SP_MOVERS`, `CONFERENCE_STRENGTH`, `WIN_STREAKS` (P6-UI-c)
- [ ] 1.3 Head-to-head (`MATCHUP_SERIES`, `MATCHUP_GAMES`, `MATCHUP_NEXT` from the schedule) and `UPSETS` from stored lines
- [ ] 1.4 `CFB_API_LIVE` (`GAME_PREVIEW`, `QUOTA`), `REFRESH_TIMELINE`, `FETCHES`; the chat role refused on the live schema

### Group A-3
- [ ] 1.5 Slice A on AWS and local (0 calls), timings, `make api-docs`, user guide and `CLAUDE.md` bullets → Phase 6's 9.7 gate

## Slice B: the engine's numbers (gate: Phase 4 1.4, 2.1 and 2.2 merged into this branch)

### Group B-1
- [ ] 2.1 `PLAYOFF_ODDS`, `PUBLISHED_RUNS`, `WHATIF_RUN`, `WHATIF_GAMES`, `WHATIF_OUTCOMES`, `WHATIF_PLAYOFF`, `WHATIF_TEAMS`, `ROOTING_GUIDE`, `ANALYSIS_SETTINGS`; run columns of `CURRENT_POINT`/`AS_OF_POINTS`; `analyses/whatif.py` adapter
- [ ] 2.2 `PREDICTIONS`, `SCORECARD_WEEKS`, `SCORECARD_SEASONS`, `SCORECARD_CALIBRATION`; engine columns of `WEEK_GAMES` and `MATCHUP_NEXT`; `UPSETS` and `MATCHUP_GAMES` from `GAME_WIN_PROB` (since 1978)

### Group B-2
- [ ] 2.3 Slice B on AWS (gate: Phase 4 2.3), hand-off column check, timings, Q8 look at the all-time top 20, docs (user guide, architecture, roadmap, `CLAUDE.md`) → Phase 6's 9.8 gate, Phase 7 contract

## Heavy plan tasks (commit c1be7e1), status under the light plan

### Group 1 (probe, scaffolding, pure code)
- [-] 1.1 Probe S8 (a)–(e). Superseded: (a) the conditional rooting guide is dropped ([50]); (b) views read with the owner's rights is confirmed by Phase 6's probe S9 (a), and the alias question is avoided by keeping `P_GAME_ID` unrenamed ([54]); (c) the schema grant over redeployed views is asserted by the roles test of new 1.1; (d) and (e) concern generated views, which no longer exist ([51]).
- [-] 1.2 `Namespace` names and placeholders. Absorbed into new 1.1 (`api_schema`, `api_live_schema`, `app_role`, `mcp_role`; no `pred_schema`).
- [-] 1.3 `analyses/whatif.py` builder with the run guard and `rooting_guide_sql`. Replaced by a thin adapter over Phase 4's `engine/whatif.py` in new 2.1; no run guard (layout A), no conditional guide ([50], [51]).
- [-] 1.4 Host oracles. Superseded: the tests compare each view with its `CFB_MODEL`/`CFB_SIM` source ([55]).
- [-] 1.5 History variant, FCS-only game, apostrophe check. Superseded: Phase 3's league has the historic meetings; predictions are Phase 4's; nothing is generated, so P5-M3 is moot ([55]).
- [-] 1.6 `ANALYSIS_SETTINGS` seed and refresh steps 200/400. Superseded: the steps are Phase 4's 200 `PREDICT` and 300 `SIMULATE`; the settings are a view over `SIM_PARAMS` in new 2.1.
- [-] 1.7 `sqltext.py` and `whatif_ddl.py`. Superseded: no SQL is generated with data in it ([51]).

### Group 2 (deploy, roles)
- [-] 2.1 `sqlobjects.py` per-file allowlists and the keep rule for generated views. Reduced into new 1.1 (Phase 4's generalized text, [56]) and new 1.4 (the `sql/api_live/` allowlist); no generated views.
- [-] 2.2 `analyses/deploy.py`, `make analyses`, stub scripts. Absorbed into new 1.1 as `make api` (views only: no data tables, scripts or generated views).
- [-] 2.3 Data tables `PREDICTION`, `PUBLICATION`, `PUBLISHED_ODDS`. Superseded: Phase 4 owns `PREDICTION`; there are no publications ([42]).
- [-] 2.4 Roles `CFB_APP`/`CFB_MCP`. Absorbed into new 1.1 (app role) and 1.4 (chat role on the live schema).

### Group 3 (analyses in `CFB_PRED`)
- [-] 3.1 Predictions (`REFRESH_PREDICT`, `PREDICT`, `analib.lua`). Superseded: Phase 4's task 2.1.
- [-] 3.2 Scorecard views. Superseded: Phase 4's `PREDICTION_SCORECARD`/`PREDICTION_CALIBRATION`, served by new 2.2.
- [-] 3.3 `UPSET` view and the Q8 check. Absorbed into new 1.3 (stored lines) and 2.2 (`GAME_WIN_PROB`, since 1978); Q8 is a look at the top 20 in new 2.3; no `RANK_MAGNITUDE` ([48]).
- [-] 3.4 `MATCHUP_SERIES`. Absorbed into new 1.3.
- [-] 3.5 Publication, `WHATIF_PUBLICATION`, generated views, `ODDS_MOVEMENT`, `ROOTING_GUIDE`. Superseded: Phase 4's `POINT_RUN`, `CURRENT_RUN`, `ODDS_MOVEMENT` and `ROOTING_GUIDE`, served by new 2.1 ([42], [51], [52]).
- [-] 3.6 `MATCHUP_NEXT`. Absorbed into new 1.3 (schedule) and 2.2 (prediction, stakes) ([49]).
- [-] 3.7 `FORCED_RUN_ODDS`. Superseded: Phase 4 has no forced re-sims.

### Group 4 (serving schemas)
- [-] 4.1 The 23 file views of `CFB_API`. Absorbed into new 1.1–1.4 and 2.1–2.2 (33 views, names kept where Phases 6 and 7 use them).
- [-] 4.2 `GAME_PREVIEW`, `QUOTA`, mock preview bodies. Kept as new 1.4 (`P_GAME_ID` unrenamed, `REQ_ROWS` passed through, no live calls) ([54]).
- [-] 4.3 What-if and guide integration against oracles. Reduced into new 2.1: one equality test against `engine/whatif.py`.
- [-] 4.4 `make api-docs`. Kept in new 1.5 (and refreshed in 2.3).
- [-] 4.5 `make predict`, `make publish`, `make what-if`. Superseded: Phase 4's `make predict` and `make what-if`; no publication.
- [-] 4.6 Roles over the full contract. Absorbed into new 1.1 and 1.4.

### Group 5 (performance)
- [-] 5.1 `make analyses-perf` with a query catalog and the S4 protocol. Superseded: page queries are timed once in new 1.5 and 2.3, recorded, not gated; no live view is timed.

### Group 6 (documentation)
- [-] 6.1–6.4 User guide, architecture, roadmap, `CLAUDE.md`/mission. Merged into new 1.5 (user guide, `CLAUDE.md`) and 2.3 (append, architecture, roadmap).

### Group 7 (live verification)
- [-] 7.1–7.5 Gates, AWS measurements, 3 ledgered preview calls, local replay, zero-call proof. Reduced to new 1.5 and 2.3 (deploy, timings, `make quota` unchanged). The preview's live latency and the stored-series check against its `RAW_JSON` move to Phase 6's live verification under its own cap ([54]).

## Cross-plan items

- [ ] Phase 6: relay § Cross-plan hand-offs (9.7 and 9.8 view by view, the contract changes, the harness names) when 1.5 and 2.3 are done.
- [ ] Phase 7: relay the `CFB_MCP` contract, the adapter, the `WHATIF_OUTCOMES` shape and RQ07's headline rule when 2.3 is done.
- [x] Phase 4: P5-M3 and P5-m13 are moot (already noted in Phase 4's `review.md`); no new request.
