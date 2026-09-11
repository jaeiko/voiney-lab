# STEP 37 — ①②③ 구현, 그리고 ① 의 규칙이 in-gel 을 풀지 못한다

## 요약 네 줄

1. **①②③ 전부 구현했다.** 승인된 정의 그대로.
2. **그러나 ① 의 정의로는 in-gel 이 풀리지 않는다.** in-gel 의 이름표 겹침
   **4건이 전부 (b) 「근거가 다름 = 진짜 충돌」**이라, 지정된 규칙은 그것들을
   fail closed 로 유지한다. 병합은 **여전히 ①에서 멈춘다.**
3. **측정으로 확인**: ① 을 (b) 까지 확장하면 ②③ 이 실제로 작동하고 병합은
   **④까지 도달**한다. 즉 **결정 2개**(① 확장, ④ 처리)가 점수와의 거리다.
4. **작업 5 는 절반만 했다.** 청크 사전검사 4개 중 3개를 넣었고,
   `top_level_claim_scope_invalid` 는 **넣으면 유료 청크 2개가 삭제**되므로
   보류했다 — 그것이 곧 ④ 이기 때문이다.

**정확도 채점: 미수행.** provider 호출 **0회**, 잔여 **4회**.
캐시 5청크 **전부 생존**. 전체 스위트 **1445 passed** (기준 1433).

---

## 작업 1. ① 이름표 충돌 — 지정된 정의 그대로 구현

### 1-1. in-gel 4건 전수 분류 (지정된 기준: 근거 동일=(a), 근거 상이=(b))

| id | 청크 | 한쪽 | 다른쪽 | 판정 |
|---|---|---|---|---|
| `c1` | 3 / 4 | p8 `21 Prepare a trypsin solution of 6ng/uL…` | p9 `peptides. To extract more peptides, soak…` | **(b)** |
| `c2` | 3 / 4 | p8 `Promega trypsin Promega Catalog #V5113` | p9 `peptides. To extract more peptides, soak…` | **(b)** |
| `c3` | 3 / 4 | p8 `21 Prepare a trypsin solution of 6ng/uL…` | p9 `25 Dry the extracted peptides to completion` | **(b)** |
| `c12` | 2 / 3 | p7 `12 Incubate for 60min at 60C 800 rpm…` | p8 `24 Quickly spin down the digest…` | **(b)** |

**(a) 0건 / (b) 4건.** 네 건 모두 페이지도 조각도 원문도 다르다.

### 1-2. 구현 — `_resolve_name_overlaps` / `_cited_the_same_place`

판별은 **인용**으로 한다: 페이지 번호, 조각 id, 복원된 발췌, 항목의 원문.
전부 서버 소유 값이다(조각 id 는 파일 바이트의 해시, 발췌는 거기서 재구성).
- **같은 자리를 같은 이름으로** → 두 번째를 문서 고유 이름으로 바꾼다.
- **다른 자리를 같은 이름으로** → 손대지 않는다. 병합이 그대로 거부한다.
- **완전히 동일한 항목** → 기존 dedup 에 맡긴다(페이지를 넘는 단계가 필요로 함).
- `step_id` / `section_id` 는 **건드리지 않는다** — 프롬프트가 그 둘의 안정성을
  요구하고, 청크 간 단계 연속성이 거기 의존한다.

**접두사 누출 확인 (1-2 요구)**: in-gel 에서 (a) 가 0건이므로 **실제로 새는
경우가 없다.** 개명이 일어나는 경우의 동작은 합성 테스트로 고정했다.
STEP 36 에서 8건 회귀가 났던 원인은 **모든 항목을 무조건 접두사화**했기 때문이고,
지금은 **충돌한 항목만** 바꾼다.

### 1-4. 원칙 13 준수 — **기존 fail closed 테스트 2개를 건드리지 않았다**

`test_valid_but_conflicting_section_identity_fails_closed` 와
`test_valid_chunk_conflict_persists_merge_conflict_and_no_candidate` 는 **둘 다
(b) 케이스를 만들고 있었다** (다른 청크의 마커에 남의 id 를 붙이므로 근거가 다름).
따라서 **수정 없이 그대로 통과**하며 계속 fail closed 를 증명한다.
데이터를 바꿀 필요가 없었다. (a) 케이스는 **새 테스트로 따로** 추가했다.

