"""Closed-form cantilever results to compare the FE answer against.

Units: mm, N, MPa.

The arm is treated as a cantilever of free length L, measured from the front
face of the mounting plate, with second moment of area I = b*t^3/12
(engineering decision 1). The section coordinate s runs from the root at s = 0
to the tip at s = L.

Two load cases, and each must match the load the FE model actually carries
(engineering decision 1): comparing a tip load against a UDL formula
disagrees by a factor of two and looks exactly like an FE error.

    tip_load  M(s) = F (L - s)            delta_tip = F L^3 / (3 E I)
    udl       load spread over s in [c, L], total F

For the UDL the FE model cannot load the full length: the flat top of the arm
begins where the fillet becomes tangent, at s = c = r. Formulas below take that
start position rather than assuming c = 0, because on the baseline geometry
(L = 80, r = 5) the difference in root moment is 6.25%, which would otherwise
be mistaken for an FE error. The textbook c = 0 values are also provided, so a
report can show both and make the distinction visible.

Wide-section stiffness (engineering decision 3): a section with b >> t bends
more like a plate than a beam, which is stiffer by 1/(1 - nu^2). Deflection is
therefore reported with both E (beam, more flexible) and E/(1 - nu^2) (plate,
stiffer). The FE result is expected near or between them, with extra
flexibility from the mounting plate itself, which is not the rigid wall beam
theory assumes.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.schemas import BracketInputs, LoadCase, Material


def second_moment_of_area(width: float, thickness: float) -> float:
    """I = b t^3 / 12, mm^4."""
    if width <= 0.0 or thickness <= 0.0:
        raise ValueError("width and thickness must be positive.")

    return width * thickness**3 / 12.0


def plate_modulus(youngs_modulus: float, poissons_ratio: float) -> float:
    """E / (1 - nu^2), the effective modulus in cylindrical bending, MPa."""
    return youngs_modulus / (1.0 - poissons_ratio**2)


def bending_stress(moment: float, width: float, thickness: float) -> float:
    """sigma = 6 M / (b t^2), MPa. Surface stress of a rectangular section."""
    if width <= 0.0 or thickness <= 0.0:
        raise ValueError("width and thickness must be positive.")

    return 6.0 * moment / (width * thickness**2)


def moment_at(
    load_case: LoadCase,
    total_load: float,
    arm_length: float,
    section: float,
    load_start: float = 0.0,
) -> float:
    """Bending moment at section s, N.mm. Hogging positive.

    Args:
        load_case: tip load or UDL.
        total_load: F, N.
        arm_length: L, mm.
        section: s, distance from the root, mm.
        load_start: c, where distributed load begins. Ignored for a tip load.
    """
    if not 0.0 <= section <= arm_length:
        raise ValueError(
            f"section {section} must lie between 0 and arm_length {arm_length}."
        )

    if load_case is LoadCase.TIP_LOAD:
        return total_load * (arm_length - section)

    span = arm_length - load_start
    if span <= 0.0:
        raise ValueError(
            f"load_start {load_start} must be less than arm_length {arm_length}."
        )

    if section <= load_start:
        # The whole distributed load acts beyond this section; its resultant
        # sits at the midpoint of the loaded span.
        return total_load * ((arm_length + load_start) / 2.0 - section)

    # Only the part of the load beyond s contributes.
    intensity = total_load / span
    return intensity * (arm_length - section) ** 2 / 2.0


def tip_deflection(
    load_case: LoadCase,
    total_load: float,
    arm_length: float,
    youngs_modulus: float,
    second_moment: float,
    load_start: float = 0.0,
) -> float:
    """Deflection at the tip, mm, positive downwards.

    For the UDL the load runs from s = c to s = L. The result comes from
    integrating M(s)(L - s)/EI over the length, and reduces to the textbook
    F L^3 / (8 E I) when c = 0, which is asserted in the tests.
    """
    stiffness = youngs_modulus * second_moment
    if stiffness <= 0.0:
        raise ValueError("E and I must be positive.")

    if load_case is LoadCase.TIP_LOAD:
        return total_load * arm_length**3 / (3.0 * stiffness)

    span = arm_length - load_start
    if span <= 0.0:
        raise ValueError(
            f"load_start {load_start} must be less than arm_length {arm_length}."
        )

    centroid = (arm_length + load_start) / 2.0
    unloaded = (
        centroid * arm_length * load_start
        - centroid * load_start**2 / 2.0
        - arm_length * load_start**2 / 2.0
        + load_start**3 / 3.0
    )
    loaded = span**3 / 8.0

    return total_load * (unloaded + loaded) / stiffness


@dataclass(frozen=True)
class AnalyticalReference:
    """Closed-form values the FE result is compared against."""

    load_case: LoadCase
    total_load: float
    arm_length: float
    load_start: float
    width: float
    thickness: float
    second_moment: float

    root_moment: float
    root_stress: float

    section_position: float
    section_moment: float
    section_stress: float

    tip_deflection_beam: float
    tip_deflection_plate: float

    textbook_root_stress: float
    textbook_tip_deflection: float

    @property
    def load_start_matters(self) -> bool:
        """True when the modelled load start shifts the result appreciably."""
        if self.textbook_root_stress == 0.0:
            return False

        shift = abs(self.root_stress - self.textbook_root_stress)
        return shift / self.textbook_root_stress > 0.005


def compute_reference(
    inputs: BracketInputs,
    material: Material,
    load_start: float,
    section_position: float | None = None,
) -> AnalyticalReference:
    """Build the analytical reference for a given model.

    Args:
        inputs: validated bracket inputs.
        material: resolved material properties.
        load_start: where the distributed load actually begins, from
            boundary_detection.loaded_length(). Zero for a tip load.
        section_position: where to compare bending stress, measured from the
            root. Defaults to mid-span, which engineering decision 2 requires:
            the root peak sits in the fillet stress concentration and must not
            be used for validation.
    """
    width = inputs.width
    thickness = inputs.thickness
    length = inputs.arm_length
    load = inputs.applied_load
    case = inputs.load_case

    if section_position is None:
        section_position = length / 2.0

    second_moment = second_moment_of_area(width, thickness)

    root_moment = moment_at(case, load, length, 0.0, load_start)
    section_moment = moment_at(case, load, length, section_position, load_start)

    textbook_root_moment = moment_at(case, load, length, 0.0, 0.0)

    return AnalyticalReference(
        load_case=case,
        total_load=load,
        arm_length=length,
        load_start=load_start,
        width=width,
        thickness=thickness,
        second_moment=second_moment,
        root_moment=root_moment,
        root_stress=bending_stress(root_moment, width, thickness),
        section_position=section_position,
        section_moment=section_moment,
        section_stress=bending_stress(section_moment, width, thickness),
        tip_deflection_beam=tip_deflection(
            case, load, length, material.youngs_modulus, second_moment, load_start
        ),
        tip_deflection_plate=tip_deflection(
            case,
            load,
            length,
            plate_modulus(material.youngs_modulus, material.poissons_ratio),
            second_moment,
            load_start,
        ),
        textbook_root_stress=bending_stress(textbook_root_moment, width, thickness),
        textbook_tip_deflection=tip_deflection(
            case, load, length, material.youngs_modulus, second_moment, 0.0
        ),
    )
