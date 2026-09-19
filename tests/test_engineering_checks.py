"""Tests for src/engineering_checks.py.

Units: mm, N, MPa.

Every check is exercised in both directions. A check that has only ever passed
is not evidence of anything, and these are the checks that decide whether the
project reports a result as trustworthy.
"""

from __future__ import annotations

import numpy as np
import pytest

from src import analytical, engineering_checks
from src.geometry_checks import ADVISORY, CheckResult
from src.result_reader import Results, SectionStress
from src.schemas import BracketInputs, LoadCase

APPLIED_LOAD = 500.0
TARGET_FOS = 2.0


@pytest.fixture(scope="module")
def inputs() -> BracketInputs:
    return BracketInputs.from_json_file("examples/baseline_bracket.json")


@pytest.fixture(scope="module")
def reference(inputs) -> analytical.AnalyticalReference:
    return analytical.compute_reference(inputs, inputs.resolved_material(), 0.0)


def make_summary(
    reference: analytical.AnalyticalReference,
    *,
    tip_deflection: float | None = None,
    section_stress: float | None = None,
    peak_stress: float = 260.435,
    reactions: tuple[float, float, float] | None = None,
    peak_location: tuple[float, float, float] = (0.0, -15.49, 29.85),
    structural_stress: float | None = None,
    structural_location: tuple[float, float, float] = (8.35, 2.62, 4.04),
    yield_strength: float = 275.0,
) -> engineering_checks.ResultSummary:
    """A summary built from chosen numbers, so each check can be steered.

    The defaults describe the shape of a real run under the bolted restraint:
    the raw peak sits at a clamped edge, and the structural peak - the one the
    verdict uses - sits in the fillet.
    """
    if structural_stress is None:
        structural_stress = peak_stress
    if tip_deflection is None:
        tip_deflection = reference.tip_deflection_plate * 1.02
    if section_stress is None:
        section_stress = reference.section_stress
    if reactions is None:
        reactions = (0.0, 0.0, reference.total_load)

    section = SectionStress(
        position=reference.section_position,
        num_top_nodes=100,
        num_bottom_nodes=100,
        top_mean=section_stress,
        bottom_mean=-section_stress,
    )

    return engineering_checks.ResultSummary(
        max_displacement=tip_deflection * 1.01,
        tip_deflection=tip_deflection,
        max_von_mises=peak_stress,
        peak_location=peak_location,
        section=section,
        reactions=np.asarray(reactions),
        stress_concentration=structural_stress / reference.root_stress,
        factor_of_safety_peak=yield_strength / peak_stress,
        factor_of_safety_section=yield_strength / abs(section_stress),
        max_von_mises_structural=structural_stress,
        structural_location=structural_location,
        factor_of_safety_structural=yield_strength / structural_stress,
        restraint_zone=4.0,
    )


# --- Equilibrium ----------------------------------------------------------


def test_equilibrium_passes_when_reactions_balance_the_load():
    reactions = np.asarray([-8.4e-9, 1.9e-9, 500.0])

    result = engineering_checks.check_equilibrium(reactions, APPLIED_LOAD)

    assert result.passed


def test_equilibrium_fails_when_the_vertical_reaction_is_wrong():
    """Half the load returned means half of it went somewhere it should not."""
    reactions = np.asarray([0.0, 0.0, 250.0])

    result = engineering_checks.check_equilibrium(reactions, APPLIED_LOAD)

    assert not result.passed


def test_equilibrium_fails_on_a_large_lateral_reaction():
    """Catches a load applied in the wrong direction.

    The vertical component can still be right while the model is being pushed
    sideways, and nothing else here would notice.
    """
    reactions = np.asarray([200.0, 0.0, 500.0])

    result = engineering_checks.check_equilibrium(reactions, APPLIED_LOAD)

    assert not result.passed


