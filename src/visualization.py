"""Off-screen rendering of meshes and results for the report.

Rendering happens with no window on screen, which on a laptop with switchable
graphics is where images silently come out black. Every render here is checked
for content before it is accepted: a blank PNG in a report is worse than no
PNG, because it looks like a result.

Units: mm.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pyvista as pv

# Minimum spread between the darkest and brightest pixel for an image to be
# considered non-blank, on a 0-255 scale.
MIN_PIXEL_SPREAD = 10.0

DEFAULT_WINDOW_SIZE = (1400, 800)
MESH_COLOUR = "#b0bec5"
EDGE_COLOUR = "#37474f"
BACKGROUND = "white"


@dataclass(frozen=True)
class RenderResult:
    """Outcome of one render, including whether the image has real content."""

    path: Path
    width: int
    height: int
    pixel_spread: float
    has_content: bool

    def __str__(self) -> str:
        status = "ok" if self.has_content else "BLANK"
        return f"{self.path.name} ({self.width}x{self.height}, {status})"


def _image_spread(path: Path) -> tuple[int, int, float]:
    """Size and dark-to-bright spread of a saved image."""
    image = pv.read(str(path))
    dimensions = image.dimensions
    array = np.asarray(image.active_scalars, dtype=float)
    spread = float(array.max() - array.min()) if array.size else 0.0

    return int(dimensions[0]), int(dimensions[1]), spread


def _verify_render(path: Path) -> RenderResult:
    """Confirm a rendered file exists and is not a flat, empty frame."""
    if not path.is_file():
        raise RuntimeError(f"Rendering reported success but {path} does not exist.")
    if path.stat().st_size == 0:
        raise RuntimeError(f"Rendering produced an empty file at {path}.")

    width, height, spread = _image_spread(path)

    return RenderResult(
        path=path,
        width=width,
        height=height,
        pixel_spread=spread,
        has_content=spread >= MIN_PIXEL_SPREAD,
    )


def load_mesh(msh_path: str | Path) -> pv.UnstructuredGrid:
    """Read a Gmsh .msh file into a PyVista grid.

    Raises:
        FileNotFoundError: if the mesh is missing.
    """
    msh_path = Path(msh_path)
    if not msh_path.is_file():
        raise FileNotFoundError(f"No mesh file at {msh_path}.")

    import meshio

    mesh = meshio.read(str(msh_path))
    return pv.wrap(mesh)


def render_mesh(
    msh_path: str | Path,
    output_path: str | Path,
    window_size: tuple[int, int] = DEFAULT_WINDOW_SIZE,
) -> RenderResult:
    """Render the mesh as two views and save a PNG.

    Two views because one is not enough to judge a mesh: the isometric shows
    overall element size, the front view shows how the holes are resolved.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    grid = load_mesh(msh_path)
    # The algorithm is named explicitly because PyVista's default is changing.
    surface = grid.extract_surface(algorithm="dataset_surface")

    pv.OFF_SCREEN = True
    plotter = pv.Plotter(off_screen=True, window_size=window_size, shape=(1, 2))

    plotter.subplot(0, 0)
    plotter.add_mesh(
        surface, color=MESH_COLOUR, show_edges=True, edge_color=EDGE_COLOUR,
        line_width=1,
    )
    plotter.add_text("Mesh, isometric", font_size=11)
    plotter.view_isometric()
    plotter.add_axes()

    plotter.subplot(0, 1)
    plotter.add_mesh(
        surface, color=MESH_COLOUR, show_edges=True, edge_color=EDGE_COLOUR,
        line_width=1,
    )
    plotter.add_text("Mesh, plate front face", font_size=11)
    plotter.view_vector((1, 0, 0), viewup=(0, 0, 1))
    plotter.add_axes()

    plotter.set_background(BACKGROUND)
    plotter.screenshot(str(output_path))
    plotter.close()

    return _verify_render(output_path)


# --- Result fringe plots --------------------------------------------------

# Colour map for result fringes. "turbo" is perceptually ordered and stays
# readable in greyscale, unlike the classic rainbow.
FRINGE_COLOUR_MAP = "turbo"

# Deformation is scaled so the largest displacement is this fraction of the
# model's diagonal. Real deflections here are under a millimetre on an 84 mm
# part, so an unscaled plot looks undeformed and tells the reader nothing.
DEFORMATION_FRACTION = 0.06

VON_MISES_LABEL = "von Mises (MPa)"
DISPLACEMENT_LABEL = "Displacement magnitude (mm)"


