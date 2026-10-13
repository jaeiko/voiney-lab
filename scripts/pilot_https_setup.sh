#!/usr/bin/env bash
# Voiney Lab 파일럿: 파일럿 서버 앞에 HTTPS 와 접속 비밀번호를 둔다 (Caddy).
#
# 휴대폰 브라우저는 HTTPS(또는 localhost)에서만 마이크를 쓴다. 파일럿 서버
# (scripts/run_pilot.sh)는 127.0.0.1:8080 에서만 듣고, 이 스크립트가 만드는
# Caddy 가 도메인의 443 에서 받아 그쪽으로 넘긴다.
#
# 줄 PL 은 이 스크립트를 실행하지 않았다(사람이 도메인을 정한 뒤 실행한다).
# 순서와 확인 방법: docs/PILOT_HTTPS_SETUP.md
#
#   sudo scripts/pilot_https_setup.sh --domain pilot.example.org
#   scripts/pilot_https_setup.sh --domain pilot.example.org --dry-run
#
# 비밀번호는 인자로 받지 않는다(ps 에 보인다). 물어서 받고, Caddy 가 bcrypt 해시로
# 바꾼다. 이미 만든 해시가 있으면 --password-hash 로 준다. 비밀번호도 해시도
# 저장소에 넣지 않는다.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE="${PILOT_HTTPS_TEMPLATE:-$ROOT/scripts/pilot_https/Caddyfile.example}"
CADDYFILE="${PILOT_HTTPS_CADDYFILE:-/etc/caddy/Caddyfile}"

DOMAIN=""
USER_NAME="pilot"
UPSTREAM="127.0.0.1:8080"
PASSWORD_HASH=""
DRY_RUN=false

usage() {
  cat >&2 <<'EOF'
usage: pilot_https_setup.sh --domain DOMAIN [--user NAME] [--upstream HOST:PORT]
                            [--password-hash BCRYPT_HASH] [--dry-run]

  --domain         파일럿 도메인 (필수). DNS A 레코드가 이 서버를 가리켜야 한다.
  --user           접속 아이디 (기본 pilot)
  --upstream       파일럿 서버 주소 (기본 127.0.0.1:8080)
  --password-hash  `caddy hash-password` 로 만든 bcrypt 해시. 없으면 비밀번호를 묻는다.
  --dry-run        아무것도 설치·변경하지 않고 만들 설정과 할 일만 보여 준다.
EOF
  exit 2
}

fail() { echo "[ERROR] $*" >&2; exit 1; }

while [[ "$#" -ne 0 ]]; do
  case "$1" in
    --domain) [[ "$#" -ge 2 ]] || usage; DOMAIN="$2"; shift 2 ;;
    --user) [[ "$#" -ge 2 ]] || usage; USER_NAME="$2"; shift 2 ;;
    --upstream) [[ "$#" -ge 2 ]] || usage; UPSTREAM="$2"; shift 2 ;;
    --password-hash) [[ "$#" -ge 2 ]] || usage; PASSWORD_HASH="$2"; shift 2 ;;
    --dry-run) DRY_RUN=true; shift ;;
    -h|--help) usage ;;
    *) usage ;;
  esac
done

[[ -n "$DOMAIN" ]] || usage
DOMAIN="$(printf '%s' "$DOMAIN" | tr 'A-Z' 'a-z')"

# 값 검사: Caddyfile 에 그대로 들어가므로 모양이 맞는 것만 받는다.
[[ "$DOMAIN" =~ ^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$ ]] \
  || fail "--domain 은 pilot.example.org 같은 도메인이어야 합니다: $DOMAIN"
if [[ "$DRY_RUN" != "true" && ( "$DOMAIN" == *example.org || "$DOMAIN" == *example.com || "$DOMAIN" == *.invalid ) ]]; then
  fail "--domain 에 예시 도메인($DOMAIN)을 쓸 수 없습니다. 실제 도메인을 정한 뒤 실행하세요."
fi
[[ "$USER_NAME" =~ ^[A-Za-z0-9._-]{1,32}$ ]] \
  || fail "--user 는 영문·숫자·._- 1~32자여야 합니다."
[[ "$UPSTREAM" =~ ^[A-Za-z0-9.-]+:[0-9]{1,5}$ ]] \
  || fail "--upstream 은 HOST:PORT 여야 합니다: $UPSTREAM"
if [[ -n "$PASSWORD_HASH" ]]; then
  [[ "$PASSWORD_HASH" =~ ^\$2[aby]\$[0-9]{2}\$[./A-Za-z0-9]{53}$ ]] \
    || fail "--password-hash 가 bcrypt 해시(\$2a\$... 꼴) 모양이 아닙니다."
