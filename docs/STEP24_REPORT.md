# STEP 24 — 승인 호출 2회 집행 · 캐시 · 계층 라벨 · 프로세스 분리

작성: 2026-09-05 · 브랜치 `feature/readiness-safety-warning-gate`
런타임 PDF/DB 는 읽기만 했다. `.env` 값은 출력하지 않았다.

---

## 맨 앞 두 줄

**1) 작업 6을 집행했다.** 네 조건 전부 만족했다.
  (a) 캐시 동작·테스트 통과 — 14건 · (b) 청크 단위 집행 가능 — 실측 확인 ·
  (c) 작업 1에서 분할을 **바꾸지 않았다**(§1-5), 그리고 캐시 키에는
  `EVIDENCE_SEGMENT_VERSION` 이 이미 들어 있다 ·
  (d) 작업 3 확장이 in-gel 라벨을 25 → 25 로 유지, 계층 라벨 0건 추가

**2) in-gel 3청크 중 2개 통과.** chunk 0 (pp.1–3) 과 chunk 2 (p.9) 가
`canonical_validation: passed` 로 캐시에 들어갔다. chunk 1 은 예산이 없어
**시도하지 않았다.** 6-4 에 따라 **병합하지 않았고 채점하지 않았다.**

---

## 작업 0 — STEP 23 작업 5 재수행

STEP 23 보고서 파일 `docs/STEP23_SAFETY_AND_CRASH_REPORT.md` **438행에 §5 가
있다.** 누락된 것은 보고서가 아니라 내 채팅 답변이었다. 지시대로 다시
측정했고, 아래 수치는 이번에 재실행한 것이다.

### 0-1 headspace 의 남은 차단 사유

코드에서 결정론적으로 도출되는 것과 분석이 있어야 아는 것을 나눈다.
P1 프로필의 지원 집합은 측정으로 확인했다:
`{fixed_range_repetition, operator_determined_repetition, informational_difference}`
(`experiment_protocol.py:508`). acknowledgeable 게이트는
`{no_declared_safety_warnings, source_text_cross_check_unavailable}` 2개뿐이다.

| 차단 사유 | headspace | 분류 |
|---|---|---|
| `source_text_cross_check_failed/unavailable` | **아니오** — 측정: `verified`, divergent 0, unresolved glyph 0 | — |
| `no_declared_safety_warnings` | **예** — 실행 단계가 있으면 무조건 발생 | **(가)** 사람이 acknowledge |
| `unconfirmed_fixed_repetition` | **예** — 고정 반복 3건 (p.5 「twice more」, p.6 「for the metal plates」, p.12 「twice more (three conditioning rounds in total)」) | **(가)** 리뷰어가 개수 확인 |
| `unsupported_fixed_range_repetition` | 아니오 — P1 지원 | — |
| `unsupported_operator_determined_repetition` | 아니오 — P1 지원 (STEP 20) | — |
| `unsupported_repeat_until` | 아니오 — 측정: 0건 | — |
| `unresolved_ambiguity` | **확인 못 함** — 분석이 있어야 안다 | (가)이면 해결 가능 |
| `missing_execution_critical_value` | **확인 못 함** | **(다)** |
| `no_executable_steps` | **확인 못 함** | **(다)** |

확정적으로 남는 것은 **(가) 2건**뿐이고 **(나) 새 능력은 필요 없다.**

### 0-2 in-gel repeat-until 3건 — 원문 인용 (구현 없음)

fixture 의 constructs 에서 그대로 인용한다.

1. **p.5** 「7 Repeat steps 2-7 until the gel band is fully destained」
   → `repeat_until` / `candidate-a-repeat-steps-02-07` / 단계 02–07
2. **p.6** 「The gel should look white (dehydrated) as seen in the above
   picture. If the band is still transparent then repeat steps 8-9 until fully
   dehydrated」 → `repeat_until` / `candidate-a-repeat-steps-08-09` / 단계 08–09
3. **p.8** 같은 문장 형태, steps 17-18
   → **`source_ambiguity`** / `candidate-a-step-20-repeat-range`

**필요 범위(구현 안 함)**: capability profile 이 `REPEAT_UNTIL` 을 선언 →
`_UNSUPPORTED_REASONS` 경로에서 사유가 생성되지 않게 한다
(**`_ACKNOWLEDGEABLE_GATES` 추가는 안 된다** — "지원한다"가 아니라 "눈감는다").
종료 판정은 사람: 루프 경계에서 멈추고 원문 조건 문장을 읽고 명시적 응답을
받는다(STEP 16 결정). 세션에 `repeat_until_awaiting_decision` /
`provide_repeat_until_decision(satisfied)` / `may_begin_step` 확장, 원장에
반복 1회분 결정마다 actor·role·시각·handles·ordinal + revocation, 조건 문장은
evidence handle 에 묶인 원문. **3번은 이것만으로 안 열린다** — 리뷰어 해결이
따로 필요하다.

### 0-3 intracellular 의 `SOURCE_TEXT_CROSS_CHECK_FAILED`

측정값 (이번에 재실행):

```
text_verification      : mismatch
divergent_page_numbers : (10, 18, 33)
unmapped code point 페이지: [10, 18, 33]      ← divergent 와 정확히 일치
glyph_resolutions      : 5   unresolved: 5
   page 10: no engine resolved an unmapped position (unresolved)
   page 18: no engine resolved an unmapped position (unresolved)
   page 33: (alignment_failed) / (unresolved) / (alignment_failed)
```

