---
description: Run the full benchmark — baselines, ablations, report
---

Run every baseline and every ablation, then write the ledger:

```bash
callbench bench --model ${1:-reference} --systems all
```

Outputs land in `reports/`: `results.json` (machine-readable, every interval
and every per-case outcome) and `report.html` (the reviewer-facing ledger).

Before quoting any number from this run, check three things:

1. **Is the banner amber?** An amber banner means the deterministic reference
   planner produced these figures. They measure the harness and its ablations,
   not a model. Only a run with `--model claude-opus-5` (or another model id)
   is a model result.
2. **Does the hidden partition agree with the others?** A system that scores
   well on `easy`–`adversarial` and poorly on `hidden` has learned tool names
   rather than tool semantics. That gap is the finding, not an anomaly.
3. **Is the unsafe-action rate zero?** It is reported separately from accuracy
   for a reason. A configuration that gains pass rate while gaining unsafe
   actions has not improved.

The paired McNemar tests compare every system against `callbench_full` over the
same task set. Concordant pairs carry no information and are excluded, which is
why the discordant counts are shown.
