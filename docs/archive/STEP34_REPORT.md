# STEP 34 — 누락 경로를 닫고, 거절 원인을 확정한다

## 결론 먼저

- **작업 1 완료.** 값을 빠뜨린 페이지는 이제 실행을 막는다. 새 사유
  `source_page_not_fully_read`, `unread_pages` 프로덕션 배선, 검토자 해소 경로.
  **캐시는 움직이지 않았다** — 3청크 생존, 다음 수집 **2회** 그대로.
- **작업 2 판정: 「범주 부재」가 맞다. 단 문자 그대로 0개는 아니다.**
  서버가 받아 주는 범주는 있지만 **전부 원문에 없는 주장을 해야 한다.**
  가장 덜 해로운 것(`explicit_missing_ambiguous_value`)조차 표를 모호성으로
  오기재한다.
- **작업 3 재판정: 「약화 없음」.** 작업 1 이 3-1 이 요구한 보장을 실제로
  만들었기 때문이다. 구현안은 제시하되 **이번 STEP 에서 구현하지 않았다.**
- 전체 스위트 **1425 passed** (기준 1413).

이번 STEP 의 provider 호출: **0회.**

---

## 작업 1. 누락 경로 닫기 — 수행함

### 1-1. 사전 측정 — 「청크 거절」 방식은 채택 불가

"값 보유 unaccounted 가 1개라도 있으면 청크를 거절한다"를 켜면:

| ord | digest | unaccounted | 값 보유 | 값+단계내부 | 규칙 A | 규칙 B |
|---|---|---|---|---|---|---|
| 0 | `48d430ebae714b2e` | 8 | 0 | 0 | 생존 | 생존 |
| 3 | `09b633223409fba5` | 4 | **1** | 0 | **사망** | 생존 |
| 4 | `a843eff59df66029` | 2 | 0 | 0 | 생존 | 생존 |

**규칙 A 는 3청크 중 1개를 죽인다.** 지시대로 채택하지 않았다.
(규칙 B = "값 보유 ∧ 단계 내부"는 0개를 죽이지만, 그것은 이번 데이터가
운이 좋았던 것이지 보장이 아니다.)

### 1-2. 채택한 방향 — 청크 거절이 아니라 **실행 차단**

| 구성 요소 | 위치 | 내용 |
|---|---|---|
| 판독 상태 도출 | `protocol_claim_analysis.py` `unaccounted_segments_by_page()` / `pages_stating_unaccounted_values()` | merge 자신의 coverage + 서버 자신의 분할기에서만 도출. provider 주장은 보지 않는다 |
| readiness 사유 | `experiment_protocol.py` `ReadinessReasonCode.SOURCE_PAGE_NOT_FULLY_READ` | 값이 미계정인 페이지가 1개라도 있으면 발생 |
| 조립 배선 | `assemble_experiment_protocol()` | merge 와 조립된 Protocol 을 **동시에 쥔 유일한 지점**이므로 여기서 도출해 넘긴다. 카탈로그가 저장하는 readiness 가 이미 사유를 갖는다 |
| 실행 차단 | `protocol_catalog.py` `available = bool(approved and execution_ready)` | 사유가 살아 있으면 `_readiness_gates_cleared` 가 False |
| 검토자 해소 | `_ACKNOWLEDGEABLE_GATES` + `_BLOCKER_RESOLUTION` (`acknowledge_gate`) | "기계가 자기 한계를 보고한 것"이라 사람이 해소할 수 있는 부류. 감사 기록에 남고, 조각 id 는 계속 주소로 남는다 |
| **`unread_pages` 배선** | `protocol_catalog.load_executable_fixture()` | **STEP 32 가 찾아낸 구멍을 메웠다.** 이전에는 전 트리에서 대입이 테스트 파일 한 곳뿐이었다 |

**위치 조건을 거절 규칙만큼 좁히지 않은 이유**: 거절은 "이 조각에 주장할 것이
없다"는 **명시적 판단**이고, 단계 밖에서는 그 판단을 모델이 내려도 된다
(STEP 30 하단 띠). **침묵은 판단이 아니므로** 같은 여유를 주지 않는다.
그래서 값 보유 unaccounted 는 위치와 무관하게 게이트를 올린다. 더 엄격한 쪽이다.

