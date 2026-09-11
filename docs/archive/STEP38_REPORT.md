# STEP 38 — 병합 성공, 그리고 첫 정확도 점수

## 요약 네 줄

1. **병합 성공.** 25단계 / 라벨 1–25 빈틈 없음 / 순서 일치 / **근거 주소 51건 전부 resolve.**
2. **첫 정확도 점수를 얻었다.** 텍스트 유사도 중앙값 **0.667**, 값 일치 **23 / 25**,
   반복 구간 **1 / 3**.
3. **원칙 13 에 걸리는 지점이 있었고, 테스트를 고치지 않고 설계를 좁혀서 해결했다.**
   claim id 만 개명하고 **marker id 는 개명하지 않는다.** 두 fail closed 테스트는
   **한 줄도 바꾸지 않았다.**
4. **STEP 34·35 가 경고했던 「무근거 귀속」이 실제로 발생했다.** 24단계가 구획
   소요시간 「16h 30m」을 자기 duration 으로 흡수했다.

provider 호출 **0회**, 잔여 **4회**. 캐시 5청크 **전부 생존**.
전체 스위트 **1458 passed** (기준 1445).

---

## 작업 1. ①-확장

### 1-1. 구현 — **claim id 만** 개명한다

근거: target 참조 **45/45 가 청크 내부**, 청크 넘김 0건. 모델은 보지 못한 청크의
주장을 지목할 수 없으므로, 청크 단위 개명 + 그 청크 참조 동시 수정으로
**모호해지는 참조가 구조적으로 없다.**

### ⚠ 원칙 13 에 걸린 지점 — 그리고 해결 방법

처음에 **모든 id**(marker 포함)를 개명하도록 구현하자
`test_valid_but_conflicting_section_identity_fails_closed` 와
`test_valid_chunk_conflict_persists_merge_conflict_and_no_candidate` **두 개가
깨졌다.** 둘 다 **marker id 재사용**이 fail closed 함을 증명하는 테스트다.

**테스트를 고치지 않았다.** 대신 **설계를 좁혔다**:

| | claim id | marker id |
|---|---|---|
| 성격 | 청크가 자기 주장을 가리키는 **사적 손잡이**. 청크 밖에서 의미 없음 | 문서의 **구조 선언** — 구획, 제목 |
| 두 청크가 같은 것을 쓰면 | 서로 못 본 채 같은 단어를 고른 것 | **문서 자체에 대한 불일치** |
| 처리 | 개명 | **fail closed 유지** |

**측정으로 성립을 확인했다**: in-gel 의 겹침은 `c1`·`c2`·`c3`·`c12` **전부 claim
id** 이고 **marker 겹침은 0건**이다. 따라서 좁힌 설계로도 병합이 열리고,
**두 테스트는 수정 없이 그대로 통과한다.**

### 1-2. [조건 A] 같은 청크 안의 중복 — **이미 존재하며 유지**
`duplicate_evidence_item_identifier` (chunk 관계 검증). 모델이 두 주장을 **모두
보고도** 같은 이름을 붙인 경우이므로 자기모순이다. 테스트로 고정했다
(merge 가 아니라 chunk 단계에서 raise 되는 것까지 AST 로 확인).

### 1-3. [조건 B] 진짜 모순 검사 — **존재하지 않았다. 신설했다.**

`_refuse_contradictory_claims` (protocol_claim_analysis.py, chunk 검증 안).

**이것이 필요한 이유**: 겹침 거부는 **모순 파수꾼처럼 보였지만 아니었다.**
서로 못 본 두 청크가 같은 단어를 고른 것에는 발동하고, **한 청크가 한 문장에
대해 양립 불가능한 두 말을 하는 것**에는 침묵했다. 그것을 치우면서 대체물을
세우지 않으면 **잘못된 파수꾼을 치우고 올바른 파수꾼은 안 세운 상태**가 된다.

