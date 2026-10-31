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

분야별 **가정 질문** 갈래(`supposed_question`)는 바이오·화학·소재·토양마다
개발용 3문장과 시험용 3문장을 따로 고정해 원래 분할 밖에 둔다(원래 분할의
지문은 그대로다). 기대는 읽기 전용 근거 답이며, 가정을 이상 사항으로 기록하면
`supposed_misrecorded`로 센다. 결과 표에는 `field`(분야) 차원이 있다.
`--development-fixture`는 in-gel 개발 원문(조건 A)을 같은 일반 사례로 공개 10종
옆에 더해 돌린다. 개발용으로만 쓰고 시험용 분할에는 넣지 않는다.

```bash
python scripts/sim/run.py --split development --development-fixture --output ~/reports/sim/development-with-in-gel.json
```

응급 별도 묶음은 눈·피부 노출, 흡입·호흡, 불·연기, 대량 쏟음, 베임·출혈,
쓰러짐·의식, 가스·냄새, 화상, 감전, 삼킴의 합성 발화와 비응급 대조군이다.
응급 놓침과 헛경보를 따로 센다. 실제 음성 인식이나 안전 대응의 검증은 아니다.

LISTEN3 충격음 묶음은 수식으로 만든 타자·유리 부딪힘·피펫 클릭을 30 cm·1 m
두 거리에서 돌린다. 소음 갈래에는 충격음만, 나머지 갈래에는 착용자 운반 신호가
끝난 0.5초 뒤 충격음을 둔다. `legacy`는 충격음 판정을 끈 수정 전 처리이고
`current`는 켠 처리다. 두 결과는 문장·분할·충격음 축 지문이 같아 비교할 수 있다.
`wearer_dropped`, `impact_reopened`, `noise_sent_to_stt`, `wrong_state`를 함께 본다.
이 PCM은 실제 한국어 음성이나 실제 도구 녹음이 아니므로 현장 검증으로 부르지 않는다.

```bash
python scripts/sim/run.py --impact-matrix --impact-filter legacy \
  --split development --output ~/reports/sim/listen3-development-before.json
python scripts/sim/run.py --impact-matrix --impact-filter current \
  --split development --output ~/reports/sim/listen3-development-after.json
python scripts/sim/compare.py ~/reports/sim/listen3-development-before.json \
  ~/reports/sim/listen3-development-after.json

python scripts/sim/run.py --impact-matrix --impact-filter legacy \
  --split holdout --output ~/reports/sim/listen3-holdout-before.json
python scripts/sim/run.py --impact-matrix --impact-filter current \
  --split holdout --output ~/reports/sim/listen3-holdout-after.json
python scripts/sim/compare.py ~/reports/sim/listen3-holdout-before.json \
  ~/reports/sim/listen3-holdout-after.json
```

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

같은 표본을 다시 비교할 때는 `--limit` 대신 저장소의 versioned case-id manifest를
쓴다. manifest 순서대로 정확히 같은 공개 case id를 고르므로 corpus에 문장이
추가되거나 생성 순서가 바뀌어도 표본이 달라지지 않는다. `--case-ids`는
`--limit`·분할·프로토콜/갈래 필터와 함께 쓰지 않으며, 알 수 없는 id가 하나라도
있으면 호출 전에 실패한다. STT5 대표 표본은 20문장에 아래 두 소음 조건을 곱한
40건이다.

```bash
python scripts/sim/live.py \
  --case-ids scripts/sim/manifests/stt5_representative_40.json \
  --voices ko-KR-Chirp3-HD-Charon --speeds 1 \
  --conditions quiet,centrifuge:10 --distances 30 --denoise on \
  --max-calls 200 --max-usd 20 \
  --output ~/reports/sim/stt5-fixed-plan.json
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

## 진단 WAV의 익명 말 끝 재생

LISTEN3의 재생은 사람이 정한 UTC 구간만 읽고, 메타데이터의 `word_count`가 0인지와
제품 `ListenerSession`이 STT 경계(`speech.end`)를 만들었는지만 센다. 원음·파일 이름·
세션·받아쓰기 글은 출력하거나 복사하지 않는다. 아래 시각은 예시이므로 실제 시험 구간으로
바꾼다. 이 명령은 공급자를 호출하지 않는다.

```bash
python scripts/hear_listening_bench.py \
  --diagnostics-dir ~/voiney-lab/data/runtime/stt-diagnostics \
  --diagnostics-start-utc 2026-10-29T13:45:00Z \
  --diagnostics-end-utc 2026-10-29T13:51:59Z
