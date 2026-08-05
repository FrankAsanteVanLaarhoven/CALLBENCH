PY ?= ./.venv/bin/python
CALLBENCH ?= ./.venv/bin/callbench
SIZE ?= 500
SEED ?= 20260805

.PHONY: help install check lint types test dataset bench bench-all doctor report clean

help:
	@echo "install    create .venv and install the package with dev extras"
	@echo "check      the whole gate: lint, types, tests"
	@echo "dataset    regenerate every partition from the seed (SIZE=$(SIZE))"
	@echo "bench      run the baselines against the reference planner"
	@echo "bench-all  run the baselines and every ablation"
	@echo "doctor     verify the harness invariants"
	@echo "clean      remove reports and caches"

install:
	python3 -m venv .venv
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -e ".[dev]"

check: lint types test

lint:
	$(PY) -m ruff check src tests mcp_server

types:
	$(PY) -m mypy

test:
	$(PY) -m pytest

dataset:
	$(CALLBENCH) generate --size $(SIZE) --seed $(SEED)

# The hidden partition is the contamination control: regenerate it on demand,
# never commit it. `make dataset` writes it locally; .gitignore keeps it out.
bench:
	$(CALLBENCH) bench --model reference --partitions easy medium hard adversarial

bench-all:
	$(CALLBENCH) bench --model reference --systems all --partitions easy medium hard adversarial

doctor:
	$(CALLBENCH) doctor

report:
	@echo "reports/report.html"
	@[ -f reports/report.html ] && open reports/report.html || echo "run make bench first"

clean:
	rm -rf reports/*.json reports/*.html reports/*.jsonl
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	rm -rf .pytest_cache .mypy_cache .ruff_cache
