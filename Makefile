SHELL := /bin/bash
.ONESHELL:
.PHONY: up down logs emulator-up emulator-down emulator-logs emulator-config emulator-ps fleet-up fleet-down fleet-logs fleet-ps fleet-config fleet-build up-standalone test test-unit test-integration seed-libraries seed-tapes seed-all clean build-frontend dev-backend dev-frontend dev frontend lint build all test-hardware

# Start all services
up:
	docker compose up -d

# Stop all services

down:
	docker compose down

# View logs
logs:
	docker compose logs -f

# Start standalone emulator services from local Docker build assets
emulator-up:
	./scripts/emulator/run_standalone_stack.sh up

emulator-down:
	./scripts/emulator/run_standalone_stack.sh down

emulator-logs:
	./scripts/emulator/run_standalone_stack.sh logs

emulator-config:
	./scripts/emulator/run_standalone_stack.sh config

emulator-ps:
	./scripts/emulator/run_standalone_stack.sh ps

# Fleet stack: OpenBlade API/web + standalone emulators
fleet-up:
	./scripts/emulator/run_fleet_stack.sh up

fleet-down:
	./scripts/emulator/run_fleet_stack.sh down

fleet-logs:
	./scripts/emulator/run_fleet_stack.sh logs

fleet-ps:
	./scripts/emulator/run_fleet_stack.sh ps

fleet-config:
	./scripts/emulator/run_fleet_stack.sh config

fleet-build:
	./scripts/emulator/run_fleet_stack.sh build

# Backward-compatible alias
up-standalone: fleet-up

# Run all tests (excluding hardware)
test:
	python3 -m pytest tests/unit/ tests/safety/ tests/integration/ -q --tb=short

# Run unit tests only (faster)
test-unit:
	python3 -m pytest tests/unit/ tests/safety/ -q --tb=short

# Run integration tests
test-integration:
	python3 -m pytest tests/integration/ -q --tb=short

# Seed default library instances (library-1, library-2, library-3)
seed-libraries:
	python3 -c $$'from openblade.catalog.db import get_session, init_db\nfrom openblade.catalog.repository import CatalogRepository\nfrom openblade.config import load_config\n\ndb_url = load_config().db_url\ninit_db(db_url)\nrepo = CatalogRepository(get_session())\nfor name, url in [("library-1", "http://localhost:8010"), ("library-2", "http://localhost:8011"), ("library-3", "http://localhost:8012")]:\n    existing = repo.get_library_instance_by_name(name)\n    if existing:\n        print(f"Skipped {name} (already exists)")\n        continue\n    repo.create_library_instance(name=name, emulator_url=url, model="Scalar i3")\n    print(f"Created {name}")'

# Seed demo tapes into the default AML state
seed-tapes:
	python3 -c $$'from openblade.api import aml_state\nfrom openblade.config import load_config\n\ndb_url = load_config().db_url\naml_state.ensure_initialized(db_url, force_reset=True)\nprint("AML state initialized with", len(aml_state.list_aml_media()), "media items")'

# Seed everything
seed-all: seed-libraries seed-tapes

# Clean build artifacts and test cache
clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name '*.pyc' -delete 2>/dev/null || true
	rm -rf .pytest_cache frontend/dist frontend/node_modules/.vite

# Build frontend
build-frontend:
	cd frontend && npm run build

# Dev: start backend only
dev-backend:
	python3 -m uvicorn openblade.api.main:app --reload --host 0.0.0.0 --port 8000

# Dev: start frontend only
dev-frontend:
	cd frontend && npm run dev

dev: dev-backend
frontend: dev-frontend

lint:
	ruff check . && ruff format --check .

build: build-frontend

all: lint test build

test-hardware:
	OPENBLADE_BACKEND=real OPENBLADE_REAL_HARDWARE_ENABLED=true python3 -m pytest tests/hardware/ -v
