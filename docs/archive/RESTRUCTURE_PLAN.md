> 보관: 2026-10-08 MVP 이전 설계 — PI·관리자 승인·반려·회수는 폐기, 리비전·검토자 입력 개념은 줄 RV 에서 다시 설계

# 저장소 구조 정리 계획 — 패키지 이름 변경과 데이터 재배치

- 상태: **실행 완료 (2026-09-29).** 1~5단계를 `feature/jaejun-restructure` 에 커밋 5개로 했다. 결과는 §10.
- §0~§9 는 실행 전 `46a0b90` 에서 쓴 조사와 계획을 그대로 둔 기록이다. 그 안의 경로는 옮기기 전
  위치다. 지금 위치와 확인 결과는 §10 에 있다.
- 기준: 브랜치 `feature/jaejun-restructure`, 커밋 `46a0b90`, 2026-09-29 측정.
- 범위: 이름과 위치만 바꾼다. 동작 변경, 버그 수정, 큰 파일 분리, 의존성 변경, 테스트 로직 변경은 하지 않는다.
  작업 중 찾은 문제는 고치지 않고 §9 에 적었다.
- 읽지 않은 것: `.env`, `docs/archive/` 의 파일 내용. (조사 중 한 번 `docs/archive/` 의 파일 이름
  목록이 출력되었으나 판단 근거로 쓰지 않았다.)
- `data/runtime/` 은 쓰지 않았다. 패키지 이름·경로 문자열이 저장돼 있는지 확인하려고 바이트 단위로
  검색했고, 일치한 식별자 토큰만 출력했다. 파일 내용은 출력하지 않았다.

---

## 실행 전에 물은 것

아래는 실행 전에 물은 질문과 권장안이다. 사람의 답은 §10-1 에 있다.

| # | 질문 | 권장안 | 이유 |
|---|---|---|---|
| Q1 | 2-4 의 "남은 곳" 규칙을 고쳐도 되나 | 고친다 (§5 2-4) | 지시대로면 2단계 뒤 남은 곳은 B-2 와 `docs/archive/` 뿐이어야 한다. 그런데 현행 문서 27건은 4단계에서 고치기로 되어 있고, `docs/course-archive/README.md` 2건은 CLAUDE.md 가 "기록으로 둔다"고 정했다. 규칙을 그대로 두면 2단계가 스스로 멈춘다. |
| Q2 | 배포 이름도 바꾸나 | `pyproject.toml` 의 `name` 을 `voice-workflow-agent` → `voiney-lab` 으로 바꾼다. 이 이름을 적은 설치 안내 문구 1곳(`moss_retrieval.py:372`)도 함께 바꾼다. 명령 이름 `voice-workflow-replay`, `voice-workflow-evaluate` 는 **그대로 둔다** | 2-3 이 "패키지 이름·설정"을 바꾸라고 했다. 명령 이름은 README 와 사용자가 이미 쓰는 인터페이스라 이번 범위 밖으로 본다. |
| Q3 | 옛 설치 흔적을 지워도 되나 | `.venv` 에서 `pip uninstall -y voice-workflow-agent` 를 하고, `src/voice_workflow_agent.egg-info/` 를 지운다 | 둘 다 git 밖에 있고 설치하면 다시 생긴다. 남겨 두면 `voice-workflow-agent` 배포 정보가 계속 보인다 (§2 B-3). |
| Q4 | 브라우저 테스트를 [검증]에 넣나 | 넣지 않는다. 대신 서버 기동 확인을 한다 (§5 2-4) | 기존 Playwright 설정 두 개는 모두 `data/runtime/` 에 쓴다. `run_ci_server.sh:25` 는 `rm -rf data/runtime/ci-e2e` 를 하고, `run_candidate_a.sh` 는 파일럿 저장소 `data/runtime/candidate-a-live-acceptance` 에 fixture 를 넣는다. |
| Q5 | `.env` 가 바뀌는 경로를 가리키나 | **사람만 확인할 수 있다.** 아래 명령의 결과가 있는지만 알려 달라 | 나는 `.env` 를 읽지 않는다. 결과가 없으면 영향도 없다. 결과가 있으면 `.env` 수정은 사람이 한다. 답이 없으면 "사람이 할 후속 작업"으로 넘기고 진행한다. |
| Q6 | 날짜가 박힌 기록 문서도 고치나 | 고치지 않는다 | `docs/ANALYSIS_2026_09_11.md` 는 스스로 "`bfc292b` 에서 잰 읽기 전용 조사"라고 밝힌 기록이다. `PILOT_READINESS_PACKAGE.md:161-162` 는 옛 모듈 경로로 한 실제 xAI 호출 기록이라, 바꾸면 존재하지 않던 경로로 호출한 것처럼 된다. 목록은 §6. |
| Q7 | `GIT_WORKFLOW.md` 는 어떻게 하나 | 건드리지 않는다 | 지시문은 `docs/GIT_WORKFLOW.md` 라고 했지만 그런 파일은 없다. 루트의 `GIT_WORKFLOW.md` 는 오늘 14:22 에 만들어진 추적 안 된 파일이다. 내용에 바꿀 곳도 없다 (`VOINEY_LAB_` 환경변수만 있다). 둘 곳과 커밋은 사람이 정한다. |
| Q8 | `tests/fixtures/candidate_a_grounded_voice_eval.json` 도 옮기나 | 옮긴다 → `data/fixtures/evaluation/` | 이 파일은 테스트가 쓰지 않는다. 평가 스크립트 `evaluate_candidate_a_grounded_qa.py:44` 만 쓴다. 짝이 되는 평가 파일과 한곳에 두는 편이 맞다. |
| Q9 | B-2 자리에 설명 주석을 다나 | 단다. 영어 한 줄씩, `curated_protocol.py:868` 과 `moss_retrieval.py:256` 두 곳 | 옛 이름이 왜 남아 있는지 적어 두지 않으면, 나중에 누가 "놓친 곳"으로 보고 고쳐서 fixture 로드나 MOSS 색인을 깨뜨린다. 주석은 동작을 바꾸지 않는다. |
| Q10 | `data/development_cache/` → `data/cache/` 는 | 이번에는 하지 않는다 (판단 불가, §8) | git 밖에 있어 `git mv` 로 옮길 수 없다. `.env` 설정도 확인할 수 없다. 캐시를 못 찾으면 다음 분석에서 provider 호출에 다시 돈을 쓴다. |

Q5 확인 명령 (사람이 직접 실행):

```bash
grep -nE 'src/voice_workflow_agent|development_protocols|data/evaluation|approved_safety_manual|development_cache|tests/fixtures' .env
```

같이 알릴 규칙 충돌 하나 (이번 작업에는 영향 없음): CLAUDE.md 의 Branch workflow 는 "`main` 에서
분기"라고 하고, `GIT_WORKFLOW.md` 는 "`dev` 에서 분기, `dev` 로 PR"이라고 한다. `GIT_WORKFLOW.md`
자신이 "충돌하면 멈추고 사람에게 묻는다"고 정했으므로 여기서 묻는다. 지금 `main`, `dev`, 작업 브랜치는
모두 같은 커밋(`46a0b90`)이고, 이 작업은 브랜치를 만들지도 push 하지도 않는다.

---

## 0. 기준선

### 0-1. 작업 트리

- 현재 브랜치: `feature/jaejun-restructure`. HEAD `46a0b90` 는 `main`, `dev`, `origin/main` 과 같다.
- `git status --short`: `?? GIT_WORKFLOW.md` 한 건. 사람이 만든 파일이라(Q7) 건드리지 않는다.
  모든 커밋은 경로를 지정해서 `git add` 하므로 이 파일은 커밋에 들어가지 않는다. 이 파일만 빼면
  트리는 깨끗하다.

### 0-2. pytest 기준

```bash
VOINEY_LAB_MOSS_ENABLED=false \
VOINEY_LAB_WORKSPACE_ENABLED=false \
VOINEY_LAB_EXPERIMENT_REPORTS_ENABLED=false \
python -m pytest -q
```

```
1513 passed, 13 skipped, 1283 subtests passed
```

exit 0. 두 번 실행했고 두 번 모두 같았다 (74.65 s, 73.38 s). **2~4단계의 모든 커밋 뒤에 이 세 숫자가
정확히 같아야 한다.**

**조건: A 에 가깝지만 A 는 아니다.** README 의 조건 A (1521 passed, 0 skipped) 는 라이선스 PDF 4개가 모두
있는 트리를 말한다. 이 트리에는 두 개만 있다.

| 원본 | 위치 | 이 트리 |
|---|---|---|
| in-gel digestion (2,581,457 B, sha256 `63d81102…`) | `data/runtime/candidate-a-source/in-gel-digestion.pdf` | 있음. 그래서 `tests/conftest.py` 가 PDF 가 없을 때 건너뛰는 14개 모듈이 모두 실행된다 |
| ANKOM (48,906,987 B, `5367ca6b…`) | `data/runtime/candidate-a-live-acceptance/objects/sha256/53/…` | 있음 |
| intracellular metabolite extraction (24 MB, `997d020c…`) | 저장소 루트 `intracellularmetaboliteextraction.pdf` | **없음** |
| dynamic headspace (5.1 MB, `2bf10277…`) | 저장소 루트 `usingdynamicheadspacecollections.pdf` | **없음** |

건너뛴 13건은 모두 이 두 파일이 없어서다 (`pytest -rs` 로 확인). 모듈별로는
`test_extraction_cross_check.py` 3, `test_numbered_label_trigger.py` 5, `test_repeat_range_integrity.py` 1,
`test_step20_label_loss_regression.py` 4 건이다.

