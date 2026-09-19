"""Tests for src/result_reader.py.

Units: mm, N, MPa.

Most of these use a small hand-written .frd rather than a solved model, so the
expected numbers are known exactly and the awkward parts of the format can be
exercised deliberately - in particular adjacent values with no separator, which
is where a whitespace-splitting parser silently corrupts results.

One real solve is included as an integration check.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from src import (
    boundary_detection,
    cad_generator,
    calculix_writer,
    mesh_generator,
    result_reader,
    solver_runner,
)
from src.schemas import BracketInputs

COARSE_SIZE = 6.0


def frd_data_line(node: int, values: list[float]) -> str:
    """One .frd data row in CalculiX's fixed-column layout."""
    return " -1" + f"{node:>10}" + "".join(f"{value:>12.5E}" for value in values)


def write_frd(path, displacements: dict[int, list[float]], stresses: dict[int, list[float]]):
    """A minimal but structurally faithful .frd file."""
    lines = [
        "    1C",
        f"    2C{len(displacements):>30}{1:>38}",
    ]
    for node in sorted(displacements):
        lines.append(frd_data_line(node, [0.0, 0.0, 0.0]))
    lines.append(" -3")

    lines.append(f"  100CL  101 1.000000000{len(displacements):>12}")
    lines.append(" -4  DISP        4    1")
    lines.append(" -5  D1          1    2    1    0")
    lines.append(" -5  D2          1    2    2    0")
    lines.append(" -5  D3          1    2    3    0")
    lines.append(" -5  ALL         1    2    0    0    1ALL")
    for node in sorted(displacements):
        lines.append(frd_data_line(node, displacements[node]))
    lines.append(" -3")

    lines.append(" -4  STRESS      6    1")
    for index, name in enumerate(result_reader.STRESS_COMPONENTS, start=1):
        lines.append(f" -5  {name:<12}1    4{index:>5}{index:>5}")
    for node in sorted(stresses):
        lines.append(frd_data_line(node, stresses[node]))
    lines.append(" -3")
    lines.append("9999")

    path.write_text("\n".join(lines) + "\n", encoding="ascii")
    return path


# --- Format handling ------------------------------------------------------


def test_adjacent_values_without_a_separator_are_read_correctly(tmp_path):
    """The reason for fixed-column parsing.

    A row like '4.00000E+00-5.00000E+00' has no space between the two values.
    Splitting on whitespace merges them into one unparsable token, or worse,
    shifts every later column.
    """
    path = write_frd(
        tmp_path / "job.frd",
        displacements={1: [4.0, -5.0, 6.0]},
        stresses={1: [1.0, -2.0, 3.0, -4.0, 5.0, -6.0]},
    )

    raw = path.read_text().splitlines()
    assert any("E+00-" in line for line in raw), "test data lost its tight packing"

    results = result_reader.read_frd(path)

    assert results.displacements[0] == pytest.approx([4.0, -5.0, 6.0])
    assert results.stresses[0] == pytest.approx([1.0, -2.0, 3.0, -4.0, 5.0, -6.0])


def test_the_all_pseudo_component_is_not_read_as_a_column(tmp_path):
    """DISP declares four entities but only three are data.

    Counting the declared number would read a fourth column that is not there.
    """
    path = write_frd(
        tmp_path / "job.frd",
        displacements={1: [1.0, 2.0, 3.0], 2: [4.0, 5.0, 6.0]},
        stresses={1: [0.0] * 6, 2: [0.0] * 6},
    )

    results = result_reader.read_frd(path)

    assert results.displacements.shape == (2, 3)


def test_rows_are_ordered_by_node_id_not_by_file_order(tmp_path):
    """Node ids are written in order here, but the mapping is explicit."""
    path = write_frd(
        tmp_path / "job.frd",
        displacements={1: [1.0, 0.0, 0.0], 2: [2.0, 0.0, 0.0], 3: [3.0, 0.0, 0.0]},
        stresses={n: [0.0] * 6 for n in (1, 2, 3)},
    )

    results = result_reader.read_frd(path)

    assert results.displacements[:, 0] == pytest.approx([1.0, 2.0, 3.0])


