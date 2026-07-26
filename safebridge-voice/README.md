# SafeBridge Voice

SafeBridge Voice는 승인된 안전 절차를 검색하고, 구조화된 사고 보고서를 작성해 책임자에게 인계하며, 감사 가능한 기록을 보존하는 voice-first safety dispatcher다. 승인되지 않은 안전 판단이나 작업 재개 승인은 하지 않는다.

이 디렉터리는 `week4-brain`을 기준으로 기존 Hands-free VAD, WebSocket,
대화 Memory, Tool Calling, 문장 단위 TTS를 보존하면서 공식 M2 Dispatcher의
빠른 Tool → JSONL Queue → 별도 Worker 구조를 통합한다.

## Phase 5 Native Speech-to-Speech

브라우저의 기본 음성 처리 방식은 xAI Realtime Native Speech-to-Speech다.
기존 STT → Brain → TTS cascade는 삭제하지 않고 비교·fallback 모드로
보존한다.

```text
브라우저 24 kHz PCM
  → SafeBridge WebSocket
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

```dotenv
XAI_REALTIME_URL=wss://api.x.ai/v1/realtime
XAI_REALTIME_MODEL=grok-voice-latest
XAI_REALTIME_VOICE=eve
XAI_REALTIME_VAD_THRESHOLD=0.6
```

`XAI_REALTIME_VAD_THRESHOLD`는 xAI server VAD의 발화 시작 감도다.
브라우저 마이크와 일반 실내 음성을 기준으로 기본값을 `0.6`으로 두며,
허용 범위는 `0.1`~`0.9`다. 값이 높을수록 더 큰 음성이 필요하다.
Native 화면은 브라우저 음성 신호 감지와 서버 전송 시작을 별도로 표시해
마이크 캡처 문제와 upstream VAD 문제를 구분한다.

브라우저 상단의 음성 처리 방식에서 `Cascade 비교 모드`를 선택하면 기존
16 kHz cascade 경로를 그대로 시험할 수 있다.

## M2 동작

```text
연구자 음성 → STT → Voice Agent + Tool Loop → TTS
                         │
                         ├─ search_approved_safety_manual
                         ├─ create_safety_report → reports/inbox.jsonl
                         └─ check_safety_report_status
                                                    ▲
reports/inbox.jsonl → 별도 worker.py → Grok → outbox/*.eml
                                      └→ reports/processed.txt + status/*.json
```

Voice Tool은 짧은 파일 기록만 수행한다. Worker 전용 Grok Prompt로 한국어
관리자 인계문을 만드는 느린 작업은 별도 프로세스에서 처리한다.

## Tool

| Tool | 역할 |
|---|---|
| `search_approved_safety_manual` | 승인된 로컬 데모 자료 검색 |
| `create_safety_report` | 위치·상황·긴급도·노출 여부를 검증하고 Queue에 기록 |
| `check_safety_report_status` | Queue·재시도·인계문 준비 상태 확인 |

Voice Agent는 최대 네 번의 Tool Round를 수행할 수 있다. 따라서 승인자료를
찾은 뒤 같은 Turn에서 안전 보고를 접수하는 Tool Chaining도 가능하다.

## 설치

```bash
cd safebridge-voice
source .venv/bin/activate
python -m pip install -e .
```

`.env`에 자신의 값만 입력한다. `.env`와 API 키는 커밋하지 않는다.

## 실행

Voice Agent:

```bash
cd safebridge-voice
source .venv/bin/activate
uvicorn safebridge_voice.server:app --reload
```

Safety Handoff Worker:

```bash
cd safebridge-voice
source .venv/bin/activate
python -m safebridge_voice.worker
```

Queue 관찰:

```bash
cd safebridge-voice
tail -f reports/inbox.jsonl
```

브라우저에서 `http://localhost:8000`을 열고 `세션 시작`을 한 번 누른다.
원격 VM에서는 기존 SSH Port Forwarding을 유지한다.

## M2 수동 데모

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
cd safebridge-voice
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
- 세 Tool의 엄격한 JSON Schema·인자 검증
- JSONL Queue와 60초 중복 방지
- Worker 우선순위, `.eml`, 성공 Ledger
- 최대 3회 재시도와 실패 상태
- 웹 세션 재시작, stale event 격리, Tool·보고 상태 표시

## 설계 문서

- [`docs/WEB_WIREFRAMES.md`](docs/WEB_WIREFRAMES.md)
- [`docs/M2_DISPATCHER_PLAN.md`](docs/M2_DISPATCHER_PLAN.md)

## 안전 경계와 제한사항

- 데모 안전자료는 공식 규정이 아니며 실제 연구실 승인문서로 교체해야 한다.
- Agent는 작업 재개나 안전 판정을 승인하지 않는다.
- 즉시 위험 시 기존 비상 연락·대피 절차가 우선이다.
- 실제 SMTP, 인증, 권한 관리, 암호화 저장, 관리자 Dashboard는 구현하지 않았다.
- Worker가 만든 `.eml`은 검토용 Outbox 산출물이며 자동 전송하지 않는다.
- 실제 마이크, 연구실 소음, 시약명·숫자·단위 STT는 별도 현장 검증이 필요하다.
# Fictional ProcedureSession demo

This demo is a test-only, fictional, non-operational color-card workflow. It is
not safety guidance and must not be used for real work. Generate fresh databases
only in a temporary directory:

```bash
demo_dir=$(mktemp -d)
./.venv/bin/python scripts/setup_procedure_demo.py --output-dir "$demo_dir"

export SAFEBRIDGE_SAFETY_CATALOG="$demo_dir/approved_catalog.sqlite"
export SAFEBRIDGE_PROCEDURE_CATALOG="$PWD/data/procedure_demo/procedures.ko.json"
export SAFEBRIDGE_PROCEDURE_STORE="$demo_dir/procedure_sessions.sqlite"
export SAFEBRIDGE_FACILITY_ID="DEMO-FACILITY"
export SAFEBRIDGE_USAGE_SCOPE="test_only"
export SAFEBRIDGE_SESSION_LANGUAGE="ko"
export SAFEBRIDGE_ALLOWED_LANGUAGES="ko"
```

These shell exports override equivalent `.env` values for that one demo process.
The procedure ID is `fictional-color-card-demo-ko`. Never place the generated
SQLite files in tracked source or the existing runtime directories.
