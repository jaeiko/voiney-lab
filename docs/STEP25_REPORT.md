# STEP 25 — in-gel chunk 1 (3회 소진, 미통과) · 게이트 불통과 · 검토자 경로 부재

작성: 2026-09-05 · 브랜치 `feature/readiness-safety-warning-gate`
런타임 PDF/DB 는 읽기만 했다. `.env` 값은 출력하지 않았다.

---

## 맨 앞 세 줄

**1) in-gel chunk 1: 통과하지 못했다. 호출 3회 사용(작업 1 배분 상한).**
세 번 모두 `canonical_validation: rejected`.

**2) 채점 결과: 없다.** 3청크 중 2개만 캐시에 모여 병합이 불가능하므로
작업 2를 **실행하지 않았다.** 단계 수·누락·순서·유사도 모두 **측정 없음** —
추측하지 않는다.

**3) 작업 3 게이트: 불통과.** (a) 단계 수 25 일치 · (b) 누락 라벨 0 · (c) 순서 일치
— **세 조건 전부 평가 불가**다. 세 조건 모두 작업 2의 병합·채점 산출물을 입력으로
받는데 그것이 존재하지 않는다. 따라서 **작업 4·5를 수행하지 않았고, 작업 5에
배분된 9회를 쓰지 않았다.** 총 사용 3회 / 승인 12회.

---

## 작업 1 — in-gel chunk 1 (4–8쪽)

### 1-1 캐시 유효성 먼저 확인 (0회) — 유효하다

```
chunk 0 pages=[1,2,3]        key=2e580b63c8f975c5 -> REVALIDATED  claims=10
chunk 1 pages=[4,5,6,7,8]    key=fff17373f69062a7 -> miss
chunk 2 pages=[9]            key=4da17cc0ca465d05 -> REVALIDATED  claims=7
key 안의 evidence_segment_version = 5, claim_schema_version = 10
```

두 항목 모두 **현재 규칙으로 재검증되어** 통과했다. 따라서 멈추지 않고 진행했다.

### 1-2 chunk 1 시도 — 3회 전부 실패

| 시도 | latency | tokens (prompt/completion) | claims | 판정 | 사유 코드 | stage / class | page |
|---|---|---|---|---|---|---|---|
| 1 | 51.5 s | 6020 / 8924 | 64 | rejected | `declined_segment_states_a_value` | `chunk_page_coverage_validation` / `segment_accounting_mismatch` | **6** |
| 2 | 41.3 s | 6020 / 7629 | 61 | rejected | `repetition_count_missing` | `chunk_claim_evidence_validation` / `claim_evidence_mismatch` | **5** |
| 3 | 46.5 s | 6020 / 7997 | 59 | rejected | `declined_segment_states_a_value` | `chunk_page_coverage_validation` / `segment_accounting_mismatch` | **6** |

세 시도 모두 action 라벨 21개를 냈다(3–24 범위). declined segments 는 각각
10 / 0 / 9. 자기보고 미완 페이지 0, `non_step_labels` 0 — 즉 라벨 인식이나
페이지 누락 문제는 아니다.

**반복 claim 은 세 번 모두 1건뿐이었다** (원문에는 3건이 있다):

| 시도 | claim_id | category | 선언 범위 | 선언 개수 | 인용 핸들 해결 | 증거가 범위를 진술하는가 |
|---|---|---|---|---|---|---|
| 1 | `rep-2-7` | `repeat_condition` | 2–7 | null | true | **true** |
| 2 | `c15` | **`fixed_range_repetition`** | 2–7 | **null** | true | true |
| 3 | `c16` | `repeat_condition` | 2–7 | null | true | true |

p.5 의 「7 Repeat steps 2-7 until the gel band is fully destained」 하나만 잡고,
p.6·p.8 의 나머지 두 건은 세 번 모두 잡지 못했다.

### 1-3 실패 사유가 같은가 — **2회는 같고 1회는 다르다. 같은 쪽을 별도로 파고들었다.**

