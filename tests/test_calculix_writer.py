"""Tests for src/calculix_writer.py.

Units: mm, N, MPa.

The deck is the handover point to a program we do not control, so these tests
check the file's contents directly rather than trusting that CalculiX will
complain if something is wrong. CalculiX is quite capable of solving a deck
that describes the wrong problem.
"""

from __future__ import annotations

import numpy as np
import pytest

from src import boundary_detection, cad_generator, calculix_writer, mesh_generator
from src.schemas import BracketInputs

COARSE_SIZE = 6.0


@pytest.fixture(scope="module")
def inputs() -> BracketInputs:
    return BracketInputs.from_json_file("examples/baseline_bracket.json")


@pytest.fixture(scope="module")
def model(inputs, tmp_path_factory):
    directory = tmp_path_factory.mktemp("deck")
    step = directory / "bracket.step"
    cad_generator.export_step(cad_generator.build_from_inputs(inputs), step)
    mesh_generator.generate_mesh(step, COARSE_SIZE, directory / "bracket.msh")

    mesh = mesh_generator.read_mesh(directory / "bracket.msh")
    fixed = boundary_detection.detect_fixed_face(mesh, inputs)
    load = boundary_detection.detect_load_face(mesh, inputs)
    forces = boundary_detection.consistent_nodal_forces(load, inputs.applied_load)

    return mesh, fixed, forces


@pytest.fixture(scope="module")
def deck(model, inputs, tmp_path_factory):
    mesh, fixed, forces = model
    path = tmp_path_factory.mktemp("written") / "bracket_analysis.inp"

    summary = calculix_writer.write_input_deck(
        path, mesh, inputs, inputs.resolved_material(), fixed, forces
    )
    return summary, path.read_text(encoding="ascii").splitlines()


# --- Structure ------------------------------------------------------------


@pytest.mark.parametrize(
    "keyword",
    ["*NODE", "*ELEMENT", "*MATERIAL", "*ELASTIC", "*SOLID SECTION",
     "*STEP", "*STATIC", "*BOUNDARY", "*CLOAD", "*END STEP"],
)
def test_deck_contains_the_required_keyword(deck, keyword):
    _, lines = deck

    assert any(line.startswith(keyword) for line in lines), f"missing {keyword}"


def test_elements_are_declared_as_c3d10(deck):
    _, lines = deck

    assert any("TYPE=C3D10" in line for line in lines)


def test_units_are_stated_in_the_header(deck):
    """The unit system is stated wherever results go."""
    _, lines = deck
    header = "\n".join(lines[:12])

    assert "mm, N, MPa" in header


def test_header_records_the_restraint_limitation(deck):
    """The holes carry no load under decision 4; that must be written down."""
    _, lines = deck
    header = "\n".join(lines[:12])

    assert "holes therefore carry no load" in header


def test_node_block_has_one_line_per_node(deck, model):
    mesh, _, _ = model
    _, lines = deck

    start = next(i for i, line in enumerate(lines) if line.startswith("*NODE,"))
    end = next(i for i, line in enumerate(lines) if line.startswith("*ELEMENT"))

    assert end - start - 1 == mesh.num_nodes


def test_element_block_has_two_lines_per_element(deck, model):
    mesh, _, _ = model
    _, lines = deck

    start = next(i for i, line in enumerate(lines) if line.startswith("*ELEMENT"))
    end = next(i for i, line in enumerate(lines) if line.startswith("*NSET"))

    assert end - start - 1 == 2 * mesh.num_elements


def test_node_numbering_starts_at_one(deck):
    """CalculiX numbers from 1; a 0-based deck is rejected or misread."""
    _, lines = deck

    start = next(i for i, line in enumerate(lines) if line.startswith("*NODE,"))
    first_id = int(lines[start + 1].split(",")[0])

    assert first_id == 1