```

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
`cut_off`는 15초 강제 종료 또는 나뉜 endpoint가 실제로 둘 이상의 STT 입력이 된 경우다.
여러 endpoint가 생겨도 continuation join 뒤 STT 호출이 하나라면 `endpoint_count`에는
남기되 `cut_off`로 세지 않는다.
첫 소리·STT·TTS·서버 처리 지연은 p50/p95와 표본 수를 함께 본다. 의도하지 않은 음절
잘림은 STT 오류에 나타날 수 있지만 여기의 취소 이벤트 수가 모든 음향 잘림을 포착하지는 않는다.

첫 실측 뒤 사람이 확정할 출시 목표는 조용함 이해 95% 이상, 중간 소음
10 dB 이해 90% 이상, 잘못 바뀐 상태·응급 놓침·번역이 있는 조건의 영어 원문 0이다. 단계 이름 겹침도 별도로 센다. 소음만에서 명령이
실행되는 수도 0이어야 한다. 오프라인에서만 통과한 수치를 현장 출시 근거로 쓰지 않는다.

### 선택 VAD 모델은 평가만

Silero 같은 새 VAD는 이 줄에서 제품 의존성이나 실행 경로에 넣지 않는다. 사람이 같은
비공개 진단 WAV와 위 충격음 개발용·시험용 분할을 현재 WebRTC+충격음 판정과 똑같이
재생하는 별도 오프라인 어댑터를 만든 뒤에만 후보로 잰다. 비교 항목은 착용자 발화 놓침,
짧은 명령 놓침, 소음의 STT 진입, 말 시작·끝 오차, CPU 시간, 최대 메모리, 처리 지연이다.
원음·받아쓰기·식별자는 결과에 쓰지 않고 숫자 집계만 남긴다. 시험용 개별 행은 보지
않는다. 실제 노트북·선택 마이크에서 사람 음성 재시험까지 끝나고 사람이 채택을 결정하기
전에는 패키지를 추가하거나 기본 VAD를 바꾸지 않는다.

## 저장 번역과 라우터 비교

`--translations data/fixtures/evaluation/sim/stored_korean.json`은 공개 원문의
평가용 번역을 붙인다. 원문 SHA·문장 키·인용문을 대조하고 제품 번역 검사를
통과한 것만 읽는다. 기계 번역으로 표시하며 사람이 검토한 번역이라 하지 않는다.
목적 10개와 단계 37개이며, 숫자 표기·부정 검사가 거절한 두 단계는 없는 상태로
남긴다. 입력의 번역 지침 판을 검증해 보존하고, 판이 없으면 0으로 기록한다.
결과에 실제 번역 수와 없는 단계 수를 낸다. 번역을 붙여도 상태는 바뀌지 않는다.

라우터만 비교할 때는 정답 글자 STT·가짜 TTS와 기존 서버 라우터를 함께 쓴다.
아래 계획 명령은 호출 0회이고, `--execute`가 있는 명령만 **사람이** 실행한다.
모델 공급자와 모델·키는 셸에 준비하며 도구가 `.env`를 읽지 않는다. 라우터는 이
명령 밖에서 켜지지 않는다. `VOINEY_LAB_ROUTER_FIRST=false`로 앞단 규칙 다음의
기존 한 길을 고정한다. 다른 제품 기능 스위치는 정답 글자 기준과 동일하게 초기화하고
다른 모델 역할은 호출하지 않는다. 결과에는 공급자·모델·추론 수준·출력 한도·시간
제한을 남기며, 일반 비교는 이 설정이 다르면 거절한다.

비용을 먼저 줄일 때는 아래 **1단계**만 실행한다. 규칙 개발용·시험용 두 결과를 먼저
만들고, 첫 유료 조건은 기존 사람 20문장 근거와 직접 이어지는 OpenAI
`gpt-6-luna`·`none`·2.5초의 **개발용만** 실행한다. 이 조건의 호출 상한은 540회,
예약 상한은 $16.20, 아래의 확인하지 않은 토큰 단가 가정에서는 $14.04 어림이다.
잘못 바뀐 상태가 1개라도 있거나, 열린 질문 갈래가 규칙보다 좋아지지 않거나, 공급자
턴 첫 소리 p95가 3초를 넘으면 여기서 멈추고 시험용·나머지 행렬을 실행하지 않는다.
이 순서는 모델 채택이 아니라 다음 비용을 써도 되는지 확인하는 게이트다.

```bash
# 1) 외부 호출 0회: 규칙 두 분할
python scripts/sim/run.py --split development --fail-on-wrong-state \
  --translations data/fixtures/evaluation/sim/stored_korean.json \
  --output ~/reports/sim/model1-rules-development.json