**무엇을 거부하는가** (좁게):
- 한 문장에 대한 두 repetition 주장이 **다른 범위 또는 다른 횟수**를 선언
- 한 문장에 대한 같은 범주의 두 주장이 **required_for_execution 에서 불일치**

**무엇을 거부하지 않는가**: 근거 주소 공유 자체. in-gel 실측 **18개 주소가 2개
이상의 주장을 갖고 전부 정상**이다 — 한 문장 안의 두 quantity, 반복 문장의
action + repeat_condition. 이것들을 거부하는 검사는 **문서가 정상적으로 쓰였다는
이유로 문서를 거부**한다.

**청크 지역성**: 주장의 근거 페이지는 자기 청크의 core 페이지여야 하고 core
페이지는 청크 간 배타적이다(3문서 확인). 따라서 서로 다른 청크의 두 주장은
**같은 근거 주소를 가질 수 없다.**

### 1-4. 접두사 누출 — **0건**
```
merge 내부에서 개명된 claim id : 4
조립된 프로토콜에 도달한 접두사 id : 0
```
STEP 36 이 8건 회귀를 낸 이유는 **무조건 전부 접두사화**했기 때문이고, 지금은
**충돌한 항목만** 바꾼다.

### 1-5. 두 fail closed 테스트 — **완화·삭제 없음.** 새 정의에서 **marker 재사용**에
해당하며, 그것은 여전히 fail closed 다. 그대로 통과한다.

---

## 작업 2. ④ 방식 B

### 2-1 / 2-2. `_keep_unverified_step_attribution`

```
material-1 (material, p3) {'section_id': 'gel-destaining', 'step_id': 'step-2',
                           'target_claim_id': 'action-2'}  status=model_claim_unverified
c2         (material, p8) {'section_id': 'sec-reduction-alkylation',
                           'step_id': 'st21', 'target_claim_id': 'c1'}  status=model_claim_unverified
```

- 주장은 도메인이 요구하는 대로 범위를 벗고,
- **모델이 말한 것은 `MergedProtocolClaims.unverified_step_attributions` 에
  그대로 보존**되며 `model_claim_unverified` 로 표시된다.

### 2-3. 모델 발언을 뒤집는가 — **뒤집지 않는다**

모델의 주장은 **부정되지도, 수정되지도, 삭제되지도 않는다.** 도메인 객체가 그것을
담지 못하므로 **담을 수 있는 자리로 옮겨** 기록한다. 반대 내용을 주장하는 것이
아무것도 없다. (거부는 관측을 버리는 것이고, 조용히 필드를 비우는 것도 마찬가지로
버리는 것이다. 둘 다 하지 않았다.)

### 2-2 [조건 C] 실행 가능 상태를 만들지 않는다 — 테스트로 고정
- readiness 가 이 필드를 읽지 않는다. 병합 후 상태는 여전히 `analysis_required`.
- 관련 사유 코드가 생기지도, 사라지지도 않는다.
- 검증된 정보와 구분 가능: **이 목록에만 존재**한다.

### 2-4. 계약 감사에 기록 — 완료
`top_level_claim_scope_invalid` 를 **`_prompt` → `_server`** 로 바꿨다.
근거: STEP 37 측정상 프롬프트에 "top-level" 이 **0회** 등장한다. 기존 근거 문구는
`target_claim_id` 일반론이지 이 규칙의 진술이 아니었다. **provider 가 들은 적 없는
규칙에 대한 정직한 라벨은 SERVER_ONLY 다.**

---

## 작업 3. 청크 사전검사 완성 — 부분 수행

### 3-1 / 3-2. `top_level_claim_scope_invalid` — **넣지 않았다**

```
ord 0 would FAIL: ['material-1']
ord 3 would FAIL: ['c2']
-> 5청크 중 2개가 삭제된다
```
방식 B 는 **병합 시점에 옮기는 것**이지 payload 를 바꾸는 것이 아니다. 캐시에 든
응답은 그대로이므로 청크 검증에 이 규칙을 넣으면 **유료 청크 2개가 사라진다.**
지시대로 **넣지 않고 보고한다.**

