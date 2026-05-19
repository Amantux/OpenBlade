.PHONY: dev frontend test lint build

dev:
	uvicorn openblade.api.main:app --reload --port 8000

frontend:
	cd frontend && npm run dev

test:
	pytest -m 'not real_hardware' -x

test-hardware:
	OPENBLADE_BACKEND=real OPENBLADE_REAL_HARDWARE_ENABLED=true pytest -m real_hardware tests/hardware/ -v

lint:
	ruff check . && ruff format --check .

build:
	cd frontend && npm run build

all: lint test build
