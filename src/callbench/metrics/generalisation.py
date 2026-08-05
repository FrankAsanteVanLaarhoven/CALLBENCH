"""The Generalisation Score: three tiers, honestly separated.

Robustness under mutation answers one question. It does not answer whether a
system transfers to a tool surface it has never seen, and reporting a single
"robustness" number invites the reader to assume it does.

====  =========================  =============================================
Tier  Condition                  What it tests
====  =========================  =============================================
GS1   Seen schema                competence on the catalogue it was given
GS2   Mutated schema             does it read the catalogue or remember one
GS3   Novel tool domain          does the method transfer past email at all
====  =========================  =============================================

**GS3 is not measurable in v1.0.** It requires a second domain — calendar,
filesystem, GitHub — and CallBench v1.0 ships one. It is defined here rather
than omitted so that the gap is visible in the metric itself rather than only
in a limitations section, and so a v2.0 result drops into a slot that already
exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class GeneralisationScore:
    system: str
    gs1_seen: float | None = None
    gs2_mutated: float | None = None
    gs3_novel_domain: float | None = None

    @property
    def transfer_gap(self) -> float | None:
        """GS1 minus GS2: how much competence was catalogue-specific."""
        if self.gs1_seen is None or self.gs2_mutated is None:
            return None
        return self.gs1_seen - self.gs2_mutated

    def to_dict(self) -> dict[str, Any]:
        return {
            "system": self.system,
            "GS1_seen": _round(self.gs1_seen),
            "GS2_mutated": _round(self.gs2_mutated),
            "GS3_novel_domain": self.gs3_novel_domain,
            "GS3_status": (
                "measured"
                if self.gs3_novel_domain is not None
                else "not measurable in v1.0: requires a second tool domain"
            ),
            "transfer_gap_gs1_minus_gs2": _round(self.transfer_gap),
        }


def build(
    system: str,
    *,
    seen_pass_rate: float | None,
    mutated_pass_rate: float | None,
    novel_domain_pass_rate: float | None = None,
) -> GeneralisationScore:
    return GeneralisationScore(
        system=system,
        gs1_seen=None if seen_pass_rate is None else seen_pass_rate * 100,
        gs2_mutated=mutated_pass_rate,
        gs3_novel_domain=novel_domain_pass_rate,
    )


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 2)
