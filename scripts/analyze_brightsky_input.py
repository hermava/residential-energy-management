"""Create a paired hourly Bright Sky / MARSTEK input dataset for offline analysis."""

import argparse
import json
import math
from dataclasses import asdict
from datetime import time
from pathlib import Path

from energy_manager.config import get_brightsky_forecast_settings
from energy_manager.config_loader import load_config
from energy_manager.forecast.horizon_analysis import evaluate_forecast_horizons
from energy_manager.forecast.offline_analysis import (
    OFFLINE_MAX_GAP_SECONDS,
    build_hourly_forecast_rows,
    evaluate_power_models,
    find_training_input_files,
    select_latest_forecasts,
    write_training_rows,
)


def _decision_time(value: str) -> time:
    try:
        parsed = time.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("use local time HH:MM, e.g. 18:00") from error
    if len(value) != 5 or value[2] != ":" or parsed.tzinfo is not None:
        raise argparse.ArgumentTypeError("use local time HH:MM, e.g. 18:00")
    return parsed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--config", type=Path, default=Path("config/sensors.yaml"))
    parser.add_argument(
        "--max-gap-seconds", type=float, default=OFFLINE_MAX_GAP_SECONDS,
        help="Maximum measurement gap for offline integration (default: 60 seconds).",
    )
    parser.add_argument(
        "--max-input-power-w", type=float,
        help="Optional PV input limit; do not substitute the battery output limit.",
    )
    parser.add_argument("--model-report", type=Path, help="Write offline comparison as JSON.")
    parser.add_argument(
        "--decision-time", type=_decision_time, default=time(18),
        help="Local planning time for tomorrow/day-after-tomorrow backtests (default: 18:00).",
    )
    parser.add_argument(
        "--max-snapshot-age-hours", type=float, default=2,
        help="Maximum weather snapshot age at the planning time (default: 2 hours).",
    )
    parser.add_argument(
        "--output", type=Path, default=Path("data/analysis/forecast/hourly_training_pairs.csv"),
    )
    args = parser.parse_args()
    if not math.isfinite(args.max_gap_seconds) or args.max_gap_seconds <= 0:
        parser.error("--max-gap-seconds must be finite and positive")
    if not math.isfinite(args.max_snapshot_age_hours) or args.max_snapshot_age_hours <= 0:
        parser.error("--max-snapshot-age-hours must be finite and positive")
    if args.max_input_power_w is not None:
        if not math.isfinite(args.max_input_power_w) or args.max_input_power_w <= 0:
            parser.error("--max-input-power-w must be finite and positive")
    config = load_config(args.config)
    settings = get_brightsky_forecast_settings()
    forecasts, measurements = find_training_input_files(args.data_dir)
    coordinates = settings[:2] if settings is not None else (None, None)
    array = config.solar_array
    archive = build_hourly_forecast_rows(
        forecasts, measurements, max_gap_seconds=args.max_gap_seconds,
        latitude_deg=coordinates[0], longitude_deg=coordinates[1],
        panel_azimuth_deg=array.azimuth_deg if array else None,
        panel_tilt_deg=array.tilt_deg if array else None,
    )
    rows = [row for row in select_latest_forecasts(archive)
            if row["actual_battery_input_wh"] != ""]
    write_training_rows(rows, args.output)
    print(f"Wrote {len(rows)} complete hourly pairs to {args.output}")
    print(f"Offline maximum measurement gap: {args.max_gap_seconds:g} seconds")
    timezone_name = settings[2] if settings else "Europe/Berlin"
    evaluation = evaluate_power_models(
        rows, timezone_name=timezone_name, max_power_w=args.max_input_power_w,
    )
    report = json.dumps({
        "max_gap_seconds": args.max_gap_seconds, "timezone_name": timezone_name,
        **asdict(evaluation),
        "horizon_evaluation": evaluate_forecast_horizons(
            archive, timezone_name=timezone_name, decision_time=args.decision_time,
            maximum_snapshot_age_hours=args.max_snapshot_age_hours,
            max_power_w=args.max_input_power_w,
        ),
    }, indent=2)
    print(f"Offline power model comparison:\n{report}")
    if args.model_report is not None:
        args.model_report.parent.mkdir(parents=True, exist_ok=True)
        args.model_report.write_text(report + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
