# Engineering notes

Design decisions, measured results, and the bugs worth remembering. Units are
mm, N, MPa (N/mm²) throughout.

## Design decisions

These were fixed before any code was written, and the rest of the project
depends on them.

### 1. The load case must match the analytical formula

Two load cases, each paired with its closed form:

| Load case | Root stress | Tip deflection |
|---|---|---|
| Tip edge load F | σ = 6FL/(bt²) | δ = FL³/(3EI) |
| UDL, total F on the shelf | σ = 3FL/(bt²) | δ = FL³/(8EI) |

with I = bt³/12 and L the *free* arm length from the front face of the plate.

Comparing a tip load against a UDL formula disagrees by a factor of two and
looks exactly like an FE error, which is a miserable thing to debug.

### 2. Do not validate against the peak stress

The peak sits in the fillet stress concentration. Refine the mesh and it keeps
rising — it does not converge, so it cannot be a validation target.

Validation uses two quantities that do settle: tip deflection against beam
theory, and bending stress at a section away from the root where St Venant's
principle applies. The peak is reported separately as
K_t = σ_FE,peak / σ_beam,root, with its location.

The location matters. A peak in the fillet is a real feature; a peak on the
edge of the fixed face is a restraint singularity and must not drive a verdict.

### 3. Wide sections bend like plates, not beams

With b/t = 15 the section is stiffer than beam theory predicts, by roughly
1/(1−ν²). Deflection is therefore reported against two bounds: E (beam, more
flexible) and E/(1−ν²) (plate, stiffer). The FE result is expected between
them.

Note the plate bound is the *lower* deflection, which is easy to get backwards.

### 4. The whole rear face is fixed

The simplest restraint that represents bolting to a wall. It has one
consequence that must be stated wherever results are: **the mounting holes
carry no load in this model.** Restraining washer-sized annuli around the holes
instead is a future improvement.

### 5. Second-order tetrahedra, at least two through the thickness

Linear tetrahedra assume constant strain within each element and lock in
bending, making a thin plate several times stiffer than it is. C3D10 elements
have mid-side nodes and can bend.

One element across the thickness cannot represent bending through it either. A
mesh failing this drives a Review verdict, not a silent run.

## Tolerances

Every tolerance is set from **what it must detect**, never from what the code
currently achieves. A tolerance tuned to today's measured error passes at zero
margin and turns red on the next library upgrade.

| Check | Tolerance | Reasoning |
|---|---|---|
| Volume vs hand calculation | 1e-4 relative | Smallest defect to catch is a missing fillet at 0.74% of the section — 74× the threshold |
| Face area | 1% | A wrong face is out by hundreds of percent. Cannot be tighter: a meshed hole is a polygon, which legitimately shifts the area by ~0.1% |
| Equilibrium | 1% | The solve actually returns the load to 1e-9 |
| Section stress vs beam theory | 5% | Achieved 0.14%; headroom left for coarser meshes in the convergence study |
| Tip deflection | band between the two stiffness bounds, 15% allowance | A physical statement rather than an arbitrary percentage |
| Mesh convergence | 2% between the two finest levels | Tighter is meaningless against the other approximations in the model |

## Measured results

### The "element size ≤ t/2" rule has no margin

Elements through the thickness, measured as t / (mean element extent in the
thin direction) for each limb separately:

| mesh size | elements | plate | arm | min quality | time |
|---|---|---|---|---|---|
| 4.00 mm | 8,294 | 1.14 | 1.02 | **0.000** | 0.8 s |
| 3.00 mm | 11,587 | 1.18 | 1.06 | **0.000** | 0.8 s |
| 2.00 mm (= t/2) | 31,769 | 2.00 | **1.98** | 0.301 | 2.2 s |
| 1.50 mm | 62,886 | 2.10 | 2.07 | 0.273 | 4.7 s |
| 1.25 mm | 117,673 | 2.71 | 2.68 | 0.212 | 10 s |
| 1.00 mm | 205,334 | 3.32 | 3.30 | 0.224 | 21 s |

At exactly t/2 the arm measures 1.98 and fails the ≥ 2 requirement. Gmsh treats
mesh size as a target rather than a cap, and unstructured tetrahedra scatter
either side of it.

**The baseline mesh was refined to 1.5 mm; the threshold was not loosened to
make the default pass.** At 3–4 mm the minimum element quality is 0.000 —
fully degenerate elements, because one element is trying to span the thickness.

An earlier version of this measure used the global mean edge length over the
whole part. That mixes the thin plate with the bulk of the arm and would let a
plate one element thick pass unnoticed.

### Convergence

| mesh size | elements | tip deflection | change | peak von Mises | change | section stress |
|---|---|---|---|---|---|---|
| 2.00 mm | 31,769 | 0.58684 | — | 126.253 | — | 62.519 |
| 1.50 mm | 62,886 | 0.58754 | 0.12% | 130.217 | 3.10% | 62.413 |
| 1.25 mm | 117,673 | 0.58779 | **0.04%** | 131.626 | **1.08%** | 62.547 |

Tip deflection and section stress have settled, and the section stress
oscillates around 62.5 MPa — the beam-theory value. The peak climbs
monotonically with every refinement and shows no sign of stopping.

That is decision 2 demonstrated rather than asserted.

### Linearity

The baseline load was halved partway through development, from 500 N to 250 N.
Every result scaled exactly: tip deflection 1.17508 → 0.58754 mm, while FE/beam
(0.9254), the section stress error (0.14%) and K_t (1.042) were unchanged to
four figures.

