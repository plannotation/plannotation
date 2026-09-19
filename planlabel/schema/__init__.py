# SPDX-License-Identifier: Apache-2.0
"""Packaged JSON Schema files.

The schema is the single source of truth for the label format. It ships inside the
wheel as package data so that a validator works from an installed package with no
network access.

The schema document itself lands in Phase 1.
"""

from __future__ import annotations
