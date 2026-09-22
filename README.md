# Residential Energy Management

A modular Python project for estimating residential electrical load and, in later stages, controlling a battery system based on the estimated demand.

The project is currently focused on building a clean and testable data pipeline around Home Assistant. It combines directly measured power values, state-based estimates, and constant baseline loads into a transparent total load estimate.

> **Status:** Work in progress. The load-estimation foundation is implemented; battery control and MARSTEK integration are planned next.

## Goals

The project is intended to evolve into a reusable residential energy-management system with the following goals:

- collect power and state information from Home Assistant and other sources,
- estimate the current apartment load from heterogeneous input data,
- preserve the origin and quality of every contribution to the estimate,
- handle stale, implausible, or unavailable data explicitly,
- control a battery system based on the estimated load,
- publish calculated values and controller states back to Home Assistant,
- remain configurable for different installations without hard-coded entity IDs.

The architecture deliberately separates infrastructure-specific code from the estimation and control logic so that additional data sources such as MQTT, ESP32 devices, or a dedicated MARSTEK adapter can be added later without changing the core domain logic.

## Current Architecture

The current design keeps the responsibilities intentionally small:

- **`config_loader.py`** reads installation-specific configuration.
- **`HomeAssistantAdapter`** translates Home Assistant data into internal models.
- **`validation.py` / `quality.py`** evaluate freshness and plausibility.
- **`estimation.py`** converts valid inputs into load contributions and builds the total load estimate.
- **`models.py`** contains the shared domain models.
- A higher-level application flow will later orchestrate adapters, estimation, control, and publishing.

## Supported Load Inputs

The load estimator currently distinguishes three contribution types.

### 1. Measured loads

A real power measurement is read from a sensor such as a smart plug.

```text
Home Assistant entity
        |
        v
PowerMeasurement
        |
        v
MEASURED PowerContribution
```

`MEASURED` describes the **origin of the value**, not a guarantee that the value represents the exact instantaneous physical power. Sensor update intervals and communication delays are handled separately through freshness information.

### 2. State-estimated loads

For devices where only a state such as `on` / `off` is available, a configured power value can be used.

Example:

```text
TV state = on
configured estimate = 85 W
        |
        v
STATE_ESTIMATED PowerContribution = 85 W
```

`unknown` and `unavailable` states are retained as unavailable contributions instead of silently being treated as valid zero-power states.

### 3. Constant-estimated loads

Loads without a sensor or state can be represented by a constant estimate, for example a long-term average refrigerator consumption.

```text
configured refrigerator baseline = 40 W
        |
        v
CONSTANT_ESTIMATED PowerContribution = 40 W
```

## Load Contributions

Every contribution keeps both its semantic name and, where available, its technical source.

Conceptually:

```text
PowerContribution
├── name                  e.g. "washing_machine"
├── source                e.g. "sensor.washing_machine_power"
├── power_w
├── contribution_type
└── status
```

The logical `name` is independent of the Home Assistant entity ID. This makes the estimate easier to interpret and keeps infrastructure details out of higher-level logic.

Current contribution types:

- `MEASURED`
- `STATE_ESTIMATED`
- `CONSTANT_ESTIMATED`

Current contribution statuses:

- `VALID`
- `STALE`
- `IMPLAUSIBLE`
- `UNAVAILABLE`

For the current control strategy, stale, implausible, or unavailable inputs contribute **0 W** to the total load. Their status is still preserved so that the reason for the zero contribution remains visible.

This allows the system to distinguish, for example:

```text
0 W + VALID        -> actual valid zero-power value
0 W + STALE        -> old data was rejected
0 W + IMPLAUSIBLE  -> implausible value was rejected
0 W + UNAVAILABLE  -> no usable data was available
```

## Power Flow Semantics

Raw power values are kept non-negative.

The meaning of a value is expressed through its channel type rather than by using negative numbers.

Current `PowerChannelType` values include:

- `CONSUMER`
- `BATTERY_INPUT`
- `BATTERY_OUTPUT`
- `INVERTER_OUTPUT`

Only `CONSUMER` channels are currently included in the residential load estimate. Battery and inverter channels will later be used for energy-flow and controller logic.

## Configuration

Installation-specific entity IDs are deliberately kept out of the public repository.

The repository contains:

```text
config/sensors.example.yaml
```

The local installation uses:

```text
config/sensors.yaml
```

`config/sensors.yaml` is ignored by Git.

Example configuration:

