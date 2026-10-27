# 배포·운영 안내 — 파일럿 서버

서버를 준비하고, 띄우고, 점검하고, 백업하는 순서다. 2026-10-20 dev `e7c31c8` 기준.
이 안내는 통제된 연구 파일럿용이다(규제 대상 GxP·임상 배포가 아니다).

- 파일럿을 여는 순서·참여자 안내·데이터 처리·고지 문안: `docs/PILOT_GUIDE.md`
- 휴대폰에서 마이크를 쓰기 위한 HTTPS: `docs/PILOT_GUIDE.md`
- 증상별 복구: `docs/TROUBLESHOOTING_GUIDE.md`
- 설정 이름 전체: `src/voiney_lab/setting_names.py`
- 고정 도메인·Cloudflare 이름 있는 터널·AWS WIF·일일 백업: [실행 순서](DOMAIN_AND_KEYLESS_GUIDE.md)

## 1. 서버에 설치할 것

`pip install -e .` 가 가져오지 않는 것들이다. 서버마다 사람이 설치한다.

| 무엇 | 왜 | 설치·확인 | 없으면 |
|---|---|---|---|
| Python 3.12 이상, 저장소의 `.venv` | 실행기가 `.venv/bin/activate` 를 쓴다 | `python -m venv .venv && .venv/bin/pip install -e .` | 실행기가 `[ERROR] venv not found` 로 멈춘다 |
| **Node.js 22 이상**(npm 포함) | 새 화면(`web/`, `/app/`)을 실행기가 빌드한다(`web/build.sh`: `npm ci` 와 Vite 빌드, 결과는 `src/voiney_lab/static/app/`). 바깥 CDN 없이 글꼴까지 서버가 제공한다 | `node --version` 이 22 이상; 실행기 출력에 `[OK] new screen built` | 서버는 시작한다. 실행기가 `[WARN]` 을 찍고 새 화면만 꺼진다(`/app/` 은 "아직 준비되지 않았어요", `/` 는 지금 화면). `VOINEY_LAB_NEW_UI=true` 여도 `/` 는 지금 화면 |
| **LibreOffice**(`libreoffice-writer-nogui`)와 한국어 글꼴 **`fonts-nanum`** | Word 원문을 PDF 로 바꾸고, 보고서 PDF 를 만든다. 한국어 글꼴이 없으면 한국어 문서의 글자 층이 띄어쓰기를 잃어 근거 검사에서 떨어진다 | `sudo apt-get install --no-install-recommends libreoffice-writer-nogui fonts-nanum`; `soffice --version`, `fc-match NanumGothic`, `GET /readyz` → `docx_conversion_available: true`. 다른 곳의 `soffice` 는 `VOINEY_LAB_OFFICE_CONVERTER` | 서버는 시작한다. Word 올리기만 `docx_conversion_unavailable`(503), 보고서 PDF 는 503. `scripts/run_pilot.sh` 는 `[WARN]` 만 찍는다 |
| **HEIC 지원**(선택) | 아이폰 HEIC 사진 | `pip install -e '.[heic]'` 후 다시 시작, `heic_photos_available: true` 확인. 바퀴에 GPLv2 코드가 들어 있어 기관의 라이선스 담당이 정한다 | HEIC 만 `heic_support_unavailable`(503). JPEG·PNG 는 된다 |
| **긴 PDF 읽기 시간** `VOINEY_LAB_PDF_WORKER_TIMEOUT_SECONDS=300` | 글자 추출은 시간 한도가 있는 자식 프로세스에서 돈다(기본 30 s, 1–600). 88쪽 문서의 추출만 121 s 걸렸다(2026-10-13 측정) | `.env` 에 적고 다시 시작 | 긴 문서 올리기가 시간 초과로 거절된다 |

`.github/workflows/deploy-staging.yml` 은 `dev` 에 push 한 CI 가 성공하면 ssh 로 스테이징 서버의 `deploy` 명령을 부른다. 그 서버에서 `deploy` 가 무엇을 하는지와 위 패키지가 깔렸는지는 저장소에서 알 수 없다(확인 안 됨).

## 2. 띄우기

파일럿은 **`scripts/run_pilot.sh` 로만** 띄운다(손으로 uvicorn 을 부르면 실행기가 고정하는 것들이 빠진다).

