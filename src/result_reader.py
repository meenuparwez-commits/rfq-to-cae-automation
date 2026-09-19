"""Read CalculiX results: displacements and stresses from .frd, reactions from .dat.

Units: mm, N, MPa.

The .frd is a fixed-column format, and that matters more than it sounds. A real
data line looks like:

    -1         1 4.00000E+00-3.00000E+01 9.00000E+00

There is no separator between the positive and the negative value: splitting on
whitespace would merge two numbers into one and silently corrupt every result
downstream. Fields are therefore read by position - 3 characters of marker, 10
of node number, then 12 per value.

Blocks are introduced by a `-4` header naming the quantity, followed by `-5`
lines naming each component and then `-1` data rows until `-3`. One subtlety:
the DISP header declares four entities but one of them is the pseudo-component
ALL, so counting the declared number would read a column too many. The
components are counted from the `-5` lines instead, skipping ALL.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

# Fixed-column layout of an .frd data line.
_MARKER_WIDTH = 3
_NODE_ID_WIDTH = 10
_VALUE_WIDTH = 12
_VALUES_START = _MARKER_WIDTH + _NODE_ID_WIDTH

# Component order CalculiX writes for the STRESS block.
STRESS_COMPONENTS = ("SXX", "SYY", "SZZ", "SXY", "SYZ", "SZX")


@dataclass(frozen=True)
class Results:
    """Nodal results, ordered by node index (CalculiX id minus one)."""

    displacements: np.ndarray = field(repr=False)
    stresses: np.ndarray = field(repr=False)

    @property
    def num_nodes(self) -> int:
        return int(self.displacements.shape[0])

    @property
    def displacement_magnitude(self) -> np.ndarray:
        return np.linalg.norm(self.displacements, axis=1)

    @property
    def max_displacement(self) -> float:
        return float(self.displacement_magnitude.max())

    @property
    def von_mises(self) -> np.ndarray:
        """Von Mises equivalent stress at every node, MPa.

        sqrt( 0.5[(sxx-syy)^2 + (syy-szz)^2 + (szz-sxx)^2] + 3[sxy^2+syz^2+szx^2] )
        """
        sxx, syy, szz, sxy, syz, szx = self.stresses.T

        return np.sqrt(
            0.5 * ((sxx - syy) ** 2 + (syy - szz) ** 2 + (szz - sxx) ** 2)
            + 3.0 * (sxy**2 + syz**2 + szx**2)
        )

    @property
    def max_von_mises(self) -> float:
        return float(self.von_mises.max())


def read_frd(path: str | Path, expected_nodes: int | None = None) -> Results:
    """Parse displacements and stresses from a CalculiX .frd file.

    Raises:
        FileNotFoundError: if the file is missing.
        ValueError: if either block is absent, or the node count is unexpected.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"No results file at {path}.")

    blocks = _read_blocks(path, wanted={"DISP", "STRESS"})

    for name in ("DISP", "STRESS"):
        if name not in blocks:
            raise ValueError(
                f"{path} contains no {name} block. The deck must request it "
                "with *NODE FILE / *EL FILE, and the solve must have completed."
            )

    displacements = _ordered(blocks["DISP"], path, "DISP")
    stresses = _ordered(blocks["STRESS"], path, "STRESS")

    if displacements.shape[1] != 3:
        raise ValueError(
            f"Expected 3 displacement components, found {displacements.shape[1]}."
        )
    if stresses.shape[1] != 6:
        raise ValueError(
            f"Expected 6 stress components, found {stresses.shape[1]}."
        )
    if displacements.shape[0] != stresses.shape[0]:
        raise ValueError(
            f"{path} has {displacements.shape[0]} displacement rows but "
            f"{stresses.shape[0]} stress rows."
        )
    if expected_nodes is not None and displacements.shape[0] != expected_nodes:
        raise ValueError(
            f"{path} holds results for {displacements.shape[0]} nodes, but the "
            f"mesh has {expected_nodes}. The results do not match the model."
        )

    return Results(displacements=displacements, stresses=stresses)


