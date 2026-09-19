# End-to-end audit report

Audit date: 2026-09-20  
Audited working tree: `C:\dev\publish\bracket-cad-cae-automation`  
Branch: `main`  
Specification used: `C:\dev\bracket-cad-cae-automation\CLAUDE.md` (761 lines). The audited repository does not contain or track its own `CLAUDE.md`; this is itself finding A-06.

## 1. Working-tree state

The mandatory preflight returned:

```text
> git branch --show-current
main

> git status --porcelain
 M LICENSE
 M README.md
 M app.py
 M docs/limitations.md
 M src/calculix_writer.py
 M src/drawing.py
 M src/logging_utils.py
 M src/pipeline.py
 M src/report_generator.py
```

The user confirmed that these uncommitted files are authoritative and are to be audited as-is. The changes appear to be a terminology and documentation pass: “educational proof of concept” becomes “demonstration project”; the drawing stamp becomes `DEMONSTRATION MODEL - NOT FOR MANUFACTURE`; and `docs/limitations.md` is updated for the washer-annulus restraint and new 220 N baseline. No unrelated implementation feature is visible in the diff. No tracked file was edited during this audit except this report.

Recent history at preflight:

```text
9125992 Re-render the demo from the bolted model
ec42e2c Hold the plate under its washers, not across the whole rear face
9702d86 Use the current st.image width argument
c80ac1a Show a dimensioned sketch of the inputs in the app
ec6cade Add a rendered demo video and a README loop
80ac382 Reuse a running app instead of failing on a port clash
64d895b Parametric CAD-to-CAE automation for a mounting bracket
```

## 2. Findings