def test_element_connectivity_lists_ten_nodes(deck):
    _, lines = deck

    start = next(i for i, line in enumerate(lines) if line.startswith("*ELEMENT"))
    first = lines[start + 1].rstrip(",").split(",")
    second = lines[start + 2].split(",")

    assert len(first) - 1 + len(second) == 10


# --- Physics --------------------------------------------------------------


def test_material_properties_are_written(deck, inputs):
    _, lines = deck
    material = inputs.resolved_material()

    index = lines.index("*ELASTIC")
    values = [float(part) for part in lines[index + 1].split(",")]

    assert values[0] == pytest.approx(material.youngs_modulus)
    assert values[1] == pytest.approx(material.poissons_ratio)


def test_all_six_restraint_degrees_of_freedom_are_fixed(deck):
    """Decision 4 is a full fix: directions 1 to 3, all zero."""
    _, lines = deck

    index = lines.index("*BOUNDARY")
    assert lines[index + 1].startswith("NFIXED, 1, 3, 0")


def test_applied_loads_sum_to_the_requested_total(deck, inputs):
    """Parse the CLOAD block back out and add it up."""
    _, lines = deck

    start = lines.index("*CLOAD")
    total = 0.0
    for line in lines[start + 1 :]:
        if line.startswith("*") or line.startswith("**"):
            break
        _, dof, value = (part.strip() for part in line.split(","))
        if int(dof) == 3:
            total += float(value)

    assert total == pytest.approx(-inputs.applied_load, rel=1e-9)


def test_reaction_totals_are_requested_for_the_equilibrium_check(deck):
    _, lines = deck

    assert any("TOTALS=ONLY" in line for line in lines)
    assert any(line.startswith("*NODE PRINT") for line in lines)


def test_displacements_and_stresses_are_written_to_the_frd(deck):
    _, lines = deck

    assert "*NODE FILE" in lines
    assert "*EL FILE" in lines


# --- Summary and failure handling ----------------------------------------


def test_summary_reports_what_was_written(deck, model, inputs):
    summary, _ = deck
    mesh, fixed, forces = model

    assert summary.num_nodes == mesh.num_nodes
    assert summary.num_elements == mesh.num_elements
    assert summary.num_fixed_nodes == fixed.num_nodes
    assert summary.num_loaded_nodes == len(forces)
    assert summary.total_applied_load == pytest.approx(inputs.applied_load, rel=1e-9)


def test_unrestrained_model_is_refused(model, inputs, tmp_path):
    """No restraint means rigid body motion and a singular stiffness matrix."""
    mesh, fixed, forces = model
    empty = boundary_detection._faces_on_plane(
        mesh, axis=0, value=1000.0, name="nowhere"
    )

    with pytest.raises(ValueError, match="rigid body motion"):
        calculix_writer.write_input_deck(
            tmp_path / "bad.inp", mesh, inputs, inputs.resolved_material(),
            empty, forces,
        )


def test_unloaded_model_is_refused(model, inputs, tmp_path):
    mesh, fixed, _ = model

    with pytest.raises(ValueError, match="carries no load"):
        calculix_writer.write_input_deck(
            tmp_path / "bad.inp", mesh, inputs, inputs.resolved_material(),
            fixed, {},
        )


def test_output_directory_is_created(model, inputs, tmp_path):
    mesh, fixed, forces = model
    target = tmp_path / "nested" / "run_001" / "bracket_analysis.inp"

    calculix_writer.write_input_deck(
        target, mesh, inputs, inputs.resolved_material(), fixed, forces
    )

    assert target.is_file()


def test_deck_is_plain_ascii(deck, model, inputs, tmp_path):
    """CalculiX reads ASCII; a stray non-ASCII character would break parsing."""
    mesh, fixed, forces = model
    path = tmp_path / "ascii.inp"

    calculix_writer.write_input_deck(
        path, mesh, inputs, inputs.resolved_material(), fixed, forces
    )

    path.read_text(encoding="ascii")  # raises if anything is outside ASCII
