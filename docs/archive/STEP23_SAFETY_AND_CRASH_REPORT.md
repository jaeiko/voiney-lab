# STEP 23 — 안전 결함과 프로세스 크래시 (provider 호출 0회)

작성: 2026-09-05 · 브랜치 `feature/readiness-safety-warning-gate`
추출 로직은 바꾸지 않았다. 런타임 PDF/DB 는 읽기만 했다.

---

## 0. 요구된 순서대로

### 작업 1-5 — 안전 수정이 UI 동작에 바꾼 것

**in-gel 은 이제 UI 에서 실행할 수 없다. 그리고 그것이 올바른 동작이다.**

수정 전후를 같은 서버에서 실제로 측정했다.

| | 수정 전 (STEP 22-B 측정) | 수정 후 (2026-09-05 02:41 측정) |
|---|---|---|
| 기동 로그 | `WARNING protocol.catalog.configuration unavailable` | `INFO ... visible_protocols=2 development_fixtures_enabled=True` |
| `GET /api/protocols` | **503** `protocol_catalog_unavailable` | **200**, 프로토콜 2건 |
| UI 드롭다운 | 비어 있음 | 2건 표시 (둘 다 비활성, 사유 라벨 포함) |
| in-gel `available_for_execution` | `true` (무조건) | **`false`** |
| in-gel `approval_status` | `development_only_not_final_acceptance` | `unapproved` |
| in-gel `execution_blocked_reason` | (필드 없음) | `development_activation_not_recorded` |
| `POST .../activate-development` | — | **503** (readiness 게이트가 안 열림) |
| 음성 세션 `session.start` | fixture 를 그대로 붙임 | `protocol_selection_unavailable` |

즉 UI 에서 사라졌던 드롭다운은 돌아왔고, 대신 **실행 버튼이 닫혔다.**
in-gel 의 blocker 4개 중 `unsupported_repeat_until` 2개는 사람이 열 수 있는
게이트가 아니므로(STEP 22-B D-3 참조) 이 프로토콜은 어떤 조작으로도
활성화되지 않는다. 시연을 위해 판정을 느슨하게 하지 않았다.

드롭다운은 실행 불가 항목도 **표시하되 비활성화**한다
(`static/index.html:1657`, `option.disabled = available_for_execution !== true`).
사라진 항목은 조사되지 않지만 사유가 적힌 항목은 조사되므로 그대로 두었고,
`execution_blocked_reason` 을 라벨로 노출하는 분기만 추가했다:
"개발 활성화 기록 없음 · 실행 불가" / "준비 게이트 차단 · 실행 불가" /
"운영 범위에서는 개발 활성화 불가 · 실행 불가".

### 작업 2-1 — 락이 이미 있었는가: **없었다**

패키지 전체에서 `threading.Lock` / `RLock` / `Semaphore` 를 전수 조사했다.
존재하는 락은 7개이고 그 어느 것도 pdfium 을 감싸지 않는다:

| 위치 | 무엇을 지키는가 |
|---|---|
| `moss_retrieval.py:301,492` | MOSS 런타임 상태 |
| `runtime_metrics.py:35` | 메트릭 카운터 |
| `generated_visuals.py:272`, `web_visuals.py:124` | asyncio 캐시 |
| `tools.py:43` | 실험 보고서 쓰기 |
| `protocol_catalog.py:137` | chunk run (analysis_run_id 별 64개) |
| `server.py:4773` | WebSocket 송신 |

따라서 STEP 22-B 의 진단은 수정할 필요가 없다. 락이 없는 상태에서
FastAPI 의 동기 핸들러가 AnyIO 워커 스레드풀에서 실행되었고, 2026-09-04
로그에서 브라우저 탭 2개가 같은 diff 엔드포인트를 병렬 호출했다.

---

## 1. 작업 1 — `available_for_execution` 무조건 참

### 실제로 실행한 것

**1-1 / 1-2 — 판정에서 파생시키고, usage scope 를 적용했다.**

`_candidate_catalog_dict()` 의 `"available_for_execution":True` 리터럴을 없애고
`_candidate_fixture_execution_state(fixture)` (server.py) 로 대체했다.
그 함수가 "예"라고 답하는 경로는 하나뿐이다:

1. `_development_activation_allowed()` — usage scope 가
   `{demo, reference_only, test_only}` 중 하나여야 한다. **이 검사가 이 경로에
   없었다.** 미설정 포함 그 외 전부 거부.
2. protocol store 가 활성화되어 있어야 한다. 비활성이면 활성화를 기록할 원장이
   없으므로 거부(`protocol_store_disabled`).
3. store 가 **이 fixture 를** materialize 하고 있어야 한다
   (작업 3 의 정체성 검사를 재사용).
4. 그 catalog entry 의 `available_for_execution` 이 참이어야 한다. 이것은
   `_is_approved`(기록된 개발 활성화) **그리고** `execution_ready`(readiness 가
   guidance_ready 이거나 모든 blocker 를 사람이 해제) 를 둘 다 요구한다.

읽지 못한 것은 전부 "아니오"다. 카탈로그를 열 수 없으면
`protocol_catalog_unavailable` 로 거부한다 (불변 제약 7).

