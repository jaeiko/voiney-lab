# mobile/ — VoineyLab 스토어 앱 뼈대 (줄 APP0)

왜 이렇게 만들었는지, 권한·심사·결제·오프라인 계획은 [`docs/APP_PLAN.md`](../docs/APP_PLAN.md) 에 있다. 이 파일은 만드는 법만 적는다.

- 방식: **Capacitor 8.5.3** 로 서버의 웹 화면(`src/voiney_lab/static/`)을 감싼다. Android 먼저.
- 원칙: 서버가 상태와 타이머의 권한을 그대로 갖는다. 앱은 기기 기능(알림·화면 켜 두기·네트워크 상태, 다음에 백그라운드 마이크)만 더한다.
- 비밀 값·서명 키는 저장소에 넣지 않는다(`.gitignore` 가 `*.keystore`·`*.jks` 를 막는다).

## 파일

| 경로 | 무엇 |
|---|---|
| `package.json` | Capacitor 와 플러그인. 버전을 정확히 고정했다 |
| `capacitor.config.js` | 앱 id·이름, 화면을 묶을지(기본) 서버 주소로 열지(`VOINEY_APP_SERVER_URL`) |
| `scripts/build-android-debug.sh` | 디버그 APK 빌드(아래 3번을 묶음) |
| `scripts/prepare-web.mjs` | `www/`(추적 안 함)를 만든다: 서버 정적 화면 + `www-src/` |
| `www-src/offline.html` | 서버 주소 모드에서 서버를 못 열 때 보이는 화면(`server.errorPath`) |
| `www-src/device-check.html` | 디버그 빌드의 기기 시험 화면: `native-bridge.js` 로 6분 시험 타이머 알림(5분 뒤 "1분 전", 6분 뒤 끝)을 예약·취소 |
| `android/` | `npx cap add android` 가 만든 Android 프로젝트. 고친 곳: `AndroidManifest.xml` 의 마이크 권한, `allowBackup=false` |

화면 쪽 다리는 `src/voiney_lab/static/native-bridge.js` 이고, 시험은 `node --test tests/native_bridge/timer_alerts.test.js`
(pytest 에서는 `tests/test_native_bridge.py`).

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

# 3. 빌드 (Node 22 이상) — 아래를 묶은 것이 scripts/build-android-debug.sh
#    (그 스크립트는 ~/app-build 를 기본으로 쓴다: APP_BUILD·JAVA_HOME·ANDROID_HOME 로 바꿀 수 있다)
cd <저장소>/mobile && npm ci
node scripts/prepare-web.mjs
VOINEY_APP_START_PATH=device-check.html npx cap sync android   # 기기 시험 화면으로 시작
cd android && GRADLE_USER_HOME=~/app-build/gradle-home ./gradlew assembleDebug -Dorg.gradle.jvmargs=-Xmx1536m
# → android/app/build/outputs/apk/debug/app-debug.apk
```

- 서버 주소 모드(파일럿 내부 시험만): `VOINEY_APP_SERVER_URL=https://pilot.<도메인>/ npx cap sync android` 뒤 같은 빌드.
  `https://` 만 받는다(마이크는 안전한 페이지에서만 열린다).
- 맥에서 iOS: `docs/APP_PLAN.md` 의 "맥에서 빌드하는 순서".