@pytest.mark.parametrize(
    "ids, what",
    [
        ((1, 3, 4), "a gap"),
        ((1, 2, 4), "a gap at the end"),
        ((0, 1, 2), "a zero-based set"),
        ((2, 3, 4), "an off-by-one set"),
    ],
)
def test_node_ids_that_are_not_a_contiguous_1_to_n_set_are_refused(
    tmp_path, ids, what
):
    """The guard exists because a gap shifts every result by one node.

    It had no negative test, so nothing proved it could fail — and its whole
    value is catching a defect that is otherwise completely silent. A result
    array off by one node still plots, still has a plausible peak, and is
    wrong everywhere.
    """
    path = write_frd(
        tmp_path / "job.frd",
        displacements={n: [float(n), 0.0, 0.0] for n in ids},
        stresses={n: [0.0] * 6 for n in ids},
    )

    with pytest.raises(ValueError, match="contiguous set of node ids"):
        result_reader.read_frd(path)


def test_a_repeated_node_id_is_refused():
    """A duplicate id means one node's results were written twice and
    another's not at all, so the array silently ends up holding a stale row.

    This one goes at the guard directly rather than through a written file:
    the test helper builds its rows from a dict, which cannot hold a repeated
    key, so a file-based case would quietly collapse to a valid set and prove
    nothing. That is worth saying out loud, because a test that cannot express
    the failure it claims to cover is worse than no test.
    """
    ids = np.asarray([1, 2, 2], dtype=np.int64)
    values = np.zeros((3, 3))

    with pytest.raises(ValueError, match="contiguous set of node ids"):
        result_reader._ordered((ids, values), Path("job.frd"), "DISP")


def test_a_contiguous_set_in_shuffled_order_is_accepted(tmp_path):
    """The guard is about the SET of ids, not the order they arrive in.

    Rejecting a shuffled-but-complete block would be a false alarm, so the
    negative tests above are paired with this one to pin the boundary.
    """
    path = write_frd(
        tmp_path / "job.frd",
        displacements={3: [3.0, 0.0, 0.0], 1: [1.0, 0.0, 0.0], 2: [2.0, 0.0, 0.0]},
        stresses={n: [0.0] * 6 for n in (3, 1, 2)},
    )

    results = result_reader.read_frd(path)

    assert results.displacements[:, 0] == pytest.approx([1.0, 2.0, 3.0])


def test_node_count_mismatch_with_the_mesh_is_refused(tmp_path):
    path = write_frd(
        tmp_path / "job.frd",
        displacements={1: [0.0] * 3},
        stresses={1: [0.0] * 6},
    )

    with pytest.raises(ValueError, match="do not match the model"):
        result_reader.read_frd(path, expected_nodes=99)


def test_missing_file_is_reported_clearly(tmp_path):
    with pytest.raises(FileNotFoundError, match="No results file"):
        result_reader.read_frd(tmp_path / "absent.frd")


def test_frd_without_a_stress_block_is_refused(tmp_path):
    path = tmp_path / "job.frd"
    path.write_text(
        "\n".join(
            [
                " -4  DISP        4    1",
                " -5  D1          1    2    1    0",
                " -5  D2          1    2    2    0",
                " -5  D3          1    2    3    0",
                frd_data_line(1, [1.0, 2.0, 3.0]),
                " -3",
                "9999",
            ]
        ),
        encoding="ascii",
    )

    with pytest.raises(ValueError, match="no STRESS block"):
        result_reader.read_frd(path)


# --- Derived quantities ---------------------------------------------------


def test_von_mises_of_pure_uniaxial_stress_equals_that_stress(tmp_path):
    """Hand-checkable case: sigma_vm = sigma for uniaxial tension."""
    path = write_frd(
        tmp_path / "job.frd",
        displacements={1: [0.0] * 3},
        stresses={1: [100.0, 0.0, 0.0, 0.0, 0.0, 0.0]},
    )

    results = result_reader.read_frd(path)

    assert results.von_mises[0] == pytest.approx(100.0)


def test_von_mises_of_hydrostatic_stress_is_zero(tmp_path):
    """Equal stress in all three directions produces no distortion."""
    path = write_frd(
        tmp_path / "job.frd",
        displacements={1: [0.0] * 3},
        stresses={1: [50.0, 50.0, 50.0, 0.0, 0.0, 0.0]},
    )

    results = result_reader.read_frd(path)

    assert results.von_mises[0] == pytest.approx(0.0, abs=1e-9)


def test_von_mises_of_pure_shear(tmp_path):
    """sigma_vm = sqrt(3) * tau."""
    path = write_frd(
        tmp_path / "job.frd",
        displacements={1: [0.0] * 3},
        stresses={1: [0.0, 0.0, 0.0, 40.0, 0.0, 0.0]},
    )

    results = result_reader.read_frd(path)

    assert results.von_mises[0] == pytest.approx(np.sqrt(3) * 40.0)


