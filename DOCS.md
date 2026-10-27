# Residential Energy Manager app

Experimental local Home Assistant OS app. The existing manager is packaged in a
container; its control, recovery and forecast calculations are unchanged.
Installation, check mode, version 0.1.1 state import and an initial live cycle have
been exercised on Home Assistant Green. HA access, MQTT status reception and
controller target calculation worked. This does not yet establish sustained
control, command acknowledgement, live shutdown or power-loss recovery.

The default `run_manager: false` validates local configuration and then exits.
It makes no HA, MQTT or weather requests. A successful check does not establish
connectivity, correct entity IDs, hardware compatibility or safe live operation.

## Local installation

This is a manual installation procedure, not an automatic deployment.

1. Use Samba share or Terminal & SSH to access HA's `local_apps` folder. Older
   installations expose this as `addons` or `/addons`.
2. Copy this repository into one app directory, for example
   `local_apps/residential_energy_manager/`. `config.yaml`, `Dockerfile`,
   `pyproject.toml`, `README.md`, `DOCS.md`, `CHANGELOG.md`, `.dockerignore`, `src/`,
   `scripts/run_energy_manager.py`, `config/sensors.example.yaml` and
   `translations/` belong there. Do not copy `.env`, `.git`, `.venv`, local
   `config/sensors.yaml` or `data/` into this source directory.
3. In Settings → Apps → Install app, check for updates and refresh. Find
   Residential Energy Manager under Local apps, then install it. The Green builds
   the image locally; internet access is needed to download the base image and
   Python dependencies. No public registry or repository publication is needed.
4. Keep `run_manager: false`. Enter the existing MQTT credentials and MARSTEK
   identifiers on the Configuration tab. Bright Sky coordinates are optional;
   supply both or leave both unset.
5. Start the app once. It creates an example `/config/sensors.yaml`, reports that
   it must be adapted, and exits with an error. This first exit is intentional.
6. In the app's public configuration folder, replace that example with your
   existing sensor configuration. Via Samba, look under
   `app_configs/local_residential_energy_manager/`; older versions use
   `addon_configs`. This is this app's folder, not HA's own configuration folder.
7. Start again with `run_manager: false`. A successful local check exits normally;
   the app consequently shows as stopped. It does not keep a background worker
   alive in this mode. Inspect its Log tab.

The app uses the HA Supervisor API proxy and its supplied `SUPERVISOR_TOKEN`.
Do not enter a personal HA token in the app options. The required
`homeassistant_api` permission gives the app administrator access through that
proxy; the token must be treated as a secret. Extra Supervisor management,
host-network, hardware and Docker-socket permissions are not requested.

If the broker is the official Mosquitto app on the same Green, use
`mqtt_host: core-mosquitto` and `mqtt_port: 1883`. This uses the internal app
network and worked in the first live test after a LAN IPv6 connection failed.
For external brokers, keep their reachable address; the packaging does not
migrate MQTT traffic or automatically configure IPv6 routing.
`localhost` inside the container means the container itself, not the Green or Pi.

## Moving the active manager

Enabling `run_manager: true` starts the existing manager, including active battery
control. Do this only as a separate, deliberate migration after the local checks.

- Stop the previous Pi service before enabling the manager in the app. Never run
  both controllers against the same battery.
- Preserve the existing JSON state using the offline import below. The private
  `/data` directory is separate from the public `app_configs` directory.
  `/data/options.json` is managed by HA and must not be replaced.
- Review a pending recovery reported by the import before enabling live control.
  State values and recovery deadlines are copied unchanged; the import does not
  convert, reset or resolve recovery state.
- Observe logs and outputs during the first live run. Start-on-boot is disabled
  by default; enable it separately after validating the handover.

The user performed the state import and an initial live start on 2026-10-26.
The assistant did not remotely deploy, stop services or send hardware commands.

## Import existing state (version 0.1.1)

First update the local app source, keeping its directory and slug unchanged,
then install version 0.1.1 through HA. Keep `run_manager: false`.

