# Review: add-cfb-analyses

Reviewer: independent plan review, 2026-10-22. Read-only: no DB session, no CFBD calls, no file edited except this one.

Files read:
- this plan's `plan.md`, `decision-log.md`, and all 12 delta specs (`analyses/*`, `api/*`);
- `docs/roadmap.md` (Phases 5–8, D13, D20, the Demo flow) and `specs/architecture.md` (layers, § Security model, § Design questions);
- Phase 4 (`add-cfb-score-engine`): `plan.md` (architecture, DDL, run SQL) and the specs `sim-runs`, `refresh-step` and `win-probability`;
- Phase 3 (`add-cfb-analytical-model`): `model/as-of`, `model/games`, and decision [9] on classification;
- Phase 2 (`add-cfb-persistence-refresh`): `loader/snapshot-restore`, `loader/refresh-week`, plus `loader/deploy.py`, `loader/plan.py` and `loader/load-plan.yaml` in the worktree `../exasol-cfb-demo-p2`;
- S4: `docs/spikes/s4-sim-storage.md`, `spikes/s4/results/latency*.csv`, and `spikes/s4/queries.py` (the Q3 shape);
- S1: `docs/cfbd-api-measurements.md` and `docs/cfbd-data-availability.md`.

Skills loaded first: `exasol:exasol-database`, `exasol:exasol-udfs` and `exasol:exasol-virtual-schema-adapter-development`.

`speq plan validate add-cfb-analyses` → **passed (12 delta specs)**. The specs hold 69 scenarios, and all 69 are mapped in Scenario Coverage.

## Verdict: **Approve with changes**

The plan is sound in its core design:
- explicit publication, with frozen odds behind the arrows;
- generated stable-name what-if views read by a pure SQL builder;
- a split rooting guide, gated by a probe;
- the head-to-head series from stored games, with the preview as the live beat.

The rooting-guide estimate holds up against S4's raw numbers (see §2). There are **no blockers**.

Seven findings are **major**. Most are cheap spec edits, but four of them touch the honesty or the stability of what demo beat 4 shows, so settle them before the tasks named:

| # | Finding | Severity | Fix before |
|---|---|---|---|
| M1 | Restore keeps later publications, so after "restore Sunday after week N" the what-if run, stakes and points stay on later weeks | major | 3.5 |
| M2 | File views read the generated `WHATIF_RUN`, which a fresh deploy creates only after its commit; "the what-if run" is defined in four places | major | 2.2 / 3.5 |
| M3 | Generated column comments embed school names unescaped (`Hawai'i`), so a publication fails every week Hawai'i has a remaining game | major | 3.5 |
| M4 | The scorecard headline mixes predictions frozen in advance with reconstructions; in a rehearsal every graded prediction is a reconstruction | major | 3.2 |
| M5 | "All-time" upset rank: the population before 1978 is anachronistic, early Elo saturates `DECIMAL(5,4)`, and Q8 is checked only in group K | major | 3.3 |
| M6 | `make analyses-perf` "every budget row, S4 protocol" on AWS would fire about 28 unledgered `GAME_PREVIEW` calls | major | 5.1 |
| M7 | `CFB_API` has page-shaped views only, but beat 5 has the chat "answer a question outside the pages" | major | 4.1 (or an explicit hand-off) |
| m1–m15 | see §7 | minor | — |

---

## 1. Correctness and leak-freedom

### M4 (major): the scorecard headline does not separate predictions made in advance from reconstructions

**Evidence**
- `analyses/scorecard`: `SCORECARD_WEEK` grades every row of `PREDICTION`. `FROZEN_BEFORE_KICKOFF` appears only as a count (`FROZEN_BEFORE_KICKOFF_GAMES`). `RECORD_LABEL` (`41–9`) and `BRIER` mix both kinds.
- `FROZEN_AT` uses the loader clock; `NOW_OVERRIDE` exists only in the test namespace (`analyses/predictions`).
- On AWS, Phase 5 is deployed around week 9, so 2026 weeks 1–9 are reconstructions (`FROZEN_BEFORE_KICKOFF` FALSE).
- In the Phase 8 rehearsal ("restore Sunday after week N" on a later real date), the week-N+1 games that the live refresh grades are frozen during rehearsal prep. That is after their real kickoff, so **every** graded prediction in the beat-4 "41–9" is a reconstruction.

Reconstructions are leak-free by construction, because the as-of views and the prior season's SP+ are used. They are still not "predictions made in advance", which is the architecture's own wording (§ CFB_PRED).

**Fix**
- Add `FREEZE_KIND` (`IN_ADVANCE` / `RECONSTRUCTED`) as a dimension of `SCORECARD_WEEK`, `SCORECARD_SEASON` and `SCORECARD_CALIBRATION`, next to the population. An `ALL` row keeps today's totals.
- Add one scenario: a week with both kinds gives three rows that add up.
- Page contract: the headline uses `IN_ADVANCE` when it has graded games. Otherwise it says "reconstructed at the as-of point (leak-free, made after kickoff)".
- Note in the Phase 8 hand-off that the rehearsal's headline will be the labeled reconstruction, unless predictions were frozen live during the season.

