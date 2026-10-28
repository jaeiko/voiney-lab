# 용어 사전 — 사용자 말 ↔ 지금 화면 말 ↔ 코드 이름

같은 것을 세 곳에서 다르게 부른다. 이 표가 그 셋을 잇는다.

- **사용자 말**: 연구자에게 쓸 말. `docs/VOICE_AND_TONE.md` 5절 "용어 사전"이 정한 말이고, 그 문서가 문구 규칙의 기준이다.
  현재 화면·서버 말 전체를 이 사용자 말로 통일한 것은 아니다. 결정 이력은 `docs/DECISIONS.md` 9절을 본다.
- **지금 화면·서버 말**: 현재 `src/voiney_lab/static/index.html` 과 서버 문장에 실제로 있는 말.
- **코드 이름**: 그 개념을 담은 이름. 문서와 코드 리뷰에서는 이 이름을 쓴다.
- 영어 문서에서는 "Source Protocol", "Protocol", "Protocol Revision", "Analysed Protocol" 을 쓴다.
  "SOP" 는 기관이 관리하는 안전 문서 묶음(`safety_pack.py`)에만 쓴다. 분석을 통과한 프로토콜은 SOP 가 아니다.

## 1. 프로토콜과 분석

| 사용자 말 | 지금 화면·서버 말 | 코드 이름 | 뜻 |
|---|---|---|---|
| 프로토콜 · 원문 | 프로토콜, 원문, 원문 파일 | `ProtocolCatalog` 의 항목; 원본 바이트 `ProtocolFileStore` | 연구실이 올린 문서. 원본 바이트와 SHA-256 을 그대로 둔다 |
| 시약 표 | 시약 표(.xlsx) | reagent table — `ProtocolCatalog.attach_reagent_table` | 프로토콜에 붙인 엑셀. 분석에 쓰지 않는다 |
| 링크로 가져오기 | 공개 링크로 가져오기 · 링크로 가져오기 | `protocol_link_import.py`, `POST /api/protocols/import-link` | Drive 파일·Docs는 화면, Sheets 시약 표는 API로 가져오기 |
| 사진 글자 읽기 | OCR, OCR 텍스트 추출 | `protocol_ocr.py`, `protocol_ocr_providers.py` | 글자 층이 없거나 읽을 수 없는 쪽만 읽는다 |
| 분석 | 분석, 구조 분석 | analysis — `experiment_protocol_analysis.py`, `ProtocolCatalog.analyze` | 모델이 단계·값·안전 주의를 구조로 뽑는다 |
| 나눠 분석한 부분 | 조각 | analysis piece — `analysis_pieces`, `ANALYSIS_PIECE_MAX_PAGES` | 긴 문서를 15쪽씩 나눈 것 |
| 다시 분석 | 다시 분석, 분석 다시 시도 | reanalysis — `POST …/reanalysis`; 재시도 `POST …/analysis` | 통과한 분석을 새로 만들기 / 실패한 분석을 다시 하기 |
| 버전 | 리비전(서버 일부) | protocol revision, analysis revision | 분석 하나하나. 시작한 실험은 시작한 버전으로 이어 간다 |
| 원문과 맞춰 보기 | 원문 근거 검사, 원문 대조 | source-evidence check — `validate_protocol_analysis_evidence` | 뽑은 값마다 인용한 쪽의 원문 글자와 맞대어 보기 |
| 원문 위치 | 근거 | evidence (page, quote) | 값이 원문 어디에 있는지 |
| 바로 쓸 수 있어요 | 바로 쓸 수 있음 · 분석 통과 | 분석 리비전이 있고 실행을 막는 사유가 없음 | |
| 실험 시작 가능 / 아직 시작할 수 없어요 | 실행 가능 / 실행 불가 | `available_for_execution` | 서버가 읽을 때마다 판정한다 |
| 시작을 막는 사유 | 실행을 막는 사유 | `execution_blockers` — `experiment_protocol.EXECUTION_BLOCKING_REASON_CODES` | 이것이 있으면 시작할 수 없다 |
| 시작 전 알림 | 시작 전 알림 | `execution_notices` — `execution_notice_reasons` | 알리기만 하고 시작은 막지 않는다 |
| 새로 분석하면 더 정확해요 | 분석기 개선 전 분석 · 다시 분석 권장 | `ANALYSIS_OUTDATED_KO`, `_analysis_outdated_codes` | |
| 분석할 쪽 | 분석할 쪽 (선택) · 분석한 쪽 | page range — `page_range` (요청 장부), `checked_page_range`, `review()` 의 `analysis_page_range` | 문서 일부만 분석할 때 고른 처음·끝 쪽 |
| 오늘 바꾼 값 | 오늘 원문과 다르게 할 값 · 20분(오늘 바꾼 값 · 원문 15분) | run change — `RunChange`, `RunChanges`, `protocol_run_changes_chosen`, `run_value_changed` | 이번 실험에만 원문 대신 쓰는 값. 사람이 고르거나 말한 값만 |
| 지난번처럼 | 지난번처럼 (n개) | `ExperimentReportStore.last_run_values`, `RunChanges.last_time` | 같은 계정의 지난 실험이 바꾼 값을 다시 고르기 |
| 개발용 예시 | 개발용 큐레이션 분석 | curated fixture — `VOINEY_LAB_CURATED_PROTOCOL_FIXTURE` | 개발 실행기가 넣는 in-gel 프로토콜 |