### 1-5. 안전 판정 — **「약화 없음」**
같은 자리를 가리키는 두 이름을 구분해 주는 것뿐이고, 다른 자리를 가리키는
같은 이름은 그대로 거부된다.

### ⚠ 그러나: **① 로는 in-gel 이 풀리지 않는다** (규칙 B 보고)

지정된 정의상 4건 전부 (b) 이므로 병합은 **①에서 멈춘다**. 실측:
```
merge -> ProtocolChunkMergeError claim_identity_conflict
```

**결정에 필요한 사실 하나를 측정했다**: 참조가 청크를 넘는 경우가 있는가.
```
target 을 가진 주장        : 45
  자기 청크 안을 가리킴    : 45
  다른 청크를 가리킴       :  0
```
모델은 **보지 못한 청크의 주장을 지목할 수 없다.** 따라서 청크 단위로 개명하고
그 청크의 참조를 함께 고치면 **어떤 참조도 모호해지지 않는다.**
(b) 를 거부하는 근거가 「하나의 이름이 두 자리를 가리켜 참조가 모호해진다」라면,
그 모호함은 **구조적으로 발생할 수 없다.**

**만약 ① 이 (b) 까지 포함한다면 어떻게 되는가 — 측정했다(코드 변경 아님):**
```
①(b 포함) → ②③ 작동 → 병합이 ④에서 멈춤
```
즉 ②③ 은 실제로 동작하며, 남는 것은 **④ 하나**다.

---

## 작업 2. ② 제목 — 구현함

### 2-1 / 2-2. `_title_from_the_file`

- 텍스트는 **추출기의 것** — 파일 메타데이터에서 서버가 직접 읽은 값이다.
- 인용은 **그 제목을 실제로 포함한 조각**을 가리킨다(in-gel 은 p.1 조각 0).
  포함하는 조각이 없으면 조각을 주장하지 않고 첫 페이지만 가리킨다 —
  "파일은 이것이 제목이라고 하는데 페이지 어디에도 가리킬 곳이 없다"는 정직한 표현.
- **출처 표시**: marker_id 가 `title-read-from-the-source-file` 이고,
  merge 기록에 `title_taken_from_the_file=True` 가 남는다. 검토자가 **모델의
  주장과 파일의 값을 같은 것으로 보지 않게** 한다.
- 파일에 제목이 없으면 **빈 제목**이 되고 추측하지 않는다.

### 2-3. 실행 차단을 풀지 않는다 — 확인함
제목 **2개 이상은 여전히 fail closed**(두 청크의 불일치는 서버가 못 고른다).
그리고 제목이 채워져도 `source_page_not_fully_read` 등 다른 사유가 그대로 남는다.

---

## 작업 3. ③ 구획 — 「이어받기」로만 구현

### 3-1. `_inherit_declared_section`

- **문서 순서상 앞에서 실제로 선언된** 구획만 이어받는다. 마커가 없으면 없다.
- **앞에 선언된 구획이 없으면 채우지 않는다** → 병합이 그대로 fail closed.
- 구획 마커가 하나도 없는 문서도 그대로 fail closed.
- 자기 구획을 스스로 말한 단계는 **건드리지 않는다.**

### 3-2. 4문서 적용 — **미측정**
①이 병합을 막아 in-gel 조차 끝까지 가지 않으므로, 문서별 「이어받기로 채워진
단계 수」를 실측할 수 없다. **추측하지 않는다.** 규칙 자체는 문서를 가리지
않는다(위치·순서만 본다).

### 3-3. 「추론된 구획」 표시 — 구현함
`MergedProtocolClaims.inferred_section_step_ids` 에 **이어받기로 채워진 단계
id 목록**이 남는다. 원문이 구획을 명시한 단계와 구분 가능하다.

---

## 작업 4. ④ 범위 오류 — 고치지 않음 (지시대로)

