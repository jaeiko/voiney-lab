# 문서 지도 — VoineyLab

2026-10-20 (줄 DOC4) 에 새로 썼다. 무엇을 · 누가 · 언제 읽나.

- **코드가 진실이다.** 문서가 코드와 다르면 코드가 맞고 문서를 고친다. 모든 문서(보관 문서 제외)의 링크·설정 이름·저장소 경로·스크립트 옵션은 `tests/test_docs_consistency.py` 가 확인한다.
- 같은 내용은 한 곳에만 쓰고 다른 문서는 그곳을 가리킨다. 줄 이름과 날짜 같은 이력은 `docs/DECISIONS.md` 에만 쓴다.
- 우선순위는 `AGENTS.md` 의 "Documentation authority" 와 같다.

## 1. 처음 보는 사람 (팀원 · 자문 교수 · 심사)

| 문서 | 무엇 | 언제 |
|---|---|---|
| [`README.md`](../README.md) | 무엇인가, 지금 상태, 사용자 흐름, 하지 않는 것, 빠른 시작, 구조 그림, 검증 | 맨 먼저 |
| [`FEATURES.md`](FEATURES.md) | 기능마다 무엇을 하고 코드 어디서 하나(파일·함수·설정·시험·한계), 공급자와 실제 호출 기록 | 기능을 알고 싶을 때 |
| [`DECISIONS.md`](DECISIONS.md) | 주제별 결정: 날짜·결정·이유·관련 줄·코드 위치·남은 질문, 결정을 기다리는 코드 상태 | "왜 이렇게 됐지?" |
| [`GLOSSARY.md`](GLOSSARY.md) | 사용자 말 ↔ 지금 화면 말 ↔ 코드 이름 | 말이 헷갈릴 때 |
| [`../.agent/product_context.md`](../.agent/product_context.md) | 제품·사용자·인터뷰와 설문 근거·성공 지표 | 제품 방향을 볼 때 |
| [`../.agent/roadmap.md`](../.agent/roadmap.md) | 지금 · 다음 · 나중 | 계획을 볼 때 |

## 2. 연구자

| 문서 | 무엇 |
|---|---|
| [`USER_GUIDE.md`](USER_GUIDE.md) | 화면 탭(홈·프로토콜·실험·기록·설정)과 시작 마법사, 말로 하는 법 |

## 3. 코딩 에이전트와 개발자

| 문서 | 무엇 |
|---|---|
| [`AGENTS.md`](../AGENTS.md) | **먼저 읽는다.** 모든 코딩 에이전트의 계약: 불변 규칙, 모듈 지도, 바꿀 때 지킬 것, 검증(조건 A/B), 파일 규칙, 용어, 문서 권위 |
| [`CLAUDE.md`](../CLAUDE.md) | Claude Code 전용 운영(사전 점검, 작업 폴더와 줄, 무거운 실행 잠금, 커밋·PR·병합, 보고서). 첫 줄이 `AGENTS.md` 를 불러온다 |
| [`../.agent/architecture.md`](../.agent/architecture.md) | 지금 구조의 유일한 깊은 설명 |
| [`../.agent/coding_rules.md`](../.agent/coding_rules.md), [`../.agent/security_rules.md`](../.agent/security_rules.md), [`../.agent/testing_strategy.md`](../.agent/testing_strategy.md) | 코드 쓰는 법, 보안·개인정보·안전 규칙, 시험이 증명해야 할 것과 평가 |
| [`GIT_WORKFLOW.md`](GIT_WORKFLOW.md) | 브랜치·커밋·PR·병합, 파일 담당 |
| [`VOICE_AND_TONE.md`](VOICE_AND_TONE.md) | 화면·음성 문구를 쓰는 규칙과 쉬운 말 사전(새 문구에 적용; 지금 문구 바꾸기는 2단계) |
| [`MIGRATION_NOTES.md`](MIGRATION_NOTES.md) | 저장 스키마와 설정 이름이 바뀐 기록 |
| [`APP_PLAN.md`](APP_PLAN.md), [`../mobile/README.md`](../mobile/README.md) | 스토어 앱 계획(조사·정책은 적힌 확인일 기준)과 앱 뼈대 빌드 |

## 4. 운영자와 파일럿 담당

