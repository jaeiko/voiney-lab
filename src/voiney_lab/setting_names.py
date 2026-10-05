"""The setting name table: every environment setting the repository's code reads.

One row per name, with the area a ``.env`` groups it under, its default and a
one-line meaning. ``tests/test_setting_names.py`` checks that the names the
code spells (src, scripts, CI, the Playwright config) are exactly the names in
this table, and that no old name is left anywhere but ``setting_renames``.

Every application setting carries the ``VOINEY_LAB_`` prefix (decision of
2026-10-04). A row without the prefix says why in ``kept_because``: the name
belongs to other software, or to the shell that starts the server.

This module is also the start-up guard. A server, launcher or test run that
finds an old name in its environment refuses to start and names it -- the name
only, never the value -- so a ``.env`` that was not migrated cannot silently
fall back to defaults. ``scripts/migrate_env.py`` renames them.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from voiney_lab.setting_renames import renamed

PROJECT_ROOT = Path(__file__).resolve().parents[2]

#: The order ``scripts/migrate_env.py`` writes a ``.env`` in.
AREAS = ("공급자 키", "OCR", "모델", "기능 켜기·끄기", "경로·저장소", "그 밖")
KEYS, OCR, MODELS, SWITCHES, PATHS, OTHER = AREAS


@dataclass(frozen=True)
class Setting:
    name: str
    area: str
    default: str
    meaning: str
    kept_because: str = ""


_NONE = "(없음)"

SETTINGS: tuple[Setting, ...] = (
    # --- 공급자 키 --------------------------------------------------------------
    Setting("XAI_API_KEY", KEYS, _NONE, "xAI API 키 (모든 모델 호출)",
            kept_because="공급자 SDK 표준 이름 (사람 결정 2)"),
    Setting("XAI_BASE_URL", KEYS, "https://api.x.ai/v1", "xAI API 주소",
            kept_because="공급자 SDK 표준 이름 (사람 결정 2)"),
    Setting("VOINEY_LAB_MOSS_PROJECT_ID", KEYS, _NONE, "Moss 프로젝트 ID"),
    Setting("VOINEY_LAB_MOSS_PROJECT_KEY", KEYS, _NONE, "Moss 프로젝트 키"),
    Setting("VOINEY_LAB_SECRET_REFERENCES", KEYS, "{}",
            "커넥터 secret:// 참조 → 비밀을 담은 환경 변수 이름 (JSON)"),
    # --- OCR ------------------------------------------------------------------
    Setting("VOINEY_LAB_OCR_PROVIDERS", OCR, _NONE,
            "쓸 OCR 엔진: clova, google (쉼표로)"),
    Setting("VOINEY_LAB_CLOVA_OCR_INVOKE_URL", OCR, _NONE,
            "NAVER CLOVA OCR 호출 주소 (https)"),
    Setting("VOINEY_LAB_CLOVA_OCR_SECRET", OCR, _NONE, "CLOVA OCR 비밀 키"),
    Setting("VOINEY_LAB_GOOGLE_VISION_API_KEY", OCR, _NONE,
            "Google Cloud Vision API 키"),
    # --- 모델 -----------------------------------------------------------------
    # --- 모델: 역할별 공급자·모델·추론 (줄 M1, 결정 1) ---------------------------
    Setting("VOINEY_LAB_ROUTER_PROVIDER", MODELS, "xai",
            "LLM 라우터 공급자 (xai/anthropic/openai/google)"),
    Setting("VOINEY_LAB_ROUTER_MODEL", MODELS, "grok-4.20-0309-non-reasoning",
            "LLM 라우터 모델"),
    Setting("VOINEY_LAB_ROUTER_REASONING", MODELS, "(공급자 기본)",
            "LLM 라우터 추론 (none/low/medium/high/xhigh/max)"),
    Setting("VOINEY_LAB_ANSWER_PROVIDER", MODELS, "xai",
            "답변 공급자: 대화·승인 문서 답변, multi-brain, 인계 워커"),
    Setting("VOINEY_LAB_ANSWER_MODEL", MODELS,
            "(없음: 대화는 필수, 워커 grok-4, multi-brain grok-4.6)", "답변 모델"),
    Setting("VOINEY_LAB_ANSWER_REASONING", MODELS, "(공급자 기본)", "답변 추론"),
    Setting("VOINEY_LAB_TRANSLATION_PROVIDER", MODELS, "xai",
            "번역 공급자: 리비전 한국어, 단계 읽기 번역"),
    Setting("VOINEY_LAB_TRANSLATION_MODEL", MODELS, "grok-4.6", "번역 모델"),
    Setting("VOINEY_LAB_TRANSLATION_REASONING", MODELS, "(공급자 기본)", "번역 추론"),
    Setting("VOINEY_LAB_ANALYSIS_PROVIDER", MODELS, "xai", "PDF 프로토콜 분석 공급자"),
    Setting("VOINEY_LAB_ANALYSIS_MODEL", MODELS, _NONE,
            "PDF 프로토콜 분석 모델 (분석에 필수)"),
    Setting("VOINEY_LAB_ANALYSIS_REASONING", MODELS, "high",
            "프로토콜 분석 추론 (low/medium/high/xhigh)"),
    Setting("VOINEY_LAB_REPORT_PROVIDER", MODELS, "xai", "실험 보고서 서술 공급자"),
    Setting("VOINEY_LAB_REPORT_MODEL", MODELS,
            "VOINEY_LAB_SUPPLEMENTAL_MODEL → grok-4.6", "실험 보고서 서술 모델"),
    Setting("VOINEY_LAB_REPORT_REASONING", MODELS, "(공급자 기본)", "보고서 서술 추론"),
    Setting("VOINEY_LAB_SUPPLEMENTAL_PROVIDER", MODELS, "xai",
            "PDF 밖 설명 공급자: 보조 모델 지식, 외부 근거 웹 검색(xai 만)"),
    Setting("VOINEY_LAB_SUPPLEMENTAL_MODEL", MODELS, "grok-4.6",
            "PDF 밖 설명·외부 근거 검색 모델"),
    Setting("VOINEY_LAB_SUPPLEMENTAL_REASONING", MODELS, "low",
            "외부 근거 검색 추론 (low/medium/high)"),
    Setting("VOINEY_LAB_LLM_ROUTER_TIMEOUT_SECONDS", MODELS, "2.5",
            "LLM 라우터 제한 시간 (초, 0.2–30)"),
    Setting("VOINEY_LAB_ANSWER_BRAIN_PRIMARY_BUDGET_SECONDS", MODELS, "1.25",
            "Answer 역할 1차 응답 예산 (초)"),
    Setting("VOINEY_LAB_ANSWER_BRAIN_TIMEOUT_SECONDS", MODELS, "8",
            "Answer 역할 제한 시간 (초)"),
    Setting("VOINEY_LAB_PLANNER_BRAIN_TIMEOUT_SECONDS", MODELS, "6",
            "Planner 역할 제한 시간 (초)"),
    Setting("VOINEY_LAB_SEMANTIC_INTENT_MODEL", MODELS,
            "grok-4.20-0309-non-reasoning", "의미 의도 보조 모델"),
    Setting("VOINEY_LAB_SEMANTIC_INTENT_TIMEOUT_SECONDS", MODELS, "2.5",
            "의미 의도 제한 시간 (초, 0.2–8)"),
    Setting("VOINEY_LAB_SEMANTIC_INTENT_MIN_CONFIDENCE", MODELS, "0.6",
            "의미 의도 읽기 전용 제안의 최소 확신도"),
    Setting("VOINEY_LAB_SEMANTIC_INTENT_MUTATION_MIN_CONFIDENCE", MODELS, "0.85",
            "의미 의도 상태 변경 제안의 최소 확신도"),
    Setting("VOINEY_LAB_REPORT_WRITER_TIMEOUT_SECONDS", MODELS, "25",
            "보고서 서술 제한 시간 (초)"),
    Setting("VOINEY_LAB_GENERATED_VISUAL_MODEL", MODELS, "grok-imagine-image-2.0",
            "설명 그림 생성 모델"),
    Setting("VOINEY_LAB_GENERATED_VISUAL_TIMEOUT_SECONDS", MODELS, "60",
            "그림 생성 제한 시간 (초, 5–120)"),
    Setting("VOINEY_LAB_EXTERNAL_REFERENCE_TIMEOUT_SECONDS", MODELS,
            "90 (candidate_a 프로필 20)", "외부 근거 검색 전체 제한 시간 (초)"),
    Setting("VOINEY_LAB_EXTERNAL_REFERENCE_CONNECT_TIMEOUT_SECONDS", MODELS, "5",
            "외부 근거 검색 연결 제한 (초)"),
    Setting("VOINEY_LAB_EXTERNAL_REFERENCE_READ_TIMEOUT_SECONDS", MODELS, "90",
            "외부 근거 검색 읽기 제한 (초)"),
    Setting("VOINEY_LAB_EXTERNAL_REFERENCE_ENRICHMENT_BUDGET_SECONDS", MODELS, "4",
            "답에 외부 근거를 기다리는 예산 (초, 전체 제한보다 짧게)"),
    Setting("VOINEY_LAB_EXTERNAL_REFERENCE_SERVICE_TIER", MODELS, "default",
            "외부 근거 검색 서비스 등급 (default/priority)"),
    Setting("VOINEY_LAB_EXTERNAL_REFERENCE_MAX_TURNS", MODELS, "1",
            "외부 근거 검색 최대 턴 수 (1–5)"),
    Setting("VOINEY_LAB_SUPPLEMENTAL_MODEL_KNOWLEDGE_TIMEOUT_SECONDS", MODELS, "8",
            "보조 모델 지식 제한 시간 (초, 1–15)"),
    Setting("VOINEY_LAB_WEB_VISUAL_TIMEOUT_SECONDS", MODELS, "6.0",
            "웹 이미지 검색 제한 시간 (초)"),
    Setting("VOINEY_LAB_TTS_VOICE", MODELS, "leo", "음성 합성 목소리"),
    Setting("VOINEY_LAB_XAI_STT_VAD_THRESHOLD", MODELS, "0.5",
            "xAI STT 음성 감지 임계값 (0–1)"),
    Setting("VOINEY_LAB_XAI_STT_FILLER_WORDS", MODELS, "0",
            "xAI STT 간투사 받아쓰기 (0/1)"),
    # --- 기능 켜기·끄기 -----------------------------------------------------------
    Setting("VOINEY_LAB_PROTOCOL_ENABLED", SWITCHES, "false", "PDF 프로토콜 카탈로그"),
    Setting("VOINEY_LAB_WORKSPACE_ENABLED", SWITCHES, "false",
            "작업공간 (조직·권한·연동)"),
    Setting("VOINEY_LAB_EXPERIMENT_REPORTS_ENABLED", SWITCHES, "false (빈 값)",
            "실험 기록 장부"),
    Setting("VOINEY_LAB_MOSS_ENABLED", SWITCHES, "false",
            "Moss 승인 안전 문서 검색"),
    Setting("VOINEY_LAB_MOSS_AUTO_REFRESH", SWITCHES, "false",
            "Moss 색인 자동 새로 고침"),
    Setting("VOINEY_LAB_LLM_ROUTER_ENABLED", SWITCHES, "false", "LLM 라우터"),
    Setting("VOINEY_LAB_MULTI_BRAIN_ENABLED", SWITCHES, "false",
            "Answer/Source/Visual 모델 역할"),
    Setting("VOINEY_LAB_ANSWER_BRAIN_ENABLED", SWITCHES,
            "VOINEY_LAB_MULTI_BRAIN_ENABLED", "Answer 역할"),
    Setting("VOINEY_LAB_SOURCE_BRAIN_ENABLED", SWITCHES,
            "VOINEY_LAB_MULTI_BRAIN_ENABLED", "Source 역할"),
    Setting("VOINEY_LAB_VISUAL_BRAIN_ENABLED", SWITCHES,
            "VOINEY_LAB_MULTI_BRAIN_ENABLED", "Visual 역할"),
    Setting("VOINEY_LAB_SEMANTIC_INTENT_ENABLED", SWITCHES, "false",
            "의미 의도 보조 (제안만, 서버가 다시 검사)"),
    Setting("VOINEY_LAB_REPORT_WRITER_ENABLED", SWITCHES, "true",
            "실험 보고서 서술 모델"),
    Setting("VOINEY_LAB_GENERATED_VISUALS_ENABLED", SWITCHES, "false",
            "설명 그림 생성"),
    Setting("VOINEY_LAB_EXTERNAL_REFERENCES_ENABLED", SWITCHES, "false",
            "외부 근거 검색"),
    Setting("VOINEY_LAB_SUPPLEMENTAL_MODEL_KNOWLEDGE_ENABLED", SWITCHES, "false",
            "보조 모델 지식 설명"),
    Setting("VOINEY_LAB_WEB_VISUAL_SEARCH_ENABLED", SWITCHES, "false (빈 값)",
            "웹 이미지 검색"),
    Setting("VOINEY_LAB_STT_DIAGNOSTICS_ENABLED", SWITCHES, "false",
            "실제 음성 STT 진단 녹음 보관"),
    Setting("VOINEY_LAB_PROTOCOL_CLAIM_CHUNKS_ENABLED", SWITCHES, "false",
            "프로토콜 주장 조각 분석"),
    Setting("VOINEY_LAB_AUTO_ACTIVATE_READY_UPLOADS", SWITCHES, "false",
            "준비된 업로드 자동 활성화 (operational 범위에서는 늘 꺼짐)"),
    Setting("VOINEY_LAB_TEST_MODE_SKIP_READINESS_GATES", SWITCHES, "false",
            "개발 테스트 모드: 실행 준비 게이트를 건너뜀"),
    # --- 경로·저장소 --------------------------------------------------------------
    Setting("VOINEY_LAB_SAFETY_CATALOG", PATHS, "(없음, 필수)",
            "승인 안전 문서 카탈로그 SQLite (절대 경로)"),
    Setting("VOINEY_LAB_PROTOCOL_DATA_DIR", PATHS, "(없음, 켜면 필수)",
            "프로토콜 카탈로그 데이터 폴더 (절대 경로)"),
    Setting("VOINEY_LAB_WORKSPACE_DATA_DIR", PATHS, "(없음, 켜면 필수)",
            "작업공간 데이터 폴더 (절대 경로)"),
    Setting("VOINEY_LAB_EXPERIMENT_REPORT_DB", PATHS, "(없음, 켜면 필수)",
            "실험 기록 SQLite"),
    Setting("VOINEY_LAB_EXPERIMENT_REPORTS_DATABASE", PATHS, _NONE,
            "VOINEY_LAB_EXPERIMENT_REPORT_DB 가 없을 때 쓰는 별칭"),
    Setting("VOINEY_LAB_PROCEDURE_CATALOG", PATHS, _NONE,
            "옛 절차 스택 카탈로그 (STORE 와 함께 설정해야 켜짐)"),
    Setting("VOINEY_LAB_PROCEDURE_STORE", PATHS, _NONE, "옛 절차 스택 저장소"),
    Setting("VOINEY_LAB_CURATED_PROTOCOL_FIXTURE", PATHS, _NONE,
            "개발용 큐레이션 프로토콜 픽스처 (셋 함께)"),
    Setting("VOINEY_LAB_CURATED_PROTOCOL_PROVENANCE", PATHS, _NONE,
            "픽스처 출처 기록"),
    Setting("VOINEY_LAB_CURATED_PROTOCOL_SOURCE_PDF", PATHS, _NONE,
            "픽스처 원본 PDF"),
    Setting("VOINEY_LAB_CANDIDATE_A_SOURCE_PDF", PATHS,
            "data/runtime/candidate-a-source/in-gel-digestion.pdf",
            "run_dev.sh 가 쓸 in-gel 원본 PDF (셸에서만 읽음)"),
    Setting("VOINEY_LAB_CHUNK_CACHE_DIR", PATHS,
            "data/development_cache/chunk_analysis", "조각 분석 캐시 폴더"),
    Setting("VOINEY_LAB_STT_DIAGNOSTIC_DIR", PATHS, "data/runtime/stt-diagnostics",
            "STT 진단 녹음 폴더 (data/runtime 아래만)"),
    # --- 그 밖 ----------------------------------------------------------------
    Setting("VOINEY_LAB_USAGE_SCOPE", OTHER, "(없음, 필수)",
            "사용 범위: operational/demo/reference_only/test_only"),
    Setting("VOINEY_LAB_SAFETY_USAGE_SCOPE", OTHER, _NONE,
            "USAGE_SCOPE 가 비었을 때 개발 활성화 판단에 쓰는 별칭"),
    Setting("VOINEY_LAB_FACILITY_ID", OTHER, _NONE, "시설 ID"),
    Setting("VOINEY_LAB_SESSION_LANGUAGE", OTHER, "ko", "기본 세션 언어"),
    Setting("VOINEY_LAB_ALLOWED_LANGUAGES", OTHER, "ko,en,vi", "허용 세션 언어"),
    Setting("VOINEY_LAB_ADMIN_TOKEN", OTHER, "(없음, 없으면 관리자 지표 닫힘)",
            "GET /api/admin/metrics 관리자 토큰"),
    Setting("VOINEY_LAB_PROTOCOL_APPROVAL_TOKEN", OTHER, _NONE,
            "작업공간 없이 프로토콜 승인·OCR 검토에 쓰는 공유 비밀"),
    Setting("VOINEY_LAB_DEV_AUTH_PROFILES", OTHER, "(없음, local-admin 하나)",
            "개발 신원 프로필 (JSON)"),
    Setting("VOINEY_LAB_OIDC_ISSUER", OTHER, _NONE, "OIDC 발급자 (셋 함께, https)"),
    Setting("VOINEY_LAB_OIDC_AUDIENCE", OTHER, _NONE, "OIDC 대상"),
    Setting("VOINEY_LAB_OIDC_JWKS_URL", OTHER, _NONE, "OIDC JWKS 주소 (https)"),
    Setting("VOINEY_LAB_OIDC_TENANT_CLAIM", OTHER, "organization_id",
            "OIDC 조직 클레임"),
    Setting("VOINEY_LAB_OIDC_ROLES_CLAIM", OTHER, "roles", "OIDC 역할 클레임"),
    Setting("VOINEY_LAB_OIDC_NAME_CLAIM", OTHER, "name", "OIDC 이름 클레임"),
    Setting("VOINEY_LAB_ANALYTICS_RETENTION_DAYS", OTHER, "90",
            "작업공간 분석 보관 일수 (1–3650)"),
    Setting("VOINEY_LAB_MOSS_INDEX_NAME", OTHER, _NONE, "Moss 색인 이름"),
    Setting("VOINEY_LAB_MOSS_MODEL_ID", OTHER, "moss-minilm",
            "Moss 색인 임베딩 모델 (sync_moss_index.py)"),
    Setting("VOINEY_LAB_MOSS_ALLOWED_SCOPES", OTHER, "demo,reference_only",
            "Moss 를 쓸 수 있는 사용 범위"),
    Setting("VOINEY_LAB_MOSS_ALPHA", OTHER, "0.65", "Moss 혼합 검색 가중치 (0–1)"),
    Setting("VOINEY_LAB_MOSS_CANDIDATE_LIMIT", OTHER, "64", "Moss 후보 수 (3–100)"),
    Setting("VOINEY_LAB_MOSS_QUERY_TIMEOUT_MS", OTHER, "250",
            "Moss 질의 제한 시간 (ms)"),
    Setting("VOINEY_LAB_MOSS_LOAD_TIMEOUT_SECONDS", OTHER, "60",
            "Moss 색인 적재 제한 시간 (초)"),
    Setting("VOINEY_LAB_MOSS_REFRESH_SECONDS", OTHER, "600",
            "Moss 자동 새로 고침 간격 (초)"),
    Setting("VOINEY_LAB_EXTERNAL_REFERENCE_DOMAIN_PROFILE", OTHER, _NONE,
            "외부 근거 도메인 프로필 (open/candidate_a/government_safety)"),
    Setting("VOINEY_LAB_EXTERNAL_REFERENCE_DOMAINS", OTHER, _NONE,
            "외부 근거 허용 도메인 1–5개 (쉼표로)"),
    Setting("VOINEY_LAB_EXTERNAL_REFERENCE_ALLOWED_DOMAINS", OTHER, _NONE,
            "EXTERNAL_REFERENCE_DOMAINS 의 별칭 (값이 다르면 시작 거부)"),
    Setting("VOINEY_LAB_EXTERNAL_REFERENCE_MAX_CITATIONS", OTHER, "5",
            "외부 근거 최대 인용 수 (1–5)"),
    Setting("VOINEY_LAB_EXTERNAL_REFERENCE_CACHE_TTL_SECONDS", OTHER, "900",
            "외부 근거 검증 결과 캐시 시간 (초)"),
    Setting("VOINEY_LAB_EXTERNAL_SEARCH_DISPLAY_MODE", OTHER, _NONE,
            "open 이면 도메인 제한 없이 검색"),
    Setting("VOINEY_LAB_CASCADE_VAD_MODE", OTHER, "3", "음성 감지 민감도 (0–3)"),
    Setting("VOINEY_LAB_CASCADE_VAD_ONSET_VOICED_FRAMES", OTHER, "4",
            "말 시작 판정: 유성 프레임 수"),
    Setting("VOINEY_LAB_CASCADE_VAD_ONSET_WINDOW_FRAMES", OTHER, "6",
            "말 시작 판정: 창 프레임 수"),
    Setting("VOINEY_LAB_CASCADE_VAD_PREFIX_MS", OTHER, "300",
            "말 앞부분 보존 (ms)"),
    Setting("VOINEY_LAB_CASCADE_BARGE_IN_PREFIX_MS", OTHER, "800",
            "끼어들기 때 말 앞부분 보존 (ms)"),
    Setting("VOINEY_LAB_CASCADE_VAD_ENDPOINT_SILENCE_MS", OTHER, "1000",
            "말 끝 판정 침묵 (ms)"),
    Setting("VOINEY_LAB_CASCADE_VAD_MIN_SPEECH_MS", OTHER, "240",
            "최소 발화 길이 (ms)"),
    Setting("VOINEY_LAB_CASCADE_VAD_MAX_UTTERANCE_MS", OTHER, "15000",
            "최대 발화 길이 (ms)"),
    Setting("VOINEY_LAB_CASCADE_VAD_COOLDOWN_MS", OTHER, "300",
            "발화 사이 쉬는 시간 (ms)"),
    Setting("VOINEY_LAB_CASCADE_VAD_PLAYBACK_ONSET_VOICED_FRAMES", OTHER, "12",
            "재생 중 말 시작 판정: 유성 프레임 수"),
    Setting("VOINEY_LAB_CASCADE_VAD_PLAYBACK_ONSET_WINDOW_FRAMES", OTHER, "15",
            "재생 중 말 시작 판정: 창 프레임 수"),
    Setting("VOINEY_LAB_CASCADE_VAD_LISTENING_ONSET_VOICED_FRAMES", OTHER, "8",
            "듣기 중 말 시작 판정: 유성 프레임 수"),
    Setting("VOINEY_LAB_CASCADE_VAD_LISTENING_ONSET_WINDOW_FRAMES", OTHER, "12",
            "듣기 중 말 시작 판정: 창 프레임 수"),
    Setting("VOINEY_LAB_CASCADE_VAD_LISTENING_RESUME_VOICED_FRAMES", OTHER, "6",
            "듣기 재개 판정: 유성 프레임 수"),
    Setting("VOINEY_LAB_CASCADE_VAD_LISTENING_RESUME_WINDOW_FRAMES", OTHER, "10",
            "듣기 재개 판정: 창 프레임 수"),
    Setting("VOINEY_LAB_CASCADE_FILLER_MODE", OTHER, "tone",
            "기다림 신호: tone 또는 phrase"),
    Setting("VOINEY_LAB_CASCADE_FILLER_DELAY_MS", OTHER, "700",
            "기다림 신호까지의 시간 (ms, 100–5000)"),
    Setting("VOINEY_LAB_CASCADE_FILLER_STATUS_DELAY_MS", OTHER, "1500",
            "진행 상황을 말하기까지의 시간 (ms, 200–10000)"),
    Setting("VOINEY_LAB_STT_DIAGNOSTIC_MAX_FILES", OTHER, "20",
            "STT 진단 녹음 최대 개수 (2–100)"),
    Setting("VOINEY_LAB_PDF_WORKER_TIMEOUT_SECONDS", OTHER, "30",
            "PDF 읽기 하위 프로세스 제한 시간 (초, 1–600)"),
    Setting("VOINEY_LAB_LAB_MANAGER_EMAIL", OTHER, "lab-manager@example.invalid",
            "인계 메일(.eml) 받는 사람"),
    Setting("VOINEY_LAB_FROM_EMAIL", OTHER, "voice_workflow_agent@example.invalid",
            "인계 메일(.eml) 보낸 사람"),
    Setting("VOINEY_LAB_PLAYWRIGHT_APP_PORT", OTHER, "8000",
            "로컬 Playwright 가 붙을 서버 포트 (playwright.config.ts)"),
    Setting("HOST", OTHER, "127.0.0.1", "실행 스크립트의 듣기 주소",
            kept_because="실행 스크립트가 셸에서만 읽는 흔한 이름. 바꾸면 시작 거부가 "
                         "다른 도구의 HOST 까지 막는다 (줄 V 에서 정함)"),
    Setting("PORT", OTHER, "8000 (run_pilot.sh 8080)", "실행 스크립트의 듣기 포트",
            kept_because="HOST 와 같음"),
    Setting("VIRTUAL_ENV", OTHER, _NONE, "run_ci_server.sh: 이미 켜진 venv 인지",
            kept_because="Python venv 가 정하는 이름"),
    Setting("RUNNER_TEMP", OTHER, _NONE, "CI: 임시 폴더 (안전 카탈로그를 둠)",
            kept_because="GitHub Actions 가 정하는 이름"),
    Setting("PYTEST_VERSION", OTHER, _NONE,
            "pytest 실행 중이면 있음: 서버·워커가 저장소 .env 를 읽지 않음 (줄 M1, 결정 6)",
            kept_because="pytest 가 정하는 이름 (pytest 8.2 이상)"),
)

#: Names that other software reads by a fixed name, or that decision 2 keeps,
#: which the repository's own code never spells. ``scripts/migrate_env.py``
#: never renames them; the ones a dependency reads are grouped with the
#: settings, and the rest are listed under "코드가 읽지 않는 설정".
EXTERNAL_NAMES: dict[str, tuple[str | None, str]] = {
    "OPENAI_API_KEY": (KEYS, "openai SDK 가 api_key 를 받지 못하면 읽음 "
                             "(openai/_client.py). 코드는 늘 api_key 를 준다"),
    "OPENAI_BASE_URL": (KEYS, "openai SDK 가 base_url 을 받지 못하면 읽음. 코드는 늘 준다"),
    "OPENAI_ORG_ID": (KEYS, "openai SDK 가 늘 읽어 요청 머리에 넣음"),
    "OPENAI_PROJECT_ID": (KEYS, "openai SDK 가 늘 읽어 요청 머리에 넣음"),
    "HTTP_PROXY": (OTHER, "httpx·requests 가 읽는 프록시"),
    "HTTPS_PROXY": (OTHER, "httpx·requests 가 읽는 프록시"),
    "ALL_PROXY": (OTHER, "httpx·requests 가 읽는 프록시"),
    "NO_PROXY": (OTHER, "httpx·requests 가 읽는 프록시 예외"),
    "SSL_CERT_FILE": (OTHER, "httpx 가 읽는 인증서 파일"),
    "SSL_CERT_DIR": (OTHER, "httpx 가 읽는 인증서 폴더"),
    "TESSDATA_PREFIX": (OTHER, "pymupdf 가 읽는 Tesseract 데이터 폴더"),
    "ANTHROPIC_API_KEY": (None, "공급자 SDK 표준 이름 (사람 결정 2). 이 저장소는 읽지 않음"),
    "GEMINI_API_KEY": (None, "공급자 SDK 표준 이름 (사람 결정 2). 이 저장소는 읽지 않음"),
    "ELEVENLABS_API_KEY": (None, "공급자 SDK 표준 이름 (사람 결정 2). 이 저장소는 읽지 않음"),
}

BY_NAME: dict[str, Setting] = {setting.name: setting for setting in SETTINGS}


def old_setting_names(names: Iterable[str]) -> list[str]:
    """The old setting names among ``names``, sorted and without repeats."""

    return sorted({name for name in names if renamed(name) is not None})


def old_names_message(old_names: Iterable[str]) -> str:
    """The refusal: how many old names, the names alone, and what to run."""

    old = list(old_names)
    return (
        f"옛 설정 이름 {len(old)}개: {', '.join(old)}. "
        "scripts/migrate_env.py 를 실행하세요."
    )


class OldSettingNamesError(RuntimeError):
    """An old setting name is set; the process refuses to start."""


def dotenv_names(path: Path) -> list[str]:
    """The names a ``.env`` sets, or none when there is no file."""

    if not path.is_file():
        return []
    from dotenv import dotenv_values

    return [name for name in dotenv_values(path) if name]


def refuse_old_setting_names(
    environment: Mapping[str, str] | None = None,
    *,
    dotenv_path: Path | None = None,
) -> None:
    """Raise when the environment (and the ``.env``, if given) sets an old name."""

    names = list(os.environ if environment is None else environment)
    if dotenv_path is not None:
        names.extend(dotenv_names(dotenv_path))
    old = old_setting_names(names)
    if old:
        raise OldSettingNamesError(old_names_message(old))


def main() -> int:
    """``python -m voiney_lab.setting_names``: the launchers' start-up check.

    Looks at the process environment and the repository ``.env`` the server
    would load, prints the refusal on stderr and exits 1 when an old name is
    set. Prints nothing and exits 0 otherwise.
    """

    try:
        refuse_old_setting_names(dotenv_path=PROJECT_ROOT / ".env")
    except OldSettingNamesError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
