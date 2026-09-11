# STEP 22-B — 진단 보고서 (provider 호출 0회)

작성: 2026-09-05 · 브랜치 `feature/readiness-safety-warning-gate`
소스 코드 변경 없음 (`git status --short` 공백). 런타임 데이터는 읽기만 했다.

---

## 0. 요구된 순서대로 — 먼저 두 개의 결론

### A-1 / A-2 — 크래시 원인: **특정됨. 그리고 재현됨.**

원인 라이브러리는 **`libpdfium.so` (pypdfium2)** 이고, 사용자의 추정이 맞다.
이번에는 `strings` 추론이 아니라 **커널이 직접 라이브러리 이름을 기록**했다.

```
2026-09-04T15:10:54  AnyIO worker th[892853]: segfault at 25 ip 000079ee27e8bff4
                     error 4 in libpdfium.so[28aff4,79ee27e80000+3a8000]
2026-09-05T01:49:52  AnyIO worker th[914956]: segfault at 31c5d978 ip 0000000031c5d978
                     error 15
```

- 위 줄은 사용자의 크래시(9/4). 아래 줄은 **내가 이번에 재현한 크래시(9/5)** 다.
- 두 번 모두 **AnyIO worker thread** 에서 죽었다. FastAPI 는 `def` 핸들러를 스레드풀에서
  돌리고, 문제의 핸들러가 정확히 `def` 다.
- 두 크래시의 **시그널이 다르다**: 9/4 는 `Signal: 6 SIGABRT`(= `length_error ...
  -fno-exceptions` 메시지 뒤의 abort), 9/5 는 `Signal: 11 SIGSEGV`. 9/5 의 폴트
  주소는 IP 와 같고(`error 15` = 실행 불가 주소로 점프), 커널이 덤프한 코드 바이트는
  `_ste`, `rtree_i3P` 같은 **SQLite 힙 문자열**이었다. 즉 프로세스가 힙 데이터를
  명령으로 실행했다.
- **해석**: 같은 코드 경로에서 시그널이 매번 다르다는 것은 특정 조건의 깨끗한 실패가
  아니라 **메모리 안전성 위반(힙 손상)** 이라는 뜻이다. 그래서 "이 조건만 막으면 된다"는
  형태의 수리는 불가능하다. 사용자의 표현대로 현재 구조는 fail closed 가 아니라
  **fail dead** 이고, 이것은 Python 레벨에서 잡을 수 없다.

**재현 경로 (측정됨)**

```
GET /api/workspace/reviewer/revisions/revision-6ebb58c8b6bd907707b9f9c12e8e095b/diff
  → _workspace_catalog_analysis_gate()            server.py:2017
  → ProtocolCatalog.review(protocol_id)           protocol_catalog.py:1355
  → extract_protocol_pdf(source)                  protocol_catalog.py:1370
  → pypdfium2.PdfDocument(path)                   experiment_protocol_pdf.py:594
```

대상은 ANKOM(leaf-carbon) 원문, **48,906,987 bytes / 40 pages**.

**재현률: 1 / 100+.** 아래는 실제로 돌린 것과 결과다.

| 시도 | 횟수 | 결과 |
|---|---|---|
| 실서버(:8000) 첫 기동 후 ANKOM diff 1회 | 1 | **크래시 (SIGSEGV)** |
| `extract_protocol_pdf` 단독, main thread | 3 | 정상 |
| `extract_protocol_pdf` 단독, worker thread | 3 | 정상 |
| `catalog.review()` 단독, main / worker thread | 3+3 | 정상 |
| FastAPI `TestClient` in-process, 같은 엔드포인트 | 3 | 정상 |
| 예비 포트(:8010) uvicorn, 브라우저 없음 | 1 | 정상 |
| :8010 동시 2건 | 2 | 정상 |
| :8010 동시 4건 × 6라운드 | 24 | 정상 |
| :8000 재기동 후 순차 5건 | 5 | 정상 |
| :8000 브라우저 모사(WS + 요청 7건 동시) × 12라운드 | 84 | 정상 |

즉 **간헐적**이다. 상관 관계로 관찰된 것: 크래시 난 요청만 16초 걸렸고(정상은 4.2–5.6초),
그 사이 브라우저가 WebSocket 을 붙였으며, 시스템 여유 메모리가 낮았다.
**인과로 확정하지 못했다.** 메모리 압박 가설은 반증에 실패하지도, 성립하지도 않았다:
주소 공간을 200 MB 까지 조여도(`RLIMIT_AS`) 같은 40페이지 PDF 추출은 그냥 성공했다.

**OOM kill 은 아니다** — 두 크래시 모두 커널 OOM 로그가 없다(마지막 OOM 은 9/3,
무관한 프로세스). VmPeak 은 9/4 709 MB, 9/5 615 MB.

