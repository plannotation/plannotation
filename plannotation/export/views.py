# SPDX-License-Identifier: Apache-2.0
"""Where to cut a real model, and what its grid and storeys say about the drawing.

The sample models stand at z = 0, along the world axes, with a grid the exporter is
told about. A model from practice does none of that. Maleva 18 stands its ground floor
at z = 14.30 m, places its IfcGrid 60.8 degrees off the world axes in national
coordinates half a million metres from the origin, and names its levels relative to a
±0,00 that is nowhere near z = 0. This module reads those facts from the model, in the
model's own terms, so that a plan can be drawn square to the grid and a section can be
marked with the levels the building uses:

* which products belong to one storey (:func:`storey_products`),
* where the building's ±0,00 is (:func:`building_datum`) and where each storey stands
  (:func:`storey_levels`),
* the grid's axes as lines in the world (:func:`grid_lines`), a plan plane square to
  them (:func:`plan_along`) and a section plane between two of them
  (:func:`section_between`), and
* which grid lines a plane shows, and where (:func:`grid_axes_on`).

Every length it returns is in metres, whatever the model's unit, because the exporter's
geometry is: the serializer works in metres, and the unit is divided out once, at the
end, when a plannotation is written.

ifcopenshell lives behind the ``ifc`` extra, so it is imported inside the functions.
"""

from __future__ import annotations

import importlib
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from plannotation.errors import ExportError, MissingExtraError
from plannotation.export.ifc_svg_pdf import GridAxis
from plannotation.export.models import Level, SectionCut

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from pathlib import Path

#: Classes a drawing of a storey leaves out: voids, which the elements they cut already
#: show, the grid, which the sheet draws itself, and anything that is not built.
NOT_DRAWN = (
    "IfcOpeningElement",
    "IfcGrid",
    "IfcAnnotation",
    "IfcVirtualElement",
    "IfcSpatialStructureElement",
)

#: How far from parallel a grid line may be to a plane axis and still be drawn along it.
_PARALLEL = 1e-6

#: How far two storeys may disagree about the building's ±0,00, in metres.
_DATUM_TOLERANCE_M = 0.001


def _require(name: str) -> Any:  # noqa: ANN401 - ifcopenshell is untyped here
    """Import one ifcopenshell module, or say what to install.

    Args:
        name: The module to import.

    Returns:
        The module.

    Raises:
        MissingExtraError: If it is not installed.
    """
    try:
        return importlib.import_module(name)
    except ImportError as exc:
        msg = (
            "drawing a real model needs ifcopenshell, which is not installed. It lives "
            "behind an optional extra: install it with `pip install 'plannotation[ifc]'` "
            "or `uv sync --extra ifc`"
        )
        raise MissingExtraError(msg) from exc


def open_model(path: Path) -> Any:  # noqa: ANN401 - ifcopenshell is untyped here
    """Open an IFC file.

    Args:
        path: The file.

    Returns:
        The ``ifcopenshell.file``.
    """
    return _require("ifcopenshell").open(str(path))


def unit_scale(model: Any) -> float:  # noqa: ANN401 - ifcopenshell is untyped here
    """Return the model's length unit in metres.

    Args:
        model: The ``ifcopenshell.file``.

    Returns:
        0.001 for a model in millimetres.
    """
    return float(_require("ifcopenshell.util.unit").calculate_unit_scale(model))


def _z(product: Any, scale: float) -> float:  # noqa: ANN401 - ifcopenshell is untyped here
    """Return the z of a product's placement in model coordinates, in metres.

    Args:
        product: The product.
        scale: The model's length unit in metres.

    Returns:
        The z.
    """
    placement = _require("ifcopenshell.util.placement")
    return float(placement.get_local_placement(product.ObjectPlacement)[2, 3]) * scale


@dataclass(frozen=True)
class Storey:
    """One storey: its name, its GlobalId and where it stands.

    Attributes:
        name: Its name.
        guid: Its GlobalId.
        z: The z of its placement in model coordinates, in metres.
        elevation: Its ``Elevation``, in metres above the building's ±0,00, or None.
    """

    name: str
    guid: str
    z: float
    elevation: float | None


