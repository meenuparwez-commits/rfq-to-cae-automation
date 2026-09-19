"""Find the fixed face and the loaded face in the mesh, by coordinate.

Units: mm, N.

After meshing, the model is a list of numbered points with no memory of which
CAD face they came from. The faces have to be recovered geometrically. Getting
this wrong does not make the solve fail; it makes it answer a different
question, which is far more dangerous. Every detected face therefore has its
area compared against a hand calculation.

Boundary condition, engineering decision 4: the plate is held only where its
bolts clamp it - a washer-sized annulus around each hole, on the rear face at
x = 0. The rest of the rear face is free to lift and rotate. The holes now
carry the load, which is the point of the change, but the price is a restraint
singularity at the edge of each annulus: the stress there rises without limit
as the mesh is refined, so it is reported and never validated against.

Load faces, matching the load cases in engineering decision 1:
    tip_load - the end face of the arm at x = t + L
    udl      - the flat top face of the arm at z = t

Both loads act downwards, in -z.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from src.geometry_checks import CheckResult
from src.mesh_generator import MeshData
from src.schemas import BracketInputs, LoadCase

# Tolerance for deciding a node lies on a plane, in mm. Gmsh places nodes on
# planar CAD faces to within kernel precision, far below this.
PLANE_TOL = 1e-6

# Allowed relative difference between detected and hand-calculated face area.
#
# Set from what this check exists to catch: the wrong face. A wrong face is out
# by hundreds of percent - the tip face is 240 mm^2 against the rear face's
# 5746 mm^2 - so 1% discriminates with enormous margin.
#
# It cannot be tightened much further, because a meshed hole is a polygon and
# an inscribed polygon has slightly less area than its circle, leaving slightly
# more plate around it. That faceting error is real, is a property of the mesh
# rather than a mistake, and shrinks with the square of element size: about
# 0.2% at 6 mm elements, and far less at the 1.5 mm baseline. A tolerance tight
# enough to trip on it would raise a false alarm on every coarse mesh.
AREA_REL_TOL = 1e-2

# The clamped annuli get their own tolerance, for a reason worth stating
# rather than quietly widening the one above.
#
# The load and rear faces are bounded by real CAD edges, so the mesh lands on
# them exactly and the only error is hole faceting, which falls away with the
# square of element size. An annulus is not a CAD feature: it is a region
# selected out of a flat face, so its boundary can only follow whole element
# faces. Selecting by face centroid makes that error UNBIASED - faces
# straddling the edge are taken and dropped in roughly equal measure - so it
# cancels rather than accumulating, but it also does not fall monotonically
# with refinement. Measured on the baseline ring (4 mm wide, hole 9, washer 17):
#
#   2.00 mm  +1.49%      1.25 mm  +1.12%      0.75 mm  +0.07%
#   1.50 mm  +0.10%      1.00 mm  +0.04%
#
# The scatter also grows sharply once the ring is thinner than about two
# elements: a 3 mm mesh across the same 4 mm ring measures 9.13% out. That is
# the mesh failing to resolve the restraint, not the selection failing to find
# it, and BracketInputs.warnings() flags it in its own right.
#
# So the threshold is set from what this check exists to catch, which is the
# WRONG REGION, not a few percent of ragged edge. Selecting the whole rear face
# instead of the annuli reads 5745 mm^2 against 653 mm^2 - an error of 780%,
# fifty times this tolerance. Dropping the annuli to the hole radius would read
# zero. A first attempt at 5% was set from the 0.10% the baseline happens to
# achieve, which is the habit this project avoids everywhere else; it also
# turned an under-resolved coarse mesh into a face-detection failure, which is
# the wrong diagnosis to hand a reader.
ANNULUS_AREA_REL_TOL = 0.15

# Downward, matching the sign convention of the analytical formulas.
LOAD_DIRECTION = np.array([0.0, 0.0, -1.0])


@dataclass(frozen=True)
class FaceSelection:
    """A set of element faces lying on one plane of the model."""

    name: str
    node_indices: np.ndarray = field(repr=False)
    corner_triangles: np.ndarray = field(repr=False)
    midside_triangles: np.ndarray = field(repr=False)
    areas: np.ndarray = field(repr=False)

    @property
    def num_faces(self) -> int:
        return int(self.corner_triangles.shape[0])

    @property
    def num_nodes(self) -> int:
        return int(self.node_indices.size)

    @property
    def total_area(self) -> float:
        return float(self.areas.sum())


def _faces_on_plane(
    mesh: MeshData,
    axis: int,
    value: float,
    tol: float = PLANE_TOL,
    name: str = "face",
    keep: Callable[[np.ndarray], np.ndarray] | None = None,
) -> FaceSelection:
    """Every element face lying in the plane `coordinate[axis] == value`.

    A tetrahedron has a face in the plane exactly when six of its ten nodes lie
    in it: three corners and the three mid-side nodes of the edges between
    them. Two corners in the plane means an edge, giving three nodes, not six.

    Selecting by "which nodes are in the plane" rather than by a hard-coded
    face-numbering table keeps this independent of element node ordering, which
    differs between Gmsh, VTK and Abaqus conventions.

    `keep` optionally narrows the result to part of the plane. It is given the
    face centroids and returns a boolean mask. Centroids rather than nodes:
    a face is either in the region or out of it, so the selected patch tracks
    the intended boundary instead of growing by "any node inside" or shrinking
    by "every node inside".
    """
    coordinate = mesh.points[:, axis]
    on_plane = np.abs(coordinate - value) <= tol

    node_flags = on_plane[mesh.tets]  # (n_elements, 10)
    corner_flags = node_flags[:, :4]

    has_face = (node_flags.sum(axis=1) == 6) & (corner_flags.sum(axis=1) == 3)
    element_indices = np.flatnonzero(has_face)

    if element_indices.size == 0:
        return FaceSelection(
            name=name,
            node_indices=np.empty(0, dtype=np.int64),
            corner_triangles=np.empty((0, 3), dtype=np.int64),
            midside_triangles=np.empty((0, 3), dtype=np.int64),
            areas=np.empty(0),
        )

    selected = mesh.tets[element_indices]
    selected_flags = node_flags[element_indices]

    corner_triangles = np.array(
        [row[:4][flags[:4]] for row, flags in zip(selected, selected_flags)],
        dtype=np.int64,
    )
    midside_triangles = np.array(
        [row[4:][flags[4:]] for row, flags in zip(selected, selected_flags)],
        dtype=np.int64,
    )

    if keep is not None:
        centroids = mesh.points[corner_triangles].mean(axis=1)
        mask = np.asarray(keep(centroids), dtype=bool)
        corner_triangles = corner_triangles[mask]
        midside_triangles = midside_triangles[mask]

        if corner_triangles.shape[0] == 0:
            return FaceSelection(
                name=name,
                node_indices=np.empty(0, dtype=np.int64),
                corner_triangles=np.empty((0, 3), dtype=np.int64),
                midside_triangles=np.empty((0, 3), dtype=np.int64),
                areas=np.empty(0),
            )

    areas = _triangle_areas(mesh.points, corner_triangles)
    node_indices = np.unique(
        np.concatenate([corner_triangles.ravel(), midside_triangles.ravel()])
    )

    return FaceSelection(
        name=name,
        node_indices=node_indices,
        corner_triangles=corner_triangles,
        midside_triangles=midside_triangles,
        areas=areas,
    )


def _triangle_areas(points: np.ndarray, triangles: np.ndarray) -> np.ndarray:
    """Area of each triangle, from its three corners.

    The faces of interest lie in a plane, so their mid-side nodes sit on
    straight edges and the corner triangle is the exact face.
    """
    a = points[triangles[:, 0]]
    b = points[triangles[:, 1]]
    c = points[triangles[:, 2]]

    return 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1)


def in_washer_annuli(inputs: BracketInputs) -> Callable[[np.ndarray], np.ndarray]:
    """Predicate for points lying under a washer, in the plane of the rear face.

    Distance is measured in (y, z) to the nearest hole centre, so a point is
    kept when it sits in the ring between the hole edge and the washer edge.
    """
    centres = np.asarray(inputs.hole_centres(), dtype=float)  # (n_holes, 2) as (y, z)
    inner = inputs.hole_diameter / 2.0
    outer = inputs.washer_diameter / 2.0

    def predicate(points: np.ndarray) -> np.ndarray:
        offsets = points[:, None, 1:] - centres[None, :, :]
        nearest = np.linalg.norm(offsets, axis=2).min(axis=1)
        return (nearest >= inner) & (nearest <= outer)

    return predicate


def detect_fixed_face(mesh: MeshData, inputs: BracketInputs) -> FaceSelection:
    """The clamped annuli under the washers, on the rear face at x = 0.

    The bolts hold the plate only where their washers press on it, so only
    that material is restrained. Everything else on the rear face is free to
    lift and rotate, which is what lets the plate carry load through the holes
    at all.
    """
    return _faces_on_plane(
        mesh,
        axis=0,
        value=0.0,
        name="washer annuli (x = 0)",
        keep=in_washer_annuli(inputs),
    )


def detect_load_face(mesh: MeshData, inputs: BracketInputs) -> FaceSelection:
    """The loaded face, chosen to match the load case.

    For `udl` this is the flat top of the arm. Note that the flat top starts at
    x = t + r, where the fillet becomes tangent, so the loaded length is L - r
    rather than L. The analytical reference must account for that when
    closed-form UDL result, which assumes the load runs the full length L.
    """
    if inputs.load_case is LoadCase.TIP_LOAD:
        return _faces_on_plane(
            mesh,
            axis=0,
            value=inputs.thickness + inputs.arm_length,
            name="tip face (x = t + L)",
        )

    return _faces_on_plane(
        mesh, axis=2, value=inputs.thickness, name="arm top face (z = t)"
    )


# --- Expected areas, by hand ---------------------------------------------


def expected_fixed_area(inputs: BracketInputs) -> float:
    """One washer annulus per hole: n * pi * (R^2 - r^2)."""
    return inputs.clamped_area


def expected_load_area(inputs: BracketInputs) -> float:
    """Area of the loaded face for the selected load case."""
    if inputs.load_case is LoadCase.TIP_LOAD:
        return inputs.width * inputs.thickness

    # The fillet eats into the top face: it is flat only from x = t + r.
    return inputs.width * (inputs.arm_length - inputs.fillet_radius)


def loaded_length(inputs: BracketInputs) -> float:
    """Length of arm actually carrying load, measured from the plate face."""
    if inputs.load_case is LoadCase.TIP_LOAD:
        return inputs.arm_length

    return inputs.arm_length - inputs.fillet_radius


# --- Equivalent nodal forces ---------------------------------------------


def consistent_nodal_forces(
    face: FaceSelection, total_force: float, direction: np.ndarray = LOAD_DIRECTION
) -> dict[int, np.ndarray]:
    """Distribute a total force over a face as equivalent nodal forces.

    For a six-node (quadratic) triangle under uniform traction the consistent
    nodal forces are zero at the corners and one third of the face load at each
    mid-side node. That is not an approximation: it is the exact integral of
    the shape functions over the face.

    Spreading the load equally over all six nodes instead would put the total
    in the right place but distort the stress locally, because it does not
    match how the element interpolates.

    Returns a mapping from node index to a force vector in N.
    """
    if face.num_faces == 0:
        raise ValueError(
            f"Cannot apply a load to '{face.name}': no element faces were found "
            "there."
        )

    area = face.total_area
    if area <= 0.0:
        raise ValueError(f"Face '{face.name}' has zero area; cannot apply a load.")

    traction = total_force / area
    unit = np.asarray(direction, dtype=float)
    unit = unit / np.linalg.norm(unit)

    forces: dict[int, np.ndarray] = {}
    for midsides, face_area in zip(face.midside_triangles, face.areas):
        share = traction * face_area / 3.0 * unit
        for node in midsides:
            key = int(node)
            if key in forces:
                forces[key] = forces[key] + share
            else:
                forces[key] = share.copy()

    return forces


# --- Checks ---------------------------------------------------------------


def check_face_area(
    face: FaceSelection, expected: float, rel_tol: float = AREA_REL_TOL
) -> CheckResult:
    """Detected face area against a hand calculation.

    This is the check that catches the wrong face being selected. A misplaced
    boundary condition does not crash the solve; it silently answers a
    different problem.
    """
    actual = face.total_area

    if face.num_faces == 0:
        return CheckResult(
            name=f"Area of {face.name}",
            passed=False,
            message=(
                f"No element faces were found on '{face.name}'. The plane may "
                "be in the wrong place, or the mesh may not align with it."
            ),
            value=0.0,
            expected=expected,
        )

    error = abs(actual - expected) / expected
    passed = error <= rel_tol

    return CheckResult(
        name=f"Area of {face.name}",
        passed=passed,
        message=(
            f"{actual:.3f} mm^2 over {face.num_faces} faces and "
            f"{face.num_nodes} nodes; hand calculation {expected:.3f} mm^2, "
            f"relative error {error:.2e} (tolerance {rel_tol:.0e})."
        ),
        value=actual,
        expected=expected,
    )


def check_applied_load(
    forces: dict[int, np.ndarray], total_force: float, rel_tol: float = 1e-9
) -> CheckResult:
    """The nodal forces really do sum to the requested load.

    Cheap to check and worth checking: an error in the distribution would
    otherwise only surface later as a puzzling equilibrium failure.
    """
    if not forces:
        return CheckResult(
            name="Applied load total",
            passed=False,
            message="No nodal forces were generated.",
            value=0.0,
            expected=total_force,
        )

    resultant = np.sum(np.stack(list(forces.values())), axis=0)
    magnitude = float(np.linalg.norm(resultant))
    error = abs(magnitude - total_force) / total_force
    passed = error <= rel_tol

    return CheckResult(
        name="Applied load total",
        passed=passed,
        message=(
            f"Nodal forces sum to {magnitude:.6f} N over {len(forces)} nodes "
            f"(requested {total_force:.6f} N, relative error {error:.2e}); "
            f"resultant [{resultant[0]:.3e}, {resultant[1]:.3e}, "
            f"{resultant[2]:.3f}] N."
        ),
        value=magnitude,
        expected=total_force,
    )


def run_boundary_checks(
    mesh: MeshData,
    inputs: BracketInputs,
    fixed: FaceSelection,
    load: FaceSelection,
    forces: dict[int, np.ndarray],
) -> list[CheckResult]:
    """Every boundary check, in order."""
    return [
        check_face_area(
            fixed, expected_fixed_area(inputs), rel_tol=ANNULUS_AREA_REL_TOL
        ),
        check_face_area(load, expected_load_area(inputs)),
        check_applied_load(forces, inputs.applied_load),
    ]
