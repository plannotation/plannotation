# SPDX-License-Identifier: Apache-2.0
"""Rule 4: cross-check a plannotation against the IFC model it says it came from.

Everything above this module is a statement about one file. This is the only rule family
that puts two files side by side and asks whether they agree, which is also why it runs
only when the caller passes ``--ifc``: the model is not part of the payload, it is not
named by anything the validator may dereference (9.1 forbids resolving a filename found
in a plannotation), and a validator that went looking for one would be doing something
the specification tells readers not to do.

Three checks, and the reason two are errors and one is not
----------------------------------------------------------
* **PL-IFC-001**, an ``ifcGuid`` that names no entity in the model, and
  **PL-IFC-002**, an entity whose class is not the ``ifcClass`` claimed, are plain
  facts about two files the caller handed over together. No tolerance decides them, so
  both are errors.
* **PL-IFC-003**, a dimension re-measured in the model, is a warning. It rests on a
  tolerance -- 1 per cent or 5 mm, whichever is larger -- and on the heuristic that the
  distance a dimension records between two elements is the distance between their
  bounding geometry projected onto the viewport's plane. That is right for the ordinary
  case and wrong for a dimension to a face, to a centre line or to a grid, and 4.4
  files a finding that rests on a tolerance or a heuristic as a warning.

Class matching is directional
-----------------------------
A plannotation may name a **supertype** of the entity's class and nothing narrower:
``IfcWall`` for an ``IfcWallStandardCase`` is a true, if less precise, statement, while
``IfcWallStandardCase`` for a plain ``IfcWall`` is false. ``ifcopenshell``'s
``entity.is_a(name)`` answers exactly that question in that direction, so the check is
one call and cannot be got backwards by accident.

ifcopenshell is behind the ``ifc`` extra
----------------------------------------
It publishes no source distribution, ships a 42 MB wheel, and drags in shapely's
prebuilt GEOS; it is not installed in CI and is not installed by a default ``pip
install plannotation``. It is therefore imported inside the function that needs it, and
its absence raises :class:`plannotation.errors.MissingExtraError` rather than being
skipped. Skipping would report a cross-check that was never made, which is the one
thing a validator may never do.
"""

from __future__ import annotations

import importlib
import logging
import math
from typing import TYPE_CHECKING, Any, Final

from plannotation.errors import InputNotValidatableError, MissingExtraError
from plannotation.validate.codes import finding
from plannotation.validate.geometric import (
    MM_PER_MODEL_UNIT,
    declared_length,
    measured_length_mm,
    millimetres,
)
from plannotation.validate.schema import json_pointer

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from plannotation.model import Annotation, Element, Plannotation
    from plannotation.validate.report import Finding

__all__ = [
    "REMEASURE_TOLERANCE_MM",
    "REMEASURE_TOLERANCE_RATIO",
    "IfcModel",
    "check_against_model",
    "open_model",
    "remeasurable",
]

_LOGGER: Final = logging.getLogger(__name__)

#: Relative tolerance on a re-measured distance -- the design brief's 1 per cent.
REMEASURE_TOLERANCE_RATIO: Final = 0.01

#: Absolute floor of that tolerance, in model millimetres -- the design brief's 5 mm.
#:
#: "1 % or 5 mm, whichever is LARGER": a 200 mm dimension is held to 5 mm and a 10 m one
#: to 100 mm. The floor exists because the smaller a dimension is, the more of its
#: length is taken up by the thickness of what it measures.
REMEASURE_TOLERANCE_MM: Final = 5.0

#: How many entities a bounding box may be computed for in one run.
#:
#: Geometry is the expensive part of reading an IFC model and the model is an input like
#: any other. A validator bounds what it spends; past this many the re-measurement is
#: reported as not done rather than allowed to run away.
MAX_MEASURED_ENTITIES: Final = 2000


