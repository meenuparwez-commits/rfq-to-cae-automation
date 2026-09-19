"""Tests for src/cad_generator.py.

Units: mm.

The baseline bracket is H 100, L 80, b 60, t 4, r 5. Its volume is worked out
by hand in test_geometry_checks.py; here the concern is that the solid is
built correctly and that impossible dimensions are refused rather than turned
into a broken solid.
"""

from __future__ import annotations

import math

import cadquery as cq
import pytest

from src import cad_generator

BASELINE = dict(
    plate_height=100.0,
    arm_length=80.0,
    width=60.0,
    thickness=4.0,
    fillet_radius=5.0,
)


@pytest.fixture(scope="module")
def bracket() -> cq.Workplane:
    """The baseline bracket, built once for the whole module."""
    return cad_generator.build_bracket(**BASELINE)


def test_build_returns_one_valid_solid(bracket):
    """A bracket that is invalid or in two pieces would mesh into nonsense."""
    shape = bracket.val()
    assert shape.isValid(), "CAD kernel reports an invalid solid."
    assert len(shape.Solids()) == 1, "Plate and arm did not fuse into one solid."


def test_coordinate_convention(bracket):
    """Rear face at x = 0, symmetric about y = 0, underside at z = 0.

    The fixed face is found by coordinate, so this convention is part of
    the contract between modules, not an incidental detail.
    """
    box = bracket.val().BoundingBox()

    assert box.xmin == pytest.approx(0.0, abs=1e-6), "Rear face is not at x = 0."
    assert box.zmin == pytest.approx(0.0, abs=1e-6), "Underside is not at z = 0."
    assert box.ymin == pytest.approx(-BASELINE["width"] / 2, abs=1e-6)
    assert box.ymax == pytest.approx(BASELINE["width"] / 2, abs=1e-6)
    assert box.zmax == pytest.approx(BASELINE["plate_height"], abs=1e-6)


def test_arm_free_length_is_measured_from_the_plate_front_face(bracket):
    """Total x extent must be thickness + arm_length, not arm_length.

    L is defined as the free length from the plate's front face. If the
    arm were built L long overall, every analytical result would be
    wrong by one thickness.
    """
    box = bracket.val().BoundingBox()
    expected = BASELINE["thickness"] + BASELINE["arm_length"]

    assert box.xmax == pytest.approx(expected, abs=1e-6)


def test_fillet_adds_material_in_the_concave_corner():
    """A fillet at an inside corner fills material in; it does not remove it.

    The added area is r^2 - pi*r^2/4, so the volume difference is a closed-form
    number rather than a vague 'it got bigger'.
    """
    sharp = cad_generator.build_bracket(**{**BASELINE, "fillet_radius": 0.0})
    filleted = cad_generator.build_bracket(**BASELINE)

    added = filleted.val().Volume() - sharp.val().Volume()
    expected_added = (
        BASELINE["fillet_radius"] ** 2 * (1.0 - math.pi / 4.0) * BASELINE["width"]
    )

    assert added > 0.0, "Fillet removed material; the wrong edge was selected."
    # 1e-6 relative on a 321.9 mm^3 difference is ~3e-4 mm^3: far tighter than
    # any real modelling error, but with margin against kernel round-off
    # changing in a future CadQuery release.
    assert added == pytest.approx(expected_added, rel=1e-6)


def test_sharp_corner_volume_is_the_two_rectangles():
    """With no fillet the volume is just plate + arm."""
    sharp = cad_generator.build_bracket(**{**BASELINE, "fillet_radius": 0.0})
    expected = (
        BASELINE["thickness"] * BASELINE["plate_height"]
        + BASELINE["arm_length"] * BASELINE["thickness"]
    ) * BASELINE["width"]

    assert sharp.val().Volume() == pytest.approx(expected, rel=1e-6)


@pytest.mark.parametrize(
    "field",
    ["plate_height", "arm_length", "width", "thickness"],
)
@pytest.mark.parametrize("bad_value", [0.0, -1.0])
def test_non_positive_dimensions_are_rejected(field, bad_value):
    """Safety-critical dimensions must fail loudly, never default silently."""
    params = {**BASELINE, field: bad_value}

    with pytest.raises(ValueError, match=field):
        cad_generator.build_bracket(**params)


def test_negative_fillet_radius_is_rejected():
    with pytest.raises(ValueError, match="fillet_radius"):
        cad_generator.build_bracket(**{**BASELINE, "fillet_radius": -1.0})