**더 큰 구멍이 하나 더 있었고, 같이 막았다.**
카탈로그 dict 는 UI 표시용이다. 실제 실행은 WebSocket `session.start` 가
결정하는데, 그 분기(server.py 8239 부근)는

```python
if curated_fixture.protocol_id == requested_protocol_id:
    selected_curated_fixture = curated_fixture   # readiness 검사 없음
```

이었다. 카탈로그 분기(`entry.available_for_execution` 확인)는
`selected_curated_fixture is None` 일 때만 도달하므로 **한 번도 실행되지 않았다.**
이제 이 분기도 같은 질문을 하고 같은 조건으로 거부한다.

부수적으로 거부 사유의 정직성도 고쳤다: 마지막 fallback 이
이미 기록된 `selection_failure` 를 덮어써서, 서버가 알고 있고 실행을 거부한
프로토콜이 스스로를 `protocol_selection_unknown`(모르는 프로토콜)이라고
보고하고 있었다. 이제 상류의 거부 사유가 살아남는다.

**1-3 — 명시적이고 기록되는 개발 활성화만 남겼다.**

- `activate_development()` 이 이제 `actor_principal_id` / `actor_role` /
  `comment` 를 원장 payload 에 기록한다. 이전에는 `decision` / `authority` /
  `readiness` 만 있었고 **누가 했는지가 없었다.**
- 엔드포인트 `POST /api/protocols/{id}/activate-development` 가
  `_development_activation_actor()` 로 주체를 확정한다. workspace 가 설정된
  환경에서는 principal 이 필수이고 `Permission.PROTOCOL_REVIEW` 를 요구한다.
  workspace 가 없는 단일 운용자 개발 호스트에서는 주체를 **지어내지 않고**
  미기록으로 남긴다.
- 철회 경로를 새로 만들었다: `deactivate_development()` +
  `POST /api/protocols/{id}/deactivate-development`, 이벤트
  `protocol_development_deactivated`.
- `_is_approved()` 가 개발 이벤트를 **순서대로 읽어 마지막 것만** 인정하도록
  바꿨다. 이전에는 `any(...)` 였으므로 철회해도 활성화가 계속 유효했을 것이다.
  서비스 승인(`_APPROVAL_EVENT`)의 의미는 그대로다.
- 재활성화가 이벤트 id 를 충돌시키지 않도록 ordinal 을 붙였다
  (`_development_activation_ordinal`), STEP 20 의 원장 충돌과 같은 형태.
- `development_activation_context()` 를 추가해 "누가·언제·무슨 권한으로"를
  읽기 전용으로 투영하고, 카탈로그 dict 와 활성화 응답에 실었다. UI 라벨은
  위 0절 참조.

`_is_approved` 에서 `_DEVELOPMENT_FIXTURE_EVENT` 와 `"development_only"` 를
승인 결정 집합에서 제거했다. **동작 변화는 없다** —
`_development_fixture_payload` 에는 `decision` 키가 아예 없어서 한 번도
매칭된 적이 없다(측정으로 확인). 오해를 부르는 분기를 지운 것이다.

**1-4 — 테스트로 고정했다.** `tests/test_development_activation_gate.py` (7건)

- 게이트가 서 있는 프로토콜은 `available_for_execution` 이 거짓이고
  `activate_development` 가 `ProtocolCatalogUnavailableError` 를 낸다
- 게이트를 해제해도 그것만으로는 실행 가능해지지 않는다 (권한과 판단은 별개)
- 활성화 후에만 참이 되고, 원장에 actor/role/시각이 남는다
- 철회하면 다시 거짓이 되고, 없는 활성화는 철회할 수 없으며,
  재활성화는 새 식별자로 기록된다 (활성화→철회→활성화 3건 순서 확인)
- materialize 안 된 fixture / operational scope / store 비활성은 각각의
  사유와 함께 거부된다
- `session.start` 가 활성화 없는 configured fixture 에 대해
  `protocol_selection_unavailable` 을 보낸다

**1-5 — 무엇이 달라지는가**: 위 0절 표. 안전 판정은 느슨하게 하지 않았다.

### 기존 검증된 동작에 대한 위험

이 변경으로 기존 테스트 **12건이 실패**했고, 전부 "configured fixture 는
그냥 실행 가능하다"를 전제하던 것들이었다. 처리는 두 갈래다.

- **2건은 단언을 고쳤다.** `test_protocol_catalog.py` 의 카탈로그 투영은 이제
  `unapproved` / `available_for_execution=False` /
  `execution_blocked_reason="development_activation_not_recorded"` 를 단언한다.
  이것이 새로 옳은 값이다.
- **10건은 벽 뒤의 동작을 보는 테스트**다(세션 지속성, 복구, 턴 처리, 보고서).
  in-gel 은 활성화될 수 없으므로 이 테스트들에 실행 가능한 상태를 만들어 줄
  방법이 없다. `tests/development_activation.py` 를 만들어 **딱 하나의 게이트만**
  드러내 놓고 우회한다. 이것은 이미 `test_pdf_to_session_walkthrough` 가
  같은 벽에 대해 쓰던 방식이고, 프로덕션 동작은 바꾸지 않는다.
  게이트 자체는 위의 새 테스트가 지킨다.