def storeys(model: Any) -> list[Storey]:  # noqa: ANN401 - ifcopenshell is untyped here
    """Return every storey, lowest first.

    Args:
        model: The ``ifcopenshell.file``.

    Returns:
        The storeys.
    """
    scale = unit_scale(model)
    found = [
        Storey(
            name=str(storey.Name or storey.GlobalId),
            guid=str(storey.GlobalId),
            z=_z(storey, scale),
            elevation=None if storey.Elevation is None else float(storey.Elevation) * scale,
        )
        for storey in model.by_type("IfcBuildingStorey")
        if storey.ObjectPlacement is not None
    ]
    return sorted(found, key=lambda storey: (storey.z, storey.name))


def find_storey(model: Any, name: str) -> Storey:  # noqa: ANN401 - ifcopenshell is untyped here
    """Return the storey with a name.

    Args:
        model: The ``ifcopenshell.file``.
        name: The storey's name.

    Returns:
        The storey.

    Raises:
        ExportError: If no storey, or more than one, has that name.
    """
    matches = [storey for storey in storeys(model) if storey.name == name]
    if len(matches) != 1:
        names = ", ".join(storey.name for storey in storeys(model))
        msg = f"the model has {len(matches)} storeys named {name!r}; its storeys are {names}"
        raise ExportError(msg)
    return matches[0]


def building_datum(model: Any) -> float:  # noqa: ANN401 - ifcopenshell is untyped here
    """Return the z of the building's ±0,00 in model coordinates, in metres.

    IFC measures a storey's ``Elevation`` from that datum, so a storey placed at z and
    elevated e puts it at z - e. Every storey that states an elevation is asked, and
    they must agree: a model whose storeys disagree about their own datum has no level
    a drawing could print. A model whose storeys state none has its datum at z = 0.

    Args:
        model: The ``ifcopenshell.file``.

    Returns:
        The datum's z.

    Raises:
        ExportError: If two storeys put the datum more than a millimetre apart.
    """
    datums = [
        storey.z - storey.elevation for storey in storeys(model) if storey.elevation is not None
    ]
    if not datums:
        return 0.0
    if max(datums) - min(datums) > _DATUM_TOLERANCE_M:
        msg = (
            f"the storeys put the building's ±0,00 at z = {min(datums):.3f} to "
            f"{max(datums):.3f} m; their Elevation and their placements disagree"
        )
        raise ExportError(msg)
    return round(sum(datums) / len(datums), 6)


def storey_levels(model: Any) -> tuple[Level, ...]:  # noqa: ANN401 - ifcopenshell is untyped
    """Return every storey as a level a section marks, lowest first.

    Args:
        model: The ``ifcopenshell.file``.

    Returns:
        One level per storey, at the z of its placement.
    """
    return tuple(
        Level(name=storey.name, elevation=round(storey.z, 6), guid=storey.guid)
        for storey in storeys(model)
    )


def storey_products(
    model: Any,  # noqa: ANN401 - ifcopenshell is untyped here
    storey: Storey,
    *,
    spaces: bool = True,
) -> tuple[str, ...]:
    """Return the GlobalIds of what one storey holds: its elements, their parts, its spaces.

    Everything the storey contains, everything those are made of, and -- unless
    ``spaces`` is False -- the spaces it is divided into, but no voids, no grid and no
    annotation (:data:`NOT_DRAWN`).

    Args:
        model: The ``ifcopenshell.file``.
        storey: The storey.
        spaces: Whether its spaces are drawn too.

    Returns:
        The GlobalIds, sorted.
    """
    element = _require("ifcopenshell.util.element")
    held = element.get_decomposition(model.by_guid(storey.guid))
    return tuple(
        sorted(str(product.GlobalId) for product in held if _drawn(product, spaces=spaces))
    )


def _drawn(product: Any, *, spaces: bool) -> bool:  # noqa: ANN401 - ifcopenshell is untyped
    """Say whether a drawing of the building shows a product.

    Args:
        product: The entity.
        spaces: Whether spaces are shown.

    Returns:
        True for a product that is built and not a void, a grid or an annotation.
    """
    if not product.is_a("IfcProduct"):
        return False
    if product.is_a("IfcSpace"):
        return spaces
    return not any(product.is_a(name) for name in NOT_DRAWN)


def building_products(model: Any, *, spaces: bool = False) -> tuple[str, ...]:  # noqa: ANN401
    """Return the GlobalIds of every element of the building, for a section through it.

    Args:
        model: The ``ifcopenshell.file``.
        spaces: Whether spaces are drawn too; a section usually leaves them out.

    Returns:
        The GlobalIds, sorted.
    """
    return tuple(
        sorted(
            {
                guid
                for storey in storeys(model)
                for guid in storey_products(model, storey, spaces=spaces)
            }
        )
    )


