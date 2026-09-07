# 결승선 A 개통 — 테스트 전제 갱신 후 REPEAT_UNTIL 선언

provider 호출 0회. `CLAIM_SCHEMA_VERSION` · `EVIDENCE_SEGMENT_VERSION` ·
시스템 프롬프트 · `capability_policy.profile_id` 모두 불변.
커밋 2개로 분리(C5).

**기준 정정**: 지난 보고서의 "1508"은 20번째 테스트를 추가하기 **전**에 돌린 값이었다.
커밋 `e354d67` 시점의 실제 기준은 **1509**다. 현재 **1521 passed** (감소 없음).

---

## C2·C3 — 각 테스트가 지키려던 성질과 그 성질의 현재 보관처

| # | 테스트 | 지키려던 성질 (한 줄) | 선언 후 그 성질은 |
| --- | --- | --- | --- |
| T1 | `test_experiment_protocol::test_repeat_until_remains_explicit_and_policy_can_evolve` | ① "repeat until" 문장은 `REPEAT_UNTIL` 로 탐지되고 유한 반복으로 격하되지 않는다 ② readiness 는 넘겨받은 정책의 함수다 ③ 정책이 지원하지 않는 기능은 대응 `unsupported_*` 사유를 올린다 | ①② 그대로 주장. ③ 은 **지원하지 않는 정책**을 만들어 같은 테스트 안에서 계속 주장 (`repeat-until-incapable`). ③ 은 6개 다른 feature code 로 `test_experiment_protocol`(754·758·790·826·830·919), `test_commercial_protocol_fixtures:72`, `test_operator_determined_repetition:117` 에서도 독립 보관 |
| T2 | `test_pdf_to_session_walkthrough::test_readiness_is_reached_and_names_its_blockers` | 파이프라인이 판정에 도달하고 **차단 사유 집합을 정확히** 명명한다(더도 덜도 없이) | 성질 그대로, 기대 원소만 3→2. 추가로 "반복 construct 는 사유와 함께 사라지지 않았다"를 직접 주장 |
| T3 | `test_pdf_to_session_walkthrough::test_resolving_the_ambiguities_narrows_the_wall` | ① 모호성은 감사 경로(id·판단·근거 인용·행위자)로만 해소된다 ② **일부만 해소하면 아무것도 활성화되지 않는다** | ① 그대로. ② 는 **두 반쪽으로 완전 보관** — 안전 게이트만 확인: 같은 클래스의 `test_activation_still_refuses_on_reasons_nobody_may_clear`(무수정 통과) / 모호성만 해소: **신규** `test_resolving_every_ambiguity_alone_does_not_clear_the_wall`. 본체는 "두 개를 다 밟으면 벽이 내려간다 + 그래도 반복 단계 게이트는 남는다"로 갱신 |
| T4 | `test_protocol_claim_analysis::test_claims_merge_then_assemble_with_exact_final_provenance_and_blockers` | merge→assemble 이 정확한 provenance 를 내고, `repeat_condition` 주장이 **버려지지 않는다** | 후자를 readiness 사유로 간접 확인하던 것을 `isinstance(construct, RepeatUntil)` 직접 확인으로 교체(더 강함) + 사유가 사라진 것도 명시 주장 |
| T5 | `test_curated_protocol_cascade::test_completion_criteria_quotes_explicit_source_result_without_mutation` | ① LLM 호출·상태 변경 없이 원문 확인 기준을 인용한다 ② 완료를 검증할 수 없는 단계에서는 **완료 처리하지 말라고 말하고 이유를 댄다** | ① 그대로. ② 는 문구가 바뀌었고 근거가 개선됨 — "검토 필요" 가 아니라 **원문 종점 인용 + 사용자가 관찰해야 한다**. 독립 보관: 신규 `TheOperatorIsStillToldTests` |
| 부수 | `tests/development_activation.py` docstring | in-gel 이 실행 불가인 **이유** 서술 | 갱신: 벽은 이제 감사 경로 자체이며, 이 헬퍼는 endpoint 관찰 게이트를 우회하지 않는다는 점을 명시 |

**삭제한 테스트 0개.** 각 갱신 지점에 갱신 이유를 주석/독스트링으로 남겼다(C1).

## C4 — 핵심 성질 검증: "모호성 해소만으로 실행 벽이 사라지지 않는다"

선언 상태(capable policy)에서 카탈로그를 통해 측정:

