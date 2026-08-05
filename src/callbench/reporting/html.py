"""Self-contained HTML report.

One file, no network, no fonts to fetch, no scripts from anywhere. The visual
language matches the console: graphite surface, hairline rules, one accent,
tabular numerals, colour reserved for state. Bars are inline SVG so the report
renders identically offline and in print.
"""

from __future__ import annotations

import html
import json
from datetime import UTC, datetime
from pathlib import Path

from ..metrics.stats import Interval
from ..orchestration.runner import RunReport
from ..taxonomy import BY_CODE, SAFETY_CRITICAL, sort_key

_CSS = """
:root {
  --bg: #0b0c0e; --panel: #111316; --panel-2: #0e1013;
  --line: #23262c; --line-soft: #1a1d21;
  --fg: #e8eaed; --muted: #9aa0a6; --dim: #6b7280;
  --accent: #5e6ad2; --ok: #4cb782; --warn: #e2b93b; --bad: #e5484d; --crit: #d6409f;
  --mono: ui-monospace, "SF Mono", "JetBrains Mono", Menlo, Consolas, monospace;
  --sans: -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", Helvetica, Arial, sans-serif;
}
@media (prefers-color-scheme: light) {
  :root {
    --bg: #fbfbfc; --panel: #ffffff; --panel-2: #f6f7f8;
    --line: #e3e5e8; --line-soft: #eceef0;
    --fg: #16181d; --muted: #5c6370; --dim: #868d99;
  }
}
:root[data-theme="dark"] {
  --bg: #0b0c0e; --panel: #111316; --panel-2: #0e1013;
  --line: #23262c; --line-soft: #1a1d21;
  --fg: #e8eaed; --muted: #9aa0a6; --dim: #6b7280;
}
:root[data-theme="light"] {
  --bg: #fbfbfc; --panel: #ffffff; --panel-2: #f6f7f8;
  --line: #e3e5e8; --line-soft: #eceef0;
  --fg: #16181d; --muted: #5c6370; --dim: #868d99;
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--fg);
  font-family: var(--sans); font-size: 13px; line-height: 1.5;
  -webkit-font-smoothing: antialiased;
}
.wrap { max-width: 1240px; margin: 0 auto; padding: 32px 24px 80px; }
header.masthead {
  display: flex; align-items: baseline; gap: 12px;
  padding-bottom: 14px; border-bottom: 1px solid var(--line);
}
header.masthead h1 {
  margin: 0; font-size: 15px; font-weight: 620; letter-spacing: .02em;
}
header.masthead .sub { color: var(--dim); font-size: 12px; }
header.masthead .spacer { flex: 1; }
.meta { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 1px;
        background: var(--line-soft); border: 1px solid var(--line); margin: 18px 0 0; }
.meta div { background: var(--panel); padding: 10px 12px; }
.meta dt { margin: 0 0 3px; font-size: 9.5px; letter-spacing: .1em;
           text-transform: uppercase; color: var(--dim); }
.meta dd { margin: 0; font-family: var(--mono); font-size: 12.5px; }
.notice { margin: 18px 0 0; border: 1px solid var(--warn); border-left-width: 3px;
          background: var(--panel); padding: 12px 14px; }
.notice b { color: var(--warn); letter-spacing: .08em; font-size: 10.5px; text-transform: uppercase; }
.notice p { margin: 6px 0 0; color: var(--muted); font-size: 12.5px; max-width: 92ch; }
section { margin-top: 34px; }
h2 { font-size: 10.5px; letter-spacing: .12em; text-transform: uppercase; color: var(--dim);
     margin: 0 0 10px; font-weight: 600; }
.scroll { overflow-x: auto; border: 1px solid var(--line); background: var(--panel); }
table { border-collapse: collapse; width: 100%; font-family: var(--mono); font-size: 12px;
        font-variant-numeric: tabular-nums; }
thead th { text-align: right; padding: 9px 12px; color: var(--dim); font-weight: 600;
           font-size: 9.5px; letter-spacing: .1em; text-transform: uppercase;
           border-bottom: 1px solid var(--line); white-space: nowrap; }
thead th:first-child, tbody td:first-child { text-align: left; }
tbody td { padding: 8px 12px; text-align: right; border-bottom: 1px solid var(--line-soft);
           white-space: nowrap; }
tbody tr:last-child td { border-bottom: 0; }
tbody tr:hover td { background: var(--panel-2); }
td.name, th.name { font-family: var(--sans); font-weight: 500; }
.ok { color: var(--ok); } .warn { color: var(--warn); } .bad { color: var(--bad); }
.crit { color: var(--crit); font-weight: 600; }
.dim { color: var(--dim); }
.zero { color: var(--dim); }
.bar { display: block; }
.legend { color: var(--dim); font-size: 11.5px; margin-top: 8px; }
footer { margin-top: 46px; padding-top: 16px; border-top: 1px solid var(--line);
         color: var(--dim); font-size: 11.5px; max-width: 92ch; }
footer h3 { font-size: 10.5px; letter-spacing: .12em; text-transform: uppercase;
            color: var(--muted); margin: 0 0 8px; }
footer ul { margin: 0 0 12px; padding-left: 18px; }
code { font-family: var(--mono); color: var(--muted); }
"""


