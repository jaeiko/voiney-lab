# VoineyLab

[![CI](https://github.com/jaeiko/voiney-lab/actions/workflows/ci.yml/badge.svg)](https://github.com/jaeiko/voiney-lab/actions/workflows/ci.yml)

**VoineyLab — 우리 랩이 올린 프로토콜 그대로, 시간은 대신 재고, 순서는 지켜 주고, 말한 것은 노트로 남기는 AI · MVP 시제품, 현장 미검증**

- 만든 곳: Voiney. 저장소: [`jaeiko/voiney-lab`](https://github.com/jaeiko/voiney-lab). 파이썬 패키지 이름은 `voiney_lab` 이다.
  코드와 옛 문서의 "Voice Workflow Agent" 는 같은 제품의 옛 이름이다.
- 상태: **MVP 시제품 — 현장 미검증.** 사람이 말로 해 본 시험은 개발 서버에서 in-gel 프로토콜로 했다(마지막 2026-10-10).
  실제 연구실(소음·장갑·여러 사람)에서 잰 숫자는 아직 없다. 검증된 GLP/GMP/임상 시스템이 아니다.
- 이 저장소는 강의 저장소 [`jaeiko/voice-ai-course`](https://github.com/jaeiko/voice-ai-course) 에서 시작했다. 그 저장소는 기록으로만 남아 있다.

## 1. 지금 흐름

아래는 지금 코드가 하는 일이다. 아직 없는 것은 [9. 다음 계획](#9-다음-계획-아직-없는-것)에 따로 적었다.

1. **업로드** — 실험자가 프로토콜 PDF 를 올린다. 원본 바이트와 SHA-256 을 그대로 보관한다. 같은 파일은 같은 프로토콜로 묶인다.
2. **글자 층 없는 쪽 자동 OCR** — 스캔처럼 글자 층이 없는 쪽만 OCR 한다(NAVER CLOVA OCR, Google Cloud Vision 중 설정한 엔진). 결과는 자동으로 받아들이고 그 사실을 기록한다.
3. **분석** — 분석 역할 모델이 단계·양·시간·온도·안전 주의를 구조로 뽑는다. 응답이 출력 한도에서 잘렸거나 구조가 깨졌을 때만 한 번 저절로 다시 시도한다. 그 밖의 실패는 사람이 "분석 다시 시도"를 누른다.
4. **원문 근거 검증** — 뽑은 값마다 인용한 쪽의 원문 글자와 맞대어 본다. 맞지 않으면 시작을 막는다.
5. **시작 전 확인** — 단계 수, 원문 안전 주의(원문과 한국어를 나란히), 시작을 막는 사유와 할 일, 시작 전 알림을 보여 준다. 실험자가 "이 프로토콜로 시작"을 누르는 것이 유일한 사람 확인이다. 승인 단계는 없다(2026-10-08 결정).
6. **음성 안내·타이머 알림·질문·그림**
   - 말 한 번은 한 줄로 처리한다: 앞단 규칙 → (켜면) LLM 라우터 → 서버 검증. 단계 넘기기·완료·타이머 같은 상태는 서버만 바꾼다.
   - 타이머: 1분 전과 끝에 알린다. "몇 분 남았어?"에는 타이머 상태로 답한다.
   - "그거", "그 용액" 같은 말은 지금 단계의 원문 값으로 맞춘다.
   - 질문: 원문에 있는 것은 원문으로 답한다. 용어 뜻처럼 원문에 없는 설명은 짧은 일반 설명을 붙이고 "AI 일반 지식"으로 표시한다(예: "HPLC water 가 뭐야?"). 이 설명은 켰을 때만 나온다 — 개발 실행기는 켜고, 파일럿 실행기는 꺼 둔다.
   - 그림: 원문 그림을 띄운다. 원문에 그림이 없으면 바로 그려 준다(켰을 때, "AI 가 그린 그림" 표시). 웹 설명과 사진도 켜면 쓴다(출처 표시).
7. **기록** — 말한 관찰·문제·메모는 지우지 않는 실험 장부에 남는다. "안 보임"처럼 부정이 든 말은 통째로 남긴다.
8. **보고서** — 실험 장부에서 보고서를 만든다. Word(.docx)·Markdown·JSON·CSV 로 내려받는다. 보고서의 값은 실험자가 확인한다.

## 2. 값과 설명의 원칙

- **값과 안전은 원문 또는 사람이 확인한 값만 쓴다.** 지금은 원문 근거 검증을 통과한 값과 실험자가 확인한 값이다.
- **용어 설명처럼 원문만으로는 불친절한 내용은 일반 지식을 허용하고 표시한다.** 화면에는 "AI 일반 지식", 말로는 "PDF에는 따로 설명이 없어요. 일반적으로는 …". 숫자·방법·양이 든 설명은 서버가 빼고, 원문이 답하는 양 질문에는 일반 지식을 붙이지 않는다.
- **안전 주의와 완료 기준은 AI 가 새로 써 넣지 않는다.**
- **모델의 말은 상태를 바꾸지 않는다.** 모델은 바꾸기를 제안만 하고, 서버가 검증한 뒤 같은 상태 기계(`CuratedProtocolSession`)가 바꾼다. 묻기만 한 말은 상태를 바꾸지 않는다.
- 줄 RV 에서 더할 것(아직 없음): 검토에서 고친 값은 "검토자 입력", AI 가 제안한 값은 "AI 제안"으로 표시하고, 사람이 확인해야 실행 값이 된다.

## 3. 하지 않는 것

- **ELN/LIMS 를 대신하지 않는다.** 실험 기록의 공식 저장소(system of record)가 아니다.
- **PI·관리자 승인이 없다.** 리비전 승인·반려·회수, 검토자·관리자 화면은 2026-10-08 에 없앴다.
- **규제 준수를 주장하지 않는다.** GLP/GMP, 21 CFR Part 11, HIPAA, ISO 등.
- 안전 판단, 작업 재개 허가, 장비 제어를 하지 않는다. 응급 상황에는 정해진 문장으로 작업을 멈추고 연구실의 비상 연락 절차를 따르라고만 한다.

## 4. 역할별 공급자 — 지금 쓰는 값

`.env.example` 에 적힌 값이다. 키 값은 비워 두었고 `.env` 에만 넣는다.

| 역할 | 하는 일 | 지금 값 |
|---|---|---|
| 분석 (`ANALYSIS`) | PDF 프로토콜 구조 분석 | `google` · `gemini-3.8-flash` · 추론 `low` |
| 번역 (`TRANSLATION`) | 리비전 한국어, 단계 읽기 번역 | `google` · `gemini-3.8-flash` · 추론 `low` |
| 라우터 (`ROUTER`) | 말 한 번마다 LLM 라우터 호출(켰을 때) | `openai` · `gpt-6-luna` |
| 답변 (`ANSWER`) | 답변 역할(Answer·Source·Visual) | `openai` · `gpt-6-luna` |
| 보조 설명 (`SUPPLEMENTAL`) | PDF 밖 일반 설명("AI 일반 지식", 켜기 `VOINEY_LAB_SUPPLEMENTAL_MODEL_KNOWLEDGE_ENABLED`) | `openai` · `gpt-6-luna` |
| 보고서 서술 (`REPORT`) | 실험 보고서 문장 | `google` · `gemini-3.8-flash` |
| 웹 설명·사진, AI 도식 | 줄 WV 기능(켜기 두 줄) | `gpt-6-luna` (`OPENAI_API_KEY`) |
| 음성 인식 (STT) | 말 → 글 | ElevenLabs `scribe_v2` |
| 음성 합성 (TTS) | 글 → 말 | Google Cloud Chirp 3 HD (`ko-KR-Chirp3-HD-Charon`) |

- 역할별 공급자·모델의 기본값은 **"설정 필요"** 다(`src/voiney_lab/setting_names.py`). 다만 코드에는 옛 기본(공급자 `xai`)이 남아 있어서, 비워 두면 `XAI_API_KEY` 가 없을 때만 그 역할이 실패로 닫힌다. 모든 역할을 `.env` 에 적는다.
- 실제 호출 기록(줄 보고서 기준): OpenAI `gpt-6-luna` 로 라우터 평가 줄을 22회 호출(줄 XO, 2026-10-05), Google Gemini 로 분석·번역 222회 호출(줄 AQ, 2026-10-10). 분석 시간은 추론 `low` 에서 p50 39 s, p90 112 s(25개 문서, 줄 AQ, 2026-10-10). 이 저장소의 테스트는 공급자를 가짜 SDK 로만 부른다(contract-tested); 그 밖의 실제 호출 기록은 각 줄 보고서에 있다(예: 음성 공급자는 줄 SV).
- 설정 이름 표 전체는 `src/voiney_lab/setting_names.py`, 옛 이름 바꾸기는 `python scripts/migrate_env.py --check` / `--write`.

## 5. 실행 방법

Python 3.12 이상.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e '.[test]'
cp .env.example .env      # 키를 채운다. .env 는 커밋하지 않는다
```

### 개발 서버

```bash
./scripts/run_dev.sh              # 127.0.0.1:8000
./scripts/run_dev.sh --check-only # 설정만 보여 주고 data/runtime 은 건드리지 않음
```

- in-gel 개발 프로토콜과 그 원본 PDF(`data/runtime/candidate-a-source/in-gel-digestion.pdf`, 외부 라이선스라 커밋하지 않음)를 SHA-256 으로 확인하고 올린다.
- 상태는 `data/runtime/candidate-a-live-acceptance/` 에 둔다.
- 승인 안전 문서 카탈로그(`VOINEY_LAB_SAFETY_CATALOG`)와 사용 범위(`VOINEY_LAB_USAGE_SCOPE`)는 `.env` 에서 읽는다. 카탈로그가 없으면 서버가 프로토콜 목록에 답하지 않는다.

### 파일럿

```bash
./scripts/run_pilot.sh              # 127.0.0.1:8080
./scripts/run_pilot.sh --check-only
```

- 상태는 `data/runtime/pilot/` 에 둔다. 승인 안전 카탈로그는 실행기가 `data/runtime/pilot/approved_safety_catalog.sqlite`, 범위는 `reference_only` 로 고정한다.
- **지금 파일럿 실행기는 승인 안전 카탈로그가 없으면 시작을 거부한다(종료 코드 1).** 파일이 없거나, 카탈로그로 읽을 수 없거나, demo 문서가 섞여 있거나, 승인된 `reference_only` 문서가 하나도 없을 때도 거부한다. 이 카탈로그를 어떻게 할지는 줄 PL 에서 정한다.

## 6. 테스트 기준 (Verification)

```bash
python scripts/replay_turns.py
VOINEY_LAB_MOSS_ENABLED=false \
VOINEY_LAB_WORKSPACE_ENABLED=false \
VOINEY_LAB_EXPERIMENT_REPORTS_ENABLED=false \
python -m pytest -q
python -m compileall -q src tests scripts
git diff --check
```

- 세 플래그는 셸이나 `.env` 의 값이 결과를 바꾸지 않게 끈다. 줄 보고서들은 `VOINEY_LAB_LLM_ROUTER_ENABLED=false` 도 함께 끈다.
- 통과 수는 외부 라이선스 PDF 가 있느냐에 따라 다르다(두 기준, two baselines). **숫자를 말할 때는 조건을 함께 말한다.**
  - 조건 A — 라이선스 PDF 4개가 있음(관리자 폴더): 이 문서에 실측값이 없다. 줄 보고서의 "원래 폴더 기대 passed" 를 본다.
  - 조건 B — 라이선스 PDF 없음(CI, 새 clone): **2418 passed, 559 skipped, 실패 0** — 2026-10-10, `453c1cc`, CI 실행 `38056883162`(줄 CL). 그 전 dev `3992f86` 은 2472 passed, 560 skipped(CI `38033027341`, 줄 VF). 줄이 테스트를 더하거나 지우면 바뀌므로 줄 보고서마다 적는다.
- 테스트는 바깥 공급자에 닿지 않는다. 키를 테스트 프로세스에서 지우고 이 컴퓨터 밖으로의 연결을 막는다(`tests/conftest.py`).
- 화면 시험(Playwright, `tests/e2e/`)은 CI 가 돌린다(같은 실행에서 72 passed): `npm install && npx playwright install --with-deps chromium && npx playwright test`.

## 7. 데이터와 외부 AI 처리

- **밖으로 나가는 것**(지금 값 기준): 말소리 → ElevenLabs(STT), 말할 문장 → Google Cloud(TTS), 글자 층 없는 쪽의 이미지 → 설정한 OCR 엔진, PDF 글자 → Google Gemini(분석·번역), 말한 글과 지금 단계 문맥 → OpenAI(라우터·답변·보조 설명), 켰을 때 웹 설명·도식 → OpenAI, 보고서 기록 → Google Gemini(보고서 서술).
- **이 컴퓨터에 남는 것**: 원본 PDF, 분석 결과, 실험 장부, 보고서는 `data/runtime/` 아래 SQLite·파일(git 이 무시)에 둔다. 원음은 저장하지 않는다 — STT 진단 녹음(`VOINEY_LAB_STT_DIAGNOSTICS_ENABLED`)을 켰을 때만 개수를 제한해 둔다.
- **기록(log)**: 말 내용은 남기지 않는다. 질의는 길이와 SHA-256 앞부분만 남긴다.
- 키는 `.env` 에만 둔다. `.env`, `data/runtime/`, 원음, 연구실 PDF 는 커밋하지 않는다.
- 공급자가 보낸 데이터를 어떻게 보관하는지는 각 공급자 약관을 따른다. 파일럿 전에 사람이 확인한다(이 저장소가 보증하지 않는다).

## 8. 라이선스 메모

- PDF 엔진 PyMuPDF 는 AGPL-3.0 과 상용 라이선스 중 하나로 쓴다. `src/voiney_lab/pdf_text_engine.py` 한 곳에서만 쓴다. 상용화 전에 상용 라이선스를 사거나 엔진을 그 한 모듈 뒤에서 바꾼다(2026-10-02·10-06 결정).
- in-gel 개발 프로토콜의 원본 PDF 는 외부 라이선스라 저장소에 없다.

## 9. 다음 계획 (아직 없는 것)

1. **검토 탭(줄 RV)** — 업로드 → 자동 분석(목표 약 1분) → 검토(본인 또는 누구나, 등록할 때 검토자 이름 필수) → "검토 완료하고 등록" → 검토본 저장(다시 쓰기, v1/v2 기록) → 시작 전 확인 → 음성 안내. 검토에서 고친 값은 "검토자 입력", AI 가 제안한 값은 "AI 제안"으로 표시하고 사람이 확인해야 실행 값이 된다.
2. **다른 사람 말 거르기** — 실험자가 아닌 사람의 말을 거른다(줄 SP1).
3. **모바일 웹(PWA)** — 화면은 모바일 웹으로 간다. 앱은 나중.
4. **알아듣기를 LLM 중심으로** — 앞단 규칙 뒤의 LLM 라우터가 더 많은 말을 맡는다.
5. 그 밖: 답변 역할들(Answer·Source·Visual) 되살리기(줄 RA), 지난 기록·매뉴얼 검색(MOSS 를 그때 다시 넣는다), 승인 안전 카탈로그와 파일럿 실행기 정리(줄 PL).

## 10. 문서

- [`docs/DOCUMENTATION_INDEX.md`](docs/DOCUMENTATION_INDEX.md) — 문서 목록과 어느 것이 지금 동작을 설명하는지.
- [`docs/BEHAVIOR_REFERENCE.md`](docs/BEHAVIOR_REFERENCE.md) — 지금 동작의 상세(영어, 옛 README 의 절들).
- [`AGENTS.md`](AGENTS.md), [`.agent/`](.agent/) — 코드가 지켜야 할 규칙. [`CLAUDE.md`](CLAUDE.md) — 코딩 에이전트 작업 규칙. [`docs/GIT_WORKFLOW.md`](docs/GIT_WORKFLOW.md) — 브랜치·PR 규칙.

## English summary

VoineyLab (by Voiney) is an MVP prototype, not field-validated: an AI that
runs a lab's own uploaded protocol as written, keeps the time, keeps the
order, and turns what the experimenter says into notes. Flow: PDF upload →
automatic OCR of pages without a text layer → structured analysis →
source-evidence check → start screen (the experimenter's "start" is the one
human confirmation; there is no PI/admin approval) → voice guidance with timer
notices, questions and pictures → append-only record → report (.docx, .md,
JSON, CSV). The server alone changes workflow state; model output only
proposes. Values and safety come from the source or from a person's
confirmation; a term the source does not explain may get a short general
explanation labelled "AI 일반 지식". It is not an ELN/LIMS and claims no
regulatory compliance.

PyMuPDF is used under its AGPL-3.0 / commercial dual licence, only through
`src/voiney_lab/pdf_text_engine.py`; before commercialisation either a
commercial PyMuPDF licence is bought or the engine is replaced behind that one
module. Details: `docs/BEHAVIOR_REFERENCE.md`.
