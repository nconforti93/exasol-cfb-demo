# Tasks: add-cfb-persistence-refresh

Worktree: ../exasol-cfb-demo-p2 (branch add-cfb-persistence-refresh)

## Group A
- [x] 1.1 Loader platform probe `docs/spikes/s5_loader_probe.py`, with findings in `docs/spikes/s5-loader-prob [expert]
- [x] 1.2 Snapshot format probe `docs/spikes/s5_snapshot_probe.py`, with findings in the same doc
- [x] 1.3 Loader additions to the foundation's targets (P2, `vs/host/target.py`; no second target module):
- [x] 1.4 Ledger `vs/host/ledger.py` + `vs/live/ledger.yaml`.
- [x] 2.1 `loader/plan.py`
- [x] 3.1 Meta DDL `sql/meta/{load_plan,load_plan_gaps,loader_config,season_numbers,refresh_steps,load_run,ref
- [x] 6.2 Synthetic data, mock and test scripts:

## Group B
- [x] 2.2 `loader/templates.py`
- [x] 3.2 Views `sql/loader/{load_slices,backfill_queue,load_status,quota}.sql` [expert]
- [x] 4.1 Bundler `loader/bundle.py` (no Lua logic):

## Group C
- [x] 2.3 Author `loader/load-plan.yaml`:
- [x] 3.3 Loader deploy `loader/deploy.py`, called by `scripts/deploy.py` after the VS deploy and by the IT ha

## Group D
- [ ] 5.1 `scripts/loader_cli.py` with the subcommands `backfill`, `refresh` and `quota`, and the make targets
- [ ] 6.1 Extend `tests/it/harness.py`:

## Group E
- [ ] 7.1 `tests/it/test_load_plan_it.py` and `tests/cli/test_load_plan_cli.py`
- [ ] 7.2 `tests/it/test_quota_it.py`

## Group F
- [ ] 4.2 `loader/lua/loadlib.lua` (owned by this task) and `loader/lua/backfill.lua`, test-first with `tests/ [expert]

## Group G
- [ ] 4.3 `loader/lua/refresh_week.lua`, after 4.2, test-first with `tests/it/test_refresh_week_it.py` [expert]
- [ ] 5.2 Snapshot and restore `loader/snapshot.py` + `scripts/snapshot.py`, with the make targets `snapshot`, [expert]

## Group H (sequential)
- [ ] 7.3 The rest of `tests/it/test_run_logs_it.py`
- [ ] 7.4 Quality gate:

## Group I
- [ ] 8.1 `docs/user-guide.md` § "Persisting and refreshing":
- [ ] 8.2 `specs/architecture.md`:
- [ ] 8.3 `docs/roadmap.md`:
- [ ] 8.4 `CLAUDE.md` dense bullets:

## Group J (sequential, gated)
- [ ] 9.0 Gates and AWS smoke (0 CFBD calls):
- [ ] 9.1 Estimate (0 calls)
- [ ] 9.2 Resumable live backfill:
- [ ] 9.3 Idempotence
- [ ] 9.4 Live refresh
- [ ] 9.5 Snapshot AWS → restore local (0 calls):
- [ ] 9.6 Live documentation and reset:

## Review and Verification
- [ ] R.1 Code review
- [ ] V.1 Checklist
- [ ] V.2 Scenario coverage audit
- [ ] V.3 Manual testing and verification-report.md