## 2. 확인 카드와 시작

| 사용자 말 | 지금 화면·서버 말 | 코드 이름 | 뜻 |
|---|---|---|---|
| 확인할 것 n개 | 확인할 것 n개 · 검토 대기 · 확인 필요 n | `review_pending` — `protocol_catalog._REVIEW_PENDING_EVENT` | 근거 검사가 다 확인하지 못한 분석. 실행 경로가 읽지 못한다 |
| 확인 카드 | 검토 카드, "확인할 것 n / m" | `review_cards`, `review_draft` 의 `cards` | 서버가 물어야 할 항목 하나에 한 장 |
| 확인할 것 | 확인 필요 항목 | `ReviewFlag`, 사유 `REVIEW_REASONS_KO` | 카드가 되는 항목. 화면의 상태 말 "확인 필요"(오류)와는 다른 뜻 |
| 확인한 사람 | 검토자 | reviewer name(계정 모드에서는 계정 이름) | |
| 확인한 버전 | 검토본 vN | review version — `REVIEW_VERSION_ANALYSIS_PREFIX = "review-v"` | 최신 검토본이 실행본 |
| 확인 끝내고 저장하기 | 검토 완료하고 등록 | `ProtocolCatalog.register_review` | |
| 원문 · AI 제안 · 직접 고친 값 | 원문 · AI 제안 · 검토자 입력 | `REVIEW_ORIGIN_KO` | 값이 어디서 왔는지 |
| 핵심 값 확인 | 핵심 값 확인 · "원문의 값을 확인해 주세요" | start check — `start_check.py` `StartCheck` | 최대 2문항. 서버가 계정과 현재 실행 대상의 답을 검사한다 |
| 안전 주의 읽기 확인 | 읽었어요 | `StartTarget`, `protocol_start_safety_acknowledged` | 값 확인 뒤 현재 대상에 대해 서버에 남기는 확인. 마지막 시작 단추와 구분 |
| 안전 주의 | 안전 주의 | 분석의 `warning` 항목; 시작 화면 `safety_notices` | 원문에 적힌 안전 문장. AI 가 쓰지 않고 검토가 고치지 않는다 |
| 시작 | 이 프로토콜로 실험 시작(프로토콜 탭 단추, 시작 마법사를 연다) · 이 프로토콜로 시작(마법사의 마지막 단추) | 시작 사건 `protocol_started`; 안전 주의 확인 기록 `safety_notices_acknowledged` | 마지막 단추가 사람의 시작 확인이다 |
| 실험 이어하기 · 이어서 하기 | 실험 이어하기, 이어서 하기 | 작업공간 실험 세션 다시 잇기 | |
| 확인함 | (화면 없음) | PI confirmation — `pi_confirmed` 사건, `confirm_review_version` | 관리자 계정이 남기는 한 줄. 시작을 막지 않는다 |
| 연구실 승인 안전 자료 | 안전 자료 | approved safety catalog — `safety_pack.py`, `document_store.py` (SOP 묶음) | 기관이 승인한 SOP·SDS·장비 설명서. 선택 |
| 기관 안전 문서 없음 · 원문 안전 주의만 | 같음 | usage scope `source_protocol_only` — `NO_INSTITUTION_DOCUMENTS_NOTICE` | 카탈로그 없이 연 파일럿 |
| (사용 범위) | 쓰지 않음 | `VOINEY_LAB_USAGE_SCOPE`: `operational`·`demo`·`reference_only`·`test_only`·`source_protocol_only` | 서버 설정 |

## 3. 실험 중