**실측 확인** (실제 캐시된 coverage):
- unaccounted 가 있는 페이지: `[1, 2, 3, 8, 9]`
- 그중 **값**이 미계정인 페이지: **`(8,)`** ← 게이트가 실제 데이터에서 발동한다

### 1-3. 캐시 영향 — **움직이지 않았다**

| ord | digest | STEP 33 대비 | 로드 |
|---|---|---|---|
| 0 | `48d430ebae714b2e` | 동일 | ✓ |
| 1 | `5619f5cfdb314260` | 동일 | miss |
| 2 | `908626b61d9e9208` | 동일 | miss |
| 3 | `09b633223409fba5` | 동일 | ✓ |
| 4 | `a843eff59df66029` | 동일 | ✓ |

`prompt_sha256` = `dca143b5b81ddffa…` (STEP 31 과 동일),
`CLAIM_SCHEMA_VERSION` = 10, `EVIDENCE_SEGMENT_VERSION` = 6 — **전부 불변.**
순수 서버측 변경이다. **다음 수집 2회 유지.**

### 1-4. 낡은 주석 수정

`protocol_claim_analysis.py` 의 해당 주석에서
*"which blocks the whole-document merge just as firmly"* 를 제거하고,
**그 문장이 STEP 28 이전 세계를 서술한 것이며 STEP 28 이 merge veto 를
제거하면서 남겨진 것**임을 명시했다. 그리고 이제 결과가 어디에 있는지
(`pages_stating_unaccounted_values` → `assess_readiness`)를 가리키게 했다.

### 1-5. 테스트 — `tests/test_unaccounted_value_gate.py` **12 passed**

- 값 미계정이 있으면 사유가 붙고 `available_for_execution` 이 False
- **안전 게이트만 해소해서는 부족하다** (이 사유가 따로 서 있다)
- 해소 + 승인 후 실행 가능해진다
- 값이 없는 미계정 페이지는 게이트를 올리지 않는다 (게이트 페이지 ⊊ 미완료 페이지)
- dict 형태 coverage(카탈로그 저장 형식)와 객체 형태가 같은 답을 낸다
- 잘못된 페이지 값(True, "9", None)은 페이지로 세지 않는다
- **`load_executable_fixture` 가 `unread_pages` 를 실제로 채운다**, 그리고
  세션의 안내 의무가 그것을 근거로 발동한다
- 캐시 키가 readiness 를 이름에 담지 않는다 / 계약 상수 3개 불변

---

## 작업 2. 모델이 왜 거절했는지 — 수행함 (측정 전용)

### 2-1. 계약이 허용하는 범주 (15개)

`material`, `equipment`, `action`, `quantity`, `concentration`, `temperature`,
`duration`, `agitation_speed`, `prerequisite`, `warning_hazard`,
`observation_checkpoint`, `repeat_condition`, `fixed_range_repetition`,
`operator_determined_repetition`, `explicit_missing_ambiguous_value`

### 2-2. 막힌 조각 2개에 대한 범주별 판정

**측정된 사실:**

| | p.5 조각 7 | p.7 조각 10 |
|---|---|---|
| 원문 | `Reduction and alkylation of cysteines 1h` | `1h / 45m / 10m / 15m` |
| 단계 영역 안인가 | **예** (감싸는 라벨 **7**) | **예** (감싸는 라벨 **20**) |
| 감싸는 단계의 지시문 | `7 Repeat steps 2-7 until the gel band is fully destained.` | `20 Remove and discard the acetonitrile. Your gel band should have a whitish appearance` |
| 그 지시문이 이 값을 말하는가 | **아니오** | **아니오** |
| document-level 이 금지되는가 | **예** — 단계 안이므로 `document_level_claim_scope_invalid` | **예** |
| top-level(material/equipment/prerequisite) 가능한가 | **아니오** — `top_level_claim_scope_invalid` | **아니오** |
| `explicit_missing_ambiguous_value` 허용되는가 | **예** (required=true, target=단계 action) | **예** |

**범주별 판정:**

