# 문서 목록 — VoineyLab

2026-10-10 (줄 CL) 에 새로 썼다. 지금 동작은 코드와 테스트가 먼저다. 문서가 코드와 다르면 코드가 맞고, 문서를 고친다.
제품 이름은 VoineyLab(회사 Voiney)이다. 옛 문서와 코드의 "Voice Workflow Agent" 는 같은 제품이다.

## 1. 지금 동작을 설명하는 문서 — 먼저 읽는다

우선순위는 `CLAUDE.md`·`AGENTS.md` 의 목록과 같다.

| 문서 | 무엇이 들어 있나 |
|---|---|
| [`README.md`](../README.md) | 한 문장 정의와 상태, 지금 흐름, 값과 설명의 원칙, 하지 않는 것, 역할별 공급자, 실행 방법, 테스트 기준, 데이터·외부 AI, 라이선스, 다음 계획 |
| [`AGENTS.md`](../AGENTS.md), [`.agent/`](../.agent/) | 코드가 지켜야 할 규칙. `.agent/architecture.md` 가 지금 구조의 기준 |
| [`BEHAVIOR_REFERENCE.md`](BEHAVIOR_REFERENCE.md) | 지금 동작의 상세(영어). 2026-10-10 README 를 한국어로 다시 쓰며 옛 README 의 절들을 옮기고 줄 CL 에 맞게 고침. README 다음 |
| [`CLAUDE.md`](../CLAUDE.md) | 코딩 에이전트의 작업 규칙 |
| [`CURRENT_ARCHITECTURE.md`](CURRENT_ARCHITECTURE.md) | 구성 요소, 상태 권한, 저장, 실패 처리 |
| [`MIGRATION_NOTES.md`](MIGRATION_NOTES.md) | 저장 스키마와 설정 이름이 바뀐 기록 (줄 CL 의 지운 설정 포함) |
| [`GIT_WORKFLOW.md`](GIT_WORKFLOW.md) | 브랜치, 커밋, PR, 병합 규칙 |

## 2. 운영 문서

| 문서 | 무엇이 들어 있나 |
|---|---|
| [`USER_GUIDE.md`](USER_GUIDE.md) | 실험자 화면 쓰는 법 (2026-10-10 음성 시험에서 쓴 기능 포함) |
| [`PILOT_DEPLOYMENT_GUIDE.md`](PILOT_DEPLOYMENT_GUIDE.md) | 파일럿 준비, 실행, 마무리 |
| [`DEPLOYMENT_RUNBOOK.md`](DEPLOYMENT_RUNBOOK.md) | 프로세스, 점검(`/healthz`·`/readyz`), 백업·복구, 모니터링 |
| [`TROUBLESHOOTING_GUIDE.md`](TROUBLESHOOTING_GUIDE.md) | 증상별로 상태를 지키며 복구하는 법 |
| [`APPROVED_DOCUMENT_OPERATIONS.md`](APPROVED_DOCUMENT_OPERATIONS.md) | 승인 안전 문서 카탈로그 만들기 — 파일럿 실행기가 요구하는 파일과 단계 안전 카드가 읽는 파일. 카탈로그를 어떻게 할지는 줄 PL 에서 정한다 |
| [`PILOT_READINESS_PACKAGE.md`](PILOT_READINESS_PACKAGE.md) | 파일럿 점검표, KPI 정의, 참여자 안내, 중단 기준 |
| [`VOICE_FIELD_EVALUATION_PLAN.md`](VOICE_FIELD_EVALUATION_PLAN.md) | 현장 음성 평가 계획 (아직 하지 않음) |

## 3. 설계·기록 — 지금 동작의 기준이 아니다

| 문서 | 무엇인가 | 왜 이 자리에 있나 |
|---|---|---|
| [`ARCHITECTURE_MAP.md`](ARCHITECTURE_MAP.md) | Laboratory Workflow OS 이전의 구조 스냅샷 | `CLAUDE.md` 가 이 경로를 이름으로 가리킨다(이 줄은 `CLAUDE.md` 를 고치지 않는다). 내용은 기록으로만 읽는다 |
| [`PROTOCOL_BOUNDARY_AND_OBLIGATION_DESIGN.md`](PROTOCOL_BOUNDARY_AND_OBLIGATION_DESIGN.md) | 분석의 단락 경계·완전성 의무에 대한 결함 기록과 설계 (스스로 "design only" 라고 적음) | 지금 분석 작업(줄 EV2)이 다루는 영역의 설계라서 남김 |
| [`course-archive/`](course-archive/) | 이 저장소가 시작된 강의 자료 | 기록으로만 둔다 |
| [`archive/`](archive/) | 보관 문서 | 읽지 않는다(`CLAUDE.md`). 사람이 지난 결정을 물을 때만 |

## 4. 2026-10-10 에 `docs/archive/` 로 보관한 문서

맨 위에 "보관: 2026-10-08 MVP 이전 설계 — PI·관리자 승인·반려·회수는 폐기, 리비전·검토자 입력 개념은 줄 RV 에서 다시 설계" 한 줄을 넣었다.

| 문서 | 보관한 이유 (내용으로 판단) |
|---|---|
| `WEB_WIREFRAMES.md` | 6주 PoC 화면 설계: 승인자료 검색·이상상황 보고·인계 상태 확인 화면. 그 기능은 없다 |
| `LAB_ADMIN_PRODUCT_PLAN.md` | PI·관리자 제품 계획 (승인·역할). 폐기된 개념 |
| `PRODUCT_IMPROVEMENT_PROPOSAL.md` | 옛 procedures 엔진을 전제로 한 개선 제안 |
| `RESTRUCTURE_PLAN.md` | 2026-09-29 에 끝난 이름·위치 정리 계획과 결과 |
| `ANALYSIS_2026_09_11.md` | 2026-09-11 시점의 읽기 전용 조사 |
| `CAPABILITY_MATRIX.md` | 2026-08-24 능력 분류표. 공급자·실제 호출 기록이 지금과 다르다 — 지금 값과 실제 호출 기록은 README §4 |
| `OPERATOR_PROCEDURE_REPEAT_STEP_GATE.md` | "검토자 해소"로 시작하는 2026-09-07 절차서. 검토자 단계가 없다 |
| `MOSS_RETRIEVAL.md` | Moss 재순위 안내. 그 코드를 줄 CL 에서 지웠다(지운 설정 이름은 `VOINEY_LAB_` 접두사를 뗐다) |
