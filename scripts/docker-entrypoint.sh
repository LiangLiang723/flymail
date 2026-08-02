#!/usr/bin/env bash
set -Eeuo pipefail

DATA_ROOT="${FLYMAIL_STORAGE_ROOT:-/data}"
MYSQL_DATA_DIR="${MYSQL_DATA_DIR:-${DATA_ROOT}/mysql}"
MYSQL_FILES_DIR="${MYSQL_FILES_DIR:-${DATA_ROOT}/mysql-files}"
FLYMAIL_DATA_DIR="${FLYMAIL_DATA_DIR:-${DATA_ROOT}/flymail}"
MYSQL_DATABASE="${MYSQL_DATABASE:-flymail}"
MYSQL_USER="${MYSQL_USER:-flymail}"
MYSQL_PASSWORD="${MYSQL_PASSWORD:-}"
FLYMAIL_SESSION_SECRET="${FLYMAIL_SESSION_SECRET:-}"
APP_HOST="${APP_HOST:-0.0.0.0}"
APP_PORT="${APP_PORT:-8080}"
FLYMAIL_APP_DIR="${FLYMAIL_APP_DIR:-/app/backend}"
MYSQL_READY_TIMEOUT="${FLYMAIL_MYSQL_READY_TIMEOUT:-60}"
WORKER_READY_TIMEOUT="${FLYMAIL_WORKER_READY_TIMEOUT:-60}"
SHUTDOWN_TIMEOUT="${FLYMAIL_SHUTDOWN_TIMEOUT:-20}"
PYTHON_BIN="${FLYMAIL_PYTHON_BIN:-python}"
MYSQLD_BIN="${FLYMAIL_MYSQLD_BIN:-mysqld}"
MYSQL_BIN="${FLYMAIL_MYSQL_BIN:-mysql}"
MYSQLADMIN_BIN="${FLYMAIL_MYSQLADMIN_BIN:-mysqladmin}"

if [[ "${FLYMAIL_ENTRYPOINT_TEST_MODE:-0}" == "1" ]]; then
  MYSQL_RUNTIME_DIR="${FLYMAIL_MYSQL_RUNTIME_DIR:-${DATA_ROOT}/run/mysqld}"
  if [[ "${FLYMAIL_APP_DIR}" == "/app/backend" ]]; then
    FLYMAIL_APP_DIR="${PWD}/backend"
  fi
else
  MYSQL_RUNTIME_DIR="${FLYMAIL_MYSQL_RUNTIME_DIR:-/run/mysqld}"
fi
MYSQL_SOCKET="${MYSQL_SOCKET:-${MYSQL_RUNTIME_DIR}/mysqld.sock}"
MYSQL_PID_FILE="${MYSQL_PID_FILE:-${MYSQL_RUNTIME_DIR}/mysqld.pid}"
MYSQL_ERROR_LOG="${MYSQL_ERROR_LOG:-${MYSQL_DATA_DIR}/error.log}"

require_integer() {
  local name="$1"
  local value="$2"
  if [[ ! "${value}" =~ ^[0-9]+$ ]] || (( value < 1 )); then
    echo "${name} 必须是正整数" >&2
    exit 1
  fi
}

if [[ ! "${MYSQL_DATABASE}" =~ ^[A-Za-z0-9_]+$ ]]; then
  echo "MYSQL_DATABASE 只能包含字母、数字和下划线" >&2
  exit 1
fi
if [[ ! "${MYSQL_USER}" =~ ^[A-Za-z0-9_]+$ ]]; then
  echo "MYSQL_USER 只能包含字母、数字和下划线" >&2
  exit 1
fi
if [[ -z "${MYSQL_PASSWORD}" ]]; then
  echo "MYSQL_PASSWORD 不能为空" >&2
  exit 1
fi
if [[ "${MYSQL_PASSWORD}" == *$'\n'* || "${MYSQL_PASSWORD}" == *$'\r'* ]]; then
  echo "MYSQL_PASSWORD 不能包含换行符" >&2
  exit 1
