# Configuration, forecasts and operation

This guide contains the details behind the [project overview](../README.md).
For installing and operating the Home Assistant OS app, use the
[app guide](../DOCS.md).

Paths and shell commands below refer to a standalone checkout and are run from
its repository root. In the HA app, enter credentials and weather coordinates in
the app options and edit `sensors.yaml` in its public configuration folder.

## Configuration

For standalone installations, secrets are stored in `.env`, for example:

```env
HOME_ASSISTANT_URL=...
HOME_ASSISTANT_TOKEN=...

MQTT_HOST=...
MQTT_PORT=1883
MQTT_USERNAME=...
MQTT_PASSWORD=...

MARSTEK_DEVICE_TYPE=...
MARSTEK_DEVICE_MAC=...

# Bright Sky forecast location; keep real coordinates only in local .env
BRIGHTSKY_LATITUDE=...
BRIGHTSKY_LONGITUDE=...
BRIGHTSKY_TIMEZONE=Europe/Berlin
BRIGHTSKY_MAX_DISTANCE_M=50000
```

Sensor configuration is located in:

```text
config/sensors.yaml
```

The example configuration includes:

- `power_sensors`, `estimated_consumers` and `constant_consumers` for household load estimation.
- `battery` for capacity, SOC limits, maximum output and measurement freshness.
- `outputs` for configurable Home Assistant destination entity IDs.
- `forecast_sources` for logical signal names mapped to provider-specific entity IDs and units.
- `solar_array` for the panel azimuth and tilt used by the empirical analysis.
- `energy_forecast` for the advisory daily balance thresholds and consumption assumption.

Forecast calculations use Wh and W internally. Configure source units explicitly, for example `kWh` for energy and `W` for power.

The optional `solar_array` section belongs in `config/sensors.yaml` and the shareable
`config/sensors.example.yaml`, not in `.env`. Azimuth uses 0 degrees north and
increases clockwise; tilt is 0 degrees when horizontal and 90 degrees when
vertical. The example values are azimuth 143 degrees and tilt 60 degrees.
These values are used to calculate empirical features; they are not applied as a
standalone physical multiplier to Bright Sky irradiation.

Bright Sky location settings are read from the local `.env`, not from
`config/sensors.yaml`. Set `BRIGHTSKY_LATITUDE` and `BRIGHTSKY_LONGITUDE` there;
the optional timezone defaults to `Europe/Berlin`, and the station search radius
defaults to 50,000 m. Real coordinates must not be committed or copied into
`.env.example`.

Control and monitoring parameters are located in:

```text
src/energy_manager/settings.py
```

Run commands from the repository root so relative configuration and data paths resolve correctly.

## Energy Forecast

The current assessment covers **the rest of today until sunset**, using `sensor.sun_next_setting` as configured under `forecast_sources.daylight.sunset` and Bright Sky hourly solar irradiation for that remaining interval. The timestamp must include a timezone. The application checks the calendar date in `Europe/Berlin`; tomorrow's sunset is not used to extend today's forecast. Forecast.Solar values are retained as independent comparison data and do not feed the Bright Sky calculation or its runtime correction.

The daily assessment is recalculated approximately every 60 seconds. Bright Sky also provides daily estimated MARSTEK input for tomorrow and the day after tomorrow, with separate status outputs. Weather and solar-position features do not currently alter the active daily balance formula; they are collected for offline model evaluation.

### Energy balance

```text
effective_input_wh = estimated_battery_input_from_brightsky_irradiation
consumption_wh = consumption_w × hours_until_sunset
balance_energy_wh = capacity_wh × SOC / 100
                  + effective_input_wh
                  - consumption_wh
reserve_change_wh = effective_input_wh - consumption_wh
```

The energy estimate uses the existing linear mapping from Bright Sky irradiation
to measured MARSTEK input energy. Its current coefficients are a starting
baseline, not an automatically learned model. The runtime does not update model
parameters online.

The default **130 W represents total future household consumption**. The current battery output is not added to it. Battery input means PV power arriving at the MARSTEK, including power passed through when the battery is full; it is not net charging power.

`balance_soc_percent` expresses the aggregate balance as a percentage of battery capacity, capped at 0–100%. `reserve_change_wh` describes the expected change from the current stored energy: a negative value means declining reserve.

This is a heuristic balance, not a time-resolved SOC prediction. It does not
model charging limits, the timing of PV generation, or individual appliance
cycles. A balance surplus is not a measured or guaranteed grid export.

### Assessment states