| 조작 | `every_ambiguity_resolved` | `readiness_gates_cleared` | `activate_development` |
| --- | --- | --- | --- |
| 모호성 4건 전부 해소, 안전 게이트 **미확인** | True | **False** | **거절** |
| 안전 게이트 확인, 모호성 **미해소** | False | **False** | **거절** |
| 둘 다 | True | True | 성공 |

→ **참이다. 멈출 사유 없음.** 첫 행이 신규 테스트로 고정되었고, 둘째 행은 기존
테스트가 무수정으로 계속 증명한다.

---

## 작업 1 — 선언 후에도 게이트가 선다는 것을 먼저 못 박음 (커밋 A)

`tests/test_repeat_until_declaration_properties.py` 신규 10개.
전역 상수를 건드리지 않고 `assess_readiness(protocol, capability_policy=…)` 로
선언 상태를 만들고, 각 테스트가 **먼저 `unsupported_repeat_until` 부재를 주장**한
뒤 본론을 주장한다. 그래서 선언 전에도 후에도 통과한다.

- **1-1** `test_the_repeat_step_is_asked_for_its_endpoint_not_advanced` (7·9·20),
  `test_a_route_around_the_question_is_refused_by_the_gate_itself` — 되묻기 변환을
  우회하는 stale 경로에서 `block_reason=endpoint_observation_not_reported` 와
  **원문 문장 인용**을 확인. 선언 후 이 분기가 완료 주장과 전진 사이에 남는
  유일한 방벽이므로 직접 고정했다.
  덧붙여 `test_the_gate_sits_at_the_step_that_carries_the_sentence` — 2-7·8-9 는
  범위의 마지막 단계가 곧 anchor 지만 17-18 은 문장이 20단계 본문에 있어 게이트가
  **18이 아니라 20**에 선다는 선택을 명시적으로 고정.
- **1-2** `test_a_release_with_no_experiment_record_is_refused_and_rolled_back`
  (7·9·20) — 실험 세션 기록이 열려 있지 않은 턴: 인덱스 불변,
  `endpoint_observations()` 비어 있음, 게이트 다시 서 있음, 발화
  "실험 세션 기록이 활성화되지 않아…".
- **1-3** 두 개 통과 확인 후 작업 2로 진행. 커밋 A 시점 **1519 passed**.

커밋 A에는 두 곳의 코드 변경이 함께 들어갔다 — 그 성질이 코드 없이는 성립하지
않기 때문이다: 다음 단계 미리보기의 "진입 승인이 아닙니다" 문구와 완료조건
답변의 거절 문구가 둘 다 readiness 사유로 골라지고 있어, 선언하면 게이트가 닫힌
채로 안내만 조용히 사라질 상태였다. 둘 다 반복 anchor 를 읽도록 바꿨다.

## 작업 2 — 전제 갱신 + 선언 (커밋 B)

**2-1** 위 표대로 갱신. **2-2** `P1_CAPABILITY_POLICY.supported_features` 에
`FeatureCode.REPEAT_UNTIL` 추가, `profile_id` 는 `p1-conservative` 유지(캐시된
claim payload 가 이 문자열과 대조되므로 개명하면 캐시 전량 무효 → provider 호출).

**2-3 남는 차단 사유 2개**

| 사유 | 단계 | `ProtocolCatalog._BLOCKER_RESOLUTION` | 검토자 조작 |
| --- | --- | --- | --- |
| `unresolved_ambiguity` | 20 (p.8) | 있음 | `이 모호성을 해소` (판단 + 근거 인용 필수) |
| `no_declared_safety_warnings` | — | 있음 | `안전 경고 확인 처리` |

둘 다 해소 가능. 신규 테스트 `test_the_two_remaining_reasons_are_both_reviewer_clearable`
로 고정.

**2-4** **1521 passed, 1293 subtests** (기준 1509 → 감소 없음).
`replay_turns.py` 실행, `compileall` ok, `git diff --check` clean.

### 선언이 드러낸 결함 하나 — 고쳤다 (규칙 B)

선언은 fixture 바이트를 바꾸지 않지만 **저장되는 분석(readiness)** 을 바꾼다.
그런데 개발 fixture 의 analysis id 는 `curated-{fixture_sha256}` 로 **fixture
바이트만** 명명하고 있었다. 그래서 이미 materialize 된 카탈로그에서는
"같은 id, 다른 payload" 가 되어 `bootstrap_development_fixture` 가
`DuplicateProtocolIdentifierError` 를 던진다 — 그리고 그 호출은
`scripts/run_candidate_a.sh` **기동 시점**에 있다.

파일럿 카탈로그를 읽기 전용(`mode=ro`)으로 확인한 결과 해당하는 상태였다:

