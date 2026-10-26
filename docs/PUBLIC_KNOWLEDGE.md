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
  제목으로 만든 `data/public_knowledge/glossary.json`, 그리고 사람이 읽고 고치는
  손 용어집 [`../data/public_knowledge/glossary_words.txt`](../data/public_knowledge/glossary_words.txt)
  (줄 KNOW2, 사람 결정 2026-10-26: 줄 KAUDIT 가 찾은 빈칸 낱말 전부와 같은 꼴의
  실험실 기본 낱말). 손 용어집은 "한국어 꼴; 다른 꼴 = English; other" 줄을
  [장비]·[물질]·[소재]·[안전]·[기법]·[현상]·[일반] 절로 나눈다: 앞의 넷은
  단계의 핵심 낱말이 될 수 있는 물건, [현상]은 무엇이 잘못됐는지(문제 질의의 문제
  낱말, "굳지 않" → polymerization), [일반]은 겹쳐도 뜻 낱말로 세지 않는 낱말.
  같은 한국어 낱말이면 손 용어집의 영어 이름이 먼저다. 서버가 한국어 질문의
  낱말(조사를 뗀 뒤)에 영어 이름을 붙여 영어 자료와 만나게 한다; 고친 뒤에는
  서버를 다시 켜면 질의 쪽에 들어가고, 조각 제목 옆 한국어 별칭은 다음 `upload`
  때 들어간다. 읽지 못한 줄은 건너뛰고 테스트가 0줄인지 확인한다. 조각은
  나오지 않는다.

## 2. 명령