def test_fillet_larger_than_plate_above_arm_is_rejected():
    """r must fit in the plate height above the arm (H - t = 96 mm here)."""
    params = {**BASELINE, "plate_height": 10.0, "fillet_radius": 6.0}

    with pytest.raises(ValueError, match="plate height above the arm"):
        cad_generator.build_bracket(**params)


def test_fillet_larger_than_arm_length_is_rejected():
    params = {**BASELINE, "arm_length": 4.0, "fillet_radius": 5.0}

    with pytest.raises(ValueError, match="arm_length"):
        cad_generator.build_bracket(**params)


def test_step_export_creates_a_readable_file(bracket, tmp_path):
    """Export, then read it back: existence alone does not prove usability."""
    step_path = tmp_path / "bracket_geometry.step"

    returned = cad_generator.export_step(bracket, step_path)

    assert returned == step_path
    assert step_path.is_file()
    assert step_path.stat().st_size > 0

    reimported = cad_generator.import_step(step_path)
    assert reimported.val().Volume() == pytest.approx(
        bracket.val().Volume(), rel=1e-6
    )


def test_export_creates_missing_parent_directories(bracket, tmp_path):
    step_path = tmp_path / "nested" / "run_001" / "bracket_geometry.step"

    cad_generator.export_step(bracket, step_path)

    assert step_path.is_file()


def test_import_step_reports_a_missing_file_clearly(tmp_path):
    with pytest.raises(FileNotFoundError):
        cad_generator.import_step(tmp_path / "does_not_exist.step")


# --- Holes ---------------------------------------------------------------

HOLE_DIAMETER = 9.0
HOLE_CENTRES = [(-15.0, 39.5), (15.0, 39.5), (-15.0, 69.5), (15.0, 69.5)]


@pytest.fixture(scope="module")
def drilled_bracket() -> cq.Workplane:
    return cad_generator.build_bracket(
        **BASELINE, hole_diameter=HOLE_DIAMETER, hole_centres=HOLE_CENTRES
    )


def test_holes_remove_the_expected_volume(bracket, drilled_bracket):
    """Each hole removes pi*r^2*t, because it passes through the plate only."""
    removed = bracket.val().Volume() - drilled_bracket.val().Volume()
    expected = (
        len(HOLE_CENTRES)
        * math.pi
        * (HOLE_DIAMETER / 2.0) ** 2
        * BASELINE["thickness"]
    )

    assert removed == pytest.approx(expected, rel=1e-6)


def test_drilled_bracket_is_still_one_valid_solid(drilled_bracket):
    """Cutting must not fragment the part or produce an invalid shape."""
    shape = drilled_bracket.val()

    assert shape.isValid()
    assert len(shape.Solids()) == 1


def test_holes_do_not_change_the_overall_extents(bracket, drilled_bracket):
    """Holes are internal, so the bounding box must be untouched."""
    before = bracket.val().BoundingBox()
    after = drilled_bracket.val().BoundingBox()

    for attribute in ("xmin", "xmax", "ymin", "ymax", "zmin", "zmax"):
        assert getattr(after, attribute) == pytest.approx(
            getattr(before, attribute), abs=1e-6
        )


def test_no_holes_are_cut_when_the_diameter_is_zero():
    plain = cad_generator.build_bracket(
        **BASELINE, hole_diameter=0.0, hole_centres=HOLE_CENTRES
    )
    reference = cad_generator.build_bracket(**BASELINE)

    assert plain.val().Volume() == pytest.approx(reference.val().Volume(), rel=1e-9)


def test_no_holes_are_cut_when_no_centres_are_given():
    plain = cad_generator.build_bracket(**BASELINE, hole_diameter=HOLE_DIAMETER)
    reference = cad_generator.build_bracket(**BASELINE)

    assert plain.val().Volume() == pytest.approx(reference.val().Volume(), rel=1e-9)


def test_build_from_inputs_matches_the_explicit_call(drilled_bracket):
    """The convenience wrapper must not quietly build something different."""
    from src.schemas import BracketInputs

    inputs = BracketInputs(
        **BASELINE,
        hole_diameter=HOLE_DIAMETER,
        hole_spacing=30.0,
        num_holes=4,
        washer_diameter=17.0,
        material="structural_steel_s275",
        applied_load=500.0,
        load_case="tip_load",
        mesh_size=2.0,
        target_factor_of_safety=2.0,
    )

    assert inputs.hole_centres() == HOLE_CENTRES

    built = cad_generator.build_from_inputs(inputs)
    assert built.val().Volume() == pytest.approx(
        drilled_bracket.val().Volume(), rel=1e-9
    )