```
analysis_id  curated-69517f0fe629d0e4dc356c78ff3d407ed0f510de24d3  (analysis 4)
저장된 payload_sha256 47df9633cf99025a5d28…
선언 후 payload_sha256 824e9b54686cefb737a5…   MATCH: False
```

STEP 31 의 캐시 키와 같은 결함이다 — 질문을 볼 수 없는 키는 지킬 수 없는 적중을
보고한다. `ProtocolCatalog._development_analysis_identity()` 를 두어 analysis id
와 bootstrap 이벤트 키가 **분석 payload 의 지문까지** 명명하게 했다. 이제 바뀐
분석은 다른 분석이므로 **새 analysis 리비전으로 append** 된다. 덮어쓰기·삭제
없음: 이전 리비전과 그 소견은 그대로 남는다. 신규 테스트 2개로 고정
(`TheAnalysisIdentitySeesTheAnalysisTests`), 그중 하나는 **이전 리비전에 기록된
소견이 새 리비전을 해제하지 않는다**는 것 — 사용자가 겪은 "엉뚱한 항목 승인"
함정의 두 번째 형태 — 를 증명한다.

---

## 작업 3 — 리비전 지문 추적

**3-1.** `revision_id` 는 fixture JSON 바이트의 지문이다:
`CuratedProtocolFixture.__post_init__` → `f"fixture-{self.fixture_sha256[:20]}"`.
`candidate_a_curated_analysis.json` 의 sha256 이 곧 지문이므로, 파일이 1바이트라도
바뀌면 지문이 바뀐다. git 이력에서 blob 별로 계산한 값:

| 커밋 | 지문 | 제목 |
| --- | --- | --- |
| `39792cc` (2026-09-06) | `fixture-69517f0fe629d0e4dc35` | Move the page-8 repeat's anchor to the step that owns the sentence |
| `4268fda` | `fixture-f91fbd70e3f8fbe3aa0b` | Add the third repeat the source states and the reference was missing |
| `2eff979` | `fixture-fb869290f1b52afab91f` | Extract page text with pypdfium2 and cross-check it independently |
| `4246363` | `fixture-c2779c24924dbeb3c83d` | Promote Voice Workflow Agent to repository root |

(`08863e3` 는 `--follow` 가 경로 생성 커밋을 함께 보여준 것으로, 그 시점에 파일이
없어 빈 문자열 해시가 나온다. 지문 이력이 아니다.)

**변경 주체는 커밋 `39792cc` 이고, JSON 델타는 정확히 한 필드다:**

```
protocol.constructs[2].step_id : 'candidate-a-step-18' -> 'candidate-a-step-20'
```

그 외 전 필드 동일. `repeated_step_ids` 는 17-18 그대로. 같은 커밋에서 sidecar
4개(`provenance` / `localization.ko` / `timers` / `visuals`)가 각자 선언한
`fixture_sha256` 을 새 값으로 갱신했다 — 로더가 불일치 시 거절하기 때문이다.

**3-2 판정: 의도된 변경이다.** 근거 네 가지.

1. 사용자가 원문을 확인한 결과에 따른 수정이다(8쪽 상단 "Expected result" 박스는
   21단계 위에 단독으로 있고, 20단계가 선언한 결과를 서술한다 — 18단계에 붙은
   것이 아니다). 커밋 본문에 그 판단이 적혀 있다.
2. 지문이 바뀐 것은 설계된 메커니즘이다 — `revision_id` 는 내용 지문이므로 내용이
   바뀌면 바뀐다. 부작용이 아니라 작동이다.
3. sidecar 4개를 같은 커밋에서 함께 옮겼으므로 부분 변경이 아니다.
4. **오늘 이 값이 실제로 하중을 받는다.** `steps_anchoring_a_repetition` 이 읽는
   것이 바로 이 anchor 다. 측정:
   - 현재 anchor → 게이트 라벨 `['20','7','9']`
   - `f91fbd70` 시절 anchor → 게이트 라벨 `['18','7','9']`
   즉 09-06 의 그 수정이 없었다면 작업 1의 하드코딩 제거가 원래의 {7,9,20} 을
   재현하지 못했다. 지문 변경은 그 수정의 부산물이고, 수정 자체가 전제였다.

---

## 작업 4 — 절차서 갱신

`docs/OPERATOR_PROCEDURE_REPEAT_STEP_GATE.md` (160행).

- **4-1** "선택 불가" 전제를 실제 상태로 교체. 실행 벽은 2단계를 끝까지 밟았을
  때만 내려가며, 반복 단계 게이트는 그와 별개로 남는다는 점을 맨 앞에 명시.
