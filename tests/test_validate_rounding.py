# SPDX-License-Identifier: Apache-2.0
"""The geometric rules against the three-decimal rounding of SPEC 3.8.

Every number in a plannotation is rounded to three decimals, and four rules test
numbers for an exact relation: unit plane axes, orthogonal plane axes, ``scale``
against ``paperToPlane``, and the plane origin against ``storey.elevation +
cutHeight``. Held to floating-point slack, those rules rejected every view not aligned
with the model's axes, because an oblique unit vector has no exact three-decimal
spelling. These tests hold the validator to the other half of the bargain as well: the
tolerance admits everything rounding can explain, and a genuine error is still one.

Every document here goes through :func:`canonical_json` before it is validated, so the
numbers checked are the ones a writer would actually emit.
"""

from __future__ import annotations

import json
import math
from typing import TYPE_CHECKING, Any

import numpy as np
import pytest

from plannotation.model import canonical_json, load_plannotation
from plannotation.validate import check_plannotation
from plannotation.validate.geometric import (
    AXIS_TOLERANCE,
    CUT_HEIGHT_TOLERANCE,
    ORTHOGONALITY_TOLERANCE,
    ROUNDING,
)
from plannotation.validate.geometry import norm

if TYPE_CHECKING:
    from plannotation.model import Plannotation

#: The angle between the Maleva 18 building's grid and its model's axes, in degrees, as
#: the model places its IfcGrid: cos and sin round to 0.873 and 0.487, where 29.18 would
#: give 0.488.
MALEVA_ANGLE = 29.17


def serialised(viewport: dict[str, Any], *, unit: str = "m") -> Plannotation:
    """Build a one-viewport plannotation and put it through the canonical form.

    Args:
        viewport: The viewport's members other than ``id``, ``kind`` and
            ``paperBBox``, at whatever precision the caller computed them.
        unit: ``model.lengthUnit``.

    Returns:
        The plannotation as a reader would load it from the canonical text, so every
        number in it is rounded to three decimals.
    """
    document = {
        "plannotation": "0.1",
        "provenance": "authored",
        "page": {"index": 0, "widthMm": 841, "heightMm": 594},
        "sheet": {"id": "A-101"},
        "model": {"lengthUnit": unit},
        "viewports": [
            {"id": "vp", "kind": "plan", "paperBBox": [20, 20, 800, 560], **viewport},
        ],
    }
    return load_plannotation(canonical_json(load_plannotation(json.dumps(document))))


def codes(plannotation: Plannotation) -> list[str]:
    """Validate a plannotation and return the codes it reports.

    Args:
        plannotation: The plannotation.

    Returns:
        Every finding's code, in the order reported.
    """
    return [f.code for f in check_plannotation(plannotation)]


def turned(degrees: float) -> tuple[list[float], list[float]]:
    """Return the axes of a plan turned about the model's z axis.

    Args:
        degrees: The angle from the model's x axis to the plan's, anticlockwise.

    Returns:
        ``xAxis`` and ``yAxis``, unit and orthogonal at full precision.
    """
    angle = math.radians(degrees)
    return (
        [math.cos(angle), math.sin(angle), 0.0],
        [-math.sin(angle), math.cos(angle), 0.0],
    )


class TestTheTolerancesComeFromTheRounding:
    """No tolerance here is a judgement: each bounds what rounding can do."""

    def test_rounding_moves_a_number_by_half_the_last_decimal(self) -> None:
        """Three decimals, so 0.0005."""
        assert pytest.approx(0.0005) == ROUNDING

    def test_the_axis_tolerance_is_the_half_diagonal_of_the_rounding_cell(self) -> None:
        """Three components, each moved by up to 0.0005."""
        assert pytest.approx(math.sqrt(3) * 0.0005) == AXIS_TOLERANCE

    def test_an_axis_along_a_model_axis_gains_nothing(self) -> None:
        """The nearest three-decimal lengths either side of 1 are outside the tolerance."""
        assert AXIS_TOLERANCE < 0.001

    def test_a_plan_disagreeing_by_one_thousandth_is_within_the_cut_height_rounding(
        self,
    ) -> None:
        """On a plan the three numbers are multiples of 0.001; 0.001 passes, 0.002 not."""
        assert 0.001 < CUT_HEIGHT_TOLERANCE < 0.002


