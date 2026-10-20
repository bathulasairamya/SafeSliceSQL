"""Shared test fixtures for SafeSliceSQL tests."""

from __future__ import annotations

import pytest

from safeslicesql.config import AppConfig, DatabaseConfig, load_config


@pytest.fixture(scope="session")
def app_config() -> AppConfig:
    """Load application configuration from .env for the test session."""
    try:
        return load_config()
    except Exception:
        pytest.skip("No valid .env configuration found; skipping tests that need config")


@pytest.fixture(scope="session")
def source_db_config(app_config: AppConfig) -> DatabaseConfig:
    """Source database configuration."""
    return app_config.source_db


@pytest.fixture(scope="session")
def target_db_config(app_config: AppConfig) -> DatabaseConfig:
    """Target database configuration."""
    return app_config.target_db
