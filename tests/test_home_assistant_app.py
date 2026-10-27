import json
import os
import select
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from energy_manager import home_assistant_app as app

ROOT = Path(__file__).resolve().parents[1]


def options():
    return {
        "run_manager": False, "mqtt_host": "broker.example", "mqtt_port": 1883,
        "mqtt_username": "example-user", "mqtt_password": "example-password",
        "marstek_device_type": "example-type", "marstek_device_mac": "example-device",
    }


@pytest.fixture
def configured_app(tmp_path, monkeypatch):
    config = tmp_path / "config" / "sensors.yaml"
    config.parent.mkdir()
    shutil.copyfile(ROOT / "config" / "sensors.example.yaml", config)
    option_path = tmp_path / "data" / "options.json"
    option_path.parent.mkdir()
    option_path.write_text(json.dumps(options()))
    monkeypatch.setattr(app, "SENSORS_PATH", config)
    monkeypatch.setattr(app, "OPTIONS_PATH", option_path)
    monkeypatch.setattr(app, "TEMPLATE_PATH", ROOT / "config" / "sensors.example.yaml")
    monkeypatch.setattr(app, "APP_DIRECTORY", tmp_path)
    monkeypatch.setattr(app, "PRIVATE_DATA_PATH", option_path.parent)
    monkeypatch.setenv("SUPERVISOR_TOKEN", "example-supervisor-token")
    execute = Mock()
    monkeypatch.setattr(app.os, "execv", execute)
    return config, option_path, execute


def test_app_options_adapt_to_existing_environment_without_mutating_it(monkeypatch):
    monkeypatch.setenv("HOME_ASSISTANT_TOKEN", "original-token")
    environment = app.app_environment(options(), "supervisor-token")
    assert environment["HOME_ASSISTANT_URL"] == "http://supervisor/core"
    assert environment["HOME_ASSISTANT_TOKEN"] == "supervisor-token"
    assert environment["MQTT_PASSWORD"] == "example-password"
    assert environment["MQTT_PORT"] == "1883"
    assert environment["MARSTEK_DEVICE_MAC"] == "example-device"
    assert environment["BRIGHTSKY_LATITUDE"] == environment["BRIGHTSKY_LONGITUDE"] == ""
    assert environment["TZ"] == "Europe/Berlin"
    assert environment["ENERGY_MANAGER_DEBUG_LOGGING"] == "false"
    assert environment["ENERGY_MANAGER_FORECAST_DATA_LOGGING"] == "true"
    assert environment["ENERGY_MANAGER_FORECAST_LOG_MAX_MIB"] == "200"
    assert environment["ENERGY_MANAGER_DEBUG_LOG_MAX_MIB"] == "10"
    assert os.environ["HOME_ASSISTANT_TOKEN"] == "original-token"


def test_zero_coordinates_are_valid_and_optional_forecast_settings_are_exported():
    configured = {**options(), "brightsky_latitude": 0, "brightsky_longitude": 0,
                  "brightsky_timezone": "UTC", "brightsky_max_distance_m": 0}
    environment = app.app_environment(configured, "token")
    assert environment["BRIGHTSKY_LATITUDE"] == environment["BRIGHTSKY_LONGITUDE"] == "0"
    assert environment["BRIGHTSKY_MAX_DISTANCE_M"] == "0"
    assert environment["TZ"] == "UTC"


@pytest.mark.parametrize("change", [
    {"mqtt_password": ""}, {"mqtt_host": " "}, {"mqtt_username": None},
    {"mqtt_port": 0}, {"mqtt_port": 65536}, {"mqtt_port": True}, {"mqtt_port": "1883"},
    {"brightsky_latitude": 49}, {"brightsky_longitude": 9},
    {"brightsky_latitude": float("nan"), "brightsky_longitude": 9},
    {"brightsky_latitude": 91, "brightsky_longitude": 9},
    {"brightsky_latitude": 49, "brightsky_longitude": 181},
    {"brightsky_latitude": True, "brightsky_longitude": 9},
    {"brightsky_max_distance_m": -1}, {"brightsky_timezone": "invalid/timezone"},
    {"debug_logging": "false"}, {"forecast_data_logging": None},
    {"forecast_log_max_mib": 0}, {"forecast_log_max_mib": 10241},
    {"debug_log_max_mib": True},
])
def test_invalid_options_are_rejected_without_leaking_values(change):
    with pytest.raises(app.AppConfigurationError) as error:
        app.app_environment({**options(), **change}, "secret-supervisor-token")
    assert "secret-supervisor-token" not in str(error.value)
    assert "example-password" not in str(error.value)


def test_missing_supervisor_token_is_rejected():
    with pytest.raises(app.AppConfigurationError, match="SUPERVISOR_TOKEN"):
        app.app_environment(options(), "")


def test_check_mode_never_launches_the_manager_or_connects(configured_app, monkeypatch, capsys):
    _, _, execute = configured_app
    get, post, client = Mock(), Mock(), Mock()
    monkeypatch.setattr("requests.get", get)
    monkeypatch.setattr("requests.post", post)
    monkeypatch.setattr("paho.mqtt.client.Client", client)
    assert app.main() == 0
    execute.assert_not_called()
    get.assert_not_called()
    post.assert_not_called()
    client.assert_not_called()
    assert "does not test connectivity" in capsys.readouterr().out


def test_first_start_creates_example_but_does_not_run_it(configured_app, capsys):
    config, _, execute = configured_app
    config.unlink()
    assert app.main() == 1
    assert config.read_bytes() == app.TEMPLATE_PATH.read_bytes()
    execute.assert_not_called()
    assert "Adapt it before starting again" in capsys.readouterr().out


