---
description: Run one benchmark case and print its full decision trail
---

Run a single case end to end and show every stage: analysis, plan, gate
verdict, each executed call with its state diff, and all four verification
layers.

```bash
callbench inspect $1 --system ${2:-callbench_full} --model ${3:-reference}
```

Read the output top to bottom. The question is never just "did it pass" — it is
*which layer* failed:

- **schema** failed → the payload was malformed (T02–T04).
- **execution** failed → a call raised, usually a reference that resolved to
  nothing (T10).
- **state_transition** failed → the wrong resource changed, or an extra one did
  (T16). This is the layer that catches a right answer reached the wrong way.
- **semantic** failed → the chain or the recipients did not match the oracle.

If the case passed but you expected it to fail, check the oracle before
changing the agent: an oracle that is wrong will fail correct agents forever.
