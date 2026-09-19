"""Write a self-contained HTML engineering report for one run.

Units: mm, N, MPa (N/mm^2), tonne/mm^3, mass in kg. Stated in the report
itself, because a number without its units is not a result.

Images are embedded as base64 rather than linked. The report is then a single
file that can be emailed, archived or opened years later without the run folder
beside it; a report whose pictures have gone missing is worse than one with no
pictures, because the gaps are silent.

The report states plainly what was checked, what was assumed and what is out of
scope. This is never presented as a certified tool, and a report that
looks authoritative while hiding its assumptions is exactly the failure mode
worth designing against.
"""

from __future__ import annotations

import base64
import html
from datetime import datetime
from pathlib import Path

from jinja2 import Template

from src.engineering_checks import Verdict

# Kept modest: base64 inflates by about a third, and a report nobody can open
# in a browser is no use. The mesh image is the large one.
MAX_EMBEDDED_IMAGE_BYTES = 8_000_000

VERDICT_CLASS = {
    Verdict.PASS: "pass",
    Verdict.REVIEW: "review",
    Verdict.FAIL: "fail",
}

LIMITATIONS = [
    "Results require independent engineering verification.",
    "Linear elastic material behaviour is assumed; no plasticity.",
    "Loads and restraints are simplified. The plate is held only under its "
    "washers, which is closer to a bolted joint than a fully fixed rear face, "
    "but it is still an idealisation: the clamp is rigid, with no bolt "
    "preload, no friction and no contact.",
    "Restraining a sharp-edged ring produces a stress singularity at the edge "
    "of each washer. That peak rises without limit as the mesh is refined, so "
    "it is reported but never used for the verdict; the factor of safety uses "
    "the highest stress outside that zone.",
    "Contact, bolt preload, fatigue, fracture, thermal loads, manufacturing "
    "tolerances and certification are outside the scope of this version.",
    "The peak stress sits in the fillet stress concentration and is mesh "
    "dependent. It is reported, not used for validation.",
    "Not suitable for product release or safety certification.",
]

