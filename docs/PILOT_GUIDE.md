# 파일럿 안내 — 실행 · 데이터 처리 · 외부 AI 고지 · 삭제

날짜: 2026-10-13 (줄 PL), 2026-10-18 지표 추가 (줄 LOG)·계정 절(7절) 추가 (줄 ON) · 대상: 파일럿을 여는 사람(기술 담당·연구실 담당)

VoineyLab 은 MVP 시제품이고 현장에서 검증하지 않았다. 검증된 GLP/GMP/임상 시스템이 아니다(README). 이 문서는
**한 연구실에서, 사람이 곁에서 지켜보며 하는 시험**을 여는 순서를 적는다. 설치·검증 목록은
[`PILOT_DEPLOYMENT_GUIDE.md`](PILOT_DEPLOYMENT_GUIDE.md), 서버·백업 명령은
[`DEPLOYMENT_RUNBOOK.md`](DEPLOYMENT_RUNBOOK.md), 휴대폰용 HTTPS 는 [`PILOT_HTTPS_SETUP.md`](PILOT_HTTPS_SETUP.md).

4절의 고지 문안은 **초안**이다. 참가자에게 보이기 전에 사람이 확정한다.

## 1. 안전 문서가 있을 때와 없을 때

파일럿 실행기(`scripts/run_pilot.sh`)는 기관 안전 문서 카탈로그가 정해진 자리
(`data/runtime/pilot/approved_safety_catalog.sqlite`)에 있는지 보고 두 가지 중 하나로 시작한다. 범위는 실행기가
정하고 `.env`·셸 값은 무시한다(다르면 `[WARN]` 을 찍는다).

| | 카탈로그 파일이 있다 | 카탈로그 파일이 없다 |
|---|---|---|
| 사용 범위 | `reference_only` | `source_protocol_only` (2026-10-13 결정, "B안") |
| 시작 조건 | 카탈로그를 점검해 통과해야 한다: 읽을 수 있고, demo 문서 0개, 승인된 `reference_only` 문서 1개 이상 | 점검할 카탈로그가 없다. 바로 시작한다 |
| 안전 자료 | 기관 문서(SOP·SDS·장비 매뉴얼)와 업로드한 PDF 의 원문 안전 주의 | **업로드한 PDF 의 원문 안전 주의만** |
| 화면·기록 | 그대로 | **"기관 안전 문서 없음 · 원문 안전 주의만"** 한 줄 |

- **없는 것을 있는 것처럼 보이지 않게 한다.** `source_protocol_only` 에서는 기관 안전 문서를 찾지도 보여 주지도
  않고, 시작 점검 배너(`SAFETY_SCOPE = …`)·시작 전 확인·안전 관련 질문의 답·실험 기록이 같은 한 줄을 낸다.
- **카탈로그 파일이 있는데 쓸 수 없으면 "없음"으로 치지 않는다.** demo 문서가 섞였거나, 승인된 `reference_only`
  문서가 없거나, SQLite 가 아니거나, 폴더·끊어진 링크가 그 자리에 있으면 시작을 거부한다(종료 코드 1). 기관 문서 없이
  시작하려면 그 파일을 치운다.
- 원문 안전 주의의 표시와 시작 전 확인("이 프로토콜로 시작")의 기록은 두 범위에서 같다.
- 개발용 픽스처(in-gel)는 `source_protocol_only` 에서 실행할 수 없다(개발 범위가 아니다).

### 1-1. `source_protocol_only` 에서 서버가 내보내는 것

| 어디 | 무엇 |
|---|---|
| 시작 전 화면(`GET /api/protocols/{id}/review`) | `safety_scope: {usage_scope, source_protocol_only: true, notice: "기관 안전 문서 없음 · 원문 안전 주의만"}` |
| 안전 관련 질문의 답(`reply.complete` 의 `text`) | 답 끝에 빈 줄과 같은 문장. 읽기만 하는 답에만 붙고 **말로는 하지 않는다** |
| `session.safety_pack` 사건 | `safety_pack.coverage_status = "source_protocol_only"`, `safety_pack.scope_notice` = 같은 문장, 문서 0개 |
| 실험 기록(장부)의 시작 확인 `safety_notices_acknowledged` | 원문 안전 주의 목록은 그대로, `safety_scope` 와 `institution_safety_notice` 가 더해진다 |

- 같은 범위가 아닌 실행(카탈로그가 있는 범위)은 위 값에 문장이 없다(`notice: null`, `scope_notice: null`).
- 서버는 문장을 보내고 화면은 그대로 그린다(화면이 말을 바꾸지 않는다). 시작 전 확인 카드의 안전 주의 위(`safety_scope.notice`)와
  실험 중 안전 상자(`safety_pack.scope_notice`)가 그 문장을 그린다(줄 UI3, 2026-10-14 병합). 안전 질문의 답 끝 한 줄도 화면에 보인다.
