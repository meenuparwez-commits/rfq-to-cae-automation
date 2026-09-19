"""Run CalculiX and decide, carefully, whether it actually succeeded.

The solver is located through the CCX_PATH environment variable and never
hard-coded: the path contains a FreeCAD version number that changes on upgrade.

Success is established from the solver's own output and from the result files,
never from the process return code. `ccx -v` prints its version and exits with
code 201, so a return-code test would call a healthy solver a failure. The
reverse is also possible: CalculiX can print *ERROR and stop while still
exiting cleanly.

A run is accepted only when all of the following hold:
  - the output contains "Job finished"
  - the output contains no "*ERROR" line
  - the .frd and .dat files exist and are non-empty
  - the .frd ends with CalculiX's 9999 end-of-file marker, so it is complete
    rather than truncated by a crash or a full disk
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from src.geometry_checks import CheckResult

# Marker CalculiX writes as the last record of a complete .frd file.
FRD_END_MARKER = "9999"

# Bytes from the end of the .frd to inspect for the marker. Reading the whole
# file would mean loading tens of megabytes to look at one line.
FRD_TAIL_BYTES = 256

DEFAULT_TIMEOUT_SECONDS = 3600


@dataclass(frozen=True)
class SolverRun:
    """Everything known about one CalculiX run."""

    inp_path: Path
    frd_path: Path
    dat_path: Path
    returncode: int
    elapsed_seconds: float
    stdout: str = field(repr=False, default="")
    stderr: str = field(repr=False, default="")
    num_equations: int | None = None
    job_finished: bool = False
    errors: tuple[str, ...] = ()

    @property
    def frd_complete(self) -> bool:
        return _ends_with_marker(self.frd_path)

    @property
    def succeeded(self) -> bool:
        return (
            self.job_finished
            and not self.errors
            and _is_non_empty(self.frd_path)
            and _is_non_empty(self.dat_path)
            and self.frd_complete
        )

    def __str__(self) -> str:
        status = "ok" if self.succeeded else "FAILED"
        return (
            f"{self.inp_path.stem}: {status} in {self.elapsed_seconds:.1f} s, "
            f"{self.num_equations} equations"
        )


def _persisted_windows_value(name: str) -> str | None:
    """Read a user environment variable from where Windows actually stores it.

    A process is handed a copy of the environment when it starts and never sees
    later changes. A long-running desktop session, or a terminal opened before
    CCX_PATH was set, therefore has a stale copy even though the variable is
    set on the machine. Consulting the registry asks the authoritative source
    rather than failing over a stale snapshot.

    This still reads CCX_PATH and nothing else: the path is never hard-coded.
    """
    if os.name != "nt":
        return None

    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _ = winreg.QueryValueEx(key, name)
    except (ImportError, OSError):
        return None

    return str(value) if value else None


def resolve_solver() -> Path:
    """Locate ccx through CCX_PATH, failing with an actionable message."""
    raw = os.environ.get("CCX_PATH") or _persisted_windows_value("CCX_PATH")

    if not raw:
        raise RuntimeError(
            "CCX_PATH is not set in this process. It must point at the "
            "CalculiX executable (ccx.exe), for example the copy bundled with "
            r"FreeCAD at ...\FreeCAD 1.1\bin\ccx.exe."
            "\n\n"
            "If it is set but not visible here, this process was started "
            "before it was set: a process keeps the environment it was given "
            "at launch. Reopen the terminal, or set it for this session with:"
            '\n    $env:CCX_PATH = [System.Environment]::'
            'GetEnvironmentVariable("CCX_PATH", "User")'
        )

    path = Path(raw)
    if not path.is_file():
        raise RuntimeError(
            f"CCX_PATH points at '{raw}', but no file exists there. If FreeCAD "
            "was updated, the version number in the folder name has changed "
            "and CCX_PATH needs updating."
        )

    return path


def run_analysis(
    inp_path: str | Path,
    num_threads: int | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> SolverRun:
    """Solve an input deck and report what happened.

    Args:
        inp_path: the .inp file. CalculiX is given the job name without the
            extension and writes its results alongside it.
        num_threads: value for OMP_NUM_THREADS. None leaves the environment
            alone.
        timeout: seconds before the run is abandoned.

    Raises:
        FileNotFoundError: if the deck is missing.
        RuntimeError: if CCX_PATH is unusable or the solver times out.
    """
    inp_path = Path(inp_path).resolve()
    if not inp_path.is_file():
        raise FileNotFoundError(f"No CalculiX input deck at {inp_path}.")

    solver = resolve_solver()
    work_dir = inp_path.parent
    job_name = inp_path.stem

    environment = dict(os.environ)
    if num_threads is not None:
        environment["OMP_NUM_THREADS"] = str(num_threads)

    started = time.perf_counter()
    try:
        # Arguments are passed as a list. CCX_PATH contains a space
        # ("FreeCAD 1.1"); a joined command string would split the path there.
        completed = subprocess.run(
            [str(solver), "-i", job_name],
            cwd=work_dir,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=environment,
        )
    except subprocess.TimeoutExpired as exc:
        elapsed = time.perf_counter() - started
        raise RuntimeError(
            f"CalculiX did not finish within {timeout:.0f} s (ran for "
            f"{elapsed:.0f} s) on {inp_path.name}. The model may be too large "
            "for the available memory, or the mesh too fine."
        ) from exc

    elapsed = time.perf_counter() - started
    stdout = completed.stdout or ""

    return SolverRun(
        inp_path=inp_path,
        frd_path=work_dir / f"{job_name}.frd",
        dat_path=work_dir / f"{job_name}.dat",
        returncode=completed.returncode,
        elapsed_seconds=elapsed,
        stdout=stdout,
        stderr=completed.stderr or "",
        num_equations=_parse_equations(stdout),
        job_finished="Job finished" in stdout,
        errors=_parse_errors(stdout),
    )


def _parse_equations(stdout: str) -> int | None:
    """Number of equations CalculiX reported.

    Useful as an independent check: it should equal three times the number of
    unrestrained nodes.
    """
    match = re.search(r"number of equations\s*\n\s*(\d+)", stdout)
    return int(match.group(1)) if match else None


def _parse_errors(stdout: str) -> tuple[str, ...]:
    """Lines CalculiX flagged as errors."""
    return tuple(
        line.strip()
        for line in stdout.splitlines()
        if "*ERROR" in line.upper()
    )


def _is_non_empty(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0


def _ends_with_marker(path: Path) -> bool:
    """True if the .frd finishes with CalculiX's end-of-file record.

    A truncated .frd - from a crash, a killed process or a full disk - can
    still be large and still parse for a while before running out of data.
    """
    if not _is_non_empty(path):
        return False

    with path.open("rb") as handle:
        handle.seek(max(0, path.stat().st_size - FRD_TAIL_BYTES))
        tail = handle.read().decode("ascii", errors="replace")

    tokens = tail.split()

    return bool(tokens) and tokens[-1] == FRD_END_MARKER


# --- Checks ---------------------------------------------------------------


def check_solver_completed(run: SolverRun) -> CheckResult:
    """CalculiX reported a finished job and no errors."""
    if run.errors:
        return CheckResult(
            name="Solver completion",
            passed=False,
            message="CalculiX reported errors: " + " | ".join(run.errors[:3]),
        )

    return CheckResult(
        name="Solver completion",
        passed=run.job_finished,
        message=(
            f"Job finished in {run.elapsed_seconds:.1f} s "
            f"({run.num_equations} equations, return code {run.returncode})."
            if run.job_finished
            else "CalculiX did not report 'Job finished'. Return code "
            f"{run.returncode} is not evidence either way: ccx exits non-zero "
            "on success in some modes."
        ),
        value=float(run.elapsed_seconds),
    )


def check_result_files(run: SolverRun) -> CheckResult:
    """Both result files exist, are non-empty, and the .frd is complete."""
    problems = []

    if not _is_non_empty(run.frd_path):
        problems.append(f"{run.frd_path.name} is missing or empty")
    elif not run.frd_complete:
        problems.append(
            f"{run.frd_path.name} does not end with the {FRD_END_MARKER} "
            "marker, so it is truncated"
        )

    if not _is_non_empty(run.dat_path):
        problems.append(f"{run.dat_path.name} is missing or empty")

    passed = not problems
    frd_mb = run.frd_path.stat().st_size / 1e6 if run.frd_path.is_file() else 0.0

    return CheckResult(
        name="Result files",
        passed=passed,
        message=(
            f"{run.frd_path.name} ({frd_mb:.2f} MB) complete, "
            f"{run.dat_path.name} present."
            if passed
            else "; ".join(problems)
        ),
    )


def check_equation_count(run: SolverRun, expected: int) -> CheckResult:
    """Equations solved against three times the unrestrained node count.

    Catches a restraint that was applied to the wrong number of nodes, which
    would otherwise show up only as a strange stiffness.
    """
    if run.num_equations is None:
        return CheckResult(
            name="Equation count",
            passed=False,
            message="CalculiX did not report an equation count.",
            expected=float(expected),
        )

    passed = run.num_equations == expected

    return CheckResult(
        name="Equation count",
        passed=passed,
        message=(
            f"{run.num_equations} equations, matching 3 x unrestrained nodes."
            if passed
            else f"{run.num_equations} equations, expected {expected} "
            "(3 x unrestrained nodes). The restraint may cover the wrong nodes."
        ),
        value=float(run.num_equations),
        expected=float(expected),
    )


def run_solver_checks(run: SolverRun, expected_equations: int) -> list[CheckResult]:
    """Every solver check, in order."""
    return [
        check_solver_completed(run),
        check_result_files(run),
        check_equation_count(run, expected_equations),
    ]
