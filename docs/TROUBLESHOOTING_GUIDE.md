# 문제 해결 안내

증상별로 상태를 지키며 복구하는 법이다. 2026-10-20 dev `e7c31c8` 기준.

언제나 **서버가 마지막으로 확인한 상태**를 지킨다. 상태를 바꾸는 명령은 첫 시도가 기록됐는지 기록(타임라인)에서 확인하기 전에 되풀이하지 않는다. 운영 데이터를 SQLite 를 직접 고쳐 고치지 않는다.

## 1. 연구자가 보는 증상

| 증상 | 뜻 | 안전한 복구 |
|---|---|---|
| 프로토콜 목록이 계속 불러오는 중이거나 비었다 | 카탈로그가 꺼졌거나 비었거나 읽는 중 | 한 번 기다리고 새로고침. 그래도면 운영자가 `/readyz` 와 안전 문서 범위 설정을 본다(4절) |
| "이 프로토콜로 실험 시작"이 없다 | 분석이 안 끝났거나, "검토 대기"이거나, **실행을 막는 사유**(읽지 못한 쪽, 실행할 단계 없음, 안전에 중요한 충돌)가 있다 | 요약 카드의 사유를 읽는다. 검토 대기면 확인 카드를 끝내고 등록한다. 막는 사유는 원문(읽을 수 있는 PDF 등)에서 고친다. 화면에서 무시할 길은 없다 |
| "시작 전 알림"이 보인다 | 막지 않는 사유(빠진 값, 애매함, 안내가 다루지 못하는 구조) | 읽고 시작한다. 그 단계에서 원문을 읽고 필요한 값을 묻는다 |
| "검토 대기 · 확인 필요 n" 이고 카드가 없다 | 확인할 항목이 너무 많아(기본 12개 초과) 다시 분석했는데도 많다(`protocol_analysis_too_many_review_items`) | 더 읽기 쉬운 원문으로 다시 올리거나 "다시 분석" |
| 핵심 값 확인에서 틀렸다 | 분석 값과 원문 조각이 다를 수 있다 | 원문 조각을 다시 본다. 분석이 틀렸으면 "분석 값이 틀렸어요"로 카드를 열어 고치고 등록한다 |
| 확인 카드에서 안전 주의 글을 고칠 수 없다 | 의도된 동작: 안전 주의는 고치지 않는다 | 메모를 남기거나, 원문에 없는 문장이면 "원문에 없어요" |
| "마이크 사용 가능"이 안 나오거나 "HTTPS 주소로 열어 주세요" | 브라우저 마이크 권한, 장치, https 가 아님 | 마이크 권한을 허용하고 https 주소로 연다. 설정 탭에서 마이크를 고른다. 실험 단계는 바뀌지 않았다 |
| 말해도 반응이 없고 "다른 사람 말로 보여 듣지 않은 말"이 늘어난다 | 옆 사람 말 거르기를 켰고, 맞춘 목소리보다 작게 말했다 | 실험이 끝난 뒤 설정에서 다시 맞추거나 목소리 민감도를 낮추거나 거르기를 끈다(기본은 끔). 연달아 걸러지면 서버가 스스로 낮추거나 끈다 |
| 마이크 옆 "잘 안 들렸어요", 또는 "잘 안 들렸어요. 다시 말씀해 주세요." | 소리를 버렸다(받아 적은 말 없음, 너무 짧음, 메아리, 언어를 알 수 없음 등) — 대화 보기에 까닭이 한 줄 | 또렷하게 다시. 같은 일이 잦으면 6절대로 진단 녹음을 켜고 사람 시험을 한다. 상태를 바꾸는 명령이었다면 먼저 지금 단계를 확인한다 |
| "음성 인식 언어가 불확실합니다" | 분명히 영어·베트남어 문장으로 들었다 | 한국어로 다시. 짧은 라틴 글자는 묻지 않고 "잘 안 들렸어요"로 버린다 |
| 일시정지 중에 말해도 대답이 없다 | 의도된 동작: 일시정지 중에는 "다시 시작"·"이어서 하자"·"계속하자"·"재개"만 알아듣는다 | 재개 말을 하거나 [다시 시작]을 누른다 |
| "지금 답하지 못했어요. 다시 말씀해 주세요." | 라우터 모델이 요청 한도에 걸렸다. 아무것도 바뀌지 않았다 | 잠시 뒤 다시 말한다 |
| 빨간 띠 "연결이 끊겼어요 — 다시 연결 중" | 연결이 끊겼다. 화면은 보기만 되고 마이크는 멈췄다 | 화면을 켜 둔 채 기다린다. 타이머는 화면이 이어 센다. "n번 시도했어요"가 계속되면 와이파이를 확인한다. 다시 이어지면 서버 상태로 바뀐다 |
| 오래된 상태·충돌 안내 | 다른 탭이나 말이 실험의 버전을 바꿨다 | 기록을 새로 읽고 실험을 다시 고른다. 새 상태를 덮어쓰지 않는다 |
| 말 처리 실패 | STT·모델·TTS 나 서버 처리 실패 | 기록에 바뀐 것이 보이지 않으면 아무것도 바뀌지 않은 것이다. 지금 단계를 확인하고 다시 |
| 로그인: "여러 번 틀려 잠시 막혔어요." | 15분 안에 5번 틀렸다 | 15분 뒤에 다시 |
| 로그인: "꺼진 계정이에요." / "초대 링크가 만료되었거나 이미 쓰였어요." | 관리자가 계정을 껐거나 초대가 끝났다 | 연구실 담당에게 새 초대 링크를 받는다 |
| "다시 로그인해 주세요." | 세션이 끝났다(30일, 비밀번호 바꿈, 로그아웃) | 다시 로그인 |
| 끝난 뒤 보고서 링크가 비어 있다 | 기록이 아직 없거나 보고서 목록을 읽지 못했다 | 기록 탭에서 해당 실험을 열어 보고서 링크를 확인한다. 실패 안내면 기록 새로고침. 이 조회가 보고서를 만들지는 않는다 |
| 첨부 내려받기 실패 | 파일이 없거나 바뀌었거나 크기·해시가 다르다 | 기록은 그대로 두고 운영자에게 알린다. 정해진 사고 절차로만 다시 붙인다 |

