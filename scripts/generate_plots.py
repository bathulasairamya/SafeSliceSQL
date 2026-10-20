"""
scripts/generate_plots.py — Publication-grade visualization of SafeSliceSQL benchmarks.

Generates comparative charts across methods (workload-aware, referential, random-repair):
1. Query Survival Rate vs Budget
2. Retention Metrics Breakdown (Key, Result, Aggregate)
3. Dependency Overhead (Closure additions)
4. Runtime and Scalability Comparison

Can be run standalone:
    python scripts/generate_plots.py results/summaries/benchmark_summary.csv -o results/plots
"""

from __future__ import annotations

import logging
from pathlib import Path
import sys

logger = logging.getLogger("safeslicesql.plots")


def generate_all_plots(summary_csv: str | Path, output_dir: str | Path = "results/plots") -> list[Path]:
    """Generate all benchmark evaluation plots from summary CSV data."""
    try:
        import matplotlib
        matplotlib.use("Agg")  # Headless backend
        import matplotlib.pyplot as plt
        import pandas as pd
    except ImportError as exc:
        logger.warning("Matplotlib or Pandas not available for plotting: %s", exc)
        return []

    csv_path = Path(summary_csv)
    if not csv_path.exists():
        logger.warning("Summary CSV file not found at %s; skipping plot generation.", summary_csv)
        return []

    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    generated_files: list[Path] = []

    try:
        df = pd.read_csv(csv_path)
    except Exception as exc:
        logger.error("Failed to read CSV at %s: %s", csv_path, exc)
        return []

    # Configure global styling
    plt.style.use("seaborn-v0_8-whitegrid" if "seaborn-v0_8-whitegrid" in plt.style.available else "default")
    plt.rcParams.update({
        "font.family": "sans-serif",
        "font.size": 11,
        "axes.titlesize": 13,
        "axes.titleweight": "bold",
        "axes.labelsize": 12,
        "axes.labelweight": "semibold",
        "figure.titlesize": 14,
        "figure.titleweight": "bold",
        "legend.fontsize": 10,
        "figure.autolayout": True,
    })

    method_palette = {
        "workload-aware": "#1D4ED8",  # Royal Blue
        "referential": "#059669",     # Emerald Green
        "random-repair": "#D97706",   # Amber / Orange
    }
    method_markers = {
        "workload-aware": "o",
        "referential": "s",
        "random-repair": "^",
    }

    # Ensure required columns exist
    if "budget_percent" not in df.columns or "method" not in df.columns:
        logger.warning("CSV missing required columns 'budget_percent' or 'method'")
        return []

    methods = [m for m in ["workload-aware", "referential", "random-repair"] if m in df["method"].unique()]
    if not methods:
        methods = list(df["method"].unique())

    # 1. Plot Query Survival Rate vs Budget
    if "query_survival_rate_mean" in df.columns or "query_survival_rate" in df.columns:
        col = "query_survival_rate_mean" if "query_survival_rate_mean" in df.columns else "query_survival_rate"
        fig, ax = plt.subplots(figsize=(7, 5), dpi=300)
        for m in methods:
            sub = df[df["method"] == m].sort_values("budget_percent")
            ax.plot(
                sub["budget_percent"],
                sub[col] * 100,
                marker=method_markers.get(m, "o"),
                linewidth=2.4,
                markersize=8,
                label=m.capitalize(),
                color=method_palette.get(m, "#4B5563"),
            )
        ax.set_title("Workload Query Survival Rate vs. Row Budget")
        ax.set_xlabel("Row Budget (% of Source Rows)")
        ax.set_ylabel("Supported Queries Survived (%)")
        ax.set_ylim(-5, 105)
        ax.legend(title="Selection Method", frameon=True)
        ax.grid(True, linestyle="--", alpha=0.6)
        p1 = out_path / "query_survival_vs_budget.png"
        fig.savefig(p1)
        plt.close(fig)
        generated_files.append(p1)

    # 2. Multi-panel Retention Metrics Comparison
    metrics = [
        ("query_survival_rate", "Query Survival Rate"),
        ("overall_result_retention", "Result Row Retention"),
        ("overall_key_retention", "Key Retention"),
        ("overall_aggregate_retention", "Aggregate Retention"),
    ]
    fig, axes = plt.subplots(2, 2, figsize=(11, 8), dpi=300)
    axes_flat = axes.flatten()

    for idx, (m_key, title) in enumerate(metrics):
        ax = axes_flat[idx]
        col = f"{m_key}_mean" if f"{m_key}_mean" in df.columns else m_key
        if col not in df.columns:
            continue
        for m in methods:
            sub = df[df["method"] == m].sort_values("budget_percent")
            ax.plot(
                sub["budget_percent"],
                sub[col] * 100,
                marker=method_markers.get(m, "o"),
                linewidth=2.0,
                markersize=7,
                label=m.capitalize(),
                color=method_palette.get(m, "#4B5563"),
            )
        ax.set_title(title)
        ax.set_xlabel("Budget (%)")
        ax.set_ylabel("Score (%)")
        ax.set_ylim(-5, 105)
        ax.grid(True, linestyle="--", alpha=0.5)
        if idx == 0:
            ax.legend(frameon=True, loc="lower right")

    fig.suptitle("SafeSliceSQL Retention Metrics Evaluation Matrix", fontsize=15, weight="bold")
    p2 = out_path / "retention_metrics_matrix.png"
    fig.savefig(p2)
    plt.close(fig)
    generated_files.append(p2)

    # 3. Closure Overhead & Total Selected Rows
    closure_col = "closure_additions_mean" if "closure_additions_mean" in df.columns else "closure_additions"
    if closure_col in df.columns:
        fig, ax = plt.subplots(figsize=(7.5, 5), dpi=300)
        budgets = sorted(df["budget_percent"].unique())
        x = range(len(budgets))
        width = 0.25

        for i, m in enumerate(methods):
            sub = df[df["method"] == m].set_index("budget_percent").reindex(budgets).fillna(0)
            offset = (i - len(methods) / 2.0 + 0.5) * width
            ax.bar(
                [pos + offset for pos in x],
                sub[closure_col],
                width=width,
                label=m.capitalize(),
                color=method_palette.get(m, "#4B5563"),
                edgecolor="black",
                linewidth=0.5,
            )

        ax.set_title("Dependency Closure Additions by Method & Budget")
        ax.set_xlabel("Row Budget (%)")
        ax.set_ylabel("Average Closure Additions (Rows)")
        ax.set_xticks(list(x))
        ax.set_xticklabels([f"{b}%" for b in budgets])
        ax.legend(title="Method", frameon=True)
        ax.grid(True, axis="y", linestyle="--", alpha=0.6)
        p3 = out_path / "closure_additions_by_method.png"
        fig.savefig(p3)
        plt.close(fig)
        generated_files.append(p3)

    # 4. Runtime / Performance Comparison
    runtime_col = "total_runtime_mean" if "total_runtime_mean" in df.columns else "total_runtime"
    if runtime_col not in df.columns:
        runtime_col = "runtime_selection_mean" if "runtime_selection_mean" in df.columns else "runtime_selection"

    if runtime_col in df.columns:
        fig, ax = plt.subplots(figsize=(7.5, 5), dpi=300)
        budgets = sorted(df["budget_percent"].unique())
        x = range(len(budgets))
        width = 0.25

        for i, m in enumerate(methods):
            sub = df[df["method"] == m].set_index("budget_percent").reindex(budgets).fillna(0)
            offset = (i - len(methods) / 2.0 + 0.5) * width
            ax.bar(
                [pos + offset for pos in x],
                sub[runtime_col],
                width=width,
                label=m.capitalize(),
                color=method_palette.get(m, "#4B5563"),
                edgecolor="black",
                linewidth=0.5,
            )

        ax.set_title("Pipeline Execution Runtime Across Budgets")
        ax.set_xlabel("Row Budget (%)")
        ax.set_ylabel("Runtime (seconds)")
        ax.set_xticks(list(x))
        ax.set_xticklabels([f"{b}%" for b in budgets])
        ax.legend(title="Method", frameon=True)
        ax.grid(True, axis="y", linestyle="--", alpha=0.6)
        p4 = out_path / "pipeline_runtime_comparison.png"
        fig.savefig(p4)
        plt.close(fig)
        generated_files.append(p4)

    logger.info("Generated %d benchmark plots in %s", len(generated_files), out_path)
    return generated_files


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Generate SafeSliceSQL benchmark evaluation plots.")
    parser.add_argument("summary_csv", help="Path to summary CSV file")
    parser.add_argument("-o", "--output-dir", default="results/plots", help="Directory to save generated plots")
    args = parser.parse_args()

    plots = generate_all_plots(args.summary_csv, args.output_dir)
    print(f"Generated {len(plots)} plots:")
    for p in plots:
        print(f" - {p}")
