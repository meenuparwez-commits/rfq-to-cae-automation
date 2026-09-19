"""Tests for src/mesh_generator.py.

Units: mm.

Meshing is slow compared with a unit test, so meshes are built once per module
and shared. A deliberately coarse mesh is kept alongside the baseline one,
because the through-thickness check is only meaningful if it is seen to fail.
"""

from __future__ import annotations

import numpy as np
import pytest

from src import cad_generator, mesh_generator
from src.schemas import BracketInputs

BASELINE_PATH = "examples/baseline_bracket.json"

# Coarse enough to mesh in about a second, and far too coarse to resolve the
# 4 mm thickness: exactly the case the checks must catch.
COARSE_SIZE = 6.0


@pytest.fixture(scope="module")
def inputs() -> BracketInputs:
    return BracketInputs.from_json_file(BASELINE_PATH)


@pytest.fixture(scope="module")
def step_path(inputs, tmp_path_factory):
    path = tmp_path_factory.mktemp("mesh") / "bracket_geometry.step"
    cad_generator.export_step(cad_generator.build_from_inputs(inputs), path)
    return path


@pytest.fixture(scope="module")
def fine_stats(step_path, inputs, tmp_path_factory):
    """The baseline mesh: the one that must satisfy every check."""
    msh = tmp_path_factory.mktemp("fine") / "bracket_mesh.msh"
    return mesh_generator.generate_mesh(step_path, inputs.mesh_size, msh)


@pytest.fixture(scope="module")
def coarse_stats(step_path, tmp_path_factory):
    msh = tmp_path_factory.mktemp("coarse") / "bracket_mesh.msh"
    return mesh_generator.generate_mesh(step_path, COARSE_SIZE, msh)


# --- Element node ordering ------------------------------------------------


# meshio's tetra10 ordering, which is asserted to match CalculiX C3D10. Node i
# of the second six sits at the midpoint of the edge joining this pair.
MIDSIDE_EDGES = [(0, 1), (1, 2), (0, 2), (0, 3), (1, 3), (2, 3)]


def test_midside_nodes_sit_on_the_edges_they_are_supposed_to(step_path, inputs,
                                                             tmp_path_factory):
    """Elements are written to the solver deck unpermuted, on the claim that
    meshio's tetra10 ordering already matches CalculiX C3D10.

    That claim was recorded as verified but had no test behind it, which is
    worse than having no claim: a reader could not check it, and a wrong
    permutation does not fail. It solves, and quietly returns the wrong
    answer.

    Each of nodes 5-10 must be closer to the midpoint of its designated edge
    than to any other edge's midpoint. Curvature on the holes and the fillet
    pulls mid-side nodes off the straight chord, so the test measures which
    midpoint is nearest rather than demanding the node sit exactly on one.
    """
    msh = tmp_path_factory.mktemp("ordering") / "bracket_mesh.msh"
    mesh_generator.generate_mesh(step_path, inputs.mesh_size, msh)
    mesh = mesh_generator.read_mesh(msh)

    points = mesh.points
    tets = mesh.tets
    corners = points[tets[:, :4]]                       # (n, 4, 3)
    midsides = points[tets[:, 4:]]                      # (n, 6, 3)

    # Midpoint of every edge, in the designated order.
    midpoints = np.stack(
        [(corners[:, a] + corners[:, b]) / 2.0 for a, b in MIDSIDE_EDGES],
        axis=1,
    )                                                   # (n, 6, 3)

    # Distance from each mid-side node to every edge midpoint.
    distances = np.linalg.norm(
        midsides[:, :, None, :] - midpoints[:, None, :, :], axis=3
    )                                                   # (n, 6, 6)
    nearest = distances.argmin(axis=2)                  # (n, 6)

    expected = np.arange(6)
    wrong = np.argwhere(nearest != expected)

    assert wrong.size == 0, (
        f"{len(wrong)} mid-side nodes are nearer another edge's midpoint; "
        f"first offender: element {wrong[0][0]}, slot {wrong[0][1]}, "
        f"nearest {nearest[wrong[0][0], wrong[0][1]]}"
    )

    # Report the worst offset so the margin is visible rather than implied.
    own = distances[:, expected, expected]
    print(f"worst mid-side offset from its chord midpoint: {own.max():.4f} mm")


