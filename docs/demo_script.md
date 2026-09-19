# 45–60 second demo video: shot list

A recording script, not a recording. Times are cumulative; the whole thing
fits in 55 seconds at a normal speaking pace.

**Before you start**

- Run the pipeline once so the solver, Gmsh and PyVista are warm. A cold first
  run is noticeably slower and makes the tool look sluggish.
- The baseline takes about 70 seconds end to end, which is longer than the
  video. **Speed the solve up 4–8× in the edit**, or start the run, cut away,
  and cut back to the finished result. Do not stage a fake result.
- Open the **What the inputs mean** panel before you record. The sketch is the
  quickest way to show what is actually restrained, and that is the assumption
  most viewers will otherwise get wrong.
- Browser at 1280×900 or wider, no bookmarks bar, no notifications.
- Have `docs/images/drawing.png` and a run's `engineering_report.html` open in
  other tabs so you can cut to them instantly.

---

### 0:00–0:06 — What it is

**Show:** the app at rest, sidebar open on the geometry inputs.

> "This turns bracket design inputs into a full finite element check —
> geometry, mesh, solve and a verdict — in about a minute."

---

### 0:06–0:14 — Inputs are validated first

**Show:** type a hole spacing of 56 mm. The red error appears immediately.

> "Inputs are validated before any CAD is attempted. An impossible design fails
> here, with the offending dimension named, instead of deep inside the CAD
> kernel."

Let the message be readable: *"the holes reach y = 32.500 mm but the plate edge
is at 30.000 mm"*. This is the single most persuasive moment in the video —
don't rush it.

**Then:** set it back to 30 mm.

---

### 0:14–0:22 — One button

**Show:** press **Generate and Analyse**. Let the progress bar move through
Geometry → Drawing → Meshing → Solving. Cut the wait.

> "One button runs the whole chain: CadQuery geometry, a Gmsh mesh of
> second-order tetrahedra, and a CalculiX linear static solve — about 320,000
> equations."

---

### 0:22–0:32 — The verdict and the numbers

**Show:** the green Pass banner, then the three metrics, then scroll to the
*FE against hand calculation* table.

> "Pass, with a factor of safety of 2.25. But the verdict isn't the point —
> this is: every result sits next to an independent hand calculation. Bending
> stress within 0.14 percent of beam theory, and a tip deflection checked
> against a bound it physically cannot fall below."

---

### 0:32–0:40 — What is deliberately not validated

**Show:** the stress fringe plot, then the caption underneath it, which names
both peaks.

> "The bracket is held only where its bolts clamp it, and restraining a
> sharp-edged ring like that gives a stress that never converges — refine the
> mesh and it climbs for ever. So the verdict doesn't use it. It's reported
> with its location, and the factor of safety comes from the stress outside
> that zone."

This is the line that shows engineering judgement rather than software. Keep
it, even if you cut something else to make room.

---

### 0:40–0:48 — The outputs

**Show:** cut to the drawing PNG, then to the HTML report scrolling past the
checks table and the convergence table.

> "Every run also produces a first-angle manufacturing drawing — with its
> dimensions measured back off the projected geometry and checked against the
> inputs — and a self-contained HTML report."

---

### 0:48–0:55 — Close honestly

**Show:** the limitations block at the top of the report, or the
`DEMONSTRATION MODEL - NOT FOR MANUFACTURE` stamp on the drawing.

> "It's a demonstration project, not a certified tool — linear elasticity, a
> rigid clamp with no bolt preload or contact, and every result needs
> independent verification. The value is the automated workflow and the
> checking, not the CAD."

---

### Optional 4 seconds — what is actually held

Worth the room if you have it, because it is the assumption most viewers will
get wrong.

**Show:** the **What the inputs mean** panel, pointing at the green rings
around the holes.

> "It's bolted, not welded. The plate is held only under the four washers — so
> the holes carry the load, and the plate itself flexes between them. That's
> most of the deflection you just watched."

---

## What to avoid

- **Don't claim it validates designs.** It compares against references and
  reports the comparison.
- **Don't hide the run time.** Speeding up the edit is fine; implying it is
  instant is not.
- **Don't show a Fail as if it were a bug.** If you demo one — raising the load
  to 300 N takes the factor of safety under its target — say it is a correct
  engineering result about the design, not a defect in the tool.
- **Don't call the clamp-edge stress a result.** If the raw peak is on screen,
  say in the same breath that it is a singularity. A big red number without
  that sentence is the one thing in this video that could actually mislead.
- **Don't show real employer work, geometry or branding.** Everything here is
  synthetic.

## If you want a 20-second cut

Keep 0:06–0:14 (validation rejecting a bad design), 0:22–0:32 (verdict beside
hand calculations) and 0:48–0:55 (the honest close). Those three carry the
whole argument.
