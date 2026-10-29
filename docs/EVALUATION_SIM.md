# 합성 음성 회귀 시험대

이 시험대는 정해진 문장의 이해와 상태 보존을 실제 서버 경계에서 잰다.
유한한 시험 묶음의 점수이며, 모든 프로토콜의 완벽한 안내나 현장 성능을 증명하지 않는다.
외부 공급자는 기본으로 호출하지 않는다. 제품 서버·라우터·상태 기계의 동작은 바꾸지 않는다.

## 오프라인: 모든 줄의 고치기 전·후 점수

개선에 앞서 `--split development`와 `--split holdout`으로 각각 잰다.
고정 씨앗은 1027, 시험용 비중은 최소 30%다. 같은 발화는 프로토콜이 달라도
한쪽에만 들어간다. 프로토콜·갈래와 두 문장 이상인 교차 묶음에 시험용 할당량을
둔다. 한 문장만 반복되는 갈래는 한쪽에만 속할 수 있다. 필터·문장 수 제한은
전체 분할을 고정한 뒤 적용한다. 분할 지문과 실제 두 묶음의 수가 결과에 남는다.
시험용 결과는 개별 행을 저장하지 않고 프로토콜·갈래·교차 점수만 내므로
문장별 퇴보 비교는 개발용에만 가능하다. 시험용 문장을 보고 규칙을 맞추지 않는다.

```bash
python scripts/sim/run.py --split development --output ~/reports/sim/development-before.json
python scripts/sim/run.py --split holdout --output ~/reports/sim/holdout-before.json
python scripts/sim/run.py --emergency-matrix --split development --output ~/reports/sim/emergency-development.json
python scripts/sim/run.py --emergency-matrix --split holdout --output ~/reports/sim/emergency-holdout.json
```

응급 별도 묶음은 눈·피부 노출, 흡입·호흡, 불·연기, 대량 쏟음, 베임·출혈,
쓰러짐·의식, 가스·냄새, 화상, 감전, 삼킴의 합성 발화와 비응급 대조군이다.
응급 놓침과 헛경보를 따로 센다. 실제 음성 인식이나 안전 대응의 검증은 아니다.

저장소의 가상환경을 활성화하고 저장소 루트에서 실행한다.

```bash
python scripts/sim/run.py --output ~/reports/sim/before.json
# 해당 줄의 변경 뒤
python scripts/sim/run.py --output ~/reports/sim/after.json
python scripts/sim/compare.py ~/reports/sim/before.json ~/reports/sim/after.json
python -m pytest -q tests/test_sim_harness.py
```

결과 JSON과 같은 이름의 Markdown 표를 만든다. 기존 결과를 덮어쓰지 않는다.
파일명은 실행마다 바꾼다. `--protocol`과 `--family`로 대상을 좁히고 `--limit`으로
말 수를 제한할 수 있다. `--fail-on-wrong-state`는 잘못 바뀐 상태가 하나라도 있으면
종료 코드 1을 낸다. 비교기는 같은 자료·모드·라우터·공급자·조건·문장 집합만 비교한다.
새로 실패한 문장은 평균 개선에 가려지지 않는다. 비교 결과의 `metrics`는 정확도·CER/WER·숫자/단위/용어·지연 p50/p95·표본 수의 전후 값과 차이를 보인다. 미측정은 null로 남기며 지연의 흔들림은 자동 퇴보 게이트로 쓰지 않는다. CI는 시험대 자체 시험과 전체 점수 생성을 실행하고 점수표를 산출물로 보관한다. 아직 사람이 정하지 않은 이해 정확도 기준을 자동 통과 기준으로 쓰지 않는다. 기존 오류도 점수에서 빼지 않으며, 상태 오류 0 강제 검사는 위 옵션으로 별도 실행한다.

오프라인은 문장을 그대로 돌려주는 STT와 결정적인 VAD 분류기를 꽂는다.
짧은 PCM 운반 신호를 `ListenerSession.accept_chunk` → `run_turn_safely`에 흘린다.
응급·언어 입장·앞단 규칙·규칙 경계·서버 검증·상태 기계·문장 합성 경계는 제품 코드다.
답변 TTS는 가짜이며 네트워크는 차단한다. 따라서 오프라인에서 STT 오류율·음향·SNR·
실제 첫 소리 지연은 **측정하지 않음**이다. 처리 시간은 가짜 공급자를 둔 프로세스 시간이다.

