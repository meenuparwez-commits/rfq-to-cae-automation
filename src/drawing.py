"""2D manufacturing drawing: first-angle projection to DXF and PDF.

Units: mm. Drawn 1:1 on an A3 sheet.

Views are true projections with hidden-line removal, computed from the solid by
OCP's HLR algorithm rather than redrawn by hand from the input numbers. That
distinction matters for the dimension check below.

    Front view          the L profile, looking along -y
    Left side view      the plate face and its holes, looking along +x
    Top view            the plan, looking down

First angle, so the view from the left is placed on the RIGHT of the front
view and the view from above is placed BELOW it. The projection symbol on the
sheet says so too, because a drawing that does not state its projection
convention is ambiguous.

**The dimension check measures the projection, not the inputs.** Writing
"100" on the drawing because the input said 100 and then comparing it with 100
would be circular and prove nothing. Every dimension here is recovered from the
projected geometry - bounding boxes, arc radii, circle centres - and then
compared with the validated inputs, so the check actually exercises the CAD,
the projection and the drawing together.

This is a demonstrator. The sheet carries an "educational demonstrator" stamp
and is not a manufacturing release.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import ezdxf
from ezdxf.enums import TextEntityAlignment
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.GCPnts import GCPnts_QuasiUniformDeflection
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt
from OCP.GeomAbs import GeomAbs_CurveType
from OCP.HLRAlgo import HLRAlgo_Projector
from OCP.HLRBRep import HLRBRep_Algo, HLRBRep_HLRToShape
from OCP.TopAbs import TopAbs_EDGE
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS

from src.geometry_checks import CheckResult
from src.schemas import BracketInputs

# Coordinates closer than this are treated as the same, in mm. Well above CAD
# kernel noise, far below any real feature.
COORD_TOL = 1e-6

# Tolerance when comparing a measured drawing dimension with its input, in mm.
# Projection and curve discretisation introduce small errors; a real mistake
# would be a whole millimetre or more.
DIMENSION_TOL = 1e-3

# Deflection for discretising curves that are neither lines nor circles, in mm.
CURVE_DEFLECTION = 0.05

SHEET_WIDTH = 420.0  # A3 landscape
SHEET_HEIGHT = 297.0
MARGIN = 10.0
VIEW_GAP = 40.0

LAYER_OUTLINE = "OUTLINE"
LAYER_HIDDEN = "HIDDEN"
LAYER_DIMENSIONS = "DIMENSIONS"
LAYER_TEXT = "TEXT"
LAYER_BORDER = "BORDER"

STAMP = "EDUCATIONAL DEMONSTRATOR - NOT FOR MANUFACTURE"


@dataclass(frozen=True)
class Line2D:
    start: tuple[float, float]
    end: tuple[float, float]

    @property
    def is_horizontal(self) -> bool:
        return abs(self.start[1] - self.end[1]) <= COORD_TOL

    @property
    def is_vertical(self) -> bool:
        return abs(self.start[0] - self.end[0]) <= COORD_TOL


@dataclass(frozen=True)
class Arc2D:
    centre: tuple[float, float]
    radius: float
    start_angle: float  # degrees, counter-clockwise
    end_angle: float


@dataclass(frozen=True)
class Circle2D:
    centre: tuple[float, float]
    radius: float


@dataclass
class Projection:
    """One projected view, in its own 2D coordinates (mm)."""

    name: str
    lines: list[Line2D] = field(default_factory=list)
    arcs: list[Arc2D] = field(default_factory=list)
    circles: list[Circle2D] = field(default_factory=list)
    hidden: list[Line2D] = field(default_factory=list)

    def points(self) -> list[tuple[float, float]]:
        found: list[tuple[float, float]] = []
        for line in self.lines:
            found.extend([line.start, line.end])
        for circle in self.circles:
            cx, cy = circle.centre
            found.extend(
                [(cx - circle.radius, cy - circle.radius),
                 (cx + circle.radius, cy + circle.radius)]
            )
        for arc in self.arcs:
            cx, cy = arc.centre
            found.extend(
                [(cx - arc.radius, cy - arc.radius),
                 (cx + arc.radius, cy + arc.radius)]
            )
        return found

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        points = self.points()
        if not points:
            return (0.0, 0.0, 0.0, 0.0)

        xs = [point[0] for point in points]
        ys = [point[1] for point in points]

        return (min(xs), min(ys), max(xs), max(ys))

    @property
    def width(self) -> float:
        xmin, _, xmax, _ = self.bounds
        return xmax - xmin

    @property
    def height(self) -> float:
        _, ymin, _, ymax = self.bounds
        return ymax - ymin

    def translated(self, dx: float, dy: float) -> Projection:
        return Projection(
            name=self.name,
            lines=[
                Line2D((l.start[0] + dx, l.start[1] + dy),
                       (l.end[0] + dx, l.end[1] + dy))
                for l in self.lines
            ],
            arcs=[
                Arc2D((a.centre[0] + dx, a.centre[1] + dy), a.radius,
                      a.start_angle, a.end_angle)
                for a in self.arcs
            ],
            circles=[
                Circle2D((c.centre[0] + dx, c.centre[1] + dy), c.radius)
                for c in self.circles
            ],
            hidden=[
                Line2D((l.start[0] + dx, l.start[1] + dy),
                       (l.end[0] + dx, l.end[1] + dy))
                for l in self.hidden
            ],
        )


# --- Projection -----------------------------------------------------------


def project(
    shape,
    direction: tuple[float, float, float],
    x_direction: tuple[float, float, float],
    name: str,
    include_hidden: bool = True,
) -> Projection:
    """Project a solid onto a plane with hidden-line removal.

    Args:
        shape: a CadQuery Workplane or Shape.
        direction: the viewing direction (the projection plane normal).
        x_direction: which model direction becomes the drawing's horizontal.
        include_hidden: collect hidden edges for dashed lines.
    """
    solid = shape.val().wrapped if hasattr(shape, "val") else shape.wrapped

    algorithm = HLRBRep_Algo()
    algorithm.Add(solid)
    algorithm.Projector(
        HLRAlgo_Projector(
            gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(*direction), gp_Dir(*x_direction))
        )
    )
    algorithm.Update()
    algorithm.Hide()

    to_shape = HLRBRep_HLRToShape(algorithm)

    projection = Projection(name=name)
    for compound in (to_shape.VCompound(), to_shape.OutLineVCompound()):
        if compound is not None:
            _collect(compound, projection, hidden=False)

    if include_hidden:
        for compound in (to_shape.HCompound(), to_shape.OutLineHCompound()):
            if compound is not None:
                _collect(compound, projection, hidden=True)

    return projection


def _collect(compound, projection: Projection, hidden: bool) -> None:
    """Turn the edges of an HLR compound into 2D entities."""
    explorer = TopExp_Explorer(compound, TopAbs_EDGE)

    while explorer.More():
        # Current() hands back a generic TopoDS_Shape; the curve adaptor needs
        # a TopoDS_Edge, so the downcast is explicit.
        _add_edge(TopoDS.Edge(explorer.Current()), projection, hidden)
        explorer.Next()


def _add_edge(edge, projection: Projection, hidden: bool) -> None:
    curve = BRepAdaptor_Curve(edge)
    first, last = curve.FirstParameter(), curve.LastParameter()
    kind = curve.GetType()

    if kind == GeomAbs_CurveType.GeomAbs_Line:
        start = curve.Value(first)
        end = curve.Value(last)
        line = Line2D((start.X(), start.Y()), (end.X(), end.Y()))
        (projection.hidden if hidden else projection.lines).append(line)
        return

    if kind == GeomAbs_CurveType.GeomAbs_Circle and not hidden:
        circle = curve.Circle()
        centre = circle.Location()
        radius = circle.Radius()

        if abs((last - first) - 2 * math.pi) <= 1e-6:
            projection.circles.append(
                Circle2D((centre.X(), centre.Y()), radius)
            )
            return

        start = curve.Value(first)
        end = curve.Value(last)
        middle = curve.Value((first + last) / 2.0)

        start_angle = _angle(centre, start)
        end_angle = _angle(centre, end)
        middle_angle = _angle(centre, middle)

        # DXF arcs sweep counter-clockwise. If the curve's own midpoint does
        # not lie on that sweep, the ends are the other way round; swapping
        # them gives the same arc rather than its 270-degree complement.
        if not _within_sweep(start_angle, end_angle, middle_angle):
            start_angle, end_angle = end_angle, start_angle

        projection.arcs.append(
            Arc2D((centre.X(), centre.Y()), radius, start_angle, end_angle)
        )
        return

    _discretise(curve, first, last, projection, hidden)


def _angle(centre, point) -> float:
    return math.degrees(
        math.atan2(point.Y() - centre.Y(), point.X() - centre.X())
    ) % 360.0


def _within_sweep(start: float, end: float, probe: float) -> bool:
    """True if `probe` lies on the counter-clockwise sweep start -> end."""
    span = (end - start) % 360.0
    offset = (probe - start) % 360.0

    return offset <= span


def _discretise(curve, first: float, last: float, projection, hidden: bool) -> None:
    """Approximate any other curve as a chain of short straight segments."""
    sampler = GCPnts_QuasiUniformDeflection(curve, CURVE_DEFLECTION, first, last)
    if not sampler.IsDone() or sampler.NbPoints() < 2:
        return

    previous = None
    for index in range(1, sampler.NbPoints() + 1):
        point = sampler.Value(index)
        current = (point.X(), point.Y())
        if previous is not None:
            line = Line2D(previous, current)
            (projection.hidden if hidden else projection.lines).append(line)
        previous = current


# --- Measuring the projection --------------------------------------------


def _distinct(values: list[float], tol: float = 1e-4) -> list[float]:
    """Sorted values with near-duplicates merged."""
    result: list[float] = []
    for value in sorted(values):
        if not result or abs(value - result[-1]) > tol:
            result.append(value)
    return result


@dataclass(frozen=True)
class MeasuredDimension:
    """One dimension read off the drawing, and what it should be."""

    name: str
    measured: float
    expected: float

    @property
    def error(self) -> float:
        return abs(self.measured - self.expected)

    @property
    def matches(self) -> bool:
        return self.error <= DIMENSION_TOL


def measure(
    front: Projection, left: Projection, inputs: BracketInputs
) -> list[MeasuredDimension]:
    """Recover the design dimensions from the projected views.

    Nothing here reads a value from `inputs` and writes it back out. Every
    number is taken from the geometry; `inputs` supplies only what it should
    have been.
    """
    measurements: list[MeasuredDimension] = []

    measurements.append(
        MeasuredDimension(
            "Overall length (t + L)",
            front.width,
            inputs.thickness + inputs.arm_length,
        )
    )
    measurements.append(
        MeasuredDimension("Plate height H", front.height, inputs.plate_height)
    )

    # The arm's top face gives the thickness: horizontal lines in the front
    # view sit at y = 0, y = t and y = H.
    horizontals = _distinct(
        [line.start[1] for line in front.lines if line.is_horizontal]
    )
    if len(horizontals) >= 2:
        measurements.append(
            MeasuredDimension(
                "Thickness t", horizontals[1] - horizontals[0], inputs.thickness
            )
        )

    if front.arcs and inputs.fillet_radius > 0.0:
        radius = min(arc.radius for arc in front.arcs)
        measurements.append(
            MeasuredDimension("Fillet radius r", radius, inputs.fillet_radius)
        )

    measurements.append(
        MeasuredDimension("Width b", left.width, inputs.width)
    )

    if left.circles:
        diameters = _distinct([circle.radius * 2 for circle in left.circles])
        measurements.append(
            MeasuredDimension(
                "Hole diameter", diameters[0], inputs.hole_diameter
            )
        )

        xs = _distinct([circle.centre[0] for circle in left.circles])
        if len(xs) >= 2:
            measurements.append(
                MeasuredDimension(
                    "Hole spacing across width", xs[-1] - xs[0], inputs.hole_spacing
                )
            )

        ys = _distinct([circle.centre[1] for circle in left.circles])
        if len(ys) >= 2:
            measurements.append(
                MeasuredDimension(
                    "Hole spacing up the plate", ys[-1] - ys[0], inputs.hole_spacing
                )
            )

    return measurements


def check_dimensions(measurements: list[MeasuredDimension]) -> CheckResult:
    """Every dimension on the drawing agrees with the validated inputs.

    This is what makes the drawing trustworthy rather than decorative: it
    catches a projection that silently dropped a feature, a view built from
    the wrong direction, or a solid that does not match the inputs it was
    supposedly built from.
    """
    if not measurements:
        return CheckResult(
            name="Drawing dimensions",
            passed=False,
            message="No dimensions could be measured from the projection.",
        )

    wrong = [item for item in measurements if not item.matches]
    passed = not wrong

    if passed:
        detail = ", ".join(
            f"{item.name} {item.measured:.3f}" for item in measurements
        )
        message = (
            f"All {len(measurements)} dimensions measured from the projected "
            f"geometry match the inputs within {DIMENSION_TOL} mm: {detail}."
        )
    else:
        detail = "; ".join(
            f"{item.name}: drawing {item.measured:.4f} mm vs input "
            f"{item.expected:.4f} mm"
            for item in wrong
        )
        message = f"{len(wrong)} dimension(s) disagree with the inputs - {detail}."

    return CheckResult(
        name="Drawing dimensions",
        passed=passed,
        message=message,
        value=float(len(measurements) - len(wrong)),
        expected=float(len(measurements)),
    )


# --- Sheet ----------------------------------------------------------------


DIMSTYLE = "BRACKET"


def _setup_document():
    document = ezdxf.new("R2010", setup=True)

    # Explicit RGB rather than ACI index colours. Index 7 means "black or
    # white, whichever contrasts with the background", so it renders almost
    # invisible in a viewer that assumes the opposite background to the one
    # being used. Explicit colours look the same everywhere.
    for name, rgb, linetype in (
        (LAYER_OUTLINE, (0, 0, 0), "CONTINUOUS"),
        (LAYER_HIDDEN, (150, 150, 150), "DASHED"),
        (LAYER_DIMENSIONS, (0, 80, 160), "CONTINUOUS"),
        (LAYER_TEXT, (0, 0, 0), "CONTINUOUS"),
        (LAYER_BORDER, (0, 0, 0), "CONTINUOUS"),
    ):
        layer = document.layers.add(name, linetype=linetype)
        layer.rgb = rgb

    # A dimension style of our own, because ezdxf's bundled EZDXF and
    # EZ_RADIUS presets carry dimlfac = 100: they assume a drawing in metres
    # annotated in centimetres. Inheriting that silently multiplies every
    # dimension by 100, so an 84 mm bracket is labelled 8400. Their text
    # height of 0.25 mm is invisible at this scale too.
    style = document.dimstyles.add(DIMSTYLE)
    style.dxf.dimlfac = 1.0  # drawing units are millimetres, 1:1
    style.dxf.dimtxt = 3.0  # text height, mm
    style.dxf.dimasz = 2.5  # arrow size, mm
    style.dxf.dimexe = 1.25  # extension line overshoot
    style.dxf.dimexo = 0.625  # extension line offset from the feature
    style.dxf.dimgap = 0.8
    style.dxf.dimdec = 2
    style.dxf.dimtad = 1  # text above the dimension line
    style.dxf.dimscale = 1.0
    # BYLAYER (256), not BYBLOCK (0). BYBLOCK leaves the colour to be supplied
    # by whatever inserts the dimension's block, which resolves to an
    # invisible colour in some renderers: the arrows and lines appear and the
    # numbers silently do not.
    style.dxf.dimclrt = 256  # text
    style.dxf.dimclrd = 256  # dimension line
    style.dxf.dimclre = 256  # extension lines

    return document


def _draw(msp, projection: Projection) -> None:
    for line in projection.hidden:
        msp.add_line(line.start, line.end, dxfattribs={"layer": LAYER_HIDDEN})
    for line in projection.lines:
        msp.add_line(line.start, line.end, dxfattribs={"layer": LAYER_OUTLINE})
    for arc in projection.arcs:
        msp.add_arc(
            center=arc.centre,
            radius=arc.radius,
            start_angle=arc.start_angle,
            end_angle=arc.end_angle,
            dxfattribs={"layer": LAYER_OUTLINE},
        )
    for circle in projection.circles:
        msp.add_circle(
            center=circle.centre,
            radius=circle.radius,
            dxfattribs={"layer": LAYER_OUTLINE},
        )


def _first_angle_symbol(msp, x: float, y: float, size: float = 6.0) -> None:
    """The ISO projection symbol: a truncated cone in two views.

    First angle puts the view from the left on the right, so the frustum's
    end view (concentric circles) is drawn to the RIGHT of its side view.
    Third angle would be the other way round. A drawing without this symbol is
    ambiguous about which convention it follows.
    """
    half = size / 2.0
    small = half * 0.55

    msp.add_lwpolyline(
        [
            (x, y - half), (x, y + half),
            (x + size, y + small), (x + size, y - small), (x, y - half),
        ],
        dxfattribs={"layer": LAYER_BORDER},
    )

    centre_x = x + size * 2.2
    msp.add_circle((centre_x, y), half, dxfattribs={"layer": LAYER_BORDER})
    msp.add_circle((centre_x, y), small, dxfattribs={"layer": LAYER_BORDER})


def _title_block(msp, inputs: BracketInputs, material_name: str) -> None:
    """Title block, including the demonstrator stamp."""
    width, height = 170.0, 40.0
    x = SHEET_WIDTH - MARGIN - width
    y = MARGIN

    msp.add_lwpolyline(
        [(x, y), (x + width, y), (x + width, y + height), (x, y + height), (x, y)],
        close=True,
        dxfattribs={"layer": LAYER_BORDER},
    )

    rows = [
        ("TITLE", "L-shaped cantilever mounting bracket"),
        ("MATERIAL", material_name),
        ("UNITS", "mm   (all dimensions)"),
        ("SCALE", "1:1   A3   FIRST ANGLE PROJECTION"),
        ("STATUS", STAMP),
    ]

    line_height = height / (len(rows) + 1)
    for index, (label, value) in enumerate(rows):
        text_y = y + height - line_height * (index + 1)
        msp.add_text(
            label,
            height=2.2,
            dxfattribs={"layer": LAYER_TEXT},
        ).set_placement((x + 3, text_y), align=TextEntityAlignment.LEFT)
        msp.add_text(
            value,
            height=2.2,
            dxfattribs={"layer": LAYER_TEXT},
        ).set_placement((x + 32, text_y), align=TextEntityAlignment.LEFT)

    _first_angle_symbol(msp, x - 28, y + height / 2)


def _border(msp) -> None:
    msp.add_lwpolyline(
        [
            (MARGIN, MARGIN),
            (SHEET_WIDTH - MARGIN, MARGIN),
            (SHEET_WIDTH - MARGIN, SHEET_HEIGHT - MARGIN),
            (MARGIN, SHEET_HEIGHT - MARGIN),
            (MARGIN, MARGIN),
        ],
        close=True,
        dxfattribs={"layer": LAYER_BORDER},
    )

    msp.add_text(
        STAMP, height=4.0, dxfattribs={"layer": LAYER_TEXT}
    ).set_placement(
        (SHEET_WIDTH / 2, SHEET_HEIGHT - MARGIN - 8),
        align=TextEntityAlignment.MIDDLE_CENTER,
    )


def _dimension_views(msp, front: Projection, left: Projection, inputs) -> None:
    """Place the dimensions that a maker would need."""
    fx0, fy0, fx1, fy1 = front.bounds
    lx0, ly0, lx1, ly1 = left.bounds

    style = {"dimstyle": DIMSTYLE}

    msp.add_linear_dim(
        base=(fx0, fy0 - 12),
        p1=(fx0, fy0),
        p2=(fx1, fy0),
        dxfattribs={"layer": LAYER_DIMENSIONS},
        **style,
    ).render()

    msp.add_linear_dim(
        base=(fx0 - 14, fy0),
        p1=(fx0, fy0),
        p2=(fx0, fy1),
        angle=90,
        dxfattribs={"layer": LAYER_DIMENSIONS},
        **style,
    ).render()

    msp.add_linear_dim(
        base=(lx0, ly0 - 12),
        p1=(lx0, ly0),
        p2=(lx1, ly0),
        dxfattribs={"layer": LAYER_DIMENSIONS},
        **style,
    ).render()

    if front.arcs:
        arc = min(front.arcs, key=lambda item: item.radius)
        msp.add_radius_dim(
            center=arc.centre,
            radius=arc.radius,
            angle=225,
            dimstyle=DIMSTYLE,
            dxfattribs={"layer": LAYER_DIMENSIONS},
        ).render()

    if left.circles:
        hole = left.circles[0]
        msp.add_diameter_dim(
            center=hole.centre,
            radius=hole.radius,
            angle=45,
            dimstyle=DIMSTYLE,
            dxfattribs={"layer": LAYER_DIMENSIONS},
        ).render()

        xs = _distinct([circle.centre[0] for circle in left.circles])
        ys = _distinct([circle.centre[1] for circle in left.circles])
        if len(xs) >= 2 and len(ys) >= 2:
            msp.add_linear_dim(
                base=(xs[0], ly1 + 12),
                p1=(xs[0], ys[-1]),
                p2=(xs[-1], ys[-1]),
                dxfattribs={"layer": LAYER_DIMENSIONS},
                **style,
            ).render()
            msp.add_linear_dim(
                base=(lx1 + 14, ys[0]),
                p1=(xs[-1], ys[0]),
                p2=(xs[-1], ys[-1]),
                angle=90,
                dxfattribs={"layer": LAYER_DIMENSIONS},
                **style,
            ).render()


@dataclass(frozen=True)
class DrawingResult:
    """What the drawing step produced."""

    dxf_path: Path
    pdf_path: Path | None
    measurements: list[MeasuredDimension]
    check: CheckResult

    def __str__(self) -> str:
        pdf = self.pdf_path.name if self.pdf_path else "no PDF"
        return f"{self.dxf_path.name} + {pdf}, {len(self.measurements)} dimensions"


def create_drawing(
    shape,
    inputs: BracketInputs,
    dxf_path: str | Path,
    pdf_path: str | Path | None = None,
    material_name: str | None = None,
) -> DrawingResult:
    """Build the three-view drawing, write DXF and PDF, and check it.

    Raises:
        RuntimeError: if the DXF could not be written.
    """
    dxf_path = Path(dxf_path)
    dxf_path.parent.mkdir(parents=True, exist_ok=True)

    # Front: looking along -y, so the sheet shows model x to the right and
    # model z up - the L profile.
    front = project(shape, (0, -1, 0), (1, 0, 0), "Front view")
    # Left side: looking along +x at the plate face, so the sheet shows model
    # y to the right and model z up - the holes appear as circles.
    left = project(shape, (1, 0, 0), (0, 1, 0), "Left side view")
    # Top: looking down.
    top = project(shape, (0, 0, -1), (1, 0, 0), "Top view")

    measurements = measure(front, left, inputs)
    check = check_dimensions(measurements)

    # Normalise each view to its own origin, then place them in first angle.
    front = front.translated(-front.bounds[0], -front.bounds[1])
    left = left.translated(-left.bounds[0], -left.bounds[1])
    top = top.translated(-top.bounds[0], -top.bounds[1])

    base_x, base_y = 45.0, 120.0
    front = front.translated(base_x, base_y)
    # View from the left goes on the RIGHT in first angle.
    left = left.translated(base_x + front.width + VIEW_GAP, base_y)
    # View from above goes BELOW in first angle.
    top = top.translated(base_x, base_y - VIEW_GAP - top.height)

    document = _setup_document()
    msp = document.modelspace()

    _border(msp)
    for view in (front, left, top):
        _draw(msp, view)
        xmin, ymin, _, _ = view.bounds
        msp.add_text(
            view.name, height=3.5, dxfattribs={"layer": LAYER_TEXT}
        ).set_placement((xmin, ymin - 6), align=TextEntityAlignment.LEFT)

    _dimension_views(msp, front, left, inputs)
    _title_block(msp, inputs, material_name or inputs.material)

    document.saveas(str(dxf_path))

    if not dxf_path.is_file() or dxf_path.stat().st_size == 0:
        raise RuntimeError(f"DXF was not written to {dxf_path}.")

    written_pdf = None
    if pdf_path is not None:
        written_pdf = _export_pdf(document, Path(pdf_path))

    return DrawingResult(
        dxf_path=dxf_path,
        pdf_path=written_pdf,
        measurements=measurements,
        check=check,
    )


def _export_pdf(document, pdf_path: Path) -> Path | None:
    """Render the sheet to PDF.

    A failed PDF does not fail the drawing: the DXF is the deliverable a
    workshop would use, and the PDF is for reading.
    """
    pdf_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from ezdxf.addons.drawing import Frontend, RenderContext
        from ezdxf.addons.drawing.matplotlib import MatplotlibBackend

        figure = plt.figure(figsize=(16.5, 11.7))  # A3 in inches
        axes = figure.add_axes([0, 0, 1, 1])
        axes.set_axis_off()

        Frontend(RenderContext(document), MatplotlibBackend(axes)).draw_layout(
            document.modelspace(), finalize=True
        )
        figure.savefig(str(pdf_path), dpi=300)
        plt.close(figure)
    except Exception:  # noqa: BLE001 - the DXF is what matters
        return None

    return pdf_path if pdf_path.is_file() and pdf_path.stat().st_size else None