class TestAnObliqueViewValidates:
    """A view that follows the building rather than the model's axes is ordinary."""

    def test_a_plan_turned_to_its_grid_validates(self) -> None:
        """The Maleva 18 case: a plan turned 29.17 degrees to follow the building."""
        x_axis, y_axis = turned(-MALEVA_ANGLE)
        plannotation = serialised(
            {
                "plane": {"origin": [0, 0, 4.7], "xAxis": x_axis, "yAxis": y_axis},
                "paperToPlane": [0.1, 0, 0, 0.1, -5, -9],
                "scale": 100,
                "cutHeight": 1.2,
                "storey": {"elevation": 3.5, "name": "1. OG"},
            }
        )
        plane = plannotation.viewports[0].plane if plannotation.viewports else None
        assert plane is not None
        # It is the axis the Maleva plan writes, and the rounding is really there: the
        # serialised axis is not unit to 1e-6.
        assert tuple(plane.x_axis) == pytest.approx((0.873, -0.487, 0))
        assert abs(norm(plane.x_axis) - 1.0) > 1e-4
        assert codes(plannotation) == []

    def test_the_maleva_18_plan_viewport_validates(self) -> None:
        """The viewport of the Maleva 18 ground-floor plan, as the trial wrote it.

        Millimetres, placed in Estonian L-EST97 coordinates, and turned to the grid.
        Its axes are 0.99965 long, and the validator rejected them at 1e-6.
        """
        plannotation = serialised(
            {
                "cutHeight": 1200,
                "paperToPlane": [100, 0, 0, 100, -10900, -25309.799],
                "plane": {
                    "origin": [538451170, 6591615320, 15500],
                    "xAxis": [0.873, -0.487, 0],
                    "yAxis": [0.487, 0.873, 0],
                },
                "scale": 100,
                "storey": {"elevation": 14300, "name": "1.korrus"},
            },
            unit="mm",
        )
        assert codes(plannotation) == []

    def test_the_maleva_18_section_viewport_validates(self) -> None:
        """Its section: a vertical plane along the grid, so only one axis is oblique."""
        plannotation = serialised(
            {
                "paperToPlane": [100, 0, 0, 100, -23085.158, -16515.856],
                "plane": {
                    "origin": [538462433.988, 6591609032.507, 14300],
                    "xAxis": [0.487, 0.873, 0],
                    "yAxis": [0, 0, 1],
                },
                "scale": 100,
            },
            unit="mm",
        )
        assert codes(plannotation) == []

    def test_every_plan_angle_validates(self) -> None:
        """A plan turned to any angle, in steps of 0.07 degrees, round the full circle."""
        rejected = []
        for hundredths in range(0, 36000, 7):
            x_axis, y_axis = turned(hundredths / 100)
            plannotation = serialised(
                {"plane": {"origin": [0, 0, 0], "xAxis": x_axis, "yAxis": y_axis}}
            )
            if codes(plannotation):
                rejected.append(hundredths / 100)
        assert rejected == []

    def test_every_oblique_view_in_three_dimensions_validates(self) -> None:
        """Random orthonormal pairs in space, which neither axis lines up with."""
        generator = np.random.default_rng(20260923)
        rejected = []
        for _ in range(500):
            basis, _ = np.linalg.qr(generator.normal(size=(3, 3)))
            x_axis, y_axis = basis[:, 0].tolist(), basis[:, 1].tolist()
            plannotation = serialised(
                {"plane": {"origin": [0, 0, 0], "xAxis": x_axis, "yAxis": y_axis}}
            )
            if codes(plannotation):
                rejected.append((x_axis, y_axis))
        assert rejected == []


class TestAWrongAxisIsStillWrong:
    """The tolerance admits rounding, and an axis that rounding cannot explain fails."""

    @pytest.mark.parametrize(
        "axis",
        [
            pytest.param([1.001, 0, 0], id="one-thousandth-long"),
            pytest.param([0.999, 0, 0], id="one-thousandth-short"),
            pytest.param([0.874, -0.488, 0], id="oblique-and-long"),
            pytest.param([0.872, -0.486, 0], id="oblique-and-short"),
            pytest.param([2, 0, 0], id="carrying-the-scale"),
        ],
    )
    def test_a_non_unit_axis_is_reported(self, axis: list[float]) -> None:
        """Lengths 1.001, 0.999, 1.00101, 0.99830 and 2 are none of them rounding."""
        assert abs(norm(axis) - 1.0) > AXIS_TOLERANCE
        plannotation = serialised(
            {"plane": {"origin": [0, 0, 0], "xAxis": axis, "yAxis": [0, 0, 1]}}
        )
        assert codes(plannotation) == ["PL-GEO-010"]

    def test_a_sheared_pair_is_reported(self) -> None:
        """The y axis turned 0.2 degrees past square: a dot product of 0.0035."""
        x_axis, _ = turned(MALEVA_ANGLE)
        _, y_axis = turned(MALEVA_ANGLE + 0.2)
        plannotation = serialised(
            {"plane": {"origin": [0, 0, 0], "xAxis": x_axis, "yAxis": y_axis}}
        )
        assert codes(plannotation) == ["PL-GEO-011"]
        assert math.sin(math.radians(0.2)) > ORTHOGONALITY_TOLERANCE


