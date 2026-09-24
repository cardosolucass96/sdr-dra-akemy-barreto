PYTHON := .venv/bin/python
PIP := .venv/bin/pip
RUFF := .venv/bin/ruff
PYTEST := .venv/bin/pytest
LANGGRAPH := .venv/bin/langgraph
PRE_COMMIT := .venv/bin/pre-commit
DOCKER_COMPOSE ?= $(shell docker compose version >/dev/null 2>&1 && printf 'docker compose' || printf 'docker-compose')
ENV_FILE ?= .env
COMPOSE_ENV_FILES := $(if $(wildcard $(ENV_FILE)),--env-file $(ENV_FILE))

.PHONY: install env-init settings-key dev local app prod test test-cov quality lint format pre-commit-install compose-up compose-down db-setup openapi golden-dataset-validate golden-dataset-sync

install:
	python3 -m venv .venv
	$(PIP) install -e ".[dev]"

env-init:
	@test ! -e "$(ENV_FILE)" || (echo "$(ENV_FILE) ja existe." >&2; exit 1)
	cp .env.example "$(ENV_FILE)"
	chmod 600 "$(ENV_FILE)"
	@echo "Criado $(ENV_FILE). Preencha somente os segredos de bootstrap."

settings-key:
	$(PYTHON) scripts/generate_settings_admin_key.py

dev:
	./scripts/run_langgraph_dev.sh

local:
	./scripts/run_local_stack.sh

app:
	./scripts/run_app.sh --reload --reload-include '.env*'

prod: compose-up

test:
	$(PYTEST)

test-cov:
	$(PYTEST) --cov=app --cov-report=term-missing

quality:
	@mkdir -p build/quality
	$(RUFF) check .
	$(RUFF) format --check .
	$(PYTHON) -m scripts.quality.check_python_structure
	COVERAGE_FILE=build/quality/.coverage $(PYTEST) --cov=app --cov-branch --cov-report=term-missing --cov-report=xml:build/quality/coverage.xml
	$(PYTHON) -m scripts.quality.check_diff_coverage

lint:
	$(RUFF) check .

format:
	$(RUFF) format .

pre-commit-install:
	$(PRE_COMMIT) install

compose-up:
	COMPOSE_ENV_FILE=$(ENV_FILE) $(DOCKER_COMPOSE) $(COMPOSE_ENV_FILES) up --build -d

compose-down:
	$(DOCKER_COMPOSE) down --remove-orphans

db-setup:
	$(PYTHON) scripts/bootstrap_postgres_checkpointer.py --env-file $(ENV_FILE)

openapi:
	$(PYTHON) scripts/export_openapi.py

golden-dataset-validate:
	$(PYTHON) scripts/validate_golden_dataset.py

golden-dataset-sync:
	$(PYTHON) scripts/sync_langfuse_golden_dataset.py
