# SPDX-License-Identifier: Apache-2.0
"""Build the small IFC models the sample drawings are made from.

These exist so that the samples are reproducible from source rather than committed as
binaries, and so that every question the benchmark asks has an answer that came from a
model rather than from someone reading a drawing.

**GlobalIds are assigned, not generated.** ``ifcopenshell.api`` mints a fresh
GlobalId for every entity it creates, so an unseeded model differs on every run and
nothing downstream -- the SVG, the plannotations, the ground truth -- can be
byte-reproducible. Each builder therefore walks its finished model and rewrites every
GlobalId from a seeded sequence, which is the only point in the chain where that can be
done once and for all.

ifcopenshell lives behind the ``ifc`` extra and is not installed in CI, so it is
imported inside the functions that need it.
"""

from __future__ import annotations

import hashlib
import importlib
from dataclasses import dataclass
from itertools import pairwise
from typing import TYPE_CHECKING, Any

from plannotation.errors import MissingExtraError

if TYPE_CHECKING:
    from pathlib import Path

#: Attributes IFC declares as sets, whose order therefore carries no meaning and
#: varies between runs. Each is sorted before a model is written.
_UNORDERED_SETS: tuple[tuple[str, str], ...] = (
    ("IfcUnitAssignment", "Units"),
    ("IfcRelContainedInSpatialStructure", "RelatedElements"),
    ("IfcRelAggregates", "RelatedObjects"),
    ("IfcRelAssociatesMaterial", "RelatedObjects"),
    ("IfcRelDefinesByProperties", "RelatedObjects"),
    ("IfcRelDefinesByType", "RelatedObjects"),
)

#: The IFC base64 alphabet, in the order the standard defines it.
_GUID_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_$"


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
            "building the sample models needs ifcopenshell, which is not installed. It "
            "lives behind an optional extra: install it with "
            "`pip install 'plannotation[ifc]'` or `uv sync --extra ifc`"
        )
        raise MissingExtraError(msg) from exc


def seeded_guid(seed: str, index: int) -> str:
    """Return a deterministic, well-formed IFC GlobalId.

    A GlobalId is 22 characters of IFC's own base64 alphabet. The value here is derived
    from a seed and an index rather than from randomness or a clock, so that a model
    rebuilt tomorrow carries the same identities as the one built today and the
    plannotations that name them stay true.

    Args:
        seed: The model's name, which makes ids unique between models.
        index: The entity's position in the model's creation order.

    Returns:
        A 22-character GlobalId.

    Examples:
        >>> seeded_guid("floorplan", 0) == seeded_guid("floorplan", 0)
        True
        >>> len(seeded_guid("floorplan", 0))
        22
    """
    # Sixteen bytes, not more: a GlobalId is a 128-bit number, which is why its first
    # character can only be 0 to 3. More bits overflow into that character and give an
    # id that does not round-trip through a UUID -- the serializer's product-<uuid> id
    # then names a different number from the ifc:guid beside it.
    digest = hashlib.sha256(f"{seed}:{index}".encode()).digest()
    value = int.from_bytes(digest[:16], "big")
    characters = []
    for _ in range(22):
        value, remainder = divmod(value, 64)
        characters.append(_GUID_ALPHABET[remainder])
    return "".join(reversed(characters))


def reseed_guids(model: Any, seed: str) -> None:  # noqa: ANN401 - ifcopenshell is untyped here
    """Rewrite every GlobalId in a model from a seeded sequence.

    Args:
        model: The ``ifcopenshell.file`` to rewrite in place.
        seed: The model's name, used as the sequence's seed.
    """
    for index, entity in enumerate(sorted(model.by_type("IfcRoot"), key=lambda e: e.id())):
        entity.GlobalId = seeded_guid(seed, index)


#: The timestamp written into every generated model's header. A model carries the
#: moment it was written, which would make two builds of the same model differ and
#: would defeat the whole point of seeding its GlobalIds.
FIXED_TIMESTAMP = "2024-01-01T00:00:00"


