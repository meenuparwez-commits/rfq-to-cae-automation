"""Tests for src/visualization.py.

The point of these is the blank-image guard. Off-screen rendering on a laptop
with switchable graphics can produce a perfectly valid PNG that is entirely one
colour, and a blank image in a report is worse than no image because it reads
as a result.
"""

from __future__ import annotations

import numpy as np
import pytest

from src import cad_generator, mesh_generator, visualization
from src.schemas import BracketInputs

COARSE_SIZE = 6.0


@pytest.fixture(scope="module")
def msh_path(tmp_path_factory):
    inputs = BracketInputs.from_json_file("examples/baseline_bracket.json")
    directory = tmp_path_factory.mktemp("viz")

    step_path = directory / "bracket_geometry.step"
    cad_generator.export_step(cad_generator.build_from_inputs(inputs), step_path)

    stats = mesh_generator.generate_mesh(
        step_path, COARSE_SIZE, directory / "bracket_mesh.msh"
    )
    return stats.msh_path


def test_mesh_loads_into_a_pyvista_grid(msh_path):
    grid = visualization.load_mesh(msh_path)

    assert grid.n_points > 0
    assert grid.n_cells > 0


def test_missing_mesh_is_reported_clearly(tmp_path):
    with pytest.raises(FileNotFoundError, match="No mesh file"):
        visualization.load_mesh(tmp_path / "absent.msh")


def test_render_produces_an_image_with_real_content(msh_path, tmp_path):
    output = tmp_path / "mesh.png"

    result = visualization.render_mesh(msh_path, output, window_size=(600, 400))

    assert output.is_file()
    assert output.stat().st_size > 0
    assert result.has_content, (
        f"Rendered image is nearly uniform (spread {result.pixel_spread}). "
        "Suspect off-screen rendering on the hybrid GPU."
    )
    assert result.pixel_spread >= visualization.MIN_PIXEL_SPREAD


def test_render_creates_missing_directories(msh_path, tmp_path):
    output = tmp_path / "nested" / "run_001" / "mesh.png"

    visualization.render_mesh(msh_path, output, window_size=(400, 300))

    assert output.is_file()


@pytest.mark.parametrize("colour", ["black", "white"])
def test_empty_scene_is_detected_as_blank(tmp_path, colour):
    """An empty scene renders a valid but uniform PNG; the guard must catch it.

    Rendered rather than fabricated, because this is the real failure mode:
    off-screen rendering that cannot reach the GPU writes a perfectly valid
    file containing one flat colour.
    """
    import pyvista as pv

    blank = tmp_path / f"blank_{colour}.png"

    plotter = pv.Plotter(off_screen=True, window_size=(64, 64))
    plotter.set_background(colour)
    plotter.screenshot(str(blank))
    plotter.close()

    result = visualization._verify_render(blank)

    assert not result.has_content
    assert result.pixel_spread == pytest.approx(0.0)
    assert "BLANK" in str(result)


# --- Result fringes -------------------------------------------------------


@pytest.fixture(scope="module")
def solved_grid(tmp_path_factory):
    """A small solved model turned into a PyVista grid with results attached."""
    from src import (
        boundary_detection,
        calculix_writer,
        result_reader,
        solver_runner,
    )

    inputs = BracketInputs.from_json_file("examples/baseline_bracket.json")
    directory = tmp_path_factory.mktemp("fringe")

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
    results = result_reader.read_frd(run.frd_path, mesh.num_nodes)

    return visualization.build_result_grid(mesh, results), results


def test_result_grid_keeps_the_quadratic_elements(solved_grid):
    """Rendering tets as linear would discard the curvature we meshed for."""
    import pyvista as pv

    grid, _ = solved_grid

    assert grid.n_cells > 0
    assert set(grid.celltypes) == {int(pv.CellType.QUADRATIC_TETRA)}


def test_result_grid_carries_the_fields(solved_grid):
    grid, results = solved_grid

    assert visualization.VON_MISES_LABEL in grid.point_data
    assert visualization.DISPLACEMENT_LABEL in grid.point_data
    assert "displacement" in grid.point_data
    assert grid.n_points == results.num_nodes


def test_grid_values_match_the_results(solved_grid):
    """The picture must show the same numbers the checks were run on."""
    grid, results = solved_grid

    assert grid.point_data[visualization.VON_MISES_LABEL].max() == pytest.approx(
        results.max_von_mises
    )


def test_stress_fringe_is_rendered_with_content(solved_grid, tmp_path):
    grid, _ = solved_grid
    output = tmp_path / "stress.png"

    result = visualization.render_stress(grid, output, window_size=(600, 400))

    assert output.is_file()
    assert result.has_content


def test_displacement_fringe_is_rendered_with_content(solved_grid, tmp_path):
    grid, _ = solved_grid
    output = tmp_path / "displacement.png"

    result = visualization.render_displacement(grid, output, window_size=(600, 400))

    assert output.is_file()
    assert result.has_content


def test_an_unknown_field_is_refused(solved_grid, tmp_path):
    grid, _ = solved_grid

    with pytest.raises(ValueError, match="No field 'temperature'"):
        visualization.render_field(
            grid, "temperature", tmp_path / "x.png", "Temperature"
        )


def test_deformation_is_scaled_to_be_visible(solved_grid):
    """Real deflections are under a millimetre on an 84 mm part.

    An unscaled plot would look undeformed and tell the reader nothing, so the
    exaggeration is computed rather than guessed - and written on the image so
    nobody mistakes it for the true shape.
    """
    grid, _ = solved_grid

    scale = visualization._deformation_scale(grid)

    assert scale > 1.0


def test_result_mesh_is_written_for_independent_inspection(solved_grid, tmp_path):
    grid, _ = solved_grid
    output = tmp_path / "results.vtu"

    visualization.write_result_mesh(grid, output)

    assert output.is_file()
    assert output.stat().st_size > 0


def test_missing_render_output_is_reported_clearly(tmp_path):
    with pytest.raises(RuntimeError, match="does not exist"):
        visualization._verify_render(tmp_path / "never_rendered.png")


def test_empty_render_output_is_reported_clearly(tmp_path):
    empty = tmp_path / "empty.png"
    empty.touch()

    with pytest.raises(RuntimeError, match="empty file"):
        visualization._verify_render(empty)