Linear elasticity must behave that way. Any deviation would have meant a
nonlinearity or a load-dependent bug.

### Why the baseline is 250 N

At 500 N the design fails its own criterion: FoS = 275 / 260.4 = 1.06 against a
target of 2.0. That is a correct engineering result, not a defect — but the
shipped example should demonstrate a pass, and raising the load in the
interface demonstrates a fail whenever one is wanted.

## Bugs worth remembering

Each of these is now covered by a regression test.

**`Workplane.val()` returns only the first object on the stack.** The
"is it one connected solid?" check used it, so two disconnected bodies read as
one solid — the check could not catch the thing it existed for. `vals()` is the
whole stack.

**A threshold argument that changed only the message.** The mesh quality check
accepted a `min_quality` argument but counted poor elements using a value fixed
at mesh time. Passing a different threshold changed the printed text and
nothing else. A parameter that silently does nothing is worse than no
parameter.

**Gmsh and worker threads.** Gmsh installs a SIGINT handler on initialise so
Ctrl+C can interrupt a long mesh. Python only allows that from the main thread,
and Streamlit runs scripts in a worker thread, so meshing failed with *"signal
only works in main thread of the main interpreter"*. It now asks for
interruption only when it is available. Every test and script passed before
this surfaced; only driving the real interface found it.

**Every drawing dimension multiplied by 100.** ezdxf's bundled dimension styles
carry `dimlfac = 100` — they assume a drawing in metres annotated in
centimetres — so an 84 mm bracket was labelled 8400. Their text height of
0.25 mm also made the radius and diameter dimensions invisible.

**Dimension text rendered in an invisible colour.** `dimclrt = 0` means
BYBLOCK, which leaves the colour to whatever inserts the dimension's block. The
arrows and extension lines drew; the numbers silently did not.

**Drawing entities that were present but invisible.** The border, title block
and view labels used ACI colour index 7, which means "black or white, whichever
contrasts with the background". On a white sheet they rendered nearly white.
They were in the file the whole time and the DXF extents were correct, which is
exactly why no assertion would have caught it.

The last three share a lesson: some defects are only visible by looking at the
artefact a human would actually use.

## Conventions the code relies on

**Coordinates.** x runs outward from the wall with the plate's rear face — the
fixed face — at x = 0. y runs across the width, centred on zero. z is up with
the arm's underside at z = 0. Several stages find faces by coordinate, so this
is a contract between modules.

**Free arm length.** L is measured from the *front* face of the plate. The
arm's solid box spans x = 0 to t + L while its free length is L. Building it L
long overall would put every analytical comparison out by one thickness.

**Element ordering.** meshio's `tetra10` ordering matches CalculiX's C3D10, so
elements are written unpermuted. Verified by a test rather than assumed: a
wrong permutation still solves and simply gives the wrong answer.

**Hole positions are derived, not input.** There is a hole spacing but no hole
height, so the pattern is centred in the band between the top of the fillet
region and the top of the plate.

**The UDL loaded length is L − r.** The flat top of the arm begins where the
fillet goes tangent. The textbook UDL result assumes the full L, a 6% error on
this geometry if ignored.

## Working with CalculiX

**The return code is not a success signal.** `ccx -v` prints its version and
exits 201. Success requires a "Job finished" line, no error line, both result
files non-empty, and the `.frd` ending with its `9999` record — a `.frd` can be
present, large and still truncated.

**The `.frd` is a fixed-column format.** A real data row reads:

```
 -1         1 4.00000E+00-3.00000E+01 9.00000E+00
```

There is no separator between a positive value and a following negative one.
Splitting on whitespace merges two numbers and shifts every later column.
Fields are read by position: 3 characters of marker, 10 of node id, 12 per
value.

**The displacement block declares four components and has three.** The fourth
is the pseudo-component `ALL`. Trusting the declared count reads a column that
is not there.

**Loads are applied as nodal forces, not pressure.** CalculiX pressure acts
along the face normal, but both load cases act downwards; on the tip face the
normal is horizontal, so a pressure would pull the arm sideways. For a six-node
triangle under uniform traction the consistent nodal forces are zero at the
corners and A/3 at each mid-side node — the exact integral of the shape
functions.

## Testing approach

Every check has a matching test that proves it can **fail**. A check that has
only ever passed is not evidence of anything, and two of the bugs above were
caught exactly that way.

The CalculiX tests fail rather than skip when the solver is missing. A skipped
test prints a quiet `s` that reads as a pass and would hide a broken toolchain.

Before any project code depended on CalculiX, a single element was solved and
compared with a hand calculation — including a Poisson-contraction check chosen
specifically because it is the one that catches an over-constrained model,
where the stress still looks correct.

## Deliberately out of scope

Gussets, cut-outs, multiple parts, bolts, contact, preload, sheet-metal forming
details, nonlinear material, fatigue, fracture, thermal loads, buckling,
manufacturing tolerances, certification.

A bent-sheet corner with outer radius r + t was costed and deferred. Both arcs
would share the centre (t+r, t+r), giving constant thickness through the bend.
The section works out to 707.9823 mm² two independent ways, a volume of
41,461.06 mm³ — 2.45% below the machined L. It was deferred because it moves
every recorded baseline, shrinks the fixed face from 5745.5 to 5205.5 mm², and
changes a schema limit, for a small difference in the result.
