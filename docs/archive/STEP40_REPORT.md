# STEP 40 — 조립 결함 수정, L1 구현, 그리고 검증 안 된 분할에 5회를 쓰지 않기로 한 결정

## 요약 다섯 줄

1. **작업 1 완료.** 범위를 가진 반복 주장이 「준비물」로 강등되던 경로를 없앴다.
   범위는 반복으로 남거나 병합이 거부한다. 조용히 다른 것이 되지 않는다.
2. **작업 2(L1) 완료 — 단, capability 선언은 하지 않았다.** `may_begin_step` 이
   **죽은 코드**임을 확인했다(턴 핸들러는 `current_index += 1` 로 진행하며 아무
   게이트도 보지 않는다). 선언만 하고 경로가 없는 상태를 만들지 않는다는
   2-4 를 지켰다.
3. **작업 3: `EVIDENCE_SEGMENT_VERSION` 을 올리지 않았다.** 3-3 검증은
   **통과했으나** 3-4 재검토에서 **높이 규칙이 너무 넓다**(in-gel 39 breaks 중
   실제 제목 약 5개). 검증 안 된 잘림에 5회를 쓰지 않는다(3-5).
4. **작업 4 해소.** 두 측정은 **서로 다른 것을 셌다.** headspace 는 원문에
   ranged repeat **5건**이고 그중 **repeat-until 은 0건**이다. 둘 다 옳았다.
5. **채점기가 「포함」과 「불일치」를 분리한다.** 25단계 중 **23개가 누락 없음**.
   「유사도 0.667」이 잘못된 것을 재던 것이었다.

provider 호출 **0회**. 가용 9회 전액 미사용. 캐시 5청크 **전부 생존 + 백업 완료**.
전체 스위트 **1480 passed** (기준 1468).

---

## 작업 0. 현재 결과 보존 — 완료

| 항목 | 위치 |
|---|---|
| 채점 결과 | `data/development_cache/accuracy/in-gel-digestion.accuracy.segver6.backup.json` |
| 병합 프로토콜 | `…/in-gel-digestion.merged-protocol.segver6.backup.json` |
| 캐시 매니페스트 | `…/segver6-cache-manifest.json` (5청크 digest + claims 수 + 계약 버전) |
| 캐시 파일 사본 | `data/development_cache/segver6-chunk-backup/` (**16 파일**) |

`data/development_cache/` 는 gitignore 대상이므로 위 백업은 **디스크에만** 있다.
저장소에 남는 before/after 비교는 이 보고서의 수치 표가 담당한다(원칙 12 —
protocol 원문을 저장소에 persist 하지 않는다).

**결과적으로 이번 STEP 은 버전을 올리지 않았으므로 캐시가 그대로 유효하고,
백업은 예방 조치로 남는다.**

---

## 작업 1. 반복이 「준비물」로 강등되는 문제 — 수행함

### 1-1. 경로

`src/voice_workflow_agent/protocol_claim_analysis.py` — `assemble_experiment_protocol`
안, `target_claim_id is None and category is not ACTION` 분기(구 4055–4071행)가
문서 수준 주장을 `global_conditions` 로 모으고, 곧바로
`BeforeStartPrerequisite` 로 만든다. `repeated_step_labels` 는 **읽히지 않는다.**

### 1-2. 왜 위험한가

검증(`validate_whole_protocol_claims`)은 그 주장을 **받아들인다.** 조립은 그것을
**표현하지 못한다.** 그 사이에서 범위가 사라지는데, 결과물에는
「준비물 안내문」이 하나 늘어 있으므로 **처리된 것처럼 보인다.** 거부라면
누군가 알아차리지만, 강등은 아무도 알아차리지 못한다. 조용한 정보 손실이다.

### 1-3. 수정

`global_repetitions` 분기를 앞에 두어, `repeated_step_labels` 를 가진 문서 수준
주장은 `steps_by_label` 이 준비된 뒤 **자기 종류의 반복 구조**로 만든다
(`RepeatUntil` / `OperatorDeterminedRepetition` / `FixedRangeRepetition` —
`section_id`/`step_id`/`action_id` 는 전부 기본값 `None` 이라 문서 수준이
표현 가능하다). 범위가 해석되지 않으면 `_repeated_range_step_ids` 가
**거부한다**(자르지 않는다). 반복이 아닌 범주에 범위가 붙어 있으면
`repeat_range_not_applicable` 로 거부한다. **조용히 다른 것이 되는 경로가 없다.**