def test_existing_configuration_is_never_replaced(configured_app):
    config, _, execute = configured_app
    original = config.read_bytes() + b"\n# Local installation\n"
    config.write_bytes(original)
    assert app.main() == 0
    assert config.read_bytes() == original
    execute.assert_not_called()


@pytest.mark.parametrize("content", ["not json", "[]", '{"run_manager": "false"}'])
def test_bad_options_stop_before_starting_the_manager(configured_app, content, capsys):
    _, path, execute = configured_app
    path.write_text(content)
    assert app.main() == 1
    execute.assert_not_called()
    assert "failed" in capsys.readouterr().err


def test_invalid_sensor_yaml_does_not_expose_its_contents(configured_app, capsys):
    config, _, execute = configured_app
    config.write_text("battery: [private-content")
    assert app.main() == 1
    execute.assert_not_called()
    assert "private-content" not in capsys.readouterr().err


def test_run_mode_executes_existing_entrypoint_with_expected_environment(
    configured_app, monkeypatch,
):
    _, path, execute = configured_app
    path.write_text(json.dumps({**options(), "run_manager": True}))
    monkeypatch.chdir(ROOT)
    for name in app.app_environment(options(), "example-supervisor-token"):
        monkeypatch.setenv(name, "old-value")
    assert app.main() == 0
    assert Path.cwd() == path.parent.parent
    assert os.environ["HOME_ASSISTANT_URL"] == "http://supervisor/core"
    assert os.environ["HOME_ASSISTANT_TOKEN"] == "example-supervisor-token"
    assert os.environ["MQTT_HOST"] == "broker.example"
    execute.assert_called_once_with(sys.executable, [
        sys.executable, "-u", str(path.parent.parent / "scripts/run_energy_manager.py"),
    ])


def test_app_manifest_starts_in_check_mode_without_privileged_access():
    manifest = yaml.safe_load((ROOT / "config.yaml").read_text())
    assert manifest["options"]["run_manager"] is False
    assert manifest["options"]["import_state"] is False
    assert manifest["options"]["debug_logging"] is False
    assert manifest["options"]["forecast_data_logging"] is True
    assert manifest["boot"] == "manual"
    assert manifest["init"] is True
    assert manifest["homeassistant_api"] is True
    assert manifest["map"] == [{"type": "app_config", "read_only": False}]
    assert "aarch64" in manifest["arch"]
    assert not any(manifest.get(key) for key in (
        "host_network", "privileged", "full_access", "docker_api", "hassio_api", "ports",
    ))


def test_logging_options_are_applied_before_fresh_manager_imports_settings():
    environment = dict(os.environ, **app.app_environment(options(), "example-token"))
    code = (
        "from energy_manager import settings; "
        "assert settings.DEBUG_LOGGING_ENABLED is False; "
        "assert settings.FORECAST_DATA_LOGGING_ENABLED is True; "
        "assert settings.FORECAST_LOG_BUDGET_BYTES == 200 * 1024 * 1024; "
        "assert settings.DEBUG_LOG_BUDGET_BYTES == 10 * 1024 * 1024"
    )
    subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=environment, check=True)


def test_import_is_only_permitted_with_the_manager_disabled(configured_app, capsys):
    _, path, execute = configured_app
    path.write_text(json.dumps({**options(), "run_manager": True, "import_state": True}))
    assert app.main() == 1
    execute.assert_not_called()
    assert "requires run_manager: false" in capsys.readouterr().err


def test_import_mode_copies_state_and_exits_without_launching(configured_app, capsys):
    config, path, execute = configured_app
    source = config.parent / "state-import"
    source.mkdir()
    (source / "marstek_recovery_state.json").write_text('{"active_until": null}')
    path.write_text(json.dumps({**options(), "import_state": True}))
    assert app.main() == 0
    execute.assert_not_called()
    assert (path.parent / "marstek_recovery_state.json").read_text() == '{"active_until": null}'
    assert "State import complete" in capsys.readouterr().out


def test_existing_shutdown_handler_receives_sigterm_without_hardware(tmp_path):
    marker = tmp_path / "restored.txt"
    child = tmp_path / "shutdown_check.py"
    child.write_text(
        "import runpy, time\n"
        "from pathlib import Path\n"
        f"namespace = runpy.run_path({str(ROOT / 'scripts/run_energy_manager.py')!r})\n"
        "main = namespace['main']\n"
        "scope = main.__globals__\n"
        "for name in list(scope):\n"
        "    if name.startswith('get_'):\n"
        "        scope[name] = lambda: 'unused'\n"
        "scope['load_config'] = lambda _: None\n"
        "scope['HomeAssistantAdapter'] = lambda **_: None\n"
        "scope['MarstekMqttAdapter'] = lambda **_: None\n"
        "scope['_read_recovery_state'] = lambda: {'active_until': 'simulated'}\n"
        f"scope['_abort_recovery_on_ha_failure'] = lambda *args: Path({str(marker)!r})"
        ".write_text('mock restoration requested')\n"
        "def cycle(**kwargs):\n"
        "    print('READY', flush=True)\n"
        "    time.sleep(30)\n"
        "scope['_run_cycle'] = cycle\n"
        "main()\n"
    )
    process = subprocess.Popen(
        [sys.executable, str(child)], cwd=ROOT, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True,
    )
    try:
        readable, _, _ = select.select([process.stdout], [], [], 5)
        assert readable, "Shutdown test process did not become ready"
        assert process.stdout.readline().strip() == "READY"
        process.terminate()
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 0, stderr
        assert "safe recovery cancellation" in stdout
        assert marker.read_text() == "mock restoration requested"
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
