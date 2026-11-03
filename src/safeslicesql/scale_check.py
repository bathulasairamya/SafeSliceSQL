"""Measured synthetic scale evidence, explicitly not production certification."""

import platform
import threading
import time
from pathlib import Path

import psutil

from safeslicesql.jobs import atomic_json
from safeslicesql.runtime import OperationInterrupted, checkpoint
from safeslicesql.security import safe_error
from safeslicesql.synthetic import run_synthetic_suite


def run_scale_check(
    output: Path,
    *,
    levels: list[int],
    seeds: list[int],
    cases: int = 5,
    mysql: bool = False,
    progress=None,
) -> dict:
    if not levels or not seeds or cases < 1 or any(value < 1 for value in levels):
        raise ValueError("Supply positive row levels/case count and at least one seed.")
    output.mkdir(parents=True, exist_ok=False)
    evidence = {
        "synthetic": True,
        "production_certification": False,
        "state": "running",
        "requested_levels": levels,
        "seeds": seeds,
        "cases": cases,
        "backend": "mysql" if mysql else "sqlite",
        "python": platform.python_version(),
        "platform": platform.platform(),
        "logical_cpus": psutil.cpu_count(),
        "measurements": [],
    }
    atomic_json(output / "evidence.json", evidence)
    process = psutil.Process()
    for number, (rows, seed) in enumerate((rows, seed) for rows in levels for seed in seeds):
        checkpoint()
        peak = [process.memory_info().rss]
        stop = threading.Event()

        def sample():
            while not stop.wait(0.05):
                peak[0] = max(peak[0], process.memory_info().rss)

        thread = threading.Thread(target=sample, daemon=True)
        started, cpu = time.perf_counter(), process.cpu_times()
        thread.start()
        directory = output / f"sample_{number + 1}"
        try:
            records = run_synthetic_suite(
                directory, cases=cases, rows=rows, seed=seed, mysql=mysql, progress=progress
            )
        except BaseException as exc:
            evidence.update(
                state="stopped" if isinstance(exc, OperationInterrupted) else "failed",
                error=safe_error(exc),
                incomplete_sample={
                    "max_rows_per_table": rows,
                    "seed": seed,
                    "directory": str(directory),
                    "seconds": time.perf_counter() - started,
                },
            )
            atomic_json(output / "evidence.json", evidence)
            raise
        finally:
            stop.set()
            thread.join()
        current_cpu = process.cpu_times()
        evidence["measurements"].append(
            {
                "max_rows_per_table": rows,
                "seed": seed,
                "cases": cases,
                "checks": len(records),
                "failed": sum(not row["success"] for row in records),
                "actual_source_rows_by_case": {
                    row["case"]: row.get("source_rows", 0) for row in records
                },
                "seconds": time.perf_counter() - started,
                "process_cpu_seconds": current_cpu.user
                + current_cpu.system
                - cpu.user
                - cpu.system,
                "peak_process_rss_bytes": max(peak[0], process.memory_info().rss),
                "retained_artifact_bytes": sum(
                    path.stat().st_size for path in directory.rglob("*") if path.is_file()
                ),
            }
        )
        atomic_json(output / "evidence.json", evidence)
    evidence["state"] = (
        "failed" if any(row["failed"] for row in evidence["measurements"]) else "completed"
    )
    atomic_json(output / "evidence.json", evidence)
    return evidence