- 안전 관련 질문인지는 서버가 정한다: 규칙이 안전 질문으로 가른 답, 또는 질문에 안전 낱말(주의·위험·장갑·후드·환기 …)이
  든 답. 알아듣지 못하고 비켜 간 답에도 붙는다. 단계를 넘기거나 기록하는 턴에는 붙이지 않는다.
- 아직 없는 것: Word·Markdown 보고서의 시작 문장은 이 한 줄을 적지 않는다(보고서 생성 코드가 이 줄의 허용 밖이었다).
  장부에는 있으므로 보고서 쪽을 고칠 때 장부의 `institution_safety_notice` 를 읽으면 된다.

## 2. 실행

1. **준비** — [`PILOT_DEPLOYMENT_GUIDE.md`](PILOT_DEPLOYMENT_GUIDE.md) 2~3절. `.env` 는 `.env.example` 에서 만들고
   **모든 역할의 공급자와 모델을 적는다.** 비워 둔 역할은 `xai` 로 가지 않고 이름을 적은 오류가 된다
   (줄 PL 단계 1, [`MIGRATION_NOTES.md`](MIGRATION_NOTES.md)). 파일럿 실행기는 "AI 일반 지식"·외부 근거 검색을 꺼 둔다
   (셸이나 `.env` 가 켜면 켜진다).
2. **점검** — 아무것도 시작하지 않고 설정만 보여 준다. 종료 코드 0 이어야 한다.
   ```bash
   ./scripts/run_pilot.sh --check-only
   ```
   기관 문서 없이 열 때의 출력(사본 데이터 폴더에서 확인한 것, 종료 코드 0):
   ```
   --- Approved safety documents (fixed by this launcher) ---
   SAFETY_CATALOG = (none: …/data/runtime/pilot/approved_safety_catalog.sqlite is not there)
   USAGE_SCOPE    = source_protocol_only
   SAFETY_SCOPE   = 기관 안전 문서 없음 · 원문 안전 주의만
   …
   [OK] --check-only: configuration printed; server not started
   ```
   카탈로그가 있으면 `SAFETY_CATALOG = <경로>`, `USAGE_SCOPE = reference_only`, `demo documents = 0`,
   `approved active reference_only documents = N` 이 나온다.

   **LibreOffice 가 없으면** 점검이 `--- Source formats ---` 아래에 `word_sources(libreoffice): unavailable` 과 `[WARN]` 몇 줄(설치 명령
   포함)을 찍지만 **종료 코드는 0 이고 시작도 막지 않는다**(사람 결정 2026-10-14). Word(.docx) 원문을 올릴 때만 `docx_conversion_unavailable`
   (503)로 거절된다. PDF 만 올리는 파일럿이면 그대로 쓰고, Word 가 필요하면 `docs/SOURCE_FORMATS.md` 1절대로 설치한다.
3. **시작** — `./scripts/run_pilot.sh` (`127.0.0.1:8080`, `HOST`·`PORT` 로 바꾼다). 실제로 시작할 때만
   `data/runtime/pilot/` 을 만들고 그 폴더에 파일럿 표시 파일(`.voiney-pilot-data`)을 둔다. 확인:
   ```bash
   curl --fail http://127.0.0.1:8080/healthz
   curl --fail http://127.0.0.1:8080/readyz
   ```
4. **휴대폰** — 휴대폰 브라우저는 HTTPS(또는 localhost)에서만 마이크를 쓴다. 서버는 `127.0.0.1` 에서만 듣는다.
   도메인이 정해진 뒤 [`PILOT_HTTPS_SETUP.md`](PILOT_HTTPS_SETUP.md) 순서로 HTTPS 와 접속 비밀번호를 둔다.
   `HOST=0.0.0.0` 으로 서버를 직접 열지 않는다. 참가자마다 계정으로 로그인하게 하려면 7절(`VOINEY_LAB_AUTH_MODE=accounts`)을
   먼저 한다 — 기본값 `dev` 에서는 로그인이 없고 모든 요청이 개발 신원 한 명이다.
5. **멈춤** — 서버 프로세스를 멈춘다. 파일럿을 마치면 5절의 삭제로 간다.

## 3. 데이터 처리

### 3-1. 무엇이 어디에 남는가

모두 `data/runtime/pilot/` 아래(git 이 무시한다). 개발 서버는 다른 폴더(`data/runtime/candidate-a-live-acceptance/`)를 쓰고,
음성 진단 녹음 폴더도 파일럿 실행기가 이 폴더 안으로 고정한다 — **파일럿 데이터와 개발 데이터를 섞지 않는다.**