## 2. 운영자가 보는 증상

| 증상 | 뜻 | 안전한 복구 |
|---|---|---|
| `/readyz` 가 503 | 설정 하나가 읽히지 않았다(`reason` 에 예외 이름) | 그 설정을 서버에서 고치고 다시 시작. 사고 기록에 비밀 값을 넣지 않는다 |
| `/readyz` 가 `safety_configuration_invalid` | 사용 범위·안전 카탈로그를 읽거나 해당 범위의 문서를 확인할 수 없다 | `fields`의 설정 이름과 실행기의 `--check-only`를 본다. 원문만 쓰는 범위는 카탈로그 없이 가능하다. 비밀 값을 사고 기록에 넣지 않는다 |
| `./scripts/run_pilot.sh` 가 1 로 끝난다 | `data/runtime/pilot/approved_safety_catalog.sqlite` 가 있는데 쓸 수 없다(읽을 수 없음, demo 문서, 승인된 `reference_only` 문서 없음), 또는 옛 설정 이름이 있다 | `[ERROR]` 줄을 읽는다. 카탈로그를 `docs/DEPLOYMENT_RUNBOOK.md` 3절대로 다시 만들거나 파일을 치운다(그러면 `source_protocol_only` 로 시작). 옛 이름은 `python scripts/migrate_env.py --check` → `--write` |
| 모델 기능이 답하지 않는다(분석·번역·라우터·AI 일반 지식·보고서 문장) | 그 역할의 공급자·모델이 비었다("설정 필요"). 다른 공급자로 가지 않고 설정 이름을 적은 오류로 닫힌다 | `.env` 에 `VOINEY_LAB_<역할>_PROVIDER`·`_MODEL` 을 `.env.example` 처럼 적고 다시 시작. `./scripts/run_dev.sh --check-only` 가 역할마다 공급자·모델을 찍는다 |
| 음성이 전혀 안 된다 | STT·TTS 공급자가 비었거나 키가 없다 | `VOINEY_LAB_STT_PROVIDER`·`VOINEY_LAB_TTS_PROVIDER` 와 키 |
| `/ws` 가 연결하자마자 닫힌다 | 켠 기능에 필요한 키·역할이 없다(라우터 먼저인데 라우터 꺼짐, 웹 설명·그림인데 `OPENAI_API_KEY` 없음, 보조 설명인데 역할 없음) | 기능을 끄거나 빠진 설정을 넣는다 |
| OCR 이 끝나지 않는다 | OCR 엔진이 없거나 실패했다 | `VOINEY_LAB_OCR_PROVIDERS` 와 엔진 키를 본다. 그 쪽은 읽힐 때까지 시작을 막는다(`source_page_requires_ocr`) |
| Word 올리기가 `docx_conversion_unavailable`(503), 실행기가 `[WARN] LibreOffice (soffice) was not found.` | 서버에 LibreOffice 가 없다. 서버는 시작하고 Word 만 거절한다 | `docs/DEPLOYMENT_RUNBOOK.md` 1절대로 설치하고 `/readyz` 의 `docx_conversion_available` 확인. 또는 PDF 로 올린다 |
| HEIC 가 `heic_support_unavailable`(503) | 선택 설치 pillow-heif 가 없다 | 설치하거나 JPEG·PNG 로 올린다 |
| 긴 문서 올리기가 시간 초과 | 글자 추출이 30 s 안에 끝나지 않았다 | `VOINEY_LAB_PDF_WORKER_TIMEOUT_SECONDS=300` |
| 분석이 "일부 조각 분석 실패" 항목을 남기거나 요청 한도(429) | 긴 문서를 여러 개 한꺼번에 분석했다 | 하나씩 올리거나 `VOINEY_LAB_ANALYSIS_PIECE_CONCURRENCY` 를 낮춘다 |
| 링크 가져오기 거절(`link_private`, `link_sign_in_required` …) | 비공개 파일, 로그인 안 함, Google 이 아닌 주소 | 서버가 돌려준 문장을 그대로 따른다(공유를 "링크가 있는 모든 사용자"로 바꾸거나 파일을 올린다) |
| 관리자 지표: 503 "관리자 지표가 닫혀 있습니다." | `VOINEY_LAB_ADMIN_TOKEN` 이 없고 관리자 계정도 아니다 | 관리자 계정으로 로그인하거나 토큰을 설정 |
| 평점·지표가 저장되지 않는다(`pilot_metrics_unavailable`, 기록 `pilot.metrics.failed`) | 작업공간이 꺼졌거나 쓰기 실패 | 작업공간을 켠다. 실험은 계속된다 |
| 운영 범위의 신원 설정 오류 | OIDC 발급자·대상·JWKS 가 덜 됐다 | 셋을 모두 HTTPS 로 넣는다. 운영 범위는 개발 신원으로 내려가지 않는다 |