**최종: 1319 passed, 1130 subtests passed, 실패 0.** (STEP 22-B 시점 1286 + 신규)

`python -m compileall -q src tests scripts`, `git diff --check`,
`python scripts/replay_turns.py` 모두 통과.

---

## 2. 작업 2 — PDFium 스레드 안전성

### 2-1 반증 — 위 0절. 락은 없었다.

### 2-2 pypdfium2 호출 지점 전수 (간접 포함)

AST 로 패키지 전체를 훑었다(`tests/test_pdfium_serialization.py` 가 같은 검사를
회귀로 고정한다).

- `pypdfium2` 를 import 하는 모듈: **1개** — `experiment_protocol_pdf.py`
- `pypdfium2` 이름을 사용하는 함수: **1개** — `_pypdfium_page_texts`
- 그 안의 pdfium API: `PdfDocument(path)`, `len(document)`, `document[i]`,
  `page.get_textpage()`, `.get_text_range()`, `document.close()`
- 렌더링·비트맵·페이지 접근의 **간접 경로 없음**:
  `.render(` / `PdfBitmap` / `to_pil` / `get_textpage` 를 패키지 전체에서
  검색해 이 모듈 밖에서는 0건
- `curated_protocol._verified_source_crop()` 은 **pypdf** 를 쓴다 (pdfium 아님)
- 비교 엔진은 `pdftotext` **별도 프로세스**다 (pdfium 아님)
- `extract_protocol_pdf` 의 호출처는 17곳이지만, 전부 이 한 함수로 수렴한다

### 2-3 즉시 조치 — 프로세스 전역 뮤텍스 (구현함)

`_PDFIUM_LOCK = threading.RLock()` 을 모듈 전역으로 두고
`_pypdfium_page_texts` 의 **본문 전체**를 감쌌다. 호출 단위가 아니라
**문서 수명 전체**를 잡는다 — page 와 textpage 는 document 에 속하므로,
그 사이에 다른 스레드가 pdfium 을 만지면 안 된다.

락을 우회하는 경로가 없음을 테스트로 고정했다
(`tests/test_pdfium_serialization.py`, 5건):

- pdfium binding 을 import 하는 모듈이 정확히 1개
- pdfium 이름을 쓰는 함수가 정확히 1개이고, 그 함수 안의 **모든** pdfium 사용이
  `with _PDFIUM_LOCK:` 블록의 줄 범위 안에 있다 (AST 의 lineno 로 판정)
- 락이 프로세스 전역 `RLock` 이다

### 2-6 동시성 재현 테스트 — **락 계측 방식이다 (명시)**

크래시 자체는 CI 에서 재현하지 않는다. 100회 넘는 시도 중 1회만 재현된
간헐적 메모리 안전성 결함이므로, "크래시가 안 났다"는 단언은 수정 전에도
통과한다. 대신 **락이 실제로 획득되는지**를 계측한다:

8 스레드 × barrier 동기화 × 총 24회 추출, `_PDFIUM_LOCK` 을 관찰용 래퍼로
교체하고 측정한 값:

- `max_holders == 1` — 두 스레드가 동시에 pdfium 을 잡은 적이 없다
- `contended > 0` — 실제로 대기가 발생했다(= 이 실행이 동시성을 겪었다).
  이 단언이 없으면 스레드가 줄줄이 실행되어도 테스트가 통과한다
- 오류 0건, 같은 바이트 → 같은 sha256

추가로 **직렬 추출 결과와 동시 추출 결과의 페이지 텍스트가 완전히 일치**하는지
비교한다(2-7 대응).

### 2-7 힙 손상이 크래시 없이 잘못된 텍스트를 반환할 수 있는가

**가능하다.** 힙 손상은 정의상 어떤 메모리를 읽었는지의 문제이고, 잘못된
바이트를 읽고도 정상 반환할 수 있다. 실제로 두 크래시의 시그널이 달랐다는 것
(SIGABRT / SIGSEGV)이 "특정 조건의 깨끗한 실패가 아니다"라는 증거다.

**지금까지의 측정 중 동시 요청 경로를 거친 것이 있는지 — 확인했다. 없다.**

- 오프라인 측정 스크립트를 전수 조사했다. `scripts/` 에서 threading /
  concurrent / multiprocessing 을 import 하는 것은
  `scripts/prototype_claim_chunks.py` **1개**뿐이다.
- 그 스크립트를 읽어 보면 `extract_protocol_pdf(path)` 는 **ThreadPoolExecutor
  블록보다 앞에서, 메인 스레드에서 1회** 호출된다(line 614). 풀 안에서 도는
  것은 `analyze_protocol_chunk(extraction, chunk, model)` 이고,
  `extraction_for_chunk` 는 이미 만들어진 dataclass 를 자르기만 한다.
  **pdfium 은 풀 안에서 호출되지 않는다.**
- STEP 22-B 의 측정 스크립트들도 단일 스레드였다(당시 명시적으로 그렇게 작성).

따라서 이 저장소가 인용해 온 추출 수치는 직렬 경로에서 나온 것이다.
단, 이것은 **스크립트에 대한 확인**이지 서버 로그로 관측된 과거 요청들에
대한 확인이 아니다. 09-04 에 브라우저 탭 2개가 같은 diff 를 병렬 호출한
그 요청들이 반환한 텍스트가 온전했는지는 **확인할 수 없다.**

