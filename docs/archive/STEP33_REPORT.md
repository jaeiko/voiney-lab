# STEP 33 — 거절 경로 확정, 그리고 구현하지 않은 이유

## 결론 먼저

- **작업 3-4 판정: 「약화 있음」 → 구현하지 않았다.**
  이유는 제안 자체가 나빠서가 아니라, 제안이 의존하는 보호 장치(3-1)가
  **아직 존재하지 않기 때문**이다. 그리고 조사 중에 그보다 큰 것이 나왔다.
- **누락 경로(unaccounted)에는 현재 어떤 강제도 없다.** 거절 경로만 막혀 있다.
  이 상태에서 거절 경로를 풀면 값 정직성 규칙은 **양쪽 다 무력**해진다.
- 사용자 결정 A·B 가 비어 있으므로 **작업 5 와 작업 7 구현은 하지 않았다.**

이번 STEP 의 provider 호출: **0회.**

---

## 작업 1. 거절 경로 확정 — 수행함 (측정 전용, 코드 변경 없음)

### 1-1. 두 코드의 위치

| 코드 | 위치 | 함수 | 조건 |
|---|---|---|---|
| `declined_segment_states_a_value` | `src/voice_workflow_agent/protocol_claim_analysis.py:2504` | `_validate_page_segment_accounting()` (2401–2516) | 거절된 조각이 **substantive** ∧ **단계 내부** ∧ **값 보유** |
| `unaccounted_segment_carries_a_value` | **존재하지 않음** | — | — |

두 번째 코드는 `src/`, `tests/`, `scripts/` 어디에도 없다. 지시문은 이것이
있다고 전제하지만 **구현된 적이 없다.** 이것이 이번 STEP 의 핵심 발견의 입구다.

### 1-2. 발생 시 나머지 주장의 운명

`fail()` 은 `ProtocolAnalysisEvidenceError` 를 raise 하고, 이는
`parse_chunk_claim_response` 를 통과하지 못한다는 뜻이다.
→ **그 청크의 주장은 전부 폐기된다.** 부분 보존은 없다.
(ord1 은 rep-7 `repeat_condition` `["2","7"]` 을 정상 산출했는데도 함께 버려졌다.)

반대로 **누락(unaccounted)** 은 raise 하지 않는다. 같은 함수가 값을 반환한다
(2513–2516행): 누락 조각 id 를 기록하고, `_derived_coverage` 가 그 페이지를
`analysis_incomplete` 로 표시한다. **청크는 통과한다.**

### 1-3. 판정식 (원문 그대로)

```python
# protocol_claim_analysis.py:2354
def segment_is_substantive(segment_text: str) -> bool:
    """True when a segment carries anything a claim could be about.
    Deterministic and vocabulary-free: one alphanumeric character is enough."""
    return bool(_SUBSTANTIVE.search(segment_text))

# protocol_claim_analysis.py:2360 (STEP 32 기준 라인)
def segment_carries_unit_bearing_value(segment_text: str) -> bool:
    return bool(_UNIT_BEARING_VALUE.search(segment_text))

# protocol_claim_analysis.py:2159
_UNIT_BEARING_VALUE = re.compile(
    r"(?<![A-Za-z0-9])(?<![A-Za-z0-9]-)[0-9]+(?:[.,][0-9]+)?\s*(?:"
    + "|".join(sorted((re.escape(u) for u in _VALUE_UNITS), key=len, reverse=True))
    + r")(?![A-Za-z0-9])", re.IGNORECASE)
```

거절이 걸리는 조건은 세 개의 **AND** 다 (2498–2504행):
`substantive` ∧ `inside_step` ∧ `carries_unit_bearing_value`.

### 1-4. 통과한 3청크의 unaccounted 14개 — 전수 판정

| 판정 | 개수 |
|---|---|
| substantive | **14 / 14** |
| 값 없음 | **13** |
| **값 있음** | **1** |
| 값 있음 **∧ 단계 내부** (= 거절 조건) | **0** |

