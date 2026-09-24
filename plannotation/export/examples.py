# SPDX-License-Identifier: Apache-2.0
"""The example drawings the site shows: real buildings, drawn from their own models.

The samples are built from models this project writes itself, which is what makes their
ground truth exact and also what makes them look like what they are. The examples are
drawn from a model somebody else built for a real building, through the same exporter
with no special cases, so that a visitor can open a drawing that looks like a drawing
and see what the plannotation says about it.

The model is pinned: its URL, its size and its SHA-256 are written here, it is fetched
once into a cache that is never committed, and a file whose hash differs is refused.
Everything drawn from it is then reproducible byte for byte, with a fixed timestamp.

Maleva 18
---------
The seniors' apartment building at Maleva tn 18, Tallinn: five storeys and 80 municipal
flats, built in 2021 for Tallinna Linnavaraamet. The model is Esplan OÜ's
preliminary-design architecture model, exported from Archicad as IFC2X3, published by
buildingSMART among its community sample files under CC BY 4.0. Two sheets are drawn
from it: the ground-floor plan, square to the building's grid, and a section across the
building between grids 4 and 5.

What ``make examples`` writes (the site reads it)::

    examples/M18-101.pdf    the plannotated PDF
    examples/M18-101.png    page 1, 1200 px wide, with the plannotation's outlines drawn
                            in the inspector's light-theme colours
    examples/index.json     one entry per sheet: its files, title, paper, scale,
                            counts and the source model's attribution
"""

from __future__ import annotations

import hashlib
import importlib
import json
import logging
import os
import shutil
import tempfile
import urllib.request
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from plannotation.errors import ExportError
from plannotation.export.drafting import RoomLabels, SectionMark, ViewTitle
from plannotation.export.ifc_svg_pdf import SheetSpec, TitleField, export_sheet
from plannotation.export.models import BuiltModel
from plannotation.export.sheet import PAPER_SIZES
from plannotation.export.to_pdf import svg_to_pdf
from plannotation.export.views import (
    building_datum,
    building_products,
    find_storey,
    grid_axes_on,
    grid_lines,
    open_model,
    plan_along,
    section_between,
    storey_levels,
    storey_products,
)
from plannotation.model import Project
from plannotation.pdf import embed

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from plannotation.model import Plannotation

_LOGGER = logging.getLogger(__name__)

#: The environment variable naming a directory that already holds the pinned models.
CACHE_ENV = "PLANNOTATION_EXAMPLES_CACHE"

#: Where the models are cached when nothing else is said: git-ignored, like all of .cache.
DEFAULT_CACHE = Path(".cache") / "examples"

#: The timestamp every example is stamped with, so that a rebuild is byte-identical:
#: the day the examples were first drawn, not the day they are rebuilt.
EXAMPLES_DATE = datetime(2026, 9, 24, tzinfo=UTC)

#: How wide the thumbnails are, in pixels.
THUMBNAIL_WIDTH_PX = 1200

#: The inspector's light-theme colours for elements, annotations and viewports
#: (``--element``, ``--annotation`` and ``--viewport`` in inspector/index.html).
INSPECTOR_COLOURS = {"element": "#1d6fa5", "annotation": "#9a5b00", "viewport": "#3f7d3f"}

#: The licence every example's source model is published under.
CC_BY_4 = ("CC BY 4.0", "https://creativecommons.org/licenses/by/4.0/")


@dataclass(frozen=True)
class Source:
    """A pinned model somebody else published, and how to credit it.

    Attributes:
        filename: The file's name in the cache.
        url: Where to download it.
        page: The page that publishes it, which the attribution links.
        size: Its size in bytes.
        sha256: Its SHA-256.
        title: What it is a model of.
        author: Who holds its copyright.
        attribution: The credit every drawing made from it prints.
    """

    filename: str
    url: str
    page: str
    size: int
    sha256: str
    title: str
    author: str
    attribution: str


