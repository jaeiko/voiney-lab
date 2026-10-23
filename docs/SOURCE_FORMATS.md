# 원문 파일 형식 — PDF · Word · 사진 · 시약 표

줄 AN(FM, 사람 결정 2026-10-13). 프로토콜 원문은 PDF 말고도 Word 문서(.docx)와 사진(JPG·PNG·HEIC)으로 올릴 수 있고, 엑셀(.xlsx)은 프로토콜에 붙는 "시약 표"가 된다. Word 와 사진은 **PDF 로 바뀐 뒤 PDF 와 똑같은 근거 경로**(글자 추출 또는 OCR → 분석 → 원문 근거 검사)를 거친다. 등록되는 원문은 그 PDF 이고, 올린 파일의 체크섬은 등록 기록에 남는다.

| 올리는 것 | `Content-Type` | 어떻게 읽나 | 서버에 필요한 것 | 없으면 |
|---|---|---|---|---|
| PDF | `application/pdf` | 그대로 | – | – |
| Word (.docx) | `application/vnd.openxmlformats-officedocument.wordprocessingml.document` | LibreOffice 가 찍은 PDF | **LibreOffice(필수)**, 한국어 문서엔 `fonts-nanum` | 503 `docx_conversion_unavailable` |
| 사진 JPG·PNG | `image/jpeg`, `image/png` | 사진 한 장 = 300 dpi 이미지 한 쪽 PDF → OCR | Pillow(의존성), OCR 공급자 설정 | – |
| 사진 HEIC | `image/heic`, `image/heif` | 위와 같음 | **선택** `pillow-heif` | 503 `heic_support_unavailable` |
| 엑셀 (.xlsx) | `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` | 시약 표(시트·셀 위치 그대로). 분석하지 않음 | openpyxl(의존성) | – |

서버가 지금 무엇을 받을 수 있는지는 `GET /readyz` 의 `capabilities.docx_conversion_available`·`heic_photos_available` 과, 서버 시작 로그의 `protocol.source_formats docx=… heic=…` 줄로 확인한다.

## 1. LibreOffice — 서버에 반드시 설치

Word 원문은 LibreOffice 가 PDF 로 바꾼다. 이 패키지(pip)가 설치하지 않는 **시스템 프로그램**이므로 서버를 준비할 때 함께 설치한다.

Ubuntu(파일럿·개발 서버, 화면 없는 판):

```bash
sudo apt-get install --no-install-recommends libreoffice-writer-nogui fonts-nanum
soffice --version          # LibreOffice 26.2.x …
fc-match NanumGothic       # NanumGothic.ttf: "NanumGothic" "Regular"
```

- **한국어 글꼴(`fonts-nanum`)도 필요하다.** 한국어 Word 문서는 보통 "맑은 고딕"처럼 리눅스 서버에 없는 글꼴을 쓴다. 그러면 LibreOffice 가 영문은 한 글꼴, 한글은 다른 글꼴로 찍고, 그 PDF 의 글자 층은 띄어쓰기가 빠지고 글자 종류가 바뀔 때마다 줄이 끊긴다("시료2 g" / "을50 mL") — 원문 근거 대조가 맞지 않는다. 서버는 한글이 든 문서를 바꿀 때만 그 실행에 한해 한국어 글꼴 이름과 기본 글꼴을 나눔 글꼴로 돌린다(영문 문서는 LibreOffice 그대로). 나눔 글꼴이 없으면 이 규칙은 아무 일도 하지 않는다.
- 다른 위치에 설치했으면 `.env` 에 `VOINEY_LAB_OFFICE_CONVERTER=/opt/libreoffice/program/soffice` 처럼 적는다. 비워 두면 `PATH`(`soffice`, `libreoffice`)와 기본 설치 위치(`/usr/bin/soffice`, `/usr/lib/libreoffice/program/soffice`, `/opt/libreoffice/program/soffice`, macOS `/Applications/LibreOffice.app/…`, Windows `C:\Program Files\LibreOffice\program\soffice.exe`)에서 찾는다. 적은 경로에 파일이 없으면 다른 LibreOffice 를 찾지 않고 거절한다.
- Windows·macOS: LibreOffice 를 설치하면 위 기본 위치에서 찾는다. 한국어 글꼴은 운영체제에 있는 것(맑은 고딕, Apple SD Gothic Neo)을 쓴다.
- 바꾸는 시간: 말뭉치 .docx 15개에서 2–20 초(문서마다 LibreOffice 를 새로 띄움). 한 문서에 120 초를 넘기면 `source_conversion_failed`.
- Word 목록 번호 뒤의 탭은 PDF 글자 층에 아무 글자도 남기지 않아 "10.Then …"처럼 번호와 본문이 붙는다(번호와 본문 사이에 공백이 있어야 단계 번호로 읽는다 — 2026-10-05 결정). 그래서 LibreOffice 에 넘기는 사본에서만 목록 번호 뒤를 공백으로 바꾼다. 올린 파일과 그 체크섬은 그대로다.
- 같은 연구실에서 같은 .docx 를 다시 올리면 원본 체크섬으로 앞서 만든 프로토콜을 찾고 다시 바꾸지 않는다. 다른 연구실은 같은 원문이라도 독립된 프로토콜로 등록한다.
- **없어도 서버는 시작한다.** 서버는 시작 로그에 경고를 남기고, 파일럿 실행기(`scripts/run_pilot.sh`)는 시작 점검에 `[WARN]` 을 찍되 시작을 막지 않는다(사람 결정 2026-10-14). 막히는 것은 Word 원문을 올리는 일뿐이다.

