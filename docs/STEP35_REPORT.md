# STEP 35 — 거절 경로 완화, 그리고 수집 가능 판정

## 결론 먼저

- **「수집 가능」** — 알려진 차단 원인(`declined_segment_states_a_value`)은
  제거되었고, **ord1·ord2 를 막았던 정확한 시나리오를 실제로 돌려 통과를 확인**했다.
  단, 그 뒤에 다른 거부가 숨어 있는지는 **미측정**이다 (아래 4-2).
- **캐시 불변 확인** — 3청크 digest 바이트 동일, 계약 상수 3개 불변.
  **다음 수집 2회.**
- 상한을 **파괴가 아니라 신호**로 만들었다. STEP 34 가 제안한
  `min(2, substantive//10)` 은 **측정 결과 폐기했다** — ord3·ord4 에서 **0** 이 나온다.
- 전체 스위트 **1433 passed** (기준 1425).

이번 STEP 의 provider 호출: **0회.**

---

## 작업 1. 상한 설계 재검토 — 수행함

### 1-1. ord1 의 declined 13건 내역 — **미측정. 복구 불가.**

`data/development_cache/provider_diagnostics/` 에는 STEP 32 의 dry-run 리포트
1개만 있고 거부 정보가 없다 (`validation=None reason=None offending=0`).
사용자의 수동 실행은 거부되어 캐시에 아무것도 남기지 않았다.

캐시 디스크에 **세그먼트 버전 5 시절의 ord1·ord2 payload 가 남아 있어** 읽어
보았으나(처분만, 원문 미출력), **declined 가 0건**이었다 — STEP 32 가 관측한
"모델이 decline 을 전혀 쓰지 않는다"와 일치하며, 사용자의 13건과는 다른 실행이다.

**따라서 13건 중 값 보유가 몇 개였는지는 추정하지 않는다.**

### 1-2. 상계 — 서버 자신의 분할기로 계산

| ord | core | substantive | 값 보유 | **값+단계내부** (거절 조건) | `substantive//10` | `min(2, 10%)` |
|---|---|---|---|---|---|---|
| 0 | 1,2,3 | 12 | 2 | **2** | 1 | 1 |
| 1 | 4,5,6 | 25 | 8 | **8** | 2 | 2 |
| 2 | 7 | 12 | 6 | **6** | 1 | 1 |
| 3 | 8 | 9 | 5 | **4** | 0 | **0** |
| 4 | 9 | 4 | 1 | **0** | 0 | **0** |

### 1-3. 판정 — **「청크 폐기」 상한은 채택 불가. 더 나쁜 것도 발견했다.**

- ord1 의 상계는 **8**, 제안 상한은 **2** → 4배 초과 가능. 수집 1회를 다시 잃는다.
- 그리고 `substantive // 10` 은 10개 미만 청크에서 **0** 이다.
  **ord3·ord4 의 상한이 0** 이므로, 값 하나만 declined 되어도 초과가 되어
  **이미 지불한 청크가 죽는다.** STEP 34 의 제안은 이 지점에서 틀렸다.
- → 작업 2 의 대안(신호 방식)을 채택했다.

---

## 작업 2. 방식 A 구현 — 수행함

### 2-1. 청크 폐기 → 조각 단위 블로커

`declined_segment_states_a_value` **제거.** 탐지 조건은 그대로다
(substantive ∧ 단계 내부 ∧ 값 보유). 달라진 것은 결과뿐이다:

- 거절 기록은 `declined_segment_ids` 에 **그대로 남는다** (기존 필드 재사용 —
  새 필드도, 스키마 변경도, 저장 형식 변경도 없다).
- `pages_declining_stated_values()` 가 그것을 집어 올려
  **`DECLINED_VALUE_NOT_RESOLVED`** readiness 사유를 만든다.
- 나머지 주장은 보존된다.

### 2-2. 상한 초과 = 신호

- **`EXCESSIVE_DECLINED_VALUES`** 신설. 청크를 폐기하지 않는다.
- 허용치: **`max(2, ceil(substantive × 0.25))`**
  - **1/4**: "이 페이지의 값 대부분이 지시가 아니다"가 페이지에 대한 *독해*이기를
    그치고 *주장*이 되는 지점. 특정 문서에서 역산하지 않았다.
  - **하한 2**: 작은 청크가 큰 청크보다 엄격한 기준을 받지 않게 한다. 순수 분수는
    바로 이 지점에서 실패한다(1-3).
  - 사후 sanity check (근거가 아니라 확인): 4문서 600 substantive 중 확정적 비지시
    값은 9개(1.5%), 외곽 상계 29개(≈5%). 둘 다 1/4 에 한참 못 미친다.