```yaml
power_sensors:
  washing_machine:
    entity_id: sensor.washing_machine_power
    channel_type: consumer
    min_power_w: 0
    max_power_w: 2500
    max_age_seconds: 30

estimated_consumers:
  tv:
    entity_id: switch.tv
    estimated_power_w: 85
    max_age_seconds: 60

constant_consumers:
  fridge:
    estimated_power_w: 40
```

The YAML configuration is loaded once and converted into a `LoadConfig` containing:

```text
LoadConfig
├── power_sensors
├── estimated_consumers
└── constant_consumers
```

The estimation logic therefore does not depend on YAML directly.

## Home Assistant Integration

The current Home Assistant adapter supports:

- connection checks,
- reading raw entity states internally,
- converting power entities into `PowerMeasurement`,
- converting generic Home Assistant states into `EntityState`,
- explicit handling of unavailable or invalid measurements.

Home Assistant credentials and URLs are supplied through environment variables rather than hard-coded values:

```text
HOME_ASSISTANT_URL
HOME_ASSISTANT_TOKEN
```

A local `.env` file can be used during development and must not be committed.

A simple integration script is available at:

```text
scripts/check_home_assistant.py
```

It is currently used to verify the real Home Assistant connection and the configured power-sensor path.

## Data Freshness and Quality

Measurements contain two timestamps:

- `reported_at`: when the source reported the value,
- `received_at`: when the energy manager received/fetched it.

This allows the application to distinguish data retrieval time from measurement freshness.

Measured power is currently evaluated using:

1. **freshness**
2. **static plausibility limits**

A measurement can therefore be classified as:

```text
VALID
STALE
IMPLAUSIBLE
```

State-based consumers use the same reported/received timestamp concept for freshness checks.

Dynamic plausibility checks may be added later. They are intentionally not implemented yet because legitimate household loads can change quickly and should not be rejected solely because of a large power jump.

## Load Estimation

`estimate_load()` combines the configured input types into a `LoadEstimate`.

Example:

```text
washing_machine   180 W   MEASURED
tv                 85 W   STATE_ESTIMATED
fridge             40 W   CONSTANT_ESTIMATED
--------------------------------------------
total              305 W
```

The resulting `LoadEstimate` retains every `PowerContribution` in addition to the total power. This is important for later diagnostics, Home Assistant visualization, and evaluation of how much of the estimate is measured versus estimated.

Missing configured measurements or entity states are represented as `UNAVAILABLE` contributions with 0 W rather than causing the complete load estimate to fail.

## Project Structure

```text
residential-energy-management/
├── config/
│   └── sensors.example.yaml
├── scripts/
│   └── check_home_assistant.py
├── src/
│   └── energy_manager/
│       ├── adapters/
│       │   └── home_assistant.py
│       ├── config.py
│       ├── config_loader.py
│       ├── estimation.py
│       ├── exceptions.py
│       ├── models.py
│       ├── quality.py
│       └── validation.py
├── tests/
├── .gitignore
├── pyproject.toml
└── README.md
```

## Development

The project currently uses:

- Python 3.12
- `pytest`
- `ruff`
- `requests`
- `python-dotenv`
- `PyYAML`

Run the test suite with:

```bash
pytest -v
```

Run static checks with:

```bash
ruff check .
```

The codebase is developed test-first for the core domain behavior. External systems such as Home Assistant are mocked in unit tests, while the integration script is used for real connectivity checks.

## Design Principles

The project currently follows a few explicit design rules:

- configuration is separated from application logic,
- infrastructure-specific formats are converted into internal models,
- domain logic does not depend on Home Assistant,
- measurement quality and estimation are separate concerns,
- missing or bad input data should not make the complete estimate unusable,
- the origin and quality of each contribution should remain traceable,
- power values stay non-negative; semantic channel types describe their role,
- new abstractions are introduced only when they solve a concrete problem.

## Roadmap

Planned next steps include:

- connect the current load-estimation flow to real configured Home Assistant inputs,
- expose calculated load estimates and statuses back to Home Assistant,
- integrate MARSTEK B2500 data/control through an appropriate adapter,
- integrate Growatt inverter output,
- implement the battery controller,
- add controller deadband / minimum hold time to avoid unnecessary setpoint changes,
- add logging and observability,
- deploy the lean runtime on a Raspberry Pi,
- optionally add MQTT / ESP32 integrations,
- later evaluate persistence, PostgreSQL, Docker, CI/CD, and ML-based estimation where they provide a clear benefit.

The long-term target is a reusable system in which a new installation can primarily be adapted through configuration instead of changing the core application code.