## 3. 서비스와 저장소

**프로세스가 없다**: 서비스 관리자와 정리된 기록을 본다 → 데이터 경로의 디스크·권한 → 설정을 고친 뒤에만 다시 시작 → `/healthz`·`/readyz` 성공 확인 → 연구자는 새로고침하고 홈의 "이어서 하기"로 돌아간다. 처리 중이던 명령이 기록됐다고 짐작하지 않는다.

**백업 실패** (`scripts/pilot_state_backup.py`):
- 상대 경로·루트 → 절대 경로로.
- 이미 있는 출력 → 새 이름으로(덮어쓰기는 일부러 거부한다).
- 원본에 심볼릭 링크 → 승인된 저장소 변경으로 없앤다.
- SQLite 검사 실패 → 서비스를 멈추고 파일을 보존한 채 보고한다. "성공한" 것처럼 보이는 묶음을 만들지 않는다.
- 검증·체크섬 실패 → 그 묶음을 격리하고 멈춘 상태에서 새로 뜬다. 복구에 쓰지 않는다.

**복구 실패**: 대상은 없는 절대 경로여야 한다. 먼저 `verify`, 새 폴더에 `restore`, 시험용 서버로 확인한 뒤 옮긴다. 목록·체크섬·SQLite 검사 없이 풀기만 한 것은 복구가 아니다.

## 4. 외부 공급자 실패

- 실험은 서버가 확인한 지점에 둔다. 웹·외부 내용은 참고일 뿐 원문이나 기관 안전 문서를 넘지 않는다.
- 진단은 가짜 공급자 시험으로 한다. 실제 공급자 시험은 일부러, 한도를 두고, 승인된 키로만 하고 키·연구실 문서·전체 프롬프트를 찍지 않는다.
- 설정 확인, 계약 시험(contract-tested), 연결, 실제 호출 성공(live)을 따로 적는다.

