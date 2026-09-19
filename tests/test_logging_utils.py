"""Tests for src/logging_utils.py.

Every failure has to reach both run_log.txt and the interface.
These check that the two stay the same record rather than drifting apart.
"""

from __future__ import annotations

from src.geometry_checks import ADVISORY, CheckResult
from src.logging_utils import RunLogger


def test_messages_are_kept_in_memory():
    logger = RunLogger()
    logger.info("hello")

    assert len(logger.lines) == 1
    assert "hello" in logger.lines[0]
    assert "INFO" in logger.lines[0]


def test_file_and_memory_hold_the_same_lines(tmp_path):
    """One record, two destinations. Two mechanisms would drift apart."""
    path = tmp_path / "run_log.txt"

    with RunLogger(path=path) as logger:
        logger.info("a message")
        logger.warning("a warning")
        logger.error("a failure")

    written = path.read_text(encoding="utf-8").splitlines()

    assert written == logger.lines


def test_the_log_is_written_as_it_goes_not_at_the_end(tmp_path):
    """If the solver kills the process, the log must still hold the evidence."""
    path = tmp_path / "run_log.txt"
    logger = RunLogger(path=path)

    logger.info("written before any close")

    assert "written before any close" in path.read_text(encoding="utf-8")

    logger.close()


def test_header_records_the_disclaimer_and_units(tmp_path):
    """A log a reviewer reads on its own must say what units it is in."""
    path = tmp_path / "run_log.txt"

    with RunLogger(path=path) as logger:
        pass

    text = "\n".join(logger.lines)

    assert "independent engineering verification" in text
    assert "mm, N, MPa" in text


def test_errors_and_warnings_can_be_filtered():
    logger = RunLogger()
    logger.info("fine")
    logger.warning("hmm")
    logger.error("bad")

    assert len(logger.errors) == 1
    assert len(logger.warnings) == 1


def test_a_passing_check_is_logged_as_info():
    logger = RunLogger()
    logger.check(CheckResult(name="Volume", passed=True, message="ok"))

    assert "INFO" in logger.lines[-1]


def test_a_failed_critical_check_is_logged_as_an_error():
    logger = RunLogger()
    logger.check(CheckResult(name="Equilibrium", passed=False, message="off"))

    assert "ERROR" in logger.lines[-1]


def test_a_failed_advisory_check_is_logged_as_a_warning():
    """Severity has to survive into the log, or the log misreports the run."""
    logger = RunLogger()
    logger.check(
        CheckResult(
            name="Mesh", passed=False, message="coarse", severity=ADVISORY
        )
    )

    assert "WARNING" in logger.lines[-1]
    assert "ERROR" not in logger.lines[-1]


def test_missing_directories_are_created(tmp_path):
    path = tmp_path / "nested" / "run_001" / "run_log.txt"

    with RunLogger(path=path):
        pass

    assert path.is_file()


def test_logger_without_a_path_writes_no_file(tmp_path):
    logger = RunLogger()
    logger.info("in memory only")
    logger.close()

    assert not list(tmp_path.iterdir())