### C-1 — 업로드된 분석의 출처: **(가)**

UI 에서 본 25단계 분석은 **손으로 만든 `candidate_a_curated_analysis.json` 이 그대로
보인 것**이다. provider 는 호출되지 않았다.

근거는 §3 에 있다. 따라서 C-2(호출 여부 대조), C-3(정확도 즉시 채점)은 해당 없고,
"0 extractions passed all chunks" 라는 기존 인식은 **유효하다**.

---

## 1. 작업 A — 네이티브 크래시

### A-1 코어 덤프 확인 — 실행함

- `ulimit -c` = 0, `core_pattern` = `|/usr/share/apport/apport ...`,
  `coredumpctl` 미설치, `/var/lib/systemd/coredump` 비어 있음.
- apport 리포트는 존재한다: `/var/crash/_usr_bin_python3.14.1000.crash`.
- `gdb` 미설치. E-3(패키지 추가 금지)에 따라 **설치하지 않았고, 따라서 backtrace 는 없다.**
  라이브러리 특정은 backtrace 대신 **커널 로그의 `in libpdfium.so[...]`** 로 했고,
  이쪽이 더 직접적인 증거다.

**보고해야 할 손실**: 내 재현이 apport 리포트를 덮어썼다.
사용자의 9/4 리포트(226,902,851 bytes, 15:11)는 9/5 리포트(129,569,639 bytes, 01:50)로
교체되었다. 덮어쓰기 전에 헤더와 `ProcMaps` 는 이미 추출해 두었으므로
(Signal 6 / SIGABRT, VmPeak 709,864 kB, VmRSS 364,460 kB, `libpdfium.so` 매핑,
libstdc++/libc++ 매핑 없음, 네이티브 확장 51개) 판단에 필요한 사실은 남아 있다.
그러나 **원본 코어 자체는 복구 불가능하다.** apport 기본 동작이 덮어쓰기라는 점을
사전에 고려하지 못한 내 실수다.

### A-2 재현 시도 — 실행함, 위 표 참조

### A-3 in-process PDF 파싱 지점 전수 — 실행함

- PDFium 진입점은 **정확히 한 곳**: `experiment_protocol_pdf.py:594`
  `pypdfium2.PdfDocument(path)`.
- 그 함수(`extract_protocol_pdf`)의 호출처는 **17곳**.
- `curated_protocol._verified_source_crop()` 은 PDFium 이 아니라 **pypdf** 를 쓴다.
- **PDFium 을 감싸는 lock/semaphore 가 없다.** 동시 진입은 구조적으로 가능하다
  (동시 2·4건 테스트에서는 크래시가 나지 않았으므로, 이것은 확인된 원인이 아니라
  발견된 구조적 위험이다).
- 측정된 비용 (단독 추출, 이 VM):

| 문서 | 페이지 | 바이트 | 추출 시간 | peak RSS | cross-check |
|---|---|---|---|---|---|
| in-gel-digestion | 9 | 2,581,457 | 0.19 s | 53 MB | verified |
| usingdynamicheadspacecollections | 16 | 5,321,494 | 0.25 s | 60 MB | verified |
| intracellularmetaboliteextraction | 34 | 24,554,048 | 1.12 s | 95 MB | **mismatch** |
| ANKOM leaf-carbon | 40 | 48,906,987 | 1.25 s | 108 MB | verified |

### A-4 별도 프로세스 PDF 파서 — **설계만. 구현하지 않았다.**

**경계 위치.** PDFium 진입점이 한 곳뿐이므로 프로세스 경계도 한 곳에 놓을 수 있다:
`extract_protocol_pdf()` 자체. 17개 호출처는 수정 대상이 아니다.

**워커 형태.** `multiprocessing` + **`spawn`** (fork 금지 — uvicorn 은 이미 다중 스레드
프로세스이고, 스레드가 있는 상태의 fork 는 그 자체로 불안정하다). 힙 손상이 누적되지
않도록 **추출 1건당 워커 1개**(또는 `maxtasksperchild=1`).

**부모가 자식을 신뢰하지 않는 지점.** 자식은 페이지 텍스트만 돌려준다. 신원(sha256,
byte_size)은 부모가 직접 계산·검증한다. 자식이 계산한 해시는 받지 않는다.

**워커가 죽었을 때의 결정적 에러.** 부모는 `exitcode` 를 본다.
`exitcode < 0`(시그널 사망) 또는 `!= 0` 이면 **새 예외** `ProtocolPdfWorkerDiedError(signal=N)`
를 올린다. 이것은 `ProtocolPdfMalformedError` 로 접으면 안 된다 — 크래시를
"손상된 문서"로 분류하는 순간, 라이브러리 버그가 문서의 죄가 되고 fail-closed 의 이유가
거짓이 된다. HTTP 로는 별도 코드(예: 503 + 고유 detail)로 매핑하고, readiness 상에서는
**텍스트 없음으로 진행하지 않는다.**

