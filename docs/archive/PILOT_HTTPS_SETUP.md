> 보관 문서입니다. 현재 파일럿 준비·운영·HTTPS·참가자 안내는 [PILOT_GUIDE.md](../PILOT_GUIDE.md)로 옮겼습니다. 아래는 당시 기록이며 현재 동작의 기준이 아닙니다.

# 파일럿 HTTPS 준비 (도메인을 정한 뒤 사람이 실행)

날짜: 2026-10-13 (줄 PL) · 대상: 파일럿을 여는 기술 담당

**줄 PL 은 이 문서의 어떤 단계도 실행하지 않았다.** 도메인은 사람이 정한다. 아래는 도메인이
정해진 뒤에 할 순서이고, 만든 도구(`scripts/pilot_https_setup.sh`, `scripts/pilot_https/Caddyfile.example`)는
`bash -n`, `--dry-run` 과 `tests/test_lane_pl_https_setup.py` 로만 확인했다. Caddy 설치·인증서 발급·방화벽은 실제 서버에서 처음 한다 —
각 단계의 "확인"이 통과할 때까지 다음으로 가지 않는다.

## 1. 왜 필요한가

휴대폰 브라우저는 **HTTPS(또는 localhost)** 에서만 마이크를 쓴다. 파일럿 서버(`scripts/run_pilot.sh`)는
일부러 `127.0.0.1:8080` 에서만 듣는다. 그 앞에 HTTPS 와 접속 비밀번호를 두는 것이 이 문서다.

```
휴대폰 ──https(443)──▶ Caddy (도메인 인증서, 기본 인증) ──http──▶ 127.0.0.1:8080 (파일럿 서버)
                        └─ /ws (웹소켓)도 같은 길로 간다
```

- 파일럿 서버에는 로그인이 없다(OIDC 없음, 개발 신원 한 명). **기본 인증이 파일럿의 유일한 문**이다.
  비밀번호는 12자 이상, 참가자에게만 따로 알린다.
- 서버를 `HOST=0.0.0.0` 으로 열지 않는다. Caddy 가 같은 서버에서 `127.0.0.1:8080` 으로 넘긴다.

## 2. 사람이 정할 것 (실행 전)

| 정할 것 | 비고 |
|---|---|
| 도메인 | 예: `pilot.<조직 도메인>`. 이 저장소는 정하지 않는다 |
| 접속 아이디·비밀번호 | 아이디 기본 `pilot`. 비밀번호는 비밀번호 관리자에 둔다. 저장소·메신저·문서에 쓰지 않는다 |
| 서버 | 공개 IP 가 있는 리눅스(Debian/Ubuntu). 스크립트는 apt 만 안다 |
| 방화벽·보안 그룹 | 80, 443 을 연다(80 은 인증서 발급과 443 으로의 이동). 8080 은 열지 않는다 |

## 3. 순서

1. **DNS** — 도메인의 A 레코드를 서버의 공개 IP 로 둔다.
   확인: `dig +short pilot.<도메인>` 이 서버 IP 를 낸다. (전파에 몇 분~몇 시간)
2. **파일럿 서버 먼저** — `./scripts/run_pilot.sh --check-only` 가 통과하고(종료 코드 0),
   `./scripts/run_pilot.sh` 로 띄운다.
   확인: 같은 서버에서 `curl -fsS http://127.0.0.1:8080/healthz` → `{"status":"ok"}`.
3. **미리보기** — 아무것도 바꾸지 않는다.
   ```bash
   scripts/pilot_https_setup.sh --domain pilot.<도메인> --dry-run
   ```
   확인: 도메인·아이디·넘길 곳(`127.0.0.1:8080`)이 맞고, 해시 자리에 `<bcrypt-hash>` 가 보인다.
4. **설치** — root 로 실행한다. Caddy 가 없으면 공식 apt 저장소로 설치하고, 비밀번호를 두 번 묻는다.
   ```bash
   sudo scripts/pilot_https_setup.sh --domain pilot.<도메인> --user pilot
   ```
   - 비밀번호는 인자로 받지 않는다(`ps` 에 보인다). 이미 만든 bcrypt 해시가 있으면 `--password-hash` 로 준다
     (`caddy hash-password` 로 만든다).
   - 기존 `/etc/caddy/Caddyfile` 은 `.bak-날짜` 로 복사해 두고, `caddy validate` 가 실패하면 되돌린다.
   - **이 스크립트의 apt 설치 줄과 `caddy hash-password` 를 파이프로 쓰는 줄은 이 줄에서 실행해 보지 못했다.**
     실패하면 Caddy 문서의 설치 방법으로 직접 설치하고, 해시를 만들어 `--password-hash` 로 준다.
5. **인증서** — Caddy 가 처음 요청을 받을 때 Let's Encrypt 인증서를 받는다(80/443 이 열려 있어야 한다).
   확인: `sudo journalctl -u caddy -n 50` 에 `certificate obtained successfully` 가 보인다.
