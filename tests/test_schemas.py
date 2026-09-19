"""Tests for src/schemas.py.

Units: mm, N, MPa.

The schema is the gatekeeper, so most of these tests are about what it
*refuses*. Each rejection test also checks the message names the offending
dimension: an error that says "validation failed" helps nobody.
"""

from __future__ import annotations

import json
import math

import pytest
from pydantic import ValidationError

from src.schemas import (
    BracketInputs,
    LoadCase,
    Material,
    load_materials,
    resolve_material,
)

BASELINE = dict(
    plate_height=100.0,
    arm_length=80.0,
    width=60.0,
    thickness=4.0,
    fillet_radius=5.0,
    hole_diameter=9.0,
    hole_spacing=30.0,
    num_holes=4,
    material="structural_steel_s275",
    applied_load=500.0,
    load_case="tip_load",
    mesh_size=2.0,
    target_factor_of_safety=2.0,
)


def make_inputs(**overrides) -> BracketInputs:
    return BracketInputs(**{**BASELINE, **overrides})


# --- Happy path ---------------------------------------------------------


def test_baseline_inputs_are_accepted():
    inputs = make_inputs()

    assert inputs.num_holes == 4
    assert inputs.load_case is LoadCase.TIP_LOAD


def test_baseline_raises_no_warnings():
    """The shipped baseline should be a clean design, not a borderline one."""
    assert make_inputs().warnings() == []


def test_example_file_loads_and_validates():
    inputs = BracketInputs.from_json_file("examples/baseline_bracket.json")

    assert inputs.material == "structural_steel_s275"
    assert inputs.warnings() == []


def test_default_inputs_file_loads_and_validates():
    inputs = BracketInputs.from_json_file("config/default_inputs.json")

    assert inputs.warnings() == []


# --- Derived hole geometry ----------------------------------------------


def test_two_holes_form_one_row_centred_in_the_usable_band():
    inputs = make_inputs(num_holes=2)
    centres = inputs.hole_centres()

    assert len(centres) == 2

    ys = sorted(y for y, _ in centres)
    assert ys == pytest.approx([-15.0, 15.0])

    # Band runs from thickness + fillet_radius = 9 mm up to 100 mm.
    expected_z = (9.0 + 100.0) / 2.0
    assert all(z == pytest.approx(expected_z) for _, z in centres)


def test_four_holes_form_a_square_pattern():
    inputs = make_inputs(num_holes=4)
    centres = inputs.hole_centres()

    assert len(centres) == 4

    zs = sorted({round(z, 6) for _, z in centres})
    assert len(zs) == 2
    assert zs[1] - zs[0] == pytest.approx(BASELINE["hole_spacing"])

    # Symmetric about the band centre.
    assert (zs[0] + zs[1]) / 2 == pytest.approx((9.0 + 100.0) / 2.0)


def test_hole_pattern_is_symmetric_about_the_bracket_centreline():
    centres = make_inputs().hole_centres()

    assert sum(y for y, _ in centres) == pytest.approx(0.0)


def test_hole_volume_matches_the_hand_calculation():
    inputs = make_inputs()
    expected = 4 * math.pi * (9.0 / 2.0) ** 2 * 4.0

    assert inputs.hole_volume == pytest.approx(expected, rel=1e-12)


# --- Rejections: individual fields --------------------------------------


@pytest.mark.parametrize(
    "field",
    [
        "plate_height",
        "arm_length",
        "width",
        "thickness",
        "hole_diameter",
        "hole_spacing",
        "applied_load",
        "mesh_size",
        "target_factor_of_safety",
    ],
)
@pytest.mark.parametrize("bad_value", [0.0, -5.0])
def test_non_positive_values_are_rejected(field, bad_value):
    with pytest.raises(ValidationError) as excinfo:
        make_inputs(**{field: bad_value})

    assert field in str(excinfo.value)


def test_negative_fillet_radius_is_rejected():
    with pytest.raises(ValidationError, match="fillet_radius"):
        make_inputs(fillet_radius=-1.0)


def test_zero_fillet_radius_is_allowed():
    """A sharp corner is a legal, if poor, design; it is not impossible."""
    assert make_inputs(fillet_radius=0.0).fillet_radius == 0.0


@pytest.mark.parametrize("count", [0, 1, 3, 5, 6])
def test_only_two_or_four_holes_are_allowed(count):
    with pytest.raises(ValidationError, match="num_holes"):
        make_inputs(num_holes=count)


def test_unknown_field_is_rejected():
    """A typo must fail loudly rather than be silently ignored."""
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        make_inputs(thicknes=4.0)


@pytest.mark.parametrize(
    "field",
    ["plate_height", "thickness", "material", "applied_load", "load_case", "mesh_size"],
)
def test_safety_critical_fields_have_no_default(field):
    """These must never default silently; omitting one has to fail."""
    payload = {key: value for key, value in BASELINE.items() if key != field}

    with pytest.raises(ValidationError, match=field):
        BracketInputs(**payload)


def test_invalid_load_case_is_rejected():
    with pytest.raises(ValidationError, match="load_case"):
        make_inputs(load_case="sideways")


# --- Rejections: cross-field geometry -----------------------------------


def test_holes_closer_than_their_diameter_are_rejected():
    with pytest.raises(ValidationError, match="overlap or touch"):
        make_inputs(hole_diameter=9.0, hole_spacing=8.0)


