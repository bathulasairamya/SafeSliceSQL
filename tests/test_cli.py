"""
Tests for safeslicesql.cli — Phase 7.
"""

from __future__ import annotations

import re

from typer.testing import CliRunner

from safeslicesql.cli import app

runner = CliRunner()


def _clean(text: str) -> str:
    """Strip ANSI escape sequences from CLI output."""
    return re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", text)


def test_cli_help():
    """Verify safeslice root help."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    out = _clean(result.output)
    assert "SafeSliceSQL" in out
    assert "inspect" in out
    assert "run" in out
    assert "validate" in out
    assert "benchmark" in out


def test_cli_run_invalid_method():
    """Verify rejection of unknown selection method."""
    result = runner.invoke(app, ["run", "--method", "nonexistent-method", "--budget-percent", "20"])
    assert result.exit_code == 2
    assert "Unknown method" in _clean(result.output)


def test_cli_run_invalid_budget():
    """Verify rejection of out-of-range budget-percent."""
    result = runner.invoke(app, ["run", "--method", "referential", "--budget-percent", "150"])
    assert result.exit_code == 2
    assert "budget-percent must be between 1 and 100" in _clean(result.output)

    result_zero = runner.invoke(app, ["run", "--method", "referential", "--budget-percent", "0"])
    assert result_zero.exit_code == 2
    assert "budget-percent must be between 1 and 100" in _clean(result_zero.output)


def test_cli_run_help_includes_dry_run():
    """Verify safeslice run help includes dry-run option."""
    result = runner.invoke(app, ["run", "--help"])
    assert result.exit_code == 0
    assert "--dry-run" in _clean(result.output)


def test_cli_benchmark_missing_config():
    """Verify benchmark command handles missing config cleanly."""
    result = runner.invoke(app, ["benchmark", "--config", "nonexistent_config_12345.yaml"])
    assert result.exit_code == 1
    out = _clean(result.output).lower()
    assert "benchmark failed" in out or "not found" in out

