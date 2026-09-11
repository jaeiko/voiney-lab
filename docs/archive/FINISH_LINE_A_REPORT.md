# 결승선 A + 도구 통제 — 보고

## 요약 다섯 줄

1. **작업 2 완료.** 단계 진행이 **단일 통제 지점** `advance_one_step` 하나를
   거친다. 우회 경로 0건이며 **구문 트리 검사 테스트**로 고정했다.
   `may_begin_step` 은 **죽은 코드에서 살아 있는 게이트**가 되었다.
2. **작업 3 미완 — 그리고 이유가 STEP 40 때와 다르다.**
   `REPEAT_UNTIL` 을 선언하면 **이미 작동 중인 사람 게이트가 사라진다.**
   선언은 게이트를 추가하는 것이 아니라 **제거하는 것**이었다. 선언하지 않았다.
3. **in-gel 은 음성으로 실행 가능해지지 않았다.** 사유 5건 중 4건은 검토자가
   해소 가능하고, `unsupported_repeat_until` 하나가 **의도적으로** 남아 있다.
4. **작업 3-5 에서 원칙 11 후보를 찾았다.** UI 에서 PDF 를 올리면 **확인 없이**
   분석 라우트가 POST 되어 provider 호출이 나간다. 고치지 않았고,
   **워크스루 런처에 한 줄 방어**를 넣었다.
5. **작업 1 에서 내 초기 판독을 정정한다.** 죽은 tool 은 **0건**이다.

provider 호출 **0회**, 가용 **9회 전액 보존**. 캐시 5청크 **전부 LOADS**.
전체 스위트 **1489 passed** (기준 1480).

---

## 작업 2. 단계 진행 단일 통제 지점 ★ — **수행함**

### 2-1 / 2-2. 우회 경로를 없앴다

`current_index` 에 대한 쓰기는 7곳이었다. 이제:

| 종류 | 건수 | 위치 |
|---|---|---|
| **전진 (`+= 1`)** | **1** | `advance_one_step` 안 **하나뿐** |
| 배치 (`= 0` / `= index`) | 6 | `__init__` / `resume_workflow` / `activate` / `reset` / `START` / recovery |

전진은 **한 곳뿐**이고, 구문 트리를 읽는 테스트가 그것을 고정한다:

```
test_every_forward_move_is_inside_the_control_point
test_the_control_point_advances_exactly_once
test_the_remaining_writes_are_starts_and_restores   (6건으로 고정)
```

배치 6건은 **진행이 아니라 시작·초기화·복구**다. 개수를 고정해 두었으므로
7번째가 생기면 테스트가 그것이 셋 중 무엇인지 말하게 만든다.

### 2-3. 게이트를 확인한다

`advance_one_step` 은 **쓰기 전에** `may_begin_step(다음 단계)` 를 묻는다
(테스트가 소스 상 순서까지 확인한다). `may_begin_step` 이 이미 답하는 것:
미판독 페이지 미승인 / 안전 경고 미낭독 / 횟수 없는 반복.

**반복 구간 게이트는 넣지 않았다.** 그 이유는 2-3 을 무시한 것이 아니라
**이미 존재하기 때문**이다 — 아래 작업 3 참조.

### 2-4. 죽은 코드를 남긴 채 새것을 만들지 않았다

`may_begin_step` 을 **되살렸다**(통제 지점이 호출한다). 삭제하지 않은 이유:
`tests/test_unread_page_safety.py` 가 그 동작을 단언하는 **안전 테스트**이고,
삭제하면 그 테스트를 고쳐야 한다(원칙 13). 하나만 남았고, 그 하나가 산다.

### 2-5. 막으면 이유를 반환한다 — 짧게

거부는 **짧은 코드**를 반환하고(`repeat_interval_open`,
`next_step_not_startable`, `already_at_final_step`, `run_not_active`),
턴 레이어가 **한 문장**으로 바꾼다. 테스트가 **160자 미만**임을 확인한다.
전체 규칙은 시스템 프롬프트에 남고, 여기에는 **지금 사실인 것 하나**만 온다.

