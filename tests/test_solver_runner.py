"""Tests for src/solver_runner.py.

The recurring theme: a CalculiX run that looks fine is not necessarily fine.
The return code is not a success signal, and a .frd file can be large, present
and still truncated. These tests exercise the ways a run can fail while
appearing to have worked.

One real solve is run, on a deliberately coarse mesh, in about two seconds.
"""

from __future__ import annotations

import pytest

from src import (
    boundary_detection,
    cad_generator,
    calculix_writer,
    mesh_generator,
    solver_runner,
)
from src.schemas import BracketInputs

COARSE_SIZE = 6.0


@pytest.fixture(scope="module")
def inputs() -> BracketInputs:
    return BracketInputs.from_json_file("examples/baseline_bracket.json")


@pytest.fixture(scope="module")
def solved(inputs, tmp_path_factory):
    """Build and solve a small model once; return the run and expected size."""
    directory = tmp_path_factory.mktemp("solve")
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
    expected_equations = (mesh.num_nodes - fixed.num_nodes) * 3

    return run, expected_equations


# --- A real solve ---------------------------------------------------------


def test_solve_succeeds(solved):
    run, _ = solved

    assert run.succeeded, f"stdout tail:\n{run.stdout[-2000:]}"
    assert run.job_finished
    assert not run.errors


def test_result_files_are_written_and_complete(solved):
    run, _ = solved

    assert run.frd_path.is_file() and run.frd_path.stat().st_size > 0
    assert run.dat_path.is_file() and run.dat_path.stat().st_size > 0
    assert run.frd_complete


def test_equation_count_matches_unrestrained_degrees_of_freedom(solved):
    """Three per free node. Catches a restraint on the wrong node set."""
    run, expected = solved

    assert run.num_equations == expected
    assert solver_runner.check_equation_count(run, expected).passed


def test_reaction_totals_are_present_for_phase_five(solved):
    run, _ = solved
    text = run.dat_path.read_text()

    assert "total force" in text
    assert "NFIXED" in text


def test_all_solver_checks_pass(solved):
    run, expected = solved

    results = solver_runner.run_solver_checks(run, expected)
    failed = [str(result) for result in results if not result.passed]

    assert not failed, "\n".join(failed)


# --- Truncated and missing results ---------------------------------------


