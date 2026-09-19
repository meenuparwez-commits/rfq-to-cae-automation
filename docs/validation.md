# Validation

Units: mm, N, MPa (N/mm²).

**A solver finishing is not validation.** This document records what was
actually checked against an independent reference, what the numbers came out
as, and what deliberately is *not* validated.

## The three levels

### 1. The solver itself

Before any project code depended on CalculiX, a single second-order element was
solved and compared with a hand calculation: a 10 mm cube in uniaxial tension,
E = 210,000 MPa, ν = 0.3, 1000 N.

| Quantity | Hand calculation | CalculiX | |
|---|---|---|---|
| σ_zz (all 8 integration points) | 10 MPa | 1.000000E+01 | ✅ |
| Extension u_z | 4.761905×10⁻⁴ mm | 4.761905E-04 | ✅ |
| Poisson contraction u_x | −1.428571×10⁻⁴ mm | −1.428571E-04 | ✅ |
| Base reactions | −1000 N | 4 × −250 | ✅ |
| Transverse stresses | 0 | ~10⁻¹⁵ | ✅ |

The restraints were deliberately minimal — the base held vertically plus just
enough to stop sliding and spinning. Clamping the whole base would have
suppressed the lateral contraction and hidden a boundary-condition error behind
a stress value that still looked right. **The Poisson row is the one that
catches over-constraint.**

This lives in the repository as `tests/test_calculix_smoke.py` and runs with
every `pytest`.

### 2. The geometry

The CAD volume is compared with a closed-form hand calculation on every run:

```
V = (t·H + L·t + r²(1 − π/4)) · b  −  n · π(d/2)² · t
  = 42,504.026735 mm³  for the baseline
```

CAD and hand calculation agree to **zero relative error**. The fillet sits at a
concave corner, so it *adds* material — a sign error there would be a 0.74%
volume discrepancy, which the 1e-4 tolerance catches with 74× margin.

### 3. The analysis

Two quantities are validated, and one is deliberately not.

## What is validated

### Equilibrium

The most fundamental check: reactions must balance the applied load. It also
catches a load applied to the wrong face or in the wrong direction, which
nothing else would notice.

| | |
|---|---|
| Applied | 220 N in −z |
| Reactions | [1.59×10⁻¹⁰, −2.88×10⁻⁹, **220.000000**] N |
| Vertical error | 0.00 |
| Lateral | 1.7×10⁻¹¹ |

### Tip deflection against the rigid-root bound

Both closed forms assume the arm grows out of a rigid wall. The bracket does
not: it is held by four washer rings on a 4 mm plate, and that plate bends and
rotates. A support can only **add** compliance, never remove it, so the
rigid-root solution is a floor:

| | |
|---|---|
| Plate bound, E/(1−ν²) | 0.50844 mm (the stiffer, hence *lower*, bound) |
| Beam bound, E | 0.55873 mm |
| **FE result** | **1.10706 mm** |
| Compliance ratio | FE/beam **1.98** |

The bracket is about twice as flexible as the same bracket welded to a wall,
and essentially all of that is the mounting plate flexing between its bolts.

This makes the check **one-sided, and sharper than the band it replaced**. A
band can be satisfied by two errors cancelling. A result *below* the rigid-root
value has exactly one explanation — the model is held more tightly than the
bracket is — which is precisely the error a restraint change is most likely to
introduce. How far *above* the floor the result sits is a property of the
design, so it is reported as a ratio rather than bounded by a number nobody
could justify.

### Bending stress away from the root

Sampled at a section far from both the load and the restraint, where St Venant's
principle applies and beam theory should be accurate.

| Section s | FE | Beam theory σ = 6M/(bt²) | Ratio |
|---|---|---|---|
| 20 mm | 187.57 MPa | 187.50 | 1.0004 |
| 40 mm (mid-span) | 124.83 MPa | 125.00 | 0.9986 |
| 60 mm | 62.68 MPa | 62.50 | 1.0029 |

*(measured at 500 N; the shipped baseline is 220 N and scales exactly)*

Top and bottom surfaces come out equal and opposite — +124.823 / −124.829 at
mid-span — confirming pure bending with no membrane component, which is exactly
what the theory assumes.

## What is deliberately not validated

**Either peak stress.** There are now two badly-behaved ones, and the
convergence study separates them instead of lumping them together:

| mesh size | elements | tip deflection | change | section stress | change | fillet peak | change | clamp peak | change |
|---|---|---|---|---|---|---|---|---|---|
| 2.00 mm | 31,769 | 1.10215 | — | 55.021 | — | 118.807 | — | 214.159 | — |
| 1.50 mm | 62,886 | 1.10706 | 0.45% | 54.923 | 0.18% | 122.113 | 2.78% | 240.762 | 12.42% |
| 1.25 mm | 117,673 | 1.10517 | **0.17%** | 55.043 | **0.22%** | 122.652 | 0.44% | 284.685 | **18.24%** |