def test_equilibrium_tolerance_is_one_percent():
    """The agreed tolerance is ~1%."""
    assert engineering_checks.EQUILIBRIUM_REL_TOL == pytest.approx(0.01)

    just_inside = np.asarray([0.0, 0.0, APPLIED_LOAD * 1.009])
    just_outside = np.asarray([0.0, 0.0, APPLIED_LOAD * 1.02])

    assert engineering_checks.check_equilibrium(just_inside, APPLIED_LOAD).passed
    assert not engineering_checks.check_equilibrium(just_outside, APPLIED_LOAD).passed


# --- Tip deflection -------------------------------------------------------


def test_deflection_above_the_rigid_root_bound_passes(reference):
    """Engineering decision 3, restated for the bolted restraint.

    Both closed forms assume a rigid wall. The bracket is held by four washer
    rings on a 4 mm plate, so it can only be MORE flexible. Sitting above the
    bound is the expected result, not a deviation to be explained away.
    """
    summary = make_summary(
        reference, tip_deflection=reference.tip_deflection_beam * 2.0
    )

    result = engineering_checks.check_tip_deflection(summary.tip_deflection, reference)

    assert result.passed
    assert "rigid-root bounds" in result.message
    assert "flexing between its bolts" in result.message


def test_a_result_stiffer_than_a_rigid_wall_fails(reference):
    """The one thing that cannot happen physically.

    A support adds compliance; it never removes it. A tip deflection below the
    rigid-root value therefore has a single explanation - the model is held
    more tightly than the bracket is - which is exactly the failure a restraint
    change is most likely to introduce.
    """
    stiffest = min(reference.tip_deflection_plate, reference.tip_deflection_beam)
    summary = make_summary(reference, tip_deflection=stiffest * 0.5)

    result = engineering_checks.check_tip_deflection(summary.tip_deflection, reference)

    assert not result.passed
    assert "over-restrained" in result.message


def test_a_very_flexible_result_still_passes_the_lower_bound(reference):
    """Deliberate: the check is one-sided.

    How far above the bound the result sits is base flexibility, which is a
    property of the design rather than an error, so it is reported as a
    compliance ratio instead of being bounded by a number nobody can justify.
    """
    summary = make_summary(
        reference, tip_deflection=reference.tip_deflection_beam * 5.0
    )

    result = engineering_checks.check_tip_deflection(summary.tip_deflection, reference)

    assert result.passed
    assert "FE/beam 5.0000" in result.message


def test_the_plate_bound_is_the_lower_deflection(reference):
    """Stiffer material bound gives less deflection, so it is the floor."""
    assert reference.tip_deflection_plate < reference.tip_deflection_beam


# --- Section stress -------------------------------------------------------


def test_section_stress_matching_beam_theory_passes(reference):
    summary = make_summary(reference, section_stress=reference.section_stress)

    result = engineering_checks.check_section_stress(summary.section, reference)

    assert result.passed


def test_section_stress_off_by_twenty_percent_fails(reference):
    summary = make_summary(
        reference, section_stress=reference.section_stress * 1.2
    )

    result = engineering_checks.check_section_stress(summary.section, reference)

    assert not result.passed
    assert "20" in result.message


def test_section_stress_tolerance_is_five_percent(reference):
    inside = make_summary(reference, section_stress=reference.section_stress * 1.04)
    outside = make_summary(reference, section_stress=reference.section_stress * 1.06)

    assert engineering_checks.check_section_stress(inside.section, reference).passed
    assert not engineering_checks.check_section_stress(
        outside.section, reference
    ).passed


# --- Stress concentration -------------------------------------------------


def test_stress_concentration_is_reported_with_its_location(reference):
    summary = make_summary(reference)

    result = engineering_checks.check_stress_concentration(summary, reference)

    assert result.passed
    assert "K_t" in result.message
    assert "x=8.35" in result.message
    assert "not validated against" in result.message