| 무엇 | 어디 | 비고 |
|---|---|---|
| 업로드한 프로토콜 PDF 원본(SHA-256 이름) | `objects/sha256/…` | 참가자가 올린 원문 |
| 분석·OCR·확인 질문·프로토콜 리비전 | `protocol_workspace.sqlite` | 업로드 PDF 의 글자가 들어 있다 |
| 실험 기록(말한 관찰·문제·메모), 보고서 문장 | `experiment_reports.sqlite` | 지우지 않는 장부 |
| 실험 세션, 번역 문장, 증거 파일(사진), 실험자 설정, 분석 지표 | `workspace/commercial_workspace.sqlite`, `workspace/evidence/…` | `dev`: 실험자 한 명(개발 신원) · `accounts`: 계정마다 |
| 파일럿 지표(숫자·코드·시각)와 실험 끝 평점·한 줄 메모 | `workspace/pilot_metrics.sqlite` | 3-3절. 계정 id 별로 남는다 |
| 계정(이름, 비밀번호 해시), 초대 링크의 해시, 로그인 세션, 로그인 시도 | `workspace/accounts.sqlite` | `accounts` 모드에서만 (7절). 비밀번호 원문은 어디에도 없다 |
| 음성 진단 녹음(WAV)과 전사(JSON) | `stt-diagnostics/` | **`VOINEY_LAB_STT_DIAGNOSTICS_ENABLED` 를 켰을 때만.** 끄면 원음을 저장하지 않는다 |
| 기관 안전 문서 카탈로그 | `approved_safety_catalog.sqlite` | 있을 때만. 참가자 데이터가 아니라서 삭제 도구가 건드리지 않는다 |

- 서버 로그에는 말 내용을 남기지 않는다(질의는 길이와 SHA-256 앞부분만).
- 키(`.env`)·원음·연구실 PDF·런타임 DB 는 저장소에 올리지 않는다.
- 사람이 정한 시험 약속: **수집한 데이터는 서비스 개선에만 쓰고 파일럿이 끝나면 삭제한다.** 5절이 그 절차다.

### 3-2. 밖으로 나가는 것(외부 AI 처리)

`.env.example` 의 지금 값 기준이다. **실제 `.env` 와 같은지 운영자가 확인한다.**

| 보내는 것 | 받는 곳 | 언제 |
|---|---|---|
| 말소리(한 번 말한 만큼) | ElevenLabs(음성 인식, `scribe_v2`) | 말할 때마다 |
| 말해 줄 문장 | Google Cloud(음성 합성) | 안내·답을 말할 때마다 |
| 업로드한 PDF 의 글자 | Google Gemini(분석·번역) | 업로드한 뒤 |
| 글자 층 없는 쪽의 이미지 | NAVER CLOVA OCR 또는 Google Cloud Vision | OCR 엔진을 설정했을 때만 |
| 말한 글과 지금 단계의 문맥 | OpenAI(라우터·답변) | 켰을 때 |
| 용어 설명 질문과 문맥 | OpenAI("AI 일반 지식") | 파일럿 실행기는 꺼 둔다. 켰을 때만 |
| 웹 설명·AI 도식 요청 | OpenAI | 켰을 때만 |
| 실험 기록의 사실 | Google Gemini(보고서 서술) | 보고서를 만들 때 |

### 3-3. 파일럿 지표 — 무엇을 재는가 (줄 LOG)

파일럿에서 "무엇을 얼마나 도왔는지"를 숫자로 보려고 서버가 남기는 것이다(10/16 제안의 지표 중 서버가 셀 수 있는 것).
**숫자·코드·시각만 남긴다.** 말한 글, 음성, 프로토콜 내용, 모델 출력은 지표에 들어가지 않는다(실험 기록은 원래 설계대로 남는다).
지표를 남기다 실패해도 실험은 멈추지 않는다 — 서버 로그에 `pilot.metrics.failed` 한 줄이 남고 그 값만 빠진다.

**어디에.** `VOINEY_LAB_WORKSPACE_DATA_DIR` 폴더의 `pilot_metrics.sqlite`(파일럿은 `data/runtime/pilot/workspace/pilot_metrics.sqlite`).
작업공간이 꺼져 있으면 저장하지 않는다(서버 로그 한 줄은 남는다). 모든 행에 **계정 id** 가 붙는다 — 로그인한 계정이 있으면 그 id,
없으면 개발 신원(`dev-local-admin`). 서버 로그 줄에는 계정이 없다.

**턴마다 (서버 로그 한 줄 + 표 한 행).** 음성 턴이 끝날 때마다 서버 로그에 이런 줄이 남는다.

