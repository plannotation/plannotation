# SPDX-License-Identifier: Apache-2.0
"""The three reference sample sets, and how to build them.

Design brief section 9 asks for three: an architectural floor plan, a structural position
plan and a section through two storeys. They exist so that every later phase has
something real to point at -- the inspector renders them, the MCP server serves them,
the inference pass is measured against them with their labels stripped, and the
benchmark asks questions whose answers came from the models rather than from anyone
reading the drawings.

They are built from source and never committed, because a generated artefact in a
repository drifts from the code that generates it, and because the design brief's limit
on committed binaries is 200 kB.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from planlabel.export.ifc_svg_pdf import GridAxis, SheetSpec, export_sheet, write_sample
from planlabel.export.models import (
    POSITIONSPLAN_X,
    POSITIONSPLAN_Y,
    BuiltModel,
    build_floorplan,
    build_positionsplan,
    build_section,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime
    from pathlib import Path

    from planlabel.model import Discipline, DrawingType


@dataclass(frozen=True)
class SampleSpec:
    """One sample set: a model, a sheet and what is drawn on it.

    Attributes:
        name: The directory the sample is written to.
        build: The model builder.
        sheet_id: The sheet number as it prints.
        title: The sheet title as it prints.
        scale: The drawing scale's denominator.
        grids: The grid lines to draw and dimension between.
        callout_to: The sheet the callout points at, which makes the three samples
            reference one another rather than standing alone.
        drawing_type: What kind of drawing it is.
        discipline: The discipline it belongs to.
    """

    name: str
    build: Callable[[Path], BuiltModel]
    sheet_id: str
    title: str
    scale: float
    grids: tuple[GridAxis, ...]
    callout_to: str
    drawing_type: DrawingType = "plan"
    discipline: Discipline = "architecture"


def _grid(axes: str, positions: tuple[float, ...], *, vertical: bool) -> tuple[GridAxis, ...]:
    """Name a run of grid lines.

    Args:
        axes: One character per line, such as ``ABCD``.
        positions: Their plane coordinates, in metres.
        vertical: Whether they run up the page.

    Returns:
        The grid lines.
    """
    return tuple(
        GridAxis(axis, vertical=vertical, position=position)
        for axis, position in zip(axes, positions, strict=True)
    )


#: The three sets, arranged so that each sheet's callout names the next.
SAMPLES: tuple[SampleSpec, ...] = (
    SampleSpec(
        name="floorplan",
        build=build_floorplan,
        sheet_id="ARC-101",
        title="Grundriss Erdgeschoss",
        scale=50.0,
        grids=_grid("ABCD", (0.0, 3.0, 5.5, 8.0), vertical=True)
        + _grid("1234", (0.0, 2.0, 4.0, 6.0), vertical=False),
        callout_to="TWP-201",
    ),
    SampleSpec(
        name="positionsplan",
        build=build_positionsplan,
        sheet_id="TWP-201",
        title="Positionsplan Gründung",
        scale=50.0,
        grids=_grid("ABC", POSITIONSPLAN_X, vertical=True)
        + _grid("123", POSITIONSPLAN_Y, vertical=False),
        callout_to="ARC-301",
        drawing_type="positionsplan",
        discipline="structure",
    ),
    SampleSpec(
        name="section",
        build=build_section,
        sheet_id="ARC-301",
        title="Schnitt A-A",
        scale=50.0,
        # The section looks along x, so the grids it crosses are the ones along y,
        # and plane x is model y: grid 1 on the left, grid 4 on the right.
        grids=_grid("1234", (0.0, 2.0, 4.0, 6.0), vertical=True),
        callout_to="ARC-101",
        drawing_type="section",
    ),
)


def build_samples(
    out_root: Path,
    *,
    mod_date: datetime,
    version: str,
    only: str | None = None,
    inkscape_fallback: bool = False,
) -> list[Path]:
    """Build every sample set into a directory.

    Args:
        out_root: The directory to write the sets into.
        mod_date: The timestamp to stamp, so the output is reproducible.
        version: The version to record in each label's ``generator``.
        only: Build just this one set, by name, or None for all of them.
        inkscape_fallback: Convert with the Inkscape command line when CairoSVG cannot
            run, rather than failing.

    Returns:
        The labelled PDFs written, in order.

    Raises:
        KeyError: If ``only`` names no sample.
    """
    names = {spec.name for spec in SAMPLES}
    if only is not None and only not in names:
        msg = f"no sample named {only!r}; the samples are {sorted(names)}"
        raise KeyError(msg)

    written: list[Path] = []
    questions: list[dict[str, object]] = []
    for spec in SAMPLES:
        if only is not None and spec.name != only:
            continue
        directory = out_root / spec.name
        directory.mkdir(parents=True, exist_ok=True)
        built = spec.build(directory / "model.ifc")
        exported = export_sheet(
            built,
            SheetSpec(
                sheet_id=spec.sheet_id,
                title=spec.title,
                scale=spec.scale,
                grids=spec.grids,
                callout_to=spec.callout_to,
                drawing_type=spec.drawing_type,
                discipline=spec.discipline,
            ),
            generator_version=version,
        )
        written.append(
            write_sample(
                exported, built, directory, mod_date=mod_date, inkscape_fallback=inkscape_fallback
            )
        )
        questions.extend(dict(question) for question in exported.ground_truth)

    if only is None:
        (out_root / "groundtruth.jsonl").write_text(
            "".join(
                json.dumps(question, ensure_ascii=False, sort_keys=True) + "\n"
                for question in questions
            ),
            encoding="utf-8",
        )
    return written