**타임아웃.** `join(timeout)` 후 만료 시 `kill()` + `ProtocolPdfWorkerTimeoutError`.
크기 근거는 위 측정치다: 최대 문서가 1.25 s. 10–15 s 면 40배 이상의 여유이고, 크래시 난
요청이 16초였다는 관측과도 구분된다.

**메모리 상한.** 자식에서 `pypdfium2` import 전에 `resource.setrlimit(RLIMIT_AS, ...)`.
근거: 48.9 MB / 40페이지 문서가 **200 MB 상한에서도 정상 완료**했다. 512 MB–1 GB 면
정상 작업에는 여유가 크고 폭주는 묶인다. **단, 상한은 안전장치가 아니다** — `-fno-exceptions`
PDFium 은 할당 실패를 검사하지 않을 수 있다. 안전을 만드는 것은 프로세스 경계이고,
상한은 피해 범위만 줄인다.

**evidence handle 계산에 미치는 영향.** canonical segment id 는
`{evidence_segment_version, source_revision, source_sha256, source_page_number,
page_text_sha256, segment_index, segment_text_sha256}` 를 해시한다. 이 중 어느 것도
"어느 프로세스에서 텍스트를 뽑았는가"에 의존하지 않는다. 따라서 **세 조건을 지키면
핸들은 불변**이다:
1. 자식은 페이지 텍스트를 **그대로** 반환한다(자식 쪽 정규화 금지).
2. `page_text_sha256` / `segment_text_sha256` 는 **부모가 다시 계산**한다.
3. `glyph_resolutions`, `unresolved_glyph_reasons`, `TextVerification`,
   `divergent_page_numbers` 가 직렬화에서 **정확히** 보존된다. 여기가 조용히 깨지면
   Class 1 / Class 2 글리프 처리가 말없이 바뀐다 — 이 경계 작업의 가장 큰 회귀 위험이다.

**얻는 것.** 지금은 문서 하나의 크래시가 서버 전체를 죽인다(진행 중이던 음성 세션 포함).
경계를 넣으면 요청 하나의 결정적 실패가 된다.

---

## 2. 작업 B — `/api/protocols` 503

### E-1 요구: 재기동 후 verbatim 캡처 — 실행함

```
HTTP/1.1 503 Service Unavailable
date: Sat, 05 Sep 2026 01:49:22 GMT
server: uvicorn
content-length: 41
content-type: application/json

{"detail":"protocol_catalog_unavailable"}
```

기동 로그도 그대로 재현되었다:
`WARNING protocol.catalog.configuration unavailable error=ProtocolCatalogUnavailableError`

### B-1 경고 발생 지점과 다섯 개의 raise 중 어느 것인가 — 특정함

E-2 지시대로 가설이 아니라 실제 예외 지점에서 시작했다. 서버 환경을 복제해
`_public_protocol_catalog_entries()` 를 직접 호출하고 traceback 을 받았다:

```
File "server.py", line 1342, in _public_protocol_catalog_entries
    raise ProtocolCatalogUnavailableError(
        "Configured development fixture conflicts with catalog state."
    )
```

경고 자체는 `log_protocol_catalog_runtime_configuration()` (server.py:1352–1381)의
`except Exception` 이 `type(exc).__name__` 만 찍기 때문에 세부가 보이지 않았던 것이다.

**왜 그 raise 가 도는가 — 조건별 측정:**

`development_fixture_is_materialized()` (protocol_catalog.py:1318)를 조건 단위로 재현:

```
fixture.protocol_id      : candidate-a-curated-development-v1
fixture.development_only : True          → 통과
protocol revisions       : 1 [1]         → 통과 (len == 1)
pdf_checksum match       : True          → 통과
analysis revisions       : 2 [1, 2]      → ★ 실패 (len != 1)
  a1 curated-c2779c24...  protocol_eq=False readiness_eq=False policy_eq=True
  a2 curated-fb869290...  protocol_eq=True  readiness_eq=True  policy_eq=True
fixture events           : 2  (rev1/a1 payload_eq=False, rev1/a2 payload_eq=True)
is_materialized          : False
```

**정확히 한 조건에서 실패한다: `len(analyses) != 1`.**
현재 fixture 에 해당하는 a2 는 protocol·readiness·policy·event payload 가 **전부 일치**한다.
즉 이 검사는 "설정된 fixture 가 materialize 되어 있는가"가 아니라
**"이 protocol 에 analysis 가 딱 하나뿐인가"** 를 묻고 있다. 이것은 정체성 검사가 아니라
**유일성 가정**이고, 그 가정이 깨진 것이다.