### 2-5 diff 엔드포인트가 요청마다 재추출하는가 — **한다. 3번.**

계측한 값 (같은 요청을 TestClient 로 실행, 48.9 MB / 40페이지 ANKOM):

| 요청 | `extract_protocol_pdf` 호출 | pdfium 진입 (캐시 없음) | pdfium 진입 (캐시 warm) | 시간(캐시 비움) | 시간(warm) |
|---|---|---|---|---|---|
| reviewer diff | **3회** | 3회 → 1회 | **0회** | 2331–3349 ms | 868–903 ms |
| `GET /api/protocols` | **7회** | 7회 → 3회 | **0회** | 2674–2684 ms | 987–990 ms |

`_entry_for_revision`(protocol_catalog.py:1117)이 **모든 카탈로그 항목마다**
`extract_protocol_pdf` 를 부르고, `review()` 가 또 부른다. 그래서 목록 1회에
7회, diff 1회에 3회다.

**추출 결과 캐시를 넣었다.** 지시대로 캐시 키는 source sha256 을 포함한다:
`(sha256, byte_size, source_path.name)`. 파일 이름이 키에 들어가는 이유는
`ProtocolPdfExtraction.original_filename` 이 경로에서 나오기 때문이다 —
같은 바이트라도 이름이 다르면 다른 항목이다(목록 요청의 cold pdfium 진입이
2가 아니라 3인 이유가 이것이다: 같은 in-gel 바이트가 객체 저장소 이름과
원본 파일 이름 두 가지로 접근된다).

규칙을 우회하지 않는다는 근거:

- 키를 만들기 위해 **파일을 실제로 읽어 해시한다**. stat 튜플 같은 대용물이
  아니다. (48.9 MB 해시 비용 측정: 325 ms cold / 138 ms warm page cache)
- 저장은 조회에 쓴 키가 아니라 **추출기가 스스로 측정한 identity** 로 한다.
  중간에 파일이 바뀌었다면 새 바이트가 새 identity 로 들어가고, 옛 키로는
  캐시 미스가 된다.
- `ProtocolPdfExtraction` 은 `frozen=True` 이므로 공유해도 변조되지 않는다.
- 값은 8개 LRU, 프로세스 수명 한정, 디스크에 쓰지 않는다.
- 캐시된 결과가 새로 추출한 결과와 같은지 확인했다 (`==` 참).
- readiness·승인·evidence 는 캐시가 없을 때 읽었을 바로 그 추출을 읽는다.

부수 효과로 전역 락을 잡고 있는 시간이 크게 줄어, 락 도입에 따른 직렬화
비용을 상쇄한다.

### 2-4 별도 프로세스 파서 — 설계함. **이번 STEP 에서는 구현하지 않는다.**

**설계**

- **경계**: `extract_protocol_pdf()` 하나. 17개 호출처는 손대지 않는다.
- **워커**: `multiprocessing` + **`spawn`** (fork 금지 — uvicorn 은 이미 다중
  스레드 프로세스이고, 스레드가 있는 상태의 fork 자체가 불안정하다).
  힙 손상이 누적되지 않도록 추출 1건당 워커 1개 (`maxtasksperchild=1`).
- **신뢰 경계**: 자식은 페이지 텍스트만 돌려준다. sha256·byte_size 는 부모가
  직접 계산하고, 자식이 계산한 해시는 받지 않는다.
- **워커 사망 시 결정적 오류**: 부모가 `exitcode` 를 보고
  `ProtocolPdfWorkerDiedError(signal=N)` 를 올린다. **`ProtocolPdfMalformedError`
  로 접으면 안 된다** — 크래시를 "손상된 문서"로 분류하면 라이브러리 버그가
  문서의 죄가 되고 fail-closed 의 이유가 거짓이 된다. readiness 상에서
  "텍스트 없음으로 진행"은 절대 하지 않는다 (fail closed, fail dead 아님).
- **타임아웃**: `join(timeout)` → `kill()` → `ProtocolPdfWorkerTimeoutError`.
  근거 측정치: 최대 문서 추출 1.25 s. 10–15 s 면 40배 여유이고, 크래시 난
  요청이 16 s 였다는 관측과도 구분된다.
- **메모리 상한**: 자식에서 import 전에 `resource.setrlimit(RLIMIT_AS, ...)`.
  근거: 48.9 MB / 40페이지가 **200 MB 상한에서도 정상 완료**했다.
  512 MB–1 GB 면 정상 작업에 여유가 크고 폭주는 묶인다. 단 상한은 안전장치가
  아니다 — `-fno-exceptions` PDFium 은 할당 실패를 검사하지 않을 수 있다.
  안전을 만드는 것은 프로세스 경계이고 상한은 피해 범위만 줄인다.