TEMPLATE = Template(
    """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{{ title }}</title>
<style>
  :root { color-scheme: light; }
  body { font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
         margin: 0 auto; max-width: 1000px; padding: 24px 16px 64px;
         color: #1a1a1a; background: #fff; line-height: 1.5; }
  h1 { margin-bottom: 4px; font-size: 1.7rem; }
  h2 { margin-top: 36px; border-bottom: 1px solid #ddd; padding-bottom: 6px;
       font-size: 1.2rem; }
  .sub { color: #555; margin-top: 0; }
  .verdict { padding: 14px 18px; border-radius: 8px; font-size: 1.15rem;
             font-weight: 600; margin: 20px 0; border: 1px solid; }
  .verdict.pass   { background: #e8f5e9; border-color: #2e7d32; color: #1b5e20; }
  .verdict.review { background: #fff8e1; border-color: #f9a825; color: #7a5200; }
  .verdict.fail   { background: #ffebee; border-color: #c62828; color: #b71c1c; }
  table { border-collapse: collapse; width: 100%; margin: 12px 0; font-size: 0.93rem; }
  th, td { border: 1px solid #ddd; padding: 7px 9px; text-align: left;
           vertical-align: top; }
  th { background: #f5f5f5; font-weight: 600; }
  td.num { text-align: right; font-variant-numeric: tabular-nums; }
  .tag { display: inline-block; padding: 1px 8px; border-radius: 10px;
         font-size: 0.8rem; font-weight: 600; }
  .tag.pass { background: #e8f5e9; color: #1b5e20; }
  .tag.warn { background: #fff8e1; color: #7a5200; }
  .tag.fail { background: #ffebee; color: #b71c1c; }
  figure { margin: 20px 0; }
  figure img { width: 100%; border: 1px solid #ddd; border-radius: 6px; }
  figcaption { color: #555; font-size: 0.9rem; margin-top: 6px; }
  .note { background: #f5f7fa; border-left: 4px solid #90a4ae; padding: 10px 14px;
          margin: 16px 0; font-size: 0.93rem; }
  ul.limits li { margin-bottom: 6px; }
  pre { background: #fafafa; border: 1px solid #eee; padding: 12px;
        overflow-x: auto; font-size: 0.78rem; line-height: 1.35; }
  details { margin-top: 12px; }
  summary { cursor: pointer; font-weight: 600; }
  footer { margin-top: 48px; color: #777; font-size: 0.85rem;
           border-top: 1px solid #eee; padding-top: 12px; }
</style>
</head>
<body>

<h1>{{ title }}</h1>
<p class="sub">Generated {{ generated }} &middot; Units: mm, N, MPa (N/mm&sup2;),
tonne/mm&sup3;, mass in kg</p>

<div class="note">
  <strong>Educational proof of concept.</strong> This report is produced by an
  automated demonstration pipeline. Results require independent engineering
  verification and are not suitable for product release or safety certification.
</div>

<div class="verdict {{ verdict_class }}">{{ verdict_headline }}</div>

{% if failure_reason %}
<p><strong>Reason:</strong> {{ failure_reason }}</p>
{% endif %}

{% if verdict_items %}
<ul>
  {% for item in verdict_items %}<li>{{ item }}</li>{% endfor %}
</ul>
{% endif %}

<h2>Design inputs</h2>
<table>
  <tr><th>Parameter</th><th>Value</th></tr>
  {% for label, value in inputs_rows %}
  <tr><td>{{ label }}</td><td class="num">{{ value }}</td></tr>
  {% endfor %}
</table>

{% if results_rows %}
<h2>Results against hand calculation</h2>
<table>
  <tr><th>Quantity</th><th>Finite element</th><th>Hand calculation</th><th>Comment</th></tr>
  {% for row in results_rows %}
  <tr>
    <td>{{ row.quantity }}</td>
    <td class="num">{{ row.fe }}</td>
    <td class="num">{{ row.hand }}</td>
    <td>{{ row.comment }}</td>
  </tr>
  {% endfor %}
</table>

<div class="note">
  Validation follows the tip deflection and the bending stress at a section away
  from the root. The peak stress sits in the fillet stress concentration, where
  the value depends on mesh refinement and never fully converges, so it is
  reported separately as K<sub>t</sub> rather than used to validate.
</div>
{% endif %}

{% if mesh_rows %}
<h2>Mesh</h2>
<table>
  <tr><th>Property</th><th>Value</th></tr>
  {% for label, value in mesh_rows %}
  <tr><td>{{ label }}</td><td class="num">{{ value }}</td></tr>
  {% endfor %}
</table>
{% endif %}

<h2>Checks ({{ passed_count }} of {{ checks|length }} passed)</h2>
<table>
  <tr><th>Check</th><th>Result</th><th>Detail</th></tr>
  {% for check in checks %}
  <tr>
    <td>{{ check.name }}</td>
    <td><span class="tag {{ check.tag }}">{{ check.label }}</span></td>
    <td>{{ check.message }}</td>
  </tr>
  {% endfor %}
</table>

{% if images %}
<h2>Results</h2>
{% for image in images %}
<figure>
  <img src="data:image/png;base64,{{ image.data }}" alt="{{ image.caption }}">
  <figcaption>{{ image.caption }}</figcaption>
</figure>
{% endfor %}
{% endif %}

{% if drawing_files %}
<h2>Manufacturing drawing</h2>
<p>A first-angle projection is produced alongside the analysis and its
dimensions are measured back off the projected geometry and compared with the
validated inputs. The files sit in the run folder:</p>
<ul>
  {% for name in drawing_files %}<li><code>{{ name }}</code></li>{% endfor %}
</ul>
{% endif %}

{% if convergence_rows %}
<h2>Mesh convergence</h2>
<table>
  <tr>
    <th>Element size (mm)</th><th>Elements</th><th>Tip deflection (mm)</th>
    <th>Change</th><th>Peak von Mises (MPa)</th><th>Change</th>
  </tr>
  {% for row in convergence_rows %}
  <tr>
    <td class="num">{{ row.size }}</td>
    <td class="num">{{ row.elements }}</td>
    <td class="num">{{ row.deflection }}</td>
    <td class="num">{{ row.deflection_change }}</td>
    <td class="num">{{ row.stress }}</td>
    <td class="num">{{ row.stress_change }}</td>
  </tr>
  {% endfor %}
</table>
<p>{{ convergence_comment }}</p>
{% endif %}

<h2>Assumptions and limitations</h2>
<ul class="limits">
  {% for item in limitations %}<li>{{ item }}</li>{% endfor %}
</ul>

<details>
  <summary>Run log</summary>
  <pre>{{ log }}</pre>
</details>

<footer>
  Educational proof of concept. Synthetic geometry and original code.
  Results require independent engineering verification.
</footer>

</body>
</html>
"""
)