값을 가진 1개는 ord3 / p.8 / 조각 2 (37자)이며 **모든 번호 단계 밖**에 있다.
따라서 **같은 조각을 거절했더라도 거절되지 않았을 것**이다 — 규칙이 두 처분을
동일하게 취급하는 위치다. 이는 STEP 30 이 하단 띠 사례를 근거로 의도적으로
좁힌 범위이며, **거절/누락 사이의 비대칭이 발현된 사례가 아니다.**

(원문은 인용하지 않았다. 판정과 개수만 보고한다.)

### 1-5. 안전 구멍 판정 — **비대칭은 이번 데이터에서 발현되지 않았다. 그러나 경로는 열려 있다.**

지시문의 문자 그대로는 "값을 가진 unaccounted 가 1개 있는데 거절되지 않았다" 가
맞다. 그러나 위와 같이 그 조각은 **거절해도 통과했을** 위치이므로, 그것 자체는
비대칭이 아니다. **추측으로 경보를 울리지 않는다.**

**진짜 구멍은 일반 경로에 있고, 이것은 측정으로 확인했다.**
`단계 내부 + 값 보유` 조각을 모델이 **누락**하면 아무것도 막지 않는다:

1. 2509행 주석은 누락이 *"blocks the whole-document merge just as firmly"* 라고
   적혀 있다. **이 주석은 낡았다.** STEP 28 이 merge veto 를 제거했고
   (`validate_whole_protocol_claims`, 3010–3033행: 페이지가 **없는** 것만 거부),
   주석은 갱신되지 않았다.
2. STEP 28 이 대신 둔 보호 장치는 실행 시점의 미판독 페이지 안내다. 그런데
   **`unread_pages` 를 채우는 코드가 `src/` 에 하나도 없다.**
   전 트리에서 대입은 `tests/test_unread_page_safety.py:126` **한 곳뿐**이다.
   `curated_protocol.py:4701` 은 `getattr(self.fixture, "unread_pages", None) or {}`
   로 읽기만 한다.
3. 판독 상태에 대응하는 **readiness reason code 가 없다.** 18개 중
   `unread` / `coverage` / `unaccounted` / `incomplete` 를 포함하는 코드: **0개.**

즉 누락 경로는 **표시는 되지만 아무것도 막지 않는다.** 이번 in-gel 실행에서
`단계 내부 ∧ 값 보유` 누락이 0건이었던 것은 다행이지 보장이 아니다.

**규칙 B 에 따라 보고한다: 작업 3 의 실패 원인은 작업 3 안에 있지 않다.**

---

## 작업 2. 막힌 조각 2개의 정체 — 수행함

### 2-1. 원문 (문서 원문이므로 출력 허용)

**p.5 조각 7 (41자)** — 값 보유 ✓, 단계 내부 ✓ (감싸는 라벨 **7**)
```
Reduction and alkylation of cysteines 1h
```

**p.7 조각 10 (15자)** — 값 보유 ✓, 단계 내부 ✓ (감싸는 라벨 **20**)
```
1h
45m
10m
15m
```

### 2-2. 판정

| 조각 | 판정 | 근거 |
|---|---|---|
| p.5 #7 | **(b) 지시가 아닌 값** | 소요 시간이 붙은 **구획 제목**이다. 동사도 대상도 없다. |
| p.7 #10 | **(b) 지시가 아닌 값** | 줄바꿈으로 나열된 **소요 시간 열**이다. 단위 글자 외에 글자가 없다. |

**모델의 판단이 옳았고 서버가 거부했다.** STEP 30 이 하단 띠(꼬리말)에서 고친
것과 같은 부류인데, 이 둘은 단계 **내부**에 있어 띠 규칙이 닿지 않는다.

### 2-3. in-gel 특수 형태인가 — **아니다**

같은 두 형태를 4문서 전체에서 셌다. 두 probe 는 **세는 도구이지 제안 규칙이 아니다.**
- probe A: 조각의 글자가 전부 단위 글자 (값만 나열된 열)
- probe B: 조각이 값으로 끝나고 문장 종결 부호가 없음 (제목 + 소요 시간)

