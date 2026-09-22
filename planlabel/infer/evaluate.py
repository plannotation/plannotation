# SPDX-License-Identifier: Apache-2.0
"""Measure how much of an authored label inference recovers.

Design brief section 12's gate is stated as recall -- at least 90% of tags, 80% of
dimensions and every grid -- against the authored ``labels.json``, with precision
reported alongside. Recall alone is easy to game: report everything as a tag and every
tag is found. Precision is what says the recovered items are real.

Items are matched by what a reader would recognise them by, not by id: a tag by its
mark, a dimension by its value, a grid by its axis. Inferred ids are the inferencer's
own numbering and mean nothing against the authored ones.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

from planlabel.infer.match_ifc import normalise_mark

if TYPE_CHECKING:
    from planlabel.model import PageLabel

#: The gate of design brief section 12, as recall per category.
GATE: dict[str, float] = {"tag": 0.90, "dimension": 0.80, "grid": 1.00}


@dataclass(frozen=True)
class Score:
    """How well one category was recovered.

    Attributes:
        category: ``tag``, ``dimension``, ``grid``, ``callout`` or ``sheet``.
        expected: How many the authored label holds.
        found: How many inference reported.
        correct: How many of those match an authored item.
    """

    category: str
    expected: int
    found: int
    correct: int

    @property
    def recall(self) -> float:
        """Return the fraction of authored items recovered."""
        return self.correct / self.expected if self.expected else 1.0

    @property
    def precision(self) -> float:
        """Return the fraction of reported items that are real."""
        return self.correct / self.found if self.found else 1.0

    @property
    def passes(self) -> bool:
        """Report whether this category meets the design brief's gate, where it has one."""
        return self.recall >= GATE.get(self.category, 0.0)


def _keys(label: PageLabel, kind: str) -> Counter[str]:
    """Return the recognisable keys of one kind of annotation.

    Args:
        label: The label.
        kind: The annotation type.

    Returns:
        A multiset of keys, so that two tags with one mark count twice.
    """
    keys: Counter[str] = Counter()
    by_id = {annotation.local_id: annotation for annotation in label.annotations or []}
    for annotation in label.annotations or []:
        if annotation.annotation_type != kind:
            continue
        if kind == "tag":
            keys[normalise_mark(annotation.text or "")] += 1
        elif kind == "dimension" and annotation.value is not None:
            # A dimension is right when its value is and it measures the same things:
            # the right number linked to the wrong grids states a false distance.
            ends = sorted(
                str(by_id[end].axis or by_id[end].text) if end in by_id else "?"
                for end in annotation.measures or []
            )
            keys[f"{annotation.value:.0f}:{'-'.join(ends)}"] += 1
        elif kind == "level":
            keys[str(annotation.text)] += 1
        elif kind == "grid":
            keys[str(annotation.axis)] += 1
        elif kind == "callout" and annotation.target is not None:
            keys[str(annotation.target.sheet_id)] += 1
    return keys


def score(authored: PageLabel, inferred: PageLabel) -> list[Score]:
    """Score one inferred label against the authored one for the same page.

    Args:
        authored: The label the exporter wrote from the model.
        inferred: The label inference reconstructed from the drawing.

    Returns:
        One score per category.
    """
    scores: list[Score] = []
    for kind in ("tag", "dimension", "grid", "level", "callout"):
        truth, guess = _keys(authored, kind), _keys(inferred, kind)
        scores.append(
            Score(
                category=kind,
                expected=sum(truth.values()),
                found=sum(guess.values()),
                correct=sum((truth & guess).values()),
            )
        )
    right = [
        authored.sheet.sheet_id == inferred.sheet.sheet_id,
        authored.sheet.scale == inferred.sheet.scale,
        authored.sheet.revision == inferred.sheet.revision,
        authored.sheet.drawing_type == inferred.sheet.drawing_type,
    ]
    scores.append(Score("sheet", expected=len(right), found=len(right), correct=sum(right)))
    return scores
