"""Standalone wrapper for the installed SafeSliceSQL plotting module."""

from safeslicesql.plots import generate_all_plots

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate SafeSliceSQL benchmark evaluation plots."
    )
    parser.add_argument("summary_csv", help="Path to summary CSV file")
    parser.add_argument(
        "-o", "--output-dir", default="results/plots", help="Directory to save generated plots"
    )
    args = parser.parse_args()

    plots = generate_all_plots(args.summary_csv, args.output_dir)
    print(f"Generated {len(plots)} plots:")
    for p in plots:
        print(f" - {p}")
