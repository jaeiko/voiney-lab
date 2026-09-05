# STEP 26 — 검토자 경로 개통 · 청크 재분할 · in-gel 4/5

작성: 2026-09-05 · 브랜치 `feature/readiness-safety-warning-gate`
`.env` 값은 출력하지 않았다.

---

## 맨 앞 세 줄

**1) 사람이 UI 만으로 게이트를 처리할 수 있게 됐다.** in-gel 의 게이트 4개 중
사람이 풀 수 있는 **2개를 실제로 처리했다** — 라이브 서버에서, UI 가 쓰는 그
라우트로, actor·역할·시각이 append-only 원장에 남는 것까지 확인했다.
남은 2개(`unsupported_repeat_until`)는 사람이 풀 수 없는 종류이고,
그래서 in-gel 은 끝까지 열리지 않는다(올바른 동작, §3-5).

**2) in-gel 은 닫히지 않았다. 5청크 중 4개 통과.** chunk 3(8쪽)만 남았고
두 번 모두 `chunk_identity_mismatch`(envelope) 로 거부됐다. 원인은 **우리 쪽
결함**이었다: 응답이 그대로 되돌려 줘야 하는 24자 `request_handle` 을 스키마가
`{"type":"string"}` 으로만 선언해 두어 강제하지 않았다. 0회로 `const` 고정했다.
병합·채점은 실행하지 않았다.

**3) provider 호출 6회 / 승인 9회.** 작업 4 배분 6회를 모두 썼고, 작업 4가
성공하지 못했으므로 작업 5의 3회는 **쓰지 않았다**(4-8·5 전제).

---

## 작업 0 — 사용자 UI 실사용 결과 (참고)

크래시 수정이 실사용에서 검증됐다는 사실을 기록한다. 회귀 테스트는 유지되고
있다: `tests/test_pdfium_serialization.py`(7건, 프로세스 경계와 락 계측),
`tests/test_pdf_worker_isolation.py`(11건, 워커를 SIGSEGV·SIGABRT·hang·잘린 응답으로
죽여도 서버가 살아남고 결정적 오류가 나오는지). 이번 STEP 에서 두 파일 모두 통과.

---

## 작업 1 — 차단 사유 표시 (0회)

### 1-1 두 표시가 왜 달랐는가 — 서로 다른 코드 경로다

| 화면 문구 | 출처 |
|---|---|
| "개발 활성화 기록 없음 · 실행 불가" (in-gel) | `catalogStatus()` 가 `entry.execution_blocked_reason` 을 보고 붙인 라벨. 그 필드는 STEP 23 이 넣은 것으로 **"이 fixture 가 왜 실행 가능하지 않은가"**를 답하고, `_candidate_fixture_execution_state()` 의 기본값이 `development_activation_not_recorded` 였다 |
| "분석 또는 안전 검토 차단 · 조치 필요" (ANKOM) | 같은 함수의 다른 분기, `entry.lifecycle_state === "blocked"`. ANKOM 은 분석이 아예 없어 `execution_blocked_reason` 이 없다 |

즉 한쪽은 "실행 권위" 축, 다른 한쪽은 "생애주기" 축이고, 둘 중 어느 것도
readiness 사유를 세지 않는다. 그래서 사유 4개 중 0개가 화면에 있었다.

### 1-2 남은 사유를 전부 표시한다 — 구현·실측

`review()` 에 `outstanding_blockers` 를 추가했다. 라이브 실측:

```
unresolved_ambiguity           settled: True   reviewer_can_clear   p.8
no_declared_safety_warnings    settled: True   reviewer_can_clear
unsupported_repeat_until       settled: False  capability_required  p.5
unsupported_repeat_until       settled: False  capability_required  p.6
```

각 항목에 원문 근거(`source_excerpt`, `source_page_number`, `step_id`)가 실려
있어 검토자가 무엇을 보고 판단하는지가 화면에 있다.

### 1-3 사람이 풀 수 있는가 / 새 능력이 필요한가 — `kind` 로 구분

- `reviewer_can_clear` + `reviewer_action` (`acknowledge_gate` /
  `resolve_ambiguity` / `confirm_repetition`) — 검토자를 부르면 된다