## 2. HEIC 사진 — 선택 설치

아이폰 기본 사진 형식인 HEIC 는 선택 의존성 `pillow-heif` 로 읽는다. 기본 설치(`pip install -e '.[test]'`)에는 들어 있지 않다.

```bash
pip install -e '.[heic]'                                   # pillow-heif 1.8.1
python -c "import pillow_heif; print(pillow_heif.__version__)"
curl -s http://127.0.0.1:8080/readyz                        # "heic_photos_available": true
```

서버를 다시 시작해야 반영된다(시작 로그 `protocol.source_formats heic=available`). 지우려면 `pip uninstall pillow-heif`.

**왜 선택인가 — 라이선스.** `pillow-heif` 의 소스는 BSD-3-Clause 이지만, PyPI 바퀴(wheel)는 HEIC 를 푸는 `libheif`·`libde265`(LGPLv3)와 HEIC 를 만드는 `x265`(GPLv2)를 함께 싣고 있어 **바퀴 전체가 GPLv2** 로 배포된다(설치 뒤 `pillow_heif-1.8.1.dist-info/licenses/LICENSES_bundled.txt` 첫 줄: "License for pillow-heif binary wheels: GPLv2"). 서버 안에서 쓰는 것과 서버를 남에게 배포하는 것의 의무가 다를 수 있으므로, 넣을지는 조직의 라이선스 담당이 정한다. 이 저장소는 그 결정 전까지 기본으로 넣지 않는다.

**설치하지 않은 서버에서.** HEIC 를 올리면 `503 heic_support_unavailable` 로 거절되고 아무것도 등록되지 않는다. 사람에게는 이렇게 안내한다:

- 아이폰: 설정 → 카메라 → 포맷 → **"높은 호환성"** 을 고르면 이후 사진이 JPEG 로 찍힌다.
- 이미 찍은 HEIC: 사진 앱에서 "공유 → 옵션 → 자동"으로 보내거나 PC 에서 JPEG 로 바꿔 올린다.

## 3. 사진(JPG·PNG·HEIC) 공통

- 사진 한 장이 한 쪽이다. EXIF 방향대로 바로 세우고, 투명한 부분은 흰 바탕으로 깐다. 1억 픽셀을 넘으면 `413 protocol_image_too_large`, 형식이 `Content-Type` 과 다르면 `422 invalid_source_file`.
- 글자 층이 없으므로 업로드 때 OCR(`VOINEY_LAB_OCR_PROVIDERS`)이 읽고 이어서 분석한다. OCR 이 설정되지 않았으면 원문만 저장되고 막힌 이유가 보인다.
- OCR 이 읽은 수치(숫자 + 같은 줄의 단위, 예 "0.5 mL", "12,000 ×g")는 `GET /api/protocols/{id}/ocr` 의 `pages[].value_boxes`(글과 상자)와 `render_dpi` 로 남는다. 손글씨에서 읽은 수치는 사람이 원본 이미지 조각과 대조해 확인해야 한다(프로토콜 상세의 확인 카드).
- 손글씨 엔진 비교(줄 AN 보고서): 숫자+단위 정확 일치 CLOVA 58%, Google Vision 문서 모드 70%, Gemini 비전 81%(단, Gemini 는 글을 고쳐 써서 원문 그대로 읽는 OCR 로는 쓰지 않음).

## 4. 시약 표(.xlsx)

아래 API 예시는 파일럿 기본 포트 8080이다. 계정 모드에서는 로그인한 세션 쿠키가 필요하며, 예시의 `…`는 실제 비밀 값이 아니다. 브라우저의 프로토콜 상세에서도 파일을 붙일 수 있다.

```bash
curl -s -X POST 'http://127.0.0.1:8080/api/protocols/<protocol_id>/reagent-tables?filename=reagents.xlsx' \
  -H 'Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' \
  -b 'voiney_session=…' \
  --data-binary @reagents.xlsx
curl -s -b 'voiney_session=…' 'http://127.0.0.1:8080/api/protocols/<protocol_id>/reagent-tables'
```

