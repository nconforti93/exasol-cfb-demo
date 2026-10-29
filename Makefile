# Run from the repo root. The repo path contains spaces (OneDrive), so every path is quoted.
# If a target breaks on the path, pass it explicitly: make CURDIR="$PWD" <target>
#
# Database targets first check that the selected Exasol Personal deployment is reachable.
#   TARGET=local|aws   which deployment (default local; aws is ~/.exasol/personal/deployments/default).
#                      Host, port, password and, for aws, the pinned certificate fingerprint come from
#                      that deployment's files (vs/host/target.py). EXASOL_DSN in the environment or
#                      .env overrides host and port. Example: make deploy TARGET=aws
#   test-it            integration tests against the local DB, mock CFBD, no network (TARGET=local only)
#   test-network       only the `network`-marked test: DoH end to end from the UDF sandbox to the real
#                      DoH endpoint and a TLS handshake with api.collegefootballdata.com (no HTTP
#                      request, 0 CFBD calls). Needs HTTPS egress; never skips.
#   record             live CFBD calls through the fetch UDF, capped by the ledger cache/live-calls.jsonl
#                      (scripts/record.py). Without CONFIRM=yes it only prints the plan. ARGS="--ledger"
#                      prints the totals, ARGS="--info" logs one free /info reading.
#   backfill           PROFILE=demo|full [FROM=year] [MAX_CALLS=n] [CONFIRM=yes]: prints the estimate and
#                      the quota, then (live mode, CONFIRM=yes, ledger check) runs CFB_INFRA.BACKFILL.
#   refresh            SEASON=y WEEK=w [SEASON_TYPE=postseason] [CONFIRM=yes]: the same for REFRESH_WEEK.
#   quota              prints CFB_META.QUOTA (reads /info, 0 calls) and the ledger room.
#                      All three are scripts/loader_cli.py. LEDGER=off turns the ledger off;
#                      MAX_CALLS defaults to the ledger room. MAX_CALLS=0 is a dry run.
#   snapshot           NAME=n: copies CFB_CORE, CFB_META.LOAD_LOG and FETCH_LOG of the target to the
#                      private ~/.cfb-demo/snapshots/<NAME>/ (CFB_SNAPSHOT_DIR overrides; never inside
#                      the repo or a OneDrive folder; an existing NAME is refused). 0 CFBD calls.
#   restore            NAME=n [CONFIRM=yes] [FORCE=yes]: without CONFIRM it checks the snapshot against
#                      the target and stops; with it, replaces those tables in one transaction (also
#                      the rehearsal reset). FORCE=yes accepts a snapshot of another catalog. 0 CFBD calls.
#   snapshots          lists the snapshots. All three are scripts/snapshot.py; snapshot and restore
#                      take the loader's run lock.
#   model              lints sql/model/, then installs CFB_MODEL in one transaction under the loader's
#                      run lock (kind DEPLOY; refused while a loader run, snapshot or restore holds it).
#                      ARGS="--prefix TEST_" deploys another namespace. `make deploy` runs it last.
#   model-check        SEASON=y WEEK=w [SEASON_TYPE=postseason]: prints MODEL_CHECKS for that point
#                      (exit 1 when an ERROR check failed). No log, 0 calls.
#   model-refresh      [SEASON=y WEEK=w [SEASON_TYPE=postseason]]: runs CFB_INFRA.REFRESH_MODEL on its
#                      own (run lock kind MODEL): rebuilds the derived tables of CFB_MODEL, then logs
#                      the checks of that point (without SEASON: rebuild only). REFRESH_WEEK runs it as
#                      step 100; backfill and restore rebuild after their run. 0 calls.
#   model-docs         regenerates docs/model-dictionary.md from sql/model/ (no database).
#   model-perf         times the model on the scaled league (ARGS="--prefix PERF_"); task 3.7.
#                      All five are scripts/model.py.
#   sim                lints sql/sim/, then installs CFB_SIM and the engine script CFB_INFRA.SIM_ENGINE in
#                      one transaction under the loader's run lock (kind DEPLOY; refused while a loader
#                      run, snapshot or restore holds it). Data tables are created when missing and
#                      never replaced, so a redeploy keeps every run. ARGS="--prefix TEST_" deploys
#                      another namespace; ARGS="--lint-only" lints and builds without a connection.
#                      scripts/sim.py; 0 CFBD calls.
#   sim-fit            fits the score model's constants on the stored 2014-2025 games and SP+ ratings of
#                      the target (read-only, 0 CFBD calls, never the virtual schema), prints them with
#                      the fit quality and rewrites the seed sql/sim/010_sim_params.sql. ARGS="--dry-run"
#                      writes nothing. Review the diff, commit it, then `make sim` deploys it.
#   simulate           SEASON=y WEEK=w [SIMS=n] [BATCH=n] [SEED=n] [LABEL=text] [SEASON_TYPE=postseason]:
#                      EXECUTE SCRIPT CFB_INFRA.SIMULATE (run lock kind SIMULATE): snapshots the inputs
#                      of the point, simulates the rest of the season in the database and stores the
#                      odds and outcome strings; prints the run's seconds and the top 25 odds.
#                      Defaults: 100,000 seasons in batches of 5,000. 0 CFBD calls.
#   what-if            TEAM=school|id [RUN_ID=id] WIN=next|game_id[,..] [LOSE=..]: the team's playoff odds
#                      now and in the stored seasons where it wins (loses) those games, the matching
#                      seasons and the query seconds; RUN_ID defaults to the newest DONE run. Read-only.
#   predict            SEASON=y WEEK=w [SEASON_TYPE=regular|postseason]: freezes the score model's
#                      next-week games through the point, marking missed games reconstructed. 0 calls.

