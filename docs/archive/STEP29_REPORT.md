# STEP 29 — 계약 감사 자동화, 판독 불완전 페이지 안전망

작성: 2026-09-06 · 브랜치 `feature/readiness-safety-warning-gate`
`--execute` 가 든 명령을 한 번도 실행하지 않았다. 런타임 DB 는 읽기만 했다.

---

## 맨 앞 세 줄

**1) 계약 감사: 거부 코드 총 57개.** 근거 종류는 PROMPT 35 / SCHEMA 6 /
SERVER_ONLY 16. **이 STEP 시작 시점(HEAD)에 진술되지 않았던 것은 0개다** —
STEP 26·28 이 마지막 두 개(`request_handle`, `protocol_title`)를 이미 막았기
때문이다. 감사의 값은 소급이 아니라 **다음 것을 잡는 데** 있다.
(감사 도중 내가 잘못 기록한 2건을 감사 자신이 잡아냈다 — §2-4.)

**2) 합성 응답으로 조립은 끝까지 간다.** 25단계까지 조립되고 채점기까지
통과한다. **남은 관문은 없다.** 호출을 쓰기 전에 확인해야 했던 것이 이것이다.

**3) in-gel 을 닫는 데 최소 4회.** 캐시에 유효한 청크는 **1개**(ord3).
기대값은 추정으로 4–10회(§4). **승인 잔량은 1회이므로 이번 조건으로는 닫을 수
없다.**

---

## 작업 1 — 판독 불완전 문서의 실행 안전망 (구현함)

STEP 28 이 커버리지 veto 를 풀어 열어 둔 위험을 닫았다.

### 1-1 도달했을 때의 동작

`CuratedProtocolFixture.unread_pages`(페이지 번호 → 그 페이지의 미처리 세그먼트
id)를 추가하고, 세션에 다음을 붙였다.

| 메서드 | 하는 일 |
|---|---|
| `step_is_on_an_unread_page(index)` | 이 단계의 증거 페이지가 미판독인가 |
| `unread_page_disclosure(index)` | 고지 문구 + **페이지 원문 그대로** + **미처리 세그먼트 원문 그대로** |
| `may_report_step_complete(index)` | 미판독 페이지에서는 **거짓** |
| `acknowledge_unread_page(page, actor…)` | 실험자가 원문을 확인했음 |
| `unread_pages_awaiting_acknowledgement()` | 아직 확인 안 된 페이지 |

고지는 요약하지 않는다. 이 고지가 존재하는 이유가 **시스템의 읽기를 믿을 수
없다**는 것이므로, 그 자리에 시스템의 읽기를 대신 내놓는 것이 정확히 하면 안
되는 일이다. 미처리 세그먼트의 원문은 저장된 id 로 서버가 원문에서 복원한다.

`may_begin_step` 을 확장해, 미판독 페이지의 단계는 확인 전에는 시작되지 않는다.

### 1-2 음성만으로 (장갑 낀 손)

`acknowledge_unread_page` 는 페이지 번호와 사람만 받는다. 화면 조작이 필요 없고,
`provide_operator_repetition_count` 와 같은 형태다. **실험 세션 사실이며 검토자
원장에 섞지 않는다**: 세션 객체에만 살고, `reset()` 에서 지워진다(새 실행은 다시
읽는다). 게이트를 하나도 해제하지 않는다.

### 1-3 안전 상태를 참으로 만들지 못함 (테스트로 고정)

- 확인 전후로 `assess_readiness` 의 status 와 reason_codes 가 **동일**하고,
  `no_declared_safety_warnings` 는 그대로 남는다
- `ReadinessReasonCode` 에 판독/커버리지 관련 코드가 **0개**임을 단언
- `_BLOCKER_RESOLUTION` 에 `unread_page` 가 없음을 단언

### 1-4 판독 완전한 페이지는 불변 (회귀 테스트)

`unread_pages=None` 인 문서에서 25단계 전부에 대해
`step_is_on_an_unread_page=False`, `disclosure=None`,
`may_report_step_complete=True`, `may_begin_step=True`.
한 페이지가 미판독일 때 **다른 페이지는 영향받지 않음**도 단언한다.

테스트 13건(`tests/test_unread_page_safety.py`), 전부 통과.

---

## 작업 2 — 계약 감사 자동화 (구현함)

