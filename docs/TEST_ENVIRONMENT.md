# Python과 시험 환경

서버는 Python 3.14를 권장한다. 최소 판은 `pyproject.toml`의 Python 3.12이며,
CI는 3.12·3.14에서 런타임만 설치한 서버 import, 전체 pytest, compileall,
규칙 replay와 공급자를 부르지 않는 합성 회귀 평가를 각각 실행한다.
브라우저 수용 시험의 서버는 3.14다. 가상환경을 바꾸면 설치된 패키지도 다시 확인한다.

```bash
python -c 'import sys; print(sys.version); print(sys.executable)'
python -m pip install -e '.[test]'
python -m pip check
```

`requirements.txt`는 `pyproject.toml`의 런타임 dependencies와 순서·판 제한까지 같다.
실행기와 CI는 패키지 설치를 쓰며 requirements 파일을 읽지 않는다. 파일로 설치하는
외부 작업을 위해 유지하고, `tests/test_infra1_environment.py`가 두 목록의 차이를 막는다.
의존성을 고칠 때 두 파일을 함께 고친다. 시험용 패키지는 requirements에 넣지 않는다.

## 시간 제한을 검사하는 법

기본 전체 명령과 공유 잠금은 `AGENTS.md`의 Verification·Heavy runs를 따른다.
실제 PDF 자식 프로세스의 시험 예산은 `tests/conftest.py`가 시험마다 120초로 주입한다.
제품의 기본 30초와 설정 범위는 그대로이며, 기본값 시험은 주입 값을 지우고 검사한다.
시간 초과의 공개 오류는 가짜 subprocess가 받은 정확한 예산과 오류 코드를 검사하고,
실제 멈춘 작업자를 죽이는 시험은 별도의 1초 제한을 유지한다.
120초는 호스트의 스케줄링 여유이며 PDF 처리 성능의 합격 기준이 아니다.

재인용 제한 시험은 가짜 모델을 사건으로 붙잡은 채 기다림 경계에서 제한 도달을
주입한다. 분석이 모델보다 먼저 돌아오는지, 늦은 답 뒤에도 카드가 남는지 검사한다.
조각 진행 문구는 첫 조각 완료와 모든 시작 사건이 장부에 저장됐다는 신호를 받은 뒤
읽고, 읽기가 끝날 때 나머지 모델을 풀어 준다. 고정 sleep과 조회 횟수로 순간을 맞추지 않는다.
각 사건 대기에는 고장 감지용 상한이 있다.

Node 기기 연결 시험은 파일별 pytest 항목으로 실행하며 각 파일에 120초 상한을 둔다.
파일마다 종료 코드와 TAP/spec 최종 요약을 모두 검사한다. 전체 파일의 누적 시간이
한 번의 120초 예산을 소비하지 않으며, 멈춘 파일의 이름을 실패 항목에서 바로 알 수 있다.

## 선택: 파일 단위 병렬 시험

기본 `python -m pytest -q`와 CI의 pytest 명령은 그대로다. 여유 RAM이 있는 개발 환경에서
다음 선택 extra와 명령을 쓸 수 있다. 공유 머신에서는 병렬 실행도 heavy 잠금을 받는다.

```bash
python -m pip install -e '.[test,test-parallel]'
flock ~/reports/.heavy.lock env \
  VOINEY_LAB_MOSS_ENABLED=false \
  VOINEY_LAB_WORKSPACE_ENABLED=false \
  VOINEY_LAB_EXPERIMENT_REPORTS_ENABLED=false \
  VOINEY_LAB_LLM_ROUTER_ENABLED=false \
  python -m pytest -q -n 2 --dist=loadfile
```

검사 범위는 Python 3.14·조건 A의 로컬 작업자 2개다. `loadfile`은 같은 모듈의 시험을
한 작업자에 묶고, 프로세스마다 서버 globals·가짜 공급자·캐시가 분리된다. SQLite와
파일은 `tmp_path`/`TemporaryDirectory`, JDK 출력은 `mktemp`, 실제 로컬 소켓은
OS가 고르는 포트 0을 사용한다. 원문 PDF는 읽기만 한다. pytest가 작업자별 임시
폴더를 만든다. 바깥 공급자 연결 차단은 각 작업자의 `tests/conftest.py`에서도 적용된다.

작업자마다 전체 수집과 서버 import 비용·메모리가 든다. 자동 CPU 수(`-n auto`)나
더 많은 작업자, 조건 B, 다른 Python·플랫폼으로 이 결과를 넓히지 않는다. 해당 환경에서
직렬 결과와 실패 목록·통과/건너뜀 수를 먼저 대조한다. 시험이 저장소나 고정 포트에
쓰기를 추가하면 병렬 실행의 공유 자원 점검도 다시 한다. 부하에 따라 더 느릴 수 있다.
