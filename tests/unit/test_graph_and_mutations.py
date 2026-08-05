"""Execution graph, provenance lineage and mutation testing."""

from __future__ import annotations

import pytest

from callbench import graph as graph_module
from callbench import mutations
from callbench.models.reference import Profile, ReferenceBackend, ReferenceConfig
from callbench.orchestration import Pipeline
from callbench.orchestration.config import BY_NAME, FULL
from callbench.policies import ProvenanceLedger

EXPECTED_SPINE = {
    "request", "intent", "entities", "dependencies",
    "plan", "gate", "execution", "verification", "ledger",
}


def _run(task, system=FULL):  # type: ignore[no-untyped-def]
    backend = ReferenceBackend(ReferenceConfig(profile=Profile(system.reference_profile)))
    pipeline = Pipeline(backend, system)
    result = pipeline.run(task)
    lineage = pipeline.last_ledger.lineage() if pipeline.last_ledger else []
    return result, graph_module.build(result, task, lineage)


# ---- execution graph ------------------------------------------------------


def test_graph_covers_every_stage_of_the_spine(suite) -> None:
    _, graph = _run(suite["public"][0])
    kinds = {node.kind for node in graph.nodes}
    assert kinds >= EXPECTED_SPINE


def test_graph_is_acyclic(suite) -> None:
    _, graph = _run(suite["public"][0])
    adjacency: dict[str, list[str]] = {}
    for edge in graph.edges:
        adjacency.setdefault(edge.source, []).append(edge.target)

    state: dict[str, int] = {}

    def visit(node: str) -> None:
        if state.get(node) == 1:
            raise AssertionError(f"cycle through {node}")
        if state.get(node) == 2:
            return
        state[node] = 1
        for nxt in adjacency.get(node, []):
            visit(nxt)
        state[node] = 2

    for node in list(adjacency):
        visit(node)


def test_graph_edges_only_reference_declared_nodes(suite) -> None:
    _, graph = _run(suite["public"][0])
    ids = {node.id for node in graph.nodes}
    for edge in graph.edges:
        assert edge.source in ids, edge.source
        assert edge.target in ids, edge.target


def test_graph_records_state_changes_as_resource_nodes(suite) -> None:
    result, graph = _run(suite["public"][0])
    changed = {r for a in result.attempts for rec in a.execution for r in rec.changed_resources}
    resources = {node.label for node in graph.nodes if node.kind == "resource"}
    assert changed <= resources


def test_graph_serialises_and_renders(suite) -> None:
    _, graph = _run(suite["public"][0])
    payload = graph.to_dict()
    assert payload["nodes"] and payload["edges"]
    assert graph.to_mermaid().startswith("flowchart TD")


# ---- provenance lineage ---------------------------------------------------


def test_lineage_records_production_and_consumption() -> None:
    ledger = ProvenanceLedger()
    ledger.add_user_request("forward msg_104 to dana.iverson@vendor.test")
    ledger.add_tool_output("s1", {"results": [{"thread_id": "thread_17"}]})
    ledger.record_use("s2", "thread_id", "thread_17")

    produced = {e.value for e in ledger.lineage() if e.kind == "produced"}
    consumed = {e.value for e in ledger.lineage() if e.kind == "consumed"}
    assert {"msg_104", "dana.iverson@vendor.test", "thread_17"} <= produced
    assert consumed == {"thread_17"}
    assert ledger.lineage_of("thread_17")[0].path == "results[0].thread_id"


def test_every_consumed_value_has_provenance_under_the_full_system(suite) -> None:
    """The safety property, checked on the graph rather than on a counter."""
    for task in suite["public"][:6]:
        _, graph = _run(task)
        for node in graph.nodes:
            if node.kind == "value":
                assert node.attrs["has_provenance"], f"{node.label} was fabricated"


# ---- mutation testing -----------------------------------------------------