| 사용자 말 | 지금 화면·서버 말 | 코드 이름 | 뜻 |
|---|---|---|---|
| 실험 | 실험, 세션, 실험 세션 | workspace experiment session(`experiment_sessions`), 음성 연결 `ListenerSession` | 로그인 이야기일 때만 "로그인" |
| 단계 | 단계, "n단계 / N" | `CuratedProtocolSession` 의 현재 단계 | 서버만 바꾼다 |
| 끝났나요 | 완료하셨나요 | 완료 확인 질문 `PendingCompletionConfirmation` | |
| 관찰 | 관찰 | observation; 끝점 관찰 `endpoint_observation_outstanding` | 원문이 정한 끝을 실험자가 말로 확인 |
| 타이머 | 단계 타이머 | `start_timer`, `due_timer_notices` | 원문에 적힌 시간만 |
| 다시 물어봐요 | 되묻기 | clarify — `clarify_candidates`, 라우터 먼저의 `clarify` 도구 | "그거"처럼 가리키는 말이 애매할 때 |
| 원문과 다른 점 | 편차 | `deviation` | 기록 종류 |
| 이상 사항 | 이상 사항, 이상 | `anomaly` | 기록 종류 |
| 문제 도움 | 문제 도움 · 도움 | `problem_help` (앞단 규칙·계획 표시·기록 사건), `CuratedProtocolAction.PROBLEM_HELP`, `knowledge.py` | 실험 중 문제에 원문·지난 기록·(켰을 때) AI 일반 지식으로 답하기. 아무것도 바꾸지 않는다 |
| 지난 기록 | 지난 기록 | `HistoryKnowledge`, `ExperimentReportStore.earlier_reports` | 같은 계정·연구실·프로토콜의 앞선 실험 기록 |
| 목소리 맞추기 | 목소리 맞추기 | voice calibration — `SpeakerLevelReference` | 내 목소리 크기를 기준으로 삼기 |
| 다른 사람 말로 보여 듣지 않은 말 | 같음 | `speech.ignored` (`other_speaker_level`) | 기준보다 많이 작은 말 |
| 시끄러운 곳 모드 | 주변 소리: 시끄러움 | 실험자 설정 `ambient_mode` = `noisy` | 상태를 바꾸는 말과 기록을 한 번 되묻는다 |
| 수치 확인 방식 | 되읽기 / 바로 확인 / 조용히 | 실험자 설정 `confirm_mode` | |
| 먼저 알려 주기 | 모두 / 필요한 것만 / 끄기 | 실험자 설정 `proactive_mode` | 타이머 끝·1분 전 알림 |
| 조건 분기·반복 횟수 질문 | 시작 전에 묻기 / 실험 중에 묻기 | 실험자 설정 `question_timing` | |
| 안내 목소리 | 안내 목소리 | 실험자 설정 `agent_voice` | |
| AI 일반 지식 | AI 일반 지식 | outside-PDF explanation — `external_references.explain_outside_pdf`, 보조 설명 역할 `supplemental` | 원문이 설명하지 않는 용어의 짧은 일반 설명 |
| 원문 그림 | 원문 PDF 그림 | `source_figures.figures_for_step` | 원문에서 잘라 낸 그림 |
| AI 가 그린 그림 | AI 가 그린 그림 — 실제와 다를 수 있음 | `drawn_diagrams.DRAWN_LABEL` | 요청했을 때만 |
| 웹 설명·사진 | 웹 참고 자료 | `web_explanations.py` | 설명과 사진만. 값·안전 지시는 원문만 |
| 공개 자료 | 공개 자료 · 일반 참고 — <출처 이름> | public knowledge — `public_knowledge.PublicKnowledgeSource`, tier `public`, 등급 A/B/C | 라이선스 확인한 공개 자료(NIOSH·CDC·PubChem·OpenStax 1판·Europe PMC CC BY·위키백과)에서 찾은 조각. 참고일 뿐 원문 값·안전 판단을 바꾸지 않는다 |
| 연결이 끊겼어요 | 연결이 끊겼어요 — 다시 연결 중 | `static/connection.js`; 사건 `connection_restored` | 화면이 읽기 전용이 된다 |

## 4. 기록과 보고서