### 2-6. 게이트는 다음 발화를 강제하지 않는다

거부는 **아무것도 움직이지 않는다**. `advance_one_step` 본문에
`_replay` / `_pending` / `requested_transition` / `target_step` /
`self.active =` / `_workflow_status` 가 **등장하지 않음**을 테스트가 소스로
확인한다. 문을 닫을 뿐 다른 문을 열지 않는다 — **옛 계획을 재실행하지 않는다.**

### 2-7. 테스트 9건 추가 (`tests/test_single_step_advance_control_point.py`)

---

## 작업 3. in-gel 을 실행 가능까지 ★ — **미완. 선언하면 안전이 낮아진다.**

### 3-1. ⚠ `REPEAT_UNTIL` 을 선언하지 않았다 — 새 측정

STEP 40 은 「경로가 없어서 선언 못 함」이라고 결론했다. **더 정확한 사실은 이렇다:**

```
CuratedProtocolSession._current_step_readiness_blocker
  → draft.readiness.reasons 를 읽는다
  → UNSUPPORTED_REPEAT_UNTIL 이면 BLOCKED plan 을 만들고 진행을 막는다
  → 사용자가 보고한 positive observation 만 그것을 풀어 준다
```

**즉 사람 게이트는 이미 작동 중이다.** 7단계는 사용자가 「탈색됐다」고 말하지
않으면 넘어가지 않는다.

`FeatureCode.REPEAT_UNTIL` 을 프로파일에 넣으면 `assess_readiness` 가 그 사유를
**더 이상 내지 않으므로**, 위 blocker 가 `None` 을 반환하고 **7단계가 자유롭게
넘어간다.** 선언은 게이트를 **추가하는 것이 아니라 제거하는 것**이다.

**직접 확인**: 내가 만든 반복 구간 게이트를 통제 지점에 넣자
**14건이 실패**했다. 실패한 것들은 `steps 7·9·20 advance after observation`
계열 — **기존 사람 게이트가 작동함을 증명하는 테스트들**이다. 관찰이 기존
게이트는 만족시키고 새 게이트는 만족시키지 못하기 때문이다.

**두 기제를 쌓을 수 없고 화해시켜야 한다.** 그런데 관찰은
`_pending_observation_confirmation` — **한 턴짜리 인가**이고 지속 기록이 아니다
(`_recorded_source_observations` 류가 **존재하지 않음**을 확인했다). 따라서
`may_leave_repeat_interval` 이 읽을 것이 없다. **화해에는 새 설계가 필요하고,
그것은 이번 범위(리팩터링 금지, 새 결함 수정 금지) 밖이다.**

→ **내가 만든 게이트를 통제 지점에서 뺐다.** 스위트 1480 복귀 → 이후 1489.
→ **원칙 13 준수**: 그 14건을 고치지 않았다.

### 3-2. in-gel readiness (실측)

```
status: analysis_required
  1 x declined_value_not_resolved
  1 x no_declared_safety_warnings
  1 x source_page_not_fully_read
  1 x source_states_an_uncaptured_repetition
  1 x unsupported_repeat_until          ← 의도적으로 남김
P1 supports: fixed_range_repetition, informational_difference,
             operator_determined_repetition
```

**`unsupported_repeat_until` 은 사라지지 않았다.** 그것이 지금 7단계를 지키는
게이트이기 때문이다.

### 3-3. 남은 4건을 화면에서 해소하는 절차

1. `scripts/run_candidate_a.sh` → `http://127.0.0.1:8000`
2. 프로토콜 목록에서 in-gel 을 고르고 `revision_id` 가
   **`fixture-69517f0fe629d0e4dc35`** 인지 확인한다(이전 것은 `fixture-f91fbd70…`).