fi
[[ -f "$TEMPLATE" ]] || fail "설정 예시 파일이 없습니다: $TEMPLATE"

render() {
  local hash="$1" text
  text="$(<"$TEMPLATE")"
  text="${text//\{\{DOMAIN\}\}/$DOMAIN}"
  text="${text//\{\{USER\}\}/$USER_NAME}"
  text="${text//\{\{UPSTREAM\}\}/$UPSTREAM}"
  text="${text//\{\{PASSWORD_HASH\}\}/$hash}"
  printf '%s\n' "$text"
}

if [[ "$DRY_RUN" == "true" ]]; then
  echo "=== --dry-run: 아무것도 설치하거나 바꾸지 않습니다 ==="
  echo "도메인   = $DOMAIN"
  echo "아이디   = $USER_NAME"
  echo "넘길 곳  = $UPSTREAM"
  echo "설정 파일 = $CADDYFILE"
  echo
  echo "할 일 (sudo 로 실행할 때):"
  echo "  1. caddy 가 없으면 Caddy 공식 apt 저장소로 설치"
  if [[ -n "$PASSWORD_HASH" ]]; then
    echo "  2. 주어진 해시를 쓴다"
  else
    echo "  2. 비밀번호를 두 번 묻고 caddy hash-password 로 해시를 만든다"
  fi
  echo "  3. 기존 $CADDYFILE 이 있으면 .bak-날짜 로 복사해 두고 아래 내용으로 쓴다"
  echo "  4. caddy validate 로 확인한 뒤 systemctl reload caddy"
  echo
  echo "--- 만들 Caddyfile (해시는 가린다) ---"
  render '<bcrypt-hash>'
  exit 0
fi

[[ "$(id -u)" -eq 0 ]] || fail "root 권한이 필요합니다: sudo $0 --domain $DOMAIN"

if ! command -v caddy >/dev/null 2>&1; then
  echo "[INFO] caddy 가 없어 공식 apt 저장소로 설치합니다."
  command -v apt-get >/dev/null 2>&1 || fail "apt-get 이 없는 시스템입니다. caddy 를 직접 설치한 뒤 다시 실행하세요."
  apt-get update
  apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update
  apt-get install -y caddy
fi

if [[ -z "$PASSWORD_HASH" ]]; then
  [[ -t 0 ]] || fail "비밀번호를 물을 수 있는 터미널이 아닙니다. --password-hash 로 해시를 주세요."
  read -rsp "접속 비밀번호 (12자 이상): " PASSWORD; echo
  read -rsp "한 번 더: " CONFIRM; echo
  [[ "$PASSWORD" == "$CONFIRM" ]] || fail "두 비밀번호가 다릅니다."
  [[ "${#PASSWORD}" -ge 12 ]] || fail "비밀번호는 12자 이상이어야 합니다."
  PASSWORD_HASH="$(printf '%s\n' "$PASSWORD" | caddy hash-password)" \
    || fail "caddy hash-password 가 해시를 만들지 못했습니다. 다른 곳에서 만든 해시를 --password-hash 로 주세요."
  unset PASSWORD CONFIRM
  [[ "$PASSWORD_HASH" =~ ^\$2[aby]\$[0-9]{2}\$[./A-Za-z0-9]{53}$ ]] \
    || fail "caddy hash-password 의 출력이 bcrypt 해시 모양이 아닙니다."
fi

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP=""
if [[ -f "$CADDYFILE" ]]; then
  BACKUP="$CADDYFILE.bak-$STAMP"
  cp -p "$CADDYFILE" "$BACKUP"
  echo "[INFO] 기존 설정을 $BACKUP 로 복사했습니다."
fi

mkdir -p "$(dirname "$CADDYFILE")"
umask 027
render "$PASSWORD_HASH" > "$CADDYFILE"
if getent group caddy >/dev/null 2>&1; then chgrp caddy "$CADDYFILE"; fi

if ! caddy validate --config "$CADDYFILE" --adapter caddyfile; then
  if [[ -n "$BACKUP" ]]; then cp -p "$BACKUP" "$CADDYFILE"; fi
  fail "caddy validate 가 실패했습니다. 설정을 되돌렸습니다."
fi

systemctl reload caddy 2>/dev/null || systemctl restart caddy
echo
echo "[OK] https://$DOMAIN 설정을 적용했습니다."
echo "다음: 방화벽·보안 그룹에서 80/443 열기, 파일럿 서버 시작(scripts/run_pilot.sh),"
echo "      docs/PILOT_HTTPS_SETUP.md 4절의 확인(HTTPS, 웹소켓 /ws, 휴대폰 마이크)."
