"""Run the whole pipeline: inputs in, verdict and artefacts out.

Units: mm, N, MPa.

The alternative was putting the sequence in app.py, which would make the
alternative was putting the sequence inside app.py, which would make the whole
pipeline untestable without starting Streamlit, and would force the
report generator to duplicate it. The UI stays a thin layer over this.

Every stage is wrapped so a failure produces a human-readable reason and a Fail
verdict rather than a traceback in the interface. The
log is written as the run proceeds, so a crash still leaves evidence.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from src import (
    analytical,
    boundary_detection,
    cad_generator,
    calculix_writer,
    engineering_checks,
    geometry_checks,
    mesh_generator,
    result_reader,
    solver_runner,
    visualization,
)
from src.engineering_checks import Verdict, VerdictResult
from src.geometry_checks import CheckResult
from src.logging_utils import RunLogger
from src.schemas import BracketInputs, LoadCase

# Slab half-width for sampling the mid-span section, in mm. An unstructured
# mesh has no nodes exactly on a plane, so a thin band is sampled instead.
SECTION_SLAB_HALF_WIDTH = 1.5

ProgressCallback = Callable[[str, float], None]


def make_run_directory(root: str | Path) -> Path:
    """A fresh, uniquely named folder for one run.

    Runs are kept side by side rather than overwriting, so a result can always
    be traced back to the inputs that produced it. The timestamp makes them
    sort chronologically; the numeric suffix covers two runs starting within
    the same second.
    """
    root = Path(root)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    directory = root / f"run_{stamp}"
    suffix = 1
    while directory.exists():
        directory = root / f"run_{stamp}_{suffix}"
        suffix += 1

    directory.mkdir(parents=True)

    return directory


@dataclass
class RunArtifacts:
    """Files a run produced. Any of them may be absent if it failed early."""

    directory: Path
    inputs: Path | None = None
    step: Path | None = None
    mesh: Path | None = None
    deck: Path | None = None
    frd: Path | None = None
    dat: Path | None = None
    result_mesh: Path | None = None
    mesh_image: Path | None = None
    stress_image: Path | None = None
    displacement_image: Path | None = None
    drawing_dxf: Path | None = None
    drawing_pdf: Path | None = None
    log: Path | None = None
    summary: Path | None = None
    report: Path | None = None

    def existing(self) -> dict[str, Path]:
        return {
            name: value
            for name, value in vars(self).items()
            if name != "directory" and isinstance(value, Path) and value.is_file()
        }


@dataclass
class RunOutcome:
    """Everything the interface and the report need from one run."""

    verdict: VerdictResult
    inputs: BracketInputs | None = None
    checks: list[CheckResult] = field(default_factory=list)
    input_warnings: list[str] = field(default_factory=list)
    artifacts: RunArtifacts | None = None
    summary: engineering_checks.ResultSummary | None = None
    reference: analytical.AnalyticalReference | None = None
    mesh_stats: mesh_generator.MeshStats | None = None
    solver_run: solver_runner.SolverRun | None = None
    log_lines: list[str] = field(default_factory=list)
    failure_reason: str | None = None
    elapsed_seconds: float = 0.0

    @property
    def passed(self) -> bool:
        return self.verdict.verdict is Verdict.PASS


def run_pipeline(
    inputs: BracketInputs,
    output_dir: str | Path | None = None,
    progress: ProgressCallback | None = None,
    render_images: bool = True,
    solver_threads: int | None = None,
    write_report: bool = True,
    make_drawing: bool = True,
) -> RunOutcome:
    """Build, mesh, solve, check and judge one bracket.

    Never raises for an engineering or tooling failure: those come back as a
    Fail verdict with a reason. Only a programming error would propagate.
    """
    started = time.perf_counter()

    # An explicit directory is used as given, which keeps tests and scripts in
    # control of where their files land. Otherwise a fresh run folder is
    # created under the configured output root, so repeated runs from the
    # interface never overwrite one another.
    if output_dir is not None:
        directory = Path(output_dir)
        directory.mkdir(parents=True, exist_ok=True)
    else:
        directory = make_run_directory(inputs.output_dir)

    artifacts = RunArtifacts(directory=directory)
    checks: list[CheckResult] = []

    logger = RunLogger(path=directory / "run_log.txt")
    artifacts.log = logger.path

    def report(stage: str, fraction: float) -> None:
        logger.section(stage)
        if progress is not None:
            progress(stage, fraction)

    def finish(reason: str | None = None) -> RunOutcome:
        elapsed = time.perf_counter() - started
        if reason:
            logger.error(reason)

        verdict = (
            VerdictResult(verdict=Verdict.FAIL, failures=(reason,))
            if reason
            else engineering_checks.decide_verdict(checks, warnings)
        )

        logger.info(f"Verdict: {verdict.headline}")
        logger.info(f"Total time {elapsed:.1f} s")

        outcome = RunOutcome(
            verdict=verdict,
            inputs=inputs,
            checks=checks,
            input_warnings=warnings,
            artifacts=artifacts,
            summary=summary,
            reference=reference,
            mesh_stats=mesh_stats,
            solver_run=solver_run,
            log_lines=list(logger.lines),
            failure_reason=reason,
            elapsed_seconds=elapsed,
        )

        # A report is written even for a failed run: the reason a run failed is
        # exactly what someone will want to read afterwards.
        if write_report:
            try:
                from src import report_generator

                artifacts.report = report_generator.write_report(
                    outcome, directory / "engineering_report.html"
                )
                logger.info(f"Report written to {artifacts.report.name}")
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"Report could not be written: {exc}")

        outcome.log_lines = list(logger.lines)
        logger.close()

        return outcome

    warnings: list[str] = []
    summary = None
    reference = None
    mesh_stats = None
    solver_run = None

    # --- Inputs ----------------------------------------------------------
    report("Inputs", 0.02)
    try:
        material = inputs.resolved_material()
    except Exception as exc:  # noqa: BLE001 - reported, not swallowed
        return finish(f"Material could not be resolved: {exc}")

    warnings = inputs.warnings()
    artifacts.inputs = directory / "inputs.json"
    artifacts.inputs.write_text(
        json.dumps(json.loads(inputs.model_dump_json()), indent=2), encoding="utf-8"
    )

    logger.info(f"Material: {material.name}, E {material.youngs_modulus} MPa, "
                f"nu {material.poissons_ratio}, yield {material.yield_strength} MPa")
    logger.info(f"Load case {inputs.load_case.value}, {inputs.applied_load} N")
    for note in warnings:
        logger.warning(f"Input warning: {note}")

    # --- Geometry --------------------------------------------------------
    report("Geometry", 0.10)
    try:
        bracket = cad_generator.build_from_inputs(inputs)
        artifacts.step = cad_generator.export_step(
            bracket, directory / "bracket_geometry.step"
        )
    except Exception as exc:  # noqa: BLE001
        return finish(f"CAD generation failed: {exc}")

    try:
        geometry_results = geometry_checks.run_checks_for_inputs(
            bracket, inputs, artifacts.step
        )
    except Exception as exc:  # noqa: BLE001
        return finish(f"Geometry checks failed to run: {exc}")

    checks.extend(geometry_results)
    for result in geometry_results:
        logger.check(result)

    # --- Drawing ---------------------------------------------------------
    if make_drawing:
        report("Drawing", 0.16)
        try:
            from src import drawing

            sheet = drawing.create_drawing(
                bracket,
                inputs,
                directory / "bracket_drawing.dxf",
                directory / "bracket_drawing.pdf",
                material_name=material.name,
            )
            artifacts.drawing_dxf = sheet.dxf_path
            artifacts.drawing_pdf = sheet.pdf_path

            # The dimension check IS part of the run: it compares the drawn
            # geometry with the validated inputs, so a mismatch means the solid
            # and the inputs have diverged.
            checks.append(sheet.check)
            logger.check(sheet.check)
            logger.info(str(sheet))
        except Exception as exc:  # noqa: BLE001
            # A drawing that fails to render does not invalidate the analysis,
            # so this warns rather than ending the run - the same reasoning as
            # the result images.
            logger.warning(f"Drawing could not be produced: {exc}")

    # --- Mesh ------------------------------------------------------------
    report("Meshing", 0.20)
    try:
        mesh_stats = mesh_generator.generate_mesh(
            artifacts.step, inputs.mesh_size, directory / "bracket_mesh.msh"
        )
        artifacts.mesh = mesh_stats.msh_path
        mesh = mesh_generator.read_mesh(artifacts.mesh)
    except Exception as exc:  # noqa: BLE001
        return finish(f"Meshing failed: {exc}")

    mesh_results = mesh_generator.run_mesh_checks(
        mesh_stats, inputs.thickness, inputs.fillet_radius
    )
    checks.extend(mesh_results)
    for result in mesh_results:
        logger.check(result)

    logger.info(
        f"Mesh: {mesh_stats.num_nodes} nodes, {mesh_stats.num_elements} "
        f"{mesh_stats.element_type}"
    )

    # --- Boundary conditions ---------------------------------------------
    report("Boundary conditions", 0.32)
    try:
        fixed = boundary_detection.detect_fixed_face(mesh, inputs)
        load_face = boundary_detection.detect_load_face(mesh, inputs)
        forces = boundary_detection.consistent_nodal_forces(
            load_face, inputs.applied_load
        )
    except Exception as exc:  # noqa: BLE001
        return finish(f"Boundary detection failed: {exc}")

    boundary_results = boundary_detection.run_boundary_checks(
        mesh, inputs, fixed, load_face, forces
    )
    checks.extend(boundary_results)
    for result in boundary_results:
        logger.check(result)

    # --- Solve -----------------------------------------------------------
    report("Writing solver input", 0.38)
    try:
        deck = calculix_writer.write_input_deck(
            directory / "bracket_analysis.inp", mesh, inputs, material, fixed, forces
        )
        artifacts.deck = deck.path
        logger.info(str(deck))
    except Exception as exc:  # noqa: BLE001
        return finish(f"Could not write the CalculiX input deck: {exc}")

    report("Solving", 0.45)
    try:
        solver_run = solver_runner.run_analysis(
            artifacts.deck, num_threads=solver_threads
        )
    except Exception as exc:  # noqa: BLE001
        return finish(f"CalculiX could not be run: {exc}")

    artifacts.frd = solver_run.frd_path
    artifacts.dat = solver_run.dat_path

    expected_equations = (mesh.num_nodes - fixed.num_nodes) * 3
    solver_results = solver_runner.run_solver_checks(solver_run, expected_equations)
    checks.extend(solver_results)
    for result in solver_results:
        logger.check(result)

    if not solver_run.succeeded:
        return finish(
            "The solver did not complete successfully; results were not read. "
            "See the solver checks above."
        )

    # --- Results ---------------------------------------------------------
    report("Reading results", 0.75)
    try:
        results = result_reader.read_frd(artifacts.frd, mesh.num_nodes)
        reactions = result_reader.read_reaction_total(artifacts.dat)
    except Exception as exc:  # noqa: BLE001
        return finish(f"Results could not be read: {exc}")

    load_start = (
        0.0 if inputs.load_case is LoadCase.TIP_LOAD else inputs.fillet_radius
    )
    reference = analytical.compute_reference(inputs, material, load_start)

    try:
        tip = result_reader.tip_deflection(
            results, mesh.points, inputs.thickness + inputs.arm_length
        )
        section = result_reader.section_stress(
            results,
            mesh.points,
            inputs.thickness + reference.section_position,
            inputs.thickness,
            slab_half_width=SECTION_SLAB_HALF_WIDTH,
        )
    except Exception as exc:  # noqa: BLE001
        return finish(f"Results could not be sampled: {exc}")

    summary = engineering_checks.summarise(
        results, mesh.points, tip, section, reactions, reference, material, inputs
    )

    result_results = engineering_checks.run_result_checks(
        summary, reference, inputs.applied_load, inputs.target_factor_of_safety
    )
    checks.extend(result_results)
    for result in result_results:
        logger.check(result)

    # --- Pictures --------------------------------------------------------
    if render_images:
        report("Rendering", 0.88)
        try:
            grid = visualization.build_result_grid(mesh, results)
            artifacts.result_mesh = visualization.write_result_mesh(
                grid, directory / "bracket_results.vtu"
            )
            artifacts.mesh_image = visualization.render_mesh(
                artifacts.mesh, directory / "mesh.png"
            ).path
            artifacts.stress_image = visualization.render_stress(
                grid, directory / "stress.png"
            ).path
            artifacts.displacement_image = visualization.render_displacement(
                grid, directory / "displacement.png"
            ).path
        except Exception as exc:  # noqa: BLE001
            # Pictures are presentation, not evidence. Losing them must not
            # discard a valid analysis, so this is logged and the run goes on.
            logger.warning(f"Rendering failed, results are unaffected: {exc}")

    # --- Summary ---------------------------------------------------------
    report("Summary", 0.96)
    artifacts.summary = directory / "summary.json"
    artifacts.summary.write_text(
        json.dumps(
            _summary_dict(inputs, material, mesh_stats, summary, reference, checks),
            indent=2,
        ),
        encoding="utf-8",
    )

    return finish()


# --- Mesh convergence -----------------------------------------------------

# Relative change between the two finest levels below which a quantity is
# treated as converged. 2% is a conventional engineering threshold for a
# displacement; tighter would be meaningless against the other approximations
# already in the model.
CONVERGENCE_REL_TOL = 0.02


@dataclass(frozen=True)
class ConvergenceLevel:
    """One mesh level of a convergence study."""

    mesh_size: float
    num_elements: int
    num_nodes: int
    tip_deflection: float
    max_von_mises: float
    # The fillet peak, with the clamp singularity set aside. Both are kept
    # because they diverge for different reasons and at different rates: the
    # clamp edge is a true singularity, while the fillet peak is a real
    # concentration that merely converges slowly.
    structural_peak: float
    section_stress: float
    verdict: Verdict


@dataclass(frozen=True)
class ConvergenceStudy:
    """Three mesh levels and what they say about convergence."""

    levels: tuple[ConvergenceLevel, ...]
    converged: bool
    deflection_change: float
    stress_change: float
    comment: str

    def as_check(self) -> CheckResult:
        from src.geometry_checks import ADVISORY

        return CheckResult(
            name="Mesh convergence",
            passed=self.converged,
            message=self.comment,
            value=self.deflection_change,
            expected=CONVERGENCE_REL_TOL,
            # Advisory: an unconverged study means the numbers need a closer
            # look, not that they are wrong. "Convergence not
            # reached" under Review.
            severity=ADVISORY,
        )


def run_convergence_study(
    inputs: BracketInputs,
    mesh_sizes: tuple[float, ...],
    output_root: str | Path,
    progress: ProgressCallback | None = None,
    solver_threads: int | None = None,
) -> ConvergenceStudy:
    """Solve the same design at several mesh sizes and compare.

    A single mesh tells you what that mesh says, not what the structure does.
    Refining and watching the answer settle is the only way to tell the two
    apart.

    Convergence is judged on tip deflection and on the bending stress away from
    the root. The peak stress is reported but deliberately not used: it sits in
    the fillet concentration and keeps creeping upwards with refinement, so
    requiring it to settle would mean no mesh ever converges.
    """
    root = Path(output_root)
    levels: list[ConvergenceLevel] = []

    for index, size in enumerate(sorted(mesh_sizes, reverse=True)):
        if progress is not None:
            progress(f"Convergence level {index + 1} of {len(mesh_sizes)} "
                     f"({size} mm)", (index + 1) / (len(mesh_sizes) + 1))

        outcome = run_pipeline(
            inputs.model_copy(update={"mesh_size": size}),
            root / f"convergence_{size:g}mm",
            render_images=False,
            solver_threads=solver_threads,
            # The geometry is identical at every level; only the mesh changes.
            # Drawing it three times would cost time and produce three
            # identical sheets.
            make_drawing=False,
        )

        if outcome.summary is None or outcome.mesh_stats is None:
            raise RuntimeError(
                f"Convergence level at {size} mm did not produce results: "
                f"{outcome.failure_reason or outcome.verdict.headline}"
            )

        levels.append(
            ConvergenceLevel(
                mesh_size=size,
                num_elements=outcome.mesh_stats.num_elements,
                num_nodes=outcome.mesh_stats.num_nodes,
                tip_deflection=outcome.summary.tip_deflection,
                max_von_mises=outcome.summary.max_von_mises,
                structural_peak=outcome.summary.max_von_mises_structural,
                section_stress=outcome.summary.section.magnitude,
                verdict=outcome.verdict.verdict,
            )
        )

    if len(levels) < 2:
        raise ValueError("A convergence study needs at least two mesh levels.")

    finest, previous = levels[-1], levels[-2]

    deflection_change = abs(
        finest.tip_deflection - previous.tip_deflection
    ) / previous.tip_deflection
    stress_change = abs(
        finest.section_stress - previous.section_stress
    ) / previous.section_stress
    peak_change = abs(
        finest.max_von_mises - previous.max_von_mises
    ) / previous.max_von_mises
    fillet_change = abs(
        finest.structural_peak - previous.structural_peak
    ) / previous.structural_peak

    converged = (
        deflection_change <= CONVERGENCE_REL_TOL
        and stress_change <= CONVERGENCE_REL_TOL
    )

    comment = (
        f"Between the two finest meshes ({previous.mesh_size:g} mm and "
        f"{finest.mesh_size:g} mm) the tip deflection changed by "
        f"{deflection_change:.2%} and the bending stress away from the root by "
        f"{stress_change:.2%}, against a threshold of "
        f"{CONVERGENCE_REL_TOL:.0%}. "
        + (
            "Both have settled, so the answer is a property of the structure "
            "rather than of the mesh. "
            if converged
            else "At least one is still moving, so the mesh is still "
            "influencing the answer. "
        )
        + f"The two peaks behave differently and neither is judged on. The "
        f"fillet peak changed by {fillet_change:.2%}: a real stress "
        "concentration, converging but slowly. The peak at the clamped edge "
        f"changed by {peak_change:.2%} and is accelerating, because "
        "restraining a sharp-edged ring of a continuum has no finite answer "
        "to converge to. Refining the mesh makes that number worse for ever, "
        "which is exactly why the verdict does not use it."
    )

    return ConvergenceStudy(
        levels=tuple(levels),
        converged=converged,
        deflection_change=deflection_change,
        stress_change=stress_change,
        comment=comment,
    )


def _summary_dict(inputs, material, mesh_stats, summary, reference, checks) -> dict:
    """Machine-readable record of one run."""
    return {
        "disclaimer": (
            "Educational proof of concept. Results require independent "
            "engineering verification and are not suitable for product release "
            "or safety certification."
        ),
        "units": {
            "length": "mm",
            "force": "N",
            "stress": "MPa (N/mm^2)",
            "density": "tonne/mm^3",
            "mass": "kg",
        },
        "inputs": json.loads(inputs.model_dump_json()),
        "material": json.loads(material.model_dump_json()),
        "mesh": {
            "element_type": mesh_stats.element_type,
            "nodes": mesh_stats.num_nodes,
            "elements": mesh_stats.num_elements,
            "min_quality": mesh_stats.min_quality,
            "mean_edge_length": mesh_stats.mean_edge_length,
        },
        "results": {
            "max_displacement_mm": summary.max_displacement,
            "tip_deflection_mm": summary.tip_deflection,
            "max_von_mises_mpa": summary.max_von_mises,
            "peak_location_mm": list(summary.peak_location),
            "reactions_n": [float(value) for value in summary.reactions],
            "stress_concentration_kt": summary.stress_concentration,
            "factor_of_safety_peak": summary.factor_of_safety_peak,
            "factor_of_safety_section": summary.factor_of_safety_section,
        },
        "analytical": {
            "second_moment_mm4": reference.second_moment,
            "root_moment_nmm": reference.root_moment,
            "root_stress_mpa": reference.root_stress,
            "section_position_mm": reference.section_position,
            "section_stress_mpa": reference.section_stress,
            "tip_deflection_beam_mm": reference.tip_deflection_beam,
            "tip_deflection_plate_mm": reference.tip_deflection_plate,
        },
        "checks": [
            {
                "name": check.name,
                "passed": check.passed,
                "severity": check.severity,
                "message": check.message,
            }
            for check in checks
        ],
    }