### 3-3. merge 전용 규칙: **16 → 13**
남은 것: `action_claim_scope_conflict`, `claim_identity_conflict`,
`document_level_claim_scope_invalid`, `incomplete_source_coverage`,
`orphan_execution_claim`, `protocol_title_missing_or_conflicting`,
`resource_claim_scope_conflict`, `section_conflict`, `source_label_conflict`,
`step_identity_conflict`, `top_level_claim_scope_invalid`,
`warning_must_attach_to_enclosing_step`, `whole_source_identity_mismatch`

---

## 작업 4. 병합 + 첫 정확도 채점 — **수행함**

### 4-1. 다섯 번째 차단이 있었고, **그것은 내가 만든 것**이었다

①②③④ 를 치우자 `action_claim_scope_conflict` 가 나왔다. 원인: ③ 이 **action 의
구획만** 채우고 그 action 을 target 으로 삼는 **parameter 주장의 구획은 비워 둬서**
둘이 불일치했다(chunk2·3 에서 각각 14건·8건). 단계를 수식하는 주장은 정의상 그
단계의 구획에 있으므로, **이어받기가 단계에 종속된 모든 것에 닿도록** 고쳤다.

### 4-2. 기본 수치

| 항목 | 값 |
|---|---|
| 제목 | `In-gel digestion protocol for protein identification` (**파일에서 읽음**, `title_taken_from_the_file=True`) |
| 섹션 수 | 4 |
| **단계 수** | **25** (라벨 1–25, 빈틈·중복 없음) |
| 반복 구간 수 | **1** |
| 근거 조각 수 | 29 (고유) |
| 미해결 블로커 | 4 (아래) |
| **추론된 구획** | **14 단계** |
| **보존된 미검증 귀속** | **2** |
| 미확인 조각 | 26 |

**남은 차단 사유** (병합 후 실측): `declined_value_not_resolved`,
`no_declared_safety_warnings`, `source_page_not_fully_read`,
`unsupported_repeat_until` → 상태 `analysis_required`.

### 4-3. 항목별 점수

| 항목 | 결과 |
|---|---|
| 단계 개수 | **25 / 25** |
| 누락 라벨 | **0** |
| 추가 라벨 | **0** |
| 순서 일치 | **일치** |
| 단계 텍스트 유사도 | 최소 **0.446** / 중앙 **0.667** / 최대 **1.000** |
| 값 일치 (시간·온도·부피) | **matching 23 / contradicted 1 / reference_silent 1** |
| 반복 구간 | **1 / 3** (2건 누락) |
| **근거 주소 유효성** | **51 검사 / 0 미해결 / 전부 resolve** |

산출 파일:
- `data/development_cache/accuracy/in-gel-digestion.accuracy.json`
- `data/development_cache/accuracy/in-gel-digestion.merged-protocol.json`

### 4-4 / 4-5. 불일치 전수와 분류

| # | 어디 | 내용 | 분류 |
|---|---|---|---|
| 1 | **24단계 값 모순** | 추출 duration **57600s(16h)**, 정답지 1800s(30min)+37°C+10µL | **(a) 추출이 틀림** |
| 2 | **23단계 텍스트 0.526** | 추출 텍스트가 `…16:00:00` 뒤에 **`Extract tryptic peptides 30m`** 을 포함 | **(a) 추출이 틀림** |
| 3 | **3단계 텍스트 0.446** | 추출이 뒤따르는 `Note If the band is extremely stained…` 를 포함 | **(c) 판단 불가** |
| 4 | **3단계 값 reference_silent** | 추출 volume 에 `1000ul` 추가 (위 Note 에서 옴) | **(c) 판단 불가** |
| 5 | **2단계 텍스트 0.569** | 추출이 뒤따르는 내용을 더 포함 | **(c) 판단 불가** |
| 6 | **반복 8-9 누락** | 모델이 안 잡음 (STEP 36 에서 확정) | **(a) 추출이 틀림** |
| 7 | **반복 17-18 누락** | 모델이 안 잡음 | **(a) 추출이 틀림** |

