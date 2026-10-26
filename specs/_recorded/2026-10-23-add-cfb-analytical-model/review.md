# Review: add-cfb-analytical-model

Reviewer: independent plan review, 2026-10-22. Read: `plan.md`, `decision-log.md`, `RESUME.md`, all 10 `model/*/spec.md`, `specs/mission.md`, `docs/roadmap.md` (Phases 3–6 and the demo flow), `docs/cfbd-data-availability.md`, `docs/cfbd-api-reference.md`, `vs/catalog/catalog.json` (extracted with python), the Phase 2 plan, its `loader/load-plan` spec and its demo entries, the foundation decision log [1]–[25], `docs/spikes/s3-python-engine-throughput.md` and `s4-sim-storage.md`, and `tests/it/harness.py`. Skills invoked: `exasol:exasol-database` and `exasol:exasol-udfs`. No database session, no CFBD call. `speq plan validate add-cfb-analytical-model` passes with 5 style warnings (scenarios with 4–5 AND steps).

## Verdict: approve with changes

The architecture is right: views only over the mirrors, seeded reference tables, the event-time vs knowledge-time split, as-of views bounded to one season, a generic atomic deployer, and checks rather than rebuilds in the refresh step. The plan is unusually well evidenced.

**One blocker must be fixed in the specs before group A starts.** For historic seasons, "the schedule is always visible" leaks results through the participants of games whose matchups depend on earlier results: conference championship games, bowls and CFP rounds. Phase 4's 2024/2025 CFP backtest would get the real CCG pairings and the real playoff bracket as its "remaining schedule". The invariance test cannot catch this, because it never mutates participants.

Six major findings also need changes before implementation. Three are semantics:
- The as-of point has two definitions, "Sunday after" and "before next kickoff".
- The "games decide classification" rule contradicts the plan's own statement that CFBD stamps today's codes.
- The championship-game heuristic is fragile, and the CFP auto-bids depend on it.

Two are about test and plan mechanics:
- The base league contains a duplicate game and probably lacks the power conferences. Either one makes an ERROR check fail, so the happy-path refresh tests cannot pass.
- The Group C dependencies are wrong.

One is performance: the materialization fallback contradicts decision [2], and it is only decided at the end (M4).

None of these needs a redesign. Each is a spec rule, a seed or a task reorder.

| Severity | Count |
|---|---|
| Blocker | 1 |
| Major | 6 |
| Minor | 19 |

---

## 1. Leak-freedom of the as-of semantics

### B1 (blocker): result-dependent schedule rows leak future results

- **Evidence.**
  - `model/as-of/spec.md:28` makes "schedule: teams, kickoff, site, venue" visible "always, as last fetched".
  - `GAME_AS_OF` therefore returns every game of the season with its teams, marked `NEXT` or `REMAINING` (as-of spec line 37; plan lines 181–191).
  - `TEAM_SEASON_AS_OF` returns "games remaining and the next game" (as-of spec line 41).
  - For a historic season, CFBD's stored schedule contains:
    - the conference championship games, whose participants are the division or standings winners;
    - every bowl, whose matchups depend on bowl eligibility and selection;
    - every CFP round, where a 2024 quarterfinal names the first-round winner and a semifinal names the quarterfinal winner.

  At the point 2024 week 9:
  - `GAME_AS_OF` lists "Ashford vs Carrow, week 15" as `REMAINING`, which reveals both SEC finalists;
  - it lists the CFP quarterfinal "Ashford (1) vs Kingsley (8)", which reveals seeds and the playoff field;
  - `TEAM_SEASON_AS_OF.GAMES_REMAINING` reveals who reached a bowl.

  The same applies at point 15/16, where every postseason game is `NEXT` (postseason week 101), including the later CFP rounds and their participants. The pregame Elo of those `NEXT` games is also visible (as-of spec line 22). A quarterfinal's pregame Elo encodes the first-round result.
- **Why it matters.**
  - Phase 4's backtest ("backtest of 2024/2025 CFP fields", roadmap line 74) and the example `as_of_engine_inputs.sql` read the remaining schedule from these views.
  - The S3 engine simulates the CCG and the bracket itself (`s3-python-engine-throughput.md:103,121,126`). Fed the stored CCG and CFP rows, it would double-count those games or validate against the answer.
  - The invariance scenario (as-of spec lines 117–128) mutates scores, flags, Elo, lines, polls and SP+, but never participants. So it passes while the leak exists.
- **Fix.**
  1. Add a visibility rule for **result-dependent games**: `IS_CONF_CHAMPIONSHIP` games and all games with `SEASON_TYPE = 'postseason'`.
     - Such a game is visible in `GAME_AS_OF`, `TEAM_SEASON_AS_OF` (remaining and next game) and the examples only once P ≥ the last `WEEK_SEQ` before it. For the CCG, that is the previous conference week. For bowls and the first CFP round, it is the last regular week.
     - CFP rounds after the first are visible only at P ≥ 101. Better, hide them always, because CFBD has one postseason week (Q3), and say so in the comment.
     - Before that, these games are not rows of `GAME_AS_OF`. Alternatively, they are rows with NULL teams and `GAME_STATUS_AS_OF = 'TBD'`.
  2. Expose `IS_CONF_CHAMPIONSHIP`, `POSTSEASON_KIND` and `CFP_ROUND` in `GAME_AS_OF`, so that Phase 4 can drop what the engine simulates.
  3. Extend `league.mutate_after(point)` to swap the participants of the 2024 CCG and the CFP quarterfinal, and to add an extra bowl game. Add these to the invariance scenario's GIVEN list.
  4. Add a scenario "Result-dependent games are hidden until their participants are known" (2024 week 9: neither the CCG nor the quarterfinal appears; week 14: the CCG appears; week 15: the bowls and the first round appear).

### M1 (major): the as-of point has two information horizons