def normalise(model: Any) -> None:  # noqa: ANN401 - ifcopenshell is untyped here
    """Put a model's unordered collections into a stable order.

    Several IFC attributes are declared as sets, and the API builds them from Python
    sets, so their order varies between runs and two otherwise identical models differ
    by a handful of bytes. The order carries no meaning -- a set of units is a set, and
    so is the list of products a storey contains -- so sorting them changes nothing
    about the model, and it is what lets every plannotation, drawing and answer derived
    from the model be reproducible too.

    Args:
        model: The ``ifcopenshell.file`` to normalise in place.
    """
    for entity_type, attribute in _UNORDERED_SETS:
        for entity in model.by_type(entity_type):
            members = getattr(entity, attribute, None)
            if members:
                setattr(entity, attribute, tuple(sorted(members, key=lambda item: item.id())))


def write_model(model: Any, out: Path) -> Path:  # noqa: ANN401 - ifcopenshell is untyped here
    """Write a model with a fixed header timestamp.

    ``ifcopenshell`` stamps the current time into ``FILE_NAME``, so two builds of the
    same model a second apart differ by those bytes. The stamp is replaced afterwards
    rather than suppressed, because the header is part of the file a reader sees and
    an empty timestamp would be less honest than a declared one.

    Args:
        model: The ``ifcopenshell.file`` to write.
        out: Where to write it.

    Returns:
        The path written.
    """
    normalise(model)
    model.write(str(out))
    text = out.read_text(encoding="utf-8")
    start = text.find("FILE_NAME(")
    if start != -1:
        head, _, rest = text[start:].partition("'")
        stamp_end = rest.find("'")
        remainder = rest[stamp_end + 1 :]
        second = remainder.find("'")
        third = remainder.find("'", second + 1)
        text = (
            text[:start]
            + head
            + "'"
            + rest[: stamp_end + 1]
            + remainder[: second + 1]
            + FIXED_TIMESTAMP
            + remainder[third:]
        )
        out.write_text(text, encoding="utf-8")
    return out


#: Mark prefixes by IFC class. Doors and windows carry their own series, as on any
#: German drawing; everything structural or enclosing is a numbered position.
MARK_SERIES: tuple[tuple[str, str], ...] = (("IfcDoor", "T"), ("IfcWindow", "W"))


def assign_tags(model: Any, prefix: str = "Pos.") -> None:  # noqa: ANN401 - untyped here
    """Give every building element a mark, stored in the model's own ``Tag``.

    A position number belongs to the model: the schedule and the drawing both refer to
    it, and it is the one thing on a drawing that names an element. Storing it in IFC's
    ``Tag`` attribute is what lets the exporter print the model's mark rather than
    inventing one, and what lets inference match a mark back to the element it names.

    Doors and windows are numbered in their own series, ``T1`` and ``W1``; every other
    building element is a position, ``Pos. 1``.

    Args:
        model: The ``ifcopenshell.file`` to tag in place.
        prefix: The positions' prefix, "Pos." by German convention.
    """
    elements = sorted(model.by_type("IfcBuildingElement"), key=lambda entity: entity.id())
    counters: dict[str, int] = {}
    for element in elements:
        series = next((mark for cls, mark in MARK_SERIES if element.is_a(cls)), None)
        key = series or prefix
        counters[key] = counters.get(key, 0) + 1
        number = counters[key]
        element.Tag = f"{series}{number}" if series else f"{prefix} {number}"


@dataclass(frozen=True)
class Level:
    """A storey a section marks with a level.

    Attributes:
        name: The storey's name.
        elevation: The z of its floor in model coordinates, in metres. A level mark
            prints it relative to the building's ±0,00 (:attr:`BuiltModel.datum`).
        guid: Its GlobalId.
    """

    name: str
    elevation: float
    guid: str


