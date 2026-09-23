# SPDX-License-Identifier: Apache-2.0
"""The rule catalogue: every code the validator can emit, in one place.

A report is something people grep, script against and gate a pipeline on. That only
works if the identifiers hold still, so every rule this validator applies has a code
here, and a finding is built by naming one rather than by spelling out a severity and a
specification reference at the call site.

The scheme
----------
``PL-<FAMILY>-<NNN>``: a fixed prefix, a three-letter family, and a three-digit number
that is allocated once and never reused.

===========  ============================================================
Family       What it covers
===========  ============================================================
``PL-SCH``   Schema validity and the declared format version (rule 1)
``PL-REF``   Identity and references, SPEC 4.5 (rule 2)
``PL-GEO``   Geometry, SPEC 3 (rule 3)
``PL-PRV``   Provenance, SPEC 4.6 (rule 5)
``PL-CAR``   Carrier consistency, SPEC 6
``PL-IFC``   Cross-check against an IFC model (rule 4)
``PL-PDF``   The optional veraPDF pass (rule 7)
===========  ============================================================

Three properties make the scheme worth having, and all three are obligations on whoever
edits this file:

1. **A code is permanent.** A rule that is withdrawn keeps its number, which is
   retired rather than recycled; a rule that is split keeps its number for the half
   that kept the meaning and the other half takes a new one. A CI job that suppresses
   ``PL-GEO-005`` must never find that number meaning something else after an upgrade.
2. **A code's severity may only get weaker.** Promoting a warning to an error inside a
   minor release turns a passing pipeline red on a document that did not change.
3. **The numbers are ordered by the order the rules run in**, family by family, so that
   reading the catalogue top to bottom reads the validator in the order it works.

Where a finding points
----------------------
A finding's ``path`` names the **subject of the rule**, which is not always the member
that happens to hold the wrong number. Where a rule is about one member -- a bounding
box that leaves the page -- the pointer names that member. Where it is about a
relationship among several -- ``plane.origin`` against ``storey.elevation`` plus
``cutHeight``, a dimension's ``value`` against its ``geometry`` through its viewport's
``scale`` -- the pointer names the object that carries them all. Pointing at one of
several would blame it, and 3.6 says in terms that a writer which cannot satisfy the
cut-height rule "has misplaced one of the three" without saying which.

The severity of each rule is decided here and justified in its ``why`` line. The
governing distinction is section 4.4's: an error is a violation of a MUST, and a
warning is a violation of a SHOULD or a finding that rests on a tolerance or a
heuristic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from plannotation.validate.report import Finding, Severity

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["MAX_MESSAGE_CHARS", "RULES", "Rule", "finding", "rule_inventory"]

#: The most characters one finding's message may carry.
#:
#: Section 9.3 (6) requires a reader to bound the work it does reporting a failure, and
#: the length of anything quoted back from the document is the document's to choose. A
#: message is also promised to be one line, so :func:`finding` collapses whitespace as
#: well: a pydantic refusal arrives as a paragraph and would otherwise break every
#: report format this package writes.
MAX_MESSAGE_CHARS: Final = 600


@dataclass(frozen=True)
class Rule:
    """One rule the validator applies.

    Attributes:
        code: The stable identifier, such as ``PL-GEO-003``.
        severity: Error or warning, decided once here rather than at the call site.
        summary: The rule in one line, as a person would state it.
        reference: The clause of the specification, or of the design brief, it comes
            from.
        why: Why it carries the severity it does. Recorded because the error/warning
            line is the validator's most consequential design decision and the one most
            likely to be argued with.
    """

    code: str
    severity: Severity
    summary: str
    reference: str
    why: str


def _rule(code: str, severity: Severity, summary: str, reference: str, why: str) -> Rule:
    """Build one catalogue entry.

    Args:
        code: The stable identifier.
        severity: Error or warning.
        summary: The rule in one line.
        reference: The clause it comes from.
        why: Why it carries that severity.

    Returns:
        The rule.
    """
    return Rule(code=code, severity=severity, summary=summary, reference=reference, why=why)


_ERROR: Final = Severity.ERROR
_WARNING: Final = Severity.WARNING

_CATALOGUE: Final = (
    # -- Rule 1: schema and version -----------------------------------------
    _rule(
        "PL-SCH-001",
        _ERROR,
        "the document validates against its JSON Schema",
        "SPEC 4.3 (1), 4.4 (2)",
        "Schema validity is the floor. A reader must treat an invalid label as absent, "
        "so nothing below this rule can be relied on at all.",
    ),
    _rule(
        "PL-SCH-002",
        _ERROR,
        "the model accepts a document the schema accepted",
        "SPEC 4.4 (2)",
        "A document the schema admits and the reference models refuse is unreadable in "
        "practice whatever the schema says, and the gap is a defect in one of the two.",
    ),
    _rule(
        "PL-SCH-003",
        _ERROR,
        "the declared format version is one this validator implements",
        "SPEC 4.3 (3), 4.4 (1)",
        "A reader must treat an unknown version as absent rather than parse it partly, "
        "so a version this validator does not implement ends the examination.",
    ),
    # -- Rule 2: identity and references -------------------------------------
    _rule(
        "PL-REF-001",
        _ERROR,
        "every localId on a page is unique across viewports, elements and annotations",
        "SPEC 4.5",
        "MUST. References do not say which collection they point into, so a repeated "
        "id makes every reference to it ambiguous.",
    ),
    _rule(
        "PL-REF-002",
        _ERROR,
        "an element's or annotation's viewport names a viewport in this label",
        "SPEC 4.5",
        "MUST. Without the viewport there is no transform, no plane and no scale, so "
        "the item's paper geometry means nothing beyond the page.",
    ),
    _rule(
        "PL-REF-003",
        _ERROR,
        "shows.element names an element in this label",
        "SPEC 4.5",
        "MUST. An unresolvable link is the cheapest way to look linked, and the level "
        "an annotation lifts a label to is graded on its links.",
    ),
    _rule(
        "PL-REF-004",
        _ERROR,
        "every measures entry names something in this label",
        "SPEC 4.5",
        "MUST, for the same reason as PL-REF-003.",
    ),
    _rule(
        "PL-REF-005",
        _ERROR,
        "a measures entry names an element, a grid or a level",
        "SPEC 4.5",
        "MUST. The table in 4.5 is exhaustive: a dimension between two callouts is not "
        "a measurement of anything.",
    ),
    _rule(
        "PL-REF-006",
        _ERROR,
        "target.pdfPage is a page of this document",
        "SPEC 4.5",
        "MUST: pdfPage is a zero-based index within the same document and MUST be less "
        "than its page count.",
    ),
    _rule(
        "PL-REF-007",
        _ERROR,
        "a target.viewportId aimed at this sheet resolves on it",
        "SPEC 4.5",
        "MUST. viewportId is read in the targeted page; where that page is this one, it "
        "is an ordinary reference within the page and must resolve like any other.",
    ),
    _rule(
        "PL-REF-008",
        _ERROR,
        "page.index is the page the label is attached to",
        "SPEC 4.5",
        "MUST. A reader is told to trust the attachment over the claim, which is a rule "
        "for surviving the defect and not permission to ship it.",
    ),
    _rule(
        "PL-REF-009",
        _WARNING,
        "shows.property is accompanied by a shows.element to read it from",
        "SPEC 4.5",
        "Warning: the specification states no MUST, and a property named with no "
        "element is useless rather than misleading.",
    ),
    _rule(
        "PL-REF-010",
        _WARNING,
        "two labelled pages do not print the same sheet.id",
        "SPEC 4.5",
        "Warning: 4.5 makes uniqueness a SHOULD and tells a reader how to resolve a "
        "target when it does not hold.",
    ),
    _rule(
        "PL-REF-011",
        _WARNING,
        "an annotation of a type that usually links carries a link",
        "SPEC 4.1",
        "Warning, and 4.1 asks for exactly that: it is a quality signal, and making it "
        "an error would let one unlinked dimension among fifty condemn a sheet.",
    ),
    _rule(
        "PL-REF-012",
        _WARNING,
        "a dotted shows.property names a property set the element carries",
        "SPEC 4.5; design brief 8 (2)",
        "Warning. The design brief asks that shows resolve and a property named on an "
        "element that does not carry it is a false statement about the page, but the "
        "schema calls shows.property free text and no MUST covers it -- and the rule "
        "only applies to the spellings this validator recognises as a property-set "
        "reference, which is a heuristic. 4.4 files both as warnings.",
    ),
    # -- Rule 3: geometry -----------------------------------------------------
    _rule(
        "PL-GEO-001",
        _ERROR,
        "the label's page block matches the page it is attached to",
        "SPEC 3.1, 3.7, 6.5.5 (2)",
        "MUST. A label that misstates its page reads every coordinate in it against the "
        "wrong sheet, which is worse than carrying no label.",
    ),
    _rule(
        "PL-GEO-002",
        _ERROR,
        "every bbox is ordered x0 <= x1, y0 <= y1",
        "SPEC 3.4",
        "MUST, and 3.4 says so in terms: the ordering cannot be expressed in JSON "
        "Schema, so schema validity does not imply it and a validator must check it.",
    ),
    _rule(
        "PL-GEO-003",
        _ERROR,
        "every bbox lies on the page",
        "SPEC 4.4 (6)",
        "MUST. 4.4 lists bounding boxes within the page among the geometric rules a "
        "validator must report violations of.",
    ),
    _rule(
        "PL-GEO-004",
        _ERROR,
        "drawn geometry lies within the bbox that carries it",
        "SPEC 3.4",
        "MUST, to within the 0.5 mm geometric tolerance. A box that does not contain "
        "what it bounds cannot be used to find anything.",
    ),
    _rule(
        "PL-GEO-005",
        _WARNING,
        "an item's bbox lies within the bbox of the viewport it names",
        "SPEC 4.4; design brief 8 (3)",
        "Warning, and both sources say so explicitly: a tag or a dimension drawn in the "
        "margin beside its viewport is ordinary drafting, not a defect.",
    ),
    _rule(
        "PL-GEO-006",
        _ERROR,
        "paperToPlane is invertible",
        "SPEC 3.5",
        "MUST. A zero determinant collapses the viewport to a line, and no reader can "
        "map a plane coordinate back to the paper it was drawn on.",
    ),
    _rule(
        "PL-GEO-007",
        _ERROR,
        "paperToPlane does not mirror the view",
        "SPEC 3.5, 6.2",
        "MUST, and 3.5 says a validator MUST report a negative determinant as an error: "
        "orientation is already carried by the plane axes, so a mirror contradicts them.",
    ),
    _rule(
        "PL-GEO-008",
        _ERROR,
        "a similarity paperToPlane agrees with the viewport's scale to within 0.1 %",
        "SPEC 3.5",
        "MUST. Where the linear part is not a similarity no scale can be recovered and "
        "3.5 forbids the comparison, so the rule is not applied there at all.",
    ),
    _rule(
        "PL-GEO-009",
        _WARNING,
        "a dimension's value matches the length it is drawn at, through its scale",
        "SPEC 3.4; design brief 8 (3)",
        "Warning. It rests on a tolerance and on the assumption that the dimension line "
        "is drawn to the full run it measures, which an abbreviated or off-scale "
        "dimension legitimately breaks; 4.4 files such findings as warnings.",
    ),
    _rule(
        "PL-GEO-010",
        _ERROR,
        "a plane's axes are unit vectors",
        "SPEC 3.6",
        "MUST, to within 1e-6. Scale is carried by paperToPlane, so an axis of another "
        "length states it a second time and the two statements can disagree.",
    ),
    _rule(
        "PL-GEO-011",
        _ERROR,
        "a plane's axes are mutually orthogonal",
        "SPEC 3.6",
        "MUST, to within 1e-6. A non-orthogonal pair describes a sheared view that "
        "P = origin + X*xAxis + Y*yAxis does not mean.",
    ),
    _rule(
        "PL-GEO-012",
        _ERROR,
        "a cut view's plane origin sits at storey.elevation + cutHeight along the normal",
        "SPEC 3.6",
        "MUST, and 3.6 says a validator MUST check it: for a cut view the drawing plane "
        "is the cutting plane, so a writer that cannot satisfy it has misplaced one of "
        "the three values.",
    ),
    _rule(
        "PL-GEO-013",
        _ERROR,
        "a viewport carrying paperToPlane belongs to a label that declares model.lengthUnit",
        "SPEC 3.5",
        "MUST. The transform's output is in the model's length unit, and a reader that "
        "does not know the unit may not report any distance derived from it as a length.",
    ),
    # -- Rule 5: provenance ---------------------------------------------------
    _rule(
        "PL-PRV-001",
        _ERROR,
        "an item whose effective provenance is inferred carries a confidence",
        "SPEC 4.6.5",
        "MUST, and 4.6.5 names the validator as the place it is enforced because the "
        "schema cannot express it. An inferred value that does not say how sure it is "
        "has withheld the thing that makes it safe to use.",
    ),
    _rule(
        "PL-PRV-002",
        _ERROR,
        "the top-level provenance is the aggregate of the page's items",
        "SPEC 4.6.4",
        "MUST. The aggregate is what a reader consults before deciding how much of a "
        "label it may present as fact.",
    ),
    _rule(
        "PL-PRV-003",
        _ERROR,
        "a mixed label records provenance on every element and annotation",
        "SPEC 4.6.4",
        "MUST. Under 4.6.3 silence in a mixed label reads as inferred, so a writer that "
        "leaves it out has made a claim it did not mean.",
    ),
    _rule(
        "PL-PRV-004",
        _WARNING,
        "an authored item does not carry a confidence",
        "SPEC 4.6.5",
        "Warning, and 4.6.5 asks for exactly that: the member has no meaning on an "
        "authored value, but rejecting a label over untidiness would serve nobody.",
    ),
    _rule(
        "PL-PRV-005",
        _WARNING,
        "an element or annotation does not declare itself mixed",
        "SPEC 4.6.2",
        "Warning: 4.6.2 is a SHOULD NOT and says so, and a conforming reader MUST "
        "accept the value and treat the object as containing inferred content.",
    ),
    _rule(
        "PL-PRV-006",
        _ERROR,
        "the index's provenance aggregates the same way over every page",
        "SPEC 4.6.4",
        "MUST. Only reported when every page the index lists is in front of the "
        "validator, since otherwise there is nothing to aggregate over.",
    ),
    # -- Carrier consistency ---------------------------------------------------
    _rule(
        "PL-CAR-001",
        _ERROR,
        "the index records the conformance level the page label reaches",
        "SPEC 2.8, 4.1",
        "MUST. The index exists so that a reader need not open every attachment; an "
        "index that may misreport the one thing it carries has no purpose left.",
    ),
    _rule(
        "PL-CAR-002",
        _ERROR,
        "the index records the sheet id the page label prints",
        "SPEC 2.8, 4.5",
        "MUST, for the same reason as PL-CAR-001: the sheet number is how a person "
        "finds the page, and an index that names the wrong one sends them to it.",
    ),
    _rule(
        "PL-CAR-003",
        _WARNING,
        "an index entry's title and revision agree with the sheet's",
        "SPEC 2.8",
        "Warning. An index may legitimately be terser than the sheets it lists, so only "
        "a value that is present and different is reported, and neither is load-bearing.",
    ),
    _rule(
        "PL-CAR-004",
        _ERROR,
        "a sidecar's plannotation matches its index's and every page label's",
        "SPEC 6.5.1",
        "MUST, and 6.5.1 says a validator MUST report it as an error.",
    ),
    _rule(
        "PL-CAR-005",
        _ERROR,
        "a sidecar's pages and index.pages correspond one to one",
        "SPEC 6.5.1",
        "MUST, on the same line of 6.5.1.",
    ),
    _rule(
        "PL-CAR-006",
        _ERROR,
        "a sidecar's pages are ordered by page.index, ascending, without repeats",
        "SPEC 6.5.1",
        "MUST, on the same line of 6.5.1.",
    ),
    _rule(
        "PL-CAR-007",
        _ERROR,
        "at least one page label pairs with a page of the document",
        "SPEC 6.5.5 (3)",
        "MUST. A payload that pairs with nothing is a payload for another document, and "
        "6.5.5 requires a validator to report each of its three pairing rules as errors.",
    ),
    _rule(
        "PL-CAR-008",
        _WARNING,
        "a labelled PDF carries the XMP PDF Declaration",
        "SPEC 6.2, 6.3",
        "Warning: the declaration is a claim and never evidence -- 9.2 forbids a reader "
        "from treating it as validity -- so its absence costs discoverability, not "
        "meaning, and the payload reads perfectly without it.",
    ),
    # -- Rule 4: the IFC cross-check -------------------------------------------
    _rule(
        "PL-IFC-001",
        _ERROR,
        "every ifcGuid names an entity in the model",
        "Design brief 8 (4)",
        "Error: a GlobalId that resolves to nothing is a plain fact about two files "
        "that were given to the validator together, decided by no tolerance.",
    ),
    _rule(
        "PL-IFC-002",
        _ERROR,
        "an entity's class is the ifcClass claimed, or a subtype of it",
        "Design brief 8 (4)",
        "Error, and a plain fact for the same reason. The label may name a supertype -- "
        "IfcWall for an IfcWallStandardCase -- and nothing narrower.",
    ),
    _rule(
        "PL-IFC-003",
        _WARNING,
        "a dimension's value matches the distance re-measured in the model",
        "Design brief 8 (4)",
        "Warning. It rests on a tolerance -- 1 % or 5 mm, whichever is larger -- and on "
        "the heuristic that the distance between two elements is the distance between "
        "their bounding geometry projected on the viewport's plane.",
    ),
    # -- Rule 7: veraPDF --------------------------------------------------------
    _rule(
        "PL-PDF-001",
        _ERROR,
        "veraPDF finds the document conforming to the flavour it declares",
        "Design brief 8 (7)",
        "Error, but only when the caller asked for the pass: a document that declares a "
        "PDF/A flavour and does not meet it is broken as a PDF, before any label.",
    ),
)

#: Every rule, by code. Ordered as :data:`_CATALOGUE` orders them.
RULES: Final[Mapping[str, Rule]] = {entry.code: entry for entry in _CATALOGUE}


def finding(code: str, *, message: str, path: str, source: str) -> Finding:
    """Build a finding by naming the rule it violates.

    The severity and the specification reference come from the catalogue rather than
    from the call site, so that one rule cannot be reported as an error in one module
    and a warning in another.

    Args:
        code: The rule's code, which must be in :data:`RULES`.
        message: What was expected and what was found, in one line.
        path: A JSON Pointer into the document named by ``source``.
        source: Which document the pointer is into.

    Returns:
        The finding.

    Raises:
        KeyError: If ``code`` names no rule. Raised rather than invented: a code that
            is not in the catalogue is a code nobody can look up.
    """
    rule = RULES[code]
    return Finding(
        code=rule.code,
        severity=rule.severity,
        message=_one_line(message),
        path=path,
        source=source,
        rule=rule.summary,
        reference=rule.reference,
    )


def _one_line(message: str) -> str:
    """Collapse a message to one bounded line.

    Args:
        message: The message as its rule wrote it, which for a model refusal is a
            paragraph quoting the document.

    Returns:
        The message with every run of whitespace collapsed to one space, cut to
        :data:`MAX_MESSAGE_CHARS` with an ellipsis.
    """
    flattened = " ".join(message.split())
    if len(flattened) <= MAX_MESSAGE_CHARS:
        return flattened
    return flattened[: MAX_MESSAGE_CHARS - 3] + "..."


def rule_inventory() -> list[Rule]:
    """Return every rule the validator can report, in catalogue order.

    Returns:
        The rules. Intended for documentation and for a tool that needs to know what
        the validator checks without running it.
    """
    return list(_CATALOGUE)
