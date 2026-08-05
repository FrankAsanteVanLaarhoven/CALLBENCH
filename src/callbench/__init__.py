"""CallBench-Email.

A benchmark and evaluation framework for autonomous function-calling agents
operating over email workflows.

The claim under test is not "can a model name the right function". It is
whether an agent can turn a natural-language instruction into a correct,
minimal, auditable and safe sequence of operations under realistic uncertainty
— and whether an evaluation harness can tell the difference between an agent
that did that and one that merely appeared to.
"""

from __future__ import annotations

__version__ = "0.1.0"
__all__ = ["__version__"]
