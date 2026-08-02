#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "$#" -ne 1 ]]; then
  echo "用法: $0 <image-tag>" >&2
  exit 2
fi

IMAGE="$1"
ROOT="$(mktemp -d /tmp/flymail-v2-secret-scan.XXXXXX)"
DATA_DIR="${ROOT}/data"
CONTAINER_NAME="flymail-v2-secret-scan-$(date +%s)-$$"
SMOKE_REPORT="${ROOT}/smoke-report.txt"
SCAN_REPORT="${ROOT}/secret-scan-report.txt"
SESSION_SECRET="scan-session-$(openssl rand -hex 32)"
ADMIN_PASSWORD="ScanAdmin-$(openssl rand -hex 24)"
PASSWORD_SUFFIX="$(openssl rand -hex 16)"
printf -v MYSQL_PASSWORD "Scan'\\\\@:/%%%s" "${PASSWORD_SUFFIX}"
unset PASSWORD_SUFFIX

cleanup() {
  local status=$?
  trap - EXIT INT TERM
  docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true
  rm -rf -- "${ROOT}"
  exit "${status}"
}
trap cleanup EXIT INT TERM

mkdir -p "${DATA_DIR}"

FLYMAIL_SMOKE_CONTAINER_NAME="${CONTAINER_NAME}" \
FLYMAIL_SMOKE_SESSION_SECRET="${SESSION_SECRET}" \
FLYMAIL_SMOKE_ADMIN_PASSWORD="${ADMIN_PASSWORD}" \
FLYMAIL_SMOKE_MYSQL_PASSWORD="${MYSQL_PASSWORD}" \
FLYMAIL_SMOKE_KEEP_CONTAINER=1 \
FLYMAIL_SMOKE_KEEP_DATA=1 \
FLYMAIL_SMOKE_REPORT_FILE="${SMOKE_REPORT}" \
  scripts/test-v2-container.sh "${IMAGE}" "${DATA_DIR}"

docker image inspect "${IMAGE}" >"${ROOT}/image-inspect.json"
docker history --no-trunc "${IMAGE}" >"${ROOT}/image-history.txt"
docker logs "${CONTAINER_NAME}" >"${ROOT}/container.log" 2>&1 || true
docker inspect --format '{{json .HostConfig.PortBindings}}' "${CONTAINER_NAME}" >"${ROOT}/port-bindings.json"
docker inspect --format '{{json .Config.ExposedPorts}}' "${CONTAINER_NAME}" >"${ROOT}/exposed-ports.json"
find "${DATA_DIR}/flymail/logs" -type f -maxdepth 2 -print0 2>/dev/null \
  | sort -z \
  | xargs -0 -r cat >"${ROOT}/application.log"
git diff --no-ext-diff >"${ROOT}/git-diff.txt"
git diff --cached --no-ext-diff >"${ROOT}/git-diff-staged.txt"
docker compose --env-file .env.example config >"${ROOT}/compose-config.yml"

scan_exact() {
  local secret="$1"
  shift
  local file
  for file in "$@"; do
    if grep -Fq -- "${secret}" "${file}"; then
      echo "检测到测试秘密泄露: $(basename "${file}")" >&2
      return 1
    fi
  done
}

ALL_SCAN_FILES=(
  "${ROOT}/image-inspect.json"
  "${ROOT}/image-history.txt"
  "${ROOT}/container.log"
  "${ROOT}/application.log"
  "${ROOT}/git-diff.txt"
  "${ROOT}/git-diff-staged.txt"
  "${ROOT}/compose-config.yml"
)
RUNTIME_SCAN_FILES=(
  "${ROOT}/image-inspect.json"
  "${ROOT}/image-history.txt"
  "${ROOT}/container.log"
  "${ROOT}/application.log"
)

scan_exact "${SESSION_SECRET}" "${ALL_SCAN_FILES[@]}"
scan_exact "${ADMIN_PASSWORD}" "${ALL_SCAN_FILES[@]}"
scan_exact "${MYSQL_PASSWORD}" "${ALL_SCAN_FILES[@]}"

if grep -Eiq 'mysql([+][A-Za-z0-9_]+)?://[^[:space:]@]+:[^*[:space:]@][^[:space:]@]*@' "${RUNTIME_SCAN_FILES[@]}"; then
  echo "检测到未脱敏数据库连接地址" >&2
  exit 1
fi
if grep -Eiq 'authorization[[:space:]]*:[[:space:]]*(bearer|basic)[[:space:]]+[A-Za-z0-9._~+/=-]+' "${RUNTIME_SCAN_FILES[@]}"; then
  echo "检测到 Authorization 凭证" >&2
  exit 1
fi
if grep -Eiq 'FLYMAIL_SESSION_SECRET[=:][^[:space:]]{16,}' "${RUNTIME_SCAN_FILES[@]}"; then
  echo "检测到会话签名密钥值" >&2
  exit 1
fi

python3 - "${ROOT}/port-bindings.json" "${ROOT}/exposed-ports.json" <<'PY'
import json
import sys

for path in sys.argv[1:]:
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle) or {}
    unexpected = sorted(key for key in payload if key != "8080/tcp")
    if unexpected:
        raise SystemExit(f"unexpected published or exposed ports in {path}: {unexpected}")
PY

if grep -qE '(^|:)3306:' "${ROOT}/compose-config.yml"; then
  echo "Compose 意外发布 MySQL 端口" >&2
  exit 1
fi

cat >"${SCAN_REPORT}" <<EOF
image=${IMAGE}
smoke=passed
exact_secret_scan=passed
database_url_scan=passed
authorization_scan=passed
session_secret_scan=passed
published_ports=8080/tcp-only
production_data_touched=no
EOF

cat "${SMOKE_REPORT}"
cat "${SCAN_REPORT}"
echo "FlyMail V2 秘密扫描通过"