1. Stop the old Pi service and check that `systemctl is-active energy-manager`
   reports `inactive`. The app must also be stopped.
2. Copy the existing state JSON files from the Pi's project `data/` directory
   to your laptop. For example, in Windows PowerShell:

   ```powershell
   $stateExport = Join-Path $env:USERPROFILE ("Downloads\energy-manager-state-" + (Get-Date -Format yyyyMMdd-HHmmss))
   New-Item -ItemType Directory -Path $stateExport
   scp "pi@raspberrypi.local:/home/pi/residential-energy-management/data/*.json" $stateExport
   ```

   Adapt the SSH host or project path if your Pi uses different ones. These files
   are local operational data; do not add them to Git or send their contents in chat.
3. In the app's Samba configuration folder, create `state-import` next to
   `sensors.yaml`. Copy only the supported files that actually exist on the Pi:

   ```text
   app_configs/local_residential_energy_manager/
     sensors.yaml
     state-import/
       marstek_recovery_state.json
       battery_daily_energy.json
       brightsky_forecast_state.json
   ```

4. Enable `import_state`, leave `run_manager` disabled, save and start the app.
   The importer validates every supplied file before writing anything. It copies
   bytes unchanged into private `/data` and exits without network or device access.
   Existing different state is never overwritten. Identical files permit a repeat
   of the same import. An error prevents the manager from starting.
5. Check the log for `State import complete` and the imported filenames. If the
   log reports pending recovery, review it before enabling battery control.
6. Disable `import_state` again. A later live start requires `run_manager: true`
   and `import_state: false`.

Only these three JSON filenames are supported. Missing files are not invented;
an empty import folder or additional files are rejected. `options.json` and
credentials must never be placed in `state-import`. The original Pi files and
the import copies remain untouched. The import does not include historical CSV
archives: keep those on the Pi for later transfer or offline analysis.

At the final handover, prevent the old service from restarting at Pi boot with
`sudo systemctl disable energy-manager`, and verify it remains inactive before
enabling the manager in the app. Do not restart the old service while the app is
actively controlling the battery.

## Storage and updates

| Container path | Purpose | Owner |
| --- | --- | --- |
| `/app` | Installed code and launch scripts from the image | Image build |
| `/config/sensors.yaml` | Your sensor and battery configuration | User |
| `/data/options.json` | App options, including credentials | HA Supervisor |
| `/data/*.json` | Recovery, daily energy and forecast state | Manager |
| `/data/logs/` | Bounded diagnostic CSV and PV/weather development data | Manager |

The image links `/app/config` to `/config` and `/app/data` to `/data`. This preserves
the existing relative Python paths without changing the control modules.
The mounted data survives container replacement and app updates. Maintain backups;
uninstalling the app can remove its data. Options and backups can contain secrets.
See the recording limits below before updating a large existing archive.

## Export measurement CSV files through Samba (version 0.3.0)

The measurement files live in the app's private `/data` mount, which Samba cannot
access directly. The `export_logs` option copies them into the public app
configuration folder. Install the updated source and app manifest first.

1. Stop the app. Set `run_manager: false`, `import_state: false` and
   `export_logs: true` in its Configuration tab, then save.
2. Start once. The app copies existing CSV files and exits. The Log tab reports
   `Log export complete`, the file count and the destination. A stopped app is
   expected after a successful export. Battery control remains paused during this
   operation; the old Pi service must remain stopped.
3. Open Samba's `app_configs/local_residential_energy_manager/log-export/`
   (older installations call the share `addon_configs`). Each successful export
   has its own folder named with its UTC creation time and a unique suffix.
4. Copy that folder to your computer. It contains `logs/forecast/` and
   `logs/marstek/` where available; legacy measurement CSV files remain at the
   export folder's root. Today's files are included up to the stopped manager's
   last recorded measurement. Missing or disabled recordings are not recreated.
5. Set `export_logs: false` and `run_manager: true`, keep `import_state: false`,
   save and start the app again to resume battery control.