### 1-5. 이 수정만으로 반복이 몇/3 이 되는가 — **1/3, 변화 없음**

현재 캐시로 실측: 반복 구조 1건(`RepeatUntil` 2–7). **모델이 p.8 그 조각에
대해 아무 주장도 내지 않았기 때문**이다(STEP 39 에서 chunk3 인용 0건 확인).
**덫을 없앤 것이고 손실을 되찾은 것은 아니다.**

부수 확인: `before_start` 는 이 수정 전에도 **0** 이었다(백업 대조). STEP 38 이
적은 「before-start 2건」은 그 이후 ④ 수정이 재료를 옮기면서 사라진 값이며,
**이번 수정으로 잃은 것은 없다.**

---

## 작업 2. L1 — 사람 주도 반복 구간

### 2-1. 구현 (`CuratedProtocolSession`)

| 메서드 | 역할 |
|---|---|
| `_repeat_intervals_by_id()` | 조립된 반복 구조를 구간(step id 범위)으로 읽는다 |
| `repeat_interval_starting_at(index)` | 이 단계가 구간을 여는가 |
| `human_led_repeat_disclosure(index)` | 에이전트가 말할 것 전부 |
| `enter_repeat_interval(index, at)` | 인계 시각 기록 + 발화 반환 |
| `declare_repeat_interval_complete(id, at, actor…)` | **사람만** 호출 |
| `repeat_intervals_awaiting_completion()` | 아직 닫히지 않은 구간 |
| `repeat_interval_record(id)` | 기록 조회 |
| `may_leave_repeat_interval(step_id)` | 구간을 벗어나도 되는가 |

### 2-2. 에이전트 발화 — 원문에 없는 완료 기준 **0건**

말하는 것은 넷뿐이다: 구간이 열렸다는 사실 / 포함 단계 라벨 /
**문서의 문장 그대로**(`condition_source_text`) / 판단 주체는 사람이라는 명시.

테스트가 **금지 문구를 문자열로** 확인한다: `충분`, `넘어가`, `완료되었`,
`된 것 같`, `보통`, `회째`, `번 반복하면`. 그리고 인용문이
`construct.condition_source_text` 와 **문자 단위로 같은지** 확인한다.

### 2-3. 기록 항목 — 회차 없음

`handed_over_at` / `completed_at` / `declared_by_principal_id` /
`declared_by_role`. 테스트가 **기록 키 목록이 정확히 이 네 개**임을 확인하고,
`rounds` 류 키가 없음을 확인한다. 에이전트가 세지 않으므로 **적을 진실이 없다.**

### 2-4. ⚠ capability 선언은 **하지 않았다** — `may_begin_step` 이 죽은 코드다

```
grep -rn "may_begin_step" src/   → 정의 1건뿐. 호출자 0건.
실제 진행 경로: curated_protocol.py 의 턴 핸들러에서 `self.current_index += 1`
```

`FeatureCode.REPEAT_UNTIL` 을 프로파일에 넣으면 `unsupported_repeat_until` 이
사라지지만, **실행 경로에 게이트가 없으므로** 「지원한다고 선언만 하고 경로가
없는 상태」가 된다. **2-4 가 금지한 상태다.** 넣지 않았다.

시범 삽입 측정(되돌림): **6건 실패**. 그중
`test_unread_page_safety.py::test_a_fully_read_document_is_unchanged` 는
**안전 테스트**였다 — 아래 7-5 참조.

### 2-5. L1 도입 후 in-gel readiness — **변화 없음**

`declined_value_not_resolved` / `no_declared_safety_warnings` /
`source_page_not_fully_read` / `source_states_an_uncaptured_repetition` /
`unsupported_repeat_until` → **`analysis_required`**.

**「실행 가능」이 되지 않는다.** 남은 것은 (가) 검토자 해소 4건과
**(다) `unsupported_repeat_until` 1건**이며, 후자는 2-4 때문에 아직 열 수 없다.

### 2-6. 못 잡은 구간 — 확인함

`_repeat_intervals_by_id()` 에 **(2,7) 만** 있고 **(8,9)·(17,18) 은 없다**
(테스트로 고정). 구조가 없으므로 안내할 수 없고, 대신
`source_states_an_uncaptured_repetition` 이 **실행 자체를 막는다.**

