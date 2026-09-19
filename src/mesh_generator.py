"""Tetrahedral meshing with Gmsh, plus checks on the mesh that comes out.

Units: mm.

Element choice (engineering decision 5): second-order tetrahedra, 10 nodes,
written as C3D10 for CalculiX. Linear tets assume constant strain inside each
element and therefore resist bending far too much - shear locking - which makes
a thin plate look several times stiffer than it is. The mid-side nodes of a
C3D10 let the element bend.

Through-thickness resolution matters for the same reason. One element across a
4 mm plate cannot represent bending through the thickness at all. The working
rule is mesh size <= t/2, and the checks below report the measured resolution
rather than trusting the requested size.

Gmsh is a global singleton: it must be initialised and finalised in balanced
pairs, which is why every entry point here uses try/finally.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path

import gmsh
import numpy as np

from src.geometry_checks import ADVISORY, CheckResult

# Gmsh element type 11 is the 10-node second-order tetrahedron, which maps to
# CalculiX C3D10.
GMSH_TET10 = 11
GMSH_TET4 = 4

# Elements through the thickness, from engineering decision 5.
MIN_ELEMENTS_THROUGH_THICKNESS = 2.0

# Gamma quality below which a tetrahedron is considered poor. 1.0 is a perfect
# regular tet; below roughly 0.1 the element is a sliver and pollutes the
# stress field around it.
MIN_ELEMENT_QUALITY = 0.1

# Fraction of elements allowed to fall below MIN_ELEMENT_QUALITY. A handful of
# slivers in a large mesh is normal; a large population is not.
MAX_POOR_ELEMENT_FRACTION = 0.001


@dataclass(frozen=True)
class MeshStats:
    """What came out of the mesher.

    `element_centroids` and `element_spans` are per-element arrays of shape
    (n, 3): the centroid of the four corners, and the bounding-box extent in
    x, y, z. They are kept because through-thickness resolution has to be
    measured in a specific direction, in a specific region, which a single
    average edge length cannot express.
    """

    msh_path: Path
    num_nodes: int
    num_elements: int
    element_type: str
    requested_size: float
    mean_edge_length: float
    max_edge_length: float
    min_quality: float
    mean_quality: float
    num_poor_elements: int
    element_centroids: np.ndarray = field(repr=False, default_factory=lambda: np.empty((0, 3)))
    element_spans: np.ndarray = field(repr=False, default_factory=lambda: np.empty((0, 3)))
    element_qualities: np.ndarray = field(repr=False, default_factory=lambda: np.empty(0))

    @property
    def poor_element_fraction(self) -> float:
        if self.num_elements == 0:
            return 0.0
        return self.num_poor_elements / self.num_elements

    def count_below_quality(self, threshold: float) -> int:
        """Elements below a given gamma quality.

        Recomputed from the stored qualities rather than read from
        `num_poor_elements`, so a caller passing its own threshold gets an
        answer that reflects it.
        """
        if self.element_qualities.size == 0:
            return 0
        return int(np.count_nonzero(self.element_qualities < threshold))

    def elements_through_thickness(
        self, thickness: float, fillet_radius: float = 0.0
    ) -> dict[str, float]:
        """Element layers across the thickness of the plate and of the arm.

        Measured as thickness / (mean element extent in the thin direction),
        using only elements in the region concerned. This answers the question
        that matters - how many elements lie between the two faces - which an
        average edge length over the whole part does not: the bulk of the arm
        dilutes it and hides a plate that is one element thick.

        The corner region is excluded from both counts. Elements there belong
        to the fillet, where the material is thicker than t and a large element
        is not evidence of poor through-thickness resolution.
        """
        result: dict[str, float] = {}

        if self.num_elements == 0:
            return {"plate": 0.0, "arm": 0.0, "worst": 0.0}

        centroids = self.element_centroids
        spans = self.element_spans
        corner_limit = thickness + fillet_radius

        # Plate: thin in x, taken above the fillet region.
        in_plate = (centroids[:, 0] <= thickness) & (centroids[:, 2] >= corner_limit)
        result["plate"] = _layers(thickness, spans[in_plate, 0])

        # Arm: thin in z, taken beyond the fillet region.
        in_arm = (centroids[:, 2] <= thickness) & (centroids[:, 0] >= corner_limit)
        result["arm"] = _layers(thickness, spans[in_arm, 2])

        populated = [value for value in result.values() if value > 0.0]
        result["worst"] = min(populated) if populated else 0.0

        return result


def generate_mesh(
    step_path: str | Path,
    mesh_size: float,
    output_path: str | Path,
    order: int = 2,
    verbose: bool = False,
) -> MeshStats:
    """Mesh a STEP file into second-order tetrahedra and write a .msh file.

    Args:
        step_path: STEP geometry to mesh.
        mesh_size: target element size, mm. Should be <= thickness / 2.
        output_path: destination .msh file.
        order: element order. 2 gives C3D10; 1 is provided only so tests can
            demonstrate why first order is unsuitable.
        verbose: let Gmsh print to the terminal.

    Raises:
        FileNotFoundError: if the STEP file is missing.
        ValueError: if mesh_size is not positive.
        RuntimeError: if meshing produces no volume elements.
    """
    step_path = Path(step_path)
    output_path = Path(output_path)

    if not step_path.is_file():
        raise FileNotFoundError(f"No STEP file to mesh at {step_path}.")
    if mesh_size <= 0.0:
        raise ValueError(f"mesh_size must be greater than zero, got {mesh_size}.")
    if order not in (1, 2):
        raise ValueError(f"order must be 1 or 2, got {order}.")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Gmsh installs a SIGINT handler on initialise so Ctrl+C can interrupt a
    # long mesh. Python only allows signal handlers to be set from the main
    # thread, and Streamlit runs the script in a worker thread, where this
    # raises "signal only works in main thread of the main interpreter".
    # Asking for interruption only when it is actually available keeps Ctrl+C
    # working on the command line without breaking the interface.
    gmsh.initialize(interruptible=threading.current_thread() is threading.main_thread())
    try:
        gmsh.option.setNumber("General.Terminal", 1 if verbose else 0)
        gmsh.model.add("bracket")

        imported = gmsh.model.occ.importShapes(str(step_path))
        if not imported:
            raise RuntimeError(f"Gmsh imported no shapes from {step_path}.")
        gmsh.model.occ.synchronize()

        volumes = gmsh.model.getEntities(dim=3)
        if len(volumes) != 1:
            raise RuntimeError(
                f"Expected exactly one volume in {step_path}, found "
                f"{len(volumes)}. The geometry is not a single connected solid."
            )

        gmsh.option.setNumber("Mesh.MeshSizeMax", mesh_size)
        # A floor well below the target, so curvature refinement can still act
        # around the holes and fillet without the mesh exploding in size.
        gmsh.option.setNumber("Mesh.MeshSizeMin", mesh_size / 4.0)

        # Resolve curved surfaces (holes, fillet) with a sensible number of
        # elements around them, otherwise a hole becomes a coarse polygon and
        # its stress concentration is lost.
        gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 12)
        gmsh.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 1)

        gmsh.option.setNumber("Mesh.Algorithm3D", 1)  # Delaunay
        gmsh.option.setNumber("Mesh.Optimize", 1)
        gmsh.option.setNumber("Mesh.ElementOrder", order)
        # Complete second-order elements: C3D10 needs all 10 nodes. An
        # incomplete element would have mid-side nodes missing and CalculiX
        # would reject it.
        gmsh.option.setNumber("Mesh.SecondOrderIncomplete", 0)
        # Pull the mid-side nodes onto the true curved surfaces rather than
        # leaving them on straight chords.
        gmsh.option.setNumber("Mesh.HighOrderOptimize", 0)

        gmsh.model.mesh.generate(3)
        if order == 2:
            gmsh.model.mesh.setOrder(2)

        # Version 2.2 is the most widely readable .msh format and is simple to
        # parse. The CalculiX input is written from this mesh.
        gmsh.option.setNumber("Mesh.MshFileVersion", 2.2)
        gmsh.write(str(output_path))

        stats = _collect_stats(output_path, mesh_size)
    finally:
        gmsh.finalize()

    if not output_path.is_file():
        raise RuntimeError(f"Gmsh reported success but {output_path} was not written.")

    return stats


def _collect_stats(output_path: Path, requested_size: float) -> MeshStats:
    """Gather node/element counts, edge lengths and quality from the live model.

    Called while Gmsh is still initialised.
    """
    node_tags, node_coords, _ = gmsh.model.mesh.getNodes()
    num_nodes = len(node_tags)

    element_types, element_tags, element_nodes = gmsh.model.mesh.getElements(dim=3)

    if not len(element_types):
        raise RuntimeError(
            "Meshing produced no volume elements. The geometry may be invalid "
            "or the mesh size may be larger than the part."
        )
    if len(element_types) > 1:
        kinds = ", ".join(str(int(t)) for t in element_types)
        raise RuntimeError(
            f"Mesh contains mixed volume element types ({kinds}); a uniform "
            "tetrahedral mesh was expected."
        )

    element_type = int(element_types[0])
    if element_type == GMSH_TET10:
        type_name = "C3D10"
        nodes_per_element = 10
    elif element_type == GMSH_TET4:
        type_name = "C3D4"
        nodes_per_element = 4
    else:
        raise RuntimeError(
            f"Unexpected Gmsh element type {element_type}; expected "
            f"tetrahedra ({GMSH_TET4} or {GMSH_TET10})."
        )

    tags = element_tags[0]
    num_elements = len(tags)

    connectivity = np.asarray(element_nodes[0], dtype=np.int64).reshape(
        num_elements, nodes_per_element
    )

    coords_by_tag = {
        int(tag): node_coords[index * 3 : index * 3 + 3]
        for index, tag in enumerate(node_tags)
    }
    corners = _corner_coordinates(connectivity[:, :4], coords_by_tag)
    mean_edge, max_edge = _edge_lengths(corners)
    centroids, spans = _corner_geometry(corners)

    qualities = np.asarray(
        gmsh.model.mesh.getElementQualities(tags, "gamma"), dtype=float
    )
    poor = int(np.count_nonzero(qualities < MIN_ELEMENT_QUALITY))

    return MeshStats(
        msh_path=output_path,
        num_nodes=num_nodes,
        num_elements=num_elements,
        element_type=type_name,
        requested_size=requested_size,
        mean_edge_length=float(mean_edge),
        max_edge_length=float(max_edge),
        min_quality=float(qualities.min()) if qualities.size else 0.0,
        mean_quality=float(qualities.mean()) if qualities.size else 0.0,
        num_poor_elements=poor,
        element_centroids=centroids,
        element_spans=spans,
        element_qualities=qualities,
    )


def _layers(thickness: float, spans: np.ndarray) -> float:
    """Element layers implied by a set of element extents in one direction."""
    if spans.size == 0:
        return 0.0

    mean_span = float(spans.mean())
    if mean_span <= 0.0:
        return 0.0

    return thickness / mean_span


def _corner_geometry(corners: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Centroid and bounding-box extent of each element, shape (n, 3) each."""
    if corners.size == 0:
        return np.empty((0, 3)), np.empty((0, 3))

    centroids = corners.mean(axis=1)
    spans = corners.max(axis=1) - corners.min(axis=1)

    return centroids, spans


