"""Console design tokens.

The console is an operator surface, not a marketing surface. The rules it
follows:

* One accent colour. Everything else is graphite. Colour carries state
  (pass / fail / warn) and nothing else — never decoration, never emphasis.
* Numbers are tabular: fixed decimals, right-aligned, so columns compare by eye
  without reading.
* Labels are uppercase and letter-spaced; values are not. The eye finds the
  label by shape and the value by position.
* Hairline rules, no double borders, no rounded corners, no shading blocks.
* No emoji and no pictographs anywhere. Status is a single geometric glyph.
"""

from __future__ import annotations

from rich import box
from rich.style import Style
from rich.theme import Theme

# Graphite scale, lightest to darkest.
FG = "#E8EAED"
FG_MUTED = "#9AA0A6"
FG_DIM = "#6B7280"
LINE = "#2A2D33"
ACCENT = "#5E6AD2"
OK = "#4CB782"
WARN = "#E2B93B"
BAD = "#E5484D"
CRIT = "#D6409F"

THEME = Theme(
    {
        "cb.label": Style(color=FG_DIM, bold=True),
        "cb.value": Style(color=FG),
        "cb.muted": Style(color=FG_MUTED),
        "cb.dim": Style(color=FG_DIM),
        "cb.accent": Style(color=ACCENT),
        "cb.rule": Style(color=LINE),
        "cb.ok": Style(color=OK),
        "cb.warn": Style(color=WARN),
        "cb.bad": Style(color=BAD),
        "cb.crit": Style(color=CRIT, bold=True),
        "cb.header": Style(color=FG, bold=True),
        "cb.unit": Style(color=FG_DIM),
    }
)

#: A hairline table: header underline only, no verticals, no outer frame.
HAIRLINE = box.Box(
    "    \n"
    "    \n"
    " ── \n"
    "    \n"
    "    \n"
    "    \n"
    "    \n"
    "    \n"
)

#: A framed variant for the run header block.
FRAME = box.Box(
    "┌──┐\n"
    "│  │\n"
    "├──┤\n"
    "│  │\n"
    "├──┤\n"
    "├──┤\n"
    "│  │\n"
    "└──┘\n"
)

GLYPH_PASS = "▪"
GLYPH_FAIL = "▪"
GLYPH_BLOCK = "▪"


def label(text: str) -> str:
    """Uppercase, letter-spaced micro-label."""
    return " ".join(text.upper())


def status_style(passed: bool, unsafe: bool = False) -> str:
    if unsafe:
        return "cb.crit"
    return "cb.ok" if passed else "cb.bad"


def rate_style(value: float, *, higher_is_better: bool = True, warn: float = 0.9) -> str:
    if not higher_is_better:
        if value <= 0.0:
            return "cb.ok"
        return "cb.warn" if value < 0.02 else "cb.bad"
    if value >= warn:
        return "cb.ok"
    if value >= warn - 0.15:
        return "cb.warn"
    return "cb.bad"
