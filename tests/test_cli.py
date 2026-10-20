"""
Tests for safeslicesql.cli — Phase 7.
"""

from __future__ import annotations

from typer.testing import CliRunner

from safeslicesql.cli import app

runner = CliRunner()


def test_cli_help():
    """Verify safeslice root help."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "SafeSliceSQL" in result.output
    assert "inspect" in result.output
    assert "run" in result.output
    assert "validate" in result.output
    assert "benchmark" in result.output


def test_cli_run_invalid_method():
    """Verify rejection of unknown selection method."""
    result = runner.invoke(app, ["run", "--method", "nonexistent-method", "--budget-percent", "20"])
    assert result.exit_code == 2
    assert "Unknown method" in result.output


def test_cli_run_invalid_budget():
    """Verify rejection of out-of-range budget-percent."""
    result = runner.invoke(app, ["run", "--method", "referential", "--budget-percent", "150"])
    assert result.exit_code == 2
    assert "budget-percent must be between 1 and 100" in result.output

    result_zero = runner.invoke(app, ["run", "--method", "referential", "--budget-percent", "0"])
    assert result_zero.exit_code == 2
    assert "budget-percent must be between 1 and 100" in result_zero.output


def test_cli_benchmark_not_implemented():
    """Verify benchmark command handles invocation cleanly."""
    result = runner.invoke(app, ["benchmark", "--config", "benchmarks/benchmarks.yaml"])
    assert result.exit_code == 1
    assert "Phase 8" in result.output or "benchmark" in result.output.lower()