3. **검토 화면** → 「남은 차단 사유 N건」 목록.
4. 다음 4건에 각각 「확인」/해소 버튼을 누른다:
   `no_declared_safety_warnings` / `source_page_not_fully_read` /
   `declined_value_not_resolved` / `source_states_an_uncaptured_repetition`
5. 승인한다.

**주의**: 위 목록은 **curated fixture** 의 분석에 대한 것이다. 5청크 실측
결과는 카탈로그에 저장된 적이 없으므로(병합은 스크립트에서만 수행) 화면의
사유 구성이 위 실측과 다를 수 있다. **그 차이는 미측정이다.**

### 3-4. 절차 후 in-gel 이 선택 가능해지는가 — **아니다 (코드로 확인)**

`available_for_execution = approved and execution_ready` 이고
`execution_ready` 는 `GUIDANCE_READY` 또는 **모든** 차단 사유가 해소된
경우다. `unsupported_repeat_until` 은 `_BLOCKER_RESOLUTION` 에 **없으므로**
검토자가 해소할 수 없다. 따라서 4건을 해소해도 **실행 가능이 되지 않는다.**

### 3-5. PDF 등록 경로 — ⚠ **원칙 11 위반 후보**

**3-5-1 / 3-5-2 추적 결과** (실제 업로드는 하지 않았다):

```
UI: registerSelectedProtocol()
  1) POST /api/protocols?filename=…            ← 등록
  2) startProtocolAnalysisPolling(protocolId)
  3) POST /api/protocols/{id}/analysis         ← 여기서 provider 호출
```

`server.py:3636` 의 그 라우트가 `_protocol_analysis_model()` 을 만들고
`catalog.analyze(...)` 를 호출한다. **사용자 확인 지점이 없다.**
단일 패스 분석은 **1회**, 청크 분석이 필요한 문서는
`ProtocolChunkedAnalysisRequiredError` 로 거부되므로 0회다.
(`tests/test_frontend.py:564` 가 이 업로드→분석 순서를 **고정**하고 있다.)

**3-5-3: 원칙 11 위반 후보로 보고한다. 고치지 않았다.**

**3-5-4: 최소 조치를 넣었다 — 런처 한 줄.**

```bash
# scripts/run_candidate_a.sh
export PROTOCOL_ANALYSIS_MODEL=""
```

`require_env` 는 **빈 값을 미설정으로 취급**하므로, 워크스루 중 실수로 PDF 를
올려도 라우트가 `provider_configuration_missing` 으로 실패하고 **예산에 닿지
않는다.** 등록과 원문 기록은 그대로 동작한다. 서버·UI·테스트를 건드리지 않았다.
새 문서를 분석하려면 이 런처 밖에서 서버를 띄우면 된다 — **호출이 의도적
행위가 된다.**

---

## 작업 1. 도구 실태 (2순위) — 수행함

### ⚠ 내 초기 판독 정정

처음에 tool 이름 상수를 `tools.py` **밖에서** 찾아 「호출자 0건」 3건을 셌다.
**틀렸다.** 디스패처 `execute_tool` 이 **`tools.py` 안에** 있고, 13개 전부를
처리한다. **죽은 tool 은 0건이다.**

### 1-1 ~ 1-3. 전수 표

| tool | 분류 | 불리지 않으면 안전이 깨지는가 |
|---|---|---|
| `complete_current_step` | **(가) 코드가 결정론적으로** | — 코드가 부른다 |
| `start_step_timer` | **(가)** | — |
| `record_step_observation` | **(가)** | — |
| `get_current_step` | **(가)** | — |
| `search_approved_safety_manual` | **(나) 모델 기대** | **★ 예** — 안 부르면 일반 지식으로 답할 수 있다 |
| `create_safety_report` | (나) | **예** — 안 부르면 보고가 남지 않는다 |
| `check_safety_report_status` | (나) | 아니오 (조회) |
| `start_procedure` | (나) | 아니오 (legacy 스택, config-gated off) |
| `get_workflow_summary` | (나) | 아니오 (조회) |
| `get_step_learning_context` | (나) | 아니오 |
| `get_protocol_version_info` | (나) | 아니오 |
| `get_experiment_history` | (나) | 아니오 |
| `continue_experiment` | (나) | **예** — 재개 경로 |
| **(다) 죽은 코드** | **0건** | — |

