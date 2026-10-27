# mobile/ — Voiney Lab 스토어 앱 (줄 APP0 뼈대 · 줄 APP1 플러그인)

왜 이렇게 만들었는지, 권한·심사·결제·오프라인 계획은 [`docs/APP_PLAN.md`](../docs/APP_PLAN.md) 에 있다. 이 파일은 만드는 법만 적는다.

- 방식: **Capacitor 8.5.3**로 새 화면(`/app/`, `src/voiney_lab/static/app/`)을 감싼다. Android 먼저. [Mac의 쉬운 빌드 순서](../docs/APP_PLAN.md#먼저-mac에서-android-앱을-만드는-쉬운-순서)를 먼저 본다.
- 원칙: 서버가 상태와 타이머의 권한을 그대로 갖는다. 앱은 기기 기능(정시 알람·화면 꺼도 듣기·화면 켜 두기·네트워크 상태)과 앱 로그인만 더한다.
- 비밀 값·서명 키는 저장소에 넣지 않는다(`.gitignore` 가 `*.keystore`·`*.jks` 를 막는다).

## 파일

| 경로 | 무엇 |
|---|---|
| `package.json` | Capacitor 와 플러그인. 버전을 정확히 고정했다 |
| `capacitor.config.js` | 앱 id·이름, 화면을 묶을지(기본) 서버 주소로 열지(`VOINEY_APP_SERVER_URL`). `loggingBehavior: "none"` — 플러그인 호출(토큰·소리)이 기기 로그에 남지 않게 |
| `scripts/build-android-debug.sh` | 디버그 APK 빌드(아래 3번을 묶음) |
| `scripts/prepare-web.mjs` | `www/`(추적 안 함)를 만든다: 빌드한 `/app/` 포함 서버 정적 파일 + `www-src/` + `app-config.js`(공개 서버 주소). 앱 다리를 첫 HTTP 요청 전에 넣는다 |
| `www-src/app-bridge.js` | 앱 전용 HTTP·소켓 인증, 저장한 무작위 기기 표시. 로그인·비밀번호 변경·계정 삭제 본문에 `device`를 넣는다. 토큰은 기존 Keystore 플러그인에만 둔다 |
| `scripts/app-config.mjs` | 그 주소를 확인하고 `app-config.js` 를 쓰는 함수(시험 `tests/native_bridge/app_config.test.js`) |
| `www-src/offline.html` | 서버 주소 모드에서 서버를 못 열 때 보이는 화면(`server.errorPath`) |
| `www-src/device-check.html` | 디버그 빌드의 기기 시험 화면: 6분 시험 타이머 알림(5분 뒤 "1분 전", 6분 뒤 끝, 알람 시계인지 표시), 화면 꺼도 듣기, 앱 로그인·웹소켓 첫 메시지 (순서는 `docs/APP_PLAN.md` 11.3절) |
| `android/app/src/main/java/com/voiney/lab/{alarm,mic,session}/` | 앱 플러그인 셋(줄 APP1): `VoineyAlarm`(setAlarmClock, 재부팅 뒤 다시), `VoineyMicrophone`(마이크 포그라운드 서비스), `VoineySessionStore`(Keystore 로 봉한 로그인 토큰). `MainActivity` 가 등록 |
| `android/` | `npx cap add android` 가 만든 Android 프로젝트. 고친 곳: `AndroidManifest.xml`(마이크·포그라운드 서비스·정확한 알람 권한, 수신기·서비스, `allowBackup=false`), `MainActivity.java`, 위 플러그인 |

화면 쪽 다리는 `src/voiney_lab/static/native-bridge.js` 이고, 시험은 `node --test tests/native_bridge/*.test.js`
(pytest 에서는 `tests/test_native_bridge.py`; 안드로이드 소스 확인은 `tests/test_lane_app1_*.py`).

## Android 디버그 APK (리눅스, sudo 없이)

내려받는 것은 모두 저장소 밖 `~/app-build/` 에 둔다.

```bash
# 1. JDK 21 (Capacitor 8 의 Android 는 Java 21 로 컴파일한다)
mkdir -p ~/app-build/downloads/jdk && cd ~/app-build/downloads/jdk
curl -sSL -o jdk21.tar.gz "https://api.adoptium.net/v3/binary/latest/21/ga/linux/x64/jdk/hotspot/normal/eclipse"
tar -xzf jdk21.tar.gz -C ~/app-build          # → ~/app-build/jdk-21.x.y+z

# 2. Android 명령줄 도구 → SDK 플랫폼 36, 빌드 도구 36.0.0
mkdir -p ~/app-build/android-sdk/cmdline-tools && cd ~/app-build/android-sdk/cmdline-tools
curl -sSLO https://dl.google.com/android/repository/commandlinetools-linux-16111833_latest.zip
python3 -c "import zipfile,sys,os;z=zipfile.ZipFile(sys.argv[1]);[(z.extract(i,'.'),os.chmod(i.filename,(i.external_attr>>16)&0o777 or 0o644)) for i in z.infolist()]" commandlinetools-linux-16111833_latest.zip
mv cmdline-tools latest
export JAVA_HOME=$(ls -d ~/app-build/jdk-21*) ANDROID_HOME=~/app-build/android-sdk
$ANDROID_HOME/cmdline-tools/latest/bin/sdkmanager --sdk_root=$ANDROID_HOME "platforms;android-36" "build-tools;36.0.0" "platform-tools"

# 3. 먼저 새 화면 빌드 (Node 22 이상)
cd <저장소>/web && npm ci && npm run build
# 다음 앱 빌드는 scripts/build-android-debug.sh 로도 된다
#    (그 스크립트는 ~/app-build 를 기본으로 쓴다: APP_BUILD·JAVA_HOME·ANDROID_HOME 로 바꿀 수 있다)
cd <저장소>/mobile && npm ci
VOINEY_APP_API_URL=https://시험서버주소 node scripts/prepare-web.mjs
npx cap sync android   # 기본은 /app/, 기기 시험은 VOINEY_APP_START_PATH=device-check.html
cd android && GRADLE_USER_HOME=~/app-build/gradle-home ./gradlew assembleDebug -Dorg.gradle.jvmargs=-Xmx1536m
# → android/app/build/outputs/apk/debug/app-debug.apk
```

- 서버 주소 모드(파일럿 내부 시험만): `VOINEY_APP_SERVER_URL=https://pilot.<도메인>/ npx cap sync android` 뒤 같은 빌드.
  `https://`만 받으며 `/app/`을 연다. 서버의 쿠키 로그인 경로다. 묶은 화면은 `VOINEY_APP_API_URL`을 쓰고 서버가 `https://localhost` 앱 출처를 허용해야 한다.
- 맥에서 iOS: `docs/APP_PLAN.md` 의 "맥에서 빌드하는 순서".

아이콘 재생성: 저장소 맨 위에서 `python scripts/generate_app_icons.py`(Pillow·librsvg 필요). 검사는 `--check`. 원본 SVG는 바꾸지 않는다. iOS 프로젝트는 아직 없다. 기기 알림·백그라운드 마이크와 직접 이미지·다운로드 링크의 bearer 연결은 별도 작업이다.