TARGET ?= local
export TARGET

ROOT := $(CURDIR)
RUN  := cd "$(ROOT)" && uv run
PY   := $(RUN) python

PATTERNS ?=
CONFIRM  ?=
FORCE    ?=
NAME     ?=
ARGS     ?=

.PHONY: generate bundle test test-it test-network lint coverage deploy grants dns-check record db-check
.PHONY: backfill refresh quota snapshot restore snapshots model model-check model-refresh model-docs
.PHONY: model-perf api api-docs sim sim-fit simulate what-if predict

db-check:
	@$(PY) -m scripts.dbcheck

generate:
	$(PY) -m vs.generate

bundle:
	$(PY) -m vs.bundle
	$(PY) -m loader.bundle
	$(PY) -m model.step

test:
	$(RUN) pytest tests/unit tests/cli

test-it: db-check
	$(RUN) pytest tests/it -m "it and not network"

test-network: db-check
	$(RUN) pytest tests/it -m network

lint:
	$(RUN) ruff check .
	$(RUN) ruff format --check .
	$(RUN) mypy

# Unit + CLI tests only. Each of the three runtime packages (vs/cfbd_vs, vs/gen, loader) must reach 90 % (fail_under in pyproject.toml).
coverage:
	$(RUN) pytest --cov --cov-report= tests/unit tests/cli
	$(RUN) coverage report --include="vs/cfbd_vs/*"
	$(RUN) coverage report --include="vs/gen/*"
	$(RUN) coverage report --include="loader/*"

# Loader settings: loader/load-plan.yaml, then the target's entry in vs/live/loader-config.yaml
# (aws: the ledger's month cap and a reserve of 50, so a plain redeploy keeps the AWS budget), then
# `LOADER_CONFIG="k=v,..."`, which wins per setting. All last until the next deploy.
# `make deploy` also deploys the loader (loader/deploy.py), under the DEPLOY run lock once the
# namespace has RUN_LOCK.
# `ARGS` passes options through, e.g. make deploy ARGS="--prefix PROBE_ --no-sync-api-features".
# `--prefix` deploys that namespace as sys (not TEST_, which the integration harness owns) and is
# refused together with the real connection.
# scripts.deploy rebuilds the bundle itself, so a direct `python -m scripts.deploy` is also safe.
deploy: db-check bundle
	$(PY) -m scripts.deploy $(if $(LOADER_CONFIG),--loader-config "$(LOADER_CONFIG)") $(ARGS)

grants: db-check
	$(PY) -m scripts.grants $(ARGS)

dns-check: db-check
	$(PY) -m scripts.dns

record: db-check
	$(PY) -m scripts.record --patterns "$(PATTERNS)" --confirm "$(CONFIRM)" $(ARGS)

backfill: db-check
	$(PY) -m scripts.loader_cli --confirm "$(CONFIRM)" backfill --profile "$(or $(PROFILE),demo)" $(if $(FROM),--from-season "$(FROM)") $(if $(MAX_CALLS),--max-calls "$(MAX_CALLS)")

refresh: db-check
	$(PY) -m scripts.loader_cli --confirm "$(CONFIRM)" refresh $(if $(SEASON),--season "$(SEASON)") $(if $(WEEK),--week "$(WEEK)") $(if $(SEASON_TYPE),--season-type "$(SEASON_TYPE)")

quota: db-check
	$(PY) -m scripts.loader_cli quota

snapshot: db-check
	$(PY) -m scripts.snapshot snapshot --name "$(NAME)"

restore: db-check
	$(PY) -m scripts.snapshot restore --name "$(NAME)" --confirm "$(CONFIRM)" --force "$(FORCE)"

snapshots:
	$(PY) -m scripts.snapshot list

model: db-check
	$(PY) -m scripts.model deploy $(ARGS)

api: db-check
	$(PY) -m scripts.api $(ARGS)

api-docs:
	$(PY) -m scripts.api docs

model-check: db-check
	$(PY) -m scripts.model check --season "$(SEASON)" --week "$(WEEK)" $(if $(SEASON_TYPE),--season-type "$(SEASON_TYPE)") $(ARGS)

model-refresh: db-check
	$(PY) -m scripts.model refresh $(if $(SEASON),--season "$(SEASON)") $(if $(WEEK),--week "$(WEEK)") $(if $(SEASON_TYPE),--season-type "$(SEASON_TYPE)") $(ARGS)

model-docs:
	$(PY) -m scripts.model docs

model-perf: db-check
	$(PY) -m scripts.model perf $(ARGS)

sim: db-check
	$(PY) -m scripts.sim deploy $(ARGS)

sim-fit: db-check
	$(PY) -m scripts.sim fit $(ARGS)

simulate: db-check
	$(PY) -m scripts.sim simulate --season "$(SEASON)" --week "$(WEEK)" $(if $(SEASON_TYPE),--season-type "$(SEASON_TYPE)") $(if $(SIMS),--sims "$(SIMS)") $(if $(BATCH),--batch "$(BATCH)") $(if $(SEED),--seed "$(SEED)") $(if $(LABEL),--label "$(LABEL)") $(ARGS)

what-if: db-check
	$(PY) -m scripts.sim what-if --team "$(TEAM)" $(if $(RUN_ID),--run "$(RUN_ID)") $(if $(WIN),--win "$(WIN)") $(if $(LOSE),--lose "$(LOSE)") $(ARGS)

predict: db-check
	$(PY) -m scripts.sim predict --season "$(SEASON)" --week "$(WEEK)" $(if $(SEASON_TYPE),--season-type "$(SEASON_TYPE)") $(ARGS)
