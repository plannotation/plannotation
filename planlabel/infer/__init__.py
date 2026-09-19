# SPDX-License-Identifier: Apache-2.0
"""Reconstruct labels for legacy PDFs that were never authored with PlanLabel.

Everything produced here carries ``provenance: inferred`` and a ``confidence``. The
input PDF is never modified.

Implemented in Phase 7.
"""

from __future__ import annotations