def test_displacement_magnitude(tmp_path):
    path = write_frd(
        tmp_path / "job.frd",
        displacements={1: [3.0, 4.0, 0.0]},
        stresses={1: [0.0] * 6},
    )

    results = result_reader.read_frd(path)

    assert results.max_displacement == pytest.approx(5.0)


# --- Reaction totals ------------------------------------------------------


def test_reaction_total_is_read(tmp_path):
    path = tmp_path / "job.dat"
    path.write_text(
        "\n".join(
            [
                "                        S T E P       1",
                "",
                " total force (fx,fy,fz) for set NFIXED and time  0.1000000E+01",
                "",
                "       -2.131617E-09 -1.522678E-09  5.000000E+02",
            ]
        ),
        encoding="ascii",
    )

    reactions = result_reader.read_reaction_total(path)

    assert reactions == pytest.approx([-2.131617e-09, -1.522678e-09, 500.0])


def test_dat_without_totals_is_refused(tmp_path):
    path = tmp_path / "job.dat"
    path.write_text("nothing useful here\n", encoding="ascii")

    with pytest.raises(ValueError, match="no 'total force' record"):
        result_reader.read_reaction_total(path)


def test_missing_dat_is_reported_clearly(tmp_path):
    with pytest.raises(FileNotFoundError, match="No .dat results file"):
        result_reader.read_reaction_total(tmp_path / "absent.dat")


# --- Integration against a real solve ------------------------------------


@pytest.fixture(scope="module")
def solved(tmp_path_factory):
    inputs = BracketInputs.from_json_file("examples/baseline_bracket.json")
    directory = tmp_path_factory.mktemp("results")

    step = directory / "bracket.step"
    cad_generator.export_step(cad_generator.build_from_inputs(inputs), step)
    mesh_generator.generate_mesh(step, COARSE_SIZE, directory / "bracket.msh")
    mesh = mesh_generator.read_mesh(directory / "bracket.msh")

    fixed = boundary_detection.detect_fixed_face(mesh, inputs)
    load = boundary_detection.detect_load_face(mesh, inputs)
    forces = boundary_detection.consistent_nodal_forces(load, inputs.applied_load)

    inp = directory / "bracket_analysis.inp"
    calculix_writer.write_input_deck(
        inp, mesh, inputs, inputs.resolved_material(), fixed, forces
    )
    run = solver_runner.run_analysis(inp, num_threads=4)
    assert run.succeeded

    return inputs, mesh, run


def test_real_results_match_the_mesh(solved):
    inputs, mesh, run = solved

    results = result_reader.read_frd(run.frd_path, mesh.num_nodes)

    assert results.num_nodes == mesh.num_nodes


def test_real_reactions_balance_the_load(solved):
    inputs, mesh, run = solved

    reactions = result_reader.read_reaction_total(run.dat_path)

    assert reactions[2] == pytest.approx(inputs.applied_load, rel=1e-6)


def test_tip_deflection_is_downwards_and_sensible(solved):
    inputs, mesh, run = solved
    results = result_reader.read_frd(run.frd_path, mesh.num_nodes)

    deflection = result_reader.tip_deflection(
        results, mesh.points, inputs.thickness + inputs.arm_length
    )

    assert deflection > 0.0  # positive means downwards
    assert deflection < 10.0


def test_tip_plane_with_no_nodes_is_refused(solved):
    inputs, mesh, run = solved
    results = result_reader.read_frd(run.frd_path, mesh.num_nodes)

    with pytest.raises(ValueError, match="No nodes found at the tip plane"):
        result_reader.tip_deflection(results, mesh.points, 1000.0)


def test_section_stress_surfaces_are_equal_and_opposite(solved):
    """Pure bending: top tension and bottom compression of the same size.

    A large mismatch would mean a membrane component, which a cantilever loaded
    transversely should not have.
    """
    inputs, mesh, run = solved
    results = result_reader.read_frd(run.frd_path, mesh.num_nodes)

    section = result_reader.section_stress(
        results,
        mesh.points,
        inputs.thickness + inputs.arm_length / 2,
        inputs.thickness,
        slab_half_width=3.0,
    )

    assert section.top_mean > 0.0
    assert section.bottom_mean < 0.0
    assert abs(section.top_mean + section.bottom_mean) < 0.1 * section.magnitude


def test_section_with_no_surface_nodes_is_refused(solved):
    inputs, mesh, run = solved
    results = result_reader.read_frd(run.frd_path, mesh.num_nodes)

    with pytest.raises(ValueError, match="No surface nodes found"):
        result_reader.section_stress(
            results, mesh.points, 1000.0, inputs.thickness, slab_half_width=0.1
        )
