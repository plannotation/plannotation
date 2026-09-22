# SPDX-License-Identifier: Apache-2.0
"""Turn two run logs into the report, and the report into the README's table.

The report is Markdown under ``bench/results/<date>-<model>.md``: accuracy per
category under each condition, the difference, cost and latency, and every answer,
so a reader can check any number in it against what the model actually said.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from planlabel.bench.page import BENCH_DPI
from planlabel.bench.pricing import PRICES_AS_OF
from planlabel.bench.prompt import PROMPT_VERSION

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from planlabel.bench.runner import Record

#: The order categories are reported in; any other category follows, by name.
CATEGORY_ORDER: Final = ("count", "sheet", "dimension", "callout", "grid", "tag", "model")

README_START: Final = "<!-- BENCHMARK:START -->"
README_END: Final = "<!-- BENCHMARK:END -->"

_PERCENT: Final = 100.0

#: The longest an answer is shown in the report's answer table.
_CELL_WIDTH: Final = 80


@dataclass(frozen=True)
class Tally:
    """How many of a group of questions one condition got right.

    Attributes:
        correct: Right answers.
        total: Questions asked.
    """

    correct: int
    total: int

    @property
    def rate(self) -> float | None:
        """Return the share answered correctly, or None when nothing was asked."""
        return None if self.total == 0 else self.correct / self.total

    def __str__(self) -> str:
        """Return ``correct/total (pct%)``, or a dash when nothing was asked."""
        if self.rate is None:
            return "—"
        return f"{self.correct}/{self.total} ({self.rate * _PERCENT:.0f}%)"


def tally(records: Sequence[Record]) -> Tally:
    """Count right answers.

    Args:
        records: The records to count.

    Returns:
        The tally.
    """
    return Tally(sum(1 for record in records if record.correct), len(records))


def delta(plain: Tally, labelled: Tally) -> str:
    """Return the difference in accuracy, in percentage points.

    Args:
        plain: The plain condition's tally.
        labelled: The labelled condition's tally.

    Returns:
        A signed figure such as ``+40 pp``, or a dash if either is missing.
    """
    if plain.rate is None or labelled.rate is None:
        return "—"
    points = (labelled.rate - plain.rate) * _PERCENT
    return f"{points:+.0f} pp"


def categories(records: Sequence[Record]) -> list[str]:
    """Return the categories present, in report order.

    Args:
        records: Every record of both conditions.

    Returns:
        The category names.
    """
    present = {record.category for record in records}
    known = [name for name in CATEGORY_ORDER if name in present]
    return known + sorted(present - set(CATEGORY_ORDER))


def render_report(
    model: str, date: str, plain: Sequence[Record], labelled: Sequence[Record]
) -> str:
    """Write the full report.

    Args:
        model: The model the runs asked.
        date: The report's date, ``YYYY-MM-DD``.
        plain: The plain run's records; may be empty.
        labelled: The labelled run's records; may be empty.

    Returns:
        The report as Markdown.
    """
    everything = [*plain, *labelled]
    sheets = sorted({record.sheet for record in everything})
    lines = [
        f"# PlanLabel benchmark — `{model}`, {date}",
        "",
        (
            f"Questions about {len(sheets)} sample sheet(s) ({', '.join(sheets)}), asked "
            "under two conditions that differ only in whether the page label is supplied."
        ),
        "",
        f"- `plain`: the page rendered at {BENCH_DPI:.0f} dpi, and its extracted text.",
        (
            "- `labelled`: the same, then the PlanLabel page label as the result of a "
            "`get_page_label` tool call."
        ),
        (
            f"- Prompt version {PROMPT_VERSION}. Numbers are scored within 1 %, everything "
            "else exactly. Every answer key comes from the IFC model the sheet was drawn from."
        ),
        "",
        "## Accuracy",
        "",
        "| Category | Questions | `plain` | `labelled` | Difference |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for category in categories(everything):
        in_plain = [record for record in plain if record.category == category]
        in_labelled = [record for record in labelled if record.category == category]
        only = " (label only)" if any(r.requires_label for r in [*in_plain, *in_labelled]) else ""
        questions = max(len(in_plain), len(in_labelled))
        lines.append(
            f"| {category}{only} | {questions} | {tally(in_plain)} | {tally(in_labelled)} "
            f"| {delta(tally(in_plain), tally(in_labelled))} |"
        )
    drawing_plain = [record for record in plain if not record.requires_label]
    drawing_labelled = [record for record in labelled if not record.requires_label]
    lines += [
        _total_row("Answerable from the drawing", drawing_plain, drawing_labelled),
        _total_row("All questions", plain, labelled),
        "",
        (
            "Label-only questions ask for something the page does not print, such as an "
            "element's IFC GlobalId. They are counted in the last row and not in the one "
            "above it."
        ),
        "",
        "## Cost and latency",
        "",
        (
            "| Condition | Requests | Input | Cache write | Cache read | Output "
            "| Est. cost (USD) | Mean latency (s) |"
        ),
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    lines += [
        _cost_row(name, records) for name, records in (("plain", plain), ("labelled", labelled))
    ]
    lines += [
        "",
        (
            f"Costs are estimated from list prices as of {PRICES_AS_OF}, for the responses "
            "as first obtained; a re-run served from the response cache costs nothing."
        ),
        "",
        "## Answers",
        "",
        "| Question | Category | Expected | `plain` | `labelled` |",
        "| --- | --- | --- | --- | --- |",
    ]
    by_id = {record.id: record for record in plain}
    labelled_by_id = {record.id: record for record in labelled}
    for question_id in sorted({*by_id, *labelled_by_id}):
        first = by_id.get(question_id) or labelled_by_id[question_id]
        lines.append(
            f"| {question_id} | {first.category} | {_cell(_expected(first))} "
            f"| {_answer_cell(by_id.get(question_id))} "
            f"| {_answer_cell(labelled_by_id.get(question_id))} |"
        )
    return "\n".join(lines) + "\n"


def _total_row(name: str, plain: Sequence[Record], labelled: Sequence[Record]) -> str:
    """Return one bold total row of the accuracy table.

    Args:
        name: The row's label.
        plain: The plain records it counts.
        labelled: The labelled records it counts.

    Returns:
        The row.
    """
    questions = max(len(plain), len(labelled))
    return (
        f"| **{name}** | **{questions}** | **{tally(plain)}** | **{tally(labelled)}** "
        f"| **{delta(tally(plain), tally(labelled))}** |"
    )


def _cost_row(condition: str, records: Sequence[Record]) -> str:
    """Return one row of the cost table.

    Args:
        condition: The condition's name.
        records: Its records.

    Returns:
        The row, with dashes when the condition was not run.
    """
    if not records:
        return f"| `{condition}` | 0 | — | — | — | — | — | — |"
    costs = [record.cost_usd for record in records]
    total_cost = "—" if any(c is None for c in costs) else f"{sum(c or 0.0 for c in costs):.2f}"
    latency = sum(record.latency_s for record in records) / len(records)
    usage = [record.usage for record in records]
    return (
        f"| `{condition}` | {len(records)} | {sum(u.input_tokens for u in usage):,} "
        f"| {sum(u.cache_creation_input_tokens for u in usage):,} "
        f"| {sum(u.cache_read_input_tokens for u in usage):,} "
        f"| {sum(u.output_tokens for u in usage):,} | {total_cost} | {latency:.1f} |"
    )


def _expected(record: Record) -> str:
    """Return a record's key as text.

    Args:
        record: The record.

    Returns:
        The expected answer, a list joined with commas.
    """
    if isinstance(record.expected, tuple):
        return ", ".join(record.expected)
    if isinstance(record.expected, float) and record.expected.is_integer():
        return str(int(record.expected))
    return str(record.expected)


def _answer_cell(record: Record | None) -> str:
    """Return a given answer marked right or wrong.

    Args:
        record: The record, or None when the condition did not ask the question.

    Returns:
        The table cell.
    """
    if record is None:
        return "—"
    mark = "✓" if record.correct else "✗"
    given = "_no answer_" if record.given is None else _cell(record.given)
    return f"{mark} {given}"


def _cell(text: str) -> str:
    """Make text safe inside a Markdown table cell.

    Args:
        text: Any text.

    Returns:
        The text on one line, with pipes escaped and length bounded.
    """
    flat = " ".join(text.split()).replace("|", "\\|")
    return flat if len(flat) <= _CELL_WIDTH else flat[: _CELL_WIDTH - 3] + "..."


def render_readme_section(
    model: str, report_path: str, plain: Sequence[Record], labelled: Sequence[Record]
) -> str:
    """Write what goes between the README's benchmark markers.

    Args:
        model: The model the runs asked.
        report_path: The full report, relative to the README.
        plain: The plain run's records.
        labelled: The labelled run's records.

    Returns:
        The section, markers excluded.
    """
    drawing = [
        [record for record in records if not record.requires_label] for records in (plain, labelled)
    ]
    only = [
        [record for record in records if record.requires_label] for records in (plain, labelled)
    ]
    total = max(len(plain), len(labelled))
    lines = [
        (
            f"Measured with `{model}` on the three sample sheets, {total} questions whose "
            "answers come from the IFC model each sheet was drawn from. Same model, same "
            "prompt, same page image and extracted text; `labelled` also gets the page "
            "label as a tool result. Full report, every answer included: "
            f"[{report_path}]({report_path})."
        ),
        "",
        "| Questions | `plain` (page render + text) | `labelled` (+ PlanLabel) | Difference |",
        "| --- | ---: | ---: | ---: |",
    ]
    lines.append(
        f"| Answerable from the drawing | {tally(drawing[0])} | {tally(drawing[1])} "
        f"| {delta(tally(drawing[0]), tally(drawing[1]))} |"
    )
    if only[0] or only[1]:
        lines.append(
            f"| Label only (IFC GlobalId) | {tally(only[0])} | {tally(only[1])} "
            f"| {delta(tally(only[0]), tally(only[1]))} |"
        )
    return "\n".join(lines)


def update_readme(readme: Path, section: str) -> bool:
    """Replace the README's benchmark section.

    Args:
        readme: The README.
        section: The new section, markers excluded.

    Returns:
        True if the file changed.

    Raises:
        ValueError: If the README does not have both markers, in order.
    """
    text = readme.read_text(encoding="utf-8")
    pattern = re.compile(re.escape(README_START) + r".*?" + re.escape(README_END), re.S)
    if pattern.search(text) is None:
        msg = f"{readme} has no {README_START} ... {README_END} section"
        raise ValueError(msg)
    updated = pattern.sub(lambda _: f"{README_START}\n{section}\n{README_END}", text, count=1)
    if updated == text:
        return False
    readme.write_text(updated, encoding="utf-8")
    return True