@dataclass(frozen=True)
class GridLine:
    """One axis of an IfcGrid as a line in the world.

    Attributes:
        tag: The axis's label, such as ``A`` or ``1``.
        start: One end, ``(x, y)`` in model coordinates, in metres.
        end: The other end.
    """

    tag: str
    start: tuple[float, float]
    end: tuple[float, float]

    @property
    def direction(self) -> tuple[float, float]:
        """Return the line's unit direction.

        Returns:
            ``(dx, dy)``.
        """
        dx, dy = self.end[0] - self.start[0], self.end[1] - self.start[1]
        length = math.hypot(dx, dy)
        return (dx / length, dy / length)


def grid_lines(model: Any, grid_name: str | None = None) -> list[GridLine]:  # noqa: ANN401
    """Return the axes of a grid as lines in model coordinates.

    Args:
        model: The ``ifcopenshell.file``.
        grid_name: The grid's name, or None for the model's only grid.

    Returns:
        One line per axis with a straight curve, in the order the grid lists them.

    Raises:
        ExportError: If there is no such grid, or more than one.
    """
    placement = _require("ifcopenshell.util.placement")
    scale = unit_scale(model)
    grids = [g for g in model.by_type("IfcGrid") if grid_name is None or g.Name == grid_name]
    if len(grids) != 1:
        msg = f"expected one IfcGrid named {grid_name!r}, found {len(grids)}"
        raise ExportError(msg)
    grid = grids[0]
    matrix = placement.get_local_placement(grid.ObjectPlacement)
    lines: list[GridLine] = []
    for axis in (*(grid.UAxes or ()), *(grid.VAxes or ()), *(grid.WAxes or ())):
        curve = axis.AxisCurve
        if not curve.is_a("IfcPolyline") or len(curve.Points) != 2:  # noqa: PLR2004 - a line
            continue
        ends = []
        for point in curve.Points:
            x, y = (float(value) for value in point.Coordinates[:2])
            world = matrix @ [x, y, 0.0, 1.0]
            ends.append((float(world[0]) * scale, float(world[1]) * scale))
        lines.append(GridLine(tag=str(axis.AxisTag), start=ends[0], end=ends[1]))
    return lines


def _cross(a: Sequence[float], b: Sequence[float]) -> tuple[float, float, float]:
    """Return the cross product of two 3-vectors.

    Args:
        a: One.
        b: The other.

    Returns:
        ``a x b``.
    """
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _intersection(first: GridLine, second: GridLine) -> tuple[float, float]:
    """Return where two grid lines cross.

    Args:
        first: One line.
        second: The other.

    Returns:
        The crossing point.

    Raises:
        ExportError: If they are parallel.
    """
    (x1, y1), (dx1, dy1) = first.start, first.direction
    (x2, y2), (dx2, dy2) = second.start, second.direction
    determinant = dx1 * (-dy2) - dy1 * (-dx2)
    if abs(determinant) < _PARALLEL:
        msg = f"grid lines {first.tag} and {second.tag} are parallel and do not cross"
        raise ExportError(msg)
    t = ((x2 - x1) * (-dy2) - (y2 - y1) * (-dx2)) / determinant
    return (x1 + t * dx1, y1 + t * dy1)


def _line(lines: Iterable[GridLine], tag: str) -> GridLine:
    """Return the grid line with a tag.

    Args:
        lines: The grid's lines.
        tag: The tag.

    Returns:
        The line.

    Raises:
        ExportError: If there is none.
    """
    for line in lines:
        if line.tag == tag:
            return line
    msg = f"the grid has no axis {tag!r}"
    raise ExportError(msg)


def plan_along(
    lines: Sequence[GridLine],
    *,
    origin: tuple[str, str],
    along: str,
    reverse: bool = False,
    z: float,
) -> SectionCut:
    """Return a plan's cutting plane, square to the grid, through the crossing of two axes.

    Args:
        lines: The grid's lines.
        origin: The tags of the two axes whose crossing is the plane's origin.
        along: The tag of an axis the paper's x runs along.
        reverse: Run the paper's x against that axis's direction instead.
        z: The height of the cut, in model coordinates, in metres.

    Returns:
        The plane, its normal pointing up.
    """
    x, y = _intersection(_line(lines, origin[0]), _line(lines, origin[1]))
    dx, dy = _line(lines, along).direction
    sign = -1.0 if reverse else 1.0
    return SectionCut(
        location=(x, y, z), direction=(0.0, 0.0, 1.0), x_axis=(sign * dx, sign * dy, 0.0)
    )