### 2-1 전수 수집 — **57개**

`src/voice_workflow_agent/claim_contract_audit.py` 가 **구문 트리에서** 수집한다
(grep 이 아니라). 세 가지 호출 형태를 덮는다: `reason_code=` 키워드,
`ProtocolClaimConsistencyError`/`ProtocolChunkMergeError` 의 첫 인자,
지역 `fail("code", ...)` 헬퍼. **파일 경계를 넘고 조립 단계를 포함한다** —
`protocol_title_missing_or_conflicting` 이 그 예다.

수집된 57개는 4개 모듈에 흩어져 있다: `protocol_claim_analysis.py`,
`experiment_protocol_analysis.py`, `protocol_chunk_analysis.py`,
`semantic_intent.py`.

### 2-2 / 2-3 판정 방식 — 문자열 검색이 아니다

코드마다 **어디에 진술되어 있는지를 명시적으로 연결**하는 표(`CONTRACT_EVIDENCE`)를
두었다. 근거는 세 종류다.

| 종류 | 뜻 | 검사 |
|---|---|---|
| `PROMPT` | 프롬프트에 쓰여 있다 | 기록한 문구가 **원문 그대로** 프롬프트에 있어야 한다 |
| `SCHEMA` | 스키마가 위반을 말할 수 없게 만든다 | 기록한 스키마 경로가 **실제로 존재**해야 한다 |
| `SERVER_ONLY` | provider 가 고를 수 있는 것이 아니다 | 왜 provider 잘못일 수 없는지를 적어야 한다 |

키워드 검색이었다면 `repeat_range_inverted` 가 프롬프트의 "repeat" 를 찾아내고
아무것도 증명하지 못했을 것이다.

**새 코드를 넣고 표에 넣지 않으면 테스트가 실패한다**
(`test_every_refusal_code_says_where_its_rule_was_stated`). 반대로
**없어진 코드의 낡은 항목도 실패한다** — 낡은 항목 뒤에 진짜 공백이 숨을 수
있기 때문이다.

### 2-4 지금 시점의 감사 결과

**HEAD 기준 진술되지 않은 요구: 0개.**

측정 방법: HEAD 의 프롬프트를 git 에서 꺼내, PROMPT 근거로 기록한 35개 문구가
그 시점 프롬프트에 있는지 전수 대조했다.

**다만 감사는 실행 중에 내 오기 2건을 잡았고, 그것이 이 장치의 실제 값이다.**

1. `repeat_range_not_in_evidence` — 내가 새 문장이라고 생각해 프롬프트에
   추가했는데, **이미 다른 표현으로 쓰여 있었다**:
   「The cited evidence must contain those two labels written as a range: two
   numbers joined by a hyphen or dash, such as 2-7.」
   → 추가한 문장을 **되돌렸다.** 그 덕에 `prompt_sha256` 이 HEAD 와 같아져
   **캐시에 남아 있던 ord3 이 살아남았다**(§4).
2. `label_disposition_not_accepted` — 프롬프트가 아니라 **스키마**가 막고 있다
   (`page_coverage.items.additionalProperties: false`). 근거 종류를 SCHEMA 로
   고쳤다.

**감사의 한계를 명시한다**: 이 검사는 "문구가 있다"를 증명하지, "그 문구가 규칙을
완전히 진술한다"를 증명하지 않는다. 지난 세 번의 결함은 전부 **0회 언급** 종류였고
그 종류는 확실히 잡는다. 미묘한 과소 진술은 잡지 못한다.

### 2-5 반영

되돌린 결과 **프롬프트·스키마 변경은 순 0**이다. 따라서 **캐시를 무효화하지
않았다.** (§2-4 의 1번 덕분이다.)

### 2-6 반대 방향 (목록만, 고치지 않음)

프롬프트 문장 중 의무를 진술하는 것과, 기록된 거부 코드 문구가 가리키지 않는
것을 세었다: 의무 문장 중 **26건**이 어떤 기록 문구와도 매칭되지 않는다.

**이 수치는 과대다** — 매칭이 내가 기록한 정확 문구로만 이루어지므로, 다른 표현으로
검사되는 것이 전부 여기 들어온다(위 1번이 바로 그 사례였다). 확인 없이
"서버가 검사하지 않는다"고 말하지 않는다. 목록은 코드에 남겼고 다음 STEP 에서
한 건씩 확인한다. 이번에 고치지 않았다.

