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

Two peaks misbehave, for different reasons, and telling them apart is the whole
point:

- The **fillet peak** is a real stress concentration on real geometry. It does
  converge, just slowly.
- The peak at the **edge of a clamped washer ring** is a mathematical
  singularity. Restraining a sharp-edged region of a continuum has no finite
  answer, so refining the mesh makes the number worse for ever.

Validation uses two quantities that do settle: tip deflection against the
rigid-root bound, and bending stress at a section away from the root where St
Venant's principle applies.

The factor of safety uses the highest stress **outside one plate thickness of
the clamped edge**, which is where the measurement shows the disturbance has
died away. Both peaks are reported, with their locations, so the number that
was set aside stays visible and a reader can disagree with the judgement
instead of having it hidden from them.

### 3. The rigid-root solutions are a floor, not a target

With b/t = 15 the section is stiffer than beam theory predicts, by roughly
1/(1−ν²), so deflection is computed against two bounds: E (beam, more flexible)
and E/(1−ν²) (plate, stiffer). Note the plate bound is the *lower* deflection,
which is easy to get backwards.

Both assume the arm grows out of a rigid wall, and the bracket is held by four
washer rings on a 4 mm plate instead. **A support can only add compliance,
never remove it**, so the stiffer closed form is a floor the FE result must
exceed — it comes out at 1.98x here, and that excess is base flexibility rather
than error.

That makes the check one-sided, and sharper than the band it replaced. A band
can be satisfied by two errors cancelling. A result *below* the floor has
exactly one explanation: the model is held more tightly than the bracket is.

### 4. The plate is held only under its washers

A washer-sized annulus around each hole on the rear face, fully fixed. The rest
of the rear face is free to lift and rotate, so the holes carry the load.

This replaced a fully fixed rear face, and the replacement is not a free win.
It trades a benign simplification for a malignant one:

| | Fully fixed rear face | Washer annuli |
|---|---|---|
| Holes | carry no load at all | carry the load |
| Peak stress | in the fillet, converges slowly | at a clamp edge, **never converges** |
| Tip deflection | comparable to beam theory | 1.98x it, from base flexibility |
| Fillet stress | 130.2 MPa | 138.8 MPa at the same load |

That last row is the interesting one: **the fully fixed face was flattering the
design by about 6.6%**, because holding the rear surface flat stiffens the very
corner the bending moment acts on.

The clamp is still an idealisation — perfectly rigid, with no bolt preload, no
friction and no contact.

`washer_diameter` is a required input with no default, because it changes the
answer. Rings are validated like any other geometry: larger than the hole, not
overlapping each other, not hanging off the plate.

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
| Clamped annulus area | 15% | Looser on purpose: the ring is not a CAD feature, so its boundary can only follow whole element faces. Selecting the whole rear face instead reads 780% out, fifty times the threshold |
| Equilibrium | 1% | The solve actually returns the load to 1e-9 |
| Section stress vs beam theory | 5% | Achieved 0.14%; headroom left for coarser meshes in the convergence study |
| Tip deflection | one-sided floor at the stiffer bound, 15% allowance | A support adds compliance and never removes it, so this cannot be violated without a real restraint error |
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

| mesh size | elements | tip deflection | change | section stress | change | fillet peak | change | clamp peak | change |
|---|---|---|---|---|---|---|---|---|---|
| 2.00 mm | 31,769 | 1.10215 | — | 55.021 | — | 118.807 | — | 214.159 | — |
| 1.50 mm | 62,886 | 1.10706 | 0.45% | 54.923 | 0.18% | 122.113 | 2.78% | 240.762 | 12.42% |
| 1.25 mm | 117,673 | 1.10517 | **0.17%** | 55.043 | **0.22%** | 122.652 | *0.44%* | 284.685 | **18.24%** |

Three different behaviours in one table:

- Tip deflection and section stress **settle**, and the section stress
  oscillates around 55.0 MPa — the beam-theory value. Convergence is judged on
  these.
- The fillet peak **converges slowly** (2.78% to 0.44%). It is a real feature
  with a finite answer; it just needs a fine mesh to find it.
- The clamp peak **diverges, and accelerates** (12.42% to 18.24%). There is no
  finite value for it to converge to.

That is decision 2 demonstrated rather than asserted, and the contrast between
"slow" and "never" is far more instructive than a single misbehaving number.

### How far the clamp singularity reaches

Peak von Mises against distance from the clamped edge, which is what sets the
exclusion zone:

