# SPDX-License-Identifier: Apache-2.0
"""Measure what a page label is worth to a model reading a drawing.

The benchmark asks a model questions about the sample sheets under two conditions
that differ in one thing only:

``plain``
    The page rendered at 150 dpi, and the text extracted from it.
``labelled``
    The same, followed by the page's Plannotation label, supplied as the result of a
    ``get_plannotation`` tool call -- the way an MCP host would hand it over.

Every answer comes from the model the drawing was exported from, never from reading
the drawing, so the answer key cannot share a mistake with the thing it grades.

Nothing in this package talks to the network except :func:`plannotation.bench.runner.
anthropic_transport`, which is only built when a question is not already in the
response cache. The test suite never builds it.
"""

from __future__ import annotations

from plannotation.bench.questions import Question, load_questions, merge_ground_truth
from plannotation.bench.runner import Record, Reply, Usage, run
from plannotation.bench.score import score

__all__ = [
    "Question",
    "Record",
    "Reply",
    "Usage",
    "load_questions",
    "merge_ground_truth",
    "run",
    "score",
]