- `capability_required` — 아무도 못 푼다. 실험자는 기다려야 한다

이 구분이 없으면 "검토자를 부를지 릴리스를 기다릴지"를 화면에서 알 수 없다.
분류는 `_BLOCKER_RESOLUTION` 표 하나이고, 표에 없는 사유는 전부
`capability_required` 다 — 즉 **모르는 사유를 사람이 풀 수 있다고 말하지 않는다**
(fail closed).

**부수 수정 1**: `already_acknowledged` 가 acknowledgement 원장만 보고 있어서,
모호성을 해소한 뒤에도 `unresolved_ambiguity` 가 "미처리"로 표시됐다. 사유를
실제로 해제하는 그 검사(`_every_ambiguity_resolved`,
`_every_fixed_repetition_confirmed`)에 물어보도록 고쳤다. 위 실측의
`settled: True` 두 줄이 그 결과다.

**부수 수정 2**: 목록 화면의 `execution_blocked_reason` 이 게이트가 안 열린
상태에서도 `development_activation_not_recorded` 라고 말했다. readiness 를
먼저 보고 `readiness_gates_blocked` 를 반환하도록 고쳤다. 라이브 실측:
`reason: readiness_gates_blocked`. 목록 항목에도 `outstanding_blockers` 를 실었다.

### 1-4 `reviewer_actions` 를 실제 가능한 것과 일치시켰다

STEP 25 측정: `["review_hazards","approve","reject"]` 중 UI 가 할 수 있는 것은
0개였다. 이제 **남아 있는 사유에서 파생**된다. 라이브 실측:
처리 전 `['acknowledge_gate','resolve_ambiguity']` → 두 건 처리 후
`['revoke_finding']`. 즉 목록이 화면의 버튼과 일치한다.

---

## 작업 2 — 검토자 경로를 HTTP 로 열었다 (0회)

### 2-1 네 조작의 라우트

```
POST /api/protocols/{id}/revisions/{rev}/findings/acknowledge-gate
POST /api/protocols/{id}/revisions/{rev}/findings/confirm-repetition
POST /api/protocols/{id}/revisions/{rev}/findings/revoke-repetition
POST /api/protocols/{id}/revisions/{rev}/findings/resolve-ambiguity
```

응답은 "ok" 가 아니라 **지금 무엇이 남았는지**다:
`readiness_gates_cleared`, `available_for_execution`, `outstanding_blockers`,
`reviewer_findings`.

### 2-2 권한 배선 — 이 작업의 최대 위험, 그래서 테스트가 가장 두껍다

`_reviewer_finding_actor()` 하나가 네 라우트의 신원을 정한다.

| 상황 | 요구 | 결과 |
|---|---|---|
| workspace 활성 | principal 필수 + `Permission.PROTOCOL_REVIEW` + 검토 역할(`reviewer`/`lab_admin`/`organization_admin`) | 없으면 401/403 |
| workspace 비활성 | 호스트가 이미 해석한 operator 신원 필수 + 검토 역할 | 없으면 401/403 |

**개발 활성화와 다르게 취급한다**: activation 은 단일 운용자 호스트에서 주체
미기록을 허용하지만, finding 은 **이 시스템이 요구하는 사람의 판단 그 자체**여서
주체 없이는 기록하지 않는다. 호스트가 이름을 못 대면 `"local"` 을 지어내지 않고
fail closed 한다.

**dev_profile 만으로 통과하는 경로가 없음**을 확인했다: 네 라우트 전부
`_REQUEST_PRINCIPAL` 에서 신원을 얻고, 역할이 없으면 거부한다. 라이브 실측에서
기록된 actor 는 `dev-local-admin` / `lab_admin` 이었다 — dev 프로필이 principal
로 해석된 결과이며, 역할 검사를 **통과해서** 들어온 것이다.

**STEP 23 이 막은 종류의 구멍(검사에 도달하지 않는 경로)** 을 테스트로 고정했다:
`test_a_workspace_principal_without_the_permission_is_refused` 는
`require_permission` 을 감시해 **실제로 호출되었고 인자가
`Permission.PROTOCOL_REVIEW` 였음**을 단언한다. 존재만으로는 부족하다는 것이
STEP 23 의 교훈이다.

