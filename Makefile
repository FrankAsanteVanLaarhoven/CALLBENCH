PY ?= ./.venv/bin/python
CALLBENCH ?= ./.venv/bin/callbench
SIZE ?= 2500
SEED ?= 20260805
MODEL ?= reference
VERSION ?= 1.0

.PHONY: help install check lint types test dataset bench bench-all mutate decompose stability conform certify spec artifact replay doctor authorship report clean

help:
	@echo "install    create .venv and install the package with dev extras"
	@echo "check      the whole gate: lint, types, tests"
	@echo "dataset    regenerate every partition from the seed (SIZE=$(SIZE))"
	@echo "bench      run the baselines against the reference planner"
	@echo "bench-all  run the baselines and every ablation"
	@echo "mutate     mutation testing: measure tool generalisation"
	@echo "decompose  attribute results to the architecture or the planner"
	@echo "stability  behavioural replay verification of the simulator"
	@echo "conform    check a backend against the adapter contract (MODEL=...)"
	@echo "replay     check the last run against the current tree"
	@echo "doctor     verify the harness invariants"
	@echo "clean      remove reports and caches"

install:
	python3 -m venv .venv
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -e ".[dev]"

check: authorship lint types test stability spec

authorship:
	python3 scripts/enforce_sole_authorship.py --tree . --skip-identity

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
	$(CALLBENCH) bench --model reference --partitions public adversarial stress

bench-all:
	$(CALLBENCH) bench --model reference --systems all --partitions public adversarial stress

mutate:
	$(CALLBENCH) mutate --limit 250

decompose:
	$(CALLBENCH) decompose --limit 250

stability:
	$(CALLBENCH) stability

conform:
	$(CALLBENCH) conform --model $(MODEL)

replay:
	$(CALLBENCH) replay

spec:
	$(CALLBENCH) spec

certify:
	$(CALLBENCH) certify --model $(MODEL)

artifact:
	VERSION=$(VERSION) CALLBENCH=$(CALLBENCH) ./scripts/build_artifact.sh

doctor:
	$(CALLBENCH) doctor

report:
	@echo "reports/report.html"
	@[ -f reports/report.html ] && open reports/report.html || echo "run make bench first"

clean:
	rm -rf reports/*.json reports/*.html reports/*.jsonl
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	rm -rf .pytest_cache .mypy_cache .ruff_cache