def test_a_peak_below_the_nominal_root_stress_is_flagged(reference):
    """A filleted corner cannot reduce stress below the nominal value."""
    summary = make_summary(reference, peak_stress=reference.root_stress * 0.5)

    result = engineering_checks.check_stress_concentration(summary, reference)

    assert not result.passed


def test_an_implausibly_high_concentration_is_flagged_as_a_singularity(reference):
    summary = make_summary(reference, peak_stress=reference.root_stress * 20)

    result = engineering_checks.check_stress_concentration(summary, reference)

    assert not result.passed
    assert "singularity" in result.message


# --- Factor of safety -----------------------------------------------------


def test_factor_of_safety_arithmetic():
    assert engineering_checks.factor_of_safety(275.0, 137.5) == pytest.approx(2.0)


def test_factor_of_safety_of_zero_stress_is_infinite():
    """An unloaded region must not divide by zero."""
    assert engineering_checks.factor_of_safety(275.0, 0.0) == float("inf")


def test_factor_of_safety_passes_when_above_the_target(reference):
    summary = make_summary(reference, peak_stress=100.0)

    result = engineering_checks.check_factor_of_safety(summary, TARGET_FOS)

    assert result.passed


def test_factor_of_safety_fails_below_the_target_and_suggests_remedies(reference):
    """The baseline at 500 N: FoS 1.06 against a target of 2."""
    summary = make_summary(reference, peak_stress=260.435)

    result = engineering_checks.check_factor_of_safety(summary, TARGET_FOS)

    assert not result.passed
    assert result.value == pytest.approx(275.0 / 260.435, rel=1e-6)
    assert "increase the thickness" in result.message


def test_every_factor_of_safety_is_reported(reference):
    """The number that was set aside has to stay visible.

    The verdict runs on the structural peak, but a reader has to be able to
    see the raw peak at the clamped edge and disagree with the judgement.
    Quietly dropping it would be the difference between an argued choice and
    a hidden one.
    """
    summary = make_summary(
        reference, peak_stress=273.593, structural_stress=138.764
    )

    result = engineering_checks.check_factor_of_safety(summary, TARGET_FOS)

    assert "structural peak 138.764" in result.message
    assert "Raw peak FoS" in result.message
    assert "restraint singularity" in result.message
    assert "away from the root" in result.message


def test_the_verdict_uses_the_structural_peak_not_the_clamp_singularity(reference):
    """A design that only fails at the clamped edge must not be failed for it.

    That stress climbs with every refinement, so a verdict driven by it would
    change every time the mesh did.
    """
    summary = make_summary(
        reference, peak_stress=1000.0, structural_stress=100.0
    )

    result = engineering_checks.check_factor_of_safety(summary, TARGET_FOS)

    assert result.passed  # 275 / 100 = 2.75, despite the raw peak at 0.275


# --- The suite ------------------------------------------------------------


def test_run_result_checks_covers_the_expected_checks(reference):
    summary = make_summary(reference)

    names = [
        check.name
        for check in engineering_checks.run_result_checks(
            summary, reference, APPLIED_LOAD, TARGET_FOS
        )
    ]

    assert names == [
        "Equilibrium",
        "Tip deflection vs rigid-root bound",
        "Bending stress away from the root",
        "Stress concentration K_t",
        "Factor of safety",
    ]


def test_correctness_checks_come_before_the_design_check(reference):
    """Order matters for the report: is it right, then is it good enough."""
    summary = make_summary(reference)
    checks = engineering_checks.run_result_checks(
        summary, reference, APPLIED_LOAD, TARGET_FOS
    )

    assert checks[0].name == "Equilibrium"
    assert checks[-1].name == "Factor of safety"