- **페이지 단위**로 센다. 페이지가 증거 원장의 기록 단위이고, merge 가 있는
  곳에서 항상 얻을 수 있는 유일한 단위다. 청크 단위는 chunk id 를 merge 를 통해
  readiness 까지 끌고 가야 하며, 다른 질문("이 실행이 부실했나")에 답한다.

### 2-3. fail closed 판정 — **「약화 없음」**

- 두 경로 **모두 실행을 막는다.** 안전 목적("사람이 보기 전에 실행되지 않는 것")은
  동일하게 달성된다.
- 탐지는 하나도 완화되지 않았다 — 같은 세 조건.
- 달라진 것은 **증거를 버리지 않는다**는 것뿐이다.
- `EXCESSIVE_DECLINED_VALUES` 도 해소 가능하게 두었다. 근거: 서명이 skim 한
  페이지를 읽은 페이지로 만들지는 않지만, 해소 경로가 없으면 게이트가 아니라
  **막다른 길**이 된다. 심각도 구분은 **별도 사유 코드**가 담당한다.

### 2-4. 검토자 노출 — 새 UI 없음

- 차단 사유 목록은 이미 일반적으로 렌더된다(`outstanding_blockers` 순회,
  "남은 차단 사유 N건"). 두 새 사유가 **자동으로 표시된다.**
- 해소는 기존 `acknowledge_gate` 라우트를 쓴다. `_ACKNOWLEDGEABLE_GATES` 와
  `_BLOCKER_RESOLUTION` 에 등록했다.

### 계약 감사 정리

`declined_segment_states_a_value` 가 사라졌으므로 두 감사 표에서 항목을 제거하고,
**어디로 갔는지를 기록했다**(조용히 지우지 않았다). 프롬프트는 여전히
"none of them may be declined" 라고 말하므로 **프롬프트가 서버보다 엄격**하다 —
안전한 방향이며, 감사 테스트가 그 문구가 계속 프롬프트에 있는지 확인한다.

---

## 작업 3. 캐시 불변 재확인 — 수행함 (구현 후)

| ord | digest | STEP 34 대비 | 로드 |
|---|---|---|---|
| 0 | `48d430ebae714b2e` | **동일** | ✓ |
| 1 | `5619f5cfdb314260` | 동일 | miss |
| 2 | `908626b61d9e9208` | 동일 | miss |
| 3 | `09b633223409fba5` | **동일** | ✓ |
| 4 | `a843eff59df66029` | **동일** | ✓ |

- `prompt_sha256` = `dca143b5b81ddffa…` **불변**
- `CLAIM_SCHEMA_VERSION` = 10 **불변**
- `EVIDENCE_SEGMENT_VERSION` = 6 **불변**

**다음 수집: 2회.**

---

## 작업 4. 수집 직전 사전 판정 — 수행함

### 4-1. 폐기 경로 추적 — **실행해서 확인했다**

추론이 아니라 실행이다. ord1·ord2 각각에 대해 계약 만족 응답을 오프라인으로
생성하고, **실제 실행을 막았던 바로 그 조각**(p.5 조각 7 / p.7 조각 10)을
declined 로 편집한 뒤 `parse_chunk_claim_response` 에 통과시켰다:

```
ord 1: ACCEPTED (36 claims kept, 1 dropped) | declined-value pages (5,) | over-allowance ()
ord 2: ACCEPTED (26 claims kept, 1 dropped) | declined-value pages (7,) | over-allowance ()
```

- **더 이상 폐기되지 않는다.**
- **무시되지도 않는다** — 블로커가 정확히 그 페이지에 붙는다.
- 1건은 허용치(ord1 7, ord2 3)에 한참 못 미친다.

이 시나리오를 **테스트로 고정**했다
(`tests/test_unaccounted_value_gate.py::TheRealRefusalScenarioTests`).
알려진 차단이 되돌아오면 수집 전에 실패한다.

declined 를 다루는 나머지 검증(`declination_malformed`,
`duplicate_declined_segment`, `declined_segment_not_on_page`,
`unknown_evidence_handle`, `coverage_mismatch`)은 **이 조각들의 내용 때문에
발동하지 않는다** — 위 실행이 그것을 함께 확인한다.

### 4-2. 판정: **「수집 가능」** — 단 조건을 명시한다

**알려진 원인은 제거되었다.** 그러나 사용자의 실행은 **첫 거부에서 멈췄으므로**,
그 뒤에 두 번째 거부(예: 라벨 9개에 대한 `numbered_action_missing`)가 있는지는
**측정된 바 없다.** 추측하지 않는다.

- 최선의 경우: 2회로 5/5 가 되어 작업 6 으로 넘어간다.
- 최악의 경우: 2회가 다른 사유로 거부된다. 그러나 이번에는 **리포트가 파일로
  남으므로**(STEP 32) 그 사유를 다음 STEP 에서 읽을 수 있다.
