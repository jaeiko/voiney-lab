# Voice Workflow Agent

Voice Workflow Agent는 **Voice Workflow Guide의 Voice Workflow Agent · Lab Pack**이다. 승인된
절차를 음성으로 안내하고, 단계별 관찰·타이머를 기록하며, 이상상황을 현재
단계와 함께 책임자에게 인계하는 voice-first workflow copilot이다.
승인되지 않은 안전 판단이나 작업 재개 승인은 하지 않는다.

이 디렉터리는 `week4-brain`을 기준으로 기존 Hands-free VAD, WebSocket,
대화 Memory, Tool Calling, 문장 단위 TTS를 보존하면서 공식 M2 Dispatcher의
빠른 Tool → JSONL Queue → 별도 Worker 구조를 통합한다.

## Phase 6 Voice Workflow Copilot

Phase 6는 독립적으로 존재하던 Procedure와 안전 보고를 하나의 서버 소유
업무 상태로 연결한다.

```text
승인 절차 시작
  → 현재 단계 안내
  → 사용자 관찰값 기록 또는 고정 타이머 실행
  → 서버 Gate를 통과한 단계 완료
  → 이상상황 보고 초안 재확인
  → 보고 Queue + 현재 단계 연결
  → blocked_for_handoff
  → Worker 인계 상태 자동 갱신
  → 완료·중단 감사 요약
```

- Procedure 상태, 관찰, 타이머, 인계 연결은 별도 SQLite에 보존한다.
- 관찰값은 사용자가 실제 말한 값만 기록하며, 서버 정의의 type과 필수 여부를
  다시 검증한다.
- 타이머 길이는 모델 인자가 아니라 승인된 ProcedureDefinition에 고정된다.
- 필수 관찰이 없거나 타이머가 끝나지 않으면 단계 완료를 거부한다.
- 보고가 접수되면 현재 procedure·version·step·출처·관찰·타이머를 보고에
  연결하고 상태를 `blocked_for_handoff`로 바꾼다.
- 차단된 워크플로는 이후 단계 완료, 추가 관찰, 새 타이머 실행을 거부하며
  작업 재개는 사람 관리자가 결정한다.
- 브라우저는 보고 ID를 2초마다 확인해
  `queued_for_handoff → processing/retry_pending → handoff_ready`를 자동 표시한다.
- Native와 Cascade 경로 모두 같은 서버 상태와 동일한 canonical UI event를
  사용한다.

## Phase 5 Native Speech-to-Speech

브라우저의 기본 음성 처리 방식은 xAI Realtime Native Speech-to-Speech다.
기존 STT → Brain → TTS cascade는 삭제하지 않고 비교·fallback 모드로
보존한다.

```text
브라우저 24 kHz PCM
  → Voice Workflow Agent WebSocket
  → 서버 전용 xAI Realtime WebSocket
  → 응답 ID가 포함된 24 kHz PCM delta
  → 브라우저 재생
```

- API 키는 서버에만 두며 브라우저에 전달하지 않는다.
- `session.updated`가 오기 전에는 마이크 입력을 upstream으로 보내지 않는다.
- 사용자 최종 transcript가 확정되기 전 모델 음성과 Tool call은 서버에서
  보류한다. 긴급 발화, 보고 승인, Procedure 완료 권한은 기존 서버 규칙이
  먼저 판정한다.
- `speech_started`에서 재생을 즉시 중단하고, 중단한 `response_id`의 후속
  audio delta를 폐기하며, 실제 재생 시간만 upstream conversation에서
  truncate한다.
- Tool 결과를 보낸 뒤에는 현재 응답의 브라우저 재생 종료를 확인하고
  후속 응답을 정확히 한 번 생성해 두 음성이 겹치지 않게 한다.
- 비정상 연결 종료는 제한된 backoff와 conversation resumption으로 복구한다.
  사용자가 누른 Stop은 재연결하지 않는다.
- watchdog은 일반적인 무음 상태가 아니라, 발화 종료 후 응답 시작이
  제한시간을 넘긴 경우에만 연결 복구를 시작한다.