### 2-3 인용 없는 확인은 라우트 수준에서도 거부된다

`_finding_segment_ids()` 는 잘못된 인용을 **수리하지 않는다** — 빈 목록으로
넘기고 카탈로그가 거부한다. 서버가 검토자를 대신해 근거를 대는 것이 이 인용이
막으려는 바로 그것이기 때문이다. 테스트: 인용 없음 / `["a", 7]` / `"seg-a"` /
`{"seg":"a"}` / `[]` 전부 거부.

라이브에서도 확인: 인용 없는 `resolve-ambiguity` → **HTTP 403
`protocol_approval_denied`**.

### 2-4 / 2-5 원장 기록과 철회 — 라우트를 통해 확인

라이브 원장 실측(2건, 이번 STEP 에 추가된 이벤트 전부):

```
protocol_readiness_gate_acknowledged  acknowledged                       dev-local-admin lab_admin 2026-09-05T16:06:11.605151+00:00
protocol_ambiguity_resolved           single_statement_is_authoritative  dev-local-admin lab_admin 2026-09-05T16:09:08.558934+00:00
```

철회는 라우트 테스트로 고정했다: 없는 확인의 철회는 거부, 철회하면 게이트가 다시
닫힌다(STEP 25 §6-3 에서 라이브러리 계층으로 이미 실측, 이번에 라우트로 재확인).

### 기존 검증된 동작에 대한 위험

**가장 큰 위험은 권한 배선이다.** 완화한 방법: (1) 네 라우트가 신원 함수 **하나**를
공유하므로 분기별로 다르게 틀릴 수 없다, (2) 그 함수가 역할 없는 principal 도
거부한다, (3) `require_permission` 이 도달하는지를 감시로 단언한다,
(4) 인용을 서버가 채우지 않는다. **남는 위험**: 이 라우트는 안전 게이트를 여는
표면이고, 배선이 조용히 약해지면 사람 없이 게이트가 열린다. 그래서 신원 함수의
거부 경로를 4개 라우트 × 3개 신원 상황으로 전수 테스트했다(12 subtests).

---

## 작업 3 — UI 검토자 화면 (0회)

### 3-1 사람이 할 수 있는 것

- **차단 사유 목록과 원문 근거**: `#protocol-blockers` 에 사유별로 코드 / 분류 /
  페이지 / 원문 발췌
- **안전 경고 확인**: 버튼 하나
- **고정 반복 확인**: 개수 입력 + **근거 세그먼트 체크박스** — 인용 affordance 다.
  구성에 세그먼트가 없으면 서버가 계산한 그 페이지의 후보 목록을 제시한다
- **모호성 해소**: **선택 목록 + 근거 세그먼트 체크박스**
- **철회**: 반복 확인마다 철회 버튼

**여기서 지시가 성립하지 않은 지점 두 개를 만났고, 둘 다 고쳤다.**

1. **인용할 것이 없었다.** 큐레이션 fixture 의 construct 는 `evidence_segment_ids`
   가 전부 빈 배열이다(STEP 24 측정: fixture 에 `seg-` 0회). 그래서 체크박스에
   후보가 0개이고 어떤 해소도 불가능했다. → `outstanding_blockers[].citable_segments`
   를 추가해 **사유가 가리키는 페이지의 세그먼트를 서버가 자기 원문 바이트에서
   계산**해 제시한다. 라이브 실측: p.8 후보 **8개**.
2. **자유 입력이 아니라 선택이었다.** `resolve_ambiguity` 의 `decision` 은
   `{single_statement_is_authoritative, statements_are_distinct}` 두 값만
   받는다(측정: 자유 문장은 `protocol_approval_denied`). 그리고 **둘 중 하나만
   사유를 해제한다.** → `decision_options` 와 `clearing_decision` 을 payload 에
   실어 UI 가 어휘를 하드코딩하지 않고 선택 목록으로 보여주며, 어느 선택이
   해제하는지도 라벨에 쓴다.

### 3-2 게이트가 남아 있는 동안 실행 승인 불가 — 실측