### B-2 503 경로 — 특정함

```
GET /api/protocols
  → list_protocol_catalog()                     server.py:3242
  → _public_protocol_catalog_entries()          server.py:1314
  → catalog.list_entries() 루프에서 fixture 와 같은 protocol_id 를 만남  server.py:1338
  → development_fixture_is_materialized() == False
  → raise ProtocolCatalogUnavailableError       server.py:1342
  → except Exception → _catalog_http_error()    server.py:3255
  → HTTPException(503, "protocol_catalog_unavailable")  server.py:1404
```

**왜 `/review` 는 200 인가:** 단건 조회 경로는 `_public_protocol_catalog_entries()` 를
전혀 타지 않는다. `get_protocol_catalog_entry()` 는 fixture id 에 대해
`_candidate_catalog_dict(candidate)` 를 **store 를 보지 않고** 그대로 돌려준다.
목록만 store 와 fixture 의 일관성을 확인하고, 그 확인이 깨져 있다.

### B-3 카탈로그 엔트리 전수 + objects/sha256 존재 확인 — 실행함

| protocol_id | rev | source sha256 | 파일 존재 | DB byte_size | 디스크 크기 |
|---|---|---|---|---|---|
| candidate-a-curated-development-v1 | 1 | `63d81102…c00bd9` | **있음** | 2,581,457 | 2,581,457 |
| protocol-5367ca6bfae9fe9bbaeac9dab2099276 | 1 | `5367ca6b…56d18a` | **있음** | 48,906,987 | 48,906,987 |

`pdf_objects` 2행, 디스크 파일 2개, 크기 전부 일치.

**→ orphan 은 없다. B-3/B-5 의 "깨진 엔트리 / 사라진 source object" 전제는 반증되었다.**
503 은 어떤 엔트리가 망가져서 나는 것이 아니다.

### B-4 엔트리 하나가 목록 전체를 503 시켜야 하는가 — **결정만. 변경하지 않았다.**

먼저 질문을 정정해야 한다. **지금 상황은 "엔트리 하나가 깨진" 경우가 아니다.**
두 엔트리 모두 멀쩡하고, 터지는 것은 fixture ↔ store 일관성 가드다.

그 구분 위에서의 결정:

1. **가드 자체는 유지해야 한다.** "설정된 개발 fixture 와 store 의 상태가 어긋났다"는
   조용히 넘어갈 사건이 아니다. 어느 쪽이 실행 권위인지 모르는 채로 목록을 그리면,
   UI 가 어떤 분석을 보여주고 있는지 아무도 보장할 수 없다. FAIL CLOSED 가 맞다.
2. **그러나 가드의 조건이 틀렸다.** 유일성(`len(analyses) == 1`)은 fixture 정체성과
   무관한 조건이다. 물어야 할 것은 "**현재 fixture 에 대응하는 analysis revision 이
   존재하고, 그것이 최신인가**"이다. 이 조건이면 a2 가 통과하고 a1 은 과거 기록으로
   남는다 — append-only 원장의 취지와도 일치한다.
3. **진짜로 엔트리 하나가 깨진 경우**(예: source object 유실)라면, 그때는 목록 전체를
   503 시키기보다 그 엔트리를 **`lifecycle_state: blocked` 로 표시해 목록에 남기는 쪽**이
   낫다. 사라진 항목은 조사되지 않지만, 빨갛게 표시된 항목은 조사된다. 다만 이것은
   현재 장애와는 다른 사안이므로 여기서 함께 바꾸면 안 된다.
4. **최소 수정 범위(제안, 미구현)**: `development_fixture_is_materialized` 의 유일성
   조건 하나. 서버·핸들러·에러 매핑은 건드릴 필요가 없다.

### B-5 이 상태가 어떻게 생겼는가 — 추정이 아니라 기록으로

삭제도 수정도 하지 않고, 원장만 읽어서 재구성했다.

1. **2026-08-30 12:19:22** — 최초 bootstrap. `experiments`, `protocol_revisions` rev1,
   `analysis_revisions` a1 = `curated-c2779c24…`, event #1 `development_fixture_materialized`.
   이 시점에는 analysis 가 1개 → 가드 통과, 목록 정상.
2. **2026-09-02, commit `2eff979`** — `data/development_protocols/candidate_a_curated_analysis.json`
   내용이 바뀌었다. fixture 내용이 바뀌면 `fixture_sha256` 이 바뀌고,
   materializer 의 analysis_id 형식이 `f"curated-{fixture.fixture_sha256}"`
   (protocol_catalog.py:1295)이므로 **새 fixture = 새 analysis id** 다.
