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