| ID | Severity | Area | Finding | Evidence | Suggested fix | Risk of fixing |
|---|---|---|---|---|---|---|
| A-01 | BLOCKER | Hard rule: input safety | Safety-critical inputs do have defaults. `config/default_inputs.json` supplies dimensions, material, load, `washer_diameter`, mesh and target FoS; the app reads it as `defaults()` and supplies each value to a widget. This directly violates the explicit rule that only `output_dir` may default. The Pydantic schema itself is correct and defaults only `output_dir`. | `config/default_inputs.json:2-15`; `app.py:47-51`; widget `value=`/selected indexes at `app.py:63-139`; schema fields have no defaults at `src/schemas.py:143-170`, with only `output_dir` defaulted at line 172. Browser startup also visibly opened with the complete 220 N baseline already populated. | Separate a clearly labelled, explicit “Load example” action from initial empty/unset inputs. Do not pass safety-critical values as widget defaults. Keep `examples/baseline_bracket.json` as an opt-in example, not an implicit input source. | High UX and test churn: Streamlit state, invalid/unset-state handling, sketch behavior and UI tests will change. Engineering behavior after valid entry should not change. |
| A-02 | BLOCKER | Engineering decision 4 | The new one-sided deflection check still moves the physical lower bound down by 15% using the obsolete `DEFLECTION_BAND_TOL`. A result 10% stiffer than a rigid wall therefore passes even though the decision log says any result below the rigid-root value indicates over-restraint. The constant’s comment still describes an “allowance either side” of a band that no longer exists. | `src/engineering_checks.py:45-50` retains `DEFLECTION_BAND_TOL = 0.15`; `src/engineering_checks.py:245-269` says the rigid-root solution is a lower bound, then calculates `floor = min(...) * (1.0 - tol)`; pass is `fe_deflection >= floor` at line 270. Tests only use 50% below and 2×/5× above (`tests/test_engineering_checks.py:133-187`), so the 1–15% forbidden region is untested. External decision log lines 666-671 says FE must be at least the rigid-root value and calls the new check sharper than the old band. | Remove the inherited band allowance or replace it with a separately justified numerical-discretization tolerance small enough to detect over-restraint. Add boundary tests immediately below and above the true plate lower bound. | Medium: legitimate FE discretization scatter might produce Review until a physically justified small tolerance is chosen. That is safer than silently passing an over-restrained model. |
| A-03 | MAJOR | HTML report / traceability | The standalone report displays numbers different from those that drove the Pass. It reports raw peak 240.762 MPa and raw FoS 1.142 beside a Pass against target 2.0, while the actual verdict used structural peak 122.113 MPa and structural FoS 2.252. It also prints structural `K_t = 1.110` beside the raw peak location, an internally impossible pairing. | Rendered fresh report `outputs/audit_baseline_20260920/engineering_report.html`; source uses `summary.max_von_mises` and `summary.peak_location` at `src/report_generator.py:317-323`, then `summary.factor_of_safety_peak` at lines 325-330. Actual verdict uses `summary.factor_of_safety_structural` at `src/engineering_checks.py:404-430`. The fresh Streamlit result correctly showed 122.1 MPa / FoS 2.25, proving the drift is report-only. | Show structural peak, structural location and structural FoS as the primary verdict values; show raw clamp peak/FoS in a separate explicitly set-aside row. Add a report regression test that asserts all four numbers and the correct locations. | Low-to-medium presentation change; no solver or verdict logic changes. Existing snapshots/docs may need regeneration. |
| A-04 | MAJOR | Test quality: element mapping | No test verifies C3D10 mid-side node ordering, despite code comments and the decision log claiming it is verified. Existing tests prove only `tetra10`/C3D10 type and ten connectivity entries. A wrong permutation can solve and silently return wrong results. | Claim at `src/mesh_generator.py:365-370`; tests at `tests/test_mesh_generator.py:52-85` check element type only; `tests/test_calculix_writer.py:124-134` checks ten nodes only. Repository-wide search found no midpoint/designated-edge/permutation assertion. | Add the decision-log test: for each C3D10, verify nodes 5–10 are closest to the midpoints of edges (0,1), (1,2), (0,2), (0,3), (1,3), (2,3), including curved elements. | Low. A failure may expose a real solver-deck mapping defect and then require a carefully verified permutation. |
| A-05 | MAJOR | Test quality: FRD identity | Code enforces contiguous result node IDs 1..N, but no test creates a gap or duplicate to prove that guard fails. The existing “node-count mismatch” test only supplies one valid node and an expected count of 99. | Guard at `src/result_reader.py:167-189`; tests at `tests/test_result_reader.py:109-130`. No test writes IDs such as `{1,3}` or a duplicate. | Add explicit gap, duplicate and zero/out-of-range ID cases and assert the parser refuses them before mapping results to mesh nodes. | Low; test-only unless it reveals an edge-case parser issue. |
| A-06 | MAJOR | Specification / repository integrity | The authoritative publish repository has no `CLAUDE.md`, even though the audit brief says it is the specification and the architecture lists it at repo root. This audit had to use an adjacent development copy, whose identity with the intended publish decision log cannot be proven from Git. | `rg --files -g 'CLAUDE.md'` and `git ls-files` returned no match in the audited repo; top-level listing has no file. External spec architecture lists `CLAUDE.md` at line 105. | Add the approved decision log to the authoritative repository, or document a single immutable external source and revision/hash. | Low code risk; moderate publication/process risk if the adjacent file contains information intentionally excluded from the published repository. Review its contents before adding. |
| A-07 | MINOR | Decision-log drift | `CheckResult` still lives in `geometry_checks`, but it is imported directly by six modules, not the recorded five. | Definition `src/geometry_checks.py:53`; imports in `boundary_detection.py:34`, `drawing.py:47`, `engineering_checks.py:31`, `mesh_generator.py:29`, `pipeline.py:37`, `solver_runner.py:29`. | Update the decision log count now; if development continues, move the shared type once to a neutral module in a separately approved change. | Moving it now creates broad import churn; documentation-only correction is low risk. |
| A-08 | MINOR | Environment reproducibility | The portable `environment.yml` omits direct runtime imports `ezdxf` and `matplotlib`/`matplotlib-base` (and relies on CadQuery for OCP). The exact Windows lock contains them and the current environment runs, but a future dependency graph need not keep accidental transitive packages. No packages are pip-installed. | Runtime imports at `src/drawing.py:35-45` and `src/drawing.py:769-774`; `environment.windows.lock.yml:69,208-209`; absent from `environment.yml`. `conda list -n bracket-cae --json`: 350 packages, `PIP_PACKAGE_COUNT=0`; all audited key packages came from conda-forge. | Declare direct runtime dependencies in `environment.yml` while leaving transitive versions to the lock file. | Medium: changing the portable solve can move dependency versions; regenerate and test the lock in the approved environment rather than installing ad hoc. |
| A-09 | MINOR | Documentation artifact drift | The committed `docs/images/drawing.png` still visibly carries `EDUCATIONAL DEMONSTRATOR - NOT FOR MANUFACTURE`, while authoritative code and README now state `DEMONSTRATION MODEL - NOT FOR MANUFACTURE`. | Visual inspection of `docs/images/drawing.png`; new stamp at `src/drawing.py:73`; README claim at `README.md:208-209`. | Regenerate/copy the drawing image from the final approved baseline after fixes. | Low; binary diff and documentation-only change. |
| A-10 | MINOR | Report wording and missing regression | Report boilerplate still says singular “the peak stress sits in the fillet” although the bolted model has a separate raw clamp singularity. It also says the wide section is “expected between” the beam/plate bounds despite the accepted flexible-base result being about 2× the beam value. Existing report tests look only for broad phrases and therefore missed A-03. | `src/report_generator.py:51-52`, `146-150`, `300-302`; generated report; broad assertions at `tests/test_report_generator.py:152-168`. | Rewrite report language for two peaks and a lower bound; assert the structural/raw values and roles explicitly. | Low. |
| A-11 | MINOR | Recorded test count | The full suite now collects and passes 420 tests, not the 415 recorded in the external decision log. This is an increase, not a shortfall, but the recorded count is stale. | Valid unrestricted run: `420 passed in 49.50s`; external decision log line 177 and 755 records 415. | Update the decision log after its authoritative location is resolved. | None. |
| A-12 | NOTE | Outputs / disk | `outputs/` is ignored and no raw `.frd/.dat/.inp/.msh/.step/.vtu`, summary or report is tracked. At audit end it held 0.465 GiB in three audit-created directories (`audit_pytest_20260920`, `audit_baseline_20260920`, `app_run`). No older stale run was observed. C: is 85.8% used with 135.03 GiB free, not the 94% recorded in the old decision log. | `.gitignore:3`; `git check-ignore -v`; `git ls-files` artifact search returned no match; PowerShell size/drive checks. | Remove the audit runs later if they are no longer useful; no immediate cleanup was performed because this audit was forbidden from deleting data. | Deletion would be recoverability-sensitive and should be explicitly approved. |

