# Publication plan

The work has grown two separable contributions. Splitting them serves both:
the benchmark paper stops carrying a systems contribution it cannot do justice
to, and the verification paper stops being framed as an email result.

---

## Paper 1 — CallBench: A Verification-Centric Benchmark for Autonomous Tool-Calling Agents

**Claim.** Tool-use evaluation that scores a call against a reference call
cannot separate a malformed payload from a well-formed one that changed the
wrong resource. A verification-centric harness can, and the separation changes
what the ablations say.

**Content.** The benchmark; the four-layer verification stack; the hierarchical
taxonomy; splits and contamination controls; the ablation ladder; the factorial
attribution of outcome to architecture versus planner; mutation-based
robustness; the reproducibility machinery.

**Venues.** NeurIPS Datasets & Benchmarks; ICLR; MLSys.

**Blocking evidence.** Cross-model results. Without them the empirical section
rests on a reference planner, and the honest framing is "an evaluation
methodology with a worked reference implementation" rather than "a benchmark
the community should adopt".

---

## Paper 2 — Behavioural Replay Verification for Autonomous Tool Execution

**Claim.** Reproducibility claims for agent harnesses should be stated over
*observable behaviour*, not source. Define equivalence as identical state
transitions over a canonical fixture suite and script; then "this is the same
benchmark" becomes checkable, and a refactor stops being indistinguishable from
a semantic change.

**Content.** The equivalence relation; the Behavioural Stability Index; the
canonical-script coverage argument (every side-effect class must be exercised
or it is unprotected); the provenance ledger as a runtime information-flow
policy; the backend conformance contract and certification gate.

**Why it is separable.** Nothing in the relation is about email. It applies
wherever a simulator has observable state and a canonical script — Kubernetes,
robotics simulators, API sandboxes, MCP servers, agentic systems generally.

**Blocking evidence.** A second domain. The generality argument is currently an
argument; demonstrating the same relation and the same index over a
non-email simulator would make it a result. This is the same gap that leaves
GS3 unmeasured.

---

## Sequencing

Paper 2's core is complete and demonstrated in one domain. Paper 1's core is
complete and demonstrated with one planner. Both are blocked on external
evidence rather than on engineering, which is the better problem to have — but
it does mean neither should be submitted on the strength of the code alone.