```
pilot.turn turn_id=7 router=fallback reason=timeout action=next changed=1 step=1 clarify=0 source=voice first_audio_ms=1830 total_ms=2410
```

| 항목 | 값 |
|---|---|
| `router` | `off`(라우터 꺼짐) · `front`(앞 규칙이 처리) · `used`(모델 답을 씀) · `fallback`(규칙으로 넘김) · `stopped`(요청 한도에서 멈춤) |
| `reason` | 넘긴 이유: `timeout`(시간 초과) · `rate_limit`(요청 한도) · `format`(빈 답·읽을 수 없는 답) · `refused`(서버가 제안을 거부하거나 답 검사에서 떨어짐) · `error`(그 밖의 공급자 오류) · `none` |
| `action` | 서버가 한 일의 코드(`next`, `current`, `record_observation` …) |
| `changed` · `step` | 상태가 바뀌었는지, 단계를 넘겼는지 |
| `clarify` | 되물었는지(완료·대상·값 확인, 다시 말해 달라는 요청) |
| `source` | 그 턴의 말이 어디서 왔나: `voice`(말) · `candidate`(되물은 후보 단추를 누름) |
| `first_audio_ms` · `total_ms` | 말이 끝난 뒤 첫 소리까지, 턴이 끝날 때까지(밀리초) |

일시정지·다시 시작 **버튼**은 턴이 아니라서 `button_change` 표시로 따로 남는다. 상태 변경의 출처는 이 셋(말·버튼·후보 단추)이다.

**실험마다 (한 행, 내보낼 때 계산).** 실험 기록 장부(`experiment_reports.sqlite`)와 위 표를 합쳐 계산한다. 이 기능 전의 기록도
장부만으로 행이 나온다(그때 재지 않은 칸은 비어 있다).

| 열 | 정의 |
|---|---|
| `account_id` · `report_id` · `protocol_id` · `development_only` · `status` | 계정 id, 실험 기록 id, 프로토콜 id, 개발용 큐레이션 기록인지(1/0), 기록 상태(`in_progress`·`completed`·`stopped`) |
| `day` · `started_at` · `ended_at` · `duration_s` | 시작한 날(한국 날짜), 시작·끝(UTC), 걸린 초 |
| `account_experiment_number` · `protocol_run_number` | 그 계정의 몇 번째 실험인지, 그 계정이 그 프로토콜을 몇 번째로 돌렸는지 — "다시 쓰는 비율"의 바탕 |
| `steps` · `steps_by_voice` · `steps_by_button` · `hands_free_ratio` | 넘긴 단계 수, 말로 넘긴 것, 버튼(후보 단추)으로 넘긴 것, 말로 넘긴 비율 — "손을 쓰지 않고 끝낸 단계 비율". 단계를 넘기는 버튼은 지금 화면에 없어 후보 단추만 센다 |
| `reverts` | 되돌린 단계 수("방금 완료 취소") — "잘못 바뀐 상태"를 보는 자리 |
| `turns` · `router_*` · `clarifies` · `first_audio_median_ms` | 턴 수, 라우터 결과별 수(쓴 것·넘긴 것·이유별), 되묻기 수, 첫 소리까지 중앙값 |
| `button_changes` | 일시정지·다시 시작 버튼을 누른 수 |
| `timer_last_minute_alerts` · `timer_end_alerts` · `alert_answer_median_ms` | 타이머 1분 전·끝 알림 수, 알림 뒤 실험자의 첫 말까지 걸린 시간(중앙값) |
| `disconnects` · `disconnected_ms` | 줄 NET 의 끊김 기록 사건(`connection_restored`, 끊겼다가 다시 연결해 이어 간 한 번)의 수와 끊긴 시간 합. 사건은 이어 간 실험 기록에 붙는다. 끊긴 순간을 모르면 시간은 빈 칸 |
| `connections_closed` | 실험이 끝나지 않은 채 음성 연결이 닫힌 수(이 줄의 표시) — 다시 연결하지 않은 것, 페이지를 닫은 것도 센다 |
| `records` · `deviations` · `anomalies` · `system_anomalies` · `corrections` | 기록(측정·관찰·메모), 편차, 이상 사항, 시스템 이상, 기록 정정·취소의 **개수** |
| `report_made` · `report_minutes_after_end` | 보고서(Word·Markdown)를 만들었는지 또는 보고서 문장이 쓰였는지, 실험 끝에서 처음 만들 때까지(분) |
| `rating` · `feedback_skipped` | 실험 끝 평점(1–5)과 건너뛰었는지(마지막 답) |