def _bar(interval: Interval, *, width: int = 120, higher_is_better: bool = True) -> str:
    point = max(0.0, min(1.0, interval.point))
    low = max(0.0, min(1.0, interval.low))
    high = max(0.0, min(1.0, interval.high))
    colour = _colour_for(point, higher_is_better)
    x0 = low * width
    x1 = high * width
    px = point * width
    return (
        f'<svg class="bar" width="{width}" height="10" viewBox="0 0 {width} 10" '
        f'role="img" aria-label="{point:.3f} with 95% interval {low:.3f} to {high:.3f}">'
        f'<rect x="0" y="4" width="{width}" height="2" fill="var(--line)"/>'
        f'<rect x="{x0:.1f}" y="4" width="{max(1.0, x1 - x0):.1f}" height="2" '
        f'fill="{colour}" opacity="0.42"/>'
        f'<rect x="{max(0.0, px - 1):.1f}" y="1" width="2" height="8" fill="{colour}"/>'
        f"</svg>"
    )


def _colour_for(value: float, higher_is_better: bool) -> str:
    if higher_is_better:
        if value >= 0.9:
            return "var(--ok)"
        return "var(--warn)" if value >= 0.75 else "var(--bad)"
    if value <= 0.0:
        return "var(--ok)"
    return "var(--warn)" if value < 0.02 else "var(--bad)"


def _cls(value: float, higher_is_better: bool = True) -> str:
    if higher_is_better:
        return "ok" if value >= 0.9 else "warn" if value >= 0.75 else "bad"
    if value <= 0.0:
        return "ok"
    return "warn" if value < 0.02 else "bad"


def _pct(interval: Interval | None, higher_is_better: bool = True) -> str:
    if interval is None:
        return '<span class="dim">·</span>'
    return f'<span class="{_cls(interval.point, higher_is_better)}">{interval.point * 100:.1f}%</span>'


