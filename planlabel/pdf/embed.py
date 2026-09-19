# SPDX-License-Identifier: Apache-2.0
"""Write and read PlanLabel data in a PDF.

Uses page-level ``/AF`` entries with ``/AFRelationship /Data``, a document-level
index file, registration in the ``EmbeddedFiles`` name tree, and an XMP PDF
Declaration. Built on pikepdf (MPL-2.0).

Content streams, annotations, existing attachments and unrelated metadata are never
touched: a labelled page must rasterise to pixels identical to the original.

Implemented in Phase 2.
"""

from __future__ import annotations