**(b) 정답지가 틀림: 0건.** (STEP 35·이번 STEP 에서 정답지 문제 2건을 이미 고쳤다.)

### ⚠ 1번은 STEP 34·35 가 예고한 실패가 **실제로 일어난 것**이다

p.8 원문: `24 Quickly spin down the digest … which will contain the 16h 30m protocols.io | …`

**`16h 30m` 은 다음 구획의 소요시간 추정치**이고 24단계의 지시가 아니다. 하단 띠는
그 뒤의 `protocols.io |…` 꼬리말만 잘라내며, `16h 30m` 은 24단계 영역 **안**에 남는다.
추출은 그것을 24단계의 duration 으로 삼았다.

STEP 35 작업 7-2 에 이렇게 적었다: *"에이전트는 타이머를 1시간으로 걸고, 한 시간
뒤 단계를 완료로 안내한다."* **같은 형태가 24단계에서 16시간으로 발생했다.**
29건 「무근거 귀속 위험」이 가설이 아니라 관측이 되었다. 23단계도 같은 부류다
(`Extract tryptic peptides 30m` 구획 제목 흡수).

### 4-6. **기준선에 미해결 항목 1건 있음**
`candidate-a-step-20-repeat-range` 는 **삭제하지 않고 열어 두었다**(결정 3).
앵커를 step-20 으로 정정했으므로 ambiguity 와 앵커가 이제 같은 단계를 가리킨다.
채점 결과는 이 항목이 열린 상태에서 산출된 것이다.

---

## 작업 5. ③ 4문서 적용

| 문서 | 응답 출처 | 단계 | 이어받기 | 비율 |
|---|---|---|---|---|
| in-gel | **실제 캐시** | 25 | **14** | **56%** |
| headspace | 오프라인 모델 | 62 | 0 | 0% |
| intracellular | — | — | — | plan 거부 (기존 cross-check MISMATCH) |
| ANKOM | 오프라인 모델 | 67 | 0 | 0% |

### 5-2. 특정 문서에 유리하게 작동하지 않는다
규칙은 **응답에 구획이 없을 때만** 발동한다. headspace·ANKOM 이 0% 인 것은
**오프라인 모델이 항상 구획을 붙이기 때문**이지 문서 성질이 아니다.
**동일 조건 비교가 아니며 그렇게 읽으면 안 된다.**

### 5-3. in-gel 56% 의 원인
chunk2·3·4(p.7·8·9, 라벨 12–25 = 14단계)가 **구획 마커를 하나도 내지 않았다.**
그 페이지들에 구획 제목이 인쇄되어 있지 않기 때문이며, 이어받기가 존재하는 이유다.

---

## 작업 5-B. 반복 조건과 범위의 불일치 — 조사만

### 5B-1. 원문 그대로 기록. `declared_range` 를 고치지 않았다.
문서는 「17-18 을 반복하라」고 말하고, 조건이 확인하는 상태(마른 흰빛)를 만드는
단계는 19–20 이다. **문서가 말한 것이 기록이다**(원칙 8).

### 5B-2. 모델이 스스로 표시했는가 — **미표시**

chunk3(p.8) 실측:
- 범주 분포: `action 4, material 1, concentration 2, quantity 2, temperature 1, agitation_speed 1, duration 1`
- **ambiguity / repetition 주장: 0건**
- **「repeat steps 17-18」 문장을 인용한 주장: 0건**

문장 자체가 **미확인 조각으로 남았다.** → **이 결함은 사람만 찾는다.**

### 5B-3. 일반 신호 3가지 — **셋 다 실패했다 (측정 결과)**

| 신호 | 정의 |
|---|---|
| 1 | 반복 문장이 선언된 범위의 마지막 단계보다 **뒤에** 있다 |
| 2 | 범위 끝과 반복 문장 사이에 낀 단계 수 > 0 |
| 3 | 반복 문장을 감싸는 단계가 **선언된 범위 안에 없다** |