6. **확인** — 4절의 네 가지를 모두 통과해야 참가자에게 주소를 알린다.

## 4. 확인

### 4-1. HTTPS 와 비밀번호

```bash
curl -sS -o /dev/null -w '%{http_code}\n' https://pilot.<도메인>/healthz            # 401 (비밀번호 없이는 열리지 않는다)
curl -sS -u pilot -o /dev/null -w '%{http_code}\n' https://pilot.<도메인>/healthz    # 200 (비밀번호를 물어보면 입력)
curl -sS -o /dev/null -w '%{http_code} %{redirect_url}\n' http://pilot.<도메인>/      # 308 https://pilot.<도메인>/
```

### 4-2. 웹소켓(`/ws`)이 프록시를 지나는가 — 가장 먼저 깨지는 곳

음성은 전부 `/ws` 로 간다. 페이지가 열려도 `/ws` 가 막히면 음성이 되지 않는다.

```bash
curl -sS -i -N --http1.1 -u pilot --max-time 5 \
  -H 'Connection: Upgrade' -H 'Upgrade: websocket' \
  -H 'Sec-WebSocket-Version: 13' -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' \
  https://pilot.<도메인>/ws
```

| 보이는 것 | 뜻 |
|---|---|
| `HTTP/1.1 101 Switching Protocols` 와 `Sec-WebSocket-Accept` 줄, 이어서 `"type":"ready"` 가 든 글자 | **통과.** 프록시·비밀번호·서버가 모두 이어졌다. 5초 뒤 curl 이 시간 초과(종료 코드 28)로 끝나는 것은 정상이다(연결이 열려 있어서) |
| `HTTP/1.1 401` | 비밀번호가 `/ws` 에 닿지 않았다. `-u` 를 줬는지, Caddyfile 의 `basic_auth` 가 사이트 전체에 걸렸는지 본다 |
| `HTTP/1.1 400` 또는 `426` | `Upgrade` 머리가 서버에 닿지 않았다. Caddy 가 아닌 다른 프록시(회사 망 장비)가 앞에 있는지 본다 |
| `HTTP/1.1 502` | 파일럿 서버가 꺼져 있거나 다른 포트다. `curl http://127.0.0.1:8080/healthz` |
| `HTTP/1.1 101` 뒤 곧바로 끊김 | 서버가 연결을 받고 거절했다. `{"type":"error",...}` 글자를 읽는다(설정 오류 이름이 적혀 있다) |

### 4-3. 휴대폰 브라우저 (사람이 직접)

1. 휴대폰에서 `https://pilot.<도메인>/` 을 연다 → 아이디·비밀번호 창 → 입력.
2. 프로토콜을 고르고 시작한다. **마이크 허용 창이 뜨고**, 허용하면 말이 들린다.
3. 말을 한 번 해 본다. 답이 오면 `/ws` 가 휴대폰에서도 지나는 것이다.
- 기본 인증 창을 거친 뒤 `/ws` 연결에 같은 인증 정보가 가는지는 **브라우저마다 다르다**(특히 iOS Safari).
  `/ws` 에서 막히면 4-2 는 통과인데 휴대폰만 실패한다 — 그때는 이 문서의 한계(6절)로 가서 사람이 정한다.

### 4-4. 기록을 남기지 않는다

Caddy 는 `log` 를 적지 않으면 요청 기록을 남기지 않는다. 예시 Caddyfile 에는 `log` 가 없다. 문제가 생겨
잠깐 켜야 하면 끝난 뒤 지운다(요청 주소에 프로토콜 번호가 들어 있다).

## 5. 되돌리기

- 스크립트가 만든 `/etc/caddy/Caddyfile.bak-날짜` 를 `/etc/caddy/Caddyfile` 로 복사하고 `sudo systemctl reload caddy`.
- 파일럿을 마친 뒤에는 `sudo systemctl disable --now caddy` 로 문을 닫고, DNS 레코드를 지운다.

## 6. 한계 · 사람이 할 일

- 이 줄은 Caddy 설치·인증서 발급·`/ws` 통과를 **실제로 해 보지 못했다**(외부 호출 0, 도메인 없음). 4절이 첫 확인이다.
- 기본 인증은 한 아이디·한 비밀번호를 같이 쓴다. 참가자별 구분·잠금(시도 횟수 제한)이 없다. 참가자가 나가면 비밀번호를 바꾼다
  (`--password-hash` 로 같은 스크립트를 다시 실행).
- 파일럿 서버에는 아직 로그인(OIDC)이 없다. 기본 인증이 뚫리면 데이터가 열린다. 주소를 공개 채널에 올리지 않는다.
- nginx 설정은 만들지 않았다. nginx 를 쓰려면 `/ws` 에 `proxy_set_header Upgrade $http_upgrade; proxy_set_header Connection "upgrade";` 와
  `proxy_read_timeout` 을 길게 두어야 한다(기본 60초에 음성 연결이 끊긴다).
