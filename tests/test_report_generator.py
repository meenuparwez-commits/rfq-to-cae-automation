"""Tests for src/report_generator.py.

The report is what someone reads instead of running anything, so these check
that it carries the things a reader needs to judge it: the units, the verdict,
the hand calculations beside the FE numbers, and the limitations. A report that
looks authoritative while omitting its assumptions is the failure mode worth
guarding against.

Most tests build an outcome directly rather than solving, so they are fast and
can exercise Fail and Review reports that a passing baseline cannot produce.
"""

from __future__ import annotations

import base64

import numpy as np
import pytest

from src import analytical, report_generator
from src.engineering_checks import ResultSummary, Verdict, VerdictResult
from src.geometry_checks import ADVISORY, CheckResult
from src.pipeline import RunArtifacts, RunOutcome
from src.result_reader import SectionStress
from src.schemas import BracketInputs


@pytest.fixture(scope="module")
def inputs() -> BracketInputs:
    return BracketInputs.from_json_file("examples/baseline_bracket.json")


@pytest.fixture(scope="module")
def reference(inputs):
    return analytical.compute_reference(inputs, inputs.resolved_material(), 0.0)


def make_outcome(
    inputs,
    reference,
    tmp_path,
    verdict: Verdict = Verdict.PASS,
    checks=None,
    failure_reason=None,
    with_summary: bool = True,
) -> RunOutcome:
    checks = checks or [CheckResult(name="Equilibrium", passed=True, message="ok")]

    summary = None
    if with_summary:
        section = SectionStress(
            position=reference.section_position,
            num_top_nodes=100,
            num_bottom_nodes=100,
            top_mean=reference.section_stress,
            bottom_mean=-reference.section_stress,
        )
        summary = ResultSummary(
            max_displacement=0.5912,
            tip_deflection=0.58754,
            max_von_mises=130.217,
            peak_location=(8.35, 2.62, 4.04),
            section=section,
            reactions=np.asarray([0.0, 0.0, inputs.applied_load]),
            stress_concentration=1.042,
            factor_of_safety_peak=2.112,
            factor_of_safety_section=4.406,
        )

    return RunOutcome(
        verdict=VerdictResult(verdict=verdict, failures=("a failure",) if verdict is Verdict.FAIL else ()),
        inputs=inputs,
        checks=checks,
        artifacts=RunArtifacts(directory=tmp_path),
        summary=summary,
        reference=reference if with_summary else None,
        log_lines=["2026-01-01 00:00:00 INFO    started"],
        failure_reason=failure_reason,
    )


# --- What every report must carry ----------------------------------------


def test_report_states_the_units(inputs, reference, tmp_path):
    """The unit system is stated wherever results go."""
    html = report_generator.build_report(make_outcome(inputs, reference, tmp_path))

    assert "mm, N, MPa" in html


def test_report_carries_the_disclaimer(inputs, reference, tmp_path):
    html = report_generator.build_report(make_outcome(inputs, reference, tmp_path))

    assert "independent engineering verification" in html
    assert "not suitable for product release or safety certification" in html


def test_report_lists_the_limitations(inputs, reference, tmp_path):
    """Specifically the ones that must always be stated."""
    html = report_generator.build_report(make_outcome(inputs, reference, tmp_path))

    for phrase in (
        "Linear elastic",
        "holes carry no load",
        "bolt preload",
        "fatigue",
        "mesh",
    ):
        assert phrase.lower() in html.lower(), f"missing: {phrase}"


def test_report_shows_the_verdict(inputs, reference, tmp_path):
    html = report_generator.build_report(make_outcome(inputs, reference, tmp_path))

    assert "Pass - every check satisfied" in html
    assert 'class="verdict pass"' in html


def test_a_failed_run_reports_its_reason(inputs, reference, tmp_path):
    outcome = make_outcome(
        inputs,
        reference,
        tmp_path,
        verdict=Verdict.FAIL,
        failure_reason="Meshing failed: something broke",
        with_summary=False,
    )

    html = report_generator.build_report(outcome)

    assert 'class="verdict fail"' in html
    assert "Meshing failed: something broke" in html


def test_a_review_run_is_styled_as_review(inputs, reference, tmp_path):
    outcome = make_outcome(inputs, reference, tmp_path, verdict=Verdict.REVIEW)

    html = report_generator.build_report(outcome)

    assert 'class="verdict review"' in html


# --- Results and their references ----------------------------------------


def test_hand_calculations_appear_next_to_the_fe_numbers(inputs, reference, tmp_path):
    """A result without its reference is an assertion, not evidence."""
    html = report_generator.build_report(make_outcome(inputs, reference, tmp_path))

    assert "0.58754" in html  # FE tip deflection
    assert "0.63492" in html  # beam bound
    assert "0.57778" in html  # plate bound


def test_report_says_the_peak_is_not_used_for_validation(inputs, reference, tmp_path):
    """Engineering decision 2, stated where a reader will see it."""
    html = report_generator.build_report(make_outcome(inputs, reference, tmp_path))

    assert "not validated against" in html
    assert "never fully converges" in html