- 시도 2는 **다른 사유**다. 「…until the gel band is fully destained」를
  `fixed_range_repetition` 으로 분류하고 `repetition_count: null` 을 냈다.
  조건 종료 반복을 고정 반복이라 부른 것이고, 원문에 개수가 없으므로 개수를
  채울 수 없었다. STEP 18 이 "고정이라 부르려면 개수를 말해야 한다"로 정한 규칙이
  정확히 그 오분류를 잡았다. 이 방향(조건→고정)은 STEP 18 이 **위험한 방향**으로
  분류한 쪽이고, 규칙이 의도대로 막았다.
- 시도 1·3은 **같은 사유**다. 지시대로 규칙 문제인지 별도로 조사했다(0회).

**조사 결과: 규칙이 옳고, provider 동작이 틀렸다.**

in-gel p.6 은 8개 세그먼트이고, 값 정직성 규칙의 검사 대상은 **정확히 2개**다:

```
[3] in_step=True value=True  CHECKED=True  '10 Prepare a solution of 1.5mg/mL of DTT 10 millimolar (mM) and 10mg/mL of iod…'
[6] in_step=True value=True  CHECKED=True  'Note You usually need around 50uL of volume to fully cover the gel band.'
[7] in_step=True value=False CHECKED=False 'protocols.io | https://dx.doi.org/… June 19, 2025 6/9'   ← 꼬리말, 값 없음
```

[3] 은 라벨 10 의 action claim 이 인용해야 하는 그 단계의 본문이므로 declined 일
수 없다(claim 이 인용한 세그먼트는 declined 목록에 들어가지 않는다). **소거법으로
문제의 세그먼트는 [6]이다** — 측정이 아니라 추론이며, 그렇게 표시한다.

[6]이 진술하는 `50uL` 은 **바로 앞 단계 11 「Add enough volume of the DTT solution
to fully cover the gel band.」 가 스스로는 말하지 않는 부피**다. 즉 이 Note 는
작업자가 필요한 유일한 수량이 적힌 곳이고, 그것을 declined 로 넘기면 그 값은
사라진다. 규칙은 정확히 옳은 것을 지켰다.

또한 **프롬프트에 이미 명시되어 있다** (측정: `declin` 8회 출현):

> "A segment you may not decline is decided by shape, not by meaning."
> "…a threshold, a limit, a condition, an interval, an elapsed time, a container
> size, a catalogue pack size, or a value you already claimed from an identical
> segment on another page all count, and **none of them may be declined**."
> "If such a segment carries nothing you can name as an instruction, claim it as
> a document-level claim of the category that fits the value, or as
> explicit_missing_ambiguous_value, **rather than declining it**."

따라서 prompt-schema parity 공백도 아니다. 규칙은 정확하고, 계약은 문장으로
쓰여 있고, 모델이 3회 중 2회 그것을 지키지 않았다. **"규칙 문제일 수 있다"는
가설은 이 케이스에서 반증되었다.**

### 부수 발견 (0회) — 코드 주석이 사실과 달랐고, 그것을 고쳤다

값 정직성 규칙의 STEP 21 narrowing 주석은 이렇게 적혀 있었다:

> "A footer is not in one, so it leaves scope without anything having to
> recognise a domain or a phrase. Measured … this takes the checked set from 96
> segments to 77."

**측정 결과 꼬리말은 단계 span **안에** 있다.** `step_block_ranges` 가 마지막
번호 단계에 페이지 끝까지 가는 span 을 주기 때문이다(in-gel p.6 실측:
`((0,283),(283,531),(531,763))`, 페이지 길이 763). 4개 문서 전수:

| 문서 | 검사 대상 세그먼트 | 그중 꼬리말을 포함한 것 |
|---|---|---|
| in-gel | 20 | **3** |
| headspace | 21 | **4** |
| intracellular | 12 | **1** |
| ANKOM | 34 | **1** |
| 합 | **87** | **9** |

특히 **narrowing 이 쓰여진 이유였던 headspace p.6 세그먼트 [9]
`'1h 30m protocols.io | … 6/16'` 은 `in_step=True, value=True, CHECKED=True`** —
여전히 검사 대상이고 여전히 거부 가능하다. narrowing 은 그 목적을 달성하지
못했다.

