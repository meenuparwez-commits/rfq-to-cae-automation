"""Judge whether the FE results are trustworthy, and whether the design passes.

Units: mm, N, MPa.

Two different questions live here and must not be confused:

    Is the answer right?    equilibrium, deflection against beam theory,
                            bending stress away from the root
    Is the design good?     factor of safety against the target

A solve that finishes proves neither. This project never claims validation
because the solver returned, so every check below compares against something
computed independently.

Engineering decision 2 governs what is validated: NOT either peak stress, and
the two misbehave for different reasons.

The FILLET peak is a real stress concentration on real geometry, so it has a
finite answer and does converge - just slowly, 2.78% then 0.44% across the
convergence study. The peak at the EDGE OF A CLAMPED WASHER RING does not
converge at all: restraining a sharp-edged region of a continuum has no finite
value to converge to, and refining the mesh raises it for ever.

Validation therefore uses tip deflection and bending stress at a section away
from the root, both of which settle. The fillet peak is reported as
K_t = sigma_FE,structural / sigma_beam,root, and the raw peak is reported and
set aside.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from enum import Enum

from src.analytical import AnalyticalReference
from src.geometry_checks import ADVISORY, CheckResult
from src.result_reader import Results, SectionStress
from src.schemas import BracketInputs, Material

# Equilibrium tolerance. In practice a linear static solve does
# far better than this: the baseline returns the applied load to 1e-9.
EQUILIBRIUM_REL_TOL = 0.01

# Bending stress away from the root should match beam theory closely, because
# the section is far from both the load and the restraint (St Venant). The
# baseline achieves 0.14% at mid-span; 5% leaves room for the coarser meshes
# used in the convergence study without ever tolerating a real error.
SECTION_STRESS_REL_TOL = 0.05

# How far below the rigid-root closed form the FE tip deflection may sit before
# the model is called over-restrained.
#
# This replaced a two-sided band, and it inherited the band's 15% allowance,
# which was wrong: 15% was sized for the gap between two ANALYTICAL
# idealisations, and reusing it here punched a hole straight through the
# physical statement the check now rests on. A model 14% stiffer than a rigid
# wall - which is impossible - passed.
#
# The bound itself is exact: a support adds compliance and never removes it. So
# the only thing this tolerance has to cover is DISCRETISATION. A displacement
# formulation converges on the true answer from the stiff side, so a coarse
# mesh deflects slightly too little. Measured across the convergence study the
# tip deflection moves 0.45% then 0.17% between levels, so 2% leaves about four
# times that scatter and is still 7.5x tighter than the value it replaces.
#
# BE CLEAR ABOUT WHAT THIS CANNOT CATCH. On the shipped geometry a fully fixed
# rear face gives 0.51704 mm against a floor of 0.50844 - it passes, by 1.7%.
# The two restraints are simply too close in stiffness for a deflection bound
# to separate them. What this does catch is a restraint stiffer than a rigid
# wall, which is unphysical and means the held region has grown into material
# it should not touch. Telling a bolted model from a welded one is the job of
# check_face_area and the equation count, not of this check.
DEFLECTION_FLOOR_TOL = 0.02

# A stress concentration below 1 would mean the peak is lower than the nominal
# root stress, which is not physical for a filleted corner. Above this, the
# peak is more likely a restraint singularity than a real feature.
MAX_PLAUSIBLE_KT = 5.0

# How far from the clamped edge the restraint's own singularity is treated as
# an artefact, as a multiple of plate thickness.
#
# Restraining a sharp-edged ring of a continuum produces a stress that rises
# without limit as the mesh is refined. It is not a material stress: a real
# bolted joint has a finite contact pressure, friction, and a washer that
# deforms. Ignoring it is not optional - it is mesh dependent, so letting it
# drive the verdict would make the verdict mesh dependent too.
#
# One thickness is St Venant applied to this geometry, and it is where the
# measurement says the disturbance has died away. Measured on the baseline,
# peak von Mises against distance from the clamped edge:
#
#   at the edge   273.593 MPa      1.0 t   138.764 MPa  (and now in the fillet)
#   0.5 t         154.473 MPa      2.1 t   138.764 MPa
#
# Note this rule does NOT rescue the shipped baseline, which still comes out
# under its target. It was set from where the singularity decays, not from
# where the example would pass.
RESTRAINT_ZONE_THICKNESSES = 1.0


@dataclass(frozen=True)
class ResultSummary:
    """The numbers a report needs, gathered in one place."""

    max_displacement: float
    tip_deflection: float
    max_von_mises: float
    peak_location: tuple[float, float, float]
    section: SectionStress
    reactions: np.ndarray
    stress_concentration: float
    factor_of_safety_peak: float
    factor_of_safety_section: float
    # The same peak with the clamp singularity set aside. This is the one the
    # verdict uses; the raw peak above is reported so the singularity stays
    # visible rather than being quietly dropped.
    max_von_mises_structural: float
    structural_location: tuple[float, float, float]
    factor_of_safety_structural: float
    restraint_zone: float


def peak_stress_location(results: Results, points: np.ndarray) -> tuple[float, float, float]:
    """Coordinates of the highest von Mises stress, mm.

    Worth reporting rather than just the value: a peak in the fillet is a real
    stress concentration, while a peak at the edge of a clamped ring is a
    restraint singularity and must not drive a verdict on its own.
    """
    index = int(np.argmax(results.von_mises))
    x, y, z = points[index]

    return float(x), float(y), float(z)


def distance_to_clamped_edge(points: np.ndarray, inputs: BracketInputs) -> np.ndarray:
    """Distance from each point to the nearest clamped washer edge, mm.

    The clamped edge is a circle of radius R in the plane x = 0, around each
    hole axis, so the set of points a fixed distance from it is a torus. Using
    the circle rather than the hole axis matters: a cylinder around the axis
    would also swallow the material straight behind the hole and, on a bracket
    with low holes, part of the arm.
    """
    centres = np.asarray(inputs.hole_centres(), dtype=float)
    outer = inputs.washer_diameter / 2.0

    radial = np.linalg.norm(points[:, None, 1:] - centres[None, :, :], axis=2)
    to_ring = np.hypot(radial - outer, points[:, 0][:, None])

    return to_ring.min(axis=1)


def structural_peak(
    results: Results, points: np.ndarray, inputs: BracketInputs
) -> tuple[float, tuple[float, float, float], float]:
    """Highest von Mises stress outside the clamp's singular zone.

    Returns the stress, its location and the zone radius used. Falls back to
    the raw peak if the zone would swallow the whole model, so a pathological
    input gives a conservative answer rather than an empty one.
    """
    zone = RESTRAINT_ZONE_THICKNESSES * inputs.thickness
    outside = distance_to_clamped_edge(points, inputs) > zone

    if not outside.any():
        index = int(np.argmax(results.von_mises))
    else:
        index = int(np.argmax(np.where(outside, results.von_mises, -np.inf)))

    x, y, z = points[index]

    return float(results.von_mises[index]), (float(x), float(y), float(z)), zone


def factor_of_safety(yield_strength: float, stress: float) -> float:
    """Yield strength divided by stress. Infinite if the stress is zero."""
    if stress <= 0.0:
        return float("inf")

    return yield_strength / stress


def summarise(
    results: Results,
    points: np.ndarray,
    tip_deflection: float,
    section: SectionStress,
    reactions: np.ndarray,
    reference: AnalyticalReference,
    material: Material,
    inputs: BracketInputs,
) -> ResultSummary:
    """Collect every headline number from one run."""
    peak = results.max_von_mises
    structural, structural_location, zone = structural_peak(results, points, inputs)

    return ResultSummary(
        max_displacement=results.max_displacement,
        tip_deflection=tip_deflection,
        max_von_mises=peak,
        peak_location=peak_stress_location(results, points),
        section=section,
        reactions=reactions,
        # K_t is taken on the structural peak. Against the raw peak it would be
        # a ratio of a mesh-dependent number to a closed-form one, which says
        # more about the mesh than about the bracket.
        stress_concentration=(
            structural / reference.root_stress
            if reference.root_stress > 0.0
            else float("nan")
        ),
        factor_of_safety_peak=factor_of_safety(material.yield_strength, peak),
        factor_of_safety_section=factor_of_safety(
            material.yield_strength, section.magnitude
        ),
        max_von_mises_structural=structural,
        structural_location=structural_location,
        factor_of_safety_structural=factor_of_safety(
            material.yield_strength, structural
        ),
        restraint_zone=zone,
    )


# --- Is the answer right? -------------------------------------------------


def check_equilibrium(
    reactions: np.ndarray,
    applied_load: float,
    rel_tol: float = EQUILIBRIUM_REL_TOL,
) -> CheckResult:
    """Reactions must balance the applied load.

    The most fundamental check there is: if the forces do not sum to zero, the
    model is not in equilibrium and nothing else it says is worth reading. It
    also catches a load that was applied to the wrong face or in the wrong
    direction, which nothing else here would notice.
    """
    vertical = float(reactions[2])
    lateral = float(np.hypot(reactions[0], reactions[1]))

    error = abs(vertical - applied_load) / applied_load
    lateral_error = lateral / applied_load
    passed = error <= rel_tol and lateral_error <= rel_tol

    return CheckResult(
        name="Equilibrium",
        passed=passed,
        message=(
            f"Reactions [{reactions[0]:.3e}, {reactions[1]:.3e}, "
            f"{vertical:.6f}] N against {applied_load:.3f} N applied; "
            f"vertical error {error:.2e}, lateral {lateral_error:.2e} "
            f"(tolerance {rel_tol:.0e})."
        ),
        value=vertical,
        expected=applied_load,
    )


def check_tip_deflection(
    fe_deflection: float,
    reference: AnalyticalReference,
    tol: float = DEFLECTION_FLOOR_TOL,
) -> CheckResult:
    """The rigid-root solutions are a LOWER BOUND on the FE tip deflection.

    Engineering decision 3, restated for the bolted restraint. Both closed
    forms assume the arm grows out of a rigid wall. The bracket does not: it is
    held by four washer-sized rings on a 4 mm plate, and that plate bends and
    rotates. A support can only ever add compliance, never remove it, so

        FE deflection >= the stiffer closed form, always.

    That makes this a one-sided check, and a sharper one than the old band.
    The band could be satisfied by two errors cancelling; a result BELOW the
    rigid-root value has only one explanation, which is that the model is held
    more tightly than the bracket is - too many restrained nodes, a restraint
    on the wrong face, or an annulus that has swallowed the whole rear face.

    The excess over the bound is not an error to be minimised. It is the base
    flexibility, and it is reported as a compliance ratio because it is the
    single number that says how much the mounting is contributing.

    The tolerance covers discretisation only, and the check is deliberately
    weak at separating restraints of similar stiffness: on this geometry a
    fully fixed rear face clears the floor by 1.7%. It catches a restraint
    stiffer than a rigid wall, which is unphysical; the detected face area and
    the equation count are what catch the wrong restraint.
    """
    plate_bound = reference.tip_deflection_plate
    beam_bound = reference.tip_deflection_beam

    # The plate bound is the stiffer of the two and therefore the smaller
    # deflection, so it is the bound that must not be undercut.
    floor = min(plate_bound, beam_bound) * (1.0 - tol)
    passed = fe_deflection >= floor

    ratio_beam = fe_deflection / beam_bound
    ratio_plate = fe_deflection / plate_bound

    if passed:
        message = (
            f"FE {fe_deflection:.5f} mm against the rigid-root bounds: plate "
            f"(E/(1-nu^2)) {plate_bound:.5f} mm, beam (E) {beam_bound:.5f} mm. "
            f"FE/plate {ratio_plate:.4f}, FE/beam {ratio_beam:.4f}. The excess "
            "is the mounting plate flexing between its bolts, which the "
            "closed forms do not model."
        )
    else:
        message = (
            f"FE {fe_deflection:.5f} mm is BELOW the rigid-root bound "
            f"{floor:.5f} mm (stiffer bound {min(plate_bound, beam_bound):.5f} "
            f"mm with {tol:.0%} allowance). A bracket on a flexible mounting "
            "cannot be stiffer than the same bracket built into a rigid wall, "
            "so the model is over-restrained: check that the clamped annuli "
            "have not grown to cover the whole rear face."
        )

    return CheckResult(
        name="Tip deflection vs rigid-root bound",
        passed=passed,
        message=message,
        value=fe_deflection,
        expected=min(plate_bound, beam_bound),
        # Advisory: "analytical deviation beyond tolerance" is a Review in
        # a Review. Beam theory is a reference, not ground truth.
        severity=ADVISORY,
    )


def check_section_stress(
    section: SectionStress,
    reference: AnalyticalReference,
    rel_tol: float = SECTION_STRESS_REL_TOL,
) -> CheckResult:
    """Bending stress away from the root, against beam theory.

    Engineering decision 2: this is the stress comparison that validates, not
    the peak. The section is far from both the load and the restraint, so St
    Venant's principle applies and beam theory should be accurate.
    """
    fe = section.magnitude
    expected = reference.section_stress

    if expected <= 0.0:
        return CheckResult(
            name="Bending stress away from the root",
            passed=False,
            message="Beam theory gives zero stress at this section; "
            "choose a different position.",
        )

    error = abs(fe - expected) / expected
    passed = error <= rel_tol

    return CheckResult(
        name="Bending stress away from the root",
        passed=passed,
        message=(
            f"At s = {reference.section_position:.1f} mm: FE {fe:.3f} MPa "
            f"(top {section.top_mean:+.3f}, bottom {section.bottom_mean:+.3f}), "
            f"beam theory {expected:.3f} MPa, relative error {error:.2%} "
            f"(tolerance {rel_tol:.0%})."
        ),
        value=fe,
        expected=expected,
        severity=ADVISORY,
    )


def check_stress_concentration(
    summary: ResultSummary,
    reference: AnalyticalReference,
    max_plausible: float = MAX_PLAUSIBLE_KT,
) -> CheckResult:
    """Report K_t, and flag an implausible one.

    This is reported, not validated against. The peak sits in the fillet, where
    the value depends on mesh refinement and never fully converges, which is
    exactly why engineering decision 2 forbids validating on it.

    A K_t below 1 would mean the peak is under the nominal root stress, which a
    filleted corner cannot produce. A very high one usually means the peak has
    been taken inside a singular region rather than on a real feature.

    K_t is taken on the structural peak, outside the clamp's singular zone.
    Against the raw peak it would be the ratio of a mesh-dependent number to a
    closed-form one, which says more about the mesh than about the bracket.
    """
    k_t = summary.stress_concentration
    x, y, z = summary.structural_location
    px, py, pz = summary.peak_location
    passed = 1.0 <= k_t <= max_plausible

    clamp_note = (
        f" The raw peak is {summary.max_von_mises:.3f} MPa at x={px:.2f}, "
        f"y={py:.2f}, z={pz:.2f} mm, within {summary.restraint_zone:.2f} mm of "
        "a clamped edge; that one is a restraint singularity and rises without "
        "limit with refinement."
    )

    return CheckResult(
        name="Stress concentration K_t",
        passed=passed,
        message=(
            f"K_t = {k_t:.3f} (structural peak "
            f"{summary.max_von_mises_structural:.3f} MPa at x={x:.2f}, "
            f"y={y:.2f}, z={z:.2f} mm; beam root stress "
            f"{reference.root_stress:.3f} MPa). Reported, not validated "
            "against: the fillet peak is mesh dependent." + clamp_note
            if passed
            else f"K_t = {k_t:.3f} is outside the plausible range 1 to "
            f"{max_plausible:.0f} (structural peak "
            f"{summary.max_von_mises_structural:.3f} MPa at x={x:.2f}, "
            f"y={y:.2f}, z={z:.2f} mm). A very high value usually means the "
            "singular zone around the clamped rings is too small and the peak "
            "is still being taken inside it." + clamp_note
        ),
        value=k_t,
        # Advisory: "high stress only at a fixed-edge or fillet
        # singularity region" under Review. The peak is reported, never
        # validated against.
        severity=ADVISORY,
    )


# --- Is the design good? --------------------------------------------------


def check_factor_of_safety(summary: ResultSummary, target: float) -> CheckResult:
    """Factor of safety on the structural peak, against the target.

    It has to be the structural peak. The raw peak now sits at the edge of a
    clamped ring, where restraining a sharp edge of a continuum produces a
    stress that climbs with every refinement and never settles. A verdict
    driven by that number would change every time the mesh changed, which is
    the opposite of what a verdict is for.

    The raw peak and the away-from-root value are both reported alongside, so
    the number that was set aside stays visible and a reader can disagree with
    the judgement rather than having it hidden from them.
    """
    fos = summary.factor_of_safety_structural
    passed = fos >= target

    context = (
        f"Raw peak FoS is {summary.factor_of_safety_peak:.3f} at the clamped "
        f"edge (set aside as a restraint singularity); away from the root the "
        f"FoS is {summary.factor_of_safety_section:.3f}."
    )

    return CheckResult(
        name="Factor of safety",
        passed=passed,
        message=(
            f"FoS {fos:.3f} on the structural peak "
            f"{summary.max_von_mises_structural:.3f} MPa, against a target of "
            f"{target:.2f}. " + context
            if passed
            else f"FoS {fos:.3f} on the structural peak "
            f"{summary.max_von_mises_structural:.3f} MPa is below the target "
            f"of {target:.2f}. " + context + " The design does not meet its "
            "own criterion: reduce the load, increase the thickness, enlarge "
            "the washers, or choose a stronger material."
        ),
        value=fos,
        expected=target,
    )


def run_result_checks(
    summary: ResultSummary,
    reference: AnalyticalReference,
    applied_load: float,
    target_factor_of_safety: float,
) -> list[CheckResult]:
    """Every result check, in order: correctness first, then adequacy."""
    return [
        check_equilibrium(summary.reactions, applied_load),
        check_tip_deflection(summary.tip_deflection, reference),
        check_section_stress(summary.section, reference),
        check_stress_concentration(summary, reference),
        check_factor_of_safety(summary, target_factor_of_safety),
    ]


# --- Verdict --------------------------------------------------------------


class Verdict(str, Enum):
    """The single answer a run reduces to.

    The three outcomes:
      PASS   - every check is satisfied and FoS >= target.
      REVIEW - the run is usable but something needs a human eye: a marginal
               mesh, a deviation from theory beyond tolerance, or a stress peak
               that may be a singularity.
      FAIL   - invalid inputs, a CAD/mesh/solver failure, missing surfaces,
               equilibrium failure, or FoS below target.
    """

    PASS = "Pass"
    REVIEW = "Review"
    FAIL = "Fail"


@dataclass(frozen=True)
class VerdictResult:
    """The verdict and the specific reasons behind it."""

    verdict: Verdict
    failures: tuple[str, ...] = ()
    advisories: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def headline(self) -> str:
        if self.verdict is Verdict.PASS:
            return "Pass - every check satisfied."
        if self.verdict is Verdict.FAIL:
            return f"Fail - {len(self.failures)} critical issue(s)."

        count = len(self.advisories) + len(self.warnings)
        return f"Review - {count} item(s) need an engineer's judgement."

    def __str__(self) -> str:
        return self.headline


def decide_verdict(
    checks: list[CheckResult],
    input_warnings: list[str] | None = None,
) -> VerdictResult:
    """Reduce every check and warning to one verdict.

    A critical failure always wins: if the model is not in equilibrium, a
    marginal mesh is beside the point. Advisory failures and input warnings
    both produce Review, because both mean the run is usable but questionable.

    Note that the verdict never upgrades itself. There is no combination of
    passes that turns a critical failure into a Review.
    """
    input_warnings = list(input_warnings or [])

    failures = tuple(
        str(check) for check in checks if not check.passed and not check.is_advisory
    )
    advisories = tuple(
        str(check) for check in checks if not check.passed and check.is_advisory
    )

    if failures:
        verdict = Verdict.FAIL
    elif advisories or input_warnings:
        verdict = Verdict.REVIEW
    else:
        verdict = Verdict.PASS

    return VerdictResult(
        verdict=verdict,
        failures=failures,
        advisories=advisories,
        warnings=tuple(input_warnings),
    )
