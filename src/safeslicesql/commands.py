"""Apply the same bounded, journaled operation lifecycle to CLI actions."""

from dataclasses import asdict
from functools import wraps
from pathlib import Path
from typing import get_type_hints
from uuid import uuid4

import typer

from safeslicesql.config import load_logging_settings
from safeslicesql.jobs import journal
from safeslicesql.runtime import Limits, OperationCancelled, OperationInterrupted, execution_context
from safeslicesql.security import safe_error


def bounded_command(function):
    @wraps(function)
    def invoke(*args, **kwargs):
        load_logging_settings()
        try:
            limits = Limits.from_env()
            folder = Path("results/jobs") / uuid4().hex[:12]
            with journal(folder, {"command": function.__name__}) as job:
                job.record["limits"] = asdict(limits)
                job.save()
                with execution_context(limits, job=job):
                    return function(*args, **kwargs)
        except (OperationInterrupted, KeyboardInterrupt) as exc:
            typer.echo(f"[stopped] {safe_error(exc)}", err=True)
            raise typer.Exit(
                130 if isinstance(exc, (OperationCancelled, KeyboardInterrupt)) else 124
            ) from exc
        except ValueError as exc:
            typer.echo(f"[error] {safe_error(exc)}", err=True)
            raise typer.Exit(1) from exc

    invoke.__annotations__ = get_type_hints(function)
    return invoke