- **evidence handle 계산에 대한 영향**: canonical segment id 는
  `{evidence_segment_version, source_revision, source_sha256, source_page_number,
  page_text_sha256, segment_index, segment_text_sha256}` 를 해시한다. 어느 것도
  "어느 프로세스가 텍스트를 뽑았는가"에 의존하지 않는다. 세 조건을 지키면
  핸들은 불변이다: (1) 자식은 페이지 텍스트를 그대로 반환(자식 쪽 정규화 금지),
  (2) `page_text_sha256` / `segment_text_sha256` 는 부모가 재계산,
  (3) `glyph_resolutions`, `unresolved_glyph_reasons`, `TextVerification`,
  `divergent_page_numbers` 가 직렬화에서 정확히 보존. (3)이 조용히 깨지면
  Class 1 / Class 2 글리프 처리가 말없이 바뀐다 — 이 작업의 최대 회귀 위험이다.
- **매니페스트(visuals, timers)의 세그먼트 인용에 대한 영향**:
  `timers.json` 검증은 "인용된 텍스트가 리터럴을 포함하는가"와 "페이지가 이
  단계 anchor 와 다음 단계 anchor 사이인가"를 본다. 둘 다 **페이지 텍스트와
  페이지 번호**에서만 나오므로, 위 (1)·(2)를 지키면 영향이 없다.
  `visuals.json` 은 페이지의 XObject 이름(`/X125`, `/X135`)을 쓰는데, 이것은
  **pypdf 경로**(`_verified_source_crop`)에서 나오고 pdfium 을 거치지 않는다.
  따라서 pdfium 을 프로세스 밖으로 내보내도 시각 자료 매니페스트는 그대로다.

**구현하지 않기로 한 근거 (2-4 가 요구한 자체 판단)**

1. 2-3 이 **증명된 원인**을 덮는다. pypdfium2 문서가 동시 호출을 금지하고,
   두 크래시 모두 AnyIO 워커 스레드였으며, diff 엔드포인트는 리뷰어 2명이
   동시에 열 수 있다. 락 이후 그 겹침은 구조적으로 불가능하다.
2. 남는 위험은 **단일 스레드 pdfium 결함**(진짜 malformed 문서)이다. 락은
   이것을 덮지 못하고, 프로세스 경계만이 덮는다. 이 잔여 위험은 남아 있다.
3. 그런데 그 경계는 **모든 evidence handle 이 의존하는 단 하나의 함수**를
   건드린다. 위 (3)의 회귀는 조용하다. 안전 게이트를 바꾸는 같은 STEP 에서
   함께 하면, 회귀가 났을 때 원인 귀속이 불가능해진다.
4. 캐시 도입으로 warm 경로의 pdfium 진입이 0이 되어 노출 빈도 자체가
   크게 줄었다(위 표).

따라서 **2-3 + 캐시를 이번에, 프로세스 분리를 다음에** 하는 것이 위험 대비
이득이 크다고 판단했다.

---

## 3. 작업 3 — 503: 유일성이 아니라 정체성

### 3-1 검사를 바꿨다

`development_fixture_is_materialized` (protocol_catalog.py:1318):

```
before:  analyses = list_analysis_revisions(...);  if len(analyses) != 1: return False
after :  analysis = self._latest_analysis(revision)
         if analysis is None: return False
         if analysis.analysis_id != f"curated-{fixture.fixture_sha256}": return False
```

정체성 판정 근거는 이미 존재하는 결속을 그대로 썼다:
materializer 가 analysis_id 를 `f"curated-{fixture.fixture_sha256}"`
(protocol_catalog.py:1295)로 만든다. protocol/readiness/capability_policy 동등성
검사와 `development_fixture_materialized` 이벤트 payload 일치 검사는 그대로다.

### 3-2 느슨해지지 않았다 — 섞여 있을 때의 동작을 명시하고 고정했다

- 일치하는 분석이 **없으면** 여전히 거짓이다.
- **최신 분석이 이 fixture 의 것이 아니면** 거짓이다. 카탈로그와 `review()` 는
  최신 분석을 제공하므로, 옛 분석과 일치한다고 참을 반환하면 독자가 보는 것과
  fixture 가 주장하는 것이 어긋난다.
- protocol revision 이 2개 이상이면 여전히 거짓이다(원문이 달라진 것).

테스트(`tests/test_aged_store.py`):
편집 전 fixture 로 bootstrap → 편집 후 fixture 로 bootstrap → 분석 2개 상태에서
`is_materialized(편집후) == True`, `is_materialized(편집전) == False`,
`is_materialized(한 번도 bootstrap 안 된 fixture) == False`.

### 3-3 항목 하나 때문에 목록 전체가 503 이 되는 구조인가 — **된다. 그대로 둔다.**

`_public_protocol_catalog_entries()` 는 `catalog.list_entries()` 를 돌면서
항목 하나가 예외를 내면 전체가 503 이 된다. `_entry_for_revision` 은
source object 가 없으면 `ProtocolCatalogUnavailableError` 를 내므로, 실제로
한 항목의 유실이 목록 전체를 죽인다.

**결정: 이번에는 바꾸지 않는다.** 근거:

1. 이번 503 은 "깨진 항목" 때문이 아니었다. 두 항목 모두 멀쩡했고(STEP 22-B
   B-3: source object 2/2 존재, 크기 일치) 터진 것은 fixture↔store 일관성
   가드였다. 그 가드는 조건이 틀렸던 것이지 존재가 틀린 게 아니다.
