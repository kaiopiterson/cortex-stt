FROM ghcr.io/astral-sh/uv:0.11.32 AS uv

FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04

LABEL org.opencontainers.image.title="CortexLab STT Engine"
LABEL org.opencontainers.image.description="CortexLab Speech-to-Text Engine"
LABEL org.opencontainers.image.version="0.3.0"
LABEL org.opencontainers.image.source="https://github.com/kaiopiterson/cortex-stt"

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/models \
    STT_MODEL_CACHE_DIR=/models \
    HOME=/home/appuser \
    NVIDIA_DRIVER_CAPABILITIES=compute,utility \
    UV_PYTHON_INSTALL_DIR=/opt/python \
    UV_NO_CACHE=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Python 3.11 standalone (Ubuntu 22.04 traz 3.10, fora de suporte desde 10/2026).
COPY --from=uv /uv /usr/local/bin/uv
RUN uv python install 3.11 \
    && uv venv /opt/venv --python 3.11

WORKDIR /srv
COPY requirements.lock /srv/requirements.lock
RUN uv pip install --require-hashes --only-binary :all: -r /srv/requirements.lock \
    && uv pip check \
    && python -c "import faster_whisper, requests, av, ctranslate2"

RUN useradd -m appuser \
    && mkdir -p /models \
    && chown -R appuser:appuser /srv \
    && chown -R appuser:appuser /models

COPY --chown=appuser:appuser app/ /srv/app/
RUN mkdir -p /data && chown $(id -u appuser):$(id -g appuser) /data
USER appuser
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=300s --retries=3 \
    CMD python3 -c "import json, urllib.request; response = urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3); assert json.load(response)['ready'] is True" || exit 1

CMD ["python3", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--ws-max-size", "2097152", "--ws-max-queue", "8", "--timeout-graceful-shutdown", "30", "--no-access-log"]
