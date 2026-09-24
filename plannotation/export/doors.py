# SPDX-License-Identifier: Apache-2.0
"""Door swings on a plan, from each door's placement, width and operation type.

A plan cut through a door draws its frame and its closed leaf, a thin rectangle in a
gap in the wall -- which is not how anyone draws a door. The convention is the leaf
open at a right angle and the arc its edge sweeps, which says which way the door opens
and on which side it hangs. The IfcOpenShell serializer draws those arcs only in its
storey-plan mode, and a model exported from Archicad carries no 2D symbol to draw
instead, so the swing is constructed here from what the model says:

* the door's placement, whose x-axis runs across the opening and whose positive
  y-axis is the side the leaf opens to;
* ``OverallWidth``, the leaf's reach;
* the operation type -- ``SINGLE_SWING_LEFT`` hangs the leaf on the left seen along
  the positive y-axis, which is the placement's origin, and ``SINGLE_SWING_RIGHT`` on
  the right, at ``OverallWidth`` -- on the door in IFC4 and on its ``IfcDoorStyle`` in
  IFC2X3;
* the face the swing starts from: the face of the wall the door stands in, or the
  door's own geometry where that reaches further.

This is IFC's convention as ``ifcopenshell.api.geometry.add_door_representation``
reads it. A double door opens two leaves, split as the first panel's ``PanelWidth``
says; a double-swing door sweeps both faces. Sliding, folding, revolving and
user-defined doors get no swing, because the model does not say how they move.

Every length here is in metres, in model coordinates.
"""

from __future__ import annotations

import importlib
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from plannotation.errors import MissingExtraError

if TYPE_CHECKING:
    from collections.abc import Iterable

#: A polyline in model x and y, in metres.
Polyline = tuple[tuple[float, float], ...]

#: Operation types whose leaves swing, with the hinge side of each leaf and whether it
#: swings through to the negative side too.
_SWINGS: dict[str, tuple[tuple[str, ...], bool]] = {
    "SINGLE_SWING_LEFT": (("left",), False),
    "SINGLE_SWING_RIGHT": (("right",), False),
    "DOUBLE_SWING_LEFT": (("left",), True),
    "DOUBLE_SWING_RIGHT": (("right",), True),
    "DOUBLE_DOOR_SINGLE_SWING": (("left", "right"), False),
    "DOUBLE_DOOR_DOUBLE_SWING": (("left", "right"), True),
}

#: How many straight segments approximate a quarter arc.
ARC_SEGMENTS = 16


@dataclass(frozen=True)
class Swing:
    """How one door opens, drawn: each leaf open square to the wall, and its arc.

    Attributes:
        leaves: One line per leaf, from the hinge to the leaf's edge.
        arcs: One quarter arc per leaf, from the open edge back to the closed one.
    """

    leaves: tuple[Polyline, ...]
    arcs: tuple[Polyline, ...]

    @property
    def polylines(self) -> tuple[Polyline, ...]:
        """Return every line the swing draws.

        Returns:
            The leaves, then the arcs.
        """
        return self.leaves + self.arcs


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
            "door swings need ifcopenshell, which is not installed. It lives behind an "
            "optional extra: install it with `pip install 'plannotation[ifc]'`"
        )
        raise MissingExtraError(msg) from exc


def operation_type(door: Any) -> str | None:  # noqa: ANN401 - ifcopenshell is untyped here
    """Return how a door operates: the door's own type, else its type object's.

    Args:
        door: The ``IfcDoor``.

    Returns:
        The operation type, such as ``SINGLE_SWING_LEFT``, or None if neither says.
    """
    own = getattr(door, "OperationType", None)
    if own and own != "NOTDEFINED":
        return str(own)
    kind = _require("ifcopenshell.util.element").get_type(door)
    value = getattr(kind, "OperationType", None) if kind is not None else None
    return str(value) if value else None


def _first_panel_share(door: Any) -> float:  # noqa: ANN401 - ifcopenshell is untyped here
    """Return the share of a double door's width that its first, left, panel takes.

    Args:
        door: The ``IfcDoor``.

    Returns:
        The first panel's ``PanelWidth`` where its type states a share below one, and
        one half otherwise.
    """
    kind = _require("ifcopenshell.util.element").get_type(door)
    for definition in getattr(kind, "HasPropertySets", None) or ():
        if not definition.is_a("IfcDoorPanelProperties"):
            continue
        share = definition.PanelWidth
        if definition.PanelPosition == "LEFT" and share and 0.0 < float(share) < 1.0:
            return float(share)
    return 0.5


def _local_bounds(door: Any, width: float, scale: float) -> tuple[float, float] | None:  # noqa: ANN401
    """Return where the faces of a door's wall are, along the door's own y-axis.

    The swing starts at the face of the wall, which is not always where the door's own
    geometry ends: a door set in the middle of a thick wall stops short of both faces.
    So the wall the door's opening cuts is measured too, beside the opening, and the
    further of the two taken on each side.

    Args:
        door: The ``IfcDoor``.
        width: Its width, in metres.
        scale: The model's length unit in metres.

    Returns:
        ``(least, most)`` local y in metres, or None for a door with no geometry.
    """
    geom = _require("ifcopenshell.geom")
    np = _require("numpy")
    try:
        shape = geom.create_shape(geom.settings(), door)
    except RuntimeError:
        return None
    ys = list(shape.geometry.verts)[1::3]
    if not ys:
        return None
    least, most = min(ys), max(ys)
    host = _host(door)
    if host is None:
        return (least, most)
    settings = geom.settings()
    settings.set("use-world-coords", True)  # noqa: FBT003 - the wrapper is positional
    try:
        wall = geom.create_shape(settings, host)
    except RuntimeError:
        return (least, most)
    matrix = np.array(
        _require("ifcopenshell.util.placement").get_local_placement(door.ObjectPlacement)
    )
    matrix[:3, 3] *= scale
    points = np.array(wall.geometry.verts, dtype=float).reshape(-1, 3)
    local = (np.linalg.inv(matrix) @ np.c_[points, np.ones(len(points))].T).T
    beside = local[(local[:, 0] > -_JAMB_M) & (local[:, 0] < width + _JAMB_M)]
    if len(beside):
        least = min(least, float(beside[:, 1].min()))
        most = max(most, float(beside[:, 1].max()))
    return (least, most)