**무엇과 무엇을 비교하는가 — 두 층이다.**
(a) `verify_page_text` 가 pypdfium2 추출과 독립 엔진(`pdftotext`, 별도 프로세스)의
**정규화된 문자 census** 를 비교한다. 줄바꿈·상첨자 순서·제어문자·줄끝 하이픈은
정규화로 제거되므로 남는 것은 순서 무관한 문자 구성이다.
(b) 그와 별개로, 문서 자신의 ToUnicode 로도 독립 엔진으로도 **어느 위치의 문자를
확정할 수 없으면** verification 을 `MISMATCH` 로 강등한다(glyph_failures 분기).

**intracellular 를 mismatch 로 만든 것은 (b)다.** divergent 페이지 3개가 unmapped
code point 를 가진 페이지 3개와 정확히 일치한다. census 가 어긋난 게 아니라,
정본 증거로 인용할 텍스트에 **출처를 댈 수 없는 문자**가 남아 거부된 것이다.
STEP 13 의 Class 2 정책이 설계대로 동작한 결과다.

**계층 번호(3.2, 3.5) 미인식과 같은 원인인가 — 아니다.** 그쪽은
`protocol_claim_analysis` 의 라벨 정규식이 단일 정수만 받던 문제이고, 추출이나
cross-check 를 전혀 거치지 않는다. 두 결함은 독립이며, 이번 STEP 작업 3 으로
후자만 해소되었고 전자는 그대로 남아 있다(§3-5 참조).

---

## 작업 1 — 세그먼트 분할의 기하 무시

### 1-1 4개 문서 전 세그먼트 기하 (실행함)

pdfium 의 문자 박스(`get_charbox`)를 읽고, CRLF→LF 정규화의 인덱스 사상을
만들어 프로덕션 경계(`_bounded_action_block_boundaries`)와 정렬해 측정했다.
읽기 전용 측정 스크립트이고 프로덕션 코드는 이 측정을 위해 바꾸지 않았다.

측정 대상 **505 세그먼트 / 99 페이지**. 13 페이지는 새로 읽은 pdfium 텍스트가
우리 추출 텍스트와 달라(글리프 해결이 적용된 페이지) 기하 비교에서 제외했다 —
제외 사실을 수치와 함께 남긴다.

| 문서 | 세그먼트 | y-spread 최대 | y-spread 중앙값 | 조각 간 최대 세로 간격 (최대 / 중앙값) |
|---|---|---|---|---|
| in-gel | 56 | 0.907 | 0.033 | 0.887 / 0.007 |
| headspace | 103 | 0.916 | 0.016 | 0.896 / 0.000 |
| intracellular | 195 | 0.888 | 0.033 | 0.471 / 0.007 |
| ANKOM | 138 | 0.881 | 0.032 | 0.869 / 0.007 |

중앙값은 한 줄~두 줄 높이인데 최대는 페이지 전체에 걸친다. 즉 문제는 전반적이지
않고 **소수의 세그먼트에 몰려 있다.**

### 1-2 세로로 크게 벌어진 세그먼트 (실행함)

| 임계 | 해당 세그먼트 | 그중 값(수치+단위) 보유 |
|---|---|---|
| 최대 간격 > 0.05 | 49 | **11** |
| > 0.10 | 31 | **7** |
| > 0.20 | 26 | **7** |
| > 0.30 | 22 | **7** |

지배적 패턴은 **꼬리말**이다. 사용자가 지적한 사례가 그대로 재현되었다:

```
headspace p.6  간격 0.458  y=0.023-0.501  값 있음
  '1h 30m protocols.io | https://dx.doi.org/... December 5, 2024 6/16'
headspace p.8  간격 0.896  y=0.023-0.939  값 있음   '1h 30m ... 8/16'
headspace p.12 간격 0.887                 값 있음   '2h ... 12/16'
in-gel    p.7  간격 0.346                 값 있음   '1h 45m 10m 15m ... 7/9'
ANKOM     p.13 간격 0.518                 값 있음   '20 g Na2SO3 4.0 mL alpha-amylase ... 13/40'
```

### 1-3 2단 조판·표 칼럼 혼입 (실행함) — **없다**

- 읽기 순서가 페이지를 0.25 이상 **거슬러 올라가는** 지점: 4개 문서 합쳐
  **12 페이지, 각 페이지에 정확히 1회.** 진짜 2단 조판이면 모든 페이지에서
  여러 번 나온다. 이 1회는 꼬리말 다음 블록으로의 복귀다.
- 세그먼트의 가로 범위에 0.20 페이지폭 이상의 빈 띠가 있는 경우 84건. 확인해
  보면 대부분 `NAME / BRAND / SKU` 형태의 **한 줄 안 우측 정렬 항목**이거나
  좌측 타이머 배지 + 꼬리말이며, 좌우 칼럼이 섞인 것이 아니다.

**결론: 칼럼·셀 혼입 사례는 4개 문서에서 발견되지 않았다.** 따라서 지시된
판정 근거 중 "칼럼 경계"는 적용 대상이 없다.

### 1-4 이주 영향 (실행함) — **정확히 10개**

| 인용처 | 세그먼트 id 인용 수 |
|---|---|
| `candidate_a_curated_analysis.timers.json` | **10** (candidate 10건, 각 1개) |
| `candidate_a_curated_analysis.visuals.json` | **0** — `object_name`(`/X99`) + `normalized_bounding_box` + `source_region_hash` 를 쓴다 |
| `candidate_a_curated_analysis.json` (fixture) | **0** — `seg-` 문자열 0회 |
| 저장된 분석 payload (`analysis_payloads`) | **0** — `seg-` 0회 |
| 검토자 원장 (`protocol_events`) | **0** — 이 store 에 기록된 리뷰어 findings 가 없다 |

