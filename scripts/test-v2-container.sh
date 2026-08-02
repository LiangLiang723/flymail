#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "$#" -lt 1 || "$#" -gt 2 ]]; then
  echo "用法: $0 <image-tag> [temporary-data-dir]" >&2
  exit 2
fi

IMAGE="$1"
REQUESTED_DATA_DIR="${2:-}"
PRODUCTION_DATA_DIR="/Docker/flymail/data"
CREATED_DATA_DIR=0
CONTAINER_NAME="${FLYMAIL_SMOKE_CONTAINER_NAME:-flymail-v2-smoke-$(date +%s)-$$}"
KEEP_CONTAINER="${FLYMAIL_SMOKE_KEEP_CONTAINER:-0}"
KEEP_DATA="${FLYMAIL_SMOKE_KEEP_DATA:-0}"
REPORT_FILE=""

canonical_path() {
  python3 - "$1" <<'PY'
import os
import sys
print(os.path.realpath(sys.argv[1]))
PY
}

if [[ -n "${REQUESTED_DATA_DIR}" ]]; then
  DATA_DIR="$(canonical_path "${REQUESTED_DATA_DIR}")"
  production_path="$(canonical_path "${PRODUCTION_DATA_DIR}")"
  case "${DATA_DIR}" in
    "${production_path}"|"${production_path}"/*)
      echo "拒绝使用生产数据目录 ${PRODUCTION_DATA_DIR}" >&2
      exit 2
      ;;
  esac
  mkdir -p "${DATA_DIR}"
else
  DATA_DIR="$(mktemp -d /tmp/flymail-v2-smoke-data.XXXXXX)"
  CREATED_DATA_DIR=1
fi
REPORT_FILE="${FLYMAIL_SMOKE_REPORT_FILE:-${DATA_DIR}/smoke-report.txt}"
mkdir -p "$(dirname "${REPORT_FILE}")"

cleanup() {
  local status=$?
  trap - EXIT INT TERM
  if [[ "${KEEP_CONTAINER}" != "1" ]]; then
    docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true
  fi
  if [[ "${CREATED_DATA_DIR}" == "1" && "${KEEP_DATA}" != "1" ]]; then
    rm -rf -- "${DATA_DIR}"
  fi
  exit "${status}"
}
trap cleanup EXIT INT TERM

if ! docker image inspect "${IMAGE}" >/dev/null 2>&1; then
  echo "镜像不存在: ${IMAGE}" >&2
  exit 2
fi

SESSION_SECRET="${FLYMAIL_SMOKE_SESSION_SECRET:-$(openssl rand -hex 32)}"
if [[ -n "${FLYMAIL_SMOKE_MYSQL_PASSWORD:-}" ]]; then
  MYSQL_PASSWORD="${FLYMAIL_SMOKE_MYSQL_PASSWORD}"
else
  RANDOM_SUFFIX="$(openssl rand -hex 16)"
  printf -v MYSQL_PASSWORD "Q'\\\\@:/%%%s" "${RANDOM_SUFFIX}"
  unset RANDOM_SUFFIX
fi

HOST_PORT="$(python3 - <<'PY'
import socket
with socket.socket() as sock:
    sock.bind(('127.0.0.1', 0))
    print(sock.getsockname()[1])
PY
)"
MARKER="smoke-$(date +%s)-$$"
EXPECTED_VERSION="$(tr -d '[:space:]' < VERSION)"

wait_for_health() {
  local deadline=$((SECONDS + 120))
  local state health
  while (( SECONDS < deadline )); do
    state="$(docker inspect --format '{{.State.Status}}' "${CONTAINER_NAME}" 2>/dev/null || true)"
    health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "${CONTAINER_NAME}" 2>/dev/null || true)"
    if [[ "${state}" == "running" && "${health}" == "healthy" ]]; then
      return 0
    fi
    if [[ "${state}" == "exited" || "${state}" == "dead" ]]; then
      docker logs "${CONTAINER_NAME}" >&2 || true
      return 1
    fi
    sleep 1
  done
  docker logs "${CONTAINER_NAME}" >&2 || true
  return 1
}

health_version() {
  python3 - "${HOST_PORT}" <<'PY'
import json
import sys
import urllib.request
with urllib.request.urlopen(f"http://127.0.0.1:{sys.argv[1]}/api/health", timeout=5) as response:
    payload = json.load(response)
if payload.get("status") != "ok":
    raise SystemExit(f"unexpected health status: {payload.get('status')!r}")
print(payload.get("version", ""))
PY
}

mysql_scalar() {
  local query="$1"
  docker exec "${CONTAINER_NAME}" \
    mysql --protocol=socket --socket=/run/mysqld/mysqld.sock -uroot -Nse "${query}"
}

verify_lease_recovery() {
  docker exec -i "${CONTAINER_NAME}" python - "${MARKER}" <<'PY'
import asyncio
import json
import os
import sys
import time
from urllib.parse import quote

from flymail.config import FlyMailSettings
from flymail.infrastructure.db.pool import DatabasePool
from flymail.repositories.jobs import JobRepository


async def main() -> None:
    marker = sys.argv[1]
    job_id = f"job_{marker}".replace("-", "_")[:64]
    now = time.time()
    os.environ["DATABASE_URL"] = (
        "mysql://{}:{}@127.0.0.1:3306/{}?charset=utf8mb4".format(
            quote(os.environ["MYSQL_USER"], safe=""),
            quote(os.environ["MYSQL_PASSWORD"], safe=""),
            quote(os.environ["MYSQL_DATABASE"], safe=""),
        )
    )
    pool = await DatabasePool.create(FlyMailSettings.from_env("worker"))
    try:
        async with pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("DELETE FROM job_attempts WHERE job_id=%s", (job_id,))
                await cursor.execute("DELETE FROM worker_jobs WHERE id=%s", (job_id,))
                await cursor.execute(
                    """
                    INSERT INTO worker_jobs (
                        id, queue_name, job_kind, status, priority, available_at,
                        lease_owner, lease_token, lease_expires_at, heartbeat_at,
                        attempt_count, max_attempts, dedupe_key, payload,
                        created_at, updated_at
                    ) VALUES (%s, 'maintenance', 'cache.cleanup', 'leased', 100, %s,
                              'smoke-worker', %s, %s, %s, 1, 3, %s, %s, %s, %s)
                    """,
                    (
                        job_id,
                        now - 60,
                        f"lease_{job_id}"[:64],
                        now - 30,
                        now - 40,
                        f"smoke:{marker}",
                        json.dumps({"marker": marker}),
                        now - 120,
                        now - 40,
                    ),
                )
            await connection.commit()
        async with pool.acquire() as connection:
            await connection.begin()
            released = await JobRepository(connection).release_expired_leases(now=now)
            await connection.commit()
        async with pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    "SELECT status, lease_owner, lease_token, last_error_class FROM worker_jobs WHERE id=%s",
                    (job_id,),
                )
                row = await cursor.fetchone()
        if released != 1 or row != ("retry_wait", "", None, "LeaseExpired"):
            raise SystemExit(f"unexpected lease recovery result: released={released}, row={row!r}")
    finally:
        await pool.close()


asyncio.run(main())
PY
}

assert_runtime() {
  local version mysql_version mysql_data_dir bind_address heartbeat_age
  version="$(health_version)"
  [[ "${version}" == "${EXPECTED_VERSION}" ]] || {
    echo "健康接口版本不匹配: ${version}" >&2
    return 1
  }

  mysql_version="$(mysql_scalar 'SELECT VERSION()')"
  [[ "${mysql_version}" == 8.0.* ]] || {
    echo "MySQL 版本不是 8.0: ${mysql_version}" >&2
    return 1
  }
  mysql_data_dir="$(mysql_scalar 'SELECT @@datadir')"
  [[ "${mysql_data_dir}" == "/data/mysql/" ]] || {
    echo "MySQL 数据目录不正确: ${mysql_data_dir}" >&2
    return 1
  }
  bind_address="$(mysql_scalar 'SELECT @@bind_address')"
  [[ "${bind_address}" == "127.0.0.1" ]] || {
    echo "MySQL 绑定地址不正确: ${bind_address}" >&2
    return 1
  }
  heartbeat_age="$(mysql_scalar "SELECT FLOOR(UNIX_TIMESTAMP() - MAX(heartbeat_at)) FROM flymail.process_heartbeats WHERE role='worker'")"
  [[ "${heartbeat_age}" =~ ^[0-9]+$ ]] && (( heartbeat_age <= 30 )) || {
    echo "Worker 心跳不新鲜: ${heartbeat_age}" >&2
    return 1
  }

  docker exec "${CONTAINER_NAME}" test -d /data/flymail/config
  docker exec "${CONTAINER_NAME}" test -d /data/flymail/logs
  docker exec "${CONTAINER_NAME}" test -d /data/flymail/objects/sha256
}

echo "启动隔离 FlyMail V2 容器"
docker run -d \
  --name "${CONTAINER_NAME}" \
  --publish "127.0.0.1:${HOST_PORT}:8080" \
  --volume "${DATA_DIR}:/data" \
  --env APP_HOST=0.0.0.0 \
  --env APP_PORT=8080 \
  --env FLYMAIL_STORAGE_ROOT=/data \
  --env FLYMAIL_DATA_DIR=/data/flymail \
  --env FLYMAIL_SESSION_SECRET="${SESSION_SECRET}" \
  --env MYSQL_DATABASE=flymail \
  --env MYSQL_USER=flymail \
  --env MYSQL_PASSWORD="${MYSQL_PASSWORD}" \
  "${IMAGE}" >/dev/null

wait_for_health
assert_runtime

mysql_scalar "CREATE TABLE IF NOT EXISTS flymail.smoke_persistence (marker VARCHAR(128) PRIMARY KEY, created_at DOUBLE NOT NULL)"
mysql_scalar "INSERT INTO flymail.smoke_persistence (marker, created_at) VALUES ('${MARKER}', UNIX_TIMESTAMP())"
docker exec "${CONTAINER_NAME}" sh -c \
  "mkdir -p /data/flymail/objects/sha256/smoke && printf '%s' '${MARKER}' > /data/flymail/objects/sha256/smoke/marker"

echo "重启隔离容器并验证持久化"
docker restart --time 30 "${CONTAINER_NAME}" >/dev/null
wait_for_health
assert_runtime
[[ "$(mysql_scalar "SELECT COUNT(*) FROM flymail.smoke_persistence WHERE marker='${MARKER}'")" == "1" ]]
[[ "$(docker exec "${CONTAINER_NAME}" cat /data/flymail/objects/sha256/smoke/marker)" == "${MARKER}" ]]
verify_lease_recovery

echo "停止隔离容器并验证 MySQL 安全关闭"
docker stop --time 30 "${CONTAINER_NAME}" >/dev/null
if ! grep -Eiq 'shutdown complete|shutdown.*completed' "${DATA_DIR}/mysql/error.log"; then
  echo "MySQL 日志未发现安全关闭记录" >&2
  tail -n 100 "${DATA_DIR}/mysql/error.log" >&2 || true
  exit 1
fi

cat >"${REPORT_FILE}" <<EOF
image=${IMAGE}
version=${EXPECTED_VERSION}
container_name=${CONTAINER_NAME}
data_dir=${DATA_DIR}
container_health=passed
mysql_version=8.0
mysql_data_dir=/data/mysql/
mysql_bind_address=127.0.0.1
worker_heartbeat=passed
restart_persistence=passed
lease_recovery=passed
mysql_shutdown=passed
production_data_touched=no
EOF

echo "FlyMail V2 隔离容器验证通过"
