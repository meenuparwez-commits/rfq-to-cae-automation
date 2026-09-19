"""A labelled schematic of the design, redrawn from the current inputs.

This exists to answer "what do these boxes actually mean?" before anyone
spends a minute on a solve. Every dimension in the sidebar appears on the
sketch, in the place it is measured, so ``arm_length`` is visibly the free
length from the plate face rather than the overall extent, and the holes are
visibly constrained to sit above the fillet.

It is a schematic, not a drawing. The real dimensioned drawing is produced by
``src/drawing.py`` from the solid itself, with every dimension measured back
off the projection. This one is drawn from the input numbers directly, so it
can never be used as evidence that the solid matches its inputs -- it is an
interface aid and says so on its face.

Output is SVG built as text: no rendering backend, no temporary file, and it
stays sharp at any zoom. Colours are fixed against a white panel so the sketch
reads the same under a light or a dark interface theme.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from src.schemas import BracketInputs, LoadCase

VIEW_WIDTH = 1000.0
MARGIN = 16.0

# Room around each view for its dimension lines and labels.
PAD_LEFT = 74.0
PAD_RIGHT = 74.0
PAD_TOP = 82.0
PAD_BOTTOM = 120.0
GAP = 30.0  # between the two views

TARGET_HEIGHT = 330.0  # of the tallest feature, the plate
MIN_SCALE, MAX_SCALE = 1.0, 9.0

INK = "#171b21"
MUTED = "#6e7882"
RULE = "#c9d1d9"
METAL = "#dfe5ec"
METAL_EDGE = "#4a5560"
DIM = "#0b5ed7"
LOAD = "#c1121f"
FIXED = "#2f7d4f"
CLAMP_FILL = "#d8ecdf"

ARROW = 7.0  # half-length of a dimension arrowhead


def _num(value: float) -> str:
    """Trim trailing zeros so 100.0 reads as 100 but 4.5 survives."""
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text if text else "0"


@dataclass(frozen=True)
class _View:
    """Maps model millimetres onto SVG pixels for one view.

    Model z is up; SVG y is down, so the vertical mapping is inverted here
    once rather than at every call site.
    """

    origin_x: float
    origin_y: float  # SVG y of model z = 0
    scale: float

    def x(self, mm: float) -> float:
        return self.origin_x + mm * self.scale

    def y(self, mm: float) -> float:
        return self.origin_y - mm * self.scale


def _text(x: float, y: float, body: str, *, cls: str = "lbl", anchor: str = "middle",
          rotate: float | None = None) -> str:
    transform = f' transform="rotate({rotate:.1f} {x:.1f} {y:.1f})"' if rotate else ""
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" class="{cls}" text-anchor="{anchor}"'
        f'{transform}>{body}</text>'
    )


def _arrowhead(x: float, y: float, dx: float, dy: float, colour: str) -> str:
    """A filled triangle at (x, y) pointing along the unit vector (dx, dy).

    Drawn as an explicit polygon rather than an SVG marker: markers are the
    part most likely to be dropped when an SVG is rendered inside an <img>,
    and a dimension line without arrowheads is ambiguous.
    """
    px, py = -dy, dx  # perpendicular
    tip = (x, y)
    back = (x - dx * ARROW * 2, y - dy * ARROW * 2)
    left = (back[0] + px * ARROW * 0.42, back[1] + py * ARROW * 0.42)
    right = (back[0] - px * ARROW * 0.42, back[1] - py * ARROW * 0.42)
    points = " ".join(f"{a:.1f},{b:.1f}" for a, b in (tip, left, right))
    return f'<polygon points="{points}" fill="{colour}"/>'


NARROW = 3 * ARROW  # a dimension narrower than this cannot hold its own arrows

OUTSIDE = 14.0  # how far outside a narrow dimension the arrows are placed


def _h_dim(x1: float, x2: float, y: float, label: str, *,
           witness: tuple[float, float] | None = None, note: str = "") -> list[str]:
    """Horizontal dimension between two SVG x positions.

    A thickness of 4 mm is only a dozen pixels wide at this scale, which is
    too narrow for arrowheads and far too narrow for the text. Narrow
    dimensions therefore get the drafting treatment: arrows outside the
    witness lines pointing in, and the text set off to one side.
    """
    narrow = abs(x2 - x1) < NARROW
    left, right = min(x1, x2), max(x1, x2)
    span = (left - OUTSIDE, right + OUTSIDE) if narrow else (left, right)

    parts = [
        f'<line x1="{span[0]:.1f}" y1="{y:.1f}" x2="{span[1]:.1f}" y2="{y:.1f}"'
        ' class="dim"/>'
    ]
    if witness is not None:
        for x in (left, right):
            parts.append(
                f'<line x1="{x:.1f}" y1="{witness[0]:.1f}" x2="{x:.1f}"'
                f' y2="{witness[1]:.1f}" class="witness"/>'
            )

    direction = 1.0 if narrow else -1.0  # inwards when the arrows sit outside
    parts.append(_arrowhead(left, y, direction, 0.0, DIM))
    parts.append(_arrowhead(right, y, -direction, 0.0, DIM))

    if narrow:
        parts.append(_text(right + OUTSIDE + 7, y + 5, label, cls="dimtext",
                           anchor="start"))
    else:
        middle = (left + right) / 2.0
        parts.append(_text(middle, y - 8, label, cls="dimtext"))
        if note:
            parts.append(_text(middle, y + 17, note, cls="note"))
    return parts


def _v_dim(y1: float, y2: float, x: float, label: str,
           witness: tuple[float, float] | None = None) -> list[str]:
    """Vertical dimension between two SVG y positions."""
    narrow = abs(y2 - y1) < NARROW
    top, bottom = min(y1, y2), max(y1, y2)
    span = (top - OUTSIDE, bottom + OUTSIDE) if narrow else (top, bottom)

    parts = [
        f'<line x1="{x:.1f}" y1="{span[0]:.1f}" x2="{x:.1f}" y2="{span[1]:.1f}"'
        ' class="dim"/>'
    ]
    if witness is not None:
        for y in (top, bottom):
            parts.append(
                f'<line x1="{witness[0]:.1f}" y1="{y:.1f}" x2="{witness[1]:.1f}"'
                f' y2="{y:.1f}" class="witness"/>'
            )

    direction = 1.0 if narrow else -1.0
    parts.append(_arrowhead(x, top, 0.0, direction, DIM))
    parts.append(_arrowhead(x, bottom, 0.0, -direction, DIM))

    middle = (top + bottom) / 2.0
    if narrow:
        parts.append(_text(x + 10, middle + 5, label, cls="dimtext", anchor="start"))
    else:
        parts.append(_text(x - 9, middle, label, cls="dimtext", rotate=-90.0))
    return parts


def _leader(target: tuple[float, float], elbow: tuple[float, float],
            label: str, *, anchor: str = "start", colour: str = DIM) -> list[str]:
    """An arrow pointing at a feature, with the text set off to one side."""
    tx, ty = target
    ex, ey = elbow
    length = math.hypot(ex - tx, ey - ty) or 1.0
    dx, dy = (tx - ex) / length, (ty - ey) / length
    text_x = ex + (6 if anchor == "start" else -6)
    style = "dimtext" if colour == DIM else "fixedtext"
    return [
        f'<line x1="{ex:.1f}" y1="{ey:.1f}" x2="{tx:.1f}" y2="{ty:.1f}"'
        f' stroke="{colour}" stroke-width="1.3"/>',
        _arrowhead(tx, ty, dx, dy, colour),
        _text(text_x, ey + 4, label, cls=style, anchor=anchor),
    ]


def _fixed_hatch(view: _View, inputs: BracketInputs) -> list[str]:
    """The restraint symbol: ground hatching only under the washers.

    Drawn as short bands at the washer heights rather than down the whole
    plate, because that is the boundary condition. A symbol covering the
    entire rear face would state the opposite of what the model does, and the
    restraint is the single assumption a reader most needs to see.
    """
    x = view.x(0.0)
    outer = inputs.washer_diameter / 2.0
    bands = sorted({z for _, z in inputs.hole_centres()})

    parts: list[str] = []
    for centre in bands:
        top, bottom = view.y(centre + outer), view.y(centre - outer)
        parts.append(
            f'<line x1="{x:.1f}" y1="{top:.1f}" x2="{x:.1f}" y2="{bottom:.1f}"'
            f' stroke="{FIXED}" stroke-width="3"/>'
        )
        step = 9.0
        y = top
        while y <= bottom - 1:
            parts.append(
                f'<line x1="{x:.1f}" y1="{y:.1f}" x2="{x - 9:.1f}"'
                f' y2="{y + 9:.1f}" stroke="{FIXED}" stroke-width="1.4"/>'
            )
            y += step

    return parts


def _load_symbols(view: _View, inputs: BracketInputs) -> list[str]:
    """Down arrows where the load is actually applied, matched to the case.

    The two cases are applied to different faces and compared against
    different formulas, so drawing them the same way would hide the choice
    that matters most for the analytical comparison.
    """
    t, r, L = inputs.thickness, inputs.fillet_radius, inputs.arm_length
    parts: list[str] = []
    top_y = view.y(t)
    # The UDL bar is lifted further clear of the arm so its label does not
    # land on the fillet leader, which starts from the same corner.
    length = 34.0 if inputs.load_case is LoadCase.TIP_LOAD else 56.0

    if inputs.load_case is LoadCase.TIP_LOAD:
        x = view.x(t + L)
        for offset in (-5.0, 0.0, 5.0):
            parts.append(
                f'<line x1="{x + offset:.1f}" y1="{top_y - length:.1f}"'
                f' x2="{x + offset:.1f}" y2="{top_y - 4:.1f}"'
                f' stroke="{LOAD}" stroke-width="2"/>'
            )
            parts.append(_arrowhead(x + offset, top_y - 2, 0.0, 1.0, LOAD))
        label = f"F = {_num(inputs.applied_load)} N at the tip"
        parts.append(_text(x, top_y - length - 12, label, cls="load", anchor="end"))
    else:
        # The flat top of the arm begins where the fillet goes tangent, so a
        # UDL cannot start at the plate face. The analytical reference uses
        # the same span.
        start, end = view.x(t + r), view.x(t + L)
        parts.append(
            f'<line x1="{start:.1f}" y1="{top_y - length:.1f}" x2="{end:.1f}"'
            f' y2="{top_y - length:.1f}" stroke="{LOAD}" stroke-width="2"/>'
        )
        count = 7
        for index in range(count):
            x = start + (end - start) * index / (count - 1)
            parts.append(
                f'<line x1="{x:.1f}" y1="{top_y - length:.1f}" x2="{x:.1f}"'
                f' y2="{top_y - 4:.1f}" stroke="{LOAD}" stroke-width="1.6"/>'
            )
            parts.append(_arrowhead(x, top_y - 2, 0.0, 1.0, LOAD))
        label = f"F = {_num(inputs.applied_load)} N spread over the arm top"
        parts.append(
            _text((start + end) / 2, top_y - length - 12, label, cls="load")
        )

    return parts


def _side_view(view: _View, inputs: BracketInputs) -> list[str]:
    """The L profile: plate on the left, arm to the right, fillet inside."""
    H = inputs.plate_height
    L = inputs.arm_length
    t = inputs.thickness
    r = inputs.fillet_radius

    profile = (
        f"M {view.x(0):.1f} {view.y(0):.1f} "
        f"L {view.x(t + L):.1f} {view.y(0):.1f} "
        f"L {view.x(t + L):.1f} {view.y(t):.1f} "
        f"L {view.x(t + r):.1f} {view.y(t):.1f} "
        f"A {r * view.scale:.1f} {r * view.scale:.1f} 0 0 1 "
        f"{view.x(t):.1f} {view.y(t + r):.1f} "
        f"L {view.x(t):.1f} {view.y(H):.1f} "
        f"L {view.x(0):.1f} {view.y(H):.1f} Z"
    )

    parts = [
        _text(view.x(0), view.y(H) - 54, "Side view &#8212; the L profile",
              cls="title", anchor="start"),
        f'<path d="{profile}" fill="{METAL}" stroke="{METAL_EDGE}" stroke-width="1.8"'
        ' stroke-linejoin="round"/>',
    ]
    parts += _fixed_hatch(view, inputs)
    # Reads bottom-up alongside the hatching, where it cannot collide with the
    # thickness dimension at the top of the plate.
    parts.append(
        _text(view.x(0) - 22, view.y(H / 2), "held under the washers only",
              cls="fixed", rotate=-90.0)
    )
    parts += _load_symbols(view, inputs)

    # H, measured on the plate.
    parts += _v_dim(
        view.y(H), view.y(0), view.x(0) - 52, f"H {_num(H)}",
        witness=(view.x(0) - 48, view.x(0)),
    )

    # L is the FREE length, from the plate front face. The overall extent is
    # dimensioned underneath it so the difference is visible rather than
    # something to be read in a tooltip.
    base = view.y(0)
    parts += _h_dim(
        view.x(t), view.x(t + L), base + 38, f"L {_num(L)}",
        witness=(base, base + 34), note="free length, from the plate face",
    )
    parts += _h_dim(view.x(0), view.x(t + L), base + 88, f"overall {_num(t + L)}")

    # Thickness appears twice: it sets both the plate and the arm.
    parts += _h_dim(
        view.x(0), view.x(t), view.y(H) - 22, f"t {_num(t)}",
        witness=(view.y(H) - 18, view.y(H)),
    )
    parts += _v_dim(
        view.y(t), view.y(0), view.x(t + L) + 30, f"t {_num(t)}",
        witness=(view.x(t + L), view.x(t + L) + 26),
    )

    # The fillet sits on the inside corner and adds material. Its callout is
    # placed in the open air beside the plate and above the load symbols,
    # which is the one region that stays empty whatever the proportions: put
    # it near the corner instead and a short arm walks it into the length
    # dimension, a long one into the UDL arrows.
    mid = t + r * (1 - math.sqrt(0.5))
    parts += _leader(
        (view.x(mid), view.y(mid)),
        (view.x(t) + 26, max(view.y(H) + 26, view.y(t) - 110)),
        f"r {_num(r)}",
    )

    return parts


def _front_view(view: _View, inputs: BracketInputs) -> list[str]:
    """The mounting plate seen from the front, carrying the hole pattern."""
    H = inputs.plate_height
    b = inputs.width
    t = inputs.thickness
    r = inputs.fillet_radius
    radius = inputs.hole_diameter / 2.0

    def hx(y_mm: float) -> float:
        """Model y is centred on zero; the view starts at the left edge."""
        return view.x(y_mm + b / 2.0)

    parts = [
        _text(view.x(0), view.y(H) - 54, "Front view &#8212; the mounting plate",
              cls="title", anchor="start"),
        f'<rect x="{view.x(0):.1f}" y="{view.y(H):.1f}"'
        f' width="{b * view.scale:.1f}" height="{H * view.scale:.1f}"'
        f' fill="{METAL}" stroke="{METAL_EDGE}" stroke-width="1.8"/>',
    ]

    # The band the holes have to live in, which is what most rejected designs
    # get wrong.
    band = view.y(t + r)
    parts.append(
        f'<line x1="{view.x(0):.1f}" y1="{band:.1f}" x2="{view.x(b):.1f}"'
        f' y2="{band:.1f}" stroke="{MUTED}" stroke-width="1.2"'
        ' stroke-dasharray="6 4"/>'
    )
    parts.append(
        _text(view.x(0) + 8, band - 9, "holes clear the fillet",
              cls="note", anchor="start")
    )

    centres = inputs.hole_centres()
    washer = inputs.washer_diameter / 2.0
    for y_mm, z_mm in centres:
        cx, cy = hx(y_mm), view.y(z_mm)
        # The clamped ring, drawn behind the hole: this is the entire
        # restraint, so it belongs on the sketch as prominently as the hole.
        parts.append(
            f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{washer * view.scale:.1f}"'
            f' fill="{CLAMP_FILL}" stroke="{FIXED}" stroke-width="1.2"'
            ' stroke-dasharray="5 3"/>'
        )
        parts.append(
            f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{radius * view.scale:.1f}"'
            f' fill="#ffffff" stroke="{METAL_EDGE}" stroke-width="1.6"/>'
        )
        cross = radius * view.scale + 5
        parts.append(
            f'<line x1="{cx - cross:.1f}" y1="{cy:.1f}" x2="{cx + cross:.1f}"'
            f' y2="{cy:.1f}" class="centreline"/>'
        )
        parts.append(
            f'<line x1="{cx:.1f}" y1="{cy - cross:.1f}" x2="{cx:.1f}"'
            f' y2="{cy + cross:.1f}" class="centreline"/>'
        )

    base = view.y(0)
    parts += _h_dim(
        view.x(0), view.x(b), base + 38, f"b {_num(b)}",
        witness=(base, base + 34),
    )

    # Spacing is one number serving both directions, so it is dimensioned in
    # both when there are four holes.
    spacing = inputs.hole_spacing
    row_z = max(z for _, z in centres)
    left, right = hx(-spacing / 2.0), hx(spacing / 2.0)
    parts += _h_dim(left, right, view.y(row_z) - 42, f"{_num(spacing)}",
                    witness=(view.y(row_z) - 38, view.y(row_z)))

    if inputs.num_holes == 4:
        low, high = min(z for _, z in centres), max(z for _, z in centres)
        parts += _v_dim(
            view.y(high), view.y(low), view.x(b) + 40, f"{_num(spacing)}",
            witness=(view.x(b), view.x(b) + 36),
        )

    first_y, first_z = centres[0]
    parts += _leader(
        (hx(first_y) - radius * view.scale * 0.71,
         view.y(first_z) + radius * view.scale * 0.71),
        (view.x(0) - 30, view.y(first_z) + 34),
        # U+00D8 rather than the true diameter sign U+2300: every system font
        # carries it, and a missing glyph would leave a bare number.
        f"{inputs.num_holes} &#215; &#216;{_num(inputs.hole_diameter)}",
        anchor="end",
    )

    # The washer callout comes in from the right, so it points at the RIGHT
    # hole of the bottom row. Pointing at the same hole as the diameter
    # callout would drag its leader straight across the hole in between.
    near_y, near_z = centres[1] if len(centres) > 1 else centres[0]
    parts += _leader(
        (hx(near_y) + washer * view.scale * 0.71,
         view.y(near_z) + washer * view.scale * 0.71),
        (view.x(b) + 34, view.y(near_z) + 44),
        f"clamped &#216;{_num(inputs.washer_diameter)}",
        anchor="start",
        colour=FIXED,
    )

    return parts


def sketch_svg(inputs: BracketInputs) -> str:
    """A dimensioned schematic of `inputs`, as an SVG document."""
    H = inputs.plate_height
    side_width = inputs.thickness + inputs.arm_length
    front_width = inputs.width

    content_width = VIEW_WIDTH - 2 * MARGIN
    usable = content_width - 2 * (PAD_LEFT + PAD_RIGHT) - GAP
    scale = min(TARGET_HEIGHT / H, usable / (side_width + front_width))
    scale = max(MIN_SCALE, min(MAX_SCALE, scale))

    block = (side_width + front_width) * scale + 2 * (PAD_LEFT + PAD_RIGHT) + GAP
    left = MARGIN + max(0.0, (content_width - block) / 2.0)
    baseline = MARGIN + PAD_TOP + H * scale

    side = _View(left + PAD_LEFT, baseline, scale)
    front = _View(
        side.x(side_width) + PAD_RIGHT + GAP + PAD_LEFT, baseline, scale
    )

    height = MARGIN * 2 + PAD_TOP + H * scale + PAD_BOTTOM
    body = _side_view(side, inputs) + _front_view(front, inputs)

    footer = (
        "Schematic, drawn from the input numbers to show what each one means. "
        "The dimensioned drawing is generated from the solid during a run and "
        "is the one that is checked."
    )

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {VIEW_WIDTH:.0f}'
        f' {height:.0f}" width="100%" role="img"'
        ' aria-label="Dimensioned schematic of the bracket design">'
        "<style>"
        "text{font-family:'Segoe UI',Roboto,Helvetica,Arial,sans-serif;}"
        f".title{{font-size:19px;font-weight:600;fill:{INK};}}"
        f".lbl{{font-size:14px;fill:{INK};}}"
        f".dimtext{{font-size:15px;font-weight:600;fill:{DIM};}}"
        f".note{{font-size:12.5px;fill:{MUTED};}}"
        f".load{{font-size:14px;font-weight:600;fill:{LOAD};}}"
        f".fixed{{font-size:12.5px;fill:{FIXED};}}"
        f".fixedtext{{font-size:14px;font-weight:600;fill:{FIXED};}}"
        f".footer{{font-size:12.5px;fill:{MUTED};}}"
        f".dim{{stroke:{DIM};stroke-width:1.3;}}"
        f".witness{{stroke:{RULE};stroke-width:1;}}"
        f".centreline{{stroke:{MUTED};stroke-width:0.9;stroke-dasharray:5 3;}}"
        "</style>"
        f'<rect x="1" y="1" width="{VIEW_WIDTH - 2:.0f}" height="{height - 2:.0f}"'
        f' rx="10" fill="#ffffff" stroke="{RULE}"/>'
        + "".join(body)
        + _text(VIEW_WIDTH / 2, height - MARGIN - 4, footer, cls="footer")
        + "</svg>"
    )
