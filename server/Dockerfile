# syntax=docker/dockerfile:1
#
# ShadowScribe server image — API and worker share this one image; only the
# command differs (see docker-compose.yml).
#
# ffmpeg is not optional: it is the only thing standing between "phone uploads a
# random m4a/opus/amr file" and "Whisper gets 16 kHz mono PCM".
FROM python:3.11-slim

ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG INSTALL_MEMORY_BACKEND=true
ARG INSTALL_SPEAKERS=false

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    SS_DATA_DIR=/data \
    SS_HF_ENDPOINT=https://hf-mirror.com \
    HF_ENDPOINT=https://hf-mirror.com \
    HF_HUB_DISABLE_XET=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ffmpeg libgomp1 curl ca-certificates tini \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY shadowscribe ./shadowscribe

RUN pip install --index-url "${PIP_INDEX_URL}" . \
 && if [ "${INSTALL_MEMORY_BACKEND}" = "true" ]; then \
      pip install --index-url "${PIP_INDEX_URL}" 'causal-memory>=0.9.3' ; \
    fi \
 && if [ "${INSTALL_SPEAKERS}" = "true" ]; then \
      pip install --index-url "${PIP_INDEX_URL}" 'sherpa-onnx>=1.10' 'numpy>=1.24' ; \
    fi

RUN mkdir -p /data/audio /data/raw /data/models /data/memory

VOLUME ["/data"]
EXPOSE 18080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:18080/healthz || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["shadowscribe", "serve"]
