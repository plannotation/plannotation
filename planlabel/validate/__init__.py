# SPDX-License-Identifier: Apache-2.0
"""Validation: schema, referential, geometric and model cross-checks.

Reports the conformance level reached (L1, L2 or L3) alongside counts, warnings and
errors. Optionally shells out to veraPDF when it is on PATH.

Implemented in Phase 3.
"""

from __future__ import annotations