def build_result_grid(mesh, results) -> pv.UnstructuredGrid:
    """Combine the mesh and the nodal results into one PyVista grid.

    The tetrahedra are written as VTK quadratic tetrahedra so the mid-side
    nodes are kept; rendering them as linear cells would throw away exactly the
    curvature the second-order elements were chosen for.
    """
    import numpy as np

    cell_count = mesh.tets.shape[0]
    nodes_per_cell = mesh.tets.shape[1]

    cells = np.hstack(
        [np.full((cell_count, 1), nodes_per_cell, dtype=np.int64), mesh.tets]
    ).ravel()
    cell_types = np.full(cell_count, pv.CellType.QUADRATIC_TETRA, dtype=np.uint8)

    grid = pv.UnstructuredGrid(cells, cell_types, mesh.points)

    grid.point_data[VON_MISES_LABEL] = results.von_mises
    grid.point_data[DISPLACEMENT_LABEL] = results.displacement_magnitude
    grid.point_data["displacement"] = results.displacements

    return grid


def write_result_mesh(grid: pv.UnstructuredGrid, output_path: str | Path) -> Path:
    """Save the results as a .vtu for opening in ParaView.

    The images below are a summary; this is the evidence someone can inspect
    themselves, which matters for a result nobody should take on trust.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    grid.save(str(output_path))

    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise RuntimeError(f"Result mesh was not written to {output_path}.")

    return output_path


def _deformation_scale(grid: pv.UnstructuredGrid) -> float:
    """Exaggeration factor that makes the deflected shape visible."""
    import numpy as np

    peak = float(np.abs(grid.point_data["displacement"]).max())
    if peak <= 0.0:
        return 0.0

    diagonal = float(np.linalg.norm(np.asarray(grid.bounds[1::2]) - np.asarray(grid.bounds[::2])))

    return DEFORMATION_FRACTION * diagonal / peak


def render_field(
    grid: pv.UnstructuredGrid,
    field: str,
    output_path: str | Path,
    title: str,
    deformed: bool = True,
    window_size: tuple[int, int] = DEFAULT_WINDOW_SIZE,
) -> RenderResult:
    """Render a fringe plot of one nodal field, two views, and save a PNG.

    Args:
        grid: from build_result_grid.
        field: point data array to colour by.
        output_path: destination PNG.
        title: shown on the image, including the deformation scale so nobody
            mistakes an exaggerated picture for the real deflected shape.
        deformed: warp the shape by the displacement vector.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if field not in grid.point_data:
        available = ", ".join(grid.point_data.keys())
        raise ValueError(f"No field '{field}' in the results. Available: {available}.")

    scale = _deformation_scale(grid) if deformed else 0.0
    shown = grid.warp_by_vector("displacement", factor=scale) if scale else grid
    surface = shown.extract_surface(algorithm="dataset_surface")

    caption = title
    if scale:
        caption = f"{title}  (deformation x{scale:,.0f})"

    scalar_bar = {
        "title": field,
        "n_labels": 5,
        "fmt": "%.3g",
        "title_font_size": 13,
        "label_font_size": 11,
        "color": "black",
    }

    pv.OFF_SCREEN = True
    plotter = pv.Plotter(off_screen=True, window_size=window_size, shape=(1, 2))

    plotter.subplot(0, 0)
    plotter.add_mesh(
        surface, scalars=field, cmap=FRINGE_COLOUR_MAP, scalar_bar_args=scalar_bar
    )
    plotter.add_text(caption, font_size=11, color="black")
    plotter.view_isometric()
    plotter.add_axes()

    plotter.subplot(0, 1)
    plotter.add_mesh(
        surface, scalars=field, cmap=FRINGE_COLOUR_MAP, scalar_bar_args=scalar_bar
    )
    plotter.add_text("Side view (x-z)", font_size=11, color="black")
    plotter.view_vector((0, -1, 0), viewup=(0, 0, 1))
    plotter.add_axes()

    plotter.set_background(BACKGROUND)
    plotter.screenshot(str(output_path))
    plotter.close()

    return _verify_render(output_path)


def render_stress(
    grid: pv.UnstructuredGrid, output_path: str | Path, **kwargs
) -> RenderResult:
    """Von Mises fringe plot on the deflected shape."""
    return render_field(
        grid, VON_MISES_LABEL, output_path, "Von Mises stress", **kwargs
    )


def render_displacement(
    grid: pv.UnstructuredGrid, output_path: str | Path, **kwargs
) -> RenderResult:
    """Displacement magnitude fringe plot on the deflected shape."""
    return render_field(
        grid, DISPLACEMENT_LABEL, output_path, "Displacement magnitude", **kwargs
    )