_COMMUNITY = (
    "https://github.com/buildingsmart-community/Community-Sample-Test-Files/tree/main/"
    "IFC%202.3.0.1%20(IFC%202x3)/"
)
_MEDIA = (
    "https://media.githubusercontent.com/media/buildingsmart-community/"
    "Community-Sample-Test-Files/main/IFC%202.3.0.1%20(IFC%202x3)/"
)

#: Esplan's preliminary-design architecture model of Maleva 18.
MALEVA_18 = Source(
    filename="1807_EP_AR_v18.ifc",
    url=_MEDIA + "Esplanades/1807_EP_AR_v18.ifc",
    page=_COMMUNITY + "Esplanades",
    size=12_109_172,
    sha256="b97db10a2cb679a6543ccf4e34cfa8e77753f14e67f51b403e6cd9a819e3748f",
    title="Maleva 18 seniors' apartment building, Tallinn (preliminary design model)",
    author="Esplan OÜ",
    attribution=(
        "Maleva 18 seniors' apartment building, Tallinn. IFC model © Esplan OÜ "
        "(esplan.ee), CC BY 4.0, via buildingSMART Community Sample Test Files. "
        "Drawings rendered and annotated from the model by Plannotation (changes made). "
        "Not an Esplan drawing."
    ),
)


@dataclass(frozen=True)
class Example:
    """One example sheet: the model it is drawn from and how.

    Attributes:
        name: The sheet number, which names its files.
        source: The model.
        draw: Given the cached model, what to draw of it and the sheet it goes on.
    """

    name: str
    source: Source
    draw: Callable[[Path], tuple[BuiltModel, SheetSpec]]


# ---------------------------------------------------------------------------
# Maleva 18
# ---------------------------------------------------------------------------
#: The ground floor, and how far above it the plan is cut.
M18_STOREY, M18_CUT_M = "1.korrus", 1.2

#: The grid axes whose crossing is the plan's origin, and the axis its x runs along.
M18_ORIGIN, M18_ALONG = ("1", "A"), "A"

#: Section A-A: halfway between these two grid axes, looking towards the third.
M18_SECTION = ("4", "5", "1")

#: What the project is called on the sheets: the model's own name, and in English.
M18_PROJECT = "Maleva tänav 18 korterelamu / Maleva 18 seniors' apartment building"

#: The title block's fields common to both sheets, in Estonian and English.
M18_FIELDS = (
    TitleField("OBJEKT / PROJECT", M18_PROJECT),
    TitleField("AADRESS / ADDRESS", "Maleva tn 18, Põhja-Tallinn, Tallinn, Estonia"),
    TitleField("STAADIUM / STAGE", "Eelprojekt / Preliminary design (model EP AR v18)"),
)

#: The title block's size on both sheets.
M18_TITLE_BLOCK_MM = (180.0, 64.0)


def _m18_fields(drawing: str, sheet: str, scale: str) -> tuple[TitleField, ...]:
    """Return a Maleva 18 sheet's title block fields.

    Args:
        drawing: The drawing's title.
        sheet: The sheet number.
        scale: The scale as printed.

    Returns:
        The fields, the sheet number and scale last.
    """
    return (
        *M18_FIELDS,
        TitleField("JOONIS / DRAWING", drawing),
        TitleField("ALLIKAS / SOURCE", MALEVA_18.attribution),
        TitleField("LEHT / SHEET", sheet),
        TitleField("MÕÕTKAVA / SCALE", scale),
    )


#: What both Maleva 18 sheets share; each sheet replaces what is its own.
M18_SHEET = SheetSpec(
    sheet_id="",
    title="",
    scale=100.0,
    grids=(),
    callout_to=None,
    project=Project(name=M18_PROJECT),
    title_block_mm=M18_TITLE_BLOCK_MM,
    presentation=True,
    grid_overshoot_mm=36.0,
    scale_bar=True,
)


