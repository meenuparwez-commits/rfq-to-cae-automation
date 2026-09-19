"""Tests for src/sketch.py.

The sketch is an interface aid, so the things worth asserting are that it
says what the inputs say, that it is well formed, and that nothing lands
outside the canvas when the proportions are pushed around. Two of the three
faults in the manufacturing drawing were invisible text and geometry that
had run off the sheet, and neither would have been caught by "the file exists".
"""

from __future__ import annotations

import xml.etree.ElementTree as ElementTree

import pytest

from src.schemas import BracketInputs
from src.sketch import _h_dim, _v_dim, sketch_svg

SVG_NS = "{http://www.w3.org/2000/svg}"

BASELINE = dict(
    plate_height=100.0,
    arm_length=80.0,
    width=60.0,
    thickness=4.0,
    fillet_radius=5.0,
    hole_diameter=9.0,
    hole_spacing=30.0,
    num_holes=4,
    washer_diameter=17.0,
    material="structural_steel_s275",
    applied_load=250.0,
    load_case="tip_load",
    mesh_size=1.5,
    target_factor_of_safety=2.0,
)


def make(**overrides) -> BracketInputs:
    return BracketInputs(**{**BASELINE, **overrides})


def parse(svg: str) -> ElementTree.Element:
    return ElementTree.fromstring(svg)


def texts(svg: str) -> list[str]:
    return [
        (node.text or "").strip()
        for node in parse(svg).iter(f"{SVG_NS}text")
        if (node.text or "").strip()
    ]


def viewbox(svg: str) -> tuple[float, float]:
    _, _, width, height = parse(svg).get("viewBox").split()
    return float(width), float(height)


# -- Structure ------------------------------------------------------------


def test_output_is_well_formed_svg():
    root = parse(sketch_svg(make()))
    assert root.tag == f"{SVG_NS}svg"
    assert root.get("viewBox")


def test_every_dimension_from_the_inputs_appears_on_the_sketch():
    """If a number can be typed in the sidebar it has to be visible here.

    A label that silently goes missing would leave the reader guessing at
    exactly the input they came to understand.
    """
    labels = " | ".join(texts(sketch_svg(make())))

    assert "H 100" in labels
    assert "L 80" in labels
    assert "b 60" in labels
    assert "t 4" in labels
    assert "r 5" in labels
    assert "4 × Ø9" in labels  # 4 x diameter 9
    assert "30" in labels  # hole spacing
    assert "250" in labels  # the load


def test_labels_follow_the_inputs_rather_than_being_fixed_text():
    labels = " | ".join(texts(sketch_svg(make(
        plate_height=70.0, arm_length=45.0, width=52.0, thickness=6.0,
        fillet_radius=8.0, hole_diameter=7.0, hole_spacing=20.0,
        applied_load=1200.0,
    ))))

    assert "H 70" in labels
    assert "L 45" in labels
    assert "b 52" in labels
    assert "t 6" in labels
    assert "r 8" in labels
    assert "1200" in labels
    assert "H 100" not in labels


def test_overall_length_is_dimensioned_separately_from_the_free_length():
    """L is measured from the plate face, which is the single most likely
    thing to be misread. Both numbers are on the sketch so the difference
    cannot be missed."""
    labels = " | ".join(texts(sketch_svg(make())))

    assert "L 80" in labels
    assert "overall 84" in labels


def test_footer_says_the_sketch_is_not_the_checked_drawing():
    """The drawing produced during a run is measured off the solid; this one
    is drawn from the numbers. Conflating them would turn an interface aid
    into false evidence."""
    footer = " ".join(texts(sketch_svg(make()))).lower()

    assert "schematic" in footer
    assert "drawing" in footer


# -- Holes ----------------------------------------------------------------


def _circles(svg: str, fill: str) -> list[ElementTree.Element]:
    return [node for node in parse(svg).iter(f"{SVG_NS}circle")
            if node.get("fill") == fill]


@pytest.mark.parametrize("count", [2, 4])
def test_hole_count_is_drawn(count):
    svg = sketch_svg(make(num_holes=count))

    assert len(_circles(svg, "#ffffff")) == count
    assert f"{count} ×" in " | ".join(texts(svg))


