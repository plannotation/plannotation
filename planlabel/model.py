# SPDX-License-Identifier: Apache-2.0
"""Pydantic v2 models mirroring the PlanLabel JSON Schema.

The JSON Schema in :mod:`planlabel.schema` is the single source of truth; these
models are generated to match it exactly and never the other way round. Round-tripping
a label through them reproduces the canonical JSON byte for byte.

Implemented in Phase 1.
"""

from __future__ import annotations