이 사실은 **작업 4의 설계도 바꾼다**: 하단 띠로 꼬리말을 **분리**해도
`step_block_ranges` 는 변하지 않으므로 꼬리말은 계속 scope 안에 남는다.
STEP 24 가 제안한 규칙만으로는 headspace p.6 케이스가 고쳐지지 않는다.
**필요한 것은 "반복되는 하단 띠를 마지막 단계 span 에서 제외하는 것"** 이고,
이것도 기하이며 문구가 아니다. 아직 하지 않았다.

주석이 측정과 어긋나 있었으므로 **주석을 정정했다**(유일한 코드 변경).
주석의 77 은 계층 라벨 인식 전 수치이고, 라벨이 늘면 단계 span 도 늘어 87 이
된 것이므로 두 수치는 모순이 아니다.

### 기존 검증된 동작에 대한 위험

없다. 규칙·프롬프트·스키마를 바꾸지 않았다. 프롬프트를 바꿨다면
`prompt_sha256` 이 달라져 **캐시된 chunk 0·2 가 무효**가 되었을 것이고,
그것은 3회를 쓰고도 아무 것도 남지 않는 결과가 된다. 그래서 재시도만 했다.
전체 스위트 1353 passed 유지.

---

## 작업 2 — 병합과 첫 채점  **실행하지 않음**

2-1 의 전제("3청크가 모두 캐시에 모이면")가 성립하지 않는다. 하네스가 스스로
거부한다: `walk.attempted = False`,
`reason = "2 of 3 chunks validated; merge requires every chunk"`.

따라서 2-2 채점, 2-3 수치 6종, 2-4 분리 보고, 2-5 파일 보존, 2-6 repeat_until
표현 보고를 **모두 실행하지 않았다.** 이 항목들에 대해 어떤 값도 보고하지 않는다.

**단, 2-6 에 대해 이번 3회에서 관측된 것만 사실로 적는다**(병합 결과가 아니라
거부된 청크의 구조 관측이다):

| 원문 | 세 시도에서의 표현 |
|---|---|
| p.5 「7 Repeat steps 2-7 until the gel band is fully destained」 | 3/3 잡음. 2회는 `repeat_condition`(옳음), 1회는 `fixed_range_repetition`(오분류, 개수 없어 거부) |
| p.6 「…If the band is still transparent then repeat steps 8-9 until fully dehydrated」 | **0/3 — 세 번 모두 claim 없음** |
| p.8 「…then repeat steps 17-18 until fully dehydrated」 | **0/3 — 세 번 모두 claim 없음** |

캐시에 남은 chunk 0·2 는 그대로 유효하다. **다음 STEP 에 1회만 있으면**
chunk 1 을 채워 3/3 이 되고 병합·채점이 가능하다.

---

## 작업 3 — 게이트 판정: **불통과**

| 조건 | 판정 | 이유 |
|---|---|---|
| (a) 단계 수 25 일치 | **평가 불가** | 병합된 프로토콜이 없어 단계 수가 존재하지 않는다 |
| (b) 누락 라벨 0건 | **평가 불가** | 같은 이유 |
| (c) 순서 일치 | **평가 불가** | 같은 이유 |

세 조건 중 하나도 만족을 확인할 수 없으므로 게이트는 불통과다.
지시대로 **작업 4·5를 수행하지 않고 멈췄으며, 남은 9회를 쓰지 않았다.**

### 무엇을 고쳐야 하는가

측정된 실패는 두 종류이고 처방이 다르다.

1. **`declined_segment_states_a_value` (2/3회, in-gel p.6 [6] `Note … 50uL …`)**
   — 규칙과 프롬프트는 옳다(§1-3). 남은 수단은 셋이고, 전부 provider 쪽이다:
   (i) 그냥 재시도(3회 중 1회는 이 실수를 하지 않았다 → 관측 빈도 2/3),
   (ii) 프롬프트에서 "Note" 로 시작하는 줄에 대한 오해를 줄이는 문장 추가 —
   **대가: `prompt_sha256` 이 바뀌어 캐시된 chunk 0·2 가 무효가 된다**,
   (iii) 거부 메시지에 문제 세그먼트 핸들을 담아 사람이 원인을 즉시 보게 하기
   (핸들은 서버 소유 신원이므로 로그 제약 위반이 아니다). (iii)은 호출 0회로
   가능하고 다음 시도의 진단 비용을 낮춘다.