---

## 작업 3. 조각 분할 — **검증했고, 버전을 올리지 않았다**

### 3-1. 원인

`_bounded_action_block_boundaries` 는 **번호 라벨**과 **문장 종결부호**에서만
끊는다(`_SENTENCE_LINE_END = [.!?]\s*\n`). `16:00:00` 이나 `contain the` 뒤에는
종결부호가 없으므로 다음 줄(구획 제목·배지)이 **그대로 붙는다.**

### 3-2 / 3-3. 수정안과 오프라인 검증 — **3-3 은 통과했다**

문서와 무관한 **기하 신호 두 개**를 측정했다(하단 띠 규칙과 같은 종류):

| 신호 | 측정값 (in-gel p.8) |
|---|---|
| **제목**: 줄 문자 높이 ≥ 페이지 중앙값 × 1.15 | 본문 0.83–1.07× / 제목 **1.28×** — 사이에 아무 줄도 없다 |
| **배지**: 줄 시작 x ≥ 페이지 폭 × 0.85 | 본문 3.6–16.6% / 배지 **91.1·91.7%** — 사이에 아무 줄도 없다 |

worker → `ProtocolPdfPage.layout_break_offsets` → 분할기까지 실제로 연결해
**오프라인으로 검증했다**(provider 0회):

```
p.8 분할 결과 (수정 후)
  seg 6 label=23  '23 Incubate overnight at 37C 800 rpm, 37°C, 16:00:00'      ← 깨끗함
  seg 7 label=-   'Extract tryptic peptides 30m'                              ← 분리됨
  seg 8 label=24  '24 Quickly spin down the digest ...'                       ← 깨끗함
  seg 9 label=-   '16h'
  seg10 label=-   '30m'

  PASS  step 23 free of 'Extract tryptic peptides'
  PASS  step 24 free of '16h'
  PASS  the repeat sentence is its own segment
  → 3-3 verification: PASS
```

### 3-4. 4문서 적용 — **여기서 재검토 조건이 걸렸다**

| 문서 | 쪽 | 조각 전 | 조각 후 | 증가 | breaks | 해당 페이지 |
|---|---|---|---|---|---|---|
| in-gel | 9 | 62 | 89 | **+27 (+44%)** | 41 | 9/9 |
| headspace | 16 | 109 | 117 | +8 (+7%) | 22 | 11/16 |
| intracellular | 34 | 242 | 322 | **+80 (+33%)** | 133 | 32/34 |
| ANKOM | 40 | 188 | 263 | **+75 (+40%)** | 120 | 33/40 |

**신호별 분해 (in-gel 41 breaks)**: 높이 **39** / 여백 **9** (중복 없음) —
합이 41 을 넘는 것은 같은 줄이 두 조건을 만족하지 않기 때문의 반대로, 페이지별
집계 차이다. 실제 내용을 보면:

| 줄 | 높이 | 좌측 | 판정 |
|---|---|---|---|
| `Gel band destaining` | 1.27× | 7% | **제목 — 옳다** |
| `Extract tryptic peptides 30m` | 1.27× | 7% | **제목 — 옳다** |
| `16h` / `30m` / `1h` / `45m` | 1.10× | 91% | **배지 — 옳다** |
| `200 µL 25mM AMBIC .` | 1.29× | **16%** | **오탐** — 들여쓴 재료 항목 |
| `Ammonium bicarbonate Merck …` | 1.21× | **16%** | **오탐** — 재료 카탈로그 |
| `800 rpm, 37°C, 00:15:00` | 1.29× | **16%** | **오탐** — 단계 **자신의** 파라미터 배지 |
| `17 Wash the gel piece …` | 1.29× | 7% | 번호 단계 — 무해(이미 경계) |

**여백 규칙은 정확하다** (in-gel 9/9 전부 진짜 구획 배지, 단계 파라미터 배지는
좌측 16% 이므로 **걸리지 않는다**).
**높이 규칙이 너무 넓다**: 들여쓴 재료·파라미터 줄이 1.21–1.29× 로 제목과
같은 크기이고, 번호 단계도 1.29× 다. **39건 중 실제 제목은 약 5건.**

