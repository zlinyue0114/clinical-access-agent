.PHONY: check lint typecheck test test-integration fhir-up fhir-down data data-sample load \
	dev-keys gateway

check: lint typecheck test

lint:
	uv run ruff check .
	uv run ruff format --check .

typecheck:
	uv run mypy

test:
	uv run pytest -q

fhir-up:
	docker compose up -d fhir

fhir-down:
	docker compose down

data:
	uv run python -m clinical_access_agent.data.generate --seed 7 --out data/generated

data-sample:
	uv run python -m clinical_access_agent.data.generate --seed 7 --patients 12 --out data/sample

load:
	uv run python -m clinical_access_agent.data.load --bundle data/generated/bundle.json --fhir http://127.0.0.1:8090/fhir

test-integration:
	uv run pytest -q -m integration

dev-keys:
	uv run caa-token keygen --dir .secrets

gateway:
	uv run caa-gateway
