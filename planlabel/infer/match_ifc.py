# SPDX-License-Identifier: Apache-2.0
"""Match inferred elements back to the IFC model they were drawn from.

A printed mark is the one thing on a drawing that names an element, and the model
stores the same mark in ``Tag``. Matching the two turns a guess -- "this is probably a
wall, because it is marked Pos. 3" -- into a fact: the GlobalId and the real IFC class
of the element the mark belongs to.

Marks are compared after normalisation, because a drawing and a model disagree about
whitespace far more often than about anything that matters: ``Pos.3`` on the page and
``Pos. 3`` in the model are the same mark.

A match stays ``inferred``. It was established by reading the drawing, and SPEC 4.6.6
forbids promoting reconstructed data to ``authored`` however confident the match. What
changes is the confidence, and the class, which the model now supplies.
"""

from __future__ import annotations

import importlib
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    from planlabel.model import Element, PageLabel

#: The confidence a mark matched to exactly one model element carries. High, and not
#: one: the mark could still be a different element's that happens to share it.
MATCHED_CONFIDENCE = 0.95


def normalise_mark(mark: str) -> str:
    """Put a mark into the form two sources agree on.

    A drawing prints ``Pos 3``, ``Pos.3`` or ``Pos. 3`` for what the model stores once,
    so whitespace and full stops are both dropped: neither distinguishes one mark from
    another. Hyphens are kept, because ``UZ-1`` and ``UZ1`` could in principle be two
    different series in one office's numbering.

    Args:
        mark: A mark as printed or as stored.

    Returns:
        The mark without whitespace or full stops, folded to one case.

    Examples:
        >>> normalise_mark("Pos.3") == normalise_mark("Pos 3") == normalise_mark("Pos. 3")
        True
        >>> normalise_mark(" St. 12 ")
        'st12'
    """
    return re.sub(r"[\s.]+", "", mark).casefold()


def model_marks(model_path: Path) -> dict[str, list[tuple[str, str]]]:
    """Read every mark the model holds.

    Args:
        model_path: The IFC file.

    Returns:
        ``(GlobalId, IFC class)`` for each element, by normalised mark. A mark held by
        more than one element maps to all of them, and is then not used to match.
    """
    ifcopenshell: Any = importlib.import_module("ifcopenshell")
    model = ifcopenshell.open(str(model_path))
    marks: dict[str, list[tuple[str, str]]] = {}
    for entity in model.by_type("IfcProduct"):
        tag = getattr(entity, "Tag", None)
        if tag:
            marks.setdefault(normalise_mark(str(tag)), []).append(
                (str(entity.GlobalId), str(entity.is_a()))
            )
    return marks


def match_to_model(label: PageLabel, model_path: Path) -> tuple[PageLabel, int]:
    """Fill in GlobalIds and classes for inferred elements whose mark the model holds.

    An ambiguous mark -- one the model gives to two elements -- is left unmatched
    rather than resolved by guessing, because a wrong GlobalId is a false statement
    about the building that no later reader could detect.

    Args:
        label: An inferred page label.
        model_path: The IFC file it was drawn from.

    Returns:
        The label with matched elements filled in, and how many were matched.
    """
    marks = model_marks(model_path)
    matched = 0
    elements: list[Element] = []
    for element in label.elements or []:
        candidates = marks.get(normalise_mark(element.tag or ""), [])
        if len(candidates) == 1:
            guid, ifc_class = candidates[0]
            elements.append(
                element.model_copy(
                    update={
                        "ifc_guid": guid,
                        "ifc_class": ifc_class,
                        "confidence": MATCHED_CONFIDENCE,
                    }
                )
            )
            matched += 1
        else:
            elements.append(element)
    if not matched:
        return label, 0
    return label.model_copy(update={"elements": elements}), matched