def test_checks_are_tabulated_with_their_severity(inputs, reference, tmp_path):
    checks = [
        CheckResult(name="Equilibrium", passed=True, message="ok"),
        CheckResult(name="Mesh", passed=False, message="coarse", severity=ADVISORY),
        CheckResult(name="Factor of safety", passed=False, message="too low"),
    ]
    html = report_generator.build_report(
        make_outcome(inputs, reference, tmp_path, checks=checks)
    )

    assert '<span class="tag pass">Pass</span>' in html
    assert '<span class="tag warn">Review</span>' in html
    assert '<span class="tag fail">Fail</span>' in html
    assert "1 of 3 passed" in html


def test_inputs_are_recorded_so_the_run_can_be_reproduced(inputs, reference, tmp_path):
    html = report_generator.build_report(make_outcome(inputs, reference, tmp_path))

    assert "Structural steel S275" in html
    assert "250.00" in html  # applied load
    assert "tip_load" in html


def test_a_report_without_results_still_renders(inputs, reference, tmp_path):
    """A run that failed early has no results, and must still produce a report."""
    outcome = make_outcome(
        inputs, reference, tmp_path, verdict=Verdict.FAIL, with_summary=False
    )

    html = report_generator.build_report(outcome)

    assert "<h1>" in html
    assert "Results against hand calculation" not in html


# --- Embedded images ------------------------------------------------------


def test_images_are_embedded_not_linked(inputs, reference, tmp_path):
    """The report must survive being moved away from its run folder."""
    import pyvista as pv

    image_path = tmp_path / "stress.png"
    plotter = pv.Plotter(off_screen=True, window_size=(64, 64))
    plotter.add_mesh(pv.Sphere())
    plotter.screenshot(str(image_path))
    plotter.close()

    outcome = make_outcome(inputs, reference, tmp_path)
    outcome.artifacts.stress_image = image_path

    html = report_generator.build_report(outcome)

    assert "data:image/png;base64," in html
    assert str(image_path) not in html


def test_a_missing_image_is_skipped_rather_than_breaking_the_report(
    inputs, reference, tmp_path
):
    outcome = make_outcome(inputs, reference, tmp_path)
    outcome.artifacts.stress_image = tmp_path / "never_written.png"

    html = report_generator.build_report(outcome)

    assert "data:image/png;base64," not in html
    assert "<h1>" in html


def test_an_oversized_image_is_skipped(inputs, reference, tmp_path, monkeypatch):
    """Better a report without a picture than one too large to open."""
    big = tmp_path / "big.png"
    big.write_bytes(b"x" * 100)
    monkeypatch.setattr(report_generator, "MAX_EMBEDDED_IMAGE_BYTES", 10)

    assert report_generator._embed(big) is None


def test_embedded_data_is_valid_base64(tmp_path):
    path = tmp_path / "small.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"payload")

    encoded = report_generator._embed(path)

    assert base64.b64decode(encoded) == path.read_bytes()


# --- Writing --------------------------------------------------------------


def test_report_is_written_to_disk(inputs, reference, tmp_path):
    target = tmp_path / "nested" / "engineering_report.html"

    report_generator.write_report(make_outcome(inputs, reference, tmp_path), target)

    assert target.is_file()
    assert target.stat().st_size > 0
    assert "<!DOCTYPE html>" in target.read_text(encoding="utf-8")


def test_convergence_table_is_included_when_a_study_is_given(
    inputs, reference, tmp_path
):
    from src.engineering_checks import Verdict as V
    from src.pipeline import ConvergenceLevel, ConvergenceStudy

    study = ConvergenceStudy(
        levels=(
            ConvergenceLevel(2.0, 31769, 57033, 0.58684, 126.253, 62.519, V.REVIEW),
            ConvergenceLevel(1.5, 62886, 108160, 0.58754, 130.217, 62.413, V.PASS),
            ConvergenceLevel(1.25, 117673, 191807, 0.58779, 131.626, 62.547, V.PASS),
        ),
        converged=True,
        deflection_change=0.00042,
        stress_change=0.00214,
        comment="Both have settled.",
    )

    html = report_generator.build_report(
        make_outcome(inputs, reference, tmp_path), convergence=study
    )

    assert "Mesh convergence" in html
    assert "117,673" in html
    assert "Both have settled." in html
    # The first level has nothing to compare against.
    assert html.count("<td class=\"num\">-</td>") >= 2


def test_no_convergence_section_when_no_study_was_run(inputs, reference, tmp_path):
    html = report_generator.build_report(make_outcome(inputs, reference, tmp_path))

    assert "Mesh convergence" not in html


def test_the_log_is_escaped_not_injected(inputs, reference, tmp_path):
    """Log text goes into HTML, so it must not be able to close a tag."""
    outcome = make_outcome(inputs, reference, tmp_path)
    outcome.log_lines = ["<script>alert('x')</script>"]

    html = report_generator.build_report(outcome)

    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html