python scripts/sim/run.py --split holdout --fail-on-wrong-state \
  --translations data/fixtures/evaluation/sim/stored_korean.json \
  --output ~/reports/sim/model1-rules-holdout.json

# 2) 사람이 계획을 확인한 뒤에만 --execute: 라우터 한 조건, 개발용만
VOINEY_LAB_LLM_ROUTER_ENABLED=true VOINEY_LAB_ROUTER_FIRST=false \
VOINEY_LAB_ROUTER_PROVIDER=openai VOINEY_LAB_ROUTER_MODEL=gpt-6-luna \
VOINEY_LAB_ROUTER_REASONING=none VOINEY_LAB_LLM_ROUTER_TIMEOUT_SECONDS=2.5 \
python scripts/sim/run.py --configured-router --execute --split development \
  --max-calls 540 --max-usd 16.20 --reserve-usd-per-request 0.03 \
  --router-input-rate 5 --router-output-rate 15 --fail-on-wrong-state \
  --translations data/fixtures/evaluation/sim/stored_korean.json \
  --output ~/reports/sim/model1-openai-none-2p5-development.json
```

1단계가 위 중단 기준을 모두 통과한 뒤에만 아래 전체 행렬을 검토한다.

```bash
python scripts/sim/run.py --configured-router --split development \
  --max-calls 540 --max-usd 16.20 --reserve-usd-per-request 0.03 \
  --router-input-rate 5 --router-output-rate 15

python scripts/sim/run.py --split development --fail-on-wrong-state \
  --translations data/fixtures/evaluation/sim/stored_korean.json \
  --output ~/reports/sim/model1-rules-development.json
python scripts/sim/run.py --split holdout --fail-on-wrong-state \
  --translations data/fixtures/evaluation/sim/stored_korean.json \
  --output ~/reports/sim/model1-rules-holdout.json

run_router_condition () {
  local provider="$1" model="$2" reasoning="$3" timeout="$4" slug="$5"
  VOINEY_LAB_LLM_ROUTER_ENABLED=true \
  VOINEY_LAB_ROUTER_FIRST=false \
  VOINEY_LAB_ROUTER_PROVIDER="$provider" \
  VOINEY_LAB_ROUTER_MODEL="$model" \
  VOINEY_LAB_ROUTER_REASONING="$reasoning" \
  VOINEY_LAB_LLM_ROUTER_TIMEOUT_SECONDS="$timeout" \
  python scripts/sim/run.py --configured-router --execute \
    --split development --max-calls 540 --max-usd 16.20 \
    --reserve-usd-per-request 0.03 --router-input-rate 5 --router-output-rate 15 \
    --fail-on-wrong-state --translations data/fixtures/evaluation/sim/stored_korean.json \
    --output "$HOME/reports/sim/model1-${slug}-development.json"
  VOINEY_LAB_LLM_ROUTER_ENABLED=true \
  VOINEY_LAB_ROUTER_FIRST=false \
  VOINEY_LAB_ROUTER_PROVIDER="$provider" \
  VOINEY_LAB_ROUTER_MODEL="$model" \
  VOINEY_LAB_ROUTER_REASONING="$reasoning" \
  VOINEY_LAB_LLM_ROUTER_TIMEOUT_SECONDS="$timeout" \
  python scripts/sim/run.py --configured-router --execute \
    --split holdout --max-calls 332 --max-usd 9.96 \
    --reserve-usd-per-request 0.03 --router-input-rate 5 --router-output-rate 15 \
    --fail-on-wrong-state --translations data/fixtures/evaluation/sim/stored_korean.json \
    --output "$HOME/reports/sim/model1-${slug}-holdout.json"
}