def _embed(path: Path | None) -> str | None:
    """Base64 of an image, or None if it is missing or too large to embed."""
    if path is None or not path.is_file():
        return None
    if path.stat().st_size > MAX_EMBEDDED_IMAGE_BYTES:
        return None

    return base64.b64encode(path.read_bytes()).decode("ascii")


def _number(value: float | None, digits: int = 3) -> str:
    if value is None:
        return "-"
    return f"{value:,.{digits}f}"


def _input_rows(inputs, material) -> list[tuple[str, str]]:
    return [
        ("Plate height H (mm)", _number(inputs.plate_height, 2)),
        ("Arm length L, free (mm)", _number(inputs.arm_length, 2)),
        ("Width b (mm)", _number(inputs.width, 2)),
        ("Thickness t (mm)", _number(inputs.thickness, 2)),
        ("Inside fillet radius r (mm)", _number(inputs.fillet_radius, 2)),
        ("Hole diameter (mm)", _number(inputs.hole_diameter, 2)),
        ("Hole spacing (mm)", _number(inputs.hole_spacing, 2)),
        ("Number of holes", str(inputs.num_holes)),
        ("Material", material.name if material else inputs.material),
        (
            "Young's modulus E (MPa)",
            _number(material.youngs_modulus, 0) if material else "-",
        ),
        ("Poisson's ratio", _number(material.poissons_ratio, 2) if material else "-"),
        ("Yield strength (MPa)", _number(material.yield_strength, 1) if material else "-"),
        ("Applied load F (N)", _number(inputs.applied_load, 2)),
        ("Load case", inputs.load_case.value),
        ("Element size (mm)", _number(inputs.mesh_size, 2)),
        ("Target factor of safety", _number(inputs.target_factor_of_safety, 2)),
    ]


def _result_rows(summary, reference) -> list[dict]:
    if summary is None or reference is None:
        return []

    deflection_ratio = (
        summary.tip_deflection / reference.tip_deflection_beam
        if reference.tip_deflection_beam
        else float("nan")
    )
    stress_error = (
        abs(summary.section.magnitude - reference.section_stress)
        / reference.section_stress
        if reference.section_stress
        else float("nan")
    )

    return [
        {
            "quantity": "Tip deflection (mm)",
            "fe": _number(summary.tip_deflection, 5),
            "hand": f"{_number(reference.tip_deflection_beam, 5)} beam / "
            f"{_number(reference.tip_deflection_plate, 5)} plate",
            "comment": f"FE/beam {deflection_ratio:.4f}. The plate bound uses "
            "E/(1-&nu;&sup2;) and is the stiffer of the two; a wide section is "
            "expected between them.",
        },
        {
            "quantity": f"Bending stress at s = {reference.section_position:.0f} mm (MPa)",
            "fe": _number(summary.section.magnitude, 3),
            "hand": _number(reference.section_stress, 3),
            "comment": f"Relative difference {stress_error:.2%}. Away from both "
            "the load and the restraint, so beam theory should be accurate.",
        },
        {
            "quantity": "Reaction force (N)",
            "fe": _number(float(summary.reactions[2]), 4),
            "hand": _number(reference.total_load, 4),
            "comment": "Equilibrium: reactions must balance the applied load.",
        },
        {
            "quantity": "Peak von Mises (MPa)",
            "fe": _number(summary.max_von_mises, 3),
            "hand": f"{_number(reference.root_stress, 3)} nominal at root",
            "comment": f"K<sub>t</sub> = {summary.stress_concentration:.3f} at "
            f"x={summary.peak_location[0]:.2f}, y={summary.peak_location[1]:.2f}, "
            f"z={summary.peak_location[2]:.2f} mm. Reported, not validated against.",
        },
        {
            "quantity": "Factor of safety",
            "fe": _number(summary.factor_of_safety_peak, 3),
            "hand": "-",
            "comment": f"On the peak stress. Away from the root it is "
            f"{summary.factor_of_safety_section:.3f}.",
        },
    ]