- **Evidence.**
  - Point = "after that week's games, before the next week's first kickoff" (as-of spec line 7; decision [4]).
  - The knowledge-time rule uses `REQ_AT < CUTOFF_AT` (next kickoff), so SP+ and lines fetched on a Wednesday are visible.
  - The poll rule uses "Sunday after the week": the committee lead is 0 "because its rankings are released on Tuesdays, after the as-of point 'Sunday after the week' has passed" (`ratings-rankings/spec.md:27`, decision [8]).
  - Q2 (decision-log line 226) assumes committee rankings are labeled by the upcoming week. With lead 0, the ranking released Tuesday 2024-11-05 (label 11) becomes visible only at point 11, a week after it existed.
- **Why it matters.**
  - The live demo refreshes after the Tuesday release. The current point stays at week 10 until a week-11 game completes, so the app and the chat show last week's committee ranking although this week's is stored.
  - Frozen weekly predictions (made before kickoff) and the "Sunday after week N" replay need different horizons, and the plan mixes them per fact.
- **Fix.**
  - Pick one definition. The `CUTOFF_AT` one is the one the plan enforces elsewhere, and it is the one Phase 5's "frozen before kickoff" needs.
  - Then make poll visibility "label rule OR (`REQ_AT` < `CUTOFF_AT`)". A slice fetched before the cutoff can only contain polls released before it, so this is leak-free by construction.
  - Historic seasons keep the label rule, because `REQ_AT` is always 2026.
  - Rewrite the committee rationale in the spec, and add a scenario: the current-season committee ranking fetched Tuesday is visible at the current point.
  - Extend Q2 to the final committee ranking. If CFBD labels the selection-day ranking postseason week 1, it is invisible at points 15/16 (selection Sunday), and `FINAL_CFP_RANK` needs the same check.

### m1 (minor): point-level columns that depend on later facts

`AS_OF_POINT.IS_CURRENT`, `IS_SEASON_COMPLETE` and `CURRENT_AS_OF` change when later games complete. That is legitimate metadata, not a leak. The 2026-week-5 invariance run flips `IS_CURRENT` if `mutate_after` un-completes weeks 6–9.

**Fix:** state in the as-of spec that the invariant covers the fact columns of the three views, and keep these flags out of them (or exclude them in the `MINUS`).

### m2 (minor): `CURRENT_AS_OF` during a partially played week

"Newest week with a completed game" (decision [19]) jumps to week 10 as soon as the Thursday game of week 10 completes. The remaining week-10 games then sit at `NOT_PLAYED` inside the point, and the next-week horizon moves to week 11.

**Fix:** define the current point as the newest week whose games are all completed or cancelled, or whose calendar `END_DATE` is at or before the newest `REQ_AT` of the season's `GAMES` slice. Add a scenario with one completed Thursday game.

### m3 (minor): prior finality and methodology drift

- **Finality.** `SP_PRIOR_RATING` requires the S−1 slice to be final (as-of spec line 25). Finality needs a load on or after (S)-02-15, and only `BACKFILL` re-fetches finalizable slices (Phase 2 `load-plan/spec.md:37–40`). If no backfill runs between 15 February and the season start, every 2027 prior is NULL, and so is the engine's prior. Document this in the user guide (6.1) and add a WARNING check `SP_PRIOR_NOT_FINAL` for the current season.
- **Methodology drift.** CFBD's historic SP+, pregame WP (CFBD's model is fit on all seasons) and Elo may embed later methodology. That is not a fact leak, but Phase 4's calibration should say so. Note it in the `RATING_SP` and `GAME` comments.

### m4 (minor): postseason granularity

Q3 is correctly an open item. Make it explicit in the as-of spec that at P ≥ 101 the whole postseason is known at once, and that Phase 4/5 cannot replay "after the CFP first round". Both are consequences of the one-postseason-week shape, and the B1 fix depends on them.

---

## 2. Data fit (Phase 2 demo profile, catalog columns, grains, era rules)

**Inputs check (passes).** Every object reads only the demo tables (`loader/load-plan.yaml` entries in the Phase 2 plan, lines 190–199). The catalog columns used exist under the stated names:
- `GAMES.ID`, `HOME_ID`, `HOME_CLASSIFICATION`, `HOME_PREGAME_ELO`, `CONFERENCE_GAME`, `PLAYOFF_COMPETITION`, `PLAYOFF_ROUND`, `PLAYOFF_HOME_SEED`, `PLAYOFF_BOWL_NAME`, `START_DATE`;
- `METRICS_WP_PREGAME.GAME_ID`, `SPREAD`, `HOME_WIN_PROBABILITY`;
- `RATINGS_SP.YEAR_`, `TEAM` (name only, no id);
- `RANKINGS_POLLS_RANKS.SEASON`, `SEASON_TYPE`, `WEEK`, `POLL`, `RANK`, `TEAM_ID`, `POINTS`;
- `CONFERENCES_AFFILIATIONS.START_YEAR`, `END_YEAR`, `CONFERENCE_DIVISION`, `P_YEAR`, `P_MIN_YEAR`, `P_MAX_YEAR`;
- `CALENDAR.FIRST_GAME_START`;
- `VENUES.STATE_`, `GRASS`, `DOME`, `LATITUDE`, `LONGITUDE`;
- `TEAMS.LOCATION_ID` (as `HOME_VENUE_ID`).

The P1 claim holds: `RANKINGS_POLLS_RANKS` has `SEASON_TYPE`. The catalog hash quoted in plan line 38 (`75c2c930…`) is not the current `catalog_hash` (`b6389f65…`), so re-check P1 against the final catalog in 7.1 (minor m5).

### M2 (major): "the games decide the classification" contradicts the plan's own premise

