# 기능 설명 — VoineyLab

지금 코드가 하는 일을 **기능 단위로** 적는다. 2026-10-20 dev `e7c31c8` 에서 확인했다.

- 기능마다: 무엇을 하나 · 어디(파일과 함수) · 설정 · 그것을 지키는 시험 · 한계.
  파일 이름 앞에 경로가 없으면 `src/voiney_lab/` 아래다.
- 왜 그렇게 정했는지와 날짜는 `docs/DECISIONS.md`, 구조와 상태 권한은 `.agent/architecture.md`, 화면 쓰는 법은 `docs/USER_GUIDE.md`.
- 설정 이름의 기본값과 뜻의 기준은 `src/voiney_lab/setting_names.py` 다. 여기 적은 기본값은 그 표의 값이다.
- 공급자 시험은 모두 가짜 전송으로 한다(**contract-tested**). 실제 외부 호출이 이 저장소의 시험에서 성공한 것은 없다.
  줄 보고서에 남은 실제 호출 기록은 [F17](#f17-공급자와-설정)에 날짜와 함께 있다.

목차: [F1 계정](#f1-계정과-로그인) · [F2 들여오기](#f2-프로토콜-들여오기) · [F3 OCR](#f3-사진-글자-읽기-ocr) · [F4 분석](#f4-분석) ·
[F5 원문 근거 검사](#f5-원문-근거-검사) · [F6 확인 카드](#f6-확인-카드) · [F7 시작](#f7-시작-전-확인과-시작) · [F8 듣기](#f8-듣기) ·
[F9 말 한 번의 길](#f9-말-한-번의-길) · [F10 단계·타이머](#f10-단계-완료-타이머) · [F11 질문과 설명](#f11-질문과-설명) ·
[F12 기록](#f12-실험-중-기록) · [F13 보고서](#f13-실험-노트와-보고서) · [F14 연결 끊김](#f14-연결-끊김) ·
[F15 파일럿 지표](#f15-파일럿-지표와-평점) · [F16 모바일](#f16-모바일-웹과-스토어-앱) · [F17 공급자·설정](#f17-공급자와-설정) ·
[F18 데이터·운영](#f18-데이터-삭제-백업-점검)

---

## F1. 계정과 로그인

- **무엇**: `VOINEY_LAB_AUTH_MODE` 가 `dev`(기본)면 로그인 없이 개발 신원 한 사람으로 쓴다. `accounts` 면 모든 `/api` 요청과 `/ws` 가 로그인을 요구하고, 가입은 **초대 링크로만** 된다(한 번, 기본 72시간).
  비밀번호는 scrypt 해시, 세션은 HttpOnly·SameSite=Lax 쿠키(HTTPS 요청이면 Secure), 30일. 같은 이름으로 15분 안에 5번 틀리면 그 이름이 잠긴다(429). 같은 Wi-Fi의 다른 계정은 잠기지 않으며 현재 비밀번호를 확인하는 변경 요청에도 같은 제한을 적용한다.
  관리자 계정은 파일럿 지표를 보고, 실험 노트·검토본에 "확인함"을 남기고, 연구실 계정을 끄고 켠다. 계정은 스스로 지울 수 있다(마지막 관리자는 안 됨).
  파일럿 실행기는 기본으로 `accounts` 로 연다(`--dev-identity` 를 주면 `dev`).
- **어디**: `accounts.py` (`AUTH_MODES`, `AccountStore.create_invite`, `sign_up`, `LOGIN_FAILURE_LIMIT`, `SESSION_LIFETIME`), `server.py` (`commercial_identity_boundary` 미들웨어, `_ACCOUNT_OPEN_PATHS`, `/api/auth/*` 경로, `POST /api/auth/delete-account`, `_account_is_admin`), `identity.py` (`DevIdentityProvider`, OIDC), `scripts/pilot_invite.py`.
- **설정**: `VOINEY_LAB_AUTH_MODE`, `VOINEY_LAB_APP_ORIGINS`(앱 출처에 토큰 로그인), `VOINEY_LAB_WORKSPACE_ENABLED`·`VOINEY_LAB_WORKSPACE_DATA_DIR`(계정은 작업공간이 있어야 한다), OIDC 는 `VOINEY_LAB_OIDC_ISSUER` 등.
- **시험**: `tests/test_lane_on_accounts_store.py`, `tests/test_lane_on_auth_api.py`, `tests/test_lane_fx4_pilot_login.py`, `tests/test_lane_fx5_account_erasure.py`, `tests/test_lane_fx5_delete_account_route.py`.
- **한계**: 초대 만들기·계정 목록은 화면이 없고 `scripts/pilot_invite.py` 로만 한다. 비밀번호 찾기(메일)는 없다. 로그인 잠금은 계정 이름 기준이며 전달 주소 머리글로 우회할 수 없다. OIDC 는 가짜 검증기로만 시험했다.

## F2. 프로토콜 들여오기

- **무엇**: PDF·Word(.docx)·사진(JPG·PNG·HEIC)을 원문으로 받는다. Word 와 사진은 서버에서 PDF 로 바꿔 같은 길로 보낸다. 같은 연구실 안에서 같은 파일(SHA-256)은 다시 등록하지 않고, 같은 PDF 를 다시 올리면 그 프로토콜의 검토본을 제안한다. 다른 연구실이 같은 파일을 올리면 별도의 카탈로그 항목으로 등록한다. 원문 바이트는 공유할 수 있지만 분석·검토·소유 범위는 연구실마다 따로 남는다. 스타터 팩 도구도 같은 규칙을 쓴다.
  엑셀(.xlsx)은 원문이 아니라 고른 프로토콜에 붙는 **시약 표**로 저장하고 분석에 쓰지 않는다.
  **공개 Google Drive·Docs·Sheets 링크**도 받는다: 서버가 내려받아 올리기와 같은 길로 넘기고(Docs 는 PDF 로, Sheets 는 있는 프로토콜의 시약 표로), 링크 주소를 장부에 출처로 남긴다. 비공개 파일은 "공유 설정을 바꾸거나 파일을 내려받아 올려 주세요"라는 서버 문장으로 거절한다.
  원문 저장·변환·PDF 추출과 OCR 상태 조회는 작업 스레드에서 실행한다. 업로드가 취소돼도 진행 중인 쓰기·등록이 끝난 뒤 임시 파일을 닫고 정리한다. 등록 뒤 OCR 상태만 읽지 못하면 등록 성공은 유지하고 OCR 상태 조회 실패를 알린다. 목록은 연구실 범위를 먼저 거르고 저장된 원문 메타데이터·분석·원장으로 만들며 PDF를 다시 추출하지 않는다. 예전 업로드의 누락된 메타데이터는 서버 시작 때 한 번 보충한다. 검증된 불변 분석 개정은 원문·개정·레코드 바이트를 기준으로 재사용하고, 바이트가 달라지면 저장소의 검증을 다시 거친다.
- **어디**: `server.py` `register_protocol_pdf`, `attach_protocol_reagent_table`, `import_protocol_link`; `experiment_protocol_pdf.py` (`SOURCE_MEDIA_TYPES`, `convert_source_to_pdf`, `docx_to_pdf`, `image_to_pdf`, `find_office_converter`); `protocol_link_import.py` (`parse_google_link`, `fetch_google_link`, `ALLOWED_LINK_HOSTS`, `MAX_REDIRECTS`, `sniff_media_type`, `LINK_IMPORT_MESSAGES`); `protocol_catalog.py` (`ProtocolCatalog.register`, `read_reagent_workbook`, `review_version_suggestion`); 저장은 `experiment_protocol_files.py`(`objects/sha256/…`)와 `experiment_protocol_store.py`.
- **한도(코드 값)**: 원문 64 MiB, 사진 1억 화소, 시약 표 5 MiB·셀 20,000개, Word 변환 120 s. 링크: 호스트 `drive.google.com`·`docs.google.com`·`drive.usercontent.google.com`, 리디렉션 5번, 공인 주소만, 전체 60 s.
- **설정**: `VOINEY_LAB_PROTOCOL_ENABLED`, `VOINEY_LAB_PROTOCOL_DATA_DIR`, `VOINEY_LAB_OFFICE_CONVERTER`(LibreOffice `soffice` 경로), `VOINEY_LAB_PDF_WORKER_TIMEOUT_SECONDS`(기본 30, 긴 문서는 300 권장 — `docs/DEPLOYMENT_RUNBOOK.md`). HEIC 는 선택 설치 `pip install -e '.[heic]'`.
- **시험**: `tests/test_lane_an_source_formats.py`, `tests/test_lane_an_reagent_tables.py`, `tests/test_lane_dr_link_import.py`, `tests/test_lane_dr_link_security.py`(가짜 HTTP), `tests/test_lane_dr_link_messages.py`.
- **한계**: LibreOffice 가 없으면 Word 만 `docx_conversion_unavailable`(503)로 거절한다. 링크 가져오기는 **화면 단추가 없다**(서버 경로뿐). 형식별 자세한 확인 방법은 `docs/SOURCE_FORMATS.md`.

## F3. 사진 글자 읽기 (OCR)

- **작업 상태**: 서버 재시작으로 끊긴 OCR은 `protocol_ocr_interrupted` 실패로 기록하며 다시 올리기나 OCR 요청으로 재시도할 수 있다. OCR 작업이 살아 있는 동안 분석 상태도 작업 중임을 알린다. 분석 모델 클라이언트의 설정 오류는 실패로 기록해 대기 상태에 남기지 않는다.
- **무엇**: 글자 층이 없거나 읽을 수 없는 **쪽만** OCR 한다. 올리기 직후 자동으로 OCR → 자동 수락 → 분석까지 이어진다(사람의 승인 단계 없음).
  엔진은 NAVER CLOVA OCR 과 Google Cloud Vision. 둘 다 설정하면 둘 다 읽고, 한글이 30 % 이상인 쪽은 CLOVA 의 글을 쓴다. 숫자 값은 두 엔진이 **같은 자리에서 같게** 읽어야 자동으로 정해지고, 다르게 읽은 값은 그 쪽의 확인 카드가 된다. 숫자가 15 % 이상 다르거나 신뢰도가 0.90 미만인 쪽은 같게 읽은 값도 묻는다.
- **어디**: `experiment_protocol_pdf.page_ocr_reason`(어느 쪽이 OCR 이 필요한지), `server.py` `_automatic_ocr_on_upload`, `_schedule_ocr_then_analysis`; `protocol_ocr.py`(`validate_ocr_result`, 결과는 원본 SHA-256 과 쪽마다 정확히 한 번); `protocol_ocr_providers.py`(`ClovaOcrRecognizer`, `GoogleVisionRecognizer`, `DualEngineOcrProvider`, `select_page_text`, `readings_by_place`); `protocol_catalog.py` `_ocr_page_gate`.
- **설정**: `VOINEY_LAB_OCR_PROVIDERS`(`clova`,`google`), `VOINEY_LAB_CLOVA_OCR_INVOKE_URL`, `VOINEY_LAB_CLOVA_OCR_SECRET`, `VOINEY_LAB_GOOGLE_VISION_API_KEY`.
- **시험**: `tests/test_protocol_ocr_providers.py`, `tests/test_lane_px_auto_ocr.py`, `tests/test_lane_an3_ocr_two_engines.py`, `tests/test_lane_an3_page_gate.py`.
- **한계**: 엔진이 하나뿐이면 숫자를 견줄 수 없어 스캔 문서는 안내로 끝날 수 있다. 두 엔진이 똑같이 잘못 읽는 경우는 막지 못한다. 기준값(15 %, 0.90)은 문서 하나에서 정했다.

## F4. 분석

- **무엇**: 분석 역할 모델이 단계·양·시간·온도·재료·장비·안전 주의를 구조(JSON)로 뽑는다. 모델의 답은 믿지 않는 초안이고 서버가 구조·근거·도메인을 검사한다.
  - **긴 문서**: 60쪽을 넘거나 출력이 한 번 호출 예산(48,000 토큰)을 넘을 것 같으면 15쪽(겹침 1쪽) 조각으로 나눠 기본 8개씩 동시에 분석하고 합친다(최대 400쪽, 넘으면 호출 전에 거절). 429 가 오면 남은 조각의 동시 수를 절반으로. 다시 시도해도 실패한 조각의 쪽은 "분석 실패 쪽" 확인 항목이 되고, 모든 조각이 실패해야 문서가 실패한다.
  - **자동으로 다시 하기**: 공급자 429·일시적 5xx 는 최대 5번 다시 보낸다. 출력 한도에서 잘린 답과 구조가 깨진 답은 요청마다 한 번 다시 분석한다(잘렸으면 추론 한 단계 아래로). 안전 필터가 막은 답은 바로 한 번 더 보낸다. 근거 검사 실패·시간 초과·설정 없음은 다시 하지 않는다.
  - **사람의 "다시 분석"**: 통과한 분석도 새로 만들 수 있다. 새 분석이 통과하기 전에는 지금 분석으로 계속 쓰고, 이미 시작한 실험은 시작한 버전으로 이어 간다. 같은 단추를 연달아 눌러도 한 번만 돈다. 옛 분석기로 만든 분석에는 "분석기 개선 전 분석 · 다시 분석 권장"이 붙는다.
  - **번역**: 분석이 통과하면 그 버전의 문장을 번역 역할 모델로 한국어로 한 번 만들어 둔다(용어집 먼저, 시약·장비 이름은 영어 유지). 기계 검사를 통과한 문장만 보여 주고 아니면 원문을 보여 준다. 어휘와 번역 검사 문장 목록은 원문·분석 내용·개정·용어집을 기준으로 재사용한다. 음성 세션은 첫 어휘 계산과 저장 번역 검사를 작업 스레드에서 준비하고 턴은 준비된 어휘를 읽는다. 열린 세션의 번역 묶음은 순서대로 합치고 준비 중 세션 구성이 바뀌면 그 결과를 버린다.
- **어디**: `server.py` `_protocol_analysis_model`, `_begin_background_analysis`, `trigger_protocol_reanalysis`; `protocol_catalog.py` `ProtocolCatalog.analyze`, `_analyze_in_pieces`, `request_analysis`; `experiment_protocol_analysis.py` (`ANALYSIS_SYSTEM_PROMPT`, `analysis_pieces`, `MAX_ANALYSIS_PAGES`, `ANALYSIS_PIECE_MAX_PAGES`, `MAX_PIECED_ANALYSIS_PAGES`, `PieceConcurrencyGate`, `_repaired_response_structure`); `model_providers.py` (`ProviderRetryPolicy`, `BLOCKED_RESENDS`, `gemini_safety_settings`); 번역 `protocol_translation.py`, `server.py` `_revision_translation_runner`.
- **설정**: `VOINEY_LAB_ANALYSIS_PROVIDER`·`_MODEL`(설정 필요), `VOINEY_LAB_ANALYSIS_REASONING`(**설정이 비면 `high`**; 쓰는 값은 `.env.example` 의 `low`), `VOINEY_LAB_PROTOCOL_ANALYSIS_TIMEOUT_SECONDS`(600), `VOINEY_LAB_ANALYSIS_PIECE_CONCURRENCY`(8), `VOINEY_LAB_PROVIDER_RETRY_MAX`(5), `VOINEY_LAB_PROVIDER_RETRY_MAX_WAIT_SECONDS`(60), `VOINEY_LAB_GEMINI_SAFETY`(relaxed), `VOINEY_LAB_ANALYSIS_RESPONSE_GRAMMAR`(false), 번역 `VOINEY_LAB_TRANSLATION_PROVIDER`·`_MODEL`·`_REASONING`.
- **시험**: `tests/test_experiment_protocol_analysis.py`, `tests/test_lane_an2_piece_concurrency.py`, `tests/test_lane_an3_long_doc_limit.py`, `tests/test_lane_an_analysis_retry.py`, `tests/test_lane_rn_reanalysis.py`, `tests/test_lane_gm_safety.py`, `tests/test_revision_translations.py`.
- **한계**: 측정(2026-10-13): 60쪽 넘는 문서 셋(88·197·315쪽)은 모두 "일부 조각 분석 실패"로 끝났다 — 그 뒤 실패 쪽을 항목으로 남기는 방식으로 바뀌었고 다시 잰 값은 없다. 여러 긴 문서가 한꺼번에 오면 요청 한도에 걸릴 수 있다.

## F5. 원문 근거 검사

- **무엇**: 분석이 말한 모든 것(단계 문장·값·안전 주의·재료…)이 원문(또는 받아들인 OCR 글)에 실제로 있는지 서버가 기계적으로 대조한다. 찾지 못한 항목은 문서를 버리지 않고 "확인 필요" 항목이 된다. 원문이 확인하지 못한 메타데이터와 설명은 비운다. 뽑은 단계가 원문 번호 단계의 25 % 미만이면 "속 빈 통과"로 거부한다. 타이머는 그 단계의 원문 범위에 적힌 시간만 인정한다.
  거부된 항목만 그 쪽 글과 함께 한 번 더 물어 원문 그대로의 구간을 받고(분석마다 한 번, 긴 문서는 조각마다), 받은 구간도 같은 검사를 거친다. 이 되묻기는 분석을 "검토 대기"로 저장한 **뒤에** 돈다(한도 300 s, 요약에 "원문 위치를 다시 찾는 중"). 다시 찾은 항목의 카드는 서버가 닫고("원문 위치를 다시 찾아 확인했어요"), 사람이 아무것도 정하지 않은 채 모든 카드가 닫히면 분석은 통과로 저장된다.
  번호 단계와 번호 없는 단계가 섞인 원문은 그대로 받고, 번호 방식이 바뀌는 경계를 확인 카드("이어져요")로 묻는다.
- **어디**: `experiment_protocol_analysis.py` (`validate_protocol_analysis_evidence`, `_claim_is_supported`, `hollow_analysis_check`, `verify_step_timers`, `review_flags_for`, `requote_unverified_items`, `REQUOTE_TIME_LIMIT_SECONDS`); `experiment_protocol.py` (`validate_protocol`, `assess_readiness`); 쪽 근거 조각 `protocol_claim_analysis.py` `generate_page_evidence_segments`.
- **설정**: `VOINEY_LAB_ANALYSIS_REQUOTE`(true).
- **시험**: `tests/test_lane_ev_evidence_rules.py`, `tests/test_lane_ev2_hollow_pass.py`, `tests/test_lane_ev2_cross_page.py`, `tests/test_lane_an4_requote.py`, `tests/test_lane_an5_requote_after_cards.py`, `tests/test_lane_an5_mixed_numbering.py`, `tests/test_lane_an5_page_end_furniture.py`.
- **한계**: 한 줄에 여러 값이 몰린 표, 번호 없는 글로 된 단계, 설명만 하는 번호 줄은 잘못 읽힐 수 있다(`.agent/architecture.md` "알려진 분석 한계").
- **분석 결과 비교**: `scripts/score_extraction.py`는 같은 PDF의 저장된 분석 JSON과 사람이 만든 기준 JSON을 현재 파서로 읽어 단계 누락·추가·순서·문장 속 값 차이와 원문 근거가 연결된 구조 항목 차이를 따로 보고한다. 한글을 보존하고, 번호가 없거나 겹치면 쪽·원문 인용으로 비교한다. 모델 호출·카탈로그 쓰기·실행 판정은 하지 않는다. 기준 자체가 불완전할 수 있고, 원문 인용이 다르면 추측해서 맞추지 않는다. 명령과 한계는 `.agent/testing_strategy.md` 3절.

## F6. 확인 카드

- **무엇**: 근거 검사가 다 확인하지 못한 분석은 "검토 대기"로 남고 실행되지 않는다. 서버가 물어야 할 항목만 **한 장씩** 카드로 보여 주고, 단추도 서버가 허락한 것만 준다(맞아요 · 직접 고칠게요 · 원문에 없어요 · 다른 줄이에요 · 다시 분석 …). 한 쪽의 OCR 값은 카드 한 장("모두 맞아요")으로 묶는다.
  모든 항목을 정하고 이름을 적어 "검토 완료하고 등록"하면 검토본(v1, v2 …)이 생기고 최신 검토본이 실행된다. 값마다 "원문 · AI 제안 · 검토자 입력"으로 출처를 표시한다.
  **안전 주의 글은 고칠 수 없다**(메모만). 원문에 없는 것만 비울 수 있다. 안전 주의 속 OCR 숫자는 두 엔진의 읽기 중 하나만 고를 수 있고, 어느 읽기도 맞지 않으면 등록을 거부한다. 단계는 비울 수 없다(다시 분석).
  항목이 12개를 넘으면 카드를 만들지 않고 추론 한 단계 위로 한 번 다시 분석하고, 그래도 많으면 안내만 한다(긴 문서는 다시 분석하지 않는다).
- **어디**: `protocol_catalog.py` (`review_cards`, `_card_choices`, `_ocr_page_card`, `review_draft`, `record_review_decision`, `register_review`, `_FIXABLE_KINDS`, `_CLEARABLE_KINDS`, `REVIEW_ORIGIN_KO`, `review_max_items`); 경로 `GET …/review-draft`, `POST …/review-decisions`, `POST …/review-versions`.
- **설정**: `VOINEY_LAB_REVIEW_MAX_ITEMS`(12), `VOINEY_LAB_REVIEW_REANALYSIS_MAX_CALLS`(16).
- **시험**: `tests/test_lane_rv2_cards.py`, `tests/test_lane_rv2_safety_statements.py`, `tests/test_lane_rv_review_versions.py`, `tests/test_lane_rv_no_path_to_run.py`.
- **한계**: 다 통과한 분석에는 카드가 없고 검토는 의무가 아니다. 관리자의 "확인함"(`POST …/review-versions/{v}/pi-confirmations`)은 기록만 하고 화면이 없다.

## F7. 시작 전 확인과 시작

- **무엇**: 분석이 통과했고 **시작을 막는 사유**(프로토콜이 맞지 않음, 실행할 단계 없음, OCR 이 안 된 쪽, 안전에 중요한 충돌)가 없으면 시작할 수 있다. 그 밖의 사유는 **시작 전 알림**으로만 보이고, 안내가 아직 다루지 못하는 구조(동시 작업, 되풀이 알림, 재사용 하위 절차)는 그 단계에서 한 번 말하고 원문을 읽는다. 승인 단계는 없다.
  시작 마법사는 (로그인) → 내 목소리 → 안내 목소리 → 프로토콜 → **핵심 값 확인**(시간·온도·속도·양 중 2개를 원문 조각 옆 객관식으로, 정답과 보기는 서버가 만든다; 틀리면 확인 카드를 연다) → **안전 주의**(원문과 한국어를 나란히, "읽었어요") → **이 프로토콜로 시작**. 모든 답은 프로토콜 장부에 남는다.
  안전 문서: 기관이 승인한 안전 문서 카탈로그(SOP·SDS·장비 설명서)는 선택이다. 없으면 사용 범위 `source_protocol_only` 로 원문 안전 주의만 쓰고, 화면·답·기록이 "기관 안전 문서 없음 · 원문 안전 주의만"이라고 말한다.
- **어디**: `experiment_protocol.py` (`EXECUTION_BLOCKING_REASON_CODES`, `execution_blocking_reasons`, `execution_notice_reasons`); `protocol_catalog.py` (`available_for_execution`, `load_executable_fixture`, `READINESS_REASON_KO`); `start_check.py` (`StartCheck`, `build_questions`, `QUESTION_COUNT`); `server.py` (`get_protocol_start_check`, `server_config`, `_source_protocol_only`); `safety_pack.py` (`resolve_safety_pack`, `SOURCE_PROTOCOL_ONLY_SCOPE`); 화면 `static/index.html` 의 `#wizard-sheet`.
- **설정**: `VOINEY_LAB_START_CHECK_ENABLED`(true), `VOINEY_LAB_USAGE_SCOPE`, `VOINEY_LAB_SAFETY_CATALOG`, `VOINEY_LAB_FACILITY_ID`.
- **시험**: `tests/test_lane_di_execution_rule.py`, `tests/test_lane_on_start_check.py`, `tests/test_lane_on_wizard_screen.py`, `tests/test_lane_pl_source_protocol_only.py`, `tests/test_safety_pack.py`.
- **서버 시작 확인**: `available_for_execution`에 더해, 같은 계정이 지금 실행할 분석·검토본의 핵심 값을 확인하고 안전 주의를 읽었다는 장부 기록을 요구한다. 안전 확인은 `/api/protocols/{protocol_id}/start-check/safety`에 현재 `target`을 보내 기록한다. 기록이 없거나 다른 계정·다른 분석의 기록이면 시작을 거절하고 할 일을 알린다. 핵심 값이 없는 원문도 안전 확인은 필요하다. 재연결은 원래 실행 버전의 확인을 읽는다. `VOINEY_LAB_START_CHECK_ENABLED=false`이면 기존 시작 길을 유지한다.

화면의 재개 제어는 시작한 실험이며 현재 설정 번호와 세대가 일치할 때만 허용한다. 같은 소켓에서 시작 요청을 다시 받으면 같은 설정은 현재 세션을 알리고, 다른 설정은 연결을 유지한 채 오류를 보낸다.

## F8. 듣기

- **세션 준비**: 프로토콜 PDF 읽기·실행 개정 선택·안전 문서 조회는 작업 스레드에서 끝낸 뒤 음성 세션을 시작한다. 재연결도 같은 준비 경로를 쓴다.
- **무엇**: 화면이 마이크 소리를 16 kHz PCM 으로 보내고, 서버가 20 ms 마다 WebRTC VAD 로 듣는다. **1초(1000 ms) 조용하면** 그 말을 끝으로 보고 한 번에 글로 옮긴다(15초가 넘으면 자른다). 답은 문장마다 음성 합성으로 보낸다. 말하는 도중에 끼어들면 안내를 멈춘다. 자기 목소리가 마이크로 되돌아온 것(최근 3초, 12자 이상, 80 % 겹침)은 버린다.
  - **목소리 맞추기**: 시작 때(또는 "목소리 다시 맞춰 줘") 한 문장을 읽어 내 목소리 크기를 기준으로 삼는다. 기준보다 많이 작은 말(민감도 높음 9 dB · 보통 12 dB · 낮음 18 dB)은 다른 사람 말로 보고 글로 옮기지 않는다("다른 사람 말로 보여 듣지 않은 말 N개"). 기준은 세션에만 있고 저장하지 않는다.
  - **시끄러운 곳**: 맞춘 세션에서는 웅성거림·펌프 소리로 VAD 가 계속 말소리를 들어도 내 목소리 크기로 말 끝을 찾는다. "시끄러운 곳 모드"를 고르면 다음·완료·타이머 시작과 기록을 한 번 되묻는다. 1분 안에 무시한 말이 3번이면 그 모드를 권한다(10분에 한 번까지).
  - **기다림 소리**: 답이 0.7초 넘게 걸리면 짧은 소리를 낸다.
  - **응급**: "도와주세요", "불났어요", "가스 누출됐어요" 같은 말에는 정해진 문장으로 작업을 멈추고 위험에서 떨어져 연구실 안전 담당에게 연락하라고만 한다.
- **어디**: `vad.py` (`EndpointDetector`, `VAD_END_SILENCE_MS`, `SpeakerLevelReference`, `NoiseFloor`, `SENSITIVITY_MARGINS_DB`); `server.py` (`ListenerSession`, `run_turn`, `_finish_voice_calibration`, `ignores_level`, `_suggest_noisy_mode`, `ECHO_TAIL_SECONDS`); `voice_providers.py` (`transcribe`, `synthesize`, `require_provider`); `cascade_filler.py` `CascadeFiller`; `emergency.py` `recognize_emergency`; `audio.py` `SAMPLE_RATE`; 화면 `static/mic-capture-worklet.js`.
- **설정**: `VOINEY_LAB_STT_PROVIDER`·`VOINEY_LAB_TTS_PROVIDER`(설정 필요), `VOINEY_LAB_STT_MODEL`, `VOINEY_LAB_TTS_VOICE`, `VOINEY_LAB_CASCADE_VAD_ENDPOINT_SILENCE_MS`(1000), `VOINEY_LAB_CASCADE_VAD_MAX_UTTERANCE_MS`(15000), `VOINEY_LAB_CASCADE_FILLER_MODE`(tone), `VOINEY_LAB_CASCADE_FILLER_DELAY_MS`(700), `VOINEY_LAB_STT_DIAGNOSTICS_ENABLED`(false). 실험자 설정 `speaker_sensitivity`, `ambient_mode`, `agent_voice`(설정 탭).
- **시험**: `tests/test_vad.py`, `tests/test_lane_sp1_level_gate.py`, `tests/test_lane_sp1_noisy_mode.py`, `tests/test_lane_sp2_level_endpoint.py`, `tests/test_lane_fx3_endpoint_pause.py`, `tests/test_own_speech_echo.py`, `tests/test_emergency.py`, `tests/test_voice_providers.py`.
- **한계**: 다른 사람 말은 **크기로만** 거른다(누구 목소리인지는 가리지 않는다). 소리 기준값은 합성 목소리·합성 소음 시험대에서 정했다. 응급 문장은 소음·필러 거르기, 목소리 맞추기 문장 대기, 언어 되묻기 **뒤에** 판정된다 — 목소리 맞추기 문장을 기다리는 동안의 "도와주세요"는 맞추기 문장으로 읽힌다(그 순서를 지키는 시험은 없다).

## F9. 말 한 번의 길

- **무엇**: 글로 옮긴 말은 한 줄로 간다. ① 응급 판정(`server.py`) → ② **앞단 규칙**(모델을 기다리지 않음: 글자 품질, 멈춤·"종료", 열린 질문의 예/아니요, 끝점 질문에 대한 답, 남은 시간, "그거", 다시 말해 줘, 지금 단계를 이름 댄 완료, 시작 등) → ③ (켰을 때) **LLM 라우터**: 답하거나 `change_state`·`record_log` 도구로 바꾸기를 **제안** → ④ **서버 검증**: 이 말의 글자 그대로의 근거, 질문·가정이 아님, 지금 단계만, 열린 서버 질문이 없을 것 → 같은 상태 기계(`CuratedProtocolSession`)가 바꾼다.
  한국어 의문·추측 어미와 물음표는 규칙과 라우터 검사에서 같은 함수로 읽는다. "투명해졌나", "타이머 시작했나", "재개했나", "투명해질지", "타이머 시작할지" 같은 말은 관찰·완료·타이머 시작·재개·이상 사항 기록을 허용하지 않고, 열린 질문도 소비하지 않는다. 설명을 함께 요청해 기존 답변 경로로 들어가도 완료 확인을 갱신하거나 지우지 않는다. 분명한 진술·명령과 "다음"·"다음 단계"·"넘어가"의 진행 동작은 유지한다.
  모델이 늦거나·실패하거나·거부되거나·답이 검사에 걸리면 같은 말을 규칙 길로 처리한다(두 번째 결정이 아니다). 라우터 모델이 요청 한도(429)에 걸리면 그 말을 멈추고 "지금 답하지 못했어요. 다시 말씀해 주세요."라고 한다.
  모델이 쓴 답은 서버 검사를 통과해야 쓴다: 숫자는 인용한 원문에서만, 상태를 바꿨다는 말 금지, 화면 라벨 금지, 서버 값은 정확히, 근거 없는 안전 지시는 빼고 "이 상황의 안전 안내는 원문에 없어요."를 붙인다.
  **라우터 먼저**(꺼짐)를 켜면 앞단 규칙은 시스템 정보·애매한 지칭 되묻기·짧고 분명한 명령·서버 질문의 답만 맡고, 라우터가 읽기 전용 도구(`show_visual`, `clarify`, `not_for_me`)도 쓴다.
- **어디**: `curated_protocol.py` (`CuratedProtocolSession.front_plan`, `plan`, `FRONT_RULES`, `apply_tool_proposal`, `apply_router_answer`, `ROUTER_FIRST_HANDED_ON`); `llm_router.py` (`route_turn_with_llm_router`, `validate_tool_proposals`, `CHANGE_STATE_RULES`, `RECORD_LOG_RULES`, `answer_check_failures`, `ROUTER_MODEL_UNAVAILABLE_KINDS`); `answer_checks.py`; `runtime_routing.py` `route_curated_runtime_turn`; `intent_fences.py`; `brain.py` `ConversationHistory`(6턴·약 1,200 토큰); `server.py` `_route_with_llm_router`.
- **설정**: `VOINEY_LAB_LLM_ROUTER_ENABLED`(false), `VOINEY_LAB_ROUTER_FIRST`(false, 라우터가 켜져 있어야 함), `VOINEY_LAB_ROUTER_PROVIDER`·`_MODEL`(설정 필요; 켰는데 비어 있으면 라우터를 끄고 이유를 알림), `VOINEY_LAB_LLM_ROUTER_TIMEOUT_SECONDS`(2.5).
- **시험**: `tests/test_front_plan.py`, `tests/test_llm_router.py`, `tests/test_llm_router_wiring.py`, `tests/test_router_answer_checks.py`, `tests/test_lane_ra_router_first.py`, `tests/test_lane_fx3_router_unavailable.py`, `tests/test_rule_parity.py`. 말 맞추기 재생 `python scripts/replay_turns.py`(라우터 꺼짐, 규칙 길).
- **한계**: 라우터는 기본으로 꺼져 있다. 측정(평가 세트 567줄, 2026-10-13, 모델 `gpt-6-luna`·추론 `none`): 알아들음 규칙 77.1 % → 라우터 먼저 82.7 %, 잘못 바뀐 상태 0, 모델 지연 p50 1.52 s · p90 2.14 s. 평가 세트는 실제 연구실 녹음이 아니다.

## F10. 단계 · 완료 · 타이머

- **무엇**: "다음", "완료했어"는 말의 꼴로 단계를 마치고 다음 단계를 읽는다. 다만 끝점 관찰이 남았거나, 단계 타이머가 돌고 있거나, "바로 확인" 모드거나, 마지막 단계면 "N단계 끝났나요?"를 먼저 묻는다. 되풀이 단계(…될 때까지)는 실험자가 말한 관찰로 끝난다. "이전 단계로", "방금 완료 취소"는 한 번 묻고 사건을 덧붙인다(지우지 않는다). "잠깐"은 바로 멈추고 "재개"·"계속"이 풀며, "종료"는 한 번 묻는다.
  **타이머**는 원문에 적힌 시간만 시작한다(다른 시간을 말하면 "원문은 15분입니다…"라고 묻는다). 끝날 때 "15분 타이머가 끝났어요. 다음은 N단계 …", 5분 이상 타이머는 "1분 남았어요."를 말하고, 화면에는 늘 알림과 소리가 나온다. 멈춘 동안에도 타이머는 돈다. 10분 넘게 말이 없다가 질문하면 지금 어디인지부터 말한다.
- **어디**: `curated_protocol.py` (앞단 규칙 `forward_step`, `targeted_completion`, `short_completion`, `observation_reply`, `step_revert`, `pause`, `end_command`; `PendingCompletionConfirmation`, `endpoint_observation_outstanding`, `start_timer`, `due_timer_notices`, `TIMER_LAST_MINUTE_FROM_SECONDS`, `RETURN_SUMMARY_AFTER_SECONDS`); `completion_intent.py`; `server.py` `_watch_timer_notices`; 타이머 근거 `experiment_protocol.read_source_durations`.
- **설정**: 실험자 설정 `confirm_mode`(되읽기·바로 확인·조용히), `proactive_mode`(모두·필요한 것만·끄기), `question_timing`(조건 분기·반복 횟수를 시작 전에/실험 중에).
- **시험**: `tests/test_endpoint_observation_gate.py`, `tests/test_completion_intent.py`, `tests/test_lane_vx_forward.py`, `tests/test_lane_vt_timer_end.py`, `tests/test_lane_vt_last_minute.py`, `tests/test_voice_pause_resume_persistence.py`, `tests/test_lane_cf_confirm_mode.py`.
- **한계**: 화면에는 "다음 단계" 단추가 없다(단계는 말로만 넘긴다). 되풀이 알림·동시 작업·재사용 하위 절차는 안내하지 못하고 원문을 읽는다.

## F11. 질문과 설명

- **무엇**: 원문이 답하는 질문(양·시간·온도·지금 단계·다른 단계)은 원문 값으로 답한다. 원문에 없는 것은 "PDF에서 확인할 수 없어요."라고 한다.
  - **"그거"**: 가리킬 값이 하나면 그것으로, 둘·셋이면 "A요, B요?"로 되묻고, 더 많으면 화면 목록을 띄운다. 다음 말이나 화면 누르기로 고른다.
  - **AI 일반 지식**: 원문이 설명하지 않는 용어 뜻("HPLC water 가 뭐야?")에는 규칙의 답 뒤에 "PDF에는 따로 설명이 없어요. 일반적으로는 …"을 붙이고 화면에 "AI 일반 지식"으로 표시한다. 120자 이내, 숫자·방법·양·안전·완료 기준이 든 설명은 서버가 뺀다. 켰을 때만 나온다(개발 실행기는 켜고 파일럿 실행기는 끈다).
  - **그림**: "그림 보여 줘"는 그 단계의 원문 그림을 먼저 보여 준다(원문에서 모양만으로 잘라 냄). "그림으로 그려 줘"는 켰을 때 간단한 그림을 그리고 "AI 가 그린 그림 — 실제와 다를 수 있음"을 붙인다(원문에 없는 숫자는 뺀다). "○○ 사진"·"웹에서 찾아봐"는 켰을 때 웹 설명과 자유 라이선스 사진(Wikimedia Commons)을 출처와 함께 보여 준다. 웹 설명에서 숫자·안전·상태 문장은 뺀다.
- **어디**: `curated_protocol.py` (`resolve_bounded_coreference`, 앞단 규칙 `coreference_clarify`, `reference_value`, `quantity_target`, `visual_request`); `external_references.py` `explain_outside_pdf`, `supplemental_knowledge_allowed`; `answer_checks.OUTSIDE_PDF_MAX_CHARS`; `source_figures.py` `figures_for_step`; `drawn_diagrams.py` `sanitize_svg`; `web_explanations.py` `check_sentences`; `multi_brain.py`(읽기 전용 Answer·Source·Visual 역할).
- **설정**: `VOINEY_LAB_SUPPLEMENTAL_MODEL_KNOWLEDGE_ENABLED`(false), `VOINEY_LAB_SUPPLEMENTAL_PROVIDER`·`_MODEL`, `VOINEY_LAB_DRAWN_DIAGRAMS_ENABLED`(false), `VOINEY_LAB_WEB_EXPLANATIONS_ENABLED`(false), `VOINEY_LAB_WEB_PHOTOS_ENABLED`(true) — 그림·웹은 `OPENAI_API_KEY` 가 필요하고 모델 기본값은 `gpt-6-luna`; `VOINEY_LAB_MULTI_BRAIN_ENABLED`(false). 실험자 설정 `web_lookup`.
- **시험**: `tests/test_lane_r6_voice_followups.py`, `tests/test_lane_vt_vague_reference.py`, `tests/test_lane_wv_source_figures.py`, `tests/test_lane_wv_drawn_diagram.py`, `tests/test_lane_wv_web_lookup.py`, `tests/test_multi_brain.py`.
- **한계**: 그림 선택은 기하만 보므로 원문에 그림이 많으면 관계없는 것이 섞일 수 있다.

## F12. 실험 중 기록

- **무엇**: 말한 관찰·측정·편차·메모와 이상 사항("시약을 엎질렀어")은 그 사람의 말 그대로 지우지 않는 장부에 남고, 서버가 되읽어 준다. "안 보임"처럼 부정이 든 말은 통째로 남는다. "방금 기록 고쳐 줘, X가 아니라 Y"는 한 번 묻고 고침을 덧붙인다(원래 것은 남는다). 소수점이 분명하지 않은 값은 다시 묻는다.
  **실험 노트 질문**(한국어, 상태를 바꾸지 않음, "넘어가"로 건너뜀): 시작 뒤 "오늘 실험 목적이나 시료를 한 줄로 말씀해 주세요.", 관찰 대상이 있는 단계를 설명 없이 마치면 "관찰한 것 있으면 말씀해 주세요.", 끝나면 "결과 한 줄과 다음에 바꿀 점이 있으면 말씀해 주세요." "목적 고쳐: …"로 고친다. 명령이나 질문은 답이 아니라 명령으로 처리된다.
  기록 탭에서 손으로 관찰을 적고 사진·문서(최대 32 MiB, 해석하지 않음)를 붙일 수 있다.
  화면에서 메모·사진을 저장한 뒤에도 열린 음성 연결의 일시정지·재개·종료는 서버의 최신 버전으로 처리한다. 연결과 현재 단계가 같은지 거래 안에서 확인하며, 다른 탭이 넘겨받았거나 단계가 바뀌면 거절한다. HTTP가 직접 보내는 버전 검사는 그대로 적용한다.
- **어디**: `curated_protocol.py` (앞단 규칙 `note_record`, `anomaly_report`, `record_fix`; `PURPOSE_QUESTION_KO`, `OBSERVATION_QUESTION_KO`, `RESULT_QUESTION_KO`, `open_notebook_question`); `tools.py` `record_value_keeping_negation`; `server.py` `_record_experiment_report_plan`, `_notebook_end_question`; 장부 `experiment_reports.py` `ExperimentReportStore.append_event`; 작업공간 `workspace_store.py`(`experiment_session_events`, `experiment_observations`, `experiment_evidence`).
- **설정**: `VOINEY_LAB_EXPERIMENT_REPORTS_ENABLED`, `VOINEY_LAB_EXPERIMENT_REPORT_DB`, `VOINEY_LAB_NOTEBOOK_OBSERVATION_QUESTION`(true, 관찰 질문만 끔).
- **시험**: `tests/test_lane_n_voice_notes.py`, `tests/test_lane_n_ledger_verbatim.py`, `tests/test_lane_ts_record_negation.py`, `tests/test_lane_rp3_voice_questions.py`.
- **한계**: 보고서 보내기는 말로 하지 않는다("보고서 전송은 지원하지 않아요…"). 작업공간 장부는 SQL 트리거로 덮어쓰기·지우기를 막지만, 실험 기록 장부는 코드 경로로만 덧붙이기를 지킨다(트리거 없음).

## F13. 실험 노트와 보고서

- **무엇**: 실험이 끝나면 장부에서 보고서를 만든다. Word·Markdown 보고서는 연구자의 **실험 노트** 모양이다: 제목은 말한 목적, 1 시료와 조건 … 원문 값과 실제 쓴 값, 단계·시료별 관찰, 결과와 다음에 바꿀 점, 원문과 다른 점, 같은 프로토콜의 지난 실험과 비교, 확인, 부록. 숫자는 모두 기록에서 서버가 만든다.
  대답하지 않은 실험 노트 질문은 일시정지·재개 뒤에도 유지하고 재개할 때 다시 묻는다. 그 질문이 열린 동안의 "넘어가"는 질문만 건너뛴다.
  보고서 모델은 끝난 뒤 한 번 불러 관찰 문장과 결과 한 단락만 쓰게 하고, 기록·원문에 없는 값·숫자·추측·안전 지시·인용을 검사해 통과한 것만 "AI 정리"로 넣는다(모델이 없으면 서버 문장만). 실험자가 아직 확인하지 않은 값은 "[미확인]"으로 표시하고 화면에서 하나씩 확인하거나 고친다.
  관찰 질문에 대한 답은 질문이 가리킨 완료 단계에 저장한다. 같은 값 확인을 다시 보내도 확인 사건은 하나만 남기며, 동시에 온 PI 확인은 각각 남긴다. 조사 뒤 보고서 갱신에도 현재 연결의 설정 식별자를 붙인다.
  내보내기: 화면에서 Word(.docx)·Markdown·JSON·CSV. 서버에는 PDF(LibreOffice 필요)·랩미팅 1쪽 요약(Word·PDF)·결과 표(xlsx)도 있다. 회의용 파일 이름도 `.meeting.docx`·`.meeting.pdf`로 실제 형식에 맞춘다. 관리자는 노트에 "확인함"(이름·시각·한 줄)을 남길 수 있다.
  기록의 소유 계정과 연구실은 서버가 정한다. HTTP·음성 소켓 조회와 내보내기는 본인 또는 같은 연구실 관리자만 가능하다. 값 확인·정정·평점·보고서 재생성은 본인만 가능하며, 관리자가 남의 기록에 쓸 수 있는 것은 PI 확인뿐이다. 다른 연구실 기록과 권한 없는 기록은 없는 것처럼 응답한다. 옛 기록의 소유자는 작업공간 세션으로 채우며, 소유자를 모르면 해당 연구실 관리자만 읽는다.
  지난 실행 비교·시작 전 확인 답·하루의 실행 순번은 같은 소유 계정·연구실·프로토콜 안에서만 구한다. 소유자가 불명인 옛 기록에는 다른 사람의 시작 전 답을 넣지 않으며 순번을 비워 둔다.
- **어디**: `experiment_reports.py` (`build_lab_notebook`, `LabNotebook`, `NOTEBOOK_SUBTITLE`, `ReportWriterBrain`, `check_ai_notes`, `export_docx`, `export_markdown`, `export_pdf`, `export_meeting_docx`, `export_xlsx`); `report_projection.py`; `server.py` `export_experiment_report`, `/api/experiment-reports/{id}/prose`, `/review/{item_id}`, `/pi-confirmations`.
- **설정**: `VOINEY_LAB_REPORT_PROVIDER`·`_MODEL`·`_REASONING`, `VOINEY_LAB_REPORT_WRITER_ENABLED`(true), `VOINEY_LAB_REPORT_WRITER_TIMEOUT_SECONDS`(25), `VOINEY_LAB_REPORT_TIMEZONE`(Asia/Seoul), `VOINEY_LAB_OFFICE_CONVERTER`.
- **시험**: `tests/test_lane_rp3_notebook.py`, `tests/test_lane_rp3_ai_notes.py`, `tests/test_lane_rp2_report_pdf.py`, `tests/test_lab_report.py`, `tests/test_lane_n_report_review.py`.
- **한계**: 새로고침 뒤에는 지난 보고서를 화면에서 다시 받는 길이 없다(링크는 실험이 끝날 때만 채워진다). PDF·랩미팅·xlsx·"확인함"은 화면 단추가 없다.

## F14. 연결 끊김

계정 모드에서는 로그아웃·비밀번호 변경·계정 끄기·계정 삭제 시 그 계정의 열린 음성 연결을 모두 닫는다. 다른 계정의 연결은 계속된다. 계정 경로는 이미 시작된 음성 보고서 저장 스레드도 완료를 기다리며, 계정 삭제는 그 뒤 삭제 목록을 만들어 늦은 저장이 기록을 다시 남기지 않게 한다. 만료나 운영자 도구의 세션 회수도 주기적으로 확인한다.

- **무엇**: 화면은 1초마다 서버와 신호를 주고받아 **3초 안에** 끊김을 알아챈다. 빨간 띠와 소리·진동으로 알리고, 화면을 읽기 전용으로 바꾸고, 마이크를 멈춘다. 끊긴 동안 화면은 서버 시각으로 단계 타이머를 이어 세고 1분 전·끝 알림을 낸다. 1·2·4·8초, 그 뒤 15초마다 다시 이어 **같은 실험**을 계속하고, 서버 상태로 화면을 바꾼다. 놓친 타이머 알림을 전하고 끊긴 시간을 내용 없는 사건으로 남긴다.
- **어디**: `static/connection.js` (`SILENT_LOST_MS`, `resumable()`); `server.py` `_send_heartbeats`, `_restore_lost_connection`, `_restore_standing_timer`, `_reconnect_timer_notices`, `_record_connection_restored`.
- **시험**: `tests/test_lane_net_reconnect.py`, `tests/test_lane_net_screen_timer.py`.
- **한계**: 실험이 작업공간 실험에 묶였을 때만 이렇게 동작한다(아니면 오류로 끝난다). 웹에서는 화면이 꺼지거나 다른 앱으로 가면 화면 타이머 알림이 울리지 않을 수 있다.

## F15. 파일럿 지표와 평점

- **무엇**: 파일럿 지표는 **숫자·코드·시각만** 둔다(말한 글·소리·프로토콜 글·모델 출력 없음): 말 한 번(라우터 결과·지연), 표시(내보내기 등), 분석 결과, 끝 평점. 행마다 계정 id. 실험이 끝나면 화면이 "이번 실험에 도움이 됐나요?" 1–5 와 한 줄 메모(선택, 200자)를 묻는다.
  관리자 화면 `/admin-metrics.html` 이 날짜별 표를 보여 주고(관리자 계정이면 토큰 없이), `scripts/pilot_metrics.py` 가 CSV 로 낸다(메모는 `notes` 로만).
- **어디**: `pilot_metrics.py` (`PilotMetricsStore`, `PilotRecorder`, `PILOT_METRICS_FILENAME`); `server.py` `get_admin_pilot_metrics`, `post_pilot_feedback`, `_require_pilot_admin`; `static/feedback.js`, `static/admin-metrics.html`; 집계 `GET /api/admin/metrics`(토큰만, `runtime_metrics.py`).
- **설정**: `VOINEY_LAB_ADMIN_TOKEN`(없으면 토큰 경로는 닫힘). 작업공간이 없으면 저장하지 않는다.
- **시험**: `tests/test_lane_log_summaries.py`, `tests/test_lane_log_feedback.py`, `tests/test_lane_fx5_admin_page.py`. 어떤 열이 무엇인지는 `docs/PILOT_GUIDE.md` 3-3절이 기준이다(`tests/test_lane_log_guide.py` 가 맞춘다).

## F16. 모바일 웹과 스토어 앱

- **무엇**: 화면은 휴대폰 폭에서도 쓰고, 브라우저의 "홈 화면에 추가"로 둘 수 있다(`manifest.webmanifest`, 오프라인 없음). 마이크·설치·화면 꺼짐 방지는 **HTTPS(또는 localhost)** 에서만 된다.
  `mobile/` 은 같은 웹 화면을 감싸는 Capacitor(안드로이드 먼저) 앱 뼈대다: 정확한 시각 알람, 화면을 꺼도 듣는 마이크 서비스, 기기에 봉한 로그인 토큰, 화면 켜 두기. 앱은 상태·타이머 권한을 갖지 않는다(서버의 타이머 시각을 복사해 알릴 뿐).
- **어디**: `static/manifest.webmanifest`, `static/native-bridge.js`; `mobile/capacitor.config.js`, `mobile/scripts/prepare-web.mjs`, `mobile/README.md`(빌드), `docs/APP_PLAN.md`(계획); 서버 `VOINEY_LAB_APP_ORIGINS`(앱 출처의 토큰 로그인·CORS·`/ws` 출처).
- **시험**: `tests/test_native_bridge.py`, `tests/test_lane_app1_app_login.py`, `tests/test_lane_app1_app_origins.py`.
- **한계**: `native-bridge.js` 는 아직 `index.html` 이 불러오지 않는다 — 일반 화면에서는 기기 알림·화면 꺼도 듣기가 없다. 묶음 빌드는 아직 서버 주소를 받지 못한다. 실제 휴대폰·HTTPS 에서 확인한 기록은 없다. iOS 빌드 없음.

## F17. 공급자와 설정

- **무엇**: 모델 역할은 `router`·`answer`·`translation`·`analysis`·`report`·`supplemental` 여섯이고, 역할마다 공급자(`xai`·`anthropic`·`openai`·`google`)·모델·추론 수준을 적는다. 기본값은 없다("설정 필요") — 빈 역할을 부르면 다른 공급자로 가지 않고 그 설정 이름을 적은 오류로 닫힌다. STT·TTS 공급자(`google_cloud`·`elevenlabs`·`xai`)도 같다.
  설정 이름은 모두 `VOINEY_LAB_` 로 시작한다(SDK 표준 이름 제외). 옛 이름이 셸이나 `.env` 에 있으면 서버·실행기·시험이 시작을 거부하고 이름만 알린다 — `python scripts/migrate_env.py --check` 로 보고 `--write` 로 바꾼다. 실행기의 기본값은 셸·`.env` 의 값을 덮지 않는다.
- **지금 쓰는 값**(`.env.example`; 키는 비워 두고 `.env` 에만 넣는다):

  | 역할 | 하는 일 | 값 |
  |---|---|---|
  | 분석 `ANALYSIS` | 프로토콜 구조 분석 | `google` · `gemini-3.8-flash` · 추론 `low` |
  | 번역 `TRANSLATION` | 버전 문장의 한국어 | `google` · `gemini-3.8-flash` · 추론 `low` |
  | 라우터 `ROUTER` | 말 한 번마다(켰을 때) | `openai` · `gpt-6-luna` |
  | 답변 `ANSWER` | 읽기 전용 Answer·Source·Visual 역할 | `openai` · `gpt-6-luna` |
  | 보조 설명 `SUPPLEMENTAL` | "AI 일반 지식"(켰을 때) | `openai` · `gpt-6-luna` · 추론 `low` |
  | 보고서 `REPORT` | 보고서의 AI 정리 | `google` · `gemini-3.8-flash` |
  | 웹 설명·AI 그림 | 켰을 때 | `gpt-6-luna` (`OPENAI_API_KEY`) |
  | STT | 말 → 글 | `elevenlabs` · `scribe_v2` |
  | TTS | 글 → 말 | `google_cloud` (목소리 기본 `ko-KR-Chirp3-HD-Charon`) |
  | OCR | 사진 글자 읽기 | `VOINEY_LAB_OCR_PROVIDERS` 에 `clova`,`google` |

- **실제 호출 기록**(줄 보고서에 적힌 것, 이 저장소의 시험이 아님): OpenAI `gpt-6-luna` 라우터 평가 22회(2026-10-05); Google Gemini 분석·번역 222회(2026-10-10); 긴 문서·Word·사진 실측 Gemini 분석·Vision 504회 USD 9.49 와 CLOVA 18회(2026-10-13); 라우터 먼저 측정 OpenAI 833회 USD 0.57 · Google 417회 USD 1.80(2026-10-13); 스타터 팩 분석 Google(Gemini 분석·Vision) USD 1.014 와 CLOVA 27쪽(2026-10-19). 분석 시간은 추론 `low` 에서 p50 39 s · p90 112 s(25개 문서, 2026-10-10); 같은 25개를 다른 실행과 동시에 돌렸을 때 15개 통과, p50 110 s · p90 268 s, 요청 한도(429) 23회(2026-10-13).
- **어디**: `model_providers.py` (`ROLES`, `PROVIDERS`, `ROLE_SETTINGS`, `RoleModel`, `chat_client`, `RoleNotConfiguredError`); `voice_providers.py`; `setting_names.py` (`SETTINGS`, `refuse_old_setting_names`); `setting_renames.py`; `configuration.py` (`launcher_defaults`); `.env.example`.
- **시험**: `tests/test_setting_names.py`, `tests/test_lane_an_unset_roles.py`, `tests/test_migrate_env.py`.

## F18. 데이터 · 삭제 · 백업 · 점검

- **무엇**:
  - 원본 PDF·분석·장부·작업공간·계정·지표는 모두 `data/runtime/` 아래 SQLite·파일에 둔다(git 이 무시). 원음은 저장하지 않는다(STT 진단 녹음을 켰을 때만 최대 20개).
  - **밖으로 나가는 것**(F17 의 값 기준): 말소리 → STT 공급자, 말할 문장 → TTS 공급자, 글자 층 없는 쪽의 이미지 → OCR 엔진, 원문 글자 → 분석·번역 공급자, 말한 글과 지금 단계 문맥 → 라우터·답변·보조 설명 공급자(켰을 때), 웹 설명·그림 요청 → OpenAI(켰을 때), 실험 기록 → 보고서 공급자, Google 링크 가져오기 → Google. 공급자가 받은 데이터를 어떻게 두는지는 각 약관을 따른다 — 파일럿 전에 사람이 확인한다.
  - **파일럿 데이터 삭제**: `python scripts/pilot_data_delete.py` — 미리보기 → 확인 단어 → 삭제 → 기록 순서만 허용, 되돌릴 수 없음. `data/runtime/pilot` 과 파일럿 표시가 있는 폴더만 받는다. `--account NAME` 은 그 계정의 실험·기록·지표 행을 지운다(연구실이 함께 쓰는 프로토콜은 남긴다). 계정이 스스로 지울 때도 범위가 같다.
  - **백업**: `python scripts/pilot_state_backup.py create|verify|restore`(SQLite 온라인 백업, 원문·첨부만, 환경 파일·원음 제외).
  - **점검**: `GET /healthz`(살아 있음), `GET /readyz`(신원 방식·작업공간·카탈로그·보고서·Word 변환·HEIC 가능 여부).
  - **기록(log)**: 말 내용은 남기지 않는다. 키는 `.env` 에만 둔다.
- **어디**: `scripts/pilot_data_delete.py`, `accounts.py`(지우는 범위), `scripts/pilot_state_backup.py`, `server.py` `healthz`, `readyz`, `persist_stt_diagnostic`.
- **설정**: `VOINEY_LAB_STT_DIAGNOSTICS_ENABLED`, `VOINEY_LAB_STT_DIAGNOSTIC_MAX_FILES`(20), `VOINEY_LAB_ANALYTICS_RETENTION_DAYS`(90).
- **시험**: `tests/test_lane_fx5_metrics_delete.py`, `tests/test_lane_fx5_secrets_not_kept.py`, `tests/test_pilot_backup.py`.
- **한계**: `/readyz` 는 안전 카탈로그·사용 범위·공급자 키를 보지 않는다. 삭제 도구는 디스크 조각까지 지우지 않는다. 운영 순서는 `docs/DEPLOYMENT_RUNBOOK.md`, 파일럿의 데이터 처리와 고지는 `docs/PILOT_GUIDE.md`.