- 어느 쪽이든 **2회 이상 쓰지 않는다.**

### 4-3. 수집 명령 (실행하지 않았다)

```
! scripts/collect_chunks.sh data/runtime/candidate-a-source/in-gel-digestion.pdf 1 2
```

청크당 1회, 첫 거부에서 정지, 리포트 자동 저장, 통과분은 캐시에서 무료.

---

## 작업 5. 정답지 불일치 — 사용자 확인 「있음」 → 별도 커밋으로 반영

### 5-1 / 5-2. 커밋 `4268fda` (코드 커밋 `119b1f7` 과 분리)

| 항목 | 값 |
|---|---|
| 쪽 | 8 |
| segment_index | 0 |
| segment_id | `seg-1fae4bff6b74269af7870a4e550a70f0defd99a58e43986f35dc62238f09f56e` |
| 원문 | `Expected result … If the band is still transparent then repeat steps 17-18 until fully dehydrated.` |
| 서버 자신의 범위 판정 | `excerpt_states_range(…, "17","18")` = **True** |
| `step_id` | `candidate-a-step-18` (기존 8-9 항목의 관례 = 범위의 마지막 단계) |

**연쇄 변경 (국소적이지 않다):** fixture 의 canonical bytes 가 `fixture_sha256` 을
결정하고, **4개 sidecar 전부**가 그것을 선언하며 loader 가 불일치를 거부한다.

- `fixture_sha256`: `fb869290f1b52afa…` → **`f91fbd70e3f8fbe3…`**
- `revision_id`: `fixture-fb869290f1b52afab91f` → **`fixture-f91fbd70e3f8fbe3aa0b`**
- 갱신: localization.ko / provenance / timers / visuals + 고정 상수 1개(테스트)

**예상되는 결과**: 패치된 fixture 를 로드하면 **새 analysis revision 이 생긴다.**
STEP 22-B 가 지난번 이 바이트가 움직였을 때 기록한 것과 동일한 동작이다.
`data/runtime` 은 건드리지 않았다.

### 5-3. 채점 도구는 계속 "회귀 기준선" 을 출력한다
`"standing": "regression_baseline_not_absolute_accuracy"` + `baseline_caveat`.
**정답지가 고쳐졌어도 이 문구는 그대로 둔다** — 한 곳이 고쳐진 것이 나머지가
정확하다는 증거는 아니다.

---

## 작업 6. 수집 이후 작업 사전 준비 — 수행함 (실행 없음)

### 6-1. provider 없는 병합 경로 — **이미 있다**

`scripts/score_extraction.py` 가 캐시만으로 5청크를 병합·조립한다
(`merge_validated_chunk_results` → `assemble_validated_protocol_claims`).
파일에 provider client 도 `--execute` 도 없고, 테스트가 그것을 확인한다.

### 6-2. 병합 후 채점 명령

```bash
VOICE_WORKFLOW_AGENT_MOSS_ENABLED=false \
VOICE_WORKFLOW_AGENT_WORKSPACE_ENABLED=false \
VOICE_WORKFLOW_AGENT_EXPERIMENT_REPORTS_ENABLED=false \
python scripts/score_extraction.py \
  data/runtime/candidate-a-source/in-gel-digestion.pdf \
  --reference  data/development_protocols/candidate_a_curated_analysis.json \
  --provenance data/development_protocols/candidate_a_curated_analysis.provenance.json
```

산출물: `data/development_cache/accuracy/in-gel-digestion.accuracy.json` 과
`…merged-protocol.json`.

### 6-3. 수집이 성공해도 남을 사유 — 미리 열거

**A. 측정됨 (합성 5청크 병합 기준, 구조적 사유):**
| 사유 | 건수 |
|---|---|
| `no_declared_safety_warnings` | 1 |
| `unresolved_ambiguity` | 4 |
| `unsupported_repeat_until` | 2 |

**B. 측정됨 (실제 캐시 3청크의 coverage 기준):**
| 사유 | 해당 페이지 |
|---|---|
| `source_page_not_fully_read` | **8** |
| `declined_value_not_resolved` | 없음 |
| `excessive_declined_values` | 없음 |

**C. 미측정**: ord1·ord2 가 무엇을 추가할지. payload 가 없으므로 계산 불가.
(실제·합성 payload 를 섞으면 `claim_identity_conflict` 로 병합이 거부된다 —
두 생성기의 식별자가 충돌하기 때문이며, 실제 수집에서는 5청크가 같은 모델에서
나오므로 발생하지 않는다.)

### 6-4. 다음 STEP 작업 목록 — 우선순위