## 3. Hard-rule compliance

| Rule | Status | Evidence |
|---|---|---|
| 1. No employer/customer/third-party IP | CANNOT VERIFY completely | Case-insensitive tracked-file, history and binary-string searches found no `Nissan`, `Renault`, `Eaton`, `ANSA` or `RNTBCI`; no suspicious company identifier or branding was found. Visual inspection of the committed stress, displacement, mesh, drawing and demo media showed only the synthetic bracket and generic project text. Authorship/provenance of all code and geometry cannot be proven from repository inspection alone. |
| 2. No personal email | PASS | All seven commits have author and committer `330936220+meenuparwez-commits@users.noreply.github.com`; tracked-file email search found none. |
| 3. No safety-critical defaults | FAIL | Finding A-01. Schema passes; UI/config fail. |
| 4. `CCX_PATH` only; loud stale-env failure | PASS | `src/solver_runner.py:78-128` reads only process or persisted `CCX_PATH`, validates the file and explains stale environments. No absolute solver path exists; README’s `C:\path\to\...` and code’s `...\FreeCAD 1.1\...` are examples, not executable fallbacks. |
| 5. Solver success not return code | PASS | `SolverRun.succeeded` requires job text, no errors, non-empty FRD/DAT and marker (`src/solver_runner.py:61-68`); tail marker reads last 256 bytes (`221-236`); return code is only reported. Tests explicitly reject clean return code alone (`tests/test_solver_runner.py:154-168`). |
| 6. Subprocess arguments are lists | PASS | Only two subprocess calls exist: list at `src/solver_runner.py:165-172` and list at `tests/test_calculix_smoke.py:175-183`. Fresh solves succeeded with a `CCX_PATH` containing `FreeCAD 1.1`. |
| 7. No V2–V6 work implemented early | PASS | Roadmap term search found gusset/cut-out/bent-sheet text only in limitations/engineering notes as deferred scope; no optimisation, two-part, LLM, retrieval, DoE, surrogate or OOD implementation was found. Washer annuli are an explicitly approved replacement decision, not an unapproved roadmap start. |
| 8. Required README/LICENSE/limitations wording | PASS with A-09 drift | Required sentence is at `README.md:7-9`; disclaimer is above the fold at `README.md:11-16`; limitation list is at `README.md:202-209` and `docs/limitations.md`; demonstration notice is at `LICENSE:25-30`. The stale committed drawing screenshot is A-09. |
| 9. No certified/release-ready claim or solver-finished validation claim | PASS | README, app, report and LICENSE explicitly disclaim certification/release readiness. README `145-155`, validation doc `5-7`, and engineering checks state that solver completion is not validation. |

