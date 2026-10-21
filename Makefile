DS ?= all

.PHONY: docker-recreate build dev debug dev-check course course-push test-benchmark test-units test-integrations config-schema config-doctor test-run-events

docker-recreate:
	docker compose up -d --force-recreate

build:
	uv run langgraph build -t langgraph-python-agent:$(TAG)

dev:
	uv run langgraph dev --host 0.0.0.0 --port 80

# 先体检再启动；任一项失败会阻止启动。
dev-check: config-doctor
	uv run langgraph dev --host 0.0.0.0 --port 80

debug:
	uv run langgraph dev --host 0.0.0.0 --port 80 --debug-port 5678

test-benchmark:
	BENCHMARK_DATASET="$(DS)" uv run pytest -q -s --tb=no tests/benchmarks/test_benchmark.py

test-units:
	uv run pytest -q tests/units

test-integrations:
	MINIO_INTEGRATION=1 DEPLOY_INTEGRATION=1 DEPLOY_SANDBOX_IMAGE=opensandbox-python-node:1.0 uv run pytest -q tests/intergrations

config-schema:
	uv run mentor config schema

config-doctor:
	uv run mentor config doctor

test-run-events:
	RUN_EVENTS_POSTGRES_DSN=$${RUN_EVENTS_DSN:-postgresql://mentor:mentor@127.0.0.1:5433/mentor} uv run pytest -q tests/intergrations/test_run_events_postgres.py
