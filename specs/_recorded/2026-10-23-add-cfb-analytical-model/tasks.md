# Tasks: add-cfb-analytical-model

Worktree: ../exasol-cfb-demo-p3 (branch add-cfb-analytical-model, branched from add-cfb-persistence-refresh)

## Group A
- [x] 1.1 Model platform probe `docs/spikes/s6_model_probe.py`, findings in `docs/spikes/s6-model-probe.md`.
- [x] 1.2 `Namespace.model_schema`, and `{MODEL_SCHEMA}` in Phase 2's placeholder renderer (`vs/host/metaddl.p
- [x] 1.3 `vs/host/sqlobjects.py`, pure part (`load`, `lint`, `render`) per Key Interfaces:
- [x] 1.4 Synthetic league `tests/synthetic/league.py`:
- [x] 1.5 Reference seeds `sql/model/010_era.sql` … `016_classification_transition.sql`, with comments, per th
- [x] 1.6 Scaled league `tests/synthetic/scaled_league.py` per Key Interfaces

## Group B
- [x] 2.1 Deploy part of `vs/host/sqlobjects.py` (`deploy`):
- [x] 2.2 `sql/meta/model_check_log.sql`, registered in `vs/host/metaddl.py`; unit test.
- [x] 2.3 `model/dictionary.py` and `make model-docs` → `docs/model-dictionary.md` (grain, columns, comments p
- [x] 2.4 Harness (`tests/it/harness.py`, `tests/it/conftest.py`):

## Group C
- [x] 2.5 Deploy integration tests on fixture model directories (`tests/it/fixtures/model_sql/{valid,lint_erro
- [x] 3.1 `090_conference_championship_game.sql`, `100_game.sql`, `110_season_week.sql`, `120_season.sql` : [expert]
- [x] 3.2 `020_great_circle_km.sql`, `130_venue.sql`

## Group D
- [x] 3.3 `140_team.sql`, `150_conference.sql` (read `GAME`)
- [x] 3.4 `200_team_game.sql` (reads `GAME`)
- [x] 3.5 `170_ranking.sql` (reads `SEASON_WEEK`)

## Group E
- [x] 3.6 `160_rating_sp.sql` (reads `TEAM`)
- [x] 3.7 Performance smoke on the scaled league (needs 1.6, 3.1, 3.3, 3.4 and a "local free" window)

## Group F
- [x] 3.8 `210_team_season.sql` : [expert]

## Group G
- [x] 3.9 `300_as_of_point.sql` … `340_team_season_as_of.sql`, per `model/as-of` : [expert]
- [x] 3.10 `400_season_coverage.sql`
- [x] 3.11 `500_model_checks.sql`

## Group H
- [x] 4.1 `model/lua/refresh_model.lua` per Key Interfaces, every statement through `pquery`: (rebuilds the derived tables first, decision [40]; run lock kind `MODEL` on its own; step 100 in `loader/load-plan.yaml`; `make model-refresh`; backfill/restore post-hooks)
- [-] 4.2 (light process: deferred) Example analyses `sql/model/examples/{as_of_engine_inputs,all_time_upsets,head_to_head,cfp_field_inp
- [x] 4.3 Performance on the full model (needs 3.9–3.11 and a "local free" window): eight derived tables (decision [40]); numbers in `docs/spikes/s6-model-probe.md`

## Group I
- [-] 5.1 Lint, coverage and the integration suite: (light process: `make test` 6926 passed and `make lint` clean on 2026-10-23; the coverage gate and a full `make test-it` were not re-run in the closing pass, the model IT suites ran as each task landed; listed as open in the verification report)

## Group J
- [x] 6.1 `docs/user-guide.md` § "The analytical model":
- [x] 6.2 `specs/architecture.md`:
- [x] 6.3 `docs/roadmap.md`: (D50 to D58; Phase 3 status)
- [x] 6.4 `CLAUDE.md` dense bullets: (also `specs/mission.md`: commands, `GEOMETRY(4326)`, as-of glossary)

## Group K (sequential, gated)
- [x] 7.1 Gates and deploy: (light: `make deploy` on AWS and local, 454 of 454 comments, step 100 registered on both; probe 1.1 not re-run on AWS)
- [x] 7.2 Checks and assumptions on AWS: (Q1 to Q13 in `docs/model-verification.md`)
- [x] 7.3 Performance and examples: (timings per decision [40]; examples other than `head_to_head` deferred with 4.2)
- [x] 7.4 Local: (deployed, same counts and checks as AWS; timings in [40])
- [x] 7.5 Zero-call proof and write-up: (`docs/model-verification.md`: FETCH_LOG 221 on both targets, ledger lines 37 before and after)
- Closing pass 2026-10-23 (light process): docs 6.1 to 6.4, step 100 registered on both targets, `docs/model-verification.md`, `verification-report.md`. Earlier note: light pre-run of 7.1–7.3 on 2026-10-23, on the model through 3.8 (no as-of views, no `MODEL_CHECKS`): `make model` on AWS and local, real-data sanity checks, one bug fixed ([36]), `TEAM` columns for the app ([37]), observations ([38]). 

## Cross-plan items (from the Phase 4 revision, 2026-10-22)
- [x] X1 Restrict championship-game detection to weeks up to the season's power-conference championship week. In the stored 2026 schedule, Army–Navy is flagged as an AAC conference game alone in week 15. The current rule would mislabel it and would miss the real AAC title game in week 14, which then enters 2026 runs as a regular game and triggers CONFERENCE_PAIR_REPEATED from the week-13 point on (late November). Fold this into task 3.1 (`CONFERENCE_CHAMPIONSHIP_GAME`) and add a scenario plus a decision-log entry. Also take the Phase 2 `DEPLOY` lock kind in the model deploy (Phase 4 H1).
- [x] X2 For information: (one sentence in the user guide § 10, with the real labels: the selection ranking of 2024 is labeled week 16 and visible from point 16) under the label rule, the 2024 selection ranking only becomes visible one week after selection. Phase 4 is unaffected because it reads final rankings as labels only. Mention it in the as-of docs.

## Review and Verification
- [-] R.1 Code review (light process: review only risky code; the model is views and tables)
- [x] V.1 Checklist
- [-] V.2 Scenario coverage audit (light process)
- [x] V.3 Manual testing and verification-report.md