이주 비용은 `timers.json` 10건 재도출뿐이고, `scripts/derive_timer_manifest.py`
가 이미 그 일을 한다.

### 1-5 판단 — **이번에는 바꾸지 않는다.** 그리고 지시된 기준은 **지시 불가**다.

**(가) 지시된 기준(세로 간격 임계)은 4개 문서에서 성립하지 않는다.**
`max(3 × 페이지 줄 간격 중앙값, 0.05)` 로 재보니 in-gel 에서 **실제 지시문 5개를
자른다**:

```
p.3 간격 0.585  '2 Prepare two wash solutions: Solution A: 2 parts of 25mM ...'   ← 실제 2단계
p.5 간격 0.375  'Expected result It is really important that the gel bands ...'
p.6 간격 0.387  'Expected result ... repeat steps 8-9 until fully dehydrated'      ← repeat_until 증거
p.8 간격 0.387  'Expected result ... repeat steps 17-18 until fully dehydrated'    ← ambiguity 증거
p.8 간격 0.097  '24 Quickly spin down the digest ... formic acid (FA, 10% v/v) ...' ← 값 보유 실제 단계
```

이유는 구조적이다: 이 protocols.io 내보내기는 그림을 단계 텍스트 흐름 **안에**
넣기 때문에, 진짜 지시문이 정당하게 큰 세로 간격을 갖는다. 세로 거리만으로는
"꼬리말"과 "그림을 감싸 흐르는 지시문"을 구분할 수 없다.
→ 표준 지시대로 **"지시 불가"로 보고하고 구현하지 않았다.**

**(나) 대신 성립하는 기하 기준을 찾아 측정했다: 하단 띠(bottom band).**
STEP 22 §14c 가 이미 4개 문서 전 페이지에서 꼬리말 줄이 정규화 y 0.023–0.026,
세로 산포 0.0000 에 있고 "하단 8% 에는 꼬리말만 있다"를 반증까지 확인해 두었다.
그 기준으로 다시 재보니:

| 문서 | 하단 띠 규칙이 자를 세그먼트 | 그중 값 보유 | 아래쪽 조각 크기 |
|---|---|---|---|
| in-gel | 6 | 3 | 83 자 (전부 동일) |
| headspace | 6 | 4 | 87–88 자 |
| intracellular | 8 | 1 | 86–87 자 |
| ANKOM | 11 | 1 | 78–81 자 |

**in-gel 에서 자르는 6건 전부가 "내용 + 꼬리말"이고, 내용은 온전히 남는다**:
p.3 은 지시문 246자를 유지하고 꼬리말 83자만 떼며, p.8 은 지시문 153자를
유지한다. p.6/p.8 의 `Expected result ... repeat steps` 세그먼트는 하단 띠에
닿지 않아 **아예 건드리지 않는다.** 즉 (가)가 망가뜨리던 것을 (나)는 보존한다.
모든 문서에서 떼어지는 조각이 78–88자로 일정하다는 것이 "이건 꼬리말이다"의
기하적 증거다.

**그런데도 이번에 구현하지 않는다. 근거:**

1. 승인 잔량 2회는 **in-gel 을 닫는 유일한 수단**이고, in-gel 은 정답지가 있는
   유일한 문서다. 현재 분할에서 in-gel 청크는 **통과한 실측 이력이 있다**
   (STEP 19 chunk 1, 그리고 이번 STEP 의 chunk 0·chunk 2).
2. 새 분할에서의 in-gel 은 provider 실행 이력이 **없다.** 마지막 2회를 검증되지
   않은 구성에 쓰는 것은 추측이고, 실패하면 "분할 때문인지 모델 때문인지"를
   귀속할 수 없다. 실제로 이번 2회는 현재 분할에서 둘 다 통과했다.
3. 하단 띠 규칙은 **기하 정보를 추출 계약에 추가**해야 한다 — 워커가 문자 박스
   또는 페이지별 "하단 띠 시작 오프셋"을 함께 돌려주고,
   `ProtocolPdfPage` 와 `_bounded_action_block_boundaries` 가 그것을 받아야 한다.
   같은 STEP 에서 프로세스 경계(작업 4)까지 바꾼 직후에 추출 계약을 또 바꾸면,
   §4-4 의 바이트 동일성 검증이 무엇에 대한 검증인지 흐려진다.

**언제 해야 하는가 (지시대로 명시):** 작업 6 직후, **in-gel 3청크가 닫히거나
그 실패 사유가 확정된 다음 STEP 의 첫 작업.** 그때 `EVIDENCE_SEGMENT_VERSION`
5→6, `timers.json` 10건 재도출, 매니페스트 재검증을 한 묶음으로 한다.

**사용자가 뒤집을 수 있도록 대가를 명시한다**: 분할을 바꾸는 순간 캐시 키의
`evidence_segment_version` 이 달라져 **이번에 캐시에 넣은 in-gel 2청크는
무효가 되고 다시 지불해야 한다**(테스트로 고정된 동작 —
`test_a_changed_segmentation_version_invalidates_every_entry`). 즉
"지금 바꾸면 이번 2회를 버린다 / 나중에 바꾸면 그때 2회를 다시 낸다"의 선택이고,
나는 **측정된 통과 이력을 지키는 쪽**을 골랐다.

