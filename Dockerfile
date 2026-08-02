FROM node:20-alpine AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY VERSION /app/VERSION
COPY frontend/ ./
RUN npm run build

FROM ubuntu:24.04

ARG DEBIAN_FRONTEND=noninteractive
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/flymail-venv/bin:$PATH \
    FLYMAIL_STORAGE_ROOT=/data \
    FLYMAIL_DATA_DIR=/data/flymail \
    FLYMAIL_UI_DIR=/app/frontend-dist \
    APP_HOST=0.0.0.0 \
    APP_PORT=8080 \
    MYSQL_DATABASE=flymail \
    MYSQL_USER=flymail

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        mysql-server \
        python3 \
        python3-pip \
        python3-venv \
        tzdata \
    && python3 -m venv /opt/flymail-venv \
    && rm -rf /var/lib/mysql/* /var/log/mysql/* /var/lib/apt/lists/*

WORKDIR /app/backend

COPY backend/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY backend/ /app/backend/
COPY VERSION /app/VERSION
COPY --from=frontend-builder /app/dist/ui /app/frontend-dist
COPY scripts/docker-entrypoint.sh /usr/local/bin/flymail-entrypoint

RUN chmod +x /usr/local/bin/flymail-entrypoint \
    && mkdir -p /data

VOLUME ["/data"]
EXPOSE 8080
STOPSIGNAL SIGTERM

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=4)" || exit 1

ENTRYPOINT ["/usr/local/bin/flymail-entrypoint"]
