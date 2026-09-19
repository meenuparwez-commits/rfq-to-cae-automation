"""Tests for src/analytical.py.

Units: mm, N, MPa.

This module is the reference the FE result is judged against, so it is checked
against the formulas in docs/engineering-notes.md rather than against
itself. If
these are wrong, every validation in the project is wrong in the same
direction and nothing would notice.
"""

from __future__ import annotations

import pytest

from src import analytical
from src.schemas import BracketInputs, LoadCase

F = 500.0
L = 80.0
B = 60.0
T = 4.0
E = 210000.0
NU = 0.3

I = B * T**3 / 12.0  # 320 mm^4


# --- Section properties ---------------------------------------------------


def test_second_moment_of_area():
    assert analytical.second_moment_of_area(B, T) == pytest.approx(320.0)


def test_plate_modulus_is_stiffer_than_youngs_modulus():
    """E/(1-nu^2) > E, so a plate is stiffer and deflects less."""
    plate = analytical.plate_modulus(E, NU)

    assert plate == pytest.approx(E / (1 - NU**2))
    assert plate > E


@pytest.mark.parametrize("bad", [0.0, -1.0])
def test_non_positive_section_is_rejected(bad):
    with pytest.raises(ValueError):
        analytical.second_moment_of_area(bad, T)
    with pytest.raises(ValueError):
        analytical.bending_stress(1000.0, B, bad)


# --- Tip load -------------------------------------------------------------


def test_tip_load_root_moment_and_stress():
    """sigma_root = 6FL/(bt^2), engineering decision 1."""
    moment = analytical.moment_at(LoadCase.TIP_LOAD, F, L, 0.0)

    assert moment == pytest.approx(F * L)
    assert analytical.bending_stress(moment, B, T) == pytest.approx(
        6 * F * L / (B * T**2)
    )


def test_tip_load_mid_span_moment_is_half_the_root():
    """Engineering decision 2 names M = FL/2 at mid-span for a tip load."""
    assert analytical.moment_at(LoadCase.TIP_LOAD, F, L, L / 2) == pytest.approx(
        F * L / 2
    )


def test_tip_load_moment_vanishes_at_the_tip():
    assert analytical.moment_at(LoadCase.TIP_LOAD, F, L, L) == pytest.approx(0.0)


def test_tip_load_deflection():
    """delta = FL^3/(3EI), engineering decision 1."""
    assert analytical.tip_deflection(
        LoadCase.TIP_LOAD, F, L, E, I
    ) == pytest.approx(F * L**3 / (3 * E * I))


# --- UDL over the full length ---------------------------------------------


def test_full_udl_root_moment_and_stress():
    """sigma_root = 3FL/(bt^2), which is 6M/(bt^2) with M = FL/2."""
    moment = analytical.moment_at(LoadCase.UDL, F, L, 0.0, load_start=0.0)

    assert moment == pytest.approx(F * L / 2)
    assert analytical.bending_stress(moment, B, T) == pytest.approx(
        3 * F * L / (B * T**2)
    )


def test_full_udl_mid_span_moment():
    """Engineering decision 2 names M = FL/8 at mid-span for a UDL."""
    assert analytical.moment_at(
        LoadCase.UDL, F, L, L / 2, load_start=0.0
    ) == pytest.approx(F * L / 8)


def test_full_udl_deflection():
    """delta = FL^3/(8EI). The partial-UDL formula must reduce to this."""
    assert analytical.tip_deflection(
        LoadCase.UDL, F, L, E, I, load_start=0.0
    ) == pytest.approx(F * L**3 / (8 * E * I))


# --- UDL starting away from the root --------------------------------------


def test_partial_udl_root_moment():
    """Load over [c, L] has resultant F at the midpoint of that span."""
    c = 5.0
    moment = analytical.moment_at(LoadCase.UDL, F, L, 0.0, load_start=c)

    assert moment == pytest.approx(F * (L + c) / 2)


def test_partial_udl_raises_the_root_moment_measurably():
    """On the baseline geometry the shift is 6.25%: not negligible.

    Ignoring the shortened load face would look exactly
    like a 6% FE error.
    """
    c = 5.0
    partial = analytical.moment_at(LoadCase.UDL, F, L, 0.0, load_start=c)
    textbook = analytical.moment_at(LoadCase.UDL, F, L, 0.0, load_start=0.0)

    assert partial / textbook == pytest.approx(1.0625)


def test_partial_udl_moment_is_continuous_at_the_load_start():
    """The two branches of M(s) must agree where they meet, at s = c.

    Both should give F(L-c)/2. A mistake in either branch shows up here.
    """
    c = 5.0
    at_start = analytical.moment_at(LoadCase.UDL, F, L, c, load_start=c)

    assert at_start == pytest.approx(F * (L - c) / 2)

    just_below = analytical.moment_at(LoadCase.UDL, F, L, c - 1e-9, load_start=c)
    just_above = analytical.moment_at(LoadCase.UDL, F, L, c + 1e-9, load_start=c)

    assert just_below == pytest.approx(just_above, rel=1e-6)