### 4-1. ①②③ 적용 후에도 발생하는가 — **①이 먼저 막아 확인 불가**
다만 ① 을 (b) 까지 확장한 측정에서 **병합이 정확히 ④에서 멈추는 것**을 확인했다.

### 4-2. 모델이 말한 것 vs 서버가 요구하는 것

| | chunk0 `material-1` | chunk3 `c2` |
|---|---|---|
| category | `material` | `material` |
| 모델이 설정 | `section_id='gel-destaining'`, `step_id='step-2'`, `target_claim_id='action-2'` | `step_id='st21'`, `target_claim_id='c1'` |
| 인용 | p3 `2 Prepare two wash solutions: Solution A: 2 parts of 25mM ammonium bic…` | p8 `Promega trypsin Promega Catalog #V5113` |
| 서버 요구 | `section_id`/`step_id`/`source_label`/`target_claim_id` **네 개 전부 None** | 동일 |
| 모델의 뜻 | "이 재료는 2단계에서 쓰인다" | "이 trypsin 은 21단계의 것" |

**프롬프트에 "top-level" 이라는 말은 0번 나온다.** 재료·장비·사전조건이 어떤
범위도 가질 수 없다는 규칙은 **진술된 적이 없다.** ① 과 같은 부류다.

### 4-3. 해결안 3가지

| | A. 서버가 범위를 벗긴다 | B. 모델 발언을 보존하되 다른 자리에 둔다 | C. 프롬프트에 명시 |
|---|---|---|---|
| 내용 | 병합 시 top-level 주장의 step/section/target 을 None 으로 만든다 | 재료의 단계 귀속을 별도 필드로 보존하고 서버 규칙은 그대로 | "재료·장비·사전조건은 어떤 단계에도 귀속되지 않는다"를 프롬프트에 쓴다 |
| **(a) 모델 발언을 뒤집는가** | **뒤집는다** — "2단계에서 쓴다"는 정보가 사라진다 | 뒤집지 않는다 | 뒤집지 않는다(앞으로의 응답이 달라질 뿐) |
| **(b) 캐시** | **생존** | **생존** | **5청크 전부 사망** |
| **(c) 안전** | 재료-단계 연결이 사라지면 실행 중 "이 단계에 무엇이 필요한가"를 못 말한다. **정보 손실 방향의 위험** | 없음. 다만 도메인 모델에 없는 개념을 추가해야 함 | 없음. 다만 재수집 5회 필요(잔여 4회 → **불가능**) |

**구현하지 않았다. 사용자 결정 항목.**

---

## 작업 5. B 청크 사전검사 — 부분 수행

### 5-1. 16개 규칙의 청크 단위 검사 가능 여부

| 규칙 | 청크에서 판정 가능? | 근거 |
|---|---|---|
| `action_structure_invalid` | **가능(구획 조건 제외)** | 단계·라벨·target·required 는 청크 내부 사실 |
| `claim_target_invalid` | **가능** | 참조는 항상 청크 내부(45/45 측정) |
| `missing_value_scope_invalid` | **가능** | 주장 자체의 속성 |
| `top_level_claim_scope_invalid` | 가능하지만 **보류** | 이것이 곧 ④ (아래 5-3) |
| `action_claim_scope_conflict` | 가능(부분) | target 이 청크 내부라 판정 가능하나 위치 비교가 병합 좌표계 |
| `document_level_claim_scope_invalid` | 가능(부분) | 페이지 지역 판정이나 병합 좌표계 의존 |
| `orphan_execution_claim` | 가능(부분) | 위와 동일 |
| `resource_claim_scope_conflict` | 가능(부분) | 위와 동일 |
| `warning_must_attach_to_enclosing_step` | 가능(부분) | 위와 동일 |
| `claim_identity_conflict` | **불가능** | 두 청크를 비교해야 성립 |
| `section_conflict` | **불가능** | 〃 |
| `step_identity_conflict` | **불가능** | 〃 |
| `source_label_conflict` | **불가능** | 〃 |
| `incomplete_source_coverage` | **불가능** | 문서 전체 페이지 집합 |
| `protocol_title_missing_or_conflicting` | **불가능** | 문서 전체 |
| `whole_source_identity_mismatch` | 이미 청크에서 검사됨 | — |