class TestScaleAllowsForRounding:
    """PL-GEO-008 against a transform whose coefficients carry a sine and a cosine."""

    @staticmethod
    def _rotated(scale: float, degrees: float, *, declared: float | None = None) -> Plannotation:
        """Build a viewport in metres whose transform is turned on the paper.

        Args:
            scale: The scale denominator the transform is computed at.
            degrees: How far the transform turns the view.
            declared: The ``scale`` the viewport declares; ``scale`` when None.

        Returns:
            The serialised plannotation.
        """
        k = scale / 1000.0
        angle = math.radians(degrees)
        cos, sin = k * math.cos(angle), k * math.sin(angle)
        return serialised(
            {
                "paperToPlane": [cos, sin, -sin, cos, 0, 0],
                "plane": {"origin": [0, 0, 0], "xAxis": [1, 0, 0], "yAxis": [0, 1, 0]},
                "scale": scale if declared is None else declared,
            }
        )

    def test_a_rotated_transform_at_its_own_scale_validates(self) -> None:
        """At 1:100 in metres and 29.17 degrees the rounded transform is at 1:99.85."""
        assert codes(self._rotated(100, MALEVA_ANGLE)) == []

    @pytest.mark.parametrize("scale", [20, 50, 100, 200, 500])
    def test_every_angle_validates_at_the_common_scales(self, scale: int) -> None:
        """Round the full circle in whole degrees, at each scale."""
        rejected = [degrees for degrees in range(360) if codes(self._rotated(scale, degrees)) != []]
        assert rejected == []

    def test_a_rotated_transform_that_contradicts_its_scale_is_reported(self) -> None:
        """Drawn at 1:100 and declared 1:50: rounding explains a unit, not fifty."""
        assert codes(self._rotated(100, MALEVA_ANGLE, declared=50)) == ["PL-GEO-008"]

    def test_an_unrotated_transform_is_held_as_closely_as_before_where_it_matters(
        self,
    ) -> None:
        """At 1:50 in metres, a declared 1:52 is two units out; rounding explains one."""
        assert codes(self._rotated(50, 0, declared=52)) == ["PL-GEO-008"]

    def test_rounding_that_breaks_the_similarity_does_not_hide_the_scale(self) -> None:
        """The transform's c is -0.048 where -b is -0.049, and that is still rounding.

        At a relative 1e-9 this transform was no similarity at all, so its scale was
        never compared and a 1:100 transform could declare 1:50 unchallenged.
        """
        plannotation = serialised(
            {
                "paperToPlane": [0.087, 0.049, -0.048, 0.087, 0, 0],
                "plane": {"origin": [0, 0, 0], "xAxis": [1, 0, 0], "yAxis": [0, 1, 0]},
                "scale": 50,
            }
        )
        assert codes(plannotation) == ["PL-GEO-008"]

    def test_a_transform_that_is_no_similarity_is_still_not_compared(self) -> None:
        """1:50 across and 1:100 up: SPEC 3.5 forbids comparing a scale with it."""
        plannotation = serialised(
            {
                "paperToPlane": [0.05, 0, 0, 0.1, 0, 0],
                "plane": {"origin": [0, 0, 0], "xAxis": [1, 0, 0], "yAxis": [0, 1, 0]},
                "scale": 20,
            }
        )
        assert codes(plannotation) == []


class TestCutHeightAllowsForRounding:
    """PL-GEO-012 compares three numbers, each rounded on its own."""

    @staticmethod
    def _plan(elevation: float, cut_height: float, origin_z: float) -> Plannotation:
        """Build a plan viewport from unrounded values.

        Args:
            elevation: ``storey.elevation``, in metres.
            cut_height: ``cutHeight``, in metres.
            origin_z: The plane origin's z, in metres.

        Returns:
            The serialised plannotation.
        """
        return serialised(
            {
                "cutHeight": cut_height,
                "plane": {"origin": [0, 0, origin_z], "xAxis": [1, 0, 0], "yAxis": [0, 1, 0]},
                "storey": {"elevation": elevation, "name": "EG"},
            }
        )

    def test_three_separate_roundings_are_not_a_misplaced_plane(self) -> None:
        """3.0004 + 1.2004 = 4.2008 is written 3 + 1.2 and 4.201, which disagree by 0.001."""
        assert codes(self._plan(3.0004, 1.2004, 3.0004 + 1.2004)) == []

    def test_a_plane_two_thousandths_out_is_reported(self) -> None:
        """No rounding of three numbers separates them by 0.002."""
        assert codes(self._plan(3.0, 1.2, 4.202)) == ["PL-GEO-012"]