2. **`repetition_count_missing` (1/3회, p.5)** — 규칙이 위험한 오분류를 잡은
   것이므로 고칠 것이 없다.

**권고**: 다음 STEP 에 chunk 1 에 최소 2회를 배분하고, 그 전에 (iii)을 0회로
구현한다. (ii)는 캐시 무효화 대가가 있으므로, chunk 1 이 (i)+(iii)으로 두 번
더 실패한 뒤에만 고려한다.

---

## 작업 4 — 하단 띠 규칙  **실행하지 않음** (게이트 불통과)

지시대로 수행하지 않았다. 다만 §1-3 의 부수 발견은 이 작업의 **설계를 수정**한다:

- STEP 24 가 측정한 분리 효과(in-gel 6건/headspace 6건/intracellular 8건/ANKOM
  11건, 떼어지는 조각 78–88자)는 그대로 유효하다.
- **그러나 분리만으로는 4-5 의 목표가 달성되지 않는다.** headspace p.6 의
  `1h 30m protocols.io | …` 을 자기 세그먼트로 떼어내도, 그 세그먼트는 여전히
  마지막 단계 span 안이고 값을 담고 있어 값 정직성 규칙의 검사 대상이다
  (측정: 현재 `CHECKED=True`).
- 따라서 작업 4는 **두 부분**이어야 한다: (1) 하단 띠를 세그먼트 경계로 삼기,
  (2) 하단 띠를 `step_block_ranges` 의 마지막 span 에서 제외하기. (2) 없이
  (1)만 하면 4-5 검증이 실패한다.

---

## 작업 5 — headspace 5조각  **실행하지 않음** (게이트 불통과)

호출 0회. 배분된 9회를 쓰지 않았다.

---

## 작업 6 — 검토자 경로 실사용 준비 (0회)

### 6-1 UI 절차 — **그런 절차가 없다.**

안전 경고 확인과 고정 반복 확인을 **UI 에서 수행할 방법이 존재하지 않는다.**
서버를 띄워 필요한 경로를 전수 조회했다(6-4):

```
GET 200  POST 405  /api/protocols/{id}/review                                  ← 읽기만
GET 404  POST 405  /api/protocols/{id}/readiness-gates
GET 404  POST 405  /api/protocols/{id}/revisions/{rev}/readiness-gates
GET 404  POST 405  /api/protocols/{id}/revisions/{rev}/acknowledge-gate
GET 404  POST 405  /api/protocols/{id}/revisions/{rev}/findings
GET 404  POST 405  /api/protocols/{id}/revisions/{rev}/fixed-repetitions
GET 404  POST 405  /api/protocols/{id}/revisions/{rev}/confirm-fixed-repetition
GET 404  POST 405  /api/protocols/{id}/revisions/{rev}/resolve-ambiguity
```

카탈로그 계층에는 네 조작이 **모두 구현되어 있고 테스트도 되어 있다**
(`acknowledge_readiness_gate`, `resolve_ambiguity`, `confirm_fixed_repetition`,
`revoke_fixed_repetition_confirmation` — 테스트 모듈 8개가 사용). 그러나
**HTTP 로 노출된 것이 하나도 없다.** 호출자는 `scripts/diagnose_provider_chunk.py`
와 `scripts/walk_pdf_end_to_end.py`, 즉 Python 스크립트뿐이다.

UI 쪽도 확인했다: `static/index.html` 의 검토자 버튼은
`activate-development` 하나이고, 그 버튼은
`review.development_activation_allowed !== true` 이면 숨겨진다. 그 플래그는
게이트가 이미 해제되어 있어야 참이 되는데, 해제하는 경로가 없다.
**즉 UI 검토자 경로는 입구가 없는 닫힌 고리다.** 실측:
`development_activation_allowed: false`, `readiness_gates_cleared: false`.

`/review` 는 사람이 **읽어야 할 것은 전부** 준다: `readiness.reasons`(사유별
증거 포함), `constructs`(`RepeatUntil` 의 `repetition_id`,
`condition_source_text`, `repeated_step_ids`), `gates`, `declared_safety_warning_count`,
`hazard_review_required`, `declined_segment_count`, `page_coverage`.
**쓰기만 없다.**