`.env`에는 기존 `XAI_API_KEY`와 함께 아래 값을 선택적으로 설정할 수 있다.

이름 변경 전의 `SAFEBRIDGE_*` 애플리케이션 키는 읽지 않는다. 최소한
`VOICE_WORKFLOW_AGENT_SAFETY_CATALOG`과
`VOICE_WORKFLOW_AGENT_USAGE_SCOPE`를 새 이름으로 설정해야 세션을 시작할 수
있다. Catalog와 procedure 경로는 절대 경로여야 하며, 디렉터리 이름 변경
후에는 경로도 `voice-workflow-agent` 위치를 가리키도록 갱신한다.

```dotenv
VOICE_WORKFLOW_AGENT_SAFETY_CATALOG=/absolute/path/to/approved_catalog.sqlite
VOICE_WORKFLOW_AGENT_USAGE_SCOPE=test_only
VOICE_WORKFLOW_AGENT_FACILITY_ID=
VOICE_WORKFLOW_AGENT_SESSION_LANGUAGE=ko
VOICE_WORKFLOW_AGENT_ALLOWED_LANGUAGES=ko,en,vi
VOICE_WORKFLOW_AGENT_PROCEDURE_CATALOG=
VOICE_WORKFLOW_AGENT_PROCEDURE_STORE=

XAI_REALTIME_URL=wss://api.x.ai/v1/realtime
XAI_REALTIME_MODEL=grok-voice-latest
XAI_REALTIME_VOICE=eve
XAI_REALTIME_VAD_THRESHOLD=0.6
XAI_REALTIME_SILENCE_DURATION_MS=1600
NATIVE_VAD_PREFIX_PADDING_MS=333

CASCADE_VAD_MODE=3
CASCADE_VAD_ONSET_VOICED_FRAMES=4
CASCADE_VAD_ONSET_WINDOW_FRAMES=6
CASCADE_VAD_PREFIX_MS=300
CASCADE_VAD_ENDPOINT_SILENCE_MS=1000
CASCADE_VAD_MIN_SPEECH_MS=240
CASCADE_VAD_MAX_UTTERANCE_MS=15000
CASCADE_VAD_COOLDOWN_MS=300
```

| 서버 정책 환경 변수 | 기본값 | 검증 |
|---|---:|---|
| `VOICE_WORKFLOW_AGENT_SAFETY_CATALOG` | 필수 | 비어 있지 않은 절대 경로 |
| `VOICE_WORKFLOW_AGENT_USAGE_SCOPE` | 필수 | `operational`, `demo`, `reference_only`, `test_only` 중 하나 |
| `VOICE_WORKFLOW_AGENT_FACILITY_ID` | 없음 | 선택 문자열 |
| `VOICE_WORKFLOW_AGENT_SESSION_LANGUAGE` | `ko` | `ko`, `en`, `vi` 또는 지원 locale |
| `VOICE_WORKFLOW_AGENT_ALLOWED_LANGUAGES` | `ko,en,vi` | 쉼표로 구분한 지원 언어, 기본 언어 포함 |
| `VOICE_WORKFLOW_AGENT_PROCEDURE_CATALOG` | 없음 | Procedure store와 함께 설정한 절대 경로 |
| `VOICE_WORKFLOW_AGENT_PROCEDURE_STORE` | 없음 | Procedure catalog와 함께 설정한 절대 경로 |

| 환경 변수 | 기본값 | 적용 경로 |
|---|---:|---|
| `CASCADE_VAD_MODE` | `3` | Cascade WebRTC VAD mode |
| `CASCADE_VAD_ONSET_VOICED_FRAMES` | `4` | Cascade onset voiced frames |
| `CASCADE_VAD_ONSET_WINDOW_FRAMES` | `6` | Cascade onset window frames |
| `CASCADE_VAD_PREFIX_MS` | `300` | Cascade prefix audio |
| `CASCADE_VAD_ENDPOINT_SILENCE_MS` | `1000` | Cascade endpoint silence |
| `CASCADE_VAD_MIN_SPEECH_MS` | `240` | Cascade minimum voiced speech |
| `CASCADE_VAD_MAX_UTTERANCE_MS` | `15000` | Cascade maximum utterance |
| `CASCADE_VAD_COOLDOWN_MS` | `300` | Cascade post-playback cooldown |
| `XAI_REALTIME_VAD_THRESHOLD` | `0.6` | Native server VAD threshold |
| `XAI_REALTIME_SILENCE_DURATION_MS` | `1600` | Native end-of-speech silence; integer `500`~`3000` ms |
| `NATIVE_VAD_PREFIX_PADDING_MS` | `333` | Native server VAD prefix |