2. "깨진 항목을 표시하고 나머지는 반환"은 **안전 원칙에 어긋나지 않는다** —
   오히려 사라진 항목보다 빨갛게 표시된 항목이 조사된다. 하지만 그것은
   `_entry_for_revision` 의 실패 처리를 바꾸는 별개의 변경이고, 이번 장애와
   원인이 다르다. 같은 STEP 에서 섞으면 3-1 의 효과를 측정할 수 없다.
3. 지금은 부분 실패 사례가 저장소에 **하나도 없다**(측정: pdf_objects 2행,
   디스크 파일 2개, 크기 전부 일치). 재현할 수 없는 상태를 위해 실패 경로를
   바꾸는 것은 검증되지 않은 코드를 늘리는 것이다.

즉 "논하되 이번엔 바꾸지 않는다"이고, 바꾼다면 별도 STEP 에서
`lifecycle_state: blocked` 로 목록에 남기는 방향이 맞다고 본다.

### 3-4 고친 뒤 실제 확인 — 위 0절 표. 200 / 프로토콜 2건 / 드롭다운 복구.

---

## 4. 작업 4 — 나이 든 저장소 테스트 공백

### 4-1 장치 (`tests/aged_store.py`)

빈 임시 store 로는 도달할 수 없는 상태를 의도적으로 만든다:

- `curated_fixture_for(...)` / `edited_fixture(...)` — fixture 를 편집하면
  해시가 바뀌고, 그것이 다른 analysis 를 이름 짓는다는 실제 인과를 재현
- 그 두 fixture 로 bootstrap 을 두 번 → **분석 리비전이 누적된 store**
- `analysis_payload_as_written_before(draft, without=...)` — **현재 직렬화기가
  실제로 그 키를 내보내는 payload** 를 만든 뒤 키를 제거한다. 데이터를 안 주는
  방식으로 키가 없는 payload 를 만들면 현재 writer 가 평범한 날에 쓰는 것과
  같은 바이트가 되어 옛 store 에 대해 아무것도 증명하지 못한다 (이 함정에
  실제로 한 번 빠졌다 — 아래 4-2 참조)

### 4-2 두 결함이 실제로 잡히는지 — 검증했다

별도 git worktree 에 과거 코드를 복원해 돌렸다.

| 결함 | 복원한 코드 | 결과 |
|---|---|---|
| STEP 22-B | `protocol_catalog.py` 를 HEAD(수정 전)로 | **실패.** `ProtocolCatalogUnavailableError: Configured development fixture conflicts with catalog state.` — 프로덕션과 같은 문구 |
| STEP 14 | 디코더가 새 필드를 필수로 요구하도록 복원 | **실패.** `ProtocolSerializationError: Stored Protocol analysis envelope is malformed.` — STEP 14 당시와 같은 문구 |

**첫 시도에서는 STEP 14 가 잡히지 않았다.** 내 aged payload 가 4키였고 당시
디코더도 4키를 요구해서 통과했다. 지시대로 "장치가 부족한 것"으로 보고
장치를 고쳤다(위 4-1의 세 번째 항목). 고친 뒤 잡힌다.

### 4-3 스키마 버전이 오르면 자동으로 걸린다

`tests/test_aged_store.py::SchemaChangeTripwireTests` 3건:

- `ANALYSIS_SCHEMA_VERSION == CORPUS_ANALYSIS_SCHEMA_VERSION` — 버전을 올리면
  즉시 실패하고, 실패 메시지가 "이미 저장된 payload 를 어떻게 할지 정하고,
  옛 형태를 aged_store 에 추가한 뒤 corpus 버전을 올려라"라고 말한다
- `serialize_analysis` 가 `KNOWN_PAYLOAD_KEYS` 밖의 키를 내보내면 실패 —
  새 필드를 추가한 순간 "그 필드가 없는 옛 payload 를 허용할지" 결정을 강제한다
- `TOLERATED_ABSENT_KEYS` 의 각 키를 `deserialize_analysis` 소스가 실제로
  명시하는지 확인 — 기록과 디코더가 어긋나면 실패

추가로 `AgedByFieldSetTests` 는 옛 payload 를 허용하는 것이
**아무거나 허용하는 것이 아님**을 고정한다: 필수 키가 없으면 여전히 거부,
모르는 키가 있으면 여전히 거부.

---

## 5. 작업 5 — 문서 역할 재배치 (측정만, 변경 없음)

### 5-1 headspace 가 실행 가능해지기까지 남은 차단 사유

분석이 없으므로(provider 0회) 코드에서 **결정론적으로 도출되는 것**과
**분석이 있어야 알 수 있는 것**을 나눠 적는다.

