"""One experiment configuration writer for desktop and terminal clients."""

from pathlib import Path

import yaml


def write_experiment_config(
    path: Path,
    *,
    methods: list[str],
    budgets: list[int],
    seeds: list[int],
    workload: str,
    output: Path,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            {
                "matrix": {"methods": methods, "budget_percents": budgets, "seeds": seeds},
                "workload": {
                    "path": "auto" if workload == "auto" else str(Path(workload).resolve())
                },
                "output": {
                    "raw_dir": str((output / "raw").resolve()),
                    "summary_dir": str((output / "summaries").resolve()),
                    "plots_dir": str((output / "plots").resolve()),
                },
            }
        ),
        encoding="utf-8",
    )
    return path