- **Evidence.**
  - Plan line 47: "CFBD stamps today's classification codes onto every season".
  - Decision [9] (decision-log lines 126–127) and `teams-conferences/spec.md:20` take classification from the games first, "because they record the team itself".
  - If the game-level `homeClassification` is today's code stamped retroactively (the premise of line 47 and of Q6's wording), then games are the *worst* source in transition years. Teams that moved from FCS to FBS (App State 2014, Georgia Southern 2014, Liberty 2018, Sam Houston 2023, Sacramento State 2026, and so on) would be `fbs` in every earlier season.
  - The rule then feeds:
    - `INVOLVES_FBS`, the coverage population and every measured share;
    - `FBS_TEAM_WITHOUT_CONFERENCE` (ERROR);
    - the Phase 4 engine's FBS team set.
  - The Fairhaven scenario (`teams-conferences/spec.md:78–84`) encodes the opposite assumption, that games carry the historic code.
- **Fix.**
  - Make Q6 decisive and concrete: in `GAME`, App State 2007 and Liberty 2017 must be `fcs`.
  - Pre-decide both branches:
    - if they are `fcs`, keep games-first;
    - if they are `fbs`, use the affiliation spell's classification first, the games second, and set `CLASSIFICATION_SOURCE` accordingly.
  - Run this check in task 1.1 or at the start of group J, not after all views are built. It decides the 3.5 rule.

### M3 (major): the championship-game heuristic is fragile, and the CFP auto-bids depend on it

- **Evidence.** The rule in `games/spec.md:15–19` and decision [11] has failure modes it does not test:
  1. **False positives.** Conferences without a CCG whose final week holds one isolated conference game after a busy week: Big 12 2011–2016, Pac-10 before 2011, Big Ten before 2011, Big East, the Sun Belt before 2018, the Mountain West before 2013, the WAC.
  2. **False negatives.** A conference game in the week after the CCG makes the CCG week no longer "the last week". Army–Navy in 2024 involved two AAC members (the AAC CCG was in week 15, Army–Navy in week 16), if it is recorded as same-conference. Postponed or makeup games after the CCG, as in 2020, do the same.
  3. **Missing participants.** CCGs that CFBD lists with TBD participants before championship week.

  Q7 verifies counts only from 2014 to 2025. Yet `cfp_field_inputs.sql` and the 12-team auto-bids (`s3…md:126`) need the champion of every FBS conference.
- **Fix.**
  - Add a seeded reference `CONF_CHAMPIONSHIP` (conference name, first season, last season), reviewed like `CONFERENCE_TIER`. Known starts: SEC 1992, WAC 1996–98, Big 12 1996–2010 and 2017–, MAC 1997, ACC 2005, C-USA 2005, Big Ten 2011, Pac-12 2011–2023, MWC 2013, AAC 2015, Sun Belt 2018.
  - Match the schedule rule only inside those spells, and drop the "more than one game in the previous week" clause.
  - Use the `NOTES` text as a corroborating WARNING check (`CONF_CHAMPIONSHIP_NOTES_MISMATCH`).
  - Extend Q7 to 1992–2025.
  - Add league cases for a no-CCG conference with an isolated final-week game, and for an Army–Navy-style game after the CCG.
  - Champions of conferences without a CCG (standings-based) are Phase 4's job. Say so in the `cfp_field_inputs.sql` header.

### m6 (minor): there is no conference-game flag

- **Evidence.** The S3 engine needs `conf_game` per remaining game and rejects "a conference game between teams of different conferences" and "a conference pair meeting more than once" (`s3…md:79,90`). The catalog has `GAMES.CONFERENCE_GAME`, but `games/spec.md` lists no `IS_CONFERENCE_GAME`. The conference records in `TEAM_SEASON` and `TEAM_SEASON_AS_OF` do not say whether they use CFBD's flag or equal conference names.
- **Fix:** expose `IS_CONFERENCE_GAME` (CFBD `conferenceGame`) on `GAME`, `TEAM_GAME` and `GAME_AS_OF`, and define conference records on it. Add a WARNING check `CONFERENCE_GAME_MISMATCH` for flagged games whose teams' `TEAM_SEASON` conferences differ. Realignment years and non-counting games such as 2024 Army–Navy will show up there.

### m7 (minor): the SP+ national-averages row

CFBD's `/ratings/sp` response includes a `nationalAverages` pseudo-team row (verify on AWS). As specified, it raises `SP_TEAM_UNMATCHED` every season. **Fix:** exclude it explicitly in `RATING_SP` (or flag it as `IS_NATIONAL_AVERAGE`), and add it to the league.

### m8 (minor): only `GAME` is deduplicated

- **Evidence.** Phase 2 replaces season slices by `REQ_SLICE`, so today duplicates can only come from overlapping slices. Once `PROMOTE()` lands (deferred, Phase 2 plan line 78), on-demand slices that overlap become stored:
  - a promoted `/games?year=2026&week=3` slice duplicates games, and `DUPLICATE_GAME` (ERROR) then stops every refresh;
  - a promoted `/ratings/sp?team=X` slice duplicates `RATING_SP`'s grain;
  - a promoted week-level `/rankings` slice duplicates `RANKING`'s grain.
- **Fix:** apply "newest `REQ_AT` wins per natural key" in `RATING_SP` (`YEAR_`, `TEAM`), `RANKING` (season, type, week, poll, team) and the `METRICS_WP_PREGAME` join (`GAME_ID`). Make `DUPLICATE_GAME` a WARNING, since the view already resolves it.

### m9 (minor): the committee's `isFinal` is lost

`RANKINGS_POLLS.IS_FINAL` (`isFinal`) is not stored: only `RANKS` is loaded (Phase 2 [27]). `/rankings` also has `latest` and `final` CFP snapshot parameters (`cfbd-api-reference.md:107`), which hints at more than one committee snapshot per season or week.

