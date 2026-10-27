# Changelog

## 0.3.0

- Add an offline `export_logs` option to copy measurement CSV files into the
  Samba-accessible app configuration folder under `log-export/`.
- Keep each export in a separate UTC-timestamped folder, including today's files
  and legacy measurement CSV files stored directly in `/data`.
- Export only while `run_manager` and `import_state` are disabled; exit afterwards
  without network access or battery commands. The export does not require valid
  sensor configuration or MQTT credentials.
- Preserve original logs and previous exports. Do not copy options, state JSON,
  environment files or analysis outputs; do not follow symlinks.

## 0.2.0

### Logging and storage policy change

- The HA app now defaults to compact forecast development data only: measured
  PV input and archived Bright Sky snapshots. Detailed diagnostic CSV and
  per-cycle console output default to off.
- Add independent `forecast_data_logging` and `debug_logging` options.
- Limit PV/weather CSV to a shared 200 MiB by default. While recording, the
  oldest completed UTC-day files are automatically removed as space is needed.
  Export valuable training data before enabling this policy on a large archive.
- Limit diagnostic CSV separately to 10 MiB per directory by default. Existing
  MARSTEK ten-day age retention still applies when diagnostics are written.
- Never delete today's files to make space. Recording pauses when today's
  files fill the budget; a later write retries when space becomes available.
- JSON state, imported files, analysis exports and unrelated files are excluded.
  Disabled categories retain their existing files and do not write or prune.
- Keep forecast calculations, HA publication, measurements and battery control
  active regardless of the recording choices. Existing control/recovery events
  and errors remain in the console.

This release requires a deliberate update because archive retention changes.
Standalone runs retain their prior debug-on default unless process environment
settings override it; the HA app explicitly supplies its quieter defaults.

## 0.1.1

- Add an explicit offline JSON-state import through the app configuration folder.
- Validate all supplied states, preserve original values and refuse conflicting
  existing app state. Imports cannot run together with the active manager.

## 0.1.0

- Package the standalone manager as an experimental local Home Assistant OS app.
- Add an offline configuration check, persistent mounts and translated options.
