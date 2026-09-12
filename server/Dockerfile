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
# Debian archive host. Left at upstream by default; mainland builds should set
# mirrors.aliyun.com or mirrors.tuna.tsinghua.edu.cn. Pulling the package index
# straight from deb.debian.org takes minutes there, and because the apt layer
# sits above every Python layer it made *every* rebuild slow.
ARG APT_MIRROR=deb.debian.org

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    SS_DATA_DIR=/data \
    SS_HF_ENDPOINT=https://hf-mirror.com \
    HF_ENDPOINT=https://hf-mirror.com \
    HF_HUB_DISABLE_XET=1

RUN if [ "${APT_MIRROR}" != "deb.debian.org" ]; then \
      sed -i "s|deb.debian.org|${APT_MIRROR}|g" \
        /etc/apt/sources.list /etc/apt/sources.list.d/*.sources 2>/dev/null || true ; \
    fi \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
      ffmpeg libgomp1 curl ca-certificates tini \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY shadowscribe ./shadowscribe

# One install layer, deliberately.
#
# A previous attempt split this in two (install deps against a stub package, then
# copy the real source and reinstall) to cache the heavy dependency resolution
# separately from source edits. It is not worth it: setuptools reuses /app/build,
# and because COPY rewrites source mtimes it silently skipped re-copying
# __init__.py, so the image shipped a zero-byte stub. The build succeeded and the
# container crash-looped. A minute of rebuild time is cheaper than that class of
# bug.
RUN pip install --index-url "${PIP_INDEX_URL}" . \
 && if [ "${INSTALL_MEMORY_BACKEND}" = "true" ]; then \
      pip install --index-url "${PIP_INDEX_URL}" 'causal-memory>=0.9.3' ; \
    fi \
 && if [ "${INSTALL_SPEAKERS}" = "true" ]; then \
      pip install --index-url "${PIP_INDEX_URL}" 'sherpa-onnx>=1.10' 'numpy>=1.24' ; \
    fi

# Assert the *installed* package is real. Must run outside /app: with cwd=/app the
# source tree shadows site-packages and the check would pass on a broken image.
RUN cd /tmp && python -c "\
import shadowscribe, shadowscribe.api, shadowscribe.service, shadowscribe.worker, shadowscribe.cli; \
print('shadowscribe', shadowscribe.__version__, 'installed and importable from', shadowscribe.__file__)"

RUN mkdir -p /data/audio /data/raw /data/models /data/memory

VOLUME ["/data"]
EXPOSE 18080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:18080/healthz || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["shadowscribe", "serve"]
