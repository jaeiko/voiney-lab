# VoineyLab

[![CI](https://github.com/jaeiko/voiney-lab/actions/workflows/ci.yml/badge.svg)](https://github.com/jaeiko/voiney-lab/actions/workflows/ci.yml)

> **VoineyLab** (by Voiney) is a hands-free voice guide that lets wet-lab researchers run their own uploaded protocol step by step.
> It analyses the protocol, checks every value against the source, asks only what it must, then guides, times and records the run and writes a lab notebook.
> The server owns all workflow state and models only propose. Coding agents: read [`AGENTS.md`](AGENTS.md) first.

## 1. 무엇인가 · 누구를 위한가

VoineyLab 은 화학·바이오 웻랩 연구자가 **장갑을 낀 채 손을 쓰지 않고** 자기 연구실의 프로토콜을 따라 실험하도록 돕는 음성 실험 가이드다. 만든 곳은 Voiney, 저장소는 [`jaeiko/voiney-lab`](https://github.com/jaeiko/voiney-lab), 파이썬 패키지는 `voiney_lab` 이다.

- 쓰는 사람: 벤치에서 실험하는 연구자(첫 파일럿은 석사생 두 명, 2026년 10월 말부터). 연구실 담당은 계정과 데이터를, PI 는 노트에 "확인함"을 남긴다.
- 근거: 생명 분야 연구자 3명 현장 인터뷰(2026-10-07·08), 13명 설문(2026-10-19) — `.agent/product_context.md`.

## 2. 사용자 흐름

1. **프로토콜 올리기** — PDF·Word·사진(서버가 PDF 로 바꾼다), 또는 화면의 **공개 링크로 가져오기**로 Google Drive 파일·Docs 주소를 넣는다. Sheets 링크는 시약 표로 붙이는 서버 API만 있다. 엑셀은 시약 표로 붙는다.
2. **분석과 원문 근거 검사** — 글자를 읽을 수 없는 쪽은 자동 OCR, 분석 모델이 단계·값·안전 주의를 뽑고, 서버가 값마다 원문과 맞대어 본다.
3. **확인 카드** — 원문에서 확인하지 못한 것만 카드 한 장씩 묻는다. 다 확인된 분석은 카드가 없다. 안전 주의 글은 고칠 수 없다.
4. **실험 시작** — 시작 마법사(내 목소리 → 안내 목소리 → 프로토콜 → 핵심 값 2문항 → 안전 주의 → **이 프로토콜로 시작**). 승인 단계는 없다.
5. **단계별 음성 안내** — 단계 읽기, 타이머와 알림, 질문에 원문으로 답하기, "그거" 되묻기, 말로 기록하기, 실험 노트 질문. 단계·완료·타이머는 서버만 바꾼다.
6. **실험 노트·보고서** — 지우지 않는 기록에서 실험 노트 모양의 보고서(Word·Markdown·JSON·CSV)를 만든다. 확인하지 않은 값은 "[미확인]".

기능마다 자세한 동작은 [`docs/FEATURES.md`](docs/FEATURES.md), 화면 쓰는 법은 [`docs/USER_GUIDE.md`](docs/USER_GUIDE.md).

## 3. 하지 않는 것

- ELN·LIMS 를 대신하지 않는다. 실험 기록의 공식 저장소가 아니다.
- 승인하지 않는다: PI·관리자 승인, 리비전 승인·반려·회수가 없다. 관리자의 "확인함"은 기록일 뿐 시작을 막지 않는다.
- 값과 안전 주의를 새로 만들지 않는다. 원문에 없는 용어 뜻은 짧은 일반 설명을 "AI 일반 지식"으로 표시해서만 붙인다.
- 안전 판단, 작업 재개 허가, 장비 제어, 자율 실험을 하지 않는다. 응급 말에는 정해진 문장으로 멈추고 연락하라고만 한다.
- 규제 준수(GLP/GMP, 21 CFR Part 11, HIPAA, ISO)나 완전한 외부 연동을 주장하지 않는다.

## 4. 빠른 시작

Python 3.12 이상.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e '.[test]'
cp .env.example .env      # 키를 채운다. .env 는 커밋하지 않는다
```

- `.env` 에 모든 모델 역할과 STT·TTS 공급자를 적는다(비어 있으면 그 기능이 설정 이름을 적은 오류로 닫힌다). 지금 쓰는 값은 `docs/FEATURES.md` F17, 설정 이름 전체는 `src/voiney_lab/setting_names.py`.
- 서버에는 LibreOffice 와 한국어 글꼴이 필요하다(Word 원문, 보고서 PDF): `sudo apt-get install --no-install-recommends libreoffice-writer-nogui fonts-nanum`. HEIC 사진은 선택(`pip install -e '.[heic]'`). 자세히는 `docs/DEPLOYMENT_RUNBOOK.md` 1절.

### 4-1. 개발 서버

```bash
./scripts/run_dev.sh              # 127.0.0.1:8000
./scripts/run_dev.sh --check-only # 설정만 보여 주고 data/runtime 은 건드리지 않는다
```

- 외부 라이선스라 커밋하지 않은 in-gel 원본 PDF(`data/runtime/candidate-a-source/in-gel-digestion.pdf`)가 있으면 개발용 프로토콜을 SHA-256 으로 확인하고 넣는다. 없으면 `[WARN]` 을 찍고 빈 목록으로 시작한다.
- 상태는 `data/runtime/candidate-a-live-acceptance/` 에 둔다. 안전 문서 범위(`VOINEY_LAB_USAGE_SCOPE`)와 카탈로그(`VOINEY_LAB_SAFETY_CATALOG`)는 `.env` 에서 읽는다 — 기관 문서 없이 쓰려면 `VOINEY_LAB_USAGE_SCOPE=source_protocol_only`.

### 4-2. 계정 모드와 초대 링크

- 기본(`VOINEY_LAB_AUTH_MODE=dev`)은 로그인 없이 개발 신원 한 사람이다. `accounts` 로 두면 모든 화면과 API 가 로그인을 요구하고, 가입은 초대 링크로만 된다(작업공간이 켜져 있어야 한다).
- 초대 링크는 서버에서 만든다:
  ```bash
  python scripts/pilot_invite.py --admin --base-url https://<서버 주소>   # 관리자 한 명
  python scripts/pilot_invite.py --base-url https://<서버 주소>           # 참여자
  python scripts/pilot_invite.py --list                                    # 열린 초대
  ```
  링크는 한 번만 쓰이고 기본 72시간 뒤 만료된다. 계정 끄기·켜기는 `--disable NAME`·`--enable NAME`. 개발 서버의 작업공간을 쓰려면 `--data-dir data/runtime/candidate-a-live-acceptance/workspace`.

### 4-3. 파일럿 실행기

```bash
./scripts/run_pilot.sh --check-only
./scripts/run_pilot.sh              # 127.0.0.1:8080, 로그인(accounts) 켜짐
```

- 상태는 `data/runtime/pilot/` 에 둔다. 기관 안전 카탈로그 파일이 없으면 `source_protocol_only`(원문 안전 주의만)로, 있으면 점검해서 `reference_only` 로 연다.
- 휴대폰 마이크에는 HTTPS 가 필요하다: `docs/PILOT_HTTPS_SETUP.md`. 파일럿을 여는 순서·고지·데이터 삭제는 `docs/PILOT_GUIDE.md`, 서버 운영은 `docs/DEPLOYMENT_RUNBOOK.md`.

## 5. 구조 한눈에

```text
 연구자 (장갑, 휴대폰·태블릿·PC 브라우저 / 스토어 앱 껍데기)
   │ 마이크 소리 16 kHz            ▲ 음성 안내 · 화면 이벤트
   ▼                               │
 ┌──────────────── server.py (FastAPI, 프로세스 하나) ────────────────┐
 │ /ws 음성: 듣기(VAD·목소리 크기) → STT → 응급 → 앞단 규칙            │
 │            → (켰을 때) LLM 라우터 = 제안만 → 서버 검증              │
 │            → CuratedProtocolSession  ← 상태를 바꾸는 유일한 곳     │
 │            → TTS                                                    │
 │ HTTP: 올리기·링크 → OCR → 분석 → 원문 근거 검사 → 확인 카드         │
 │       → 시작 판정 · 기록 · 보고서 · 계정 · 지표                     │
 └──────────────────────────────┬──────────────────────────────────────┘
                                │ SQLite + 원본 파일 (data/runtime/…)
     프로토콜 장부 · 작업공간(실험·관찰) · 실험 기록 · 계정 · 파일럿 지표
 외부 AI(설정한 공급자): 분석·번역·보고서, 라우터·답변, STT·TTS, OCR
```

깊은 설명은 [`.agent/architecture.md`](.agent/architecture.md).

## 6. 문서 지도

**코딩 에이전트(Codex, Claude Code 등)는 [`AGENTS.md`](AGENTS.md) 를 먼저 읽는다.** 규칙은 모두 거기 있다. [`CLAUDE.md`](CLAUDE.md) 는 Claude Code 가 그 파일을 불러오게 하는 한 줄(`@AGENTS.md`)뿐이다.

| 알고 싶은 것 | 문서 |
|---|---|
| 문서 전체 목록, 누가 언제 읽나 | [`docs/DOCUMENTATION_INDEX.md`](docs/DOCUMENTATION_INDEX.md) |
| 화면 쓰는 법(연구자) | [`docs/USER_GUIDE.md`](docs/USER_GUIDE.md) |
| 기능마다 코드가 하는 일 | [`docs/FEATURES.md`](docs/FEATURES.md) |
| 왜 그렇게 정했나(날짜·줄) | [`docs/DECISIONS.md`](docs/DECISIONS.md) |
| 같은 것을 부르는 말들 | [`docs/GLOSSARY.md`](docs/GLOSSARY.md) |
| 구조 | [`.agent/architecture.md`](.agent/architecture.md) |
| 제품·사용자·근거, 다음 계획 | [`.agent/product_context.md`](.agent/product_context.md), [`.agent/roadmap.md`](.agent/roadmap.md) |
| 서버 운영, 문제 해결, 파일럿 | [`docs/DEPLOYMENT_RUNBOOK.md`](docs/DEPLOYMENT_RUNBOOK.md), [`docs/TROUBLESHOOTING_GUIDE.md`](docs/TROUBLESHOOTING_GUIDE.md), [`docs/PILOT_GUIDE.md`](docs/PILOT_GUIDE.md) |
| 브랜치·PR 규칙 | [`docs/GIT_WORKFLOW.md`](docs/GIT_WORKFLOW.md) |

## 7. 검증

```bash
python scripts/replay_turns.py
VOINEY_LAB_MOSS_ENABLED=false \
VOINEY_LAB_WORKSPACE_ENABLED=false \
VOINEY_LAB_EXPERIMENT_REPORTS_ENABLED=false \
VOINEY_LAB_LLM_ROUTER_ENABLED=false \
python -m pytest -q
python -m compileall -q src tests scripts
git diff --check
```

통과 수는 외부 라이선스 PDF 가 있느냐에 따라 다르다. **숫자를 말할 때는 조건을 함께 말한다**(조건의 뜻은 `AGENTS.md` "Verification").

| 조건 | 결과 | 언제·어디서 |
|---|---|---|
| A 근처 — in-gel PDF 하나만 있는 작업 폴더 | 4223 passed · 16 skipped · 실패 0 | 2026-10-20, dev `e7c31c8`(줄 DOC4 측정). 라이선스 PDF 가 더 있는 원래 폴더는 몇 개 더 돈다 |
| B — 라이선스 PDF 없음(CI) | 3581 passed · 506 skipped · 실패 0 | 2026-10-19, 줄 PK 의 PR 머리 CI(줄 PK 보고서) |
| 화면 시험(Playwright, CI) | 108 passed | 2026-10-19, 줄 IA 의 CI |

시험은 바깥 공급자에 닿지 않는다: 키를 지우고 이 컴퓨터 밖으로의 연결을 막는다(`tests/conftest.py`). 공급자 시험은 모두 가짜 전송이다(contract-tested).

## 8. 라이선스 메모

- PDF 엔진 PyMuPDF 는 AGPL-3.0 과 상용 라이선스 중 하나로 쓰고, `src/voiney_lab/pdf_text_engine.py` 한 곳에서만 쓴다. 상용화 전에 상용 라이선스를 사거나 그 모듈 뒤에서 엔진을 바꾼다. 결정 기록(영어 원문): *PyMuPDF is used under its AGPL-3.0 / commercial dual licence, only through that one module; before commercialisation either a commercial PyMuPDF licence is bought or the engine is replaced behind that one module.*
- HEIC 지원(pillow-heif)은 바퀴에 GPLv2 코드가 들어 있어 선택 설치다.
- in-gel 개발 프로토콜의 원본 PDF 는 외부 라이선스라 저장소에 없다. 스타터 팩 목록(`scripts/pilot_starter_pack.csv`)은 CC BY·CC0·퍼블릭 도메인 문서만 받는다.
- 이 저장소는 강의 저장소 [`jaeiko/voice-ai-course`](https://github.com/jaeiko/voice-ai-course) 에서 시작했다. 옛 이름 "Voice Workflow Agent" 는 코드 식별자와 보관 문서에만 남아 있다.
