#!/usr/bin/env bash
# Build the artifact-evaluation package.
#
# Everything a reviewer needs to reproduce the reported numbers, and nothing
# that would let them reproduce them *accidentally*: the hidden split is
# excluded by design and regenerated from the seed.
set -euo pipefail

VERSION="${VERSION:-1.0}"
OUT="${OUT:-dist}"
STAGE="$OUT/callbench-v$VERSION-artifact"
CALLBENCH="${CALLBENCH:-./.venv/bin/callbench}"

rm -rf "$STAGE" && mkdir -p "$STAGE"

echo "==> verifying the tree before packaging"
$CALLBENCH doctor >/dev/null
$CALLBENCH spec >/dev/null
$CALLBENCH stability >/dev/null

echo "==> staging"
mkdir -p "$STAGE"/{src,tests,docs,datasets,reports,scripts,mcp_server}
cp -R src/callbench "$STAGE/src/"
cp -R tests/. "$STAGE/tests/"
cp -R mcp_server/. "$STAGE/mcp_server/"
cp -R docs/. "$STAGE/docs/"
cp -R scripts/. "$STAGE/scripts/"
cp README.md GOVERNANCE.md LICENSE Makefile pyproject.toml .env.example .gitignore "$STAGE/"

# Committed splits only. `hidden` is the contamination control and is
# regenerated from the seed by the reviewer, never shipped.
for split in public validation adversarial stress; do
  mkdir -p "$STAGE/datasets/$split"
  cp "datasets/$split/tasks.jsonl" "$STAGE/datasets/$split/"
done

for f in results.json mutations.json decomposition.json report.html; do
  if [ -f "reports/$f" ]; then cp "reports/$f" "$STAGE/reports/"; fi
done

cat > "$STAGE/REPRODUCE.md" <<'MD'
# Reproducing CallBench v1.0

Everything below runs offline. No credentials, no network.

```bash
python3 -m venv .venv && ./.venv/bin/pip install -e ".[dev]"

./.venv/bin/callbench doctor      # harness invariants
./.venv/bin/callbench spec        # this tree implements the frozen v1.0 spec
./.venv/bin/callbench stability   # BSI = 100 against the committed baseline
./.venv/bin/python -m pytest      # full test suite

# The hidden split is NOT shipped. Regenerate it from the seed:
./.venv/bin/callbench generate --size 2500 --seed 20260805 --partitions hidden

./.venv/bin/callbench bench --model reference --systems all \
    --partitions public validation adversarial stress
./.venv/bin/callbench decompose
./.venv/bin/callbench mutate
```

## Checking that you reproduced *this* run

```bash
./.venv/bin/callbench replay      # component hashes vs the recorded run
```

A clean `replay` means every component that decides a number matches. A drift
report names which one moved, so a mismatch is a diff rather than an argument.

## What the numbers are, and are not

Reference-planner results measure the **evaluation architecture**, not a
language model. Every report labels them `SYNTHETIC PLANNER`. There are no
cross-model results in v1.0; the adapter contract (`callbench conform`) and the
certification gate (`callbench certify`) exist so that adding one is bounded
work rather than research.
MD

echo "==> checksums"
( cd "$STAGE" && find . -type f -not -name SHA256SUMS -print0 \
    | sort -z | xargs -0 shasum -a 256 > SHA256SUMS )

echo "==> archiving"
tar -czf "$OUT/callbench-v$VERSION-artifact.tar.gz" -C "$OUT" "callbench-v$VERSION-artifact"
echo "artifact: $OUT/callbench-v$VERSION-artifact.tar.gz"
du -h "$OUT/callbench-v$VERSION-artifact.tar.gz"