통과 수는 `data/runtime/` 의 내용에도 달려 있다. `test_stored_payloads_still_load.py:27` 이
`data/runtime` 아래 저장소를 찾아서 테스트를 만든다. 이번 작업은 `data/runtime/` 을 건드리지 않으므로
기준은 그대로다.

### 0-3. 나머지 검증 기준 (모두 `46a0b90` 에서)

| 확인 | 결과 |
|---|---|
| `python scripts/replay_turns.py` | exit 0 |
| `python -m compileall -q src tests scripts` | exit 0 |
| `git diff --check` | 깨끗함 |
| `python scripts/evaluate_candidate_a_grounded_qa.py` | exit 0. cases 20, route_accuracy 1.0, provider 호출 0 |
| `python scripts/evaluate_candidate_a_hardening.py` | exit 1. case_count 100, route_accuracy 0.98, provider 호출 0. CI 에 적힌 기존 실패 2건과 같다 (`ci.yml:42-47`) |
| 현행 문서가 가리키는 경로·모듈 검사 (§5 4-2 의 스크립트) | 534곳 중 23곳이 이미 없다. 목록은 §9-5 |

평가 스크립트 두 개는 3단계에서 옮길 파일의 유일한 소비자라서 기준에 넣었다. pytest 는 이 스크립트들을
실행하지 않는다. 두 스크립트는 파일을 쓰지 않고 네트워크도 부르지 않는다 (코드 확인, 실행 전후
`data/runtime/` 비교).

### 0-4. `data/runtime/` 관찰

pytest 실행 전후로 `data/runtime/` 의 파일 목록, 크기, 수정 시각을 비교했다. **하위 항목 32개는 모두
그대로였다.** 다만 `data/runtime/` 디렉터리 자체의 수정 시각은 바뀌었다.
`tests/test_candidate_a_final_hardening.py:72` 가 `tempfile.TemporaryDirectory(dir=ROOT / "data/runtime")`
로 임시 디렉터리를 만들고 지우기 때문이다. 즉 **[검증] 명령이 매번 `data/runtime/` 에 잠깐 쓴다.** 원래
있던 동작이고 테스트 변경은 범위 밖이라 고치지 않는다 (§9-1). 매 단계 이 비교를 반복해서 하위 항목이
그대로인지 확인한다.

---

## 1. 조사 A — 데이터 위치

### A-1. PDF 와 프로토콜 관련 JSON 목록

`data/`, `tests/`, `scripts/` 전체와 저장소 루트를 찾았다. `tests/` 와 `scripts/` 아래에는 PDF 가 없다.
PDF 가 필요한 테스트는 임시 디렉터리에 합성 PDF 를 만들어 쓰거나, 아래의 라이선스 PDF 를 쓴다.

**git 이 추적하는 파일 (12개)**

| 경로 | 크기 (B) | 용도 | 출처·라이선스 |
|---|---:|---|---|
| `data/development_protocols/candidate_a_curated_analysis.json` | 19,311 | 수작업 정답. in-gel 의 구조화 분석 fixture | 저장소 작성. **in-gel PDF 원문 발췌가 들어 있다** (A-4) |
| `data/development_protocols/candidate_a_curated_analysis.provenance.json` | 945 | 위 fixture 의 출처·무결성 기록 (`fixture_sha256`, `canonical_schema_sha256`, `candidate_sha256`) | 저장소 작성 |
| `data/development_protocols/candidate_a_curated_analysis.localization.ko.json` | 6,566 | 한국어 번역 사이드카. `fixture_sha256` 으로 묶임 | 저장소 작성 (발췌 번역) |
| `data/development_protocols/candidate_a_curated_analysis.timers.json` | 2,736 | 타이머 사이드카. `fixture_sha256`, `source_literal` | 저장소 작성 (원문 시간 표기 포함) |
| `data/development_protocols/candidate_a_curated_analysis.visuals.json` | 1,747 | 그림 사이드카. `source_region_hash` | 저장소 작성 |
| `data/evaluation/candidate_a_real_voice_hardening.json` | 21,759 | 평가 정답 100건 (발화 → 기대 동작) | 저장소 작성 |
| `data/approved_safety_manual.demo.json` | 7,689 | 데모 안전자료 레코드. 운영 모드가 아닐 때 `safety_pack.py` 가 fallback 으로 읽는다 | 저장소 작성, 가상 ("classroom demo record") |
| `data/moss_demo/approved_documents.ko.json` | 4,375 | MOSS 데모 매니페스트 | 저장소 작성, 가상 (`FICTIONAL-MOSS-DEMO-*`) |
| `data/procedure_demo/approved_document.ko.json` | 2,100 | 옛 절차 경로의 데모 매니페스트 | 저장소 작성, 가상 |
| `data/procedure_demo/procedures.ko.json` | 2,702 | 옛 절차 경로의 데모 절차 | 저장소 작성, 가상 |
| `tests/fixtures/candidate_a_grounded_voice_eval.json` | 2,670 | 평가 정답 20건. 평가 스크립트만 쓰고 테스트는 쓰지 않는다 | 저장소 작성 |
| `tests/fixtures/fictional_ingestion_manifest.json` | 1,578 | 테스트용 가상 매니페스트 | 저장소 작성, 가상 |

**git 밖에 있는 파일**

| 경로 | 크기 | 용도 | 비고 |
|---|---:|---|---|
| `data/runtime/candidate-a-source/in-gel-digestion.pdf` | 2,581,457 | 라이선스 원본. 테스트 35개 모듈과 `conftest.py`, 스크립트 6개 (평가 스크립트, `run_candidate_a.sh` 포함)가 쓴다 | `.gitignore:11` |
| `data/runtime/candidate-a-live-acceptance/objects/sha256/53/5367ca6b….pdf` | 48,906,987 | 라이선스 원본 (ANKOM). 파일럿 저장소 안의 내용 주소 사본 | 실행 데이터 |
| `data/runtime/candidate-a-live-acceptance/objects/sha256/63/63d81102….pdf` | 2,581,457 | in-gel 과 같은 바이트. 파일럿 저장소 사본 | 실행 데이터 |
| `data/runtime/candidate-a-live-acceptance.backup-20260827-154309/objects/…/63d81102….pdf` | 2,581,457 | 위 사본의 백업 | 실행 데이터 |
| `data/runtime/**` 의 `*.sqlite`, `workspace/` | 약 57 MB (PDF 포함) | 파일럿 기록, CI 저장소, MOSS 데모 카탈로그 | 이번 작업에서 옮기지 않는다 |
| `data/development_cache/` (파일 40개, 약 700 KB) | — | 분석 캐시 (`chunk_analysis/`), 정확도 결과 (`accuracy/`), provider 진단 (`provider_diagnostics/`), 백업 (`segver6-chunk-backup/`) | `.gitignore:22` |
| 루트 `intracellularmetaboliteextraction.pdf`, `usingdynamicheadspacecollections.pdf` | 24 MB, 5.1 MB | 라이선스 원본 | **이 트리에 없음**. `.gitignore:20-21` |

### A-2. 경로를 참조하는 곳

표기: **R** = 저장소 루트 기준 하드코딩 (`Path(__file__).parents[n]` 이나 `$ROOT`), **C** = 현재 작업
디렉터리 기준 하드코딩 (`Path("data/...")`), **E** = 환경변수.

**3단계에서 옮길 파일을 참조하는 곳 (27개 파일)**

| 대상 | 참조 (파일:줄) | 종류 |
|---|---|---|
| `data/development_protocols/*` | `scripts/run_candidate_a.sh:6,7`, `scripts/derive_timer_manifest.py:27,29`, `scripts/evaluate_candidate_a_grounded_qa.py:39`, `scripts/evaluate_candidate_a_hardening.py:42,43`, `scripts/score_extraction.py:26,27` (사용 예시 docstring) | R |
| 〃 | `tests/test_candidate_a_acceptance_phase2.py:17`, `test_candidate_a_final_hardening.py:27`, `test_candidate_a_live_voice_generalization.py:26,27`, `test_candidate_a_research_hardening.py:41,42`, `test_candidate_a_websocket_integration.py:115,116`, `test_curated_protocol_cascade.py:67,68`, `test_endpoint_observation_gate.py:43,45`, `test_experiment_reports.py:278,279`, `test_phase3_acceptance.py:38`, `test_protocol_catalog.py:784,786`, `test_protocol_provider_diagnostics.py:29`, `test_repeat_until_declaration_properties.py:52,54`, `test_runtime_intent_routing.py:26,27`, `test_safety_pack.py:38,39,40`, `test_score_extraction_tool.py:20`, `test_semantic_intent_fallback.py:55,57`, `test_stability_and_semantic_hardening.py:30`, `test_transcript_admission.py:17` | R |
| 〃 | `tests/test_extraction_accuracy.py:28`, `test_timer_manifest.py:29`, `test_ui_data_provenance.py:21,22,89,105,128,209` | C |
| 〃 (서버 실행 시) | `server.py:1007-1011` 이 `VOINEY_LAB_CURATED_PROTOCOL_FIXTURE` / `_PROVENANCE` / `_SOURCE_PDF` 를 읽는다. 값은 `run_candidate_a.sh:59-61` 이 넣는다 | E |
| 사이드카 3개 | `curated_protocol.py:992-996` 이 fixture 파일 옆에서 `with_name(...)` 으로 찾는다. **5개 파일은 한 디렉터리에 같이 있어야 한다** | fixture 경로 기준 |
| `data/evaluation/candidate_a_real_voice_hardening.json` | `scripts/evaluate_candidate_a_hardening.py:47` | R |
| `data/approved_safety_manual.demo.json` | `src/voice_workflow_agent/safety_pack.py:494` (`parents[2] / "data" / ...`). 파일이 없으면 **조용히** 빈 데모 팩이 된다. `tests/test_safety_pack.py::test_17c_candidate_a_demo_safety_pack` 가 `total_document_count > 0` 으로 지키고, 이 트리에서는 실행된다 (CI 조건 B 에서는 건너뛴다) | R |
| `tests/fixtures/candidate_a_grounded_voice_eval.json` | `scripts/evaluate_candidate_a_grounded_qa.py:44` | R |