**좋은 소식**: 워크플로우 **상태를 바꾸는** tool 4개가 전부 **(가)** 다.
`server.py:7828-7841` 의 `deterministic_tool` 이 서버 자신의 의도 판정으로
고르고 부른다 — **모델이 부르기를 기대하지 않는다.** 5주차 구조가 이미 여기엔
적용되어 있다.

**가장 위험한 부류(1-3)**: `search_approved_safety_manual` 이다. 안전 자료
조회가 모델의 재량이며, 부르지 않으면 **부르지 않았다는 사실 자체가 남지 않는다.**

### 1-4. tool 을 거치지 않는 상태 변경

| 행동 | 경로 | 상태 |
|---|---|---|
| **단계 진행** | `curated_protocol.py` 턴 핸들러 | **이번에 통제 지점으로 묶었다** |
| 타이머 시작 | `CuratedProtocolSession.start_timer` (4379행) — `start_step_timer` tool 과 별도 | **우회 가능. 미수정** |
| 실험 시작/종료 | `resume_workflow` / `activate` / `reset` — tool 없이 세션 메서드 | **우회 가능. 미수정** |

### 1-5. 매 턴 실행되는 tool — **없음**
`deterministic_tool` 은 **조건이 맞을 때만** 설정된다(`authorized_step_id`,
`authorized_timer_step_id`, `observation_arguments`, 또는 한국어 타이머 질문).
**매 턴 고정 비용은 없다.** 판정만 하고 고치지 않았다.

---

## 작업 4. 사용자 절차서 (2순위) — 수행함

### 4-1. 한 장

```bash
scripts/run_candidate_a.sh          # → http://127.0.0.1:8000
```

1. 브라우저 접속. 프로토콜 목록에서 in-gel 선택.
   **정상**: `revision_id` 가 `fixture-69517f0fe629d0e4dc35`.
2. **검토 화면**에서 「남은 차단 사유」 4건을 해소하고 승인.
   **정상**: 각 사유에 해소 버튼이 있고, 누르면 목록에서 빠진다.
3. **연구자 화면**에서 in-gel 을 고른다.
   **⚠ 정상 동작은 「선택 불가」다** — `unsupported_repeat_until` 이 남아 있다.
   이것이 이번 결승선의 실제 도달 지점이다.
4. 음성 단계 안내와 반복 구간은 **현재 이 경로로 도달할 수 없다.**

### 4-2 / 4-3. 반복 구간에서 에이전트가 할 말

L1 기제(`human_led_repeat_disclosure`)는 **구현·테스트되어 있으나 턴 핸들러에
연결되어 있지 않다.** 따라서 **사용자가 이 문장을 실제로 듣지 못한다.**
문장 자체는 이렇다(테스트가 고정):

```
"2~7번은 반복 구간입니다. 원문 조건을 그대로 읽어 드립니다.
 언제 끝낼지는 원문을 보고 직접 판단해 주시고, 끝나면 말씀해 주세요."
+ 원문: "7 Repeat steps 2-7 until the gel band is fully destained."
```

**현재 실제로 들리는 것**은 기존 경로의 BLOCKED 안내다:
「7단계는 관찰 결과가 충족될 때까지 반복해야 하지만 … 완료 처리되지 않았습니다.」
그리고 사용자가 「탈색됐다」고 말하면 진행된다. **이것이 현재의 L1 이다** —
문구가 다르고 기록이 남지 않는 형태로.

### 4-4. 안 될 때 확인할 3가지