## 자료와 기대

[data/fixtures/evaluation/sim/corpus.json](../data/fixtures/evaluation/sim/corpus.json)은
starter pack의 공개 자료에서 가져온 불완전한 원문 발췌다. 출처 URL·저자·라이선스·원본
SHA-256과 저장된 분석 형식을 함께 보존한다. 세포·전달·qPCR·전기영동·면역검출·단백질
정량·화학 용액·토양 영양·페로브스카이트 소재를 포함한다. 영어 지시문·숫자·단위·주의문은
원문 그대로다. 한국어 번역을 만들어 넣지 않는다. 자료를 읽을 때 제품의 원문 근거 검증을
다시 통과시킨다. 이 발췌는 업로드 분석 통과나 전체 절차의 실행 가능 여부를 판정하지 않는다.

갈래별 정해진 문장을 만들고, 수치 관찰·값 변경·영어 용어 질문은 그 단계에 실제로 있는
값·용어로 만든다. 단위의 원문 대소문자를 보존하며 `mM`과 `mm`, `µM`과 `µm`를 구분한다. 같은 원문 값 요청은 명시적 무변경/확인으로 처리할 수 있다. 별도의 값 변경 양성 대조군은 원문 값과 구분한 합성 실험자 입력이며 안전 범위의 근거가 아니다. 원문 복귀는 합성 실험자가 확인한 변경 값을 준비한 경우도 시험한다. 범위 밖 값은 잘못된 요청을 막는 시험 입력이며 프로토콜 값이 아니다.
용어 표본은 원문에 있는 약어·화학식·브랜드의 대문자 표기를 보수적으로 고른다. `Use`, `Incubate` 같은 영어 지시어와 단위는 용어 분모에서 뺀다. 소문자로만 쓴 과학 이름을 모두 포착하는 사전은 아니며, 찾은 표기가 없는 단계는 용어 질문을 만들어 채우지 않는다.
기대 동작은 서버 분류 결과와 별도로 선언한다. 현재 단계 읽기/다시 읽기의 전체 읽기, 막연한 완료의 확인 질문, 변경 값이 없는 원문 복귀의 명시적 무변경, 원문 밖 값의 거절/확인 질문은 정상 처리다. 이 기대는 실제 상태 보존/변경 조건과 함께 검사한다. 각 문장은 독립된 시험 세션에서 실행하며 타이머 실행/일시정지 같은 선행 상태를 준비한다. 한 실험의 모든 연속 발화·연결·재생 순서를 시험한다는 뜻은 아니다. 단계·완료·타이머·일시정지·확인 질문·
기록·오늘의 값·조건 답의 실제 전후 상태를 비교한다. 읽기 전용 요청은 이 상태를 보존해야 한다.
1단계에서 앞으로의 3단계로 “돌아가”라는 모순된 요청은 상태를 전혀 바꾸지 않고 복귀를 거절해야 한다.
설명과 다음을 섞은 말은 완료 확인 질문을 열 수 있지만 단계 이동은 허용하지 않는다.
완료 명령도 엉뚱한 단계로 갔다면 잘못 바뀐 상태다. 실패하거나 버린 턴은 기대했던 동작이라도 상태를 바꾸면 잘못 바뀐 상태로 센다. 재생·담화·마지막 답변 캐시는
실험 상태가 아니므로 비교 대상에서 제외한다. 판정 코드는
[scripts/sim/metrics.py](../scripts/sim/metrics.py), 상태 목록은
[scripts/sim/runner.py](../scripts/sim/runner.py)에 있다.

원래 카탈로그는 먼저 복사하고 복사본만 시험한다. 원본은 SQLite 읽기 전용 백업과
원문 객체 복사에만 사용한다. 복사본은 보고서 폴더 아래에 둔다.

```bash
python scripts/sim/catalog.py \
  --source ~/voiney-lab/data/runtime/candidate-a-live-acceptance \
  --destination ~/reports/sim/catalog-snapshot
python scripts/sim/run.py --catalog-copy ~/reports/sim/catalog-snapshot \
  --output ~/reports/sim/offline-with-uploads.json
```