**Fix:** in Q2, verify that (poll, season, type, week, team) is unique for the committee. If it is not, `RANKING`'s grain breaks. Recommend to the foundation the same parent-key override for `IS_FINAL` as for `SEASON_TYPE`.

### m10 (minor): the coverage threshold will flag every lines season as partial

CFBD omits games without a spread (`cfbd-data-availability.md`, pregame WP note). The population "completed games involving an FBS team" includes FBS–FCS games, which usually have no line, so real shares will sit below 0.95. `PREGAME_WP` would then be `PARTIAL` for every season, and the chat would say so.

**Fix:** use FBS-vs-FBS games as the `PREGAME_WP` population, or record the share and calibrate the threshold in 7.2. Add this as Q13.

### m11 (minor): era nuances

- `TIES_POSSIBLE` is FBS-level only, but Divisions I-AA, II and III adopted overtime in 1981. State this in the column comment, or make it per classification.
- 2024 and 2025 have different CFP seeding rules (byes for the top conference champions in 2024, straight seeding in 2025). Phase 4's backtest of both fields needs that distinction. Either add `CFP_SEEDING` as an era kind, or note it explicitly as Phase 4's job.
- Verify that `PLAYOFF_COMPETITION = 'cfp'` is populated for 2014–2023, not only for 2024 and later. Add it to Q3. Otherwise `POSTSEASON_KIND` for older semifinals and finals falls back to `BOWL`.

---

## 3. Fitness for Phases 4–6

**What is covered (good).**
- Engine inputs: records, conference records, point differentials, tier, Elo as of the point, the SP+ prior and ranks as of the point.
- Upsets: pregame WP from 2013 and pregame Elo before.
- Head-to-head from `TEAM_GAME` at zero calls (decision [18] is a good catch versus D13).
- The scorecard: results in `GAME`.
- The as-of picker: `AS_OF_POINT` with its label.
- The step timeline: `MODEL`.

**Missing or underspecified:**
- `IS_CONFERENCE_GAME` (m6).
- Result-dependent game flags in `GAME_AS_OF` (B1).
- A CCG reference (M3).
- Placeholder or TBD games, meaning NULL team ids in a scheduled CCG or bowl: say whether `TEAM_GAME` drops them and whether a check reports them (minor m12).
- S3 requires fixed-scale `DECIMAL`, not `DOUBLE` (`s3…md:22,87`), but the model passes SP+, spread and WP through as `DOUBLE`. Either expose `DECIMAL` columns for the engine-facing values, or document that Phase 4 casts them (minor m13).

**YAGNI candidates (minor m14; trim or defer, none blocking):**
- `CONFERENCE_SEASON`: no consumer is named. It is a `GROUP BY` over `TEAM_SEASON`.
- `ERA` kinds `CHAMPION` and `OVERTIME`. They are cheap, but nothing reads them except `TIES_POSSIBLE`.
- `TEAM_GAME.TRAVEL_KM` and the `ST_WITHIN` scenario: no v1 page or engine input uses travel (the engine has a constant `hfa`). Keep `GEO_POINT` because the mission names it, and consider deferring `TRAVEL_KM` and the function to the backlog. At least drop probe 1.1d's 1M-row benchmark.
- `SEASON_COVERAGE` rows for the 14 `ON_DEMAND` datasets × 158 seasons. The `DATASET` row already carries the declared seasons. Per-season rows add little beyond what the chat can derive.

---

## 4. Correctness and feasibility on Exasol (skill-checked claims)

| Claim in the plan | Skill evidence | Result |
|---|---|---|
| No `GEOGRAPHY` type; `GEOMETRY(4326)` | `exasol-database/references/exasol-sql.md` data types: "`GEOMETRY(srid)` | srid: optional (default 0) | WKT/WKB; POINT …"; no `GEOGRAPHY` row | **Confirmed** (by the type list; no explicit negative statement) |
| `ST_DISTANCE` units on 4326 | `exasol-grammar-functions.md` `spatial_1` only lists `ST_DISTANCE` | **Silent.** The plan correctly treats it as unverified (probe 1.1c) and does not rely on it |
| No `COMMENT ON VIEW`, so comments go inline | `exasol-grammar.md` `comment_object_statement` = COLUMN, SCHEMA, FUNCTION, SCRIPT, USER, ROLE, CONNECTION, CONSUMER GROUP; `comment_table_statement_1` = `COMMENT ON [TABLE] table`; `create_view_statement2` = `"(" [column ["COMMENT" "IS" string]] … ")" "AS" subquery`; `create_view_statement3` = `["COMMENT" "IS" string]` | **Confirmed for the literal claim**, but the conclusion overreaches. `COMMENT ON COLUMN` and `COMMENT ON [TABLE] <view>` may well work on views (probe 1.1a). Inline comments remain the better choice for atomicity. Reword decision [13]'s "impossible for views" to "not in the grammar for views; inline is atomic" (m15) |
| `CREATE FUNCTION … IS v DOUBLE; BEGIN …; RETURN …; END; /` | `create_function_statement_3` = `["IS"] (variable data_type ";")+`; `_4` = `"BEGIN" function_statement+ "RETURN" expr ";"` | **Confirmed**, including "at least one variable and one statement". `COMMENT ON FUNCTION` is in `comment_object_statement`: confirmed |
| Transactional DDL | Neither skill states it | **Silent.** Probe 1.1b with a recorded fallback is the right handling |
| Reserved words | `exasol-reserved-keywords.md` (2026.1.0, 468 words). I checked all 328 upper-case identifiers in the plan and specs: the only hits (`NEXT`, `NONE`, `PARTIAL`, `LEVEL`, `RESULT`, `SOURCE`, `STATE`, `VALUE`, …) are SQL keywords, string-literal codes or names the plan deliberately avoids. `RANK`, `POINTS`, `WEEK`, `SEASON`, `SITE` and `SCHOOL` are not reserved | **Confirmed.** Note: the skill says "Never use quoted identifiers in DDL statements" (`exasol-sql.md` line 47), and the plan quotes DDL names (`"GAME_AS_OF"`). That is harmless only while the names are upper case, so the lint must reject non-upper-case quoted DDL identifiers (m15) |
| `CREATE OR REPLACE VIEW` over a missing object fails (1.1f) | `create_view_statement` = `"CREATE" ["OR" "REPLACE"] ["FORCE"] "VIEW"` | The skill shows `FORCE` exists. Consider `CREATE OR REPLACE FORCE VIEW` to remove the file-order fragility, with the read-back proving validity. Not required |
| Lua refresh step: nested `EXECUTE SCRIPT`, `pquery`, `error()`, no `WITH OUTPUT` | `exasol-udfs/references/lua-execute-scripts.md`: nested `query`/`pquery` of `EXECUTE SCRIPT` "possible but … recursion limit of 255"; `pquery` returns `success, result` with `error_message`; script parameters are untyped; `output()` is discarded without `WITH OUTPUT` | **Confirmed** for calling, errors and output. **Silent** on `COMMIT` inside a nested script. That commit ends the caller's open transaction too, so any uncommitted `REFRESH_WEEK` state is committed by the model step. Probe 1.1h covers survival. Also confirm with Phase 2 that its step wrapper commits before each step (m16) |