def test_partial_udl_moment_vanishes_at_the_tip():
    assert analytical.moment_at(
        LoadCase.UDL, F, L, L, load_start=5.0
    ) == pytest.approx(0.0)


def test_partial_udl_deflects_more_than_the_textbook_case():
    """Moving the load outboard increases both moment and deflection."""
    partial = analytical.tip_deflection(LoadCase.UDL, F, L, E, I, load_start=5.0)
    textbook = analytical.tip_deflection(LoadCase.UDL, F, L, E, I, load_start=0.0)

    assert partial > textbook


def test_udl_concentrated_at_the_tip_approaches_the_tip_load_case():
    """As the loaded span shrinks to a point at the tip, both must converge.

    An independent cross-check between the two load cases.
    """
    almost_tip = L - 1e-4

    udl_moment = analytical.moment_at(LoadCase.UDL, F, L, 0.0, load_start=almost_tip)
    tip_moment = analytical.moment_at(LoadCase.TIP_LOAD, F, L, 0.0)
    assert udl_moment == pytest.approx(tip_moment, rel=1e-5)

    udl_delta = analytical.tip_deflection(
        LoadCase.UDL, F, L, E, I, load_start=almost_tip
    )
    tip_delta = analytical.tip_deflection(LoadCase.TIP_LOAD, F, L, E, I)
    assert udl_delta == pytest.approx(tip_delta, rel=1e-4)


# --- Moment behaviour -----------------------------------------------------


@pytest.mark.parametrize("case", [LoadCase.TIP_LOAD, LoadCase.UDL])
def test_moment_decreases_from_root_to_tip(case):
    previous = float("inf")
    for step in range(11):
        moment = analytical.moment_at(case, F, L, L * step / 10, load_start=5.0)
        assert moment <= previous + 1e-9
        previous = moment


@pytest.mark.parametrize("section", [-1.0, L + 1.0])
def test_section_outside_the_arm_is_rejected(section):
    with pytest.raises(ValueError, match="must lie between"):
        analytical.moment_at(LoadCase.TIP_LOAD, F, L, section)


def test_load_start_beyond_the_tip_is_rejected():
    with pytest.raises(ValueError, match="must be less than arm_length"):
        analytical.moment_at(LoadCase.UDL, F, L, 0.0, load_start=L)


# --- The assembled reference ---------------------------------------------


@pytest.fixture
def inputs() -> BracketInputs:
    return BracketInputs.from_json_file("examples/baseline_bracket.json")


def test_reference_matches_hand_values_for_the_baseline(inputs):
    """Hand calculation for the shipped baseline: 250 N tip load, b60 t4 L80.

        I         = 60 * 4^3 / 12                 = 320 mm^4
        M_root    = F L = 250 * 80                = 20000 N.mm
        sigma_root= 6 M / (b t^2) = 120000 / 960  = 125 MPa
        M at s=L/2 = F L / 2                      = 10000 N.mm
        sigma      = 62.5 MPa
    """
    assert inputs.applied_load == pytest.approx(250.0), (
        "The baseline load changed; the hand values below must change with it."
    )

    reference = analytical.compute_reference(inputs, inputs.resolved_material(), 0.0)

    assert reference.second_moment == pytest.approx(320.0)
    assert reference.root_moment == pytest.approx(20000.0)
    assert reference.root_stress == pytest.approx(125.0)
    assert reference.section_position == pytest.approx(40.0)
    assert reference.section_stress == pytest.approx(62.5)


def test_reference_defaults_the_section_to_mid_span(inputs):
    """Engineering decision 2: validate away from the root, not at the peak."""
    reference = analytical.compute_reference(inputs, inputs.resolved_material(), 0.0)

    assert reference.section_position == pytest.approx(inputs.arm_length / 2)


def test_plate_bound_is_stiffer_than_the_beam_bound(inputs):
    """Engineering decision 3: the plate bound is the LOWER deflection."""
    reference = analytical.compute_reference(inputs, inputs.resolved_material(), 0.0)

    assert reference.tip_deflection_plate < reference.tip_deflection_beam


def test_load_start_flag_is_off_for_a_tip_load(inputs):
    reference = analytical.compute_reference(inputs, inputs.resolved_material(), 0.0)

    assert not reference.load_start_matters


def test_load_start_flag_is_on_for_a_fillet_shortened_udl(inputs):
    udl = inputs.model_copy(update={"load_case": LoadCase.UDL})
    reference = analytical.compute_reference(
        udl, udl.resolved_material(), udl.fillet_radius
    )

    assert reference.load_start_matters
    assert reference.root_stress > reference.textbook_root_stress
