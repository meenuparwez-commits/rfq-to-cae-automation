"""Solver verification: CalculiX solves a known problem correctly.

This is a solver trust test, not a test of project code. It builds a single
C3D8 cube, pulls it in uniaxial tension, and compares the solver's answer with
a closed-form hand calculation.

Why a single element: uniform stretching produces constant strain, and a linear
brick reproduces constant strain exactly. The expected answer is therefore
exact, not approximate, so any real deviation is a defect rather than
discretisation error.

Units: mm, N, MPa (N/mm^2).

Hand calculation for a 10 mm cube, E = 210000 MPa, nu = 0.3, F = 1000 N:
    sigma_zz = F / A       = 1000 / 100          = 10 MPa
    u_z      = sigma L / E = 10 * 10 / 210000    = 4.761905e-4 mm
    u_x      = -nu sigma L / E                   = -1.428571e-4 mm
    sum of base reactions                        = -1000 N

Note: success is judged by parsing solver output and confirming result files
exist, never by the process return code. `ccx -v` prints its version and exits
with code 201, so a return-code check would misreport a healthy solver.

The .dat parsing here is deliberately local to this test; src/result_reader.py
is the real parser.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

E_MODULUS = 210000.0  # MPa
POISSON = 0.3
CUBE_SIDE = 10.0  # mm
TOTAL_LOAD = 1000.0  # N

EXPECTED_STRESS_ZZ = TOTAL_LOAD / (CUBE_SIDE * CUBE_SIDE)  # 10 MPa
EXPECTED_UZ = EXPECTED_STRESS_ZZ * CUBE_SIDE / E_MODULUS
EXPECTED_UX = -POISSON * EXPECTED_STRESS_ZZ * CUBE_SIDE / E_MODULUS

# Relative tolerance. CalculiX prints 7 significant figures, so agreement is
# limited by the printed precision rather than by the solution.
REL_TOL = 1e-6
# Absolute tolerance for quantities that should be zero. Values around 1e-15
# are floating-point noise; anything larger indicates a genuine problem.
ABS_ZERO_TOL = 1e-9

CUBE_INP = """\
** Smoke test: single C3D8 cube, uniaxial tension
** Units: mm, N, MPa
*NODE, NSET=NALL
1,  0.0,  0.0,  0.0
2, 10.0,  0.0,  0.0
3, 10.0, 10.0,  0.0
4,  0.0, 10.0,  0.0
5,  0.0,  0.0, 10.0
6, 10.0,  0.0, 10.0
7, 10.0, 10.0, 10.0
8,  0.0, 10.0, 10.0
*ELEMENT, TYPE=C3D8, ELSET=EALL
1, 1, 2, 3, 4, 5, 6, 7, 8
*NSET, NSET=NBOT
1, 2, 3, 4
*NSET, NSET=NTOP
5, 6, 7, 8
*MATERIAL, NAME=STEEL
*ELASTIC
210000.0, 0.3
*SOLID SECTION, ELSET=EALL, MATERIAL=STEEL
*STEP
*STATIC
** Minimal restraint: the base is held vertically, plus just enough to stop
** sliding and spinning. Clamping the whole base would suppress the lateral
** Poisson contraction and quietly change the physics while the stress result
** still looked correct.
*BOUNDARY
NBOT, 3, 3, 0.0
1, 1, 1, 0.0
1, 2, 2, 0.0
2, 2, 2, 0.0
*CLOAD
NTOP, 3, 250.0
*NODE PRINT, NSET=NALL
U, RF
*EL PRINT, ELSET=EALL
S
*NODE FILE
U
*EL FILE
S
*END STEP
"""


def _resolve_ccx() -> Path:
    """Locate the solver through CCX_PATH, failing clearly if it is unusable."""
    raw = os.environ.get("CCX_PATH")
    if not raw:
        pytest.fail(
            "CCX_PATH is not set in this process. It must point at the CalculiX "
            "executable (ccx.exe), for example the copy bundled with FreeCAD at "
            r"...\FreeCAD 1.1\bin\ccx.exe."
            "\n\n"
            "If you have already set it, this shell was most likely started "
            "before it was set: a process keeps the environment it was given at "
            "launch. Either reopen the terminal, or set it for this session "
            "with:\n"
            '    $env:CCX_PATH = [System.Environment]::GetEnvironmentVariable('
            '"CCX_PATH", "User")'
        )
    path = Path(raw)
    if not path.is_file():
        pytest.fail(
            f"CCX_PATH is set to '{raw}', but no file exists there. If FreeCAD "
            "was updated, the version number in the folder name will have "
            "changed and the variable needs updating."
        )
    return path


def _parse_dat(text: str) -> dict[str, list[list[float]]]:
    """Parse a CalculiX .dat file into displacement, force and stress rows.

    The .dat file is plain text despite its extension. Each block starts with a
    descriptive line ('displacements ...', 'forces ...', 'stresses ...')
    followed by rows of numbers.
    """
    sections: dict[str, list[list[float]]] = {}
    current: str | None = None

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        lowered = stripped.lower()
        if lowered.startswith("displacements"):
            current = "U"
            sections[current] = []
            continue
        if lowered.startswith("forces"):
            current = "F"
            sections[current] = []
            continue
        if lowered.startswith("stresses"):
            current = "S"
            sections[current] = []
            continue

        if current is None:
            continue

        try:
            sections[current].append([float(token) for token in stripped.split()])
        except ValueError:
            # A non-numeric line ends the current block (e.g. a new heading).
            current = None

    return sections


@pytest.fixture(scope="module")
def solver_output(tmp_path_factory) -> dict[str, list[list[float]]]:
    """Run the cube model once and return the parsed results."""
    ccx = _resolve_ccx()
    work_dir = tmp_path_factory.mktemp("ccx_smoke")
    (work_dir / "cube.inp").write_text(CUBE_INP, encoding="ascii")

    # Arguments are passed as a list. CCX_PATH contains a space
    # ('FreeCAD 1.1'), which a joined command string would split.
    completed = subprocess.run(
        [str(ccx), "-i", "cube"],
        cwd=work_dir,
        capture_output=True,
        text=True,
        timeout=300,
    )

    dat_file = work_dir / "cube.dat"
    frd_file = work_dir / "cube.frd"

    # Success is established by solver output and result files, never by the
    # return code.
    assert "Job finished" in completed.stdout, (
        "CalculiX did not report 'Job finished'.\n"
        f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    )
    assert dat_file.is_file(), "CalculiX produced no .dat results file."
    assert frd_file.is_file(), "CalculiX produced no .frd results file."
    assert dat_file.stat().st_size > 0, "The .dat results file is empty."

    results = _parse_dat(dat_file.read_text(encoding="utf-8", errors="replace"))
    for block in ("U", "F", "S"):
        assert block in results and results[block], (
            f"No '{block}' block found in cube.dat; the result format may have "
            "changed."
        )
    return results


def test_axial_stress_matches_hand_calculation(solver_output):
    """sigma_zz must equal F/A at every integration point."""
    stresses = solver_output["S"]
    assert len(stresses) == 8, "Expected 8 integration points in a C3D8 element."

    for row in stresses:
        s_zz = row[4]  # elem, ip, sxx, syy, szz, ...
        assert s_zz == pytest.approx(EXPECTED_STRESS_ZZ, rel=REL_TOL)


def test_transverse_stresses_are_zero(solver_output):
    """Uniaxial tension leaves all other stress components at zero."""
    for row in solver_output["S"]:
        s_xx, s_yy = row[2], row[3]
        s_xy, s_xz, s_yz = row[5], row[6], row[7]
        for value in (s_xx, s_yy, s_xy, s_xz, s_yz):
            assert value == pytest.approx(0.0, abs=ABS_ZERO_TOL)


def test_axial_displacement_matches_hand_calculation(solver_output):
    """Top face rises by sigma*L/E; the base stays put."""
    top_nodes = {5, 6, 7, 8}
    base_nodes = {1, 2, 3, 4}

    for row in solver_output["U"]:
        node, u_z = int(row[0]), row[3]
        if node in top_nodes:
            assert u_z == pytest.approx(EXPECTED_UZ, rel=REL_TOL)
        elif node in base_nodes:
            assert u_z == pytest.approx(0.0, abs=ABS_ZERO_TOL)


def test_poisson_contraction_is_not_suppressed(solver_output):
    """Nodes on the x = 10 face must contract inwards by -nu*sigma*L/E.

    This is the check that catches an over-constrained model. If the whole base
    were clamped, the axial stress would still look correct while this value
    would be wrong.
    """
    x_face_nodes = {2, 3, 6, 7}

    for row in solver_output["U"]:
        node, u_x = int(row[0]), row[1]
        if node in x_face_nodes:
            assert u_x == pytest.approx(EXPECTED_UX, rel=REL_TOL)


def test_reactions_balance_the_applied_load(solver_output):
    """Base reactions must sum to -F, and the whole model to zero."""
    base_nodes = {1, 2, 3, 4}

    base_reaction_z = sum(row[3] for row in solver_output["F"] if int(row[0]) in base_nodes)
    assert base_reaction_z == pytest.approx(-TOTAL_LOAD, rel=REL_TOL)

    total_z = sum(row[3] for row in solver_output["F"])
    assert total_z == pytest.approx(0.0, abs=TOTAL_LOAD * 1e-9)
