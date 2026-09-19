"""Run logging: one file on disk, one list in memory for the UI.

Every failure has to reach both run_log.txt and the interface. Two separate
mechanisms would drift apart, so this keeps one record and hands the same lines
to both.

Plain text with a timestamp and a level, on purpose: it is evidence a reviewer
can read without running anything, which matters more here than structured
logging would.
"""

from __future__ import annotations

import platform
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

LEVELS = ("INFO", "WARNING", "ERROR")


@dataclass
class RunLogger:
    """Collects log lines, optionally mirroring them to a file as they arrive.

    Writing as we go rather than at the end matters: if the solver crashes the
    process, the log still holds everything up to that point, which is exactly
    when it is needed.
    """

    path: Path | None = None
    lines: list[str] = field(default_factory=list)
    _handle: object = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.path is not None:
            self.path = Path(self.path)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._handle = self.path.open("w", encoding="utf-8")
            self._write_header()

    def _write_header(self) -> None:
        self.info("Bracket CAD-to-CAE automation - educational proof of concept.")
        self.info("Results require independent engineering verification.")
        self.info("Units: mm, N, MPa (N/mm^2), tonne/mm^3, mass in kg.")
        self.info(f"Python {sys.version.split()[0]} on {platform.platform()}")
        self.info(f"Interpreter: {sys.executable}")

    def _emit(self, level: str, message: str) -> str:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"{stamp} {level:<7} {message}"

        self.lines.append(line)
        if self._handle is not None:
            self._handle.write(line + "\n")
            self._handle.flush()

        return line

    def info(self, message: str) -> str:
        return self._emit("INFO", message)

    def warning(self, message: str) -> str:
        return self._emit("WARNING", message)

    def error(self, message: str) -> str:
        return self._emit("ERROR", message)

    def check(self, result) -> str:
        """Log a CheckResult at a level matching its outcome and severity."""
        if result.passed:
            return self.info(str(result))
        if result.is_advisory:
            return self.warning(str(result))
        return self.error(str(result))

    def section(self, title: str) -> str:
        return self.info(f"--- {title} ---")

    @property
    def errors(self) -> list[str]:
        return [line for line in self.lines if " ERROR " in line]

    @property
    def warnings(self) -> list[str]:
        return [line for line in self.lines if " WARNING " in line]

    def text(self) -> str:
        return "\n".join(self.lines) + "\n"

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    def __enter__(self) -> RunLogger:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
