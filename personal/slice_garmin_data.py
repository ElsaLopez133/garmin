import argparse
import json
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = REPO_ROOT / "data"
DEFAULT_WINDOWS = [60, 90, 180, 365]


def parse_args():
    parser = argparse.ArgumentParser(
        description="Create smaller Garmin day-window CSVs from a larger downloaded CSV."
    )
    parser.add_argument(
        "--source-days",
        type=int,
        default=730,
        help="Existing source window under data/garmin_data_{N}days.",
    )
    parser.add_argument(
        "--windows",
        type=int,
        nargs="+",
        default=DEFAULT_WINDOWS,
        help="Smaller day windows to create.",
    )
    parser.add_argument(
        "--source-csv",
        type=Path,
        default=None,
        help="Optional explicit source CSV path.",
    )
    return parser.parse_args()


def source_csv_path(source_days, source_csv):
    if source_csv is not None:
        return source_csv.expanduser().resolve()
    return DATA_ROOT / f"garmin_data_{source_days}days" / f"garmin_data_{source_days}days.csv"


def save_cycle_json(df, output_dir):
    cycle_cols = ["date", "is_period", "garmin_predicted_fertile"]
    existing_cols = [c for c in cycle_cols if c in df.columns]
    if not existing_cols:
        return

    cycle_rows = df[existing_cols].copy()
    cycle_rows["date"] = cycle_rows["date"].astype(str)
    with open(output_dir / "cycle_data.json", "w") as f:
        json.dump(cycle_rows.to_dict(orient="records"), f, indent=2)


def main():
    args = parse_args()
    source_csv = source_csv_path(args.source_days, args.source_csv)
    if not source_csv.exists():
        raise FileNotFoundError(f"Source CSV not found: {source_csv}")

    df = pd.read_csv(source_csv)
    if "date" not in df.columns:
        raise ValueError(f"Source CSV has no 'date' column: {source_csv}")

    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"]).sort_values("date")
    max_date = df["date"].max()

    print(f"Source: {source_csv}")
    print(f"Date range: {df['date'].min().date()} to {max_date.date()}")

    for days in sorted(set(args.windows)):
        if days >= args.source_days and args.source_csv is None:
            print(f"Skipping {days}: not smaller than source-days={args.source_days}")
            continue

        start_date = max_date - pd.Timedelta(days=days)
        out = df[df["date"] >= start_date].copy()
        out["date"] = out["date"].dt.date.astype(str)

        output_dir = DATA_ROOT / f"garmin_data_{days}days"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_csv = output_dir / f"garmin_data_{days}days.csv"
        out.to_csv(output_csv, index=False)
        save_cycle_json(out, output_dir)

        print(
            f"Wrote {len(out):>4} rows for {days:>4} days: "
            f"{output_csv.relative_to(REPO_ROOT)}"
        )


if __name__ == "__main__":
    main()