**분석마다 (한 행).** `account_id`·`protocol_id`·`revision_id`·`recorded_at`(분석이 끝난 시각, UTC)과 `outcome`: `passed`(바로 실행 가능) · `review_pending`(검토 대기) · `notice`(끝났지만 실행을 막는 이유가 있어
안내함) · `failed`(분석 실패). `duration_ms`(분석에 걸린 시간), `card_count`(검토 카드 수), `review_registered_after_s`(분석 끝에서
"검토 완료하고 등록"까지 초), `executed` · `executed_after_review`(그 뒤 그 프로토콜로 실험을 시작했는지, 검토 등록 뒤에 시작했는지)
— "올린 PDF 가 바로/검토 뒤 실행된 비율과 검토 시간".

**하루마다 (관리자 화면의 표).** 위 실험·분석 행을 날짜별로 더한 것. `accounts`(그날 실험한 계정), `returning_accounts`(그 전 날에도
실험한 계정), `experiments`·`experiments_ended`(끝난 실험), `steps`·`hands_free_ratio`, `timer_alerts`·`alert_answer_median_ms`, `disconnects`·`disconnected_ms`·`connections_closed`, `reports_made`, `ratings`·
`rating_mean`, `analyses_*` 등.

**실험 끝 한 문항.** 실험이 끝나면 "실험 기록 검토" 칸 아래에 "이번 실험에 도움이 됐나요?" 1–5 단추와 한 줄 메모(선택, 200자)가
나온다. 건너뛸 수 있고, 같은 기기에서 같은 실험을 다시 묻지 않는다. 마지막 답이 그 실험의 평점이다. **메모는 지표가 아니다** —
관리자 화면과 지표 CSV 에는 나오지 않고, 아래 `notes` 로만 꺼낸다.

**관리자 화면.** 서버에 `VOINEY_LAB_ADMIN_TOKEN` 을 긴 임의 값으로 설정하고(비어 있으면 닫혀 있다) 서버를 다시 켠 뒤,
브라우저에서 `https://<서버>/admin-metrics.html` 을 연다. 토큰을 넣고 기간(기본 최근 14일)·계정 id 를 골라 "불러오기".
토큰은 그 페이지의 메모리에만 있고 요청 머리글(`X-Voice-Workflow-Admin-Token`)로만 보낸다. 서버 쪽 경로는
`GET /api/admin/pilot-metrics?from=YYYY-MM-DD&to=YYYY-MM-DD&account=<id>`. 줄 ON 의 계정에 관리자 역할(`admin`)이 생기면 그 계정은
토큰 없이 볼 수 있다.

**CSV 내보내기.** 읽기만 하고, 원본 폴더가 아니라 사본을 읽는다(서버가 켜져 있어도 된다). 밖으로 보내는 것은 없다.

```bash
python scripts/pilot_metrics.py days --from 2026-10-20 --to 2026-10-26        # 하루 요약
python scripts/pilot_metrics.py experiments --out experiments.csv              # 실험 단위 (엑셀용 UTF-8 BOM)
python scripts/pilot_metrics.py analyses --account oidc:1a2b3c...              # 분석 단위, 한 계정
python scripts/pilot_metrics.py notes                                          # 평점 메모 (팀 내부용)
```

기본 폴더는 `data/runtime/pilot`; 다른 폴더는 `--data-dir`, 파일 하나씩은 `--metrics-db`·`--reports-db`·`--workspace-db`.
내보낸 CSV 는 삭제 도구가 모르는 **복사본**이다(5절 3번).

**지우는 법.** `pilot_metrics.sqlite` 는 `workspace/` 안에 있어 5절의 삭제 도구가 "실험 세션·관찰·번역 문장·증거 파일·설정
(작업공간)" 범주로 함께 지운다. 따로 지우는 명령은 없다.

## 4. 외부 AI 처리 고지 문안 (초안 — 사람이 확정)

참가자에게 시작 전에 읽어 주거나 보여 줄 문안의 초안이다. 괄호 `[ ]` 는 운영자가 사실에 맞게 채운다.