4문서 실측 (범위를 선언한 반복 문장 8건):

| 문서 | 건수 | 신호 1 | 신호 2 | 신호 3 | 판정 |
|---|---|---|---|---|---|
| in-gel p.5 (2-7) | 1 | — | — | — | 정상, 미발동 ✓ |
| in-gel p.6 (8-9) | 1 | — | — | — | 정상, 미발동 ✓ |
| **in-gel p.8 (17-18)** | 1 | — | — | — | **실제 결함인데 미발동 ✗** |
| headspace 5건 | 5 | **전부 발동** | — | **전부 발동** | **전부 오탐 ✗** |
| **합계** | **8** | **5** | **0** | **5** | **진양성 0 / 오탐 5 / 미탐 1** |

- (a) 문서 의존: 세 신호 모두 위치만 보므로 **비의존**. 그러나 그것이 문제였다.
- (b) 오탐: headspace 의 정상 패턴 「16 Repeat steps 12-15 twice more」 —
  반복 지시 자체가 범위 바로 다음 번호 단계인 형태 — 에 **100% 오탐**.
- (c) 미탐: in-gel p.8 은 문장이 **페이지 첫 조각**이라 그 페이지의 어떤 단계
  블록에도 속하지 않는다(`enclosing step None`). 세 신호 모두 놓친다.

**결론**: 이 부류의 구조 신호로는 「16 Repeat steps 12-15」(정상)와
「step 20 뒤의 repeat 17-18」(의심)을 **가를 수 없다.** 가르려면 조건이 무엇을
관찰하는지 읽어야 하고, 그것은 의미 판단이다. **사람에게 묻거나, 모델에게
그 질문만 따로 던지는 방식**이지 구조 규칙이 아니다.

### 5B-4. 탐지기를 구현하지 않았다. 서버가 범위를 고치는 동작도 만들지 않았다.

### 5B-5. 실행 가능 판정에 대한 영향 — **판단 불가**

종료 조건에 도달하지 못할 수 있는 반복이 남은 문서를 실행 가능으로 표시해도
되는가에 대해, **이 사례에서는 질문이 성립하지 않는다**: 해당 반복이 애초에
추출되지 않았으므로 실행 대상에 없다. 그리고 in-gel 은 `unsupported_repeat_until`
로 어차피 막혀 있다.

**일반적으로는 판단 불가다.** 탐지가 안 되는 결함에 대해 "탐지되면 막는다"는
정책은 성립하지 않고, 5B-3 이 탐지 불가를 보였다. **추측하지 않는다.**

---

## 작업 6. 연구자 가시성 — 설계안만 (구현 없음)

### 6-1. 최소 변경안
`ProtocolCatalogEntry` 에 두 필드를 더한다(둘 다 이미 서버가 계산해 둔 값):
```
blocking_reason_codes : tuple[str, ...]   # analysis.readiness.reason_codes
blocking_kind         : "reviewer_can_clear" | "not_currently_supported" | "mixed" | None
```
`/api/protocols` 가 그대로 실어 보낸다. 새 라우트도 새 화면도 필요 없다 —
연구자 드롭다운의 라벨 옆에 한 줄이 붙는다.

### 6-2. 두 부류로 나누는 것은 **가능하다**
카탈로그가 이미 `_BLOCKER_RESOLUTION` 을 갖고 있다.
- **(가) 검토자를 부르면 풀림**: `_BLOCKER_RESOLUTION` 에 항목이 있는 것 —
  `no_declared_safety_warnings`, `source_page_not_fully_read`,
  `declined_value_not_resolved`, `unresolved_ambiguity`,
  `unconfirmed_fixed_repetition`, `excessive_declined_values`,
  `source_text_cross_check_unavailable`
- **(나) 지금은 아무도 못 풂**: 그 표에 없는 것 —
  `unsupported_repeat_until`, `unsupported_conditional_branch`,
  `source_text_cross_check_failed`, `no_executable_steps` 등

