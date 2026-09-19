"""Validated inputs for the bracket pipeline (Pydantic v2).

This is the gatekeeper. Nothing downstream should ever receive a dimension it
has to re-check, and no impossible geometry should reach CadQuery, where the
failure would surface as an opaque kernel error.

Units: mm, N, MPa (N/mm^2), tonne/mm^3 for density.

Two severities, deliberately kept apart:

    errors   - the design is impossible or the analysis would be invalid.
               Raised as ValidationError; nothing is built.
    warnings - legal, but questionable. Returned by `warnings()` and intended
               to drive a Review verdict rather than a Fail.

Collapsing the two would either block valid designs or hide real problems.

Safety-critical inputs (dimensions, material, load, load case, mesh size,
target factor of safety) have no defaults on purpose. A missing load must fail
loudly, not quietly become zero.
"""

from __future__ import annotations

import json
import math
from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Ratio of edge distance (hole centre to nearest edge) to hole diameter below
# which a warning is raised. A common design guideline; not a standard.
MIN_EDGE_DISTANCE_RATIO = 1.5

# Ligament between adjacent holes, as a multiple of hole diameter.
MIN_LIGAMENT_RATIO = 1.0

# Slenderness below which engineer's beam theory becomes a poor reference,
# because shear deflection and end effects stop being negligible.
MIN_SLENDERNESS_RATIO = 10.0

# Working rule from engineering decision 5: at least 2 elements through the
# thickness means an element no larger than half the thickness.
MAX_MESH_SIZE_FRACTION_OF_THICKNESS = 0.5

DEFAULT_MATERIALS_PATH = Path("config/materials.json")


class LoadCase(str, Enum):
    """Load cases, each matched to a closed-form reference in analytical.py.

    The names matter: engineering decision 1 requires the applied load and the
    analytical formula to describe the same thing. A tip load compared against
    a UDL formula would disagree by a factor of two and look like an FE error.
    """

    TIP_LOAD = "tip_load"
    UDL = "udl"


class Material(BaseModel):
    """Linear elastic material properties."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    youngs_modulus: float = Field(gt=0.0, description="E, MPa")
    poissons_ratio: float = Field(
        gt=0.0,
        lt=0.5,
        description="nu. At exactly 0.5 the material is incompressible and the "
        "displacement formulation becomes singular.",
    )
    yield_strength: float = Field(gt=0.0, description="MPa")
    density: float = Field(gt=0.0, description="tonne/mm^3, e.g. 7.85e-9 steel")


def load_materials(path: str | Path = DEFAULT_MATERIALS_PATH) -> dict[str, Material]:
    """Read and validate the material library.

    Raises:
        FileNotFoundError: if the library is missing.
        ValueError: if it cannot be parsed or a material is invalid.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(
            f"Material library not found at {path}. The pipeline cannot pick a "
            "material for you: E, nu, yield strength and density all change the "
            "result."
        )

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Material library at {path} is not valid JSON: {exc}") from exc

    entries = raw.get("materials")
    if not isinstance(entries, dict) or not entries:
        raise ValueError(
            f"Material library at {path} has no 'materials' object, or it is empty."
        )

    return {key: Material(**value) for key, value in entries.items()}


def resolve_material(
    name: str, materials: dict[str, Material] | None = None
) -> Material:
    """Look a material up by key, listing the options when it is not found."""
    library = load_materials() if materials is None else materials

    if name not in library:
        available = ", ".join(sorted(library))
        raise ValueError(
            f"Unknown material '{name}'. Available materials: {available}."
        )

    return library[name]