> **VoineyLab 시험 참여 안내**
>
> 1. VoineyLab 은 시제품입니다. 실제 연구실에서 검증된 시스템이 아니고, 안전 판단이나 장비 제어를 하지 않습니다.
>    위험한 상황에서는 작업을 멈추고 연구실의 비상 절차를 따르세요.
> 2. 이 시험에서 [기관 안전 문서가 연결되어 있습니다 / **기관 안전 문서가 없고, 올리신 프로토콜 PDF 에 적힌 안전 주의만 보여 드립니다**].
> 3. 이 서비스는 **외부 AI 서비스**를 씁니다. 시험 중 다음이 연구실 밖 서비스로 전송되어 처리됩니다.
>    - 말씀하신 소리 → [ElevenLabs] (글로 바꾸기)
>    - 안내·답변 문장 → [Google Cloud] (소리로 바꾸기)
>    - 올리신 프로토콜 PDF 의 글자 → [Google Gemini] (분석·한국어 번역), 글자가 없는 쪽의 이미지 → [OCR 서비스]
>    - 말씀하신 질문과 지금 단계의 내용 → [OpenAI] (답변)
>    - 실험 기록 내용 → [Google Gemini] (보고서 문장)
> 4. 보내는 내용은 이 목록에 있는 것뿐이며, 서비스 제공업체가 보내진 내용을 어떻게 보관하는지는 각 업체의 약관을 따릅니다.
>    [업체별 보관·학습 사용 여부를 확인한 결과를 적는다].
> 5. 연구실 서버에 남는 것: 올리신 PDF 와 분석 결과, 실험 기록, [음성 진단 녹음을 켰다면 그 녹음과 전사],
>    그리고 서비스가 얼마나 도움이 됐는지 보는 **사용 지표**(단계를 말로 넘긴 수, 응답 시간, 연결 끊김 같은 숫자·시각 — 말씀하신
>    내용은 들어가지 않습니다)와 실험 끝에 남기신 평점·메모. 사용 지표는 계정별로 남고 관리자만 봅니다. 지표는 외부 AI 로 보내지 않습니다.
>    **이 데이터는 서비스를 개선하는 데에만 쓰며, 파일럿이 끝나면 삭제합니다.** 삭제한 날짜와 항목(내용은 아님)은 기록으로 남깁니다.
> 6. 참여를 멈추거나 데이터 삭제를 먼저 요청하실 수 있습니다: [연락처].

- 확정 전에 사람이 확인할 것: 연구 윤리·법무 검토, 각 업체의 데이터 보관 약관과 삭제 요청 방법, 실제 `.env` 의 공급자.
- 이 저장소는 업체 쪽 보관을 보증하지 않는다. 3-2절의 표가 문안 3번의 근거다.

## 5. 삭제 절차 (파일럿이 끝난 뒤)

삭제는 되돌릴 수 없다. 도구(`scripts/pilot_data_delete.py`)는 **미리보기 → 확인 → 삭제 → 기록** 순서만 허용한다.

1. **파일럿 서버를 멈춘다.** 도구는 파일을 열고 있는 프로세스가 있으면(Linux) 삭제를 거부한다.
2. **남길 것을 먼저 꺼낸다.** 참가자와 약속한 보고서(Word 등)를 내려받는다. 이것은 삭제 대상 밖의 복사본이 된다.
3. **복사본을 찾는다.** `scripts/pilot_state_backup.py` 로 만든 백업과 모든 내보내기는 이 도구가 모르는 복사본이다.
   같이 지운다(고지 문안 5번의 약속은 복사본에도 적용된다).
4. **미리보기** — 아무것도 지우지 않는다.
   ```bash
   python scripts/pilot_data_delete.py
   ```
   무엇이 몇 개인지 나온다(업로드 PDF, 분석·OCR 기록, 실험 기록, 실험 세션·증거 파일·번역 문장, 음성 진단 녹음). 읽을 수 있는지
   확인하는 곳이고, 마지막 줄이 이 내용에 꼭 맞는 확인 단어를 알려 준다. 내용은 출력하지 않는다.
   ```
   [1] 업로드한 프로토콜 원문 PDF
       폴더 objects/ — 파일 3개, 12.3 MB
       안에 든 것: PDF 3
   …
   지우지 않는 것: approved_safety_catalog.sqlite — 기관 안전 문서 (참가자 데이터가 아님)
   지우려면 파일럿 서버를 멈춘 뒤 이 줄을 그대로 실행하세요:
     python scripts/pilot_data_delete.py --delete --confirm 3FA9C2
   ```
5. **확인하고 삭제** — 미리보기가 알려 준 줄을 그대로 실행한다.
   ```bash
   python scripts/pilot_data_delete.py --delete --confirm 3FA9C2
   ```
   확인 단어는 미리본 파일들(경로·크기)에서 나온다. 그 사이 폴더가 바뀌었으면(서버가 쓰고 있었거나 파일이 늘었으면) 맞지 않아
   거부한다 — 처음부터 다시 한다.
6. **삭제 기록을 확인한다** — `data/runtime/pilot/deletion_log.jsonl`. 한 번에 한 줄이고, 언제(UTC)·무엇을(범주·파일 수·크기·
   표의 행 수)·결과(deleted/partial)만 있다. 파일 이름·내용·사용자 식별자는 없고, 도구가 이 기록을 지우지 않는다.