@dataclass(frozen=True)
class SectionCut:
    """A cutting plane placed explicitly: a vertical section, or a plan.

    A plan whose x-axis is not the model's -- one drawn square to a grid that the
    model places at an angle -- is cut through a horizontal plane given here rather
    than through the serializer's storey plans, which only draw along the model axes.

    Attributes:
        location: A point on the cutting plane, in metres. For a section its z is the
            height the plane's y coordinate is measured from, so zero makes plane y an
            elevation; for a plan it is the height of the cut.
        direction: The plane's normal, pointing at the viewer: up for a plan.
        x_axis: The direction that runs to the right on the paper.
    """

    location: tuple[float, float, float]
    direction: tuple[float, float, float]
    x_axis: tuple[float, float, float]


@dataclass(frozen=True)
class BuiltModel:
    """A model that has been built and written, or found, and how to draw it.

    Attributes:
        path: Where it was written.
        seed: The seed its GlobalIds were derived from.
        storey_elevation: The z of the drawn storey's floor in model coordinates, in
            metres: the storey's placement, which is its ``Elevation`` only where the
            building's ±0,00 is at z = 0.
        cut_height: Where a plan of it is cut above that storey, in metres; None when
            it is drawn as a section instead.
        section: The cutting plane, where it is placed explicitly: the vertical cut of
            a section, or the horizontal cut of a plan drawn along axes of its own.
        levels: The storeys a section marks, lowest first.
        storey_name: The drawn storey's name, recorded in the viewport's ``storey``.
        storey_guid: Its GlobalId, recorded beside the name when known.
        datum: The z of the building's ±0,00 in model coordinates, in metres. Level
            marks print their height above it.
        include: The GlobalIds of the products to draw, or None to draw every one.
    """

    path: Path
    seed: str
    storey_elevation: float
    cut_height: float | None
    section: SectionCut | None = None
    levels: tuple[Level, ...] = ()
    storey_name: str | None = None
    storey_guid: str | None = None
    datum: float = 0.0
    include: tuple[str, ...] | None = None

    @property
    def is_plan(self) -> bool:
        """Say whether the drawing is a plan: cut by a horizontal plane.

        Returns:
            True for a storey plan or an explicit plane whose normal is vertical.
        """
        if self.section is None:
            return True
        return abs(self.section.direction[2]) > _VERTICAL


#: How close to 1 a unit normal's z must be for its plane to count as horizontal.
_VERTICAL = 0.999999


def _metric_units(model: Any) -> None:  # noqa: ANN401 - ifcopenshell is untyped here
    """Make the model's units metres, square metres and cubic metres.

    ``assign_unit`` with no arguments gives millimetres, which is a fine choice for a
    model but not the one these samples' numbers are written in. Stating the unit is
    what keeps the plannotation's ``model.lengthUnit`` true.

    Args:
        model: The ``ifcopenshell.file`` whose project needs units.
    """
    api_unit = _require("ifcopenshell.api.unit")
    units = [
        api_unit.add_si_unit(model, unit_type=kind)
        for kind in ("LENGTHUNIT", "AREAUNIT", "VOLUMEUNIT")
    ]
    api_unit.assign_unit(model, units=units)


def _setup(model: Any, name: str) -> tuple[Any, Any]:  # noqa: ANN401 - ifcopenshell is untyped
    """Create the project, units, contexts and spatial tree a model needs.

    Args:
        model: The ``ifcopenshell.file`` to populate.
        name: The project name.

    Returns:
        The body context and the ground-floor storey.
    """
    api_root = _require("ifcopenshell.api.root")
    api_context = _require("ifcopenshell.api.context")
    api_aggregate = _require("ifcopenshell.api.aggregate")

    project = api_root.create_entity(model, ifc_class="IfcProject", name=name)
    _metric_units(model)
    parent = api_context.add_context(model, context_type="Model")
    body = api_context.add_context(
        model,
        context_type="Model",
        context_identifier="Body",
        target_view="MODEL_VIEW",
        parent=parent,
    )
    site = api_root.create_entity(model, ifc_class="IfcSite", name="Grundstück")
    building = api_root.create_entity(model, ifc_class="IfcBuilding", name="Haus A")
    for child, holder in ((site, project), (building, site)):
        api_aggregate.assign_object(model, products=[child], relating_object=holder)
    storey = _storey(model, building, "Erdgeschoss", 0.0)
    return body, storey


