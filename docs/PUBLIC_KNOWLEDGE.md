# 공개 지식 묶음과 Moss 검색 — 운영 안내

실험 중 질문·문제에 근거 있는 참고를 주기 위해, 라이선스를 확인한 **공개**
실험·안전·과학 자료만 모아 서버 안에서 검색한다(사람 결정 2026-10-24, 줄
KNOW). 연구실 자료는 넣지 않는다. 동작과 코드 위치는
[`FEATURES.md`](FEATURES.md) F20, 구조는
[`../.agent/architecture.md`](../.agent/architecture.md) 5절, 결정과 날짜는
[`DECISIONS.md`](DECISIONS.md) 4절.

## 1. 무엇이 들어가나

- **출처 목록**: [`../data/public_knowledge/sources.toml`](../data/public_knowledge/sources.toml).
  출처마다 라이선스, 라이선스 근거 URL, 확인 날짜, 이용 조건(속도 제한,
  robots.txt), 분야, 언어, 등급을 적는다. 퍼블릭 도메인·CC0·CC BY·CC BY-SA 만
  읽힌다; 라이선스 글에 NC·ND·"all rights reserved" 가 있으면 파이프라인이
  출처 이름을 들어 거부한다. 후보였으나 뺀 출처는 같은 파일의 `[[excluded]]`
  에 이유와 근거 URL 로 남긴다.
- **등급**(추가 결정 3): `A` 공공기관·표준·심사 논문, `B` 공개 교재, `C`
  위키백과. 답은 높은 등급을 먼저 쓰고 위키백과만 근거면 그렇게 밝힌다.
- **목록 파일**: `wikipedia_titles.en.txt`·`wikipedia_titles.ko.txt`(문서
  제목, `# 분야` 줄로 분야 구분), `pubchem_compounds.txt`(시약 이름, 필요하면
  `이름<TAB>CID` 또는 `이름<TAB>enwiki:문서제목`), `glossary_terms.txt`(용어집에
  더할 위키데이터 항목). 한 줄 더하면 다음 실행에 들어간다.
- **조각**: 최대 1,000자. 필드는 tier `public`, 제목, 본문, 위치(섹션·쪽·CID),
  URL, 라이선스, 언어, 분야, 출처 id·이름, 등급, 출처 버전, 받은 날짜.
- **용어집**: 위키데이터의 한국어·영어 이름(CC0)과 한국어 위키백과 넘겨주기
  제목으로 만든 `data/public_knowledge/glossary.json`. 서버가 한국어 질문의
  낱말(조사를 뗀 뒤)에 영어 이름을 붙여 영어 자료와 만나게 한다. 조각은
  나오지 않는다.

## 2. 명령

```bash
source .venv/bin/activate
# 바뀐 것만 받아 묶음을 다시 만든다 (처음이면 전부). 원본과 묶음은
# data/runtime/public-knowledge/ (커밋하지 않음).
python -u scripts/public_knowledge_pipeline.py collect
python -u scripts/public_knowledge_pipeline.py collect --only wikipedia-ko,pubchem
python -u scripts/public_knowledge_pipeline.py collect --full      # 조건 없이 전부 다시
python scripts/public_knowledge_pipeline.py status                 # 출처별 조각 수·용량·예상 메모리
python scripts/public_knowledge_pipeline.py glossary               # 용어집을 data/public_knowledge/glossary.json 으로
# Moss 에 올린다. 키는 셸의 MOSS_PROJECT_ID·MOSS_PROJECT_KEY 또는 --env-file (값은 출력하지 않음).
python scripts/public_knowledge_pipeline.py upload --index voiney-public-knowledge --dry-run
python scripts/public_knowledge_pipeline.py upload --index voiney-public-knowledge --env-file .env
# 질문 세트로 FTS5 기준선과 Moss 를 잰다 (recall@k, 지연 p50/p95, 불러오기 시간, RSS 증가).
python scripts/public_knowledge_pipeline.py evaluate --backend fts5 --questions data/fixtures/evaluation/public_knowledge_questions.json
python scripts/public_knowledge_pipeline.py evaluate --backend moss --index voiney-public-knowledge --questions data/fixtures/evaluation/public_knowledge_questions.json --env-file .env --out /tmp/moss_eval.json
```