The export runs locally without HA, MQTT or weather access, and does not require
valid sensor configuration or credentials. It copies recognized CSV files directly
from `/data/logs/forecast/` and `/data/logs/marstek/`, plus legacy root-level files
matching `pv_input_history_*.csv`, `brightsky_forecast_*.csv`,
`energy_forecast_*.csv`, `marstek_*.csv` or `*_marstek_*.csv`.
It excludes options, JSON state, environment files, analysis and previous exports.
Source files and previous exports are preserved; symlinks are rejected. A failed
copy is not published as a completed export.

Exports consume additional disk space and are not covered by CSV recording
budgets. After copying them to your computer, remove unneeded export folders
through Samba. Diagnostic CSV files can include device identifiers and household
usage information; review them before sharing publicly.

## Development data and disk limits (version 0.2.0)

The default profile is `forecast_data_logging: true` and `debug_logging: false`.
It keeps the original PV measurement resolution and hourly archived weather
retrievals, while skipping full MARSTEK CSV, minute-by-minute diagnostic CSV and
routine per-cycle console values. Errors and existing control/recovery event
messages remain visible. The app can therefore run quietly while HA values update.

`forecast_log_max_mib` defaults to 200 MiB shared by the PV and Bright Sky CSV
families. `debug_log_max_mib` defaults to 10 MiB separately for diagnostic files
in each of the two log directories (20 MiB combined). Budgets are configurable
from 1 to 10240 MiB. These limits cover managed CSV only, not total HA storage.

While recording, the writer removes the oldest completed UTC-day files as needed.
Today's files are never deleted or truncated to make room; recording pauses if
they fill the budget. Future-dated files are also protected. With external file
changes or a newly lowered budget, protected existing data can exceed the limit;
new writes pause until room becomes available. External changes are reconciled
at least every five minutes when writing continues. Disabled categories retain
their existing files and do not prune. JSON states, credentials, import copies,
analysis exports and unrelated/legacy filename families are excluded.

This replaces the previous unbounded PV/weather archive policy. Export training
data to your laptop before old files expire. No automatic export is implemented.
The release is marked as a breaking version to prevent an unattended update from
silently changing archive retention. Updating the app does not itself start
recording or prune data; cleanup begins when its manager writes enabled CSV.

Debug recording is intended for targeted troubleshooting. Enabling it restores
the existing diagnostic columns and per-cycle console values. Console rotation
is managed by HA/container infrastructure, separately from these CSV limits.
Recovery, daily energy and forecast-state persistence, forecast calculation and
HA publication continue independently of both recording toggles.

To update a locally built app, copy the changed source files into its local app
directory, increment `version` in `config.yaml`, reload the app store and install
the offered update. A plain restart uses the existing image; it does not rebuild
Python source. Keep the slug and app location stable to retain the same app identity.

## Development and validation

```bash
docker build --tag residential-energy-manager:dev .
```

Building an image does not start the manager. Do not run the image with real
credentials and `run_manager: true` while the Pi service is active.

CI builds an amd64 image and runs the check mode with `--network none`, mock
credentials and temporary mounts. Nothing is pushed to a registry. The user has
also built and installed the app on aarch64 Green, imported state and observed
an initial live cycle. Sustained operation and live shutdown remain to be checked.
The existing SIGTERM handler is covered by a subprocess test with mocked adapters;
this does not replace testing signal delivery and shutdown on HA OS.

The base image is `python:3.11-slim-bookworm`, matching the existing Python 3.11 CI.
The tag can receive updates and project dependencies are not locked; builds are
not guaranteed to be byte-identical. Pinning a reviewed base digest and dependency
versions is a follow-up for reproducible releases.

A German explanation of the container concepts and design choices is available in
[Container und HA-App verstehen](docs/container-guide.md).

Official references: [local apps](https://contributors.home-assistant.io/apps/first-app/),
[app configuration](https://contributors.home-assistant.io/apps/reference/configuration/),
[API access](https://contributors.home-assistant.io/apps/guides/communication/).