3. **2026-09-04 15:04:56** — 그 변경 이후 첫 서버 기동. `bootstrap_development_fixture`
   가 experiment 는 dedup 하되 **analysis revision 2** (`curated-fb869290…`)를 새로
   만들고 event #17 을 append 했다. 이 순간부터 `len(analyses) == 2` → 가드 영구 실패.
4. **2026-09-04 15:06:17** — 사용자의 UI 업로드. PDF sha 가 같아 dedup 되었고 새 revision 을
   만들지 않았다(§3 참조). **업로드는 원인이 아니다.** 원인은 3번의 기동 시 bootstrap 이다.

검증: 현재 fixture 를 로드하면 `fixture_sha256 = fb869290f1b52afab91f6f256a85ab5a…`,
a2 의 analysis_id 와 정확히 일치한다. a1 의 `c2779c24…` 는 현재 fixture 와 불일치한다.
`2eff979^` 시점의 fixture 로 a1 해시를 역산해 보려 했으나, 그 파일은 현재 코드로
로드되지 않는다(`CuratedProtocolFixtureError: schema identity is unsupported`).
따라서 **a1 을 만든 정확한 fixture 버전은 확인하지 못했다.** 확인된 것은 a1 ≠ 현재 fixture,
a2 = 현재 fixture 라는 사실이다.

**왜 테스트가 못 잡았나 (이전에 검증된 동작에 대한 위험).**
`tests/test_protocol_catalog.py:799` `test_bootstrap_is_idempotent_...` 는 매번
**빈 임시 store** 로 시작한다. 그래서 "같은 fixture 를 두 번" 은 검증하지만
"**fixture 파일이 바뀐 뒤 기존 store 에 다시 bootstrap**" 은 구조적으로 검증할 수 없다.
1145개 테스트가 전부 통과하면서도 이 상태가 만들어진 이유다. 이것은 STEP 14 때
"오래된 저장 payload 가 안 열리는데 테스트가 못 잡은" 것과 같은 종류의 공백이다.

---

## 3. 작업 C — UI 분석의 출처

### C-1 결론: **(가)** — 손으로 만든 fixture 가 그대로 보인 것

runtime DB 는 `python3` 내장 `sqlite3` 모듈로 `mode=ro` 로만 열었다 (E-3 준수).

| 표 | 행 수 | 내용 |
|---|---|---|
| `experiments` | 2 | candidate-a(08-30 12:19:22), protocol-5367ca6b(08-30 16:20:22) |
| `protocol_revisions` | 2 | 둘 다 rev1. candidate-a → `in-gel-digestion.pdf` / `63d81102…` |
| `analysis_revisions` | 2 | a1 `curated-c2779c24…`(08-30), a2 `curated-fb869290…`(09-04 15:04:56) |
| `protocol_events` | 17 | #17 = `development_fixture_materialized`, 09-04 15:04:56, a2 |
| `pdf_objects` | 2 | 위 B-3 표 |

- a2 의 readiness 는 `analysis_required`, reasons =
  `["unresolved_ambiguity", "no_declared_safety_warnings", "unsupported_repeat_until",
  "unsupported_repeat_until"]` — 사용자가 UI 에서 본 blocker 와 정확히 일치한다.
- **9/4 에 `protocol_analysis_requested` / `_started` / `_ready` 이벤트가 하나도 없다.**
  원장에 있는 분석 실행 기록은 08-30 의 ANKOM 실패 5건뿐이다.
- a2 의 analysis_id = 현재 fixture 의 `fixture_sha256`. 즉 a2 는 파이프라인 산출물이
  아니라 **fixture 를 그대로 materialize 한 것**이다.
- 업로드(`POST /api/protocols?filename=protein-experiment.pdf` → 201)는 PDF sha
  `63d81102…` 가 이미 있는 것과 같아 **dedup** 되었고, 새 protocol revision 을 만들지
  않았다. 그래서 25단계와 페이지 인용이 그대로 보였다.

### C-2 / C-3 — 해당 없음

(나)가 아니므로 provider 호출 대조도, `protocol_extraction_accuracy` 즉시 채점도
발동 조건이 아니다. **"0 extractions passed all chunks" 라는 인식은 그대로 유효하다.**

### C-4 — `pdf-1-analysis-2` 의 "-2"

`_revision_id(protocol_revision_number, analysis_revision_number)` 의 출력이다.
`pdf-1` = protocol revision 1, `analysis-2` = **analysis revision 2**.
즉 "-2" 는 두 번째 PDF 도, 두 번째 업로드도 아니고, **같은 원문 revision 에 대한 두 번째
분석 리비전**을 가리킨다. 그리고 그 2가 바로 §2 B-5 의 원인이다.

---

## 4. 작업 D — repeat-until 의 영향 범위

### D-1 문서별 repeat-until 개수 — 측정함 (단, 성격을 분명히 해야 한다)