`collect` 는 출처마다 조건부 요청(ETag·Last-Modified, 위키백과 개정 id,
PubChem·위키데이터는 30일 간격)으로 바뀐 것만 받고, 원본 캐시에서 묶음
전체를 다시 만든다(파서를 고치면 모두에 적용, 조각 id 는 그대로). 실패한
출처·항목은 `bundle/failures.jsonl` 에 적고 건너뛴다. 묶음은
`bundle/chunks.jsonl`, `bundle/manifest.json`(출처별 조각 수·글자 수·요청
수), `bundle/glossary.json`.

이용 규칙: 모든 요청에 연락처가 든 User-Agent, 출처마다 정한 초당 요청 수,
robots.txt(막힌 경로는 실패로 기록, `Crawl-delay` 는 그대로 따름; 위키미디어
API 는 API 예절 문서를 따름), MediaWiki `maxlag` 과 PubChem 의
`X-Throttling-Control` 머리글에 맞춰 기다림. PubChem 의 이름 → CID 조회(PUG
REST)는 robots.txt 가 막으므로 위키데이터의 PubChem CID(P662)나 목록의 CID
를 쓰고 PUG View 만 읽는다.

## 3. 서버

- 설정: `VOINEY_LAB_PUBLIC_KNOWLEDGE_ENABLED`(기본 false),
  `VOINEY_LAB_PUBLIC_KNOWLEDGE_INDEX`(기본 `voiney-public-knowledge`),
  `MOSS_PROJECT_ID`, `MOSS_PROJECT_KEY`(`.env` 에만). 패키지 `moss==1.15.1`
  (라이선스 PolyForm Shield 1.0.0; 경쟁 제품 외 모든 용도에 쓰고 고칠 수
  있고, 사본에 고지를 함께 둔다).
- 시작할 때 색인 하나를 불러오고(`server.py` `_start_public_knowledge`),
  질의는 프로세스 안에서 돈다. 디스크 캐시는 프로토콜 카탈로그 폴더 옆
  `public-knowledge-cache/`. 켰는데 키가 없거나 불러오기에 실패하면 이
  등급만 꺼지고 이유는 설정 이름이다; `/readyz` 의 `public_knowledge` 와
  `ready.research_capabilities.public_knowledge` 가 `disabled`·`enabled`·
  `unavailable` 을 알린다. 다른 공급자로 넘어가지 않는다.
- 답의 규칙(결정 6)은 `answer_checks.public_reference_violations` 가 지킨다:
  "공개 자료 · 일반 참고 — <출처 이름>" 표시(서버가 붙임), 인용한 조각에 있는
  수치만, 지시가 아니라 참고 사실, 160자 이내, 원문 60자 이상 그대로 읽지
  않음, "계속해도 돼요"·원문 값 바꾸기·위험 허가·상태 변경 주장·URL 금지.
- 실험 중 문제 도움(줄 HELP) ③ 단계와의 연결은 줄 HELP 가 `dev` 에 병합된
  뒤에 한다. 그 전에는 서버가 색인을 불러오고 상태만 알린다.

## 4. 한도와 확대

- Moss 무료 단계(docs.moss.dev 가격표, 2026-10-24 읽음): 프로젝트 1, 색인 3,
  저장 500 MB, 올리기 50 MB/월, 내려받기 10 GB/월, 세션 60분/월. 로컬 질의는
  세지 않는다.
- 메모리(Moss 문서의 측정치): 임베딩 모델 149 MiB(프로세스당 한 번), SDK
  37 MiB, 조각당 약 7 KB(한글은 약 2배), 불러오는 동안 색인 크기의 약 2배가
  더 든다. `status` 와 `upload --dry-run` 이 이 식으로 어림한다; 실측은 줄
  보고서(`~/reports/lane_know_report.md`)의 표.
- 넓히는 순서: 목록 파일에 제목·시약을 더하거나 `sources.toml` 에 출처를
  더하고 `collect` → `status` 로 조각 수·예상 메모리를 보고 → `upload`. 한도를
  넘는 확대는 사람이 정한다.

## 5. 하지 않는 것

- 연구실 자료·로그인이 필요한 자료·라이선스를 확인하지 못한 자료는 넣지
  않는다. 공개 자료는 참고일 뿐 원문의 값·조건·안전 판단을 바꾸지 못한다.
- 서버는 모델을 쓰지 않고 검색한다; 바꿔 말하기(`paraphrase_public_reference`)는
  보조 설명 역할이 켜졌을 때만, 그 결과도 같은 검사를 거친다.
- 완전성 보증이 아니다: 들어 있지 않은 자료는 찾지 못한다.
