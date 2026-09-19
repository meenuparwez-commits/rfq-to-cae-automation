"""Tests for src/boundary_detection.py.

Units: mm, N.

Face detection is the step where a mistake does not crash anything: the solve
succeeds and answers a different problem. So these tests check not only that
faces are found, but that the nodes found actually lie where they should.

A coarse mesh is used throughout for speed. Where faceting matters it is called
out explicitly.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from src import boundary_detection, cad_generator, mesh_generator
from src.schemas import BracketInputs, LoadCase

COARSE_SIZE = 6.0


@pytest.fixture(scope="module")
def inputs() -> BracketInputs:
    return BracketInputs.from_json_file("examples/baseline_bracket.json")


@pytest.fixture(scope="module")
def mesh(inputs, tmp_path_factory):
    directory = tmp_path_factory.mktemp("bcs")
    step = directory / "bracket.step"
    cad_generator.export_step(cad_generator.build_from_inputs(inputs), step)
    mesh_generator.generate_mesh(step, COARSE_SIZE, directory / "bracket.msh")
    return mesh_generator.read_mesh(directory / "bracket.msh")


@pytest.fixture(scope="module")
def udl_inputs(inputs) -> BracketInputs:
    return inputs.model_copy(update={"load_case": LoadCase.UDL})


# --- Fixed face -----------------------------------------------------------


def test_fixed_face_is_found(mesh, inputs):
    fixed = boundary_detection.detect_fixed_face(mesh, inputs)

    assert fixed.num_faces > 0
    assert fixed.num_nodes > 0


def test_every_fixed_node_lies_on_the_rear_plane(mesh, inputs):
    """Engineering decision 4: the restraint is the x = 0 plane, nothing else."""
    fixed = boundary_detection.detect_fixed_face(mesh, inputs)
    x = mesh.points[fixed.node_indices, 0]

    assert np.allclose(x, 0.0, atol=boundary_detection.PLANE_TOL)


def test_fixed_face_area_accounts_for_the_holes(mesh, inputs):
    """b*H less four holes. Getting the holes wrong is a 4% error, detectable."""
    expected = boundary_detection.expected_fixed_area(inputs)
    gross = inputs.width * inputs.plate_height

    assert expected < gross
    assert gross - expected == pytest.approx(
        4 * math.pi * (inputs.hole_diameter / 2) ** 2, rel=1e-12
    )

    fixed = boundary_detection.detect_fixed_face(mesh, inputs)
    assert boundary_detection.check_face_area(fixed, expected).passed


def test_fixed_face_area_is_slightly_over_the_ideal_because_holes_are_faceted(
    mesh, inputs
):
    """A meshed hole is a polygon, which is smaller than its circle.

    So slightly more plate is left around it. The measured area should exceed
    the hand calculation, not fall short: a shortfall would mean something
    else is wrong.
    """
    fixed = boundary_detection.detect_fixed_face(mesh, inputs)
    expected = boundary_detection.expected_fixed_area(inputs)

    assert fixed.total_area > expected
    assert fixed.total_area < expected * 1.01


# --- Load faces -----------------------------------------------------------


def test_tip_load_face_is_the_end_of_the_arm(mesh, inputs):
    assert inputs.load_case is LoadCase.TIP_LOAD

    load = boundary_detection.detect_load_face(mesh, inputs)
    x = mesh.points[load.node_indices, 0]

    assert np.allclose(
        x, inputs.thickness + inputs.arm_length, atol=boundary_detection.PLANE_TOL
    )


def test_tip_face_area_is_exact(mesh, inputs):
    """A flat rectangle with no curved edges: no faceting, so it is exact."""
    load = boundary_detection.detect_load_face(mesh, inputs)

    assert load.total_area == pytest.approx(inputs.width * inputs.thickness, rel=1e-9)


def test_udl_load_face_is_the_top_of_the_arm(mesh, udl_inputs):
    load = boundary_detection.detect_load_face(mesh, udl_inputs)
    z = mesh.points[load.node_indices, 2]

    assert np.allclose(z, udl_inputs.thickness, atol=boundary_detection.PLANE_TOL)


def test_udl_face_is_shortened_by_the_fillet(mesh, udl_inputs):
    """The flat top starts where the fillet becomes tangent, at x = t + r.

    The closed-form UDL result assumes load over the full length L, so the
    analytical comparison has to account for the difference.
    """
    expected = boundary_detection.expected_load_area(udl_inputs)

    assert expected == pytest.approx(
        udl_inputs.width * (udl_inputs.arm_length - udl_inputs.fillet_radius)
    )
    assert boundary_detection.loaded_length(udl_inputs) == pytest.approx(
        udl_inputs.arm_length - udl_inputs.fillet_radius
    )

    load = boundary_detection.detect_load_face(mesh, udl_inputs)
    assert boundary_detection.check_face_area(load, expected).passed

    x = mesh.points[load.node_indices, 0]
    assert x.min() >= udl_inputs.thickness + udl_inputs.fillet_radius - 1e-6


def test_tip_load_length_is_the_full_arm(inputs):
    assert boundary_detection.loaded_length(inputs) == pytest.approx(
        inputs.arm_length
    )


# --- Equivalent nodal forces ---------------------------------------------


def test_nodal_forces_sum_to_the_applied_load(mesh, inputs):
    load = boundary_detection.detect_load_face(mesh, inputs)
    forces = boundary_detection.consistent_nodal_forces(load, inputs.applied_load)

    resultant = np.sum(np.stack(list(forces.values())), axis=0)

    assert resultant[2] == pytest.approx(-inputs.applied_load, rel=1e-12)
    assert resultant[0] == pytest.approx(0.0, abs=1e-9)
    assert resultant[1] == pytest.approx(0.0, abs=1e-9)


def test_only_mid_side_nodes_carry_load(mesh, inputs):
    """The consistent load vector of a quadratic triangle is zero at corners.

    Not an approximation: it is the exact integral of the shape functions. If
    corners appeared here, the distribution would be a lumped guess instead.
    """
    load = boundary_detection.detect_load_face(mesh, inputs)
    forces = boundary_detection.consistent_nodal_forces(load, inputs.applied_load)

    corner_nodes = set(int(node) for node in mesh.tets[:, :4].ravel())
    loaded_nodes = set(forces)

    assert loaded_nodes
    assert not (loaded_nodes & corner_nodes)


def test_load_direction_is_downwards(mesh, inputs):
    load = boundary_detection.detect_load_face(mesh, inputs)
    forces = boundary_detection.consistent_nodal_forces(load, inputs.applied_load)

    assert all(force[2] < 0.0 for force in forces.values())


def test_applied_load_check_passes_for_a_correct_distribution(mesh, inputs):
    load = boundary_detection.detect_load_face(mesh, inputs)
    forces = boundary_detection.consistent_nodal_forces(load, inputs.applied_load)

    assert boundary_detection.check_applied_load(forces, inputs.applied_load).passed


def test_applied_load_check_fails_when_the_total_is_wrong(mesh, inputs):
    load = boundary_detection.detect_load_face(mesh, inputs)
    forces = boundary_detection.consistent_nodal_forces(load, inputs.applied_load)

    result = boundary_detection.check_applied_load(forces, inputs.applied_load * 2)

    assert not result.passed


def test_applied_load_check_reports_no_forces():
    result = boundary_detection.check_applied_load({}, 500.0)

    assert not result.passed
    assert "No nodal forces" in result.message


# --- Failure handling -----------------------------------------------------


def test_a_plane_with_no_faces_is_reported_not_guessed(mesh, inputs):
    """Ask for a plane in empty space: the check must fail, loudly."""
    empty = boundary_detection._faces_on_plane(
        mesh, axis=0, value=1000.0, name="nowhere"
    )

    assert empty.num_faces == 0

    result = boundary_detection.check_face_area(empty, 100.0)
    assert not result.passed
    assert "No element faces" in result.message


def test_loading_an_empty_face_is_refused(mesh, inputs):
    empty = boundary_detection._faces_on_plane(
        mesh, axis=0, value=1000.0, name="nowhere"
    )

    with pytest.raises(ValueError, match="no element faces were found"):
        boundary_detection.consistent_nodal_forces(empty, 500.0)


def test_face_area_check_fails_for_the_wrong_expected_area(mesh, inputs):
    """Compare the tip face against the rear face's area: must fail."""
    load = boundary_detection.detect_load_face(mesh, inputs)

    result = boundary_detection.check_face_area(
        load, boundary_detection.expected_fixed_area(inputs)
    )

    assert not result.passed


def test_run_boundary_checks_covers_the_expected_checks(mesh, inputs):
    fixed = boundary_detection.detect_fixed_face(mesh, inputs)
    load = boundary_detection.detect_load_face(mesh, inputs)
    forces = boundary_detection.consistent_nodal_forces(load, inputs.applied_load)

    results = boundary_detection.run_boundary_checks(
        mesh, inputs, fixed, load, forces
    )

    assert len(results) == 3
    failed = [str(result) for result in results if not result.passed]
    assert not failed, "Baseline failed:\n" + "\n".join(failed)