class IfcModel:
    """An open IFC model, with the two questions this module asks it.

    Thin on purpose. It exists so that ``ifcopenshell`` is named in one place and so
    that the rest of this module can be read, and reasoned about, without it installed.

    Attributes:
        path: The file the model was read from.
    """

    def __init__(self, path: Path, model: Any) -> None:  # noqa: ANN401 - ifcopenshell is untyped here
        """Wrap an opened ifcopenshell file.

        Args:
            path: The file it was read from.
            model: The ``ifcopenshell.file`` object.
        """
        self.path = path
        self._model = model
        self._boxes: dict[str, tuple[tuple[float, float, float], tuple[float, float, float]]] = {}
        self._measured = 0

    def entity(self, guid: str) -> Any | None:  # noqa: ANN401 - ifcopenshell is untyped here
        """Return the entity with a GlobalId, or None.

        Args:
            guid: The IFC GlobalId, 22 characters.

        Returns:
            The entity, or None when the model holds no such GlobalId.
        """
        try:
            return self._model.by_guid(guid)
        except RuntimeError:
            return None

    def bounds(
        self, guid: str
    ) -> tuple[tuple[float, float, float], tuple[float, float, float]] | None:
        """Return an entity's axis-aligned bounds in model coordinates.

        Args:
            guid: The entity's GlobalId.

        Returns:
            The minimum and maximum corner, or None when the entity has no geometry,
            when its geometry cannot be built, or when this run has already measured
            :data:`MAX_MEASURED_ENTITIES` entities.
        """
        if guid in self._boxes:
            return self._boxes[guid]
        if self._measured >= MAX_MEASURED_ENTITIES:
            return None
        entity = self.entity(guid)
        if entity is None or getattr(entity, "Representation", None) is None:
            return None
        self._measured += 1
        computed = self._shape_bounds(entity)
        if computed is not None:
            self._boxes[guid] = computed
        return computed

    def _shape_bounds(
        self,
        entity: Any,  # noqa: ANN401 - ifcopenshell is untyped here
    ) -> tuple[tuple[float, float, float], tuple[float, float, float]] | None:
        """Build one entity's shape and return its bounds.

        Args:
            entity: The ifcopenshell entity.

        Returns:
            Its bounds in model coordinates, or None when the shape cannot be built.
            A model that refuses to tessellate one element is an ordinary occurrence
            and must not end the run, so the failure is logged and treated as "no
            geometry".
        """
        geom = _module("ifcopenshell.geom")
        settings = geom.settings()
        # ifcopenshell's own signature is set(k, v), positional. The True is a value
        # passed to a generic setter, not a mode flag, so the boolean-trap rule does
        # not apply and the keyword form it suggests is a TypeError.
        settings.set("use-world-coords", True)  # noqa: FBT003
        try:
            shape = geom.create_shape(settings, entity)
        except (RuntimeError, OSError) as exc:  # pragma: no cover - model-dependent
            _LOGGER.debug("no geometry for %s: %s", entity.GlobalId, exc)
            return None
        vertices = [float(value) for value in shape.geometry.verts]
        if not vertices:
            return None
        xs, ys, zs = vertices[0::3], vertices[1::3], vertices[2::3]
        return ((min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs)))


def open_model(path: Path) -> IfcModel:
    """Open an IFC model, or say plainly why it cannot be opened.

    Args:
        path: The ``.ifc`` file.

    Returns:
        The opened model.

    Raises:
        MissingExtraError: If ifcopenshell is not installed. It lives behind the
            ``ifc`` extra and is not installed in CI, and a cross-check that was asked
            for and not made must not be reported as a pass.
        InputNotValidatableError: If the file cannot be read as IFC.
    """
    ifcopenshell = _module("ifcopenshell")
    try:
        model = ifcopenshell.open(str(path))
    except (OSError, RuntimeError) as exc:
        msg = f"{path} could not be read as an IFC model: {exc}"
        raise InputNotValidatableError(msg) from exc
    return IfcModel(path, model)