---

## 작업 2 — 호출 간 청크 캐시

### 실행한 것

새 모듈 `src/voice_workflow_agent/chunk_analysis_cache.py`, 테스트 14건
(`tests/test_chunk_analysis_cache.py`), 하네스 배선
(`scripts/diagnose_provider_chunk.py`).

**2-1 보존**: 검증을 통과한 claim payload 를 파일로 남기고 다음 invocation 에서
읽는다. 실측 확인: §6 의 두 번째 호출에서 chunk 0 이 `cache_hits` 로 잡혔고,
세 번째 invocation 은 `calls_sent: 0` 으로 두 청크를 모두 캐시에서 가져왔다.

**2-2 캐시 키** — `ChunkCacheKey.identity()` 가 해시하는 항목:
`cache_format_version`, **`source_sha256`**, **`chunk_id`**, `ordinal`,
**`core_page_refs`**, `context_page_refs`, `source_revision`,
**`claim_schema_version`**, **`evidence_segment_version`**,
**`prompt_sha256`**, `capability_policy_id`.
9개 필드를 하나씩 바꿔 가며 "옛 항목이 그래도 제공되는가"를 테스트했다 —
전부 미적중. 특히 `evidence_segment_version` 전용 테스트를 따로 두었다
(작업 1에서 분할을 바꾸면 자동 무효가 되어야 한다는 요구).

**2-3 재검증** — 캐시에서 꺼낸 payload 는 **live 응답과 같은
`parse_chunk_claim_response`** 를 통과해야 하고, 실패하면 파일을 지우고 미적중으로
처리한다. 인위적 변조 테스트 결과:

| 변조 | 결과 |
|---|---|
| evidence handle 을 존재하지 않는 것으로 위조 | **거부 + 파일 삭제** |
| `repeated_step_labels` 를 원문이 말하지 않는 범위로 변경 | **거부 + 파일 삭제** |
| `identity` 를 다른 계약으로 위조 | **거부 + 파일 삭제** |
| JSON 깨짐 / 다른 도구가 쓴 파일 / 빈 파일 | **미적중** |
| `repetition_count` 를 2 → 7 로 변경 | **통과한다** (아래) |

마지막 항목은 **설계이고 구멍이 아니다.** 개수는 모델의 원문 독해이고 STEP 18 이
"모델 말로 실행하지 않는다"고 정했다 — 바운드는
`unconfirmed_fixed_repetition` 게이트가 잡고 리뷰어가 원문 대조로 해제한다.
캐시된 개수도 live 개수와 **똑같이 미확인 게이트 아래로** 들어온다. 즉 캐시는
"호출 비용"만 바꾸고 "claim 이 무엇을 할 수 있는가"는 바꾸지 않는다.
이 경계를 테스트 본문에 명시해 두었다.

**2-4 저장 대상**: 파싱된 JSON 을 정규 형태(키 정렬, 공백 제거)로 다시 낸 것.
provider 원문 completion(공백·키 순서·감싼 문장)은 저장하지 않는다.
**측정으로 확인된 부수 효과**: claim payload 에는 원문 텍스트가 **아예 없다** —
provider 는 handle 과 숫자만 보내고 텍스트는 서버가 소유한다. 그래서 캐시가
protocol 산문을 디스크에 남기는 일은 실수로도 일어나지 않는다(테스트로 고정).
실제 저장물에서 8자 이상 원문 단어를 찾아보면 `analysis`, `complete`,
`material`, `temperature` 뿐이고 전부 스키마 어휘다.

**2-5 위치**: `data/development_cache/chunk_analysis/`, `.gitignore` 에 추가.
런타임 DB 는 건드리지 않는다. 진단 전후 `protocol_workspace.sqlite` sha256 동일.

**2-6 재시도**: 적중 청크는 호출을 건너뛴다. 병합 조건은 여전히 "모든 청크 검증됨"
이고 출처가 캐시여도 성립한다 —
`test_merge_accepts_a_set_that_is_part_cache_and_part_fresh` 가 3청크 문서에서
캐시 2 + 신규 1 로 병합을 성립시킨다.

**2-7 청크 단위 집행**: `--chunk N --budget 1 --execute` 로 1청크만 시도하고
결과를 캐시에 남긴다. 3청크 문서에서 invocation 당 1회씩 3번 → 3청크 전부 확보,
4번째 invocation 은 0회. 실측으로 §6 에서 같은 일이 in-gel 에 일어났다.

### 기존 검증된 동작에 대한 위험

캐시가 잘못된 항목을 통과시키면 규칙 우회가 된다. 그래서 게이트를 체크섬이 아니라
**재검증**으로 두었다(체크섬이면 "규칙이 바뀐 옛 항목"을 잡지 못한다).
남는 위험은 재검증이 잡지 못하는 층 — 위 `repetition_count` — 인데, 그 층은
캐시 이전에도 사람이 확인하는 층이었다.

---

## 작업 3 — 계층 번호 라벨 인식

### 3-1 확장 (실행함)

```
before: ^[ \t]*([1-9][0-9]{0,2})(?:[.)])?[ \t]+(\S+)
after : ^[ \t]*([1-9][0-9]{0,2}(?:\.[0-9]{1,3}){0,2})(?:[.)])?[ \t]+(\S+)
```

`3.2`, `3.10`, `3.2.1` 을 받는다.

### 3-2 오탐 방지 — 판정 근거 3개, 의미 판단 0개

