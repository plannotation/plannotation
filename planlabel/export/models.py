# SPDX-License-Identifier: Apache-2.0
"""Build the small IFC models the sample drawings are made from.

These exist so that the samples are reproducible from source rather than committed as
binaries, and so that every question the benchmark asks has an answer that came from a
model rather than from someone reading a drawing.

**GlobalIds are assigned, not generated.** ``ifcopenshell.api`` mints a fresh
GlobalId for every entity it creates, so an unseeded model differs on every run and
nothing downstream -- the SVG, the labels, the ground truth -- can be byte-reproducible.
Each builder therefore walks its finished model and rewrites every GlobalId from a
seeded sequence, which is the only point in the chain where that can be done once and
for all.

ifcopenshell lives behind the ``ifc`` extra and is not installed in CI, so it is
imported inside the functions that need it.
"""

from __future__ import annotations

import hashlib
import importlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from planlabel.errors import MissingExtraError

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
            "`pip install 'planlabel[ifc]'` or `uv sync --extra ifc`"
        )
        raise MissingExtraError(msg) from exc


def seeded_guid(seed: str, index: int) -> str:
    """Return a deterministic, well-formed IFC GlobalId.

    A GlobalId is 22 characters of IFC's own base64 alphabet. The value here is derived
    from a seed and an index rather than from randomness or a clock, so that a model
    rebuilt tomorrow carries the same identities as the one built today and the labels
    that name them stay true.

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
    digest = hashlib.sha256(f"{seed}:{index}".encode()).digest()
    value = int.from_bytes(digest[:17], "big")
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
    about the model, and it is what lets every label, drawing and answer derived from
    the model be reproducible too.

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


def assign_tags(model: Any, prefix: str = "Pos.") -> None:  # noqa: ANN401 - untyped here
    """Give every building element a mark, stored in the model's own ``Tag``.

    A position number belongs to the model: the schedule and the drawing both refer to
    it, and it is the one thing on a drawing that names an element. Storing it in IFC's
    ``Tag`` attribute is what lets the exporter print the model's mark rather than
    inventing one, and what lets inference match a mark back to the element it names.

    Args:
        model: The ``ifcopenshell.file`` to tag in place.
        prefix: The mark's prefix, "Pos." by German convention.
    """
    elements = sorted(model.by_type("IfcBuildingElement"), key=lambda entity: entity.id())
    for number, element in enumerate(elements, start=1):
        element.Tag = f"{prefix} {number}"


@dataclass(frozen=True)
class BuiltModel:
    """A model that has been built and written.

    Attributes:
        path: Where it was written.
        seed: The seed its GlobalIds were derived from.
        storey_elevation: The storey's elevation in model units.
        cut_height: Where a plan of it should be cut, above the storey.
        length_unit: The model's length unit, as a PlanLabel ``lengthUnit``.
    """

    path: Path
    seed: str
    storey_elevation: float
    cut_height: float
    length_unit: str


def build_floorplan(out: Path, *, seed: str = "floorplan") -> BuiltModel:
    """Build a one-storey architectural model: four walls, a door and a window.

    Small on purpose. It has to be large enough that a plan of it says something and
    small enough that building, drawing and validating it stays inside a test.

    Args:
        out: Where to write the IFC file.
        seed: The seed for the GlobalId sequence.

    Returns:
        The model, and what a drawing of it needs to know.

    Raises:
        MissingExtraError: If ifcopenshell is not installed.
    """
    ifcopenshell = _require("ifcopenshell")
    api_root = _require("ifcopenshell.api.root")
    api_unit = _require("ifcopenshell.api.unit")
    api_context = _require("ifcopenshell.api.context")
    api_geometry = _require("ifcopenshell.api.geometry")
    api_spatial = _require("ifcopenshell.api.spatial")
    api_aggregate = _require("ifcopenshell.api.aggregate")
    numpy = _require("numpy")

    model = ifcopenshell.file(schema="IFC4")
    project = api_root.create_entity(model, ifc_class="IfcProject", name="Wohnanlage Lindenhof")
    api_unit.assign_unit(model)
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
    storey = api_root.create_entity(model, ifc_class="IfcBuildingStorey", name="Erdgeschoss")
    for child, holder in ((site, project), (building, site), (storey, building)):
        api_aggregate.assign_object(model, products=[child], relating_object=holder)
    api_geometry.edit_object_placement(model, product=storey, matrix=numpy.eye(4), is_si=True)

    # A closed room, 8.0 by 6.0 metres to the outside face, in 240 mm walls.
    walls = (
        ("Außenwand Süd", 8.0, (0.0, 0.0), 0.0),
        ("Außenwand Ost", 6.0, (8.0, 0.0), 90.0),
        ("Außenwand Nord", 8.0, (8.0, 6.0), 180.0),
        ("Außenwand West", 6.0, (0.0, 6.0), 270.0),
    )
    for name, length, (x, y), rotation in walls:
        wall = api_root.create_entity(model, ifc_class="IfcWall", name=name)
        shape = api_geometry.add_wall_representation(
            model, context=body, length=length, height=3.0, thickness=0.24
        )
        api_geometry.assign_representation(model, product=wall, representation=shape)
        api_geometry.edit_object_placement(
            model, product=wall, matrix=_placed(numpy, x, y, rotation), is_si=True
        )
        api_spatial.assign_container(model, products=[wall], relating_structure=storey)

    assign_tags(model)
    reseed_guids(model, seed)
    write_model(model, out)
    return BuiltModel(path=out, seed=seed, storey_elevation=0.0, cut_height=1.2, length_unit="m")


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


def build_positionsplan(out: Path, *, seed: str = "positionsplan") -> BuiltModel:
    """Build a structural position plan: a grid of columns carrying beams.

    A *Positionsplan* numbers every structural member so that the schedule and the
    drawing can refer to the same thing. That is what makes it the interesting sample
    for tags: every element has a mark, and the mark is a property of the model rather
    than a caption on a picture.

    Args:
        out: Where to write the IFC file.
        seed: The seed for the GlobalId sequence.

    Returns:
        The model, and what a drawing of it needs to know.

    Raises:
        MissingExtraError: If ifcopenshell is not installed.
    """
    return _structure(out, seed)


def build_section(out: Path, *, seed: str = "section") -> BuiltModel:
    """Build a two-storey model, for a drawing that carries levels.

    Args:
        out: Where to write the IFC file.
        seed: The seed for the GlobalId sequence.

    Returns:
        The model, and what a drawing of it needs to know.

    Raises:
        MissingExtraError: If ifcopenshell is not installed.
    """
    return _two_storeys(out, seed)


def _setup(model: Any, name: str) -> tuple[Any, Any]:  # noqa: ANN401 - ifcopenshell is untyped
    """Create the project, units, contexts and spatial tree a model needs.

    Args:
        model: The ``ifcopenshell.file`` to populate.
        name: The project name.

    Returns:
        The body context and the ground-floor storey.
    """
    api_root = _require("ifcopenshell.api.root")
    api_unit = _require("ifcopenshell.api.unit")
    api_context = _require("ifcopenshell.api.context")
    api_geometry = _require("ifcopenshell.api.geometry")
    api_aggregate = _require("ifcopenshell.api.aggregate")
    numpy = _require("numpy")

    project = api_root.create_entity(model, ifc_class="IfcProject", name=name)
    api_unit.assign_unit(model)
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
    storey = api_root.create_entity(model, ifc_class="IfcBuildingStorey", name="Erdgeschoss")
    for child, holder in ((site, project), (building, site), (storey, building)):
        api_aggregate.assign_object(model, products=[child], relating_object=holder)
    api_geometry.edit_object_placement(model, product=storey, matrix=numpy.eye(4), is_si=True)
    return body, storey


def _structure(out: Path, seed: str) -> BuiltModel:
    """Build the position-plan model.

    Args:
        out: Where to write the IFC file.
        seed: The GlobalId seed.

    Returns:
        The built model.
    """
    ifcopenshell = _require("ifcopenshell")
    api_root = _require("ifcopenshell.api.root")
    api_geometry = _require("ifcopenshell.api.geometry")
    api_spatial = _require("ifcopenshell.api.spatial")
    numpy = _require("numpy")

    model = ifcopenshell.file(schema="IFC4")
    body, storey = _setup(model, "Wohnanlage Lindenhof")

    # Four columns on a 5.70 by 4.80 metre grid, with beams spanning between them.
    for index, (x, y) in enumerate(((0.0, 0.0), (5.7, 0.0), (0.0, 4.8), (5.7, 4.8))):
        column = api_root.create_entity(
            model, ifc_class="IfcColumn", name=f"Stütze St-{index + 1:02d}"
        )
        shape = api_geometry.add_wall_representation(
            model, context=body, length=0.3, height=3.0, thickness=0.3
        )
        api_geometry.assign_representation(model, product=column, representation=shape)
        api_geometry.edit_object_placement(
            model, product=column, matrix=_placed(numpy, x, y, 0.0), is_si=True
        )
        api_spatial.assign_container(model, products=[column], relating_structure=storey)

    for index, (x, y, length, rotation) in enumerate(((0.0, 0.0, 5.7, 0.0), (0.0, 4.8, 5.7, 0.0))):
        beam = api_root.create_entity(model, ifc_class="IfcBeam", name=f"Unterzug UZ-{index + 1}")
        shape = api_geometry.add_wall_representation(
            model, context=body, length=length, height=0.5, thickness=0.3
        )
        api_geometry.assign_representation(model, product=beam, representation=shape)
        api_geometry.edit_object_placement(
            model, product=beam, matrix=_placed(numpy, x, y, rotation), is_si=True
        )
        api_spatial.assign_container(model, products=[beam], relating_structure=storey)

    assign_tags(model)
    reseed_guids(model, seed)
    write_model(model, out)
    # Cut low, at 400 mm. The beams here are ground beams spanning between the column
    # bases, so a plan cut at the usual 1.2 m would pass above them and the drawing
    # would show four columns with nothing to carry -- a Positionsplan that numbers
    # half of its members.
    return BuiltModel(path=out, seed=seed, storey_elevation=0.0, cut_height=0.4, length_unit="m")


def _two_storeys(out: Path, seed: str) -> BuiltModel:
    """Build the two-storey model a section is drawn from.

    Args:
        out: Where to write the IFC file.
        seed: The GlobalId seed.

    Returns:
        The built model.
    """
    ifcopenshell = _require("ifcopenshell")
    api_root = _require("ifcopenshell.api.root")
    api_geometry = _require("ifcopenshell.api.geometry")
    api_spatial = _require("ifcopenshell.api.spatial")
    api_aggregate = _require("ifcopenshell.api.aggregate")
    numpy = _require("numpy")

    model = ifcopenshell.file(schema="IFC4")
    body, ground = _setup(model, "Wohnanlage Lindenhof")
    building = model.by_type("IfcBuilding")[0]
    upper = api_root.create_entity(model, ifc_class="IfcBuildingStorey", name="1. Obergeschoss")
    api_aggregate.assign_object(model, products=[upper], relating_object=building)
    upper_matrix = numpy.eye(4)
    upper_matrix[2, 3] = 3.35
    api_geometry.edit_object_placement(model, product=upper, matrix=upper_matrix, is_si=True)

    for storey, elevation in ((ground, 0.0), (upper, 3.35)):
        for index, (name, length, position, rotation) in enumerate(
            (
                ("Außenwand Süd", 7.2, (0.0, 0.0), 0.0),
                ("Außenwand Nord", 7.2, (7.2, 5.4), 180.0),
            )
        ):
            wall = api_root.create_entity(model, ifc_class="IfcWall", name=f"{name} {storey.Name}")
            shape = api_geometry.add_wall_representation(
                model, context=body, length=length, height=3.0, thickness=0.365
            )
            api_geometry.assign_representation(model, product=wall, representation=shape)
            matrix = _placed(numpy, position[0], position[1], rotation)
            matrix[2, 3] = elevation
            api_geometry.edit_object_placement(model, product=wall, matrix=matrix, is_si=True)
            api_spatial.assign_container(model, products=[wall], relating_structure=storey)
            del index

    assign_tags(model)
    reseed_guids(model, seed)
    write_model(model, out)
    return BuiltModel(path=out, seed=seed, storey_elevation=0.0, cut_height=1.2, length_unit="m")
