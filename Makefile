.PHONY: help install fmt lint type test test-int audit modules-check modules-audit migrate revision run worker compose-up compose-down observability-up observability-down prod-up prod-up-tls prod-up-observability prod-down prod-logs prod-migrate prod-bootstrap prod-create-superuser prod-seed-finance prod-backup prod-ps loadtest-baseline loadtest-stress loadtest-soak readiness clean

help:
	@echo "install        install dev deps"
	@echo "fmt            format code (ruff)"
	@echo "lint           lint (ruff)"
	@echo "type           type-check (mypy strict)"
	@echo "test           unit tests (skips integration)"
	@echo "test-int       full test suite incl. testcontainers"
	@echo "audit          bandit + pip-audit"
	@echo "modules-check  7-contract rule (strict, grandfather-aware); fails CI on new gaps"
	@echo "modules-audit  matrix of all modules vs 7 contracts (no fail)"
	@echo "migrate        run alembic upgrade head"
	@echo "revision       m=<msg> alembic autogenerate revision"
	@echo "run            run API locally (uvicorn reload)"
	@echo "worker         run ARQ worker locally"
	@echo "compose-up     dev: docker compose up --build"
	@echo "compose-down   dev: docker compose down -v"
	@echo "observability-up    dev + Jaeger + Prometheus + Grafana (UIs at :16686, :9090, :3001)"
	@echo "observability-down  tear down dev + observability stack (volumes preserved)"
	@echo ""
	@echo "--- production (single-host docker) ---"
	@echo "prod-up        prod stack up (postgres+redis+migrate+api+worker+pg_backup)"
	@echo "prod-up-tls    prod stack + Caddy TLS terminator (requires API_DOMAIN, ACME_EMAIL)"
	@echo "prod-up-observability  prod + TLS + Jaeger/Prometheus/Grafana (basic-auth, separate FQDN)"
	@echo "prod-down      prod stack down (volumes preserved)"
	@echo "prod-ps        list prod containers + health"
	@echo "prod-logs      follow api+worker prod logs"
	@echo "prod-migrate            re-run alembic upgrade head against prod DB"
	@echo "prod-bootstrap          re-sync IAM roles + permissions (idempotent)"
	@echo "prod-create-superuser   email=admin@example.com  (interactive password)"
	@echo "prod-backup             trigger an immediate pg_dump (in addition to nightly cron)"
	@echo ""
	@echo "--- load testing (k6) ---"
	@echo "loadtest-baseline       100 VUs for 5 min — SLO-gated; fails if p95>500ms or err>1%"
	@echo "loadtest-stress         ramp 50→500 VUs to find the breaking point (no thresholds)"
	@echo "loadtest-soak           50 VUs for 30 min — catches memory leaks + outbox backlog"
	@echo "                         all accept: API=https://api.example.com EMAIL=u@x.com PASSWORD=..."
	@echo ""
	@echo "--- Module 35 production readiness ---"
	@echo "readiness               run auto-checkable subset of docs/PRODUCTION_READINESS.md gates"
	@echo "                          (pytest + Vitest + rollback runbook check + optional smoke + manual checklist)"
	@echo "                          Optional env: API_BASE_URL, FRONTEND_DIR, SMOKE_*, READINESS_*"

install:
	pip install -e ".[dev]"

fmt:
	ruff format app tests
	ruff check --fix app tests

lint:
	ruff check app tests
	ruff format --check app tests

type:
	mypy app

test:
	pytest -m "not integration"

test-int:
	pytest

# Run the full suite against an EXTERNAL Postgres + Redis you already have
# running locally. Set HYPERSHOP_TEST_DATABASE_URL and HYPERSHOP_TEST_REDIS_URL
# in your shell, or use scripts/run_tests_local.ps1 / scripts/run_tests_local.sh.
test-local:
	pytest

audit:
	bandit -q -r app
	pip-audit --strict