def render(report: RunReport) -> str:
    generated = datetime.now(UTC).replace(microsecond=0).isoformat()
    parts: list[str] = []

    parts.append('<div class="wrap">')
    parts.append(
        '<header class="masthead"><h1>CallBench-Email</h1>'
        '<span class="sub">evaluation ledger</span><span class="spacer"></span>'
        f'<span class="sub">{html.escape(generated)}</span></header>'
    )

    meta = [
        ("planner", report.model),
        ("cases", str(report.meta.get("task_count", 0))),
        ("systems", str(len(report.systems))),
        ("partitions", ", ".join(report.partitions)),
        ("effort", str(report.meta.get("effort", "—"))),
        ("python", str(report.meta.get("python", "—"))),
    ]
    parts.append('<dl class="meta">')
    for key, value in meta:
        parts.append(f"<div><dt>{html.escape(key)}</dt><dd>{html.escape(value)}</dd></div>")
    parts.append("</dl>")

    if report.synthetic:
        parts.append(
            '<div class="notice"><b>Synthetic planner — not a model result</b>'
            "<p>These figures were produced by the deterministic reference planner. They "
            "measure the evaluation architecture and its ablations, with planner competence "
            "and defect rates prescribed by configuration. They are not a measurement of any "
            "language model and must not be reported as one. Re-run with a model backend "
            "(<code>--model claude-opus-5</code>) for a model result.</p></div>"
        )

    parts.append(_kpi_section(report))
    parts.append(_interval_section(report))
    parts.append(_partition_section(report))
    parts.append(_tier_section(report))
    parts.append(_operations_section(report))
    parts.append(_taxonomy_section(report))
    parts.append(_comparison_section(report))
    parts.append(_notes_section(report))
    parts.append(_fingerprint_section(report))
    parts.append(_skipped_section(report))
    parts.append(_footer())
    parts.append("</div>")

    body = "\n".join(parts)
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>CallBench-Email — evaluation ledger</title>"
        f"<style>{_CSS}</style></head><body>{body}</body></html>"
    )


def _kpi_section(report: RunReport) -> str:
    head = (
        "<tr><th class='name'>System</th><th>Tool selection</th><th>Argument exact</th>"
        "<th>Schema validity</th><th>Plan success</th><th>Final state</th>"
        "<th>Fabrication</th><th>Unsafe action</th><th>Mean calls</th>"
        "<th>Trust</th><th>Score</th></tr>"
    )
    rows: list[str] = []
    for metrics in report.systems:
        composite = metrics.composite.point if metrics.composite else 0.0
        score_cls = "ok" if composite >= 80 else "warn" if composite >= 55 else "bad"
        rows.append(
            "<tr>"
            f"<td class='name'>{html.escape(metrics.system)}</td>"
            f"<td>{_pct(metrics.rates.get('tool_selection_accuracy'))}</td>"
            f"<td>{_pct(metrics.rates.get('argument_exact_match'))}</td>"
            f"<td>{_pct(metrics.rates.get('schema_validity_rate'))}</td>"
            f"<td>{_pct(metrics.rates.get('plan_success_rate'))}</td>"
            f"<td>{_pct(metrics.rates.get('state_transition_accuracy'))}</td>"
            f"<td>{_pct(metrics.rates.get('fabrication_rate'), False)}</td>"
            f"<td>{_pct(metrics.rates.get('unsafe_action_rate'), False)}</td>"
            f"<td class='dim'>{metrics.mean_tool_calls:.2f}</td>"
            f"<td class='{_trust_cls(metrics)}'>{_trust_value(metrics)}</td>"
            f"<td class='{score_cls}'>{composite:.1f}</td>"
            "</tr>"
        )
    return (
        "<section><h2>Primary KPIs</h2><div class='scroll'><table><thead>"
        f"{head}</thead><tbody>{''.join(rows)}</tbody></table></div>"
        "<p class='legend'>Point estimates. <b>Trust</b> is a weighted rate for ranking; "
        "<b>Score</b> is the safety-weighted composite after absolute penalties. Where the "
        "two disagree, the score is describing a tail the trust score averaged away — and "
        "that disagreement is itself a finding.</p></section>"
    )


def _interval_section(report: RunReport) -> str:
    rows: list[str] = []
    for metrics in report.systems:
        overall = metrics.rates.get("overall_pass_rate")
        unsafe = metrics.rates.get("unsafe_action_rate")
        composite = metrics.composite
        rows.append(
            "<tr>"
            f"<td class='name'>{html.escape(metrics.system)}</td>"
            f"<td>{_bar(overall) if overall else ''}</td>"
            f"<td>{_ci_text(overall)}</td>"
            f"<td>{_bar(unsafe, higher_is_better=False) if unsafe else ''}</td>"
            f"<td>{_ci_text(unsafe)}</td>"
            f"<td>{_ci_text(composite, scale=1.0, unit='')}</td>"
            "</tr>"
        )
    return (
        "<section><h2>Confidence intervals</h2><div class='scroll'><table><thead>"
        "<tr><th class='name'>System</th><th>Pass rate</th><th>95% CI</th>"
        "<th>Unsafe rate</th><th>95% CI</th><th>Composite 95% CI</th></tr>"
        f"</thead><tbody>{''.join(rows)}</tbody></table></div>"
        "<p class='legend'>Wilson score intervals for rates; percentile bootstrap "
        "(2000 resamples, seeded) for the composite.</p></section>"
    )


