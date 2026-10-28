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
