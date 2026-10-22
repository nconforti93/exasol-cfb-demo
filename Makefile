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

TARGET ?= local
export TARGET

ROOT := $(CURDIR)
RUN  := cd "$(ROOT)" && uv run
PY   := $(RUN) python

PATTERNS ?=
CONFIRM  ?=
ARGS     ?=

.PHONY: generate bundle test test-it test-network lint coverage deploy grants dns-check record db-check

db-check:
	@$(PY) -m scripts.dbcheck

generate:
	$(PY) -m vs.generate

bundle:
	$(PY) -m vs.bundle

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

# Unit + CLI tests only. Each of the two runtime packages must reach 90 % (fail_under in pyproject.toml).
coverage:
	$(RUN) pytest --cov --cov-report= tests/unit tests/cli
	$(RUN) coverage report --include="vs/cfbd_vs/*"
	$(RUN) coverage report --include="vs/gen/*"

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
	$(PY) -m scripts.record --patterns "$(PATTERNS)" --confirm "$(CONFIRM)"