### M4 (major): the materialization fallback contradicts decision [2]

- **Evidence.** The Requirements say that a view over its target "is materialized as a table rebuilt by `REFRESH_MODEL`" (plan line 387; decision [2] line 64). Decision [2]'s own rationale for views (line 66) is that `BACKFILL` and `make restore` run no refresh steps, so materialized tables go stale. The fallback reintroduces that failure mode, and it is decided last (7.3), after all views exist.
- **Fix.**
  - Define the fallback as "rebuilt by `make model` and `REFRESH_MODEL`, **and** after `BACKFILL` and `make restore`", using a post-hook that Phase 2's scripts call.
  - Better still, run an **early performance smoke** in group C or D on a scaled league: replicate the league to about 250k games and 157 seasons with a generator, at zero calls.
  - Measure the `GAME` stack (dedupe `QUALIFY`, the line join, the championship window), `TEAM_SEASON` and `MODEL_CHECKS` before groups E and F, so that a redesign happens before the as-of views depend on it.

### m17 (minor): performance patterns to steer now

- **`GAME` carries too much.** It holds the dedupe window, the line join and the `IS_CONF_CHAMPIONSHIP` window (per conference and week, plus the previous week), and every other view stacks on it. `MODEL_CHECKS` is presumably a `UNION ALL` of 11 branches that each re-derive `GAME` and `TEAM_SEASON`. Put the championship rule in its own small view joined into `GAME` by id, and filter `MODEL_CHECKS` by season inside each branch so that the season predicate is applied before the windows (extend probe 1.1g to a `UNION ALL` view).
- **Partition windows by season.** Implement `SP_PRIOR_RATING` as a join on `SEASON = S - 1`, not as `LAG` over a team partition, so that a season filter on `TEAM_SEASON`/`TEAM_SEASON_AS_OF` can push down. Do the same for any team-history window: partition by (team, season).
- **No obviously expensive pattern otherwise.** The as-of fan-out is bounded per season (about 17 points × about 4,000 games, all divisions). There are no correlated subqueries in the shown SQL.

---

## 5. Testability

**Coverage of scenarios.** 71 scenarios in the specs map 1:1 to 71 rows of the Verification table. Every row names a file and a test. `speq plan validate` passes with 5 style warnings, which are worth splitting as RESUME item 1 suggests.

### M5 (major): the base league makes the happy-path refresh tests fail

- **Evidence.**
  - The base league's 2026 row has "Carrow vs Dunmore week 3 stored twice" (plan line 308; `games/spec.md:42`), and `DUPLICATE_GAME` is an ERROR check (`refresh-step/spec.md:11`). So `REFRESH_WEEK(2026, 9)` fails at `MODEL`, yet "The refresh runs the model step" expects every step `DONE` (`refresh-step/spec.md:39`).
  - Storing a game twice also needs a second `GAMES` slice key. If that slice is registered in `LOAD_LOG` with a loader `LOADED_BY`, Phase 2's orphan guard (`load-plan/spec.md`, orphan bullet) blocks the `FETCH GAMES` step.
  - `POWER_CONFERENCE_MISSING` (ERROR) fails for any season 1998–2026 in which the league has no team-season in one of the seeded power conferences. The league facts name SEC, Pac-12, Mountain West, Sun Belt and Big Sky, but no ACC, Big Ten or Big 12. That breaks both the 2026 refresh test and "The checks run for any point outside a refresh" (2024, which must succeed).
- **Fix.**
  - Move the duplicate to the `with_duplicate_game` variant, registered as a non-plan slice that the orphan guard ignores (or with `LOADED_BY` outside the guarded prefixes).
  - Give the base league one member team in every seeded power conference for every season it stores from 1998 on (1998, 2000, 2013, 2014, 2023–2026), and add a `test_league.py` unit assertion that no ERROR check fails on the base league.
  - Make Ashford's 2024 record consistent with its CCG and CFP games. "10–2" plus a CCG win plus a quarterfinal does not add up unless the comment and the scenario say regular season only.

### M6 (major): Group C runs dependent tasks in parallel

- **Evidence.** Group C = 2.5, 3.1, 3.2 and 3.3 (plan line 598). But:
  - `TEAM` reads `GAME` (plan line 147), so 3.2 needs 3.1;
  - `RATING_SP` reads `TEAM` (line 149), so 3.3 needs 3.2;
  - `GAME.HAS_VENUE_LOCATION` (`games/spec.md:25`) needs venue coordinates, but `GAME`'s Reads column (line 143) lists no `VENUES`. Either 3.1 reads `CFB_CORE.VENUES` directly (fine) or it needs 3.2.

  Parallel implementers would block or write against missing views. File order also matters for deploy (probe 1.1f).