## 5. 사고 기록에 남길 것

시각, 실험 세션 id, 화면의 단계·상태, 하려던 일, 응답 코드나 화면 문구, 기록이 바뀌었는지, 브라우저·서버 버전, 다시 일으키는 순서.
남기지 않을 것: 원음, 말한 글 전체, 토큰, `.env`, 키, 모델 추론, 첨부 파일, 연구실 프로토콜 글, 사용자 식별자.

## 6. 받아쓰기 진단 녹음 — 사람 시험 동안만

받아쓰기가 자주 틀리거나 "잘 안 들렸어요"가 잦을 때, 원인을 보려면 실제 소리가 필요하다. 서버에는 **켤 때만** 소리를 남기는 진단 녹음이 있다. 연구실 사람의 목소리가 남으므로 **시험하는 동안만** 켜고, 시험한 사람에게 알리고, 끝나면 지운다.

- **무엇이 남나**: 말 한 번마다 `<세션>-turn-<n>-g-<세대>.wav`(16 kHz 소리)와 같은 이름의 `.json`(받아 적은 글, 언어, 길이, 보낸 용어, 브라우저 음성 처리 설정). 받아 적은 말이 없거나 언어를 알 수 없어 버린 말도 남긴다(그때 `endpoint_reason` 이 `dropped_<이유>`). 버린 소리가 몇 번인지는 녹음과 별도로 지표에 이유 코드로만 남는다.
- **켜기**: 서버의 설정 파일에 `VOINEY_LAB_STT_DIAGNOSTICS_ENABLED=true` 를 적고 서버를 다시 켠다. 보관 개수는 `VOINEY_LAB_STT_DIAGNOSTIC_MAX_FILES`(기본 20, 2~100) — 시험 한 번(30분)이면 100 으로 늘린다. 넘으면 오래된 것부터 지운다. 위치는 `VOINEY_LAB_STT_DIAGNOSTIC_DIR`(기본 `data/runtime/stt-diagnostics`, `data/runtime` 아래만 된다; 시범 실행기는 `data/runtime/pilot/` 안).
- **끄기**: 시험이 끝나면 `false` 로 되돌리고 서버를 다시 켠다. 꺼 두면 소리는 남지 않는다.
- **지우기**: 비교가 끝나면 그 폴더의 `.wav`·`.json` 을 모두 지운다. 시범 서버는 `scripts/pilot_data_delete.py` 가 이 폴더도 찾는다. 소리·글을 메신저나 저장소에 올리지 않는다(AGENTS 규칙 8).
- **공급자 비교**: 사람이 녹음을 듣고 맞는 글을 한 줄씩 적는다(`{"identity": "<파일 이름>", "reference": "<맞는 글>"}`, 용어를 따로 보려면 `"terms": [...]`). 그다음

  ```bash
  python scripts/stt_compare.py data/runtime/stt-diagnostics references.jsonl
  python scripts/stt_compare.py data/runtime/stt-diagnostics references.jsonl --live google_cloud_v2,google_cloud,elevenlabs --language ko
  ```

  첫 줄은 공급자를 부르지 않고 그때 받아 적은 글만 채점한다. `--live` 는 녹음마다 공급자마다 인식 요청을 한 번씩 보낸다(요청 수를 찍는다, 인증 설정이 필요하다 — 사람만 돌린다). V2 토큰을 처음 받거나 갱신하는 인증 요청은 이 숫자와 별도다. 셸에 인증 설정을 넣은 뒤 실행한다. Python에서 실행하려면 먼저 `from dotenv import load_dotenv; load_dotenv()`로 서버와 같은 설정을 읽는다(설정값을 출력하지 않는다). 단어 오류율·글자 오류율, 숫자와 단위가 맞은 비율, 실험 용어가 맞은 비율, 빈 결과 수를 공급자마다 보인다. `--details` 는 턴마다 글도 찍으므로 화면 공유에 주의한다.