def section_between(
    lines: Sequence[GridLine],
    *,
    first: str,
    second: str,
    looking_to: str,
    datum: float,
) -> SectionCut:
    """Return a vertical section plane halfway between two parallel grid axes.

    Args:
        lines: The grid's lines.
        first: One axis.
        second: The axis parallel to it on the other side of the cut.
        looking_to: An axis parallel to both that the section looks towards.
        datum: The z the plane's y is measured from, in metres: the building's ±0,00,
            so that plane y reads as a level.

    Returns:
        The plane, its normal pointing away from ``looking_to`` and at the viewer.

    Raises:
        ExportError: If the axes are not parallel.
    """
    one, two, target = (_line(lines, tag) for tag in (first, second, looking_to))
    dx, dy = one.direction
    if abs(dx * two.direction[1] - dy * two.direction[0]) > _PARALLEL:
        msg = f"grid axes {first} and {second} are not parallel"
        raise ExportError(msg)
    normal = (-dy, dx)

    def offset(line: GridLine) -> float:
        return (line.start[0] * normal[0]) + (line.start[1] * normal[1])

    middle = (offset(one) + offset(two)) / 2.0
    towards = 1.0 if offset(target) > middle else -1.0
    point = (
        one.start[0] + normal[0] * (middle - offset(one)),
        one.start[1] + normal[1] * (middle - offset(one)),
    )
    # The normal points at the viewer, who stands on the far side from the target.
    facing = (-towards * normal[0], -towards * normal[1], 0.0)
    x_axis = _cross((0.0, 0.0, 1.0), facing)
    return SectionCut(location=(point[0], point[1], datum), direction=facing, x_axis=x_axis)


def grid_axes_on(lines: Sequence[GridLine], cut: SectionCut) -> tuple[GridAxis, ...]:
    """Return the grid lines a view shows, placed in its plane's coordinates.

    On a plan, a grid line square to the paper's x is drawn up the page at its plane x,
    and one square to its y across the page at its plane y; an oblique line is left
    out. On a section, a grid line that crosses the plane is drawn up the page where it
    crosses; one parallel to the plane does not appear.

    Args:
        lines: The grid's lines.
        cut: The view's cutting plane.

    Returns:
        The grid lines to draw, in the order the grid lists them.
    """
    x_axis, y_axis = cut.axes()
    ox, oy, _ = cut.location
    axes: list[GridAxis] = []
    horizontal = abs(cut.direction[2]) > 1.0 - _PARALLEL
    for line in lines:
        dx, dy = line.direction
        sx, sy = line.start[0] - ox, line.start[1] - oy
        if horizontal:
            if abs(dx * x_axis[0] + dy * x_axis[1]) < _PARALLEL:
                axes.append(
                    GridAxis(line.tag, vertical=True, position=sx * x_axis[0] + sy * x_axis[1])
                )
            elif abs(dx * y_axis[0] + dy * y_axis[1]) < _PARALLEL:
                axes.append(
                    GridAxis(line.tag, vertical=False, position=sx * y_axis[0] + sy * y_axis[1])
                )
            continue
        normal = cut.direction
        along = dx * normal[0] + dy * normal[1]
        if abs(along) < _PARALLEL:
            continue
        t = -(sx * normal[0] + sy * normal[1]) / along
        px, py = sx + t * dx, sy + t * dy
        axes.append(GridAxis(line.tag, vertical=True, position=px * x_axis[0] + py * x_axis[1]))
    return tuple(axes)


def true_north(model: Any) -> tuple[float, float]:  # noqa: ANN401 - ifcopenshell is untyped here
    """Return the direction of true north in model coordinates.

    Args:
        model: The ``ifcopenshell.file``.

    Returns:
        A unit ``(x, y)``: the model context's ``TrueNorth``, or +y where it states none.
    """
    for context in model.by_type("IfcGeometricRepresentationContext"):
        if context.is_a("IfcGeometricRepresentationSubContext"):
            continue
        north = getattr(context, "TrueNorth", None)
        if north is not None:
            x, y = (float(value) for value in north.DirectionRatios[:2])
            length = math.hypot(x, y)
            return (x / length, y / length)
    return (0.0, 1.0)