| 차단 사유 | headspace 에 적용되는가 | 분류 |
|---|---|---|
| `source_text_cross_check_failed/unavailable` | **아니오.** 측정: `verified` | — |
| `no_declared_safety_warnings` | **예.** 실행 가능한 단계가 있으면 무조건 발생 | **(가)** 사람이 acknowledge (`_ACKNOWLEDGEABLE_GATES`) |
| `unconfirmed_fixed_repetition` | **예.** 고정 반복 3건 (p.5 「twice more」, p.6 「for the metal plates」, p.12 「twice more (three conditioning rounds in total)」) | **(가)** 리뷰어가 개수 확인 |
| `unsupported_fixed_range_repetition` | **아니오.** P1 프로필이 지원 | — |
| `unsupported_operator_determined_repetition` | **아니오.** P1 프로필이 지원 (STEP 20) | — |
| `unsupported_repeat_until` | **아니오.** 측정: 0건 | — |
| `unresolved_ambiguity` | **확인 못 함** — 분석이 있어야 안다 | (가) 이면 해결 가능 |
| `missing_execution_critical_value` | **확인 못 함** | **(다)** 추출/원문 문제 |
| `no_executable_steps` | **확인 못 함** — 청크가 통과해야 안다 | **(다)** |

P1 지원 집합은 측정으로 확인했다:
`{FIXED_RANGE_REPETITION, OPERATOR_DETERMINED_REPETITION, INFORMATIONAL_DIFFERENCE}`
(`experiment_protocol.py:508`).

**요약**: 확정적으로 남는 것은 (가) 두 건뿐이고 새 능력 (나) 은 필요 없다.
나머지는 분석을 한 번 돌려야 알 수 있으며, 그 경로는 STEP 19–20 에서
headspace 7회 예산으로 이미 산정되어 있다.

### 5-2 in-gel 의 repeat-until 3건 — 원문과 필요한 범위 (구현 없음)

fixture 의 constructs 에서 그대로 인용한다.

1. **p.5** 「7 Repeat steps 2-7 until the gel band is fully destained」
   → `repeat_until`, `candidate-a-repeat-steps-02-07`, 단계 02–07
2. **p.6** 「The gel should look white (dehydrated) as seen in the above picture.
   If the band is still transparent then repeat steps 8-9 until fully dehydrated」
   → `repeat_until`, `candidate-a-repeat-steps-08-09`, 단계 08–09
3. **p.8** 「The gel should look white (dehydrated) as seen in the above picture.
   If the band is still transparent then repeat steps 17-18 until fully dehydrated」
   → **`source_ambiguity`**, `candidate-a-step-20-repeat-range`.
   같은 문장 형태인데 repeat_until 이 아니라 미해결 모호성으로 기록되어 있다.

**지원하려면 필요한 것 (범위만)**

1. capability profile 이 `REPEAT_UNTIL` 을 선언 → `_UNSUPPORTED_REASONS` 경로에서
   사유가 애초에 생성되지 않는다. **`_ACKNOWLEDGEABLE_GATES` 에 추가하는 방식은
   안 된다** — 그건 "지원한다"가 아니라 "사람이 눈감는다"이다.
2. 종료 판정 주체는 사람. STEP 16 결정대로 서버의 반복 상한은 종료 조건이 될
   수 없고, 상한에 닿으면 정지·에스컬레이션이다. 실행 형태는 루프 경계에서
   멈추고 원문 조건 문장을 읽어 주고 명시적 응답을 받는 것.
3. 세션: `repeat_until_awaiting_decision`,
   `provide_repeat_until_decision(satisfied: bool)`, `may_begin_step` 확장.
   기존 `provide_operator_repetition_count` 가 그대로 본이 된다.
4. 원장: 반복 1회분 결정마다 actor/role/시각/handles/ordinal + revocation.
5. 증거 결속: 읽어 주는 조건 문장은 evidence handle 에 묶인 원문이어야 한다.
   의역이 들어가면 3층 판단이 다시 모델로 돌아온다.
6. **이것만으로 in-gel 은 안 열린다** — 3번 항목이 `source_ambiguity` 이므로
   리뷰어 해결이 별도로 필요하다.

### 5-3 intracellular 의 `SOURCE_TEXT_CROSS_CHECK_FAILED` 는 무엇을 비교하는가

측정값:

```
text_verification      : mismatch
divergent_page_numbers : (10, 18, 33)
unmapped code point 이 있는 페이지 : [10, 18, 33]
glyph_resolutions      : 5   (해결된 위치)
unresolved_glyph_reasons: 5
   page 10: no engine resolved an unmapped position (unresolved)
   page 18: no engine resolved an unmapped position (unresolved)
   page 33: no engine resolved an unmapped position (alignment_failed)
   page 33: no engine resolved an unmapped position (unresolved)
   page 33: no engine resolved an unmapped position (alignment_failed)
```

**무엇을 비교하는가**: 두 층이다.
(a) `verify_page_text` 가 pypdfium2 추출과 독립 엔진(`pdftotext`, 별도 프로세스)의
**정규화된 문자 census** 를 비교한다. 줄바꿈·상첨자 순서·제어문자·줄끝 하이픈은
정규화로 제거되므로 남는 것은 순서 무관한 문자 구성이다.
(b) 그와 별개로, 문서 자신의 ToUnicode 로도 독립 엔진으로도 **어느 위치의
문자를 확정할 수 없으면** verification 을 `MISMATCH` 로 강등한다
(`experiment_protocol_pdf.py`, glyph_failures 분기).