```bash
./scripts/run_pilot.sh --check-only   # 설정만 보여 주고 끝낸다
./scripts/run_pilot.sh                # 127.0.0.1:8080
./scripts/run_pilot.sh --dev-identity # 로그인 없이(개발 신원) — 파일럿에는 쓰지 않는다
```

실행기가 고정하는 것:
- 데이터 위치: `data/runtime/pilot/`(프로토콜·원문·실험 기록), `data/runtime/pilot/workspace/`(작업공간·계정·지표), 시작 직전에 파일럿 표시 파일 `.voiney-pilot-data`.
- 로그인: `VOINEY_LAB_AUTH_MODE=accounts`(셸·`.env` 의 다른 값은 `[WARN]` 과 함께 무시). 초대 링크는 `python scripts/pilot_invite.py`(`docs/PILOT_GUIDE.md` 7절).
- 안전 문서 범위: `data/runtime/pilot/approved_safety_catalog.sqlite` 가 없으면 `source_protocol_only`, 있으면 `reference_only` 로 점검하고 쓸 수 없으면 시작을 거부한다(3절).
- PDF 밖 일반 설명은 끈다. 작업공간이 꺼졌거나 `VOINEY_LAB_ADMIN_TOKEN` 이 비었거나 LibreOffice 가 없으면 `[WARN]`.
- 새 화면: 시작 전에 `web/build.sh` 로 빌드해 `/app/` 에서 제공한다. `.env` 의 `VOINEY_LAB_NEW_UI=true` 가 `/` 를 `/app/` 으로 보낸다(기본은 지금 화면). node 가 없으면 `[WARN]` 과 함께 새 화면만 꺼진다.
- 기본은 루프백(127.0.0.1)이다. 밖에서는 HTTPS 프록시(Caddy) 또는 Cloudflare 이름 있는 터널을 앞에 둔다 — [고정 주소 안내](DOMAIN_AND_KEYLESS_GUIDE.md). `HOST=0.0.0.0` 으로 그냥 열지 않는다.

모든 모델 역할과 STT·TTS 공급자는 `.env` 에 적는다(비면 그 기능이 설정 이름을 적은 오류로 닫힌다). 지금 쓰는 값은 `docs/FEATURES.md` F17, 옛 설정 이름이 있으면 `python scripts/migrate_env.py --check`.

### Google Chirp 3 받아쓰기를 시험하려면

1. Google Cloud 콘솔에서 크레딧을 쓸 **프로젝트**를 고르고 결제 연결을 확인한다. **API 및 서비스 → 라이브러리 → Cloud Speech-to-Text API → 사용**을 누른다.
2. **IAM 및 관리자 → 서비스 계정 → 서비스 계정 만들기**에서 서버용 계정을 만든다. 같은 프로젝트에 **Cloud Speech Client** 역할(`roles/speech.client`)을 준다. 프로젝트 ID를 적어 둔다.
3. EC2에서는 [AWS Workload Identity Federation 설정](DOMAIN_AND_KEYLESS_GUIDE.md) 5절로 **새 키 없이** 연결한다. 기존 서비스 계정 키 JSON과 개발용 사용자 ADC도 같은 설정 이름으로 읽힌다. 파일은 서비스를 실행하는 사용자만 읽도록 모든 저장소 밖의 `~/.config/voiney/`에 둔다. 파일 내용은 복사해 문서·메신저·터미널 출력에 넣지 않는다.

   ```bash
   mkdir -p ~/.config/voiney
   chmod 700 ~/.config/voiney
   chmod 600 ~/.config/voiney/google-stt-wif.json
   ```

   JSON 파일을 그 위치에 옮긴 다음 권한을 준다. 서비스 사용자가 `ubuntu`가 아니면 그 사용자의 홈과 소유권을 쓴다. 저장소 안 파일이나 저장소로 이어지는 링크, 다른 사용자가 읽을 수 있는 파일은 거절한다.
