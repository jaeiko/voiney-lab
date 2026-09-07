# 결승선 A 마무리 — 관찰 게이트를 문서에서 세운다

작업 범위: 지시한 4개 작업만. provider 호출 배분 0회.
기준 테스트: 1489 → **1508 passed, 1272 subtests passed** (신규 20개 추가,
기존 테스트 파일은 **한 줄도 수정하지 않았다** — `git diff --name-only -- tests/`
결과 없음).

---

## 작업 1 — in-gel 라벨 하드코딩 제거 · **완료**

### 1-1. 없앤 자리

`curated_protocol.py` 에서 in-gel 의 단계 번호로 동작을 고르던 자리는 셋이 아니라
**다섯**이었다. 셋은 지시받은 것이고, 둘은 같은 게이트 경로에서 찾은 것이다.

| 자리 | 이전 | 이후 |
| --- | --- | --- |
| 게이트가 서는 곳 (`_current_step_readiness_blocker`) | `{"7","9"}` + `"20"`, blocker 종류와 짝지음 | 반복 construct 가 붙은 단계인지로 판정, blocker 종류 짝 해제 |
| 종점 predicate 부여 (`build_step_semantic_frame`) | `step.source_label in {"7","9","20"}` | `step.step_id in steps_anchoring_a_repetition(fixture)` |
| 발화를 종점 보고로 읽어도 되는 단계 (`plan`) | `source_label in {"7","9","20"}` | 같은 파생 집합 |
| 종점 미달 응답 문장 | `== "7"` / `== "9"` / else 로 `"fully destained"`·`"Steps 2–7"`·`"Steps 17–18"` 선택 | construct 의 `condition_source_text` 인용 + `repeated_step_ids` 라벨 범위 |
| 종점 되묻는 질문 | `== "7"` 이면 탈색 질문, **그 외 전부** 탈수 질문 | construct 의 `condition_source_text` 인용 |

뒤 두 줄은 원칙 1 위반이면서 동시에 **원칙 8 위반**이었다. 다른 문서에서
"원문이 지시한 17–18단계 반복 주기를 계속하세요" 라고 말하거나 "젤이 흰색으로
변하고 탈수 종점에 도달했나요" 라고 물었을 것이다. 그 문서에는 그런 문장이
없다. 지어낸 안전/완료 지시가 실험자 귀에 닿는 지점이라 같이 고쳤다.

**남긴 라벨 비교 2곳** — `_observation_predicate` 안의
`step_label in {"7"}` / `{"9","20"}`. 이것은 게이트가 아니라 **종점 표현 두 벌**
(투명·탈색 / 흰색·탈수) 중 어느 것을 대조할지 고르는 자리다. 두 벌은 in-gel 의
문장이고, 한쪽을 다른 단계에 적용하면 그 단계에 없는 완료 기준을 받아들이는
것이 된다(원칙 8). 표현을 인식하지 못하면 되묻기 확인 경로로 떨어지고, **그
경로는 라벨을 전혀 읽지 않는다**(`binary_reply in {"affirmative","negative"}`).
그래서 이 모듈이 처음 보는 문서에서도 게이트는 "네/아니요"로 해제된다 — 표현
인식이 아니라 확인으로. 이 판단은 신규 테스트
`test_no_source_label_set_decides_the_gate_any_more` 에 근거와 함께 고정했다.

### 1-2. 파생 규칙

`steps_anchoring_a_repetition(fixture)` — 분석이 실은 construct 중 반복 범위를
가진 것(`repeated_step_ids` 또는 `start_step_id`)의 **anchor step_id** 집합.
범위의 마지막 단계가 아니라 anchor 인 이유: in-gel 8쪽은 "repeat steps 17-18"을
20단계 본문에 적어 두었고, 실험자는 문장이 적힌 자리에서 그 지시를 만난다.
범위를 갖지 않는 construct(예: SourceAmbiguity)는 반복이 아니므로 기여하지 않고,
anchor 가 없는 반복은 만날 자리가 없으므로 버린다(게이트 0번이 아니라 게이트 없음).

### 1-3. 문서별 게이트 수 (provider 호출 0회, 서버 자체 페이지 텍스트에서 측정)

`explicit_repeat_instructions` — 문서가 반복을 진술한 자리의 수.

| 문서 | 쪽 | 문서가 진술한 반복 | 파생될 게이트 수 |
| --- | --- | --- | --- |
| in-gel | 9 | 3 (p5 `2-7`, p6 `8-9`, p8 `17-18`) | 3 |
| headspace | 16 | 5 (p5 `12-15`, p5 `19-20`, p6 `23-26`, p12 `36-41`, p14 `43-50`) | 5 |
| intracellular | 34 | 0 | 0 |
| ANKOM | 40 | 0 | 0 |