@pytest.mark.parametrize("count", [2, 4])
def test_clamped_ring_is_drawn_around_every_hole(count):
    """The washer annuli are the entire restraint, so they have to be on the
    sketch. A drawing that showed only the holes would leave the reader to
    assume the whole rear face is held, which is what the model used to do."""
    svg = sketch_svg(make(num_holes=count))

    assert len(_circles(svg, "#d8ecdf")) == count
    assert "clamped Ø17" in " | ".join(texts(svg))


def test_clamped_ring_follows_the_washer_input():
    small = _circles(sketch_svg(make(washer_diameter=13.0)), "#d8ecdf")
    large = _circles(sketch_svg(make(washer_diameter=20.0)), "#d8ecdf")

    assert float(large[0].get("r")) > float(small[0].get("r"))


def test_hole_positions_match_the_schema():
    inputs = make()
    circles = list(parse(sketch_svg(inputs)).iter(f"{SVG_NS}circle"))
    centres = {(round(float(c.get("cx")), 1), round(float(c.get("cy")), 1))
               for c in circles}

    assert len(centres) == 4  # four distinct positions, not four stacked
    # The pattern is symmetric about the plate centreline, so the x values
    # come in a mirrored pair.
    xs = sorted({x for x, _ in centres})
    assert len(xs) == 2


def test_spacing_is_dimensioned_in_both_directions_for_four_holes():
    four = texts(sketch_svg(make(num_holes=4)))
    two = texts(sketch_svg(make(num_holes=2)))

    assert four.count("30") == 2
    assert two.count("30") == 1


def test_hole_diameter_follows_the_input():
    assert "Ø7" in "".join(texts(sketch_svg(make(hole_diameter=7.0))))


# -- Load cases -----------------------------------------------------------


def test_tip_load_and_udl_are_drawn_differently():
    """The two cases are applied to different faces and compared against
    different formulas. Drawing them the same way would hide the choice."""
    tip = " | ".join(texts(sketch_svg(make(load_case="tip_load"))))
    udl = " | ".join(texts(sketch_svg(make(load_case="udl"))))

    assert "at the tip" in tip
    assert "spread over the arm top" in udl
    assert tip != udl


def _load_lines(svg: str) -> list[ElementTree.Element]:
    return [node for node in parse(svg).iter(f"{SVG_NS}line")
            if node.get("stroke") == "#c1121f"]


def test_udl_symbols_start_where_the_flat_top_of_the_arm_starts():
    """The fillet runs tangent up to x = t + r, so a UDL cannot begin at the
    plate face, and the analytical reference uses that same shortened span.
    A sketch showing load right up to the corner would contradict it.

    Only the fillet is changed here, which leaves the drawing scale alone,
    so the load symbols must move right by exactly the extra radius.
    """
    small = sketch_svg(make(load_case="udl", fillet_radius=3.0))
    large = sketch_svg(make(load_case="udl", fillet_radius=9.0))

    assert _load_lines(small), "no load symbols drawn"

    left_small = min(float(node.get("x1")) for node in _load_lines(small))
    left_large = min(float(node.get("x1")) for node in _load_lines(large))

    assert left_large > left_small


def test_restraint_symbol_covers_only_the_washer_bands():
    """The hatching has to stop where the clamping stops.

    A ground symbol down the whole rear face would state the opposite of the
    boundary condition, and the restraint is the assumption a reader most
    needs to get right.
    """
    inputs = make()
    svg = sketch_svg(inputs)

    green = [node for node in parse(svg).iter(f"{SVG_NS}line")
             if node.get("stroke") == "#2f7d4f"]
    assert len(green) > 3  # face lines plus hatching

    ys = [float(node.get(key)) for node in green for key in ("y1", "y2")]
    plate_top, plate_bottom = _plate_span(svg)
    covered = (max(ys) - min(ys)) / (plate_bottom - plate_top)

    # Two bands of one washer each, 30 mm apart on a 100 mm plate: well under
    # half the height. The old full-face restraint would give 1.0.
    assert covered < 0.6
    assert "held under the washers only" in " | ".join(texts(svg))


