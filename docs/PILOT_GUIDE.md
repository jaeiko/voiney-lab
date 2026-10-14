# 파일럿 안내 — 실행 · 데이터 처리 · 외부 AI 고지 · 삭제

날짜: 2026-10-13 (줄 PL) · 대상: 파일럿을 여는 사람(기술 담당·연구실 담당)

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
- **화면은 줄 UI2 의 몫이다.** 서버는 문장을 보내고 화면은 그대로 그린다(화면이 말을 바꾸지 않는다). 시작 전 화면과 안전 상자가
  `notice`·`scope_notice` 를 그리는지는 UI2 가 확인한다. 그 전에도 안전 질문의 답 끝 한 줄은 화면에 보인다.
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
3. **시작** — `./scripts/run_pilot.sh` (`127.0.0.1:8080`, `HOST`·`PORT` 로 바꾼다). 실제로 시작할 때만
   `data/runtime/pilot/` 을 만들고 그 폴더에 파일럿 표시 파일(`.voiney-pilot-data`)을 둔다. 확인:
   ```bash
   curl --fail http://127.0.0.1:8080/healthz
   curl --fail http://127.0.0.1:8080/readyz
   ```
4. **휴대폰** — 휴대폰 브라우저는 HTTPS(또는 localhost)에서만 마이크를 쓴다. 서버는 `127.0.0.1` 에서만 듣는다.
   도메인이 정해진 뒤 [`PILOT_HTTPS_SETUP.md`](PILOT_HTTPS_SETUP.md) 순서로 HTTPS 와 접속 비밀번호를 둔다.
   `HOST=0.0.0.0` 으로 서버를 직접 열지 않는다(파일럿 서버에는 로그인이 없다).
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
| 실험 세션, 번역 문장, 증거 파일(사진), 실험자 설정, 분석 지표 | `workspace/commercial_workspace.sqlite`, `workspace/evidence/…` | 실험자 한 명(개발 신원) |
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
> 5. 연구실 서버에 남는 것: 올리신 PDF 와 분석 결과, 실험 기록, [음성 진단 녹음을 켰다면 그 녹음과 전사].
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

## 6. 사람이 할 일 · 알려진 한계

1. 4절의 고지 문안 확정(윤리·법무 검토, 업체 약관 확인). 이 저장소는 외부 서비스의 보관을 보증하지 않는다.
2. 도메인 결정과 [`PILOT_HTTPS_SETUP.md`](PILOT_HTTPS_SETUP.md) 실행(줄 PL 은 실행하지 않았다).
3. 화면(줄 UI2): 시작 전 화면·안전 상자가 `safety_scope.notice`·`safety_pack.scope_notice` 를 그리는지.
4. 보고서 문서(Word)의 시작 문장에 `institution_safety_notice` 를 적는 일(보고서 생성 코드 담당).
5. 삭제 도구의 "파일을 열고 있는 프로세스" 확인은 Linux(`/proc`)에서만 하고, 다른 사용자가 연 파일은 볼 수 없다. 서버를 멈추는 것이 첫 단계다.
6. 이 줄은 실제 외부 호출을 하지 않았다(0회). 위 표의 공급자 호출은 이 저장소의 테스트에서 모두 가짜 응답으로만 확인했다(contract-tested).