def _corner_coordinates(
    corner_connectivity: np.ndarray, coords_by_tag: dict[int, np.ndarray]
) -> np.ndarray:
    """Corner coordinates of every element, shape (n_elements, 4, 3).

    Mid-side nodes are excluded throughout: the physical size of an element is
    set by its corners, and counting mid-side nodes would halve the apparent
    element size and flatter every resolution measure taken from it.
    """
    if corner_connectivity.size == 0:
        return np.empty((0, 4, 3))

    unique_tags = np.unique(corner_connectivity)
    lookup = {int(tag): index for index, tag in enumerate(unique_tags)}
    points = np.array([coords_by_tag[int(tag)] for tag in unique_tags])

    indices = np.vectorize(lookup.__getitem__)(corner_connectivity)
    return points[indices]


def _edge_lengths(corners: np.ndarray) -> tuple[float, float]:
    """Mean and maximum of the six corner-to-corner edges of each tet."""
    if corners.size == 0:
        return 0.0, 0.0

    pairs = ((0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3))
    lengths = np.concatenate(
        [
            np.linalg.norm(corners[:, a, :] - corners[:, b, :], axis=1)
            for a, b in pairs
        ]
    )

    return float(lengths.mean()), float(lengths.max())


@dataclass(frozen=True)
class MeshData:
    """Nodes and second-order tetrahedra read back from a .msh file.

    `points` is (n_nodes, 3) in mm. `tets` is (n_elements, 10) of 0-based
    indices into `points`.

    Node ordering follows meshio's `tetra10` convention, which is the VTK
    order: four corners, then the mid-side nodes of edges (0,1), (1,2), (0,2),
    (0,3), (1,3), (2,3). That happens to match the CalculiX/Abaqus C3D10
    ordering exactly, so elements can be written out unpermuted. This is
    verified by a test rather than assumed, because a wrong permutation can
    still solve and simply give the wrong answer.
    """

    points: np.ndarray
    tets: np.ndarray

    @property
    def num_nodes(self) -> int:
        return int(self.points.shape[0])

    @property
    def num_elements(self) -> int:
        return int(self.tets.shape[0])