def _module(name: str) -> Any:  # noqa: ANN401 - the whole point is that it is untyped here
    """Import one ifcopenshell module, or say what to install.

    Imported through :func:`importlib.import_module` rather than with an ``import``
    statement, and deliberately. The extra is installed on a developer's machine and
    absent from CI, so a plain import type-checks differently in the two places: with
    the package present it resolves to ifcopenshell's real (and elaborate) types, and
    without it, it does not resolve at all. Going through :mod:`importlib` makes this
    module read the same in both, and the values it returns are treated as the untyped
    data they are either way.

    Args:
        name: ``"ifcopenshell"`` or ``"ifcopenshell.geom"``.

    Returns:
        The imported module.

    Raises:
        MissingExtraError: If it is not installed.
    """
    try:
        return importlib.import_module(name)
    except ImportError as exc:
        package = name.partition(".")[0]
        msg = (
            f"--ifc needs {package}, which is not installed. It lives behind "
            f"an optional extra because it ships a 42 MB wheel and pulls a prebuilt "
            f"GEOS: install it with `pip install 'plannotation[ifc]'` or "
            f"`uv sync --extra ifc`, then run the command again. The rest of the "
            f"validation is unaffected and runs without --ifc"
        )
        raise MissingExtraError(msg) from exc


def check_against_model(
    plannotation: Plannotation, model: IfcModel, *, source: str
) -> list[Finding]:
    """Cross-check one plannotation against an IFC model.

    Args:
        plannotation: The loaded plannotation.
        model: The opened model.
        source: Which document the plannotation is, for the findings.

    Returns:
        Every violation: unresolved GlobalIds, class mismatches, and dimensions whose
        printed value disagrees with the distance re-measured in the model.
    """
    found = _check_guids(plannotation, model, source=source)
    found += _check_dimensions(plannotation, model, source=source)
    return found


def _check_guids(plannotation: Plannotation, model: IfcModel, *, source: str) -> list[Finding]:
    """Check every GlobalId a plannotation carries against the model.

    Args:
        plannotation: The loaded plannotation.
        model: The opened model.
        source: Which document it is, for the findings.

    Returns:
        One finding per GlobalId the model does not hold, plus one per element whose
        class the entity is not.
    """
    found: list[Finding] = []
    for name, path, guid, claimed in _guids(plannotation):
        entity = model.entity(guid)
        if entity is None:
            found.append(
                finding(
                    "PL-IFC-001",
                    message=(f"{name}: ifcGuid {guid!r} names no entity in {model.path.name}"),
                    path=path,
                    source=source,
                )
            )
            continue
        if claimed is None or entity.is_a(claimed):
            continue
        found.append(
            finding(
                "PL-IFC-002",
                message=(
                    f"{name}: declares ifcClass {claimed!r}, but {guid} is an "
                    f"{entity.is_a()} in {model.path.name}; a plannotation may name a "
                    f"supertype of the entity's class and nothing narrower"
                ),
                path=path,
                source=source,
            )
        )
    return found


def _guids(plannotation: Plannotation) -> list[tuple[str, str, str, str | None]]:
    """Collect every GlobalId in a plannotation with a name, a pointer and a claimed class.

    Args:
        plannotation: The plannotation to walk.

    Returns:
        One entry per ``ifcGuid``: a human-readable name, a JSON Pointer, the GlobalId,
        and the ``ifcClass`` claimed for it where there is one. A viewport's storey and
        an annotation carry a GlobalId with no class beside it, so theirs is None and
        only PL-IFC-001 applies to them.
    """
    collected: list[tuple[str, str, str, str | None]] = []
    for position, viewport in enumerate(plannotation.viewports or []):
        storey = viewport.storey
        if storey is not None and storey.ifc_guid is not None:
            collected.append(
                (
                    f"viewport {viewport.local_id!r} storey",
                    json_pointer(["viewports", position, "storey", "ifcGuid"]),
                    storey.ifc_guid,
                    None,
                )
            )
    for position, element in enumerate(plannotation.elements or []):
        if element.ifc_guid is None:
            continue
        collected.append(
            (
                f"element {element.local_id!r}",
                json_pointer(["elements", position, "ifcGuid"]),
                element.ifc_guid,
                element.ifc_class,
            )
        )
    for position, annotation in enumerate(plannotation.annotations or []):
        if annotation.ifc_guid is None:
            continue
        collected.append(
            (
                f"annotation {annotation.local_id!r}",
                json_pointer(["annotations", position, "ifcGuid"]),
                annotation.ifc_guid,
                None,
            )
        )
    return collected