`.gitignore`, `.github/workflows/ci.yml`, `pyproject.toml`, Playwright 설정 중 위 파일을 가리키는 곳은 없다.

**옮기지 않는 파일을 참조하는 곳 (기록용)**

| 대상 | 참조 | 종류 |
|---|---|---|
| `data/runtime/candidate-a-source/in-gel-digestion.pdf` | `tests/conftest.py:19` 외 테스트 35개 모듈, `scripts/` 6개 (`run_candidate_a.sh:8` 은 `VOINEY_LAB_CANDIDATE_A_SOURCE_PDF` 로 덮어쓸 수 있음) | R / C (`test_extraction_cross_check.py:33`, `test_repeat_range_integrity.py:44,425,511`, `test_timer_manifest.py:34`, `test_extraction_accuracy.py:27`, `test_pdf_to_session_walkthrough.py:49`, `test_numbered_label_trigger.py:25`, `test_claim_contract_audit.py:184`) / E |
| ANKOM 객체 경로 | `tests/test_extraction_cross_check.py:30`, `test_repeat_range_integrity.py:515`, `test_timer_manifest.py:37`, `test_numbered_label_trigger.py:19`, `test_claim_contract_audit.py:188`, `scripts/diagnose_hazard_claim_chunk.py:46`, `diagnose_protocol_claim_latency.py:63`, `prototype_claim_chunks.py:56`, `diagnose_full_document_run.py:50` | C / R |
| 루트의 PDF 2개 | `tests/test_extraction_cross_check.py:632,636`, `test_numbered_label_trigger.py:22,23`, `test_step20_label_loss_regression.py:41`, `test_repeat_range_integrity.py:473,512,513`, `test_claim_contract_audit.py:185,186`, `test_timer_manifest.py:40,41`, `test_endpoint_observation_gate.py:48,49`, `scripts/diagnose_figure_label_chunk.py:55`, `.gitignore:20,21` | C / R |
| `data/runtime` 전체 | `tests/test_stored_payloads_still_load.py:27` (C, 동적 수집), `server.py:1075,1089` (R, STT 진단 디렉터리 기본값이며 반드시 `data/runtime` 아래여야 함), `run_candidate_a.sh:9`, `run_ci_server.sh:15`, `test_protocol_catalog.py:977` (run_candidate_a.sh 의 한 줄을 그대로 고정), `.gitignore:11` | R / C |
| `data/development_cache/*` | `chunk_analysis_cache.py:61` (C, `VOINEY_LAB_CHUNK_CACHE_DIR` 가 없을 때 기본값), `scripts/score_extraction.py:298` (C), `scripts/diagnose_provider_chunk.py:239` (C), `scripts/collect_chunks.sh:14,57`, `tests/test_chunk_analysis_cache.py:436` (이름 `development_cache` 를 고정), `tests/test_score_extraction_tool.py:87`, `.gitignore:22` | C / E |
| `data/moss_demo/approved_documents.ko.json` | `scripts/setup_moss_demo.py:26`, `tests/test_moss_retrieval.py:250` | R |
| `data/procedure_demo/*.json` | `scripts/setup_procedure_demo.py:27`, `tests/test_procedure_demo.py:23`, `test_server_procedure_integration.py:28`, `test_curated_protocol_cascade.py:4377` | R |
| `tests/fixtures/fictional_ingestion_manifest.json` | `tests/test_server_procedure_integration.py:497` | R |

C 로 표기한 곳은 저장소 루트에서 실행해야만 맞는 경로다. pytest 도 루트에서 실행해야 한다 (§9-6).

### A-3. 등록된 프로토콜이 실행 중 저장되는 곳

- 켜는 법: `VOINEY_LAB_PROTOCOL_ENABLED=true`.
- 위치: `VOINEY_LAB_PROTOCOL_DATA_DIR` (`experiment_protocol_config.py:12`). 켜져 있으면 반드시
  절대 경로여야 하고 기본값은 없다 (`experiment_protocol_config.py:48-59`).
- 안의 구조: `protocol_workspace.sqlite` (`experiment_protocol_store.py:35`) 와 PDF 원본을 내용 주소로
  저장한 `objects/sha256/<앞 2자리>/<sha256>.pdf` (`experiment_protocol_files.py:113,126`).
- 함께 쓰는 설정: `VOINEY_LAB_WORKSPACE_DATA_DIR` (`workspace_store.py:132`, 절대 경로 필수),
  `VOINEY_LAB_EXPERIMENT_REPORT_DB` (`experiment_reports.py:48`, 옛 이름
  `…_EXPERIMENT_REPORTS_DATABASE` 도 받음).
- 실행 스크립트별 값:
  - `run_candidate_a.sh:9,71-77` → `data/runtime/candidate-a-live-acceptance/` (보고서 DB 와 workspace 도 그 안)
  - `run_ci_server.sh:15,25-33` → `data/runtime/ci-e2e/`. **켤 때마다 `rm -rf` 로 지운다.**
  - `.env.example:33,43,141` → 예시로 `/absolute/ignored/runtime/...`
  - `.env` → 읽지 않았으므로 모른다 (Q5)

### A-4. 라이선스 때문에 git 에 있으면 안 되는 파일

- 지금 추적 중인 PDF: **0개.** 모든 ref 의 기록(`git log --all --diff-filter=A`)에서도 PDF 가 추가된 적이 없다.
- **판단 불가:** `candidate_a_curated_analysis.json` 과 사이드카(localization, timers)에는 in-gel PDF 의 원문
  발췌가 들어 있다 (`source_excerpt`, `source_text`, `source_literal`). README 는 이 PDF 를 "externally
  licensed"라고만 적었고, 발췌를 다시 배포해도 되는 조건은 저장소 어디에도 없다. 사람이 원본 라이선스를
  확인해야 한다. 이번 작업은 이 파일들의 내용을 바꾸지 않고 위치만 옮긴다.
- 평가 파일 두 개(`candidate_a_real_voice_hardening.json`, `candidate_a_grounded_voice_eval.json`)는
  작성된 발화(`text`, `transcript`)와 기대 결과로 되어 있다.

### A-5. 매니페스트와 fixture 안의 파일 경로 문자열

| 파일 | 문자열 | 어떻게 쓰이나 |
|---|---|---|
| `candidate_a_curated_analysis.provenance.json` | `"candidate_filename": "in-gel-digestion.pdf"` | 원본 PDF 의 **파일 이름**과 정확히 같아야 한다 (`curated_protocol.py:903-918`). 디렉터리는 옮겨도 되지만 파일 이름은 바꾸면 안 된다 |
| 〃 | `"extraction_method": "voice_workflow_agent.experiment_protocol_pdf.extract_protocol_pdf"` | 패키지 이름이 든 저장 식별자. B-2 |
| `data/moss_demo/approved_documents.ko.json` (줄 23, 94) | `"source_path": "data/moss_demo/approved_documents.ko.json"` | 문서 카탈로그의 `source_path` 열에 저장된다 (`document_store.py:25,91`). 실제로 `data/runtime/moss_demo_catalog.sqlite` 안에 이 문자열이 있다. 파일로 열지는 않고, 해시에도 들어가지 않는다 (`retrieval.py:45-50`, `moss_retrieval.py:153-165`) |
| `data/procedure_demo/approved_document.ko.json:23` | `"source_path": "data/procedure_demo/approved_document.ko.json"` | 위와 같은 방식 |
| `tests/fixtures/fictional_ingestion_manifest.json:23` | `"source_path": "tests/fixtures/fictional-cli-source.json"` | 가상의 경로다 (그런 파일은 없다). 위와 같은 방식 |
| `data/development_cache/accuracy/segver6-cache-manifest.json`, `provider_diagnostics/*.json` 3개 | 캐시 자신의 경로 (`data/development_cache/chunk_analysis/...`) | git 밖에 있다. 코드가 읽지 않는 수작업 기록이다 (`segver6` 을 참조하는 코드는 없다) |
| 나머지 추적 JSON 7개 | 경로 문자열 없음 | — |

이 조사의 결론: 자기 경로를 내용에 담은 매니페스트는 옮기면 두 선택지만 남는다. 내용을 고치면
3-3 ("내용은 그대로") 을 어기고, 고치지 않으면 저장되는 메타데이터가 틀린 위치를 가리킨다. 그래서
옮기지 않는다 (§8).

---

## 2. 조사 B — 패키지 이름 `voice_workflow_agent` → `voiney_lab`

### B-1. 소문자 `voice_workflow_agent` 가 나오는 곳

추적 파일 전체(`docs/archive/` 제외)에서 **1,193건, 168개 파일**. 한 건씩 앞뒤 4줄을 보고 분류했다.

