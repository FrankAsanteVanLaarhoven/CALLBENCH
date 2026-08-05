"""Certification, the frozen specification, and the second backend's planning model."""

from __future__ import annotations

from pathlib import Path

import pytest

from callbench import certification, spec
from callbench.models.reference import ReferenceBackend

COMPONENTS = {"schemas": "aaa", "taxonomy": "bbb", "verifier": "ccc"}


# ---- certification --------------------------------------------------------


def test_a_conformant_backend_is_certified() -> None:
    certificate = certification.issue(ReferenceBackend, components=COMPONENTS)
    assert certificate.status == certification.Status.CERTIFIED
    assert certificate.admissible


def test_a_nonconformant_backend_is_excluded_not_omitted() -> None:
    """An absent row reads as 'not tried'; it must read as 'not trustworthy'."""
    from callbench.contracts import Decision, Plan, PlanStep

    class Rogue(ReferenceBackend):
        def plan(self, contract, catalogue, analysis):  # type: ignore[no-untyped-def]
            return Plan(decision=Decision.EXECUTE, steps=[PlanStep("s1", "send_email", {})])

    certificate = certification.issue(Rogue, components=COMPONENTS)
    assert certificate.status == certification.Status.EXCLUDED
    assert not certificate.admissible
    assert certificate.failures


def test_a_backend_that_cannot_be_constructed_is_excluded() -> None:
    def broken():  # type: ignore[no-untyped-def]
        raise RuntimeError("no credentials")

    certificate = certification.issue(broken, components=COMPONENTS)
    assert not certificate.admissible
    assert "could not be constructed" in certificate.failures[0]


def test_an_untested_backend_is_not_silently_admissible(tmp_path: Path) -> None:
    status = certification.status_for("never-seen", COMPONENTS, path=tmp_path / "reg.json")
    assert status.status == certification.Status.UNTESTED
    assert not status.admissible


def test_a_certificate_earned_against_another_tree_goes_stale(tmp_path: Path) -> None:
    """Otherwise a backend certified against an older schema registry would
    appear admissible on a benchmark it has never satisfied."""
    registry = tmp_path / "reg.json"
    certification.write_registry(
        {"x": certification.Certificate("x", certification.Status.CERTIFIED, "2026-01-01", COMPONENTS)},
        registry,
    )
    moved = {**COMPONENTS, "schemas": "different"}
    status = certification.status_for("x", moved, path=registry)
    assert status.status == certification.Status.STALE
    assert not status.admissible
    assert "schemas" in status.note


def test_registry_round_trips(tmp_path: Path) -> None:
    registry = tmp_path / "reg.json"
    original = certification.issue(ReferenceBackend, components=COMPONENTS)
    certification.write_registry({"reference": original}, registry)
    assert certification.load_registry(registry)["reference"].status == original.status


# ---- frozen specification -------------------------------------------------


def test_the_tree_implements_the_frozen_specification() -> None:
    frozen, drift = spec.verify()
    assert frozen is not None, "docs/spec-v1.0.json must be committed"
    # The dataset hash depends on which splits are present locally, so it is
    # excluded here; `callbench spec` reports it in full.
    material = {k: v for k, v in drift.items() if k != "dataset"}
    assert not material, f"specification drift: {material}"


def test_the_frozen_spec_records_the_counts_a_reader_would_check() -> None:
    frozen = spec.load()
    assert frozen is not None
    assert frozen.counts["tools"] == 16
    assert frozen.counts["splits"] == 5
    assert frozen.counts["mutation_operators"] == 14
    assert frozen.counts["taxonomy_codes"] >= 21


def test_spec_drift_is_detected(tmp_path: Path) -> None:
    """A freeze that cannot fail is a version string, not a specification."""
    path = tmp_path / "spec.json"
    spec.freeze(path, version="test")
    frozen = spec.load(path)
    assert frozen is not None
    frozen.components["schemas"] = "moved"
    assert frozen.diff(spec.current(version="test"))


# ---- generalisation score -------------------------------------------------


def test_generalisation_tiers_are_separated() -> None:
    from callbench.metrics import generalisation_score

    score = generalisation_score("sys", seen_pass_rate=0.99, mutated_pass_rate=68.0)
    payload = score.to_dict()
    assert payload["GS1_seen"] == 99.0
    assert payload["GS2_mutated"] == 68.0
    assert payload["GS3_novel_domain"] is None
    assert "requires a second tool domain" in payload["GS3_status"]
    assert payload["transfer_gap_gs1_minus_gs2"] == pytest.approx(31.0)


# ---- the tool-use backend's planning model --------------------------------


def test_symbolic_results_are_their_own_provenance_references() -> None:
    """The mechanism that lets a plan-only loop compose real references.

    A model that uses what the tool handed back emits a reference; a model that
    invents an id emits a literal. The distinction the benchmark measures
    survives without executing anything.
    """
    from callbench.models.anthropic_tools import _symbolic_result

    search = _symbolic_result("search_messages", "s1")
    assert search["results"][0]["thread_id"] == "$s1.results[0].thread_id"
    assert search["results"][0]["message_id"] == "$s1.results[0].message_id"

    thread = _symbolic_result("read_thread", "s2")
    assert thread["external_participants"][0] == "$s2.external_participants[0]"


def test_symbolic_write_results_do_not_imply_execution() -> None:
    from callbench.models.anthropic_tools import _symbolic_result

    result = _symbolic_result("send_message", "s3")
    assert result["acknowledged"] is True
    assert "not executed" in result["note"]


def test_symbolic_references_resolve_against_a_real_result_shape() -> None:
    """The reference paths must be paths the resolver can actually walk."""
    from callbench.models.anthropic_tools import _symbolic_result
    from callbench.policies.references import resolve
    from callbench.simulator import build_fixture, invoke

    store = build_fixture("fixture_std_201")
    real = invoke(store, "search_messages", {"limit": 3}, "2026-08-05T09:00:00+00:00")
    symbolic = _symbolic_result("search_messages", "s1")
    reference = symbolic["results"][0]["thread_id"]
    assert resolve(reference, {"s1": real}) == real["results"][0]["thread_id"]


def test_a_placeholder_api_key_fails_fast() -> None:
    """A bogus key shadows every credential source; failing at call time would
    mean discovering it thousands of cases into a run."""
    import os

    from callbench.models.anthropic_backend import AnthropicBackend, BackendUnavailable

    previous = os.environ.get("ANTHROPIC_API_KEY")
    os.environ["ANTHROPIC_API_KEY"] = "ollama"
    try:
        with pytest.raises(BackendUnavailable, match="not an Anthropic key"):
            AnthropicBackend()
    finally:
        if previous is None:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        else:
            os.environ["ANTHROPIC_API_KEY"] = previous
