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
| Applied | 250 N in −z |
| Reactions | [−4.17×10⁻⁹, 9.69×10⁻¹⁰, **250.000000**] N |
| Vertical error | 0.00 |
| Lateral | 1.7×10⁻¹¹ |

### Tip deflection against beam theory

Engineering decision 3 predicted — *before any numbers existed* — that a wide
section would sit between the beam and plate stiffness bounds, because a
section with b ≫ t bends more like a plate, which is stiffer by 1/(1−ν²).

| | |
|---|---|
| Plate bound, E/(1−ν²) | 0.57778 mm (the stiffer, hence *lower*, bound) |
| **FE result** | **0.58754 mm** |
| Beam bound, E | 0.63492 mm |

FE lands 1.7% above the plate bound and 7.5% below the beam bound, with
b/t = 15. The prediction held.

The check is therefore a *band* between the two bounds rather than a percentage
tolerance — a physical statement rather than an arbitrary number.

### Bending stress away from the root

Sampled at a section far from both the load and the restraint, where St Venant's
principle applies and beam theory should be accurate.

| Section s | FE | Beam theory σ = 6M/(bt²) | Ratio |
|---|---|---|---|
| 20 mm | 187.57 MPa | 187.50 | 1.0004 |
| 40 mm (mid-span) | 124.83 MPa | 125.00 | 0.9986 |
| 60 mm | 62.68 MPa | 62.50 | 1.0029 |

*(measured at 500 N; the shipped baseline is 250 N and halves exactly)*

Top and bottom surfaces come out equal and opposite — +124.823 / −124.829 at
mid-span — confirming pure bending with no membrane component, which is exactly
what the theory assumes.

## What is deliberately not validated

**The peak stress.** It sits in the fillet stress concentration, where the
mathematical stress rises without limit as the mesh is refined. Validating
against it would mean validating against a number that depends on the mesh.

The convergence study makes this visible rather than merely asserting it:

| mesh size | elements | tip deflection | change | peak von Mises | change | section stress |
|---|---|---|---|---|---|---|
| 2.00 mm | 31,769 | 0.58684 | — | 126.253 | — | 62.519 |
| 1.50 mm | 62,886 | 0.58754 | 0.12% | 130.217 | 3.10% | 62.413 |
| 1.25 mm | 117,673 | 0.58779 | **0.04%** | 131.626 | **1.08%** | 62.547 |

Tip deflection and section stress have settled — and the section stress
oscillates around **62.5 MPa, the beam-theory value**. The peak climbs
monotonically with every refinement and shows no sign of stopping.

The peak is instead **reported** as a stress concentration factor:

```
K_t = σ_FE,peak / σ_beam,root = 130.217 / 125.000 = 1.042
```

with its location — x = 8.35, y = 2.62, z = 4.04 mm. That matters: the fillet
runs from (x=4, z=9) to (x=9, z=4), so the peak is at the fillet's lower
tangent. A peak at x = 0 would have been a singularity at the edge of the fixed
face, which must not drive a verdict.

## A free check: linearity

The baseline load was reduced from 500 N to 250 N partway through development.
Every result scaled exactly:

| | 500 N | 250 N | ratio |
|---|---|---|---|
| Tip deflection | 1.17508 mm | 0.58754 mm | 0.5000 |
| FE/beam | 0.9254 | 0.9254 | unchanged |
| Section stress error | 0.14% | 0.14% | unchanged |
| K_t | 1.042 | 1.042 | unchanged |

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

H 100, L 80, b 60, t 4, r 5, 4 × ⌀9 holes, S275 steel, 250 N tip load,
1.5 mm mesh.

| Check | Result |
|---|---|
| CAD volume vs hand calculation | 0.00e+00 relative error |
| Equilibrium | exact to 1e-9 |
| Tip deflection | between the plate and beam bounds |
| Bending stress at mid-span | 0.14% from beam theory |
| Mesh through thickness | 2.1 elements |
| Convergence | settled to 0.04% |
| K_t | 1.042, peak in the fillet |
| **Factor of safety** | **2.112** against a target of 2.0 |
| **Verdict** | **Pass**, 21 checks |

**None of this makes the tool certified.** It makes the numbers traceable. See
[limitations.md](limitations.md).