`XAI_REALTIME_VAD_THRESHOLD`는 기존 xAI server VAD 설정 이름을 그대로
재사용한다. 허용 범위는 `0.1`~`0.9`다. Cascade의 밀리초 값은 설정한
시간보다 짧아지지 않도록 20 ms frame 단위로 올림 변환한다.
Native 화면은 브라우저 음성 신호 감지와 서버 전송 시작을 별도로 표시해
마이크 캡처 문제와 upstream VAD 문제를 구분한다.

브라우저 상단의 음성 처리 방식에서 `Cascade 비교 모드`를 선택하면 기존
16 kHz cascade 경로를 그대로 시험할 수 있다.

## 전체 동작

```text
연구자 음성 ─┬─ Native Realtime S2S
             └─ STT → Voice Agent + Tool Loop → TTS
                         │
                         ├─ SQLite 승인 Gate → Moss 인메모리 검색(선택)
                         ├─ Procedure·관찰·타이머 → procedure_sessions.sqlite
                         └─ 안전 보고 + 현재 단계 → reports/inbox.jsonl
                                                        │
reports/inbox.jsonl → 별도 worker.py → 한국어 관리자 인계문 → outbox/*.eml
                                      └→ status/*.json ─→ 브라우저 자동 갱신
```

Voice Tool은 짧은 파일 기록만 수행한다. Worker 전용 Grok Prompt로 한국어
관리자 인계문을 만드는 느린 작업은 별도 프로세스에서 처리한다.

## Tool

| Tool | 역할 |
|---|---|
| `search_approved_safety_manual` | SQLite 승인 Gate를 통과한 자료 검색 및 선택적 Moss 인메모리 순위화 |
| `create_safety_report` | 위치·상황·긴급도·노출 여부를 검증하고 Queue에 기록 |
| `check_safety_report_status` | Queue·재시도·인계문 준비 상태 확인 |
| `start_procedure` | 서버가 검증한 Procedure 시작 |
| `get_current_step` | 현재 승인 단계와 출처 확인 |
| `record_step_observation` | 현재 단계에 사용자 관찰값 기록 |
| `start_step_timer` | 현재 단계에 정의된 고정 타이머 시작 |
| `complete_current_step` | 명시적 완료 확인과 서버 Gate 통과 후 한 단계 전이 |
| `get_workflow_summary` | 완료 단계·관찰·타이머·인계 감사 요약 |

Voice Agent는 최대 네 번의 Tool Round를 수행할 수 있다. 따라서 승인자료를
찾은 뒤 같은 Turn에서 안전 보고를 접수하는 Tool Chaining도 가능하다.

## 설치

```bash
cd voice-workflow-agent
source .venv/bin/activate
python -m pip install -e .
```

`.env`에 자신의 값만 입력한다. `.env`와 API 키는 커밋하지 않는다.

Moss 검색을 사용할 때만 선택 의존성을 추가한다.

```bash
python -m pip install -e '.[moss]'
```

Moss는 서버 시작 시 승인 인덱스를 메모리에 올리고, 기존 SQLite가 승인한
후보 ID 안에서만 hybrid semantic/keyword 순위를 정한다. 자격 증명 누락,
인덱스 로드 실패, timeout, 알 수 없는 결과 ID가 발생하면 기존 SQLite
순서로 자동 복귀한다. 인덱스 동기화와 기밀자료 경계는
[`docs/MOSS_RETRIEVAL.md`](docs/MOSS_RETRIEVAL.md)를 따른다.

## 실행

Voice Agent:

```bash
cd voice-workflow-agent
source .venv/bin/activate
uvicorn voice_workflow_agent.server:app --reload
```

Safety Handoff Worker:

```bash
cd voice-workflow-agent
source .venv/bin/activate
python -m voice_workflow_agent.worker
```

Queue 관찰:

```bash
cd voice-workflow-agent
tail -f reports/inbox.jsonl
```

브라우저에서 `http://localhost:8000`을 열고 `세션 시작`을 한 번 누른다.
원격 VM에서는 기존 SSH Port Forwarding을 유지한다.

## Phase 6 실음성 검증 순서

영상 촬영과 무관하게 아래 순서로 현재 라이브 동작을 검증한다. 모든 데이터와
화면 동작은 `FICTIONAL NON-OPERATIONAL`이다.

### 정상 워크플로

1. “가상 샘플 점검 워크플로를 시작해 줘.”
2. Step 1에서 “가상 라벨은 A-170이야.”라고 말하고, 화면과 음성 응답에
   `A-170`이 글자·숫자 그대로 기록되는지 확인한다. `A-17`처럼 축약된 Tool
   인자는 서버가 거부해야 한다.
3. “현재 단계를 완료했습니다.”라고 말해 Step 2로 이동한다.
4. “고정 타이머를 시작해 줘.”라고 말하고 화면의 10초 countdown을 확인한다.
5. 타이머가 끝나기 전 완료를 요청해 서버가 거부하는 것을 보여준 뒤,
   종료 후 같은 완료 문구로 Step 3에 진입한다.

### 이상상황 인계

1. Step 3에서 “가상 표시창은 빨간색이야.”라고 관찰값을 기록한다.
2. “제3 실험실 B 작업대의 가상 혼합 장치에서 표시창이 빨간색이고 이상한
   소리가 나. 노출된 사람은 없고 긴급한 관리자 확인이 필요해. 보고서 초안을
   만들어 줘.”라고 위치·상황·긴급도·노출 상태를 한 번에 말한다.
3. Agent가 읽어 준 초안을 확인하고 “네, 제출해줘.” 또는 “보고서를 제출해
   주세요.”라고 승인한다.
4. Procedure 카드가 `관리자 인계 대기 · 진행 차단`으로 바뀌고 보고 ID,
   현재 step, 출처, 관찰값이 연결되는지 확인한다.
5. “현재 단계를 완료했습니다.”라고 말해 차단 이후 진행이 거부되는지 확인한다.
6. Worker를 실행해 보고 카드가 자동으로 `관리자 인계문 준비 완료`로
   바뀌는지 확인한다.
7. 긴 Agent 답변 도중 “잠깐, 핵심만 말해 줘.”라고 끼어들어 Native
   Barge-in을 함께 보여준다.

초안이 화면에 `사용자 제출 확인 대기`로 남아 있는 동안은 아직 보고 접수나
워크플로 차단이 아니다. 승인·취소·명시적 수정이 아닌 발화에는 서버가 다시
제출 또는 취소를 요청하며, 모델이 차단 완료를 임의로 주장하지 않는다.

### 긴급 보고

1. “3층 유기화학실 후드 앞에서 아세톤으로 보이는 용액이 새고 있어.
   노출 여부는 모르겠고 긴급하게 확인이 필요해.”
2. Agent가 누락 정보를 확인한 뒤 보고 ID를 말하는지 확인한다.
3. Queue 한 줄, `.eml`, `processed.txt`, `status/<id>.json`을 확인한다.
4. “방금 보고가 관리자에게 인계됐어?”라고 물어 상태 Tool을 확인한다.

### 일반 보고

1. “분석실 원심분리기 덮개가 느슨해 보였지만 지금 사용하지 않고 있어.
   노출은 없고 일반 보고로 남겨줘.”
2. 새 보고가 Queue에 한 번만 들어가는지 확인한다.
3. 같은 문장을 바로 반복했을 때 60초 중복 방지가 동작하는지 확인한다.

## 자동 검증

테스트는 STT, Grok, TTS 네트워크 호출을 만들지 않는다.