Three different behaviours in one table:

- **Tip deflection and section stress settle.** The section stress oscillates
  around **55.0 MPa, the beam-theory value**. These are what convergence is
  judged on.
- **The fillet peak converges, but slowly** — 2.78% then 0.44%. It is a real
  stress concentration on real geometry, so it does have a finite answer; it
  just takes a fine mesh to find it.
- **The clamp peak diverges, and accelerates** — 12.42% then 18.24%. This one
  has no finite answer to converge to. Restraining a sharp-edged ring of a
  continuum is a mathematical singularity, and refining the mesh makes the
  number worse for ever.

That distinction is the whole reason the verdict uses neither. The factor of
safety is taken on the highest stress **outside one plate thickness** of the
clamped edge, which is where the disturbance has measurably died away:

| distance from the clamped edge | peak | where |
|---|---|---|
| 0 | 273.593 MPa | at the clamp edge |
| 0.5 t | 154.473 MPa | still near the ring |
| **1.0 t** | **138.764 MPa** | **the fillet** |
| 2.1 t | 138.764 MPa | the fillet |

*(measured at 250 N before the baseline load was reduced)*

The fillet peak is then **reported** as a stress concentration factor:

```
K_t = σ_FE,structural / σ_beam,root = 122.113 / 110.000 = 1.110
```

with its location — x = 4.04, y = 1.12, z = 8.35 mm. That matters: the fillet
runs from (x=4, z=9) to (x=9, z=4), so the peak is on it. Taking K_t on the raw
peak instead would give 2.189, which is the ratio of a mesh-dependent number to
a closed-form one and says more about the mesh than about the bracket.

## A free check: linearity

The baseline load was reduced from 500 N to 250 N partway through development.
Every result scaled exactly:

| | 500 N | 250 N | ratio |
|---|---|---|---|
| Tip deflection | 1.17508 mm | 0.58754 mm | 0.5000 |
| FE/beam | 0.9254 | 0.9254 | unchanged |
| Section stress error | 0.14% | 0.14% | unchanged |
| K_t | 1.042 | 1.042 | unchanged |

*(measured under the earlier fully-fixed restraint. The same holds now: the
section stress error stays at 0.14% and the compliance ratio at 1.98 whether
the load is 250 N or 220 N.)*

Linear elasticity *must* behave this way. Any deviation would have meant a
nonlinearity or a load-dependent bug.

## Mesh adequacy

Engineering decision 5 requires at least two elements through the thickness,
because a single element cannot represent bending through it and the result
comes out too stiff — the dangerous direction to be wrong in.

A measured sweep showed the conventional "mesh size ≤ t/2" rule has **no
margin**:

| mesh size | elements | plate | arm | min quality |
|---|---|---|---|---|
| 4.00 mm | 8,294 | 1.14 | 1.02 | **0.000** |
| 3.00 mm | 11,587 | 1.18 | 1.06 | **0.000** |
| 2.00 mm (= t/2) | 31,769 | 2.00 | **1.98** | 0.301 |
| 1.50 mm | 62,886 | 2.10 | 2.07 | 0.273 |
| 1.25 mm | 117,673 | 2.71 | 2.68 | 0.212 |

At exactly t/2 the arm measures 1.98 and fails the requirement. Gmsh treats
mesh size as a target rather than a cap, and unstructured tetrahedra scatter
either side of it. The **baseline was moved to 1.5 mm**; the threshold was not
loosened to make the default pass.

At 3–4 mm the minimum element quality is 0.000 — fully degenerate elements,
because one element is trying to span the thickness.

## The drawing

The drawing's dimensions are measured back off the projected geometry and
compared with the validated inputs, so the check exercises the CAD, the
projection and the drawing together rather than comparing inputs with
themselves. All eight agree exactly.

## Summary of the shipped baseline

H 100, L 80, b 60, t 4, r 5, 4 × ⌀9 holes clamped under ⌀17 washers, S275
steel, 220 N tip load, 1.5 mm mesh. 62,886 elements, 319,398 equations.

| Check | Result |
|---|---|
| CAD volume vs hand calculation | 0.00e+00 relative error |
| Clamped area vs hand calculation | 654.133 vs 653.451 mm², 0.10% |
| Equilibrium | exact to 1e-9 |
| Tip deflection | 1.10706 mm, 1.98× the rigid-root floor |
| Bending stress at mid-span | 0.14% from beam theory |
| Mesh through thickness | 2.1 elements |
| Convergence | settled to 0.17% |
| K_t | 1.110, fillet peak 122.113 MPa |
| Clamp singularity | 240.762 MPa, reported and set aside |
| **Factor of safety** | **2.252** against a target of 2.0 |
| **Verdict** | **Pass**, 22 checks |

**None of this makes the tool certified.** It makes the numbers traceable. See
[limitations.md](limitations.md).
