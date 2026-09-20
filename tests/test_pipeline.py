"""Tests for src/pipeline.py.

Units: mm, N, MPa.

The pipeline is the thing the interface calls, so the contract that matters is:
it never raises for an engineering or tooling failure, it always returns a
verdict with a reason, and it writes the artefacts it claims to.

A coarse mesh is used to keep a full run to a few seconds. That mesh
deliberately fails the through-thickness check, which makes it useful in its
own right: it exercises the Review path that a passing baseline cannot.
"""

from __future__ import annotations

import json

import pytest

from src import pipeline
from src.engineering_checks import Verdict
from src.pipeline import run_pipeline
from src.schemas import BracketInputs

COARSE_SIZE = 6.0


@pytest.fixture(scope="module")
def baseline() -> BracketInputs:
    return BracketInputs.from_json_file("examples/baseline_bracket.json")


@pytest.fixture(scope="module")
def coarse_run(baseline, tmp_path_factory):
    """A full run on a mesh too coarse to resolve the thickness."""
    inputs = baseline.model_copy(update={"mesh_size": COARSE_SIZE})
    directory = tmp_path_factory.mktemp("coarse_run")

    return run_pipeline(inputs, directory, render_images=False, solver_threads=4)


# --- A complete run -------------------------------------------------------


def test_a_coarse_run_completes_and_returns_a_verdict(coarse_run):
    assert coarse_run.verdict.verdict in set(Verdict)
    assert coarse_run.failure_reason is None
    assert coarse_run.elapsed_seconds > 0.0


def test_a_coarse_mesh_produces_review_not_pass(coarse_run):
    """The mesh is too coarse to trust, but the run is otherwise sound.

    This is the case the severity split exists for: reporting Fail would be
    wrong, and reporting Pass would be worse.
    """
    assert coarse_run.verdict.verdict is Verdict.REVIEW
    assert any("through thickness" in item.lower() for item in coarse_run.verdict.advisories)


def test_the_critical_checks_still_pass_on_a_coarse_run(coarse_run):
    """A coarse mesh must not break equilibrium or the geometry checks."""
    critical = [
        check for check in coarse_run.checks if not check.is_advisory
    ]

    failed = [str(check) for check in critical if not check.passed]
    assert not failed, "\n".join(failed)


def test_every_stage_contributed_checks(coarse_run):
    names = {check.name for check in coarse_run.checks}

    for expected in (
        "CAD validity",
        "Element type",
        "Area of washer annuli (x = 0)",
        "Solver completion",
        "Equilibrium",
        "Factor of safety",
    ):
        assert expected in names, f"missing {expected}"


def test_results_are_summarised(coarse_run):
    summary = coarse_run.summary

    assert summary is not None
    assert summary.max_von_mises > 0.0
    assert summary.tip_deflection > 0.0
    assert summary.factor_of_safety_peak > 0.0


def test_inputs_are_carried_on_the_outcome(coarse_run):
    """The report and the interface both need them; re-reading is a bug source."""
    assert coarse_run.inputs is not None
    assert coarse_run.inputs.mesh_size == COARSE_SIZE


# --- Artefacts ------------------------------------------------------------


def test_the_expected_files_are_written(coarse_run):
    produced = coarse_run.artifacts.existing()

    for expected in ("inputs", "step", "mesh", "deck", "frd", "dat", "log", "summary"):
        assert expected in produced, f"{expected} was not written"


def test_inputs_json_round_trips(coarse_run):
    """The saved inputs must rebuild the same model, or a run is not repeatable."""
    saved = json.loads(coarse_run.artifacts.inputs.read_text(encoding="utf-8"))
    rebuilt = BracketInputs(**saved)

    assert rebuilt.mesh_size == coarse_run.inputs.mesh_size
    assert rebuilt.applied_load == coarse_run.inputs.applied_load


def test_summary_json_records_units_and_the_disclaimer(coarse_run):
    """The unit system is stated wherever results go."""
    summary = json.loads(coarse_run.artifacts.summary.read_text(encoding="utf-8"))

    assert summary["units"]["stress"].startswith("MPa")
    assert "independent engineering verification" in summary["disclaimer"]
    assert summary["results"]["max_von_mises_mpa"] > 0.0
    assert len(summary["checks"]) == len(coarse_run.checks)


