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

TARGET ?= local
export TARGET

ROOT := $(CURDIR)
RUN  := cd "$(ROOT)" && uv run
PY   := $(RUN) python

PATTERNS ?=
CONFIRM  ?=
ARGS     ?=

.PHONY: generate bundle test test-it test-network lint coverage deploy grants dns-check record db-check \
        app-deps app-build app-test app-lint \
        app app-serve app-user app-e2e app-perf

db-check:
	@$(PY) -m scripts.dbcheck

generate:
	$(PY) -m vs.generate

bundle:
	$(PY) -m vs.bundle

test:
	$(RUN) pytest tests/unit tests/cli
	@$(MAKE) --no-print-directory app-test

test-it: db-check
	$(RUN) pytest tests/it -m "it and not network and not e2e"

test-network: db-check
	$(RUN) pytest tests/it -m network

lint:
	$(RUN) ruff check .
	$(RUN) ruff format --check .
	$(RUN) mypy
	@$(MAKE) --no-print-directory app-lint

# Unit + CLI tests only. Each of the two runtime packages must reach 90 % (fail_under in pyproject.toml).
coverage:
	$(RUN) pytest --cov --cov-report= tests/unit tests/cli
	$(RUN) coverage report --include="vs/cfbd_vs/*"
	$(RUN) coverage report --include="vs/gen/*"
	$(RUN) coverage report --include="$(APP_PURE_MODULES)"

# `ARGS` passes options through, e.g. make deploy ARGS="--prefix PROBE_ --no-sync-api-features".
# `--prefix` deploys that namespace as sys (not TEST_, which the integration harness owns) and is
# refused together with the real connection.
# scripts.deploy rebuilds the bundle itself, so a direct `python -m scripts.deploy` is also safe.
deploy: db-check bundle
	$(PY) -m scripts.deploy $(ARGS)

grants: db-check
	$(PY) -m scripts.grants $(ARGS)

dns-check: db-check
	$(PY) -m scripts.dns

record: db-check
	$(PY) -m scripts.record --patterns "$(PATTERNS)" --confirm "$(CONFIRM)" $(ARGS)

# Frontend (app/frontend: Node >= 22.12, exact pins, package-lock.json is the source of truth).
# app-deps runs `npm ci` only when package.json or the lockfile changed; on a Windows drive it installs
# into a native directory and links node_modules (see app/frontend/scripts/deps.sh).
FRONTEND := $(ROOT)/app/frontend

app-deps:
	@"$(FRONTEND)/scripts/deps.sh"

app-build: app-deps
	cd "$(FRONTEND)" && npm run build

app-test: app-deps
	cd "$(FRONTEND)" && npm test

app-lint: app-deps
	cd "$(FRONTEND)" && npm run lint && npm run typecheck

# Backend (app/backend). `ROTATE=yes` sets a new password on existing app users (make app-user).
# make app          dev: uvicorn --reload on 127.0.0.1:8000 plus Vite on 127.0.0.1:5173 (no live refresh)
# make app-serve    one process serving the API and app/frontend/dist (TARGET=aws works)
# make app-e2e      browser smoke against the TEST_ stack; run it under the IT lock (flock <lock> make app-e2e)
# make app-perf     times the fixed catalog of read endpoints, never a live endpoint
ROTATE ?=
APP_PURE_MODULES := app/backend/settings.py,app/backend/security.py,app/backend/spend.py,app/backend/sessions.py,app/backend/sqlcatalog.py,app/backend/explain.py,app/backend/console_catalog.py

app: db-check app-deps
	$(PY) -m scripts.app dev

app-serve: db-check
	$(PY) -m scripts.app serve

app-user: db-check
	$(PY) -m scripts.app user $(if $(filter yes,$(ROTATE)),--rotate) $(ARGS)

app-e2e: db-check app-build
	$(RUN) pytest tests/it -m e2e

app-perf: db-check
	$(PY) -m scripts.app perf
