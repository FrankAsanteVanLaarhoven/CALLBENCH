---
description: Regenerate the stratified task suite from the seed
---

Regenerate every partition deterministically:

```bash
callbench generate --size ${1:-500} --seed ${2:-20260805}
```

Then confirm the suite is intact:

```bash
callbench doctor
wc -l datasets/*/tasks.jsonl
```

Three things to remember:

1. **Generation is deterministic.** The same `--size` and `--seed` produce the
   same suite byte for byte. If a regenerated file differs, a generator change
   is the cause — review it as a change to ground truth, not as noise.
2. **The hidden partition is gitignored on purpose.** It is the contamination
   control. Regenerate it from the seed when you need it; never commit it.
3. **Oracles are computed from the fixture, not asserted.** If you add a task
   family, derive its oracle from the mailbox you just built. A hand-written
   expectation will drift from the simulator the first time either changes.