| 종류 | 건수 | 파일 | 처리 |
|---|---:|---:|---|
| import 문 (`.py`) | 670 | 151 | 2단계 |
| `mock.patch` 등 대상 문자열 (`patch(`, `patch.object`, `patch.multiple`, 여러 줄로 이어 쓴 문자열 포함) | 432 | 17 | 2단계 |
| 테스트가 직접 읽는 소스·static 파일 경로 (`src/voice_workflow_agent/...`) — `test_frontend.py` 13, `test_ui_data_provenance.py` 5, `test_server_helpers.py:1804`, `test_endpoint_observation_gate.py:50` | 20 | 4 | 2단계 |
| 로거 이름 — `getLogger("voice_workflow_agent...")` 6곳 (`server.py:238`, `tools.py:19`, `vad.py:20`, `worker.py:28`, `safety_pack.py:30`, `moss_retrieval.py:28`), 이름에 기대는 `assertLogs` 5곳 (`test_configuration.py:177`, `test_protocol_catalog.py:862`, `test_moss_retrieval.py:164`, `test_vad.py:108,136`) | 11 | 10 | 2단계 |
| `python -m` / subprocess 로 부르는 모듈 — `experiment_protocol_pdf.py:683` (별도 프로세스 `pdf_text_worker`), `test_pdf_worker_isolation.py:60,62,196`, `test_pdfium_serialization.py:248`, `scripts/replay_turns.py:5` (docstring) | 6 | 4 | 2단계 |
| `run_candidate_a.sh` 안 python heredoc 의 import (`:117-123`, `:169-172`) | 8 | 1 | 2단계 |
| uvicorn `"모듈:app"` 문자열 — `run_candidate_a.sh:205`, `run_ci_server.sh:42` | 2 | 2 | 2단계 |
| importlib / `__import__` — `test_experiment_protocol_store.py:124-125` | 1 | 1 | 2단계 |
| 실행 중 라벨 (스레드 이름) — `moss_retrieval.py:346` | 1 | 1 | 2단계 (저장되지 않음) |
| `pyproject.toml` — entry point 2 (`:31,32`), package-data 키 (`:51`) | 3 | 1 | 2단계 |
| `package.json`, `package-lock.json`, Playwright 설정 | 0 | 0 | — |
| `.github/workflows/ci.yml` | 0 | 0 | — |
| 문서 (현행) | 27 | 9 | 4단계에서 일부 (§5) |
| 문서 (`docs/course-archive/README.md:75,92`) | 2 | 1 | 바꾸지 않음 (CLAUDE.md) |
| **B-2 — 바꾸지 않음** | 10 | 7 | 아래 |
| 합계 | **1,193** | | 2단계에서 바꾸는 것 **1,154건** |

로그 형식은 `%(asctime)s %(levelname)s %(message)s` 다 (`server.py:237`, `worker.py:27`). 로거 이름이
바뀌어도 로그 한 줄의 모양은 그대로다. 모듈 대부분은 `getLogger(__name__)` 을 쓰지 않지만
`semantic_intent.py:50` 은 쓴다. 이름을 한꺼번에 바꿔야 로거 계층(`voiney_lab` 아래 `voiney_lab.*`)이 지금과
같은 모양으로 남는다.

`__file__` 로 루트를 찾는 곳 (`server.py:227`, `tools.py:21`, `worker.py:24`, `safety_pack.py:494`,
`claim_contract_audit.py:49`, `server.py:323` 의 `STATIC_DIR`) 은 모두 깊이로만 계산한다. 디렉터리 이름이
바뀌어도 그대로 맞는다.

### B-2. ★바꾸지 않을 목록★ — 저장되거나 밖으로 나가는 식별자

| # | 위치 | 값 | 바꾸면 생기는 일 |
|---|---|---|---|
| 1 | `src/voice_workflow_agent/curated_protocol.py:868` | `"voice_workflow_agent.experiment_protocol_pdf.extract_protocol_pdf"` | 이 문자열과 provenance 파일의 `extraction_method` 가 정확히 같아야 fixture 가 로드된다. import 경로가 아니라 2026-08-02 에 fixture 를 만든 함수의 기록이다 |
| 2 | `data/development_protocols/candidate_a_curated_analysis.provenance.json` | 위와 같은 값 (`extraction_method`) | 바꾸면 기록을 고쳐 쓰는 것이 된다. 1번과 짝이다 |
| 3 | `moss_retrieval.py:256,431` | `"voice_workflow_agent_key"` | 외부 Moss 색인에 저장되는 메타데이터 필드 이름이다. 바꾸면 이미 올린 색인 항목과 맞지 않는다 |
| 4 | `scripts/sync_moss_index.py:82` | 〃 | 동기화가 기존 색인 항목의 이 필드를 비교한다 |
| 5 | `tests/test_moss_retrieval.py:223,404` | 〃 | 위 필드를 확인하는 테스트다 |
| 6 | `.env.example:70` (`docs/MOSS_RETRIEVAL.md:65,179` 도 같은 값) | `MOSS_INDEX_NAME=voice_workflow_agent-approved-safety` (옛 설정; 줄 CL, 2026-10-10 에 Moss 연결과 함께 삭제) | 외부 서비스에 이미 있는 색인 이름이다 |
| 7 | `worker.py:171`, `.env.example:21` | `voice_workflow_agent@example.invalid` | 알림 메일 발신 주소의 기본값이다. 밖으로 나가는 값이라 바꾸면 동작이 바뀐다 |

**저장소 안에 저장된 데이터를 확인한 결과 (바꿔도 기존 기록을 읽을 수 있다는 근거):**

- `data/runtime/` 전체 (sqlite, 객체 저장소, 백업 포함): 패키지 이름 0건.
- `data/development_cache/`: 0건.
- `_CANONICAL_SCHEMA_SHA256` 의 입력(`ANALYSIS_RESPONSE_SCHEMA` 의 정규 JSON 바이트): 패키지 이름 0건. 해시는
  지금 값 그대로 맞는다.
- 저장되는 분석 결과는 클래스를 **짧은 이름으로만** 적는다 (`{"$type": "ClassName"}`,
  `experiment_protocol_store.py:356-430`). 모듈 경로가 들어가지 않는다.
- `pickle`, `shelve`, `marshal`, `__reduce__`, `__module__` 을 쓰는 곳: `src/`, `scripts/` 에 0건.
- DB 에 저장되는 이벤트 이름·스키마 이름 중 패키지 이름이 든 것: 0건 (B-1 전수 목록에 없다).

**범위 밖이라 바꾸지 않는 비슷한 이름 (소문자 `voice_workflow_agent` 가 아님, 기록용):**

- `VOINEY_LAB_*` 환경변수 전부 (지시 규칙).
- `external_references.py:549` 의 `"prompt_cache_key": "voice-workflow-agent-grok46-v1"`: xAI 쪽 프롬프트 캐시
  키라 바꾸면 캐시가 무효가 된다.
- `brain.py` 의 페르소나 이름 "Voice Workflow Agent": 모델에게 보내는 문구이고 `test_brain.py:20` 이 확인한다.
- `approved_safety_manual.demo.json` 의 `source_label`, OIDC audience 예시 `voice-workflow-agent`,
  `package.json` 의 `"name": "voice-workflow-agent-e2e"`, 명령 이름 `voice-workflow-replay` /
  `voice-workflow-evaluate` (Q2), 배포 디렉터리 예시 `/opt/voice-workflow-agent`.

### B-3. 옛 이름으로 설치된 흔적

| 위치 | 내용 |
|---|---|
| `.venv/lib/python3.14/site-packages/__editable__.voice_workflow_agent-0.1.0.pth` | 한 줄: `/home/ubuntu/voice-workflow-agent/src`. **경로 방식의 편집 설치**다 |
| `.venv/lib/python3.14/site-packages/voice_workflow_agent-0.1.0.dist-info/` | entry point 가 `voice_workflow_agent.replay_turns:main`, `voice_workflow_agent.voice_evaluation:main` 을 가리킨다 |
| `.venv/bin/voice-workflow-replay`, `.venv/bin/voice-workflow-evaluate` | 위 entry point 의 실행 파일 |
| `src/voice_workflow_agent.egg-info/` | git 밖 (`.gitignore:7`), 2026-08-25 생성 |
| `src/voice_workflow_agent/__pycache__/` | git 밖. `git mv` 할 때 디렉터리와 함께 옮겨진다 |
| 사용자 site-packages, 다른 venv | 찾지 못했다 |

`.pth` 가 `src/` 를 통째로 경로에 넣으므로, `git mv` 직후부터 `import voiney_lab` 은 되고
`import voice_workflow_agent` 는 실패한다. 다시 설치하기 전까지는 명령 두 개와 배포 정보가 옛 이름으로
남는다. 코드는 `importlib.metadata` 를 쓰지 않는다.

---

## 3. 조사 C — 목표 데이터 구조로 옮길 때의 영향

### 제안한 구조와 조사 결과 대조

