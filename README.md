# Residential Energy Management

Python-based energy manager for a residential setup with Home Assistant and a MARSTEK battery storage system.

The application estimates the current household load, calculates a battery output target and controls the MARSTEK battery via MQTT.

## Features

- Load estimation from Home Assistant power sensors and state-based consumers
- Validation of stale, unavailable and implausible measurements
- Battery output control with deadband and minimum command interval
- Configurable minimum battery output
- SOC-based control inhibition
- Oscillation detection for unstable battery output
- Automatic recovery by temporarily increasing the battery target
- Recovery abort at low SOC and return to the minimum output target
- Home Assistant status entities for monitoring
- CSV logging for diagnostics and analysis
- Persistent recovery state across restarts

## Project Structure

```text
scripts/
  run_energy_manager.py      Application entry point

src/energy_manager/
  runtime.py                 Main cycle orchestration
  battery_control.py         Battery control and recovery handling
  recovery.py                Recovery state persistence
  oscillation.py             Oscillation detection
  ha_reporting.py            Home Assistant reporting
  csv_logging.py             Diagnostic CSV logging
  estimation.py              Household load estimation
  validation.py              Measurement validation
  controller.py              Battery target calculation
  settings.py                Runtime and control parameters
  adapters/                  Home Assistant and MARSTEK interfaces

config/
  sensors.yaml               Sensor and consumer configuration

data/
  *.csv                      Runtime logs
  marstek_recovery_state.json
```

## Configuration

Secrets are stored in `.env`, for example:

```env
HOME_ASSISTANT_URL=...
HOME_ASSISTANT_TOKEN=...

MQTT_HOST=...
MQTT_PORT=1883
MQTT_USERNAME=...
MQTT_PASSWORD=...

MARSTEK_DEVICE_TYPE=...
MARSTEK_DEVICE_MAC=...
```

Sensor configuration is located in:

```text
config/sensors.yaml
```

Control and monitoring parameters are located in:

```text
src/energy_manager/settings.py
```

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

The application normally runs as a systemd service:

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
.venv/bin/python -m compileall -q scripts src
```

Synchronize the project from WSL to the Raspberry Pi:

```bash
rsync -av \
  --exclude '.venv' \
  --exclude '.git' \
  --exclude '.env' \
  ./ pi@raspberrypi.local:~/residential-energy-management/
```

Then restart the service on the Pi.

## Battery Control

Normal control follows the estimated household load while respecting the configured deadband, command interval, SOC limits and minimum output.

If unstable battery output is detected, the recovery strategy temporarily applies a higher output target. Afterwards the controller restores the normal target.

If the SOC reaches the lower limit, low-SOC handling takes priority and the battery target is returned to the configured minimum output after the normal command interval.