| 문서 | 쪽 | 단계 내부 값 조각 | probe A | probe B | A∪B |
|---|---|---|---|---|---|
| in-gel | 9 | 20 | 2 | 1 | **3** |
| headspace | 16 | 21 | 3 | 3 | **5** |
| intracellular | 34 | 12 | 1 | 2 | **2** |
| ANKOM | 40 | 34 | 0 | 0 | **0** |
| **합계** | **99** | **87** | **6** | **6** | **10** |

**4문서 중 3문서에 존재한다.** 따라서 문서별 하드코딩(원칙 1·8)의 유혹은
성립하지 않는다 — in-gel 만의 형태였다면 그것이야말로 하드코딩 신호였을 것이다.
동시에 **10/87 은 일반 규칙으로 다루기에 충분히 흔하고, ANKOM 0건은 그 규칙이
문서를 가리지 않아야 함을 보여준다.**

---

## 작업 3. 설계 판정 — 3-1 ~ 3-4 수행함 / 구현 안 함

### 3-1. 미해결 블로커가 실행을 막는가 — **현재 보장 수단이 없다**

`protocol_catalog.py:1246` 이 결정한다:
```python
available = bool(approved and execution_ready)
```
`execution_ready` 는 `readiness == GUIDANCE_READY` 또는
`_readiness_gates_cleared(...)` (2394행) 이고, 후자는 **readiness reason code**
위에서만 동작한다. 판독/미해결 조각에 대응하는 reason code 는 **0개**다.

→ 제안대로 하려면 **새 `ReadinessReasonCode`** 를 만들고,
`_BLOCKER_RESOLUTION` 항목과 `_readiness_gates_cleared` 절을 추가해야 한다.
**지금은 없다.** 즉 3-1 은 "보장한다"가 아니라 "먼저 만들어야 한다"이다.

### 3-2. 청크당 상한 — 근거와 보수적 값

특정 문서를 보고 정하지 않는다. 근거만으로:
- 미해결 조각 1개 = 실험자가 **듣지 못하는 값** 1개. 손실은 조각 수에 선형이
  아니라, "이 프로토콜은 검토가 필요하다"는 신호가 희석되므로 그보다 나쁘다.
- 상한의 목적은 "가끔 있는 표·제목"과 "이 청크를 제대로 읽지 못했다"를
  가르는 것이다. 후자는 청크 폐기가 맞다.
- 값 정직성 규칙이 걸리는 조각은 4문서 87개 / 5청크 문서 기준 청크당 평균
  한 자릿수다. 상한이 그 평균에 가까우면 "제대로 못 읽음"을 걸러내지 못한다.

**제안값: `max_unresolved_segments_per_chunk = 2`, 그리고 청크의 substantive
조각 수의 10% 중 작은 쪽.** 2 는 관측된 A∪B 최대치(headspace 5)보다 작게 잡은
것이 아니라, **문서당이 아니라 청크당**이라는 점과 "예외는 드물어야 한다"는
원칙에서 나온 값이다. 상한 초과 시 기존대로 청크 전체 거절.
**이 값은 제안이며 구현하지 않았다.**

### 3-3. 기존 검토자 경로 재사용 — **부분 가능. 새 화면은 불필요.**

- 차단 사유 목록은 **일반적으로 렌더된다** (`index.html`: `outstanding_blockers`
  를 순회하며 "남은 차단 사유 N건"). 새 사유는 **자동으로 표시된다.**
- 그러나 **해소 동작**은 사유별이다: 라우트 4개
  (`findings/acknowledge-gate`, `confirm-repetition`, `revoke-repetition`,
  `resolve-ambiguity`) 가 각각 있고, UI 컨트롤도 `reviewer_action` 값으로
  분기한다.
- → **새 화면은 만들지 않아도 되지만**, 새 사유 코드 · `_BLOCKER_RESOLUTION`
  항목 · 해소 라우트 1개 · 컨트롤 1개가 필요하다.

### 3-4. fail closed 판정 — **「약화 있음」**

**따라서 구현하지 않았다.** 근거:

1. 제안은 거절 경로(현재 유일하게 강제되는 경로)를 **블로커 등록으로 완화**한다.
2. 그 완화가 안전한 것은 3-1 의 보장 — "블로커가 남아 있으면 실행 불가" — 이
   성립할 때뿐인데, **그 보장 수단이 아직 없다** (readiness reason code 0개).
