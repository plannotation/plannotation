# SPDX-License-Identifier: Apache-2.0
"""What a validation run produces, and the two ways it is rendered.

A report is the validator's whole output. Section 4.4 of the specification fixes most
of its contents -- the declared version and whether it is implemented, schema validity
with a machine-readable location for each failure, the conformance level reached, the
counts of viewports, elements and annotations, every referential, geometric and
provenance violation, and the validator's own identity so that a report can be
reproduced -- and this module is the shape those obligations take in code.

Two renderers, two audiences
----------------------------
:func:`render_markdown` writes for a person: a summary they can read at the top, then
the findings grouped by the page they belong to, worst first. :func:`render_json`
writes for a machine: one object, with a stable shape, described below and versioned by
its own ``reportVersion`` member.

The two are rendered from the same :class:`Report` and neither is derived from the
other. A Markdown report parsed by a script and a JSON report read by a person are both
mistakes this separation is meant to make unnecessary.

The JSON shape
--------------
Stable from 0.1 onwards. Members are never removed or given a new meaning within a
``reportVersion``; new members may be added, so a consumer MUST ignore what it does not
recognise::

    {
        "reportVersion": "1",
        "validator": {"name": "plannotation", "version": "0.1.0", "schema": "0.1"},
        "source": "TWP-202.pdf",
        "carrier": "pdf",
        "strict": false,
        "exitCode": 1,
        "counts": {"error": 2, "warning": 1, "finding": 3},
        "pages": [
            {
                "pageIndex": 4,
                "sheetId": "TWP-202",
                "level": "L3",
                "plannotation": "0.1",
                "implemented": true,
                "counts": {"viewports": 5, "elements": 7, "annotations": 12},
            }
        ],
        "findings": [
            {
                "code": "PL-GEO-003",
                "severity": "error",
                "message": "bbox [0, 0, 1200, 900] leaves the 1189 x 841 mm page",
                "path": "/elements/2/paperBBox",
                "source": "page 4",
                "rule": "a bounding box lies on the page",
                "reference": "SPEC 4.4",
            }
        ],
        "notes": ["target.pdfPage was not checked: no document to count pages in"],
    }

``path`` is a JSON Pointer (RFC 6901) into the page label the finding belongs to, or
into the index or the sidecar where the finding belongs to one of those; the empty
string points at the document itself. ``source`` says which document that is.
``findings`` is ordered: errors before warnings, then by page, then by code, then by
path -- so two runs over one input produce identical bytes, which is what makes a
report diffable in review.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Final

from plannotation import __version__
from plannotation.constants import SCHEMA_VERSION

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence
    from pathlib import Path

    from pydantic import JsonValue

    from plannotation.model import ConformanceLevel

__all__ = [
    "REPORT_VERSION",
    "Finding",
    "PageSummary",
    "Report",
    "Severity",
    "render_json",
    "render_json_text",
    "render_markdown",
]

#: Version of the JSON report shape described in this module's docstring.
#:
#: It is deliberately not the package version and not the schema version. The report is
#: this implementation's output rather than part of the format, and it changes when the
#: shape changes and at no other time.
REPORT_VERSION: Final = "1"


class Severity(StrEnum):
    """How much a finding matters.

    Two values and no more, because section 4.4 of the specification defines exactly
    two and requires a validator to classify every finding as one of them. An **error**
    is a violation of a MUST. A **warning** is a violation of a SHOULD, or a finding
    that depends on a tolerance or on a heuristic -- an element's bounding box falling
    outside its viewport's, which is legitimate for a tag drawn in the margin.

    A third level was considered and refused. Informational output -- the level
    reached, the counts, what was skipped for want of a document -- is not a finding
    about the label, and putting it in the same list would make ``counts.finding`` mean
    less rather than more. It goes in the report body instead.
    """

    ERROR = "error"
    WARNING = "warning"

    @property
    def rank(self) -> int:
        """Return the sort rank, worst first.

        Returns:
            0 for an error and 1 for a warning.
        """
        return 0 if self is Severity.ERROR else 1


@dataclass(frozen=True, order=False)
class Finding:
    """One violation, at one place, of one rule.

    Attributes:
        code: The rule's stable identifier, such as ``PL-GEO-003``. Stable across
            releases: a report is something people grep, script against and put in CI,
            and an identifier that moves makes all three impossible.
        severity: Error or warning, as :class:`Severity` defines them.
        message: What was expected and what was found, in one line, for a person.
        path: A JSON Pointer (RFC 6901) into the document named by ``source``. The
            empty string points at the document itself.
        source: Which document the pointer is into -- ``"page 4"``, ``"index"``,
            ``"sidecar"``, ``"plannotations.json"``.
        rule: The rule in one line, as :mod:`plannotation.validate.codes` states it.
        reference: The clause of the specification the rule comes from.
    """

    code: str
    severity: Severity
    message: str
    path: str
    source: str
    rule: str
    reference: str

    @property
    def sort_key(self) -> tuple[int, str, str, str]:
        """Return the key that orders findings deterministically.

        Returns:
            Severity rank, then source, then code, then path. Two runs over one input
            therefore produce byte-identical reports.
        """
        return (self.severity.rank, self.source, self.code, self.path)


@dataclass(frozen=True)
class PageSummary:
    """What was found on one page, apart from its findings.

    Section 4.4 requires a validator to report the declared version, whether it
    implements it, the conformance level reached and the counts of viewports, elements
    and annotations. This is those four, per page.

    Attributes:
        page_index: The page the label was found on, which for a PDF is the page it is
            attached to and not the ``page.index`` inside it -- section 4.5 makes the
            attachment the fact and ``page.index`` a claim.
        sheet_id: The sheet number as printed, or None when the label did not load.
        level: The level reached, or None when the label is invalid and therefore
            reaches no level at all.
        declared_version: The value of ``plannotation``, or None when there was none.
        implemented: Whether this validator implements that version.
        viewports: How many viewports the label declares.
        elements: How many elements.
        annotations: How many annotations.
    """

    page_index: int
    sheet_id: str | None
    level: ConformanceLevel | None
    declared_version: str | None
    implemented: bool
    viewports: int
    elements: int
    annotations: int


@dataclass(frozen=True)
class Report:
    """Everything one validation run has to say.

    Attributes:
        source: The file validated.
        carrier: ``"pdf"``, ``"sidecar"`` or ``"plannotations"`` -- the last being a bare page
            label or array of page labels, which is a payload with no carrier at all.
        pages: One summary per page label examined, ascending by page.
        findings: Every violation found, in :attr:`Finding.sort_key` order.
        notes: What the run did and did not do, in a person's words: every check that
            was skipped and why, and every optional check that ran and over what. A
            validator that silently skips a check it could not make reports a success
            it has not established, and one that silently makes an optional check
            leaves its reader unable to tell a clean cross-check from no cross-check.
        strict: Whether warnings were promoted to errors for the exit code.
    """

    source: Path
    carrier: str
    pages: tuple[PageSummary, ...] = ()
    findings: tuple[Finding, ...] = ()
    notes: tuple[str, ...] = ()
    strict: bool = False
    validator: Mapping[str, str] = field(
        default_factory=lambda: {
            "name": "plannotation",
            "version": __version__,
            "schema": SCHEMA_VERSION,
        }
    )

    @property
    def error_count(self) -> int:
        """Return how many findings are errors.

        Returns:
            The count of :attr:`Severity.ERROR` findings, regardless of
            :attr:`strict`.
        """
        return sum(1 for finding in self.findings if finding.severity is Severity.ERROR)

    @property
    def warning_count(self) -> int:
        """Return how many findings are warnings.

        Returns:
            The count of :attr:`Severity.WARNING` findings.
        """
        return sum(1 for finding in self.findings if finding.severity is Severity.WARNING)

    @property
    def failed(self) -> bool:
        """Report whether this run should be treated as a failure.

        Returns:
            True when there is at least one error, or -- under :attr:`strict` -- at
            least one finding of any severity.
        """
        return bool(self.error_count or (self.strict and self.warning_count))

    @property
    def exit_code(self) -> int:
        """Return the process exit code this report implies.

        Returns:
            1 when :attr:`failed`, and 0 otherwise. Exit code 2 belongs to an input
            that could not be validated at all, which produces no report: see
            :mod:`plannotation.validate`.
        """
        return 1 if self.failed else 0

    def with_findings(self, findings: Iterable[Finding]) -> Report:
        """Return a copy of this report carrying a different set of findings.

        Args:
            findings: The findings to carry. They are sorted into
                :attr:`Finding.sort_key` order.

        Returns:
            A new report; :class:`Report` is frozen.
        """
        ordered = tuple(sorted(findings, key=lambda item: item.sort_key))
        return Report(
            source=self.source,
            carrier=self.carrier,
            pages=self.pages,
            findings=ordered,
            notes=self.notes,
            strict=self.strict,
            validator=self.validator,
        )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def render_json(report: Report) -> dict[str, JsonValue]:
    """Render a report as the JSON document described in this module's docstring.

    Args:
        report: The report to render.

    Returns:
        A plain dictionary, ready for :func:`json.dumps`.
    """
    return {
        "reportVersion": REPORT_VERSION,
        "validator": dict(report.validator),
        "source": str(report.source),
        "carrier": report.carrier,
        "strict": report.strict,
        "exitCode": report.exit_code,
        "counts": {
            "error": report.error_count,
            "warning": report.warning_count,
            "finding": len(report.findings),
        },
        "pages": [_page_json(page) for page in report.pages],
        "findings": [_finding_json(finding) for finding in report.findings],
        "notes": list(report.notes),
    }


def render_json_text(report: Report) -> str:
    """Render a report as JSON text in Plannotation's canonical serialisation.

    Args:
        report: The report to render.

    Returns:
        UTF-8-safe JSON with sorted keys, a two-space indent and one trailing newline,
        matching the form every other JSON document this project writes takes.
    """
    document = render_json(report)
    return json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _page_json(page: PageSummary) -> dict[str, JsonValue]:
    """Render one page summary.

    Args:
        page: The summary.

    Returns:
        Its JSON form.
    """
    return {
        "pageIndex": page.page_index,
        "sheetId": page.sheet_id,
        "level": None if page.level is None else page.level.value,
        "plannotation": page.declared_version,
        "implemented": page.implemented,
        "counts": {
            "viewports": page.viewports,
            "elements": page.elements,
            "annotations": page.annotations,
        },
    }


def _finding_json(finding: Finding) -> dict[str, JsonValue]:
    """Render one finding.

    Args:
        finding: The finding.

    Returns:
        Its JSON form.
    """
    return {
        "code": finding.code,
        "severity": finding.severity.value,
        "message": finding.message,
        "path": finding.path,
        "source": finding.source,
        "rule": finding.rule,
        "reference": finding.reference,
    }


def render_markdown(report: Report) -> str:
    """Render a report as Markdown, for a person.

    Args:
        report: The report to render.

    Returns:
        Markdown text ending in one newline.
    """
    lines = [
        f"# Plannotation validation report: {report.source.name}",
        "",
        *_markdown_summary(report),
        "",
        *_markdown_pages(report.pages),
        "",
        *_markdown_findings(report.findings),
    ]
    if report.notes:
        lines += ["", "## Notes", ""]
        lines += [f"- {note}" for note in report.notes]
    lines += [
        "",
        "---",
        "",
        (
            f"Validated by {report.validator['name']} {report.validator['version']}, "
            f"Plannotation schema {report.validator['schema']}."
        ),
    ]
    return "\n".join(lines) + "\n"


def _markdown_summary(report: Report) -> list[str]:
    """Render the head of a Markdown report.

    Args:
        report: The report.

    Returns:
        The summary lines.
    """
    verdict = "FAIL" if report.failed else "PASS"
    promoted = " (warnings promoted to errors by --strict)" if report.strict else ""
    return [
        (
            f"**{verdict}** -- {report.error_count} error(s), "
            f"{report.warning_count} warning(s){promoted}; exit code {report.exit_code}."
        ),
        "",
        f"- Source: `{report.source}`",
        f"- Carrier: {report.carrier}",
        f"- Page labels examined: {len(report.pages)}",
    ]


def _markdown_pages(pages: Sequence[PageSummary]) -> list[str]:
    """Render the per-page table.

    Args:
        pages: The page summaries.

    Returns:
        The table lines, or a single line when there is nothing to tabulate.
    """
    if not pages:
        return ["## Pages", "", "No page label was read."]
    lines = [
        "## Pages",
        "",
        "| page | sheet | level | version | viewports | elements | annotations |",
        "| ---: | --- | --- | --- | ---: | ---: | ---: |",
    ]
    for page in pages:
        version = page.declared_version or "-"
        if page.declared_version is not None and not page.implemented:
            version += " (not implemented)"
        lines.append(
            f"| {page.page_index} | {page.sheet_id or '-'} | "
            f"{'none' if page.level is None else page.level.value} | {version} | "
            f"{page.viewports} | {page.elements} | {page.annotations} |"
        )
    return lines


def _markdown_findings(findings: Sequence[Finding]) -> list[str]:
    """Render the findings, grouped by the document they were found in.

    Args:
        findings: The findings, already ordered.

    Returns:
        The finding lines.
    """
    if not findings:
        return ["## Findings", "", "None."]
    lines = ["## Findings", ""]
    current: str | None = None
    for finding in findings:
        if finding.source != current:
            current = finding.source
            lines += [f"### {finding.source}", ""]
        location = f" at `{finding.path}`" if finding.path else ""
        lines.append(
            f"- **{finding.severity.value}** `{finding.code}`{location} "
            f"-- {finding.message} _({finding.reference})_"
        )
    return lines