in-gel 큐레이션 픽스처에서 실제로 파생된 anchor 는
`candidate-a-step-07 / -09 / -20`, 라벨로 `['20','7','9']` — **이전 하드코딩
집합과 완전히 동일**하다. 게이트도 같은 세 자리에 서고, predicate id
(`candidate_a_step_7_endpoint` 등) 문자열도 그대로다. 20단계의 blocker 는
`unresolved_ambiguity` 이고 7·9단계는 `unsupported_repeat_until` 이었다 —
blocker 종류 짝을 푼 이유가 이것이다(20단계의 모호성은 곧 그 반복의 범위다).

### 1-4. 기존 테스트

`-k observation` 38 tests / 54 subtests **전부 통과, 수정 없음**. 그중
7·9·20단계가 관찰 후 전진함을 직접 주장하는 것:
`test_source_observation_gates_steps_7_9_and_20`,
`test_stale_observation_confirmation_never_advances`,
`test_observation_yes_no_inherits_only_owned_source_predicate`,
`test_source_observation_persists_once_before_steps_7_9_20_advance`,
`test_observation_persistence_failure_restores_steps_7_9_20`.
전체 스위트도 1508 통과. 원칙 13 정지 사유 없음.

---

## 작업 2 — 게이트를 여는 관찰의 지속 기록 · **완료**

### 2-1. 기존 기록 경로 (조회 경로: **있음**)

찾은 것을 썼고 새로 만들지 않았다.

- 저장: `experiment_observations` (workspace store, `MIGRATION_2_TO_3`).
  append-only 트리거(`_no_update` / `_no_delete`)가 걸려 있다.
- 필드 대응: `protocol_id`·`protocol_revision_id` ← `experiment_sessions` 행,
  `protocol_step_id`·`protocol_step_label`, `content`(발화 원문),
  `author_principal_id`(선언자), `created_at`(시각),
  `capture_source='voice'`, `knowledge_effect='observation_only'` (CHECK 제약).
- 쓰기 지점: `server._record_workspace_observation` — `reported_observation`
  이 있는 모든 턴.
- **조회 경로: 있음** — `WorkspaceStore.session_timeline` 및
  `GET /api/workspace/experiments/{session_id}/timeline`.
  (부가 경로: `ExperimentReportStore` 의 `step_completed` 이벤트가
  `user_wording` + `payload.observation_predicate` 를 남긴다.)

### 2-2. 막은 구멍

게이트를 여는 관찰의 저장은 **시도되되 조용히 생략**될 수 있었다.
`session.experiment_state_version is None` 이면(실험 세션 기록이 열려 있지 않으면)
`workspace_observation_required` 가 거짓이어서 게이트가 열리고 단계가 이동하고
아무것도 기록되지 않았다.

- `workspace_observation_required` 에 "게이트를 연 양성 관찰" 조건을 더했다.
  기록이 없으면 턴이 차단된다.
- 그 분기가 `state_changed=False` 라고 말하면서 실제로는 이동해 있던 문제도
  같이 닫았다 — `curated._restore(checkpoint)` 로 되돌린다.
- 결과: **지속 기록을 받지 못한 해제는 해제가 아니다.**

### 2-3. 세션 자체 기록 (게이트의 발판)

`CuratedProtocolSession._endpoint_observations` — step_id → 기록 1건.
키는 정확히 9개:

```
protocol_id, protocol_revision_id, step_id, step_label,
observation_predicate, utterance, declared_at,
declared_by_principal_id, declared_by_role
```

- `utterance` 는 공백만 정규화한 발화 원문이다. 요약·의역하지 않는다.
- **반복 횟수 키는 없다.** 원문에 횟수가 없으므로 횟수를 쓰면 문서에 없는 완료
  기준이 된다(원칙 8). 테스트 `test_no_round_count_is_recorded_or_inferred`.
- `declared_by_principal_id` 는 workspace 가 없으면 `None` 이다. "local" 같은
  아무도 갖지 않은 신원을 채우지 않는다(원칙 7). 귀속된 행은 같은 턴이 쓰는
  `experiment_observations` 이고, 위 2-2 때문에 그 행 없이는 해제가 성립하지
  않는다. 서버는 `_voice_turn_actor()` 로 principal 을 해석해
  `plan(..., actor_principal_id=, actor_role=)` 으로 넘긴다.
- `reset()` 에서 지운다(새 실행은 새 관찰을 진다). `_checkpoint`/`_restore` 에
  포함해 롤백된 턴이 기록을 남기지 않게 했다.

