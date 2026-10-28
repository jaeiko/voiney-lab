# README 화면 자료

**시험용 가짜 자료로 찍은 화면.** 실제로 빌드한 새 화면(`/app/`)을 Playwright로 조작해 캡처했다. 촬영 기준 코드는 `689fb0d`, 촬영일은 2026-10-28 UTC다. 외부 모델·OCR·STT·TTS·웹 호출은 0회다.

- 가로 태블릿: Chromium 터치 화면 1180 × 820, 바이오·단백질 처리 견본.
- 휴대폰: Chromium 터치 화면 412 × 915, 화학·토양 분석 견본.
- 실제 하드웨어 시험이 아닌 브라우저 화면 크기 재현이다. 원문 페이지·분석·확인 카드·음성 사건·타이머·기록·노트 응답은 가짜 서버가 제공한다. 마이크는 Chromium의 가짜 장치를 쓴다.
- 기존 화면 시험의 단계 문장·값과 합성 관찰을 재사용한다. 견본은 실제 실행할 프로토콜이 아니며, 공급자 분석이나 노트 작성 품질을 입증하지 않는다.
- 원문은 SVG로 만든 시험용 페이지다. 실제 PDF·연구실 문서·음성·사용자 정보는 넣지 않았다. 이 SVG는 캡처용 응답이며 제품의 원문 표시 방식을 바꾸지 않는다.
- 중간에 멈춘 실험을 촬영했다. 끝 화면과 노트도 멈춘 상태이며 전체 완료를 주장하지 않는다.

| 장면 | 가로 태블릿 | 휴대폰 |
|---|---|---|
| 올리기 | [tablet-01-upload.webp](tablet-01-upload.webp) | [phone-01-upload.webp](phone-01-upload.webp) |
| 쪽 고르기 | [tablet-02-pages.webp](tablet-02-pages.webp) | [phone-02-pages.webp](phone-02-pages.webp) |
| 확인 카드 | [tablet-03-review.webp](tablet-03-review.webp) | [phone-03-review.webp](phone-03-review.webp) |
| 시작 흐름 · 핵심 값 | [tablet-04-start.webp](tablet-04-start.webp) | [phone-04-start.webp](phone-04-start.webp) |
| 실험 · 한국어·타이머·기록 | [tablet-05-run.webp](tablet-05-run.webp) | [phone-05-run.webp](phone-05-run.webp) |
| 끝 화면 | [tablet-06-end.webp](tablet-06-end.webp) | [phone-06-end.webp](phone-06-end.webp) |
| 실험 노트 | [tablet-07-notebook.webp](tablet-07-notebook.webp) | [phone-07-notebook.webp](phone-07-notebook.webp) |

[flow.gif](flow.gif)은 위 태블릿 7장면을 장면당 2.2초로 연결한 무음 요약이다. 실제 동작 속도나 음성 지연을 표현하지 않는다. 정지 사진은 각 300,000바이트 이하, GIF는 3,000,000바이트 이하다. 원본 PNG는 보고서 폴더에만 둔다. WebP 압축 외 화면을 덧그리거나 제품 CSS를 바꾸지 않는다.

## 다시 찍기

저장소 루트에서 실행한다. 먼저 Python 가상환경과 Node.js 22 이상, 잠금 파일의 npm 의존성, Playwright Chromium을 준비한다. 의존성을 이미 받은 환경에서는 `npm ci --offline --ignore-scripts --no-audit --no-fund`를 루트와 `web/`에서 각각 실행할 수 있다.

```bash
source .venv/bin/activate
npm --prefix web run build
README_CAPTURE=1 ./node_modules/.bin/playwright test --config=tests/e2e/readme.config.ts
python tests/e2e/readme_media.py
```

촬영 설정은 127.0.0.1:8046에서 정적 파일만 제공한다. 제품 실행기·`.env`·런타임 저장소를 사용하지 않는다. 외부 HTTP는 차단하고 HTTP API와 음성 WebSocket은 가짜로 응답한다. 다른 서버가 이 포트를 쓰면 재사용하지 않고 실패한다.

원본 저장 위치는 기본 `~/reports/readme-capture/`다. `README_RAW_DIR`로 촬영 위치를 바꿨으면 압축 명령에도 `--raw-dir`로 같은 경로를 준다. `README_CAPTURE=1`을 주지 않은 기본 CI에서는 사진용 걷기를 건너뛴다.

## 실제 음성 영상 넣기

1. 사람이 실제 음성 시연을 촬영하고 비공개 자료·계정·키가 보이지 않는지 확인한다.
2. GitHub 이슈나 PR 편집기에 영상을 올려 생성된 링크를 복사하거나 YouTube 공유 링크를 준비한다.
3. [README](../../../README.md)의 “실제 음성 시연 영상 — 준비 중” 인용문을 영상 링크로 교체한다. 그 자리의 HTML 주석에 같은 순서가 있다.
4. 촬영일·기기·음성 및 서버 조건을 적고 GitHub에서 링크와 재생을 확인한다. 이 GIF는 음성 시연 영상으로 쓰지 않는다.