### 5-2. 넣은 것 — `_refuse_chunk_local_inconsistency` (3개)
`action_structure_invalid`, `claim_target_invalid`, `missing_value_scope_invalid`

### 5-3. ⚠ **캐시를 죽이는 지점을 발견해 보류했다**

`top_level_claim_scope_invalid` 를 청크 검증에 넣으면:
```
ord 0  top_level_claim_scope_invalid: ['material-1']
ord 3  top_level_claim_scope_invalid: ['c2']
```
그리고 `ChunkAnalysisCache.load()` 는 **재검증에 실패한 항목을 삭제**하므로
**유료 청크 2개가 사라진다.** 이번 STEP 이 금지한 것이다.

**캐시를 죽이지 않는 방법**: 그 규칙만 빼고 나머지를 넣는다. 이것은 회피가
아니라 정합적이다 — **처분이 미결인 규칙을 청크 단계에서 강제하면, 그 결정을
「유료 청크 2개를 지우는 방식으로」 내려 버리는 것**이기 때문이다.

**적용 후 실측: 5청크 전부 LOADS.** 삭제 0건.

### 5-4. 사전검사가 걸리는 시점 — **호출 후, 저장 전**
호출 전에는 불가능하다. 검사 대상이 **응답**이고 응답은 호출 전에 없다.
호출 1건의 비용은 이미 나간 뒤이지만, **그 청크에서 즉시** 알게 되므로
5회를 다 쓰고 병합에서 네 번 연속 거부당하는 일은 없어진다.

---

## 작업 6. 병합 + 채점 — **미수행**

①이 막고 있다(위 1-5). **6-2 ~ 6-4 는 수행하지 않았고 숫자를 지어내지 않는다.**

### 6-6. ⚠ 기준선 자체의 미해결 항목 — **STEP 35 의 내 추가가 문제를 만들었다**

**구획 정합성: 문제 없음.** 세 repeat_until 의 `section_id` 가 각 단계의 실제
소속 구획과 **전부 일치**한다. 17-18 이 8-9 와 같은
`candidate-a-section-reduction-alkylation` 인 것은 **옳다** — 단계 17·18 이
실제로 그 구획에 속한다. 앞 항목을 본뜬 결과가 아니다.

**그러나 중복/모순 판정: (b) 모순이다.**

| | 기존 ambiguity | STEP 35 가 추가한 repeat_until |
|---|---|---|
| id | `candidate-a-step-20-repeat-range` | `candidate-a-repeat-steps-17-18` |
| 앵커 | `candidate-a-step-20` | `candidate-a-step-18` |
| 근거 | p.8 「The gel should look white… If the band is still transparen…」 | **같은 p.8 문장** |
| 주장 | "이 반복의 **범위가 모호하다**" | "이 반복의 범위는 **17-18 이다**" |

**같은 문장을 근거로 한쪽은 「범위 불명」이라 하고 다른쪽은 「범위는 17-18」이라
한다. 둘 다 확정된 독해일 수 없다.** ambiguity id 가 문자 그대로
`repeat-range` 이므로 "서로 다른 질문"(c) 도 아니다.

**앵커 판정: step-20 쪽이 위치상 옳다.** 그 문장은 p.8 **조각 0**, 즉 페이지의
첫 조각이다. 라벨 20 은 p.7 의 마지막 라벨이므로 **20단계의 영역이 p.8 로
이어진 것**이다. 라벨 17·18 도 p.7 이지만 20 보다 앞이다. 내가 STEP 35 에서
step-18 을 고른 것은 8-9 항목의 관례(범위의 마지막 단계)를 따른 것인데,
**그 항목은 문장과 단계가 같은 페이지에 있었고 이번은 다르다.**

**고치지 않았다.** 6-6 지시대로 근거만 제출한다. 사용자 확인 후 별도 커밋.

**채점 시 명시할 것: 기준선 자체에 미해결 항목 1건 있음** (위 모순).

---

## 작업 7. UI 안전 확인 — 코드로만 (서버 띄우지 않음)

