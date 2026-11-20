FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04

LABEL org.opencontainers.image.title="CortexLab STT Engine"
LABEL org.opencontainers.image.description="CortexLab Speech-to-Text Engine"
LABEL org.opencontainers.image.version="0.2.0"
LABEL org.opencontainers.image.source="https://github.com/kaiopiterson/cortex-stt"

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/models \
    STT_MODEL_CACHE_DIR=/models \
    HOME=/home/appuser \
    NVIDIA_DRIVER_CAPABILITIES=compute,utility

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-pip ca-certificates \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY requirements.txt /srv/requirements.txt
RUN python3 -m pip install --no-cache-dir -r /srv/requirements.txt \
    && python3 -m pip check \
    && python3 -c "import faster_whisper, requests"

RUN useradd -m appuser \
    && mkdir -p /models \
    && chown -R appuser:appuser /srv \
    && chown -R appuser:appuser /models

COPY --chown=appuser:appuser app/ /srv/app/
USER appuser
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=300s --retries=3 \
    CMD python3 -c "import json, urllib.request; response = urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3); assert json.load(response)['ready'] is True" || exit 1

CMD ["python3", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--ws-max-size", "2097152", "--ws-max-queue", "8", "--timeout-graceful-shutdown", "30"]