class BracketInputs(BaseModel):
    """Every input needed to build and analyse one bracket.

    Geometry (see cad_generator for the coordinate system):
        plate_height   H, vertical mounting plate height
        arm_length     L, FREE arm length from the plate's front face
        width          b, width of plate and arm
        thickness      t, thickness of plate and arm
        fillet_radius  r, inside corner radius
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    # --- Geometry -------------------------------------------------------
    plate_height: float = Field(gt=0.0, description="H, mm")
    arm_length: float = Field(gt=0.0, description="L, free length, mm")
    width: float = Field(gt=0.0, description="b, mm")
    thickness: float = Field(gt=0.0, description="t, mm")
    fillet_radius: float = Field(ge=0.0, description="r, mm. 0 gives a sharp corner.")

    # --- Holes ----------------------------------------------------------
    hole_diameter: float = Field(gt=0.0, description="mm")
    hole_spacing: float = Field(
        gt=0.0,
        description="Centre-to-centre spacing, mm. Used across the width, and "
        "also vertically when there are 4 holes (square pattern).",
    )
    num_holes: Literal[2, 4]

    # --- Analysis -------------------------------------------------------
    material: str = Field(min_length=1, description="Key into config/materials.json")
    applied_load: float = Field(gt=0.0, description="Total load F, N")
    load_case: LoadCase
    mesh_size: float = Field(gt=0.0, description="Target element size, mm")
    target_factor_of_safety: float = Field(gt=0.0)

    output_dir: Path = Field(default=Path("outputs"))

    # -- Derived geometry -------------------------------------------------

    @property
    def hole_band_bottom(self) -> float:
        """Lowest z a hole may occupy: the top of the fillet region."""
        return self.thickness + self.fillet_radius

    @property
    def hole_band_top(self) -> float:
        return self.plate_height

    @property
    def hole_band_centre(self) -> float:
        return (self.hole_band_bottom + self.hole_band_top) / 2.0

    def hole_centres(self) -> list[tuple[float, float]]:
        """Hole centres as (y, z) pairs, in mm.

        There is a hole spacing input but no hole height, so the pattern is
        centred in the band between the top of the fillet and the top of the
        plate. Derived rather than invented as a hidden default.
        """
        half_spacing = self.hole_spacing / 2.0
        y_positions = (-half_spacing, half_spacing)

        if self.num_holes == 2:
            z_positions: tuple[float, ...] = (self.hole_band_centre,)
        else:
            z_positions = (
                self.hole_band_centre - half_spacing,
                self.hole_band_centre + half_spacing,
            )

        return [(y, z) for z in z_positions for y in y_positions]

    @property
    def hole_volume(self) -> float:
        """Total material removed by the holes, mm^3.

        Valid only because the holes are constrained to sit above the fillet
        region, so each one passes through the plate thickness and nothing else.
        """
        radius = self.hole_diameter / 2.0
        return self.num_holes * math.pi * radius**2 * self.thickness

    # -- Cross-field validation -------------------------------------------

    @model_validator(mode="after")
    def _check_geometry_is_possible(self) -> BracketInputs:
        errors: list[str] = []
        errors.extend(self._fillet_errors())
        errors.extend(self._hole_errors())

        if errors:
            raise ValueError(
                "The dimensions describe an impossible bracket:\n  - "
                + "\n  - ".join(errors)
            )

        return self

    def _fillet_errors(self) -> list[str]:
        problems = []

        free_plate_height = self.plate_height - self.thickness
        if free_plate_height <= 0.0:
            problems.append(
                f"plate_height ({self.plate_height} mm) must exceed thickness "
                f"({self.thickness} mm); otherwise there is no plate above the arm."
            )
        elif self.fillet_radius >= free_plate_height:
            problems.append(
                f"fillet_radius ({self.fillet_radius} mm) must be less than the "
                f"plate height above the arm ({free_plate_height} mm); the fillet "
                "would run past the top of the plate."
            )

        if self.fillet_radius >= self.arm_length:
            problems.append(
                f"fillet_radius ({self.fillet_radius} mm) must be less than "
                f"arm_length ({self.arm_length} mm); the fillet would run past "
                "the tip of the arm."
            )

        return problems

    def _hole_errors(self) -> list[str]:
        problems = []
        radius = self.hole_diameter / 2.0

        if self.hole_spacing <= self.hole_diameter:
            problems.append(
                f"hole_spacing ({self.hole_spacing} mm) must be greater than "
                f"hole_diameter ({self.hole_diameter} mm); the holes would "
                "overlap or touch."
            )

        half_width = self.width / 2.0
        outer_edge = self.hole_spacing / 2.0 + radius
        if outer_edge >= half_width:
            problems.append(
                f"the holes reach y = {outer_edge:.3f} mm but the plate edge is "
                f"at {half_width:.3f} mm (width / 2); they would break out of "
                "the side of the plate."
            )

        centres = self.hole_centres()
        highest = max(z for _, z in centres)
        lowest = min(z for _, z in centres)

        if highest + radius >= self.hole_band_top:
            problems.append(
                f"the top holes reach z = {highest + radius:.3f} mm but the "
                f"plate ends at {self.hole_band_top:.3f} mm; they would break "
                "out of the top of the plate."
            )

        if lowest - radius <= self.hole_band_bottom:
            problems.append(
                f"the bottom holes reach z = {lowest - radius:.3f} mm but the "
                f"fillet region ends at {self.hole_band_bottom:.3f} mm "
                "(thickness + fillet_radius); they would cut into the fillet, "
                "which is the highest-stressed part of the bracket."
            )

        return problems

    # -- Advisory checks ---------------------------------------------------

    def warnings(self) -> list[str]:
        """Legal but questionable inputs, intended to drive a Review verdict."""
        notes: list[str] = []
        min_edge_distance = MIN_EDGE_DISTANCE_RATIO * self.hole_diameter

        side_edge_distance = self.width / 2.0 - self.hole_spacing / 2.0
        if side_edge_distance < min_edge_distance:
            notes.append(
                f"Hole centres are {side_edge_distance:.2f} mm from the side "
                f"edge, below the {MIN_EDGE_DISTANCE_RATIO} x diameter guideline "
                f"({min_edge_distance:.2f} mm). Risk of tear-out at the edge."
            )

        centres = self.hole_centres()
        top_edge_distance = self.hole_band_top - max(z for _, z in centres)
        if top_edge_distance < min_edge_distance:
            notes.append(
                f"Top hole centres are {top_edge_distance:.2f} mm from the top "
                f"edge, below the {MIN_EDGE_DISTANCE_RATIO} x diameter guideline "
                f"({min_edge_distance:.2f} mm)."
            )

        fillet_clearance = min(z for _, z in centres) - self.hole_band_bottom
        if fillet_clearance < min_edge_distance:
            notes.append(
                f"Bottom hole centres are {fillet_clearance:.2f} mm above the "
                "fillet region, below the "
                f"{MIN_EDGE_DISTANCE_RATIO} x diameter guideline "
                f"({min_edge_distance:.2f} mm). Holes close to the highest-stressed "
                "region raise the stress concentration."
            )

        ligament = self.hole_spacing - self.hole_diameter
        if ligament < MIN_LIGAMENT_RATIO * self.hole_diameter:
            notes.append(
                f"Material between adjacent holes is {ligament:.2f} mm, below "
                f"the {MIN_LIGAMENT_RATIO} x diameter guideline "
                f"({MIN_LIGAMENT_RATIO * self.hole_diameter:.2f} mm)."
            )

        slenderness = self.arm_length / self.thickness
        if slenderness < MIN_SLENDERNESS_RATIO:
            notes.append(
                f"Arm slenderness L/t is {slenderness:.1f}, below "
                f"{MIN_SLENDERNESS_RATIO:.0f}. Engineer's beam theory ignores "
                "shear deflection, so the analytical comparison is a "
                "weaker reference for this geometry."
            )

        max_mesh_size = MAX_MESH_SIZE_FRACTION_OF_THICKNESS * self.thickness
        if self.mesh_size > max_mesh_size:
            notes.append(
                f"mesh_size {self.mesh_size:.2f} mm exceeds half the thickness "
                f"({max_mesh_size:.2f} mm), so there may be fewer than 2 elements "
                "through the thickness. Bending stiffness will be overestimated "
                "(engineering decision 5)."
            )

        return notes

    # -- Convenience --------------------------------------------------------

    def resolved_material(
        self, materials: dict[str, Material] | None = None
    ) -> Material:
        """The Material object this input names."""
        return resolve_material(self.material, materials)

    @classmethod
    def from_json_file(cls, path: str | Path) -> BracketInputs:
        """Load inputs from a JSON file, failing clearly if it is unreadable."""
        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError(f"No input file at {path}.")

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Input file {path} is not valid JSON: {exc}") from exc

        return cls(**data)