## 4. Engineering-decision integrity

| Decision | Status | Implementing code/evidence |
|---|---|---|
| Load case/formula/free length/partial UDL | PASS | `moment_at()` implements tip `F(L-s)` and partial UDL branches at `src/analytical.py:81-97`; `tip_deflection()` implements `FL³/(3EI)` and the integrated partial UDL at lines `100-136`; stress is `6M/(bt²)` at lines `52-57`; `compute_reference()` uses `inputs.arm_length` and mid-span at `174-235`; pipeline sets UDL `load_start = fillet_radius` at `src/pipeline.py:362-365`. Analytical tests cover the textbook `c=0` limits and continuity. |
| Verdict not driven by raw peak | PASS in computation; report FAIL | Validation checks are equilibrium, one-sided deflection, away-from-root section stress, reported Kt and FoS (`src/engineering_checks.py:207-454`). FoS uses `factor_of_safety_structural` (`404-430`); raw peak remains visible. The report misrepresents these values (A-03). |
| Wide-plate stiffness bounds | PASS | `plate_modulus = E/(1-nu²)` (`src/analytical.py:47-49`); both deflections computed at `221-230`; code explicitly chooses the plate value as smaller/lower at `src/engineering_checks.py:264-269`. Fresh run: plate 0.50844 mm < beam 0.55873 mm. |
| Bolted restraint | FAIL only on lower-bound tolerance | Annulus selection uses face centroids (`src/boundary_detection.py:113-170,209-241`); `RESTRAINT_ZONE_THICKNESSES = 1.0` and structural peak exclusion are at `src/engineering_checks.py:57-151`; FoS uses structural peak and raw is reported. One-sided upper behavior is correct, but the lower threshold is incorrectly reduced 15% (A-02). |
| C3D10 and produced-mesh thickness | PASS in code; missing ordering test | Gmsh sets complete second order with `SecondOrderIncomplete = 0` (`src/mesh_generator.py:197-210`); thickness is measured per limb from produced element spans and drives an advisory Review under 2 (`src/mesh_generator.py:452-478`). Fresh baseline measured plate 2.098, arm 2.068. Ordering regression is missing (A-04). |

## 5. Tolerance and threshold audit