또한 `/review` 는 `reviewer_actions: ["review_hazards","approve","reject"]` 를
내보내는데, 이 셋 중 `review_hazards` 와 `reject` 는 **엔드포인트가 없고**,
`approve` 는 존재하지만 docstring 이 "Service-authorized approval; deliberately
absent from the public UI" 라고 적어 둔 서비스 토큰 경로다. 즉 서버가 UI 에
할 수 있다고 알리는 세 조작 중 UI 가 할 수 있는 것은 0개다.
(이 필드는 프론트엔드도 테스트도 소비하지 않는다 — 측정으로 확인. 그래서
살아 있는 결함은 아니고, 오해를 부르는 표시다.)

**필요한 절차를 적으라는 지시에 대해**: 현재 UI 에는 없으므로, 사람이 지금
할 수 있는 유일한 방법을 적는다 — 서버 프로세스 안의 Python 에서
카탈로그 메서드를 직접 호출하는 것. 실험자 앞에 놓을 수 있는 절차가 아니다.

### 6-2 원장 기록 — **기록된다** (라이브러리 계층에서 실측)

임시 store 에서 두 조작을 실제로 수행했다:

```
protocol_readiness_gate_acknowledged   decision=acknowledged          actor=reviewer@example.org role=reviewer at=2026-09-05T14:58:36.660484+00:00
protocol_fixed_repetition_confirmed    decision=fixed_count_confirmed actor=reviewer@example.org role=reviewer at=2026-09-05T14:58:36.668216+00:00
```

시작 상태 `reasons = ['no_declared_safety_warnings','unconfirmed_fixed_repetition']`,
`gates_cleared = False`, `available_for_execution = False`.
안전 게이트 acknowledge 후에도 여전히 False → 고정 반복 확인까지 하면 **True**.
즉 **두 조작이 모두 필요하고, 하나로는 열리지 않는다.**

### 6-3 철회 — **가능하고, 철회하면 다시 차단된다** (실측)

```
revoke_fixed_repetition_confirmation → gates_cleared: True → False
                                       available_for_execution: False
두 번째 철회               → 거부: "This analysis revision carries no confirmation to revoke."
철회 후 재확인             → gates_cleared: True (새 식별자로 기록, 충돌 없음)
원장 이벤트 총 5건 (append-only, 삭제 없음)
```

### 6-4 서버 실제 시도 — 실행했고 종료했다

`nohup bash scripts/run_candidate_a.sh` 로 배경 실행, 13초 후 기동,
위 경로 전수 조회, 그 뒤 종료 확인(`port 8000 closed`).
런타임 DB sha256 불변
(`9102701e0fd65ef8be6b5cce2ffc59ff18fddd880390bc9ad26db0a1fbe69866`).

### 이번 STEP 에서 고칠지 — **고치지 않는다.** 근거

1. **지금 고쳐도 아무것도 열리지 않는다.** 작업 3 게이트가 불통과라 headspace 는
   이번 STEP 에 도달하지 못한다. 검토자 경로가 오늘 생겨도 통과시킬 문서가 없다.
2. **권위를 만드는 외부 표면이다.** 이 네 조작은 안전 게이트를 해제한다.
   scope·permission 배선을 틀리면 STEP 23 이 막은 것과 같은 종류의 구멍
   (사람 확인 없이 게이트가 열리는 경로)이 생긴다. in-gel 루프가 아직 열려 있는
   상태에서, end-to-end 로 한 번도 통과시켜 보지 못한 채 얹는 것은
   FAIL CLOSED 원칙에 어긋난다.
3. **버튼 하나가 아니다.** `confirm_fixed_repetition` 은 리뷰어가
   `evidence_segment_ids` 를 **인용**하도록 요구한다(인용 없는 확인은 거부된다 —
   테스트로 고정). 즉 UI 에 세그먼트 선택 affordance 가 필요하고, 그것은
   라우트 4개보다 큰 작업이다.
