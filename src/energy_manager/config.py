import os

from dotenv import load_dotenv

load_dotenv()


def get_home_assistant_url() -> str:
    url = os.getenv("HOME_ASSISTANT_URL")

    if not url:
        raise RuntimeError("HOME_ASSISTANT_URL is not configured")

    return url


def get_home_assistant_token() -> str:
    token = os.getenv("HOME_ASSISTANT_TOKEN")

    if not token:
        raise RuntimeError("HOME_ASSISTANT_TOKEN is not configured")

    return token

def get_mqtt_host() -> str:
    host = os.getenv("MQTT_HOST")
    if not host:
        raise RuntimeError("MQTT_HOST is not configured")
    return host


def get_mqtt_port() -> int:
    port = os.getenv("MQTT_PORT")
    if not port:
        raise RuntimeError("MQTT_PORT is not configured")
    return int(port)


def get_mqtt_username() -> str:
    username = os.getenv("MQTT_USERNAME")
    if not username:
        raise RuntimeError("MQTT_USERNAME is not configured")
    return username


def get_mqtt_password() -> str:
    password = os.getenv("MQTT_PASSWORD")
    if not password:
        raise RuntimeError("MQTT_PASSWORD is not configured")
    return password


def get_marstek_device_type() -> str:
    device_type = os.getenv("MARSTEK_DEVICE_TYPE")
    if not device_type:
        raise RuntimeError("MARSTEK_DEVICE_TYPE is not configured")
    return device_type


def get_marstek_device_mac() -> str:
    device_mac = os.getenv("MARSTEK_DEVICE_MAC")
    if not device_mac:
        raise RuntimeError("MARSTEK_DEVICE_MAC is not configured")
    return device_mac