def test_a_good_result_passes_everything(inputs):
    """A design that is both correct and adequate passes every check.

    The load has to be reduced to build this case at all. At the baseline
    500 N the root stress is 250 MPa, so K_t >= 1 needs a peak of at least
    250 MPa while FoS >= 2 on S275 needs at most 137.5 MPa: no result can
    satisfy both. That is not a quirk of the test, it is the finding that the
    baseline design is overstressed.
    """
    lighter = inputs.model_copy(update={"applied_load": 150.0})
    reference = analytical.compute_reference(
        lighter, lighter.resolved_material(), 0.0
    )

    # Root stress is now 75 MPa; a peak of 78 MPa is K_t = 1.04, FoS = 3.5.
    summary = make_summary(reference, peak_stress=78.0)

    checks = engineering_checks.run_result_checks(
        summary, reference, lighter.applied_load, TARGET_FOS
    )

    failed = [str(check) for check in checks if not check.passed]
    assert not failed, "\n".join(failed)


# --- Verdict --------------------------------------------------------------


def passing(name: str = "a check") -> CheckResult:
    return CheckResult(name=name, passed=True, message="fine")


def failing_critical(name: str = "Equilibrium") -> CheckResult:
    return CheckResult(name=name, passed=False, message="not balanced")


def failing_advisory(name: str = "Elements through thickness") -> CheckResult:
    return CheckResult(
        name=name, passed=False, message="too coarse", severity=ADVISORY
    )


def test_all_checks_passing_gives_pass():
    result = engineering_checks.decide_verdict([passing(), passing()])

    assert result.verdict is engineering_checks.Verdict.PASS


def test_a_critical_failure_gives_fail():
    result = engineering_checks.decide_verdict([passing(), failing_critical()])

    assert result.verdict is engineering_checks.Verdict.FAIL
    assert len(result.failures) == 1


def test_an_advisory_failure_gives_review():
    """A coarse mesh is usable but needs a human eye, not a rejection."""
    result = engineering_checks.decide_verdict([passing(), failing_advisory()])

    assert result.verdict is engineering_checks.Verdict.REVIEW
    assert len(result.advisories) == 1
    assert not result.failures


def test_input_warnings_alone_give_review():
    """Legal but questionable inputs must not pass silently."""
    result = engineering_checks.decide_verdict(
        [passing()], input_warnings=["Holes are close to the edge."]
    )

    assert result.verdict is engineering_checks.Verdict.REVIEW
    assert result.warnings


def test_a_critical_failure_outranks_advisories():
    """If the model is not in equilibrium, a marginal mesh is beside the point."""
    result = engineering_checks.decide_verdict(
        [failing_critical(), failing_advisory()],
        input_warnings=["something"],
    )

    assert result.verdict is engineering_checks.Verdict.FAIL


def test_no_combination_of_passes_rescues_a_critical_failure():
    """The verdict never upgrades itself."""
    checks = [passing(f"check {i}") for i in range(20)] + [failing_critical()]

    result = engineering_checks.decide_verdict(checks)

    assert result.verdict is engineering_checks.Verdict.FAIL


def test_verdict_headline_counts_the_reasons():
    result = engineering_checks.decide_verdict(
        [failing_advisory(), failing_advisory("Mesh quality")]
    )

    assert "2 item(s)" in result.headline


def test_an_empty_check_list_gives_pass():
    """Nothing to object to. The pipeline never calls it this way, but the
    boundary should be defined rather than accidental."""
    assert (
        engineering_checks.decide_verdict([]).verdict
        is engineering_checks.Verdict.PASS
    )


def test_peak_location_is_taken_from_the_highest_stress_node():
    displacements = np.zeros((3, 3))
    stresses = np.zeros((3, 6))
    stresses[1, 0] = 500.0  # node index 1 is the hottest
    stresses[0, 0] = 10.0

    results = Results(displacements=displacements, stresses=stresses)
    points = np.asarray([[0.0, 0.0, 0.0], [1.0, 2.0, 3.0], [9.0, 9.0, 9.0]])

    assert engineering_checks.peak_stress_location(results, points) == (1.0, 2.0, 3.0)