def _storey(model: Any, building: Any, name: str, elevation: float) -> Any:  # noqa: ANN401
    """Add a storey at an elevation, stating it both as placement and as attribute.

    Args:
        model: The ``ifcopenshell.file``.
        building: The building it belongs to.
        name: The storey's name.
        elevation: Its elevation in metres.

    Returns:
        The storey.
    """
    api_root = _require("ifcopenshell.api.root")
    api_geometry = _require("ifcopenshell.api.geometry")
    api_aggregate = _require("ifcopenshell.api.aggregate")
    numpy = _require("numpy")
    storey = api_root.create_entity(model, ifc_class="IfcBuildingStorey", name=name)
    storey.Elevation = elevation
    api_aggregate.assign_object(model, products=[storey], relating_object=building)
    matrix = numpy.eye(4)
    matrix[2, 3] = elevation
    api_geometry.edit_object_placement(model, product=storey, matrix=matrix, is_si=True)
    return storey


def _box(
    model: Any,  # noqa: ANN401
    context: Any,  # noqa: ANN401
    storey: Any,  # noqa: ANN401
    ifc_class: str,
    name: str,
    *,
    at: tuple[float, float, float],
    size: tuple[float, float, float],
    rotation: float = 0.0,
) -> Any:  # noqa: ANN401
    """Create a product whose body is an extruded rectangle.

    The rectangle runs ``size[0]`` along the product's x and ``size[1]`` along its y
    from its placement point, and ``size[2]`` up from it. Walls, columns, beams, slabs
    and openings in these samples are all such boxes.

    Args:
        model: The ``ifcopenshell.file``.
        context: The body representation context.
        storey: The storey it is contained in, or None for an opening, which belongs
            to the element it cuts.
        ifc_class: Its IFC class.
        name: Its name.
        at: Its placement point, in metres.
        size: Length, thickness and height, in metres.
        rotation: Its rotation about z, in degrees, anticlockwise.

    Returns:
        The product.
    """
    api_root = _require("ifcopenshell.api.root")
    api_geometry = _require("ifcopenshell.api.geometry")
    api_spatial = _require("ifcopenshell.api.spatial")
    numpy = _require("numpy")
    product = api_root.create_entity(model, ifc_class=ifc_class, name=name)
    shape = api_geometry.add_wall_representation(
        model, context=context, length=size[0], thickness=size[1], height=size[2]
    )
    api_geometry.assign_representation(model, product=product, representation=shape)
    matrix = _placed(numpy, at[0], at[1], rotation)
    matrix[2, 3] = at[2]
    api_geometry.edit_object_placement(model, product=product, matrix=matrix, is_si=True)
    if storey is not None:
        api_spatial.assign_container(model, products=[product], relating_structure=storey)
    return product


def _reference(model: Any, product: Any, pset: str, value: str) -> None:  # noqa: ANN401
    """Record a member's cross-section as ``<pset>.Reference``, as IFC's common psets do.

    Args:
        model: The ``ifcopenshell.file``.
        product: The member.
        pset: The common property set for its class, such as ``Pset_ColumnCommon``.
        value: The cross-section as it prints, such as ``30/30``.
    """
    api_pset = _require("ifcopenshell.api.pset")
    holder = api_pset.add_pset(model, product=product, name=pset)
    api_pset.edit_pset(model, pset=holder, properties={"Reference": value})


def _opening(
    model: Any,  # noqa: ANN401
    context: Any,  # noqa: ANN401
    host: Any,  # noqa: ANN401
    filling: Any,  # noqa: ANN401
    *,
    at: tuple[float, float, float],
    size: tuple[float, float, float],
    rotation: float,
) -> None:
    """Cut an opening in a wall and fill it with a door or window.

    Args:
        model: The ``ifcopenshell.file``.
        context: The body representation context.
        host: The wall the opening cuts.
        filling: The door or window in it.
        at: The opening's placement, in metres.
        size: Its width, depth through the wall, and height.
        rotation: Its rotation about z, in degrees.
    """
    api_feature = _require("ifcopenshell.api.feature")
    opening = _box(
        model, context, None, "IfcOpeningElement", "Öffnung", at=at, size=size, rotation=rotation
    )
    api_feature.add_feature(model, feature=opening, element=host)
    api_feature.add_filling(model, opening=opening, element=filling)