def _read_blocks(
    path: Path, wanted: set[str]
) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Collect the requested result blocks as (node ids, values)."""
    blocks: dict[str, tuple[np.ndarray, np.ndarray]] = {}

    name: str | None = None
    components = 0
    ids: list[int] = []
    rows: list[list[float]] = []

    with path.open("r", encoding="ascii", errors="replace") as handle:
        for line in handle:
            if line.startswith(" -4"):
                name = line[5:13].strip()
                components = 0
                ids = []
                rows = []
            elif name is not None and line.startswith(" -5"):
                # ALL is a summary pseudo-component, not a data column.
                if line[5:13].strip().upper() != "ALL":
                    components += 1
            elif name is not None and components and line.startswith(" -1"):
                ids.append(int(line[_MARKER_WIDTH:_VALUES_START]))
                rows.append(
                    [
                        float(
                            line[
                                _VALUES_START + index * _VALUE_WIDTH :
                                _VALUES_START + (index + 1) * _VALUE_WIDTH
                            ]
                        )
                        for index in range(components)
                    ]
                )
            elif name is not None and line.startswith(" -3"):
                if name in wanted and rows:
                    blocks[name] = (
                        np.asarray(ids, dtype=np.int64),
                        np.asarray(rows, dtype=float),
                    )
                name = None

    return blocks


def _ordered(
    block: tuple[np.ndarray, np.ndarray], path: Path, name: str
) -> np.ndarray:
    """Reorder a block's rows into node-index order (CalculiX id minus one).

    The deck writes node ids 1..N in mesh order, so the ids should be exactly
    that set. This is checked rather than assumed: a gap would silently shift
    every result by one node.
    """
    ids, values = block
    count = ids.size

    expected = np.arange(1, count + 1, dtype=np.int64)
    if not np.array_equal(np.sort(ids), expected):
        raise ValueError(
            f"{name} block in {path} does not hold a contiguous set of node "
            f"ids 1..{count}. Results cannot be matched to the mesh."
        )

    ordered = np.empty_like(values)
    ordered[ids - 1] = values

    return ordered


def read_reaction_total(path: str | Path) -> np.ndarray:
    """Total reaction force on the fixed set, from the .dat file.

    The deck asks for TOTALS=ONLY, so the file holds one summary line rather
    than a row per node. Returns [fx, fy, fz] in N.

    Raises:
        FileNotFoundError: if the file is missing.
        ValueError: if no total force record is present.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"No .dat results file at {path}.")

    lines = path.read_text(encoding="ascii", errors="replace").splitlines()

    for index, line in enumerate(lines):
        if "total force" not in line.lower():
            continue

        for candidate in lines[index + 1 :]:
            parts = candidate.split()
            if len(parts) == 3:
                try:
                    return np.asarray([float(part) for part in parts])
                except ValueError:
                    continue

    raise ValueError(
        f"{path} contains no 'total force' record. The deck must request "
        "*NODE PRINT with RF for the fixed set."
    )


# --- Extracting specific quantities --------------------------------------


def tip_deflection(
    results: Results, points: np.ndarray, tip_x: float, tol: float = 1e-6
) -> float:
    """Mean downward deflection over the tip face, in mm (positive downwards).

    Averaged across the end face rather than taken as the single worst node.
    Beam theory gives the deflection of the neutral axis, and averaging over
    the face cancels the small rotation of the section, making the comparison
    like for like.
    """
    on_tip = np.abs(points[:, 0] - tip_x) <= tol
    if not on_tip.any():
        raise ValueError(f"No nodes found at the tip plane x = {tip_x}.")

    return float(-results.displacements[on_tip, 2].mean())


@dataclass(frozen=True)
class SectionStress:
    """Axial bending stress sampled on a cross-section of the arm."""

    position: float
    num_top_nodes: int
    num_bottom_nodes: int
    top_mean: float
    bottom_mean: float

    @property
    def magnitude(self) -> float:
        """Mean of the two surface magnitudes.

        Beam theory predicts equal and opposite values at the two surfaces, so
        averaging their magnitudes is the fairest single number to compare and
        cancels any small membrane component.
        """
        return (abs(self.top_mean) + abs(self.bottom_mean)) / 2.0


def section_stress(
    results: Results,
    points: np.ndarray,
    position_x: float,
    thickness: float,
    slab_half_width: float,
    surface_tol: float = 1e-6,
) -> SectionStress:
    """Mean axial stress on the top and bottom surfaces of the arm at a section.

    Sampled in a thin slab of elements around `position_x` because an
    unstructured tetrahedral mesh has no nodes lying exactly on a chosen plane.

    Averaged across the width because the section is wide (b/t = 15 here), so
    stress varies across it; beam theory predicts the average, not the edge.
    """
    in_slab = np.abs(points[:, 0] - position_x) <= slab_half_width
    on_top = in_slab & (np.abs(points[:, 2] - thickness) <= surface_tol)
    on_bottom = in_slab & (np.abs(points[:, 2]) <= surface_tol)

    if not on_top.any() or not on_bottom.any():
        raise ValueError(
            f"No surface nodes found at x = {position_x} +/- {slab_half_width} "
            f"mm (top: {int(on_top.sum())}, bottom: {int(on_bottom.sum())}). "
            "Widen the slab or check the section position."
        )

    sxx = results.stresses[:, 0]

    return SectionStress(
        position=position_x,
        num_top_nodes=int(on_top.sum()),
        num_bottom_nodes=int(on_bottom.sum()),
        top_mean=float(sxx[on_top].mean()),
        bottom_mean=float(sxx[on_bottom].mean()),
    )