| 범주 | 서버가 받는가 | 참인가 |
|---|---|---|
| `duration` → 단계 action | **받는다** | **거짓.** 단계 7 은 "탈색될 때까지 반복"이라 고정 소요시간이 없다. 1h 를 붙이면 실험자에게 **없는 완료 기준**을 준다 (원칙 8 위반) |
| `quantity`/`concentration`/`temperature`/`agitation_speed` | 받는다 | 거짓 — 그 종류의 값이 아니다 |
| `action` | 라벨이 없어 불가 | — |
| `material`/`equipment`/`prerequisite` | **거부** (top-level 은 단계 안에서 불가) | — |
| `warning_hazard` | 받는다 | 거짓 — 위험 문구가 아니다 |
| `observation_checkpoint` / `repeat_condition` | 받는다 | 거짓 |
| 반복 3종 | 범위가 없어 불가 | — |
| **`explicit_missing_ambiguous_value`** | **받는다** | **거짓이지만 안전하다.** 원문은 모호하지 않고 빠진 값도 없다. 표 항목을 모호성 목록에 올린다 |
| **거절** | **거부** ← 실제로 일어난 일 | **참** |

### 2-3. 결함 유형 판정 — **「범주 부재」. 단 "0개"가 아니라 "참인 것이 0개"**

STEP 30 모순 탐지기는 옳았다. **수는 있다** — `explicit_missing_ambiguous_value`
가 열려 있다. 그리고 STEP 30 이 고친 탈출구는 **바로 그것을 하라고 지시한다**:
*"claim it anyway rather than declining it, as the category that fits the value
or as explicit_missing_ambiguous_value."*

그러나 **참인 수는 없다.** 이 조각이 실제로 무엇인지 —
「어느 단계에도 속하지 않는, 구획 제목/표에 적힌 값」— 을 말할 범주가 없다.
모델은 거짓 주장(위험한 것과 안전한 것 모두)과 거절 사이에서 거절을 골랐고,
서버가 그것을 막았다.

**모순 탐지기 확장안 (이번 STEP 구현하지 않음):**

현재 탐지기는 `prompt_permits ∩ server_accepts = ∅` 만 본다. 확장은
**"수는 있으나 전부 원문에 없는 관계를 주장해야 하는 경우"**를 세 번째 결과로
추가하는 것이다. 완전한 판정은 의미 판단이라 기계화할 수 없지만, 다음은 구조적
으로 계산 가능하다:

> 값을 가진 조각이 단계 N 의 영역 안에 있으면서, **단계 N 의 라벨을 담은
> 조각이 아니다** (= 지시문 자체가 아니라 그 뒤의 주석·표·제목이다).

이 경우 그 값이 단계 N 을 수식한다는 보장이 없으므로, `duration` 등으로
붙이는 것은 **검증되지 않은 관계 주장**이다. 탐지기는 이것을 「모순」이 아니라
**「무근거 귀속 위험」**으로 별도 보고하면 된다. 문서를 가리지 않는 구조 규칙이다.

### 2-4. 4문서 전수 적용

| 문서 | 단계 내부 값 조각 | 라벨을 담은 조각(=지시문) | 그 뒤의 조각 | 그중 A∪B 형태 |
|---|---|---|---|---|
| in-gel | 20 | 14 | 6 | 3 |
| headspace | 21 | 16 | 5 | 4 |
| intracellular | 12 | 4 | 8 | 2 |
| ANKOM | 34 | 24 | 10 | 0 |
| **합계** | **87** | **58** | **29** | **9** |

- **58개는 문제가 없다** — 조각 자체가 지시문이므로 값이 그 단계의 것이 맞다.
- **29개는 위험 구간** — 값이 그 단계의 것인지 보장되지 않는다.
- **9개는 확정적으로 지시가 아닌 값**이다 (STEP 33 의 A∪B 형태).
- **ANKOM 은 0** — 문서를 가리는 규칙이 아니라, 문서마다 다른 것뿐이다.

---

## 작업 3. 거절 경로 완화 재판정 — 수행함 (구현 안 함)

### 3-1. 재판정: **「약화 없음」**

STEP 33 에서 「약화 있음」이었던 이유는 단 하나였다 — 제안이 기대는 보장
("블로커가 남아 있으면 실행 불가")이 **존재하지 않았다.** 작업 1 이 그것을
만들었고 테스트로 고정했다:

| STEP 33 시점 | 지금 |
|---|---|
| 판독 상태 readiness reason code 0개 | `source_page_not_fully_read` 존재 |
| `unread_pages` 배선 없음 | `load_executable_fixture` 가 채움 |
| 누락 경로 무강제 | 값 미계정이 실행을 막음 |
| 해소 경로 없음 | `acknowledge_gate` 로 해소 가능, 감사 기록 |

이제 거절 경로를 완화해도 **같은 하나의 장치로 수렴**하므로, 완화가 손실이 아니다.

### 3-2. 구현안 (제시만, 구현하지 않음)

1. `declined_segment_states_a_value` 를 **청크 폐기에서 조각 등록으로** 바꾼다.
   그 조각을 `unaccounted_segment_ids` 와 **같은 원장**에 넣어
   `pages_stating_unaccounted_values` 가 집어 올리게 한다. 새 사유 코드를
   또 만들지 않는다 — 두 경로가 하나의 게이트로 모이는 것이 요점이다.
2. **청크당 상한**: `max_unresolved_segments_per_chunk = 2`, 그리고 그 청크의
   substantive 조각 수의 10% 중 **작은 쪽**. 초과 시 기존대로 청크 전체 거절.
   근거: 상한의 목적은 "가끔 있는 표·제목"과 "이 청크를 제대로 못 읽었다"를
   가르는 것이다. 특정 문서의 관측치에서 역산하지 않았다.
3. **검토자 노출**: 새 화면을 만들지 않는다. 차단 사유 목록은 이미 일반적으로
   렌더되고, 해소는 기존 `acknowledge_gate` 라우트를 쓴다. 조각 id 가
   `unread_pages` 를 통해 실행 시점 안내로도 흐른다.
4. **캐시 영향: 없음.** 순수 서버측 완화이므로 프롬프트·스키마·세그먼트 버전이
   그대로다. 다음 수집 **2회 유지**.

### 3-3. 완화 vs 계약 수정 — 비교

작업 2 가 「참인 범주 없음」으로 판정했으므로, 근본 해법은 **그 위치에서 쓸 수
있는 참인 범주를 여는 것**일 수 있다 (예: 어느 단계에도 귀속되지 않는 값을
표현하는 범주, 또는 단계 안이면서 그 단계를 수식하지 않는 값의 표현).

| | 방식 A — 서버측 완화 (3-2) | 방식 B — 계약 수정 (범주 신설) |
|---|---|---|
| 무엇을 고치나 | 증상: 옳은 판단이 청크를 죽이는 것을 막는다 | 원인: 모델이 참인 말을 할 수 있게 한다 |
| 결과물 품질 | 그 조각은 **미해결로 남는다.** 사람이 매번 본다 | 그 조각이 **정확히 기술된다.** 사람이 볼 필요가 준다 |
| 87개 중 대상 | 미해결 누적(최대 29) | 같은 29를 실제로 표현 |
| **캐시** | **살아남는다** | **죽는다.** 프롬프트 변경 → `prompt_sha256` 변경 → 3청크 전부 무효 |
| **다음 수집 비용** | **2회** | **5회** |
| 위험 | 미해결이 쌓이면 검토 피로 → 형식적 해소 | 새 범주가 또 다른 오용 경로가 될 수 있음. 재수집으로 재검증 필요 |
| 되돌리기 | 쉬움 | 어려움 (프롬프트 되돌리면 캐시가 또 죽는다) |

**권고 (결정은 사용자)**: A 를 먼저 하고, 2회로 ord1·ord2 를 수집해
**나머지 두 청크의 거부 사유를 실제로 확인한 뒤** B 를 판단한다. B 를 먼저
하면 5회를 쓰고도 그 5회가 새 계약을 검증할 뿐, 지금 막힌 것이 정말 범주 부재
때문인지는 여전히 한 번밖에 확인하지 못한다.

---

## 작업 4. curated fixture 불일치 — 증거만 제출 (수정하지 않음)

### 4-1. 증거

| 항목 | 값 |
|---|---|
| 페이지 | **8** |
| segment_index | **0** |
| segment_id | `seg-1fae4bff6b74269af7870a4e550a70f0defd99a58e43986f35dc62238f09f56e` |
| page_text_sha256 | `721713a99235eee3aa961da410720894…` |
| 원문 | `Expected result The gel should look white (dehydrated) as seen in the above picture. If the band is still transparent then repeat steps 17-18 until fully dehydrated.` |
| 서버 자신의 범위 판정 `excerpt_states_range(…, "17", "18")` | **True** |