제품 카탈로그의 `available_for_execution`을 통과한 최신 분석/검토본만 불러온다.
실패·검토 대기 자료는 개수로 보고하며 억지로 실행하지 않는다. in-gel 고정본·headspace·
CUT&Tag 같은 올린 자료는 여기에 포함될 수 있다. 라이선스 PDF·카탈로그·원음·받아쓰기·
대답·신원·열쇠는 커밋하지 않는다. 점수에는 고정 시험 코드·코드 값·수치·시간만 남긴다.

선택적인 모델 말 바꾸기는 사람이 다음 명령으로 따로 실행한다. 기본은 비용 안내만 한다.
`--execute`가 있는 명령만 셸의 router 역할 모델을 한 번 호출한다. 결과는 사람이 검토할
제안이며 자동으로 시험 문장이나 기대 동작이 되지 않는다.

```bash
python scripts/sim/expand.py --case pio_37443_passaging.current.0 \
  --output ~/reports/sim/phrasing-proposals.json
python scripts/sim/expand.py --case pio_37443_passaging.current.0 \
  --execute --max-usd 1 --output ~/reports/sim/phrasing-proposals.json
```

## 실측: 사람이 실행

공급자 자격증명은 셸에 준비한다. 이 도구들은 `.env`를 읽지 않는다.
Google STT V2 서비스 계정 설정은 [DEPLOYMENT_RUNBOOK.md](DEPLOYMENT_RUNBOOK.md),
그 밖의 공급자 설정은 [FEATURES.md](FEATURES.md#f17-공급자와-설정)를 따른다.

우선 외부 호출 0회의 예상표를 본다.

```bash
python scripts/sim/live.py --limit 20 --max-calls 200 --max-usd 5 \
  --output ~/reports/sim/chirp3-first.json
```

사람이 실제로 돌리는 명령:

```bash
python scripts/sim/live.py --execute --limit 20 --max-calls 200 --max-usd 5 \
  --output ~/reports/sim/chirp3-first.json
python scripts/sim/live.py --execute --limit 20 --stt-provider google_cloud \
  --stt-model latest_long --max-calls 200 --max-usd 5 \
  --output ~/reports/sim/latest-long-first.json
python scripts/sim/live.py --execute --limit 20 --stt-provider elevenlabs \
  --max-calls 200 --max-usd 5 --output ~/reports/sim/elevenlabs-first.json
```

기본은 Google TTS 한국어 Chirp 3 HD의 Charon·Kore, 속도 0.85·1·1.15,
조용함과 팬 20 dB·원심분리기 10 dB·초음파 세척기 10 dB·말소리 모사 10 dB·키보드 5 dB,
클린벤치·생물안전작업대 팬 10 dB·흄후드 배기 10 dB·글러브박스 순환 펌프 10 dB·
팬과 원심분리기 10 dB·팬과 옆 사람 말소리 모사 10 dB다.
`--voices`, `--speeds`, `--conditions quiet,fan:20`으로 작은 묶음부터 시작한다.
속도는 받은 소리를 로컬에서 재표본화하므로 **음높이도 바뀐다**. 공급자의 속도 조절 기능을
검증했다는 뜻이 아니다. 모든 입력은 제품 PCM 변환으로 16 kHz mono PCM16이 되고,
실제 WebRTC VAD → 선택한 실제 STT 어댑터 → 제품 서버 경로로 들어간다.
기본 받아쓰기는 `google_cloud_v2`의 `chirp_3`다. 답변도 실제 Google TTS로 합성한다.
라우터는 기본으로 끈다. `--configured-models`는 셸의 라우터와 번역 역할을 사용하며,
라우터 설정이 불완전하면 실행하지 않는다. 역할 공급자를 바꿔 재시도하지 않는다.
필수 설정이 빠지면 음성 합성 전에 멈추고 필요한 설정 이름만 결과에 남긴다.

작업 공간 소음에서 Chirp 3 잡음 제거 켬·끔과 마이크 거리 30 cm·1 m를 비교할 때는
먼저 아래 호출 0회 계획을 보고, 사람이 같은 명령에 `--execute`를 더한다. 문장 1개 ×
소음 5개 × 거리 2개 × 잡음 제거 2개 = 20조건이다. `--max-calls 80`과 `--max-usd 8`은
입력 TTS·STT·응답 TTS·인증·재시도를 포함해 멈추는 상한이며 실제 청구액 약속은 아니다.

```bash
python scripts/sim/live.py --limit 1 \
  --voices ko-KR-Chirp3-HD-Charon --speeds 1 \
  --conditions cleanbench_bsc_fan:10,fume_hood_exhaust:10,glovebox_circulation_pump:10,fan_centrifuge:10,fan_adjacent_speech:10 \
  --distances 30,100 --denoise on,off \
  --max-calls 80 --max-usd 8 --output ~/reports/sim/hood-chirp3-denoise.json
# 계획을 확인한 사람이 위 명령에 --execute를 더한다.
```

`--denoise on,off` 비교는 `google_cloud_v2`에서만 받는다. 각 행과 표에는 소음·지정 SNR,
유효 SNR, 마이크 거리, 잡음 제거 켬·끔이 남고 비교기는 이 축이 다른 행을 같은 조건으로
합치지 않는다.

올린 프로토콜은 오프라인과 같은 `--catalog-copy`로 추가한다. 공개 고정 묶음 뒤에
추가되므로 `--limit`만 주면 앞의 공개 문장만 선택될 수 있다. 오프라인 결과의
`tables.protocol`에서 `catalog_`로 시작하는 코드를 골라 `--protocol`로 지정한다.
계획과 결과에 실행 가능/제외 개수도 남긴다. 예에서 `catalog_code`는 그 코드로 바꾼다.

```bash
python scripts/sim/live.py --catalog-copy ~/reports/sim/catalog-snapshot \
  --protocol catalog_code --limit 2 --max-calls 50 --max-usd 5 \
  --output ~/reports/sim/uploaded-chirp3.json
```

먼저 계획을 확인한 뒤 사람이 같은 명령에 `--execute`를 더해 실행한다.

소음은 도구가 수식으로 만든 견본이며 별도의 외부 라이선스가 없다. 실제 팬·원심분리기·
세척기·클린벤치·생물안전작업대·흄후드·글러브박스에서 잰 소리가 아니며 말소리 모사도
알아들을 수 있는 옆 사람 발화가 아니다.
지정한 SNR로 섞은 뒤 포화가 없도록 신호와 소음을 함께 낮춘다. 소음 조건은 끝의
1초에도 소음을 유지하고, 시험 입력 뒤에는 유한한 무음 종료 구간을 둔다.
거리 축은 30 cm를 기준으로 목소리만 `30 / 거리(cm)`로 낮추는 자유음장 계획용 근사다.
1 m에서는 같은 소음 바닥에서 유효 SNR이 약 10.46 dB 낮아진다. 창·후드의 반사와 차음,
마이크 방향, Bluetooth 처리, 실제 방의 무한한 배경 소음·방언은 모사하지 않으며 별도
사람 시험 대상이다.

캐시는 `~/reports/sim/audio-cache/`의 해시 이름 WAV다. 같은 문장·목소리는 다시
합성하지 않고 속도·소음은 로컬에서 바꾼다. 점수·캐시·원장은 모두 `~/reports/sim/` 아래만
허용한다. 캐시 적중 수를 보고하며 지연에는 캐시의 영향이 포함된다. 서버가 오디오 바이트를
만든 시점까지이고 브라우저나 이어폰에서 들린 시점은 측정하지 않는다.

비용표 기본값 **TTS $30/백만 문자, STT $0.024/분**은 계산용 가정이며 최신 요금 확인이나
청구액 약속이 아니다. `--tts-usd-per-million`, `--stt-usd-per-minute`으로 사람이 확인한
요금을 넣는다. TTS 입력 문자 수, 최대 15초 STT 요청 수와 가정 비용을 보여 준다.
답변 TTS·번역·라우터·OAuth·실패·재시도는 추가다. HTTP 전송마다 기본 $0.10을 먼저
예약하고 `--max-calls` 또는 `--max-usd`에 도달하면 중단한다. 모델을 켤 때는 사람이
`--reserve-usd-per-request`를 충분히 높여야 한다. 예약 비용은 실청구액이 아니다.
원장에는 전송 종류·예약액·시각만 남으며, 같은 출력 이름의 원장은 누적한다.
원장이 있어도 결과 파일은 덮어쓰지 않는다. 한도 중단은 종료 코드 3과 완료/계획 말 수로
표시한다. 비교되지 않은 조건은 0점이나 성공으로 채우지 않는다.

## 사람 녹음과 참조 문장

진단 파일은 읽기만 한다. 먼저 사람이 순서를 대조할 빈 참조 목록을 만든다.

```bash
python scripts/sim/references.py \
  --recordings ~/voiney-lab/data/runtime/stt-diagnostics \
  --output ~/reports/sim/recording-inventory.json
```

WAV 수정 시각과 숫자 turn 순으로 후보를 정렬한다. 복사 시각·파일 유실로 순서가
달라질 수 있으므로 사람이 원음을 듣고 `voice_test3.md`의 실제 시험 순서와 대조한다.
`ordinal`, `reference`, 선택적인 `terms`를 담은 JSON 배열을 별도 파일에 완성한다.
받아쓰기 결과를 참조로 베끼지 않는다. 비어 있거나 빠진 문장이 있으면 정렬을 확정하지 않는다.

```bash
python scripts/sim/references.py \
  --recordings ~/voiney-lab/data/runtime/stt-diagnostics \
  --sequence ~/reports/sim/reviewed-sequence.json \
  --output ~/reports/sim/references.jsonl
python scripts/stt_compare.py ~/voiney-lab/data/runtime/stt-diagnostics \
  ~/reports/sim/references.jsonl
python scripts/sim/diagnostics.py ~/voiney-lab/data/runtime/stt-diagnostics \
  ~/reports/sim/references.jsonl --output ~/reports/sim/human-stt-compare.json
python scripts/sim/diagnostics.py ~/voiney-lab/data/runtime/stt-diagnostics \
  ~/reports/sim/references.jsonl --execute --max-calls 50 --max-usd 5 \
  --output ~/reports/sim/human-stt-compare.json
```

마지막 도구는 기존 `scripts/stt_compare.py`의 비교 함수·공급자 어댑터를 사용한다.
기본 비교는 Chirp 3와 latest_long이며 `--providers google_cloud_v2,google_cloud,elevenlabs`로
추가한다. 기본은 호출 계획만 출력한다. 참조문·진단 파일·원음은 로컬에만 둔다.

## 읽는 법과 기준

이해 정확도는 독립적으로 정한 동작/확인 질문/상태 보존 기대의 충족률이다. 답변의 과학적
설명이 모두 맞는지를 재는 지표는 아니다. 잘못 바뀐 상태·영어 원문 복사·단계 이름 겹침은
이해와 따로 센다. 영어 원문은 연속 네 영어 단어 이상의 원문 일치를 탐지하는 보수적인
검사이며, 두세 단어의 짧은 영어 조작 문장도 원문과 대조한다. 한국어 문장 속 시약·장비 이름 자체는 오류로 세지 않는다. 한국어 포함 여부도
별도로 보고한다. 소음·지정 SNR·유효 SNR·거리·잡음 제거·갈래·프로토콜·목소리·속도별 표를 본다.

실측에서 STT 호출 경계에 도달한 입력만 CER·WER와 숫자·단위·숫자+단위·용어 정확도를 낸다. VAD에서 버린 입력은 STT 분모에서 제외하고 버림으로 센다. 공급자 실패는 실패 요청으로 남긴다. 실제 받아쓰기 응답이 하나도 없으면 STT 측정 완료로 표시하지 않는다. 기준 문장이 없는 소음만의
입력은 STT 분모를 만들지 않는다. 버린 말의 이유 코드·미종료 턴·취소 이벤트와 강제 종료·여러 조각으로 나뉜 입력도 센다.
첫 소리·STT·TTS·서버 처리 지연은 p50/p95와 표본 수를 함께 본다. 의도하지 않은 음절
잘림은 STT 오류에 나타날 수 있지만 여기의 취소 이벤트 수가 모든 음향 잘림을 포착하지는 않는다.

첫 실측 뒤 사람이 확정할 출시 목표는 조용함 이해 95% 이상, 중간 소음
10 dB 이해 90% 이상, 잘못 바뀐 상태·응급 놓침·번역이 있는 조건의 영어 원문 0이다. 단계 이름 겹침도 별도로 센다. 소음만에서 명령이
실행되는 수도 0이어야 한다. 오프라인에서만 통과한 수치를 현장 출시 근거로 쓰지 않는다.

## 저장 번역과 라우터 비교

`--translations data/fixtures/evaluation/sim/stored_korean.json`은 공개 원문의
평가용 번역을 붙인다. 원문 SHA·문장 키·인용문을 대조하고 제품 번역 검사를
통과한 것만 읽는다. 기계 번역으로 표시하며 사람이 검토한 번역이라 하지 않는다.
목적 10개와 단계 37개이며, 숫자 표기·부정 검사가 거절한 두 단계는 없는 상태로
남긴다. 입력의 번역 지침 판을 검증해 보존하고, 판이 없으면 0으로 기록한다.
결과에 실제 번역 수와 없는 단계 수를 낸다. 번역을 붙여도 상태는 바뀌지 않는다.

라우터만 비교할 때는 정답 글자 STT·가짜 TTS와 기존 서버 라우터를 함께 쓴다.
아래 첫 명령은 호출 0회 계획이고, 두 번째는 **사람이** 실행한다. 모델 공급자와
모델·키는 셸에 준비하며 도구가 `.env`를 읽지 않는다. 라우터는 이 명령 밖에서
켜지지 않는다. `VOINEY_LAB_ROUTER_FIRST=true`를 사람이 앞에 붙여 앞단 규칙의
위임 범위를 달리한 조건도 잴 수 있다. 다른 제품 기능 스위치는 정답 글자 기준과
동일하게 초기화한다. 다른 모델 역할은 호출하지 않는다. 결과에는 공급자·모델·
추론 수준·출력 한도·시간 제한을 남기며, 일반 비교는 이 설정이 다르면 거절한다.

```bash
python scripts/sim/run.py --configured-router --split development --limit 20 --max-calls 20 --max-usd 5
VOINEY_LAB_LLM_ROUTER_ENABLED=true python scripts/sim/run.py --configured-router --execute \
  --split development --limit 20 --max-calls 20 --max-usd 5 \
  --translations data/fixtures/evaluation/sim/stored_korean.json --output ~/reports/sim/router-development.json
python scripts/sim/compare.py --compare-routing ~/reports/sim/rules-development.json ~/reports/sim/router-development.json
```

두 결과는 같은 분할·제한·저장 번역이어야 한다. 계획의 모델 요금은 입력 4000토큰·
출력 최대 400토큰/턴과 입력 $5·출력 $15/백만 토큰이라는 계산용 가정이다.
20턴이면 $0.52 어림이며 실제 토큰·요금 확인값이 아니다. 전송마다 기본 $0.25를
예약해 20회·예약 $5에서 멈춘다. 실제 청구액 상한을 보장하지 않는다. 음성 실측은
기존 `live.py --configured-models`를 쓰고 음성·번역·라우터의 모든 HTTP 전송을
함께 예약한다. HTTPX와 SDK의 HTTPX2 전송도 같은 원장에 센다.

|조건|이해|잘못 바뀐 상태|응급 놓침|영어 원문|호출 수|예약액|
|---|---:|---:|---:|---:|---:|---:|
|규칙·개발용|실행 결과|실행 결과|실행 결과|실행 결과|0|0|
|라우터·개발용|사람 실행 뒤|사람 실행 뒤|사람 실행 뒤|사람 실행 뒤|원장|원장|
|규칙·시험용|집계만|집계만|집계만|집계만|0|0|
|라우터·시험용|사람 실행 뒤 집계만|사람 실행 뒤|사람 실행 뒤|사람 실행 뒤|원장|원장|

듣기 시험대는 입력 소리가 끝나도 처리 중인 마지막 STT·답변·재생 완료를 기다린다.
일정 시간 뒤 연결을 닫아 마지막 실패 알림을 잃지 않으며, 기다림 상한이 지나면
불완전한 점수를 성공으로 내지 않고 시험을 실패시킨다.