def test_summary_json_carries_the_verdict_driving_values(coarse_run):
    """Other software reads this file, and it must not have to parse prose.

    It previously published only the raw clamp singularity, so a machine could
    see a stress of 240 MPa and a factor of safety of 1.14 beside a passing
    run, with the structural numbers available nowhere but inside English
    check messages. Same defect class as the report, one artefact further out.
    """
    summary = json.loads(coarse_run.artifacts.summary.read_text(encoding="utf-8"))
    results = summary["results"]
    expected = coarse_run.summary

    assert results["factor_of_safety_structural"] == pytest.approx(
        expected.factor_of_safety_structural
    )
    assert results["max_von_mises_structural_mpa"] == pytest.approx(
        expected.max_von_mises_structural
    )
    assert results["factor_of_safety_raw"] == pytest.approx(
        expected.factor_of_safety_peak
    )

    # The file must say which number the verdict was made from, rather than
    # leaving a reader to guess between two factors of safety.
    assert results["verdict_uses"] == "factor_of_safety_structural"
    assert "singularity" in results["raw_peak_note"]

    # The retained keys hold the raw peak. Their meaning is fixed, so anything
    # already reading them keeps working instead of silently changing.
    assert results["max_von_mises_mpa"] == pytest.approx(expected.max_von_mises)
    assert results["factor_of_safety_peak"] == pytest.approx(
        expected.factor_of_safety_peak
    )


def test_the_log_file_holds_the_run(coarse_run):
    text = coarse_run.artifacts.log.read_text(encoding="utf-8")

    assert "Verdict:" in text
    assert "mm, N, MPa" in text
    assert coarse_run.log_lines


def test_images_are_skipped_when_not_requested(coarse_run):
    produced = coarse_run.artifacts.existing()

    assert "stress_image" not in produced
    assert "displacement_image" not in produced


# --- Drawing --------------------------------------------------------------


def test_the_drawing_is_produced_as_part_of_a_run(coarse_run):
    produced = coarse_run.artifacts.existing()

    assert "drawing_dxf" in produced
    assert "drawing_pdf" in produced


def test_the_drawing_dimension_check_is_part_of_the_run(coarse_run):
    """A mismatch means the solid and the inputs have diverged, which matters."""
    names = [check.name for check in coarse_run.checks]

    assert "Drawing dimensions" in names

    check = next(c for c in coarse_run.checks if c.name == "Drawing dimensions")
    assert check.passed, check.message
    assert not check.is_advisory


def test_the_drawing_can_be_skipped(baseline, tmp_path):
    inputs = baseline.model_copy(
        update={"mesh_size": COARSE_SIZE, "material": "unobtainium"}
    )

    outcome = run_pipeline(
        inputs, tmp_path, render_images=False, make_drawing=False
    )

    assert "drawing_dxf" not in outcome.artifacts.existing()


def test_convergence_levels_do_not_redraw_the_same_geometry(study):
    """The geometry is identical at every level; only the mesh changes."""
    assert all(level.num_elements > 0 for level in study.levels)


# --- Failing safely -------------------------------------------------------


def test_an_unknown_material_fails_with_a_reason_not_a_traceback(
    baseline, tmp_path
):
    """Fail safely, with a human-readable reason."""
    inputs = baseline.model_copy(update={"material": "unobtainium"})

    outcome = run_pipeline(inputs, tmp_path, render_images=False)

    assert outcome.verdict.verdict is Verdict.FAIL
    assert outcome.failure_reason is not None
    assert "unobtainium" in outcome.failure_reason
    assert "structural_steel_s275" in outcome.failure_reason


def test_a_failed_run_still_writes_its_log(baseline, tmp_path):
    """The log is most valuable exactly when the run did not finish."""
    inputs = baseline.model_copy(update={"material": "unobtainium"})

    outcome = run_pipeline(inputs, tmp_path, render_images=False)

    assert outcome.artifacts.log.is_file()
    assert "ERROR" in outcome.artifacts.log.read_text(encoding="utf-8")


def test_a_failed_run_reports_the_reason_in_the_verdict(baseline, tmp_path):
    inputs = baseline.model_copy(update={"material": "unobtainium"})

    outcome = run_pipeline(inputs, tmp_path, render_images=False)

    assert outcome.verdict.failures
    assert not outcome.passed


