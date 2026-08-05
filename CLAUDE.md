# Repository Authorship and Branding Policy

## Ownership

This repository and all project outputs are owned and authored by Frank Van
Laarhoven.

AI systems are development tools only. They are not authors, co-authors,
contributors, maintainers, copyright holders, or owners of any part of this
project.

## Commit Policy

1. Do not add any AI attribution or co-authorship trailer.
2. Do not add co-authorship trailers naming an assistant, generation or
   assistance metadata trailers, "AI-generated" markers, or similar
   attribution language. The exhaustive list of prohibited tokens is in
   `docs/AUTHORSHIP.md`, which is the one file permitted to contain them.
3. Do not mention an AI provider or coding assistant in commit subjects or
   commit bodies.
4. Use only the configured human Git author identity:
   `Frank Van Laarhoven <frankleroyvan@gmail.com>`.
5. Before committing, inspect the complete commit message and remove
   prohibited attribution.
6. Before pushing, inspect recent commits for prohibited trailers or branding.
7. Never amend, rewrite, or force-push published history without explicit
   approval from Frank Van Laarhoven.

## Codebase Branding Policy

Do not introduce AI-assistant branding into source-code comments, file headers,
documentation, README files, package metadata, application banners, CLI output,
UI labels, badges, generated reports, test fixtures, repository descriptions,
changelogs, release notes, branch names, or commit messages.

## Permitted Technical References

Provider and model names are permitted only where technically necessary: API
integration code, dependency names, provider adapters, environment variables,
model identifiers, compatibility documentation, benchmark labels, factual
citations, licence compliance, and user-facing provider selection where it is a
product feature. They must never imply that a provider or AI system owns,
authors, maintains, or contributes to the repository.

## Required Pre-Commit Checks

    python3 scripts/check_authorship_policy.py     # staged files, message, identity
    bash scripts/check_git_history.sh              # before pushing

Both are wired: `.githooks/pre-commit`, `.githooks/commit-msg`, the
`Authorship Policy` CI workflow, and `make check`. Full text and the historical
exception: `docs/AUTHORSHIP.md`.

---

# CallBench

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
    make dataset     # regenerate every split from the seed (2500 each)
    make bench       # baselines against the reference planner -> reports/
    make bench-all   # baselines and ablations
    make mutate      # tool generalisation under catalogue mutation
    make decompose   # attribute results to the architecture or the planner
    make stability   # behavioural replay verification (part of `make check`)
    make conform     # check a backend against the adapter contract
    make replay      # check the last run against the current tree
    make doctor      # harness invariants
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
- **The hidden split paraphrases the object, never the verb.** Rewriting the
  verb too would fold in a second question and make a score drop unattributable.
- **Splits must stay disjoint at the fixture level.** Salting the PRNG is not
  enough; templated prompts collide as strings. Each split owns a fixture-id
  range, and `tests/regression` asserts it.
- **A missing state change is not an unsafe action.** `ST01` (a wrong change
  happened) is safety-critical; `ST03` (the right change did not) is not.
  Conflating them makes a blocked agent look dangerous.
- **Taxonomy ids append; they never renumber.** Published results cite `T01`…
  Family codes (`SF01`) are a presentation layer over that stable spine.
- **Never mutate the simulator in mutation testing.** A respelled parameter is
  translated back at the executor boundary, so the mutation changes what the
  agent must read, not what correct means.
- **The state model must cover every mutable surface.** `store.labels` was
  reachable by a tool but missing from `snapshot()`, so creating a label was
  invisible to the state hash *and* to `changed_resources`. If you add a
  mutable field, add it to the snapshot in the same commit.
- **`docs/behavioural-baseline.json` is a gate, not an artefact.** Changing the
  simulator's observable behaviour fails `make check`. Re-record deliberately
  with `callbench stability --record` and review the diff.
- **The decomposition's architecture axis must have no duplicate columns.** Two
  configurations that behave identically make the architecture look flatter
  than it is; a regression test asserts the ladder does not collapse.

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
