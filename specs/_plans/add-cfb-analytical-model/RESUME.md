# RESUME: add-cfb-analytical-model (planner state, 2026-10-22, after the review revision)

## Done
- Independent review `review.md` (verdict: approve with changes; 1 blocker, 6 major, 19 minor) resolved per the orchestrator's decisions. The mapping of every finding to its fix is `review.md` § Review resolution.
- `plan.md` revised: one as-of horizon, result-dependent games hidden (`PARTICIPANTS_RULE`), engine contract mapping (D19/D20), fixed-scale `DECIMAL`, championship seed, classification transition seed, deduplication of every slice-scoped source, `FORCE` views and unquoted upper-case DDL names, run lock, materialization fallback with rebuild hooks, scaled-league performance smoke, league fixes, regrouped tasks (35 tasks, 11 groups, 3 `[expert]`), 90 scenario mappings.
- 10 spec deltas under `model/` rewritten (90 scenarios). `CONFERENCE_SEASON`, `TEAM_GAME.TRAVEL_KM`, the `CONF_CHAMPIONSHIP` era kind and the per-season rows of never-stored datasets are gone.
- `decision-log.md`: the review-resolution interview entry, decisions [1]–[20] revised where the review changed them, new decisions [21]–[31], open questions Q1–Q13.
- `speq plan validate add-cfb-analytical-model`: passed with no warnings.

## Left
1. Hand the plan to `speq-implement` once the foundation (through group I) and the listed Phase 2 tasks have landed.
2. Run Q6 as soon as stored 2007 and 2017 games exist on any target (read-only, zero calls); it changes only the seed rows of `CLASSIFICATION_TRANSITION`.
3. The revision is not committed; the owner commits.
