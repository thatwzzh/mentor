DS ?= all

.PHONY: docker-recreate build dev debug course course-push test-benchmark

docker-recreate:
	docker compose up -d --force-recreate

build:
	uv run langgraph build -t langgraph-python-agent:$(TAG)

dev:
	uv run langgraph dev --host 0.0.0.0 --port 80

debug:
	uv run langgraph dev --host 0.0.0.0 --port 80 --debug-port 5678

test-benchmark:
	BENCHMARK_DATASET="$(DS)" uv run pytest -q -s --tb=no tests/benchmarks/test_benchmark.py
