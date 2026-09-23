# SPDX-License-Identifier: Apache-2.0
"""The Plannotation validator: schema, references, geometry, provenance, model, veraPDF.

::

    plannotation validate file.pdf|plannotations.json [--ifc model.ifc] [--strict]
                          [--report md|json]

One entry point, :func:`validate`, which takes a plannotated PDF, a sidecar, a bare
plannotation, an array of plannotations or an index, and returns a
:class:`~plannotation.validate.report.Report`. Two rule engines for a single page, for a
caller that has already read one: :func:`check_page_document` for the rules that must
see the parsed JSON, and :func:`check_plannotation` for everything the loaded model can
answer. ``tools/check_fixtures.py`` is that caller, and it calls these rather than
keeping its own copy of any rule, which is the whole point of the arrangement.

The severity model
------------------
Section 4.4 of the specification gives a validator two classifications and requires it
to use one of them for every finding.

**An error is a violation of a MUST.** The document says something the specification
forbids, and no tolerance or judgement is involved in noticing it: a reference that
resolves to nothing, a bounding box whose corners are the wrong way round, a transform
that cannot be inverted, an inferred value that does not say how sure it is.

**A warning is a violation of a SHOULD, or a finding that rests on a tolerance or a
heuristic.** 4.4 gives the canonical example itself -- an element's bounding box
falling outside its viewport's, which is exactly what a tag drawn in the margin beside
the view looks like. A warning is a thing worth a person's attention that the validator
cannot be sure about, and a plannotation with warnings and no errors **is
conforming**. The validator says so: it exits 0.

Every rule's severity is decided once, in :mod:`plannotation.validate.codes`, with the
reasoning recorded beside it. No module decides a severity at a call site, so one rule
cannot be an error in one place and a warning in another.

Exit codes, and why they are what they are
------------------------------------------
This mapping is a public contract. Scripts will branch on it and CI jobs will gate on
it, so it is stated here and will not move without a major version.

======  =======================================================================
Code    Meaning
======  =======================================================================
``0``   The input was validated and has no errors. It may have warnings: 4.4
        says a plannotation with warnings and no errors is conforming, so
        reporting anything else here would contradict the specification.
``1``   The input was validated and has at least one error. With ``--strict``,
        also when it has at least one warning -- a project may hold its own
        drawings to the SHOULDs, and that is the switch for it. ``--strict``
        never changes a finding's recorded severity, only the verdict drawn
        from the set, so a ``--strict`` report and an ordinary one over the
        same file differ in exactly one field.
``2``   The input could not be validated at all. It does not exist, cannot be
        read, is not a Plannotation document, carries no Plannotation data, or a
        check the caller explicitly asked for could not be made -- ``--ifc``
        without ifcopenshell, ``--verapdf`` without a runnable veraPDF.
======  =======================================================================

The third code is the one that earns its keep. Without it, a missing optional
dependency would either fail the document, which blames the drawing for the
toolchain, or pass it, which reports a check that was never made. Neither is true, and
a pipeline needs to tell a broken drawing from a broken machine. The same reasoning is
why a check that was requested is never silently skipped: it is why
:class:`plannotation.errors.MissingExtraError` and
:class:`plannotation.errors.ExternalToolError` exist, and why what the validator did and
did not do is printed in the report under "Notes" rather than left to be assumed. "No
errors" and "no cross-check" look identical in a findings list and are not the same
statement.

A validator is a reader
-----------------------
4.4 opens with the one prohibition that has no exception: *a validator MUST NOT modify
the document or the plannotation it validates, under any circumstance, including to
repair an error it has just reported.* Nothing in this package opens a file for writing.

Section 9 binds it as well, because a validator reads attacker-supplied input by
definition. A PDF is read through the bounded reader in :mod:`plannotation.pdf.embed`,
never around it; a JSON file is bounded by its size on the filesystem before a byte of
it is parsed; the number of schema violations reported and the length of anything
quoted back from the document are both capped, because both are the document's to
choose.
"""

from __future__ import annotations

from plannotation.validate.codes import RULES, Rule, rule_inventory
from plannotation.validate.report import (
    REPORT_VERSION,
    Finding,
    PageSummary,
    Report,
    Severity,
    render_json,
    render_json_text,
    render_markdown,
)
from plannotation.validate.runner import check_page_document, check_plannotation, validate

__all__ = [
    "REPORT_VERSION",
    "RULES",
    "Finding",
    "PageSummary",
    "Report",
    "Rule",
    "Severity",
    "check_page_document",
    "check_plannotation",
    "render_json",
    "render_json_text",
    "render_markdown",
    "rule_inventory",
    "validate",
]
