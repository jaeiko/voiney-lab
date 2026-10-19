# 문서 목록 — VoineyLab

2026-10-14 (줄 DOC3) 에 새로 썼다. 지금 동작은 코드와 테스트가 먼저다. 문서가 코드와 다르면 코드가 맞고, 문서를 고친다.
제품 이름은 VoineyLab(회사 Voiney)이다. 옛 문서와 코드의 "Voice Workflow Agent" 는 같은 제품이다.
상태 표현은 "MVP 시제품 — 현장 미검증"이다.

문서는 **지금 동작**만 쓴다. 아직 없는 것은 README 9절과 `.agent/roadmap.md` 의 "다음 계획"에 따로 있다.

## 1. 지금 동작을 설명하는 문서 — 먼저 읽는다

우선순위는 `CLAUDE.md`·`AGENTS.md` 의 목록과 같다.

| 문서 | 무엇이 들어 있나 |
|---|---|
| [`README.md`](../README.md) | 한 문장 정의와 상태, 지금 흐름, 값과 설명의 원칙, 하지 않는 것, 역할별 공급자, 실행 방법, 테스트 기준, 데이터·외부 AI, 라이선스, 다음 계획 |
| [`AGENTS.md`](../AGENTS.md), [`.agent/`](../.agent/) | 코드가 지켜야 할 규칙. `.agent/architecture.md` 가 지금 구조의 기준 |
| [`BEHAVIOR_REFERENCE.md`](BEHAVIOR_REFERENCE.md) | 지금 동작의 상세(영어). 옛 README 의 절들을 옮겨 둔 것. README 다음 |
| [`CLAUDE.md`](../CLAUDE.md) | 코딩 에이전트의 작업 규칙 |
| [`CURRENT_ARCHITECTURE.md`](CURRENT_ARCHITECTURE.md) | 구성 요소, 상태 권한, 저장, 실패 처리 |
| [`MIGRATION_NOTES.md`](MIGRATION_NOTES.md) | 저장 스키마와 설정 이름이 바뀐 기록 (지운 설정 포함) |
| [`GIT_WORKFLOW.md`](GIT_WORKFLOW.md) | 브랜치, 커밋, PR, 병합 규칙 |
| [`VOICE_AND_TONE.md`](VOICE_AND_TONE.md) | 화면·음성 문구를 쓰는 규칙(해요체, 한 문장 한 뜻, 내부 말 금지, 단추는 동사)과 용어 사전. 새 문구에 적용한다. 지금 문구를 이 규칙대로 고치는 일은 아직 안 했다(2단계) |

`.agent/` 안의 파일: `architecture.md`(지금 구조), `product_context.md`(제품이 약속하는 것·하지 않는 것),
`roadmap.md`(지금 상태와 다음 계획), `coding_rules.md`·`security_rules.md`·`testing_strategy.md`·`evaluation_strategy.md`
(지켜야 할 규칙과 평가 기준), `product_improvement_strategy.md`(우선순위를 매기는 일반 틀 — 계획은 `roadmap.md` 가 앞선다).

## 2. 운영 문서

| 문서 | 무엇이 들어 있나 |
|---|---|
| [`USER_GUIDE.md`](USER_GUIDE.md) | 실험자 화면 쓰는 법 — 올리기·긴 문서·다시 분석, 목소리 맞추기·시끄러운 곳, 휴대폰 (영어) |
| [`PILOT_GUIDE.md`](PILOT_GUIDE.md) | 파일럿을 여는 순서 — 기관 안전 문서가 있을 때와 없을 때(`source_protocol_only`), 실행, 데이터 처리, 외부 AI 고지 초안, 데이터 삭제 도구, 사람이 할 일 |
| [`PILOT_HTTPS_SETUP.md`](PILOT_HTTPS_SETUP.md) | 휴대폰에서 마이크를 쓰려면 필요한 HTTPS 준비(도메인을 정한 뒤 사람이 실행. 실제 서버에서는 아직 실행하지 않음) |
| [`SOURCE_FORMATS.md`](SOURCE_FORMATS.md) | 올릴 수 있는 원문 형식 — PDF · Word · 사진 · 시약 표(엑셀), 서버에 필요한 것(LibreOffice·나눔 글꼴·선택 HEIC)과 없을 때의 오류 |
| [`PILOT_DEPLOYMENT_GUIDE.md`](PILOT_DEPLOYMENT_GUIDE.md) | 파일럿 준비, 실행, 마무리의 점검 목록 (영어) |
| [`DEPLOYMENT_RUNBOOK.md`](DEPLOYMENT_RUNBOOK.md) | 서버에 설치할 것(LibreOffice·나눔 글꼴·선택 HEIC)과 PDF 읽기 시간 권고, 프로세스, 점검(`/healthz`·`/readyz`), 백업·복구, 모니터링 (영어) |
| [`TROUBLESHOOTING_GUIDE.md`](TROUBLESHOOTING_GUIDE.md) | 증상별로 상태를 지키며 복구하는 법 (영어) |
| [`APPROVED_DOCUMENT_OPERATIONS.md`](APPROVED_DOCUMENT_OPERATIONS.md) | 승인 안전 문서 카탈로그 만들기 — **선택**이다. 파일럿 실행기는 카탈로그가 없으면 `source_protocol_only` 로 시작하고, 있으면 점검한다. 단계 안전 카드도 이 카탈로그를 읽는다 (영어) |
| [`PILOT_READINESS_PACKAGE.md`](PILOT_READINESS_PACKAGE.md) | 파일럿 점검표, KPI 정의, 참여자 안내, 중단 기준 (영어, 숫자는 날짜가 적힌 기록) |
| [`VOICE_FIELD_EVALUATION_PLAN.md`](VOICE_FIELD_EVALUATION_PLAN.md) | 현장 음성 평가 계획 (영어, 아직 하지 않음) |