7. **다음 시험이 있으면** 서버를 다시 켠다. 저장소가 비어 있는 채로 새로 만들어진다.

도구가 거부하는 것(종료 코드 2, 아무것도 지우지 않는다): 개발 데이터 폴더(`candidate-a-live-acceptance`, `ci-e2e`,
`stt-diagnostics`, `candidate-a-source`, `data/development_cache`, `data/fixtures`)는 파일럿 표시가 있어도 거부하고,
`data/runtime/pilot` 도 아니고 표시 파일도 없는 폴더, 심볼릭 링크가 든 폴더, 확인 단어가 없거나 틀린 요청도 거부한다.
`--data-dir` 로 다른 폴더를 줄 수 있는 것은 실행기가 만든 표시 파일이 있는 폴더뿐이다.

**이 도구가 하지 않는 것**(사람이 한다): 백업·내보낸 보고서 같은 복사본 삭제, 외부 서비스(3-2절의 업체) 쪽에 남은 데이터의 삭제 요청,
디스크 조각의 안전 삭제, 참가자 휴대폰 브라우저의 캐시, 서버 로그(말 내용은 원래 없다).

**참가자 한 사람만 지우기**(`accounts` 모드, 줄 ON): 한 사람이 참여를 멈추고 삭제를 요청하면 폴더 전체 대신 그 계정의 데이터만 지운다.
같은 순서(미리보기 → 확인 단어 → 삭제 → 기록)이고, 서버를 먼저 멈춘다.
```bash
python scripts/pilot_data_delete.py --account "김재준"                       # 미리보기
python scripts/pilot_data_delete.py --account "김재준" --delete --confirm 3FA9C2
```
지우는 것: 그 계정의 실험 세션과 사건·완료 단계·관찰·증거(기록과 파일), 그 계정에 묶인 실험 기록(장부·보고서 문장), 실험자 설정, 접근 기록,
구성원 기록, 그리고 계정 자체(로그인 세션·로그인 시도). 덧붙이기만 하는 표는 삭제 중에만 잠금을 풀고 다시 잠근다.
지우지 않는 것: 그 계정이 올린 프로토콜(연구실이 함께 쓰는 목록) — 그런 프로토콜이 있으면 신원 행은 이름 없이 남는다. 삭제 기록에는 범주와 개수만 남고 이름은 없다.

## 7. 계정 (`VOINEY_LAB_AUTH_MODE=accounts`, 줄 ON 결정 ①, 2026-10-18)

기본값 `dev` 는 지금까지와 같다: 로그인이 없고 모든 요청이 개발 신원 한 명("실험자 (개발 로그인)")이다. 파일럿에서 참가자마다
계정을 두려면 `.env` 에 `VOINEY_LAB_AUTH_MODE=accounts` 를 적는다(작업공간 `VOINEY_LAB_WORKSPACE_ENABLED=true` 가 필요한데
파일럿 실행기가 켠다). 그러면:

- 모든 `/api` 요청과 음성 연결(`/ws`)에 로그인한 계정이 필요하다. 예외는 건강 확인(`/healthz`, `/readyz`), 세션 질문
  (`GET /api/auth/session`), 로그인·가입·초대 확인, 그리고 제 토큰을 쓰는 관리자 지표(`/api/admin/metrics`)뿐이다.
  로그인 없이 부르면 `401 authentication_required`.
- 세션은 쿠키 `voiney_session` 하나다: HttpOnly, SameSite=Lax, HTTPS(또는 `X-Forwarded-Proto: https` 를 붙이는 Caddy) 뒤에서는
  Secure, 30일. 서버에는 세션 토큰의 SHA-256 만 있다.
- 비밀번호는 표준 라이브러리 scrypt 해시(소금 16바이트, N=2¹⁴)로만 저장한다. 8–200자.
- 로그인 실패가 15분 안에 5번이면 그 이름(그리고 그 접속 주소)의 로그인을 15분 막는다(`429 login_locked`, `Retry-After`).
- 계정에는 "이용 권한" 칸 하나가 있고 값은 `pilot` 뿐이다(결제는 만들지 않았다).
- 실험·기록·검토본에 계정이 남는다: 실험 세션의 소유자, 실험 기록의 실험자 이름, 검토 결정과 검토본의 검토자 이름은 **계정 이름으로
  서버가 채운다**(화면이 보낸 이름은 무시). 같은 연구실(`--lab`)의 계정은 프로토콜 목록을 함께 쓰고, 실험 기록은 본인 것만 보인다
  (관리자는 연구실 전체를 읽는다 — 읽기만).

### 7-1. 초대 → 가입 → 로그인