| 문서 | 무엇 |
|---|---|
| [`DEPLOYMENT_RUNBOOK.md`](DEPLOYMENT_RUNBOOK.md) | 서버에 설치할 것, `scripts/run_pilot.sh` 로 띄우기, 기관 안전 문서 카탈로그, 점검, 백업·복구, 지켜볼 것 |
| [`TROUBLESHOOTING_GUIDE.md`](TROUBLESHOOTING_GUIDE.md) | 증상별 안전한 복구 |
| [`PILOT_GUIDE.md`](PILOT_GUIDE.md) | 파일럿을 여는 순서, 데이터 처리, 외부 AI 고지 초안, 지표, 데이터 삭제, 계정과 초대, 스타터 팩 |
| [`PILOT_HTTPS_SETUP.md`](PILOT_HTTPS_SETUP.md) | 휴대폰 마이크를 위한 HTTPS(Caddy) 준비 — 아직 실제 서버에서 실행하지 않음 |
| [`SOURCE_FORMATS.md`](SOURCE_FORMATS.md) | 올릴 수 있는 원문 형식, 서버에 필요한 것, 링크 가져오기, 오류 코드 |
| [`PILOT_DEPLOYMENT_GUIDE.md`](PILOT_DEPLOYMENT_GUIDE.md), [`PILOT_READINESS_PACKAGE.md`](PILOT_READINESS_PACKAGE.md) | 파일럿 점검표·참여자 안내(영어). 문서 다시 쓰기 2단계에서 `PILOT_GUIDE.md` 하나로 합친다 |

## 5. 옮긴 문서

2026-10-20 에 보관한 문서와 내용이 간 곳. 보관본(`docs/archive/`)은 맨 위 한 줄에 같은 안내가 있고, 지금 동작의 기준이 아니다.

| 옛 문서 | 지금도 맞는 내용이 간 곳 |
|---|---|
| [`CURRENT_ARCHITECTURE.md`](archive/CURRENT_ARCHITECTURE.md) | `.agent/architecture.md` 2·6·11절(누가 무엇을 가지나, 저장소, 실패 처리) |
| [`ARCHITECTURE_MAP.md`](archive/ARCHITECTURE_MAP.md) | `.agent/architecture.md` 1·3절(음성 파이프라인), 지운 모듈의 이력은 `docs/DECISIONS.md` |
| [`BEHAVIOR_REFERENCE.md`](archive/BEHAVIOR_REFERENCE.md) | `docs/FEATURES.md`(기능 단위), `docs/DECISIONS.md`(결정·날짜) |
| [`PROTOCOL_BOUNDARY_AND_OBLIGATION_DESIGN.md`](archive/PROTOCOL_BOUNDARY_AND_OBLIGATION_DESIGN.md) | `.agent/architecture.md` 4절 "Page evidence segments and known analysis limits" |
| [`VOICE_FIELD_EVALUATION_PLAN.md`](archive/VOICE_FIELD_EVALUATION_PLAN.md) | `.agent/testing_strategy.md` 5절 |
| [`APPROVED_DOCUMENT_OPERATIONS.md`](archive/APPROVED_DOCUMENT_OPERATIONS.md) | `docs/DEPLOYMENT_RUNBOOK.md` 3절 |
| [`.agent` 의 `evaluation_strategy.md`](archive/AGENT_EVALUATION_STRATEGY.md) | `.agent/testing_strategy.md` 3·4절 |
| [`.agent` 의 `product_improvement_strategy.md`](archive/AGENT_PRODUCT_IMPROVEMENT_STRATEGY.md) | `.agent/roadmap.md` 머리의 우선순위 규칙 |

`docs/APPROVED_DOCUMENT_OPERATIONS.md` 와 `docs/PROTOCOL_BOUNDARY_AND_OBLIGATION_DESIGN.md` 자리에는 짧은 안내만 남겼다 — 코드(`scripts/run_pilot.sh` 의 안내 문장, 두 주석)가 아직 그 경로를 가리킨다. 코드가 새 자리를 가리키면 지운다.

## 6. 기록으로만 두는 것

| 폴더 | 무엇 |
|---|---|
| [`archive/`](archive/) | 보관한 옛 보고서·설계. 읽지 않는다 — 사람이 지난 결정을 물을 때만 |
| [`course-archive/`](course-archive/) | 이 저장소가 시작된 강의 자료. 옛 이름 "Voice Workflow Agent" 그대로 둔다 |

`docs/demo_script.md` 는 사람이 갖고 있는 파일이다(추적하지 않음). 고치거나 올리지 않는다. 시연 흐름은 새 파일(`docs/DEMO_WALKTHROUGH_*.md`)에 쓴다.

## 7. 문서를 고칠 때

- 동작이 바뀌는 PR 은 `docs/FEATURES.md` 와 (설명하고 있다면) `.agent/architecture.md`·`docs/USER_GUIDE.md` 를 같은 PR 에서 고친다. 결정은 `docs/DECISIONS.md` 에 한 줄 더한다.
- 숫자·성능은 보고서에 있는 값만, 측정일과 조건(A/B)을 함께 쓴다. "live-tested"는 실제 외부 요청이 성공했을 때만 쓰고, 가짜 전송 시험은 "contract-tested"라고 쓴다.
- 확인하지 못한 것은 쓰지 않거나 "확인 안 됨"이라고 쓴다.
