"""Bracket geometry: builds the solid, handles STEP import and export.

Product-specific. The meshing, solving and reporting stages know nothing about
brackets.

Units: mm.

Coordinate system, relied on by every later stage:

    x  outward from the wall, plate rear face (the fixed face) at x = 0
    y  across the width, centred on zero
    z  up, arm underside at z = 0

Side elevation (x-z plane)::

    z
    ^
    |  +----+
    |  |    |  <- mounting plate, height H, thickness t
    |  |    |
    |  |    \\        <- inside fillet, radius r
    |  |     `-----------+
    |  |                 |   <- arm, free length L, thickness t
    |  +-----------------+
    +--------------------------> x
       0    t          t + L

The arm's box spans x = 0 to t + L, but its FREE length is L, measured from the
plate's front face at x = t. The analytical comparison depends on that
distinction: building the arm L long overall would put it out by one thickness.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

import cadquery as cq

if TYPE_CHECKING:  # avoids a runtime import cycle; used only for type hints
    from src.schemas import BracketInputs

# Tolerance for locating geometry by coordinate, in mm. Generous next to CAD
# kernel precision (~1e-7 mm) but far tighter than any real dimension.
_GEOM_TOL = 1e-6


def build_bracket(
    plate_height: float,
    arm_length: float,
    width: float,
    thickness: float,
    fillet_radius: float,
    hole_diameter: float = 0.0,
    hole_centres: Sequence[tuple[float, float]] | None = None,
) -> cq.Workplane:
    """Return the bracket solid.

    Args:
        plate_height: H, height of the vertical mounting plate (mm).
        arm_length: L, free length of the arm from the plate's front face (mm).
        width: b, width of both plate and arm (mm).
        thickness: t, thickness of both plate and arm (mm).
        fillet_radius: r, radius of the inside corner fillet (mm).
        hole_diameter: bolt hole diameter (mm). Holes are cut only when this is
            greater than zero and centres are supplied.
        hole_centres: (y, z) centres of the holes, in mm. Positions come from
            BracketInputs.hole_centres(); this function places holes, it does
            not decide where they belong.

    Raises:
        ValueError: if the dimensions cannot produce a valid solid.

    Note:
        Only the geometric guards needed to avoid building a broken solid live
        here. Full input validation, including hole conflicts, belongs to
        src/schemas.py, which runs before anything reaches the CAD kernel.
    """
    _validate_dimensions(
        plate_height=plate_height,
        arm_length=arm_length,
        width=width,
        thickness=thickness,
        fillet_radius=fillet_radius,
    )

    # Vertical plate: x from 0 to t, z from 0 to H, centred in y.
    plate = cq.Workplane("XY").box(
        thickness, width, plate_height, centered=(False, True, False)
    )

    # Horizontal arm: x from 0 to t + L, z from 0 to t, centred in y.
    # It deliberately overlaps the plate's lower region; the union merges them
    # into one solid with no internal face.
    arm = cq.Workplane("XY").box(
        thickness + arm_length, width, thickness, centered=(False, True, False)
    )

    bracket = plate.union(arm).clean()

    if fillet_radius > 0.0:
        bracket = _fillet_inside_corner(bracket, thickness, fillet_radius)

    if hole_diameter > 0.0 and hole_centres:
        bracket = _cut_holes(bracket, thickness, hole_diameter, hole_centres)

    return bracket


def _cut_holes(
    bracket: cq.Workplane,
    thickness: float,
    hole_diameter: float,
    hole_centres: Sequence[tuple[float, float]],
) -> cq.Workplane:
    """Drill through the plate along x at each (y, z) centre.

    The cutting cylinders are deliberately longer than the plate and start
    behind it. A cylinder that merely touches the faces it cuts leaves
    zero-thickness slivers, which are a classic source of invalid solids and
    of mesh failures later.
    """
    radius = hole_diameter / 2.0
    overshoot = max(1.0, thickness)
    length = thickness + 2.0 * overshoot

    solid = bracket.val()
    for y, z in hole_centres:
        cutter = cq.Solid.makeCylinder(
            radius,
            length,
            pnt=cq.Vector(-overshoot, y, z),
            dir=cq.Vector(1.0, 0.0, 0.0),
        )
        solid = solid.cut(cutter)

    return cq.Workplane(obj=solid).clean()


def build_from_inputs(inputs: "BracketInputs") -> cq.Workplane:
    """Build the bracket described by a validated BracketInputs.

    The single entry point the pipeline should use: by the time inputs are a
    BracketInputs they have already passed every conflict check, so a failure
    here indicates a bug rather than bad user input.
    """
    return build_bracket(
        plate_height=inputs.plate_height,
        arm_length=inputs.arm_length,
        width=inputs.width,
        thickness=inputs.thickness,
        fillet_radius=inputs.fillet_radius,
        hole_diameter=inputs.hole_diameter,
        hole_centres=inputs.hole_centres(),
    )


def _validate_dimensions(
    plate_height: float,
    arm_length: float,
    width: float,
    thickness: float,
    fillet_radius: float,
) -> None:
    """Reject dimensions that cannot produce a valid bracket solid."""
    positive = {
        "plate_height": plate_height,
        "arm_length": arm_length,
        "width": width,
        "thickness": thickness,
    }
    for name, value in positive.items():
        if value <= 0.0:
            raise ValueError(f"{name} must be greater than zero, got {value}.")

    if fillet_radius < 0.0:
        raise ValueError(
            f"fillet_radius must not be negative, got {fillet_radius}."
        )

    # The fillet is cut back along the plate above the arm, and along the arm
    # ahead of the plate. If it is larger than either run, the blend has
    # nowhere to land and the CAD kernel fails with an opaque error. Catching
    # it here gives a reason instead.
    free_plate_height = plate_height - thickness
    if fillet_radius >= free_plate_height:
        raise ValueError(
            f"fillet_radius ({fillet_radius}) must be less than the plate "
            f"height above the arm ({free_plate_height} = plate_height - "
            "thickness). The fillet cannot reach past the top of the plate."
        )
    if fillet_radius >= arm_length:
        raise ValueError(
            f"fillet_radius ({fillet_radius}) must be less than arm_length "
            f"({arm_length}). The fillet cannot reach past the tip of the arm."
        )


def _fillet_inside_corner(
    bracket: cq.Workplane, thickness: float, fillet_radius: float
) -> cq.Workplane:
    """Round the single concave edge where the plate meets the arm.

    The edge runs along y at x = thickness, z = thickness. It is found by
    coordinate rather than by index, because edge ordering from the CAD kernel
    is not guaranteed stable across versions. If the search does not return
    exactly one edge, that is reported rather than guessed at.
    """
    solid = bracket.val()
    corner_edges = [
        edge
        for edge in solid.Edges()
        if _is_inside_corner_edge(edge, thickness)
    ]

    if len(corner_edges) != 1:
        raise ValueError(
            "Expected exactly one inside corner edge at "
            f"x = z = {thickness} mm, found {len(corner_edges)}. The geometry "
            "is not the expected L shape."
        )

    return cq.Workplane(obj=solid.fillet(fillet_radius, corner_edges))


def _is_inside_corner_edge(edge: cq.Edge, thickness: float) -> bool:
    """True if this edge lies along the line x = thickness, z = thickness."""
    box = edge.BoundingBox()
    return (
        abs(box.xmin - thickness) < _GEOM_TOL
        and abs(box.xmax - thickness) < _GEOM_TOL
        and abs(box.zmin - thickness) < _GEOM_TOL
        and abs(box.zmax - thickness) < _GEOM_TOL
    )


def export_step(bracket: cq.Workplane, path: str | Path) -> Path:
    """Write the solid to a STEP file and confirm it was created.

    STEP is the neutral CAD exchange format, and Gmsh reads it when meshing.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    cq.exporters.export(bracket, str(path), exportType="STEP")

    if not path.is_file():
        raise RuntimeError(f"STEP export reported success but {path} does not exist.")
    if path.stat().st_size == 0:
        raise RuntimeError(f"STEP export produced an empty file at {path}.")

    return path


def import_step(path: str | Path) -> cq.Workplane:
    """Read a STEP file back in.

    Used to prove the exported file is genuinely readable, rather than merely
    present on disk.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"No STEP file at {path}.")

    return cq.importers.importStep(str(path))