| 순위 | 항목 | 이유 |
|---|---|---|
| **1** | `source_page_not_fully_read` (p.8) 를 검토자가 해소하거나, 그 값이 실제로 무엇인지 확인 | 실행을 막는 사유이고, 원인이 이미 특정돼 있다 |
| **2** | `unsupported_repeat_until` **3건** — P1 능력 프로파일이 repeat-until 을 지원하지 않는다 | in-gel 의 핵심 구조이며 3건 전부 해당. 능력 확장 여부는 설계 결정 |
| **3** | `unresolved_ambiguity` 4건 — 검토자가 어느 진술이 권위인지 판정 | 기존 `resolve_ambiguity` 경로가 이미 있다 |
| **4** | `no_declared_safety_warnings` — 원본이면 `source_lineage` 배선 문제(결정 B 보류 중) | 결정 대기 |
| **5** | 29건 「무근거 귀속 위험」 탐지기 확장 (작업 7) | 정확도 문제이지 차단 사유는 아니다 |

---

## 작업 7. 「무근거 귀속 위험」 — 문서만 (코드 변경 없음)

### 7-1. 탐지기 확장안

현재 모순 탐지기는 `prompt_permits ∩ server_accepts = ∅` 만 본다. 확장은 세
번째 결과를 추가하는 것이다:

> 값을 가진 조각이 단계 N 의 영역 안에 있으면서 **N 의 라벨을 담은 조각이 아니다.**

이 경우 그 값이 N 을 수식한다는 근거가 없으므로, `duration`/`quantity` 등으로
붙이는 것은 **검증되지 않은 관계 주장**이다. 「모순」이 아니라
**「무근거 귀속 위험」**으로 별도 보고한다. 문서를 가리지 않는 구조 규칙이다.

### 7-2. 문서별 건수와, 방치될 경우의 실패 장면

| 문서 | 단계 내부 값 조각 | 라벨을 담은 조각 | **무근거 귀속 위험** | 확정적 비지시 |
|---|---|---|---|---|
| in-gel | 20 | 14 | **6** | 3 |
| headspace | 21 | 16 | **5** | 4 |
| intracellular | 12 | 4 | **8** | 2 |
| ANKOM | 34 | 24 | **10** | 0 |
| **합계** | **87** | **58** | **29** | **9** |

**방치될 경우의 실패 장면.** 실험자가 in-gel 7단계에 도착한다. 원문은 "밴드가
완전히 탈색될 때까지 2–7단계를 반복하라"고만 말한다. 그런데 그 단계의 영역 안에
다음 구획의 제목 「Reduction and alkylation of cysteines 1h」이 놓여 있고,
추출이 그 `1h` 를 7단계의 duration 으로 붙였다. 에이전트는 타이머를 1시간으로
걸고, 한 시간 뒤 단계를 완료로 안내한다. 밴드는 아직 파랗다. **원문에 없는
완료 기준이 생성되었고**(원칙 8), 실험자는 시스템이 원문을 읽어 준다고
믿었으므로 그것을 의심할 이유가 없다. 이번 STEP 의 완화는 이 조각이 declined
되었을 때 **청크를 죽이지 않게** 했지만, 모델이 declined 대신 **duration 으로
붙이는 쪽을 고르면 아무것도 막지 않는다** — 그 경로가 29건 남아 있다.

### 7-3. 코드 변경 없음.

---

## 작업 8. 보고

### 8-1. 작업별 상태

| 작업 | 상태 | 비고 |
|---|---|---|
| 1. 상한 재검토 | **수행함** | 1-1 은 **미측정**(자료 없음). 1-3 에서 제안 상한 폐기 |
| 2. 방식 A 구현 | **수행함** | 「약화 없음」. 상한은 신호로 |
| 3. 캐시 재확인 | **수행함** | 3청크 바이트 동일, 상수 3개 불변 |
| 4. 수집 직전 판정 | **수행함** | **「수집 가능」**, 실제 시나리오 실행 확인 + 테스트 고정 |
| 5. 정답지 | **수행함** | 사용자 확인 「있음」 → 별도 커밋 `4268fda` |
| 6. 수집 이후 준비 | **수행함** | 병합 경로 존재, 명령 문서화, 게이트 A/B 측정 · C 미측정 |
| 7. 29건 | **문서만** | 코드 변경 없음 |
| 8. 보고 | **수행함** | 이 문서 |

### 8-2. 전체 테스트: 기준 1425 → **1433 passed, 1210 subtests** (감소 없음)

### 8-3. provider 호출 실사용량: **0회**
`--execute` 와 `collect_chunks.sh` 를 실행하지 않았다. 잔여 승인 호출 **6회** 유지.

### 8-4. **「수집 가능」**
알려진 차단 원인은 제거되었고 실제 시나리오로 확인했다. 그 뒤의 두 번째 거부
가능성은 미측정이며, 이번에는 리포트가 파일로 남는다. **2회를 넘기지 않는다.**