- **용어**: 지금 실험의 힌트는 한국어 명령 9개, 저장 번역 용어집의 한국어 이름, 기존 단계·제어 말, 원문 기술용어 순으로 최대 100개다. 일반 턴과 끼어들기가 같은 목록을 쓴다. ElevenLabs 는 `keyterms`(50자 미만·5단어 이하·100개까지), Google v1은 `speechContexts`, V2는 `VOINEY_LAB_GOOGLE_STT_V2_ADAPTATION_ENABLED=true`일 때만 인라인 음성 적응 구절(중복 제거, 한 구절 100자·1,000개·총 100,000자 이하)로 받는다. 요청 모양은 가짜 전송으로 확인했다. 번역 문장 전체나 새 용어를 만들지 않는다.

### Google V2가 답하지 않을 때

설정 순서는 `docs/DEPLOYMENT_RUNBOOK.md` 2절의 **Google Chirp 3 받아쓰기**를 따른다. EC2의 키 없는 연결은 [WIF 안내](DOMAIN_AND_KEYLESS_GUIDE.md) 5절이다. 콘솔에서 Speech-to-Text API 사용, 서비스 계정의 **Cloud Speech Client** 역할을 먼저 확인한다. ADC JSON(기존 서비스 계정 키·AWS WIF·개발자 사용자 자격)은 저장소 밖에 두고 `chmod 600`을 준다. `.env`의 `VOINEY_LAB_GOOGLE_STT_V2_CREDENTIALS_FILE`에는 그 절대 경로, `VOINEY_LAB_GOOGLE_STT_V2_PROJECT`에는 요청 프로젝트 ID를 쓴다. `GOOGLE_APPLICATION_CREDENTIALS`가 이 명시적 설정을 대신하지 않는다. 키 내용이나 토큰을 로그·화면에 출력하지 않는다.

| 보인 것 | 사람이 확인할 것 |
|---|---|
| `/readyz` 503 `speech_configuration_invalid` | `fields`에 나온 설정 이름을 고친다. JSON의 위치·파일 권한도 확인한다. 성공해도 실제 인증 성공을 뜻하지 않는다 |
| `unauthenticated` / 401 | 키가 지워지거나 사용 중지됐는지, 서버 시각이 맞는지 확인한다 |
| `permission_denied` / 403 | Speech-to-Text API 사용 설정, 같은 프로젝트의 Cloud Speech Client 역할, 결제 연결을 확인한다 |
| `resource_exhausted` / 429 | 콘솔의 할당량과 결제·크레딧을 확인한다. 같은 말을 계속 반복하지 않는다 |
| `invalid_argument` / 400 | 모델은 `chirp_3`, 지원 지역과 언어인지 확인한다. 공급자 변경 때 남은 `scribe_v2` 값을 지운다 |
| `not_found` / 404가 용어를 보낼 때만 남음 | 기본 설정은 이미 adaptation을 보내지 않는다. 사람이 `VOINEY_LAB_GOOGLE_STT_V2_ADAPTATION_ENABLED=true`로 시험할 때는 아래 같은-WAV 진단을 실행한다. 서버는 그 턴을 adaptation 없이 한 번 재시도하고, 성공하면 같은 세션에서 용어 힌트를 끈다 |
| `timeout`, `unreachable`, `unavailable` | EC2의 외부 HTTPS 연결과 Google 서비스 상태를 확인한다 |
| WIF 인증 확인 명령이 실패 | EC2 IAM 역할 연결·IMDSv2 설정, 공급자의 AWS 계정/역할 조건, 서비스 계정의 Workload Identity User 바인딩, IAM Credentials·STS API를 확인한다. 파일의 URL·audience를 직접 임의 수정하지 말고 `create-cred-config --aws --enable-imdsv2`로 다시 만든다 |
| 개발자 ADC는 되는데 EC2 WIF만 실패 | EC2 서비스의 오래된 AWS 액세스 키 환경 변수를 제거하고, 자격 파일 소유자·권한과 metadata 연결을 확인한다. 사용자 로그인 파일을 운영에 복사하지 않는다 |

