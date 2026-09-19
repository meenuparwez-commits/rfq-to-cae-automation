"""Tests for src/geometry_checks.py.

Units: mm.

Every check here is also tested in its failing direction. A check that has
never been seen to fail is not evidence of anything.

Baseline hand calculation, H 100, L 80, b 60, t 4, r 5:

    plate area  = 4 * 100                = 400.0          mm^2
    arm area    = 80 * 4                 = 320.0          mm^2
    fillet area = 5^2 * (1 - pi/4)       =   5.365045915  mm^2
    total area                           = 725.365045915  mm^2
    volume      = 725.365045915 * 60     = 43521.90275490 mm^3
"""

from __future__ import annotations

import pytest

from src import cad_generator, geometry_checks

BASELINE = dict(
    plate_height=100.0,
    arm_length=80.0,
    width=60.0,
    thickness=4.0,
    fillet_radius=5.0,
)

# Worked out by hand in the module docstring above, not read back from the CAD
# kernel. If this number and the CAD volume ever disagree, one of them is wrong
# and the test should say so.
EXPECTED_BASELINE_VOLUME = 43521.902754903826


@pytest.fixture(scope="module")
def bracket():
    return cad_generator.build_bracket(**BASELINE)


def test_analytical_volume_matches_the_hand_calculation():
    volume = geometry_checks.analytical_volume(**BASELINE)

    assert volume == pytest.approx(EXPECTED_BASELINE_VOLUME, rel=1e-12)


def test_analytical_volume_without_fillet_is_two_rectangles():
    volume = geometry_checks.analytical_volume(**{**BASELINE, "fillet_radius": 0.0})

    assert volume == pytest.approx((400.0 + 320.0) * 60.0, rel=1e-12)


def test_baseline_bracket_passes_every_check(bracket, tmp_path):
    step_path = tmp_path / "bracket_geometry.step"
    cad_generator.export_step(bracket, step_path)

    results = geometry_checks.run_all_checks(
        bracket, step_path=step_path, **BASELINE
    )

    failed = [str(result) for result in results if not result.passed]
    assert not failed, "Baseline bracket failed checks:\n" + "\n".join(failed)


def test_run_all_checks_covers_the_expected_checks(bracket, tmp_path):
    step_path = tmp_path / "bracket_geometry.step"
    cad_generator.export_step(bracket, step_path)

    names = [
        result.name
        for result in geometry_checks.run_all_checks(
            bracket, step_path=step_path, **BASELINE
        )
    ]

    assert names == [
        "CAD validity",
        "Single connected volume",
        "Volume vs hand calculation",
        "Bounding box and origin",
        "STEP export re-imports",
    ]


def test_step_check_is_skipped_when_no_path_is_given(bracket):
    names = [
        result.name for result in geometry_checks.run_all_checks(bracket, **BASELINE)
    ]

    assert "STEP export re-imports" not in names


def test_volume_check_reports_the_measured_and_expected_values(bracket):
    result = geometry_checks.check_volume(bracket, **BASELINE)

    assert result.passed
    assert result.value == pytest.approx(EXPECTED_BASELINE_VOLUME, rel=1e-6)
    assert result.expected == pytest.approx(EXPECTED_BASELINE_VOLUME, rel=1e-6)


def test_volume_check_fails_when_the_expected_dimensions_are_wrong(bracket):
    """Compare the real solid against a different thickness: it must fail.

    This is the negative case that proves the check has teeth.
    """
    result = geometry_checks.check_volume(bracket, **{**BASELINE, "thickness": 5.0})

    assert not result.passed
    assert "relative error" in result.message


def test_volume_check_fails_if_the_fillet_is_ignored(bracket):
    """A missing fillet must be caught.

    The fillet is 5.365 / 725.365 = 0.74% of the section, so it sits about 74
    times above the 1e-4 tolerance. This test is what pins that tolerance: if
    anyone loosens it past ~7e-3, this fails and says so.
    """
    result = geometry_checks.check_volume(bracket, **{**BASELINE, "fillet_radius": 0.0})

    assert not result.passed


def test_bounding_box_check_fails_for_wrong_expected_extents(bracket):
    result = geometry_checks.check_bounding_box(
        bracket,
        plate_height=120.0,  # actual solid is 100 mm tall
        arm_length=BASELINE["arm_length"],
        width=BASELINE["width"],
        thickness=BASELINE["thickness"],
    )

    assert not result.passed
    assert "zmax" in result.message