### M5 (major): the "all-time" upset rank needs a defined population and resolution now, not in group K

**Evidence**
- `UPSET` requires `INVOLVES_FBS` (`analyses/upsets`). Phase 3 [9] treats classification before 1978 as retroactive: CFBD may stamp today's code or the code of the time, and Q6 decides which. So for 1890–1977 the population ("involves an FBS team") is anachronistic either way. The Ivies (FCS today) may drop out, or schools that were minor then may count.
- `GAME_WIN_PROB` fits `ELO_PROB_SCALE`/`HFA` on 2014–2025 FBS games (`win-probability`) and applies them to 1890s Elo gaps of 1,000+ points (the plan's own 1898 fixture: 2400 vs 1000 → 0.9998).
- `UPSET_MAGNITUDE` is `DECIMAL(5,4)`, so many early upsets will tie at 0.9999 / 1.0000. The likely all-time top 20 is a cluster of tied pre-war results. "The 37th biggest of all time" would then be dominated by sparse, early-era Elo.
- Q8 recognizes this but verifies it in task 7.2, the last group, after the docs (group J) are written.

**Fix**
- Seed `ALL_TIME_FROM_SEASON` in `ANALYSIS_SETTINGS` now, and name it in `UPSETS` (`ALL_TIME_RANK` = "since <season>"). The default is a defensible start, for example 1936 (AP era) or 1978 (Division I-A); 1890 needs an argument.
- Rank on an unrounded magnitude (compute it from the line or Elo probability in the view, or widen to `DECIMAL(9,8)`), so ties are real ties.
- Move Q8's check forward: it is read-only SQL at 0 calls. Run it on the locally restored Phase 2 snapshot (U2) during task 3.3, not in 7.2.

### Leak-freedom and freezing: correct

- **Freezing is correct as specified.** First freeze wins. Each game is frozen at its own point (week − 1) at horizon 1, with params `CURRENT` (fitted on 2014–2025, so not in-sample for 2026). Phase 4's `TEAM_RATING_AS_OF` uses only as-of views and the prior season's SP+.
- **Reconstructed weeks are marked** per game by `FROZEN_BEFORE_KICKOFF`. A late refresh (after a Thursday kickoff) marks exactly those games.
- **Arrows** read only `PUBLISHED_ODDS`, so they survive `sim-drop` and are robust to recalibration ([3]).
- **Pre-2013 probabilities** come from Phase 4's `GAME_WIN_PROB`, as required.

The residual gaps are m1 (threshold arithmetic) and m14 (rescheduled games).

---

## 2. Rooting guide (decision [6])

**The estimate is sound, and S4's raw data support it more strongly than the plan argues.**

From `spikes/s4/results/latency_aws.csv` (1M, AWS):

| Q3 cell | Matching seasons (median) | p50 / p95 |
|---|---|---|
| k = 0, `agg`, 400 columns | 1,000,000 | 2.42 / 2.52 s |
| k = 3, `cols`, 400 columns | 208,951 | 2.10 / 2.26 s |
| Q2 k = 3 (k condition columns only) | 208,951 | 0.12 / 0.14 s |

What the numbers show:
- **The cost is close to independent of the matching rows and follows the aggregated columns.** With 5× fewer seasons the time drops only 13 %.
- **S4's shape aggregates 2 × 400 `SUM(CASE…)` columns** (`spikes/s4/queries.py`, `per_game_counts` called twice, once with `bubble_only`). The plan's one-pass shape over about 60 games aggregates about 120 columns.
- **Linear in aggregated columns:** ≈ 0.12 s + 2.0 s × 120/800 ≈ 0.4 s at 1M.
- **Locally:** 100k C k = 3 is 0.95 s p95 for the same 800 columns, which gives about 0.15–0.25 s.

The plan's 0.5 s / 1 s targets are therefore realistic.

**The gate and the fallbacks make sense.** The guide is measured on AWS at 1M before group B, re-measured on real data in 7.2, then falls back to F1 (top 25 by unconditional |leverage|, fewer columns again) and F2 (≤ 2 s with a "computing" state).

Refinements (m4):
- The "one alternative" in 1.1(a) should be S4's measured two-pass shape restricted to 60 columns. That gives a provenance-backed fallback if the one-pass shape misbehaves.
- The plan's flag `"SIM_ID" IN (SELECT … WHATIF_PLAYOFF …)` inside `SUM(CASE …)` is a predicate subquery in the select list. The skills do not cover it, so the probe must confirm it is accepted and evaluated once, not per row.
- Apply the gate to k = 1 as well. It has the most matching seasons.

**The full-season guide from a forced re-sim is correct, but under forcing semantics** (m5).
- Phase 4's `SIMULATE_FROM` keeps every other game's draws, and Phase 4 builds `LEVERAGE` for the forced run. So `ROOTING_GUIDE` of the forced run is P(T in CFP | g) with the results forced.
- That is not the conditional (toggle) semantics. The same game can show different numbers in the two guides, as [8] says for odds.
- Forced runs are owner-only (about 40 s at 1M), so app users have no full-season conditional guide.

State both points in the page contract. Also exclude fixed games from the forced run's `ROOTING_GUIDE`: today it emits a row with an empty branch (`N_HOME` 0, `LOW_SAMPLE`), which the conditional guide drops for the same situation.

---

## 3. Head-to-head (decision [14])

**Dropping `/teams/matchup` is sound.**
- Every game since 1869 is stored (D15, Phase 3 [18]), so the endpoint adds no information.
- It measured 4.26 s at CFBD and 4.87 s end to end, 2.4× over the mission's 2 s.
- The series is keyed by team id, which is more robust than CFBD's name-keyed matchup across renames.
- The stored-series check costs 1 ledgered call (7.3), which is a fair price for the claim "stored = CFBD's".

**It still delivers "head-to-head with a live preview fetched on click"**: `GAME_PREVIEW` is one call, 0.7 s at CFBD, about 1.3 s end to end, and genuinely un-stored data. Two caveats:
- **m7:** the live element exists only when the pair has a meeting left this season (`MATCHUP_NEXT`). Most pairs have none, so most head-to-head pages have no click.
  - Either allow the preview on the latest meeting from `MATCHUP_GAMES` (the spec already lists "the points once played", so CFBD seems to answer for played games; verify in 7.3), or fix the demo pair in the script (a rivalry-week game).
- **m8:** the preview's `RAW_JSON` already carries "recent meetings". Comparing them with `MATCHUP_GAMES` gives the stored-equals-CFBD evidence at 0 extra calls, so the `/teams/matchup` call in 7.3 becomes optional (planned calls 4 → 3).

Owner review U3 / Q13 is correctly flagged, because this changes D13's wording.

---

## 4. `CFB_API` contract, roles and concurrency

### M7 (major): the contract is complete for the Phase 6 pages, not for the Phase 7 chat

**Evidence**
- Demo beat 5: the chat "answers a question outside the pages".
- Roadmap Phase 7: Claude + MCP (`CFB_MCP`) over `CFB_API`. The architecture: apps and the MCP user read only `CFB_API`.
- The 24 views are page-shaped. Besides `TEAMS` and `MATCHUP_GAMES`, nothing exposes games, team seasons, standings, ratings or rankings. A question like "which unbeaten teams have the weakest schedule?" or "how did Michigan do in 1997?" has no source.

**Fix.** Either:
- add 3–5 thin, grain-first pass-through views over `CFB_MODEL`, for example:
  - `GAMES` (game);
  - `TEAM_SEASONS` (team and season, records, conference, Elo or SP+);
  - `TEAM_SEASONS_AS_OF` (as-of point and team);
  - `RANKINGS` (poll, week and team);

  These stay within the existing allowlist, cost 0 calls, and make the chat demo-able;
- or record an explicit Phase 7 hand-off ("Phase 7 adds model pass-through views to `CFB_API`") and drop the claim that the contract covers the chat.

The "new views may be added" clause permits either; the plan should pick one.

### Contract quality and roles

**Contract quality.** Grain-first comments (100 %, read back), the generated dictionary and the per-object allowlist (lint plus `EXA_ALL_DEPENDENCIES`) are good. Minimality is good too: no tables or functions, and the logic stays in `CFB_PRED`.

**Roles (Q2).**
- `SELECT ON SCHEMA CFB_API` only. No system privileges, no connection, no `EXECUTE`.
- The key is unreachable: the preview runs with the owner's rights, and the key-protection scenario covers values and errors.
- Note for the probe: in Exasol a schema object's owner is the schema's owner. So the owner whose rights the views run with is `CFB_API`'s owner, who must hold the underlying privileges. That is true when the same owner owns every schema: `sys`, or `CFB_TEST_OWNER` in `TEST_`.

**m6:** a schema grant cannot exclude `GAME_PREVIEW` from `CFB_MCP`. If Phase 7 decides the chat must not spend quota, the view has to move schemas, which breaks the contract. Decide the shape now: on-demand views in their own schema (for example `CFB_API_LIVE`, granted to `CFB_APP` and optionally to `CFB_MCP`), or a documented acceptance.

### Generated views under concurrent readers

**Regenerated what-if views and concurrent readers: atomic, with two gaps.**
- All three views flip in one transaction, so a single query always sees one consistent run.
- **m2, stale client state:** a page that read `WHATIF_GAMES` before the live refresh's step 400 sends conditions validated against run N to views that now show run N+1. The result is a column-not-found error, or numbers from the new run under the old run's labels. Beat 3 → beat 4 is exactly this moment.
  - Fix: the library takes `run_id`, adds `WHERE "RUN_ID" = <run>` to the `WHATIF_OUTCOMES` subquery and a guard on `WHATIF_RUN`, and returns `RUN_ID` on every row. Phase 6 reloads on a mismatch.
- **m3:** probe 1.1(e) tests only an autocommit reader of the view. Add:
  - a reader with autocommit off that read the views before the publication, and runs again after it (pooled backend or MCP sessions);
  - readers of `PLAYOFF_ODDS`, because the publication also writes `PUBLISHED_ODDS`;
  - a 2 s guide query running across the commit.

  Then state "consumers use autocommit" in the page contract.

---

## 5. Interfaces with Phases 2–4

### M1 (major): restore and "the newest published point" contradict each other

**Evidence**
- [10]: predictions and publications are deployment history and survive a restore. `loader/snapshot-restore` confirms the scope is `CFB_CORE` + `LOAD_LOG` + `FETCH_LOG`, and `CFB_SIM` is not in it either.
- `CFB_MODEL.CURRENT_AS_OF` follows the restored data (`model/as-of`).
- The what-if run is "the latest publication of the newest published point" (`analyses/what-if`). An older point's publication explicitly leaves the views alone (the published-odds scenario "Publishing an older point…").
- After "restore Sunday after week N" on a laptop that had published weeks > N:
  - `WHATIF_RUN`, `WHATIF_GAMES`, the `MATCHUP_NEXT` stakes and `CURRENT_POINT.WHATIF_RUN_ID` stay on the later run, with games that are already played in the later world;
  - `AS_OF_POINTS` and `PLAYOFF_ODDS` list future points;
  - the rehearsal recipe's own week-N publication ([10] rationale) cannot take over.

**Fix**
- Define the what-if publication relative to the current point: the latest publication of the newest published point ≤ `CURRENT_AS_OF`.
- Flag or hide points after the current one in `AS_OF_POINTS` and `PLAYOFF_ODDS` (`IS_AFTER_CURRENT`).
- Re-run `WHATIF_VIEWS` after a restore: a documented step in the restore recipe, or `make analyses`.
- Add a scenario: publications at weeks 8 and 9, restore data to week 8, then a week-8 republish gives a what-if run at week 8.

### M2 (major): one definition of "the what-if run", and generated views created before the file views that read them

**Evidence**
- `api/serving-contract`: `CURRENT_POINT` reads `WHATIF_RUN`, a generated view.
- `analyses/deploy`: file views are deployed and compiled in one transaction, with comments read back. `WHATIF_VIEWS` runs only after the commit.
- On a fresh namespace, `CURRENT_POINT` therefore references a missing object. A `FORCE` view is created invalid, has no readable columns, and fails the "every view compiled, 100 % comments" check.
- Meanwhile "the what-if run" is derived separately:
  - in `WHATIF_VIEWS` ("newest publication whose run is `DONE`");
  - in `analib.publish` ("newest published point");
  - in `WHATIF_GAMES` (its reads list no `WHATIF_RUN`);
  - in `MATCHUP_NEXT` (via `POINT_PUBLICATION`).

  These rules can disagree, for example after a drop (Q14) or with M1.

**Fix**
- One `CFB_PRED` view, `WHATIF_PUBLICATION` (≤ 1 row, the rule of M1), used by `WHATIF_VIEWS`, `analib.publish`, `MATCHUP_NEXT`, `WHATIF_GAMES` and `CURRENT_POINT`.
- `analyses/deploy` renders the empty generated views inside the deploy transaction, before the file views, then regenerates after the commit as today. Alternatively, file views read only `CFB_PRED`.

### Refresh chain, retention and snapshot exclusion

**Steps 200 and 400: fit.**
- Both use Phase 2's contract `(run_id, season, week, season_type)` and commit their own rows.
- 200 sits between Phase 3's 100 and Phase 4's 300. 400 finds Phase 4's run by `LOAD_RUN_ID` + `REFRESH` + `DONE`, and skips cleanly when 300 skipped after the selection week.
- **m9:** `REFRESH_STEPS` is written by the *loader* deploy from the YAML (`loader/deploy.py` `_write_plan`), before the analyses scripts exist.
  - Partial deploys, and the Phase 3 and Phase 4 integration tests if they use the project YAML, then run a step 200 whose script is missing.
  - State who writes rows 200 and 400 (the plan says "through `make analyses`", while the code path is the loader deploy). Confirm that the earlier phases' integration tests use plan variants.

**Retention: compatible.**
- Arrows no longer need runs. Phase 4's "keep weekly refresh runs" still serves older guides. The recorder note is right.
- **m13:** dropping the published what-if run is left to documentation (Q14). A one-line guard is cheaper than a broken page mid-demo: Phase 4's `DROP_SIM_RUN` / `make sim-drop` refuses the run named by `WHATIF_PUBLICATION` unless forced. This is a hand-off to Phase 4 (unrecorded, so an edit there is cheap).

**Snapshot exclusion ([10]): consistent** with Phase 2's rule that run history stays per deployment. Its consequence is M1.

---

## 6. YAGNI and missing pieces

### M3 (major): generated DDL embeds names unescaped

**Evidence**
- The column comments of `WHATIF_OUTCOMES` are `'Week 12: Brookvale at Ashford (game …)'`, built from `SIM_RUN_GAME`/roster names, by `LISTAGG` or Lua.
- CFBD's FBS school "Hawai'i" contains an apostrophe. Any publication with a remaining Hawai'i game produces invalid DDL. With the newest-point rule, that rolls back every weekly publication of the season.
- Neither this plan nor Phase 4 (same comments on `SIM_PATH_R<run>`) mentions escaping.
- The synthetic league has no such name, so no test would catch it.

**Fix**
- Escape `'` → `''` in both generator paths (one helper).
- Give one synthetic team an apostrophe (for example "St. Kestrel's").
- Add a unit test on the renderer.
- Record a hand-off note for Phase 4's per-run DDL.

### M6 (major): the performance harness can spend quota

**Evidence**
- Task 7.2: "every budget row at 1M (S4 protocol)". The budget table contains "`GAME_PREVIEW`, live". The S4 protocol is 3 warm-ups + 5 × 5 samples, about 28 live calls, outside `make record`'s ledger.
- That breaks the ≤ 6 cap and the "ledger = `/info` drop" proof. `QUOTA` would also hit `/info` on every sample.

**Fix**
- `analyses/perf.py` excludes `GAME_PREVIEW` and `QUOTA` by construction, with a unit test asserting that no perf query references either.
- The preview latency comes only from 7.3's 3 ledgered calls.

### What could be cut (small)

- `SCORECARD_SEASON` per as-of point. Per season with a week bound would do, but the as-of picker justifies it.
- Probe 1.1(d)'s `LISTAGG` length question (m12). About 900 × ~110 characters ≈ 100k, against `LISTAGG`'s 2,000,000-character `VARCHAR` limit, so the risk is low. Keep the DDL timing and drop the length investigation. `LISTAGG` needs `WITHIN GROUP (ORDER BY GAME_IDX)` for a deterministic column order.
- `FORCED_RUN_ODDS` serves no demo beat. Keep it, because the roadmap lists forced re-sims, but schedule it last.

Nothing else is gold-plating. F1/F2, the two roles and the history variant are each cheap and earn their place.

### Missing (beyond M3 and M6)

- **m14:** rescheduled or added games. Which week grades a postponed game (the frozen `WEEK` or the game's current week)? Is `FROZEN_BEFORE_KICKOFF` re-evaluated if a kickoff moves earlier? State both.
- **m15:** Exasol `RANK()` ties on `DECIMAL(5,4)` already produce "tied for 37th". The page contract should say whether ties show as "T-37".

---

## 7. Testability, groups and quota

- **Mapping:** 69 of 69 scenarios are mapped to named tests, and the unit, CLI and integration tiers are consistent with Phases 2–4. `speq plan validate` passes.
- **m10, ordering:** some integration tests reference views their group does not build.
  - Task 3.5 (group E) owns `test_forced_resim_it.py`, whose scenarios query `CFB_API.FORCED_RUN_ODDS` and `CFB_API.ROOTING_GUIDE`. Those are built in 4.1 (group G).
  - Task 2.4 (group D) owns `test_api_roles_it.py`, whose "reads every serving view" and preview scenarios need 4.1 and 4.2.

  Either write those scenarios against `CFB_PRED` objects first, or move the test completion to group H. State which task finishes each file.
- **Probe placement:** 1.1(a) needs AWS at 1M and `spikes/s4/`, which is untracked today. The dependency is listed; make "committed `spikes/s4/`" an explicit gate of group A.
- **m11, zero-call proof:** "ledger total = `/info` drop over group K" is fragile while other sessions use the same key (the October mandate shares it). Compare the ledger with `FETCH_LOG` rows of the plan's context, or run group K's live step in an exclusive window and record the other sessions' ledger entries.
- **Live cap:** at most 6 calls, 4 planned (3 previews + 1 matchup). The arithmetic is right, and it holds once M6 is fixed (and becomes 3 with m8).
- **m1, movement threshold:** [7] calls 0.0050 "more than 3 SE at 100k".
  - The arrow compares two independent runs (different weekly seeds), so the SE of the difference at p = 0.5 is √2 × 0.158 % = 0.224 %. 0.0050 is about 2.2 SE.
  - Across about 130 teams, a few spurious arrows per week are expected near the bubble.
  - Fix: use 0.0070, or compute the threshold per team as 3·√(p(1−p)(1/n₁+1/n₂)) in `ODDS_MOVEMENT`. That also handles comparing 1M with 100k.

---

## 8. Exasol claims checked against the skills

The three skills loaded say nothing on most of these semantics. I checked the grammar files and give my expectation; the plan already routes each through a probe with a fallback, which is the right call.

| Claim | Skills | Assessment |
|---|---|---|
| Owner's rights through views | Not stated (`exasol-database` grammar only) | Expected to hold. In Exasol the view's owner is the schema owner (see §4); the virtual-table part rests on foundation probe 1.4g. Probe 1.1(b) is right |
| `GRANT SELECT ON SCHEMA` covers later views | Grammar only (`grant_objprivs_statement_1`) | Expected to hold (schema-level object privileges apply to the schema's objects, including later ones). Probe 1.1(c) plus the re-grant fallback (Q3) are adequate |
| About 900-column generated view, `LISTAGG` | `listagg_1` / `listagg_overflow` confirmed in `exasol-grammar-functions.md` | About 100k characters is far below `LISTAGG`'s 2,000,000; 900 columns are far below table/view limits. The real risk is quoting (M3) and ordering (m12) |
| Literal `GAME_ID` pushdown through an aliased view column | Adapter skill: `EXPLAIN VIRTUAL` validates pushdown; nothing on views | Views are inlined before pushdown, so a literal predicate on an alias normally reaches the adapter. Probe 1.1(b) with the `P_GAME_ID` fallback (Q4) is right. `EXPLAIN VIRTUAL` takes any `SELECT`, so it works through the view |
| Transactional view regeneration | Not stated; Phase 3 probe 1.1b | DDL is transactional, so rollback is fine. The concurrency semantics (WAIT FOR COMMIT vs rollback) are the open part: widen 1.1(e) per m3 |
| Reserved words | `exasol-reserved-keywords.md` | The plan's list (`NEXT`, `CURRENT`, `RESULT`, `SOURCE`, …) matches. New names (`FREEZE_KIND`, `ALL_TIME_FROM_SEASON`, `IS_AFTER_CURRENT`) are not reserved |

---

## 9. What's good

- **Publication as an explicit act** ([2]) answers "what do the pages show" once. It is the single trigger for frozen odds and regenerated views, and it makes the scale-dial beat one command.
- **Frozen published odds behind the arrows** ([3]) decouple movement from retention. That is the right call and it costs about 136 rows per point.
- **Generated stable-name views plus a pure, validated SQL builder** ([4], [5]) is the cleanest answer to "no parameterized views" with read-only consumers. Injection is impossible by construction (integers and `TRUE`/`FALSE` only), and host oracles test it.
- **The rooting-guide design is evidence-driven.** The estimate matches S4's raw scaling, a decisive probe comes before any dependent code, and the fallbacks are ordered.
- **The sample size sits beside every conditional number**, with explainable thresholds, and the 1M vs 100k dial becomes visible through `LOW_SAMPLE`.
- **Honest grading machinery:** first freeze wins, reconstruction at the game's own point, a per-game flag, two populations, and the line on exactly the same games, labeled as closing-line information.
- **Head-to-head from stored games** is a better product and a better D15 story than `/teams/matchup`. The live beat moves to data nobody stores.
- **Quota discipline:** one API-reaching view, a lint plus `EXA_ALL_DEPENDENCIES` allowlist, a ≤ 6 ledgered cap with 4 planned, and replay for the local check.
- **Process:** every Exasol claim is tabulated with its evidence and fallback, the integration test file comes before the code, the parallel groups touch disjoint files, and the plan is explicit about what it does not change in Phases 2–4.

---

## Review resolution

Planner revision, 2026-10-22. The owner (Nico) was away; the orchestrator decided each finding for him, and the decisions are logged in `decision-log.md` (§ Interview → "Review resolution", design entries [26]–[40], revised [4], [6], [7], [10], [13], [14], [16], [17], [20], [22], [23], and Q1–Q17). No DB session, no CFBD call, no file outside this plan directory changed. Skills loaded first: `exasol:exasol-database`, `exasol:exasol-udfs`, `exasol:exasol-virtual-schema-adapter-development`.

Result: 78 scenarios (69 + 9 new), all mapped; 34 tasks (31 + 3: 1.7, 3.7, 4.6), 2 `[expert]`, 12 parallel groups; `speq plan validate add-cfb-analyses` passes without warnings.

### Major findings

| # | Resolution | Where |
|---|---|---|
| M1 | **Accepted.** `CFB_PRED.WHATIF_PUBLICATION` = latest publication of the newest published point **at or before `CURRENT_AS_OF`** whose run is `DONE`. `IS_AFTER_CURRENT` on `POINT_PUBLICATION`, `ODDS_MOVEMENT`, `PLAYOFF_ODDS`, `AS_OF_POINTS`; a run after the current point cannot be published. `make analyses` is the restore recipe's last step and regenerates the views. `CURRENT_POINT.WHATIF_VIEWS_CURRENT` + an empty `WHATIF_GAMES` make a lag visible. New scenario "After a restore the what-if follows the restored point" (weeks 8 and 9 published, restore to week 8, `make analyses`, week-9 republish refused) | [26]; `analyses/what-if`, `analyses/published-odds`, `api/serving-contract`; tasks 3.5, 4.1, 4.3, 6.1; Phase 8 hand-off |
| M2 | **Accepted.** One view, `WHATIF_PUBLICATION`, read by `WHATIF_VIEWS`, `analib.publish` (inside its own transaction, after its inserts), `WHATIF_GAMES`, `CURRENT_POINT` and `MATCHUP_NEXT`. The deploy creates the empty generated views (where missing) inside its transaction, after `CFB_PRED` and before the file views; existing ones are kept. Deploy scenario asserts every view compiles before the commit | [26], [27]; `analyses/deploy`; tasks 2.2, 3.5 |
| M3 | **Accepted.** One escaping rule (`'` → `''`) for every string a generator writes into SQL: `analyses/sqltext.py` `sql_str` (host) and `analib.sql_str` (Lua). A pure host reference renderer `analyses/whatif_ddl.py`; unit tests `test_sqltext.py`, `test_whatif_ddl.py`; the integration test compares the deployed columns and comments with the renderer. `Hawai'i` in the sim league via Phase 4 hand-off P5-M3 (already in Phase 4's `review.md`), with a fallback variant in this plan. New scenario "School names with an apostrophe are quoted in the generated views" | [28], [39], Q17; task 1.7 (new), 1.5, 3.5, 4.3 |
| M4 | **Accepted.** `FREEZE_KIND` (`IN_ADVANCE` / `RECONSTRUCTED` / `ALL`) in all three scorecard views; `IN_ADVANCE` also requires the freeze to precede the game's *current* kickoff. `IS_HEADLINE` (in `CFB_PRED`, passed through by `CFB_API`) marks the `IN_ADVANCE` row when it has graded games, else the labeled `ALL` row. Phase 8 hand-off: the rehearsal headline is the labeled reconstruction unless predictions were frozen live. New scenario "Predictions made in advance and reconstructions are graded apart" | [29]; `analyses/scorecard`, `api/serving-contract`; task 3.2 |
| M5 | **Accepted.** `ALL_TIME_FROM_SEASON` = **1978** (first Division I-A season: "involves an FBS team" is a retroactive label before it, and early Elo gaps lie far outside the 2014–2025 fit). `RANK_MAGNITUDE` (`DOUBLE`, recomputed from Elo with `CURRENT`'s parameters; line rows keep the stored 4-decimal value) drives every rank, pinned to `UPSET_MAGNITUDE` within 0.00005. History variant gains 1985 and 1987 games for an exact tie and a near-tie. Q8 moved to task 3.3 (restored snapshot, else synthetic for mechanics), repeated in 7.2. New scenarios "Ranks use the magnitude before rounding", "Upsets before the all-time window rank within their season only" | [30], Q8; `analyses/upsets`; tasks 1.5, 3.3, 7.2 |
| M6 | **Accepted.** `analyses/perf.py` times a fixed catalog over `CFB_API` only; `tests/unit/test_analyses_perf.py` asserts no query names `GAME_PREVIEW`, `QUOTA`, `CFB_API_LIVE` or a virtual schema. The preview's latency comes only from 7.3's 3 ledgered calls; cap ≤ 6, all through the ledger | [31]; `plan.md` budget and protocol; task 5.1 |
| M7 | **Accepted.** Four pass-through views: `GAMES` (`CFB_MODEL.GAME`), `TEAM_SEASONS` (`TEAM_SEASON`), `RANKINGS` (`RANKING`), `SP_RATINGS` (`RATING_SP`). Source columns unchanged with the source's column comments; grain-first view comments that say "stored history as it is now". New scenario "The chat can answer questions outside the pages from the model views" | [32]; `api/serving-contract`; task 4.1 |

### Orchestrator decisions beyond the major findings

| Topic | Resolution | Where |
|---|---|---|
| Roles (m6) | `GAME_PREVIEW` and `QUOTA` (the only views that reach the network) moved to **`CFB_API_LIVE`**. `CFB_APP`: `SELECT` on `CFB_API` and `CFB_API_LIVE`; `CFB_MCP`: `CFB_API` only. Scenario "The chat role has the same access as the app role" replaced by "The chat role reads the serving views but cannot reach CFBD" | [17], [33]; `api/roles`, `api/on-demand`; tasks 2.4, 4.2, 4.6 |
| Head-to-head (m7, m8) | Preview of the next meeting, else of the latest meeting (`MATCHUP_SERIES.LAST_GAME_ID`). 7.3 plans **3 calls** (2 coming-week games, 1 latest meeting); stored series checked against the previews' `RAW_JSON` recent meetings on the host; `/teams/matchup` optional. New scenario "The latest meeting of a pair can be previewed after it was played"; new Q15 | [14], [22], [34]; `analyses/head-to-head`, `api/on-demand`; task 7.3 |
| Rooting guide (m4, m5) | Probe 1.1(a) adds S4's two-pass shape cut to 60 columns, confirms the `IN`-subquery flag inside `SUM(CASE)` (accepted, a semi-join; else a `LEFT JOIN` flag, Q16), and gates k = 1 as well as k = 3. `ROOTING_GUIDE` drops games the run fixed and carries `RUN_ORIGIN` and `FIXED_RESULTS`; pages label a forced run's guide "with these results forced" | [6], [37]; `analyses/rooting-guide`, `analyses/forced-resim`; tasks 1.1, 3.5 |
| Arrow threshold (m1) | **0.0070**, a constant: 3.1 SE of a difference at 100k vs 100k (4.2 at 1M vs 100k, 9.9 at 1M vs 1M). Simpler than a per-team threshold and valid for every run size the demo publishes (≥ 100k); the setting's note says so | [35]; `analyses/published-odds` |
| Probe 1.1(e) (m3) | Widened: autocommit reader; a reader with autocommit off across the publication; readers of the published odds; a ≈ 2 s guide query across the commit. "Consumers use autocommit" is in the page contract | [23], Q5; task 1.1; `api/serving-contract` |
| U3 / Q13 | Kept as a non-blocking owner-review item | Q13; Dependencies |

### Minor findings

| # | Resolution |
|---|---|
| m1 | Arrow threshold 0.0070 ([35]) |
| m2 | `what_if_odds_sql(run_id, …)` and `rooting_guide_sql(run_id, …)`: `WHERE "RUN_ID" = <run>` on `WHATIF_OUTCOMES`, a guard on `WHATIF_RUN`, `RUN_ID` on every row; empty result or unknown column → the page reloads. New scenario "A what-if built for an earlier run returns no rows" ([36]) |
| m3 | Probe 1.1(e) widened; autocommit in the page contract |
| m4 | Two-pass alternative, `IN`-subquery check, k = 1 gate ([6], Q16) |
| m5 | Forced guide drops forced games and is labeled; both semantics stated in `analyses/rooting-guide` and the page contract ([37]) |
| m6 | `CFB_API_LIVE` ([33]) |
| m7 | Preview of the latest meeting ([34]) |
| m8 | `RAW_JSON` recent meetings vs `MATCHUP_GAMES`; matchup call optional; 3 planned ([34], [22]) |
| m9 | Steps 200/400 are written by the loader deploy from the YAML (stated in `plan.md` and `analyses/deploy`); `make analyses` reports them and warns about a missing script or row; tests use `tests/it/fixtures/load-plan.analyses.yaml`; task 1.6 confirms Phase 3/4 tests use plan variants; task 2.2 deploys stub scripts so every registered step exists ([38]) |
| m10 | New "who starts and who finishes each integration test file" table in `plan.md`. `test_forced_resim_it.py` now reads `CFB_PRED` objects (task 3.7, group F). `test_api_roles_it.py` is started in 2.4 and completed by 3.5, 4.2 and 4.6 (new). Deploy and contract scenarios are added by the tasks that build their last object. `IS_HEADLINE` moved into `CFB_PRED` so the scorecard tests need no `CFB_API` view. On-demand scenarios read `CFB_PRED.MATCHUP_SERIES` and `CFB_MODEL.GAME` instead of later `CFB_API` views. 3.3 moved to group E (its prediction scenario needs 3.1) |
| m11 | 7.3 runs in a window agreed with the orchestrator with no other session spending calls; 7.5 matches the ledger entries one to one with the `FETCH_LOG` rows of the `make record` runs and the `/info` drop with every ledger entry in the window, each listed with its plan |
| m12 | No `LISTAGG`: the Lua generator iterates rows `ORDER BY GAME_IDX`; probe 1.1(d) only times the DDL; Q12 resolved ([39]) |
| m13 | Hand-off **P5-m13** for Phase 4 (in `plan.md` Dependencies): `make sim-drop` refuses the run named by `WHATIF_PUBLICATION` unless `FORCE=yes` (a host-side check, so no `CFB_SIM` object references `CFB_PRED`). The orchestrator should add it to Phase 4's `review.md`; this plan could not edit outside its directory. Without it, [26]'s lag handling limits the damage (Q14) |
| m14 | Games are graded in the week they are played (`GAME_WEEK*`, `IS_RESCHEDULED`); a frozen game is never frozen again; an added game is frozen at its own freeze point; `FREEZE_KIND` re-judges the freeze against the current kickoff. New scenario "A rescheduled game is graded in the week it is played" ([29]) |
| m15 | `ALL_TIME_TIED`, `LINE_ERA_TIED`; pages show "T-37" ([30]) |
| Probe placement | "`spikes/s4/` committed" is an explicit gate of group A / probe 1.1(a) |
| Small cuts | `SCORECARD_SEASON` per point kept (the as-of picker); `FORCED_RUN_ODDS` kept and scheduled last among the analyses (task 3.7); probe 1.1(d)'s `LISTAGG` length investigation dropped (m12) |

### Rejected or changed from the review's suggestion

- **Nothing was rejected.** Three choices among the review's options, with reasons:
  - **M2:** both of the review's fixes were possible; the orchestrator chose empty generated views inside the deploy transaction. File views do read the generated `WHATIF_RUN` (for `WHATIF_VIEWS_CURRENT` and `WHATIF_GAMES`'s guard), which "file views read only `CFB_PRED`" would have ruled out.
  - **M5:** the review offered "compute it in the view or widen to `DECIMAL(9,8)`". The plan computes `RANK_MAGNITUDE` in `CFB_PRED.UPSET`, because widening `GAME_WIN_PROB` is a Phase 4 edit; the duplicated Elo formula is pinned to Phase 4's rounded value by the upsets test.
  - **m1:** the constant 0.0070, not the per-team threshold; reason in [35].
- **`WHATIF_GAMES` reads both `WHATIF_PUBLICATION` and the generated `WHATIF_RUN`.** The review listed it among the plain readers of `WHATIF_PUBLICATION`. It also checks that the generated views name the same run, because its rows name the generated columns; when they disagree it returns no rows instead of offering toggles on columns that do not exist.
- **`CFB_API.WHATIF_SETTINGS` renamed `ANALYSIS_SETTINGS`** ([40]), because it now also holds `MOVE_THRESHOLD` and `ALL_TIME_FROM_SEASON`. Not asked for by the review; free while the contract is unrecorded.

### For the orchestrator

- Add hand-off **P5-m13** to Phase 4's `review.md` (`make sim-drop` refuses the what-if publication's run unless forced).
- Phase 4 hand-off **P5-M3** is a dependency of this plan's group B (Q17).
- `spikes/s4/` must be committed before probe 1.1(a).
- 7.3 needs a window with no other session spending CFBD calls.
- Owner review U3/Q13 (no `/teams/matchup`) stays open and does not block.