- 통합 문서마다 기록 하나(append-only). 모든 시트와 비어 있지 않은 셀의 위치(`ref`)·값(`value`)·보이는 값(`display`, 백분율·소수 자리 반영)·서식·수식, 병합 범위, 숨김 시트.
- 수식 셀은 엑셀이 저장한 계산값을 쓴다. 계산값 없이 저장된 파일(프로그램이 만든 파일)은 경고가 붙는다 — 엑셀에서 열어 저장한 뒤 다시 올린다.
- 5 MB 또는 셀 20,000개를 넘으면 `413 reagent_table_too_large`(잘라서 받지 않음). 분석·단계 근거·실행 준비에는 쓰지 않는다.

## 5. 오류 코드 — 화면 문구 제안(줄 UI2)

| 코드 | HTTP | 제안 문구 |
|---|---|---|
| `docx_conversion_unavailable` | 503 | 이 서버에는 Word 변환기(LibreOffice)가 설치되어 있지 않습니다. 관리자에게 알리거나 PDF 로 저장해 올려 주세요. |
| `heic_support_unavailable` | 503 | 이 서버는 HEIC 사진을 받지 않습니다. 아이폰 설정 → 카메라 → 포맷 → "높은 호환성"으로 찍거나 JPEG 로 바꿔 올려 주세요. |
| `source_conversion_failed` | 422 | Word 문서를 PDF 로 바꾸지 못했습니다. 문서를 PDF 로 저장해 올려 주세요. |
| `invalid_source_file` | 422 | 파일이 고른 형식과 맞지 않거나 손상되었습니다. |
| `protocol_image_too_large` | 413 | 사진이 너무 큽니다(1억 픽셀 초과). 해상도를 낮춰 올려 주세요. |
| `invalid_reagent_table` | 422 | 엑셀(.xlsx) 시약 표로 읽을 수 없습니다. |
| `reagent_table_too_large` | 413 | 시약 표가 너무 큽니다(5 MB 또는 셀 20,000개 초과). |

## 6. 구글 드라이브 링크로 가져오기

파일을 내려받아 올리는 대신 구글 드라이브·문서·시트의 **공유 링크**를 넣는다. 서버가 그 파일을 받아 **올리기 경로(`POST /api/protocols`, `POST /api/protocols/{id}/reagent-tables`)에 그대로 넘긴다** — 형식 확인, 크기 상한, Word·사진 변환, OCR, 분석, 원문 근거 검사가 올린 파일과 똑같다. 받은 주소는 그 프로토콜 원장에 `protocol_source_linked` 기록(주소, 링크 종류, 파일 ID, 받은 호스트, 형식, 체크섬, 크기, 파일 이름)으로 남는다.

화면에서는 **새 프로토콜 올리기 → 공개 링크로 가져오기**로 Drive 파일·Docs를 가져온다. 주소를 넣고 **링크로 가져오기**를 누르면 파일 올리기와 같은 분석 결과를 보여 준다. Sheets 시약 표는 아래 API로만 붙일 수 있다.

```bash
# 파일럿 기본 주소 8080. 계정 모드에서는 로그인 세션 쿠키가 필요하다
curl -s -X POST http://127.0.0.1:8080/api/protocols/import-link \
  -H 'Content-Type: application/json' -b 'voiney_session=…' \
  -d '{"url":"https://drive.google.com/file/d/<파일ID>/view?usp=sharing"}'
# 구글 시트 → 시약 표: 붙일 프로토콜을 함께 보낸다
curl -s -X POST http://127.0.0.1:8080/api/protocols/import-link \
  -H 'Content-Type: application/json' -b 'voiney_session=…' \
  -d '{"url":"https://docs.google.com/spreadsheets/d/<ID>/edit","protocol_id":"<protocol_id>"}'
```

| 링크 | 받는 곳 | 무엇으로 |
|---|---|---|
| `drive.google.com/file/d/<ID>/…`, `drive.google.com/open?id=<ID>`, `drive.google.com/uc?id=<ID>`, `drive.usercontent.google.com/download?id=<ID>` | `https://drive.usercontent.google.com/download?id=<ID>&export=download` | 내용이 PDF·Word·사진이면 프로토콜 원문 |
| `docs.google.com/document/d/<ID>/…` | `…/document/d/<ID>/export?format=pdf` | PDF 로 내보내 프로토콜 원문 |
| `docs.google.com/spreadsheets/d/<ID>/…` | `…/spreadsheets/d/<ID>/export?format=xlsx` | `protocol_id` 가 있을 때만 시약 표 |

링크의 `resourcekey=` 는 함께 넘긴다(2021년 전에 공유한 파일). 폴더·구글 프레젠테이션·설문 링크는 받지 않는다.