| 목표 | 조사 결과 | 제안 |
|---|---|---|
| `data/samples/` 공개 라이선스 테스트 PDF | 저장소에 공개 라이선스 PDF 가 **하나도 없다.** PDF 가 필요한 테스트는 합성 PDF 를 임시로 만들거나 라이선스 PDF 를 쓴다 | 지금은 만들지 않는다 (git 은 빈 디렉터리를 추적하지 않는다). 첫 공개 PDF 를 넣을 때 라이선스 기록과 함께 만든다 |
| `data/fixtures/` 수작업 정답·매니페스트 | 해당하는 파일 9개. 그중 매니페스트 3개는 자기 경로를 내용에 담고 있다 (A-5) | 자기 경로가 없는 5+1+1+1 = 8개만 옮긴다 |
| `data/cache/` 분석 캐시·정확도 결과 | `data/development_cache/` 가 해당한다. 하지만 git 밖이고 설정 확인이 안 된다 | 이번에는 옮기지 않는다 (Q10, 판단 불가) |
| `data/runtime/` 실행 데이터·파일럿 증거·라이선스 PDF | 이미 그렇게 쓰인다. 다만 라이선스 PDF 2개는 지금 저장소 루트를 가리킨다 | 옮기지 않는다 (지시). 루트 PDF 2개를 `data/runtime/` 아래로 모으는 일은 그 파일을 가진 사람과 따로 한다 (§8 판단 불가) |

구조를 두 곳 조정하자고 제안한다.

1. **`tests/fixtures/` 는 테스트 전용 fixture 자리로 남긴다.** pytest 관례대로 테스트 옆에 두는 편이 찾기
   쉽다. 여기서는 테스트가 쓰지 않는 평가 파일 하나만 `data/fixtures/evaluation/` 으로 옮긴다 (Q8).
2. **자기 경로를 담은 데모 매니페스트(`data/moss_demo/`, `data/procedure_demo/`)는 제자리에 둔다.** 둘 다
   조사 D 에서 "지금 방향과 관계없는 묶음"(MOSS, 옛 절차 경로)에 속한다. 그 모듈을 나눌 때 내용과 함께 한
   번에 옮기는 편이 맞다.

### 옮길 파일마다: 새 위치, 바꿀 코드, 위험

| 옮길 파일 | 새 위치 | 바꿀 곳 | 위험 | 확인 방법 |
|---|---|---|---|---|
| `data/development_protocols/` 5개 (한 묶음) | `data/fixtures/development_protocols/` | 27개 중 26개 파일, 47줄 (A-2 표) | 경로 하나를 놓치면 테스트가 로드 실패로 **시끄럽게** 깨진다. 단 CI (조건 B) 에서는 이 테스트 대부분이 건너뛰어지므로 **이 트리에서만** 확인된다. `test_ui_data_provenance.py:209` 의 glob 은 정확한 목록과 비교하므로 조용히 통과하지 않는다 | pytest 기준 일치. `fixture_sha256` 재계산. 새 경로로 `load_curated_protocol_fixture` 호출 (읽기 전용) |
| `data/evaluation/candidate_a_real_voice_hardening.json` | `data/fixtures/evaluation/` | `scripts/evaluate_candidate_a_hardening.py:47` | pytest 가 이 스크립트를 부르지 않는다 | 스크립트 출력이 0-3 기준과 같은지 비교 (지연 시간 필드는 제외) |
| `tests/fixtures/candidate_a_grounded_voice_eval.json` (Q8) | `data/fixtures/evaluation/` | `scripts/evaluate_candidate_a_grounded_qa.py:44` | 위와 같다 | 위와 같다 |
| `data/approved_safety_manual.demo.json` | `data/fixtures/approved_safety_manual.demo.json` | `safety_pack.py:494` (2단계 뒤 경로는 `src/voiney_lab/safety_pack.py`) | 경로가 틀리면 데모 안전팩이 **조용히** 비어 버린다. `test_17c` 가 잡는다 (이 트리에서 실행됨) | pytest 기준 일치 |

네 경우 모두 `data/runtime/` 의 저장소에는 옛 경로가 기록돼 있지 않다 (바이트 검색 0건). 옮겨도
파일럿 기록에는 영향이 없다.

---

## 4. 조사 D — 이번에 하지 않을 구조 문제 (기록만)

모듈 57개, 59,517줄이 `src/voice_workflow_agent/` 한 폴더에 있다. 가장 큰 두 파일은
`curated_protocol.py` 10,270줄과 `server.py` 9,200줄이다. `server.py` 는 다른 모듈 56개 중 37개를 import 하는
중심이다. 아래는 모듈 docstring 과 import 관계로 묶어 본 **후보**다. 코드는 건드리지 않았다.

| 묶음 후보 | 모듈 | 줄 수 | 지금 방향(음성 에이전트 파일럿)과의 관계 |
|---|---|---:|---|
| `voice` 음성 입출력 | audio, vad, configuration, cascade_filler, language, emergency, voice_evaluation | 1,665 | 핵심 |
| `dialogue` 의도·라우팅·LLM 역할 | intent_arbitration, completion_intent, semantic_intent, runtime_routing, brain, multi_brain, tools | 4,619 | 핵심 |
| `protocol_runtime` 실행 상태기계 | curated_protocol, replay_turns | 10,575 | 핵심. `curated_protocol.py` 는 분리 대상 1순위 |
| `protocol_ingest` PDF → 구조화 분석·카탈로그 | experiment_protocol, experiment_protocol_pdf, pdf_text_worker, experiment_protocol_analysis, experiment_protocol_store, experiment_protocol_files, experiment_protocol_config, protocol_catalog, protocol_chunk_analysis, protocol_claim_analysis, protocol_ocr, chunk_analysis_cache | 16,235 | 필요 (프로토콜 등록·분석). `experiment_protocol_pdf` 는 13개 모듈이 쓰는 바닥층이다 |
| `protocol_diagnostics` 개발용 진단·채점 | claim_contract_audit, protocol_claim_semantic_audit, protocol_claim_stream_telemetry, protocol_extraction_accuracy, protocol_provider_diagnostics | 2,909 | 개발 도구. 서버 실행 경로에서 import 되지 않는다 (telemetry 만 diagnostics 가 씀) |
| `safety` 승인 안전자료 (SOP·SDS 문서 묶음) | safety_pack, safety_documents, document_store, retrieval, moss_retrieval, notifications, worker | 2,488 | 부분. `moss_retrieval` 은 선택 기능 |
| `workspace` 테넌트·권한·기록 | workspace_store, identity, experiment_reports, report_projection, runtime_metrics | 6,694 | 부분 |
| `integrations` 외부 연동 | protocol_sources (protocols.io·Drive·GitHub), drylab_workflows, eln_connectors | 1,252 | 지금 방향과 관계없는 후보 |
| `research_visuals` 외부 참고자료·이미지 | external_references, web_visuals, generated_visuals | 2,259 | 기능 플래그로 켜는 부가 기능 (docstring: "Feature-gated") |
| `legacy_procedures` 설정으로 꺼진 옛 절차 경로 | procedures, procedure_definitions, procedure_store | 1,443 | 관계없음. CLAUDE.md 가 "격리된 경로, 확장 금지"라고 정했다 |
| `server` HTTP/WebSocket 앱 | server, protocol (M4 오디오 JSON 헬퍼) | 9,375 | 분리 대상. API 묶음별로 나눌 수 있다 (`/api/protocols`, `/api/workspace/*`, admin, 음성 WebSocket, STT/TTS 클라이언트) |

나눌 때 먼저 풀어야 할 결합:

- `experiment_reports.py:862` 가 함수 안에서 `server` 를 import 한다 (`server` 도 `experiment_reports` 를
  import 한다). 모듈 수준의 순환은 아니지만 방향이 거꾸로 된 의존이다.
- 서브패키지로 나누면 모듈 경로가 또 바뀌어 `mock.patch` 문자열 432건을 다시 고쳐야 한다. 이번 이름 변경과
  섞지 말고, 끝난 뒤 따로 여러 커밋으로 하는 편이 안전하다.

---

## 5. 2~4단계 계획

각 단계는 커밋 하나다. 각 커밋 뒤에 [검증]을 돌린다. 통과 수가 0-2 기준과 다르거나 확인 하나라도
실패하면 멈추고 보고한다. 커밋은 모두 `feature/jaejun-restructure` 에만 하고 push 는 하지 않는다.

**[검증]** (지시문 그대로, 매 커밋 뒤)

1. 세 플래그를 끈 `python -m pytest -q` → `1513 passed, 13 skipped, 1283 subtests passed`
2. `python scripts/replay_turns.py` exit 0, `python -m compileall -q src tests scripts` exit 0
3. `git diff --check` 깨끗함
4. (추가) `data/runtime/` 하위 32개 항목의 목록·크기·수정 시각이 0-4 와 같음

### 2단계 — 패키지 이름 변경 (커밋 1개)

바꾸는 경로 **188개**: 이름만 바뀌는 파일 60개(`src/voice_workflow_agent/` 의 추적 파일, 그중 28개는
내용도 바뀜), 테스트 106개, `scripts/` 21개 (`.sh` 2개 포함), `pyproject.toml`.

1. `git mv src/voice_workflow_agent src/voiney_lab`
2. B-1 의 1,154건을 `voiney_lab` 으로 바꾼다. B-2 의 10건은 줄 번호가 아니라 **내용으로** 찾아서 제외한다.
   Q9 가 승인되면 B-2 두 곳에 영어 주석 한 줄씩을 단다.
3. `pyproject.toml`: entry point 대상 2곳, package-data 키, (Q2) `name = "voiney-lab"`.
   (Q2) `moss_retrieval.py:372` 안내 문구의 `voice-workflow-agent[moss]` → `voiney-lab[moss]`.
   다시 설치: `pip uninstall -y voice-workflow-agent` → (Q3) `rm -rf src/voice_workflow_agent.egg-info` →
   `pip install -e '.[test]'`. 네트워크가 없으면 `--no-build-isolation` 으로 설치한다 (`.venv` 에
   setuptools 84.0.0 이 있다).
