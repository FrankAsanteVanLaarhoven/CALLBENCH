"""``callbench`` — the operator entry point."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from rich.progress import TaskID

from .datasets import GeneratorConfig, generate_suite, iter_partitions
from .datasets.generate import PARTITIONS
from .datasets.task import Task
from .orchestration import Runner, resolve_systems
from .orchestration.config import BY_NAME
from .reporting import console as ui
from .reporting import html as html_report
from .reporting.theme import label
from .schemas import get_catalogue
from .simulator import build_fixture

DEFAULT_DATASET = Path("datasets")
DEFAULT_REPORTS = Path("reports")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="callbench",
        description="CallBench-Email: a benchmark for autonomous function-calling agents.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="generate the stratified task suite")
    gen.add_argument("--size", type=int, default=2500, help="tasks per split")
    gen.add_argument("--seed", type=int, default=20260805)
    gen.add_argument("--partitions", nargs="*", default=list(PARTITIONS))
    gen.add_argument("--root", type=Path, default=DEFAULT_DATASET)

    bench = sub.add_parser("bench", help="run the benchmark and write the ledger")
    bench.add_argument("--model", default="reference", help="'reference' or a Claude model id")
    bench.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"])
    bench.add_argument(
        "--systems",
        nargs="*",
        default=None,
        help="system names, or 'all' / 'ablations'. Default: the six baselines.",
    )
    bench.add_argument(
        "--partitions",
        nargs="*",
        default=["public", "adversarial", "stress"],
        help="splits to evaluate; `hidden` and `validation` are opt-in",
    )
    bench.add_argument("--limit", type=int, default=None, help="cap cases per partition")
    bench.add_argument("--root", type=Path, default=DEFAULT_DATASET)
    bench.add_argument("--out", type=Path, default=DEFAULT_REPORTS)
    bench.add_argument("--price-input", type=float, default=None, help="USD per 1M input tokens")
    bench.add_argument("--price-output", type=float, default=None, help="USD per 1M output tokens")
    bench.add_argument(
        "--with-mutation",
        type=int,
        default=0,
        metavar="N",
        help="also run mutation testing on N sampled cases to fill the robustness dimension",
    )
    bench.add_argument(
        "--graphs",
        type=int,
        default=0,
        metavar="N",
        help="write execution graphs for the first N cases per system",
    )
    bench.add_argument(
        "--no-cases",
        action="store_true",
        help="skip the per-case JSONL sidecar (summary JSON is always written)",
    )

    inspect = sub.add_parser("inspect", help="run one task and print its full decision trail")
    inspect.add_argument("task_id")
    inspect.add_argument("--model", default="reference")
    inspect.add_argument("--system", default="callbench_full")
    inspect.add_argument("--root", type=Path, default=DEFAULT_DATASET)
    inspect.add_argument("--partitions", nargs="*", default=list(PARTITIONS))
    inspect.add_argument(
        "--graph", type=Path, default=None, help="write the execution graph to this path"
    )

    mutate = sub.add_parser("mutate", help="mutation testing: measure tool generalisation")
    mutate.add_argument("--model", default="reference")
    mutate.add_argument("--system", default="callbench_full")
    mutate.add_argument("--partitions", nargs="*", default=["public"])
    mutate.add_argument("--limit", type=int, default=100, help="cases per partition")
    mutate.add_argument("--root", type=Path, default=DEFAULT_DATASET)
    mutate.add_argument("--out", type=Path, default=DEFAULT_REPORTS)

    decompose = sub.add_parser(
        "decompose", help="attribute results to the planner or to the architecture"
    )
    decompose.add_argument("--model", default="reference")
    decompose.add_argument("--partitions", nargs="*", default=["public", "adversarial"])
    decompose.add_argument("--limit", type=int, default=200, help="cases per split")
    decompose.add_argument("--root", type=Path, default=DEFAULT_DATASET)
    decompose.add_argument("--out", type=Path, default=DEFAULT_REPORTS)
    decompose.add_argument(
        "--metric", default="pass_rate", choices=["pass_rate", "unsafe_rate", "fabrication_rate"]
    )

    conform = sub.add_parser(
        "conform", help="check that a model backend satisfies the adapter contract"
    )
    conform.add_argument("--model", default="reference")
    conform.add_argument("--effort", default="high")

    stability = sub.add_parser(
        "stability", help="behavioural replay verification of the simulator"
    )
    stability.add_argument(
        "--record", action="store_true", help="rewrite the committed baseline"
    )
    stability.add_argument("--baseline", type=Path, default=None)

    replay = sub.add_parser("replay", help="check a recorded run against the current tree")
    replay.add_argument("replay_id", nargs="?", default=None)
    replay.add_argument("--results", type=Path, default=DEFAULT_REPORTS / "results.json")

    tools = sub.add_parser("tools", help="print a tool catalogue")
    tools.add_argument("--catalogue", default="catalogue_v1")

    sub.add_parser("doctor", help="self-check the harness invariants")

    args = parser.parse_args(argv)
    console = ui.make_console()

    if args.command == "generate":
        return _generate(console, args)
    if args.command == "bench":
        return _bench(console, args)
    if args.command == "inspect":
        return _inspect(console, args)
    if args.command == "mutate":
        return _mutate(console, args)
    if args.command == "decompose":
        return _decompose(console, args)
    if args.command == "conform":
        return _conform(console, args)
    if args.command == "stability":
        return _stability(console, args)
    if args.command == "replay":
        return _replay(console, args)
    if args.command == "tools":
        return _tools(console, args)
    if args.command == "doctor":
        return _doctor(console)
    return 2


def _generate(console, args) -> int:  # type: ignore[no-untyped-def]
    config = GeneratorConfig(size=args.size, seed=args.seed)
    counts = generate_suite(args.root, config, args.partitions)
    console.print()
    console.print(f"[cb.header]{label('generated')}[/]")
    for partition, count in counts.items():
        target = args.root / partition / "tasks.jsonl"
        console.print(f"  [cb.value]{partition:<14}[/] [cb.accent]{count:>6}[/] [cb.dim]{target}[/]")
    if "hidden" in counts:
        console.print()
        console.print(
            "  [cb.warn]▪[/] [cb.muted]the hidden partition is gitignored by design; "
            "regenerate it from the seed rather than committing it[/]"
        )
    console.print()
    return 0


def _load_tasks(root: Path, partitions: list[str], limit: int | None) -> list[Task]:
    tasks: list[Task] = []
    for _, partition_tasks in iter_partitions(root, partitions):
        tasks.extend(partition_tasks[:limit] if limit else partition_tasks)
    return tasks


def _bench(console, args) -> int:  # type: ignore[no-untyped-def]
    tasks = _load_tasks(args.root, args.partitions, args.limit)
    if not tasks:
        console.print(
            f"[cb.bad]no tasks found under {args.root}[/] "
            "[cb.dim]run `callbench generate` first[/]"
        )
        return 1

    systems = resolve_systems(args.systems)
    progress = ui.make_progress(console)
    task_ids: dict[str, TaskID] = {}

    console.print()
    with progress:
        def on_progress(system: str, position: int, total: int) -> None:
            if system not in task_ids:
                task_ids[system] = progress.add_task("", total=total, system=system)
            progress.update(task_ids[system], completed=position)

        runner = Runner(
            args.model,
            systems,
            effort=args.effort,
            progress=on_progress,
            dataset_root=args.root,
            price_input=args.price_input,
            price_output=args.price_output,
        )
        report = runner.run(tasks, args.partitions)

    if args.with_mutation:
        from . import mutations
        from .metrics import dimensions as dimensions_module

        sample = tasks[: args.with_mutation]
        scores: dict[str, float] = {}
        for system in systems:
            if system.name not in report.results:
                continue
            # Bind the loop variable explicitly: a closure over `system` would
            # capture the last iteration and score every row against it.
            def factory(config=system):  # type: ignore[no-untyped-def]
                return runner._backend_for(config)

            # Absolute, not retention: see metrics.dimensions.build.
            scores[system.name] = mutations.run(sample, factory, system).absolute_score
        report.dimensions = dimensions_module.to_dict(
            dimensions_module.build(
                report.systems,
                behavioural_stability=report.behavioural_stability,
                replay_match=100.0,
                generalisation=scores,
            )
        )
        console.print(
            f"  [cb.dim]robustness measured over {len(sample)} sampled cases per system[/]"
        )

    ui.render_report(console, report)

    json_path = html_report.write_json(report, args.out / "results.json")
    html_path = html_report.write(report, args.out / "report.html")
    console.print(f"  [cb.label]{label('written')}[/]")
    console.print(f"    [cb.dim]{json_path}[/]")
    console.print(f"    [cb.dim]{html_path}[/]")
    if not args.no_cases:
        cases_path = html_report.write_cases_jsonl(report, args.out / "cases.jsonl")
        console.print(f"    [cb.dim]{cases_path}[/]")
    if args.graphs:
        written = _write_graphs(report, tasks, args.out / "graphs", args.graphs)
        console.print(f"    [cb.dim]{args.out / 'graphs'}  ({written} execution graphs)[/]")
    console.print()
    return 0


def _inspect(console, args) -> int:  # type: ignore[no-untyped-def]
    tasks = {t.id: t for t in _load_tasks(args.root, args.partitions, None)}
    task = tasks.get(args.task_id)
    if task is None:
        console.print(f"[cb.bad]no such task: {args.task_id}[/]")
        return 1

    from .models import build_backend
    from .models.base import Backend
    from .models.reference import Profile, ReferenceBackend, ReferenceConfig
    from .orchestration import Pipeline

    system = BY_NAME[args.system]
    backend: Backend
    if args.model.startswith("reference"):
        backend = ReferenceBackend(
            ReferenceConfig(profile=Profile(system.reference_profile), strict_json=system.strict_json)
        )
    else:
        backend = build_backend(args.model)

    pipeline = Pipeline(backend, system)
    result = pipeline.run(task)
    ui.case_detail(console, result, task)

    if args.graph is not None:
        from . import graph as graph_module

        lineage = pipeline.last_ledger.lineage() if pipeline.last_ledger else []
        built = graph_module.build(result, task, lineage)
        args.graph.parent.mkdir(parents=True, exist_ok=True)
        args.graph.write_text(
            json.dumps(
                {**built.to_dict(), "mermaid": built.to_mermaid()}, indent=2, sort_keys=True
            ),
            encoding="utf-8",
        )
        console.print(f"  [cb.dim]execution graph -> {args.graph}[/]")

    console.print()
    return 0 if result.passed else 1


def _write_graphs(report, tasks, out_dir: Path, limit: int) -> int:  # type: ignore[no-untyped-def]
    """Sample execution graphs.

    Graphs are sampled rather than written for every case: a full sweep is tens
    of thousands of files. The sample size is printed so nobody mistakes a
    sample for a census.
    """
    from . import graph as graph_module

    by_id = {t.id: t for t in tasks}
    out_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for system, results in sorted(report.results.items()):
        for result in results[:limit]:
            task = by_id.get(result.task_id)
            if task is None:
                continue
            built = graph_module.build(result, task, [])
            path = out_dir / f"{system}__{result.task_id}.json"
            path.write_text(
                json.dumps(
                    {**built.to_dict(), "mermaid": built.to_mermaid()}, indent=2, sort_keys=True
                ),
                encoding="utf-8",
            )
            written += 1
    return written


def _mutate(console, args) -> int:  # type: ignore[no-untyped-def]
    from . import mutations
    from .models import build_backend
    from .models.reference import Profile, ReferenceBackend, ReferenceConfig

    tasks = _load_tasks(args.root, args.partitions, args.limit)
    if not tasks:
        console.print(f"[cb.bad]no tasks found under {args.root}[/]")
        return 1

    system = BY_NAME[args.system]

    def factory():  # type: ignore[no-untyped-def]
        if args.model.startswith("reference"):
            return ReferenceBackend(
                ReferenceConfig(
                    profile=Profile(system.reference_profile), strict_json=system.strict_json
                )
            )
        return build_backend(args.model)

    console.print()
    console.print(f"[cb.header]{label('mutation testing')}[/] [cb.dim]{len(tasks)} cases[/]")

    def on_progress(name: str, index: int, total: int) -> None:
        console.print(f"  [cb.dim]{index}/{total}  {name}[/]")

    report = mutations.run(tasks, factory, system, progress=on_progress)
    ui.mutation_table(console, report)

    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / "mutations.json"
    path.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    console.print(f"  [cb.dim]{path}[/]")
    console.print()
    return 0


def _decompose(console, args) -> int:  # type: ignore[no-untyped-def]
    from . import decompose as decompose_module

    tasks = _load_tasks(args.root, args.partitions, args.limit)
    if not tasks:
        console.print(f"[cb.bad]no tasks found under {args.root}[/]")
        return 1

    if not args.model.startswith("reference"):
        console.print(
            "[cb.bad]the planner axis for a model backend is the model itself[/] "
            "[cb.dim]run one --model per row and compare the reports; a single model "
            "cannot populate a planner-competence axis[/]"
        )
        return 1

    console.print()
    console.print(f"[cb.header]{label('decomposition')}[/] [cb.dim]{len(tasks)} cases per cell[/]")

    def on_progress(planner: str, architecture: str) -> None:
        console.print(f"  [cb.dim]{planner:<10} x {architecture}[/]")

    result = decompose_module.run(
        tasks,
        decompose_module.reference_factory,
        metric=args.metric,
        progress=on_progress,
    )
    ui.decomposition_table(console, result)

    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / "decomposition.json"
    path.write_text(json.dumps(result.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    console.print(f"  [cb.dim]{path}[/]")
    console.print()
    return 0


def _conform(console, args) -> int:  # type: ignore[no-untyped-def]
    from . import conformance
    from .models import build_backend

    console.print()
    console.print(f"[cb.header]{label('backend conformance')}[/] [cb.dim]{args.model}[/]")
    console.print("[cb.rule]" + "─" * 78 + "[/]")

    try:
        report = conformance.check(lambda: build_backend(args.model, effort=args.effort))
    except Exception as exc:  # noqa: BLE001 - construction failure is a conformance failure
        console.print(f"  [cb.bad]backend could not be constructed[/] [cb.dim]{exc}[/]")
        return 1

    for item in report.checks:
        mark = (
            "[cb.ok]pass[/]"
            if item.passed
            else ("[cb.bad]fail[/]" if item.required else "[cb.warn]note[/]")
        )
        console.print(f"  {mark}  [cb.value]{item.name:<50}[/][cb.dim]{item.detail}[/]")

    console.print()
    if report.conformant:
        console.print(
            "  [cb.ok]▪[/] [cb.muted]this backend satisfies the adapter contract; its "
            "numbers are comparable with other conformant backends[/]"
        )
    else:
        console.print(
            "  [cb.bad]▪[/] [cb.muted]required checks failed. A cross-model comparison "
            "using this adapter would measure the adapter, not the model[/]"
        )
    console.print()
    return 0 if report.conformant else 2


def _stability(console, args) -> int:  # type: ignore[no-untyped-def]
    """Behavioural Replay Verification: does the simulator still behave the same?"""
    from . import stability as stability_module

    path = args.baseline or stability_module.DEFAULT_BASELINE

    console.print()
    console.print(f"[cb.header]{label('behavioural stability')}[/]")
    console.print(
        "[cb.dim]  equivalence: identical observable state transitions over the "
        "canonical fixture suite and script[/]"
    )

    if args.record:
        signatures = stability_module.write_baseline(path)
        console.print()
        console.print(
            f"  [cb.warn]▪[/] [cb.muted]baseline rewritten with {len(signatures)} "
            f"signatures -> {path}[/]"
        )
        console.print(
            "  [cb.dim]review the diff: a changed signature means the simulator's "
            "observable behaviour changed, which invalidates prior results[/]"
        )
        console.print()
        return 0

    report = stability_module.measure(path)
    if report is None:
        console.print(
            f"  [cb.bad]no baseline at {path}[/] "
            "[cb.dim]record one with `callbench stability --record`[/]"
        )
        return 1

    style = "cb.ok" if report.stable else "cb.bad"
    console.print()
    console.print(
        f"  [{style}]BS = {report.score:.1f}%[/]  "
        f"[cb.dim]{report.matched}/{report.total} fixtures behaviourally identical[/]"
    )
    for fixture in report.drifted:
        console.print(f"    [cb.bad]drift[/]    [cb.value]{fixture}[/]")
    for fixture in report.missing:
        console.print(f"    [cb.warn]new[/]      [cb.value]{fixture}[/] [cb.dim]not in baseline[/]")
    for fixture in report.added:
        console.print(f"    [cb.warn]absent[/]   [cb.value]{fixture}[/] [cb.dim]baseline only[/]")

    console.print()
    if report.stable:
        console.print(
            "  [cb.dim]the simulator is behaviourally equivalent to the recorded "
            "implementation; prior results remain comparable[/]"
        )
    else:
        console.print(
            "  [cb.dim]observable behaviour changed. Prior numbers describe a different "
            "simulator — re-record deliberately, and re-run anything you intend to cite[/]"
        )
    console.print()
    return 0 if report.stable else 2


def _replay(console, args) -> int:  # type: ignore[no-untyped-def]
    """Compare a recorded fingerprint against the current tree."""
    from . import repro
    from .orchestration.config import BY_NAME as SYSTEMS

    recorded = repro.load(args.results)
    if recorded is None:
        console.print(
            f"[cb.bad]no fingerprint in {args.results}[/] "
            "[cb.dim]run `callbench bench` first[/]"
        )
        return 1
    if args.replay_id and args.replay_id != recorded.replay_id:
        console.print(
            f"[cb.bad]{args.results} records {recorded.replay_id}, not {args.replay_id}[/]"
        )
        return 1

    config = recorded.config
    systems = [SYSTEMS[name] for name in config.get("systems", []) if name in SYSTEMS]
    current = repro.fingerprint(
        model=config.get("model", "reference"),
        systems=systems,
        partitions=config.get("partitions", []),
        dataset_root=DEFAULT_DATASET,
        seed=config.get("seed"),
        effort=config.get("effort", "high"),
    )

    console.print()
    console.print(f"[cb.header]{label('replay')}[/]")
    console.print(f"  [cb.label]RECORDED[/]  [cb.value]{recorded.replay_id}[/]")
    console.print(f"  [cb.label]CURRENT [/]  [cb.value]{current.replay_id}[/]")

    drift = recorded.diff(current)
    if not drift:
        console.print()
        console.print(
            "  [cb.ok]▪[/] [cb.muted]every component matches; this run is reproducible "
            "from the current tree[/]"
        )
        console.print()
        return 0

    console.print()
    console.print(f"  [cb.warn]▪[/] [cb.muted]{len(drift)} component(s) changed since the run[/]")
    for name, (was, now) in drift.items():
        console.print(f"    [cb.bad]{name:<12}[/] [cb.dim]{was} -> {now}[/]")
    console.print()
    console.print(
        "  [cb.dim]the recorded numbers describe a different benchmark; regenerate "
        "or check out the recorded revision before comparing[/]"
    )
    console.print()
    return 2


def _tools(console, args) -> int:  # type: ignore[no-untyped-def]
    catalogue = get_catalogue(args.catalogue)
    console.print()
    console.print(f"[cb.header]{label(catalogue.name)}[/] [cb.dim]{len(catalogue)} tools[/]")
    console.print("[cb.rule]" + "─" * 78 + "[/]")
    for spec in catalogue:
        console.print(
            f"  [cb.value]{spec.name:<24}[/][cb.dim]{spec.side_effect.value:<12}"
            f"{'idempotent' if spec.idempotent else 'not idempotent'}[/]"
        )
    console.print()
    return 0


def _doctor(console) -> int:  # type: ignore[no-untyped-def]
    """Assert the invariants a result depends on. Cheap, and run in CI."""
    checks: list[tuple[str, bool, str]] = []

    first = build_fixture("fixture_std_201")
    second = build_fixture("fixture_std_201")
    checks.append(
        ("fixture determinism", first.state_hash() == second.state_hash(), first.state_hash()[:23])
    )

    from .simulator.tools import HANDLERS

    catalogue = get_catalogue("catalogue_v1")
    missing = [spec.name for spec in catalogue if spec.name not in HANDLERS]
    checks.append(("every tool is simulated", not missing, ", ".join(missing) or "16/16"))

    v4 = get_catalogue("catalogue_v4")
    renamed = sum(1 for spec in v4 if v4.canonical(spec.name) != spec.name)
    checks.append(("catalogue_v4 renames every tool", renamed == len(v4), f"{renamed}/{len(v4)}"))

    from .taxonomy import ALL_CODES, describe

    family_codes = [describe(code).family_code for code in ALL_CODES]
    checks.append(
        (
            "taxonomy ids are unique",
            len(set(ALL_CODES)) == len(ALL_CODES) == len(set(family_codes)),
            f"{len(ALL_CODES)} codes across 6 families",
        )
    )

    from .mutations import MUTATIONS, build_mutant

    mutants_ok = all(len(build_mutant(m)) == 16 for m in MUTATIONS)
    checks.append(
        ("mutation operators build", mutants_ok, f"{len(MUTATIONS)} operators")
    )

    from .datasets.generate import PARTITIONS as SPLIT_NAMES
    from .datasets.generate import GeneratorConfig, generate_partition

    probe = {
        split: {t.fixture for t in generate_partition(split, GeneratorConfig(size=12, seed=1))}
        for split in SPLIT_NAMES
    }
    overlaps = [
        f"{a}∩{b}"
        for i, a in enumerate(SPLIT_NAMES)
        for b in SPLIT_NAMES[i + 1:]
        if probe[a] & probe[b]
    ]
    checks.append(
        ("splits are disjoint", not overlaps, ", ".join(overlaps) or f"{len(SPLIT_NAMES)} splits")
    )

    import os

    simulation_only = os.environ.get("CALLBENCH_SIMULATION_ONLY", "1") != "0"
    checks.append(("simulation-only mode", simulation_only, "no real connector is reachable"))

    console.print()
    console.print(f"[cb.header]{label('doctor')}[/]")
    console.print("[cb.rule]" + "─" * 78 + "[/]")
    for name, ok, detail in checks:
        mark = "[cb.ok]pass[/]" if ok else "[cb.bad]fail[/]"
        console.print(f"  {mark}  [cb.value]{name:<34}[/][cb.dim]{detail}[/]")
    console.print()
    return 0 if all(ok for _, ok, _ in checks) else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