```
readiness_gates_cleared        : False
available_for_execution        : False
development_activation_allowed : False
POST activate-development      -> HTTP 503
드롭다운 항목                   -> available_for_execution=False, reason=readiness_gates_blocked
```

STEP 23 이 고정한 동작을 약화시키지 않았다. 관련 테스트 전부 통과.

### 3-3 누가 언제 처리했는지 — `#protocol-findings` 에 표시

`reviewer_findings` 를 종류 / 결정 / 대상 / 횟수 / actor / 역할 / 시각으로 렌더한다.

### 3-4 실제로 해봤다 (라이브)

서버를 배경 실행하고 UI 가 쓰는 그 라우트로 두 건을 처리했다. 결과는 §2-4 의
원장 2건과 §3-2 의 실측. 서버는 종료했다(`port 8000 closed`).

### 3-5 어디까지 처리됐고 무엇이 남았는가

| 사유 | 분류 | 상태 |
|---|---|---|
| `no_declared_safety_warnings` | reviewer_can_clear | **처리됨** |
| `unresolved_ambiguity` (p.8) | reviewer_can_clear | **처리됨** |
| `unsupported_repeat_until` (p.5) | capability_required | 남음 — 사람이 풀 수 없다 |
| `unsupported_repeat_until` (p.6) | capability_required | 남음 — 사람이 풀 수 없다 |

**사람이 풀 수 있는 것은 전부 풀렸고, in-gel 은 그래도 열리지 않는다.** 지시대로
그것이 올바른 동작이므로 그대로 두었다. repeat-until 지원 범위는 STEP 25 §0-2 에 있다.

### 3-6 사용자가 브라우저에서 따라 할 절차

1. `bash scripts/run_candidate_a.sh` → `http://<host>:8000` 접속
2. **프로토콜 선택** 드롭다운에서 `In-gel digestion protocol for protein
   identification` 을 고른다. 항목은 비활성이고 라벨이
   **"준비 게이트 차단 · 실행 불가"** 로 보인다 — 이제 "개발 활성화 기록 없음"이
   아니다.
3. **"업로드 분석 검토 · 원문 근거와 실행 준비 상태"** 패널을 펼친다.
   비어 보이면 항목을 선택하지 않은 상태다(작업 0 참고).
4. 패널 안 **"남은 차단 사유 4건"** 섹션을 본다. 각 줄에 사유 코드,
   `검토자가 해제 가능` / `새 능력 필요 · 사람이 해제 불가`, 페이지, 원문 발췌.
5. **안전 경고**: `no_declared_safety_warnings` 줄의
   **[안전 경고 확인 처리]** 버튼을 누른다.
   → 그 줄이 `· 확인됨` 으로 바뀌고, **"기록된 검토"** 섹션에
   `게이트 확인 · <내 계정> (lab_admin) · <시각>` 이 나타난다.
6. **모호성**: `unresolved_ambiguity` 줄에서
   (a) 선택 목록에서 **`single_statement_is_authoritative · 이 판단이 사유를
   해제합니다`** 를 고르고,
   (b) 아래 **근거 세그먼트 체크박스**에서 판단의 근거가 된 원문 조각을
   하나 이상 고른다(p.8 은 8개 제시된다),
   (c) **[이 모호성을 해소]** 를 누른다.
   → 그 줄이 `· 확인됨` 으로 바뀌고 원장에 한 건 더 쌓인다.
   근거를 고르지 않으면 서버가 거부하고 패널에 `protocol_approval_denied` 가
   표시된다 — 정상 동작이다.
7. **고정 반복이 있는 프로토콜**이라면 `unconfirmed_fixed_repetition` 줄에서
   반복마다 **개수를 입력**하고 **근거 세그먼트를 선택**한 뒤
   **[이 반복 횟수를 확인]** 을 누른다. 되돌리려면 **[확인 철회]**.
   (in-gel 에는 고정 반복이 없어 이 섹션이 나타나지 않는다.)
8. **정상일 때 보여야 할 것**: 처리한 줄은 `확인됨`, "기록된 검토" 건수가 늘고,
   **`새 능력 필요` 줄이 하나라도 남아 있으면 실행은 계속 불가**다.
   in-gel 은 그 줄이 2개라서 끝까지 잠긴 채 남는다. 이것이 올바른 화면이다.