기록은 readiness 를 해제하지 않는다 — 미판독 페이지 확인·안전 경고 낭독과 같은
실험 세션 사실이다(`test_the_record_clears_no_readiness_gate`).

---

## 작업 3 — REPEAT_UNTIL 선언 · **측정 완료, 선언은 되돌림. 사용자 결정 필요 (원칙 13)**

### 3-1. 두 조건은 모두 참이었다

`P1_CAPABILITY_POLICY.supported_features` 에 `FeatureCode.REPEAT_UNTIL` 을 넣고
측정했다.

- **이유 사라짐** — in-gel readiness 이유 5개 → 2개.
  `unsupported_repeat_until` ×3 소멸.
- **게이트 남음** — 7·9·20단계에서 `endpoint_observation_outstanding` 이 참,
  관찰 없는 전진 시도는 `block_reason=endpoint_observation_not_reported` 로
  차단, 원문 인용 문장이 나왔다. 관찰을 말하면 전진하고 기록이 남았다.
  반복이 없는 단계(1·3·12·16·25)는 영향 없음.

즉 지시한 3-3의 두 조건은 동시에 참이다. 게이트는 능력 프로필이 아니라 문서에
서 있다.

### 3-2. 그런데 기존 테스트 5개가 깨진다 → **멈추고 보고한다**

선언 상태에서 `1484 passed, 5 failed`. 다섯 개 모두 "P1 은 repeat-until 을
지원하지 않는다"를 전제로 한 주장이다. 원칙 13에 따라 **한 줄도 고치지 않고**
선언을 되돌렸다(현재 트리는 1508 green).

| 테스트 | 주장 | 선언 후 |
| --- | --- | --- |
| `test_experiment_protocol.py::test_repeat_until_remains_explicit_and_policy_can_evolve` (L662) | 기본 정책이 `unsupported_repeat_until` 을 올린다 | 올리지 않는다. 이 테스트의 뒷부분은 이미 "repeat-until-capable 정책이면 안전 게이트만 남는다"를 주장한다 |
| `test_pdf_to_session_walkthrough.py::test_readiness_is_reached_and_names_its_blockers` (L140) | 이유 집합이 정확히 3종 | 2종 |
| `test_pdf_to_session_walkthrough.py::test_resolving_the_ambiguities_narrows_the_wall` (L315) | 모호성·안전을 다 해결해도 `_readiness_gates_cleared` 는 거짓이고 `activate_development` 는 거절한다 | 참이 되고 활성화된다. **가장 무게가 큰 항목: in-gel 앞의 실행 벽이 사라진다** |
| `test_protocol_claim_analysis.py::test_claims_merge_then_assemble_with_exact_final_provenance_and_blockers` (L1378) | 합성 draft 이유에 `unsupported_repeat_until` 포함 | 미포함 |
| `test_curated_protocol_cascade.py::test_completion_criteria_quotes_explicit_source_result_without_mutation` (L3500) | 7단계 완료조건 답변에 "반복 종료 확인 방식은 아직 검토가 필요합니다" 포함 | 그 문구가 사라진다. 선언을 받아들이면 이 문장을 **새 사실**("이 단계는 관찰 결과가 충족될 때까지 반복하며 완료 판단은 사용자 관찰에 달려 있다")로 바꿔야 한다 — 반복 anchor 에서 파생, readiness 코드에서가 아니라 |

