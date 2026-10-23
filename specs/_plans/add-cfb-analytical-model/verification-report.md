# Verification report: add-cfb-analytical-model

Date: 2026-10-23. Branch `add-cfb-analytical-model`, from HEAD 8d4d739. Light process by the owner's request ("this is a demo, not a full-blown product"): R.1 and V.2 skipped, gaps listed, not closed. Zero CFBD calls.

## V.1 Checklist

| Item | Result |
|---|---|
| `make test` (unit + cli, no DB) | 6926 passed |
| `make lint` (ruff, `ruff format --check`, mypy) | clean: 269 files formatted, mypy no issues in 196 source files |
| `make model-docs` | writes 27 objects; no diff against the committed `docs/model-dictionary.md` |
| `uv run python -m scripts.model deploy --lint-only` | "lint ok: 27 objects" |
| `make deploy TARGET=aws` and `TARGET=local` | exit 0 both; 15 tables, 11 views, 1 function; comments 454 of 454; 0 CFBD calls |
| Refresh step 100 `MODEL` registered | yes on both targets (`CFB_META.REFRESH_STEPS` = `(100, MODEL, CFB_INFRA.REFRESH_MODEL)`) |
| Layering: `git grep CFBD_VS -- sql/model` | only descriptive comment text (`DATASET` seed rows and column comments); no object reads the virtual schema (the lint enforces it) |
| Zero calls | `FETCH_LOG` 221 rows on both targets before and after, latest `LOGGED_AT` unchanged, live ledger 37 lines before and after |
| No data in git (`*.parquet`, `*.cfbd.json`, `*.csv.gz`) | empty |
| Real-data checks (`MODEL_CHECKS`) | no ERROR except 2020 `GAME_WITHOUT_WEEK` (spring games); 2026 week 7: 13 of 16 pass, 3 known warnings, both targets identical (`docs/model-verification.md`) |
| Performance targets | every target met on AWS and local (`docs/model-verification.md`, decision [40]) |

## V.2 Scenario coverage audit

Skipped (light process). The 90 scenarios were mapped to tests in the plan's coverage table when each task landed.

## V.3 Manual flows

Evidence, not repeated here: `docs/model-verification.md` (targets, stored counts, zero-call proof, checks, Q1 to Q13, coverage, timings), decision log [35] to [40], `docs/spikes/s6-model-probe.md`. In this pass: `make deploy` on both targets, then read-only queries for the registration, the checks, the coverage and the assumption outcomes. The known 2024 selection-ranking lateness (X2) is in the user guide.

## Gaps (listed, not closed)

1. The coverage gate (`make coverage`, 90 % for `model` and `vs/host/sqlobjects.py`) and a full `make test-it` were not re-run in this pass.
2. Task 4.2: only the `head_to_head` example exists; `as_of_engine_inputs`, `all_time_upsets` and `cfp_field_inputs` are deferred (backlog). Done-when proof rests on the as-of views holding the engine contract's inputs and on `head_to_head`.
3. The `MATERIALIZED_STALE` check for the derived tables is not built; the staleness rule is documented (user guide § 10, `CLAUDE.md`).
4. Probe 1.1 was not re-run against AWS (task 7.1); the successful AWS deploy exercises the same features.
5. The live `REFRESH_WEEK` chain with step 100 has not run on a target since registration (the next weekly refresh does it); the replay chain is covered by an integration test.
6. Review R.1 skipped; the model is SQL views and tables, the risky Lua step (`refresh_model.lua`) has a lupa unit test and integration tests.

## Nothing blocks recording
