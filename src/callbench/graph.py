"""Execution and provenance graphs.

A linear trace answers "what happened". A graph answers "what depended on
what" — which is the question you need for an audit, and the one a list of
tool calls cannot answer at all.

Two graphs are emitted, in one document:

**The execution DAG** walks the decision spine — request, intent, entities,
dependencies, plan, gate, each executed call, each verification layer, ledger.
Every stage is a node, so a failure has a *location* rather than a message.

**The provenance graph** is a value-level overlay. Each governed identifier or
address becomes a node with edges from the step that produced it to every field
that consumed it, and on to the mailbox resources that changed as a result. It
makes the central safety property visually checkable: a value with no inbound
``produced`` edge is a fabrication, and you can see it rather than trust a
counter.

The document is JSON with a Mermaid rendering alongside, so it is both
machine-readable and reviewable without tooling.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .contracts import CaseResult
from .datasets.task import Task
from .policies.provenance import LineageEdge
from .taxonomy import describe


@dataclass(frozen=True)
class GraphNode:
    id: str
    kind: str
    label: str
    status: str = "ok"  # ok | fail | blocked | skipped | info
    attrs: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GraphEdge:
    source: str
    target: str
    kind: str = "flow"  # flow | produced | consumed | changed
    label: str = ""


@dataclass
class ExecutionGraph:
    task_id: str
    system: str
    model: str
    nodes: list[GraphNode] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def add(self, node: GraphNode) -> str:
        self.nodes.append(node)
        return node.id

    def link(self, source: str, target: str, *, kind: str = "flow", label: str = "") -> None:
        self.edges.append(GraphEdge(source, target, kind, label))

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "system": self.system,
            "model": self.model,
            "meta": self.meta,
            "nodes": [asdict(n) for n in self.nodes],
            "edges": [asdict(e) for e in self.edges],
        }

    def to_mermaid(self) -> str:
        """A reviewable rendering. Provenance edges are dashed."""
        lines = ["flowchart TD"]
        shapes = {"fail": ('["', '"]'), "blocked": ('{{"', '"}}'), "info": ('(["', '"])')}
        for node in self.nodes:
            open_token, close_token = shapes.get(node.status, ('["', '"]'))
            safe = node.label.replace('"', "'")
            lines.append(f"  {node.id}{open_token}{safe}{close_token}")
        for edge in self.edges:
            arrow = "-.->" if edge.kind in {"produced", "consumed"} else "-->"
            label = f"|{edge.label}|" if edge.label else ""
            lines.append(f"  {edge.source} {arrow}{label} {edge.target}")
        return "\n".join(lines)


def _slug(value: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in value)[:48]


def build(
    result: CaseResult,
    task: Task,
    lineage: list[LineageEdge] | None = None,
) -> ExecutionGraph:
    """Build the execution DAG and provenance overlay for one case."""
    graph = ExecutionGraph(
        task_id=result.task_id,
        system=result.system,
        model=result.model,
        meta={
            "split": task.split,
            "tier": task.tier,
            "catalogue": task.catalogue,
            "fixture": task.fixture,
            "passed": result.passed,
            "oracle_decision": task.oracle.decision,
        },
    )

    request = graph.add(
        GraphNode("n_request", "request", f"User request\n{task.prompt}", "info")
    )

    attempt = result.final_attempt
    analysis = attempt.analysis if attempt else None

    intent = graph.add(
        GraphNode(
            "n_intent",
            "intent",
            f"Intent: {analysis.primary_intent if analysis else 'unknown'}",
            attrs={"risk_level": analysis.risk_level.value if analysis else None},
        )
    )
    graph.link(request, intent)

    entities = graph.add(
        GraphNode(
            "n_entities",
            "entities",
            "Entities: "
            + (
                ", ".join(f"{k}={v}" for k, v in (analysis.target or {}).items())
                if analysis and analysis.target
                else "none"
            ),
        )
    )
    graph.link(intent, entities)

    dependencies = graph.add(
        GraphNode(
            "n_dependencies",
            "dependencies",
            "Dependencies: "
            + (
                ", ".join(analysis.dependencies)
                if analysis and analysis.dependencies
                else "none"
            ),
            attrs={"ambiguities": list(analysis.ambiguities) if analysis else []},
        )
    )
    graph.link(entities, dependencies)

    plan_node = graph.add(
        GraphNode(
            "n_plan",
            "plan",
            f"Plan: {attempt.plan.decision.value if attempt and attempt.plan else 'none'} "
            f"({len(attempt.plan.steps) if attempt and attempt.plan else 0} steps)",
            attrs={"attempts": len(result.attempts)},
        )
    )
    graph.link(dependencies, plan_node)

    # One node per planned step, so a plan is inspectable independently of
    # whether it ran.
    previous = plan_node
    step_nodes: dict[str, str] = {}
    if attempt and attempt.plan:
        for step in attempt.plan.steps:
            node_id = graph.add(
                GraphNode(
                    f"n_step_{_slug(step.step_id)}",
                    "step",
                    f"{step.step_id}: {step.tool}",
                    attrs={"arguments": step.arguments},
                )
            )
            step_nodes[step.step_id] = node_id
            graph.link(previous, node_id, label="plans" if previous == plan_node else "then")
            previous = node_id

    gate = graph.add(
        GraphNode(
            "n_gate",
            "gate",
            "Policy gate",
            "fail" if attempt and attempt.guard and not attempt.guard.approved else "ok",
            attrs={
                "violations": [
                    {"code": v.code, "family_code": describe(v.code).family_code,
                     "step_id": v.step_id, "message": v.message}
                    for v in (attempt.guard.violations if attempt and attempt.guard else [])
                ]
            },
        )
    )
    graph.link(previous, gate)

    execution = graph.add(
        GraphNode("n_execution", "execution", f"Execution: {result.tool_calls} call(s)")
    )
    graph.link(gate, execution)

    for record in (attempt.execution if attempt else []):
        node_id = graph.add(
            GraphNode(
                f"n_call_{_slug(record.step_id)}",
                "call",
                f"{record.step_id}: {record.tool}",
                "ok" if record.ok else "fail",
                attrs={
                    "before_hash": record.before_hash,
                    "after_hash": record.after_hash,
                    "changed_resources": record.changed_resources,
                    "error": record.error,
                    "latency_ms": round(record.latency_ms, 3),
                },
            )
        )
        graph.link(execution, node_id)
        if record.step_id in step_nodes:
            graph.link(step_nodes[record.step_id], node_id, kind="flow", label="executes")
        for resource in record.changed_resources:
            resource_id = f"n_res_{_slug(resource)}"
            if all(n.id != resource_id for n in graph.nodes):
                graph.add(GraphNode(resource_id, "resource", resource, "info"))
            graph.link(node_id, resource_id, kind="changed", label="changes")

    verification = graph.add(
        GraphNode(
            "n_verification",
            "verification",
            "Verification",
            "ok" if result.passed else "fail",
        )
    )
    graph.link(execution, verification)

    verdict = attempt.verdict if attempt else None
    for layer in (verdict.layers if verdict else []):
        node_id = graph.add(
            GraphNode(
                f"n_layer_{_slug(layer.name)}",
                "layer",
                f"{layer.name}{'' if layer.authoritative else ' (advisory)'}",
                "ok" if layer.passed else ("info" if not layer.authoritative else "fail"),
                attrs={"detail": layer.detail, "authoritative": layer.authoritative},
            )
        )
        graph.link(verification, node_id)

    ledger = graph.add(
        GraphNode(
            "n_ledger",
            "ledger",
            f"Ledger: {'PASS' if result.passed else 'FAIL'}",
            "ok" if result.passed else "fail",
            attrs={
                "error_codes": [
                    {"code": c, "family_code": describe(c).family_code} for c in result.error_codes
                ],
                "unsafe": result.unsafe,
                "fabrication_count": result.fabrication_count,
            },
        )
    )
    graph.link(verification, ledger)

    _add_provenance(graph, lineage or [])
    return graph


def _add_provenance(graph: ExecutionGraph, lineage: list[LineageEdge]) -> None:
    """Overlay value lineage on the execution spine.

    A value node with no inbound ``produced`` edge is, by construction, a value
    the agent could not have known — which is what a fabrication *is*. The
    graph therefore makes the property checkable by inspection.
    """
    if not lineage:
        return

    consumed = {edge.value for edge in lineage if edge.kind == "consumed"}
    if not consumed:
        return

    for value in sorted(consumed):
        value_id = f"n_val_{_slug(value)}"
        produced_by = [e for e in lineage if e.value == value and e.kind == "produced"]
        graph.add(
            GraphNode(
                value_id,
                "value",
                value,
                "ok" if produced_by else "fail",
                attrs={"has_provenance": bool(produced_by)},
            )
        )
        for edge in produced_by:
            source = "n_request" if edge.step_id == "request" else f"n_call_{_slug(edge.step_id)}"
            graph.link(source, value_id, kind="produced", label=edge.path)
        for edge in [e for e in lineage if e.value == value and e.kind == "consumed"]:
            target = f"n_call_{_slug(edge.step_id)}"
            graph.link(value_id, target, kind="consumed", label=edge.path)