- **Fix.** Order the work as C = {2.5, 3.1, `130_venue`}, then {3.2 rest}, then 3.3. Alternatively, split 3.2 into "venue + function" (C) and "team + conference" (after 3.1). Add `VENUES` to `GAME`'s Reads, and update the sequential dependency list (lines 607–614).

### m18 (minor): the invariance test is narrower than the invariant

The scenario mutates a hand-picked list. Add these to `mutate_after`:
- the participants of result-dependent games (B1);
- adding a new bowl game;
- `CALENDAR` rows after the point;
- the S−1 SP+ `IS_FINAL` flag (it must not matter for the current-season points);
- attendance and excitement index of later games.

Keep the positive control.

### m19 (minor): test-harness feasibility

Feasible as planned:
- `vs.cfbd_vs.flatten.flatten(table, body)` exists;
- the harness has `register_slice`, `write_recordings` and `reset_state`.

Points to settle:
- **`test_model_over_generated_shapes`.** It stages every demo table through the virtual schema from `fake.py`. The 1869–2026 `GAMES` range costs one fake slice per season, which is slow. Bound it to a few seasons.
- **`model_readonly` marker.** Read-only tests skip truncation, so state that mutating tests (invariance, renamed conference, duplicate) run in their own module or re-seed. That keeps test order from leaking state.
- **Concurrent deploys.** `make model` replaces the reference tables in one transaction. A concurrent `REFRESH_WEEK` or app read causes a transaction conflict (Exasol rolls back one side). Have the model deploy take Phase 2's run lock, and test "deploy refused while a refresh holds the lock".

---

## What is good

- **Event-time vs knowledge-time is the right abstraction.** Gating overwritten values on `REQ_AT < CUTOFF_AT` makes the current season leak-free without snapshot tables, and it degrades safely (to NULL) for replays of earlier current-season weeks. Decision [7] records the trade-off and the upgrade path.
- **Views only, with a reasoned exception rule.** No rebuild order to get wrong after backfill, refresh or restore. Once M4 is fixed, the rule is sound.
- **Slice-scoped membership expansion.** "Each slice speaks only for its request" is a subtle, correct handling of the static 1869–2025 range slice next to per-season slices, and it has a dedicated scenario.
- **The coverage model distinguishes "not stored, fetch on demand via `CFBD_VS.<table>`" from "does not exist".** That is exactly what the Phase 7 chat needs, and the model still never reads the VS (lint plus dependency walk plus mock hits plus `FETCH_LOG`).
- **The `MODEL` step is checks, not a rebuild.** The `CONFERENCE_TIER` ERROR guard turns a silent CFBD rename into a stopped refresh before the sims choose a wrong playoff field.
- **Exasol diligence.** Every claim is tabulated with its evidence, and the unverified ones (transactional DDL, `ST_DISTANCE` units, comment limits, nested `COMMIT`) go through probe 1.1 with pre-decided fallbacks. Reserved words are checked mechanically.
- **Executable done-when.** The four example analyses double as acceptance tests and as starting points for analysis builders.
- **Zero-call discipline throughout.** The synthetic league goes through the real flattener, live verification is read-only, and `/info` is checked before and after.

---

## Review resolution

Resolved 2026-10-22 by the planner, per the orchestrator's decisions for the away owner (logged in `decision-log.md`, interview entry "review resolution"). No DB session, no CFBD call. `speq plan validate add-cfb-analytical-model` passes with no warnings. The engine spikes' results (D19, D20, `specs/architecture.md` § Engine contract) were folded in at the same time; see the last table.

### Blocker and major findings

