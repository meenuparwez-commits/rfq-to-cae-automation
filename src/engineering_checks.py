"""Judge whether the FE results are trustworthy, and whether the design passes.

Units: mm, N, MPa.

Two different questions live here and must not be confused:

    Is the answer right?    equilibrium, deflection against beam theory,
                            bending stress away from the root
    Is the design good?     factor of safety against the target

A solve that finishes proves neither, so every check here compares against
something computed independently.

The peak stress is deliberately not a validation target. It sits in the fillet
stress concentration, where the value depends on mesh refinement and never
fully converges. Validation uses tip deflection and bending stress away from
the root; the peak is reported as K_t = sigma_FE,peak / sigma_beam,root.
See docs/engineering-notes.md.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from enum import Enum

from src.analytical import AnalyticalReference
from src.geometry_checks import ADVISORY, CheckResult
from src.result_reader import Results, SectionStress
from src.schemas import Material

# A linear static solve does far better than 1%: the baseline returns the
# applied load to 1e-9.
EQUILIBRIUM_REL_TOL = 0.01

# Away from the load and the restraint, St Venant applies and beam theory
# should be accurate. The baseline achieves 0.14% at mid-span; 5% leaves room
# for the coarser meshes in the convergence study.
SECTION_STRESS_REL_TOL = 0.05

# Allowance either side of the beam/plate stiffness band. The band itself is
# physical (a wide section bends more like a plate, stiffer by 1/(1-nu^2)), so
# the FE deflection is expected between the two. The allowance covers the
# mounting plate's own flexibility, which beam theory treats as a rigid wall.
DEFLECTION_BAND_TOL = 0.15

# A stress concentration below 1 would mean the peak is lower than the nominal
# root stress, which is not physical for a filleted corner. Above this, the
# peak is more likely a restraint singularity than a real feature.
MAX_PLAUSIBLE_KT = 5.0


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


def peak_stress_location(results: Results, points: np.ndarray) -> tuple[float, float, float]:
    """Coordinates of the highest von Mises stress, mm.

    Worth reporting rather than just the value: a peak in the fillet is a real
    stress concentration, while a peak on the edge of the fixed face is a
    restraint singularity and must not drive a verdict on its own.
    """
    index = int(np.argmax(results.von_mises))
    x, y, z = points[index]

    return float(x), float(y), float(z)


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
) -> ResultSummary:
    """Collect every headline number from one run."""
    peak = results.max_von_mises

    return ResultSummary(
        max_displacement=results.max_displacement,
        tip_deflection=tip_deflection,
        max_von_mises=peak,
        peak_location=peak_stress_location(results, points),
        section=section,
        reactions=reactions,
        stress_concentration=(
            peak / reference.root_stress if reference.root_stress > 0.0 else float("nan")
        ),
        factor_of_safety_peak=factor_of_safety(material.yield_strength, peak),
        factor_of_safety_section=factor_of_safety(
            material.yield_strength, section.magnitude
        ),
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
    tol: float = DEFLECTION_BAND_TOL,
) -> CheckResult:
    """FE tip deflection should fall between the plate and beam bounds.

    Engineering decision 3. The plate bound uses E/(1-nu^2) and is the stiffer
    of the two, so it is the LOWER deflection; the beam bound uses E and is the
    higher. A wide section (b/t = 15 here) behaves more like a plate, so the FE
    result is expected near the plate bound, with the mounting plate's own
    flexibility pushing it back towards the beam value.

    This is a physical band rather than an arbitrary percentage, which is why
    the tolerance only has to cover what sits outside the physics.
    """
    lower = reference.tip_deflection_plate * (1.0 - tol)
    upper = reference.tip_deflection_beam * (1.0 + tol)
    passed = lower <= fe_deflection <= upper

    ratio_beam = fe_deflection / reference.tip_deflection_beam
    ratio_plate = fe_deflection / reference.tip_deflection_plate

    where = (
        "between the bounds"
        if reference.tip_deflection_plate
        <= fe_deflection
        <= reference.tip_deflection_beam
        else "outside the bounds"
    )

    return CheckResult(
        name="Tip deflection vs beam theory",
        passed=passed,
        message=(
            f"FE {fe_deflection:.5f} mm, {where}: plate (E/(1-nu^2)) "
            f"{reference.tip_deflection_plate:.5f} mm, beam (E) "
            f"{reference.tip_deflection_beam:.5f} mm. "
            f"FE/beam {ratio_beam:.4f}, FE/plate {ratio_plate:.4f}."
            if passed
            else f"FE {fe_deflection:.5f} mm lies outside the accepted band "
            f"{lower:.5f} to {upper:.5f} mm, set by the plate bound "
            f"{reference.tip_deflection_plate:.5f} and beam bound "
            f"{reference.tip_deflection_beam:.5f} mm with {tol:.0%} allowance."
        ),
        value=fe_deflection,
        expected=reference.tip_deflection_beam,
        # Advisory rather than critical: beam theory is a reference, not ground
        # truth, and the model legitimately includes effects it ignores.
        severity=ADVISORY,
    )


def check_section_stress(
    section: SectionStress,
    reference: AnalyticalReference,
    rel_tol: float = SECTION_STRESS_REL_TOL,
) -> CheckResult:
    """Bending stress away from the root, against beam theory.

    This is the stress comparison that validates, not the peak. The section is
    far from both the load and the restraint, so St Venant's principle applies
    and beam theory should be accurate.
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
    exactly why it is not a validation target.

    A K_t below 1 would mean the peak is under the nominal root stress, which a
    filleted corner cannot produce. A very high one usually means the peak is a
    restraint singularity at the edge of the fixed face rather than a real
    feature.
    """
    k_t = summary.stress_concentration
    x, y, z = summary.peak_location
    passed = 1.0 <= k_t <= max_plausible

    return CheckResult(
        name="Stress concentration K_t",
        passed=passed,
        message=(
            f"K_t = {k_t:.3f} (peak {summary.max_von_mises:.3f} MPa at "
            f"x={x:.2f}, y={y:.2f}, z={z:.2f} mm; beam root stress "
            f"{reference.root_stress:.3f} MPa). Reported, not validated "
            "against: the fillet peak is mesh dependent."
            if passed
            else f"K_t = {k_t:.3f} is outside the plausible range 1 to "
            f"{max_plausible:.0f} (peak {summary.max_von_mises:.3f} MPa at "
            f"x={x:.2f}, y={y:.2f}, z={z:.2f} mm). A very high value usually "
            "means the peak is a restraint singularity at the edge of the "
            "fixed face, not a real stress concentration."
        ),
        value=k_t,
        # Advisory: a high peak in a singularity region needs a human eye, not
        # an automatic rejection. The peak is reported, never validated against.
        severity=ADVISORY,
    )


# --- Is the design good? --------------------------------------------------


def check_factor_of_safety(summary: ResultSummary, target: float) -> CheckResult:
    """Factor of safety on the peak stress, against the target.

    Reported on the peak, which is the conservative choice, with the
    away-from-root value alongside. A design failing only on the fillet peak
    may still be acceptable once the concentration is assessed properly, but
    that is an engineering judgement this tool does not make.
    """
    peak_fos = summary.factor_of_safety_peak
    passed = peak_fos >= target

    return CheckResult(
        name="Factor of safety",
        passed=passed,
        message=(
            f"FoS {peak_fos:.3f} on the peak von Mises "
            f"{summary.max_von_mises:.3f} MPa, against a target of "
            f"{target:.2f}. Away from the root the FoS is "
            f"{summary.factor_of_safety_section:.3f}."
            if passed
            else f"FoS {peak_fos:.3f} on the peak von Mises "
            f"{summary.max_von_mises:.3f} MPa is below the target of "
            f"{target:.2f}. Away from the root the FoS is "
            f"{summary.factor_of_safety_section:.3f}. The design does not meet "
            "its own criterion: reduce the load, increase the thickness, or "
            "choose a stronger material."
        ),
        value=peak_fos,
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