# Module governance — enforce the 7-contract rule. Non-grandfathered
# modules must score 7/7. New modules added without 7/7 fail CI. See
# scripts/MODULE_GOVERNANCE.md for the workflow.
modules-check:
	python -m scripts.check_module_contracts --strict --skip-grandfather

# Audit-only matrix (no exit-1) — useful for "where are the gaps today".
modules-audit:
	python -m scripts.check_module_contracts

migrate:
	alembic upgrade head

revision:
	@if [ -z "$(m)" ]; then echo "usage: make revision m=\"message\""; exit 2; fi
	alembic revision --autogenerate -m "$(m)"

run:
	uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

worker:
	arq app.worker.WorkerSettings

compose-up:
	docker compose up --build

compose-down:
	docker compose down -v

# ---------------- observability overlay ----------------
# Brings up base dev stack + Jaeger (16686) + Prometheus (9090) + Grafana
# (3001, admin/admin). Auto-injects OTEL_EXPORTER_OTLP_ENDPOINT on api +
# worker. See docs/OBSERVABILITY_TRACING.md for details.
OBS_COMPOSE := docker compose -f docker-compose.yml -f docker-compose.observability.yml

observability-up:
	$(OBS_COMPOSE) up --build

observability-down:
	$(OBS_COMPOSE) down

# ---------------- production single-host ----------------
PROD_COMPOSE := docker compose -f docker-compose.prod.yml
PROD_TLS_COMPOSE := docker compose -f docker-compose.prod.yml -f docker-compose.tls.yml
PROD_OBS_COMPOSE := docker compose -f docker-compose.prod.yml -f docker-compose.tls.yml -f docker-compose.observability.prod.yml

prod-up:
	$(PROD_COMPOSE) up -d --build

prod-up-tls:
	$(PROD_TLS_COMPOSE) up -d --build

# Production stack + observability (Jaeger + Prometheus + Grafana
# behind Caddy basic auth on a separate FQDN). See
# docker-compose.observability.prod.yml for required env vars.
prod-up-observability:
	$(PROD_OBS_COMPOSE) up -d --build

prod-down:
	$(PROD_COMPOSE) down

prod-ps:
	$(PROD_COMPOSE) ps

prod-logs:
	$(PROD_COMPOSE) logs -f --tail=200 api worker

prod-migrate:
	$(PROD_COMPOSE) run --rm migrate

prod-bootstrap:
	$(PROD_COMPOSE) run --rm bootstrap

prod-create-superuser:
	@if [ -z "$(email)" ]; then echo "usage: make prod-create-superuser email=admin@example.com"; exit 2; fi
	$(PROD_COMPOSE) run --rm -it api python -m app.cli create-superuser --email "$(email)"

prod-backup:
	$(PROD_COMPOSE) exec pg_backup /usr/local/bin/backup.sh

# ---------------- load testing ----------------
# Default API to localhost so `make loadtest-baseline` works against
# the Codespaces-forwarded port. Override with API=... for production
# hosts. EMAIL/PASSWORD only needed for the auth-using paths in baseline.
LOADTEST_API ?= http://localhost:8000
LOADTEST_EMAIL ?= ci-admin@hypershop.local
LOADTEST_PASSWORD ?=

loadtest-baseline:
	@mkdir -p loadtest/results
	k6 run loadtest/k6-baseline.js \
		-e API=$(LOADTEST_API) \
		-e EMAIL=$(LOADTEST_EMAIL) \
		-e PASSWORD=$(LOADTEST_PASSWORD)

loadtest-stress:
	@mkdir -p loadtest/results
	k6 run loadtest/k6-stress.js -e API=$(LOADTEST_API)

loadtest-soak:
	@mkdir -p loadtest/results
	k6 run loadtest/k6-soak.js -e API=$(LOADTEST_API)

readiness:
	@bash scripts/run_readiness.sh

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov coverage.xml dist build *.egg-info
