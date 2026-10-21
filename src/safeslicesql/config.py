"""
safeslicesql.config — Configuration loading and validation.

Loads database credentials and application settings from a `.env` file.
Separates source (read-only) and target (write) database configurations
and validates that required fields are present.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy.engine import URL

logger = logging.getLogger("safeslicesql.config")
PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class DatabaseConfig:
    """Connection parameters for a single MySQL database."""

    host: str
    port: int
    user: str
    password: str
    database: str

    @property
    def url(self) -> URL:
        """Return a SQLAlchemy URL without rendering or masking credentials."""
        return URL.create(
            drivername="mysql+pymysql",
            username=self.user,
            password=self.password if self.password else None,
            host=self.host,
            port=self.port,
            database=self.database,
        )

    def __repr__(self) -> str:
        """Redact password in string representation."""
        return (
            f"DatabaseConfig(host={self.host!r}, port={self.port}, "
            f"user={self.user!r}, password='***', "
            f"database={self.database!r})"
        )


@dataclass
class AppConfig:
    """Top-level application configuration."""

    source_db: DatabaseConfig
    target_db: DatabaseConfig
    log_level: str = "INFO"
    log_dir: str = "logs"

    def validate(self) -> list[str]:
        """
        Validate the configuration and return a list of error messages.
        Returns an empty list if the configuration is valid.
        """
        errors: list[str] = []

        # Source database must be specified
        if not self.source_db.database:
            errors.append("Source database name is required.")
        if not self.source_db.host:
            errors.append("Source database host is required.")
        if not self.source_db.user:
            errors.append("Source database user is required.")
        if not 1 <= self.source_db.port <= 65535:
            errors.append("Source database port must be between 1 and 65535.")

        # Target database must be specified
        if not self.target_db.database:
            errors.append("Target database name is required.")
        if not self.target_db.host:
            errors.append("Target database host is required.")
        if not self.target_db.user:
            errors.append("Target database user is required.")
        if not 1 <= self.target_db.port <= 65535:
            errors.append("Target database port must be between 1 and 65535.")

        # Safety: source and target must differ if on the same host
        if (
            self.source_db.host == self.target_db.host
            and self.source_db.port == self.target_db.port
            and self.source_db.database == self.target_db.database
        ):
            errors.append(
                "Source and target database names must differ when on the "
                "same host. This prevents accidental modification of the "
                f"source database '{self.source_db.database}'."
            )

        return errors


def _get_env(key: str, default: str = "") -> str:
    """Retrieve an environment variable, returning *default* if unset."""
    return os.environ.get(key, default)


def _get_env_int(key: str, default: int) -> int:
    """Retrieve an environment variable as an integer."""
    raw = os.environ.get(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"Environment variable {key} must be an integer.") from None


def load_config(env_path: str | Path | None = None) -> AppConfig:
    """
    Load application configuration from a `.env` file.

    Parameters
    ----------
    env_path : str, Path, or None
        Optional path to an environment file. By default, `.env` is read
        from the project root, independent of the current working directory.

    Returns
    -------
    AppConfig
        Validated application configuration.

    Raises
    ------
    ValueError
        If the configuration is invalid (e.g. source == target on same
        host, missing required fields).
    """
    env_file = Path(env_path) if env_path is not None else PROJECT_ROOT / ".env"
    env_file = env_file.resolve()
    if env_file.exists():
        load_dotenv(dotenv_path=env_file, override=False)
        logger.info("Loaded environment from %s", env_file.resolve())
    else:
        logger.warning(
            "Environment file %s not found; using system environment variables only.",
            env_file,
        )

    source_db = DatabaseConfig(
        host=_get_env("SAFESLICE_SOURCE_HOST", "localhost"),
        port=_get_env_int("SAFESLICE_SOURCE_PORT", 3306),
        user=_get_env("SAFESLICE_SOURCE_USER"),
        password=_get_env("SAFESLICE_SOURCE_PASSWORD", ""),
        database=_get_env("SAFESLICE_SOURCE_DATABASE"),
    )

    target_db = DatabaseConfig(
        host=_get_env("SAFESLICE_TARGET_HOST", "localhost"),
        port=_get_env_int("SAFESLICE_TARGET_PORT", 3306),
        user=_get_env("SAFESLICE_TARGET_USER"),
        password=_get_env("SAFESLICE_TARGET_PASSWORD", ""),
        database=_get_env("SAFESLICE_TARGET_DATABASE"),
    )

    config = AppConfig(
        source_db=source_db,
        target_db=target_db,
        log_level=_get_env("SAFESLICE_LOG_LEVEL", "INFO"),
        log_dir=_get_env("SAFESLICE_LOG_DIR", "logs"),
    )

    errors = config.validate()
    if errors:
        msg = "Configuration validation failed:\n" + "\n".join(f"  - {e}" for e in errors)
        logger.error(msg)
        raise ValueError(msg)

    logger.info(
        "Configuration loaded: source=%s, target=%s", source_db.database, target_db.database
    )
    return config