3. 동시에 **누락 경로는 이미 아무것도 막지 않는다** (작업 1-5).
4. 3-1 을 먼저 만들지 않고 2 를 하면, 값 정직성 규칙은 거절에서도 누락에서도
   강제되지 않는다. 규칙이 **조언으로 격하**된다.

**순서가 뒤집혀 있다.** 안전하게 하려면:
**(1) 누락 경로에 강제를 붙인다** (reason code + 실행 게이트, 그리고
`unread_pages` 를 실제로 채운다) → **(2) 그 다음에** 거절 경로를 블로커로
완화한다. 그러면 두 경로가 같은 하나의 장치로 수렴하고, 완화가 손실이 아니다.

### 3-5. 사용자 결정 A: **비어 있음** → 3-4 까지만 보고했다. 구현하지 않았다.

---

## 작업 4. 캐시 영향 사전 측정 — 수행함

작업 3 을 구현하지는 않았으나, 측정은 지시대로 작업 5 보다 먼저 했다.
서버측 검증 완화를 **메모리에서 시뮬레이션**해 계산했다(디스크 변경 없음).

### 4-1. 키가 움직이는가 — **아니다**

캐시 키 필드: `cache_format_version`, `source_sha256`, `chunk_id`, `ordinal`,
`core_page_refs`, `context_page_refs`, `source_revision`,
`claim_schema_version`, `evidence_segment_version`, `prompt_sha256`,
`capability_policy_id`, `request_sha256`.
서버측 검증 완화는 **이 중 어느 것도 건드리지 않는다.**

### 4-2. 통과한 3청크가 살아남는가 — **살아남는다 (3 → 3)**

| ord | 완화 전 digest | 완화 후 digest | 동일 | 로드 전 | 로드 후 |
|---|---|---|---|---|---|
| 0 | `48d430ebae714b2e` | `48d430ebae714b2e` | ✓ | ✓ | ✓ |
| 1 | `5619f5cfdb314260` | `5619f5cfdb314260` | ✓ | ✗ | ✗ |
| 2 | `908626b61d9e9208` | `908626b61d9e9208` | ✓ | ✗ | ✗ |
| 3 | `09b633223409fba5` | `09b633223409fba5` | ✓ | ✓ | ✓ |
| 4 | `a843eff59df66029` | `a843eff59df66029` | ✓ | ✓ | ✓ |

완화는 **더 많은 payload 를 통과시키므로** 이미 통과한 것은 계속 통과한다.
`load()` 가 현재 규칙으로 재검증하는 구조 덕분이다.

### 4-3. 캐시를 죽이지 않는 구현이 가능한가 — **가능하다.**
프롬프트 텍스트 · `CLAIM_SCHEMA_VERSION` · `EVIDENCE_SEGMENT_VERSION` 을
건드리지 않는 순수 서버측 완화로 충분하다. **다음 수집 비용은 2회 그대로.**

### 4-4. 해당 없음 (4-3 이 가능하므로).

---

## 작업 5. 구현 — **미수행**

3-4 가 「약화 있음」이고 사용자 결정 A 가 비어 있다. 게이트를 통과하지 못했다.

---

## 작업 6. 정확도 채점 도구 — 수행함 (실행하지 않음)

### 6-1 / 6-2. `scripts/score_extraction.py`

**특정 문서에 묶지 않았다** (원칙 1): source · reference · provenance 를 전부
인자로 받는다.

채점 항목:
| 항목 | 출처 |
|---|---|
| 단계 개수 · 순서 · 누락 라벨 · 추가 라벨 | 기존 `score_extraction` |
| 단계별 텍스트 유사도 (최소/중앙/최대 + 최저 3개) | 기존 `score_extraction` |
| 값 일치 (시간/온도/부피, matching·contradicted·reference_silent) | 기존 `score_extraction` |
| **반복 구간 일치** | **신규** `_repeat_comparison` — 식별자가 아니라 **반복되는 라벨 범위**로 대조하고, 양쪽의 잉여를 각각 보고 |
| **근거 조각 주소 유효성** | **신규** `_evidence_addresses_resolve` — 인용된 모든 segment id 가 서버가 다시 계산한 조각과 맞는지 |
| 판독 불완전 페이지와 미계정 조각 수 | `merged.page_coverage` |