1. **초대 링크**는 서버 셸에서만 만든다(기한·1회용). 링크는 지금 한 번만 보이고 저장소에는 해시만 남는다.
   ```bash
   source .venv/bin/activate
   python scripts/pilot_invite.py --admin --base-url https://pilot.<도메인> --note "연구실 담당"   # 첫 관리자
   python scripts/pilot_invite.py --base-url https://pilot.<도메인> --hours 48 --note "참가자 A"   # 참가자
   ```
   출력 예: `초대 링크 (지금 한 번만 보입니다 · 48시간 뒤 만료 · 한 번 쓰면 끝): https://pilot.<도메인>/?invite=…`.
   폴더는 파일럿 실행기가 쓰는 `data/runtime/pilot/workspace`(셸의 `VOINEY_LAB_WORKSPACE_DATA_DIR` 이 있으면 그것). 다른 연구실은 `--lab <id>`.
   `--list` 가 초대 목록(열림/사용됨/취소됨/만료됨), `--revoke <초대 id>` 가 취소.
2. **가입**: 참가자가 링크를 열면 화면이 이름·비밀번호를 묻는다(단계 ② 화면; 그 전에는 `POST /api/auth/signup`
   `{"invite": 토큰, "name": 이름, "password": 비밀번호}` → 201, 쿠키). 이름은 2–40자, 대소문자·띄어쓰기를 무시하고 하나뿐이다.
   가입하면 바로 로그인 상태다.
3. **로그인**: `POST /api/auth/login` `{"name","password"}` → 200 + 쿠키 / 401 `login_refused` / 429 `login_locked` / 403 `account_disabled`.
   **로그아웃**: `POST /api/auth/logout`(이 기기의 세션만 끝). **비밀번호 바꾸기**: `POST /api/auth/password` `{"current","new"}` —
   바꾸면 다른 기기의 세션은 모두 끝난다.
4. **지금 누구인가**: `GET /api/auth/session` → `{"mode": "accounts", "signed_in": true, "account": {"name", "admin", "entitlement": "pilot", …}}`.

### 7-2. 계정 끄기 (관리자 또는 서버 셸)

- 관리자 계정은 `GET /api/auth/accounts` 로 연구실의 계정을 보고 `POST /api/auth/accounts/{account_id}/disabled` `{"disabled": true}` 로
  끈다(그 계정의 열린 세션은 그 자리에서 끝나고 로그인도 막힌다). `false` 로 다시 켠다. 자기 자신은 끌 수 없다(`409 admin_self_disable`).
- 서버 셸에서는 관리자 없이도 끌 수 있다: `python scripts/pilot_invite.py --disable "이름"` / `--enable "이름"`, 목록은 `--accounts`.
- 한 사람의 데이터를 지우는 것은 5절 끝의 `--account`.

### 7-3. 사람이 확인할 것

- HTTPS 앞문([`PILOT_HTTPS_SETUP.md`](PILOT_HTTPS_SETUP.md))의 기본 인증은 그대로 둘 수도, 계정 로그인으로 갈음할 수도 있다 —
  둘 다 두면 참가자는 두 번 로그인한다. 그 문서는 아직 "파일럿 서버에는 로그인이 없다"고 적혀 있다(줄 ON 의 허용 밖).
- 계정 저장소(`workspace/accounts.sqlite`)는 작업공간 폴더에 있어 백업(`scripts/pilot_state_backup.py`)과 삭제 도구가 함께 다룬다.

## 8. 사람이 할 일 · 알려진 한계

1. 4절의 고지 문안 확정(윤리·법무 검토, 업체 약관 확인). 이 저장소는 외부 서비스의 보관을 보증하지 않는다.
2. 도메인 결정과 [`PILOT_HTTPS_SETUP.md`](PILOT_HTTPS_SETUP.md) 실행(줄 PL 은 실행하지 않았다).
3. 보고서 문서(Word)의 시작 문장에 `institution_safety_notice` 를 적는 일(보고서 생성 코드 담당).
4. 삭제 도구의 "파일을 열고 있는 프로세스" 확인은 Linux(`/proc`)에서만 하고, 다른 사용자가 연 파일은 볼 수 없다. 서버를 멈추는 것이 첫 단계다.
5. 파일럿 지표(3-3절): 끊김 수·시간은 줄 NET 의 `connection_restored` 사건으로 센다(다시 연결해 이어 간 것만).
   관리자 계정은 줄 ON 이 역할을 정하면 쓴다(지금은 토큰만). 평점 메모를 누가 언제 읽을지는 사람이 정한다.
6. 줄 PL 과 줄 LOG 는 실제 외부 호출을 하지 않았다(0회). 위 표의 공급자 호출은 이 저장소의 테스트에서 모두 가짜 응답으로만 확인했다(contract-tested).