| Finding | Resolution | Where |
|---|---|---|
| B1 result-dependent games leak | Accepted. `GAME.PARTICIPANTS_RULE` (`FIXED`, `CONF_CHAMPIONSHIP`, `SELECTION`, `PREVIOUS_ROUND`) decides from which point a game is a row of `GAME_AS_OF`: championship games from the point before their week, bowls with an FBS team and the first CFP round (`CFP_ROUND_SEQ` 1) from the last regular week, later CFP rounds and lower-division postseason games from their own week. Hidden games are not rows, so teams, existence and pregame Elo cannot leak; the quarterfinal's Elo never shows before P = 101. `GAME_AS_OF` exposes `IS_CONF_CHAMPIONSHIP`, `POSTSEASON_KIND`, `CFP_ROUND` and `PARTICIPANTS_RULE`. `TEAM_SEASON_AS_OF` counts remaining and next games over `GAME_AS_OF`; game-derived conference and classification use `FIXED` games only. `mutate_after` swaps the participants of the 2024 SEC championship game and the quarterfinal and adds a bowl. New scenarios: "Games whose participants depend on results are hidden until they are known", "Remaining games count only games with known participants", "Postseason games without an FBS team wait for their own week". Deviation: no `TBD` rows (an added bowl would still change the row set) | `model/as-of`, `model/games`, plan Key Interfaces (SQL example, As-of semantics), decision [21] |
| M1 two horizons | Accepted. One horizon: everything known before the next week's first kickoff (`CUTOFF_AT`). Polls are visible by the label rule or when `REQ_AT < CUTOFF_AT`. A committee ranking labeled postseason is visible at the last regular week (`POLL.FINAL_BEFORE_POSTSEASON`). Q2 now covers the first ranking's label, the selection ranking and uniqueness. New scenarios: "A ranking fetched before the next kickoff is visible at that point", "A committee ranking labeled postseason is visible after the regular season" | `model/as-of`, `model/ratings-rankings`, decisions [4], [8], Q2 |
| M2 classification source | Accepted. Q6 is concrete (Appalachian State 2007 and Liberty 2017 must be `fcs` in `GAME`). Both branches are decided by one code path: the reference table `CLASSIFICATION_TRANSITION` comes first in `TEAM_SEASON` and `GAME`, then the `FIXED` games, then the membership. Q6 only decides whether the seed is empty (historic codes) or lists every differing team and season range since 1978 (today's codes), so it can run whenever stored data exists and never forces a view redesign. New scenario: "A seeded transition overrides the recorded classification". Deviation from the review's branch B (affiliation spell first): a spell's code may be the conference's code of today (the Western Athletic is FCS today), so the orchestrator chose the seed | `model/teams-conferences`, `model/games`, decision [9], Q6 |
| M3 championship heuristic | Accepted. Reference table `CONFERENCE_CHAMPIONSHIP` (12 rows, the review's list) and the view `CONFERENCE_CHAMPIONSHIP_GAME`; the rule applies only inside a conference's seeded seasons, the "busy previous week" clause is gone, and the game must be flagged as a conference game, which skips Army–Navy-type games. `NOTES` is a warning cross-check (`CONF_CHAMPIONSHIP_NOTES_MISMATCH`), and `CONF_CHAMPIONSHIP_MISSING` warns about a finished season without a detected game. Q7 covers 1992–2025 with a pre-decided fallback rule. League cases: a no-championship conference with an isolated final-week game (Pac-12 2024), a same-conference game after the championship (American Athletic 2023), a missed championship game (Mountain West 2014). `cfp_field_inputs.sql` says standings-based champions are Phase 4's job | `model/games`, `model/refresh-step`, `model/documentation`, decision [11], Q7 |
| M4 materialization fallback | Accepted. A materialized table would be rebuilt by every `make model`, by `REFRESH_MODEL`, and by a post-hook of `make backfill` and `make restore`, with a `MATERIALIZED_STALE` ERROR check for direct `EXECUTE SCRIPT` backfills. A scaled synthetic league (task 1.6, about 250,000 games) feeds a performance smoke (3.7) on the `GAME` stack and an as-of join prototype before `TEAM_SEASON` and the as-of views (groups F and G), and again on the full model (4.3). Restructuring comes before materializing | plan Key Interfaces (Materialization fallback), Requirements, tasks 1.6, 3.7, 4.3, decision [2] |
| M5 base league breaks happy paths | Accepted. The duplicate game moved to the `with_duplicate_rows` variant, used only by view-level tests that run no loader step. The range slice gives a member to every power conference of every stored season from 1998 (Fillmore, Granton, Hollis, Ivel, Carrow, Dunmore, Ashford, Brookvale). Ashford 2024: 10–2 in the regular season, then the SEC title and a quarterfinal loss, 11–3 overall; the records scenario states that conference records leave out the championship game. A unit guard mirrors the ERROR checks, and `test_base_league_passes_error_checks` runs them over every season | plan Key Interfaces (Synthetic league), tasks 1.4, 3.11, `model/teams-conferences`, decision [17] |
| M6 Group C dependencies | Accepted. C = {2.5, 3.1 `GAME` stack, 3.2 venue + function}; D = {3.3 `TEAM` + `CONFERENCE`, 3.4 `TEAM_GAME`, 3.5 `RANKING`}, which read `GAME` or `SEASON_WEEK`; E = {3.6 `RATING_SP` (reads `TEAM`), 3.7 performance smoke}; F = {3.8 `TEAM_SEASON`}; G = {3.9 as-of, 3.10 coverage, 3.11 checks}. `GAME` reads `CFB_CORE.VENUES` directly for `HAS_VENUE_LOCATION`. The dependency list is rewritten | plan Implementation Tasks, Parallelization |

### Minor findings

| Finding | Resolution | Where |
|---|---|---|
| m1 point metadata | Fixed. `IS_CURRENT` and `IS_SEASON_COMPLETE` live only in `AS_OF_POINT` and `CURRENT_AS_OF`; the invariant covers the three as-of views | `model/as-of` |
| m2 Thursday edge case | Fixed. `SEASON_WEEK.IS_DONE` (all games completed, or the games fetched at least a day after the week ended); `CURRENT_AS_OF` is the newest done week. Scenarios "A week is done when…" and "A partly played week does not move the current point" (`with_thursday_result`) | `model/seasons-eras`, `model/as-of`, decision [19] |
| m3 prior finality, methodology drift | Fixed differently. The prior is the previous season's SP+ as last fetched, final or not (it is in the past at every point, so it cannot leak), with `SP_PRIOR_IS_FINAL` and the warning `SP_PRIOR_NOT_FINAL`; requiring finality would null every prior when no backfill ran after 15 February. Methodology drift is in the `GAME` and `RATING_SP` comments and the user guide | `model/ratings-rankings`, task 6.1, decision [24] |
| m4 postseason granularity | Fixed. The as-of spec states that at P ≥ 101 the whole postseason is known at once and that "after the CFP first round" cannot be replayed | `model/as-of`, decision [5] |
| m5 catalog hash | Fixed. Plan cites `vs/catalog/catalog.json` `catalog_hash` `b6389f65…`; P1 (including `POLLS_NO`) and the column types are re-checked against the final hash in 7.1 | plan Context, Dependencies, task 7.1 |
| m6 conference-game flag | Fixed. `IS_CONFERENCE_GAME` on `GAME`, `TEAM_GAME` and `GAME_AS_OF`; conference records and `CONF_POINT_DIFF` count flagged games without championship games; warning `CONFERENCE_GAME_MISMATCH` | `model/games`, `model/teams-conferences`, `model/as-of`, `model/refresh-step`, decision [25] |
| m7 `nationalAverages` | Fixed. Left out of `RATING_SP`; a league row and a scenario step; Q12 verifies the name | `model/ratings-rankings`, Q12 |
| m8 only `GAME` deduplicated | Fixed. Every slice-scoped source is deduplicated by its natural key, newest fetch first (rankings tie-break on `POLLS_NO`); `DUPLICATE_SOURCE_ROW` (WARNING) replaces the ERROR `DUPLICATE_GAME`. Scenarios for games with their line and for SP+ with rankings | `model/games`, `model/ratings-rankings`, `model/refresh-step`, decision [23] |
| m9 committee `isFinal` | Fixed by derivation: `IS_FINAL_POLL` for the committee's last ranking of a season whose regular weeks are all done; Q2(c) checks uniqueness, with `POLLS_NO` and a foundation recommendation as the fallback. `FINAL_CFP_RANK` uses it, so a season in progress has none | `model/ratings-rankings`, decision [26], Q2 |
| m10 lines coverage | Fixed. `PREGAME_WP` is measured on completed FBS-vs-FBS games; `DATASET.COMPLETE_SHARE` per dataset; Q13 calibrates it | `model/coverage`, decision [27], Q13 |
| m11 era nuances | Fixed: `TIES_POSSIBLE` comment (lower divisions: overtime from 1981); the 2024 vs 2025 seeding rules are named as Phase 4's selection configuration (no era kind); Q3 checks `playoff.competition` and `round` for 2014–2025 with a fallback for the CFP final | `model/seasons-eras`, Non-Goals, Q3 |
| m12 placeholder games | Fixed. `GAME` keeps them with `HAS_TEAMS` FALSE and `PARTICIPANTS_RULE` `FIXED`; `TEAM_GAME` has no row for an unnamed side; no check (placeholders are normal). Scenario "A game without named teams is a placeholder" | `model/games`, decision [30] |
| m13 `DECIMAL` for engine values | Fixed. Spreads `DECIMAL(6,2)`, win probabilities `DECIMAL(5,4)`, SP+ ratings `DECIMAL(6,2)`; probe 1.1i checks the cast's rounding. Scenario "Engine inputs are fixed-scale decimals" | `model/games`, `model/ratings-rankings`, `model/as-of`, decision [22] |
| m14 YAGNI | `CONFERENCE_SEASON` dropped (no Phase 4/5 consumer of standings, decision [28]); `TRAVEL_KM` and its scenario dropped, probe 1.1d's 1M-row benchmark dropped (decision [12]); per-season rows of never-stored datasets dropped (decision [27]); the `CONF_CHAMPIONSHIP` era kind replaced by the seed. Not taken: the `CHAMPION` and `OVERTIME` era kinds stay (cheap; `OVERTIME` drives `TIES_POSSIBLE`), and `GREAT_CIRCLE_KM` stays (the only correct km distance for a `GEOMETRY(4326)` point) | `model/seasons-eras`, `model/venues`, `model/coverage`, `model/teams-conferences` |
| m15 comment wording, `FORCE`, quoted DDL names | Fixed. Decision [13] reworded ("not in the grammar for views; inline is atomic"). Views use `CREATE OR REPLACE FORCE VIEW` (grammar confirmed by the skill; semantics probe 1.1f; fallback plain views plus an order lint), and the deploy compiles every view. DDL names are unquoted upper case per the skill rule; the lint rejects quoted, lower-case or reserved names. Scenario "A view may reference a view of a later file"; the lint scenario gained a quoted lower-case name | `model/deploy`, `model/documentation`, decisions [13], [14] |
| m16 nested `COMMIT` | Confirmed from Phase 2's spec: the refresh commits its step row when a step starts (`loader/refresh-week`, logging), so the step's `COMMIT` ends no open refresh state. Probe 1.1h stays | `model/refresh-step`, plan Key Interfaces, decision [15] |
| m17 performance patterns | Fixed. The championship rule is its own small view joined by id; each check filters by season before it aggregates, and probe 1.1g covers a `UNION ALL` view with a fallback; the prior is a join on `SEASON = S - 1`; team-history windows partition by team and season | `model/games`, `model/refresh-step`, plan Key Interfaces, task 1.1 |
| m18 invariance test too narrow | Fixed. `mutate_after` also changes the participants of the championship game and the quarterfinal, adds a bowl, changes calendar rows after the next week, the previous season's SP+ finality, attendance, excitement and postgame win probability; the positive control stays | `model/as-of`, plan Key Interfaces (Synthetic league) |
| m19 harness feasibility | Fixed. `test_model_over_generated_shapes` stages four seasons; read-only modules seed once, the variants module and the refresh-step module re-seed before every test; the model deploy joins Phase 2's run lock, with the scenario "A deploy is refused while a loader run holds the lock" | `model/deploy`, task 2.4, decision [29] |
| 5 validator style warnings | Fixed: the scenarios were split; `speq plan validate` reports no warnings | all specs |

### Engine contract fit (new input, not a review finding)

| Check | Result |
|---|---|
| Gaps closed | `IS_NEUTRAL_SITE` on `GAME` and `GAME_AS_OF` (it was only `TEAM_GAME.SITE`), `IS_CONFERENCE_GAME`, `MARGIN` in `GAME_AS_OF` for the final conference games, `CONF_POINT_DIFF`, conference records without championship games, fixed-scale `DECIMAL` |
| Extras removed | `TEAM_GAME.TRAVEL_KM` (the engine has a constant `hfa`), `CONFERENCE_SEASON` (the engine ranks standings per sim) |
| Left to Phase 4 on purpose | indices, μ, σ, totals, ratings, strength of schedule, and `capped_pd` (its cap is the engine parameter `margin_cap`; `as_of_engine_inputs.sql` shows it with the default 24) |
| Mapping | plan.md, Key Interfaces, "Engine contract mapping"; decision [31] |