좁히려면 「높이 ≥1.15× **그리고** 좌측 < 폭×0.12 **그리고** 번호로 시작하지 않음」
이 필요한데, **0.12 라는 상수를 4문서에서 검증하지 않았다**(원칙 1).

### 3-5. **판정: 검증 미완 → 버전을 올리지 않는다**

여백 규칙만 채택하면 **step 24 는 고쳐지지만 step 23 은 안 고쳐진다**
(`Extract tryptic peptides 30m` 은 좌측 7% 이므로 여백 규칙에 안 걸린다).
따라서 **3-3 의 세 항목을 검증된 규칙만으로는 통과할 수 없다.**

→ **`EVIDENCE_SEGMENT_VERSION` 6 유지. 수정을 되돌렸다.**
→ **재수집 5회를 쓰지 않았다.** 가용 9회 전액 보존.

### 3-6. 해당 없음 (3-5 에서 멈췄다).

### 부수 측정: 타이머 매니페스트 이관 가능성 (다음 STEP 용)

버전을 올릴 때 필요한 정보를 미리 측정해 두었다. 10건 중 **9건은 literal 이
새 분할에서도 정확히 한 조각에만 나타나 1:1 이관 가능**하고,
**`candidate-a-step-08` (p.5 `00:15:00`) 은 2개 조각에 나타나 판단이 필요하다.**

---

## 작업 4. 반복 개수 모순 — **해소. 두 측정이 서로 다른 것을 셌다.**

### 4-2 / 4-3. 무엇을 셌는가

| 측정 | 대상 | 코드 |
|---|---|---|
| 과거 「headspace 0건」 | **`repeat_until` 범주** (분석 결과의 구조) | `FeatureCode.REPEAT_UNTIL` 미지원 판정 경로 |
| 이번 「headspace 5건」 | **원문의 `repeat steps N-M` 문장** | `explicit_repeat_instructions` |

### headspace 원문 5건 (실측, 전문)

```
p5  16 Repeat steps 12-15 twice more, to wash bacterial cells.
p5  21 Repeat steps 19-20 for the required number of bacterial isolates/replicates …
p6  27 Repeat steps 23-26 for the metal plates.
p12 42 If using newly made Porapak tubes, repeat steps 36-41 twice more (three conditioning rounds in total) …
p14 51 Repeat steps 43-50 for the required number of treatments/replicates (minimum of four replicates …)
```

문서 전체에서 `repeat` 는 **정확히 5회** 등장한다 → 5건이 전부다.

**범주 판정**: 「twice more」 2건 = **fixed_range_repetition**,
「for the required number」 2건 = **operator_determined_repetition**,
「for the metal plates」 1건 = 횟수도 조건도 없음(범주 판정 필요).
→ **repeat-until 은 0건.** 과거 측정이 옳았다.

### ANKOM

원문에 `repeat steps N-M` **0건**. `repeat` 단어는 **2회** 등장하지만 범위가 없다.
**「과거 ANKOM 1건」이라는 기록을 내 보고서에서 찾지 못했다**(`docs/STEP3*.md`
전수 검색). 내가 그렇게 적은 적이 있다면 근거를 대지 못하므로, 지금 측정치
**0건**을 사실로 제출한다.

### 4-4 / 4-5. 계획에 미치는 영향과 두 번째 문서 추천

**정정할 과거 측정은 없다** — 둘 다 각자 옳았다. 그리고 이것은
**headspace 에 유리한 사실**이다: 반복 5건이 전부 **P1 이 이미 지원하는 범주**
이므로 headspace 는 `unsupported_repeat_until` 벽에 **걸리지 않는다.**

| 문서 | 쪽 | 라벨 | 청크=호출 | ranged repeat | repeat-until | 「그 뒤의 조각」 위험 |
|---|---|---|---|---|---|---|
| **headspace** | 16 | 61 | **8** | 5 | **0** | 5 |
| intracellular | 34 | ? | **plan 거부** | 0 | 0 | 8 |
| ANKOM | 40 | 67 | ~13 | **0** | 0 | 10 |

**추천: headspace.** 유일하게 (a) plan 이 통과하고 (b) repeat-until 벽이 없고
(c) 호출 수가 가장 적다. **단 조각 융합 수정 이후에 수집해야 한다** —
headspace 도 「그 뒤의 조각」 5건을 갖고 있고, 융합 수정은 재수집을 강제한다.

---