def test_truncated_frd_is_detected(solved, tmp_path):
    """A .frd cut short is still big and still parses for a while.

    Only the trailing 9999 marker proves it is complete.
    """
    run, _ = solved

    truncated = tmp_path / "bracket_analysis.frd"
    data = run.frd_path.read_bytes()
    truncated.write_bytes(data[: len(data) // 2])

    assert not solver_runner._ends_with_marker(truncated)


def test_check_result_files_fails_for_a_truncated_frd(solved, tmp_path):
    run, _ = solved

    frd = tmp_path / "job.frd"
    dat = tmp_path / "job.dat"
    data = run.frd_path.read_bytes()
    frd.write_bytes(data[: len(data) // 2])
    dat.write_text("totals")

    broken = solver_runner.SolverRun(
        inp_path=tmp_path / "job.inp", frd_path=frd, dat_path=dat,
        returncode=0, elapsed_seconds=1.0, job_finished=True,
    )

    result = solver_runner.check_result_files(broken)
    assert not result.passed
    assert "truncated" in result.message
    assert not broken.succeeded


def test_check_result_files_fails_when_files_are_missing(tmp_path):
    run = solver_runner.SolverRun(
        inp_path=tmp_path / "job.inp",
        frd_path=tmp_path / "job.frd",
        dat_path=tmp_path / "job.dat",
        returncode=0, elapsed_seconds=1.0, job_finished=True,
    )

    result = solver_runner.check_result_files(run)

    assert not result.passed
    assert "missing or empty" in result.message


# --- Success is not the return code --------------------------------------


def test_a_clean_return_code_is_not_enough(tmp_path):
    """Return code 0 with no 'Job finished' must still be a failure."""
    frd = tmp_path / "job.frd"
    frd.write_text("some output\n9999\n")
    dat = tmp_path / "job.dat"
    dat.write_text("totals")

    run = solver_runner.SolverRun(
        inp_path=tmp_path / "job.inp", frd_path=frd, dat_path=dat,
        returncode=0, elapsed_seconds=1.0,
        stdout="it started and then stopped", job_finished=False,
    )

    assert not run.succeeded
    assert not solver_runner.check_solver_completed(run).passed


def test_errors_in_the_output_fail_the_run(tmp_path):
    frd = tmp_path / "job.frd"
    frd.write_text("9999\n")
    dat = tmp_path / "job.dat"
    dat.write_text("totals")

    run = solver_runner.SolverRun(
        inp_path=tmp_path / "job.inp", frd_path=frd, dat_path=dat,
        returncode=0, elapsed_seconds=1.0, job_finished=True,
        errors=("*ERROR in e_c3d: nonpositive jacobian",),
    )

    assert not run.succeeded

    result = solver_runner.check_solver_completed(run)
    assert not result.passed
    assert "nonpositive jacobian" in result.message


def test_error_lines_are_extracted_from_output():
    stdout = "starting\n *ERROR in something: bad\n more text\n"

    assert solver_runner._parse_errors(stdout) == (
        "*ERROR in something: bad",
    )


def test_equation_count_is_parsed_from_output():
    stdout = " number of equations\n 28563\n more\n"

    assert solver_runner._parse_equations(stdout) == 28563


def test_missing_equation_count_is_reported(tmp_path):
    run = solver_runner.SolverRun(
        inp_path=tmp_path / "job.inp", frd_path=tmp_path / "job.frd",
        dat_path=tmp_path / "job.dat", returncode=0, elapsed_seconds=1.0,
        num_equations=None,
    )

    result = solver_runner.check_equation_count(run, 100)

    assert not result.passed
    assert "did not report" in result.message


def test_equation_count_mismatch_is_reported(solved):
    run, expected = solved

    result = solver_runner.check_equation_count(run, expected + 3)

    assert not result.passed
    assert "restraint may cover the wrong nodes" in result.message


# --- Locating the solver --------------------------------------------------


def test_solver_is_found_through_the_environment():
    assert solver_runner.resolve_solver().is_file()


def test_ccx_path_is_recovered_when_the_process_copy_is_stale(monkeypatch):
    """A process keeps the environment it was given at launch.

    A desktop session or terminal started before CCX_PATH was set has a stale
    copy even though the variable is set on the machine. Falling back to the
    persisted value means the app works instead of demanding a restart. It is
    still CCX_PATH that is read; the path is never hard-coded.
    """
    monkeypatch.delenv("CCX_PATH", raising=False)

    assert solver_runner.resolve_solver().is_file()


def test_missing_ccx_path_is_reported_with_the_fix(monkeypatch):
    """Genuinely unset everywhere: fail loudly and say how to fix it."""
    monkeypatch.delenv("CCX_PATH", raising=False)
    monkeypatch.setattr(solver_runner, "_persisted_windows_value", lambda name: None)

    with pytest.raises(RuntimeError) as excinfo:
        solver_runner.resolve_solver()

    message = str(excinfo.value)
    assert "CCX_PATH is not set" in message
    assert "GetEnvironmentVariable" in message


def test_ccx_path_pointing_nowhere_is_reported(monkeypatch, tmp_path):
    monkeypatch.setenv("CCX_PATH", str(tmp_path / "not_here.exe"))

    with pytest.raises(RuntimeError, match="no file exists there"):
        solver_runner.resolve_solver()


def test_missing_input_deck_is_reported(tmp_path):
    with pytest.raises(FileNotFoundError, match="No CalculiX input deck"):
        solver_runner.run_analysis(tmp_path / "absent.inp")
