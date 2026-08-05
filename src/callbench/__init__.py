"""CallBench — a verification-centric benchmark for function-calling agents.

Most tool-use benchmarks evaluate one arrow: prompt to tool call to correct.
CallBench evaluates the pipeline that decides whether an autonomous agent is
safe to deploy — analysis, planning, a deterministic safety gate, execution,
state transition, verification, ledger — with an oracle at every stage, so a
failure has a location rather than only a score.

Email is the first domain. The simulator, the catalogue and the task
generators are domain-specific; the verification stack, taxonomy, metrics,
graphs and reproducibility machinery are not.
"""

from __future__ import annotations

__version__ = "0.1.0"
__all__ = ["__version__"]