## 3. 설계·기록 — 지금 동작의 기준이 아니다

| 문서 | 무엇인가 | 왜 이 자리에 있나 |
|---|---|---|
| [`ARCHITECTURE_MAP.md`](ARCHITECTURE_MAP.md) | Laboratory Workflow OS 이전의 구조 스냅샷 | `CLAUDE.md` 가 이 경로를 이름으로 가리킨다. 내용은 기록으로만 읽는다. 코드와 다르면 `.agent/architecture.md` 가 맞다 |
| [`PROTOCOL_BOUNDARY_AND_OBLIGATION_DESIGN.md`](PROTOCOL_BOUNDARY_AND_OBLIGATION_DESIGN.md) | 분석의 단락 경계·완전성 의무에 대한 결함 기록과 설계 (스스로 "design only" 라고 적음) | 분석 작업이 다루는 영역의 설계라서 남김 |
| [`APP_PLAN.md`](APP_PLAN.md) | 스토어 앱 계획(줄 APP0): 하이브리드·RN·네이티브 비교, 권한·심사 위험, 결제 규칙과 이용 권한 설계, 오프라인 단계, 앱 뼈대(`mobile/`)와 빌드, 사람이 할 일 | 조사와 뼈대뿐이고 앱은 아직 내지 않았다. 정책·수수료는 적힌 확인일 기준 |
| [`course-archive/`](course-archive/) | 이 저장소가 시작된 강의 자료 | 기록으로만 둔다 |
| [`archive/`](archive/) | 보관한 옛 보고서와 설계 | 읽지 않는다(`CLAUDE.md`). 사람이 지난 결정을 물을 때만 |

## 4. 보관한 문서

`docs/archive/` 의 문서는 **지금 동작의 기준이 아니다.** 2026-10-10 에 보관한 8개는 맨 위에 "보관: 2026-10-08 MVP 이전 설계 —
PI·관리자 승인·반려·회수는 폐기, 리비전·검토자 입력 개념은 줄 RV 에서 다시 설계" 한 줄이 있다.

| 문서 | 보관한 이유 (내용으로 판단) |
|---|---|
| `WEB_WIREFRAMES.md` | 6주 PoC 화면 설계: 승인자료 검색·이상상황 보고·인계 상태 확인 화면. 그 기능은 없다 |
| `LAB_ADMIN_PRODUCT_PLAN.md` | PI·관리자 제품 계획 (승인·역할). 폐기된 개념 |
| `PRODUCT_IMPROVEMENT_PROPOSAL.md` | 옛 procedures 엔진을 전제로 한 개선 제안 |
| `RESTRUCTURE_PLAN.md` | 2026-09-29 에 끝난 이름·위치 정리 계획과 결과 |
| `ANALYSIS_2026_09_11.md` | 2026-09-11 시점의 읽기 전용 조사 |
| `CAPABILITY_MATRIX.md` | 2026-08-24 능력 분류표. 공급자·실제 호출 기록이 지금과 다르다 — 지금 값과 실제 호출 기록은 README §4 |
| `OPERATOR_PROCEDURE_REPEAT_STEP_GATE.md` | "검토자 해소"로 시작하는 2026-09-07 절차서. 검토자 단계가 없다 |
| `MOSS_RETRIEVAL.md` | Moss 재순위 안내. 그 코드를 줄 CL 에서 지웠다 |

## 5. 문서를 고칠 때

- 지금 동작이 바뀌는 줄은 README·`USER_GUIDE.md`·`.agent/` 의 해당 절을 같은 PR 에서 고친다.
- 숫자·성능은 줄 보고서에 있는 것만 쓰고 **측정일**을 적는다. 이 저장소의 테스트는 바깥 공급자를 가짜로만 부르므로(contract-tested),
  "실제로 불러 봤다"는 말은 실제 요청이 이 환경에서 성공했을 때만 쓴다.
- `docs/demo_script.md` 는 사람이 갖고 있는 파일이다(추적하지 않음). 고치거나 올리지 않는다. 시연 흐름은 새 파일(`docs/DEMO_WALKTHROUGH_*.md`)에 쓴다.