def _check_dimensions(plannotation: Plannotation, model: IfcModel, *, source: str) -> list[Finding]:
    """Re-measure every dimension that runs between two elements with geometry.

    Args:
        plannotation: The loaded plannotation.
        model: The opened model.
        source: Which document it is, for the findings.

    Returns:
        One warning per dimension whose value disagrees with the re-measured distance
        by more than 1 per cent or 5 mm, whichever is larger. A dimension that names
        fewer than two elements, names one without a GlobalId, or names one whose
        geometry cannot be built is not reported at all: there is nothing to compare.
    """
    unit = plannotation.source_model.length_unit if plannotation.source_model else None
    if unit is None:
        return []
    elements = {element.local_id: element for element in plannotation.elements or []}
    found: list[Finding] = []
    for position, annotation in enumerate(plannotation.annotations or []):
        expected = measured_length_mm(annotation)
        if expected is None:
            continue
        measured = _remeasure(annotation, elements, model, unit=unit)
        if measured is None:
            continue
        tolerance = max(REMEASURE_TOLERANCE_MM, REMEASURE_TOLERANCE_RATIO * abs(expected))
        if abs(measured - expected) <= tolerance:
            continue
        found.append(
            finding(
                "PL-IFC-003",
                message=(
                    f"annotation {annotation.local_id!r}: the value is "
                    f"{declared_length(annotation)}, but the two elements it measures "
                    f"are {millimetres(measured)} mm apart in {model.path.name} "
                    f"(tolerance {millimetres(tolerance)} mm)"
                ),
                path=json_pointer(["annotations", position]),
                source=source,
            )
        )
    return found


def _remeasure(
    annotation: Annotation,
    elements: Mapping[str, Element],
    model: IfcModel,
    *,
    unit: str,
) -> float | None:
    """Measure the distance between the two elements a dimension names.

    The distance is the gap between the two elements' axis-aligned bounds, which is
    zero where they touch or overlap and the clear distance otherwise. It is the
    quantity an ordinary running dimension records, and the heuristic PL-IFC-003 is a
    warning for.

    Args:
        annotation: The dimension.
        elements: Every element on the page, by local id.
        model: The opened model.
        unit: ``model.lengthUnit``, which the model's coordinates are in.

    Returns:
        The distance in model millimetres, or None when it cannot be measured.
    """
    named = [elements[ref] for ref in (annotation.measures or []) if ref in elements]
    if len(named) != 2:  # noqa: PLR2004 - a dimension runs between exactly two things
        return None
    boxes = [model.bounds(item.ifc_guid) for item in named if item.ifc_guid is not None]
    if len(boxes) != 2 or any(box is None for box in boxes):  # noqa: PLR2004 - as above
        return None
    first, second = boxes
    if first is None or second is None:  # pragma: no cover - narrowed by the test above
        return None
    gaps = [
        max(first[0][axis] - second[1][axis], second[0][axis] - first[1][axis], 0.0)
        for axis in range(3)
    ]
    distance = math.sqrt(sum(gap * gap for gap in gaps))
    return distance * MM_PER_MODEL_UNIT[unit]


def remeasurable(plannotation: Plannotation) -> int:
    """Count the dimensions a model cross-check could re-measure.

    Args:
        plannotation: The plannotation.

    Returns:
        How many dimensions name exactly two elements that carry a GlobalId. Reported
        alongside the findings so that "no mismatches" can be told from "nothing was
        compared".
    """
    elements = {element.local_id: element for element in plannotation.elements or []}
    count = 0
    for annotation in plannotation.annotations or []:
        if measured_length_mm(annotation) is None:
            continue
        named = [elements[ref] for ref in (annotation.measures or []) if ref in elements]
        if len(named) == 2 and all(item.ifc_guid is not None for item in named):  # noqa: PLR2004
            count += 1
    return count
