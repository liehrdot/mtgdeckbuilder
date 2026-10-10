# Sync-Server des MTG-Deckbuilders (nur der Server-Teil: FastAPI + SQLite, keine Karten-/KI-Abhängigkeiten).
# Bauen aus dem Repo-Wurzelordner:  docker build -f deploy/sync/Dockerfile -t mtgdeck-sync .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MTG_SYNC_DATA=/data MTG_SYNC_HOST=0.0.0.0 MTG_SYNC_PORT=8080

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
# the package without its app dependencies (MCP, Pillow …): the server needs only FastAPI and uvicorn
RUN pip install --no-deps . && pip install "fastapi>=0.115,<1" "uvicorn>=0.30,<1" && rm -rf /app/src \
    && useradd --uid 1000 --no-create-home --shell /usr/sbin/nologin mtgsync && mkdir /data && chown mtgsync /data

USER mtgsync
VOLUME /data
EXPOSE 8080
HEALTHCHECK --interval=60s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=4)"
CMD ["mtg-sync-server"]