### 7-1 / 7-2. 「승인」 버튼이 실행 승인을 줄 수 있는가 — **없다. 안전 결함 아님.**

`protocol_catalog.approve()` 3333행:
```python
if analysis.readiness.status is not domain.ReadinessStatus.GUIDANCE_READY:
    if not self._readiness_gates_cleared(protocol_id, protocol_revision_number, analysis):
        raise ProtocolApprovalError("Protocol analysis is not ready for execution approval.")
```
준비되지 않은 리비전에 대해 **서버가 거부한다.** 버튼을 눌러도 승인이 기록되지
않는다. 그리고 `available_for_execution = approved and execution_ready` 이므로
승인만으로 실행이 열리지도 않는다.

### 7-3. **코드를 바꾸지 않았다.** UI 개선 항목으로만 남긴다:
버튼이 활성으로 보이는데 누르면 거부되는 것은 **오해를 부르는 표시**다.
게이트 로직은 정확하다.

### 7-4. `revision-e8408b2e…` 는 다른 경로의 항목이다 — 확인함

id 모양이 저장소를 가른다:
- **워크스페이스 계보**: `revision-{sha256[:32]}` (`workspace_store.py:2264`) —
  검토자 inbox 에 뜨는 것
- **카탈로그**: `pdf-{n}` (`protocol_catalog.py:324`) 또는
  `fixture-{sha[:20]}` (`curated_protocol.py:271`) — 5청크 파이프라인이 쓰는 것

**접두사로 구분된다.** 다만 화면이 그 구분을 설명하지는 않는다.

### 7-5. 두 문구는 실제로 다른 사유에서 나온다 — 확인함. **한쪽은 부정확하다.**

| 문구 | 조건 |
|---|---|
| `준비 게이트 차단 · 실행 불가` | `entry.execution_blocked_reason === "readiness_gates_blocked"` |
| `분석 또는 안전 검토 차단 · 조치 필요` | `entry.lifecycle_state === "blocked"` |

`lifecycle_state = "review_required" if execution_ready else "blocked"` 이므로
`blocked` 는 **"분석됐고, 승인 안 됐고, 실행 준비가 안 됨"**을 뜻한다. 원인은
모호성·repeat-until·미판독 페이지 등 **어떤 readiness 사유든** 될 수 있다.
**문구가 원인을 「분석 또는 안전 검토」 둘로 좁혀 말하는 것은 부정확하다.**

**이번에는 문구도 고치지 않았다.** ①이 막혀 병합 결과를 화면에서 확인할 수
없는 상태라, 문구 변경의 효과를 사용자가 검증할 수 없다. 다음 STEP 항목으로 남긴다.

### 7-6. 연구자가 「왜 막혔는지」 볼 수 있는가 — **경로는 있으나 연구자 화면에는 없다**

- 사유는 `/api/protocols/{id}/review` 의 `outstanding_blockers` 에 있고,
  UI 가 `protocol-blockers` 패널에 **일반적으로 렌더한다.**
- 그러나 연구자 드롭다운은 `fetch("/api/protocols")` 만 쓴다. 그 응답의
  `ProtocolCatalogEntry` 에는 **사유 필드가 없다** — 상태 라벨뿐이다.
  (개발용 curated fixture 항목만 `outstanding_blockers` 를 함께 낸다.)
- 연구자가 검토 패널을 열 권한이 있는지는 **미측정**이다.

**평가**: 파일럿에서 연구자는 「분석 또는 안전 검토 차단 · 조치 필요」라는
한 줄만 보고, 그 문구는 위 7-5 대로 실제 원인을 정확히 말하지도 않는다.
막힌 이유가 「4쪽에 미판독 값이 있다」인지 「repeat-until 을 이 프로파일이
지원하지 않는다」인지 알 수 없고, 둘은 연구자가 할 수 있는 일이 전혀 다르다
(전자는 검토자 호출, 후자는 아무도 못 함). **연구자는 검토자를 찾아가야 하고,
찾아가서도 무엇을 물어야 할지 모른다.** 제품 결함 후보로 보고한다.
**신규 UI 를 만들지 않았다.**