용어를 보낸 요청만 400·404이고 같은 요청에서 용어를 빼면 성공하는지 보려면, 서버 설정을 안전하게 셸 환경에 읽은 뒤 같은 16 kHz 모노 PCM16 WAV 하나로 아래 명령을 사람이 실행한다. 이 스크립트는 `.env`를 직접 읽지 않고 정확히 세 번만 인식한다: 현재 지역+공식 인라인 모양, 공식 지원표의 다른 다중 지역(`us`이면 `eu`, 그 밖이면 `us`)+같은 모양, 현재 지역+adaptation 없음. 인식문은 출력하지 않고 시나리오·지역·HTTP 상태·글자 수·응답 시간(ms)만 출력한다. WAV가 손상·잘렸거나 PCM 인코딩이 아니면 그 이유와 필요한 16 kHz 모노 PCM 형식을 말한다. `--max-calls`는 3~10만 받으며 아래 값 10은 상한이지 실제 호출 수가 아니다.

```bash
set -a; source .env; set +a; source .venv/bin/activate; python scripts/check_google_stt_v2_adaptation.py --execute --wav /absolute/path/to/the-same-recording.wav --max-calls 10
```

공식 근거는 [REST V2 SpeechAdaptation·인라인 PhraseSet 모양](https://docs.cloud.google.com/speech-to-text/docs/reference/rest/v2/projects.locations.recognizers), [Chirp 3 기능·`us`/`eu` 지역과 1,000구절 한도](https://docs.cloud.google.com/speech-to-text/docs/models/chirp-3), [모델·지역·언어별 기능표](https://docs.cloud.google.com/speech-to-text/docs/speech-to-text-supported-languages), [일반 adaptation 한도와 boost 최대 20](https://docs.cloud.google.com/speech-to-text/docs/quotas)다. 현재 제품 모양·기본 `us`·다섯 구절은 이 표 안이고 boost는 보내지 않는다. 그런데 2026-10-29 같은 한국어 WAV 실측은 `us` 힌트 404·3,573 ms, `eu` 힌트 404·5,021 ms, `us` 힌트 없음 200·3글자·3,915 ms였다. Google 내부 원인은 확인 안 됨이며 기본 지역은 `us`, adaptation 기본은 false다. 결과의 세 줄과 실행 시각만 사고 기록에 남기고 WAV·인식문·자격은 남기지 않는다.

실패해도 다른 공급자로 자동으로 바뀌지 않는다. 기본 false에서는 첫 요청부터 adaptation을 빼며, true에서만 adaptation 400·404를 같은 공급자·모델에서 힌트 없이 한 번 더 보낸다. 성공하면 화면 턴 진단에 `stt_adaptation_disabled`, 지표에 `stt_adaptation_fallback`을 남긴다. 그 밖의 실패는 화면에 실패 코드와 까닭이 보이고, 같은 장애에서는 한 번만 말로 알린다. V2 내장 잡음 제거는 `VOINEY_LAB_GOOGLE_STT_V2_DENOISE=false`로 끌 수 있으나 2026-10-28 실측에서는 이 404와 무관했다. 실제 인증·지역 지원·인식 품질은 가짜 전송 시험만으로 확인할 수 없다.

### 고정 주소와 매일 백업

- HTTPS 502/터널 연결 실패: EC2에서 두 서비스 상태와 로컬 `/readyz`부터 확인한다. 로컬 성공·외부 실패면 터널 UUID·DNS·인증서·ingress 호스트를 확인한다. 등록처 네임서버 변경 후 DNSSEC의 옛 DS가 남았는지도 확인한다.
- 로그인은 되는데 쿠키 쓰기·`/ws`가 거절됨: 새 주소의 Host·Origin·`X-Forwarded-Proto`가 일치하는지 확인한다. 앱 Origin 목록을 `*`로 넓혀 해결하지 않는다. 새 도메인에서는 다시 로그인·마이크 허용이 필요하다.
- 일일 백업 실패: `voineylab-backup.service`의 성공/실패 상태와 `voineylab.service` 재시작 상태를 함께 확인한다. 꺼져 있던 서버에서는 의도대로 백업이 실패한다. 핵심 DB가 없으면 데이터 경로를 확인하며 빈 DB를 만들어 통과시키지 않는다.
- 백업 때 원치 않는 연결 끊김: 타이머를 끄고 실험이 없는 시각으로 `OnCalendar`를 바꾼다. 이미 진행 중인 백업은 임의 종료하지 않고 담당자가 상태를 확인한다. 설치·복구 순서는 [고정 주소 안내](DOMAIN_AND_KEYLESS_GUIDE.md) 6절이다.
