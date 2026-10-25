#!/usr/bin/env bash
# Builds the new screen (web/, React + Vite) into src/voiney_lab/static/app/,
# which server.py serves at /app/ (lane RX0, human decision 2026-10-24).
#
# Called by the launchers (scripts/run_dev.sh, run_pilot.sh, run_ci_server.sh)
# before the server starts, and by hand. It never stops a launcher: when node
# is missing or the build fails it prints [WARN], removes a stale build so
# /app/ answers "not built" and / stays the current screen, and exits 1 --
# the launcher goes on and starts the server without the new screen.
#
# Dependencies come from web/package-lock.json (npm ci), installed only when
# web/node_modules is missing or older than the lock file.
set -uo pipefail

WEB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$WEB/.." && pwd)"
OUT="$ROOT/src/voiney_lab/static/app"

warn_off() {
  echo "[WARN] $1"
  echo "[WARN] 새 화면(/app/)은 꺼진 채 서버만 시작합니다. 지금 화면(/)은 그대로입니다."
  rm -rf "$OUT"
  return 1
}

if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
  warn_off "node 또는 npm 이 없어 새 화면을 빌드하지 못했습니다 (Node.js 22 이상을 설치하세요)."
  exit 1
fi

cd "$WEB" || { warn_off "web/ 폴더를 열지 못했습니다."; exit 1; }

if [[ ! -d node_modules || package-lock.json -nt node_modules/.package-lock.json ]]; then
  echo "=== Installing the new screen's packages (npm ci) ==="
  if ! npm ci --no-fund --no-audit --loglevel=error; then
    warn_off "npm ci 가 실패해 새 화면을 빌드하지 못했습니다."
    exit 1
  fi
fi

echo "=== Building the new screen (web/ -> src/voiney_lab/static/app/) ==="
if ! npm run --silent build; then
  warn_off "새 화면 빌드가 실패했습니다."
  exit 1
fi
if [[ ! -f "$OUT/index.html" ]]; then
  warn_off "빌드 결과($OUT/index.html)가 없습니다."
  exit 1
fi
echo "[OK] new screen built: $OUT (served at /app/)"