#: How far past the jambs the wall is measured, in metres.
_JAMB_M = 0.05


def _host(door: Any) -> Any:  # noqa: ANN401 - ifcopenshell is untyped here
    """Return the wall a door's opening is cut in, if the model says.

    Args:
        door: The ``IfcDoor``.

    Returns:
        The wall, or None.
    """
    for fills in getattr(door, "FillsVoids", None) or ():
        for voids in getattr(fills.RelatingOpeningElement, "VoidsElements", None) or ():
            return voids.RelatingBuildingElement
    return None


def _arc(
    centre: tuple[float, float], radius: float, start: float, end: float
) -> tuple[tuple[float, float], ...]:
    """Return a circular arc as a polyline.

    Args:
        centre: The centre.
        radius: The radius.
        start: The start angle in radians.
        end: The end angle.

    Returns:
        :data:`ARC_SEGMENTS` + 1 points from ``start`` to ``end``.
    """
    return tuple(
        (
            centre[0] + radius * math.cos(start + (end - start) * index / ARC_SEGMENTS),
            centre[1] + radius * math.sin(start + (end - start) * index / ARC_SEGMENTS),
        )
        for index in range(ARC_SEGMENTS + 1)
    )


def local_swing(
    operation: str, width: float, faces: tuple[float, float], first_share: float = 0.5
) -> Swing | None:
    """Return a door's swing in its own coordinates.

    Args:
        operation: The operation type.
        width: ``OverallWidth``, in metres.
        faces: The door's least and most local y, in metres; the leaf opens from the
            most, and a double-swing leaf from the least as well.
        first_share: The left leaf's share of a double door's width.

    Returns:
        The swing, or None for an operation type that does not swing.
    """
    if operation not in _SWINGS or width <= 0.0:
        return None
    hinges, both_ways = _SWINGS[operation]
    spans = {"left": (0.0, width)} if hinges == ("left",) else {"right": (0.0, width)}
    if len(hinges) == 2:  # noqa: PLR2004 - a double door
        split = width * first_share
        spans = {"left": (0.0, split), "right": (split, width)}
    leaves: list[Polyline] = []
    arcs: list[Polyline] = []
    for side in (1.0, -1.0) if both_ways else (1.0,):
        face = faces[1] if side > 0 else faces[0]
        for hinge_side, (low, high) in spans.items():
            reach = high - low
            hinge = (low, face) if hinge_side == "left" else (high, face)
            leaves.append((hinge, (hinge[0], face + side * reach)))
            # From the open leaf's edge round to where the closed leaf's edge is.
            closed = 0.0 if hinge_side == "left" else math.pi * side
            opened = math.pi / 2.0 * side
            arcs.append(_arc(hinge, reach, opened, closed))
    return Swing(leaves=tuple(leaves), arcs=tuple(arcs))


def door_swings(
    model: Any,  # noqa: ANN401 - ifcopenshell is untyped here
    guids: Iterable[str],
) -> tuple[dict[str, Swing], dict[str, str]]:
    """Return the swing of each door, in model x and y, and why the others have none.

    Args:
        model: The ``ifcopenshell.file``.
        guids: The doors, by GlobalId; anything that is not a door is passed over.

    Returns:
        The swings by GlobalId, and a reason for each door that has none.
    """
    placement = _require("ifcopenshell.util.placement")
    scale = float(_require("ifcopenshell.util.unit").calculate_unit_scale(model))
    swings: dict[str, Swing] = {}
    reasons: dict[str, str] = {}
    for guid in sorted(guids):
        door = model.by_guid(guid)
        if not door.is_a("IfcDoor"):
            continue
        operation = operation_type(door)
        if operation not in _SWINGS:
            reasons[guid] = f"operation type {operation or 'not stated'} does not swing"
            continue
        if not door.OverallWidth:
            reasons[guid] = "no OverallWidth"
            continue
        width = float(door.OverallWidth) * scale
        faces = _local_bounds(door, width, scale)
        if faces is None:
            reasons[guid] = "no geometry"
            continue
        swing = local_swing(operation, width, faces, _first_panel_share(door))
        if swing is None:  # pragma: no cover - every _SWINGS type swings
            continue
        matrix = placement.get_local_placement(door.ObjectPlacement)

        def world(point: tuple[float, float], matrix: Any = matrix) -> tuple[float, float]:  # noqa: ANN401
            x = matrix[0][0] * point[0] + matrix[0][1] * point[1] + matrix[0][3] * scale
            y = matrix[1][0] * point[0] + matrix[1][1] * point[1] + matrix[1][3] * scale
            return (float(x), float(y))

        swings[guid] = Swing(
            leaves=tuple(tuple(world(p) for p in line) for line in swing.leaves),
            arcs=tuple(tuple(world(p) for p in line) for line in swing.arcs),
        )
    return swings, reasons