---

## 작업 3 — 조립까지 dry run (0회, 구현·측정함)

### 3-1 / 3-2 — **끝까지 간다. 남은 관문 없다.**

계약을 만족하는 합성 응답(`ExactNumberedStepClaimModel`)으로 5청크를 채워
in-gel 을 조립했다. 결과: **25단계**, `page_coverage` 9페이지 전부.
`incomplete_source_coverage` 도 `protocol_title_missing_or_conflicting` 도
나타나지 않는다. **호출을 쓰기 전에 찾아야 했던 남은 관문은 0개다.**

### 3-3 채점 경로 — 통과한다

`score_extraction` + `audit_reference` 가 조립 결과를 읽는다:
`reference_steps=25`, `candidate_steps=25`, `order_matches=True`,
`steps_unscorable_on_values` 가 공개 dict 에 실린다.
**합성 입력이므로 점수 자체는 의미가 없다. 경로만 확인했다.**

### 3-4 판독 불완전 표시가 조립 결과에 실린다

`merged.page_coverage` 가 원본의 모든 페이지를 갖고, `analysis_incomplete` 인
페이지는 **비어 있지 않은 `unaccounted_segment_ids`** 를 갖는다 —
표시되고, 주소가 남고, 그 id 로 원문이 복원된다.

---

## 작업 4 — in-gel 재수집 비용 (0회)

### 4-1 현재 캐시 — **5개 중 1개 유효**

```
ord=0 [1,2,3] miss    ord=1 [4,5,6] miss    ord=2 [7] miss
ord=3 [8]     CACHED  structure_markers=2   ord=4 [9] miss
```

ord3 은 STEP 28 의 사고 호출로 **새 계약(구조 마커 의무 포함)** 아래 들어왔고,
구조 마커 2개를 갖고 있다.

### 4-2 이번 계약 변경이 다시 무효화했는가 — **아니다**

§2-4 에서 프롬프트 추가를 되돌려 `prompt_sha256` 이 HEAD 와 동일해졌다
(`aab24123cca52262`). ord3 은 살아 있다.

### 4-3 필요한 호출 수

- **최소 4회** (ord0, ord1, ord2, ord4).
- **기대값 4–10회 (추정)**. 근거와 그 한계:
  STEP 27 에서 측정된 "호출당 완결 청크" 비율은 2/5 = 0.4 였고, 그대로 외삽하면
  4/0.4 = 10회다. 그러나 그 5회는 **구조 마커 의무가 없던 계약** 아래였고,
  당시 미완결의 원인(미처리 세그먼트 누락, 구조 마커 부재)은 지금 둘 다
  프롬프트에 진술되어 있다. 그래서 실제 비율은 더 높을 것으로 보이지만
  **새 계약에서의 provider 데이터는 1건(ord3, 통과)뿐**이라 추정의 신뢰구간을
  말할 수 없다.
- **승인 잔량 1회.** 이 조건으로는 in-gel 을 닫을 수 없다.

---

## 작업 5 — Bedrock 구조화 출력 (0회, 문서 근거)

AWS 공식 문서
(`https://docs.aws.amazon.com/bedrock/latest/userguide/structured-output.html`)
기준. **Bedrock 호출은 하지 않았다.**

### 5-1 (a) `strict: true` 스키마 강제 — **된다. 두 가지 방식이 있다.**

- **JSON Schema output format**: open-weight 모델은 `response_format`,
  Converse API 는 `outputConfig.textFormat`, Anthropic 은 `output_config.format`.
- **Strict tool use**: 도구 정의에 `strict: true`.
- 스키마는 **JSON Schema Draft 2020-12 의 지원 부분집합**으로 검증되고,
  지원하지 않는 기능이 있으면 **즉시 400**.

### 5-1 (b) `const` — **지원된다**

문서의 지원 목록에 `const`, `anyOf`, `allOf`(제한 있음)가 명시되어 있다.
즉 STEP 26 의 `request_handle` 고정은 Bedrock 경유로도 그대로 성립한다.

### 5-1 (c) / (d) — **그러나 우리 스키마는 그대로는 거부된다**

우리 스키마를 문서의 미지원 목록과 대조해 측정했다(in-gel chunk 1 기준,
5,722 바이트):

