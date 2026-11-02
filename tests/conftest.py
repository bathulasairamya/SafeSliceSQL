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


@pytest.fixture
def live_source_engine(app_config):
    """Read-only live checks follow the configured source, with owned connections."""
    from safeslicesql.db import create_engine, test_connection

    engine = create_engine(app_config.source_db)
    try:
        if not test_connection(engine):
            pytest.skip("Configured MySQL source is not accessible.")
        yield engine
    finally:
        engine.dispose()