The assessment is the **state** of `sensor.residential_energy_forecast_today`, configurable through `outputs.forecast_status_today`. It is also included in the attributes.

| State | Suggested color | Default rule |
| --- | --- | --- |
| `surplus_expected` | Green | Current SOC > 30% and balance SOC > 95% |
| `reserve_at_risk` | Red | Current SOC < 30%, mean recent PV input < 130 W, and `reserve_change_wh` < 0 |
| `limited` | Yellow | Required data is available and neither rule above applies |
| `unavailable` | Gray | Required data is missing, invalid or stale, or today's sunset has passed |

The inequalities are strict: exactly 30% SOC, exactly 130 W input, or zero reserve change does not satisfy the red rule. If low SOC and declining reserve require the input history to distinguish red from yellow, missing or insufficient history results in `unavailable`.

Colors belong to the Home Assistant presentation; the Python model uses the descriptive states above. `data_quality` is separate and currently reports `heuristic` or `unavailable`, not a probability. Yellow does not mean a calculated 50% chance of success.

Useful sensor attributes include `reason`, `sunset_at`, `horizon_hours`, `soc_percent`, `original_pv_wh`, `effective_pv_wh`, `correction_factor`, `correction_status`, `balance_soc_percent`, `reserve_change_wh` and `input_mean_w`.

### Forecast parameters

Configure these under `energy_forecast` in `config/sensors.yaml`:

| Parameter | Default | Meaning |
| --- | --- | --- |
| `consumption_w` | 130 | Assumed total future household load |
| `surplus_min_soc_percent` | 30 | Current SOC must exceed this for green |
| `surplus_target_soc_percent` | 95 | Balance SOC must exceed this for green |
| `reserve_soc_percent` | 30 | Current SOC must be below this for red |
| `input_history_max_age_seconds` | 120 | Maximum age of the latest valid input sample used for assessment |

Reserve SOC must not exceed the surplus minimum SOC. Forecast thresholds are independent of battery control SOC limits.

### Bright Sky and panel geometry

Bright Sky solar irradiation is not specific to the plane of the installed PV
modules. The runtime stores hourly irradiation, sunshine duration and cloud
cover. For each forecast interval it can also store solar altitude, solar
azimuth, the cosine of the angle of incidence for the configured panel
orientation, and cyclic day-of-year features. These geometry values are
predictors for empirical analysis; the code does not treat them as a direct
conversion of horizontal irradiation to module-plane energy.

Configure the array orientation in YAML:

```yaml
solar_array:
  azimuth_deg: 143
  tilt_deg: 60
```

The orientation convention is azimuth 0 degrees north, increasing clockwise;
tilt 0 degrees horizontal and 90 degrees vertical. Bright Sky coordinates and
timezone remain in the ignored `.env` file.

The previous `ratio_of_means_v1` helper remains available and tested, but the
runtime no longer calls it. In particular, Forecast.Solar `power_now` is not
used to adjust the Bright Sky estimate. Forecast.Solar snapshots are retained
only as a separate comparison series.

### Forecast outputs and daily comparisons

Additional outputs expose `battery_input_forecast_now_w` for the Bright Sky
hour containing the current time, `battery_input_forecast_tomorrow_kwh`,
`battery_input_forecast_day_after_tomorrow_kwh`, and
`battery_input_forecast_tomorrow_error_kwh`. The two daily kWh outputs represent
estimated battery input from Bright Sky, not Forecast.Solar PV generation.
They publish `unavailable` when the corresponding daily estimate is missing or
incomplete. The day-after-tomorrow output defaults to
`sensor.residential_energy_forecast_day_after_tomorrow_kwh` in the example config.
The daily prediction used for the error comparison is the latest
available estimate before local midnight. After that local day closes, its
signed error is calculated as actual battery input minus forecast and persisted
in `data/brightsky_forecast_state.json` with the measured-coverage flag.

### Offline calibration data

Generate hourly Bright Sky / measured MARSTEK input pairs with:

```bash
.venv/bin/python scripts/analyze_brightsky_input.py \
  --data-dir data \
  --config config/sensors.yaml \
  --max-gap-seconds 60 \
  --decision-time 18:00 \
  --max-snapshot-age-hours 2 \
  --output /tmp/bright_sky_input_training_pairs.csv \
  --model-report /tmp/power_model_comparison.json
```

The tool reads the organized log folders and legacy CSV files directly in
`data/`. It combines detailed V5 measurements with the compact PV
history, counting identical timestamps only once. Conflicting measurements at
the same timestamp are rejected. Analysis exports are not scanned as raw logs.
The default output is `data/analysis/forecast/hourly_training_pairs.csv`.

