FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY clara-instituto-saudavelmente-configuracao-sdr.json ./
COPY src ./src

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg passwd \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip install --no-cache-dir . \
    && groupadd --gid 10001 app \
    && useradd \
        --uid 10001 \
        --gid app \
        --create-home \
        --home-dir /home/app \
        --shell /usr/sbin/nologin \
        app \
    && mkdir -p /app/.runtime/generated-audio \
    && chown -R app:app /app /home/app

USER 10001:10001

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=3).read()"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