def draw_m18_plan(model_path: Path) -> tuple[BuiltModel, SheetSpec]:
    """Say what M18-101 draws: the ground floor, square to the grid, at 1:100 on A1.

    Args:
        model_path: The cached model.

    Returns:
        The model as drawn, and the sheet.
    """
    model = open_model(model_path)
    storey = find_storey(model, M18_STOREY)
    lines = grid_lines(model)
    cut = plan_along(lines, origin=M18_ORIGIN, along=M18_ALONG, z=storey.z + M18_CUT_M)
    grids = grid_axes_on(lines, cut)
    first, second, towards = M18_SECTION
    at = {grid.axis: grid.position for grid in grids}
    built = BuiltModel(
        path=model_path,
        seed="maleva-18",
        storey_elevation=storey.z,
        cut_height=M18_CUT_M,
        section=cut,
        storey_name=storey.name,
        storey_guid=storey.guid,
        datum=building_datum(model),
        include=storey_products(model, storey),
    )
    title = "1. korrus / Ground floor plan"
    spec = replace(
        M18_SHEET,
        sheet_id="M18-101",
        title=title,
        grids=grids,
        page_size="A1",
        viewport_box=(20.0, 30.0, 821.0, 574.0),
        title_fields=_m18_fields(title, "M18-101", "1:100"),
        dimension_offsets_mm=(20.0, 30.0),
        rooms=RoomLabels(area_property="AR_Ruum.120_Pindala"),
        door_swings=True,
        section_marks=(
            SectionMark(
                label="A",
                target_sheet="M18-301",
                position=(at[first] + at[second]) / 2.0,
                looking=-1 if at[towards] < at[first] else 1,
            ),
        ),
        view_title=ViewTitle(text=title),
        north_arrow=True,
    )
    return built, spec


def draw_m18_section(model_path: Path) -> tuple[BuiltModel, SheetSpec]:
    """Say what M18-301 draws: section A-A across the building, at 1:100 on A2.

    Args:
        model_path: The cached model.

    Returns:
        The model as drawn, and the sheet.
    """
    model = open_model(model_path)
    datum = building_datum(model)
    lines = grid_lines(model)
    first, second, towards = M18_SECTION
    cut = section_between(lines, first=first, second=second, looking_to=towards, datum=datum)
    title = "Lõige A-A / Section A-A"
    built = BuiltModel(
        path=model_path,
        seed="maleva-18",
        storey_elevation=datum,
        cut_height=None,
        section=cut,
        levels=storey_levels(model),
        datum=datum,
        include=building_products(model),
    )
    spec = replace(
        M18_SHEET,
        sheet_id="M18-301",
        title=title,
        grids=grid_axes_on(lines, cut),
        drawing_type="section",
        page_size="A2",
        viewport_box=(20.0, 80.0, 574.0, 400.0),
        title_fields=_m18_fields(title, "M18-301", "1:100"),
        view_title=ViewTitle(text=title, label="A", target_sheet="M18-101"),
    )
    return built, spec


#: The examples, in the order the site shows them.
EXAMPLES: tuple[Example, ...] = (
    Example(name="M18-101", source=MALEVA_18, draw=draw_m18_plan),
    Example(name="M18-301", source=MALEVA_18, draw=draw_m18_section),
)


# ---------------------------------------------------------------------------
# The cache
# ---------------------------------------------------------------------------
def cache_dir(explicit: Path | None = None) -> Path:
    """Return the directory the pinned models are cached in.

    Args:
        explicit: A directory named on the command line, which wins.

    Returns:
        That, else :data:`CACHE_ENV` where it is set, else :data:`DEFAULT_CACHE`.
    """
    if explicit is not None:
        return explicit
    named = os.environ.get(CACHE_ENV)
    return Path(named) if named else DEFAULT_CACHE