| Constant/default | Value | Location | Detect-based justification assessment |
|---|---:|---|---|
| `_GEOM_TOL` | 1e-6 mm | `src/cad_generator.py:44-46` | Justified relative to ~1e-7 kernel precision and real feature scale. |
| `VOLUME_REL_TOL` | 1e-4 | `src/geometry_checks.py:25-32` | PASS: detects missing 0.74% fillet with ~74× margin; explicitly not tuned to zero baseline error. |
| `BBOX_ABS_TOL` | 1e-6 mm | `src/geometry_checks.py:34-35` | Numeric CAD-coordinate tolerance; only terse justification. |
| Hole-radius match | 1e-6 mm | `src/geometry_checks.py:204-212` | Used with axis discrimination to distinguish holes from fillet; consistent with CAD tolerance. |
| `MIN_EDGE_DISTANCE_RATIO` | 1.5 | `src/schemas.py:33-35` | Advisory design guideline, explicitly not claimed as a standard. |
| `MIN_LIGAMENT_RATIO` | 1.0 | `src/schemas.py:37-38` | Advisory feature-resolution/design threshold. |
| `MIN_SLENDERNESS_RATIO` | 10 | `src/schemas.py:40-42` | Physics-based point where beam theory weakens. |
| `MIN_RING_ELEMENTS` | 2.0 | `src/schemas.py:44-48` | PASS: detects under-resolved clamped ring independently of annulus area. |
| `MAX_MESH_SIZE_FRACTION_OF_THICKNESS` | 0.5 | `src/schemas.py:50-52` | Working warning rule corresponding to two elements through thickness. |
| `MIN_ELEMENTS_THROUGH_THICKNESS` | 2.0 | `src/mesh_generator.py:36-37` | PASS: measured from produced mesh, not requested size. |
| `MIN_ELEMENT_QUALITY` | 0.1 | `src/mesh_generator.py:39-42` | Detects sliver tetrahedra likely to pollute stress. |
| `MAX_POOR_ELEMENT_FRACTION` | 0.001 | `src/mesh_generator.py:44-46` | Allows isolated slivers but detects a population; not tuned to baseline. |
| `PLANE_TOL` | 1e-6 mm | `src/boundary_detection.py:38-40` | Set above Gmsh/kernel placement noise. |
| `AREA_REL_TOL` | 1e-2 | `src/boundary_detection.py:42-54` | PASS: detects wrong face (hundreds of percent) while allowing mesh-faceted holes. |
| `ANNULUS_AREA_REL_TOL` | 0.15 | `src/boundary_detection.py:56-84` | PASS: 15%, not 5%; detects whole rear face at 780% error while leaving ring resolution to `MIN_RING_ELEMENTS`. No evidence it was loosened to pass baseline. Message formatting rounds it to `1e-01`, which is imprecise but computation uses 0.15. |
| Applied-load sum tolerance | 1e-9 relative | `src/boundary_detection.py:378-398` | Bookkeeping identity for exactly assembled nodal forces, not FE physics. |
| `EQUILIBRIUM_REL_TOL` | 0.01 | `src/engineering_checks.py:35-37` | PASS: specified 1%; baseline is much tighter. |
| `SECTION_STRESS_REL_TOL` | 0.05 | `src/engineering_checks.py:39-43` | PASS: 5%, intended to detect section/formula errors while allowing coarse meshes. Comment mentions baseline 0.14% but does not derive threshold solely from it. |
| `DEFLECTION_BAND_TOL` | 0.15 | `src/engineering_checks.py:45-50,240-270` | FAIL: stale two-sided-band rationale retained and now weakens a physical lower bound. Finding A-02. |
| `MAX_PLAUSIBLE_KT` | 5.0 | `src/engineering_checks.py:52-55` | Plausibility/advisory discriminator for a singular-region mistake; not a verdict stress limit. |
| `RESTRAINT_ZONE_THICKNESSES` | 1.0 | `src/engineering_checks.py:57-76` | PASS: decision log and comment relate it to measured decay/St Venant; it did not rescue the then-failing baseline. |
| `COORD_TOL` | 1e-6 mm | `src/drawing.py:50-52` | Projection coordinate merge above kernel noise and below feature scale. |
| `DIMENSION_TOL` | 1e-3 mm | `src/drawing.py:54-57` | Detects millimetre-scale projection/drawing error while allowing discretization. |
| `_distinct()` tolerance | 1e-4 mm | `src/drawing.py:312-318` | Near-duplicate projection coordinate merge; no result-tuning evidence. |
| Result plane/surface tolerances | 1e-6 mm | `src/result_reader.py:229-243,267-302` | Mesh-plane membership tolerance; consistent with CAD/Gmsh coordinate precision. |
| `CONVERGENCE_REL_TOL` | 0.02 | `src/pipeline.py:430-434,540-558` | PASS: 2%, judged only on deflection and section stress. |
| `MIN_PIXEL_SPREAD` | 10/255 | `src/visualization.py:19-21,44-68` | Detects a near-uniform blank render. Fresh embedded images each had full 255 spread. |

