# SPDX-License-Identifier: Apache-2.0
"""Carrier consistency: the rules of section 6 that a validator must report.

Sections 1 to 4 define the payload and section 6 defines the ways it travels. Most of
section 6 binds writers and readers, but three passages bind a validator in terms, and
this module is those three:

* **6.5.1** -- a sidecar's ``plannotation`` must match its index's and every
  plannotation's, its ``pages`` must correspond one to one with ``index.pages``, and
  those pages must be ordered by ``page.index`` without repeats. "A validator MUST
  report each of these as an error."
* **6.5.5** -- a plannotation whose ``page.index`` is not a page of the document, or
  whose page dimensions differ from that page's by more than the geometric tolerance,
  is absent; a payload where no plannotation pairs with any page is absent entirely.
  "A validator MUST report each of the three as an error." The first two are
  PL-REF-008 and PL-GEO-001, which are checked per page in the modules that own them;
  the third is here, because only the whole carrier can fail it.
* **2.8** -- where the index and a plannotation disagree, the plannotation is correct.
  That rule tells a reader how to survive the disagreement; it does not make the
  index's claim true, and an index whose whole purpose is to save a reader from opening
  every attachment is worth nothing if it may misreport the two things it carries that
  the attachment also carries.

The declaration is deliberately only a warning. 9.2 forbids a reader from treating a
PDF Declaration as evidence that a payload is valid -- the declaration is a claim and
the schema is the check -- so its absence costs discoverability and not meaning, and
every plannotation in the document reads perfectly without it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from plannotation.constants import SCHEMA_VERSION
from plannotation.model import conformance_level
from plannotation.validate.codes import finding
from plannotation.validate.schema import json_pointer

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from plannotation.model import Plannotation, PlannotationIndex, Sidecar
    from plannotation.validate.report import Finding

__all__ = [
    "check_declaration",
    "check_index_against_plannotations",
    "check_page_identity",
    "check_pairing",
    "check_sidecar_consistency",
    "check_sidecar_versions",
]


def check_sidecar_versions(document: Mapping[str, object], *, source: str) -> list[Finding]:
    """Check 6.5.1's first agreement rule on the parsed sidecar.

    On the parsed document and not on the model, because the model cannot break it:
    ``plannotation`` is a ``Literal["0.1"]`` on the sidecar, on the index and on every
    plannotation, so a loaded sidecar's three members are the same string by
    construction. The rule is real all the same -- it is a MUST, and 6.5.1 names the
    validator as the place it is reported -- and a sidecar that pairs a 0.1 index with
    a 0.2 plannotation is exactly the file it exists for. Reported here, before the
    schema pass returns, so that such a file is described as a version disagreement
    rather than only as three ``const`` failures with no relation between them.

    Args:
        document: The parsed sidecar.
        source: Which document it is, for the findings.

    Returns:
        One finding per member that declares a different version from the sidecar's.
    """
    declared = document.get("plannotation")
    if not isinstance(declared, str):
        return []
    found: list[Finding] = []
    index = document.get("index")
    if isinstance(index, dict) and index.get("plannotation") != declared:
        found.append(
            finding(
                "PL-CAR-004",
                message=(
                    f"the sidecar declares plannotation {declared!r} and its index declares "
                    f"{index.get('plannotation')!r}"
                ),
                path="/index/plannotation",
                source=source,
            )
        )
    pages = document.get("pages")
    if isinstance(pages, list):
        found += [
            finding(
                "PL-CAR-004",
                message=(
                    f"the sidecar declares plannotation {declared!r} and the plannotation at "
                    f"pages[{position}] declares {member.get('plannotation')!r}"
                ),
                path=json_pointer(["pages", position, "plannotation"]),
                source=source,
            )
            for position, member in enumerate(pages)
            if isinstance(member, dict) and member.get("plannotation") != declared
        ]
    return found


def check_sidecar_consistency(sidecar: Sidecar, *, source: str) -> list[Finding]:
    """Check the two agreement rules of 6.5.1 that need the loaded sidecar.

    The redundancy those rules police is deliberate: the index and the plannotations
    are separate documents in the PDF carrier, so a sidecar carries both rather than
    deriving one from the other, and the price of that is a consistency rule. The
    third rule, on the declared version, is :func:`check_sidecar_versions`.

    Args:
        sidecar: The loaded sidecar.
        source: Which document it is, for the findings.

    Returns:
        Every violation.
    """
    return [
        *_check_sidecar_correspondence(sidecar, source=source),
        *_check_sidecar_order(sidecar, source=source),
    ]


def _check_sidecar_correspondence(sidecar: Sidecar, *, source: str) -> list[Finding]:
    """Check that a sidecar's pages and its index's entries name the same pages.

    Args:
        sidecar: The loaded sidecar.
        source: Which document it is, for the findings.

    Returns:
        One finding naming the pages listed without a plannotation, and one naming the
        plannotations the index does not list.
    """
    indexed = {entry.page_index for entry in sidecar.index.pages}
    carried = {plannotation.page.index for plannotation in sidecar.pages}
    found: list[Finding] = []
    missing = sorted(indexed - carried)
    if missing:
        listed = ", ".join(str(page) for page in missing)
        found.append(
            finding(
                "PL-CAR-005",
                message=(
                    f"the index lists page(s) {listed}, for which the sidecar carries no "
                    f"plannotation; a sidecar contains exactly one plannotation per index "
                    f"entry"
                ),
                path="/index/pages",
                source=source,
            )
        )
    extra = sorted(carried - indexed)
    if extra:
        listed = ", ".join(str(page) for page in extra)
        found.append(
            finding(
                "PL-CAR-005",
                message=(
                    f"the sidecar carries a plannotation for page(s) {listed}, which its "
                    f"index does not list"
                ),
                path="/pages",
                source=source,
            )
        )
    return found


def _check_sidecar_order(sidecar: Sidecar, *, source: str) -> list[Finding]:
    """Check that a sidecar's pages ascend by ``page.index``.

    Args:
        sidecar: The loaded sidecar.
        source: Which document it is, for the findings.

    Returns:
        A single finding when they do not. Repeats are refused by the model before a
        sidecar loads at all, so what is left to report here is the ordering.
    """
    indices = [plannotation.page.index for plannotation in sidecar.pages]
    if indices == sorted(indices):
        return []
    return [
        finding(
            "PL-CAR-006",
            message=(
                f"the sidecar's plannotations are ordered {indices}, not ascending by "
                f"page.index; 6.5.1 fixes the order so that two sidecars for one "
                f"document are the same bytes"
            ),
            path="/pages",
            source=source,
        )
    ]


def check_index_against_plannotations(
    index: PlannotationIndex,
    plannotations: Sequence[tuple[int, Plannotation]],
    *,
    source: str,
) -> list[Finding]:
    """Check an index entry against the plannotation it describes.

    Entries are matched to plannotations by page, which is the identity both carry and
    the one 4.5 makes a fact about the document. An entry naming a page that carries no
    plannotation is not reported: 4.3 forbids a reader from assuming that an index lists
    every plannotated page, and the same restraint applies the other way.

    Args:
        index: The loaded index.
        plannotations: Every plannotation in the carrier, paired with the page it was
            found on.
        source: Which document it is, for the findings.

    Returns:
        Every disagreement, entry by entry.
    """
    by_page = dict(plannotations)
    found: list[Finding] = []
    for position, entry in enumerate(index.pages):
        plannotation = by_page.get(entry.page_index)
        if plannotation is None:
            continue
        base = json_pointer(["pages", position])
        reached = conformance_level(plannotation)
        if entry.level is not reached:
            found.append(
                finding(
                    "PL-CAR-001",
                    message=(
                        f"page {entry.page_index} is indexed as {entry.level.value}, but "
                        f"its plannotation reaches {reached.value}"
                    ),
                    path=f"{base}/level",
                    source=source,
                )
            )
        if entry.sheet_id != plannotation.sheet.sheet_id:
            found.append(
                finding(
                    "PL-CAR-002",
                    message=(
                        f"page {entry.page_index} is indexed as sheet "
                        f"{entry.sheet_id!r}, but its plannotation prints "
                        f"{plannotation.sheet.sheet_id!r}"
                    ),
                    path=f"{base}/sheetId",
                    source=source,
                )
            )
        found += _check_entry_text(entry.title, plannotation.sheet.title, "title", base, source)
        found += _check_entry_text(
            entry.revision, plannotation.sheet.revision, "revision", base, source
        )
    return found


def _check_entry_text(
    indexed: str | None,
    printed: str | None,
    member: str,
    base: str,
    source: str,
) -> list[Finding]:
    """Compare one text member an index entry copies from a sheet.

    Args:
        indexed: The value the index records, or None when it omits it.
        printed: The value the sheet prints, or None when it has none.
        member: ``"title"`` or ``"revision"``, for the message.
        base: The JSON Pointer of the index entry.
        source: Which document it is, for the finding.

    Returns:
        A single finding when the index records a value that differs. A member the
        index omits is not a disagreement: an index may be terser than the sheets it
        lists.
    """
    if indexed is None or indexed == printed:
        return []
    return [
        finding(
            "PL-CAR-003",
            message=(f"the index records {member} {indexed!r}, but the sheet prints {printed!r}"),
            path=f"{base}/{member}",
            source=source,
        )
    ]


def check_page_identity(
    plannotation: Plannotation,
    *,
    page_count: int,
    attached_to: int | None = None,
    source: str,
) -> list[Finding]:
    """Check that a plannotation's ``page.index`` names the page it describes.

    One rule with two faces, and one code, because they are the same claim failing in
    the two carriers. In a PDF the plannotation is attached to a page, and 4.5 says the
    attachment is the fact while ``page.index`` is a claim inside the plannotation; the
    claim must agree with it. In a sidecar there is no attachment, so ``page.index`` is
    all there is -- and 6.5.5 (1) requires a validator to report a plannotation whose
    ``page.index`` is not a page of the document at all.

    Args:
        plannotation: The plannotation.
        page_count: How many pages the document has.
        attached_to: The page the plannotation was found attached to, for a PDF. None
            for a sidecar, where nothing but the plannotation says which page it
            describes.
        source: Which document it is, for the findings.

    Returns:
        A single finding when the claim does not hold.
    """
    claimed = plannotation.page.index
    if attached_to is not None and claimed != attached_to:
        return [
            finding(
                "PL-REF-008",
                message=(
                    f"the plannotation attached to page {attached_to} declares "
                    f"page.index {claimed}; the page a plannotation is attached to is a fact "
                    f"about the document and page.index is a claim inside the plannotation"
                ),
                path="/page/index",
                source=source,
            )
        ]
    if 0 <= claimed < page_count:
        return []
    return [
        finding(
            "PL-REF-008",
            message=(
                f"this plannotation describes page {claimed}, which this "
                f"{page_count}-page document does not have; a reader pairing a payload "
                f"with a document treats such a plannotation as absent"
            ),
            path="/page/index",
            source=source,
        )
    ]


def check_pairing(
    plannotations: Sequence[tuple[int, Plannotation]],
    *,
    page_count: int,
    source: str,
) -> list[Finding]:
    """Check 6.5.5 (3): a payload must pair with the document it was given with.

    Args:
        plannotations: Every plannotation read, paired with the page it claims or was
            found on.
        page_count: How many pages the document has.
        source: Which document it is, for the finding.

    Returns:
        A single finding when no plannotation pairs with any page. The per-plannotation
        halves of 6.5.5 are PL-REF-008 and PL-GEO-001; this is the whole-payload one,
        which no single plannotation can fail.
    """
    if not plannotations:
        return []
    if any(0 <= page_index < page_count for page_index, _ in plannotations):
        return []
    claimed = ", ".join(str(page_index) for page_index, _ in plannotations)
    return [
        finding(
            "PL-CAR-007",
            message=(
                f"no plannotation pairs with a page of this {page_count}-page document; "
                f"the payload describes page(s) {claimed}. A payload that pairs with "
                f"nothing was written for another document, and a reader must treat the "
                f"whole of it as absent"
            ),
            path="",
            source=source,
        )
    ]


def check_declaration(*, present: bool, plannotated: bool, source: str) -> list[Finding]:
    """Check that a plannotated PDF carries the XMP PDF Declaration.

    Args:
        present: Whether a declaration naming the Plannotation specification was found.
        plannotated: Whether the document carries any Plannotation payload at all.
        source: Which document it is, for the finding.

    Returns:
        A single warning when a plannotated document carries no declaration.
    """
    if present or not plannotated:
        return []
    return [
        finding(
            "PL-CAR-008",
            message=(
                f"the document carries Plannotation data but no XMP PDF Declaration naming "
                f"the specification. The declaration is how a reader discovers that "
                f"there is something to read; it is never evidence that what it finds "
                f"is valid, which is why this is a warning and not an error "
                f"(plannotation {SCHEMA_VERSION})"
            ),
            path="",
            source=source,
        )
    ]