**호출하지 않는다.** 파일에 provider client 도 `--execute` 도 없다.
캐시가 부족하면 **거절하고 산술을 말한다** (실측):
```json
{"chunks_cached": 3, "chunks_total": 5, "missing_ordinals": [1, 2],
 "provider_calls_needed": 2, "reason": "cache_incomplete", "scored": false}
```

### 6-3. 이번 STEP 에서 실행하지 않았다. 단위테스트만 확인 — **4 passed, 7 subtests**
(`tests/test_score_extraction_tool.py`) — 도구가 호출 수단을 갖고 있지 않다는 것,
불완전 캐시 거절, 반복 대조, 깨진 근거 주소 검출.

### 6-4. curated fixture 를 정답으로 쓰는 것이 타당한가

**부분적으로만 타당하다.** 사람이 만든 5섹션 25단계는 구조·순서·라벨의 기준으로
쓸 만하지만 **정답(ground truth)이 아니다.** 이번 STEP 에서 직접 확인한 반례가
있다: 이 fixture 의 `RepeatUntil` 은 **2건**뿐이고, p.5 「2-7」과 p.6 「8-9」는
있으나 **p.8 「repeat steps 17-18 until fully dehydrated」가 빠져 있다.**
즉 반복 항목에서 추출이 3건을 모두 잡으면 채점기는 그것을 "추가로 생긴 것"으로
보고할 것이고, 그 판정은 **틀린 것이 추출이 아니라 기준선**이다. 그래서 채점기는
`audit_reference` 결과를 `reference_notes` 로 **점수와 분리해** 함께 내고,
반복 대조도 평균 내지 않고 `in_reference_only` / `in_candidate_only` 를 각각
보고한다. 이 fixture 는 **회귀 감지용 기준선**으로는 유효하고,
**절대 정확도의 근거로는 유효하지 않다.**

---

## 작업 7. source_lineage 결정 자료 — 수행함 (구현 금지 준수)

### 7-1. 두 저장소가 분리되어 있다는 제약 (파일:라인)

| | 카탈로그 | 워크스페이스 |
|---|---|---|
| DB 파일 | `protocol_workspace.sqlite` — `experiment_protocol_store.py:35` | `commercial_workspace.sqlite` — `workspace_store.py:29` |
| 경로 환경변수 | `VOICE_WORKFLOW_AGENT_PROTOCOL_DATA_DIR` — `experiment_protocol_config.py:12` | `VOICE_WORKFLOW_AGENT_WORKSPACE_DATA_DIR` — `workspace_store.py:132` |
| 리비전 테이블 | `protocol_revisions` — `experiment_protocol_store.py:106` | `protocol_lineage_revisions` — `workspace_store.py:305` |
| 부모 컬럼 | **없음** (experiment_id, revision_number, pdf_checksum, original_filename, created_at 뿐) | `parent_revision_id` — `workspace_store.py:310` |
| 계보 | 없음 | `family_id`, `protocol_adaptation_revisions` — `workspace_store.py:665` |

결정적: **워크스페이스는 통째로 꺼질 수 있다**
(`VOICE_WORKFLOW_AGENT_WORKSPACE_ENABLED`). 꺼져 있으면 계보 정보가 **존재하지
않는다.** 어떤 방안이든 그 경우 fail closed 여야 한다.

### 7-2. 최소 변경안 3가지