def _ci_text(interval: Interval | None, *, scale: float = 100.0, unit: str = "%") -> str:
    if interval is None:
        return "<span class='dim'>·</span>"
    return (
        f"{interval.point * scale:.1f}{unit} "
        f"<span class='dim'>[{interval.low * scale:.1f}, {interval.high * scale:.1f}]</span>"
    )


def _partition_section(report: RunReport) -> str:
    partitions = sorted({p for m in report.systems for p in m.by_partition})
    if not partitions:
        return ""
    head = "".join(f"<th>{html.escape(p)}</th>" for p in partitions)
    rows: list[str] = []
    for metrics in report.systems:
        cells: list[str] = []
        for partition in partitions:
            stats = metrics.by_partition.get(partition)
            if stats is None:
                cells.append("<td class='dim'>·</td>")
                continue
            rate = stats["pass_rate"]
            cells.append(f"<td class='{_cls(rate)}'>{rate * 100:.1f}%</td>")
        rows.append(
            f"<tr><td class='name'>{html.escape(metrics.system)}</td>{''.join(cells)}</tr>"
        )
    return (
        "<section><h2>Pass rate by partition</h2><div class='scroll'><table><thead>"
        f"<tr><th class='name'>System</th>{head}</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div></section>"
    )


def _taxonomy_section(report: RunReport) -> str:
    codes: dict[str, dict[str, int]] = {}
    for metrics in report.systems:
        for code, count in metrics.taxonomy.items():
            codes.setdefault(code, {})[metrics.system] = count
    if not codes:
        return ""
    head = "".join(f"<th>{html.escape(m.system)}</th>" for m in report.systems)
    rows: list[str] = []
    for code in sorted(codes, key=sort_key):
        spec = BY_CODE.get(code)
        cls = "crit" if code in SAFETY_CRITICAL else ""
        cells = []
        for metrics in report.systems:
            count = codes[code].get(metrics.system, 0)
            cells.append(
                f"<td class='{cls if count else 'zero'}'>{count if count else '·'}</td>"
            )
        rows.append(
            f"<tr><td class='name {cls}'>{html.escape(spec.family_code if spec else code)}</td>"
            f"<td class='name dim'>{html.escape(spec.family.value if spec else '—')}</td>"
            f"<td class='name dim'>{html.escape(spec.title if spec else '—')}</td>"
            f"{''.join(cells)}</tr>"
        )
    return (
        "<section><h2>Failure taxonomy</h2><div class='scroll'><table><thead>"
        f"<tr><th class='name'>Code</th><th class='name'>Family</th>"
        f"<th class='name'>Failure</th>{head}</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>"
        "<p class='legend'>Magenta codes are safety-critical: they carry hard score "
        "penalties and are reported separately from accuracy. Family codes are shown "
        "here; the stable identifiers (T01…) are in results.json.</p></section>"
    )


def _trust_value(metrics: object) -> str:
    trust = getattr(metrics, "trust", None)
    return f"{trust.score:.1f}" if trust else "·"


def _trust_cls(metrics: object) -> str:
    trust = getattr(metrics, "trust", None)
    if trust is None:
        return "dim"
    return "ok" if trust.score >= 90 else "warn" if trust.score >= 70 else "bad"


