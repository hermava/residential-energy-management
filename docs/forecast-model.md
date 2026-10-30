# PV power model and interval forecasts

This document describes the pure calculation core in
`src/energy_manager/forecast/power_forecast.py`. It is prepared for future runtime
integration; **the running manager still uses its existing forecast formulas**.
See the [user guide](user-guide.md#energy-forecast) for current outputs and rules.

## Target and model

PV connects directly to the MARSTEK, with the inverter downstream. The target is
PV power at the battery input, measured as `w1 + w2`. Battery output, SOC changes
and the 130 W household-consumption assumption belong to a separate storage balance.

The candidate model is:

```text
G = solar energy per area / interval duration in seconds     [W/m²]
c = max(0, cos(angle of incidence on the modules))
P = G × (a + b × c)                                         [W]
```

Location, timestamp, panel azimuth and tilt determine the angle of incidence at
each interval midpoint. Power is constant within that interval. Coefficients
`a` and `b` are fitted offline, must be nonnegative and introduce no power offset
at zero irradiation. This is an empirical model, not a complete physical
conversion from horizontal to tilted-plane irradiance.

An optional `max_power_w` caps predictions at a known PV input limit. The battery
output limit must not be substituted. Fitting minimizes the uncapped squared
error; capping is applied afterwards, without a separate curtailment model.

## Intervals, units and missing data

| API | Purpose |
| --- | --- |
| `WeatherInterval` | Start, end and forecast solar energy per area in J/m² |
| `predict_power_series(...)` | Mean PV power in W for each weather interval |
| `energy_between(series, start, end)` | Energy in Wh within `[start, end)` |
| `energy_for_day(series, day, zone)` | Energy for one local calendar day |
| `forecast_horizon_end(now, zone)` | End of the day after tomorrow, in UTC |

Energy is power multiplied by overlapping duration; partial intervals contribute
proportionally. Divide Wh by 1000 for kWh. Results also provide `mean_power_w`.
The same series supports now, +1 h, +2 h and daily queries, with arbitrary interval
lengths.

Timestamps must be timezone-aware. Integration uses UTC; calendar-day boundaries
use `Europe/Berlin`, preserving 23- and 25-hour days. Tomorrow excludes today's
energy. Bright Sky irradiation belongs to the hour ending at `forecast_at`.

Missing irradiation is `None`, not zero. Missing power or incomplete coverage
makes `energy_wh` unavailable; `covered_seconds` and `expected_seconds` explain
coverage. Known 0 W remains valid. Overlapping intervals are rejected, and even
a missing half-second prevents a complete result. Complete coverage is marked
`heuristic`, not statistical confidence or a traffic-light state. Weather
freshness must be checked by the adapter and orchestration layer.

## Offline fitting and evaluation

`fit_power_model()` and `evaluate_power_models()` in `offline_analysis.py` compare:

1. The existing fixed hourly power formula, separate from the daily regression.
2. A fitted irradiance-only line, `P = a × G`.
3. The irradiance-and-geometry model, `P = G × (a + b × c)`.

Fitting uses nonnegative least squares without additional numerical libraries.
A constant incidence angle is supported; night-only data does not produce a
parameter set. At least 30 valid training pairs are required for model comparison.
Zero measurements remain included. Invalid pairs and forecasts retrieved after
the target interval starts are excluded.

Later whole local days are held out for evaluation. Training intervals must not
cross the test-day boundary or the earliest evaluated retrieval or planning time.
Each group is fitted once and its parameters are frozen.
Reports include training counts, cutoffs and model status. Insufficient training
leaves the fixed baseline available where inputs permit, without fitted scores.

Scores are MAE, RMSE and bias (**forecast minus measurement**) in W, or kWh for
complete days. `active_mae_w` separately scores intervals with positive forecast
irradiation or measured power, while pure zero intervals remain in overall scores.
More hourly pairs alone do not establish daily or seasonal accuracy.

## Lead times and daily planning

`horizon_analysis.py` adds `horizon_evaluation` to the JSON report:

- **Hourly groups:** `[0, 1)`, `[1, 3)`, `[3, 12)`, `[12, 24)`, `[24, 48)` and
  48+ hours. Each group selects the latest eligible version per target hour;
  repeated snapshots do not increase a target's weight.
- **Daily planning:** At `--decision-time` (default 18:00 local), tomorrow and the
  day after tomorrow use the last single snapshot available then. Forecasts
  retrieved later are excluded, and different snapshots are not mixed.
- **Coverage:** The default maximum snapshot age is two hours. Missing hours,
  including night hours, are not invented. Ambiguous or nonexistent local
  planning times at daylight-saving transitions are excluded.

Groups use the same later test days but may contain different target hours and
training counts. Daily errors require complete matching forecasts and
measurements; reports retain exclusion reasons for incomplete cases. The daily
baseline integrates the hourly power formula rather than the runtime daily regression.

## Running the analysis and retaining data

```bash
.venv/bin/python scripts/analyze_brightsky_input.py \
  --max-gap-seconds 60 \
  --decision-time 18:00 \
  --max-snapshot-age-hours 2 \
  --output data/analysis/forecast/hourly_pairs.csv \
  --model-report data/analysis/forecast/power_model_comparison.json
```

The 60-second gap limit affects offline integration only; runtime daily counters
retain their 30-second limit. The CSV export selects the latest forecast before
each target hour. The CLI compares the three models above; the older ridge helper
remains available separately. No fitted parameters are applied to the running manager.

Analysis reads organized and legacy logs, deduplicating identical measurements.
PV history and archived weather snapshots under `data/logs/forecast/` share the
app's default 200 MiB budget. Older completed UTC-day files can expire, so archive
data externally for seasonal comparisons. Detailed diagnostics are optional and
have an additional ten-day age limit. The source-level `CSV_LOGGING_ENABLED`
master switch applies to both recording categories.

For operational details, see [calibration usage](user-guide.md#offline-calibration-data),
[log formats and archive migration](user-guide.md#logs), and
[Samba export](../DOCS.md#export-measurement-csv-files-through-samba-version-030).
