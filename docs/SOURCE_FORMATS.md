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
- 같은 .docx 를 다시 올리면 원본 체크섬으로 앞서 만든 프로토콜을 찾고 다시 바꾸지 않는다.
- **없어도 서버는 시작한다.** 서버는 시작 로그에 경고를 남기고, 파일럿 실행기(`scripts/run_pilot.sh`)는 시작 점검에 `[WARN]` 을 찍되 시작을 막지 않는다(사람 결정 2026-10-14). 막히는 것은 Word 원문을 올리는 일뿐이다.

## 2. HEIC 사진 — 선택 설치

아이폰 기본 사진 형식인 HEIC 는 선택 의존성 `pillow-heif` 로 읽는다. 기본 설치(`pip install -e '.[test]'`)에는 들어 있지 않다.

```bash
pip install -e '.[heic]'                                   # pillow-heif 1.8.1
python -c "import pillow_heif; print(pillow_heif.__version__)"
curl -s http://127.0.0.1:8000/readyz                        # "heic_photos_available": true
```

서버를 다시 시작해야 반영된다(시작 로그 `protocol.source_formats heic=available`). 지우려면 `pip uninstall pillow-heif`.

**왜 선택인가 — 라이선스.** `pillow-heif` 의 소스는 BSD-3-Clause 이지만, PyPI 바퀴(wheel)는 HEIC 를 푸는 `libheif`·`libde265`(LGPLv3)와 HEIC 를 만드는 `x265`(GPLv2)를 함께 싣고 있어 **바퀴 전체가 GPLv2** 로 배포된다(설치 뒤 `pillow_heif-1.8.1.dist-info/licenses/LICENSES_bundled.txt` 첫 줄: "License for pillow-heif binary wheels: GPLv2"). 서버 안에서 쓰는 것과 서버를 남에게 배포하는 것의 의무가 다를 수 있으므로, 넣을지는 조직의 라이선스 담당이 정한다. 이 저장소는 그 결정 전까지 기본으로 넣지 않는다.

**설치하지 않은 서버에서.** HEIC 를 올리면 `503 heic_support_unavailable` 로 거절되고 아무것도 등록되지 않는다. 사람에게는 이렇게 안내한다:

- 아이폰: 설정 → 카메라 → 포맷 → **"높은 호환성"** 을 고르면 이후 사진이 JPEG 로 찍힌다.
- 이미 찍은 HEIC: 사진 앱에서 "공유 → 옵션 → 자동"으로 보내거나 PC 에서 JPEG 로 바꿔 올린다.

## 3. 사진(JPG·PNG·HEIC) 공통

- 사진 한 장이 한 쪽이다. EXIF 방향대로 바로 세우고, 투명한 부분은 흰 바탕으로 깐다. 1억 픽셀을 넘으면 `413 protocol_image_too_large`, 형식이 `Content-Type` 과 다르면 `422 invalid_source_file`.
- 글자 층이 없으므로 업로드 때 OCR(`VOINEY_LAB_OCR_PROVIDERS`)이 읽고 이어서 분석한다. OCR 이 설정되지 않았으면 원문만 저장되고 막힌 이유가 보인다.
- OCR 이 읽은 수치(숫자 + 같은 줄의 단위, 예 "0.5 mL", "12,000 ×g")는 `GET /api/protocols/{id}/ocr` 의 `pages[].value_boxes`(글과 상자)와 `render_dpi` 로 남는다. 손글씨에서 읽은 수치는 사람이 원본 이미지 조각과 대조해 확인해야 한다(검토 탭: 줄 RV).
- 손글씨 엔진 비교(줄 AN 보고서): 숫자+단위 정확 일치 CLOVA 58%, Google Vision 문서 모드 70%, Gemini 비전 81%(단, Gemini 는 글을 고쳐 써서 원문 그대로 읽는 OCR 로는 쓰지 않음).

## 4. 시약 표(.xlsx)

```bash
curl -s -X POST 'http://127.0.0.1:8000/api/protocols/<protocol_id>/reagent-tables?filename=reagents.xlsx' \
  -H 'Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' \
  --data-binary @reagents.xlsx
curl -s http://127.0.0.1:8000/api/protocols/<protocol_id>/reagent-tables
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