def _tier_section(report: RunReport) -> str:
    tiers = sorted({t for m in report.systems for t in m.by_tier})
    if len(tiers) < 2:
        return ""
    head = "".join(f"<th>{html.escape(t)}</th>" for t in tiers)
    rows: list[str] = []
    for metrics in report.systems:
        cells: list[str] = []
        for tier in tiers:
            stats = metrics.by_tier.get(tier)
            if stats is None:
                cells.append("<td class='dim'>·</td>")
                continue
            rate = stats["pass_rate"]
            cells.append(f"<td class='{_cls(rate)}'>{rate * 100:.1f}%</td>")
        rows.append(f"<tr><td class='name'>{html.escape(metrics.system)}</td>{''.join(cells)}</tr>")
    return (
        "<section><h2>Pass rate by difficulty tier</h2><div class='scroll'><table><thead>"
        f"<tr><th class='name'>System</th>{head}</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>"
        "<p class='legend'>Tiers cut across splits, so a mixed split is still analysable by "
        "difficulty. Splits decide who may see a task; tiers decide what it is.</p></section>"
    )


def _operations_section(report: RunReport) -> str:
    rows: list[str] = []
    priced = False
    for metrics in report.systems:
        lat = metrics.latency
        cost = metrics.cost
        if cost is not None and cost.priced:
            priced = True
        rows.append(
            "<tr>"
            f"<td class='name'>{html.escape(metrics.system)}</td>"
            f"<td>{lat.planning_ms:.2f}</td>" if lat else "<td class='dim'>·</td>"
        )
        rows[-1] += (
            f"<td>{lat.execution_ms:.2f}</td><td>{lat.verification_ms:.2f}</td>"
            f"<td>{lat.repair_ms:.2f}</td><td>{lat.total_ms:.2f}</td>"
            f"<td>{lat.p95_total_ms:.2f}</td>" if lat else "<td class='dim'>·</td>" * 5
        )
        rows[-1] += (
            f"<td class='dim'>{metrics.retry_rate * 100:.1f}%</td>"
            + (
                f"<td>{cost.usd_per_case:.5f}</td>"
                if cost and cost.priced
                else "<td class='dim'>n/a</td>"
            )
            + "</tr>"
        )
    note = (
        "Cost uses the cached price table; override with --price-input/--price-output."
        if priced
        else "No model tokens were consumed, so there is no monetary cost to report."
    )
    return (
        "<section><h2>Latency and cost</h2><div class='scroll'><table><thead>"
        "<tr><th class='name'>System</th><th>Plan ms</th><th>Exec ms</th><th>Verify ms</th>"
        "<th>Repair ms</th><th>Total ms</th><th>p95 ms</th><th>Retries</th>"
        "<th>USD / case</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>"
        f"<p class='legend'>Latency is the median per stage, with p95 on the total — agent "
        f"latency is long-tailed by construction and a mean hides the tail. {note}</p></section>"
    )


def _fingerprint_section(report: RunReport) -> str:
    if not report.fingerprint:
        return ""
    components = report.fingerprint.get("components", {})
    environment = report.fingerprint.get("environment", {})
    rows = "".join(
        f"<tr><td class='name'>{html.escape(k)}</td><td class='dim'>{html.escape(str(v))}</td></tr>"
        for k, v in sorted(components.items())
    )
    env = ", ".join(f"{k} {v}" for k, v in sorted(environment.items()))
    return (
        "<section><h2>Reproducibility</h2>"
        f"<dl class='meta'><div><dt>replay id</dt><dd>"
        f"{html.escape(str(report.fingerprint.get('replay_id', '?')))}</dd></div>"
        f"<div><dt>environment</dt><dd>{html.escape(env)}</dd></div></dl>"
        f"<div class='scroll'><table><tbody>{rows}</tbody></table></div>"
        "<p class='legend'>Component hashes over the tool schemas, taxonomy, fixture "
        "generator, verifier, scoring weights, system configurations and dataset bytes. "
        "<code>callbench replay &lt;id&gt;</code> recomputes them against the current tree "
        "and reports which ones moved — turning \"I cannot reproduce this\" into a diff. "
        "Wall-clock, hostname and paths are deliberately excluded.</p></section>"
    )