**보안.** 서버가 밖으로 요청을 보내는 길이므로 다음을 지킨다(`protocol_link_import.py`).

- 링크는 `https` + `drive.google.com`·`docs.google.com`·`drive.usercontent.google.com` 만, 기본 포트, 주소 안의 사용자 이름·비밀번호 없음.
- 넘겨주기(redirect)는 위 세 호스트와, 구글이 문서·시트 내보내기를 넘겨주는 `doc-<xx>-<xx>-{docs,docstext,sheets}.googleusercontent.com` 으로만 따라간다(2026-10-20 실측: 문서 → `doc-0g-3o-docstext…`, 시트 → `doc-08-4o-sheets…`). 이 모양은 링크 자체로는 받지 않는다. 구글 로그인(`accounts.google.com`)으로 넘겨주면 비공개 파일로 보고 멈춘다. 그 밖은 따라가지 않는다. 최대 5번.
- 쿠키·자격 증명·Referer 를 보내지 않는다. 매 단계 새 요청이며, 앞 단계에서 구글이 준 쿠키는 버린다. 환경 변수의 프록시·`.netrc` 를 읽지 않는다.
- 호스트 이름이 가리키는 주소가 모두 공인 주소여야 하고, 실제로 연결된 주소도 본문을 읽기 전에 다시 확인한다. 내부·루프백·링크 로컬(169.254.169.254 포함)·예약 주소로는 가지 않는다.
- 크기 상한은 올리기와 같다: 원문 64 MB, 시약 표 5 MB. 받는 중 넘으면 끊고 아무것도 남기지 않는다. 전체 60 초(연결 10 초, 읽기 20 초).
- 형식은 받은 바이트로 정한다(구글의 Content-Type·파일 이름은 쓰지 않는다).
- 로그인해야 열리는 비공개 파일은 받지 않는다. 구글 계정 연결(OAuth)은 사람이 정할 때까지 하지 않는다.

**실패 응답.** `detail` 이 `{"code": …, "message": …}` 이고, `message` 는 화면에 그대로 보여 줄 서버 문장이다. 받은 파일을 올리기 경로가 거절하면(`invalid_pdf`, `docx_conversion_unavailable` 등) 올리기와 같은 응답이 그대로 온다(5절 표).

| 코드 | HTTP | 서버 문장 |
|---|---|---|
| `link_private` | 403 | 이 파일은 공유 설정이 '링크가 있는 모든 사용자'가 아니라서 가져올 수 없어요. 공유 설정을 바꾸거나 파일을 내려받아 올려 주세요. |
| `link_too_large` | 413 | 파일이 너무 커서 가져올 수 없어요. 64MB 까지 가져올 수 있어요. 파일 크기를 줄이거나 나눠서 올려 주세요. (시약 표: "시약 표가 … 5MB 까지 …") |
| `link_unsupported_format` | 415 | 이 파일은 PDF·Word 문서·사진(JPG·PNG·HEIC)이 아니라서 가져올 수 없어요. 이 형식으로 저장해 링크를 넣거나 파일을 올려 주세요. |
| `link_not_google_drive` | 422 | 구글 드라이브·구글 문서·구글 시트의 링크만 가져올 수 있어요. 주소가 https://drive.google.com 이나 https://docs.google.com 으로 시작하는지 확인해 주세요. |
| `link_not_a_file` | 422 | 이 링크는 파일 하나를 가리키지 않아요. 폴더라면 폴더 안의 파일을 열고 그 파일의 공유 링크를 넣어 주세요. |
| `link_sheet_needs_protocol` | 422 | 구글 시트는 프로토콜에 시약 표로만 붙일 수 있어요. 프로토콜을 연 뒤 시약 표 자리에서 링크를 넣어 주세요. |
| `link_not_a_reagent_table` | 415 | 시약 표로는 구글 시트나 엑셀(.xlsx) 파일만 붙일 수 있어요. 링크가 시트를 가리키는지 확인해 주세요. |
| `link_not_found` | 404 | 이 링크의 파일을 찾지 못했어요. 주소가 맞는지 확인하고, 공유 설정이 '링크가 있는 모든 사용자'인지도 확인해 주세요. |
| `link_redirect_refused` | 502 | 구글 드라이브가 허용하지 않은 주소로 넘겨줘서 가져오지 않았어요. 파일을 내려받아 올려 주세요. |
| `link_fetch_failed` | 502 | 구글 드라이브에서 파일을 받지 못했어요. 잠시 뒤 다시 하거나 파일을 내려받아 올려 주세요. |
| `link_timeout` | 504 | 구글 드라이브가 제때 답하지 않아 가져오지 못했어요. 잠시 뒤 다시 해 주세요. |
| `link_sign_in_required` | 401 | 로그인한 뒤에 링크로 가져올 수 있어요. |