def _filling(  # noqa: PLR0913 - a door or window is its kind, its size and its place
    model: Any,  # noqa: ANN401
    context: Any,  # noqa: ANN401
    storey: Any,  # noqa: ANN401
    ifc_class: str,
    name: str,
    *,
    at: tuple[float, float, float],
    width: float,
    height: float,
    rotation: float,
) -> Any:  # noqa: ANN401
    """Create a door or a window with IfcOpenShell's parametric representation.

    Args:
        model: The ``ifcopenshell.file``.
        context: The body representation context.
        storey: The storey it is contained in.
        ifc_class: ``IfcDoor`` or ``IfcWindow``.
        name: Its name.
        at: Its placement, in metres.
        width: Its overall width.
        height: Its overall height.
        rotation: Its rotation about z, in degrees.

    Returns:
        The door or window.
    """
    api_root = _require("ifcopenshell.api.root")
    api_geometry = _require("ifcopenshell.api.geometry")
    api_spatial = _require("ifcopenshell.api.spatial")
    numpy = _require("numpy")
    product = api_root.create_entity(model, ifc_class=ifc_class, name=name)
    add = (
        api_geometry.add_door_representation
        if ifc_class == "IfcDoor"
        else api_geometry.add_window_representation
    )
    shape = add(model, context=context, overall_width=width, overall_height=height)
    api_geometry.assign_representation(model, product=product, representation=shape)
    product.OverallWidth, product.OverallHeight = width, height
    matrix = _placed(numpy, at[0], at[1], rotation)
    matrix[2, 3] = at[2]
    api_geometry.edit_object_placement(model, product=product, matrix=matrix, is_si=True)
    api_spatial.assign_container(model, products=[product], relating_structure=storey)
    return product


def build_floorplan(out: Path, *, seed: str = "floorplan") -> BuiltModel:
    """Build a one-storey architectural model: five walls, two doors, two windows.

    An 8.0 by 6.0 metre house on the grid A-D / 1-4, in 240 mm external walls, with a
    115 mm internal wall on grid C. The front door and the internal door sit in
    openings cut into their walls, and so do the two windows -- a plan cut at 1.2 m
    passes through all four.

    Args:
        out: Where to write the IFC file.
        seed: The seed for the GlobalId sequence.

    Returns:
        The model, and what a drawing of it needs to know.

    Raises:
        MissingExtraError: If ifcopenshell is not installed.
    """
    ifcopenshell = _require("ifcopenshell")
    model = ifcopenshell.file(schema="IFC4")
    body, storey = _setup(model, "Wohnanlage Lindenhof")

    walls = {
        name: _box(
            model,
            body,
            storey,
            "IfcWall",
            name,
            at=(x, y, 0.0),
            size=(length, thickness, 3.0),
            rotation=rotation,
        )
        for name, length, thickness, (x, y), rotation in (
            ("Außenwand Süd", 8.0, 0.24, (0.0, 0.0), 0.0),
            ("Außenwand Ost", 6.0, 0.24, (8.0, 0.0), 90.0),
            ("Außenwand Nord", 8.0, 0.24, (8.0, 6.0), 180.0),
            ("Außenwand West", 6.0, 0.24, (0.0, 6.0), 270.0),
            ("Innenwand Achse C", 5.52, 0.115, (5.5575, 0.24), 90.0),
        )
    }

    # Front door between grids A and B, internal door in the wall on grid C.
    front = _filling(
        model,
        body,
        storey,
        "IfcDoor",
        "Haustür",
        at=(1.0, 0.0, 0.0),
        width=1.0,
        height=2.1,
        rotation=0.0,
    )
    _opening(
        model,
        body,
        walls["Außenwand Süd"],
        front,
        at=(1.0, -0.01, 0.0),
        size=(1.0, 0.26, 2.1),
        rotation=0.0,
    )
    inner = _filling(
        model,
        body,
        storey,
        "IfcDoor",
        "Innentür",
        at=(5.5575, 2.5, 0.0),
        width=0.9,
        height=2.1,
        rotation=90.0,
    )
    _opening(
        model,
        body,
        walls["Innenwand Achse C"],
        inner,
        at=(5.5675, 2.5, 0.0),
        size=(0.9, 0.135, 2.1),
        rotation=90.0,
    )

    # A window in the north wall and one in the east wall, sills at 0.9 m.
    north = _filling(
        model,
        body,
        storey,
        "IfcWindow",
        "Fenster Nord",
        at=(3.0, 6.0, 0.9),
        width=1.5,
        height=1.2,
        rotation=180.0,
    )
    _opening(
        model,
        body,
        walls["Außenwand Nord"],
        north,
        at=(3.0, 6.01, 0.9),
        size=(1.5, 0.26, 1.2),
        rotation=180.0,
    )
    east = _filling(
        model,
        body,
        storey,
        "IfcWindow",
        "Fenster Ost",
        at=(8.0, 2.5, 0.9),
        width=1.2,
        height=1.2,
        rotation=90.0,
    )
    _opening(
        model,
        body,
        walls["Außenwand Ost"],
        east,
        at=(8.01, 2.5, 0.9),
        size=(1.2, 0.26, 1.2),
        rotation=90.0,
    )

    assign_tags(model)
    reseed_guids(model, seed)
    write_model(model, out)
    return BuiltModel(
        path=out, seed=seed, storey_elevation=0.0, cut_height=1.2, storey_name=str(storey.Name)
    )