def test_holes_breaking_out_of_the_side_are_rejected():
    with pytest.raises(ValidationError, match="side of the plate"):
        make_inputs(hole_spacing=55.0)


def test_holes_breaking_out_of_the_top_are_rejected():
    # Wide plate so the side check passes and the top check is the one that
    # fires.
    with pytest.raises(ValidationError, match="top of the plate"):
        make_inputs(width=200.0, hole_spacing=82.0)


def test_holes_cutting_into_the_fillet_region_are_rejected():
    with pytest.raises(ValidationError, match="cut into the fillet"):
        make_inputs(width=200.0, hole_spacing=82.0)


def test_fillet_taller_than_the_plate_above_the_arm_is_rejected():
    with pytest.raises(ValidationError, match="plate height above the arm"):
        make_inputs(plate_height=8.0, fillet_radius=5.0, hole_diameter=1.0,
                    hole_spacing=2.0)


def test_fillet_longer_than_the_arm_is_rejected():
    with pytest.raises(ValidationError, match="tip of the arm"):
        make_inputs(arm_length=4.0, fillet_radius=5.0)


def test_plate_thinner_than_its_own_thickness_is_rejected():
    with pytest.raises(ValidationError, match="no plate above the arm"):
        make_inputs(plate_height=4.0, thickness=4.0, fillet_radius=0.0)


def test_error_message_lists_every_problem_at_once():
    """One round trip should reveal all the conflicts, not just the first."""
    with pytest.raises(ValidationError) as excinfo:
        make_inputs(width=200.0, hole_spacing=82.0)

    message = str(excinfo.value)
    assert "top of the plate" in message
    assert "cut into the fillet" in message


# --- Warnings ------------------------------------------------------------


def test_tight_side_edge_distance_warns_but_is_allowed():
    inputs = make_inputs(hole_spacing=39.0)

    warnings = inputs.warnings()
    assert any("side edge" in note for note in warnings)


def test_thin_ligament_between_holes_warns():
    inputs = make_inputs(hole_diameter=20.0, hole_spacing=28.0, width=120.0)

    assert any("between adjacent holes" in note for note in inputs.warnings())


def test_stubby_arm_warns_that_beam_theory_is_a_weak_reference():
    # L/t = 60/4 = 15, comfortably slender: no warning.
    slender = make_inputs(arm_length=60.0, thickness=4.0)
    assert slender.warnings() == []

    # L/t = 30/4 = 7.5, below the threshold of 10.
    stubby = make_inputs(arm_length=30.0, thickness=4.0)
    assert any("slenderness" in note for note in stubby.warnings())


def test_mesh_coarser_than_half_the_thickness_warns():
    """Engineering decision 5: at least 2 elements through the thickness."""
    inputs = make_inputs(mesh_size=3.0)

    assert any("through the thickness" in note for note in inputs.warnings())


def test_mesh_exactly_half_the_thickness_does_not_warn():
    assert make_inputs(mesh_size=2.0).warnings() == []


# --- Materials -----------------------------------------------------------


def test_material_library_loads_and_every_entry_is_valid():
    materials = load_materials()

    assert "structural_steel_s275" in materials
    assert all(isinstance(value, Material) for value in materials.values())


def test_steel_properties_are_in_the_expected_unit_system():
    """mm/N/MPa with density in tonne/mm^3: steel is ~7.85e-9, not 7850."""
    steel = resolve_material("structural_steel_s275")

    assert steel.youngs_modulus == pytest.approx(210000.0)
    assert steel.density == pytest.approx(7.85e-9)
    assert 0.0 < steel.poissons_ratio < 0.5


def test_unknown_material_lists_the_available_options():
    with pytest.raises(ValueError) as excinfo:
        resolve_material("unobtainium")

    message = str(excinfo.value)
    assert "unobtainium" in message
    assert "structural_steel_s275" in message


def test_poissons_ratio_of_half_is_rejected():
    """At nu = 0.5 the material is incompressible and the solve is singular."""
    with pytest.raises(ValidationError, match="poissons_ratio"):
        Material(
            name="incompressible",
            youngs_modulus=210000.0,
            poissons_ratio=0.5,
            yield_strength=275.0,
            density=7.85e-9,
        )


def test_missing_material_library_is_reported_clearly(tmp_path):
    with pytest.raises(FileNotFoundError, match="Material library not found"):
        load_materials(tmp_path / "nope.json")


def test_malformed_material_library_is_reported_clearly(tmp_path):
    path = tmp_path / "materials.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(ValueError, match="not valid JSON"):
        load_materials(path)


def test_material_library_without_materials_key_is_rejected(tmp_path):
    path = tmp_path / "materials.json"
    path.write_text(json.dumps({"stuff": {}}), encoding="utf-8")

    with pytest.raises(ValueError, match="no 'materials' object"):
        load_materials(path)


def test_resolved_material_returns_the_named_material():
    assert make_inputs().resolved_material().name == "Structural steel S275"


# --- File loading --------------------------------------------------------


def test_missing_input_file_is_reported_clearly(tmp_path):
    with pytest.raises(FileNotFoundError, match="No input file"):
        BracketInputs.from_json_file(tmp_path / "absent.json")


def test_malformed_input_file_is_reported_clearly(tmp_path):
    path = tmp_path / "inputs.json"
    path.write_text("{oops", encoding="utf-8")

    with pytest.raises(ValueError, match="not valid JSON"):
        BracketInputs.from_json_file(path)