1. **줄 맨 앞인가** — 정규식이 `(?m)^[ \t]*` 로 이미 앵커되어 있다. 본문 중간의
   `3.5 mL` 는 애초에 매칭되지 않는다.
2. **바로 뒤가 단위인가** — 기존 `_VALUE_UNITS` 필터를 그대로 쓴다. 줄이
   `3.5 mL of buffer` 로 시작하면 `mL` 때문에 탈락한다.
3. **같은 상위 번호와의 관계** — 여기서 **지시를 문자 그대로 적용하면 4개 문서에서
   성립하지 않았다.** "같은 페이지에 형제(`3.x`)가 2개 이상이면 채택"으로 재보니
   intracellular 의 진짜 단계 12개(`3.1 Grow`, `3.4 Using`, `3.8 Centrifuge`,
   `3.11 Resuspend` …)가 페이지마다 1개씩 흩어져 있어 전부 탈락했다.

   그래서 **대우(對偶)로 뒤집어 구현했다: 상위 번호가 이 페이지에서 스스로
   라벨이면 그 `N.M` 은 하위 노트다.** 근거는 측정이다 —
   headspace p.4 는 「6 Transfer 10 mL of autoclaved LB…」 바로 아래
   「6.1 While we use LB as the primary medium, other media can also be utilized」
   (매체 선택에 대한 **주석**), p.5 는 step 18 아래
   「18.1 Equation for working out dilution volume」(**수식**)이다.
   intracellular 에는 `3` 과 `3.4` 가 같은 페이지에 함께 있는 경우가 없다 —
   `3.4` 가 곧 단계이기 때문이다.
   여전히 기하(줄 앞) + 단위 + 번호 관계만 쓰고, 줄을 읽지 않는다.

### 3-3 monotonicity 구멍을 넓히는가 — **넓히지 않는다**

`fixture_scope`(깔끔한 증가 수열 게이트) 확장 후 재측정:

| 문서 | in_scope | duplicates | descents | strictly_increasing |
|---|---|---|---|---|
| in-gel | True | 0 | 0 | True |
| headspace | True | 0 | 0 | True |
| intracellular | **False** | 7 | 4 | False |
| ANKOM | True | 0 | 0 | True |

4개 문서 전부 확장 **전과 동일**하다. intracellular 는 여전히 오프라인 채점기의
범위 밖이며(제목·목차의 정수와 계층 라벨이 섞여 단조가 아니다) 그것이 보수적으로
맞다.

### 3-4 확장 전/후 인식 라벨 수 (4개 문서)

| 문서 | 전 | 후 | 추가 | 손실 | 계층 라벨 |
|---|---|---|---|---|---|
| **in-gel** | **25** | **25** | **0** | **0** | **0** |
| headspace | 61 | 61 | 0 | 0 | 0 |
| intracellular | 9 | **29** | 20 | 0 | 20 |
| ANKOM | 67 | 67 | 0 | 0 | 0 |

**in-gel 의 비-지시 라벨은 0 에서 늘지 않았다** — 계층 번호가 아예 없는 문서라
정규식 확장이 도달하지 않는다. 따라서 작업 6 조건 (d) 만족. headspace 도
0 증가(6.1·18.1 이 규칙 3으로 탈락). 손실 0.

### 3-5 intracellular 의 변화

인식된 단계 라벨 **9 → 29** (계층 20개 추가). 페이지별:
`{4:(1,2,3), 5:(3.2,3.3), 6:(3.4,), 7:(3.5,3.6,3.7), 8:(3.8,), 9:(3.9,3.10),
12:(3.11,), 13:(3.12,4), 14:(4.1,4.2), 18:(4.3,1), 19:(2,4.4), 20:(4.5,),
22:(4.6,), 27:(4.7,), 30:(5,1,2), 31:(5.1,), 32:(5.2,)}`

값 보유 세그먼트 중 **단계 내부 비율**:

| 문서 | 전 | 후 |
|---|---|---|
| in-gel | 20/22 (90.9%) | 20/22 (**90.9%**) |
| headspace | 21/23 (91.3%) | 21/23 (**91.3%**) |
| intracellular | 2/14 (**14.3%**) | 12/14 (**85.7%**) |
| ANKOM | 34/37 (91.9%) | 34/37 (**91.9%**) |

STEP 22 의 intracellular 이상치(14.3%)가 사라지고 나머지 셋과 같은 대역에 들어왔다.

### 기존 검증된 동작에 대한 위험

`tests/test_numbered_label_trigger.py::test_what_the_narrowing_leaves_behind`
가 "9개가 남고 그중 실행 단계는 없다"를 단언하고 있었다. 그 전제가 이번 작업의
대상이므로 새 현실을 단언하도록 갱신하고, 그 대신 **더 강한 두 테스트를 추가**했다:
headspace 의 `6.1`/`18.1` 이 라벨이 **아님**을, in-gel·ANKOM 의 개수가 25/67 로
불변이며 계층 라벨이 0임을 고정한다.

---

## 작업 4 — PDF 파싱 프로세스 분리

### 4-1 분리 (실행함)

새 모듈 `src/voice_workflow_agent/pdf_text_worker.py`. 자식이 하는 일은 문서 하나를
열어 페이지 텍스트를 읽어 돌려주는 것뿐이다. **판단하는 모든 것은 부모에 남는다** —
바이트 sha256, 페이지 해시, 세그먼트 경계, 독립 cross-check, 글리프 해결.
부모는 신원을 자식에게 묻지 않는다.