1. `revision_id` 가 `fixture-69517f0f…` 인가 (옛 리비전을 고르면 반복이 2건으로 보인다)
2. 서버 로그의 `protocol.catalog.configuration` 줄에서 `visible_protocols` 가 1 이상인가
3. 「선택 불가」가 뜨면 **정상**이다 — 위 3-4 대로 아직 실행 가능이 아니다

---

## 작업 5. 죽은 코드·더티 파일 (3순위) — 수행함

### 5-1 / 5-2. 삭제하지 않고 「배선 필요」로 남긴 것

| 항목 | 이유 |
|---|---|
| `may_leave_repeat_interval` + L1 6개 메서드 | 안전 게이트. 턴 핸들러 배선 필요 |
| `may_report_step_complete` | 안전 게이트(`may_begin_step` 이 호출) |
| `source_lineage` 파라미터 | 안전 게이트. 호출자 0건 (결정 B 보류) |

### 5-3. 삭제한 죽은 코드 — **0건**
tool 은 13개 전부 살아 있고(작업 1 정정), 나머지 후보는 전부 안전 게이트라
5-2 에 따라 남겼다. **확신 없는 삭제를 하지 않았다.**

### 5-4. 스크래치·백업 파일 조사 — **삭제할 것 0건**

- 추적되고 있는데 있으면 안 되는 파일: **없음**
  (`scripts/pilot_state_backup.py` / `tests/test_pilot_backup.py` 는 정식 기능)
- `.gitignore` 누락: **없음** (`data/development_cache/` 포함됨)
- 이전 STEP 백업: `data/development_cache/` 안에만 있고 **gitignore 대상**
  → **삭제하지 않았다** (캐시 5청크 + accuracy 백업 + segver6 사본 16파일 전부 보존)

### 5-5. 정리 후 스위트 — **1489 passed** (삭제한 것이 없으므로 되돌릴 것도 없음)

---

## 작업 6. 5주차 원칙 점검 (3순위) — 조사·제안만

### 6-1 / 6-2. 소프트 제어로만 남아 있는 규칙 (난이도 순)

| # | 규칙 | 현재 | 구조화 가능? | 난이도 |
|---|---|---|---|---|
| 1 | 「안전 자료는 tool 로 조회하고 일반 지식으로 답하지 않는다」 | 프롬프트 문장뿐 | **가능** — 안전 질문 의도로 판정되면 `deterministic_tool` 에 `search_approved_safety_manual` 을 넣는다 (단계 tool 4개와 같은 방식) | 낮음 |
| 2 | 타이머 시작이 tool 을 거친다 | `start_timer` 세션 메서드가 우회 가능 | **가능** — 단계 진행과 같은 단일 통제 지점 패턴 | 낮음 |
| 3 | 「완료 판단은 사람이 한다」 | 관찰 경로 + 하드코딩된 라벨 `{7,9,20}` | **가능** — L1 화해(작업 3) | **중** |
| 4 | 실험 종료 시 필수 기록 | **미측정** — `end` 게이트를 찾지 못했다 | 미판정 | 미판정 |
| 5 | 편차 기록이 tool 을 거친다 | **미측정** | 미판정 | 미판정 |

### 6-3. 세 항목

- **실험 종료(end) 게이트**: `CuratedProtocolAction.END` 에 해당하는 필수 기록
  검사를 **찾지 못했다. 미측정으로 보고한다** — 없다고 단정하지 않는다.
- **타이머 시작**: `start_step_timer` tool 이 있으나 `start_timer` 세션 메서드가
  **별도 경로**다. **tool 을 거치지 않는 경로가 있다.**
- **편차 기록**: **미측정.**

### 6-4. Tool output 이 지시 전달 지점인가