@pytest.mark.parametrize("mutation", mutations.MUTATIONS, ids=lambda m: m.name)
def test_every_mutation_produces_a_usable_catalogue(mutation) -> None:  # type: ignore[no-untyped-def]
    """Structure operators change the tool count on purpose; every tool must
    still resolve to a real simulator handler."""
    mutant = mutations.build_mutant(mutation)
    assert 14 <= len(mutant) <= 18
    canonical_names = {t.name for t in mutations.CANONICAL_TOOLS}
    for spec in mutant:
        assert mutant.canonical(spec.name) in canonical_names
        assert spec.input_schema["additionalProperties"] is False


@pytest.mark.parametrize(
    "mutation",
    [m for m in mutations.MUTATIONS if m.category is not mutations.Category.STRUCTURE],
    ids=lambda m: m.name,
)
def test_non_structural_mutations_preserve_the_tool_count(mutation) -> None:  # type: ignore[no-untyped-def]
    expected = 16 - len(mutations.build_mutant(mutation).plan.shadowed)
    assert len(mutations.build_mutant(mutation)) == expected


def test_merging_routes_on_the_mode_argument() -> None:
    """A merged tool is two operations behind one name.

    Routing must follow the declared discriminator, and an unrecognised mode
    must not fall through to the benign branch — guessing there would hide the
    failure the merge exists to probe.
    """
    mutant = mutations.build_mutant(mutations.BY_NAME["tool_merge"])
    assert mutant.canonical_call("dispose_message", {"message_id": "m", "mode": "archive"}) == (
        "archive_message", {"message_id": "m"}
    )
    assert mutant.canonical_call("dispose_message", {"message_id": "m", "mode": "trash"}) == (
        "delete_message", {"message_id": "m"}
    )
    unresolved, _ = mutant.canonical_call("dispose_message", {"message_id": "m"})
    assert unresolved.endswith("unresolved_mode")


def test_splitting_keeps_both_halves_backed_by_one_handler() -> None:
    mutant = mutations.build_mutant(mutations.BY_NAME["tool_split"])
    assert "search_by_sender" in mutant and "search_by_text" in mutant
    assert mutant.canonical("search_by_sender") == "search_messages"
    assert mutant.canonical("search_by_text") == "search_messages"


def test_mutations_cover_every_category() -> None:
    covered = {m.category for m in mutations.MUTATIONS}
    assert covered == set(mutations.Category)


def test_renaming_tools_preserves_every_schema() -> None:
    mutant = mutations.build_mutant(mutations.BY_NAME["rename_tools"])
    from callbench.schemas import get_catalogue

    base = get_catalogue("catalogue_v1")
    for spec in mutant:
        assert spec.input_schema == base.spec(mutant.canonical(spec.name)).input_schema


def test_parameter_renaming_is_translated_back_for_the_simulator() -> None:
    """The simulator is ground truth and is never mutated.

    If a respelled parameter reached the simulator, the mutation would change
    what "correct" means rather than what the agent must read.
    """
    mutant = mutations.build_mutant(mutations.BY_NAME["rename_parameters"])
    assert mutant.canonical_arguments({"conversation_ref": "thread_10"}) == {
        "thread_id": "thread_10"
    }


def test_generalisation_report_scores_only_preserving_mutations(suite) -> None:
    tasks = suite["public"][:6]
    report = mutations.run(tasks, ReferenceBackend, FULL)
    assert report.results
    assert 0.0 <= report.score <= 100.0
    preserving = [r for r in report.results if r.preserves_meaning]
    assert preserving, "the score would be undefined without them"


def test_a_renamed_catalogue_does_not_break_a_reader(suite) -> None:
    """Tool renaming is semantics-preserving: a system that reads the catalogue
    it was handed must be unaffected."""
    tasks = suite["public"][:6]
    report = mutations.run(
        tasks, ReferenceBackend, FULL, mutations=(mutations.BY_NAME["rename_tools"],)
    )
    assert report.results[0].retention == pytest.approx(1.0)


def test_ablated_gate_still_runs_under_mutation(suite) -> None:
    report = mutations.run(
        suite["public"][:4],
        ReferenceBackend,
        BY_NAME["ablate_policy_guardian"],
        mutations=(mutations.BY_NAME["reorder_properties"],),
    )
    assert report.results[0].n == 4
