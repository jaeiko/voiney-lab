# Git 작업 규칙

Voiney Lab 팀(김재준, 최수진, 강이수)과 코딩 에이전트가 함께 지키는 규칙이다.

## 브랜치

| 브랜치 | 용도 | 규칙 |
|---|---|---|
| `main` | 파일럿 연구실에 배포하는 안정 버전 | 직접 push 금지. `dev` 에서 PR 로만 합친다 |
| `dev` | 매일 작업을 합치는 곳 | 기능 브랜치는 여기서 따고, 여기로 합친다 |
| `feature/<이름>-<작업>` | 기능 하나 | 예: `feature/jaejun-keyterms`, `feature/sujin-mobile-step` |
| `fix/<이름>-<내용>` | 버그 수정 하나 | 예: `fix/sujin-ci-missing-pdf` |
| `refactor/<이름>-<내용>` | 동작을 바꾸지 않는 정리 | 예: `refactor/jaejun-comment-cleanup` |
| `docs/<이름>-<내용>` | 문서만 | 코드를 바꾸지 않는다 |

- 브랜치 하나에는 작업 하나만 담는다.
- CI(`.github/workflows/ci.yml`)는 `main`·`dev` 로의 push 와 `dev`·`main` 으로의 PR 에서 돈다. 기능·수정·정리·문서 브랜치는 push 만으로 돌지 않고 PR 을 열면 한 번 돈다. staging 배포는 성공한 `dev` push CI 뒤에만 돈다.
- 합친 브랜치는 바로 삭제한다. 삭제는 사람이 한다.

## 작업 흐름

1. `git checkout dev && git pull` 로 최신 dev 를 받는다.
2. `git checkout -b feature/<이름>-<작업>` 으로 브랜치를 만든다.
3. 작업하고 커밋한다.
4. 아래 "PR 전 확인"을 통과시킨다.
5. `dev` 로 PR 을 연다. 가능하면 다른 팀원 한 명이 보고 합친다.
   코딩 에이전트가 직접 합쳐도 되는 조건은 아래 "코딩 에이전트" 절에 있다.
6. 파일럿 배포 시점에만 `dev` → `main` PR 을 열고, 합친 뒤 태그를 붙인다.
   예: `v0.1-pilot1`

## 커밋

- 커밋 하나에 한 가지 일만 담는다.
- 메시지 첫 줄에 무엇을 바꿨는지 쓴다. 예: `Derive STT keyterms from the active protocol`
- 테스트 수정과 코드 수정은 가능하면 다른 커밋으로 나눈다.

## PR 전 확인

`AGENTS.md` 의 "Verification" 명령이 통과해야 한다(플래그를 붙인 `python -m pytest -q`, `scripts/replay_turns.py`, `compileall`, `git diff --check`). 플래그는 셸에 남은 설정 값이 결과를 바꾸지 않게 끈다.

- PR 설명에 통과 수를 적고, 라이선스 PDF 가 있는 환경(조건 A)인지 없는 환경(조건 B)인지 밝힌다. 두 조건의 뜻은 `AGENTS.md` 에 있다.
- 문서를 고쳤으면 `tests/test_docs_consistency.py` 가 링크·설정 이름·경로·스크립트 옵션을 확인한다.
- 통과 수가 기준보다 줄었으면 PR 을 열지 않는다.

## 파일 담당

같은 파일을 두 사람이 동시에 고치지 않는다. 담당이 아닌 파일을 고쳐야 하면 먼저 담당자에게 말한다.

| 영역 | 담당 |
|---|---|
| `server.py`, `curated_protocol.py`, 음성 파이프라인, 프로토콜 분석·원문 근거 검증 | 김재준 |
| `static/` 화면, `protocol_ocr.py`, 음성 평가 도구, 테스트 환경(CI) | 최수진 |
| 인터뷰·파일럿 기록, 현장 녹음 데이터 | 강이수 |

## 금지

- `main` 에 직접 push
- `git push --force`, `git reset --hard` 로 공유 브랜치의 기록 지우기
- `.env`, `data/runtime/`, API 키, 녹음 원본을 커밋하기
- 기존 테스트를 통과시키려고 테스트를 고치기 (전제가 바뀐 경우는 AGENTS.md 절차를 따른다)

## 코딩 에이전트(Codex, Claude Code 등)

코딩 에이전트가 지키는 작업 규칙 — `dev` 에서 사람이 만든 브랜치, 작업 전 실패 목록 재기, 스스로 합쳐도 되는 네 가지 조건, 사람만 하는 일, 커밋·PR 본문의 AI 도구 표시 금지와 그 `grep` 확인, 보고서 형식 — 은 [`AGENTS.md`](../AGENTS.md) 의 "How to work here" 한 곳에 있다. Claude Code 는 `CLAUDE.md` 의 `@AGENTS.md` 한 줄로 그것을 읽는다.

## PR 본문

한국어로 쓴다. 다른 팀원이 이 PR 만 읽고 이어서 작업할 수 있게 쉬운 말로 쓴다. 항목 순서:

1. 왜 고쳤나
2. 바뀐 것 (커밋별 표)
3. 바뀌지 않은 것
4. 테스트 (환경 — 조건 A·B 중 어느 것인지, 숫자, 새 테스트 파일)
5. 사용자 입장에서 달라지는 것
6. 알려진 한계·다음 할 일 (사람이 할 일 포함)
7. 담당과 핵심 함수 위치

## 충돌할 때

이 문서와 `AGENTS.md` 가 충돌하면 멈추고 사람에게 묻는다.
