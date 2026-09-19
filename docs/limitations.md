# Limitations

**This is an educational proof of concept.** It is not a certified or
release-ready engineering tool. Every result it produces requires independent
engineering verification and is not suitable for product release or safety
certification.

Units throughout: mm, N, MPa (N/mm²), tonne/mm³ for density, mass in kg.

## What the analysis assumes

**Linear elastic material.** Stress is proportional to strain with no yielding,
no plasticity and no failure model. A reported stress above the yield strength
is not a prediction of what the part does — it is a statement that the linear
assumption has stopped being valid there.

**Small displacements.** Geometry is not updated as the part deflects. Fine at
0.59 mm on an 84 mm part; not fine for anything that visibly bends.

**Static loading only.** No dynamics, no impact, no resonance.

## What the boundary conditions assume

**The plate is held only where its bolts clamp it** — a washer-sized annulus
around each hole on the rear face, fully fixed in all three directions. The
rest of the rear face is free to lift and rotate, so the holes do carry the
load. That is closer to a bolted joint than a fully fixed face, but it buys a
specific problem that has to be stated plainly:

> **There is a stress singularity at the edge of every clamped ring.**
> Restraining a sharp-edged region of a continuum produces a stress that rises
> without limit as the mesh is refined — there is no finite value for it to
> converge to. On the baseline the raw peak reads 240.8 MPa at a washer edge
> and climbs by 18% per refinement with no sign of stopping.

The tool therefore reports that peak but never judges on it. The factor of
safety uses the highest stress **outside a zone of one plate thickness around
the clamped edge**, which is where the measurement shows the disturbance has
died away (273.6 → 154.5 → 138.8 MPa at 0, 0.5t and 1.0t on an earlier run).
Beyond that the highest stress is the fillet again, which is a real feature.

The clamp itself is still an idealisation: it is perfectly rigid, with **no
bolt preload, no friction and no contact**. A real washer deforms, the joint
can slip, and the plate can only lift where the clamping pressure runs out.
None of that is modelled.

**The tip deflection is no longer comparable to a built-in cantilever.** With
a flexible mounting the bracket deflects about twice what the rigid-root
closed form predicts (1.107 mm against 0.559 mm). That excess is real base
flexibility, not an error — but it does mean beam theory is a lower bound here
rather than a target.

**The load is applied as equivalent nodal forces** on either the tip face or
the flat top of the arm. Real loads arrive through a bracket, a bolt or a
contact patch. St Venant's principle means this matters only near the load,
which is why validation is done away from it.

## What is outside this version

- Gussets, cut-outs and other stiffening features
- Multiple parts, bolts, contact and preload
- Sheet-metal forming details (bend allowance, springback, thinning). The
  corner here is a machined fillet, not a bend. A bent-sheet variant with outer
  radius r + t is costed in the decision log and deferred to V2
- Nonlinear material behaviour
- Fatigue and fracture
- Thermal loads and thermal expansion
- Manufacturing tolerances and their effect on the result
- Weld, adhesive or fastener assessment
- Buckling
- Any form of certification

## What the numbers mean, and do not mean

**The peak stress is mesh dependent and is not used for validation.** It sits
in the fillet stress concentration, where the mathematical stress rises as the
mesh is refined. The convergence study shows it: between 1.5 mm and 1.25 mm
elements the tip deflection settles to 0.04% while the peak von Mises is still
climbing by about 1% per refinement, with no sign of stopping.

Validation therefore uses two quantities that *do* settle:

1. **Tip deflection** against beam theory, bounded by the beam modulus E and
   the plate modulus E/(1−ν²)
2. **Bending stress at a section away from the root**, where St Venant's
   principle applies

The peak is reported separately as K_t = σ_FE,peak / σ_beam,root, with its
location, so a peak sitting on the edge of the fixed face — a restraint
singularity rather than a real feature — can be recognised as such.

**A factor of safety here is a factor on first yield under a single static
load.** It carries no allowance for fatigue, load uncertainty, material
variability, manufacturing deviation or corrosion, all of which a real design
factor would have to cover.

## What the drawing is and is not

The drawing is generated from the same solid the analysis used, and its
dimensions are measured back off the projected geometry and compared with the
inputs. That makes it consistent with the model.

It is **not** a manufacturing release. It carries no tolerances, no surface
finish, no material specification beyond a name, no datums, no geometric
dimensioning and tolerancing, no weld symbols, no revision control and no
approval signatures. Every sheet is stamped
`EDUCATIONAL DEMONSTRATOR - NOT FOR MANUFACTURE`.

## Known software limitations

- The `.frd` parser reads CalculiX's fixed-column format by position. It is
  tested, but it is the component most likely to need attention after a
  CalculiX version change.
- The through-thickness measure is a mean element extent in the thin direction,
  not a true count of element layers. It is a good proxy and is calibrated
  against a measured mesh sweep, but it is a proxy.
- Results are read at nodes, as CalculiX extrapolates them. Stresses are most
  accurate at integration points.
- The tests require CalculiX to be installed and `CCX_PATH` to be set. They
  fail rather than skip when it is missing, deliberately: a skipped test reads
  as a pass and would hide a broken toolchain.