---

## 작업 4 — chunk 1 재분할 (6회)

### 4-1 캐시가 유효한가 — **유효하지 않다. 지시대로 재검토했다.**

`chunk_id` 는 `planner_configuration_sha256`(= limits 전체의 해시)을 재료에
포함한다. 그래서 **어떤 limit 하나를 바꾸면 모든 청크의 id 가 바뀌고**, 페이지
구성이 그대로인 chunk 0 조차 캐시 키가 달라진다. 실측:

```
현재            ord0 core=[1,2,3] key=2e580b63…
pages 3         ord0 core=[1,2,3] key=227b050d…   ← 페이지 같은데 키가 다름
bytes 2500      ord0 core=[1]     key=ea90bf46…
```

**즉 chunk 1 만 쪼개는 것은 불가능하고, 쪼개면 chunk 0·2 도 다시 지불해야 한다.**
그 사실 위에서 진행했다(재검토 결과: 그래도 쪼개는 편이 낫다 — §4-2).

### 4-2 경계를 어디에 두었는가 — 근거는 "청크가 지는 의무"다

측정이 기존 planner 의 근거를 반박했다. 코드 주석은 source bytes 를 claim
cardinality 의 프록시라고 말하는데:

| 문서 | 청크 | core bytes | **번호 라벨 수** | 결과 |
|---|---|---|---|---|
| in-gel | ord0 pp.1–3 | 3398 | **2** | 3회 시도 중 통과 |
| in-gel | ord1 pp.4–8 | 4007 | **22** | **3회 시도 3회 거부** |
| in-gel | ord2 p.9 | 388 | 1 | 통과 |
| headspace | 최악 청크 | 3688 | **25** | 미실행 |
| ANKOM | 최악 청크 | 3457 | 13 | 미실행 |

바이트는 거의 같고(3398 vs 4007) 라벨은 11배 다르다. **완결성 불변식이 청크에
청구하는 것은 라벨당 action claim 이므로, 라벨 수가 실제 의무다.**

그래서 `ChunkAnalysisLimits.max_core_labels_per_chunk = 12` 를 추가했다.
서버가 자기 페이지 텍스트에서 세고, 의미를 읽지 않으며, 특정 문서를 편애하지
않는다. **상한이 아니라 목표**다 — provenance 가 페이지 단위여서 페이지는
쪼갤 수 없고, in-gel 7쪽은 혼자 9개를 진다.

4개 문서 전부 측정(표준 지시):

| 문서 | 청크 수 | 청크당 최대 라벨 |
|---|---|---|
| in-gel | 3 → **5** | 22 → **9** |
| headspace | 5 → **8** | 25 → **11** |
| ANKOM | 8 → **8** (불변) | 13 → **12** |
| intracellular | 거부 → 거부 (불변, cross-check 실패) | — |

ANKOM 은 청크 수가 그대로다 — 이 상한은 부담이 실제로 몰린 곳만 자른다.

새 in-gel 계획: `[1,2,3]`=2, `[4,5,6]`=9, `[7]`=9, `[8]`=4, `[9]`=1.

### 4-3 거부 메시지에 문제 세그먼트를 담았다 — 호출 전에 먼저 구현

`ProtocolEvidenceDiagnostic.offending_segment_ids` 를 추가하고
`declined_segment_states_a_value` / `declined_segment_not_on_page` 가 채우도록
했다. 세그먼트 id 는 서버가 자기 원문 바이트에서 계산한 **신원**이므로 제약 12
위반이 아니다. 하네스는 그 위에 서버 계산 형태 정보(인덱스 / 길이 /
단계 내부 여부 / 값 보유 여부 / 그 페이지의 라벨)를 붙여 출력한다 — STEP 25 가
손으로 재도출해야 했던 정보다.

### 4-4 결과 — 5청크 중 4개 통과, 6회 소진