No tolerance other than the inherited 15% deflection allowance appears to have been loosened to make the shipped baseline pass. The annulus change from 5% to 15% is documented by what it must distinguish and remains fifty-fold below the wrong-region error.

## 6. Runtime evidence

### Environment and tests

```text
> conda run -n bracket-cae python -c "import sys; print(sys.executable)"
C:\Users\meenu\miniconda3\envs\bracket-cae\python.exe
```

The first sandboxed test attempt could not access pytest’s normal temp root and produced `2 failed, 220 passed, 198 errors`; a second attempt with a writable `--basetemp` reached `378 passed, 9 failed, 33 errors` but was denied access to `ccx.exe`. These were audit-sandbox permission failures, not application results. The authorized unrestricted command was:

```text
> conda run -n bracket-cae python -m pytest -ra -p no:cacheprovider --basetemp outputs/audit_pytest_20260920
collected 420 items
...
============================ 420 passed in 49.50s =============================
```

No skip, xfail, warning or error occurred in the valid run. The CalculiX smoke tests ran and passed; they did not skip.

### Fresh baseline

Command (full rendering, drawing, report and eight solver threads):

```text
> conda run -n bracket-cae python -c "... BracketInputs.from_json_file('examples/baseline_bracket.json'); run_pipeline(..., Path('outputs/audit_baseline_20260920'), solver_threads=8) ..."
```

Actual result:

```text
Verdict: Pass - every check satisfied.
Checks: 22/22 passed
Elapsed: 62.395 s; solver: 46.117 s
Mesh: 62,886 C3D10 elements; 108,160 nodes
Through thickness: plate 2.09774; arm 2.06794
Equations: 319,398
Volume: 42504.02673514074 mm^3
Tip deflection: 1.1070649914 mm
FE/beam: 1.9814 (displayed)
Section stress: 54.9232997312 MPa vs 55.0 MPa
Raw peak: 240.7617708149 MPa
Structural peak: 122.1125556657 MPa
K_t: 1.1101141424
FoS structural/raw/section: 2.2520206747 / 1.1422079139 / 5.0069824891
Clamped area: 654.1332030852 mm^2 vs 653.4512719467 mm^2
```

All expected artifacts existed and were non-empty:

| Artifact | Bytes |
|---|---:|
| `inputs.json` | 409 |
| `bracket_geometry.step` | 41,211 |
| `bracket_mesh.msh` | 11,828,036 |
| `bracket_analysis.inp` | 8,946,876 |
| `bracket_analysis.frd` | 31,295,560 |
| `bracket_analysis.dat` | 216 |
| `bracket_results.vtu` | 10,233,783 |
| `mesh.png` | 555,083 |
| `stress.png` | 125,971 |
| `displacement.png` | 90,155 |
| `bracket_drawing.dxf` | 187,458 |
| `bracket_drawing.pdf` | 87,686 |
| `run_log.txt` | 5,248 |
| `summary.json` | 7,187 |
| `engineering_report.html` | 1,046,864 |