def test_a_permuted_element_is_caught(step_path, inputs, tmp_path_factory):
    """Proof the ordering test can fail.

    A check that has only ever passed is not evidence of anything, and this
    one guards a defect whose whole danger is that it stays silent.
    """
    msh = tmp_path_factory.mktemp("permuted") / "bracket_mesh.msh"
    mesh_generator.generate_mesh(step_path, inputs.mesh_size, msh)
    mesh = mesh_generator.read_mesh(msh)

    points = mesh.points
    tets = mesh.tets.copy()
    # Swap two mid-side slots, the classic symptom of a wrong convention.
    tets[:, [4, 5]] = tets[:, [5, 4]]

    corners = points[tets[:, :4]]
    midsides = points[tets[:, 4:]]
    midpoints = np.stack(
        [(corners[:, a] + corners[:, b]) / 2.0 for a, b in MIDSIDE_EDGES],
        axis=1,
    )
    distances = np.linalg.norm(
        midsides[:, :, None, :] - midpoints[:, None, :, :], axis=3
    )
    nearest = distances.argmin(axis=2)

    assert not np.array_equal(nearest, np.tile(np.arange(6), (len(tets), 1)))


# --- Output -------------------------------------------------------------


def test_mesh_file_is_written_and_readable(fine_stats):
    """A .msh that exists but cannot be parsed is no use to the solver."""
    import meshio

    assert fine_stats.msh_path.is_file()
    assert fine_stats.msh_path.stat().st_size > 0

    mesh = meshio.read(str(fine_stats.msh_path))
    assert any(block.type == "tetra10" for block in mesh.cells)


def test_mesh_has_nodes_and_elements(fine_stats):
    assert fine_stats.num_nodes > 0
    assert fine_stats.num_elements > 0
    # Second-order tets share mid-side nodes, so nodes outnumber elements.
    assert fine_stats.num_nodes > fine_stats.num_elements


def test_elements_are_second_order_tetrahedra(fine_stats):
    assert fine_stats.element_type == "C3D10"
    assert mesh_generator.check_element_type(fine_stats).passed


def test_first_order_mesh_is_rejected_by_the_element_type_check(
    step_path, tmp_path_factory
):
    """C3D4 locks in bending; the check exists to stop it being used."""
    msh = tmp_path_factory.mktemp("linear") / "linear.msh"
    stats = mesh_generator.generate_mesh(step_path, COARSE_SIZE, msh, order=1)

    assert stats.element_type == "C3D4"

    result = mesh_generator.check_element_type(stats)
    assert not result.passed
    assert "lock" in result.message


def test_meshing_twice_in_a_row_works(step_path, tmp_path_factory):
    """Gmsh is a global singleton; unbalanced init/finalise breaks the second run."""
    directory = tmp_path_factory.mktemp("repeat")

    first = mesh_generator.generate_mesh(step_path, COARSE_SIZE, directory / "a.msh")
    second = mesh_generator.generate_mesh(step_path, COARSE_SIZE, directory / "b.msh")

    assert first.num_elements == second.num_elements


# --- Through-thickness resolution ---------------------------------------


def test_baseline_mesh_resolves_the_thickness(fine_stats, inputs):
    result = mesh_generator.check_through_thickness(
        fine_stats, inputs.thickness, inputs.fillet_radius
    )

    assert result.passed, result.message
    assert result.value >= mesh_generator.MIN_ELEMENTS_THROUGH_THICKNESS


def test_coarse_mesh_fails_the_through_thickness_check(coarse_stats, inputs):
    result = mesh_generator.check_through_thickness(
        coarse_stats, inputs.thickness, inputs.fillet_radius
    )

    assert not result.passed
    assert "below the minimum" in result.message
    assert "overestimated" in result.message


