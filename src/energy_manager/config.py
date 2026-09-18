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