**이 구분이 핵심인 이유**: in-gel 은 (가) 3건 + (나) 1건이다. 연구자가 검토자를
불러 3건을 지워도 **여전히 실행되지 않는다.** 그것을 모르면 검토자를 부르는
행동이 헛수고가 된다.

### 6-3. 위험
- 사유 코드를 그대로 노출하면 연구자에게 **내부 용어**가 보인다. 한국어 설명
  문자열이 필요하고, 그 문자열이 또 STEP 37 7-5 처럼 **부정확해질 수 있다.**
  코드와 문구를 한 곳에서 관리해야 한다.
- (나) 를 「아무도 못 풂」이라고 표시하면 **포기 신호**로 읽힐 수 있다.
  「이 기능은 현재 프로파일이 지원하지 않음」이 정확한 표현이다.
- **구현하지 않았다.**

---

## 작업 7. 보고

### 7-1. 작업별 상태

| 작업 | 상태 | 비고 |
|---|---|---|
| 1. ①-확장 | **수행함** | claim id 만. marker id 는 fail closed 유지 (원칙 13) |
| 2. ④ 방식 B | **수행함** | 조건 C 포함. 계약 감사에 SERVER_ONLY 로 기록 |
| 3. 청크 사전검사 | **부분 수행** | `top_level_…` 미도입 — 유료 청크 2개가 삭제됨. 16 → 13 |
| 4. 병합·채점 | **수행함** | **첫 정확도 점수 산출** |
| 5. ③ 4문서 | **수행함** | in-gel 56%, 나머지는 오프라인 모델이라 동일 비교 아님 |
| 5-B. 반복 불일치 | **수행함 (조사만)** | 모델 **미표시**. 신호 3개 **전부 실패** |
| 6. 연구자 가시성 | **설계안만** | 구현 없음 |
| 7. 보고 | **수행함** | 이 문서 |

### 7-2. 전체 테스트: 기준 1445 → **1458 passed, 1225 subtests** (감소 없음)
### 7-3. provider 호출 실사용량: **0회.** 잔여 **4회.**
### 7-4. 캐시 5청크: **48d430eb / 5619f5cf / 908626b6 / 09b63322 / a843eff5 전부 LOADS**

### 7-5. 원칙 13 에 걸려 멈춘 지점 — **1건**

**marker id 개명.** 모든 id 를 개명하는 구현이 fail closed 테스트 2개를 깨뜨렸다.
**테스트를 완화하거나 삭제하지 않았고, 그 전제가 틀렸다고 판정하지도 않았다.**
대신 **설계를 좁혀** claim id 만 개명하도록 바꿨다. marker 는 문서의 구조 선언이고
claim id 는 사적 손잡이라는 구분이 실제로 옳으며, in-gel 실측상 그것으로 충분하다
(marker 겹침 0건). **두 테스트는 한 줄도 바뀌지 않았다.**

부수적으로 3-2 도 같은 성격으로 멈췄다: `top_level_claim_scope_invalid` 를 청크
검증에 넣는 것은 테스트가 아니라 **유료 청크 2개를 지운다.**

---

## 다음 STEP 후보 (우선순위)

| 순위 | 항목 | 근거 |
|---|---|---|
| **1** | **구획 소요시간 흡수** — 24단계 16h, 23단계 30m | 관측된 정확도 결함이고, **원문에 없는 완료 기준**을 만든다(원칙 8). 29건 위험군의 실현 |
| **2** | 반복 2/3 누락 (8-9, 17-18) | 안전 관련 구조가 절반 넘게 사라진다 |
| **3** | `unsupported_repeat_until` 능력 확장 | in-gel 이 실행되려면 필수. STEP 35 5-3 에 필요 항목 5개 정리됨 |
| **4** | 연구자 가시성 (작업 6) | 파일럿 사용성. 설계안 준비됨 |
| **5** | merge 전용 13개 규칙 추가 이전 | 재발 방지. 캐시를 죽이지 않는 범위에서만 |