| 우리가 쓰는 것 | Bedrock |
|---|---|
| `pattern` (7회) | **미지원** (문자열 제약) |
| `minLength` (1회) | **미지원** |
| `minimum` (3회), `maximum` (1회) | **미지원** (수치 제약) |
| `maxItems` (6회) | **미지원** |
| `minItems` 값 1, **2, 3** | 0 과 1 만 지원 → **2·3 은 미지원** |
| `additionalProperties: false` (7회) | 지원 (false 만) |
| `const` (6회), `enum` (6회), `$ref`/`$defs` 내부 참조 | 지원 |
| `oneOf` (1회) | **미확인** — 지원 목록에 `anyOf`/`allOf` 는 있으나 `oneOf` 는 없다 |

즉 **가용성은 위험이 아니지만 이식은 드롭인이 아니다.** 위 5종을 걷어내면
서버 측 검증이 그만큼 더 일해야 한다(스키마가 막던 것을 파싱 후에 잡게 된다).
`oneOf` 는 페이지별 evidence 분기의 핵심이므로 **가장 먼저 확인해야 할 항목**이다.

기타 차이: 새 스키마는 최초 1회 문법 컴파일에 최대 수 분이 걸리고, 성공한 문법은
계정 단위로 24시간 캐시된다. **우리 스키마는 청크마다 다르다**(handle enum 이
들어간다) — 즉 매 호출이 새 문법일 수 있고, 그 지연이 얼마인지는 **미확인**이다.

### 미확인으로 남긴 것

`oneOf` 지원 여부 · 청크별 새 스키마의 컴파일 지연 실측 · Bedrock 경유 Grok 4.6
의 오류 본문 형식 · xAI 직접 호출과의 응답 필드 차이. **추측하지 않는다.**

---

## 내 지시 중 성립하지 않은 것

| 지시 | 어디가 왜 |
|---|---|
| 2-4 "진술되지 않은 요구가 몇 개인지" (묵시적 전제: 여러 개 있다) | **0개다.** STEP 26·28 이 마지막 둘을 이미 막았다. 감사가 실제로 잡은 것은 **내 기록 오류 2건**이었고, 그중 하나를 고치면서 캐시를 지켰다 |
| 2-5 "반영이 캐시를 무효화하면 명시한다" | 반영할 것이 없었으므로 **무효화하지 않았다** |
| 3-2 "조립이 또 다른 관문에서 막히면" | 막히지 않는다. 관문 0개 |

---

## 기존 검증된 동작을 깨뜨릴 위험

- **작업 1**: `CuratedProtocolFixture` 에 필드 하나를 **기본값 None 으로** 추가했고,
  `unread_pages` 가 없는 기존 fixture 의 동작은 전부 이전과 같다(회귀 테스트로
  고정). `may_begin_step` 이 한 조건 늘었으나 미판독 페이지가 없으면 이전과
  동일한 값을 낸다.
- **작업 2**: 검사만 추가했고 런타임 동작을 바꾸지 않는다. 위험은 **감사 표가
  형식적으로 채워질 수 있다**는 것이다 — 그래서 PROMPT 는 원문 대조, SCHEMA 는
  경로 해석, SERVER_ONLY 는 이유 서술을 각각 강제한다. 그래도 "문구가 규칙을
  완전히 진술하는가"는 사람이 봐야 한다(§2-4 한계).
- **프롬프트·스키마**: 순 변경 0. 캐시 무효화 없음.
- **런타임 DB**: 쓰기 없음.
- 전체 스위트 **1383 passed / 1192 subtests**, 실패 0.
  `compileall`, `git diff --check` 통과.

---

## 실행한 것 / 실행하지 않은 것

**실행**: 작업 1 전부(+테스트 13), 작업 2 전부(+테스트 7/19 subtests),
작업 3 전부(+테스트 3), 작업 4 전부, 작업 5-1 (a)(b)(c)(d) 문서 근거 확인과
우리 스키마 대조.

**실행하지 않음**: 2-6 의 26건 개별 확인(목록만), Bedrock 실호출,
provider 호출(0회), `--execute` 명령.

**미확인으로 남긴 것**: `oneOf` 지원 여부, 청크별 스키마 컴파일 지연,
Bedrock 오류 본문 형식.

---

Provider 호출 횟수: 0