| distance | peak | where |
|---|---|---|
| 0 | 273.593 MPa | at the clamp edge |
| 0.5 t | 154.473 MPa | still near the ring |
| **1.0 t** | **138.764 MPa** | **the fillet** |
| 2.1 t | 138.764 MPa | the fillet |

One plate thickness is St Venant applied to this geometry, and it is where the
disturbance measurably dies. At the moment that zone was chosen the baseline
still came out at FoS 1.982 against a target of 2.0 — **still failing** — which
is the evidence it was set from where the singularity decays rather than from
where the example would go green.

### Linearity

The baseline load was halved partway through development, from 500 N to 250 N.
Every result scaled exactly: tip deflection 1.17508 → 0.58754 mm, while FE/beam
(0.9254), the section stress error (0.14%) and K_t (1.042) were unchanged to
four figures.

Linear elasticity must behave that way. Any deviation would have meant a
nonlinearity or a load-dependent bug.

*(Measured under the earlier fully-fixed restraint, which is why the numbers
differ from the current baseline. The property still holds: the section stress
error stays at 0.14% and the compliance ratio at 1.98 whether the load is
250 N or 220 N.)*

### Why the baseline is 220 N

At 250 N under the bolted restraint the design misses its own criterion by
0.9% — FoS 1.982 against a target of 2.0. That is a correct engineering result
rather than a defect, and it is the direct cost of modelling the restraint
honestly: the fillet stress rises about 6.6% once the plate is free to rotate
at the root.

The shipped example should demonstrate a pass, so the load is 220 N, giving FoS
2.252. Raising it in the interface demonstrates a fail whenever one is wanted.

### One prediction that was wrong

Before building the annulus selection I expected the selected area to be 10-20%
out, because the ring is not a CAD feature and its boundary can only follow
whole element faces.

It is **0.10%** at the baseline. Selecting a face by its *centroid* makes the
error unbiased: straddling faces are taken and dropped in roughly equal
measure, so it cancels rather than accumulating.

| mesh | 2.00 | 1.50 | 1.25 | 1.00 | 0.75 |
|---|---|---|---|---|---|
| area error | +1.49% | +0.10% | +1.12% | +0.04% | +0.07% |

Note it does *not* fall monotonically. That is scatter, not a bias, which is
why the test bounds the magnitude and never asserts a direction.

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

**Every layout fault in the input sketch, again.** The thickness dimension
landed on the fixed-face label. The overall length collided with the footer.
The fillet callout sat on the arm; moved up, it sat under the load arrows;
moved again, it walked into the length dimension whenever the arm was short. It
now sits in the open air beside the plate and above the load symbols, the one
region that stays empty at any proportions. Not one of these was caught by a
test, and the tests that do exist — every input appears as a label, the labels
follow the inputs, all drawn geometry stays inside the canvas at seven
different proportions, the two views never overlap — would not have caught them
either. Text collision is a thing you see.

## The input sketch is not the drawing

`sketch.py` draws a dimensioned schematic of whatever is currently in the
sidebar, and the app shows it above the run button. It exists because three
things that matter were text-only until then: **L is the free length from the
front face of the plate**, the **holes live in the band above the fillet**
(much the most common reason a design is rejected), and the plate is **held
only under its washers**, which is the single assumption a reader most needs
to get right.

That last one nearly slipped through. When the restraint changed, the sketch
still hatched the whole rear face and said "rear face fully fixed" — a plain
statement of the opposite of what the model now does. A user-facing picture
that contradicts the model is worse than no picture at all.

It carries a footer saying it is a schematic, and that distinction is load
bearing. `drawing.py` measures every dimension back off the projected solid,
which is what makes it evidence that the geometry matches its inputs. The
sketch is drawn straight from the input numbers, so it can only ever restate
them. An interface aid presented as a check would be precisely the kind of
false assurance the rest of this project is built to avoid.

The load symbols follow the load case, since the applied load and its formula
have to describe the same thing. The UDL arrows start at x = t + r rather than
at the plate face, matching the shortened span the analytical reference
actually uses; a test increases the fillet radius and asserts they move right.

It is SVG written as text — no rendering backend, no temporary file, sharp at
any zoom, cheap enough to regenerate on every keystroke. Two details that
mattered: arrowheads are explicit polygons rather than SVG markers, which are
the first thing dropped when an SVG is rendered inside an `<img>`; and a 4 mm
thickness is a dozen pixels wide at this scale, too narrow to hold its own
arrows or its own label, so narrow dimensions get the drafting treatment —
arrows outside the witness lines pointing in, text set off to one side.

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
