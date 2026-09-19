"""Tests for src/drawing.py.

Units: mm.

The point of the dimension check is that it measures the projected geometry
rather than echoing the inputs. Several tests below exist specifically to prove
it is not circular: change an input, and the measured dimension must follow.

Two regression tests guard bugs that were found by looking at the rendered
sheet rather than by any assertion: dimensions multiplied by 100, and
dimension text rendered in an invisible colour.
"""

from __future__ import annotations

import ezdxf
import pytest

from src import cad_generator, drawing
from src.schemas import BracketInputs


@pytest.fixture(scope="module")
def inputs() -> BracketInputs:
    return BracketInputs.from_json_file("examples/baseline_bracket.json")


@pytest.fixture(scope="module")
def bracket(inputs):
    return cad_generator.build_from_inputs(inputs)


@pytest.fixture(scope="module")
def result(bracket, inputs, tmp_path_factory):
    directory = tmp_path_factory.mktemp("drawing")
    return drawing.create_drawing(
        bracket,
        inputs,
        directory / "bracket_drawing.dxf",
        directory / "bracket_drawing.pdf",
        material_name="Structural steel S275",
    )


# --- Projections ----------------------------------------------------------


def test_front_view_matches_the_l_profile(bracket, inputs):
    front = drawing.project(bracket, (0, -1, 0), (1, 0, 0), "front")

    assert front.width == pytest.approx(inputs.thickness + inputs.arm_length)
    assert front.height == pytest.approx(inputs.plate_height)


def test_left_view_shows_the_plate_face(bracket, inputs):
    left = drawing.project(bracket, (1, 0, 0), (0, 1, 0), "left")

    assert left.width == pytest.approx(inputs.width)
    assert left.height == pytest.approx(inputs.plate_height)


def test_holes_appear_as_circles_in_the_left_view(bracket, inputs):
    """Four holes, all at the right diameter, in the view that faces them."""
    left = drawing.project(bracket, (1, 0, 0), (0, 1, 0), "left")

    assert len(left.circles) == inputs.num_holes
    for circle in left.circles:
        assert circle.radius * 2 == pytest.approx(inputs.hole_diameter)


def test_hole_centres_in_the_view_match_the_model(bracket, inputs):
    """The projection must put the holes where the schema says they are."""
    left = drawing.project(bracket, (1, 0, 0), (0, 1, 0), "left")

    drawn = sorted(
        (round(circle.centre[0], 6), round(circle.centre[1], 6))
        for circle in left.circles
    )
    expected = sorted(
        (round(y, 6), round(z, 6)) for y, z in inputs.hole_centres()
    )

    assert drawn == expected


def test_the_fillet_appears_as_an_arc(bracket, inputs):
    front = drawing.project(bracket, (0, -1, 0), (1, 0, 0), "front")

    assert front.arcs
    assert min(arc.radius for arc in front.arcs) == pytest.approx(
        inputs.fillet_radius
    )


def test_hidden_edges_are_collected_separately(bracket):
    """Holes behind the plate are hidden lines, drawn dashed, not solid."""
    front = drawing.project(bracket, (0, -1, 0), (1, 0, 0), "front")

    assert front.hidden
    assert front.lines


def test_hidden_edges_can_be_suppressed(bracket):
    front = drawing.project(
        bracket, (0, -1, 0), (1, 0, 0), "front", include_hidden=False
    )

    assert not front.hidden


def test_translating_a_view_moves_every_entity(bracket):
    front = drawing.project(bracket, (0, -1, 0), (1, 0, 0), "front")
    moved = front.translated(10.0, 20.0)

    before = front.bounds
    after = moved.bounds

    assert after[0] == pytest.approx(before[0] + 10.0)
    assert after[1] == pytest.approx(before[1] + 20.0)
    assert moved.width == pytest.approx(front.width)


# --- The dimension check is not circular ---------------------------------


def test_every_dimension_matches_the_inputs(result):
    assert result.check.passed, result.check.message
    assert len(result.measurements) == 8
    assert all(item.matches for item in result.measurements)


def test_measured_values_come_from_the_geometry_not_the_inputs(inputs, tmp_path):
    """Change the design and the measurements must follow.

    If `measure` were echoing inputs back, this would pass trivially for any
    geometry. Building a different bracket and checking the numbers move with
    it is what makes the check meaningful.
    """
    wider = inputs.model_copy(update={"width": 80.0, "plate_height": 120.0})
    shape = cad_generator.build_from_inputs(wider)

    front = drawing.project(shape, (0, -1, 0), (1, 0, 0), "front")
    left = drawing.project(shape, (1, 0, 0), (0, 1, 0), "left")
    measurements = {item.name: item.measured for item in drawing.measure(front, left, wider)}

    assert measurements["Width b"] == pytest.approx(80.0)
    assert measurements["Plate height H"] == pytest.approx(120.0)


def test_a_mismatch_between_drawing_and_inputs_is_caught(inputs, bracket):
    """Measure a real bracket, then compare against different inputs."""
    front = drawing.project(bracket, (0, -1, 0), (1, 0, 0), "front")
    left = drawing.project(bracket, (1, 0, 0), (0, 1, 0), "left")

    lying = inputs.model_copy(update={"width": 75.0})
    measurements = drawing.measure(front, left, lying)
    check = drawing.check_dimensions(measurements)

    assert not check.passed
    assert "Width b" in check.message
    assert "60" in check.message and "75" in check.message