| | 방안 1 — 카탈로그 자체 판단 | 방안 2 — 읽을 때 교차 조회 | 방안 3 — 등록 시점에 기록 |
|---|---|---|---|
| **내용** | `revision_number == 1` 이면 원본 | readiness 계산 시 워크스페이스에 `pdf_checksum` 으로 계보를 묻는다 | `/api/protocols` 등록 시 ingest 결과를 카탈로그 리비전 행에 남긴다 (nullable 컬럼, 기본 NULL=unknown) |
| **(a) 변경 범위** | 매우 작음 — `protocol_catalog` 한 표현식 | 중간 — 카탈로그→워크스페이스 의존 1개 + 부재/비활성 시 fallback | 큼 — 스키마 마이그레이션 + 쓰기 경로 + 읽기 경로 |
| **(b) 안전 위험** | **높음.** 카탈로그의 `revision_number` 는 **한 experiment 안에서 PDF 재업로드 횟수**이지 프로토콜 계보가 아니다. 편집된 문서를 처음 올리면 revision 1 → **원본으로 오판하고 게이트를 건너뛴다** | **낮음.** 행이 없거나 워크스페이스가 꺼져 있으면 UNKNOWN → 게이트 유지. 다만 readiness 가 다른 저장소 상태에 의존하게 된다 | **가장 낮음(읽기 시점).** 교차 조회 없음, NULL 이 fail closed. 위험은 쓰기 경로가 틀렸을 때뿐 |
| **(c) 되돌리기** | 매우 쉬움 (한 줄) | 보통 (호출부 + fallback 제거) | 어려움 (스키마 되돌리기, 기록된 값 처리) |

**평가**: 방안 1 은 **채택해서는 안 된다** — 안전을 약화시키는 방향으로 틀린다.
방안 2 와 3 은 둘 다 fail closed 를 지킨다. 2 는 되돌리기 쉽고, 3 은 읽기
경로가 더 단순하다.

### 7-3. 사용자 결정 B: **비어 있음** → 구현하지 않았다.
현재의 fail closed 상태(모든 호출자가 `UNKNOWN` → 게이트 유지)를 유지한다.

---

## 작업 8. 보고

### 8-1. 작업별 상태

| 작업 | 상태 | 비고 |
|---|---|---|
| 1. 거절 경로 확정 | **수행함** | `unaccounted_segment_carries_a_value` 는 존재하지 않음을 확인 |
| 2. 막힌 조각 2개 | **수행함** | 둘 다 (b) 지시가 아닌 값. 4문서 중 3문서에 동종 10건 |
| 3. 설계 판정 | **수행함 (3-1~3-4)** | 판정 **「약화 있음」**. 결정 A 비어 있음 |
| 4. 캐시 영향 | **수행함** | 키 불변, 3청크 생존, 다음 수집 2회 유지 |
| 5. 구현 | **미수행** | 3-4 가 「약화 있음」 + 결정 A 비어 있음 |
| 6. 채점 도구 | **수행함 (실행 안 함)** | 도구 + 단위테스트 4 passed |
| 7. lineage 자료 | **수행함 (구현 금지 준수)** | 결정 B 비어 있음 |
| 8. 보고 | **수행함** | 이 문서 |

### 8-2. provider 호출 실제 사용량: **0회**
`collect_chunks.sh` 와 `--execute` 를 한 번도 실행하지 않았다.
잔여 승인 호출: **6회** (변동 없음).

### 8-3. 다음 수집에 필요한 호출 수: **2회** (ord 1, ord 2)
캐시 키가 움직이지 않으므로 통과한 3청크는 무료다. 다만 계약을 고치지 않은
상태로 다시 보내면 **같은 사유로 다시 거절될 가능성이 높다** — ord1·ord2 를
막은 두 조각(p.5 #7, p.7 #10)은 지금도 거절 조건을 그대로 만족한다.

### 8-4. 전체 스위트
기준 1409 → **1413 passed, 1208 subtests** (감소 없음, +4 는 작업 6 의 테스트).

---

## 다음 STEP 을 위한 권고 (구현하지 않음)

작업 3 을 하려면 순서를 뒤집어야 한다.

1. **먼저 누락 경로를 닫는다**: 판독 상태에 대응하는 readiness reason code 를
   만들고, `merged.page_coverage` → `unread_pages` 를 실제로 채우고,
   낡은 2509행 주석을 고친다. 이것은 **완화가 아니라 강화**이고 결정이 필요 없다.
2. **그 다음에** 거절 경로를 블로커로 완화한다. 그때는 두 경로가 같은 장치로
   수렴하므로 3-4 판정이 「약화 없음」으로 바뀔 수 있다.
3. 그러고 나서 ord1·ord2 를 2회로 수집한다.