def _plate_span(svg: str) -> tuple[float, float]:
    plate = next(node for node in parse(svg).iter(f"{SVG_NS}rect")
                 if node.get("rx") is None)
    top = float(plate.get("y"))
    return top, top + float(plate.get("height"))


# -- Layout ---------------------------------------------------------------


# Washer diameters here are sized to each geometry rather than left at the
# baseline 17 mm: a washer that runs off the plate is a validation error, and
# these cases exist to stress the layout, not the schema.
EXTREMES = [
    dict(),
    dict(plate_height=300.0, arm_length=10.0),
    dict(arm_length=250.0, plate_height=40.0, hole_spacing=20.0,
         hole_diameter=6.0, washer_diameter=10.0),
    dict(thickness=12.0, fillet_radius=10.0),
    dict(width=200.0, hole_spacing=80.0, washer_diameter=11.0),
    dict(width=25.0, hole_spacing=12.0, hole_diameter=5.0,
         washer_diameter=9.0),
    dict(num_holes=2, load_case="udl"),
]


@pytest.mark.parametrize("overrides", EXTREMES)
def test_geometry_stays_inside_the_canvas(overrides):
    """Nothing is allowed to fall off the sheet.

    A view that overflows its canvas is clipped by the browser without any
    error, so this is the kind of fault that only shows up as a missing
    dimension somebody never notices.
    """
    svg = sketch_svg(make(**overrides))
    width, height = viewbox(svg)
    root = parse(svg)

    xs: list[float] = []
    ys: list[float] = []
    for node in root.iter():
        tag = node.tag
        if tag == f"{SVG_NS}line":
            xs += [float(node.get("x1")), float(node.get("x2"))]
            ys += [float(node.get("y1")), float(node.get("y2"))]
        elif tag == f"{SVG_NS}circle":
            radius = float(node.get("r"))
            xs += [float(node.get("cx")) - radius, float(node.get("cx")) + radius]
            ys += [float(node.get("cy")) - radius, float(node.get("cy")) + radius]
        elif tag == f"{SVG_NS}polygon":
            for point in node.get("points").split():
                x, y = point.split(",")
                xs.append(float(x))
                ys.append(float(y))

    assert min(xs) >= 0.0
    assert max(xs) <= width
    assert min(ys) >= 0.0
    assert max(ys) <= height


@pytest.mark.parametrize("overrides", EXTREMES)
def test_extreme_proportions_still_produce_a_sketch(overrides):
    svg = sketch_svg(make(**overrides))

    assert parse(svg).tag == f"{SVG_NS}svg"
    assert len(texts(svg)) > 8


@pytest.mark.parametrize("overrides", EXTREMES)
def test_views_do_not_overlap(overrides):
    """Side view on the left, front view on the right, with a gap.

    The load symbols sit at the far end of the arm, so they are the side
    view's rightmost feature and the one that would collide first.
    """
    svg = sketch_svg(make(**overrides))
    plate = next(node for node in parse(svg).iter(f"{SVG_NS}rect")
                 if node.get("rx") is None)

    rightmost = max(
        max(float(node.get("x1")), float(node.get("x2")))
        for node in _load_lines(svg)
    )

    assert float(plate.get("x")) > rightmost


# -- Dimension primitives -------------------------------------------------


def test_wide_and_narrow_dimensions_both_get_two_arrowheads():
    """A 4 mm thickness is a dozen pixels wide, far too narrow to hold its
    own arrows, so they move outside. Losing them would leave a bare line
    that reads as a leader rather than a dimension."""
    wide = "".join(_h_dim(100.0, 300.0, 50.0, "wide"))
    narrow = "".join(_h_dim(100.0, 108.0, 50.0, "narrow"))

    assert wide.count("<polygon") == 2
    assert narrow.count("<polygon") == 2
    assert "wide" in wide and "narrow" in narrow


def test_narrow_dimension_text_is_moved_clear_of_the_line():
    narrow = "".join(_v_dim(100.0, 108.0, 50.0, "t 4"))

    assert 'text-anchor="start"' in narrow
    assert "rotate" not in narrow  # rotated text in a 8 px gap is unreadable
