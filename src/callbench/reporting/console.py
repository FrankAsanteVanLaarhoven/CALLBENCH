"""The operator console.

Every view here is designed to be read in a terminal by someone deciding
whether a result is trustworthy — so the provenance banner, the scan window and
the safety column are never optional, and a synthetic-planner run says so at
the top of every screen rather than in a footnote.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from rich.console import Console, Group
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn
from rich.table import Table
from rich.text import Text

from ..metrics import SystemMetrics
from ..metrics.stats import Interval
from ..orchestration.runner import RunReport
from ..taxonomy import BY_CODE, SAFETY_CRITICAL
from .theme import HAIRLINE, THEME, label, rate_style

#: Width used when output is redirected. Rich would otherwise fall back to 80
#: columns and truncate the KPI table into unreadable ellipses, which is how a
#: piped report ends up less legible than the terminal one.
PIPED_WIDTH = 150


def make_console(*, width: int | None = None) -> Console:
    import sys

    if width is None and not sys.stdout.isatty():
        width = PIPED_WIDTH
    return Console(theme=THEME, width=width, highlight=False, soft_wrap=False)


def make_progress(console: Console) -> Progress:
    return Progress(
        TextColumn("[cb.dim]{task.fields[system]:<32}[/]"),
        BarColumn(bar_width=28, style="cb.rule", complete_style="cb.accent", finished_style="cb.accent"),
        MofNCompleteColumn(),
        TextColumn("[cb.dim]cases[/]"),
        TimeElapsedColumn(),
        console=console,
        transient=False,
    )


def banner(console: Console, report: RunReport) -> None:
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="cb.label", justify="left", no_wrap=True)
    grid.add_column(style="cb.value", justify="left")

    grid.add_row(label("suite"), "CallBench-Email")
    grid.add_row(label("planner"), report.model)
    grid.add_row(label("partitions"), ", ".join(report.partitions))
    grid.add_row(label("cases"), str(report.meta.get("task_count", 0)))
    grid.add_row(label("systems"), str(len(report.systems)))
    grid.add_row(label("runtime"), f"python {report.meta.get('python', '?')}")

    console.print()
    console.print(Text("CALLBENCH-EMAIL", style="cb.header"), Text("  evaluation ledger", style="cb.dim"))
    console.print(Text("─" * 78, style="cb.rule"))
    console.print(grid)

    if report.synthetic:
        console.print()
        warning = Table.grid(padding=(0, 1))
        warning.add_column(style="cb.warn", no_wrap=True)
        warning.add_column(style="cb.muted")
        warning.add_row("▪", Text("SYNTHETIC PLANNER — NOT A MODEL RESULT", style="cb.warn"))
        warning.add_row(
            " ",
            "These figures measure the evaluation architecture using the deterministic "
            "reference planner.\n  They are not a measurement of any language model and must "
            "not be cited as one. Run with\n  --model claude-opus-5 for a model result.",
        )
        console.print(warning)
    console.print(Text("─" * 78, style="cb.rule"))


def results_table(console: Console, report: RunReport) -> None:
    table = Table(
        box=HAIRLINE,
        show_edge=False,
        pad_edge=False,
        header_style="cb.label",
        border_style="cb.rule",
        expand=False,
    )
    table.add_column("SYSTEM", style="cb.value", no_wrap=True)
    table.add_column("TOOL SEL", justify="right")
    table.add_column("ARG EXACT", justify="right")
    table.add_column("SCHEMA", justify="right")
    table.add_column("PLAN", justify="right")
    table.add_column("FINAL STATE", justify="right")
    table.add_column("FABRICATE", justify="right")
    table.add_column("UNSAFE", justify="right")
    table.add_column("CALLS", justify="right", style="cb.muted")
    table.add_column("SCORE", justify="right")

    for metrics in report.systems:
        table.add_row(
            metrics.system,
            _pct(metrics.rates.get("tool_selection_accuracy")),
            _pct(metrics.rates.get("argument_exact_match")),
            _pct(metrics.rates.get("schema_validity_rate")),
            _pct(metrics.rates.get("plan_success_rate")),
            _pct(metrics.rates.get("state_transition_accuracy")),
            _pct(metrics.rates.get("fabrication_rate"), higher_is_better=False),
            _pct(metrics.rates.get("unsafe_action_rate"), higher_is_better=False),
            f"{metrics.mean_tool_calls:5.2f}",
            _score(metrics.composite),
        )

    console.print()
    console.print(Text(label("primary kpis"), style="cb.label"))
    console.print(table)
    console.print(
        Text(
            "  rates are point estimates; 95% Wilson intervals in results.json. "
            "score is safety-weighted, 0–100.",
            style="cb.dim",
        )
    )


def interval_table(console: Console, report: RunReport) -> None:
    table = Table(
        box=HAIRLINE, show_edge=False, pad_edge=False,
        header_style="cb.label", border_style="cb.rule",
    )
    table.add_column("SYSTEM", style="cb.value", no_wrap=True)
    table.add_column("PASS RATE  95% CI", justify="left")
    table.add_column("UNSAFE RATE  95% CI", justify="left")
    table.add_column("COMPOSITE  95% CI", justify="left")

    for metrics in report.systems:
        table.add_row(
            metrics.system,
            _interval(metrics.rates.get("overall_pass_rate")),
            _interval(metrics.rates.get("unsafe_action_rate")),
            _interval(metrics.composite, scale=1.0, decimals=1),
        )

    console.print()
    console.print(Text(label("confidence intervals"), style="cb.label"))
    console.print(table)


def taxonomy_table(console: Console, report: RunReport) -> None:
    codes: dict[str, dict[str, int]] = {}
    for metrics in report.systems:
        for code, count in metrics.taxonomy.items():
            codes.setdefault(code, {})[metrics.system] = count
    if not codes:
        console.print()
        console.print(Text("  no taxonomy findings", style="cb.dim"))
        return

    table = Table(
        box=HAIRLINE, show_edge=False, pad_edge=False,
        header_style="cb.label", border_style="cb.rule",
    )
    table.add_column("CODE", style="cb.value", no_wrap=True)
    table.add_column("FAILURE", style="cb.muted", no_wrap=True)
    for metrics in report.systems:
        table.add_column(_abbrev(metrics.system), justify="right")

    for code in sorted(codes, key=lambda c: (c not in SAFETY_CRITICAL, c)):
        spec = BY_CODE.get(code)
        style = "cb.crit" if code in SAFETY_CRITICAL else "cb.value"
        row: list[Any] = [Text(code, style=style), spec.title if spec else "—"]
        for metrics in report.systems:
            count = codes[code].get(metrics.system, 0)
            row.append(Text(str(count) if count else "·", style="cb.dim" if not count else style))
        table.add_row(*row)

    console.print()
    console.print(Text(label("failure taxonomy"), style="cb.label"))
    console.print(table)
    console.print(Text("  magenta codes are safety-critical", style="cb.dim"))


def comparison_table(console: Console, report: RunReport) -> None:
    if not report.comparisons:
        return
    table = Table(
        box=HAIRLINE, show_edge=False, pad_edge=False,
        header_style="cb.label", border_style="cb.rule",
    )
    table.add_column("SYSTEM", style="cb.value", no_wrap=True)
    table.add_column("METRIC", style="cb.muted", no_wrap=True)
    table.add_column("FULL", justify="right")
    table.add_column("SYSTEM", justify="right")
    table.add_column("DISCORDANT", justify="right", style="cb.dim")
    table.add_column("MCNEMAR P", justify="right")
    table.add_column("EFFECT", justify="right", style="cb.muted")

    for comparison in report.comparisons:
        significant = comparison.p_value < 0.05
        table.add_row(
            comparison.system,
            comparison.metric.replace("_", " "),
            f"{comparison.baseline_rate * 100:6.1f}%",
            f"{comparison.system_rate * 100:6.1f}%",
            f"{comparison.only_baseline}/{comparison.only_system}",
            Text(
                f"{comparison.p_value:.4f}",
                style="cb.ok" if significant else "cb.dim",
            ),
            f"{comparison.effect:+.2f} {comparison.effect_size}",
        )

    console.print()
    console.print(Text(label("paired comparison vs callbench_full"), style="cb.label"))
    console.print(table)
    console.print(
        Text(
            "  exact McNemar over the shared task set; "
            "discordant = full-only / system-only wins",
            style="cb.dim",
        )
    )


def partition_table(console: Console, report: RunReport) -> None:
    partitions = sorted({p for m in report.systems for p in m.by_partition})
    if not partitions:
        return
    table = Table(
        box=HAIRLINE, show_edge=False, pad_edge=False,
        header_style="cb.label", border_style="cb.rule",
    )
    table.add_column("SYSTEM", style="cb.value", no_wrap=True)
    for partition in partitions:
        table.add_column(_abbrev(partition), justify="right")

    for metrics in report.systems:
        row: list[Any] = [metrics.system]
        for partition in partitions:
            stats = metrics.by_partition.get(partition)
            if stats is None:
                row.append(Text("·", style="cb.dim"))
                continue
            row.append(
                Text(
                    f"{stats['pass_rate'] * 100:5.1f}%",
                    style=rate_style(stats["pass_rate"]),
                )
            )
        table.add_row(*row)

    console.print()
    console.print(Text(label("pass rate by partition"), style="cb.label"))
    console.print(table)


def notes_notice(console: Console, report: RunReport) -> None:
    if not report.notes:
        return
    console.print()
    console.print(Text(label("interpretation"), style="cb.label"))
    for note in report.notes:
        console.print(Text("  ▪ ", style="cb.warn"), Text(note, style="cb.muted"))


def skipped_notice(console: Console, report: RunReport) -> None:
    if not report.skipped:
        return
    console.print()
    console.print(Text(label("skipped"), style="cb.label"))
    for name, reason in report.skipped.items():
        console.print(Text(f"  {name}", style="cb.warn"), Text(f"  {reason}", style="cb.dim"))


def case_detail(console: Console, result, task) -> None:  # type: ignore[no-untyped-def]
    """Single-case inspector: the full decision trail, top to bottom."""
    header = Table.grid(padding=(0, 2))
    header.add_column(style="cb.label", no_wrap=True)
    header.add_column(style="cb.value")
    header.add_row(label("task"), result.task_id)
    header.add_row(label("partition"), result.partition)
    header.add_row(label("system"), result.system)
    header.add_row(label("prompt"), task.prompt)
    header.add_row(label("oracle"), task.oracle.decision)
    header.add_row(
        label("verdict"),
        Text("PASS" if result.passed else "FAIL", style="cb.ok" if result.passed else "cb.bad"),
    )

    console.print()
    console.print(Text("─" * 78, style="cb.rule"))
    console.print(header)

    for attempt in result.attempts:
        console.print()
        console.print(Text(f"  attempt {attempt.index}", style="cb.accent"))
        if attempt.plan is not None:
            console.print(Text(f"    decision  {attempt.plan.decision.value}", style="cb.muted"))
            for step in attempt.plan.steps:
                console.print(
                    Text(f"    ▸ {step.step_id}  {step.tool}", style="cb.value"),
                    Text(f"  {step.arguments}", style="cb.dim"),
                )
        if attempt.guard is not None and attempt.guard.violations:
            for violation in attempt.guard.violations:
                console.print(
                    Text(f"    ✕ {violation.code}", style="cb.bad"),
                    Text(f"  {violation.message}", style="cb.dim"),
                )
        for record in attempt.execution:
            style = "cb.ok" if record.ok else "cb.bad"
            console.print(
                Text(f"    · {record.step_id}  {record.tool}", style=style),
                Text(
                    f"  changed={record.changed_resources or '[]'}"
                    + (f"  error={record.error}" if record.error else ""),
                    style="cb.dim",
                ),
            )
        if attempt.verdict is not None:
            for layer in attempt.verdict.layers:
                mark = "pass" if layer.passed else "fail"
                suffix = "" if layer.authoritative else "  (advisory)"
                console.print(
                    Text(f"    {layer.name:<24}{mark}{suffix}", style="cb.ok" if layer.passed else "cb.bad"),
                    Text(f"  {layer.detail}", style="cb.dim"),
                )


def render_report(console: Console, report: RunReport) -> None:
    banner(console, report)
    results_table(console, report)
    interval_table(console, report)
    partition_table(console, report)
    taxonomy_table(console, report)
    comparison_table(console, report)
    notes_notice(console, report)
    skipped_notice(console, report)
    console.print()


# ---- formatting -----------------------------------------------------------


def _pct(interval: Interval | None, *, higher_is_better: bool = True) -> Text:
    if interval is None:
        return Text("·", style="cb.dim")
    return Text(
        f"{interval.point * 100:6.1f}%",
        style=rate_style(interval.point, higher_is_better=higher_is_better),
    )


def _interval(interval: Interval | None, *, scale: float = 100.0, decimals: int = 1) -> Text:
    if interval is None:
        return Text("·", style="cb.dim")
    unit = "%" if scale == 100.0 else ""
    return Text(
        f"{interval.point * scale:6.{decimals}f}{unit}  "
        f"[{interval.low * scale:.{decimals}f}, {interval.high * scale:.{decimals}f}]",
        style="cb.value",
    )


def _score(interval: Interval | None) -> Text:
    if interval is None:
        return Text("·", style="cb.dim")
    style = "cb.ok" if interval.point >= 80 else "cb.warn" if interval.point >= 55 else "cb.bad"
    return Text(f"{interval.point:6.1f}", style=style)


def _abbrev(name: str) -> str:
    """Short, non-letter-spaced column header for a matrix axis."""
    return name[:14].upper()


def summarise(metrics: Iterable[SystemMetrics]) -> Group:
    return Group(*(Text(m.system) for m in metrics))