def test_single_solid_check_fails_for_two_disconnected_bodies():
    """Two separate boxes must be reported as two solids, not one."""
    import cadquery as cq

    first = cq.Workplane("XY").box(10, 10, 10)
    second = cq.Workplane("XY").box(10, 10, 10).translate((100, 0, 0))
    two_bodies = first.add(second)

    result = geometry_checks.check_single_solid(two_bodies)

    assert not result.passed
    assert "found 2" in result.message


def test_step_roundtrip_check_fails_for_a_missing_file(bracket, tmp_path):
    result = geometry_checks.check_step_roundtrip(
        tmp_path / "never_written.step", bracket
    )

    assert not result.passed
    assert "could not be read back" in result.message


# --- Holes ---------------------------------------------------------------

HOLE_DIAMETER = 9.0
NUM_HOLES = 4
HOLE_CENTRES = [(-15.0, 39.5), (15.0, 39.5), (-15.0, 69.5), (15.0, 69.5)]

# 43521.902754903826 - 4 * pi * 4.5^2 * 4 = 42504.026735140734
EXPECTED_DRILLED_VOLUME = 42504.026735140734


@pytest.fixture(scope="module")
def drilled():
    return cad_generator.build_bracket(
        **BASELINE, hole_diameter=HOLE_DIAMETER, hole_centres=HOLE_CENTRES
    )


def test_analytical_volume_subtracts_the_holes():
    volume = geometry_checks.analytical_volume(
        **BASELINE, hole_diameter=HOLE_DIAMETER, num_holes=NUM_HOLES
    )

    assert volume == pytest.approx(EXPECTED_DRILLED_VOLUME, rel=1e-12)


def test_drilled_volume_matches_the_hand_calculation(drilled):
    result = geometry_checks.check_volume(
        drilled, **BASELINE, hole_diameter=HOLE_DIAMETER, num_holes=NUM_HOLES
    )

    assert result.passed
    assert result.value == pytest.approx(EXPECTED_DRILLED_VOLUME, rel=1e-6)


def test_volume_check_fails_if_the_holes_are_ignored(drilled):
    """Holes are 2.3% of the volume, well above the 1e-4 tolerance."""
    result = geometry_checks.check_volume(drilled, **BASELINE)

    assert not result.passed


def test_hole_count_check_finds_every_hole(drilled):
    result = geometry_checks.check_hole_count(drilled, HOLE_DIAMETER, NUM_HOLES)

    assert result.passed
    assert result.value == 4.0


def test_hole_count_check_fails_when_holes_are_missing(drilled):
    result = geometry_checks.check_hole_count(drilled, HOLE_DIAMETER, num_holes=6)

    assert not result.passed
    assert "found 4" in result.message


def test_hole_count_ignores_the_fillet_even_at_the_same_radius():
    """The fillet is cylindrical too, so radius alone cannot separate them.

    Here the fillet radius equals the hole radius on purpose: only the axis
    direction distinguishes a hole (along x) from the fillet (along y).
    """
    radius_clash = {**BASELINE, "fillet_radius": HOLE_DIAMETER / 2.0}
    bracket = cad_generator.build_bracket(
        **radius_clash, hole_diameter=HOLE_DIAMETER, hole_centres=HOLE_CENTRES
    )

    result = geometry_checks.check_hole_count(bracket, HOLE_DIAMETER, NUM_HOLES)

    assert result.passed, result.message


def test_run_all_checks_includes_the_hole_count_when_holes_exist(drilled, tmp_path):
    step_path = tmp_path / "drilled.step"
    cad_generator.export_step(drilled, step_path)

    names = [
        result.name
        for result in geometry_checks.run_all_checks(
            drilled,
            **BASELINE,
            hole_diameter=HOLE_DIAMETER,
            num_holes=NUM_HOLES,
            step_path=step_path,
        )
    ]

    assert "Hole count" in names


def test_run_checks_for_inputs_passes_for_the_baseline(tmp_path):
    from src.schemas import BracketInputs

    inputs = BracketInputs.from_json_file("examples/baseline_bracket.json")
    bracket = cad_generator.build_from_inputs(inputs)
    step_path = tmp_path / "bracket.step"
    cad_generator.export_step(bracket, step_path)

    results = geometry_checks.run_checks_for_inputs(bracket, inputs, step_path)

    failed = [str(result) for result in results if not result.passed]
    assert not failed, "Baseline failed:\n" + "\n".join(failed)


def test_check_result_formats_readably():
    result = geometry_checks.CheckResult(
        name="Example", passed=False, message="something went wrong"
    )

    assert str(result) == "[FAIL] Example: something went wrong"