The tool picks the latest forecast snapshot available before each target hour,
integrates `input_power_w` over that same hour, and excludes intervals with
missing data or gaps over 60 seconds by default. Use `--max-gap-seconds 30` for
the stricter comparison. Values between adjacent readings are integrated with
the trapezoidal rule; allowing longer gaps can smooth short PV fluctuations.
This option only affects offline analysis; daily runtime energy counters keep
their 30-second limit. Older forecast logs without geometry
columns get those features reconstructed from their timestamps, location and
configured array orientation. The output contains forecast lead time, weather,
solar geometry, season and actual battery-input energy.

The offline evaluator holds out later whole local calendar days and compares
the existing hourly power formula, a fitted irradiation-only model, and the
candidate `P = G * (a + b * max(0, cos(incidence)))`. Coefficients are nonnegative;
the candidate has no power offset at zero irradiation. It requires at least
30 training rows and never writes model parameters back to runtime configuration.
An optional `--max-input-power-w` caps predictions when the actual PV input limit
is known; the battery output limit is not substituted. The number of usable
hourly pairs depends on the selected gap limit. More pairs alone do not establish
forecast accuracy, especially for tomorrow or the day after tomorrow.
Collect several weeks of complete measurements before drawing conclusions;
this still does not establish seasonal performance. Any model change requires
offline review and explicit adoption.

The JSON report also contains `horizon_evaluation`. It selects the latest archived
version per target hour within each lead range: [0, 1), [1, 3), [3, 12), [12, 24),
[24, 48), and 48+ hours. Each target is counted once per range. Counts and model
parameters are reported separately; different ranges can contain different targets.

For daily planning, `--decision-time` uses local time in the configured Bright Sky
timezone (default 18:00). Tomorrow and the day after tomorrow each use the last
single weather snapshot available at that decision time, without mixing updates.
`--max-snapshot-age-hours` rejects old snapshots (default 2 hours). Each daily case
records the decision and retrieval times, expected and covered hours, measured
and predicted kWh, and an exclusion reason when incomplete. Local days retain
their actual 23/24/25-hour length. Ambiguous or nonexistent local decision times
at DST transitions are excluded rather than guessed.

The horizon evaluation holds out the same later local target days across all
groups. Within each group the model is fitted once, using only measurements that
end before both the test-day boundary and the earliest evaluated issue time
(the decision time for daily planning). This can leave fewer training pairs for
longer leads. A group without enough training data still reports the fixed hourly
baseline, but leaves fitted-model scores unavailable. Daily scores use only fully
covered matching days and report MAE, RMSE and prediction-minus-measurement bias in
kWh. The daily baseline integrates the existing hourly power formula; it does not
use the separate runtime daily regression. The CSV training export and original
top-level comparison retain their latest-before-target selection.

The separate pure calculation API in `forecast/power_forecast.py` generates one
power series and integrates only the requested interval or local calendar day.
Missing coverage stays unavailable, and DST days retain their actual length.
This API is prepared for future runtime integration; current HA forecasts still
use the existing formulas. See [Forecast model](forecast-model.md) for the
algorithm, interval definitions, units and validation boundaries.

## Daily Battery Energy

The following optional `outputs` publish measured daily totals in kWh:

| Output key | Example entity ID |
| --- | --- |
| `battery_input_energy_today` | `sensor.residential_battery_input_energy_today` |
| `battery_output_energy_today` | `sensor.residential_battery_output_energy_today` |

Input and output power are integrated with the trapezoidal rule. The counters reset at midnight in `Europe/Berlin`, including daylight-saving changes. Totals are saved to `data/battery_daily_energy.json` each cycle and restored after a restart.

Missing, invalid or stale readings and intervals longer than 30 seconds are not integrated. The `complete` attribute marks incomplete days; a first start during the day or a restart leaves the known totals available but marks coverage incomplete. Downtime is not estimated or backfilled. These counters measure energy crossing the input/output interfaces, not the change in stored battery energy.

## Logs