`multiprocessing` 대신 **subprocess** 를 쓴다: `spawn` 은 부모의 `__main__` 을
재import 하고, 그 모듈은 서버·테스트 러너·스크립트에서 각각 다르다 — 문서와
무관한 이유로 파서가 실패하는 경로가 셋 생긴다(실제로 재현했다).
프로토콜은 stdin 에 JSON 요청 1개, stdout 에 JSON 응답 1개.

### 4-2 결정적 오류 (실행함)

워커가 죽으면 **raise 한다.** 여기서 "모든 페이지 판독 불가"를 반환하면 죽은
파서가 "텍스트가 없는 문서"로 보이고, readiness 가 읽지도 않은 원문에 대해
판단하게 된다 — fail closed 의 옷을 입은 fail dead.

- `ProtocolPdfWorkerError` (`code = protocol_pdf_worker_failed`)
- `ProtocolPdfWorkerTimeoutError` (`code = protocol_pdf_worker_timeout`)
- **`ProtocolPdfMalformedError` 의 하위가 아니다** — 크래시를 "손상된 문서"로
  분류하면 라이브러리 결함이 원문의 죄가 되고, 독자가 받는 422 가 거짓이 된다.
- HTTP 매핑: `_catalog_http_error` 가 **503 + 고유 detail** 로 보낸다(422 아님).

### 4-3 타임아웃과 메모리 상한 — 값과 근거

- **타임아웃 30 s.** 최대 문서(48.9 MB / 40 페이지) 추출 실측 1.25 s → **약 24배**.
  동시에, 재현된 크래시가 죽기까지 걸린 **16 s 보다 위**여서 정상적으로 느린
  파싱을 잘라 결함으로 오인하지 않는다.
- **주소 공간 1 GiB.** 같은 문서 peak RSS 실측 108 MB → **약 9배**. 그리고
  200 MB 상한에서도 정상 완료함을 직접 측정해 둔 바 있어 하한도 확인되어 있다.
  3.8 GB 머신이 곤란해지기 훨씬 전에 폭주를 죽인다.
  **상한은 안전장치가 아니다** — `-fno-exceptions` 빌드는 할당 실패를 검사하지
  않을 수 있다. 안전을 만드는 것은 프로세스 경계이고 상한은 피해 범위만 줄인다.
  자식이 pypdfium2 를 import 하기 **전에** 설정한다.

부수 비용(실측): 인터프리터 기동 + pypdfium2 import 로 추출 1회당 **약 0.22–0.25 s**
(in-gel 0.19→0.44 s, ANKOM 1.25→1.47 s). STEP 23 의 추출 캐시가 있어 문서·프로세스당
1회만 지불된다.

### 4-4 evidence handle 바이트 동일성 — **확인함. 동일하다.**

같은 작업 3 코드를 양쪽에 두고 프로세스 경계만 다르게 한 두 실행을 비교했다
(별도 git worktree 에 이전 커밋을 꺼내 in-process pdfium 을 남기고, 작업 3 파일만
복사). 비교 대상: 4개 문서 **99 페이지 566 세그먼트**의
`page_text_sha256` 전부, `segment_id` 전부, `text_verification`,
`divergent_page_numbers`, `glyph_resolutions` 수, `unresolved_glyph_reasons` 전문.

```
BEFORE digest: a2390a731e04db50b832ae16c18ce57c5c00e2f22d9953c17bb0fe43b8c91fb8
AFTER  digest: a2390a731e04db50b832ae16c18ce57c5c00e2f22d9953c17bb0fe43b8c91fb8
cmp: 두 JSON 파일 바이트 동일
```

이것이 이 작업의 가장 큰 회귀 위험이었고, 통과했다.

### 4-5 매니페스트 검증 — 계속 통과

큐레이션 fixture 를 로드하면 timers 매니페스트가 원문 대조 검증을 거친다:
`fixture_sha256 = fb869290f1b52afab91f…`(불변), **timer 10건 verified**,
**visual 2건 selected**(단계 인덱스 6, 8). 전체 스위트 1353건 통과.

### 4-6 `_PDFIUM_LOCK` 유지/제거 판단

**서버 프로세스에서는 제거했다. 워커 모듈에는 유지했다.**

- 제거 근거: 서버 프로세스는 이제 pdfium 을 import 하지 않는다(AST 테스트로 고정).
  없는 라이브러리를 위해 락을 잡을 이유가 없고, 추출 1건 = 프로세스 1개이므로
  동시 추출은 이제 **안전하게 병렬**이다. 락을 남기면 그 이득을 스스로 없앤다.
- 유지 근거: `read_page_texts` 는 import 가능한 함수다. 자식은 단일 스레드지만,
  누군가 in-process 로 그것을 호출하면 pdfium 의 "동시 호출 금지, 다른 문서라도
  안 된다"가 다시 문제가 된다. 그래서 그 함수 안에는 락을 남기고,
  **in-process 직접 호출이 실제로 직렬화되는지**를 계측 테스트로 고정했다
  (8 스레드 × 24회, `max_holders == 1`, `contended > 0`).

### 4-7 워커를 강제로 죽이는 테스트 (실행함)

`tests/test_pdf_worker_isolation.py`, 11건. 워커를 죽는 방식마다 대체해 넣고
**서버가 살아남고 결정적 오류가 나오는지** 고정한다:

