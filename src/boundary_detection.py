"""Find the fixed face and the loaded face in the mesh, by coordinate.

Units: mm, N.

After meshing, the model is a list of numbered points with no memory of which
CAD face they came from. The faces have to be recovered geometrically. Getting
this wrong does not make the solve fail; it makes it answer a different
question, which is far more dangerous. Every detected face therefore has its
area compared against a hand calculation.

Boundary condition, engineering decision 4: the entire rear face of the
mounting plate (x = 0) is fully fixed. The holes carry no load in this model -
they are simply absent from the fixed face - and that limitation is stated in
the report.

Load faces, matching the load cases in engineering decision 1:
    tip_load - the end face of the arm at x = t + L
    udl      - the flat top face of the arm at z = t

Both loads act downwards, in -z.
"""

from __future__ import annotations

import math
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
) -> FaceSelection:
    """Every element face lying in the plane `coordinate[axis] == value`.

    A tetrahedron has a face in the plane exactly when six of its ten nodes lie
    in it: three corners and the three mid-side nodes of the edges between
    them. Two corners in the plane means an edge, giving three nodes, not six.

    Selecting by "which nodes are in the plane" rather than by a hard-coded
    face-numbering table keeps this independent of element node ordering, which
    differs between Gmsh, VTK and Abaqus conventions.
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


def detect_fixed_face(mesh: MeshData, inputs: BracketInputs) -> FaceSelection:
    """The rear face of the mounting plate, at x = 0."""
    return _faces_on_plane(mesh, axis=0, value=0.0, name="fixed face (x = 0)")


def detect_load_face(mesh: MeshData, inputs: BracketInputs) -> FaceSelection:
    """The loaded face, chosen to match the load case.

    For `udl` this is the flat top of the arm. Note that the flat top starts at
    x = t + r, where the fillet becomes tangent, so the loaded length is L - r
    rather than L. The analytical comparison must account for that against the
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
    """b*H less the holes, which pass right through the plate."""
    hole_area = inputs.num_holes * math.pi * (inputs.hole_diameter / 2.0) ** 2
    return inputs.width * inputs.plate_height - hole_area


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
        check_face_area(fixed, expected_fixed_area(inputs)),
        check_face_area(load, expected_load_area(inputs)),
        check_applied_load(forces, inputs.applied_load),
    ]
