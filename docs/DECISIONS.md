# 결정 기록 — VoineyLab

사람이 정한 것을 주제별로 모은다. 한 줄에 날짜 · 결정 · 이유 · 관련 줄(lane) · 지금 코드에서 어디 · 남은 질문.

- 줄 이름(DI, RV2 …)과 날짜 같은 이력은 **이 문서에만** 쓴다. 다른 문서는 기능 단위로 쓰고 여기를 가리킨다.
- "코드에서" 칸은 2026-10-20 dev `e7c31c8` 에서 확인한 파일·이름이다. 확인하지 못한 것은 "확인 안 됨"이라고 적었다.
- 근거 보고서는 저장소 밖(`~/reports/lane_<줄>_report.md`)에 있다. 저장소 안의 근거는 PR 번호와 `docs/MIGRATION_NOTES.md` 의 같은 이름 절이다.
- 결정이 아니라 **권고**로만 남은 것은 "권고"라고 적었다. 사람이 답한 기록을 찾지 못한 것이다.

## 1. 상태 권한과 말 한 번의 길

| 날짜 | 결정 | 이유 | 줄 | 코드에서 | 남은 질문 |
|---|---|---|---|---|---|
| 2026-10-02 (D1), 10-03 | 말 한 번은 한 줄로 간다: 앞단 규칙 → LLM 라우터(답하거나 도구로 바꾸기를 **제안**) → 서버 검증. 같은 말의 상태 바꿈을 두 길이 따로 정하면 안 된다 | 모델은 제안만 하고, 상태 기계는 하나(`CuratedProtocolSession`)여야 한다 | R, R2a | `curated_protocol.py` `CuratedProtocolSession.front_plan`, `apply_tool_proposal`; `llm_router.py` `route_turn_with_llm_router`, `validate_tool_proposals` | 파일럿에서 라우터를 켤지 |
| 2026-08-19 | 음성은 Cascade(말 → 글 → 말) 하나뿐이다. Native/Realtime 길은 문서에도 설정에도 두지 않는다 | 실행되고 시험된 길만 문서에 쓴다 | — | `server.py` `ready` 의 `pipelines=["cascade"]` | — |
| 2026-10-08 | **승인 단계가 없다.** 분석이 통과(원문 근거 검사 포함)하고 실험자가 시작을 누르면 실행된다. 검토자·관리자 승인, 회수, 연구실 변형은 없다 | MVP 줄이기. 사람 확인은 시작 하나이고 장부에 남는다 | DI (PR #52) | `experiment_protocol.py` `EXECUTION_BLOCKING_REASON_CODES`, `execution_blocking_reasons`; `protocol_catalog.py` `available_for_execution` | — |
| 2026-10-08 | 옛 `procedures.py`·`procedure_store.py` 작업 흐름을 지운다. 두 번째 작업 흐름은 다시 만들지 않는다 | 상태 기계는 하나 | DI | 두 파일 없음 | — |
| 2026-10-14 | 라우터 먼저 스위치 `VOINEY_LAB_ROUTER_FIRST` 는 **꺼진 채** 둔다. 사람이 음성 시험을 한 뒤 사람이 켠다 | 평가 세트에서 알아들음 77.1 % → 81.8 %, 잘못 바뀐 상태 0, 하지만 지연은 사람 시험이 필요 | RA (PR #74) | `setting_names.py` 기본 `false`; `llm_router.py` `ROUTER_FIRST_ENV`(라우터가 켜져 있어야 함) | ① 켤지 ② 조각 잇기를 스위치 밖으로 뺄지 |
| 2026-10-17 | 라우터 모델이 요청 한도(HTTP 429)에 걸리면 규칙 길로 넘기지 않고 그 말을 멈추고 "지금 답하지 못했어요. 다시 말씀해 주세요." 라고 한다 | 한도에 걸린 순간 규칙이 대신 상태를 바꾸면 사람이 모르는 결과가 생긴다 | FX3 | `llm_router.py` `ROUTER_MODEL_UNAVAILABLE_KINDS`; `server.py` `ROUTER_UNAVAILABLE_WORDS` | — |

## 2. 분석 · 모델 · 공급자

| 날짜 | 결정 | 이유 | 줄 | 코드에서 | 남은 질문 |
|---|---|---|---|---|---|
| 2026-10-23 | 분석 비교 도구를 현재 분석 JSON 파서로 복구한다. 한글을 보존하고, 번호가 없거나 겹치는 단계는 쪽·원문 인용으로 비교한다. 모델 ID를 비교 근거로 쓰지 않는다. 원문 근거 검사 결과와 누락·추가 차이는 별도이며 실행 판정은 만들지 않는다 | 지운 주장 조각 캐시를 읽던 도구가 실행되지 않았고, 번호만으로 비교하면 항목이 겹쳤다 | pilot-foundation | `scripts/score_extraction.py`, `protocol_extraction_accuracy.py` | 기준은 사람이 만든 비교 자료이고 완전성을 보증하지 않는다. 실시간 누락 검사는 바꾸지 않는다 |
| 2026-10-10, 10-16 | 분석 역할은 Google `gemini-3.8-flash`, 추론 `low`. 번역도 `low`. 보고서 서술은 `gemini-3.8-flash`. 라우터·답변·보조 설명은 OpenAI `gpt-6-luna` | 선택 규칙 "p50 ≤ 120 s, p90 ≤ 300 s 안에서 통과가 가장 많은 것": 25개 문서에서 3.8 Flash `low` 만 안에 들었다(p50 30.8 s, p90 221.6 s, 통과 14) | AQ, M3, GM | `.env.example` 의 역할 값. **코드는 설정이 비면 추론을 `high` 로 읽는다**(`server.py` `_protocol_analysis_model`, `setting_names.py` 의 기본값) | 줄 M3 은 `medium` 을 권했다. 지금 값은 GM 권고(`low`) |
| 2026-10-16 | Gemini 4 는 분석에 쓰지 않는다 | 지연 규칙을 넘고 비용이 약 4배, 통과 12 대 14 | GM | `.env.example` 에 없음 | 권고. 사람 결정 기록 확인 안 됨 |
| 2026-10-10 · 13 | 모든 모델 역할과 STT·TTS 공급자는 직접 적는다("설정 필요"). 빈 역할은 다른 공급자로 가지 않고 그 설정 이름을 적은 오류로 닫힌다 | 아무도 고르지 않은 공급자로 요청이 가면 안 된다 | CL, PL, AN, RA | `setting_names.py` `_SET_ME`; `model_providers.py` `DEFAULT_MODELS`, `RoleNotConfiguredError` | — |
| 2026-10-16 | 분석·번역·보고서의 Gemini 요청은 가장 느슨한 안전 필터(`relaxed`, 6개 분류 모두 `OFF`)로 보낸다. `default`·`strict` 도 고를 수 있다. 음성 턴 역할에는 보내지 않는다 | 안전 필터 차단이 긴 문서 전체를 멈췄다 | GM (PR #87) | `model_providers.py` `DEFAULT_GEMINI_SAFETY`, `gemini_safety_settings`; 설정 `VOINEY_LAB_GEMINI_SAFETY` | — |
| 2026-10-16 | 안전 필터가 막은 답은 바로 한 번 더 보낸다 | 다시 보내면 통과한 조각이 있었고, 막힌 시도는 토큰이 들지 않는다 | AN3 | `model_providers.py` `BLOCKED_RESENDS = 1` | — |
| 2026-10-02 · 06 | OCR 은 두 엔진(NAVER CLOVA, Google Cloud Vision)을 쓰고, 올리기의 OCR 결과는 자동으로 받아들인다 | 승인 단계 없이 흐름이 이어져야 한다 | S, PX | `protocol_ocr_providers.py` `ClovaOcrRecognizer`, `GoogleVisionRecognizer`; `protocol_catalog.py` `AUTOMATIC_OCR_AUTHORITY` | — |
| 2026-10-13 | 긴 문서는 15쪽(겹침 1쪽) 조각으로 400쪽까지 분석한다. 한 번에 읽는 한도는 60쪽. 옛 주장 조각(claim-chunk) 길은 지운다 | 긴 설명서가 통째로 실패했다 | AN | `experiment_protocol_analysis.py` `MAX_ANALYSIS_PAGES`, `ANALYSIS_PIECE_MAX_PAGES`, `MAX_PIECED_ANALYSIS_PAGES` | 옛 길을 쓰던 진단 스크립트 9개가 import 에서 실패한다(아래 "알려진 빈틈") |
| 2026-10-14 | 분석·번역·보고서 호출은 429·일시적 5xx 에 최대 5번 다시 보낸다(기다림 최대 60 s). 조각은 8개씩 동시에, 429 가 오면 절반으로 | 조각 분석 중 요청 한도 | AN2 | `model_providers.py` `ProviderRetryPolicy`, `DEFAULT_PROVIDER_RETRY_MAX`; `experiment_protocol_analysis.py` `PieceConcurrencyGate` | — |
| 2026-10-16 · 18 | 스캔 값은 두 OCR 엔진이 **자리마다** 같게 읽어야 정해진다. 한 쪽의 값은 카드 한 장. 숫자가 15 % 이상 다르거나 신뢰도가 0.90 미만인 쪽은 아무것도 자동으로 정하지 않는다. 안전 주의 속 숫자도 견준다. 조각 문서는 자동 다시 분석을 하지 않는다 | 실측에서 두 엔진이 나쁜 쪽을 똑같이 잘못 읽은 자동 통과 4건 | AN3 (PR #88·#91·#95) | `protocol_catalog.py` `OCR_PAGE_MAX_DIFFERING_SHARE`, `OCR_PAGE_MIN_CONFIDENCE`, `_ocr_page_gate` | 기준이 문서 하나에서 나왔다. 파일럿 자료로 다시 잴 것 |
| 2026-10-19 | 첫 분석 뒤 근거 검사가 거부한 항목만 그 쪽 글과 함께 한 번 더 묻고, 받은 구간도 같은 검사를 거친다 | 카드 −14 %(154 → 133), 잘못 받아들인 것 0 | AN4 (PR #103) | `experiment_protocol_analysis.py` `requote_unverified_items`; 설정 `VOINEY_LAB_ANALYSIS_REQUOTE` | 순서 검사 90 % |
| 2026-10-20 | 분석 마무리: 한 번 읽은 분석(또는 조각 하나)에도 번호 단계와 번호 없는 단계를 원문대로 받고 경계를 카드로 묻는다; 쪽 끝을 넘는 인용은 쪽 번호·머리말 같은 여백 줄을 건너 받는다; 되묻기는 분석을 저장한 뒤 300 s 안에 돌고 다시 찾은 카드를 서버가 닫는다; 분석·번역 호출마다 글 없는 기록 한 줄 | 카드를 띄운 채 기다리지 않게, 섞인 번호를 실패로 보지 않게 | AN5 (PR #109) | `experiment_protocol_analysis.py` `REQUOTE_TIME_LIMIT_SECONDS`; `docs/MIGRATION_NOTES.md` "Lane AN5". 이 문서의 나머지 반영은 문서 다시 쓰기 2단계 | — |

## 3. 확인 카드와 안전 주의

| 날짜 | 결정 | 이유 | 줄 | 코드에서 | 남은 질문 |
|---|---|---|---|---|---|
| 2026-10-12 · 14 | 근거 검사가 다 확인하지 못한 분석은 버리지 않고 "검토 대기"로 둔다(실행 경로가 읽지 못하는 장부 사건). 사람이 항목마다 정하고 검토본(v1, v2 …)을 등록하면 최신 검토본이 실행된다. 다 통과한 분석에는 검토가 필요 없다 | 거의 맞는 분석을 버리지 않되, 확인 안 된 값은 구조상 실행되지 않게 | RV (PR #81·#83) | `protocol_catalog.py` `_REVIEW_PENDING_EVENT`, `register_review`, `load_executable_fixture` | **검토를 의무로 할지** (OCR 쪽·긴 문서·고친 응답) |
| 2026-10-14, 10-16 | 안전 주의 글은 검토에서 고칠 수 없다(메모만). 원문에 없는 것은 비울 수 있고 그러면 실행되지 않는다. 확인한 것은 원문 그대로 실행된다 | 안전 주의는 AI 가 쓰지도, 검토가 고쳐 쓰지도 않는다 | RV, RV2 | `protocol_catalog.py` `_FIXABLE_KINDS`(warning 없음), `_CLEARABLE_KINDS`. 예외: 안전 주의 속 OCR 숫자는 **다른 엔진의 읽기 중 하나**만 고를 수 있다 | — |
| 2026-10-16 | 검토는 **확인 카드**로 줄인다. 서버가 물어야 할 항목만 한 장씩, 서버가 허락한 단추로. 다 통과한 분석은 카드 0장. 항목이 12개를 넘으면 카드를 만들지 않고 추론 한 단계 위로 한 번 다시 분석하고(16회 이내), 그래도 많으면 안내만 | 목표: 카드 중앙값 ≤ 3, p90 ≤ 8 | RV2 (PR #86·#90) | `protocol_catalog.py` `review_cards`, `REVIEW_MAX_ITEMS`, `REVIEW_REANALYSIS_MAX_CALLS`; 설정 `VOINEY_LAB_REVIEW_MAX_ITEMS`, `VOINEY_LAB_REVIEW_REANALYSIS_MAX_CALLS` | 잰 값은 중앙값 3·p90 10(목표 밖), 실측 문서 세트에서는 2·8 |
| 2026-10-20 | 관리자 계정은 실험 노트와 검토본에 "확인함"(이름·시각·한 줄)을 남길 수 있다. **시작을 막지 않는다** | 설문의 "PI 검토" 요청. 승인 단계는 두지 않는다 | RP3 (PR #106) | `server.py` `pi_confirmation_admin_only`; `protocol_catalog.py` `confirm_review_version` | 화면은 아직 없다 |

## 4. 안전 문서 범위와 지운 연결

| 날짜 | 결정 | 이유 | 줄 | 코드에서 | 남은 질문 |
|---|---|---|---|---|---|
| 2026-10-13 ("B안") | 파일럿 실행기는 기관 안전 문서 카탈로그 없이 사용 범위 `source_protocol_only`(올린 프로토콜의 원문 안전 주의만)로 시작하고, 화면·답·기록이 "기관 안전 문서 없음 · 원문 안전 주의만"이라고 말한다. 있는데 쓸 수 없는 카탈로그는 시작을 거부한다 | 파일럿 연구실에 기관 문서가 없었다 | PL (PR #72·#75) | `scripts/run_pilot.sh`; `safety_pack.py` `SOURCE_PROTOCOL_ONLY_SCOPE`, `NO_INSTITUTION_DOCUMENTS_NOTICE` | — |
| 2026-10-10 · 14 | MOSS(재순위·색인 동기화·설정 12개·승인 매뉴얼 음성 검색)를 지운다 | MVP 가 쓰지 않는 코드 | CL, DOC3 | `moss` 코드 없음. `VOINEY_LAB_MOSS_ENABLED` 는 표준 시험 명령의 글자로만 남음 | 지난 기록·매뉴얼 검색을 다시 넣을지 |
| 2026-10-08 · 13 · 14 | xAI 전용 기능을 지운다: 의미 판정 도우미·웹 이미지 검색·이미지 생성(10-08), grok 대체(10-13), xAI 웹 근거 검색과 설정 13개(10-14) | 빈 역할이 xAI 로 가면 안 되고, 검색은 꺼져서 쓰이지 않았다 | DI, PL, DOC3 | `configuration.py` `XAI_ONLY_FEATURES = {}`. xAI 는 고를 수 있는 공급자로 남아 있다(`model_providers.py` `PROVIDERS`) | — |
| 2026-10-08 | eLabFTW 내보내기, protocols.io·Drive·GitHub 연결(옛 커넥터), dry-lab 메타데이터를 지운다 | MVP 줄이기 | DI | 해당 모듈 없음 | Drive 는 10-20 에 링크 가져오기로 따로 다시 들어왔다(5절) |

## 5. 원문 형식 · 라이선스 · 링크

| 날짜 | 결정 | 이유 | 줄 | 코드에서 | 남은 질문 |
|---|---|---|---|---|---|
| 2026-10-02 · 06 | PDF 엔진은 PyMuPDF 하나, AGPL-3.0 으로 쓰되 한 모듈 뒤에만 둔다. 상용화 전에 상용 라이선스를 사거나 그 모듈 뒤에서 엔진을 바꾼다 | 세 엔진 대조를 엔진 하나로 바꿨다 | S, PX | `pdf_text_engine.py` 만 `pymupdf` 를 import (`tests/test_lane_px_pymupdf_rules.py`) | 상용 라이선스 대 교체 |
| 2026-10-13 | Word(.docx)는 서버의 LibreOffice(+ `fonts-nanum`)로 PDF 로 바꾼다. 없으면 Word 올리기만 거절하고 서버는 시작한다 | 연구실은 프로토콜을 Word 로 갖고 있다 | AN | `experiment_protocol_pdf.py` `docx_to_pdf`, `find_office_converter` | — |
| 2026-10-13 | HEIC 사진은 선택 설치(`.[heic]`, pillow-heif) | pillow-heif 바퀴가 통째로 GPLv2 | AN | `pyproject.toml` `heic` | README 는 날짜를 10-14 로 적었었다. `pyproject.toml` 의 주석은 10-13 |
| 2026-10-14 | 파일럿 실행기는 LibreOffice 가 없으면 `[WARN]` 만 찍고 시작한다 | PDF 만 올리는 파일럿에는 필요 없다 | DOC3 | `scripts/run_pilot.sh` 의 `--- Source formats ---` | — |
| 2026-10-20 | 공개 Google Drive 파일·Docs(PDF 로)·Sheets(시약 표로, 있는 프로토콜에만) 링크로 가져온다. 호스트를 정해 두고, 리디렉션 5회, 공인 주소만, 올리기와 같은 크기 한도, 바이트로 형식 판단, 로그인 필요, 비공개 파일은 서버 문장으로 거절(OAuth 없음) | 설문(10-19, 13명)에서 프로토콜을 두는 곳으로 Google Drive 가 가장 많았다(5명) | DR (PR #107) | `protocol_link_import.py` `ALLOWED_LINK_HOSTS`, `MAX_REDIRECTS`; `server.py` `import_protocol_link`. 시험은 가짜 HTTP(contract-tested) | 화면 단추는 아직 없다. protocols.io 가져오기와 비공개 파일 OAuth 는 나중 결정 |

## 6. 계정 · 파일럿 운영 · 지표

| 날짜 | 결정 | 이유 | 줄 | 코드에서 | 남은 질문 |
|---|---|---|---|---|---|
| 2026-10-16 · 18 | 계정은 초대로만 만든다. `VOINEY_LAB_AUTH_MODE` = `dev`(기본) 또는 `accounts`. 초대 링크(서버 셸에서 만듦, 만료, 한 번)로만 가입, scrypt 비밀번호, HttpOnly 쿠키. 검토자 이름 = 계정 이름 | 10월 말부터 석사생 두 명이 파일럿을 한다 | ON (PR #93·#97) | `accounts.py` `AUTH_MODES`, `AccountStore.create_invite`; `scripts/pilot_invite.py` | 웹의 `/ws` 출처 검사를 늘 할지 |
| 2026-10-19 | 파일럿 실행기는 기본으로 로그인(`accounts`)을 켠다. 개발 신원은 `--dev-identity` 를 줄 때만 | 파일럿 계정은 실제여야 한다 | FX4 | `scripts/run_pilot.sh` `PILOT_AUTH_MODE` | — |
| 2026-10-16 · 18 | 파일럿 지표는 숫자·코드·시각만 둔다(말한 글·소리·프로토콜 글·모델 출력 없음). 모든 행에 계정 id. 자유 글은 끝 평점 밑 한 줄 메모뿐이고 지표가 아니다 | 개인정보 최소화 | LOG (PR #94) | `pilot_metrics.py` 머리 설명; `scripts/pilot_metrics.py` | — |
| 2026-10-13 · 20 | 파일럿 데이터 삭제 도구(미리보기 → 확인 단어 → 삭제 → 기록), 계정별(`--account`)은 그 계정의 지표 행까지 지운다. 계정이 스스로 지우는 경로도 있고 지우는 범위는 도구와 같다. 마지막 관리자는 지울 수 없다 | 요청이 오면 지울 수 있어야 한다 | PL, ON, FX5 (PR #108) | `scripts/pilot_data_delete.py`; `accounts.py`; `server.py` `POST /api/auth/delete-account` | 디스크 조각의 안전 삭제는 사람이 |
| 2026-10-20 | 시작 첫날 쓸 스타터 팩: 공개 라이선스(CC BY·CC0·퍼블릭 도메인, NC/ND/SA 거절) 프로토콜 12개를 서버와 같은 카탈로그 호출로 미리 분석한다. `--dry-run` 비용 추정, 다시 실행해도 같은 결과 | 파일럿 참여자가 아직 프로토콜을 주지 않았다 | PK (PR #105) | `scripts/pilot_starter_pack.py` `license_allowed`; `scripts/pilot_starter_pack.csv` | 배터리 셀 조립 프로토콜 없음, 미리 번역할지, 카드 9장을 누가 볼지 |

## 7. 음성 · 듣기

| 날짜 | 결정 | 이유 | 줄 | 코드에서 | 남은 질문 |
|---|---|---|---|---|---|
| 2026-10-23 | 인식된 응급 말은 빈 입력·되울림을 거른 뒤 기존 고정 응답 관문에서 처리하고, 보정·언어 확인·필러 분류가 먼저 소비하지 않는다. 이미 인식된 응급 말에는 앞 발화 조각을 붙이지 않고, 조각을 붙여 완성된 응급 말도 같은 관문으로 간다 | 보정 대기 중의 응급 말이 보정 입력이 됐다 | pilot-foundation | `server.py` `run_turn`, `tests/test_emergency_admission_boundary.py` | STT 전에 버린 소리와 현장 인식 성능은 이 변경으로 검증되지 않는다 |
| 2026-10-15 (권고) | 말 끝 침묵은 **1초(1000 ms)를 유지**한다. 부족하면 1200 ms 가 가장 싼 다음 단계, 1500 ms 이상은 권하지 않는다 | 1500 ms 는 모든 말을 약 0.5 초 늦춘다 | SP2, FX3 | `vad.py` `VAD_END_SILENCE_MS = 1000`; 설정 `VOINEY_LAB_CASCADE_VAD_ENDPOINT_SILENCE_MS`. 1.2 s 를 2026-10-16 에 해 보고 같은 날 되돌림(`tests/test_lane_fx3_endpoint_pause.py`) | 권고로 기록됨. 실제 녹음으로 다시 잴 것 |
| 2026-10-15 (권고) | **목소리 등록(화자 확인)은 보류**한다. 다른 사람 말은 목소리 크기로만 거른다 | 합성 목소리 측정: EER 9.5 %(조용) · 20–21 %(웅성거림), 1초 미만 명령은 27–42 % — "다음"을 가를 수 없다. 학습 데이터 권리·생체정보 동의는 법률 검토가 필요 | SP1, SP2 | 등록 코드 없음. `server.py` `LEVEL_ENDPOINT_FLOOR_GAP_DB`, `ROOM_VOICED_SHARE`; `vad.py` `SpeakerLevelReference` | 만들지, 법률 검토, 동의한 팀원의 실제 녹음으로 다시 잴 것 |
| 2026-10-15 | 목소리를 맞춘 세션에서는 VAD 가 계속 말소리를 들어도(웅성거림·펌프) 착용자 목소리 크기로 말 끝을 찾고, 주변 소음 안내는 10분에 한 번까지 | 합성 시험대에서 명령이 97–100 % 사라졌다 | SP2 (PR #85) | `vad.py` `EndpointDetector`, `NoiseFloor` | 모든 기준값을 실제 카페·연구실 녹음으로 다시 |
| 2026-10-08 | 현장 인터뷰에서: 값 확인 방식은 실험자 설정, 소수점은 잃지 않는다, "방금 완료 취소"·"이전 단계로"는 사건을 덧붙인다, 시작 전에 묻는다, 기록한 것은 지우지 않는다 | 생명 분야 연구자 3명 인터뷰(10-07·08) | CF (PR #55) | `curated_protocol.py` 앞단 규칙 `experimenter_setting`, `step_revert` | — |
| 2026-10-09 | "다음 단계"는 말의 꼴로 넘어간다(매번 "N단계 완료하셨나요?"를 묻지 않는다). 원문에 없는 타이머는 만들지 않는다 | 인터뷰: "단순한 명령은 바로, 중요한 데이터만 확인", "타이밍을 놓치면 안 된다" | VX | 앞단 규칙 `forward_step`; `curated_protocol.py` `start_timer` | 단계 이름 없는 "완료했어"도 단순 명령인지 |
| 2026-10-09 | 올린 프로토콜이 먼저다. 웹 설명과 그림은 설명만 하고 값이나 안전 주의를 주지 않는다 | 인터뷰의 첫 걱정: 올린 것과 다른 조건을 들을까 | WV | `web_explanations.py` `check_sentences`; `drawn_diagrams.py` `sanitize_svg`, `source_numbers` | — |

## 8. 보고서와 실험 노트

| 날짜 | 결정 | 이유 | 줄 | 코드에서 | 남은 질문 |
|---|---|---|---|---|---|
| 2026-10-19 | 보고서 앞쪽을 한눈에, LibreOffice 로 PDF 내보내기, 확인 안 된 값은 "[미확인]" 표시와 제목 아래 개수(내보내기를 막지는 않음). 숫자는 모두 기록에서 결정적으로 | 실험 흐름이 보이지 않았다 | RP2 (PR #102) | `experiment_reports.py` `export_pdf`, `report_docx_to_pdf`. 앞쪽 구성은 아래 실험 노트가 대신했다 | — |
| 2026-10-19 · 20 | 보고서를 연구자의 **실험 노트**로 바꾼다(제목 = 말한 목적, 1 시료와 조건 … 8 부록). 시작 때 목적·시료, 끝에 결과·다음에 바꿀 점, 관찰 대상이 있는 단계에서 관찰을 묻는다. AI 는 검사를 통과한 "AI 정리"만 쓴다. 랩미팅 요약(Word·PDF)과 결과 표(xlsx) | 10-19 평가: 보고서가 음성 시험 중심이라 연구자에게 쓸모가 없었다 | RP3 (PR #106) | `experiment_reports.py` `build_lab_notebook`, `check_ai_notes`, `export_meeting_pdf`, `export_xlsx`; 설정 `VOINEY_LAB_NOTEBOOK_OBSERVATION_QUESTION` | 화면에는 Word·Markdown·JSON·CSV 링크만 있다 |

## 9. 연결 · 앱 · 말 · 화면

| 날짜 | 결정 | 이유 | 줄 | 코드에서 | 남은 질문 |
|---|---|---|---|---|---|
| 2026-10-23 | 공개 Drive 파일·Docs 링크 입력을 새 프로토콜 올리기에 연결하고, 등록 응답·분석·OCR·중복·결과 표시를 파일 올리기와 공유한다. Sheets 시약 표는 API로 남긴다 | 기존 자료를 내려받아 다시 올리는 수고를 줄인다 | pilot-foundation | `static/index.html` `registerProtocolLink`, `acceptRegisteredProtocol`; `tests/test_protocol_link_screen.py` | 실제 태블릿·Google 다운로드는 별도 시험. 브라우저 시험은 CI |
| 2026-10-18 | 연결 끊김은 3초 안에 알아채고(1초 심장 박동, 2.8초 무응답), 소리·진동과 큰 띠로 알리고, 화면을 읽기 전용으로, 마이크를 멈춘다. 화면은 서버 시각으로 단계 타이머를 이어 세고, 1·2·4·8·15초 간격으로 다시 이어 같은 실험을 계속한다 | 와이파이가 끊겨도 안전해야 하고 타이머가 가장 중요하다 | NET (PR #92) | `server.py` `_send_heartbeats`, `_record_connection_restored`; `static/connection.js` | 웹에서는 화면 꺼짐을 막을 수 없다(앱 필요) |
| 2026-10-18 | 웹이 쓸모를 보인 뒤 스토어 앱(Capacitor 8.5.3 하이브리드 + 백그라운드 마이크·정확한 알람 플러그인). 앱 로그인은 허용한 출처로 | 화면을 끄고 듣기, 앱 밖 타이머 알림 | APP0, APP1 (PR #96·#98) | `mobile/`; 설정 `VOINEY_LAB_APP_ORIGINS` | 스토어 등록, 결제, iOS, 실제 기기 시험 |
| 2026-10-19 | 쉬운 말 규칙: 해요체 하나, 한 문장 한 뜻, 내부 말 금지(용어 사전), 단추는 동사, 오류 = 무엇이 일어났나 + 무엇을 하나. 숫자·단위·인용·안전 글은 그대로 | 사용자는 해요체와 합니다체를 섞어 듣는다(말하는 511줄 중 277 : 234) | WORD (PR #99) | 1단계는 문서만(`docs/VOICE_AND_TONE.md`). 코드 문구는 아직 바꾸지 않았다 | 2단계 진행 |
| 2026-10-19 | 화면을 다섯 탭(홈·프로토콜·실험·기록·설정)과 떠오르는 시트로 나눈다 | 한 화면에 모든 것이 있었다 | IA (PR #100) | `static/index.html` `TAB_PANELS`, `TAB_ROUTE_PATTERN` | 단계 ②③ 은 진행 중 |
| 2026-10-04 | 모든 환경 설정 이름에 `VOINEY_LAB_` 머리를 붙인다. 옛 `.env` 는 `scripts/migrate_env.py` 로 바꾼다. 옛 이름이 있으면 시작을 거부한다 | 이름 하나로 | V | `setting_renames.py`; `setting_names.py` `refuse_old_setting_names` | — |
| 2026-10-20 | 사람이 읽는 글의 제품 이름은 VoineyLab(회사 Voiney). 옛 이름 "Voice Workflow Agent" 는 코드 식별자·설정 이름·보관 문서·옛 기록 인용에만 남긴다 | 문서와 제품 이름을 맞춘다 | DOC4 | 이 결정은 문서만 바꾼다 | — |
| 2026-10-20 | 에이전트 규칙은 모두 `AGENTS.md` 한 곳에 쓴다(사전 점검, 줄과 작업 폴더, 무거운 실행 잠금, 다른 줄 프로세스, 병합 조건, AI 도구 표시 금지, 파괴적 git 금지, live/contract 구분 포함). `CLAUDE.md` 는 `@AGENTS.md` 한 줄 | 같은 규칙이 모든 에이전트에 필요하다. Claude Code 는 `AGENTS.md` 가 아니라 `CLAUDE.md` 를 읽고, `@경로` 줄로 다른 파일을 불러온다 | DOC4 | `CLAUDE.md`; `tests/test_docs_consistency.py` `AgentContractTests`(그 줄과 파일이 있는지) | — |
| 2026-10-20 | README 와 문서에 상태 문구("MVP 시제품 — 현장 미검증")를 쓰지 않는다. 검증된 것처럼 쓰지도 않는다: 성능·정확도 숫자는 측정 날짜·조건과 함께, 상용·인증·규제 준수 주장은 없다. 현장 검증 전이라는 사실은 `.agent/product_context.md` 한 줄에만 | 과장도, 상태 딱지도 없이 사실만 | DOC4 | 문서만 바꾼다 | `docs/PILOT_GUIDE.md` 5줄(2단계), `docs/APP_PLAN.md` 의 스토어 문구 계획 |

## 알려진 빈틈 (결정을 기다리는 코드 상태)

이 줄(DOC4)이 문서를 쓰며 코드에서 본 것이다. 코드는 고치지 않았다.

- 진단 도구 정리(2026-10-23, QA2): 지운 모듈을 부르던 Python 스크립트 9개와 그중 하나를 실행하던 `collect_chunks.sh`를 삭제했다. import 실패를 다시 확인했다. PR #116에서 복구한 `scripts/score_extraction.py`는 남겼다.
- 시작 전 핵심 값과 안전 확인은 서버가 계정·실행 버전에 묶어 검사한다(SAFE `c86b0fe`, QA2 재확인). `available_for_execution`과 함께 통과해야 시작한다. 명시적으로 시작 확인을 끈 설정은 기존 경로를 유지한다.
- `GET /readyz`는 사용 범위·안전 카탈로그를 로컬에서 검사하며, 읽을 수 없으면 이유 코드와 설정 이름만 담아 503으로 응답한다(2026-10-23, QA2). 공급자 호출은 하지 않는다.
- 미설정·빈 `VOINEY_LAB_USAGE_SCOPE`는 신원·설정 양쪽에서 그 설정 이름으로 거부한다(2026-10-23, QA2). 기동 준비의 카탈로그 집계는 요청 신원을 요구하지 않는다.