def read_mesh(msh_path: str | Path) -> MeshData:
    """Read a .msh file into plain arrays.

    Raises:
        FileNotFoundError: if the mesh is missing.
        ValueError: if it holds no second-order tetrahedra.
    """
    import meshio

    msh_path = Path(msh_path)
    if not msh_path.is_file():
        raise FileNotFoundError(f"No mesh file at {msh_path}.")

    mesh = meshio.read(str(msh_path))
    blocks = [block for block in mesh.cells if block.type == "tetra10"]

    if not blocks:
        present = ", ".join(sorted({block.type for block in mesh.cells})) or "none"
        raise ValueError(
            f"{msh_path} contains no second-order tetrahedra (tetra10). "
            f"Cell types present: {present}."
        )

    tets = np.vstack([block.data for block in blocks]).astype(np.int64)

    return MeshData(points=np.asarray(mesh.points, dtype=float), tets=tets)


# --- Checks ---------------------------------------------------------------
#
# CheckResult is shared with geometry_checks. If a third module needs it, it
# should move to a common location rather than being duplicated.


def check_element_type(stats: MeshStats) -> CheckResult:
    """Second-order tetrahedra, as required by engineering decision 5."""
    passed = stats.element_type == "C3D10"

    return CheckResult(
        name="Element type",
        passed=passed,
        message=(
            "Second-order tetrahedra (C3D10)."
            if passed
            else f"Elements are {stats.element_type}, not C3D10. First-order "
            "tetrahedra lock in bending and would overestimate stiffness."
        ),
    )


