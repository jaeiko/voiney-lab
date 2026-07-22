# SafeBridge Lab — Hands-free Voice Copilot

SafeBridge Lab은 신규 Wet-lab 연구자가 장갑을 착용한 채 승인된 안전자료를
찾고, 이상상황을 구조화해 사람에게 인계하도록 돕는 6주 PoC다.

이 디렉터리는 `week4-brain`을 기준으로 기존 Hands-free VAD, WebSocket,
대화 Memory, Tool Calling, 문장 단위 TTS를 보존하면서 공식 M2 Dispatcher의
빠른 Tool → JSONL Queue → 별도 Worker 구조를 통합한다.

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
cd student-work/safebridge-lab
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

`.env`에 자신의 값만 입력한다. `.env`와 API 키는 커밋하지 않는다.

## 실행

세 터미널에서 같은 가상환경을 활성화한다.

```bash
# 터미널 1 — Voice Agent
uvicorn server:app --reload --port 8000

# 터미널 2 — Safety Handoff Worker
python worker.py

# 터미널 3 — Queue 관찰
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
python -m unittest discover -s tests -v
python -m py_compile audio.py brain.py protocol.py server.py tools.py vad.py worker.py
```

검증 범위:

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