4. 확인 (Q1 에 따라 수정한 규칙)
   - `python -c "import voice_workflow_agent"` 가 **실패**한다. 저장소 루트와 `/tmp` 두 곳에서 실행한다.
   - `python -c "import voiney_lab.server"` 가 성공한다 (세 플래그를 끈 상태).
   - `voice-workflow-replay` exit 0, `voice-workflow-evaluate --help` exit 0.
   - `git diff --cached -M --name-status` 에서 패키지 파일 60개가 모두 R(이름 변경)로 보인다
     (`git log --follow` 가 이어진다는 근거).
   - 소문자 `voice_workflow_agent` 를 다시 센다. **남아도 되는 곳은 정확히 39건이다:** B-2 10건,
     현행 문서 27건 (4단계 대상 9개 파일), `docs/course-archive/README.md` 2건. 그 밖에 하나라도 있으면
     멈춘다.
   - (Q4) 서버 기동 확인: `run_ci_server.sh` 와 같은 환경변수를 쓰되 데이터 디렉터리만 scratchpad 로
     바꾸고, `python -m uvicorn voiney_lab.server:app --port 8123` 을 띄운다. `/healthz`, `/readyz` 가
     200 인지 본 뒤 끈다. 두 실행 스크립트의 `모듈:app` 문자열은 pytest 가 확인하지 않아서 넣은 확인이다.
5. [검증]

커밋 메시지: `Rename the Python package voice_workflow_agent to voiney_lab`

### 3단계 — 데이터 재배치 (커밋 1개)

바꾸는 경로 **35개**: `git mv` 8개, 참조 수정 27개 (A-2 표의 파일들).

1. `git mv` 할 것 (§8 "옮겨도 안전한 것"만):
   - `data/development_protocols/` 5개 → `data/fixtures/development_protocols/`
   - `data/evaluation/candidate_a_real_voice_hardening.json` → `data/fixtures/evaluation/`
   - (Q8) `tests/fixtures/candidate_a_grounded_voice_eval.json` → `data/fixtures/evaluation/`
   - `data/approved_safety_manual.demo.json` → `data/fixtures/approved_safety_manual.demo.json`
2. 참조 27개 파일을 고친다. `.gitignore` 는 고칠 곳이 없다 (git 밖 경로는 옮기지 않는다).
3. 해시 확인
   - 8개 파일의 `sha256sum` 이 옮기기 전과 같다. `git diff --cached -M --name-status` 에서 8개 모두 `R100`.
   - provenance 의 `fixture_sha256` (`69517f0f…`) 이 옮긴 `candidate_a_curated_analysis.json` 의
     sha256 과 같다. 사이드카 3개의 `fixture_sha256`, `document_sha256` 이 바뀌지 않았다.
   - 새 경로로 `load_curated_protocol_fixture(...)` 가 성공한다 (읽기 전용이며 `data/runtime` 에 쓰지 않는다).
     `run_candidate_a.sh --bootstrap-only` 는 파일럿 저장소에 쓰므로 **쓰지 않는다.**
   - 평가 스크립트 두 개의 출력이 0-3 과 같다 (지연 시간 필드 제외).
4. [검증]

커밋 메시지: `Move the hand-curated fixtures under data/fixtures`

### 4단계 — 문서 갱신 (커밋 1개)

바꾸는 경로 **6개**, 13곳과 이 계획서의 결과 기록.

| 파일 | 줄 | 바꿀 것 |
|---|---|---|
| `README.md` | 3 | CI 배지 URL 2곳 → `github.com/jaeiko/voiney-lab` |
| 〃 | 5 | 저장소 주소 2곳 → `jaeiko/voiney-lab` |
| 〃 | 73, 185 | `src/voice_workflow_agent/...` → `src/voiney_lab/...` |
| 〃 | 501, 508, 628 | 실행 명령 `uvicorn voiney_lab.server:app`, `python -m voiney_lab.worker`, `python -m voiney_lab.replay_turns` |
| `CLAUDE.md` | 71 | `src/voiney_lab/procedures.py` |
| `AGENTS.md` | 31 | `src/voiney_lab/server.py` |
| `docs/DEPLOYMENT_RUNBOOK.md` | 26 | systemd 예시의 `voiney_lab.server:app` |
| `docs/VOICE_FIELD_EVALUATION_PLAN.md` | 27 | `python -m voiney_lab.voice_evaluation` |
| `docs/RESTRUCTURE_PLAN.md` | — | 상태를 "실행 완료"로 바꾸고 결과를 적는다 |

- 3단계에서 옮긴 데이터 경로를 가리키는 현행 문서는 없다. 가리키는 곳은 날짜가 박힌 기록뿐이다 (Q6, §6).
- `.agent/*.md` 에는 바꿀 곳이 없다 (0건). 루트 `GIT_WORKFLOW.md` 에도 없다 (Q7).
- 4-2 확인: 현행 문서(README, CLAUDE, AGENTS, `.agent/*.md`, `docs/*.md`)의 경로·모듈·명령을 모두 뽑아서
  있는지 검사하는 스크립트를 돌린다 (scratchpad 에 두고 커밋하지 않는다). 통과 조건은 두 가지다. 없는
  곳이 0-3 의 기존 23곳 안에만 있어야 하고, 이번에 고친 줄이 가리키는 것은 모두 있어야 한다.
- 4단계 뒤 소문자 `voice_workflow_agent` 가 남아도 되는 곳은 **정확히 30건이다:** B-2 10건,
  `docs/course-archive/` 2건, 날짜가 박힌 기록 18건 (`ANALYSIS_2026_09_11.md` 9,
  `PROTOCOL_BOUNDARY_AND_OBLIGATION_DESIGN.md` 5, `PILOT_READINESS_PACKAGE.md` 2, `MOSS_RETRIEVAL.md` 의
  B-2 색인 이름 2).
- [검증]

커밋 메시지: `Point the current docs at voiney_lab and jaeiko/voiney-lab`

---

## 6. 바꾸지 않을 목록

| 대상 | 이유 |
|---|---|
| B-2 의 10건 (§2) | 저장되거나 밖으로 나가는 식별자 |
| `VOINEY_LAB_*` 환경변수 | 지시 규칙. 서버 `.env` 와 테스트 명령이 이 이름에 기대고 있다 |
| in-gel PDF 의 파일 이름 `in-gel-digestion.pdf` | provenance 가 파일 이름을 고정한다 (A-5) |
| `data/runtime/**` | 지시 규칙 |
| `data/moss_demo/`, `data/procedure_demo/`, `tests/fixtures/fictional_ingestion_manifest.json` | 자기 경로가 내용에 들어 있다. MOSS 데모의 경로는 `data/runtime/moss_demo_catalog.sqlite` 에도 저장돼 있다 |
| `data/development_cache/` | 판단 불가 (Q10) |
| 루트의 라이선스 PDF 경로 (지금 없음) | 판단 불가 (§8) |
| `docs/archive/`, `docs/course-archive/` | CLAUDE.md |
| `docs/ANALYSIS_2026_09_11.md` 전체 | `bfc292b` 에서 잰 날짜 있는 기록이다 (Q6) |
| `docs/PROTOCOL_BOUNDARY_AND_OBLIGATION_DESIGN.md:3134, 3675-3680` | 특정 커밋의 diffstat 과 측정 기록 (Q6) |
| `docs/PILOT_READINESS_PACKAGE.md:161-162` | 옛 모듈 경로로 한 실제 xAI 호출 기록이다. 바꾸면 기록이 사실과 달라진다 (CLAUDE.md "No fake external validation") |
| `docs/MOSS_RETRIEVAL.md:65,179` | B-2 의 색인 이름 |
| 명령 이름 `voice-workflow-replay`, `voice-workflow-evaluate`, `package.json` 의 이름 | 이번 범위 밖의 인터페이스 (Q2) |
| 루트 `GIT_WORKFLOW.md` | 사람이 만든 추적 안 된 파일 (Q7) |
| 로컬 디렉터리 이름 `/home/ubuntu/voice-workflow-agent` | 범위 밖. `.pth` 와 도구 설정이 이 경로를 쓴다 |

---

## 7. 위험과 되돌리는 방법

| 위험 | 가능성 | 대응 |
|---|---|---|
| 문자열 치환이 B-2 를 건드림 | 낮음 | 내용으로 제외하고, 치환 뒤 남은 건수를 정확히 39건으로 확인한다 (2-4) |
| 패치 대상 문자열 하나를 놓침 | 낮음 | 남은 건수 확인과 pytest 로 잡힌다 (patch 대상이 없으면 `AttributeError`/`ModuleNotFoundError`) |
| CI 에서는 안 잡히는 누락 | 중간 | CI 는 조건 B 라 fixture 를 쓰는 테스트 대부분을 건너뛴다. 그래서 이 트리(in-gel PDF 있음)에서 검증한다 |
| 실행 스크립트의 `모듈:app` 문자열 오류 | 낮음 | 서버 기동 확인 (2-4, Q4) |
| 저장소 밖에서 옛 이름을 쓰는 곳 (파일럿 서버의 systemd, `.env`, 다른 clone·venv, 로그 필터, IDE 실행 설정) | 높음 (확실히 있다) | 저장소 안에서는 막을 수 없다. 최종 보고에 "사람이 할 후속 작업"으로 적는다 |
| 편집 설치가 네트워크를 요구 | 낮음 | `--no-build-isolation` |
| 데이터 이동 뒤 해시 변화 | 없음이 목표 | `R100` 과 `sha256sum` 으로 확인 (3-3) |