fi
if (( ${#FLYMAIL_SESSION_SECRET} < 16 )); then
  echo "FLYMAIL_SESSION_SECRET 必须至少 16 个字符" >&2
  exit 1
fi
if [[ ! "${APP_PORT}" =~ ^[0-9]+$ ]] || (( APP_PORT < 1 || APP_PORT > 65535 )); then
  echo "APP_PORT 必须是 1 到 65535 的端口" >&2
  exit 1
fi
require_integer "FLYMAIL_MYSQL_READY_TIMEOUT" "${MYSQL_READY_TIMEOUT}"
require_integer "FLYMAIL_WORKER_READY_TIMEOUT" "${WORKER_READY_TIMEOUT}"
require_integer "FLYMAIL_SHUTDOWN_TIMEOUT" "${SHUTDOWN_TIMEOUT}"

export FLYMAIL_DATA_DIR FLYMAIL_SESSION_SECRET
export MYSQL_DATABASE MYSQL_USER MYSQL_PASSWORD MYSQL_DATA_DIR MYSQL_FILES_DIR
export APP_HOST APP_PORT

if [[ -z "${DATABASE_URL:-}" ]]; then
  DATABASE_URL="$(python3 -c 'import os; from urllib.parse import quote; print("mysql://{}:{}@127.0.0.1:3306/{}?charset=utf8mb4".format(quote(os.environ["MYSQL_USER"], safe=""), quote(os.environ["MYSQL_PASSWORD"], safe=""), quote(os.environ["MYSQL_DATABASE"], safe="")))')"
  export DATABASE_URL
fi

sql_escape() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//\'/\'\'}"
  printf '%s' "${value}"
}

wait_for_pid_exit() {
  local pid="$1"
  local timeout_seconds="$2"
  local attempts=$((timeout_seconds * 10))
  local attempt
  for ((attempt = 0; attempt < attempts; attempt += 1)); do
    if ! kill -0 "${pid}" 2>/dev/null; then
      return 0
    fi
    sleep 0.1
  done
  return 1
}

stop_process() {
  local pid="${1:-}"
  if [[ -z "${pid}" ]] || ! kill -0 "${pid}" 2>/dev/null; then
    return 0
  fi
  kill -TERM "${pid}" 2>/dev/null || true
  if ! wait_for_pid_exit "${pid}" "${SHUTDOWN_TIMEOUT}"; then
    kill -KILL "${pid}" 2>/dev/null || true
  fi
  wait "${pid}" 2>/dev/null || true
}

shutting_down=0
shutdown_services() {
  local exit_code="${1:-0}"
  if [[ "${shutting_down}" == "1" ]]; then
    exit "${exit_code}"
  fi
  shutting_down=1
  trap - TERM INT

  stop_process "${api_pid:-}"
  stop_process "${worker_pid:-}"

  if [[ -n "${mysql_pid:-}" ]] && kill -0 "${mysql_pid}" 2>/dev/null; then
    "${MYSQLADMIN_BIN}" --protocol=socket --socket="${MYSQL_SOCKET}" -uroot shutdown >/dev/null 2>&1 || \
      kill -TERM "${mysql_pid}" 2>/dev/null || true
    if ! wait_for_pid_exit "${mysql_pid}" "${SHUTDOWN_TIMEOUT}"; then
      kill -KILL "${mysql_pid}" 2>/dev/null || true
    fi
    wait "${mysql_pid}" 2>/dev/null || true
  fi

  exit "${exit_code}"
}

mkdir -p \
  "${MYSQL_DATA_DIR}" \
  "${MYSQL_FILES_DIR}" \
  "${FLYMAIL_DATA_DIR}/config" \
  "${FLYMAIL_DATA_DIR}/files/uploads" \
  "${FLYMAIL_DATA_DIR}/files/download" \
  "${FLYMAIL_DATA_DIR}/logs" \
  "${MYSQL_RUNTIME_DIR}"

# Bind-mounted data roots are commonly created with mode 0700. MySQL runs as
# its own user and needs traverse permission on the mount root, but not list or
# read permission for unrelated entries.
chmod o+x "${DATA_ROOT}"

if [[ "${FLYMAIL_ENTRYPOINT_TEST_MODE:-0}" != "1" ]]; then
  chown -R mysql:mysql "${MYSQL_DATA_DIR}" "${MYSQL_FILES_DIR}" "${MYSQL_RUNTIME_DIR}"
fi

if [[ ! -d "${MYSQL_DATA_DIR}/mysql" ]]; then
  echo "首次启动：正在初始化 MySQL 数据目录"
  "${MYSQLD_BIN}" --initialize-insecure --user=mysql --datadir="${MYSQL_DATA_DIR}"
fi

"${MYSQLD_BIN}" \
  --user=mysql \
  --datadir="${MYSQL_DATA_DIR}" \
  --socket="${MYSQL_SOCKET}" \
  --pid-file="${MYSQL_PID_FILE}" \
  --bind-address=127.0.0.1 \
  --port=3306 \
  --secure-file-priv="${MYSQL_FILES_DIR}" \
  --log-error="${MYSQL_ERROR_LOG}" \
  --skip-log-bin \
  --skip-name-resolve &
mysql_pid=$!

trap 'shutdown_services 143' TERM
trap 'shutdown_services 130' INT

mysql_ready=0
for ((attempt = 0; attempt < MYSQL_READY_TIMEOUT; attempt += 1)); do
  if "${MYSQLADMIN_BIN}" --protocol=socket --socket="${MYSQL_SOCKET}" -uroot ping --silent >/dev/null 2>&1; then
    mysql_ready=1
    break
  fi
  if ! kill -0 "${mysql_pid}" 2>/dev/null; then
    set +e
    wait "${mysql_pid}"
    mysql_status=$?
    set -e
    echo "MySQL 启动失败" >&2
    tail -n 100 "${MYSQL_ERROR_LOG}" 2>/dev/null || true
    shutdown_services "${mysql_status:-1}"
  fi
  sleep 1
done

if [[ "${mysql_ready}" != "1" ]]; then
  echo "MySQL 未在启动时限内就绪" >&2
  tail -n 100 "${MYSQL_ERROR_LOG}" 2>/dev/null || true
  shutdown_services 1
fi

escaped_password="$(sql_escape "${MYSQL_PASSWORD}")"
"${MYSQL_BIN}" --protocol=socket --socket="${MYSQL_SOCKET}" -uroot <<SQL
CREATE DATABASE IF NOT EXISTS \`${MYSQL_DATABASE}\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '${MYSQL_USER}'@'127.0.0.1' IDENTIFIED BY '${escaped_password}';
ALTER USER '${MYSQL_USER}'@'127.0.0.1' IDENTIFIED BY '${escaped_password}';
GRANT ALL PRIVILEGES ON \`${MYSQL_DATABASE}\`.* TO '${MYSQL_USER}'@'127.0.0.1';
FLUSH PRIVILEGES;
SQL
unset escaped_password

cd "${FLYMAIL_APP_DIR}"
set +e
"${PYTHON_BIN}" migrate.py
migration_status=$?
set -e
if [[ "${migration_status}" != "0" ]]; then
  echo "数据库迁移失败" >&2
  shutdown_services "${migration_status}"
fi

echo "数据库迁移完成，正在启动 Worker"
"${PYTHON_BIN}" worker.py &
worker_pid=$!

wait_for_worker_heartbeat() {
  local heartbeat_count
  local attempt
  for ((attempt = 0; attempt < WORKER_READY_TIMEOUT; attempt += 1)); do
    if ! kill -0 "${worker_pid}" 2>/dev/null; then
      return 2
    fi
    heartbeat_count="$(
      "${MYSQL_BIN}" --protocol=socket --socket="${MYSQL_SOCKET}" -uroot -Nse \
        "SELECT COUNT(*) FROM \`${MYSQL_DATABASE}\`.process_heartbeats WHERE role='worker' AND heartbeat_at >= UNIX_TIMESTAMP() - 30" \
        2>/dev/null || true
    )"
    if [[ "${heartbeat_count}" =~ ^[1-9][0-9]*$ ]]; then
      return 0
    fi
    sleep 1
  done
  return 1
}

set +e
wait_for_worker_heartbeat
worker_ready_status=$?
set -e
if [[ "${worker_ready_status}" == "2" ]]; then
  set +e
  wait "${worker_pid}"
  worker_status=$?
  set -e
  echo "Worker 在就绪前退出" >&2
  shutdown_services "${worker_status:-1}"
fi
if [[ "${worker_ready_status}" != "0" ]]; then
  echo "Worker 心跳未在启动时限内就绪" >&2
  shutdown_services 1
fi

echo "Worker 已就绪，正在启动 API"
"${PYTHON_BIN}" -m uvicorn main:app --host "${APP_HOST}" --port "${APP_PORT}" --log-level warning &
api_pid=$!

set +e
failed_pid=""
wait -n -p failed_pid "${mysql_pid}" "${worker_pid}" "${api_pid}"
process_status=$?
set -e

case "${failed_pid}" in
  "${mysql_pid}") echo "MySQL 进程意外退出" >&2 ;;
  "${worker_pid}") echo "Worker 进程意外退出" >&2 ;;
  "${api_pid}") echo "API 进程意外退出" >&2 ;;
  *) echo "关键进程意外退出" >&2 ;;
esac

shutdown_services "${process_status}"