def sha256_of(path: Path) -> str:
    """Return a file's SHA-256, read in chunks.

    Args:
        path: The file.

    Returns:
        The hex digest.
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch(source: Source, directory: Path) -> Path:
    """Return the cached model, downloading it first if it is not there.

    Args:
        source: The model.
        directory: The cache.

    Returns:
        The model's path, its size and hash verified.

    Raises:
        ExportError: If the file, cached or downloaded, is not the pinned one.
    """
    path = directory / source.filename
    if not path.is_file():
        directory.mkdir(parents=True, exist_ok=True)
        _LOGGER.info("downloading %s", source.url)
        with (
            tempfile.NamedTemporaryFile(dir=directory, delete=False, suffix=".part") as part,
            urllib.request.urlopen(source.url, timeout=120) as response,  # noqa: S310 - pinned https
        ):
            shutil.copyfileobj(response, part)
        Path(part.name).replace(path)
    size, digest = path.stat().st_size, sha256_of(path)
    if size != source.size or digest != source.sha256:
        msg = (
            f"{path} is {size} bytes with SHA-256 {digest}, but the pinned model is "
            f"{source.size} bytes with SHA-256 {source.sha256}; delete it to download "
            f"it again"
        )
        raise ExportError(msg)
    return path


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------
def build_examples(
    out_dir: Path,
    *,
    cache: Path,
    mod_date: datetime,
    version: str,
    only: Sequence[str] | None = None,
) -> list[Path]:
    """Build every example sheet, its thumbnail and the index.

    Args:
        out_dir: Where to write them.
        cache: Where the pinned models are cached.
        mod_date: The timestamp to stamp, so that a rebuild is byte-identical.
        version: The version to record in each plannotation's ``generator``.
        only: Build just these sheets, by name; the index then lists only them.

    Returns:
        The plannotated PDFs written.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    entries: list[dict[str, object]] = []
    for example in EXAMPLES:
        if only and example.name not in only:
            continue
        model = fetch(example.source, cache)
        built, spec = example.draw(model)
        exported = export_sheet(built, spec, generator_version=version)
        plannotation = exported.plannotation
        with tempfile.TemporaryDirectory(prefix="plannotation-example-") as scratch:
            plain = svg_to_pdf(
                exported.svg,
                Path(scratch) / "sheet.pdf",
                width_mm=plannotation.page.width_mm,
                height_mm=plannotation.page.height_mm,
                mod_date=mod_date,
            )
            pdf = out_dir / f"{example.name}.pdf"
            embed.attach(
                plain, [plannotation], embed.build_index([plannotation]), pdf, mod_date=mod_date
            )
        thumbnail(pdf, plannotation, out_dir / f"{example.name}.png")
        written.append(pdf)
        entries.append(index_entry(example, plannotation))
    (out_dir / "index.json").write_text(
        json.dumps({"examples": entries}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return written


def index_entry(example: Example, plannotation: Plannotation) -> dict[str, object]:
    """Describe one example for the site.

    Args:
        example: The example.
        plannotation: Its sheet's plannotation.

    Returns:
        Its entry in ``index.json``.
    """
    sheet = plannotation.sheet
    source = example.source
    return {
        "name": example.name,
        "pdf": f"{example.name}.pdf",
        "png": f"{example.name}.png",
        "sheet": sheet.sheet_id,
        "title": sheet.title,
        "paper": _paper_name(plannotation.page.width_mm, plannotation.page.height_mm),
        "scale": f"1:{sheet.scale:.0f}" if sheet.scale else None,
        "elements": len(plannotation.elements or []),
        "annotations": len(plannotation.annotations or []),
        "source": {
            "title": source.title,
            "url": source.page,
            "author": source.author,
            "license": CC_BY_4[0],
            "license_url": CC_BY_4[1],
        },
    }


def _paper_name(width_mm: float, height_mm: float) -> str:
    """Name an A-series page from its size.

    Args:
        width_mm: The width.
        height_mm: The height.

    Returns:
        ``A1`` and so on, or the size in millimetres for a page that is none of them.
    """
    for name, (width, height) in PAPER_SIZES.items():
        if {round(width), round(height)} == {round(width_mm), round(height_mm)}:
            return name
    return f"{width_mm:g} x {height_mm:g} mm"


def thumbnail(pdf: Path, plannotation: Plannotation, out: Path) -> Path:
    """Render page 1 with the plannotation's outlines over it, as the inspector shows it.

    Viewports dashed in green, elements' boxes and outlines in blue, annotations' boxes
    and geometry in amber, one pixel wide -- the inspector's light theme.

    Args:
        pdf: The plannotated PDF.
        plannotation: Its first page's plannotation.
        out: Where to write the PNG.

    Returns:
        The path written.
    """
    pdfium = importlib.import_module("pypdfium2")
    image_draw = importlib.import_module("PIL.ImageDraw")
    document = pdfium.PdfDocument(str(pdf))
    try:
        page = document[0]
        width_pt = page.get_width()
        bitmap = page.render(scale=THUMBNAIL_WIDTH_PX / width_pt)
        image = bitmap.to_pil().convert("RGB")
    finally:
        document.close()
    scale = image.width / plannotation.page.width_mm
    height = plannotation.page.height_mm

    def pixel(point: Sequence[float]) -> tuple[float, float]:
        return (point[0] * scale, (height - point[1]) * scale)

    draw = image_draw.Draw(image)
    for viewport in plannotation.viewports or []:
        _dashed_box(draw, [pixel(viewport.paper_bbox[:2]), pixel(viewport.paper_bbox[2:])])
    for element in plannotation.elements or []:
        colour = INSPECTOR_COLOURS["element"]
        _box(draw, element.paper_bbox, pixel, colour)
        for outline in element.paper_outlines or []:
            draw.line([pixel(p) for p in outline], fill=colour, width=1)
    for annotation in plannotation.annotations or []:
        colour = INSPECTOR_COLOURS["annotation"]
        _box(draw, annotation.paper_bbox, pixel, colour)
        if annotation.geometry:
            draw.line([pixel(p) for p in annotation.geometry], fill=colour, width=1)
    image.save(out, format="PNG", optimize=True)
    return out


def _box(
    draw: object,
    box: Sequence[float],
    pixel: Callable[[Sequence[float]], tuple[float, float]],
    colour: str,
) -> None:
    """Stroke a paper box.

    Args:
        draw: The ``PIL.ImageDraw.Draw``.
        box: The box in paper millimetres.
        pixel: Paper to pixels.
        colour: The stroke colour.
    """
    (x0, y1), (x1, y0) = pixel(box[:2]), pixel(box[2:])
    draw.rectangle([x0, y0, x1, y1], outline=colour, width=1)  # type: ignore[attr-defined]


def _dashed_box(draw: object, corners: list[tuple[float, float]]) -> None:
    """Stroke a box in the inspector's viewport dash, six on and four off.

    Args:
        draw: The ``PIL.ImageDraw.Draw``.
        corners: Two opposite corners in pixels.
    """
    (xa, ya), (xb, yb) = corners
    x0, x1, y0, y1 = min(xa, xb), max(xa, xb), min(ya, yb), max(ya, yb)
    colour = INSPECTOR_COLOURS["viewport"]
    for start, end in (
        ((x0, y0), (x1, y0)),
        ((x1, y0), (x1, y1)),
        ((x1, y1), (x0, y1)),
        ((x0, y1), (x0, y0)),
    ):
        length = max(abs(end[0] - start[0]), abs(end[1] - start[1]))
        step = 0.0
        while step < length:
            a = step / length if length else 0.0
            b = min(step + 6.0, length) / length if length else 0.0
            draw.line(  # type: ignore[attr-defined]
                [
                    (start[0] + (end[0] - start[0]) * a, start[1] + (end[1] - start[1]) * a),
                    (start[0] + (end[0] - start[0]) * b, start[1] + (end[1] - start[1]) * b),
                ],
                fill=colour,
                width=1,
            )
            step += 10.0
