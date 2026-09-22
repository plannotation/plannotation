# SPDX-License-Identifier: Apache-2.0
"""Rule 5: the provenance rules of section 4.6.

Provenance is what lets a reader tell a measurement taken from the model from a number
recovered off a picture, and 4.6.6 calls mislabelling reconstructed data as authored
the one violation of the specification that is not merely a defect. The rules here are
the checkable part of that.

None of the arithmetic is written twice. :func:`planlabel.model.aggregate_provenance`
already implements 4.6.3's inheritance and 4.6.4's aggregation, and
:attr:`planlabel.model.PageLabel.provenance_is_consistent` already applies them to a
label; this module reports what they decide and adds the three rules they do not cover
-- a missing confidence, a confidence where there should be none, and a ``mixed`` label
whose items are silent.

One rule runs on the parsed JSON rather than on the model, for the reason given in
:mod:`planlabel.validate.referential`: :class:`planlabel.model.Element` refuses to
construct an inferred item with no confidence, so by the time there is a model to
examine the violation has become an unreadable file rather than a finding. It is
checked where it is still visible.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from planlabel.model import Provenance, aggregate_provenance
from planlabel.validate.codes import finding
from planlabel.validate.schema import json_pointer

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from planlabel.model import LabelIndex, PageLabel
    from planlabel.validate.report import Finding

__all__ = [
    "check_index_provenance",
    "check_inferred_confidence",
    "check_provenance",
    "effective_provenance",
]


def effective_provenance(declared: str | None, top_level: str | None) -> str:
    """Return an item's effective provenance, as 4.6.3 defines it.

    Args:
        declared: The item's own ``provenance``, or None when it omits it.
        top_level: The label's top-level ``provenance``.

    Returns:
        The item's own value where it has one; otherwise the label's, except that where
        the label's is ``mixed`` the effective value is ``inferred`` -- silence is read
        conservatively, never as ``authored``.
    """
    if declared is not None:
        return declared
    if top_level == Provenance.MIXED.value:
        return Provenance.INFERRED.value
    return top_level or Provenance.AUTHORED.value


def check_inferred_confidence(document: Mapping[str, object], *, source: str) -> list[Finding]:
    """Check 4.6.5 and 4.6.4's silence rule on the parsed document.

    Two rules, both run here because both concern items the model may refuse to load:

    * **PL-PRV-001**, an item whose effective provenance is ``inferred`` and which
      carries no ``confidence``. 4.6.5 names the validator as the place this is
      enforced, because the schema cannot express it.
    * **PL-PRV-003**, a label whose top level is ``mixed`` and whose items do not all
      say which half they came from. Under 4.6.3 that silence reads as ``inferred``,
      which is a smaller claim than the writer meant to make.

    Args:
        document: The parsed page label, already schema-valid.
        source: Which document it is, for the findings.

    Returns:
        Every violation, in document order.
    """
    top_level = document.get("provenance")
    top = top_level if isinstance(top_level, str) else None
    found: list[Finding] = []
    for collection in ("elements", "annotations"):
        members = document.get(collection)
        if not isinstance(members, list):
            continue
        for position, member in enumerate(members):
            if not isinstance(member, dict):
                continue
            found += _check_one_item(member, collection, position, top=top, source=source)
    return found


def _check_one_item(
    member: Mapping[str, object],
    collection: str,
    position: int,
    *,
    top: str | None,
    source: str,
) -> list[Finding]:
    """Check one element or annotation's provenance members.

    Args:
        member: The parsed item.
        collection: ``"elements"`` or ``"annotations"``.
        position: Its index in that collection.
        top: The label's top-level provenance, or None when it is absent or not a
            string.
        source: Which document it is, for the findings.

    Returns:
        Every violation this item commits.
    """
    declared = member.get("provenance")
    own = declared if isinstance(declared, str) else None
    effective = effective_provenance(own, top)
    identifier = member.get("id")
    base = json_pointer([collection, position])
    found: list[Finding] = []
    if effective == Provenance.INFERRED.value and member.get("confidence") is None:
        inherited = "" if own is not None else f" (inherited from the label's {top!r})"
        found.append(
            finding(
                "PL-PRV-001",
                message=(
                    f"{collection[:-1]} {identifier!r} has effective provenance "
                    f"'inferred'{inherited} and carries no confidence; an inferred value "
                    f"that does not say how sure it is has withheld what makes it safe "
                    f"to use"
                ),
                path=base,
                source=source,
            )
        )
    if own is None and top == Provenance.MIXED.value:
        found.append(
            finding(
                "PL-PRV-003",
                message=(
                    f"{collection[:-1]} {identifier!r} omits provenance in a label whose "
                    f"top level is 'mixed'; 4.6.3 reads that silence as 'inferred', so a "
                    f"mixed label must record provenance on every item"
                ),
                path=base,
                source=source,
            )
        )
    if effective == Provenance.AUTHORED.value and member.get("confidence") is not None:
        found.append(
            finding(
                "PL-PRV-004",
                message=(
                    f"{collection[:-1]} {identifier!r} is authored and carries a "
                    f"confidence; the member has no meaning there and invites a reader "
                    f"to discount a value that is not in doubt"
                ),
                path=f"{base}/confidence",
                source=source,
            )
        )
    if own == Provenance.MIXED.value:
        found.append(
            finding(
                "PL-PRV-005",
                message=(
                    f"{collection[:-1]} {identifier!r} declares itself 'mixed'; nothing "
                    f"in the format can say which of its members came from which source, "
                    f"so an item whose members come from both should be 'inferred'"
                ),
                path=f"{base}/provenance",
                source=source,
            )
        )
    return found


def check_provenance(label: PageLabel, *, source: str) -> list[Finding]:
    """Check a label's top-level provenance against its items.

    Args:
        label: The loaded page label.
        source: Which document it is, for the findings.

    Returns:
        A single finding when the declaration is not the aggregate, and nothing when it
        is, or when the page has no items and the rule therefore decides nothing.
    """
    if label.provenance_is_consistent:
        return []
    aggregate = label.aggregate_provenance
    spelled = "nothing" if aggregate is None else aggregate.value
    return [
        finding(
            "PL-PRV-002",
            message=(
                f"the label declares provenance {label.provenance.value!r}, but its "
                f"elements and annotations aggregate to {spelled!r}; 4.6.4 makes the top "
                f"level 'authored' only if every value is authored, 'inferred' only if "
                f"every value is inferred, and 'mixed' otherwise"
            ),
            path="/provenance",
            source=source,
        )
    ]


def check_index_provenance(
    index: LabelIndex,
    labels: Sequence[tuple[int, PageLabel]],
    *,
    source: str,
) -> list[Finding]:
    """Check an index's ``provenance`` against the pages it lists.

    Only reported when every page the index lists is in front of the validator. An
    index may legitimately describe a document whose pages the validator was not given
    -- 4.3 forbids a reader from assuming the index lists every labelled page, and the
    converse holds here -- and aggregating over a subset would report a disagreement
    that is an artefact of what was read.

    Args:
        index: The loaded index.
        labels: Every page label in the carrier, paired with the page it was found on.
        source: Which document it is, for the finding.

    Returns:
        A single finding when the index's declaration is not the aggregate over those
        pages, and nothing otherwise.
    """
    if index.provenance is None:
        return []
    by_page = dict(labels)
    listed = [entry.page_index for entry in index.pages]
    if not listed or any(page_index not in by_page for page_index in listed):
        return []
    items: list[Provenance | None] = []
    for page_index in listed:
        label = by_page[page_index]
        declared = [item.provenance for item in (label.elements or [])]
        declared += [item.provenance for item in (label.annotations or [])]
        items += [
            value
            if value is not None
            else Provenance(effective_provenance(None, label.provenance.value))
            for value in declared
        ]
    aggregate = aggregate_provenance(items)
    if aggregate is None or aggregate is index.provenance:
        return []
    return [
        finding(
            "PL-PRV-006",
            message=(
                f"the index declares provenance {index.provenance.value!r}, but the "
                f"{len(listed)} page(s) it lists aggregate to {aggregate.value!r}; the "
                f"index aggregates the same way a label does"
            ),
            path="/provenance",
            source=source,
        )
    ]