### Baseline deviation from the decision log

Every apparent difference is below the decision log’s displayed precision and is therefore rounding, not a changed baseline:

| Quantity | Recorded | Fresh | Signed deviation |
|---|---:|---:|---:|
| Elements | 62,886 | 62,886 | 0 |
| Nodes | 108,160 | 108,160 | 0 |
| Equations | 319,398 | 319,398 | 0 |
| Volume (mm³) | 42504.0267 | 42504.02673514 | +0.00003514 |
| Tip deflection (mm) | 1.10706 | 1.107064991 | +0.000004991 |
| FE/beam | 1.9814 | 1.9814 displayed | 0 at recorded precision |
| Section FE (MPa) | 54.923 | 54.923300 | +0.000300 |
| Section theory (MPa) | 55.000 | 55.000 | 0 |
| Raw peak (MPa) | 240.762 | 240.761771 | -0.000229 |
| Structural peak (MPa) | 122.113 | 122.112556 | -0.000444 |
| Kt | 1.110 | 1.110114 | +0.000114 |
| Structural FoS | 2.252 | 2.252021 | +0.000021 |
| Raw FoS | 1.142 | 1.142208 | +0.000208 |
| Section FoS | 5.007 | 5.006982 | -0.000018 |
| Detected annulus area (mm²) | 654.133 | 654.133203 | +0.000203 |
| Hand annulus area (mm²) | 653.451 | 653.451272 | +0.000272 |
| Verdict/checks | Pass, 22/22 | Pass, 22/22 | none |

### HTML report and images

The fresh report was rendered in a browser. It stated `Units: mm, N, MPa (N/mm²), tonne/mm³, mass in kg`, showed Pass and 22/22 checks, and rendered three image elements. Independent file inspection returned:

```json
{
  "html_bytes": 1046864,
  "image_src_count": 3,
  "embedded_image_count": 3,
  "external_image_srcs": [],
  "units_present": true,
  "pre_count": 1,
  "images": [
    {"size": [1400, 800], "spread": 255},
    {"size": [1400, 800], "spread": 255},
    {"size": [1400, 800], "spread": 255}
  ]
}
```

The report’s log is inserted as `html.escape(...)` at `src/report_generator.py:452`; the passing injection test at `tests/test_report_generator.py:307-315` proves `<script>` cannot escape the `<pre>` block. The actual run log contained no script token. `_verify_render()` rejects spread below 10 (`src/visualization.py:44-68`), and the suite includes real black and white blank-scene failures (`tests/test_visualization.py:69-88`).

### Streamlit UI

The live app was driven at `http://localhost:8511`:

- Initial sketch visibly showed four green clamped washer rings and two short restraint hatch bands labelled “held under the washers only”; it did not hatch the full rear face.
- `hole_spacing = 56` was rejected before a run button was offered. The readable message said the holes reach `y = 32.500 mm` while the plate edge is `30.000 mm`, and the UI stated: “Nothing was built. Inputs are checked before any CAD is attempted.”
- Restored 220 N baseline: green `Pass - every check satisfied.` after 63.5 s, structural stress 122.1 MPa, tip deflection 1.1071 mm and FoS 2.25.
- Raised load to 500 N: red `Fail - 1 critical issue(s).` after 59.2 s, with structural FoS 0.991 below target 2.0. No traceback appeared.

### Gmsh lifecycle

Only one production initialization entry point exists. It calls:

```python
gmsh.initialize(interruptible=threading.current_thread() is threading.main_thread())
try:
    ...
finally:
    gmsh.finalize()
```

at `src/mesh_generator.py:163-219`. `test_meshing_twice_in_a_row_works` passed (`tests/test_mesh_generator.py:89-96`).

## 7. Test-quality checklist