## 작업 5. 채점기 개선 — 수행함

### 5-2. 「포함」과 「불일치」를 분리했다

`text_containment` 신설:

| 결과 | 건수 | 단계 |
|---|---|---|
| `identical` | **1** | 25 |
| `candidate_contains_reference` | **22** | 1–4, 6–23 |
| `reference_contains_candidate` | **1** | **5** |
| `differs` | **1** | **24** |

**25단계 중 23개는 정답지 내용이 하나도 빠지지 않았다.** 빠진 것이 있는 단계는
**5번과 24번뿐**이고, 양쪽이 서로 없는 것을 가진 단계는 **24번 하나**다.

### 5-3. 주장 기반 값 유지 확인 — `values_claimed` / `steps_claiming_no_value` 유지.
텍스트 파생 지표는 **제거하지 않았다**(실험자가 듣는 텍스트이므로 의미가 있고,
둘의 불일치 자체가 발견이다).

### 5-4. 백업 대비 비교

| 항목 | segver6 백업 (STEP 39) | 이번 (STEP 40) | 변화 |
|---|---|---|---|
| 단계 수 | 25 / 25 | 25 / 25 | — |
| 누락·추가 라벨 | 0 / 0 | 0 / 0 | — |
| 순서 | 일치 | 일치 | — |
| 유사도 (최소/중앙/최대) | 0.446 / 0.667 / 1.000 | 동일 | — |
| 값 (텍스트 파생) | 23 / 1 / 1 | 동일 | — |
| **텍스트 포함 분해** | 없었음 | **23 누락 없음 / 2 누락** | **신설** |
| 반복 | 1 / 3 | 1 / 3 | — |
| 근거 주소 | 51 / 0 미해결 | 51 / 0 미해결 | — |
| 차단 사유 | 5건 | 5건 | — |

**수치가 움직이지 않은 것이 정상이다** — 이번 STEP 은 계약을 바꾸지 않았고,
바꾸려던 것(분할)을 검증 미달로 보류했다.

---

## 작업 6. 재수집 준비 — **이번에는 필요 없어졌다**

버전을 올리지 않았으므로 **재수집이 필요하지 않다.** 6-1 의 명령은 다음 STEP
에서 분할 수정이 검증된 뒤에 유효해진다. 그 시점의 준비 정보를 남긴다.

### 6-2. 예상 호출 수 (분할 수정 후)
in-gel 5청크 = **5회**. 가용 9회 → **잔여 4회**.

### 6-3. 개선될 것 / 안 될 것 (전부 **예상**)

| 개선될 것으로 **예상** | 근거 |
|---|---|
| 23·24단계 지시문에서 남의 구획 텍스트가 빠진다 | 3-3 검증에서 실제로 분리됨 |
| p.8 반복 문장이 독립 조각이 되어 **인용 가능**해진다 | 3-3 검증 |
| 24단계 텍스트 파생 duration 57600s 가 사라진다 | `16h` 가 다른 조각으로 감 |

| 개선되지 **않을** 것으로 예상 | 근거 |
|---|---|
| 반복 2/3 누락이 자동으로 회복되지 않는다 | 조각이 인용 가능해져도 모델이 주장해야 한다 |
| `unsupported_repeat_until` | 2-4 미해결(경로 없음) |
| 5번단계 누락 | 원인 미측정 |

### 6-4. 재수집 실패 경로

| 경로 | 계산 |
|---|---|
| 청크당 라벨 상한(12) 초과 | **걸리지 않는다.** 분할은 **조각**을 늘리고 **라벨 수**는 바꾸지 않는다. 수정 적용 상태에서 plan 을 계산해 `5 chunks [(1,2,3),(4,5,6),(7,),(8,),(9,)]` 로 **동일**함을 확인했다 |
| 조각 증가로 요청 크기 초과 | **미측정.** in-gel +44% 이므로 확인 필요 |
| 조각 증가로 `coverage_mismatch` | **미측정.** 페이지당 계정 대상이 늘어난다 |
| 타이머 매니페스트 이관 | 9/10 자동, **1건 판단 필요**(위 3-6 부수 측정) |

---

## 작업 7. 보고

### 7-1. 작업별 상태