run_router_condition openai gpt-6-luna none 2.5 openai-none-2p5
run_router_condition openai gpt-6-luna none 4 openai-none-4
run_router_condition google gemini-3.8-flash none 2.5 google-none-2p5
run_router_condition google gemini-3.8-flash none 4 google-none-4
run_router_condition google gemini-3.8-flash low 2.5 google-low-2p5
run_router_condition google gemini-3.8-flash low 4 google-low-4
```

규칙과 각 모델 결과는 같은 분할·저장 번역이어야 한다. 현재 고정 묶음은 개발용
540문장·시험용 332문장이다. 위 조건 하나의 최악 호출 상한은 872회, 예약 상한은
$26.16이다. 계획의 모델 요금은 입력 4000토큰·출력 최대 400토큰/턴과 입력 $5·출력
$15/백만 토큰이라는 **확인하지 않은 계산 가정**이며, 모든 턴이 모델을 부르면 조건당
$22.672 어림이다. 여섯 조건 합계의 호출 상한은 5,232회, 예약 상한은 $156.96,
같은 가정의 비용 어림은 $136.032이다. 앞단 규칙이 처리한 턴에는 모델을 부르지 않으므로
실제 호출은 이보다 적다. 최신 공급자 요금이나 청구액 약속이 아니며, 실행 전에 사람이
요금과 상한을 다시 정한다.

`overall.router`와 갈래별 `tables.family.*.router`는 모델 호출·처리 주체·규칙 폴백·
폴백 이유·시간 초과 수/율과 반환된 토큰을 낸다. `overall.latency_ms`는 첫 소리와
라우터 모델 p50/p95를 함께 낸다. `router_cost_estimate`는 **사용량을 돌려준 호출만**
위 가정으로 계산하며 시간 초과 등 사용량이 없는 호출 수를 따로 남긴다.
`provider_requests`·`reserved_usd`는 HTTP 전송 원장 값이다. 이해율은 전체와 갈래별로,
특히 `problem_question`·`purpose`·`safety`·`term` 같은 열린 질문/설명 갈래를 본다.
잘못 바뀐 상태는 모든 조건에서 0이어야 한다. 시험용은 개별 행 없이 집계만 읽는다.

현재 Google 어댑터는 `gemini-3.8-flash`의 도구 호출을 지원한다. 다만 Gemini 3.x는
추론을 끌 수 없어 설정 `none`도 요청에서는 `low`가 된다. 따라서 Google `none`과
`low`는 서로 다른 추론 단계 비교가 아니라 같은 요청 설정의 반복/변동 확인으로 읽는다.
이는 가짜 전송 계약 시험을 통과한 지원이며 실제 공급자 성공을 뜻하는 live-tested가 아니다.
OpenAI·Google 어느 쪽도 실패할 때 다른 공급자로 넘어가지 않는다.

|조건|이해|잘못 바뀐 상태|응급 놓침|영어 원문|호출 수|예약액|
|---|---:|---:|---:|---:|---:|---:|
|규칙·개발용|실행 결과|실행 결과|실행 결과|실행 결과|0|0|
|라우터·개발용|사람 실행 뒤|사람 실행 뒤|사람 실행 뒤|사람 실행 뒤|원장|원장|
|규칙·시험용|집계만|집계만|집계만|집계만|0|0|
|라우터·시험용|사람 실행 뒤 집계만|사람 실행 뒤|사람 실행 뒤|사람 실행 뒤|원장|원장|

## 번역·분석 추론 비교

`scripts/model_reasoning_eval.py`도 기본은 호출 0회 계획이며 `--execute`가 있어야
모델을 부른다. 원본 카탈로그가 아니라 `~/reports/` 아래의 일관된 복사본을 쓴다.
번역 표본은 사람이 분야별 프로토콜 id를 정하고, 각 분야 안에서 원문 길이 3분위가
고르게 되도록 60문장을 한 번 고정한다. manifest에는 원문 대신 프로토콜/문장 id,
원문 SHA-256, 글자 수와 길이 구간만 남는다. 아래의 id와 공급자·모델은 실제 값으로
바꾼다.

```bash
python scripts/model_reasoning_eval.py translation-sample \
  --data-dir ~/reports/model1/catalog-translation \
  --group biology=BIOLOGY_PROTOCOL_ID \
  --group chemistry=CHEMISTRY_PROTOCOL_ID \
  --group materials=MATERIALS_PROTOCOL_ID \
  --sample-size 60 --seed 1028 \
  --output ~/reports/model1/translation-60.json

# 호출 0회 계획: effort와 output 이름만 low/medium/high로 바꿔 각각 먼저 본다.
VOINEY_LAB_TRANSLATION_PROVIDER="$MODEL1_TRANSLATION_PROVIDER" \
VOINEY_LAB_TRANSLATION_MODEL="$MODEL1_TRANSLATION_MODEL" \
VOINEY_LAB_TRANSLATION_REASONING=low \
python scripts/model_reasoning_eval.py translation \
  --data-dir ~/reports/model1/catalog-translation \
  --manifest ~/reports/model1/translation-60.json --mode batch \
  --output-dir ~/reports/model1/translation-batch-low \
  --max-calls 20 --max-usd 2 --reserve-usd-per-request 0.10 \
  --input-rate 5 --output-rate 15

