#!/usr/bin/env bash
set -Eeuo pipefail

DATA_ROOT="${FLYMAIL_STORAGE_ROOT:-/data}"
MYSQL_DATA_DIR="${MYSQL_DATA_DIR:-${DATA_ROOT}/mysql}"
FLYMAIL_DATA_DIR="${FLYMAIL_DATA_DIR:-${DATA_ROOT}/flymail}"
MYSQL_DATABASE="${MYSQL_DATABASE:-flymail}"
MYSQL_USER="${MYSQL_USER:-flymail}"
MYSQL_PASSWORD="${MYSQL_PASSWORD:-flymail}"
MYSQL_SOCKET="/run/mysqld/mysqld.sock"
MYSQL_PID_FILE="/run/mysqld/mysqld.pid"
MYSQL_ERROR_LOG="${MYSQL_ERROR_LOG:-${MYSQL_DATA_DIR}/error.log}"
MYSQL_FILES_DIR="${DATA_ROOT}/mysql-files"

if [[ ! "${MYSQL_DATABASE}" =~ ^[A-Za-z0-9_]+$ ]]; then
  echo "MYSQL_DATABASE 只能包含字母、数字和下划线" >&2
  exit 1
fi

if [[ ! "${MYSQL_USER}" =~ ^[A-Za-z0-9_]+$ ]]; then
  echo "MYSQL_USER 只能包含字母、数字和下划线" >&2
  exit 1
fi

export FLYMAIL_DATA_DIR
export MYSQL_DATABASE MYSQL_USER MYSQL_PASSWORD

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

shutdown_services() {
  local exit_code="${1:-0}"
  trap - TERM INT

  if [[ -n "${app_pid:-}" ]] && kill -0 "${app_pid}" 2>/dev/null; then
    kill -TERM "${app_pid}" 2>/dev/null || true
    wait "${app_pid}" 2>/dev/null || true
  fi

  if [[ -n "${mysql_pid:-}" ]] && kill -0 "${mysql_pid}" 2>/dev/null; then
    mysqladmin --protocol=socket --socket="${MYSQL_SOCKET}" -uroot shutdown >/dev/null 2>&1 || \
      kill -TERM "${mysql_pid}" 2>/dev/null || true
    wait "${mysql_pid}" 2>/dev/null || true
  fi

  exit "${exit_code}"
}

mkdir -p "${MYSQL_DATA_DIR}" "${MYSQL_FILES_DIR}" "${FLYMAIL_DATA_DIR}" /run/mysqld
chown -R mysql:mysql "${MYSQL_DATA_DIR}" "${MYSQL_FILES_DIR}" /run/mysqld

if [[ ! -d "${MYSQL_DATA_DIR}/mysql" ]]; then
  echo "首次启动：正在初始化 MySQL 数据目录 ${MYSQL_DATA_DIR}"
  mysqld --initialize-insecure --user=mysql --datadir="${MYSQL_DATA_DIR}"
fi

mysqld \
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
for _ in $(seq 1 60); do
  if mysqladmin --protocol=socket --socket="${MYSQL_SOCKET}" -uroot ping --silent >/dev/null 2>&1; then
    mysql_ready=1
    break
  fi

  if ! kill -0 "${mysql_pid}" 2>/dev/null; then
    echo "MySQL 启动失败" >&2
    tail -n 100 "${MYSQL_ERROR_LOG}" 2>/dev/null || true
    exit 1
  fi

  sleep 1
done

if [[ "${mysql_ready}" != "1" ]]; then
  echo "MySQL 在 60 秒内未就绪" >&2
  tail -n 100 "${MYSQL_ERROR_LOG}" 2>/dev/null || true
  shutdown_services 1
fi

escaped_password="$(sql_escape "${MYSQL_PASSWORD}")"
mysql --protocol=socket --socket="${MYSQL_SOCKET}" -uroot <<SQL
CREATE DATABASE IF NOT EXISTS \`${MYSQL_DATABASE}\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '${MYSQL_USER}'@'127.0.0.1' IDENTIFIED BY '${escaped_password}';
ALTER USER '${MYSQL_USER}'@'127.0.0.1' IDENTIFIED BY '${escaped_password}';
GRANT ALL PRIVILEGES ON \`${MYSQL_DATABASE}\`.* TO '${MYSQL_USER}'@'127.0.0.1';
FLUSH PRIVILEGES;
SQL

echo "MySQL 已就绪，正在启动 FlyMail"
cd /app/backend
python main.py &
app_pid=$!

set +e
wait "${app_pid}"
app_status=$?
set -e

shutdown_services "${app_status}"