def check_mesh_populated(stats: MeshStats) -> CheckResult:
    """Node and element counts are present and consistent."""
    passed = stats.num_nodes > 0 and stats.num_elements > 0

    return CheckResult(
        name="Node and element counts",
        passed=passed,
        message=(
            f"{stats.num_nodes} nodes, {stats.num_elements} elements."
            if passed
            else f"Mesh is empty: {stats.num_nodes} nodes, "
            f"{stats.num_elements} elements."
        ),
        value=float(stats.num_elements),
    )


def check_through_thickness(
    stats: MeshStats,
    thickness: float,
    fillet_radius: float = 0.0,
    minimum: float = MIN_ELEMENTS_THROUGH_THICKNESS,
) -> CheckResult:
    """At least two elements across the thickness (engineering decision 5).

    Measured in the thin direction of each limb, from the mesh that was
    actually produced. Gmsh treats the requested size as a target rather than
    a cap, so a mesh asked for at t/2 does not necessarily deliver two layers;
    the requested size is exactly what a too-coarse mesh would hide behind.

    Failing this does not mean the solve will crash. It means the bracket will
    come out stiffer and stronger than it is, which is the dangerous direction
    to be wrong in, so it drives a Review verdict rather than a silent run.
    """
    layers = stats.elements_through_thickness(thickness, fillet_radius)
    worst = layers["worst"]
    passed = worst >= minimum

    detail = (
        f"plate {layers['plate']:.1f}, arm {layers['arm']:.1f} "
        f"(requested size {stats.requested_size:.2f} mm)"
    )

    return CheckResult(
        name="Elements through thickness",
        passed=passed,
        message=(
            f"{worst:.1f} elements across the {thickness:.2f} mm thickness: "
            f"{detail}."
            if passed
            else f"Only {worst:.1f} elements across the {thickness:.2f} mm "
            f"thickness: {detail}, below the minimum of {minimum:.0f}. "
            "Reduce mesh_size; bending stiffness is overestimated with fewer "
            "than two elements through the thickness."
        ),
        value=worst,
        expected=minimum,
        # Advisory: a coarse mesh still solves, it just overestimates
        # stiffness. Engineering decision 5 calls for Review, not a silent run
        # and not an outright Fail.
        severity=ADVISORY,
    )


def check_mesh_quality(
    stats: MeshStats,
    min_quality: float = MIN_ELEMENT_QUALITY,
    max_poor_fraction: float = MAX_POOR_ELEMENT_FRACTION,
) -> CheckResult:
    """Few enough sliver elements to trust the stress field.

    A sliver tetrahedron has a nearly degenerate shape, which makes its
    contribution to the stiffness matrix ill-conditioned and produces local
    stress noise. A handful in a large mesh is normal.
    """
    poor = stats.count_below_quality(min_quality)
    fraction = poor / stats.num_elements if stats.num_elements else 0.0
    passed = fraction <= max_poor_fraction

    return CheckResult(
        name="Mesh quality",
        passed=passed,
        message=(
            f"Minimum gamma quality {stats.min_quality:.3f}, mean "
            f"{stats.mean_quality:.3f}; {poor} of "
            f"{stats.num_elements} elements below {min_quality:.2f} "
            f"({fraction:.3%})."
        ),
        value=fraction,
        expected=max_poor_fraction,
        # Advisory: slivers add local stress noise but do not invalidate the
        # solve. A human should look rather than the run being rejected.
        severity=ADVISORY,
    )


def run_mesh_checks(
    stats: MeshStats, thickness: float, fillet_radius: float = 0.0
) -> list[CheckResult]:
    """Every mesh check, in order."""
    return [
        check_mesh_populated(stats),
        check_element_type(stats),
        check_through_thickness(stats, thickness, fillet_radius),
        check_mesh_quality(stats),
    ]