| # | 청크 | core | 결과 | 사유 | latency | claims |
|---|---|---|---|---|---|---|
| 1 | ord0 | 1,2,3 | **passed** | — | 18.9 s | 24 |
| 2 | ord4 | 9 | **passed** | — | 9.7 s | 8 |
| 3 | ord1 | 4,5,6 | **passed** | — | 20.5 s | 28 (라벨 3–11) |
| 4 | ord2 | 7 | **passed** | — | 17.0 s | 24 (라벨 12–20) |
| 5 | ord3 | 8 | rejected | `chunk_identity_mismatch` (envelope) | 8.0 s | 14 |
| 6 | ord3 | 8 | rejected | `chunk_identity_mismatch` / **`request_handle_mismatch`** | 7.8 s | 15 |

**STEP 25 의 두 실패는 사라졌다**: `declined_segment_states_a_value` 0회,
`repetition_count_missing` 0회. pp.4–6 청크는 `declined_segments: 0` 으로 통과했고,
그 안에 STEP 25 에서 문제였던 p.6 `Note … 50uL` 이 들어 있다.

### 남은 실패의 원인 — **우리 쪽 결함이었다 (0회로 특정·수정)**

응답은 `request_handle` 을 그대로 되돌려 줘야 하고 서버가 그것을 대조한다.
그런데 스키마는 `{"type": "string"}` 이었다 — **24자 엔트로피를 손으로
전사하라고 요구하면서 무엇을 써야 하는지 말하지 않았다.** 같은 문서의 다른 네
청크는 맞게 옮겼고 ord3 만 두 번 틀렸다. 우리가 만든 주사위다.

`claim_response_schema()` 가 `request_handle` 과 `capability_policy_id` 를
요청별 `const` 로 고정하도록 고쳤다. `strict: true` 아래에서는 맞는 값만 말할 수
있게 된다. 서버 대조는 그대로 남긴다 — 스키마는 provider 의 제약이고 대조는
우리 것이며, 어느 쪽도 다른 쪽을 대신하지 않는다.

**캐시는 무효화되지 않는다**(측정): 재검증은 JSON 스키마를 적용하지 않고
`parse_chunk_claim_response` 를 적용하므로, 이미 저장된 4청크는 그대로 유효하다.
`chunk_analysis_cache` 14건 + 신규 3건 통과.

### 4-5 ~ 4-7 — 실행하지 않았다

3청크가 아니라 5청크 중 4개만 모였다. 하네스가 스스로 거부한다:
`walk.attempted = False`, `reason = "4 of 5 chunks validated; merge requires
every chunk"`. 따라서 병합·채점·파일 보존·repeat_until 3건 보고를 **실행하지
않았다.** 단계 수·누락 라벨·순서·유사도·값 일치는 **측정 없음**이다.

**단, 거부된 청크에서 관측된 것만 사실로 적는다**(병합 결과가 아니다):
ord1(pp.4–6)이 `repeat_condition` 로 p.5 「7 Repeat steps 2-7 until the gel band
is fully destained」 1건을 잡았고 `evidence_states_declared_range: true` 였다.
p.6·p.8 의 나머지 두 건은 이번에도 claim 이 없었다 — STEP 25 와 같다.

### 4-8 6회를 다 쓰고 실패 → 멈췄다. 작업 5로 넘어가지 않았다.

**다음 STEP 에 1회만 있으면** ord3 을 채워 5/5 가 되고 병합·채점이 가능하다
(캐시가 4청크를 들고 있고, `const` 고정으로 이 실패 종류가 제거된 상태에서).

---

## 작업 5 — headspace — 실행하지 않음

작업 4가 성공하지 않았으므로 전제가 성립하지 않는다. 배분된 3회를 쓰지 않았다.

---

## 작업 6 — 하단 띠 규칙 — 실행하지 않음

지시대로 세그먼트 분할을 바꾸지 않았다. `EVIDENCE_SEGMENT_VERSION` 은 5 그대로다.

---

## 내 지시 중 성립하지 않은 것