def _placed(numpy: Any, x: float, y: float, degrees: float) -> Any:  # noqa: ANN401
    """Return a placement matrix for a product at a position and rotation.

    Args:
        numpy: The numpy module.
        x: Model x of the placement.
        y: Model y.
        degrees: Rotation about z, anticlockwise.

    Returns:
        A 4x4 placement matrix in SI units.
    """
    radians = numpy.radians(degrees)
    cos, sin = float(numpy.cos(radians)), float(numpy.sin(radians))
    matrix = numpy.eye(4)
    matrix[0, 0], matrix[0, 1] = cos, -sin
    matrix[1, 0], matrix[1, 1] = sin, cos
    matrix[0, 3], matrix[1, 3] = x, y
    return matrix


#: The position plan's grid, in metres: three bays by two.
POSITIONSPLAN_X: tuple[float, ...] = (0.0, 5.7, 11.4)
POSITIONSPLAN_Y: tuple[float, ...] = (0.0, 3.6, 7.2)


def build_positionsplan(out: Path, *, seed: str = "positionsplan") -> BuiltModel:
    """Build a foundation position plan: columns, ground beams and a base slab.

    A *Positionsplan* numbers every structural member so that the schedule and the
    drawing can refer to the same thing, and states each member's cross-section beside
    its number. Here that is nine 30/30 columns on the grid A-C / 1-3, six 30/75
    ground beams along the grid lines 1-3 between them, and a 25 cm base slab under
    all of it, which they stand in. Each cross-section is stored as ``Pset_*Common.Reference``.

    Args:
        out: Where to write the IFC file.
        seed: The seed for the GlobalId sequence.

    Returns:
        The model, and what a drawing of it needs to know.

    Raises:
        MissingExtraError: If ifcopenshell is not installed.
    """
    ifcopenshell = _require("ifcopenshell")
    model = ifcopenshell.file(schema="IFC4")
    body, storey = _setup(model, "Wohnanlage Lindenhof")

    number = 0
    for y in POSITIONSPLAN_Y:
        for x in POSITIONSPLAN_X:
            number += 1
            column = _box(
                model,
                body,
                storey,
                "IfcColumn",
                f"Stütze St-{number:02d}",
                at=(x - 0.15, y - 0.15, 0.0),
                size=(0.3, 0.3, 3.0),
            )
            _reference(model, column, "Pset_ColumnCommon", "30/30")
    number = 0
    for y in POSITIONSPLAN_Y:
        for start, end in pairwise(POSITIONSPLAN_X):
            number += 1
            beam = _box(
                model,
                body,
                storey,
                "IfcBeam",
                f"Fundamentbalken FB-{number}",
                at=(start + 0.15, y - 0.15, 0.0),
                size=(end - start - 0.3, 0.3, 0.75),
            )
            _reference(model, beam, "Pset_BeamCommon", "30/75")
    slab = _box(
        model,
        body,
        storey,
        "IfcSlab",
        "Bodenplatte",
        at=(-0.15, -0.15, 0.0),
        size=(POSITIONSPLAN_X[-1] + 0.3, POSITIONSPLAN_Y[-1] + 0.3, 0.25),
    )
    _reference(model, slab, "Pset_SlabCommon", "d = 25 cm")

    assign_tags(model)
    reseed_guids(model, seed)
    write_model(model, out)
    # Cut low, at 500 mm. The beams here are ground beams 750 mm deep, so a plan cut
    # at the usual 1.2 m would pass above them and number only the columns; the 250 mm
    # slab lies below the cut and is drawn beyond it.
    return BuiltModel(
        path=out, seed=seed, storey_elevation=0.0, cut_height=0.5, storey_name=str(storey.Name)
    )


