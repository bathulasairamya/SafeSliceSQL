"""Credential references and public error messages without bound row values."""

import os
from pathlib import Path

from sqlalchemy.exc import DBAPIError

_secrets: set[str] = set()


def os_vault():
    import keyring

    backend = keyring.get_keyring()
    allowed = (
        "keyring.backends.Windows",
        "keyring.backends.macOS",
        "keyring.backends.OS_X",
        "keyring.backends.SecretService",
        "keyring.backends.kwallet",
        "keyring.backends.libsecret",
    )
    if not type(backend).__module__.startswith(allowed) or backend.priority <= 0:
        raise ValueError(
            "Select a supported OS credential backend; plaintext and chained backends are not accepted."
        )
    return backend


def remember_secret(value: str) -> str:
    if value:
        _secrets.add(value)
    return value


def redact(value: str) -> str:
    for secret in sorted(_secrets, key=len, reverse=True):
        value = value.replace(secret, "[REDACTED]")
    return value


def safe_error(error: BaseException) -> str:
    seen = set()
    while error.__cause__ is not None and id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, DBAPIError):
            break
        error = error.__cause__
    if isinstance(error, DBAPIError):
        arguments = getattr(error.orig, "args", ())
        code = arguments[0] if arguments else None
        return (
            f"Database operation failed (code {code if isinstance(code, int) else 'unavailable'})."
        )
    return redact(str(error))


def credential(role: str, host: str, port: int, user: str) -> str:
    prefix = f"SAFESLICE_{role.upper()}_PASSWORD"
    file = os.environ.get(prefix + "_FILE")
    service = os.environ.get(prefix + "_KEYRING_SERVICE")
    if file and service:
        raise ValueError(f"Choose either {prefix}_FILE or {prefix}_KEYRING_SERVICE.")
    if service:
        try:
            value = os_vault().get_password(service, f"{user}@{host}:{port}")
        except Exception as exc:
            raise ValueError(f"OS credential store is unavailable for {role}.") from exc
        if value is None:
            raise ValueError(f"No OS-stored password found for {role}.")
    elif file:
        path = Path(file)
        if not path.is_file():
            raise ValueError(f"Password file is unavailable for {role}.")
        if os.name != "nt" and path.stat().st_mode & 0o077:
            raise ValueError(f"Password file for {role} must be accessible only by its owner.")
        value = path.read_text(encoding="utf-8").rstrip("\r\n")
    else:
        value = os.environ.get(prefix, "")
    return remember_secret(value)