def _mesh_rows(stats) -> list[tuple[str, str]]:
    if stats is None:
        return []

    return [
        ("Element type", stats.element_type),
        ("Nodes", f"{stats.num_nodes:,}"),
        ("Elements", f"{stats.num_elements:,}"),
        ("Requested element size (mm)", _number(stats.requested_size, 2)),
        ("Mean edge length (mm)", _number(stats.mean_edge_length, 3)),
        ("Minimum gamma quality", _number(stats.min_quality, 3)),
    ]


def _check_rows(checks) -> list[dict]:
    rows = []
    for check in checks:
        if check.passed:
            tag, label = "pass", "Pass"
        elif check.is_advisory:
            tag, label = "warn", "Review"
        else:
            tag, label = "fail", "Fail"

        rows.append(
            {
                "name": check.name,
                "tag": tag,
                "label": label,
                "message": check.message,
            }
        )

    return rows


def _convergence_rows(study) -> tuple[list[dict], str]:
    if study is None or not study.levels:
        return [], ""

    rows = []
    previous = None
    for level in study.levels:
        rows.append(
            {
                "size": _number(level.mesh_size, 2),
                "elements": f"{level.num_elements:,}",
                "deflection": _number(level.tip_deflection, 5),
                "deflection_change": (
                    "-"
                    if previous is None
                    else f"{abs(level.tip_deflection - previous.tip_deflection) / previous.tip_deflection:.2%}"
                ),
                "stress": _number(level.max_von_mises, 2),
                "stress_change": (
                    "-"
                    if previous is None
                    else f"{abs(level.max_von_mises - previous.max_von_mises) / previous.max_von_mises:.2%}"
                ),
            }
        )
        previous = level

    return rows, study.comment


def build_report(outcome, convergence=None, title: str = "Bracket analysis report") -> str:
    """Render the HTML report for a run as a string."""
    material = None
    if outcome.inputs is not None:
        try:
            material = outcome.inputs.resolved_material()
        except Exception:  # noqa: BLE001 - the report must render regardless
            material = None

    images = []
    if outcome.artifacts is not None:
        for caption, path in (
            ("Von Mises stress on the deflected shape", outcome.artifacts.stress_image),
            ("Displacement magnitude", outcome.artifacts.displacement_image),
            ("Mesh", outcome.artifacts.mesh_image),
        ):
            data = _embed(path)
            if data is not None:
                images.append({"caption": caption, "data": data})

    verdict_items = [
        *outcome.verdict.failures,
        *outcome.verdict.advisories,
        *(f"Input warning: {item}" for item in outcome.verdict.warnings),
    ]

    drawing_files = []
    if outcome.artifacts is not None:
        for path in (outcome.artifacts.drawing_dxf, outcome.artifacts.drawing_pdf):
            if path is not None and path.is_file():
                drawing_files.append(path.name)

    convergence_rows, convergence_comment = _convergence_rows(convergence)

    return TEMPLATE.render(
        title=title,
        generated=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        verdict_class=VERDICT_CLASS[outcome.verdict.verdict],
        verdict_headline=outcome.verdict.headline,
        failure_reason=outcome.failure_reason,
        verdict_items=verdict_items,
        inputs_rows=_input_rows(outcome.inputs, material) if outcome.inputs else [],
        results_rows=_result_rows(outcome.summary, outcome.reference),
        mesh_rows=_mesh_rows(outcome.mesh_stats),
        checks=_check_rows(outcome.checks),
        passed_count=sum(1 for check in outcome.checks if check.passed),
        images=images,
        drawing_files=drawing_files,
        convergence_rows=convergence_rows,
        convergence_comment=convergence_comment,
        limitations=LIMITATIONS,
        log=html.escape("\n".join(outcome.log_lines)),
    )


def write_report(
    outcome, output_path: str | Path, convergence=None, title: str = "Bracket analysis report"
) -> Path:
    """Render and save the report, confirming it was actually written."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    output_path.write_text(
        build_report(outcome, convergence=convergence, title=title), encoding="utf-8"
    )

    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise RuntimeError(f"Report was not written to {output_path}.")

    return output_path
