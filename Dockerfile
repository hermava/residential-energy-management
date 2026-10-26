FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Copy only application files. Local configuration and history never enter the image.
COPY pyproject.toml README.md ./
COPY src/ ./src/
COPY scripts/run_energy_manager.py ./scripts/run_energy_manager.py
COPY config/sensors.example.yaml ./defaults/sensors.example.yaml

RUN python -m pip install --no-cache-dir . \
    && ln -s /data /app/data \
    && ln -s /config /app/config

# HA mounts /data and /config at runtime; the existing relative paths keep working.
STOPSIGNAL SIGTERM
ENTRYPOINT ["python", "-m", "energy_manager.home_assistant_app"]