`candidate_a_curated_analysis.json` 의 `RepeatUntil`: p.5 `[2..7]`, p.6 `[8,9]`.
**8쪽에 해당하는 것이 없다.**

### 4-2. **fixture 를 수정하지 않았다.** 사람이 확인해야 한다.

### 4-3. 채점 도구가 이 fixture 를 "회귀 기준선"으로만 쓰도록 조정

- 모듈 docstring 에 위 증거를 그대로 적었다.
- 출력 payload 에 `"standing": "regression_baseline_not_absolute_accuracy"` 와
  `baseline_caveat` 를 넣었다. **거절 응답에도 넣었다.**
- 반복 대조는 이미 `in_reference_only` / `in_candidate_only` 로 **양쪽을 따로**
  보고하며 상계하지 않는다. `audit_reference` 는 점수와 분리해 나온다.

---

## 작업 5. 결정 B 반영 — 수행함

### 5-1. 배선하지 않았다. 현재 fail closed 유지
모든 호출자가 `source_lineage` 를 넘기지 않아 `UNKNOWN` → 수정본과 동일하게
안전 게이트가 걸린다.

### 5-2. 방안 1 **영구 배제** — 근거를 코드에 남겼다
`experiment_protocol.py` 의 `SourceLineage` docstring 에 기록:

> 카탈로그의 `revision_number` 는 **한 experiment 안에서 PDF 가 등록된 횟수**이지
> 프로토콜 계보가 아니다. 편집된 문서를 처음 업로드하면 새 experiment 의
> revision 1 이 되고, 이 규칙은 그것을 **원본으로 판정해 안전 게이트를 통과시킨다**
> — 실수가 절대 가서는 안 되는 방향이다. 싸고, 한 줄이고, 틀렸다.

### 5-3. 방안 2·3 은 보류
- **방안 2** (읽을 때 교차 조회): 되돌리기 쉬움. readiness 가 다른 저장소 상태에
  의존하게 됨.
- **방안 3** (등록 시 기록): 읽기 경로가 단순, NULL 이 fail closed. 스키마
  마이그레이션 필요.
둘 다 워크스페이스가 꺼진 경우 fail closed 여야 한다. **구현하지 않았다.**

---

## 작업 6. 보고

### 6-1. 작업별 상태

| 작업 | 상태 | 비고 |
|---|---|---|
| 1. 누락 경로 닫기 | **수행함** | 1-1~1-5 전부. 캐시 불변 확인 |
| 2. 거절 원인 확정 | **수행함** | 「범주 부재」— 단 "참인 범주 0개"이지 "범주 0개"가 아니다 |
| 3. 완화 재판정 | **수행함 (구현 안 함)** | 「약화 없음」. 구현안·상한·캐시 비용 제시 |
| 4. fixture 불일치 | **수행함** | 증거 제출. **수정하지 않음.** 도구 문구 조정 |
| 5. 결정 B | **수행함** | 방안 1 코드에 영구 배제 기록 |
| 6. 보고 | **수행함** | 이 문서 |

### 6-2. 전체 테스트: 기준 1413 → **1425 passed, 1208 subtests** (감소 없음)

### 6-3. provider 호출 실사용량: **0회**
`--execute` 와 `collect_chunks.sh` 를 실행하지 않았다. 잔여 승인 호출 **6회** 유지.

### 6-4. 다음 수집에 필요한 호출 수
- **계약을 바꾸지 않으면: 2회** (ord1, ord2). 캐시 3청크 생존 확인 완료.
- **계약을 바꾸면 (방식 B): 5회.** `prompt_sha256` 이 바뀌면 3청크 전부 무효.

**주의**: 계약을 바꾸지 않고 2회를 지금 쓰면, ord1·ord2 를 막은 두 조각은
여전히 거절 조건을 만족하므로 **같은 사유로 다시 거절될 가능성이 높다.**
작업 3 의 방식 A 를 먼저 적용하면 그 2회가 실제로 청크를 얻는다.