**되돌리는 방법:** 각 단계가 커밋 하나이므로 `git revert <커밋>` 으로 되돌린다. 순서는 4 → 3 → 2 다. 기록을
지우는 `git reset --hard` 는 쓰지 않는다 (CLAUDE.md). 2단계를 되돌리면 설치도 다시 해야 한다:
`pip uninstall -y voiney-lab && rm -rf src/voiney_lab.egg-info && pip install -e '.[test]'`.
`data/runtime/` 을 건드리지 않으므로 되돌릴 실행 데이터는 없다.

---

## 8. 결론

### 옮겨도 안전한 것 (2~4단계에서 한다)

- 패키지 디렉터리 `src/voice_workflow_agent` → `src/voiney_lab`, 그리고 B-1 의 1,154건. 근거: 저장된
  데이터와 해시 입력에 패키지 이름이 없다. B-2 10건은 제외한다.
- `data/development_protocols/` 5개 → `data/fixtures/development_protocols/` (한 묶음으로)
- `data/evaluation/candidate_a_real_voice_hardening.json` → `data/fixtures/evaluation/`
- `tests/fixtures/candidate_a_grounded_voice_eval.json` → `data/fixtures/evaluation/` (Q8)
- `data/approved_safety_manual.demo.json` → `data/fixtures/`
- 현행 문서 5개의 13곳

### 옮기면 깨지는 것 (건드리지 않는다)

- B-2 10건. 바꾸면 fixture 로드가 실패하고, MOSS 색인이 어긋나고, 알림 발신 주소가 바뀐다.
- `data/moss_demo/approved_documents.ko.json`: 자기 경로가 내용에 있고 실행 카탈로그에도 저장돼 있다.
- `data/procedure_demo/*.json`, `tests/fixtures/fictional_ingestion_manifest.json`: 자기 경로가 내용에 있다.
- in-gel PDF 의 파일 이름.

### 판단 불가 (2~4단계에서 건드리지 않는다)

- `data/development_cache/` → `data/cache/`. git 밖이라 `git mv` 를 쓸 수 없다. `.env` 의
  `VOINEY_LAB_CHUNK_CACHE_DIR` 를 확인할 수 없다. 캐시 안 4개 파일이 자기 경로를 기록하고,
  `test_chunk_analysis_cache.py:436` 이 이름을 고정한다. 캐시를 못 찾으면 provider 비용이 다시 든다.
- 루트의 라이선스 PDF 2개를 `data/runtime/` 아래로 옮기는 일. 파일이 없어서 테스트가 어느 쪽이든
  건너뛰므로, 이 트리에서는 맞게 고쳤는지 확인할 수 없다.
- 추적 중인 fixture 에 든 원문 발췌의 라이선스 (A-4).
- `.env` 가 바뀌는 경로를 가리키는지 (Q5). 이것은 옮길지 말지가 아니라 사람이 할 후속 작업의 범위를 정한다.

---

## 9. 조사 중 발견했지만 고치지 않은 문제

1. **[검증] 명령이 `data/runtime/` 에 잠깐 쓴다.** `tests/test_candidate_a_final_hardening.py:72` 가
   `data/runtime` 아래 임시 디렉터리를 만들었다 지운다. 테스트 변경이라 범위 밖이다.
2. **CI 의 평가 단계가 실행되지 않는다.** `.github/workflows/ci.yml:40` 이 옛 경로
   `/home/student/protocol-test-files/in-gel-digestion.pdf` 를 확인한다. 지금 원본은
   `data/runtime/candidate-a-source/` 에 있다. 같은 단계의 주석(`ci.yml:45`)은 archive 로 옮겨진 문서를 가리킨다.
3. **CI 가 팀 브랜치에서 돌지 않는다.** `ci.yml:4-7` 은 `main`, `refactor/**` 의 push 와 `main` 으로 가는
   PR 에서만 돈다. `GIT_WORKFLOW.md` 가 쓰는 `dev`, `feature/*`, `fix/*` 와 `dev` 로 가는 PR 에서는 돌지 않는다.
4. **CLAUDE.md 와 `GIT_WORKFLOW.md` 의 분기 기준이 다르다** (`main` 대 `dev`). 위 "실행 전에 물은 것" 참고.
5. **현행 문서의 끊어진 참조 23곳** (이번 작업 전부터 있음). `AGENTS.md:80-91` 의 "Documentation
   authority" 는 archive 로 옮겨진 문서 6개를 아직 권위 문서로 적고 있다. `README.md:89,778`,
   `PILOT_READINESS_PACKAGE.md:23,131,167`, `PROTOCOL_BOUNDARY_AND_OBLIGATION_DESIGN.md:64` 도 옮겨진
   문서를 가리킨다. `CLAUDE.md:48` 의 `docs/demo_script.md` 는 사용자 소유 파일이라 없을 수 있다. 나머지
   (`processed.txt`, `Next.js` 등)는 검사 스크립트가 경로로 잘못 본 예시 문구다.
6. **작업 디렉터리에 기대는 경로.** `chunk_analysis_cache.py:61` 의 기본 캐시 경로가 상대 경로라, 서버를
   다른 디렉터리에서 켜면 캐시가 다른 곳에 생긴다. A-2 의 C 로 표기한 테스트들은 저장소 루트에서만 맞다.
7. `tests/test_safety_pack.py:40` 이 만든 적 없는 파일
   (`data/development_protocols/candidate_a_source_in_gel_digestion.pdf`)을 먼저 찾고 `:42` 에서
   `data/runtime` 으로 넘어간다. 3단계에서 경로 문자열만 새 위치로 바꾼다.
8. `playwright.ci.config.ts` 는 포트 8000 을 고정했다 (`VOINEY_LAB_PLAYWRIGHT_APP_PORT` 를 읽지 않는다). 기존
   Playwright 설정 두 개는 모두 `data/runtime/` 에 쓴다 (Q4).
9. README 의 조건 A 수치는 라이선스 PDF 4개를 전제로 한다. 이 트리에는 2개뿐이라 재현되지 않는다 (0-2).
   버그가 아니라 환경 차이다.
10. `experiment_reports.py:862` 가 `server` 를 거꾸로 import 한다 (§4).
11. 작업 지시문이 가리킨 `docs/GIT_WORKFLOW.md` 는 없다. 실제 파일은 루트에 있고 추적되지 않는다 (Q7).

---

## 10. 실행 결과 (2~5단계)

### 10-1. 사람의 결정 (2026-09-29)

- Q1~Q4, Q6, Q8~Q10: 권장안대로.
- Q5: 사람이 확인했다. `.env` 는 바뀌는 경로를 가리키지 않는다.
- Q7: 5단계를 새로 두어 처리했다.
- 분기 기준은 `dev` 다. `docs/GIT_WORKFLOW.md` 의 규칙을 따른다.
- 루트의 라이선스 PDF 2개(intracellular, headspace)는 서버에서도 찾지 못했다. 기준선은 0-2 를 그대로 쓴다.

### 10-2. 커밋

| 단계 | 커밋 | 메시지 | 바뀐 파일 | pytest |
|---|---|---|---:|---|
| 1 | `3abae05` | Record the repository restructure plan | 1 | 1513 passed, 13 skipped, 1283 subtests passed |
| 2 | `4572358` | Rename the Python package voice_workflow_agent to voiney_lab | 188 | 같음 |
| 3 | `56b0d15` | Move the hand-curated fixtures under data/fixtures | 35 | 같음 |
| 4 | `40e2f78` | Point the current docs at voiney_lab and jaeiko/voiney-lab | 5 | 같음 |
| 5 | 이 커밋 | Adopt the dev-based branch workflow and align agent docs | 5 | 같음 |

모든 커밋 뒤에 `python scripts/replay_turns.py` exit 0, `python -m compileall -q src tests scripts` exit 0,
`git diff --check` 깨끗함을 확인했다. `data/runtime/` 하위 32개 항목도 목록·크기·수정 시각이 모두
그대로였다. 커밋은 로컬 브랜치에만 있고 push 하지 않았다.

### 10-3. §5 확인 결과

**2단계**

- `git mv` 뒤 옛 디렉터리는 남지 않았다 (`__pycache__` 까지 함께 옮겨졌다). 옛 디렉터리가 남았다면
  `src/` 가 경로에 있으므로 이름공간 패키지로 import 되었을 것이다.
- 156개 파일에서 1,154건을 바꿨다 (계획과 같음). B-2 는 줄 번호가 아니라 내용으로 제외했고, 코드 쪽
  7줄이 그대로 남은 것을 출력으로 확인했다.
- `python -c "import voice_workflow_agent"`: 저장소 루트와 `/tmp` 모두 `ModuleNotFoundError`, exit 1.
- `import voiney_lab.server`: 성공. `voice-workflow-replay` exit 0, `voice-workflow-evaluate --help` exit 0.
- 패키지 파일 60개가 모두 이름 변경으로 잡혔다 (R100 32개, R097~R099 28개). `git log --follow` 가 이어진다.
- 남은 옛 이름은 **정확히 39건**이었다 (B-2 10, 현행 문서 27, `docs/course-archive/` 2). 이 계획서 안의
  31건은 따로 셌다. 계획서는 이름 변경 자체를 기록하는 문서라 옛 이름을 담고, 39건 규칙을 정할 때는
  아직 없었다.