`RepeatUntil` construct 는 provider 의 구조화 claim 에서 나온다. provider 호출이 0회이므로
**저장된 analysis 가 있는 문서는 in-gel 하나뿐**이다. 나머지 세 문서는 우리 추출기로
원문 텍스트를 뽑아 세었다. 아래 표에서 in-gel 만 파이프라인 실측이고,
**나머지는 원문 기준 측정치(파이프라인 산출물이 아님)** 다.

| 문서 | 페이지 | `repeat` 토큰 | repeat…until | repeat steps N–M |
|---|---|---|---|---|
| in-gel-digestion | 9 | 3 | **3** (p.5, p.6, p.8) | 3 |
| usingdynamicheadspacecollections | 16 | 5 | **0** | 5 |
| intracellularmetaboliteextraction | 34 | 4 | **0** | 0 |
| ANKOM leaf-carbon | 40 | 2 | **1** (p.32) | 0 |

**in-gel 교차 검증 (프록시가 맞는지 확인):** 원문 3건 ↔ fixture 의 construct 3개 —
`repeat_until` 2개(p.5 `Repeat steps 2-7 until the gel band is fully destained`,
p.6 `repeat steps 8-9 until fully dehydrated`) + `source_ambiguity` 1개
(p.8 `repeat steps 17-18 until fully dehydrated`, `candidate-a-step-20-repeat-range`).
프록시가 원문을 정확히 세었고, 파이프라인은 3건 중 2건만 `repeat_until` 로 표현했다.

**나머지 문서의 실제 문장** (성격 판정용):

- headspace (5건, **전부 횟수형**):
  p.5 「Repeat steps 12-15 twice more」(고정), p.5 「Repeat steps 19-20 for the required
  number of bacterial isolates/replicates」(작업자 결정), p.6 「Repeat steps 23-26 for the
  metal plates」(고정), p.12 「repeat steps 36-41 twice more (three conditioning rounds in
  total)」(고정), p.14 「Repeat steps 43-50 for the required number of treatments/replicates」
  (작업자 결정). → **조건 종료 반복 0건.** STEP 17 의 결론이 독립적으로 재확인되었다.
- intracellular (4건, **실행 반복 아님**): p.13 「avoid repeated freeze-thaw cycles」(권고),
  p.23/25/27 「repeat this process for all the compounds/samples」(소프트웨어 데이터 분석
  구간). → **조건 종료 반복 0건.**
- ANKOM (2건): p.24 「Repeat steps a and b if necessary」(조건형, 종료 조건이 작업자 판단),
  p.32 「Repeat rinses until the pH paper shows neutral color」— **관측 가능한 종료 조건을
  가진 진짜 repeat-until 1건.**

### D-2 repeat-until 없이 실행 가능해질 수 있는 문서 — **headspace 하나뿐**

- **in-gel**: 불가. repeat-until 2건 + p.8 미해결 ambiguity.
- **ANKOM**: 불가. p.32 의 진짜 repeat-until 1건(+ p.24 조건형).
- **intracellular**: repeat-until 은 0건이지만 **여전히 불가**. 추출 cross-check 가
  `mismatch` 다(위 A-3 표). `SOURCE_TEXT_CROSS_CHECK_FAILED` 는 `_ACKNOWLEDGEABLE_GATES`
  에 없으므로 사람이 승인해서 열 수 있는 게이트가 아니다. 이건 이번에 처음 측정된 사실이다.
- **headspace**: 유일하게 가능성이 있다. repeat 5건이 전부 이미 지원되는 형태
  (`fixed_range_repetition` 3건 — 사람 확인 필요, `operator_determined_repetition` 2건 —
  세션 시작 시 횟수 입력)이고, cross-check 는 `verified` 다. 남는 것은
  `no_declared_safety_warnings` 인데 이건 acknowledgeable 게이트다.
  **단, 이는 원문 구조 기준 판단이고 headspace 의 analysis 는 아직 없다.**

### D-3 "명시적 development activation" 경로 — 무엇이고 무엇을 우회하는가

**경로**: `POST /api/protocols/{protocol_id}/activate-development` (server.py:3792)
→ `ProtocolCatalog.activate_development()` (protocol_catalog.py:3055)

**세 개의 관문:**

1. `_development_activation_allowed()` (server.py:3387) — `VOICE_WORKFLOW_AGENT_USAGE_SCOPE`
   (또는 `_SAFETY_USAGE_SCOPE`)가 `{demo, reference_only, test_only}` 중 하나여야 한다.
   미설정 포함 그 외 전부 403 `development_activation_not_allowed`. fail closed.
   *(이 환경에서는 `True` 로 측정되었다. 값 자체는 출력하지 않았다.)*