**intracellular 를 mismatch 로 만든 것은 (b)다.** divergent 페이지 3개가
unmapped code point 를 가진 페이지 3개와 정확히 일치한다. 즉 census 자체가
어긋난 게 아니라, 정본 증거로 인용할 텍스트 안에 **출처를 댈 수 없는 문자**가
남아 있어서 거부된 것이다. STEP 13 의 Class 2 정책(문서가 선언하지 않은 위치는
추측하지 않고 거부)이 설계대로 동작한 결과다.

**STEP 22 의 계층 번호(3.2, 3.5 …) 미인식과 같은 원인인가 — 아니다.**
그쪽은 `protocol_claim_analysis.py:57` 의
`_NUMBERED_SOURCE_LINE = ^[ \t]*([1-9][0-9]{0,2})(?:[.)])?[ \t]+(\S+)` 이
`3.2` 를 매칭하지 못하는 문제다. 실제로 확인했다:

```
'3.2 Wash the pellet'  -> no match
'3 Wash the pellet'    -> match label=3
'12. Wash'             -> match label=12
'3.5.1 Wash'           -> no match
```

이것은 **다른 모듈의 claims 트리거 범위** 문제이고, 추출이나 cross-check 를
전혀 거치지 않는다. 두 결함은 독립이다.

---

## 6. 내 지시 중 틀린 것 / 전제가 어긋난 것

| 지시 | 판정 |
|---|---|
| 2-1 "락이 이미 있는지 확인" | 없었다. 진단 수정 불필요 |
| 1-3 "큐레이션 fixture 가 계속 실행 가능해야 한다면" | **조건이 성립하지 않는다.** in-gel 은 활성화 자체가 불가능하므로, 기록되는 활성화를 만들어도 이 문서는 실행되지 않는다. 메커니즘은 만들었고 in-gel 은 닫혔다 |
| 3-3 "항목 하나 때문에 전체 503 이 되는 구조인지" | 구조는 맞다. 다만 **이번 503 의 원인은 그것이 아니었다** — 깨진 항목은 없었다 |
| 4-2 "이 장치로 두 결함이 다 잡히는지" | 첫 장치는 STEP 14 를 **못 잡았다.** 지시대로 장치를 고쳤고, 고친 뒤 잡힌다 |
| (내 이전 추정) diff 가 PDF 를 2회 재추출 | **3회다.** 목록은 7회 |
| (내 이전 진술) 테스트 baseline 은 플래그 2개 | **3개다.** `EXPERIMENT_REPORTS_ENABLED=false` 가 빠지면 cascade 테스트 1건이 `.env` 의 보고서 DB 때문에 실패한다. HEAD 를 clean worktree 에서 돌려 이 실패가 **내 변경과 무관한 기존 조건**임을 확인했다 |

---

## 7. 기존 검증된 동작에 대한 위험

- **작업 1**: 12건이 새 게이트에 걸렸다. 2건은 단언 갱신, 10건은 벽을 드러낸 채
  우회. 프로덕션 동작은 우회하지 않는다. 게이트는 새 테스트 7건이 지킨다.
  **실제 위험**: `development_activation_recorded()` 가 붙은 테스트는 게이트를
  검증하지 않는다. 게이트가 조용히 망가져도 그 10건은 계속 통과한다 —
  그래서 게이트 전용 테스트를 별도 파일로 분리했다.
- **작업 2 락**: 동시 추출이 직렬화되므로 최악의 경우 지연이 누적된다.
  캐시로 warm 경로의 pdfium 진입이 0이 되어 실측 지연은 오히려 줄었다
  (diff 4.2–5.6 s → 0.86–1.02 s).
- **작업 2 캐시**: 가장 큰 위험은 "같은 키에 다른 내용"인데, 키가 내용 해시라서
  구조적으로 불가능하다. 남는 위험은 환경 변화(`pdftotext` 유무가 바뀌면
  `TextVerification` 이 달라질 수 있음)인데, 캐시가 프로세스 수명 한정이므로
  재기동하면 다시 판정한다.
- **작업 3**: 정체성 검사가 **더 엄격해진 경우**가 하나 있다 — 최신 분석이
  fixture 의 것이 아니면 이제 거짓이다. 이전 코드는 분석이 1개이기만 하면
  참이었으므로, "분석 1개인데 그게 다른 fixture 것"인 store 는 이전에 참,
  지금 거짓이다. 그런 store 는 fixture 를 바꾼 뒤 처음 기동하기 전 상태이고,
  거기서 거짓이 맞다.
- **작업 4**: 새 tripwire 가 스키마를 올릴 때 반드시 실패한다. 이것은 의도된
  마찰이고, 실패 메시지가 무엇을 해야 하는지 말한다.
- **런타임 데이터**: `protocol_workspace.sqlite` 는 진단 전후 sha256 동일
  (`9102701e…69866`), mtime `2026-09-04 15:04:56` 그대로. PDF·객체 저장소 읽기만.
  `.env` 값은 출력하지 않았다.

---

## 8. 이번 STEP 에서 하지 않은 것

- 추출 로직 개선 (지시대로 하지 않음)
- 프로세스 분리 구현 (2-4, 설계만 — 근거는 §2)
- 목록 엔드포인트의 부분 실패 처리 변경 (3-3, 결정만)
- repeat-until 지원 (5-2, 범위만)
- provider 호출

---

Provider 호출 횟수: 0