| 사용자 말 | 지금 화면·서버 말 | 코드 이름 | 뜻 |
|---|---|---|---|
| 기록 | 실험 기록, 타임라인, 체크포인트 | experiment report ledger `experiment_report_events`; 작업공간 `experiment_session_events` | 지우지 않고 덧붙이기만 하는 장부 |
| 저장해 둔 지점 | 체크포인트 | checkpoint — `POST …/checkpoint-restart` | 끝난 실험의 그 지점에서 새 실험 |
| 지난 실험 기록 | 기록 탭의 목록 → 한 실험 기록 | `#/records`, `#/records/<id>`, `renderRecordsViews` | 서버의 목록에서 타임라인과 보고서 링크를 연다. 남의 실험은 읽기만 |
| 실험 노트 | 실험 노트 | `experiment_reports.LabNotebook`, `NOTEBOOK_SECTIONS` | 화면·Word·PDF·Markdown이 공유하는 여덟 절 초안. 목적·결과는 사람 기록만, 해석은 빈칸 |
| Methods 조건표 | Methods 조건표 | `NotebookInventoryItem`, `notebook_inventory_recorded` | 원문 시약·장비와 선택 입력한 구매처·카탈로그·Lot |
| 같은 프로토콜 기록 비교 | 같은 프로토콜 기록 비교 | `notebook_share_changed`, `notebook_comparison_reports`, `GET …/comparisons` | 같은 연구실에서 현재 공유에 동의한 기록의 사실만 소유자 식별자를 빼고 읽기 전용으로 비교 |
| 연구실 노트 절 이름·순서 | 연구실 노트 절 이름·순서 | `experiment_notebook_templates`, `/api/notebook-template` | 관리자가 여덟 절의 이름과 순서만 정하는 덧붙이기 설정 |
| 보고서 | Word·PDF·Markdown·JSON·CSV·모임 요약·결과 표 내려받기 | `GET /api/experiment-reports/{id}.{형식}` | 기존 장부에서 다시 만든 파생 파일 |
| AI 정리 | 실험 노트에는 표시하지 않음 | report writer — `check_ai_notes` | 별도 파생 출력에 쓰는 검사 통과 제안; 실험 노트 본문에는 넣지 않음 |
| 미확인 | [미확인] | 보고서 값 확인 `POST …/review/{item_id}` | 실험자가 아직 확인하지 않은 값 |
| 평점 | 이번 실험에 도움이 됐나요? (지금 화면) · 오늘 안내는 어땠나요? (새 화면의 끝 화면) | `POST /api/pilot/feedback`, `pilot_feedback`, `FEEDBACK_TAGS` | 1–5 와 한 줄 메모(선택); 새 화면은 표정 단추와 칩(`tags`) |

## 5. 계정과 운영

| 사용자 말 | 지금 화면·서버 말 | 코드 이름 | 뜻 |
|---|---|---|---|
| 계정 지우기 | 설정 탭의 계정 지우기 | `POST /api/auth/delete-account` | 현재 비밀번호 재확인, 마지막 관리자 삭제 불가 |
| 로그인 · 가입 | 로그인, 가입하기 | `VOINEY_LAB_AUTH_MODE=accounts`, `accounts.py` | 계정 모드에서만 |
| 초대 링크 | 초대 링크 | invite — `AccountStore.create_invite`, `scripts/pilot_invite.py` | 서버 셸에서 만든다 |
| 관리자 | · 관리자 | account `admin` flag, `server._account_is_admin` | 지표 화면·확인함·계정 끄기 |
| 파일럿 지표 | 파일럿 지표 | `pilot_metrics.py`, `/admin-metrics.html` | 숫자·코드·시각만 |
| 개발 신원 | 개발 로그인 · 계정 없음 (개발 모드) | `identity.DevIdentityProvider` | 계정 모드가 아닐 때의 한 사람 |

## 6. 대화가 지나가는 길 (개발자용)

| 말 | 코드 이름 | 뜻 |
|---|---|---|
| 앞단 규칙 | `CuratedProtocolSession.front_plan`, `FRONT_RULES` | 모델을 기다리지 않고 먼저 읽는 규칙(응급 문장은 `server.py` 가 그보다 먼저) |
| LLM 라우터 | `llm_router.route_turn_with_llm_router` | 답하거나 도구(`change_state`, `record_log`)로 바꾸기를 제안 |
| 라우터 먼저 | `VOINEY_LAB_ROUTER_FIRST` | 앞단 규칙을 줄이고 라우터가 더 많이 고르게 하는 스위치(꺼짐) |
| 서버 검증 | `llm_router.validate_tool_proposals`, `answer_checks.py` | 제안과 답을 서버가 검사 |
| 규칙 길 | `runtime_routing.route_curated_runtime_turn` | 라우터가 꺼졌거나 실패했을 때의 같은 말의 길 |
| 상태 기계 | `CuratedProtocolSession` | 단계·완료·타이머를 바꾸는 유일한 곳 |