def test_no_measurements_is_a_failure_not_a_pass():
    """An empty projection must not read as 'nothing wrong'."""
    check = drawing.check_dimensions([])

    assert not check.passed
    assert "No dimensions" in check.message


def test_measured_dimension_tolerance():
    inside = drawing.MeasuredDimension("x", 10.0, 10.0 + drawing.DIMENSION_TOL / 2)
    outside = drawing.MeasuredDimension("x", 10.0, 10.1)

    assert inside.matches
    assert not outside.matches


# --- Sheet ----------------------------------------------------------------


def test_dxf_and_pdf_are_written(result):
    assert result.dxf_path.is_file() and result.dxf_path.stat().st_size > 0
    assert result.pdf_path is not None
    assert result.pdf_path.is_file() and result.pdf_path.stat().st_size > 0


def test_the_sheet_carries_the_demonstrator_stamp(result):
    """The sheet must never read as a production drawing."""
    document = ezdxf.readfile(str(result.dxf_path))
    texts = [
        entity.dxf.text
        for entity in document.modelspace()
        if entity.dxftype() == "TEXT"
    ]

    assert any(drawing.STAMP in text for text in texts)
    assert any("FIRST ANGLE PROJECTION" in text for text in texts)
    assert any("mm" in text for text in texts)


def test_dimension_scale_factor_is_one(result):
    """Regression: ezdxf's bundled dimstyles carry dimlfac = 100.

    Inheriting one labelled an 84 mm bracket as 8400. The sheet is 1:1 in
    millimetres, so the factor must be exactly 1.
    """
    document = ezdxf.readfile(str(result.dxf_path))
    style = document.dimstyles.get(drawing.DIMSTYLE)

    assert style.dxf.dimlfac == pytest.approx(1.0)


def test_dimension_colours_are_bylayer(result):
    """Regression: BYBLOCK (0) rendered the numbers invisibly.

    The arrows and lines appeared and the text silently did not.
    """
    document = ezdxf.readfile(str(result.dxf_path))
    style = document.dimstyles.get(drawing.DIMSTYLE)

    assert style.dxf.dimclrt == 256
    assert style.dxf.dimclrd == 256


def test_layers_have_explicit_colours(result):
    """ACI 7 means 'contrast with the background', which is viewer dependent."""
    document = ezdxf.readfile(str(result.dxf_path))

    for name in (drawing.LAYER_OUTLINE, drawing.LAYER_TEXT, drawing.LAYER_BORDER):
        assert document.layers.get(name).rgb == (0, 0, 0)


def test_the_sheet_contains_all_three_views_and_dimensions(result):
    document = ezdxf.readfile(str(result.dxf_path))
    msp = document.modelspace()

    kinds = [entity.dxftype() for entity in msp]

    assert kinds.count("DIMENSION") >= 5
    assert kinds.count("CIRCLE") >= 4  # the holes in the left view
    assert "ARC" in kinds  # the fillet in the front view


def test_views_are_arranged_in_first_angle(bracket, inputs, tmp_path):
    """View from the left goes on the RIGHT; view from above goes BELOW.

    Third angle would place them the other way round, so this is the
    difference between a correct drawing and a mirrored one.
    """
    result = drawing.create_drawing(
        bracket, inputs, tmp_path / "d.dxf", pdf_path=None
    )
    document = ezdxf.readfile(str(result.dxf_path))
    msp = document.modelspace()

    labels = {
        entity.dxf.text: (entity.dxf.insert.x, entity.dxf.insert.y)
        for entity in msp
        if entity.dxftype() == "TEXT" and entity.dxf.text.endswith("view")
    }

    front = labels["Front view"]
    left = labels["Left side view"]
    top = labels["Top view"]

    assert left[0] > front[0], "left side view must be to the right of the front view"
    assert top[1] < front[1], "top view must be below the front view"


def test_drawing_works_without_a_pdf(bracket, inputs, tmp_path):
    result = drawing.create_drawing(
        bracket, inputs, tmp_path / "only.dxf", pdf_path=None
    )

    assert result.dxf_path.is_file()
    assert result.pdf_path is None
    assert result.check.passed


def test_output_directories_are_created(bracket, inputs, tmp_path):
    target = tmp_path / "nested" / "run" / "bracket.dxf"

    drawing.create_drawing(bracket, inputs, target, pdf_path=None)

    assert target.is_file()


# --- Helpers --------------------------------------------------------------


@pytest.mark.parametrize(
    "start, end, probe, expected",
    [
        (0.0, 90.0, 45.0, True),
        (0.0, 90.0, 180.0, False),
        (350.0, 10.0, 0.0, True),  # sweep across zero
        (350.0, 10.0, 180.0, False),
    ],
)
def test_arc_sweep_direction(start, end, probe, expected):
    """Picking the wrong sweep gives the 270-degree complement of the arc."""
    assert drawing._within_sweep(start, end, probe) is expected


def test_distinct_merges_near_duplicates():
    assert drawing._distinct([1.0, 1.0000001, 2.0]) == pytest.approx([1.0, 2.0])
