# RESUME: add-cfb-analytical-model (planner state, 2026-10-22, stopped before the 14:33 CEST restart)

## Done
- `plan.md`: complete (Summary, Design with Key Interfaces + Exasol claims table, Consequences, Features, Requirements, Dependencies, Migration, 7 task groups, Parallelization, Dead Code Removal, Verification with 71 scenario mappings, Manual Testing, Checklist).
- 10 spec deltas under `model/`: deploy, documentation, seasons-eras, teams-conferences, games, ratings-rankings, venues, coverage, as-of, refresh-step (71 scenarios).
- `decision-log.md`: interview, decisions [1]–[20] (ADR flags set), open questions Q1–Q12 as assumption + verify step.
- `speq plan validate add-cfb-analytical-model`: passed (5 style warnings: scenarios with 4–5 AND steps).

## Left (optional polish, none blocking)
1. Split the 5 scenarios with >3 AND steps if the reviewer wants the warnings gone (coverage "declared but not stored", games "results"/"championship", seasons-eras "eras", teams-conferences "tiers").
2. Cross-read pass: plan ↔ specs consistency (league facts table in Key Interfaces vs spec GIVENs; object count in Manual Testing fixed to 18 views + 5 tables + 1 function).
3. Hand the report (files, validation, key decisions, object list with grain, open questions) back to the orchestrator.