2. analysis 가 존재해야 한다.
3. readiness 가 `GUIDANCE_READY` **이거나** `_readiness_gates_cleared()` 가 True 여야 한다.

**우회하는 것:** 사람/시설의 최종 승인 기록(`_APPROVAL_EVENT`, policy·secret·actor·role)이다.
대신 `protocol_development_activated` 이벤트를 `authority: "development_policy"` 로 남기고,
`_entry_for_revision` (protocol_catalog.py:1198)이 이 이벤트를 승인과 동등하게 취급해
`available_for_execution` 을 열어 준다.

**우회하지 못하는 것: readiness 게이트.** `_readiness_gates_cleared()`
(protocol_catalog.py:2117)는 blocking reason 을 하나씩 보고,
- `_ACKNOWLEDGEABLE_GATES` = `{no_declared_safety_warnings, source_text_cross_check_unavailable}`
  → 해당 게이트를 사람이 acknowledge 했을 때만 통과
- `unresolved_ambiguity` → 모든 ambiguity 에 유효한 해결 기록이 있을 때만
- `unconfirmed_fixed_repetition` → 모든 고정 반복에 개수 일치 확인이 있을 때만
- **그 외 전부 `return False`**

`unsupported_repeat_until` 은 이 셋 중 어디에도 없다. 따라서
**repeat-until 이 걸린 protocol 은 어떤 사람의 어떤 조작으로도 activation 을 통과할 수 없다.**
실측:

```
readiness_status        : analysis_required
readiness_gates_cleared : False
gates : {parsing: passed, structural_readiness: blocked, hazard_review: review_required,
         human_approval: pending, operational_authorization: blocked}
entry.available_for_execution : False
entry.lifecycle_state         : blocked
development_activation_allowed (이 환경) : True
```

**"시연 이상으로 쓰일 수 있는가": activation 경로는 아니다. 그러나 다른 문이 하나 있다.**

UI 에서 25단계가 실행 가능한 것처럼 보이는 것은 activation 때문이 아니라
**설정된 curated fixture** 때문이다. `_candidate_catalog_dict()` (server.py:1280)는
fixture 에 대해 `"available_for_execution": True` 를 **readiness 와 무관하게 무조건**
넣는다. 그리고 `get_protocol_catalog_entry()` 는 fixture id 에 대해 store 를 아예 보지
않고 이 dict 를 돌려준다.

- 이 문이 열리는 조건은 런처가 `VOICE_WORKFLOW_AGENT_CURATED_PROTOCOL_FIXTURE` 를
  설정하는 것뿐이다. usage scope 검사를 받지 않는다.
- 다만 fixture 는 `load_curated_protocol_fixture` 를 통과해야 하고, 그것은 특정 PDF sha
  와 provenance 에 해시로 묶여 있다. 즉 **임의의 문서로 이 문을 열 수는 없다.**
- 그래도 이것은 **readiness 게이트를 사람 없이 우회하는 유일한 경로**이고,
  "development activation 이 게이트를 우회한다"는 서술보다 실제로는 이쪽이 더 넓다.
  구조적 위험으로 기록해 둔다. (이번 STEP 에서는 아무것도 바꾸지 않았다.)

### D-4 repeat-until 을 지원하려면 무엇이 필요한가 — **범위만**

1. **도메인**: `RepeatUntil` construct 와 `UNSUPPORTED_REPEAT_UNTIL` 은 이미 있다.
   지원한다는 것은 capability profile 이 이 feature 를 선언하게 만드는 것이고, 그러면
   `_UNSUPPORTED_REASONS` 경로에서 reason 이 애초에 생성되지 않는다.
   **`_ACKNOWLEDGEABLE_GATES` 에 추가하는 방식은 안 된다** — 그건 "지원한다"가 아니라
   "사람이 눈감는다"이다.
2. **종료 조건의 주체**: STEP 16 에서 이미 결정했다 — 서버의 반복 상한은 종료 조건이
   될 수 없고, 상한에 닿으면 정지·에스컬레이션이다. 따라서 실행 형태는
   **루프 경계에서 멈추고, 원문의 조건 문장을 읽어 주고, 작업자의 명시적 응답을 받는 것**이다.
   판정 주체는 사람이지 모델이 아니다 (STEP 21 의 3계층 원칙에서 이건 3층이다).
3. **세션**: `CuratedProtocolSession` 에 루프 프레임 —
   `repeat_until_awaiting_decision`, `provide_repeat_until_decision(satisfied: bool)`,
   그리고 `may_begin_step` 확장(결정이 기록되기 전에는 루프 다음 단계 시작 불가).
   이미 있는 `provide_operator_repetition_count` / `repetitions_awaiting_a_count` 가
   그대로 본이 된다.