`tests/development_activation.py` 의 docstring 도 같은 전제를 서술한다
("두 블로킹 이유가 `unsupported_repeat_until` 이고, 어떤 검토자 조작도 지원되지
않는 능력을 해제하지 못한다"). 테스트는 아니지만 함께 갱신 대상이다.

**필요한 결정** — 위 5개(+docstring 1)의 전제를 선언에 맞춰 갱신해도 되는가.
승인하면 선언 + 5개 갱신 + 완료조건 문장 교체를 한 커밋으로 올린다.
거절하면 게이트는 지금처럼 `unsupported_repeat_until` 위에 얹힌 채 남고, in-gel
은 계속 선택 불가다(작업 1·2 는 그대로 유효하다).

### 3-3. 남은 readiness 이유와 선택 가능성

선언 **전**(현재):

| 이유 | 단계 | 검토자 해결 가능 |
| --- | --- | --- |
| `unresolved_ambiguity` | 20 | 예 |
| `no_declared_safety_warnings` | — | 예 |
| `unsupported_repeat_until` | 7 | **아니오** |
| `unsupported_repeat_until` | 9 | **아니오** |
| `unsupported_repeat_until` | 20 | **아니오** |

선언 **후**: 위 두 줄만 남고 둘 다 `ProtocolCatalog._BLOCKER_RESOLUTION` 에 있다.

> **in-gel 이 선택 가능해졌는가 — 아니다. 선언을 되돌렸으므로 현재도 선택
> 불가다. 선언하면 남는 이유 2개가 모두 검토자 해결 가능이므로 리뷰어 해결로
> 선택 가능해진다(코드에서 확인).**

### 3-4. `profile_id`

`p1-conservative` 를 바꾸지 않았다. 캐시된 claim payload 안에 들어 있고
`experiment_protocol_analysis.py:1290` 이 그 값과 대조하므로, 이름을 바꾸면
캐시 청크 전부가 무효화되어 provider 호출을 다시 써야 한다. 사용자의 "캐시·계약
상수 불변" 제약과도 일치한다. 선언을 승인할 경우 이 사실을 코드 주석에 남긴다.

---

## 작업 4 — 사용자 절차 1장 · **완료**

`docs/OPERATOR_PROCEDURE_REPEAT_STEP_GATE.md` (127행).
서버 기동 → 검토자 해결 클릭(`안전 경고 확인 처리`, `이 모호성을 해소`,
`데모·연구용 초안 활성화`) → 연구자 선택 → 음성 진행 → 7단계 게이트 →
관찰 발화 → 전진. 각 지점에 "이때 보여야 하는 것"과 **코드에서 측정한 실제
발화문**을 넣었다. 실패 시 확인 3가지: ① 드롭다운에 없음 = readiness 이유 잔존,
② "네"인데 이동 안 함 = 되묻기 1턴 유효성 / 실험 기록 미개방,
③ 원문 대신 일반 문장으로 되묻음 = 그 단계 반복 construct 미포착.
in-gel 이 현재 선택 불가라는 사실을 문서 맨 앞에 전제로 명시했다.

---

## 검증

```
python -m pytest -q            → 1508 passed, 1272 subtests passed (기준 1489 + 신규 20, 하락 없음)
python scripts/replay_turns.py → 실행 완료
python -m compileall -q src tests scripts → ok
git diff --check               → clean
기존 테스트 수정               → 0건 (git diff --name-only -- tests/ 비어 있음)
```

**provider 호출: 0회.** `--execute` 플래그가 든 명령을 실행하지 않았다.
`data/development_cache/chunk_analysis` 최신 엔트리 mtime 은 2026-09-06 13:09 로
이번 작업 중 새로 쓰인 항목이 없다. 이 셸의 `PROTOCOL_ANALYSIS_MODEL` 은 미설정.

`CLAIM_SCHEMA_VERSION`, `EVIDENCE_SEGMENT_VERSION`, 시스템 프롬프트,
`capability_policy` 의 `profile_id` 모두 불변.

---

## 발견했지만 고치지 않음 (목록만)

1. **완료조건 답변이 반복 단계임을 알리는 근거가 readiness 코드다.**
   `COMPLETION_CRITERIA` 분기가 `blocker is UNSUPPORTED_REPEAT_UNTIL` 로
   "반복 종료 확인 방식은 아직 검토가 필요합니다"를 붙인다. 작업 3을 승인하면
   반복 anchor 파생으로 바꿔야 한다(위 3-2 마지막 줄).
2. **다음 단계 미리보기의 blocker 문구도 같은 코드에 의존한다**
   (`curated_protocol.py` 의 `NEXT_INFORMATION` 분기). 같은 이유로 선언 후
   7·9단계에서 안내 문장이 사라진다.
3. **인용문에 단계 라벨이 붙어 나온다.** 7단계 종점 인용이
   `“7 Repeat steps 2-7 until …”` 로 시작한다. 분할 산물이며, 앞머리 숫자를
   떼는 규칙은 문서별 정리 규칙이 되므로 원문 그대로 두었다. 낭독 가독성 문제.
4. **`acknowledge_unread_page` 는 여전히 프로덕션 호출자가 없다.** 테스트만
   호출한다. (이전 단계에서도 보고된 항목.)
5. **`SourceLineage` 를 넘기는 프로덕션 호출자가 없다.** (이전 단계 보고 유지.)
6. **조각 융합(section heading/badge 가 step 본문에 흡수)** — 버전 상향과
   재수집 5회가 필요하다. (STEP 40 보고 유지.)
7. **in-gel 반복 2/3 미포착** — `source_states_an_uncaptured_repetition` 이
   문서를 실행에서 막고 있다. (STEP 39–40 보고 유지.)
8. **PDF 업로드가 확인 없이 provider 호출을 쓴다** — 원칙 11 후보. (유지.)
9. **타이머 시작과 실험 시작/종료가 도구를 우회한다.** (유지.)
10. **실험 종료 기록 게이트와 이탈 기록 도구 경로: 미측정.** (유지.)