4. 서버의 `.env`에 아래 줄을 넣는다. 프로젝트 ID와 절대 경로를 자기 값으로 바꾼다. 기존 `VOINEY_LAB_STT_MODEL=scribe_v2`도 함께 바꾼다.

   ```dotenv
   VOINEY_LAB_STT_PROVIDER=google_cloud_v2
   VOINEY_LAB_STT_MODEL=chirp_3
   VOINEY_LAB_STT_LANGUAGE=ko
   VOINEY_LAB_GOOGLE_STT_V2_PROJECT=your-project-id
   VOINEY_LAB_GOOGLE_STT_V2_CREDENTIALS_FILE=/home/ubuntu/.config/voiney/google-stt-wif.json
   VOINEY_LAB_GOOGLE_STT_V2_LOCATION=us
   VOINEY_LAB_GOOGLE_STT_V2_DENOISE=true
   ```

5. 서버를 다시 띄우고 `/readyz`를 확인한다. 잘못된 V2 설정은 503 `speech_configuration_invalid`와 **설정 이름만** 보인다. 이 점검은 로컬 설정·파일 위치·권한을 확인하며 Google 인증이나 음성 인식 성공을 뜻하지 않는다. 실제 확인은 사람이 `docs/TROUBLESHOOTING_GUIDE.md` 6절의 녹음 비교 명령을 실행한다. 최종 공급자는 녹음 비교 후 사람이 정한다.

