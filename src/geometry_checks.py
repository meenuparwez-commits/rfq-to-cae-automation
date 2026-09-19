"""Independent checks on the bracket solid.

Kept separate from cad_generator on purpose: a builder that validates its own
output tends to confirm its own assumptions. These functions recompute what
the geometry *should* be from first principles and compare.

Units: mm throughout.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import cadquery as cq

if TYPE_CHECKING:  # type hints only; avoids a runtime import cycle
    from src.schemas import BracketInputs

# Tolerance is set from what has to be detected, not from what the kernel
# currently achieves. The smallest feature this check must catch is a missing
# fillet, which is r^2(1 - pi/4) = 5.365 mm^2 of a 725.365 mm^2 section, i.e.
# 0.74% of the volume - roughly 74 times this threshold.
#
# Pinning it tighter (1e-6) would pass today at zero error, then risk turning
# red on a CadQuery/OCCT upgrade that shifts volume integration in the last
# digits. A false Fail verdict on a sound bracket costs more than this margin,
# and the fillet geometry is separately verified against its closed form in
# tests/test_cad_generator.py.
VOLUME_REL_TOL = 1e-4

# Bounding box comparison, in mm.
BBOX_ABS_TOL = 1e-6


# Severity of a failed check, which decides the overall verdict.
#
#   critical - the result cannot be believed, or the design does not meet its
#              criterion. Drives a Fail.
#   advisory - the run is usable but something needs a human eye: a mesh that
#              is marginal, a deviation from theory beyond tolerance, a stress
#              peak that may be a singularity. Drives a Review.
#
# Collapsing the two would either cry wolf on every marginal mesh or hide a
# model that is not in equilibrium.
CRITICAL = "critical"
ADVISORY = "advisory"


@dataclass(frozen=True)
class CheckResult:
    """Outcome of a single engineering check."""

    name: str
    passed: bool
    message: str
    value: float | None = None
    expected: float | None = None
    severity: str = CRITICAL

    @property
    def is_advisory(self) -> bool:
        return self.severity == ADVISORY

    def __str__(self) -> str:
        if self.passed:
            status = "PASS"
        else:
            status = "FAIL" if self.severity == CRITICAL else "WARN"

        return f"[{status}] {self.name}: {self.message}"


def analytical_volume(
    plate_height: float,
    arm_length: float,
    width: float,
    thickness: float,
    fillet_radius: float,
    hole_diameter: float = 0.0,
    num_holes: int = 0,
) -> float:
    """Volume of the bracket by hand calculation, in mm^3.

    The L cross-section is the plate rectangle plus the arm rectangle. The
    fillet sits in a concave corner, so it *adds* the material between the
    square corner and the quarter circle: r^2 - pi*r^2/4.

        A = t*H + L*t + r^2 * (1 - pi/4)
        V = A * b  -  n * pi * (d/2)^2 * t

    The hole term assumes every hole passes through the plate thickness and
    nothing else. That holds because the schema forbids holes from reaching
    into the fillet region, where the material is thicker than t.
    """
    plate_area = thickness * plate_height
    arm_area = arm_length * thickness
    fillet_area = fillet_radius**2 * (1.0 - math.pi / 4.0)

    gross = (plate_area + arm_area + fillet_area) * width
    holes = num_holes * math.pi * (hole_diameter / 2.0) ** 2 * thickness

    return gross - holes


def _all_solids(bracket: cq.Workplane) -> list[cq.Solid]:
    """Every solid on the Workplane stack.

    `Workplane.val()` returns only the first object on the stack, so counting
    solids through it silently misses disconnected bodies that sit alongside
    each other rather than inside one compound. `vals()` sees all of them.
    """
    return [solid for shape in bracket.vals() for solid in shape.Solids()]


def check_is_valid_solid(bracket: cq.Workplane) -> CheckResult:
    """The CAD kernel considers every shape geometrically valid."""
    shapes = bracket.vals()
    invalid = [index for index, shape in enumerate(shapes) if not shape.isValid()]
    passed = bool(shapes) and not invalid

    if not shapes:
        message = "No geometry was produced."
    elif passed:
        message = "Solid is valid."
    else:
        message = (
            f"CAD kernel reports {len(invalid)} invalid shape(s); downstream "
            "meshing would fail."
        )

    return CheckResult(name="CAD validity", passed=passed, message=message)


def check_single_solid(bracket: cq.Workplane) -> CheckResult:
    """Exactly one connected solid.

    Two solids would mean the plate and arm never fused. Meshing would still
    succeed and the stress result would be nonsense, so this is checked before
    anything is believed.
    """
    count = len(_all_solids(bracket))
    passed = count == 1

    return CheckResult(
        name="Single connected volume",
        passed=passed,
        message=(
            "Bracket is one connected solid."
            if passed
            else f"Expected 1 connected solid, found {count}; the plate and arm "
            "are not fused."
        ),
        value=float(count),
        expected=1.0,
    )


def check_volume(
    bracket: cq.Workplane,
    plate_height: float,
    arm_length: float,
    width: float,
    thickness: float,
    fillet_radius: float,
    hole_diameter: float = 0.0,
    num_holes: int = 0,
    rel_tol: float = VOLUME_REL_TOL,
) -> CheckResult:
    """CAD volume against the hand calculation.

    This is the check that catches a wrong coordinate convention, a missing
    fillet, or an arm measured from the wrong face, because all of those change
    the volume.
    """
    actual = sum(solid.Volume() for solid in _all_solids(bracket))
    expected = analytical_volume(
        plate_height=plate_height,
        arm_length=arm_length,
        width=width,
        thickness=thickness,
        fillet_radius=fillet_radius,
        hole_diameter=hole_diameter,
        num_holes=num_holes,
    )

    error = abs(actual - expected) / expected
    passed = error <= rel_tol

    return CheckResult(
        name="Volume vs hand calculation",
        passed=passed,
        message=(
            f"CAD {actual:.4f} mm^3 vs hand calculation {expected:.4f} mm^3, "
            f"relative error {error:.2e} (tolerance {rel_tol:.0e})."
        ),
        value=actual,
        expected=expected,
    )


def _cylindrical_hole_faces(
    bracket: cq.Workplane, expected_radius: float, tol: float = 1e-6
) -> list[cq.Face]:
    """Cylindrical faces of the given radius whose axis runs along x.

    The inside fillet is cylindrical too, so radius alone is not a safe
    discriminator: a bracket whose fillet radius happened to equal the hole
    radius would report one hole too many. The axis direction separates them
    reliably, because the holes are drilled along x and the fillet runs along y.
    """
    from OCP.BRepAdaptor import BRepAdaptor_Surface

    faces = []
    for face in bracket.val().Faces():
        if face.geomType() != "CYLINDER":
            continue

        adaptor = BRepAdaptor_Surface(face.wrapped)
        cylinder = adaptor.Cylinder()

        if abs(cylinder.Radius() - expected_radius) > tol:
            continue

        axis = cylinder.Axis().Direction()
        if abs(abs(axis.X()) - 1.0) > 1e-6:
            continue

        faces.append(face)

    return faces


def check_hole_count(
    bracket: cq.Workplane, hole_diameter: float, num_holes: int
) -> CheckResult:
    """The expected number of bolt holes is actually present.

    The volume check would catch holes that are missing entirely, but not a
    hole cut in the wrong place, or two holes merged into one opening. Counting
    the cylindrical faces directly is a different kind of evidence.
    """
    expected_radius = hole_diameter / 2.0
    found = len(_cylindrical_hole_faces(bracket, expected_radius))
    passed = found == num_holes

    return CheckResult(
        name="Hole count",
        passed=passed,
        message=(
            f"Found {found} hole(s) of diameter {hole_diameter:.3f} mm."
            if passed
            else f"Expected {num_holes} hole(s) of diameter {hole_diameter:.3f} "
            f"mm, found {found}. Holes may have merged, broken out of an edge, "
            "or not been cut."
        ),
        value=float(found),
        expected=float(num_holes),
    )


def check_bounding_box(
    bracket: cq.Workplane,
    plate_height: float,
    arm_length: float,
    width: float,
    thickness: float,
    abs_tol: float = BBOX_ABS_TOL,
) -> CheckResult:
    """Overall extents and origin placement.

    Volume alone cannot detect a solid built in the wrong place or the wrong
    orientation. Boundary detection locates the fixed face at x = 0 by
    coordinate, so the
    origin convention has to be confirmed, not assumed.
    """
    box = bracket.val().BoundingBox()

    expected = {
        "xmin": 0.0,
        "xmax": thickness + arm_length,
        "ymin": -width / 2.0,
        "ymax": width / 2.0,
        "zmin": 0.0,
        "zmax": plate_height,
    }
    actual = {
        "xmin": box.xmin,
        "xmax": box.xmax,
        "ymin": box.ymin,
        "ymax": box.ymax,
        "zmin": box.zmin,
        "zmax": box.zmax,
    }

    discrepancies = [
        f"{key}: got {actual[key]:.6f}, expected {value:.6f}"
        for key, value in expected.items()
        if abs(actual[key] - value) > abs_tol
    ]
    passed = not discrepancies

    return CheckResult(
        name="Bounding box and origin",
        passed=passed,
        message=(
            f"Extents correct: x 0 to {expected['xmax']:.3f}, "
            f"y {expected['ymin']:.3f} to {expected['ymax']:.3f}, "
            f"z 0 to {expected['zmax']:.3f} mm."
            if passed
            else "Bounding box wrong - " + "; ".join(discrepancies)
        ),
    )


def check_step_roundtrip(
    step_path: str | Path,
    original: cq.Workplane,
    rel_tol: float = VOLUME_REL_TOL,
) -> CheckResult:
    """The exported STEP file re-imports and still has the same volume.

    A STEP file can exist, be non-empty, and still be unusable. Gmsh reads this
    file when meshing, so it is read back here while the failure is still cheap
    to diagnose.
    """
    from src import cad_generator  # local import keeps the modules decoupled

    step_path = Path(step_path)

    try:
        reimported = cad_generator.import_step(step_path)
    except Exception as exc:  # noqa: BLE001 - reported, not swallowed
        return CheckResult(
            name="STEP export re-imports",
            passed=False,
            message=f"STEP file at {step_path} could not be read back: {exc}",
        )

    original_volume = original.val().Volume()
    reimported_volume = reimported.val().Volume()
    error = abs(reimported_volume - original_volume) / original_volume
    passed = error <= rel_tol

    return CheckResult(
        name="STEP export re-imports",
        passed=passed,
        message=(
            f"Re-imported volume {reimported_volume:.4f} mm^3, relative "
            f"difference {error:.2e} (tolerance {rel_tol:.0e})."
        ),
        value=reimported_volume,
        expected=original_volume,
    )


def run_all_checks(
    bracket: cq.Workplane,
    plate_height: float,
    arm_length: float,
    width: float,
    thickness: float,
    fillet_radius: float,
    hole_diameter: float = 0.0,
    num_holes: int = 0,
    step_path: str | Path | None = None,
) -> list[CheckResult]:
    """Run every geometry check and return the results in order."""
    results = [
        check_is_valid_solid(bracket),
        check_single_solid(bracket),
        check_volume(
            bracket,
            plate_height=plate_height,
            arm_length=arm_length,
            width=width,
            thickness=thickness,
            fillet_radius=fillet_radius,
            hole_diameter=hole_diameter,
            num_holes=num_holes,
        ),
        check_bounding_box(
            bracket,
            plate_height=plate_height,
            arm_length=arm_length,
            width=width,
            thickness=thickness,
        ),
    ]

    if num_holes:
        results.append(check_hole_count(bracket, hole_diameter, num_holes))

    if step_path is not None:
        results.append(check_step_roundtrip(step_path, bracket))

    return results


def run_checks_for_inputs(
    bracket: cq.Workplane,
    inputs: "BracketInputs",
    step_path: str | Path | None = None,
) -> list[CheckResult]:
    """Run every geometry check against a validated BracketInputs."""
    return run_all_checks(
        bracket,
        plate_height=inputs.plate_height,
        arm_length=inputs.arm_length,
        width=inputs.width,
        thickness=inputs.thickness,
        fillet_radius=inputs.fillet_radius,
        hole_diameter=inputs.hole_diameter,
        num_holes=inputs.num_holes,
        step_path=step_path,
    )
