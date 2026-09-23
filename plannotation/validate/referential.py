# SPDX-License-Identifier: Apache-2.0
"""Rule 2: identity and references, as section 4.5 of the specification defines them.

Every reference in a plannotation points inside its own page, with one exception.
Viewports, elements and annotations share a single ``localId`` namespace -- they must,
because ``measures`` may name an element or a grid annotation and the reference does not
say which -- and ``target`` is the only member through which a plannotation may name
another page.

Two of these rules run on the parsed JSON rather than on the loaded model, and the
reason is worth stating because it looks like an inconsistency. :class:`Plannotation`
already refuses to load a plannotation with a repeated ``localId``; so does the schema
refuse several other things. A rule whose violation stops the model loading cannot be
checked after the model has loaded, and reporting it as "the model would not load" would
tell a person nothing they could act on. So the two rules the model enforces beyond the
schema -- a repeated ``localId`` here, and an inferred item with no confidence in
:mod:`plannotation.validate.provenance` -- are checked on the parsed document, where
they are still visible, and every other rule is checked on the model.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from plannotation.model import annotation_has_link
from plannotation.validate.codes import finding
from plannotation.validate.schema import json_pointer

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from plannotation.model import Annotation, Element, Plannotation, Viewport
    from plannotation.validate.report import Finding

__all__ = [
    "LINKABLE_ANNOTATIONS",
    "MEASURABLE_ANNOTATIONS",
    "check_duplicate_ids",
    "check_references",
    "check_sheet_ids",
]

#: Annotation types a ``measures`` entry may name, besides an element.
#:
#: The table in 4.5 admits levels as well as grids, because a dimension in a section
#: runs between levels and refusing to record that link would lose a meaning that is
#: visibly on the page.
MEASURABLE_ANNOTATIONS: Final = frozenset({"grid", "level"})

#: Annotation types that usually have something to link to.
#:
#: From 4.1, which lists them and asks a validator to report an unlinked one as a
#: warning. The types left out -- ``text``, ``revisionCloud``, ``keynote``, ``symbol``,
#: ``leader``, ``hatch``, ``northArrow``, ``scaleBar`` -- have nothing to link to, and a
#: north arrow that means what it looks like should not be reported for saying so.
LINKABLE_ANNOTATIONS: Final = frozenset({"dimension", "tag", "callout", "sectionMark", "grid"})


def check_duplicate_ids(document: Mapping[str, object], *, source: str) -> list[Finding]:
    """Check that no ``localId`` is used twice on one page.

    Run on the parsed document rather than on the model, because the model refuses to
    load a plannotation that breaks this rule and the rule would then be unreportable.

    Args:
        document: The parsed plannotation, already schema-valid.
        source: Which document it is, for the findings.

    Returns:
        One finding per repeated identifier, in the order the identifiers first appear.
    """
    seen: dict[str, str] = {}
    found: list[Finding] = []
    reported: set[str] = set()
    for collection in ("viewports", "elements", "annotations"):
        members = document.get(collection)
        if not isinstance(members, list):
            continue
        for position, member in enumerate(members):
            if not isinstance(member, dict):
                continue
            identifier = member.get("id")
            if not isinstance(identifier, str):
                continue
            pointer = json_pointer([collection, position, "id"])
            if identifier not in seen:
                seen[identifier] = pointer
                continue
            if identifier in reported:
                continue
            reported.add(identifier)
            found.append(
                finding(
                    "PL-REF-001",
                    message=(
                        f"localId {identifier!r} is declared here and at "
                        f"{seen[identifier]}; viewports, elements and annotations share "
                        f"one namespace, so a repeated id makes every reference to it "
                        f"ambiguous"
                    ),
                    path=pointer,
                    source=source,
                )
            )
    return found


def check_references(
    plannotation: Plannotation,
    *,
    source: str,
    page_count: int | None = None,
) -> list[Finding]:
    """Check every reference a plannotation carries.

    Args:
        plannotation: The loaded plannotation.
        source: Which document it is, for the findings.
        page_count: How many pages the document has, when one is in front of the
            validator. None when there is not, in which case ``target.pdfPage`` cannot
            be bounded and is not reported.

    Returns:
        Every violation, in document order.
    """
    viewports = {viewport.local_id: viewport for viewport in plannotation.viewports or []}
    elements = {element.local_id: element for element in plannotation.elements or []}
    annotations = {item.local_id: item for item in plannotation.annotations or []}
    found = _check_viewport_refs(plannotation, viewports, source=source)
    for position, annotation in enumerate(plannotation.annotations or []):
        base = json_pointer(["annotations", position])
        found += _check_shows(annotation, elements, base=base, source=source)
        found += _check_measures(annotation, elements, annotations, base=base, source=source)
        found += _check_target(
            annotation,
            viewports,
            sheet_id=plannotation.sheet.sheet_id,
            page_count=page_count,
            base=base,
            source=source,
        )
        if annotation.annotation_type in LINKABLE_ANNOTATIONS and not annotation_has_link(
            annotation
        ):
            found.append(
                finding(
                    "PL-REF-011",
                    message=(
                        f"annotation {annotation.local_id!r} is a {annotation.annotation_type} "
                        f"and carries no measures, shows, target, axis or ifcGuid; an "
                        f"annotation of a type that usually links to something should"
                    ),
                    path=base,
                    source=source,
                )
            )
    return found


def _check_viewport_refs(
    plannotation: Plannotation,
    viewports: Mapping[str, Viewport],
    *,
    source: str,
) -> list[Finding]:
    """Check that every ``viewport`` member names a viewport on the page.

    Args:
        plannotation: The loaded plannotation.
        viewports: Its viewports, by local id.
        source: Which document it is, for the findings.

    Returns:
        One finding per element or annotation naming a viewport that is not declared.
    """
    found: list[Finding] = []
    items: list[tuple[str, int, Element | Annotation]] = [
        ("elements", position, item) for position, item in enumerate(plannotation.elements or [])
    ]
    items += [
        ("annotations", position, item)
        for position, item in enumerate(plannotation.annotations or [])
    ]
    for collection, position, item in items:
        if item.viewport is None or item.viewport in viewports:
            continue
        declared = ", ".join(sorted(viewports)) or "none"
        found.append(
            finding(
                "PL-REF-002",
                message=(
                    f"{collection[:-1]} {item.local_id!r} names viewport "
                    f"{item.viewport!r}, which this plannotation does not declare; it "
                    f"declares {declared}"
                ),
                path=json_pointer([collection, position, "viewport"]),
                source=source,
            )
        )
    return found


def _check_shows(
    annotation: Annotation,
    elements: Mapping[str, Element],
    *,
    base: str,
    source: str,
) -> list[Finding]:
    """Check one annotation's ``shows`` member.

    Args:
        annotation: The annotation.
        elements: Every element on the page, by local id.
        base: The JSON Pointer of the annotation itself.
        source: Which document it is, for the findings.

    Returns:
        A finding when ``shows.element`` does not resolve, or when a property is named
        with no element to read it from.
    """
    shows = annotation.shows
    if shows is None:
        return []
    if shows.element is None:
        if shows.property_name is None:
            return []
        return [
            finding(
                "PL-REF-009",
                message=(
                    f"annotation {annotation.local_id!r} shows property "
                    f"{shows.property_name!r} but names no element to read it from"
                ),
                path=f"{base}/shows",
                source=source,
            )
        ]
    element = elements.get(shows.element)
    if element is None:
        return [
            finding(
                "PL-REF-003",
                message=(
                    f"annotation {annotation.local_id!r} shows element {shows.element!r}, "
                    f"which is not an element on this page"
                ),
                path=f"{base}/shows/element",
                source=source,
            )
        ]
    return _check_shown_property(annotation, element, base=base, source=source)


def _check_shown_property(
    annotation: Annotation,
    element: Element,
    *,
    base: str,
    source: str,
) -> list[Finding]:
    """Check that a dotted ``shows.property`` names something the element carries.

    Only the ``PsetName.PropertyName`` spelling is checked, because it is the only one
    the plannotation says where to look up: an undotted name is an IFC attribute, or
    a convention between two programs, and nothing in the document resolves it. That
    restriction is a heuristic, which with the absence of any MUST is why the finding
    is a warning.

    Args:
        annotation: The annotation doing the showing.
        element: The element it names, already known to exist.
        base: The JSON Pointer of the annotation itself.
        source: Which document it is, for the finding.

    Returns:
        A single warning when the set or the property is not there.
    """
    name = annotation.shows.property_name if annotation.shows else None
    if name is None or "." not in name:
        return []
    set_name, _, property_name = name.partition(".")
    quantities = element.pset(set_name)
    path = f"{base}/shows/property"
    if quantities is None:
        carried = ", ".join(sorted(element.properties or {})) or "no property sets"
        return [
            finding(
                "PL-REF-012",
                message=(
                    f"annotation {annotation.local_id!r} shows {name!r}, but element "
                    f"{element.local_id!r} carries {carried}"
                ),
                path=path,
                source=source,
            )
        ]
    if property_name in quantities:
        return []
    held = ", ".join(sorted(quantities))
    return [
        finding(
            "PL-REF-012",
            message=(
                f"annotation {annotation.local_id!r} shows {name!r}, but property set "
                f"{set_name} of element {element.local_id!r} holds only {held}"
            ),
            path=path,
            source=source,
        )
    ]


def _check_measures(
    annotation: Annotation,
    elements: Mapping[str, Element],
    annotations: Mapping[str, Annotation],
    *,
    base: str,
    source: str,
) -> list[Finding]:
    """Check one annotation's ``measures`` entries.

    Args:
        annotation: The annotation.
        elements: Every element on the page, by local id.
        annotations: Every annotation on the page, by local id.
        base: The JSON Pointer of the annotation itself.
        source: Which document it is, for the findings.

    Returns:
        One finding per entry that resolves to nothing, or to an annotation of a type
        4.5 does not admit.
    """
    found: list[Finding] = []
    for position, referenced in enumerate(annotation.measures or []):
        path = f"{base}/measures/{position}"
        if referenced in elements:
            continue
        target = annotations.get(referenced)
        if target is None:
            found.append(
                finding(
                    "PL-REF-004",
                    message=(
                        f"annotation {annotation.local_id!r} measures {referenced!r}, which "
                        f"no element and no annotation on this page declares"
                    ),
                    path=path,
                    source=source,
                )
            )
        elif target.annotation_type not in MEASURABLE_ANNOTATIONS:
            found.append(
                finding(
                    "PL-REF-005",
                    message=(
                        f"annotation {annotation.local_id!r} measures {referenced!r}, an "
                        f"annotation of type {target.annotation_type!r}; 4.5 admits an "
                        f"element, a grid or a level"
                    ),
                    path=path,
                    source=source,
                )
            )
    return found


def _check_target(
    annotation: Annotation,
    viewports: Mapping[str, Viewport],
    *,
    sheet_id: str,
    page_count: int | None,
    base: str,
    source: str,
) -> list[Finding]:
    """Check one annotation's ``target`` member.

    ``viewportId`` is a ``localId`` read in the *targeted* page, so a target naming
    another sheet is left alone: this plannotation does not hold that page's
    identifiers. A target that names this very sheet is a reference within the page
    like any other.

    Args:
        annotation: The annotation.
        viewports: Every viewport on the page, by local id.
        sheet_id: The sheet number this plannotation prints.
        page_count: The document's page count, or None when there is no document.
        base: The JSON Pointer of the annotation itself.
        source: Which document it is, for the findings.

    Returns:
        A finding per member of ``target`` that does not hold.
    """
    target = annotation.target
    if target is None:
        return []
    found: list[Finding] = []
    if page_count is not None and target.pdf_page is not None and target.pdf_page >= page_count:
        found.append(
            finding(
                "PL-REF-006",
                message=(
                    f"annotation {annotation.local_id!r} targets pdfPage "
                    f"{target.pdf_page}, but the document has {page_count} page(s); "
                    f"pdfPage is a zero-based index within the same document"
                ),
                path=f"{base}/target/pdfPage",
                source=source,
            )
        )
    aims_here = target.sheet_id in (None, sheet_id)
    if aims_here and target.viewport_id is not None and target.viewport_id not in viewports:
        found.append(
            finding(
                "PL-REF-007",
                message=(
                    f"annotation {annotation.local_id!r} targets viewportId "
                    f"{target.viewport_id!r} on this sheet ({sheet_id}), which declares "
                    f"no such viewport"
                ),
                path=f"{base}/target/viewportId",
                source=source,
            )
        )
    return found


def check_sheet_ids(plannotations: Sequence[tuple[int, Plannotation]]) -> list[Finding]:
    """Check that no two plannotated pages of one document print the same sheet number.

    Args:
        plannotations: Every plannotation in the carrier, paired with the page it was
            found on, ascending.

    Returns:
        One finding per sheet number used more than once, reported against the second
        and later pages that use it.
    """
    first: dict[str, int] = {}
    found: list[Finding] = []
    for page_index, plannotation in plannotations:
        sheet_id = plannotation.sheet.sheet_id
        if sheet_id not in first:
            first[sheet_id] = page_index
            continue
        found.append(
            finding(
                "PL-REF-010",
                message=(
                    f"sheet id {sheet_id!r} is also printed by page {first[sheet_id]}; "
                    f"4.5 says it should be unique within a document, and a reader "
                    f"resolving a target by sheetId cannot tell the two apart"
                ),
                path="/sheet/id",
                source=f"page {page_index}",
            )
        )
    return found