| 죽는 방식 | 결과 |
|---|---|
| `SIGSEGV` (2026-09-05 시그니처) | `ProtocolPdfWorkerError` + 이후 정상 추출 성공 |
| `SIGABRT` (2026-09-04 시그니처) | `ProtocolPdfWorkerError` |
| exit code 3 | `ProtocolPdfWorkerError` |
| 30초 hang (타임아웃 1초로 축소) | `ProtocolPdfWorkerTimeoutError`, 프로세스 kill |
| 잘린 JSON / JSON 아님 / 아무 출력 없음 | `ProtocolPdfWorkerError` |
| 페이지 수가 다른 응답 | `ProtocolPdfWorkerError` (부모가 페이지를 센다) |
| 페이지가 문자열이 아닌 응답 | `ProtocolPdfWorkerError` |
| 오류가 422 로 새는지 | `_catalog_http_error` → **503 `protocol_pdf_worker_failed`** |

추가로 "열 수 없는 파일은 여전히 문서 결함(모든 페이지 None)"과
"추출 1건당 워커 프로세스 1개"를 고정했다.

---

## 작업 5 — 사전 채점 (호출 0회)

### 5-1 `audit_reference` 재실행 — **불일치 0건 유지**

작업 1(변경 없음)·작업 3(확장) 이후 in-gel 큐레이션 정답지 25단계에 대해
`audit_reference` **notes = 0**. 정답지를 정답지로 채점한 천장도 확인:
`mean_text_similarity = 1.0`, `steps_with_matching_values = 25/25`,
missing/extra labels 없음, order_matches True.

### 5-2 "정답지에 없는 원문 값" — **2개가 아니라 3개다. 그리고 채점기는 그것을 불일치로 셌다.**

**지시의 수치가 성립하지 않는다.** STEP 22 문서
(`PROTOCOL_BOUNDARY_AND_OBLIGATION_DESIGN.md`)는 "정답지가 **27개 값**(duration 10,
temperature 8, volume 9)을 25단계 중 11단계에 걸쳐 진술하고, 원문 페이지에는
**29개 distinct 값**이 있다"고 적었다. 27은 **출현 횟수**이고 29는 **distinct 개수**다.
서로 다른 단위를 뺀 값이므로 "차이 2"는 성립하지 않는다.

같은 기준(distinct)으로 다시 재면:

| 종류 | 정답지 distinct | 원문 distinct | 원문에만 있는 것 |
|---|---|---|---|
| durations | 6 | 7 | **`1200`** (20분) |
| temperatures | 3 | 3 | — |
| volumes | 5 | 7 | **`1000ul`, `50ul`** |
| 합 | 14 | 17 | **3개** |

역방향(정답지에만 있고 원문에 없는 값)은 **0개** — `audit_reference` 의 0건과 일치.

**채점기의 처리 — 확인 결과: "불일치"로 셌다.** `StepComparison.values_match` 가
`reference_values == candidate_values` 엄격 동등이라, 정답지가 침묵하는 값을
후보가 맞게 읽어도 불일치가 된다. `50min` 을 `15min` 으로 잘못 읽은 것과
구분되지 않는다. 즉 **추출이 좋아질수록 점수가 내려간다.**

지시는 "확인한다"였고 확인 결과가 결함이므로, 작업 6 의 숫자가 뜻을 갖도록
**계측 도구만 최소 수정**했다(readiness 권위 없음, 규칙 변경 없음):
`value_outcome` 을 `matching` / `contradicted` / `reference_silent` 로 나누고
`steps_with_contradicted_values`, `steps_unscorable_on_values` 를 따로 보고한다.
`values_match`(엄격)는 그대로 남겨 기존 의미를 바꾸지 않았다. 테스트 5건 추가.

---

## 작업 6 — 승인된 잔여 provider 호출 2회 집행

### 집행 조건 점검

| 조건 | 판정 | 근거 |
|---|---|---|
| (a) 캐시 동작 + 테스트 통과 | **만족** | `tests/test_chunk_analysis_cache.py` 14건 통과 |
| (b) 청크 단위 집행 가능 | **만족** | `--chunk N --budget 1 --execute`; 3청크 문서에서 invocation 당 1회 테스트 |
| (c) 분할을 바꿨다면 키에 반영 | **만족** | 분할을 바꾸지 않았고, 키에는 `evidence_segment_version` 이 이미 포함 |
| (d) in-gel 비-지시 라벨 0 유지 | **만족** | in-gel 25 → 25, 계층 라벨 0, 손실 0 |

### 6-1/6-2 집행 (in-gel, 청크 1개씩 2회)

| 호출 | 청크 | core pages | 결과 | latency | prompt/completion tokens | 캐시 |
|---|---|---|---|---|---|---|
| 1 | **0** | 1, 2, 3 | **passed** | 9.41 s | 4351 / 1245 | stored `2e580b63…` |
| 2 | **2** | 9 | **passed** | 6.60 s | 3662 / 941 | stored `4da17cc0…` |

두 번째 invocation 은 chunk 0 을 **캐시에서 재검증해 가져왔고**(`cache_hits: [0]`),
그 청크에는 호출을 쓰지 않았다. 즉 2-6 이 실측으로 확인되었다.

세 번째 invocation(0회 소비): `calls_sent: 0`, `cache_hits: [0, 2]`,
`validated_from_cache: [0, 2]`.

**chunk 1 은 시도하지 않았다** — 예산 소진.

### 6-3 통과한 청크 수 — **2개.** 실패 없음.

관측된 구조(원문 산문은 로그하지 않는다):

- chunk 0: claims 10 — action 2 (`source_label` "1","2"), material 3, quantity 5.
  document-level 2, declined segments 0, 자기보고 미완 페이지 0, `non_step_labels` 0.