4. **원장**: 반복 1회분 결정마다 actor / role / timestamp / handles / ordinal 을 가진
   이벤트와 revocation. `confirm_fixed_repetition` 과 동일한 형태.
5. **증거 결속**: 읽어 주는 조건 문장은 반드시 evidence handle 에 묶인 원문이어야 한다.
   요약·의역이 들어가는 순간 3층 판단이 다시 모델로 돌아온다.
6. **in-gel 은 이것만으로 안 열린다**: p.8 의 세 번째 반복은 `repeat_until` 이 아니라
   `source_ambiguity`(`candidate-a-step-20-repeat-range`)로 기록되어 있어서,
   repeat-until 지원과 별개로 리뷰어 해결이 필요하다.

**범위 밖(이번에 하지 않은 것)**: 위 어느 것도 구현하지 않았고 설계 문서도 만들지 않았다.

---

## 5. 사용자 추정 중 틀린 것 / 내 이전 진술 정정

| 항목 | 판정 |
|---|---|
| "PDFium 이 유일한 C++ 의존성이고 `-fno-exceptions` 로 빌드됨" | **맞음.** 커널이 `libpdfium.so` 를 직접 지목 |
| "webrtcvad-wheels 는 C 라서 `std::length_error` 를 던질 수 없다" | **맞음.** 해당 메시지는 `libpdfium.so` 에만 존재(2회), `_webrtcvad`·`_pydantic_core`·`uvloop` 에 0회 |
| "네이티브 abort 는 Python 이 못 잡는다 → fail dead" | **맞음.** 더 나쁘다: 두 크래시의 시그널이 달랐다(6 / 11) = 힙 손상 |
| B-3/B-5 "깨진 엔트리 / orphan source object" | **틀림.** 두 source object 모두 존재하고 크기까지 일치 |
| "엔트리 하나가 목록 전체를 503 시키는가" (B-4 전제) | **전제가 다름.** 깨진 엔트리는 없다. 터지는 것은 fixture↔store 유일성 가드 |
| 업로드가 새 분석을 만들었을 가능성 (C 의 (나)) | **아님.** 09-04 에 분석 실행 이벤트 0건 |
| 내 이전 진술 "A-2 재현 실패" | **정정.** 이번에 재현되었다 (2026-09-05 01:49:52, SIGSEGV) |

---

## 6. 런타임 데이터에 대한 처리 — 정확히 무엇이 일어났는가

- **`protocol_workspace.sqlite`**: 진단 전후 sha256 **동일**
  (`9102701e0fd65ef8be6b5cce2ffc59ff18fddd880390bc9ad26db0a1fbe69866`),
  mtime 도 `2026-09-04 15:04:56` 그대로. 행 수 변화 없음(2/2/2/2/17/2).
  서버를 두 번 기동했지만 bootstrap 이 dedup 되어 **쓰기가 없었다.**
- **`commercial_workspace.sqlite`**: 크기 동일(651,264 bytes)이나 mtime 이
  `2026-09-04 15:06` → `2026-09-05 01:57:27` 로 바뀌었다. 종료 시 WAL checkpoint 때문이다.
  **사전 해시를 잡아두지 않아 바이트 동일성은 증명하지 못한다.** 내가 직접 쓴 것은 없고,
  호출한 것은 읽기 엔드포인트뿐이다.
- **PDF/objects**: 읽기만. 삭제·수정·이동 없음.
- **`/var/crash`**: 위 A-1 에 적은 대로 내 재현이 사용자의 9/4 리포트를 덮어썼다.
- **부수 효과 하나**: 진단 서버를 `0.0.0.0:8000` 으로 띄웠을 때, 열려 있던 브라우저 탭이
  자동 재접속해 `GET /`, `/app.css`, `WS /ws`, `/api/workspace/session`,
  `/api/workspace/connectors` 를 호출했다. 전부 읽기 엔드포인트다.
  이후 격리가 필요한 실험은 예비 포트 `127.0.0.1:8010` 에서 돌렸다.
- 시스템 설정은 바꾸지 않았다(E-5 해당 없음). 패키지도 설치하지 않았다(E-3 준수) —
  그래서 gdb backtrace 는 없다.
- 두 서버 모두 종료했고 포트 8000/8010 은 비어 있다.

---

## 7. 이번 STEP 에서 하지 않은 것

- 코드 변경 0건 (`git status --short` 공백). 따라서 기존 테스트에 대한 회귀 위험 없음.
- A-4, B-4, D-4 는 지시대로 **설계·결정·범위만**. 구현 없음.
- provider 호출 없음.
- `.env` 값 출력 없음. 필요한 곳은 "설정됨 / 판정 결과"로만 적었다.
- 런타임 DB·PDF 에 대한 쓰기·삭제 없음.

---

Provider 호출 횟수: 0