4. 그래서 **라우트 + 페이로드 + UI + 테스트를 한 묶음으로** 별도 STEP 에서
   하는 편이 안전하다. 부분적으로 얹으면 "권한은 열렸는데 인용은 못 하는" 중간
   상태가 남는다.

**남은 위험을 명시한다**: 이 상태에서는 **어떤 문서도 실험자 앞에 놓을 수 없다.**
in-gel 은 `unsupported_repeat_until` 로 사람도 열 수 없고(STEP 22-B D-3),
headspace 는 사람이 열 수 있는 게이트만 남지만(§5-5 대상) **여는 수단이 없다.**
제품 목표에 대한 단일 최대 차단 요인은 지금 추출 정확도가 아니라 **검토자 경로의
부재**다.

### 6-5 참고 — headspace 가 실행 가능해지기까지 사람이 풀어야 할 항목

작업 5를 실행하지 않았으므로 headspace 의 분석은 없다. STEP 24 §0-1 에서
코드로부터 결정론적으로 도출된 것만 다시 적는다(분석이 있어야 아는 3개는
여전히 **확인 못 함**):

1. `no_declared_safety_warnings` — `acknowledge_readiness_gate` 1회
2. `unconfirmed_fixed_repetition` — `confirm_fixed_repetition` **3회**
   (p.5 「Repeat steps 12-15 twice more」, p.6 「Repeat steps 23-26 for the metal
   plates」, p.12 「repeat steps 36-41 twice more (three conditioning rounds in
   total)」), 각각 개수와 인용 세그먼트를 함께
3. 그 뒤 `activate-development` 1회

**총 5회의 사람 조작이 필요하고, 그 중 4회는 현재 UI 에서 불가능하다.**

---

## 내 지시 중 4개 문서에서 성립하지 않은 것

| 지시 | 어디가 왜 |
|---|---|
| 4-5 "하단 띠 규칙으로 headspace 6페이지 세그먼트가 분리되는지 확인" | 분리는 되지만 **목표가 달성되지 않는다.** 분리해도 그 세그먼트는 마지막 단계 span 안에 남아 값 정직성 규칙의 검사 대상이다(측정: 4개 문서 87개 검사 대상 중 9개가 꼬리말 포함). 작업 4는 span 제외까지 포함해야 한다 — 설계 수정으로 보고 |
| 1-3 "같은 사유가 반복되면 규칙 문제일 수 있다" | 2/3회 반복했으나 **규칙 문제가 아니었다.** 문제의 세그먼트가 담은 `50uL` 은 단계 11 이 스스로 말하지 않는 유일한 부피이고, 프롬프트가 그 금지를 다섯 문장으로 명시하고 있다 → provider 동작 문제 |
| 5-2 "`declined_segment_states_a_value` 가 6페이지에서 또 나오면 작업 4가 못 고친 것" | 작업 5를 실행하지 않았지만, **in-gel p.6 에서 실제로 그 사유가 났고**(2/3회) 작업 4는 그것을 고칠 수 없다 — in-gel p.6 의 문제 세그먼트는 꼬리말이 아니라 `Note … 50uL` 이다. 지시의 전제(그 사유 = 꼬리말 문제)가 in-gel 에서는 성립하지 않는다 |

---

## 실행한 것 / 실행하지 않은 것

**실행**: 1-1 캐시 재검증(0회), 1-2 chunk 1 3회 시도, 1-3 사유 비교 + 반복 사유
근본 원인 조사(0회), 부수 발견 측정 4개 문서 전수(0회), 코드 주석 정정,
6-1~6-4 검토자 경로 전수 조사 + 서버 실측(0회), 전체 스위트.

**실행하지 않음**: 작업 2 전체(병합·채점·파일 보존), 작업 3 게이트 통과 판정
(평가 불가), 작업 4 전체, 작업 5 전체, 검토자 라우트/UI 구현.

**측정 없음으로 남긴 것**: in-gel 단계 수·누락 라벨·추가 라벨·순서·텍스트 유사도
분포·값 일치/불일치/채점 불가 — 병합이 없어 존재하지 않는다.

전체 스위트 **1353 passed, 1150 subtests**, 실패 0.
코드 변경은 사실과 어긋난 주석 정정 1건뿐이다.

---

Provider 호출 횟수: 3