- 재설치: `pip uninstall -y voice-workflow-agent`, 옛 egg-info 삭제, `pip install -e '.[test]'`.
  build isolation 을 쓴 기본 설치가 그대로 성공해서 `--no-build-isolation` 은 필요 없었다. 결과는
  `voiney-lab 0.1.0`, `.venv` 의 `__editable__.voiney_lab-0.1.0.pth`, `src/voiney_lab.egg-info/` 다.
  옛 배포 정보는 남지 않았다.
- 서버 기동 확인: `run_ci_server.sh` 와 같은 환경변수에 데이터 디렉터리만 scratchpad 로 바꿔
  `python -m uvicorn voiney_lab.server:app --port 8123` 을 띄웠다. `/healthz` 200, `/readyz` 200
  (workspace·protocol catalog·experiment reports 켜짐, MOSS 꺼짐), `/` 200 (static 파일도 새 패키지에서
  나온다).
- `replay_turns.py` 출력이 기준과 같았다 (지연 시간 줄 제외).

**3단계**

- `git mv` 8개가 모두 `R100` 이고 sha256 도 옮기기 전과 같다.
- provenance 의 `fixture_sha256` (`69517f0f…`) 이 옮긴 fixture 와 일치한다. 사이드카 3개의
  `fixture_sha256` 도 일치하고, `document_sha256` 은 모두 `63d81102…` 그대로다.
- 새 경로(`data/fixtures/development_protocols/`)로 `load_curated_protocol_fixture` 를 부르면 성공한다:
  `candidate-a-curated-development-v1`, 25단계.
- 평가 스크립트 두 개의 출력이 0-3 과 같다 (지연 시간 필드 제외). `evaluate_candidate_a_hardening.py` 의
  exit 1 은 기준과 같은 기존 실패 2건이다.
- 참조는 27개 파일 51곳을 고쳤다. 옛 위치를 가리키는 코드·테스트·스크립트는 0곳이다.
- 파일을 옮긴 뒤 비어 버린 옛 평가 디렉터리(`data` 아래 `evaluation`)는 `rmdir` 로 지웠다. Q8 로 옮긴 뒤
  `tests/fixtures/` 에는 `fictional_ingestion_manifest.json` 하나만 남았다.

**4단계**

- 5개 파일 13곳을 고쳤다. 고친 줄이 가리키는 경로·모듈·명령은 모두 있다.
  `python -m voiney_lab.replay_turns` exit 0, `python -m voiney_lab.voice_evaluation --help` exit 0.
  `git ls-remote https://github.com/jaeiko/voiney-lab.git` 가 응답했다 (exit 0).
- 새로 끊어진 참조는 Q6 로 남긴 날짜 기록(`ANALYSIS_2026_09_11.md` 9곳,
  `PROTOCOL_BOUNDARY_AND_OBLIGATION_DESIGN.md` 6곳, `PILOT_READINESS_PACKAGE.md` 2곳)과 이 계획서
  §0~§9 에만 있다.
- 남은 옛 이름은 **정확히 30건**이다 (10-5).

**5단계**

- 루트의 `GIT_WORKFLOW.md` 를 `docs/GIT_WORKFLOW.md` 로 옮기고 `git add` 했다. sha256 (`476635fc…`)
  이 옮기기 전과 같다.
- `CLAUDE.md` 의 Branch workflow: `dev` 에서 분기하고 `dev` 로 PR 한다. `main` 은 파일럿 배포용이며
  `dev` 에서만 합친다. 자세한 규칙은 `docs/GIT_WORKFLOW.md` 를 가리킨다.
- `AGENTS.md` 의 Documentation authority: `docs/archive/` 로 옮겨진 문서 6개를 빼고 `CLAUDE.md` 의
  목록과 같게 맞췄다 (`README.md` → `AGENTS.md` 와 `.agent/*.md` → `docs/CURRENT_ARCHITECTURE.md` →
  `docs/MIGRATION_NOTES.md`). 새 문서를 지목하지 않았다.
- `README.md:89`: 옮겨진 단계별 증거 문서를 가리키던 구절을 지우고, 같은 문장에 있던
  `docs/MIGRATION_NOTES.md` 안내만 남겼다. 그 역할을 이어받은 현행 문서는 없다.
- `README.md:778`: 옮겨진 PASS4 보고서 참조를 지우고, 같은 문장에 이미 있던 `docs/CAPABILITY_MATRIX.md`
  만 남겼다. 이 문서가 연동별 분류(Contract-tested, Live-tested historically 등)를 담고 있다.
- 5-6 확인: 0-3 의 끊어진 참조 23곳 중 10곳이 해결됐다 (`AGENTS.md` 6, `README.md` 4). 이번에 고친
  문서(`README.md`, `CLAUDE.md`, `AGENTS.md`, `docs/GIT_WORKFLOW.md`, 이 계획서 §10)에 새로 끊어진
  참조는 없다. 남은 것은 `CLAUDE.md:48` 한 곳인데, 사용자 소유의 추적 안 된 데모 스크립트를 가리키는
  규칙 문장이라 그대로 둔다.

### 10-4. 계획과 달랐던 점

- 4단계는 계획의 6개가 아니라 5개 파일을 바꿨다. 계획서 결과 기록은 지시에 따라 5단계로 옮겼다.
- 2-4 의 39건은 이 계획서를 빼고 센 수다 (10-3).
- 4단계에서 `git grep` 의 경로 지정 `docs/*.md` 가 `docs/archive/` 아래까지 맞아, archive 파일의 몇 줄이
  출력됐다. 그 내용은 판단에 쓰지 않았다. 1단계에서 파일 이름 목록을 출력한 것과 같은 종류의 실수다.
  이후 검색은 `docs/archive` 와 `docs/course-archive` 를 명시적으로 뺐다.

### 10-5. 남은 옛 이름 (소문자 `voice_workflow_agent`, 30건)

| 묶음 | 파일 (건수) |
|---|---|
| B-2 (10) | `.env.example` (2), `data/fixtures/development_protocols/candidate_a_curated_analysis.provenance.json` (1), `scripts/sync_moss_index.py` (1), `src/voiney_lab/curated_protocol.py` (1), `src/voiney_lab/moss_retrieval.py` (2), `src/voiney_lab/worker.py` (1), `tests/test_moss_retrieval.py` (2) |
| 날짜가 박힌 기록 (18) | `docs/ANALYSIS_2026_09_11.md` (9), `docs/PROTOCOL_BOUNDARY_AND_OBLIGATION_DESIGN.md` (5), `docs/PILOT_READINESS_PACKAGE.md` (2), `docs/MOSS_RETRIEVAL.md` (2, B-2 색인 이름) |
| 과정 기록 (2) | `docs/course-archive/README.md` |

이 밖에 `docs/archive/` 와 이 계획서가 옛 이름을 담고 있다.

### 10-6. §9 목록의 현재 상태

- 해결: §9-4 (분기 기준은 `dev`), §9-5 중 `AGENTS.md` 와 `README.md` 부분, §9-11 (`docs/GIT_WORKFLOW.md`).
- 남음: §9-1, §9-2, §9-3 (CI 는 별도 작업), §9-5 중 `PILOT_READINESS_PACKAGE.md:23,131,167` 과
  `PROTOCOL_BOUNDARY_AND_OBLIGATION_DESIGN.md:64`, §9-6, §9-7 (경로 문자열은 새 위치로 바뀌었지만 그 파일은
  여전히 없다), §9-8, §9-9, §9-10.

### 10-7. 사람이 할 후속 작업

- **파일럿 서버와 다른 체크아웃**: 코드를 받은 뒤 그 venv 에서 `pip uninstall -y voice-workflow-agent` 를
  하고, `src/` 아래 옛 이름의 egg-info 디렉터리를 지우고, `pip install -e .` 를 다시 한다. 편집 설치의
  entry point 는 다시 설치하기 전까지 옛 모듈을 가리킨다.
- **서버 실행 명령**: `uvicorn voiney_lab.server:app ...`. systemd 를 쓰면 `ExecStart` 도 바꾼다
  (`docs/DEPLOYMENT_RUNBOOK.md:26` 예시). 워커는 `python -m voiney_lab.worker`. 지금 떠 있는 서버는 옛
  모듈을 메모리에 들고 있으므로, 코드를 받은 뒤에는 새 명령으로 다시 켜야 한다.
- `scripts/run_candidate_a.sh`, `scripts/run_ci_server.sh` 는 저장소 안에서 이미 바뀌었다. 코드를 받으면
  그대로 쓸 수 있다.
- **저장소 밖에서 옛 이름을 쓰는 곳**: 로그 수집·필터가 로거 이름 `voice_workflow_agent*` 에 기대면
  `voiney_lab*` 로 바꾼다 (로그 한 줄의 형식에는 로거 이름이 없다). IDE 실행 설정, 개인 스크립트,
  cron 도 확인한다. 3단계에서 옮긴 fixture 경로를 저장소 밖에서 쓰는 곳이 있으면 `data/fixtures/` 로
  바꾼다.
- **로컬 디렉터리 이름 변경** (사람이 한다): `.pth` 파일과 `.venv` 안의 실행 파일이 저장소의 절대 경로를
  담고 있다. 디렉터리를 옮기면 venv 를 다시 만들고 편집 설치를 다시 하는 편이 안전하다.
- **push 와 PR**: 사람이 한다. PR 은 `dev` 로 연다 (`docs/GIT_WORKFLOW.md`).
- **이번에 하지 않은 것**: CI 복구 (§9-2, §9-3), `data/development_cache/` 이동, 루트 라이선스 PDF 위치,
  환경변수 접두어, 명령 이름, 추적 중인 fixture 의 원문 발췌 라이선스 확인 (A-4).
