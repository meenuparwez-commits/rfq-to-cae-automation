"""Write a CalculiX linear-static input deck.

Units: mm, N, MPa. Consistent throughout, and stated in the deck header so
anyone opening the file can see which system it is in.

Element type is C3D10, the second-order tetrahedron. Node ordering is taken
straight from the mesh: meshio's `tetra10` order matches CalculiX's C3D10
ordering, which is checked by a test rather than assumed.

The load is applied as equivalent nodal forces rather than as a pressure,
because a pressure in CalculiX acts along the face normal and both load cases
here act downwards in -z. On the tip face the normal is along x, so a pressure
would pull the arm sideways instead of loading it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from src.boundary_detection import FaceSelection
from src.mesh_generator import MeshData
from src.schemas import BracketInputs, Material

# CalculiX reads free-format lines but is happier with short ones; these keep
# the deck within conventional limits and readable in a text editor.
NODES_PER_SET_LINE = 8
ELEMENT_FIRST_LINE_NODES = 8

ELEMENT_TYPE = "C3D10"
ELSET_NAME = "EALL"
NSET_ALL = "NALL"
NSET_FIXED = "NFIXED"
MATERIAL_NAME = "BRACKET_MATERIAL"


@dataclass(frozen=True)
class DeckSummary:
    """What was written, for logging and for the report."""

    path: Path
    num_nodes: int
    num_elements: int
    num_fixed_nodes: int
    num_loaded_nodes: int
    total_applied_load: float

    def __str__(self) -> str:
        return (
            f"{self.path.name}: {self.num_nodes} nodes, {self.num_elements} "
            f"{ELEMENT_TYPE}, {self.num_fixed_nodes} fixed nodes, "
            f"{self.num_loaded_nodes} loaded nodes, "
            f"{self.total_applied_load:.3f} N applied"
        )


def write_input_deck(
    path: str | Path,
    mesh: MeshData,
    inputs: BracketInputs,
    material: Material,
    fixed_face: FaceSelection,
    nodal_forces: dict[int, np.ndarray],
) -> DeckSummary:
    """Write the .inp file and return a summary of what went into it.

    Raises:
        ValueError: if the model would be unsolvable - no restraint, or no load.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if fixed_face.num_nodes == 0:
        raise ValueError(
            "No fixed nodes were found. Without restraint the model has rigid "
            "body motion and the solve is singular."
        )
    if not nodal_forces:
        raise ValueError("No loaded nodes were found; the model carries no load.")

    lines: list[str] = []
    lines.extend(_header(inputs, material))
    lines.extend(_nodes(mesh))
    lines.extend(_elements(mesh))
    lines.extend(_node_set(NSET_FIXED, fixed_face.node_indices))
    lines.extend(_material(material))
    lines.extend(_step(nodal_forces))

    path.write_text("\n".join(lines) + "\n", encoding="ascii")

    total = float(np.linalg.norm(np.sum(np.stack(list(nodal_forces.values())), axis=0)))

    return DeckSummary(
        path=path,
        num_nodes=mesh.num_nodes,
        num_elements=mesh.num_elements,
        num_fixed_nodes=fixed_face.num_nodes,
        num_loaded_nodes=len(nodal_forces),
        total_applied_load=total,
    )


def _header(inputs: BracketInputs, material: Material) -> list[str]:
    return [
        "** Bracket linear-static analysis, generated automatically.",
        "** Educational proof of concept: results require independent",
        "** engineering verification before any use.",
        "**",
        "** Units: mm, N, MPa (N/mm^2), tonne/mm^3.",
        f"** Material    : {material.name}, E = {material.youngs_modulus} MPa, "
        f"nu = {material.poissons_ratio}",
        f"** Load case   : {inputs.load_case.value}, "
        f"{inputs.applied_load} N total, acting in -z",
        f"** Geometry    : H {inputs.plate_height}, L {inputs.arm_length}, "
        f"b {inputs.width}, t {inputs.thickness}, r {inputs.fillet_radius} mm",
        f"** Restraint   : washer annuli at x = 0, {inputs.num_holes} rings of "
        f"OD {inputs.washer_diameter} mm around the holes, fully fixed.",
        "**               The rest of the rear face is free, so the holes do",
        "**               carry the load - at the cost of a stress singularity",
        "**               at the edge of each clamped ring.",
        "**",
    ]


def _nodes(mesh: MeshData) -> list[str]:
    """Node block. CalculiX numbers from 1, so every index is offset by one."""
    lines = [f"*NODE, NSET={NSET_ALL}"]
    lines.extend(
        f"{index + 1}, {x:.9g}, {y:.9g}, {z:.9g}"
        for index, (x, y, z) in enumerate(mesh.points)
    )
    return lines


def _elements(mesh: MeshData) -> list[str]:
    """Element block, split across two lines per element.

    A C3D10 needs an id plus ten node numbers. Splitting after eight keeps
    lines short; CalculiX continues an element when the line ends in a comma.
    """
    lines = [f"*ELEMENT, TYPE={ELEMENT_TYPE}, ELSET={ELSET_NAME}"]

    for index, connectivity in enumerate(mesh.tets):
        numbers = [int(node) + 1 for node in connectivity]
        first = numbers[:ELEMENT_FIRST_LINE_NODES]
        rest = numbers[ELEMENT_FIRST_LINE_NODES:]

        lines.append(f"{index + 1}, " + ", ".join(str(n) for n in first) + ",")
        lines.append(", ".join(str(n) for n in rest))

    return lines


def _node_set(name: str, node_indices: np.ndarray) -> list[str]:
    lines = [f"*NSET, NSET={name}"]
    numbers = [int(index) + 1 for index in np.sort(node_indices)]

    for start in range(0, len(numbers), NODES_PER_SET_LINE):
        chunk = numbers[start : start + NODES_PER_SET_LINE]
        lines.append(", ".join(str(number) for number in chunk))

    return lines


def _material(material: Material) -> list[str]:
    return [
        f"*MATERIAL, NAME={MATERIAL_NAME}",
        "*ELASTIC",
        f"{material.youngs_modulus:.9g}, {material.poissons_ratio:.9g}",
        f"*SOLID SECTION, ELSET={ELSET_NAME}, MATERIAL={MATERIAL_NAME}",
    ]


def _step(nodal_forces: dict[int, np.ndarray]) -> list[str]:
    lines = [
        "*STEP",
        "*STATIC",
        "** Engineering decision 4: the plate is fixed in all three directions",
        "** only where its bolts clamp it, under the washers.",
        "*BOUNDARY",
        f"{NSET_FIXED}, 1, 3, 0.0",
        "*CLOAD",
    ]

    for node in sorted(nodal_forces):
        force = nodal_forces[node]
        for dof, component in enumerate(force, start=1):
            if component != 0.0:
                lines.append(f"{node + 1}, {dof}, {component:.9g}")

    lines.extend(
        [
            "** Reaction totals only: the full list would be tens of thousands",
            "** of lines, and the equilibrium check needs only the sum.",
            f"*NODE PRINT, NSET={NSET_FIXED}, TOTALS=ONLY",
            "RF",
            "*NODE FILE",
            "U",
            "*EL FILE",
            "S",
            "*END STEP",
        ]
    )

    return lines