def test_progress_is_reported_through_the_callback(baseline, tmp_path):
    """The interface needs this; a silent minute reads as a hang."""
    seen: list[tuple[str, float]] = []
    inputs = baseline.model_copy(update={"material": "unobtainium"})

    run_pipeline(
        inputs,
        tmp_path,
        progress=lambda stage, fraction: seen.append((stage, fraction)),
        render_images=False,
    )

    assert seen
    assert all(0.0 <= fraction <= 1.0 for _, fraction in seen)
    assert seen[0][0] == "Inputs"


def test_the_output_directory_is_created(baseline, tmp_path):
    target = tmp_path / "nested" / "run_042"
    inputs = baseline.model_copy(update={"material": "unobtainium"})

    run_pipeline(inputs, target, render_images=False)

    assert target.is_dir()


# --- Run folders ----------------------------------------------------------


def test_each_run_gets_its_own_folder(tmp_path):
    """Runs must not overwrite each other, or a result cannot be traced back."""
    first = pipeline.make_run_directory(tmp_path)
    second = pipeline.make_run_directory(tmp_path)

    assert first.is_dir() and second.is_dir()
    assert first != second
    assert first.name.startswith("run_")


def test_run_folders_sort_chronologically(tmp_path):
    first = pipeline.make_run_directory(tmp_path)
    second = pipeline.make_run_directory(tmp_path)

    assert sorted([first.name, second.name]) == [first.name, second.name]


def test_a_run_without_an_explicit_directory_creates_one(baseline, tmp_path):
    inputs = baseline.model_copy(
        update={"material": "unobtainium", "output_dir": tmp_path}
    )

    outcome = run_pipeline(inputs, render_images=False)

    assert outcome.artifacts.directory.parent == tmp_path
    assert outcome.artifacts.directory.name.startswith("run_")


# --- Report ---------------------------------------------------------------


def test_a_report_is_written_for_a_successful_run(coarse_run):
    assert coarse_run.artifacts.report is not None
    assert coarse_run.artifacts.report.is_file()
    assert "<!DOCTYPE html>" in coarse_run.artifacts.report.read_text(encoding="utf-8")


def test_a_report_is_written_even_when_the_run_failed(baseline, tmp_path):
    """The reason a run failed is exactly what someone will want to read."""
    inputs = baseline.model_copy(update={"material": "unobtainium"})

    outcome = run_pipeline(inputs, tmp_path, render_images=False)

    assert outcome.artifacts.report.is_file()
    assert "unobtainium" in outcome.artifacts.report.read_text(encoding="utf-8")


# --- Convergence ----------------------------------------------------------


@pytest.fixture(scope="module")
def study(baseline, tmp_path_factory):
    """Two coarse levels, for speed. The physics is checked, not the mesh."""
    return pipeline.run_convergence_study(
        baseline,
        mesh_sizes=(8.0, 6.0),
        output_root=tmp_path_factory.mktemp("convergence"),
        solver_threads=4,
    )


def test_convergence_study_runs_every_level(study):
    assert len(study.levels) == 2
    assert all(level.num_elements > 0 for level in study.levels)


def test_levels_are_ordered_coarse_to_fine(study):
    """Refining must increase the element count, or the study means nothing."""
    sizes = [level.mesh_size for level in study.levels]
    counts = [level.num_elements for level in study.levels]

    assert sizes == sorted(sizes, reverse=True)
    assert counts == sorted(counts)


def test_convergence_reports_the_change_between_the_finest_levels(study):
    assert study.deflection_change >= 0.0
    assert study.stress_change >= 0.0
    assert "threshold" in study.comment


def test_convergence_is_judged_on_settled_quantities_not_the_peak(study):
    """Engineering decision 2 again, now with two peaks that misbehave.

    The fillet concentration converges slowly; the clamped edge does not
    converge at all. Requiring either to settle would mean no mesh ever
    passes, so the comment has to tell them apart rather than lumping them
    together as "the peak".
    """
    assert "fillet peak changed by" in study.comment
    assert "clamped edge" in study.comment
    assert "the verdict does not use it" in study.comment


def test_convergence_produces_an_advisory_check(study):
    check = study.as_check()

    assert check.name == "Mesh convergence"
    assert check.is_advisory


def test_a_single_level_is_refused(baseline, tmp_path):
    with pytest.raises(ValueError, match="at least two mesh levels"):
        pipeline.run_convergence_study(
            baseline, mesh_sizes=(8.0,), output_root=tmp_path, solver_threads=4
        )