| 작업 | 상태 | 이유 |
|---|---|---|
| 0. 결과 보존 | **수행함** | 4종 백업 + 매니페스트 |
| 1. 조립 결함 | **수행함** | 강등 경로 제거. 실효는 0건(모델이 주장 안 함) |
| 2. L1 | **수행함 (선언 제외)** | `may_begin_step` 이 죽은 코드 → 2-4 준수 |
| 3. 조각 분할 | **검증 후 보류** | 3-3 통과, 3-4 재검토에서 높이 규칙 과다 → 3-5 |
| 4. 반복 개수 모순 | **수행함** | 두 측정 모두 옳았음. 정정 대상 없음 |
| 5. 채점기 | **수행함** | 포함/불일치 분리 |
| 6. 재수집 준비 | **부분 수행** | 버전을 안 올렸으므로 재수집 불필요. 준비 정보만 남김 |
| 7. 보고 | **수행함** | 이 문서 |

### 7-2. 전체 테스트: 기준 1468 → **1480 passed, 1245 subtests** (감소 없음)
### 7-3. provider 호출 실사용량: **0회.** 가용 **9회 전액 보존.**
### 7-4. 백업 온전함: 채점 2종 + 매니페스트 1종 + 캐시 사본 **16파일** 확인.
캐시 5청크 digest 는 STEP 39 와 **바이트 동일**하며 전부 LOADS.

### 7-5. ⚠ 원칙 13 에 걸려 멈춘 지점 — **1건**

**L1 게이트를 `may_begin_step` 에 넣자 `test_unread_page_safety.py::
test_a_fully_read_document_is_unchanged` 가 실패했다.** 이 테스트는 「완전히 읽힌
문서에는 미판독 규칙이 아무것도 하지 않는다」를 증명한다.

**테스트를 고치지 않았다.** 대신 **설계를 고쳤다**: L1 게이트를
`may_leave_repeat_interval` 이라는 **자기 술어**로 옮겼다. 근거는 두 가지다 —
(a) 「이 단계를 시작해도 되는가」와 「반복을 벗어나도 되는가」는 다른 질문이고,
(b) 한 답에 섞은 결과가 **미판독과 무관한 이유로 미판독 테스트를 깨뜨린 것**
자체가 섞으면 안 된다는 신호였다. 내가 이번에 새로 쓴 L1 테스트만 새 술어에
맞춰 고쳤다.

부수적으로, `FeatureCode.REPEAT_UNTIL` 시범 삽입 때도 같은 테스트가 깨졌다.
그것 역시 고치지 않았고, 삽입 자체를 하지 않았다(2-4).

### 7-6. in-gel 은 「실행 가능」에 도달했는가 — **아니다**

readiness 는 여전히 `analysis_required` 이고 사유 5건이 남아 있다. 그중 4건은
검토자가 해소할 수 있고(`no_declared_safety_warnings`,
`source_page_not_fully_read`, `declined_value_not_resolved`,
`source_states_an_uncaptured_repetition`), **`unsupported_repeat_until` 하나가
남는다.** L1 의 기제는 구현·테스트되었지만 **턴 핸들러가 어떤 게이트도 보지
않으므로**(`current_index += 1`) 능력 선언을 할 수 없다. 즉 in-gel 의 실행
가능 여부는 이제 **L1 을 실제 실행 경로에 배선하는 일** 하나에 걸려 있고,
그것은 이 저장소에서 가장 안전에 민감한 코드(음성 턴 라우팅)를 건드리는
작업이므로 자기 STEP 이 필요하다.

---

## 다음 STEP 후보 (우선순위)

| 순위 | 항목 | 비용 | 근거 |
|---|---|---|---|
| **1** | **L1 을 턴 핸들러에 배선** | 0회 | in-gel 실행 가능의 유일한 잔여 조건. `may_begin_step`·`may_leave_repeat_interval`·`may_report_step_complete` 세 술어가 모두 죽은 코드다 |
| **2** | **높이 규칙 좁히기 + 4문서 검증** | 0회 검증 → **5회 재수집** | 좌측 여백 조건과 라벨 배제 조건의 상수를 4문서에서 검증한 뒤에만 |
| **3** | 5번단계 누락 원인 규명 | 0회 | 유일하게 원인 미측정인 정확도 결함 |
| **4** | headspace 수집 | 8회 | 2번 이후. repeat-until 벽 없음이 확인됨 |
| **5** | 연구자 가시성 | 0회 | STEP 38 설계안 |
