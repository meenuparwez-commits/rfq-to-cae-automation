"""Streamlit interface: enter a design, press one button, get a verdict.

Educational proof of concept. Results require independent engineering
verification and are not suitable for product release or safety certification.

Units throughout: mm, N, MPa (N/mm^2), tonne/mm^3, mass in kg.

This file is deliberately thin. All the engineering lives in src/pipeline.py so
it can be tested without starting a browser; anything added here that cannot be
reached from a test is a mistake.

Run with:
    streamlit run app.py
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st
from pydantic import ValidationError

from src.engineering_checks import Verdict
from src.pipeline import run_pipeline
from src.schemas import BracketInputs, LoadCase, load_materials
from src.sketch import sketch_svg

DEFAULTS_PATH = Path("config/default_inputs.json")
OUTPUT_ROOT = Path("outputs/app_run")

VERDICT_STYLE = {
    Verdict.PASS: ("✅", "success"),
    Verdict.REVIEW: ("⚠️", "warning"),
    Verdict.FAIL: ("❌", "error"),
}


st.set_page_config(page_title="Bracket CAD-to-CAE", layout="wide")


@st.cache_data
def material_options() -> dict[str, str]:
    """Material keys mapped to their display names."""
    return {key: value.name for key, value in load_materials().items()}


@st.cache_data
def defaults() -> dict:
    import json

    return json.loads(DEFAULTS_PATH.read_text(encoding="utf-8"))


def sidebar_inputs(preset: dict) -> dict:
    """Collect the design inputs. Returns a raw dict for the schema to judge.

    Deliberately returns unvalidated values: the Pydantic schema is the single
    place that decides what is acceptable. Duplicating limits in the widgets
    would let the two definitions drift apart.
    """
    st.sidebar.header("Geometry (mm)")
    geometry = {
        "plate_height": st.sidebar.number_input(
            "Plate height H", value=float(preset["plate_height"]), step=5.0
        ),
        "arm_length": st.sidebar.number_input(
            "Arm length L (free, from plate face)",
            value=float(preset["arm_length"]),
            step=5.0,
        ),
        "width": st.sidebar.number_input(
            "Width b", value=float(preset["width"]), step=5.0
        ),
        "thickness": st.sidebar.number_input(
            "Thickness t", value=float(preset["thickness"]), step=0.5
        ),
        "fillet_radius": st.sidebar.number_input(
            "Inside fillet radius r", value=float(preset["fillet_radius"]), step=0.5
        ),
    }

    st.sidebar.header("Holes")
    holes = {
        "hole_diameter": st.sidebar.number_input(
            "Hole diameter", value=float(preset["hole_diameter"]), step=0.5
        ),
        "hole_spacing": st.sidebar.number_input(
            "Hole spacing (centre to centre)",
            value=float(preset["hole_spacing"]),
            step=1.0,
        ),
        "num_holes": st.sidebar.selectbox(
            "Number of holes",
            options=[2, 4],
            index=[2, 4].index(int(preset["num_holes"])),
        ),
        "washer_diameter": st.sidebar.number_input(
            "Washer diameter (clamped ring)",
            value=float(preset["washer_diameter"]),
            step=1.0,
            help="The bolts hold the plate only under their washers. This "
            "outside diameter is the whole restraint, so it changes the "
            "stiffness and the stresses, not just the drawing.",
        ),
    }

    st.sidebar.header("Load and material")
    options = material_options()
    keys = list(options)
    analysis = {
        "material": st.sidebar.selectbox(
            "Material",
            options=keys,
            index=keys.index(preset["material"]),
            format_func=lambda key: options[key],
        ),
        "applied_load": st.sidebar.number_input(
            "Applied load F (N)", value=float(preset["applied_load"]), step=50.0
        ),
        "load_case": st.sidebar.selectbox(
            "Load case",
            options=[case.value for case in LoadCase],
            index=[case.value for case in LoadCase].index(preset["load_case"]),
            help="Tip load acts on the end face; UDL is spread over the top of "
            "the arm. The analytical comparison follows whichever is chosen.",
        ),
        "target_factor_of_safety": st.sidebar.number_input(
            "Target factor of safety",
            value=float(preset["target_factor_of_safety"]),
            step=0.25,
        ),
    }

    st.sidebar.header("Mesh")
    mesh = {
        "mesh_size": st.sidebar.number_input(
            "Element size (mm)",
            value=float(preset["mesh_size"]),
            step=0.25,
            help="At least two elements through the thickness are needed, so "
            "aim below t/2. A coarse mesh overestimates stiffness.",
        )
    }

    return {**geometry, **holes, **analysis, **mesh, "output_dir": str(OUTPUT_ROOT)}


def show_sketch(inputs: BracketInputs, *, expanded: bool) -> None:
    """A dimensioned schematic of whatever is currently in the sidebar.

    Open before a run, because that is when someone is working out what to
    type; collapsed afterwards so the verdict stays at the top of the page.
    """
    with st.expander("What the inputs mean", expanded=expanded):
        st.image(sketch_svg(inputs), width="stretch")
        st.caption(
            "Redrawn from the sidebar as you change it. Green is the "
            "restraint — **the plate is held only under its washers**, not "
            "across the whole rear face — and red is the applied load. Note "
            "that **L is the free length from the front face of the plate**, "
            "not the overall extent, and that the holes sit in the band above "
            "the fillet, which is why a large fillet or a wide spacing can be "
            "rejected. This is a schematic drawn from the numbers; the "
            "dimensioned drawing produced during a run is measured off the "
            "solid itself."
        )


def show_verdict(outcome) -> None:
    """The headline answer, then the reasons behind it."""
    icon, style = VERDICT_STYLE[outcome.verdict.verdict]
    banner = getattr(st, style)
    banner(f"{icon}  {outcome.verdict.headline}")

    if outcome.failure_reason:
        st.error(outcome.failure_reason)

    for item in outcome.verdict.failures:
        st.error(item)
    for item in outcome.verdict.advisories:
        st.warning(item)
    for item in outcome.verdict.warnings:
        st.warning(f"Input warning: {item}")


def show_numbers(outcome) -> None:
    """Headline results, with the analytical reference beside each one."""
    summary = outcome.summary
    reference = outcome.reference
    if summary is None or reference is None:
        return

    target = outcome.inputs.target_factor_of_safety if outcome.inputs else None

    # These have to be the numbers the verdict used. Showing the raw peak here
    # would put "FoS 1.14" beside a green Pass, which reads as a broken tool
    # even though both numbers are correct.
    left, middle, right = st.columns(3)
    left.metric(
        "Max von Mises (structural)",
        f"{summary.max_von_mises_structural:.1f} MPa",
        help="Highest stress outside the singular zone around the clamped "
        "washer rings. The raw peak is reported below.",
    )
    middle.metric("Tip deflection", f"{summary.tip_deflection:.4f} mm")
    right.metric(
        "Factor of safety",
        f"{summary.factor_of_safety_structural:.2f}",
        delta=(
            f"{summary.factor_of_safety_structural - target:+.2f} vs target"
            if target is not None
            else None
        ),
    )

    st.subheader("FE against hand calculation")
    st.table(
        {
            "Quantity": [
                "Tip deflection (mm)",
                f"Bending stress at s = {reference.section_position:.0f} mm (MPa)",
                "Reaction force (N)",
            ],
            "FE": [
                f"{summary.tip_deflection:.5f}",
                f"{summary.section.magnitude:.3f}",
                f"{summary.reactions[2]:.4f}",
            ],
            "Hand calculation": [
                f"{reference.tip_deflection_beam:.5f} (beam), "
                f"{reference.tip_deflection_plate:.5f} (plate)",
                f"{reference.section_stress:.3f}",
                f"{reference.total_load:.4f}",
            ],
        }
    )

    st.caption(
        f"Structural peak {summary.max_von_mises_structural:.3f} MPa at "
        f"x={summary.structural_location[0]:.2f}, "
        f"y={summary.structural_location[1]:.2f}, "
        f"z={summary.structural_location[2]:.2f} mm, giving "
        f"K_t = {summary.stress_concentration:.3f} against the beam root "
        f"stress of {reference.root_stress:.3f} MPa. It sits in the fillet and "
        "is reported, not validated against: it is mesh dependent. "
        f"The raw peak is {summary.max_von_mises:.3f} MPa at "
        f"x={summary.peak_location[0]:.2f}, y={summary.peak_location[1]:.2f}, "
        f"z={summary.peak_location[2]:.2f} mm, within "
        f"{summary.restraint_zone:.2f} mm of a clamped washer edge — a "
        "restraint singularity that rises without limit as the mesh is "
        "refined, so the verdict does not use it."
    )


def show_images(outcome) -> None:
    artifacts = outcome.artifacts
    if artifacts is None:
        return

    pictures = [
        ("Von Mises stress", artifacts.stress_image),
        ("Displacement", artifacts.displacement_image),
        ("Mesh", artifacts.mesh_image),
    ]
    for caption, path in pictures:
        if path is not None and path.is_file():
            st.image(str(path), caption=caption, width="stretch")


def show_report_download(outcome) -> None:
    """Offer the HTML report, which is self-contained and can be shared."""
    artifacts = outcome.artifacts
    if artifacts is None or artifacts.report is None or not artifacts.report.is_file():
        return

    st.download_button(
        "Download engineering report (HTML)",
        data=artifacts.report.read_bytes(),
        file_name="engineering_report.html",
        mime="text/html",
        help="Self-contained: images are embedded, so it can be shared on its "
        "own without the run folder.",
    )


def show_checks(outcome) -> None:
    st.subheader(f"Checks ({sum(c.passed for c in outcome.checks)}/{len(outcome.checks)} passed)")
    st.table(
        {
            "Check": [check.name for check in outcome.checks],
            "Result": [
                "Pass" if check.passed else ("Warn" if check.is_advisory else "Fail")
                for check in outcome.checks
            ],
            "Detail": [check.message for check in outcome.checks],
        }
    )


def main() -> None:
    st.title("Parametric bracket: CAD to CAE")
    st.caption(
        "Educational proof of concept. Linear elasticity; loads and restraints "
        "simplified; the plate is held only under its washers, with a rigid "
        "clamp and no bolt preload, friction or contact. "
        "Contact, bolt preload, fatigue, fracture, thermal loads, manufacturing "
        "tolerances and certification are outside this version. Results require "
        "independent engineering verification and are not suitable for product "
        "release or safety certification. Units: mm, N, MPa."
    )

    raw = sidebar_inputs(defaults())

    try:
        inputs = BracketInputs(**raw)
    except ValidationError as exc:
        st.error("The design cannot be built as specified:")
        for error in exc.errors():
            st.write(f"- {error['msg']}")
        st.info(
            "Nothing was built. Inputs are checked before any CAD is attempted, "
            "so an impossible design fails here with a reason rather than deep "
            "inside the CAD kernel."
        )
        # The sketch cannot be drawn from inputs that were rejected, so the
        # shipped example stands in: the point here is to show what each
        # dimension means, which is usually what the reader needs after a
        # rejection.
        try:
            st.caption("The example design, for reference — not your inputs:")
            show_sketch(BracketInputs(**defaults()), expanded=True)
        except ValidationError:
            pass
        return

    for note in inputs.warnings():
        st.warning(note)

    run = st.button("Generate and Analyse", type="primary")

    if not run:
        st.info(
            "Set the design in the sidebar, then press Generate and Analyse. "
            "A run takes roughly a minute at the default mesh size."
        )

    show_sketch(inputs, expanded=not run)

    if not run:
        return

    status = st.empty()
    bar = st.progress(0.0)

    def progress(stage: str, fraction: float) -> None:
        status.text(stage)
        bar.progress(min(fraction, 1.0))

    with st.spinner("Running..."):
        # No directory is passed, so the pipeline creates a fresh run folder
        # under the configured output root. Runs are kept side by side rather
        # than overwriting, so a result can always be traced to its inputs.
        outcome = run_pipeline(inputs, progress=progress, solver_threads=8)

    bar.progress(1.0)
    status.text(f"Finished in {outcome.elapsed_seconds:.1f} s")

    show_verdict(outcome)
    show_numbers(outcome)

    show_report_download(outcome)

    tabs = st.tabs(["Results", "Checks", "Log", "Files"])
    with tabs[0]:
        show_images(outcome)
    with tabs[1]:
        show_checks(outcome)
    with tabs[2]:
        st.code("\n".join(outcome.log_lines), language="text")
    with tabs[3]:
        if outcome.artifacts is not None:
            st.write(f"Run folder: `{outcome.artifacts.directory}`")
            for name, path in outcome.artifacts.existing().items():
                st.write(f"**{name}** — `{path.name}` ({path.stat().st_size / 1e6:.2f} MB)")


if __name__ == "__main__":
    main()