For the HA app's logging switches, storage budgets and CSV export procedure, see
[App recording settings](../DOCS.md#development-data-and-disk-limits-version-020)
and [Samba export](../DOCS.md#export-measurement-csv-files-through-samba-version-030).
Those settings take effect on the next app start. Quiet console output is normal
when diagnostics are disabled; startup, shutdown, errors and control/recovery
messages remain enabled.

### Standalone recording settings

Standalone runs keep diagnostic logging enabled by default. Set the following
process environment variables before starting Python to override recording:

| Variable | Standalone default |
| --- | --- |
| `ENERGY_MANAGER_DEBUG_LOGGING` | `true` |
| `ENERGY_MANAGER_FORECAST_DATA_LOGGING` | `true` |
| `ENERGY_MANAGER_FORECAST_LOG_MAX_MIB` | `200` |
| `ENERGY_MANAGER_DEBUG_LOG_MAX_MIB` | `10` per diagnostic directory |

The HA starter sets these variables from the app options and defaults to
`debug_logging: false`. `CSV_LOGGING_ENABLED = False` in
`src/energy_manager/settings.py` disables both CSV categories. Recording switches
leave battery control, forecasts, HA publication and operational JSON state active.
Disabled periods leave gaps; there is no backfill.

All CSV files use the shared writer in `csv_logging.py`. Storage accounting is
cached, with rescans every five minutes or on UTC-day changes. External file edits
are reconciled on the next rescan. MARSTEK diagnostics also keep their existing
ten-day age retention when a new daily file is created. These limits cover managed
CSV families, not total disk usage, console logs or exported copies.

### Storage and file formats

Detailed MARSTEK diagnostics are under `data/logs/marstek/`. Forecast data is under
`data/logs/forecast/`:

| Daily CSV family | Contents |
| --- | --- |
| `pv_input_history_YYYY-MM-DD.csv` | Timestamp and measured PV input power; subject to the forecast storage budget |
| `brightsky_forecast_YYYY-MM-DD.csv` | Hourly weather forecasts, retrieval times and solar-array features |
| `energy_forecast_history_YYYY-MM-DD.csv` | Battery, solar forecast and weather diagnostics |
| `energy_forecast_daily_v4_YYYY-MM-DD.csv` | Daily balance and separate Bright Sky estimates |

Older daily v2/v3 files can have different schemas; affected v3 versions could
replace today's logged assessment with a later day's result. Raw CSV timestamps
and filename dates use UTC. Energy-counter resets and sunset date checks use
`Europe/Berlin`.

Compact PV measurements are recorded for each received MARSTEK status. Bright Sky
archives are collected hourly through the end of the day after tomorrow and keep
all retrieved versions, subject to the shared storage budget. Input power is the
sum of MARSTEK's `w1` and `w2` channels. Detailed forecast diagnostics normally run
every 60 seconds. Forecast.Solar remains an independent comparison series;
its `power_now` does not adjust the Bright Sky estimate.

### Organize older local archives

Run this with the manager stopped or on an offline copy:

```bash
.venv/bin/python scripts/organize_energy_logs.py --data-dir data
```

The command moves legacy CSV files into the log folders and preserves compact PV
measurements from V5 diagnostics. It refuses conflicting destination files and
can be repeated. JSON state, analysis reports and CSV files already tracked by
Git stay in place. The analysis tool also reads legacy files without migration.

## Run

Activate the virtual environment and start manually:

```bash
source .venv/bin/activate
python -u scripts/run_energy_manager.py
```

Before a manual test, stop the systemd service to avoid running two instances:

```bash
sudo systemctl stop energy-manager
```

## Raspberry Pi Service

Standalone installations can run as a systemd service:

```bash
sudo systemctl restart energy-manager
sudo systemctl status energy-manager
journalctl -u energy-manager -f
```

Autostart:

```bash
sudo systemctl enable energy-manager
```

## Development Workflow

Before deploying:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .
.venv/bin/python -m compileall -q scripts src
```

Synchronize the project from WSL to the Raspberry Pi:

```bash
rsync -av \
  --exclude '.venv' \
  --exclude '.git' \
  --exclude '.env' \
  --exclude 'config/sensors.yaml' \
  --exclude 'data/' \
  ./ pi@raspberrypi.local:~/residential-energy-management/
```

Then restart the service on the Pi.

This synchronization keeps the Pi's local sensor configuration, logs and persistent state intact. Update `config/sensors.yaml` on the Pi separately when adding new source or output mappings. The command intentionally does not use `--delete`.

## Battery Control

Normal control follows the estimated household load while respecting the configured deadband, command interval, SOC limits and minimum output.

If unstable battery output is detected, the recovery strategy temporarily applies a higher output target. Afterwards the controller restores the normal target.

If the SOC reaches the lower limit, low-SOC handling takes priority and the battery target is returned to the configured minimum output after the normal command interval.