```bash
cd voice-workflow-agent
source .venv/bin/activate
python -m unittest discover -s tests -v
python -m compileall -q src tests
```

검증 범위:

- Native session 설정, 24 kHz PCM, 응답 ID 기반 audio correlation
- 최종 transcript 이전 assistant audio·Tool call preflight 차단
- barge-in 재생 중단, stale response 폐기, 안전한 playback truncation
- Tool exactly-once 실행과 다중 Tool 이후 단일 continuation
- 연결 재개, 제한된 reconnect buffer, 발화 기반 watchdog, Stop 취소
- PCM, FrameBuffer, WebRTC VAD, endpoint, cooldown 회귀
- ConversationHistory와 Tool-call 메시지 순서
- 다중 Tool Round와 선택-pass 텍스트 비노출
- 아홉 Tool의 엄격한 JSON Schema·인자 검증
- JSONL Queue와 60초 중복 방지
- Procedure 관찰·타이머 Gate와 append-only 감사 기록
- Procedure–Report 연결, `blocked_for_handoff`, 차단 이후 전이 거부
- Native·Cascade의 동일 workflow event와 브라우저 countdown
- 보고 상태 WebSocket polling과 Worker handoff 자동 갱신
- Worker 우선순위, `.eml`, 성공 Ledger
- 최대 3회 재시도와 실패 상태
- 웹 세션 재시작, stale event 격리, Tool·보고 상태 표시

## 설계 문서

- [`docs/PHASE6_WORKFLOW_COPILOT.md`](docs/PHASE6_WORKFLOW_COPILOT.md)
- [`docs/MOSS_RETRIEVAL.md`](docs/MOSS_RETRIEVAL.md)
- [`docs/WEB_WIREFRAMES.md`](docs/WEB_WIREFRAMES.md)
- [`docs/M2_DISPATCHER_PLAN.md`](docs/M2_DISPATCHER_PLAN.md)

## 안전 경계와 제한사항

- 데모 안전자료는 공식 규정이 아니며 실제 연구실 승인문서로 교체해야 한다.
- Agent는 작업 재개나 안전 판정을 승인하지 않는다.
- 즉시 위험 시 기존 비상 연락·대피 절차가 우선이다.
- 실제 SMTP, 인증, 권한 관리, 암호화 저장, 관리자 Dashboard는 구현하지 않았다.
- Moss 인덱스 생성·갱신은 선택한 section text를 Moss Cloud로 업로드하므로
  조직이 외부 서비스 사용을 승인한 비기밀 자료에만 사용한다.
- Worker가 만든 `.eml`은 검토용 Outbox 산출물이며 자동 전송하지 않는다.
- 실제 마이크, 연구실 소음, 시약명·숫자·단위 STT는 별도 현장 검증이 필요하다.
## Fictional Workflow Copilot demo

This demo is a test-only, fictional, non-operational sample-inspection workflow.
It is not safety guidance and must not be used for real work. Generate fresh
databases only in a temporary directory:

```bash
demo_dir=$(mktemp -d)
./.venv/bin/python scripts/setup_procedure_demo.py --output-dir "$demo_dir"

export VOICE_WORKFLOW_AGENT_SAFETY_CATALOG="$demo_dir/approved_catalog.sqlite"
export VOICE_WORKFLOW_AGENT_PROCEDURE_CATALOG="$PWD/data/procedure_demo/procedures.ko.json"
export VOICE_WORKFLOW_AGENT_PROCEDURE_STORE="$demo_dir/procedure_sessions.sqlite"
export VOICE_WORKFLOW_AGENT_FACILITY_ID="DEMO-FACILITY"
export VOICE_WORKFLOW_AGENT_USAGE_SCOPE="test_only"
export VOICE_WORKFLOW_AGENT_SESSION_LANGUAGE="ko"
export VOICE_WORKFLOW_AGENT_ALLOWED_LANGUAGES="ko"
```

These shell exports override equivalent `.env` values for that one demo process.
The procedure ID is `fictional-wet-lab-workflow-demo-ko`. Never place the generated
SQLite files in tracked source or the existing runtime directories.