def test_through_thickness_is_measured_for_plate_and_arm_separately(
    fine_stats, inputs
):
    layers = fine_stats.elements_through_thickness(
        inputs.thickness, inputs.fillet_radius
    )

    assert set(layers) == {"plate", "arm", "worst"}
    assert layers["plate"] > 0.0
    assert layers["arm"] > 0.0
    assert layers["worst"] == min(layers["plate"], layers["arm"])


def test_a_finer_mesh_resolves_the_thickness_better(
    fine_stats, coarse_stats, inputs
):
    """Refining must improve resolution; if not, the metric is meaningless."""
    fine = fine_stats.elements_through_thickness(
        inputs.thickness, inputs.fillet_radius
    )["worst"]
    coarse = coarse_stats.elements_through_thickness(
        inputs.thickness, inputs.fillet_radius
    )["worst"]

    assert fine > coarse


def test_through_thickness_uses_the_measured_mesh_not_the_requested_size(
    coarse_stats, inputs
):
    """A coarse mesh must not pass by quoting the size that was asked for."""
    result = mesh_generator.check_through_thickness(
        coarse_stats, inputs.thickness, inputs.fillet_radius
    )

    assert not result.passed
    assert coarse_stats.requested_size == COARSE_SIZE
    assert coarse_stats.mean_edge_length != COARSE_SIZE


# --- Quality -------------------------------------------------------------


def test_baseline_mesh_quality_is_acceptable(fine_stats):
    result = mesh_generator.check_mesh_quality(fine_stats)

    assert result.passed, result.message
    assert 0.0 < fine_stats.min_quality <= 1.0
    assert fine_stats.mean_quality > fine_stats.min_quality


def test_quality_check_fails_when_the_threshold_is_impossible(fine_stats):
    """Demand a perfect mesh and the check must refuse it."""
    result = mesh_generator.check_mesh_quality(
        fine_stats, min_quality=1.0, max_poor_fraction=0.0
    )

    assert not result.passed


# --- Check suite ---------------------------------------------------------


def test_run_mesh_checks_covers_the_expected_checks(fine_stats, inputs):
    names = [
        result.name
        for result in mesh_generator.run_mesh_checks(
            fine_stats, inputs.thickness, inputs.fillet_radius
        )
    ]

    assert names == [
        "Node and element counts",
        "Element type",
        "Elements through thickness",
        "Mesh quality",
    ]


def test_baseline_passes_every_mesh_check(fine_stats, inputs):
    results = mesh_generator.run_mesh_checks(
        fine_stats, inputs.thickness, inputs.fillet_radius
    )

    failed = [str(result) for result in results if not result.passed]
    assert not failed, "Baseline mesh failed:\n" + "\n".join(failed)


# --- Failure handling ----------------------------------------------------


def test_missing_step_file_is_reported_clearly(tmp_path):
    with pytest.raises(FileNotFoundError, match="No STEP file to mesh"):
        mesh_generator.generate_mesh(
            tmp_path / "absent.step", 2.0, tmp_path / "out.msh"
        )


@pytest.mark.parametrize("bad_size", [0.0, -1.0])
def test_non_positive_mesh_size_is_rejected(step_path, tmp_path, bad_size):
    with pytest.raises(ValueError, match="mesh_size must be greater than zero"):
        mesh_generator.generate_mesh(step_path, bad_size, tmp_path / "out.msh")


@pytest.mark.parametrize("bad_order", [0, 3])
def test_unsupported_element_order_is_rejected(step_path, tmp_path, bad_order):
    with pytest.raises(ValueError, match="order must be 1 or 2"):
        mesh_generator.generate_mesh(
            step_path, COARSE_SIZE, tmp_path / "out.msh", order=bad_order
        )


def test_output_directory_is_created(step_path, tmp_path):
    target = tmp_path / "nested" / "run_001" / "bracket_mesh.msh"

    mesh_generator.generate_mesh(step_path, COARSE_SIZE, target)

    assert target.is_file()
