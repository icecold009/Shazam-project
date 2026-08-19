FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=5000 \
    APP_ENV=production \
    TMPDIR=/tmp

RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        ca-certificates \
        curl \
        ffmpeg \
        libasound2 \
        libchromaprint-tools \
        libportaudio2 \
        libsndfile1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid 10001 --home-dir /app --no-create-home app

WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt

COPY gunicorn.conf.py main.py README.md ./
COPY shazam_project ./shazam_project
COPY web ./web
COPY scripts ./scripts

RUN mkdir -p /tmp/audio-recognition \
    && chown -R app:app /app /tmp/audio-recognition

USER 10001:10001

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl --fail --silent http://127.0.0.1:${PORT}/healthz || exit 1

CMD ["gunicorn", "--config", "gunicorn.conf.py", "web.app:app"]