- chunk 2: claims 7 — action 1 (`source_label` "25"), duration 1, equipment 1,
  material 2, quantity 1, temperature 1. declined 0, 미완 0, `non_step_labels` 0.

repetition claim 은 두 청크 모두 0건 — in-gel 의 repeat 3건은 모두 chunk 1
(pp.4–8) 안에 있으므로 예상과 일치한다.

### 6-4 병합/채점 — **하지 않았다**

3청크 중 **2개**만 캐시에 모였다. 하네스가 스스로 거부한다:
`walk.attempted = False`, `reason = "2 of 3 chunks validated; merge requires
every chunk"`. 지시대로 **그 상태를 보고하고 멈췄으며 추가 호출을 하지 않았다.**

따라서 `protocol_extraction_accuracy` **채점은 실행하지 않았다.**
다음 STEP 에 **1회**만 있으면 chunk 1 을 채워 3/3 이 되고 병합·채점이 가능하다
(캐시가 chunk 0·2 를 들고 있는 한).

### 6-5 채점 결과와 규칙 통과의 분리

이번에 보고할 채점 결과는 **없다**(실행하지 않음). 규칙 통과는 위 6-3 이고,
그 둘은 서로 다른 질문이라 합치지 않는다. 채점기가 준비되었는지만 사전 확인했다
(§5-1: 정답지 자체 감사 0건, 천장 1.0).

---

## 내 지시 중 4개 문서에서 성립하지 않은 것

| 지시 | 어디가 왜 |
|---|---|
| 1-5 "기하 기준(세로 간격 임계, 칼럼 경계)으로만 판정" | **세로 간격 임계는 in-gel 에서 실제 지시문 5개를 자른다**(2 Prepare two wash solutions, 24 Quickly spin down…, Expected result ×3). 그림이 단계 텍스트 흐름 안에 있어 진짜 지시문이 큰 간격을 갖기 때문. **칼럼 경계는 적용 대상이 없다** — 4개 문서에 칼럼 혼입 0건. → 지시 불가로 보고, 대신 성립하는 하단 띠 기준을 측정해 제시 |
| 3-2 "같은 상위 번호를 공유하는 수열을 이루는가" | 문자 그대로 적용하면 intracellular 의 진짜 단계 12개가 페이지마다 1개씩 흩어져 전부 탈락. **대우로 뒤집어** "상위 번호가 이 페이지의 라벨이면 하위 노트" 로 구현. 여전히 번호 관계만 쓴다 |
| 5-2 "정답지에 없는 원문 값 2개" | **3개다** (`1200`, `1000ul`, `50ul`). STEP 22 의 "27 vs 29" 는 출현 횟수와 distinct 개수를 비교한 것이라 그 차이 2 는 like-for-like 가 아니었다 |
| 4-6 "`_PDFIUM_LOCK` 유지할지 제거할지" | 양분 선택이 아니었다. 서버 프로세스에서 제거하고 워커 모듈에 유지하는 것이 옳다(§4-6) |

---

## 기존 검증된 동작을 깨뜨릴 위험

- **작업 3**: 라벨 트리거 확장은 in-gel·headspace·ANKOM 을 라벨 단위로 불변으로
  유지했고(측정), `fixture_scope` 판정도 4개 문서 불변이다. 위험은 intracellular
  쪽인데, 그 문서는 여전히 오프라인 채점 범위 밖이고 cross-check 도 실패 상태다.
  테스트 1건의 전제를 갱신하고 더 강한 2건을 추가했다.
- **작업 4**: 최대 위험은 evidence handle 변동이었고 4개 문서 99 페이지 566
  세그먼트에서 **바이트 동일**을 확인했다. 남는 위험은 성능(추출당 +0.22–0.25 s)과,
  워커 기동 실패가 문서 결함으로 오인될 가능성 — 후자는 별도 예외·별도 HTTP 코드로
  분리하고 테스트로 고정했다.
- **작업 2**: 캐시가 규칙을 우회하면 치명적이므로 게이트를 재검증으로 두고 변조
  4종을 테스트했다. 재검증이 잡지 못하는 층(`repetition_count`)은 캐시 도입 전에도
  사람이 확인하던 층이며, 그 사실을 테스트 본문에 남겼다.
- **작업 5**: `values_match`(엄격)의 의미를 바꾸지 않고 새 지표를 병렬 추가했으므로
  기존 단언은 그대로 성립한다.
- **작업 1**: 아무것도 바꾸지 않았으므로 위험 0. 대신 다음 STEP 에서 바꿀 때
  캐시된 in-gel 2청크가 무효화된다는 대가를 §1-5 에 명시했다.
- **런타임 데이터**: `protocol_workspace.sqlite` sha256 불변
  (`9102701e0fd65ef8be6b5cce2ffc59ff18fddd880390bc9ad26db0a1fbe69866`),
  PDF·객체 저장소 읽기만. 캐시는 `data/development_cache/`(gitignore).

---

## 이번 STEP 에서 하지 않은 것

- 세그먼트 분할 변경 (작업 1-5, 근거는 §1-5) — 다음 STEP 첫 작업으로 명시
- repeat-until 지원 (작업 0-2, 범위만)
- in-gel 병합·채점 (작업 6-4, 3청크 중 2개)
- chunk 1 호출 (예산 소진)

전체 스위트 **1353 passed, 1150 subtests**, 실패 0.
`compileall`, `git diff --check`, `scripts/replay_turns.py` 통과.

---

Provider 호출 횟수: 2
