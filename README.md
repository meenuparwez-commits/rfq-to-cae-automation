# Parametric CAD-to-CAE automation: mounting bracket

One button turns validated design inputs into geometry, a mesh, a finite
element solve, engineering checks against hand calculations, a manufacturing
drawing and an HTML report with a **Pass / Review / Fail** verdict.

This project demonstrates a general engineering automation architecture
applicable to configurable brackets, supports, enclosures and similar
manufactured components.

> **Educational proof of concept.** Not a certified or release-ready
> engineering tool. Every result requires independent engineering verification
> and is not suitable for product release or safety certification. See
> [docs/limitations.md](docs/limitations.md).

Units throughout: mm, N, MPa (N/mm²), tonne/mm³ for density, mass in kg.

---

## What it does

```
inputs → validate → CAD → drawing → mesh → boundary conditions
       → solve → read results → compare with theory → verdict → report
```

Every stage checks its own output against something computed **independently**:
the CAD volume against a closed-form hand calculation, the detected face areas
against `b·H − holes`, the deflection against beam theory, the drawing's
dimensions against the projected geometry.

| | |
|---|---|
| **Geometry** | CadQuery → STEP |
| **Drawing** | First-angle DXF + PDF with hidden-line removal |
| **Mesh** | Gmsh, second-order tetrahedra (C3D10) |
| **Solver** | CalculiX, linear static |
| **Interface** | Streamlit |
| **Report** | Self-contained HTML, images embedded |
| **Tests** | 379, one file per module |

## Results on the shipped baseline

H 100 × L 80 × b 60 × t 4 mm, r 5 fillet, 4 × ⌀9 holes, S275 steel, 250 N tip
load, 1.5 mm mesh. 108,160 nodes, 62,886 elements, 286,695 equations, about a
minute end to end.

| Check | Result |
|---|---|
| CAD volume vs hand calculation | 42,504.0267 mm³, **zero** relative error |
| Equilibrium | reactions return 250.000000 N |
| Tip deflection | 0.58754 mm, **between** the plate (0.57778) and beam (0.63492) bounds |
| Bending stress at mid-span | 62.413 vs 62.500 MPa — **0.14%** |
| Mesh convergence | settled to **0.04%** between the two finest meshes |
| Stress concentration | K_t = 1.042, peak in the fillet |
| Factor of safety | **2.112** against a target of 2.0 |
| **Verdict** | **Pass** — 21 checks |

![Von Mises stress](docs/images/stress.png)

*Von Mises stress on the deflected shape. The exaggeration factor is written
into the caption of every fringe plot, so an exaggerated picture is never
mistaken for the real deflected shape — the true tip deflection is 0.59 mm on
an 84 mm part.*

![Displacement](docs/images/displacement.png)

![Manufacturing drawing](docs/images/drawing.png)

*First-angle projection with hidden-line removal. All eight dimensions are
measured back off the projected geometry and compared with the validated
inputs, so the check is not circular.*

![Mesh](docs/images/mesh.png)

## Running it

Requires [Miniconda](https://docs.conda.io/en/latest/miniconda.html) and
CalculiX (`ccx.exe`, bundled with [FreeCAD](https://www.freecad.org/)).

```bash
conda env create -f environment.yml
```

Point `CCX_PATH` at the solver — the code reads this variable and never
hard-codes a path:

```bash
setx CCX_PATH "C:\path\to\FreeCAD\bin\ccx.exe"
```

Then start the app. On Windows, **double-click `run_app.bat`** — it finds
conda, checks the environment and opens your browser at
`http://localhost:8501`. Leave the console window open while you use the app;
closing it stops the server.

Or from a **new** terminal (a process keeps the environment it was given at
launch, so an already-open shell will not see `CCX_PATH`):

```bash
conda run -n bracket-cae streamlit run app.py
```

Run the tests:

```bash
conda run -n bracket-cae python -m pytest -q
```

Or drive the pipeline directly:

```python
from src.pipeline import run_pipeline
from src.schemas import BracketInputs

inputs = BracketInputs.from_json_file("examples/baseline_bracket.json")
outcome = run_pipeline(inputs)

print(outcome.verdict.headline)
```

Each run writes a timestamped folder under `outputs/` containing the inputs,
STEP, drawing, mesh, solver deck, CalculiX output, result mesh, images, log,
summary and the HTML report.

## Three ideas the project is built on

### A solver finishing is not validation

The pipeline could produce confident-looking numbers for a model that is not in
equilibrium, meshed too coarsely to bend, or restrained on the wrong face. So
every result is compared with an independent reference, and the comparisons are
reported whether or not they agree.

Before any project code depended on CalculiX, a single element was solved and
checked against a hand calculation — including a Poisson-contraction check
specifically chosen because it is the one that catches an over-constrained
model, where the stress still looks right.

### Do not validate against the peak stress

The peak sits in the fillet stress concentration, where the stress rises
without limit as the mesh is refined. The convergence study shows exactly that:
between 1.5 mm and 1.25 mm elements the tip deflection settles to 0.04% while
the peak is still climbing 1% per refinement.

Validation therefore uses the tip deflection and a bending stress away from the
root — both of which converge. The peak is reported separately as K_t, with its
location, so a peak on the edge of the fixed face can be recognised as a
restraint singularity rather than a real feature.

### Tolerances come from what must be detected

Not from what the code currently achieves. The volume tolerance is 1e-4 because
the smallest defect it must catch is a missing fillet at 0.74% of the section —
74× the threshold. A tighter 1e-6 would pass today at exactly zero error and
risk a false Fail after a library upgrade.

This cuts both ways. A measured mesh sweep showed the textbook "element size ≤
t/2" rule lands at 1.98 elements through the thickness — just under the
requirement. The **baseline mesh was refined**; the threshold was not loosened
to make the default pass.

## Documentation

- [docs/architecture.md](docs/architecture.md) — module responsibilities, data
  flow, the conventions every stage relies on
- [docs/validation.md](docs/validation.md) — what was checked against what, and
  the numbers
- [docs/limitations.md](docs/limitations.md) — assumptions, what is out of
  scope, what the numbers do not mean
- [docs/engineering-notes.md](docs/engineering-notes.md) — design decisions,
  measured results, the bugs found and why each tolerance is what it is

## Limitations in brief

Linear elasticity. Small displacements. Static loading. The entire rear face is
fixed, so **the mounting holes carry no load in this model**. No contact, bolt
preload, fatigue, fracture, thermal loads, manufacturing tolerances or
certification. The drawing carries no tolerances or GD&T and is stamped
`EDUCATIONAL DEMONSTRATOR - NOT FOR MANUFACTURE`.

Full detail in [docs/limitations.md](docs/limitations.md).

## Licence

MIT — see [LICENSE](LICENSE). Synthetic geometry and original code throughout.
