# SPDX-License-Identifier: Apache-2.0
"""PlanLabel: machine-readable semantics for 2D construction drawings.

PlanLabel attaches a small JSON label to each page of a drawing PDF describing the
sheet, its viewports and their paper-to-model transforms, the elements shown and the
annotations placed on them.

The governing principle, from which every design decision follows: **the PDF page is
the leading document and the label is auxiliary**. Writing a label never changes how
a page looks or prints.
"""

from __future__ import annotations

from planlabel.constants import (
    BASE_URL,
    INDEX_SCHEMA_ID,
    SCHEMA_ID,
    SCHEMA_VERSION,
    SPEC_URI,
)

__version__ = "0.1.0.dev0"

__all__ = [
    "BASE_URL",
    "INDEX_SCHEMA_ID",
    "SCHEMA_ID",
    "SCHEMA_VERSION",
    "SPEC_URI",
    "__version__",
]