| Required failure-sensitive test | Status | Evidence |
|---|---|---|
| Every geometry check has a negative test | PASS | Negative volume, ignored fillet/holes, bounding box, disconnected solids, missing STEP and missing holes at `tests/test_geometry_checks.py:102-216`. |
| Drawing measures projection; different bracket proves non-circularity | PASS | `tests/test_drawing.py:132-161`. |
| `dimlfac == 1`; text colour BYLAYER | PASS | `tests/test_drawing.py:203-224`. |
| First-angle placement | PASS | `tests/test_drawing.py:246-269`. |
| C3D10 mid-side ordering verified | MISSING | Finding A-04. |
| No corner node in `*CLOAD` | PASS | Equivalent loads are limited to midsides and test asserts disjoint corner/load sets at `tests/test_boundary_detection.py:205-218`. |
| FRD parsed by column position with tight packing | PASS | `tests/test_result_reader.py:71-90`; parser slices fixed positions at `src/result_reader.py:132-155`. |
| DISP pseudo-component `ALL` excluded | PASS | `tests/test_result_reader.py:93-106`. |
| Node IDs contiguous 1..N | MISSING negative test | Guard exists, but finding A-05. |
| Equation count = 3 × unrestrained nodes | PASS | `tests/test_solver_runner.py:75-81`; production expected count at `src/pipeline.py:342-343`. |
| Critical failure cannot be rescued by passes | PASS | `tests/test_engineering_checks.py:427-444`. |
| `<script>` in log cannot escape `<pre>` | PASS | `tests/test_report_generator.py:307-315`. |
| Meshing twice succeeds | PASS | `tests/test_mesh_generator.py:89-96`. |

## 8. Consistency and drift

- The external architecture list contains all production modules except `pipeline.py` and `__init__.py`; the decision log separately acknowledges `pipeline.py` as an addition. No listed production module is missing. The more fundamental drift is that `CLAUDE.md` itself is absent from the audited repository (A-06).
- `CheckResult` has not moved or duplicated, but the recorded importer count drifted from five to six (A-07).
- The input sketch agrees with washer-annulus restraint in code, tests and the live UI.
- `docs/limitations.md:24-27` explicitly says the holes do carry load. `docs/validation.md` and the report describe washer-only restraint and contain no stale “holes carry no load” wording. The report should state the load path more explicitly when A-03/A-10 is fixed.
- Exact lock and active environment satisfy imports and contain no pip packages. The portable environment’s reliance on transitive drawing dependencies is A-08.
- `outputs/` is ignored and raw run artifacts are not tracked. Documentation PNG/GIF/MP4 media are intentionally committed Phase 8 assets, not a committed run folder; their content is synthetic, though the drawing screenshot is stale (A-09).

## 9. Could not verify

- Original authorship and absence of all third-party IP cannot be proven from code/history searches or visual inspection alone. A provenance declaration from the author and source records for any reused algorithms/assets would be needed.
- The adjacent `CLAUDE.md` cannot be cryptographically tied to this publish repository. An approved committed copy or recorded hash/revision is needed (A-06).
- This audit did not inspect every frame of the 41-second MP4; the tracked images, GIF frame, filenames/strings and freshly generated artifacts showed no prohibited branding. A frame-by-frame provenance review would be needed for an absolute media claim.
- No independent commercial FE package or physical test was run. The audit verifies the agreed CalculiX/analytical workflow, not external engineering validation or certification.

## 10. Three highest-priority fixes

1. **Remove safety-critical UI/config defaults (A-01).** This is an explicit hard-rule breach at the point where a user defines the analysis. A missing or overlooked load must not silently become the demonstrator’s 220 N.
2. **Restore a true one-sided rigid-root lower bound (A-02).** The current 15% inherited allowance can let the exact over-restraint error the check was designed to detect pass silently.
3. **Make the HTML report show the verdict-driving structural values and separately label raw singular values (A-03), with regression assertions.** The current report can say Pass next to FoS 1.142 and pairs Kt with the wrong location, undermining traceability even though the computation is correct.

No fixes were applied.
