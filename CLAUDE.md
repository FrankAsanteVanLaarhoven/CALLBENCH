# CallBench-Email

Read `docs/METHODOLOGY.md` before changing anything under `src/callbench/datasets/`,
`src/callbench/verification/`, or `src/callbench/metrics/`. Those three define
ground truth, and a change to any of them invalidates previously published
numbers whether or not the tests still pass.

## Non-negotiable

- **No connector may reach a real mailbox.** The executor refuses any tool the
  simulator does not back, and `.claude/hooks/block_real_email.py` blocks
  transport commands before they run. If you find yourself adding a network
  client to this repo, stop.
- **Oracles are computed from fixtures, never asserted.** A hand-written
  expectation drifts from the simulator the first time either changes, and a
  wrong oracle fails correct agents forever.
- **The advisory model judge never decides pass/fail.** It is recorded
  alongside the deterministic layers and excluded from the verdict.
- **A repair may never change** the recipient set, the deletion scope, the
  forwarded content, reply-all membership, or the attachment set. If that is
  the only way to satisfy the violations, the answer is `refuse`.
- **Never repair past a committed side effect.** Once state has moved, the
  trace is final.
- `make check` green before every commit.

## Commands

    make check       # the whole gate: ruff, mypy --strict, pytest
    make dataset     # regenerate every partition from the seed (500 each)
    make bench       # baselines against the reference planner -> reports/
    make bench-all   # baselines and ablations
    make doctor      # harness invariants: determinism, coverage, simulation-only
    make clean       # remove reports/ and __pycache__

`callbench inspect <task_id>` is the debugger. Use it before changing anything:
it prints the analysis, the plan, each gate verdict, every call with its state
diff, and all four verification layers.

## Reading a failure

The question is never "did it pass" — it is *which layer* failed.

| Layer | What a failure means |
|---|---|
| `schema` | the payload was malformed (T02–T04) |
| `execution` | a call raised, usually a reference resolving to nothing (T10) |
| `state_transition` | the wrong resource changed, or an extra one did (T16) |
| `semantic` | the chain or the recipients did not match the oracle |

If a case passes and you expected it to fail, check the oracle before you touch
the agent.

## Gotchas that have already cost time

- **Deferred references must not be schema-validated as literals.** Replacing
  `"$s1.results[0].thread_id"` with a type-appropriate placeholder is required;
  dropping the key instead reports correct deferral as a missing argument.
- **A reply goes to the sender of the thread's *last* message**, which is not
  always the sender named in the prompt. Reply oracles must select a
  thread-final message.
- **Prevented ≠ committed.** A gate-blocked unsafe action must not count
  towards the unsafe-action rate, or a guard that guards scores worse than no
  guard at all. Fabrication rate is the opposite: it counts what was *emitted*,
  repaired or not.
- **The hidden partition paraphrases the object, never the verb.** Rewriting
  the verb too would fold in a second question and make a score drop
  unattributable.

## Adding a task family

1. Write the builder in `src/callbench/datasets/generate.py`. Derive the oracle
   from the store you were handed.
2. Add it to the partition's `Builder` tuple.
3. Run `pytest tests/integration -k solves_the_partition`. If the reference
   planner cannot satisfy your oracle, the oracle is unsatisfiable — fix the
   oracle, not the test.
4. Run `pytest tests/regression`. Generation must stay reproducible.

## Open work

See "What this does not yet establish" in `README.md` and the release gate in
`docs/METHODOLOGY.md`.