# 사람이 계획의 호출 수를 확인한 뒤에만 --execute를 더한다.
# batch는 low/medium/high 각각 호출 20·예약 $2 상한,
# spot은 mode/output만 바꾸고 각각 호출 60·예약 $6 상한으로 실행한다.
```

묶음 결과는 검사 통과율, 명시적인 문체 금지 꼴의 기계 검사율, 응답 꼴 실패,
번역 호출 지연 p50/p95, 반환 토큰과 비용 어림을 낸다. 그 자리 결과는 같은 값과
함께 6초 안에 끝난 호출 비율을 낸다. 문체 값은 합니다체·독자 호칭·영어 동사+
한국어 어미·저자 1인칭·명시한 직역투·지침에서 금지한 표기의 **기계적 대리값**이지
자연스러움의 사람 판정이 아니다. 실행은 low/medium/high 중 하나를 명시해야 하고 각
reasoning은 같은 manifest를 써야 한다. 묶음과 그 자리는 제품에서 서로 다른 프롬프트·
시간 경계를 쓰므로 결과도 따로 정한다. 6초 취소도 논리 호출과 사용량 누락에 세며,
논리 호출 상한이 다음 요청을 막았는지는 `calls.logical_call_cap_stopped`에 남는다.
`budget_stopped`나 `logical_call_cap_stopped`가 참인 조건은 불완전하므로 다른 조건과
성능을 비교하지 않고 더 보수적인 상한으로 새 출력 폴더에서 다시 실행한다.

분석은 low와 medium마다 같은 원본 snapshot에서 만든 **서로 다른 복사본**을 쓴다.
`--case`는 기준 분석이 있는 프로토콜 id와 현재 분석 응답 JSON을 짝짓는다. 실행은
append-only 재분석이고 현재 `ProtocolCatalog.analyze`의 자동 재시도·근거 다시 묻기·
카드 생성을 그대로 거친다. 원응답은 지정한 개인 보고 폴더에만 두고 커밋하지 않는다.
0호출 계획에는 기준 JSON의 경로 대신 SHA-256만 남긴다.
0호출 계획의 모든 `cases[].pieces`가 0인 프로토콜만 비교한다. 조각 분석의 개별 응답은
문서 전체 응답이 아니므로 실행 명령은 조각이 필요한 프로토콜을 공급자 호출 전에
거부한다. 그런 프로토콜을 비교하려면 병합된 분석을 같은 기준으로 채점하는 별도 도구가
먼저 필요하다.

```bash
ANALYSIS_CASES=(
  --case "PROTOCOL_ID_1=$HOME/reports/model1/reference-1.json"
  --case "PROTOCOL_ID_2=$HOME/reports/model1/reference-2.json"
)

# low 복사본으로 0호출 계획을 본 뒤, 같은 명령에 --execute를 더한다.
VOINEY_LAB_ANALYSIS_PROVIDER="$MODEL1_ANALYSIS_PROVIDER" \
VOINEY_LAB_ANALYSIS_MODEL="$MODEL1_ANALYSIS_MODEL" \
VOINEY_LAB_ANALYSIS_REASONING=low \
python scripts/model_reasoning_eval.py analysis \
  --data-dir ~/reports/model1/catalog-analysis-low "${ANALYSIS_CASES[@]}" \
  --output-dir ~/reports/model1/analysis-low --timeout 600 \
  --max-calls 16 --max-usd 8 --reserve-usd-per-request 0.50 \
  --input-rate 5 --output-rate 15

# medium은 원본 snapshot에서 새로 만든 catalog-analysis-medium과 별도 output을 쓴다.
```

조건마다 분석 호출 16회·예약 $8 상한이다. 결과는 프로토콜별 호출 수·전체 시간·
확인 카드 수와 `scripts/score_extraction.py`의 단계 수/순서/문장 유사도/값 비교를
원문 PDF·기준 분석 SHA와 함께 내고, 조건 전체의 단계 수·순서 일치율과 문장 유사도
평균·값 비교 합계도 낸다.
기준본은 회귀 기준이지 절대 정답이 아니며 실행 판정으로 쓰지 않는다.
모델 응답이 여러 번이면 도구는 전부 개인 폴더에 보존하고, 현재 파서와 근거 검사를
통과한 마지막 전체 응답을 채점한다. 시간 초과/실패로 사용량이 없는 호출은 비용 어림에서
빠졌다고 별도로 센다. 두 분석 조건의 예약 상한 합계는 $16이고, 번역 3단계의 묶음+
그 자리 예약 상한 합계는 $24다. 이 역시 실제 청구액 보장이 아니다.

듣기 시험대는 입력 소리가 끝나도 처리 중인 마지막 STT·답변·재생 완료를 기다린다.
일정 시간 뒤 연결을 닫아 마지막 실패 알림을 잃지 않으며, 기다림 상한이 지나면
불완전한 점수를 성공으로 내지 않고 시험을 실패시킨다.
