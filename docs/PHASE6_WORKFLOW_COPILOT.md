# Phase 6 — Voice Workflow Copilot

## 목적

Voice Workflow Guide의 Voice Workflow Agent · Lab Pack을 “질문에 답하는 음성 챗봇”이
아니라 “업무를 안내하고, 기록하고, 사람에게 인계하는 코파일럿”으로 만든다.
이 구현의 데모 Procedure와 문서는 모두 `FICTIONAL NON-OPERATIONAL`이며 실제
실험 또는 안전 지침이 아니다.

## 요구사항 추적

| 제안서·AX 요구 | 구현 |
|---|---|
| 승인 SOP 기반 단계 안내 | immutable `ProcedureDefinition`과 출처 카드 |
| Session state | SQLite `procedure_sessions`와 서버 소유 current step |
| Timer | 정의에 고정된 duration, 서버 deadline, 브라우저 countdown |
| Observation | 최종 음성 원문과 값의 완전 일치·type 검증 후 append-only 기록 |
| Report Tool | 초안 read-back 후 JSONL Queue 접수 |
| Workflow handoff | report에 procedure/version/step/observation/timer 연결 |
| Human-in-the-loop | 접수 즉시 `blocked_for_handoff`, 재개 승인 금지 |
| Worker | 별도 모델이 한국어 관리자 인계문 생성 |
| 상태 추적 | WebSocket `report.status.get` 2초 polling |
| Audit | 완료 단계·관찰·타이머·인계 요약 Tool |
| Native live UX | 24 kHz PCM, server VAD, Barge-in, reconnect 유지 |
| Agentic AI | 9개 Tool과 서버 결정적 Gate의 Hybrid 구조 |
| Tool 결과 정합성 | Tool 선택 중 임시 음성을 폐기하고 서버 결과만 확정 응답 |

## 서버 상태 전이

```text
unattached
  └─ start_procedure
       └─ active(step N)
            ├─ record_step_observation
            ├─ start_step_timer
            ├─ complete_current_step
            │    ├─ active(step N+1)
            │    └─ completed
            └─ create_safety_report + user approval
                 └─ blocked_for_handoff
```

`blocked_for_handoff`에서는 단계 완료, 관찰 추가, 타이머 시작을 거부한다.
보고 상태가 `handoff_ready`가 되어도 서버가 자동으로 작업 재개를 승인하지
않는다.

## 단계 완료 Gate

1. 서버가 현재 step ID와 이번 Turn의 명시적 완료 문구를 대조한다.
2. `observation_schema.required=true`이면 적어도 한 개의 검증된 관찰값이
   있어야 한다.
3. timer가 정의된 단계는 서버 timer가 시작되어 deadline이 지나야 한다.
4. 인계가 연결된 세션은 완료 전이를 거부한다.
5. 전이가 성공하면 append-only step event와 session index를 하나의 SQLite
   transaction으로 갱신한다.

텍스트 관찰값에 영문자나 숫자가 포함되면 최종 STT 원문의 완전한 토큰과
일치해야 한다. 예를 들어 원문이 `A-170`일 때 모델이 `A-17`을 전달하면
`observation_evidence_mismatch`로 거부하고 아무 관찰 레코드도 쓰지 않는다.

## 보고 확인과 Native Tool 응답

- 보고 초안은 `awaiting_user_confirmation`이며 아직 접수·차단 상태가 아니다.
- `네, 제출해줘`를 포함한 전체 발화 allow-list만 제출을 승인한다.
- 승인·취소가 아니면 명시적 수정 발화만 초안 재작성 경로로 보낸다.
- 그 외 발화에는 서버 고정 확인 문구를 사용해 미제출 초안을 차단 완료로
  오인하지 않게 한다.
- Native 응답이 Tool 결과 전에 성공을 말하기 시작하면 해당 재생과 자막을
  지우고, Tool 실행 뒤 서버가 만든 성공·거부 문구만 재생한다.

## Canonical UI event

| 이벤트 | 의미 |
|---|---|
| `procedure.started` | 새 ProcedureSession 생성 |
| `procedure.observation_recorded` | 현재 단계 관찰값 기록 |
| `procedure.timer_started` | 서버 고정 타이머 시작 |
| `procedure.step_completed` | 정확히 한 단계 완료 |
| `procedure.blocked_for_handoff` | 보고서와 현재 단계 연결 후 진행 차단 |
| `procedure.completed` | 마지막 단계 완료 |
| `procedure.audit_summary` | 서버 소유 감사 요약 |
| `procedure.state` | 화면이 렌더링할 canonical snapshot |
| `report.status` | Queue·Worker 처리 상태 |

모델의 일반 답변 텍스트는 Procedure 카드를 변경하지 않는다. Native와
Cascade 모두 위 이벤트를 같은 형태로 보낸다.

## 데모 Procedure

ID: `fictional-wet-lab-workflow-demo-ko`

1. 가상 라벨 확인·기록
2. 10초 가상 혼합 타이머
3. 가상 표시창 관찰·판정

정상 경로는 세 단계를 완료하고 감사 요약을 확인한다. 이상 경로는 3단계의
가상 관찰값을 기록한 뒤 보고 초안을 승인하여 현재 워크플로가
`blocked_for_handoff`가 되는 모습을 보여준다.

## 완료 기준

- 필수 관찰값 없이 단계 완료 불가
- 타이머 시작 전 또는 종료 전 단계 완료 불가
- 보고 ID와 current step이 한 레코드로 연결됨
- 보고 승인 직후 Procedure 카드가 진행 차단 상태로 바뀜
- 차단 이후 단계 완료 불가
- Worker 상태가 화면에서 자동 갱신됨
- Native Barge-in·watchdog·reconnect 회귀 없음
- 데스크톱 UI는 세로 기능 메뉴를 없애고 음성 콘솔·기능 요약·Procedure·보고
  상태를 전체 폭 대시보드로 사용하며, 모바일에서 가로 overflow가 없음
- 전체 단위·통합·프런트엔드 테스트와 compile·diff 검사 통과