| 지시 | 어디가 왜 |
|---|---|
| 4-1 전제 "chunk 1 의 범위만 바꾸면" | **범위만 바꿀 수 없다.** `chunk_id` 가 limits 전체 해시를 포함해, 어떤 limit 을 바꿔도 모든 청크 id 가 바뀐다. 페이지 구성이 같은 chunk 0 도 키가 달라진다(실측) |
| 3-1 "근거 세그먼트를 선택" | 큐레이션 fixture 의 construct 는 `evidence_segment_ids` 가 전부 비어 있어 **선택할 후보가 0개**였다. 서버가 그 페이지 세그먼트를 계산해 제시하도록 `citable_segments` 를 추가해야 성립했다 |
| 3-1 "모호성을 해소한다" | `decision` 은 자유 입력이 아니라 두 값 중 선택이고(자유 문장은 403), **둘 중 하나만 사유를 해제한다.** 선택 목록과 "어느 것이 해제하는가"를 payload 로 내려야 성립했다 |
| 4-2 "repeat_until 위치와 Note 위치를 보고 경계를 정하라" | 그 위치로 정하는 것은 **문서에 맞춘 경계**가 된다(제약 8). 대신 4개 문서에서 측정 가능한 예측자(청크당 번호 라벨 수)로 정했고, 결과적으로 p.6 의 Note 는 통과했다 |

---

## 기존 검증된 동작을 깨뜨릴 위험

- **작업 2·3 권한 배선**: §2-2 참조. 신원 함수 1개 공유 + 역할 없는 principal 거부
  + `require_permission` 도달 단언 + 인용 무수리. 남는 위험은 이 표면이 안전
  게이트를 연다는 사실 자체이고, 그래서 4 라우트 × 3 신원 상황을 전수 테스트했다.
- **작업 4 planner 변경**: 4개 문서 전부 측정했다. in-gel 5청크 / headspace 8 /
  ANKOM 8(불변) / intracellular 거부(불변). 위험은 **미래 호출 비용 상승**이다:
  headspace 5→8, 즉 5조각 예산이 8조각이 된다. 그 대가로 최악 청크의 의무가
  25→11 로 내려간다. `test_extraction_and_admission` 의 3→5 단언을 갱신했다.
- **작업 4 스키마 `const` 고정**: 캐시 무효화 없음을 측정으로 확인했다.
  위험은 provider 가 `const` 를 지원하지 않는 경우인데, 이미 `claim_schema_version`
  과 페이지 번호가 `const` 로 들어가 있고 4청크가 그 아래서 통과했다.
- **PDF 워커 타임아웃**: 전체 스위트를 2코어·가용 180 MB 에서 돌릴 때 30 s 를
  한 번 넘겼다(실측: 1페이지 왕복 0.11–0.41 s, 최대 문서 1.25 s — 즉 정상값의
  70배). 파서가 멈춘 것이 아니라 **호스트가 프로세스를 못 띄운 것**이므로,
  기본값 30 s 는 유지하고 호스트가 올릴 수 있는 환경 변수를 두었다. 범위 밖 값과
  오타는 무시하고 30 s 로 돌아간다(테스트로 고정). 이 STEP 의 스위트는
  `=120` 으로 돌렸고 그 사실을 여기 적는다.
- **런타임 DB**: 3-4 가 실사용 확인을 지시했으므로 **이번 STEP 에 2건이
  추가됐다.** 사전에 `/tmp/step26-db-before.sqlite` 로 스냅샷했다.
  `protocol_events` 17 → 19, 추가된 것은
  `protocol_readiness_gate_acknowledged`, `protocol_ambiguity_resolved` 2건뿐이고
  다른 표는 전부 불변(experiments 2, protocol_revisions 2, analysis_revisions 2,
  pdf_objects 2). append-only 검토 기록이며 삭제·수정은 없다.

---

## 실행한 것 / 실행하지 않은 것

**실행**: 작업 1 전부, 작업 2 전부(라우트 4개 + 테스트 7건/12 subtests),
작업 3 전부(UI + 라이브 2건 처리), 작업 4-1~4-4 및 원인 규명·스키마 수정,
전체 스위트 **1363 passed / 1166 subtests**, `compileall`, `git diff --check`.

**실행하지 않음**: 작업 4-5~4-7(병합·채점·파일 보존·repeat_until 보고),
작업 5 전부, 작업 6 전부.

**측정 없음으로 남긴 것**: in-gel 단계 수·누락 라벨·추가 라벨·순서·유사도 분포·
값 일치/불일치/채점 불가 — 병합이 없어 존재하지 않는다.

---

Provider 호출 횟수: 6