#: The section model's storeys: name and elevation in metres. The last is the roof.
SECTION_STOREYS: tuple[tuple[str, float], ...] = (
    ("Erdgeschoss", 0.0),
    ("1. Obergeschoss", 3.0),
    ("Dach", 6.0),
)

#: Where the section is cut: across the building at x = 3.6 m, grid 1 on the left.
SECTION_CUT = SectionCut(
    location=(3.6, 0.0, 0.0), direction=(1.0, 0.0, 0.0), x_axis=(0.0, 1.0, 0.0)
)


def build_section(out: Path, *, seed: str = "section") -> BuiltModel:
    """Build a two-storey house for a section: walls on two floors, three slabs.

    A 7.2 by 6.0 metre house with 365 mm external walls on both storeys, a base slab,
    a floor slab over the ground floor and a roof slab. The storeys stand at 0.0, 3.0
    and -- for the roof -- 6.0 metres, stated both as placements and as each storey's
    ``Elevation``, so that the levels a section marks have one source.

    Args:
        out: Where to write the IFC file.
        seed: The seed for the GlobalId sequence.

    Returns:
        The model, and what a drawing of it needs to know.

    Raises:
        MissingExtraError: If ifcopenshell is not installed.
    """
    ifcopenshell = _require("ifcopenshell")
    model = ifcopenshell.file(schema="IFC4")
    body, ground = _setup(model, "Wohnanlage Lindenhof")
    building = model.by_type("IfcBuilding")[0]
    storeys = [ground] + [
        _storey(model, building, name, elevation) for name, elevation in SECTION_STOREYS[1:]
    ]

    for storey, (name, elevation) in zip(storeys[:2], SECTION_STOREYS[:2], strict=True):
        for side, (x, y), rotation in (("Süd", (0.0, 0.0), 0.0), ("Nord", (7.2, 6.0), 180.0)):
            _box(
                model,
                body,
                storey,
                "IfcWall",
                f"Außenwand {side} {name}",
                at=(x, y, elevation),
                size=(7.2, 0.365, 2.8),
                rotation=rotation,
            )
    for storey, name, top in (
        (storeys[0], "Bodenplatte", 0.0),
        (storeys[1], "Decke über EG", 3.0),
        (storeys[2], "Dachdecke", 6.0),
    ):
        _box(model, body, storey, "IfcSlab", name, at=(0.0, 0.0, top - 0.2), size=(7.2, 6.0, 0.2))

    assign_tags(model)
    reseed_guids(model, seed)
    write_model(model, out)
    levels = tuple(
        Level(name=str(storey.Name), elevation=float(storey.Elevation), guid=str(storey.GlobalId))
        for storey in storeys
    )
    return BuiltModel(
        path=out,
        seed=seed,
        storey_elevation=0.0,
        cut_height=None,
        section=SECTION_CUT,
        levels=levels,
    )
