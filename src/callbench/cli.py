"""``callbench`` — the operator entry point."""

from __future__ import annotations

import argparse
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
    gen.add_argument("--size", type=int, default=500, help="tasks per partition")
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
    bench.add_argument("--partitions", nargs="*", default=["easy", "medium", "hard", "adversarial"])
    bench.add_argument("--limit", type=int, default=None, help="cap cases per partition")
    bench.add_argument("--root", type=Path, default=DEFAULT_DATASET)
    bench.add_argument("--out", type=Path, default=DEFAULT_REPORTS)
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

        runner = Runner(args.model, systems, effort=args.effort, progress=on_progress)
        report = runner.run(tasks, args.partitions)

    ui.render_report(console, report)

    json_path = html_report.write_json(report, args.out / "results.json")
    html_path = html_report.write(report, args.out / "report.html")
    console.print(f"  [cb.label]{label('written')}[/]")
    console.print(f"    [cb.dim]{json_path}[/]")
    console.print(f"    [cb.dim]{html_path}[/]")
    if not args.no_cases:
        cases_path = html_report.write_cases_jsonl(report, args.out / "cases.jsonl")
        console.print(f"    [cb.dim]{cases_path}[/]")
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

    result = Pipeline(backend, system).run(task)
    ui.case_detail(console, result, task)
    console.print()
    return 0 if result.passed else 1


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

    from .taxonomy import ALL_CODES

    checks.append(("taxonomy is complete", len(ALL_CODES) == 18, f"{len(ALL_CODES)} codes"))

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
