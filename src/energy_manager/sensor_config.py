from energy_manager.models import PowerSensorConfig

SMART_PLUG_OFFICE = PowerSensorConfig(
    entity_id="sensor.smart_plug_valentin_power",
    min_power_w=0,
    max_power_w=4000,
    max_age_seconds=30,
)

SMART_PLUG_WASHING_MACHINE = PowerSensorConfig(
    entity_id="sensor.DEINE_BATTERY_INPUT_ENTITY",
    min_power_w=0,
    max_power_w=800,
    max_age_seconds=30,
)

BATTERY_OUTPUT = PowerSensorConfig(
    entity_id="sensor.DEINE_BATTERY_OUTPUT_ENTITY",
    min_power_w=0,
    max_power_w=800,
    max_age_seconds=30,
)

INVERTER_OUTPUT = PowerSensorConfig(
    entity_id="sensor.inverter_output_power_currently",
    min_power_w=0,
    max_power_w=800,
    max_age_seconds=30,
)