```bash
source .venv/bin/activate
# 바뀐 것만 받아 묶음을 다시 만든다 (처음이면 전부). 원본과 묶음은
# data/runtime/public-knowledge/ (커밋하지 않음).
python -u scripts/public_knowledge_pipeline.py collect
python -u scripts/public_knowledge_pipeline.py collect --only wikipedia-ko,pubchem
python -u scripts/public_knowledge_pipeline.py collect --full      # 조건 없이 전부 다시
python -u scripts/public_knowledge_pipeline.py collect --offline   # 요청 없이 받아 둔 원본만으로 다시 (파서를 고친 뒤)
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
전체를 다시 만든다(파서를 고치면 모두에 적용, 조각 id 는 그대로). 같은
자료는 최신 것만 남는다: 바뀐 문서는 새 사본이 옛 사본을 대신하고, 같은
글이 두 출처에 있으면 출처 버전(날짜)이나 받은 날짜가 늦은 쪽을 둔다(사람
결정 2026-10-24). 실패한 출처·항목은 `bundle/failures.jsonl` 에 적고
건너뛴다. 묶음은
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
- 실험 중 문제 도움(줄 HELP)의 ③ 단계에 연결돼 있다(2026-10-25). 줄 KNOW2
  (사람 결정 2026-10-26)부터:
  - **질의**: 사람의 말 전체가 아니라 문제 낱말(거품·침전·안 떨어짐 등)과
    이름(사람이 말한 것; 없을 때만 지금 단계 원문이 다루는 물질·장비)으로
    만든다(`public_knowledge.problem_query`). 둘 중 하나가 없으면 묻지 않는다.
  - **관련도 하한**: 조각의 한 문장 안에 이름과(제목이 그 이름인 기록은 제목으로
    충분), 그 이름이 아닌 낱말로 된 문제 낱말이 함께 있고, 원인·효과·실패·위험을
    말하며(정의·설명만인 조각은 아님),
    색인 점수가 `SCORE_FLOOR` 이상이어야 붙는다(`problem_relevance`). 넘는
    조각이 없으면 공개 자료 층을 붙이지 않는다.
  - **순서**: 색인 점수, 등급은 점수가 비슷할 때(0.03 안)만 앞세운다.
  - **말**: "공개 자료에 관련 내용이 있어요."만. 조각의 문장·정의는 읽지 않고
    바꿔 말하지도 않는다; 내용은 화면에 표시·출처·라이선스와 함께.
  동작은 [`FEATURES.md`](FEATURES.md) F11·F20.

## 4. 한도와 확대

- Moss 무료 단계(docs.moss.dev 가격표, 2026-10-24 읽음): 프로젝트 1, 색인 3,
  저장 500 MB, 올리기 50 MB/월, 내려받기 10 GB/월, 세션 60분/월. 로컬 질의는
  세지 않는다.
- 메모리(Moss 문서의 측정치): 임베딩 모델 149 MiB(프로세스당 한 번), SDK
  37 MiB, 조각당 약 7 KB(한글은 약 2배), 불러오는 동안 색인 크기의 약 2배가
  더 든다. `status` 와 `upload --dry-run` 이 이 식으로 어림한다; 실측은 줄
  보고서(`~/reports/lane_know_report.md`)의 표.
- 서버 메모리 한도(사람 확인 2026-10-24): 공개 지식을 켰을 때 서버 프로세스의
  RSS 증가는 정상 상태 768 MiB, 불러오는 동안 1.2 GiB 를 넘지 않게 둔다. v1
  (23,975 조각) 실측은 정상 +511 MiB · 최고 +735 MiB.
- 넓히는 순서: 목록 파일에 제목·시약을 더하거나 `sources.toml` 에 출처를
  더하고 `collect` → `status` 로 조각 수·예상 메모리를 보고 → `upload`. 다음
  수집 규모와 다국어 임베딩은 사람이 정한다(한도를 넘는 확대도 같다).

## 5. 덮는 범위(10/25 점검)

v1 묶음이 젖은 실험실 전체(화학·바이오·소재·배터리)를 얼마나 덮는지 줄
KAUDIT 가 쟀다. 새 수집·올리기는 없었다.

- **표**: [`../data/fixtures/evaluation/public_knowledge_coverage.json`](../data/fixtures/evaluation/public_knowledge_coverage.json)
  — 7분야 88칸(안전 16, 장비 29, 소재·배터리 7, 바이오 기법 12, 화학 기법 9,
  시약 사실 7, 문제 해결 8), 칸마다 실험 중에 하는 질문 4–5개(359문: 한국어
  271·영어 88). 질문은 묶음을 보기 전에 썼고, 답이 되는 조각(정답)을 읽어서
  골랐다; 262문은 정답 조각이 있고 97문은 없다(이유를 적음). 칸의 판정:
  ✓ = 질문의 2/3 이상이 상위 5개 안에서 정답 조각을 찾음, ✗ = 정답 조각이
  묶음에 하나도 없음, △ = 그 사이.
- **도구**: `scripts/public_knowledge_audit.py`. 서버와 같은 검색
  (`PublicKnowledgeSource`, 용어집 확장, alpha 0.5, 상위 5개)으로 잰다.

```bash
python scripts/public_knowledge_audit.py inventory --out inventory.json --out-md inventory.md
python scripts/public_knowledge_audit.py map --backend fts5 --out map.json      # 오프라인 FTS5 (서버 순위가 아님)
# Moss: 사람이 터미널에서. 키는 셸 먼저, 없으면 --env-file 에서 세 이름만 (값은 어디에도 적지 않음).
python scripts/public_knowledge_audit.py moss-run --env-file .env --protocol-data-dir <카탈로그 사본> --out-dir <결과 폴더>
```

`moss-run` 은 색인을 한 번 불러와 덮는 범위 질문과 줄 HELP 평가 세트의 도움
턴(STT·TTS·원인 모델은 가짜)을 돌리고 JSON 만 쓴다(`map.json`, `help.json`,
`run.json`); 끝나면 "끝: 질문 N개, 파일 M개" 한 줄만 낸다.

**결과 (Moss, 2026-10-26 00:17 UTC, 색인 `voiney-public-knowledge` 23,975 조각,
사람이 실행; 불러오기 10.1 s, 질의 p50 22.5 ms, 프로세스 RSS 최고 919 MiB)**:

| 분야 | 칸 ✓ · △ · ✗ | recall@5 | 한국어 질문 recall@5 |
|---|---|---|---|
| 안전 | 0 · 16 · 0 | 0.188 | 3/48 |
| 장비 | 1 · 26 · 2 | 0.198 | 9/87 |
| 소재·배터리 | 1 · 6 · 0 | 0.250 | 5/21 |
| 바이오 기법 | 1 · 11 · 0 | 0.229 | 5/36 |
| 화학 기법 | 1 · 8 · 0 | 0.278 | 8/27 |
| 시약 사실 | 1 · 6 · 0 | 0.400 | 10/28 |
| 문제 해결 | 0 · 8 · 0 | 0.031 | 1/24 |
| **합계 88칸** | **5 · 81 · 2** | **0.217** (정답 있는 질문 0.298) | 0.151 (정답 있는 질문 0.214; 영어 0.529) |

- 못 찾은 281문 중 184문은 정답 조각이 묶음에 있는데 상위 5개에 없었고,
  97문은 묶음에 답이 없다. 정답 있는 한국어 질문을 놓친 151문 중 133문에
  용어집에 없는 낱말이 있다(흄후드·시료·튜브·버퍼·배지·전기영동·폐액·보안경·
  콜드트랩 등; 용어집에는 퓸후드·겔 전기영동처럼 다른 꼴만 있다).
- 33칸은 정답 조각이 위키백과뿐이다(장비 대부분, 화학 기법, 문제 해결 다섯 칸).
  ✗ 두 칸은 히팅블록·스피드백.
- 도움(줄 HELP 세트, 도움을 기대하는 76행): 공개 조각이 붙은 행 33(0.434),
  말로 한 짧은 사실 5·화면 안내 28, 공개 자료 규칙 위반 0. 붙은 조각을 읽은
  줄의 판단은 맞음 1 · 애매 12 · 엉뚱 20 — 대부분 "cell"·"Gel"·"Ab" 같은 낱말
  하나만 겹친 교재·위키 조각이다. 1행(H46)은 위키 수식 조각의 한 글자 줄("p")
  이 도움 검사(`problem_help_violations`)에서 "p.3" 의 p 를 지워 숫자가 남아
  도움 전체가 기본 문장으로 바뀌었다.
- 재고(200조각 무작위 표본, 줄이 읽고 분류): 실험대에서 쓸 만함 39.5% ·
  배경 25.5% · 실험과 무관 22% · 깨짐 13%. OpenStax 두 교재(8,030조각)는 표본
  66 중 1 만 쓸 만했다.
- 오프라인 FTS5 로 같은 표를 재면 ✓3 · △83 · ✗2, recall@5 0.192(참고값; 서버
  순위가 아님). 줄 KNOW 의 0.638 은 다른 질문 세트(130문)의 값이다.

다음 수집 범위·순서와 다국어 임베딩은 사람이 정한다; 후보(라이선스 확인한
출처·조각 수·메모리 어림)는 줄 보고서(`~/reports/lane_kaudit_report.md`).

## 6. 하지 않는 것

- 연구실 자료·로그인이 필요한 자료·라이선스를 확인하지 못한 자료는 넣지
  않는다. 공개 자료는 참고일 뿐 원문의 값·조건·안전 판단을 바꾸지 못한다.
- 서버는 모델을 쓰지 않고 검색하고, 공개 조각을 모델로 바꿔 말하지 않는다(줄
  KNOW2 에서 바꿔 말하기를 뺐다).
- 완전성 보증이 아니다: 들어 있지 않은 자료는 찾지 못한다.