**부분적으로만.** `deterministic_tool` 결과는 상태를 돌려주지만 「다음에 무엇을
하라」를 담지 않는다. **넣을 자리는 이번에 만든 거부 코드 경로**다 —
`_advance_refusal_sentence` 가 이미 「무엇이 막혔고 무엇이 필요한지」를
한 문장으로 만든다. 그 패턴을 `get_current_step` 결과에 확장하는 것이 자연스럽다.

**길이 위험**: 현재 BLOCKED 안내문은 3개 언어 × 2~3문장으로 **길다**
(위 3-2 의 기존 문구 참조). 확장할 때 **전체 규칙을 반복하지 않는 것**이
핵심이며, 이번에 추가한 거부 문장은 **160자 미만을 테스트로 고정**했다.

### 6-5. 구현하지 않았다. 우선순위는 위 표의 # 순서.

---

## 발견했지만 고치지 않은 것 (범위 제한 준수)

| # | 내용 | 위치 |
|---|---|---|
| 1 | **원칙 1 위반** — in-gel 의 단계 라벨 `{"7","9"}` 와 `"20"` 하드코딩 | `curated_protocol.py:6877-6881` |
| 2 | **원칙 11 후보** — PDF 업로드가 확인 없이 provider 호출 | `server.py:3636` + `static/index.html` |
| 3 | 타이머 시작이 tool 을 우회 | `curated_protocol.py:4379` |
| 4 | 실험 시작/종료가 tool 을 우회 | `resume_workflow` / `activate` / `reset` |
| 5 | 관찰이 한 턴짜리 인가라 L1 과 화해 불가 | `_pending_observation_confirmation` |
| 6 | 실험 종료 필수 기록 게이트 **미측정** | — |
| 7 | 편차 기록 tool 경유 여부 **미측정** | — |

---

## 최종

| 항목 | 결과 |
|---|---|
| 작업 2 (★) | **수행함** |
| 작업 3 (★) | **부분 수행** — 3-2·3-3·3-4·3-5 완료, 3-1 은 **선언하지 않음이 정답**으로 판정 |
| 작업 1 | **수행함** (초기 판독 정정 포함) |
| 작업 4 | **수행함** |
| 작업 5 | **수행함** — 삭제 0건 |
| 작업 6 | **조사·제안만** (4·5 항목 미측정) |
| 전체 테스트 | **1489 passed, 1251 subtests** (기준 1480, 감소 없음) |
| provider 호출 | **0회.** 가용 9회 전액 보존 |
| 캐시 5청크 | `48d430eb` / `5619f5cf` / `908626b6` / `09b63322` / `a843eff5` **전부 LOADS** |
| 계약 상수 | seg v6 / schema v10 / prompt `dca143b5b81ddffa…` **불변** |
| 삭제한 것 | **없음** |
| 원칙 13 저촉 | **1건** — 아래 |

### 원칙 13 에 걸려 멈춘 지점

**반복 구간 게이트를 통제 지점에 넣자 14건이 실패했다.** 그중
`test_source_observation_persists_once_before_steps_7_9_20_advance`,
`test_step_7_observation_criterion_survives_intervening_qa` 등은
**기존 사람 게이트가 작동함을 증명하는 테스트**다.
**고치지 않았고, 내 게이트를 뺐다.** 그 과정에서 「선언은 게이트를 제거하는
것」이라는 사실을 알게 되었으므로, 멈춘 것이 옳았다.

### ★ in-gel 이 음성으로 실행 가능해졌는가

**아니다.** 그리고 지금은 그것이 **의도된 상태**다 — `unsupported_repeat_until`
을 없애면 7단계를 지키는 유일한 사람 게이트가 함께 사라진다. 실행 가능까지
남은 일은 **하나**다: 사용자 관찰(한 턴 인가)과 L1 완료 선언(지속 기록)을
**하나의 기제로 화해**시키고, 그것을 턴 핸들러에 배선하는 것. 그 뒤에야
`REPEAT_UNTIL` 선언이 게이트를 **옮기는** 일이 되고, 지금처럼 **없애는** 일이
되지 않는다.