- **4-2** 0절을 새로 넣어 **fixture 항목 vs 업로드 PDF(local_pdf) 항목**을 표로
  구분했다. 측정한 사실:
  - fixture: `protocol_id = candidate-a-curated-development-v1`,
    카탈로그 `revision_id = pdf-1-analysis-N`
  - 업로드 PDF: `protocol_id = protocol-<PDF sha256 앞 32자>`
    (예: `protocol-63d81102fb644fca21e1c2296b566987`),
    분석 후 `revision_id` 도 `pdf-1-analysis-N`
  - **버전 문자열은 둘이 같은 모양이므로 구분에 쓸 수 없다. 구분 기준은
    `protocol_id` 하나뿐이다.** 같은 PDF를 올렸다면 제목·파일명까지 같다.
  - `fixture-69517f0fe629d0e4dc35` 는 fixture 자체 지문이며 **검토 엔드포인트가
    받는 값이 아니다**(그 값은 `pdf-1-analysis-N`). 지난번 혼동의 원인 후보다.
  - 클릭 순서: ① `이 모호성을 해소`(판단 `single_statement_is_authoritative` +
    8쪽 근거 세그먼트 최소 1개 — 후보 9개 중 맨 위 `Expected result The gel
    should look white (dehydrated) …`) ② `안전 경고 확인 처리`
    ③ `데모·연구용 초안 활성화`(반드시 마지막).
  - 선언 직후 1회 주의: 새 analysis 리비전이 append 되므로 **선언 이전의 확인·
    해소는 이어지지 않는다**. 다시 밟아야 한다.

전체 경로를 임시 카탈로그에서 끝까지 걸어 측정했다(파일럿 DB 미개방):
`readiness_gates_cleared: True` → `activate_development` 성공 →
`available_for_execution: True` → `load_executable_fixture` 성공.

---

## 검증

```
python -m pytest -q            → 1521 passed, 1293 subtests (기준 1509, 감소 없음)
python scripts/replay_turns.py → 실행 완료
python -m compileall -q src tests scripts → ok
git diff --check               → clean
삭제한 테스트                  → 0개
```

**provider 호출 0회.** `--execute` 플래그가 든 명령을 실행하지 않았다.
`data/development_cache/chunk_analysis` 최신 엔트리 mtime 은 2026-09-06 13:09 로
이번 작업 중 새로 쓰인 항목이 없다. 파일럿 데이터베이스는 `mode=ro` 로만 읽었고
쓰기·삭제하지 않았다.

## in-gel 선택 가능 여부

> **가능하다.** `REPEAT_UNTIL` 선언으로 해소 불가 사유 3건이 사라졌고, 남은 2건
> (`unresolved_ambiguity` p.8 · `no_declared_safety_warnings`)을 검토 화면에서
> 해소한 뒤 개발 활성화하면 `available_for_execution: True` 가 되고
> `load_executable_fixture` 가 성공한다 — 측정으로 확인. 반복 단계 7·9·20 의
> 관찰 게이트는 선택 가능해진 뒤에도 그대로 서 있다.

## 발견했지만 고치지 않음 (목록만)

1. **`unresolved_ambiguity` 블로커 payload 의 `ambiguity_id` 가 `None` 이다.**
   브라우저가 construct 목록과 대조해 메워 쓰고 있어(`ambiguityIdFor`) 동작하지만,
   서버가 그 id를 직접 주지 않는다.
2. **인용문에 단계 라벨이 붙어 나온다** (`“7 Repeat steps 2-7 until …”`).
   앞머리 숫자를 떼는 규칙은 문서별 정리 규칙이 되므로 원문 그대로 두었다.
3. **`acknowledge_unread_page` 프로덕션 호출자 없음.** (유지)
4. **`SourceLineage` 를 넘기는 프로덕션 호출자 없음.** (유지)
5. **조각 융합**(section heading/badge 가 step 본문에 흡수) — 버전 상향 + 재수집
   5회 필요. (유지)
6. **in-gel 반복 2/3 미포착** — `source_states_an_uncaptured_repetition` 이
   문서를 막고 있다. 단, 이 사유는 큐레이션 fixture 의 readiness 에는 없고
   파이프라인 draft 쪽 이야기다. (유지)
7. **PDF 업로드가 확인 없이 provider 호출을 쓴다** — 원칙 11 후보. (유지)
8. **타이머 시작과 실험 시작/종료가 도구를 우회한다.** (유지)
9. **실험 종료 기록 게이트와 이탈 기록 도구 경로: 미측정.** (유지)