V2는 받아쓰기만 바꾼다. 말하기 설정은 따로 둔다. 지정한 파일을 google-auth ADC 로더로 읽어 OAuth 토큰을 만들고, 토큰은 프로세스 메모리에만 둔다. AWS WIF는 EC2 역할로 짧은 토큰을 받고 서비스 계정을 사용한다. 파일이 없거나 틀리면 다른 자격을 찾지 않는다. `us`는 [공식 Chirp 3 지역 표](https://docs.cloud.google.com/speech-to-text/docs/models/chirp-3)에 명시된 기본 지역이다. 다른 지역은 `chirp_3`·한국어 지원을 확인한 뒤 넣는다. 자동 구두점·단어 시각을 요청하고, 잡음 제거는 기본으로 켠다(효과는 사람 시험 뒤 확정). 단어 시각은 인식 품질에 영향을 줄 수 있다. [공식 IAM 역할 안내](https://docs.cloud.google.com/speech-to-text/docs/iam)도 참고한다.

### systemd 로 두기 (예시, 이 줄에서 실행해 보지 않음)

적용할 서비스 파일은 `deploy/voineylab.service`이며 설치·사용자·자격 경로를 맞추는
명령은 [고정 주소 안내](DOMAIN_AND_KEYLESS_GUIDE.md) 3절에 있다. 아래는 핵심 필드다.

```ini
[Unit]
Description=Voiney Lab pilot
After=network.target

[Service]
Type=simple
User=voineylab
WorkingDirectory=/opt/voineylab
ExecStart=/opt/voineylab/scripts/run_pilot.sh
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

- 저장소를 `/opt/voineylab` 에 두고, 그 안에 `.venv` 와 `.env`(커밋하지 않음)를 둔다. API 키는 `.env`에, Google V2 ADC JSON은 위 안내대로 저장소 밖에 둔다. 실행기가 `exec` 로 uvicorn 을 띄우므로 systemd 가 그 프로세스를 그대로 본다.
- 전용 비루트 사용자로 돌린다. 데이터는 저장소 안 `data/runtime/pilot/` 이다.

## 3. 기관 안전 문서 카탈로그 (선택)

파일럿에는 없어도 된다(`source_protocol_only`: 올린 원문의 안전 주의만, 화면·답·기록이 "기관 안전 문서 없음 · 원문 안전 주의만"). 기관의 SOP·SDS·장비 설명서를 쓰려면:

1. **검토한 목록(manifest) 만들기** — 골라 낸 문서마다 고정 id·제목·버전·SHA-256·언어·종류·출처 권한·승인 상태·사용 범위·유효일과 검토일·절. 폐기·대체된 버전은 `active: false`. 쓰기 전에 검사한다:
   ```bash
   .venv/bin/python -B scripts/audit_approved_catalog.py --manifest /abs/path/reviewed-manifest.json
   ```
2. **새 경로에 후보 카탈로그 만들기**(운영 파일을 직접 고치지 않는다):
   ```bash
   candidate="$(mktemp -d)/approved-lab-catalog.sqlite"
   .venv/bin/python -B scripts/ingest_safety_documents.py --manifest /abs/path/reviewed-manifest.json --db "$candidate"
   .venv/bin/python -B scripts/audit_approved_catalog.py --db "$candidate" --scope reference_only --query "2단계 acetonitrile 주의사항"
   ```
   검사는 id 와 점수만 찍고 본문은 찍지 않는다. 의도한 질문이 승인된 활성 문서를 돌려줘야 한다.
3. **서버를 멈춘 사이에 옮기기**:
   ```bash
   cp "$candidate" data/runtime/pilot/approved_safety_catalog.sqlite
   ./scripts/run_pilot.sh --check-only   # 0 으로 끝나야 한다
   ```
   파일럿 카탈로그의 모든 문서는 `usage_scope: reference_only`, `approval_status: approved`, `active: true`, `test_fixture` 가 아닌 출처, 제목에 "fictional" 없음이어야 한다. 하나라도 demo 문서가 있거나, 승인된 `reference_only` 문서가 없거나, 파일을 읽을 수 없으면 실행기가 거부한다(종료 코드 1). 기관 문서 없이 열려면 파일을 치운다.
4. **폐기·교체**는 새 목록으로 한다: 옛 문서를 `active: false`, `approval_status: superseded`(또는 `rejected`)로 하고 새 버전을 더해 2–3 을 다시 한다. 돌고 있는 SQLite 를 고치지 않는다.

카탈로그는 단계 안전 카드에만 쓰이고 프로토콜을 바꾸지 않는다. 개발 서버(`scripts/run_dev.sh`)는 `VOINEY_LAB_SAFETY_CATALOG`·`VOINEY_LAB_USAGE_SCOPE` 를 `.env` 에서 읽는다. CI 는 가상 demo 문서(`data/moss_demo/approved_documents.ko.json`)로 카탈로그를 만든다.

## 4. 점검

- `GET /healthz` — 살아 있는지만. 답하지 않으면 다시 띄운다.
- `GET /readyz` — 설정이 읽히는지. 신원·작업공간·프로토콜 저장소·실험 기록·앱 출처·로그인 방식 설정 중 하나라도 읽을 수 없으면 503(`reason` 에 예외 이름). 200 이면 `identity_mode`(`accounts`·`oidc`·`development`), `workspace_enabled`, `protocol_catalog_enabled`, `experiment_reports_enabled`, `docx_conversion_available`, `heic_photos_available`.
- `/readyz`는 사용 범위와 그 범위에 맞는 활성 안전 카탈로그도 검사한다. 안전 설정 오류는 503 `safety_configuration_invalid`와 설정 이름(`fields`)만 보여 준다. `source_protocol_only`는 카탈로그 없이 가능하다. 공급자를 호출하지 않으며 실제 모델·음성·외부 연결 성공을 보장하지 않는다.
- 실행기는 실제 라우터·라우터 먼저·PDF 작업 제한을 출력한다. 개발 실행기의 기본 켜짐은 명시한 셸·설정 파일 값을 덮지 않는다. 카탈로그·작업공간이 꺼져 있으면 그 설정 이름과 켜는 방법을 출력한다. PDF 기본 30초 안에서 모든 긴 문서가 성공하는 것은 아니다.
- 사용 범위의 미설정·공백과 시작 확인·작업공간 boolean 오타도 설정 이름으로 거절한다.
- 설정이 틀리면 조용히 기본값으로 가지 않는다: 작업공간이 켜졌는데 절대 경로가 없거나, `operational` 범위인데 OIDC 설정이 덜 됐거나, 계정 모드인데 작업공간이 없으면 시작이나 첫 요청에서 거부한다. 옛 설정 이름이 있으면 서버·실행기가 시작을 거부한다.

## 5. 데이터와 백업·복구

매일 실행은 `scripts/daily_backup.sh`와 `deploy/voineylab-backup.service`·
`deploy/voineylab-backup.timer`를 사용한다. 기본 꺼짐이며 사람이 설치·활성화한다.
네 핵심 DB를 확인하고 실행 중인 서버를 잠깐 멈춰 기존 도구로 생성·검증한 뒤,
실패 때도 재시작을 시도한다. 시간·중복 잠금·실패 확인·복구 폴더 배치는
[고정 주소 안내](DOMAIN_AND_KEYLESS_GUIDE.md) 6절에 있다.

| 무엇 | 파일럿 위치 |
|---|---|
| 프로토콜 장부, 원문 | `data/runtime/pilot/protocol_workspace.sqlite`, `data/runtime/pilot/objects/sha256/…` |
| 실험 기록 | `data/runtime/pilot/experiment_reports.sqlite` |
| 작업공간(실험·관찰·첨부) | `data/runtime/pilot/workspace/commercial_workspace.sqlite`, `…/workspace/evidence/…` |
| 계정, 파일럿 지표 | `data/runtime/pilot/workspace/accounts.sqlite`, `…/workspace/pilot_metrics.sqlite` |
| 기관 안전 카탈로그(있으면) | `data/runtime/pilot/approved_safety_catalog.sqlite` |

`scripts/pilot_state_backup.py` 는 모든 `.sqlite`(SQLite 백업 API, 원본·사본 `PRAGMA quick_check`)와 `objects/`·`evidence/` 의 허용한 원문·첨부만 담고, `.env`·원음·그 밖의 파일은 뺀다. 모든 파일의 SHA-256 을 목록에 남긴다. 절대 경로만 받는다. 시점을 맞추려면 서버를 멈추고 뜬다:

```bash
P="$PWD/data/runtime/pilot"
sudo systemctl stop voineylab
.venv/bin/python scripts/pilot_state_backup.py create \
  --protocol-data-dir "$P" \
  --workspace-data-dir "$P/workspace" \
  --report-database "$P/experiment_reports.sqlite" \
  --output /var/backups/voineylab/pilot-before-session-001.tar.gz
.venv/bin/python scripts/pilot_state_backup.py verify /var/backups/voineylab/pilot-before-session-001.tar.gz
sudo systemctl start voineylab
curl --fail http://127.0.0.1:8080/healthz && curl --fail http://127.0.0.1:8080/readyz
```

- 백업에는 계정(비밀번호 해시)과 연구 기록이 들어 있다. 기관이 승인한 암호화 저장소에, 파일럿 약속에 맞는 기간만 둔다.
- 세션 전후에 한 번씩 뜨고, 복구를 실제로 해 본다. 만든 것만으로 복구된다고 보지 않는다.
- 복구는 **새 빈 절대 경로**에만 한다: `pilot_state_backup.py restore <archive> /var/lib/voineylab-restore-001`, 그 경로로 가리킨 시험용 서버에서 확인한 뒤 옮긴다. 상대 경로·루트·덮어쓰기·심볼릭 링크·깨진 SQLite·위험한 묶음 항목·체크섬 불일치는 거부한다.
- 파일럿을 마치고 지우는 것은 `scripts/pilot_data_delete.py`(`docs/PILOT_GUIDE.md` 5절). 실제 파일럿 데이터로 복구를 연습한 기록은 아직 없다.

## 6. 지켜볼 것

- `/healthz` 응답, `/readyz` 의 200 아닌 응답, 다시 시작한 횟수, 디스크, 백업 검사 실패.
- 파일럿 지표: 관리자 화면 `/admin-metrics.html`(관리자 계정 또는 토큰), CSV 는 `python scripts/pilot_metrics.py experiments|analyses|days|notes`. 숫자·코드·시각만 있다.
- 집계: `GET /api/admin/metrics`(머리글 `X-Voice-Workflow-Admin-Token` = `VOINEY_LAB_ADMIN_TOKEN`, 없으면 503) — 경로·도구·지연 집계와 실험 기록 수·완료율·이상·차단 수.
- 서버 기록(log)의 `pilot.metrics.failed`(지표 쓰기 실패, 실험은 계속된다).

## 7. 사고가 나면

- 상태를 바꾸다 실패하면 마지막으로 서버가 확인한 상태에 둔다. 연구자에게 기록을 새로 읽게 한다.
- 정리된 서버 기록과 실험 세션 id 만 사고 기록에 남긴다. 원음·말한 글·토큰·`.env`·첨부 파일·내부 DB 경로는 기록이나 티켓에 붙이지 않는다.