### 7-7. 상세 확인 경로 — UI 에 있다. 이번에 못 본 것은 다른 이유다.

검토 패널이 단계·차단 사유를 렌더한다. 사용자가 이번에 5청크 결과를 화면에서
확인하지 못한 것은 **UI 부재 때문이 아니라 병합이 완료되지 않아 카탈로그에
저장된 적이 없기 때문**이다.

**CLI 로 확인하는 명령** (병합 성공 후):
```bash
VOICE_WORKFLOW_AGENT_MOSS_ENABLED=false \
VOICE_WORKFLOW_AGENT_WORKSPACE_ENABLED=false \
VOICE_WORKFLOW_AGENT_EXPERIMENT_REPORTS_ENABLED=false \
python scripts/score_extraction.py \
  data/runtime/candidate-a-source/in-gel-digestion.pdf \
  --reference  data/development_protocols/candidate_a_curated_analysis.json \
  --provenance data/development_protocols/candidate_a_curated_analysis.provenance.json
```

---

## 작업 8. 보고

### 8-1. 작업별 상태

| 작업 | 상태 | 이유 |
|---|---|---|
| 1. ① 구현 | **수행함** | 지정 정의대로. 다만 in-gel 4건이 전부 (b) 라 병합은 여전히 막힘 |
| 2. ② 제목 | **수행함** | 출처 표시 포함. 실행 차단 안 풂 |
| 3. ③ 구획 | **수행함** (3-2 미측정) | ①이 막아 4문서 실측 불가 |
| 4. ④ 분석 | **수행함 (미구현)** | 대조표·해결안 3개 제시 |
| 5. B 사전검사 | **부분 수행** | 3개 도입. `top_level_…` 은 유료 청크 2개를 죽이므로 보류 |
| 6. 병합·채점 | **미수행** | ①이 막음. 6-6 은 수행 — **기준선에 모순 1건 발견** |
| 7. UI 확인 | **수행함** | 안전 결함 없음. 문구 부정확·연구자 가시성 결함 후보 보고 |
| 8. 보고 | **수행함** | 이 문서 |

### 8-2. 전체 테스트: 기준 1433 → **1445 passed, 1218 subtests** (감소 없음)
### 8-3. provider 호출 실사용량: **0회.** 잔여 **4회.**
### 8-4. 캐시 5청크: **48d430eb / 5619f5cf / 908626b6 / 09b63322 / a843eff5 전부 LOADS**

### 8-5. 원칙 13 에 걸려 멈춘 지점

**1건 — 그러나 테스트를 건드리지 않고 지나갔다.** 기존 fail closed 테스트 2개가
(b) 케이스를 쓰고 있어 **수정 없이 그대로 통과**했다. 완화·삭제 없음.

**추가로, 원칙 13 의 정신에 따라 멈춘 지점 1건**: `top_level_claim_scope_invalid`
를 청크 검증에 넣는 것은 테스트가 아니라 **유료 청크 2개를 지워** 미결 결정을
강제 종결시키는 일이므로 보류했다(5-3).

---

## 사용자 결정 요청 — 2건이면 점수까지 간다

**결정 ①-확장: 근거가 다른 이름표 겹침도 개명할 것인가**
- 근거: 참조는 **45/45 전부 청크 내부**다. 모델은 보지 못한 청크의 주장을
  지목할 수 없으므로, 청크 단위 개명 + 그 청크 참조 동시 수정으로 **모호해지는
  참조가 구조적으로 없다.**
- 하면: 병합이 ④까지 간다. 안 하면: in-gel 은 영원히 ①에서 멈춘다.
- **[                    ]**

**결정 ④: 재료의 단계 귀속 (작업 4-3 의 A/B/C 중)**
- C 는 재수집 5회가 필요해 **잔여 4회로 불가능**하다.
- **[                    ]**

**결정 6-6: 기준선의 모순 (ambiguity step-20 vs repeat 17-18)**
- 하나를 빼거나, 앵커를 step-20 으로 옮기거나, 둘 다 두고 모순을 기록한다.
- **[                    ]**