def _comparison_section(report: RunReport) -> str:
    if not report.comparisons:
        return ""
    rows: list[str] = []
    for comparison in report.comparisons:
        significant = comparison.p_value < 0.05
        rows.append(
            "<tr>"
            f"<td class='name'>{html.escape(comparison.system)}</td>"
            f"<td class='name dim'>{html.escape(comparison.metric.replace('_', ' '))}</td>"
            f"<td>{comparison.baseline_rate * 100:.1f}%</td>"
            f"<td>{comparison.system_rate * 100:.1f}%</td>"
            f"<td class='dim'>{comparison.only_baseline}/{comparison.only_system}</td>"
            f"<td class='{'ok' if significant else 'dim'}'>{comparison.p_value:.4f}</td>"
            f"<td class='dim'>{comparison.effect:+.2f} {html.escape(comparison.effect_size)}</td>"
            "</tr>"
        )
    return (
        "<section><h2>Paired comparison vs callbench_full</h2><div class='scroll'><table><thead>"
        "<tr><th class='name'>System</th><th class='name'>Metric</th><th>Full</th><th>System</th>"
        "<th>Discordant</th><th>McNemar p</th><th>Effect (h)</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>"
        "<p class='legend'>Exact two-sided McNemar test over the shared task set. Discordant "
        "pairs are shown as full-only / system-only wins; concordant pairs carry no "
        "information and are excluded.</p></section>"
    )


def _notes_section(report: RunReport) -> str:
    if not report.notes:
        return ""
    items = "".join(f"<p>{html.escape(note)}</p>" for note in report.notes)
    return (
        "<section><h2>Interpretation</h2>"
        f"<div class='notice'><b>Read before quoting this table</b>{items}</div></section>"
    )


def _skipped_section(report: RunReport) -> str:
    if not report.skipped:
        return ""
    items = "".join(
        f"<tr><td class='name warn'>{html.escape(name)}</td>"
        f"<td class='name dim'>{html.escape(reason)}</td></tr>"
        for name, reason in report.skipped.items()
    )
    return (
        "<section><h2>Skipped configurations</h2><div class='scroll'><table><tbody>"
        f"{items}</tbody></table></div>"
        "<p class='legend'>Reported rather than silently omitted: a configuration that "
        "cannot produce signal is not a null finding.</p></section>"
    )


def _footer() -> str:
    return (
        "<footer><h3>How to read this report</h3><ul>"
        "<li>Pass requires <em>every</em> authoritative verification layer to pass: schema, "
        "execution, state transition, and the deterministic semantic oracle.</li>"
        "<li>The advisory model judge, when enabled, is recorded but excluded from pass/fail. "
        "A benchmark graded by a language model measures agreement, not correctness.</li>"
        "<li>Unsafe action rate counts cases carrying any safety-critical taxonomy code. It is "
        "never averaged into accuracy.</li>"
        "<li>The hidden partition uses a renamed tool catalogue and is held outside the "
        "repository. A system that scores well elsewhere and poorly there has learned tool "
        "names rather than tool semantics.</li>"
        "</ul><h3>Reproduction</h3>"
        "<ul><li><code>make dataset</code> regenerates every partition from the seed.</li>"
        "<li><code>make bench</code> reproduces this report.</li>"
        "<li><code>results.json</code> beside this file carries every interval, every "
        "taxonomy count and every paired comparison; <code>cases.jsonl</code> carries "
        "one streamable record per case.</li></ul></footer>"
    )


def write(report: RunReport, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(report), encoding="utf-8")
    return path


def write_json(report: RunReport, path: Path) -> Path:
    """Write the reviewable summary: metrics, intervals, comparisons, notes.

    Per-case traces are deliberately *not* included. Inlining them produced a
    163 MB file for a 22,000-case run, which is not a machine-readable result —
    it is an artefact nobody opens. They go to a JSONL sidecar instead, where
    they can be streamed line by line.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report.to_dict(include_cases=False), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return path


def write_cases_jsonl(report: RunReport, path: Path) -> Path:
    """One JSON object per case, streamable and greppable."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for system, results in sorted(report.results.items()):
            for result in results:
                payload = result.to_json()
                payload["system"] = system
                handle.write(json.dumps(payload, sort_keys=True) + "\n")
    return path
