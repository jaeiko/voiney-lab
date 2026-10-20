# 스토어 앱 계획 — 화면이 꺼져도 듣기, 앱 밖 타이머 알림, 끊겨도 울리는 타이머 (줄 APP0 · APP1)

2026-10-19 줄 APP1 이 고친 곳: 정시 알람 플러그인(9.3절), 화면 꺼도 듣기 플러그인(9.4절), 앱 로그인과 앱 출처 허용(9.5절, 서버),
다리 연결 지점을 함수 이름으로 다시 적음(9.2절), APK 다시 만듦(10.1절), 기기 시험 순서(11.3절). 사람의 결정(2026-10-18): 앱은 반드시 낸다 —
화면이 꺼져도 듣기, 앱 밖 타이머 알림(가장 중요), 와이파이가 끊겨도 안전. 파일럿은 웹이고 앱은 그 뒤를 준비한다.

2026-10-18 작성(줄 APP0). 조사와 뼈대만 담았다. 실제 기기에서는 아직 아무것도 시험하지 않았다. 스토어 정책·수수료는 아래에 적은 날짜에
공식 문서에서 확인한 것만 적었고, 확인하지 못한 것은 "확인 못 함"으로 남겼다(8절). 규정은 바뀌므로 등록 직전에 링크를 다시 연다.

## 1. 요약

- **권고: Capacitor(하이브리드)로 지금의 웹 화면을 감싸고, 웹으로는 안 되는 두 가지만 네이티브 플러그인으로 만든다.**
  1. 화면이 꺼져도 듣기: Android `microphone` 포그라운드 서비스 / iOS `audio` 백그라운드 모드 안에서 **네이티브로 마이크를 받아** 서버로 보낸다.
  2. 정시에 울리는 끝 알림: Android `setAlarmClock` / iOS AlarmKit(iOS 26) 또는 시간 민감 알림.
  타이머 알림의 기본(1분 전·끝, 끊겨도 울림)은 공식 플러그인 `@capacitor/local-notifications` 로 이 줄에서 바로 된다.
- 웹 화면은 **앱에 묶는다**(스토어 빌드). 서버 주소로 여는 방식은 파일럿 내부 시험에만 쓴다 — Capacitor 문서가 그 설정을
  "not intended for use in production" 이라고 적는다(5절).
- 결제: 개인은 앱 안 구독(Apple IAP·Google Play 결제), 기관은 스토어 밖 계약. 둘 다 서버의 계정 "이용 권한" 하나를 채운다(6절).
  앱 안에서 라이선스 키로 잠금을 푸는 화면은 만들지 않는다(Apple 3.1.1 이 금지).
- 오프라인: 지금은 끊긴 동안 **기기에 예약된 타이머만 울리고 상태는 바뀌지 않는다**. 음성 안내를 오프라인에서 이어 가려면
  서버 권한과의 관계부터 사람이 정해야 한다(7절).

## 2. 지금 제품과 이 줄의 원칙

- 서버 중심이다. 상태와 타이머의 권한은 서버(`CuratedProtocolSession`)에 있고, 음성은 서버의 STT·TTS·LLM 이 처리하고,
  화면은 웹소켓(`/ws`)으로 상태를 받는다. 화면은 PWA(`src/voiney_lab/static/index.html`, manifest 있음, 서비스 워커 없음)이고,
  끊김 대비는 줄 NET 의 `static/connection.js`(2026-10-19 `dev` 에 합침)가 맡는다.
- 서버가 화면에 보내는 단계 타이머(`timers.step`, `curated_protocol.py` 의 `timer_status()`):
  `state`(`not_started`·`running`·`expired`), `duration_seconds`, `remaining_seconds`, `started_at`, `deadline_at`(ISO, UTC),
  `step_id`, `step_label`, `name`("최소 2시간"·"15분"), `bound`(`minimum`·`maximum`·`approximate`·`exact`).
  일시정지는 단계 타이머를 멈추지 않는다(실험의 반응은 계속 진행되므로).
- 앱의 원칙:
  - 앱은 같은 웹 화면을 감싸고 **기기 기능만 더한다**: 알림, 백그라운드 오디오, 화면 켜 두기, 네트워크 상태.
  - 기기 알림은 **서버가 준 타이머 시각의 사본**이다. 앱은 타이머를 시작·연장·종료하지 않고 단계를 넘기지 않는다.
  - 끊긴 동안 앱은 상태를 바꾸지 않는다. 다시 연결되면 서버 상태를 그대로 따른다.
  - 비밀 값·서명 키는 저장소에 넣지 않는다.

## 3. 방식 비교

### 3.1 우리 요구 넷 — 가능한가

표기: ◎ 공식 문서로 경로 확인 · ○ 가능하나 직접 만든 네이티브 코드 필요 · △ 문서상 불확실, 실기기 확인 필요.
"하이브리드"는 Capacitor, "RN"은 React Native, "네이티브"는 Kotlin·Swift.

| 요구 | Android: 하이브리드 / RN / 네이티브 | iOS: 하이브리드 / RN / 네이티브 | 근거 (확인 2026-10-18) |
|---|---|---|---|
| ① 화면 꺼짐·다른 앱일 때도 마이크로 듣기 | 웹뷰 안 녹음은 △ · 포그라운드 서비스 + 네이티브 캡처 플러그인 ○ / ○ / ◎ | 웹뷰 안 녹음은 △(WebKit 버그 보고가 엇갈림) · 네이티브 캡처 플러그인 ○ / ○ / ◎ | [Android FGS 유형](https://developer.android.com/develop/background-work/services/fgs/service-types), [Android 백그라운드 마이크](https://developer.android.com/media/platform/mediarecorder), [iOS record 카테고리](https://developer.apple.com/documentation/avfaudio/avaudiosession/category-swift.struct/record), [WebKit 226620](https://bugs.webkit.org/show_bug.cgi?id=226620)·[241480](https://bugs.webkit.org/show_bug.cgi?id=241480) |
| ② 앱 밖 타이머 알림(1분 전·끝) | ◎ `@capacitor/local-notifications` / ○ / ◎ | ◎ 같은 플러그인 / ○ / ◎ | [Local Notifications](https://capacitorjs.com/docs/apis/local-notifications), [iOS 로컬 알림](https://developer.apple.com/documentation/usernotifications/scheduling-a-notification-locally-from-your-app) |
| ③ 와이파이가 끊겨도 울리는 타이머 | ○ 플러그인은 `setExactAndAllowWhileIdle` — Doze 중에는 늦을 수 있음(아래), 정시는 `setAlarmClock` 네이티브 / ○ / ◎ | ◎ 시간·날짜 트리거 · 무음 모드를 뚫으려면 AlarmKit(iOS 26, 네이티브) / ○ / ◎ | [Doze](https://developer.android.com/training/monitoring-device-state/doze-standby), [AlarmManager](https://developer.android.com/reference/android/app/AlarmManager), [AlarmKit](https://developer.apple.com/documentation/alarmkit/scheduling-an-alarm-with-alarmkit) |
| ④ 블루투스 헤드셋 마이크 | 웹뷰의 장치 선택 △ · `setCommunicationDevice`(API 31+) ○ / ○ / ◎ | 웹뷰 △ · `allowBluetoothHFP` + `playAndRecord` ○ / ○ / ◎ | [AudioManager](https://developer.android.com/reference/android/media/AudioManager), [allowBluetoothHFP](https://developer.apple.com/documentation/avfaudio/avaudiosession/categoryoptions-swift.struct/allowbluetoothhfp) |

세 방식 모두 ①·③(정시)·④에는 **같은 네이티브 코드**가 든다. React Native 코어에도 백그라운드 녹음 API 는 없다
([Turbo Native Modules](https://reactnative.dev/docs/turbo-native-modules-introduction) — "You can write the integration code yourself").
차이는 화면이다. 하이브리드는 지금의 웹 화면(4,588줄 `index.html`)을 그대로 쓰고, RN·네이티브는 화면을 다시 써야 한다.

**③의 Doze 문제(중요).** Android 공식 문서: "Neither setAndAllowWhileIdle() nor setExactAndAllowWhileIdle() can fire alarms more than once
per nine minutes, per app." ([Doze](https://developer.android.com/training/monitoring-device-state/doze-standby)). API 레퍼런스는
"when in low-power idle modes this duration may be significantly longer, such as 15 minutes" 라고 적는다
([AlarmManager](https://developer.android.com/reference/android/app/AlarmManager)). 같은 Doze 문서: "Alarms set with setAlarmClock() continue to
fire normally." `@capacitor/local-notifications` 8.3.1 의 Android 코드는 `setExactAndAllowWhileIdle` 를 쓰고 `setAlarmClock` 은 쓰지 않는다.
그래서 **기기가 Doze 에 들어간 뒤에는 1분 전 알림이 울리고 60초 뒤의 끝 알림이 늦을 수 있다.** 끝 알림을 정시에 울리려면
`setAlarmClock` 을 쓰는 작은 플러그인이 다음 단계다. 마이크 포그라운드 서비스가 도는 동안 기기가 Doze 에 들어가는지는
공식 문서로 확인하지 못했다(실기기 시험 항목, 11.3절).

### 3.2 필요한 권한

| 기능 | Android | iOS |
|---|---|---|
| 마이크 | `RECORD_AUDIO`(실행 중 요청) | `NSMicrophoneUsageDescription` — "This key is required if your app uses APIs that access the device's microphone." ([문서](https://developer.apple.com/documentation/bundleresources/information-property-list/nsmicrophoneusagedescription)) |
| 화면 꺼짐에도 듣기 | `FOREGROUND_SERVICE`, `FOREGROUND_SERVICE_MICROPHONE`(API 34), 서비스 유형 `microphone`. **앱이 화면에 보일 때만 시작할 수 있다** — "you cannot create a microphone foreground service while your app is in the background" ([문서](https://developer.android.com/develop/background-work/services/fgs/service-types)) | `UIBackgroundModes` 에 `audio` — "To continue recording audio when your app transitions to the background … add the audio value to the UIBackgroundModes key" ([문서](https://developer.apple.com/documentation/avfaudio/avaudiosession/category-swift.struct/record)) |
| 알림 | `POST_NOTIFICATIONS`(Android 13+, 실행 중 요청 — "notifications are off by default", [문서](https://developer.android.com/develop/ui/views/notifications/notification-permission)) | 알림 승인(UNUserNotificationCenter). 선택: Time Sensitive, Critical(Apple 승인 필요 — "requires an approved entitlement", [문서](https://developer.apple.com/documentation/usernotifications/unnotificationinterruptionlevel/critical)) |
| 정확한 알람 | `SCHEDULE_EXACT_ALARM` — Android 14 새 설치는 **기본 거부**, 사용자가 설정에서 허용 ([문서](https://developer.android.com/about/versions/14/changes/schedule-exact-alarms)). `USE_EXACT_ALARM` 은 Play 정책상 "alarm or timer app"·캘린더 앱만 ([정책](https://support.google.com/googleplay/android-developer/answer/9888170)) | AlarmKit: `NSAlarmKitUsageDescription` — "If the NSAlarmKitUsageDescription key is missing … apps can't schedule alarms with AlarmKit." ([문서](https://developer.apple.com/documentation/alarmkit/scheduling-an-alarm-with-alarmkit)) |
| 블루투스 마이크 | `MODIFY_AUDIO_SETTINGS`. 기기 이름 등 BluetoothDevice API 를 쓰면 `BLUETOOTH_CONNECT`(실행 중 요청) | 권한 없음(오디오 세션 옵션) |
| 개인정보 | Play Console 데이터 보안 양식 | `PrivacyInfo.xcprivacy` — "apps that don't describe their use of required reason API in their privacy manifest file aren't accepted" ([문서](https://developer.apple.com/documentation/bundleresources/describing-use-of-required-reason-api)) |

줄 APP0 의 뼈대가 선언한 것: `INTERNET`, `RECORD_AUDIO`, `MODIFY_AUDIO_SETTINGS`(앱), `POST_NOTIFICATIONS`·`SCHEDULE_EXACT_ALARM`·
`RECEIVE_BOOT_COMPLETED`·`WAKE_LOCK`(알림 플러그인이 합쳐 넣음). 줄 APP1 이 더한 것: `FOREGROUND_SERVICE`·`FOREGROUND_SERVICE_MICROPHONE`
(마이크 서비스가 생겨서), 그리고 앱 플러그인이 직접 쓰는 `POST_NOTIFICATIONS`·`SCHEDULE_EXACT_ALARM`·`RECEIVE_BOOT_COMPLETED` 를 앱 매니페스트에도 적었다.
쓰지 않는 포그라운드 서비스 유형은 선언하지 않는다 — Play 선언 대상만 늘어난다.

### 3.3 스토어 심사 위험

| 위험 | 규정 원문 (확인 2026-10-18) | 우리 대응 |
|---|---|---|
| 웹 감싸기 앱 거절 (Apple) | 4.2: "Your app should include features, content, and UI that elevate it beyond a repackaged website." 4.2.2: "apps shouldn't primarily be … web clippings" ([가이드라인](https://developer.apple.com/app-store/review/guidelines/), Last Updated: October 16, 2026) | 화면을 앱에 묶고(5절), 웹으로 못 하는 기능 — 화면 꺼짐에도 음성 안내, 앱 밖 타이머 알림, 끊겨도 울리는 타이머 — 을 심사 메모에 적는다. 통과 기준을 정한 숫자는 없다 |
| 백그라운드 오디오 사유 (Apple) | 2.5.4: "Multitasking apps may only use background services for their intended purposes: VoIP, audio playback, location, task completion, local notifications, etc." | 듣기와 음성 안내 재생이 앱의 본 기능이다. 심사 메모와 시연 영상에 "화면을 끈 채 장갑 낀 손으로 음성으로만 실험 단계를 진행"을 보인다. 통과 여부는 확인 못 함 |
| 마이크 포그라운드 서비스 선언 (Google) | "Provide a description of the app functionality that is using each foreground service type." / "Include a link to a video demonstrating each foreground service feature." ([정책](https://support.google.com/googleplay/android-developer/answer/13392821)) | 서비스를 만드는 줄에서 설명문과 시연 영상을 준비한다 |
| 정확한 알람 (Google) | `USE_EXACT_ALARM` 은 "The app is an alarm or timer app." 등 본 기능일 때만 ([정책](https://support.google.com/googleplay/android-developer/answer/9888170)) | `SCHEDULE_EXACT_ALARM`(사용자 허용)을 쓴다. 허용을 안 하면 플러그인이 정확하지 않은 알람으로 내려간다 — 화면에 알린다 |
| 외부 AI 로 개인 데이터 전달 (Apple) | 5.1.2(i): "You must clearly disclose where personal data will be shared with third parties, including with third-party AI, and obtain explicit permission before doing so." | 음성·문서가 외부 공급자(STT·TTS·LLM)로 가는 것을 앱 안에서 먼저 알리고 동의를 받는다. README §4 의 공급자 목록이 근거 |
| 계정 삭제·개인정보 처리방침 (Apple) | 5.1.1(i): privacy policy 링크를 "within the app"; 5.1.1(v): "If your app supports account creation, you must also offer account deletion within the app." | 줄 ON(계정)에서 앱 안 계정 삭제를 만든다 |
| 라이선스 키로 잠금 해제 (Apple) | 3.1.1: "Apps may not use their own mechanisms to unlock content or functionality, such as license keys … QR codes" | 기관 이용 권한은 서버 계정에 붙이고 앱에는 키 입력 화면을 두지 않는다(6절) |
| 목표 API (Google) | "Starting August 31 2026: New apps and app updates must target Android 16 (API level 36) or higher" ([문서](https://developer.android.com/google/play/requirements/target-sdk)) | Capacitor 8 기본값이 `targetSdkVersion = 36` 이다 |

## 4. 권고와 근거

**Capacitor 8 하이브리드 + 네이티브 플러그인 둘(백그라운드 마이크, 정시 알람). Android 먼저.**

| 근거 | 내용 |
|---|---|
| 화면을 다시 쓰지 않는다 | 화면·서버 계약(웹소켓 메시지, 카드, 단계 표시)이 지난 줄들에서 테스트로 못 박혀 있다(`tests/test_frontend.py`, Playwright). RN·네이티브는 이것을 처음부터 다시 만들고 다시 시험해야 한다 |
| 네이티브 코드 양은 세 방식이 같다 | ①③④는 어느 방식이든 포그라운드 서비스·AVAudioSession·AlarmManager 를 직접 부르는 코드가 든다. RN 의 이점은 그 코드를 줄여 주지 않는다 |
| 서버 권한 그대로 | 앱은 화면과 기기 기능 사이의 다리(`static/native-bridge.js`)만 갖는다. 두 번째 상태 기계·두 번째 타이머 권한이 생기지 않는다(AGENTS.md 규칙 2–3) |
| 공식 플러그인으로 바로 되는 것 | 알림(1분 전·끝, 끊겨도 울림, 재부팅 뒤 복원), 네트워크 상태, 앱 상태. 화면 켜 두기는 커뮤니티 플러그인 `@capacitor-community/keep-awake` |
| 버전 | Capacitor 8.5.3(2026-10-07 안정판). Node 22 이상("Capacitor 8 requires NodeJS 22 or greater", [문서](https://capacitorjs.com/docs/updating/8-0)), Android minSdk 24·compile/targetSdk 36, Gradle 플러그인 8.13.0, Gradle 8.14.3, Java 21(Capacitor 의 `build.gradle` 이 `JavaVersion.VERSION_21`), iOS 는 Xcode 26 이상·배포 대상 iOS 15 |

하지 않을 것: 백그라운드 듣기를 웹뷰 안 `getUserMedia` 로만 버티기. Android 는 "apps running in the background cannot access the microphone"
([문서](https://developer.android.com/media/platform/mediarecorder))가 앱 단위 규칙이고 웹뷰 안 녹음이 포그라운드 서비스로 이어지는지는
공식 문서가 없다. iOS 는 WebKit 버그 보고가 엇갈린다. 둘 다 실기기로 먼저 재 보되, 기본 계획은 네이티브 캡처다.
`@capacitor/background-runner` 도 맞지 않는다 — "It's not possible to run persistent, always running background services on mobile operating
systems." ([문서](https://capacitorjs.com/docs/apis/background-runner)).

다음 단계(순서대로):

1. 줄 APP0(끝남): 뼈대, 알림 다리(1분 전·끝), Android 디버그 APK, 기기 시험 화면.
2. 줄 APP1(끝남): 끝 알림을 `setAlarmClock` 으로(9.3절). Android 마이크 포그라운드 서비스 플러그인 — 화면이 보일 때 시작, 네이티브 `AudioRecord`,
   페이지의 소켓 하나로 같은 형식(9.4절). 앱 로그인(토큰, Bearer, 소켓 첫 메시지)과 앱 출처 허용(9.5절). 묶은 화면의 서버 주소(`VOINEY_APP_API_URL`).
3. 다음 줄: 다리를 `index.html` 에 연결(9.2절의 연결 지점 ①–⑫). 화면 담당(줄 IA 이후 구조)과 함께. 그다음 실기기 시험(11.3절).
4. 그 뒤: 재생(TTS)이 화면 꺼진 채 안 나오면 재생도 네이티브로. 블루투스 마이크는 `setCommunicationDevice`.
5. iOS: 맥에서 `npx cap add ios`, `audio` 백그라운드 모드 + AVAudioEngine 캡처, AlarmKit 또는 시간 민감 알림. 같은 플러그인 이름으로 만들면 다리는 그대로 쓴다.

## 5. 화면: 앱에 묶을까, 서버 주소로 열까

**권고: 스토어 빌드는 묶는다. 서버 주소로 여는 것은 파일럿 내부 시험만.**

| | 묶기 (`webDir`) | 서버 주소 (`server.url`) |
|---|---|---|
| Capacitor 문서 | 기본 방식 | "This is intended for use with live-reload servers. **This is not intended for use in production.**" ([config](https://capacitorjs.com/docs/config)) |
| Apple 4.2 | 앱 안에 화면이 있다 | 원격 웹사이트를 띄우는 앱으로 보일 위험이 더 크다 |
| 오프라인으로 앱을 열 때 | 앱 화면이 뜨고 마지막 상태·예약된 알림을 보여 줄 수 있다 | 웹뷰 오류 → `server.errorPath`(`offline.html`) 만 뜬다. Android 에서는 그 쪽이 플러그인을 못 쓴다("On Android the html file won't have access to Capacitor plugins") |
| 화면 고침 배포 | 앱 업데이트(심사)가 필요 | 서버 배포만으로 바뀐다 |
| 지금 `index.html` 로 되나 | **아직 안 된다.** 화면이 서버를 `location.host` 로 찾는다. 앱 안에서는 그 값이 `localhost` 다. 서버 쪽 준비(출처 허용·앱 로그인)와 다리(`createAppFetch`·`socketUrl`)는 줄 APP1 에서 됐고, 남은 것은 `index.html` 의 연결(9.2절 ②④⑤) | 된다 |

그래서 뼈대(`mobile/capacitor.config.js`)는 두 방식을 빌드할 때 고른다. 기본은 묶기, `VOINEY_APP_SERVER_URL=https://…` 를 주면 서버 주소.
묶기로 실제 서버에 붙으려면 9.2절 ②④⑤의 연결이 필요하다. 서버 쪽 준비는 줄 APP1 이 했다: 서버의 `VOINEY_LAB_APP_ORIGINS` 에
`https://localhost`(iOS 는 `capacitor://localhost`)를 넣으면 웹소켓·API 가 그 출처를 받고, 로그인은 쿠키 대신 토큰이다(9.5절; `SameSite=None` 쿠키는
쓰지 않았다 — 교차 사이트 쿠키는 브라우저마다 막히고 CSRF 대비가 따로 필요하다). 묶은 화면의 서버 주소는 빌드할 때 `VOINEY_APP_API_URL`. 화면 고침을 심사 없이 내보내는 일(라이브 업데이트)은 이 줄에서 다루지 않는다.

파일럿 서버는 HTTP 기본 인증 뒤에 있다(`docs/PILOT_HTTPS_SETUP.md`). Capacitor 웹뷰가 기본 인증 창을 띄우는지는 확인하지 않았다 —
서버 주소 모드로 시험할 때 첫 번째로 볼 것이다(11.3절).

## 6. 결제

### 6.1 규칙 요약 (모두 확인 2026-10-18)

| 규칙 | 내용 | 링크 |
|---|---|---|
| Apple 3.1.1 | 구독·기능 잠금 해제는 앱 안 구매. "Apps may not use their own mechanisms to unlock content or functionality, such as license keys" | [가이드라인](https://developer.apple.com/app-store/review/guidelines/) |
| Apple 3.1.1(a) | 미국 스토어프런트: "These entitlements are not required for developers to include buttons, external links, or other calls to action in their United States storefront apps." 그 밖의 나라는 외부 구매로 이끄는 버튼·링크 금지 | 같음 |
| Apple 3.1.2(a) | 자동 갱신 구독은 7일 이상, 사용자의 모든 기기에서 사용 가능. 예시에 "software as a service" | 같음 |
| Apple 3.1.3(b) | 다른 곳(웹)에서 산 구독을 앱에서 쓰게 해도 된다 — "provided those items are also available as in-app purchases within the app" | 같음 |
| Apple 3.1.3(c) | "If your app is only sold directly by you to organizations or groups for their employees or students … you may allow enterprise users to access previously-purchased content or subscriptions. Consumer, single user, or family sales must use in-app purchase." | 같음 |
| Apple 수수료 | 30%. Small Business Program(전년 수익 100만 USD 이하) 15%. 구독은 첫해 70% 수령, 1년 뒤 85% | [whats-included](https://developer.apple.com/programs/whats-included/), [small-business](https://developer.apple.com/app-store/small-business-program/), [subscriptions](https://developer.apple.com/app-store/subscriptions/) |
| Apple 한국 외부 결제 | StoreKit External Purchase Entitlement(한국): "Apple will charge a 26% commission on the price paid by the user". "The entitlement can only be used with a new binary distributed solely on the South Korea storefront." 아직 출시하지 않은 번들 ID 만 | [storekit-external-entitlement-kr](https://developer.apple.com/support/storekit-external-entitlement-kr/) |
| Google 결제 정책 | 앱 안 기능·디지털 콘텐츠는 Play 결제. 예시에 "business productivity software". 정책 본문에 B2B 예외 조항은 없다 | [answer/9858738](https://support.google.com/googleplay/android-developer/answer/9858738) |
| Google 소비 전용 앱 | "Google Play allows any app to be consumption-only, even if it is part of a paid service. For example, a user could log in when the app opens and access content paid for somewhere else." 단 그 앱에서는 아무것도 살 수 없어야 한다 | [answer/10281818](https://support.google.com/googleplay/android-developer/answer/10281818) |
| Google 수수료 (한국 등) | 해마다 첫 100만 USD 15%, 그 위 30%, 자동 갱신 구독은 15%. 미국·EEA·영국은 2026-06-30부터 새 요율표(이 표의 칸별 값은 확인 못 함) | [answer/112622](https://support.google.com/googleplay/android-developer/answer/112622) |
| Google 한국 대체 결제 | "If a user pays through an alternative billing system, the Google Play service fee will be reduced by 4%." 2023-08-02부터 대체 결제 API 필수, PCI DSS 인증 | [answer/11222040](https://support.google.com/googleplay/android-developer/answer/11222040) |
| 전기통신사업법 제50조 제1항 제9호 | 앱 마켓사업자가 "특정한 결제방식을 강제하는 행위" 금지(2021. 9. 14. 개정) | [국가법령정보센터](https://www.law.go.kr/법령/전기통신사업법/제50조) |
| 개발자 등록비 | Apple "99 USD per membership year"; Google "US$25 one-time registration fee" | [Apple](https://developer.apple.com/programs/whats-included/), [Google](https://support.google.com/googleplay/android-developer/answer/6112435) |

가격은 정하지 않았다. 원문 조사의 전문(인용 전체)은 줄 보고서 쪽 자료(`~/reports/app0/research_payments.md`)에 있다.

### 6.2 하나의 계정 "이용 권한"을 두 길로 채우는 설계안

설계만 적는다. 구현은 줄 ON(계정) 다음이다.

```
계정(account) ──< 이용 권한(entitlement) >── 기관(organization)
                    source: app_store | google_play | institution
                    product_id, status(active|grace|expired|revoked)
                    period_start, period_end
                    store_ref  (Apple originalTransactionId / Google purchaseToken / 계약 번호)
```

- **서버가 유일한 판정자다.** 앱은 "지금 쓸 수 있나"를 서버에 묻고, 마지막 답을 만료 시각과 함께 캐시한다(끊긴 동안의 유예).
  이용 권한은 **들어올 수 있는지**만 정한다. 실험 상태·타이머·단계에는 손대지 않는다.
- 개인(앱스토어): 앱이 StoreKit 2 로 구독 → 서명된 거래를 서버로 → 서버가 App Store Server API 로 확인, 갱신·환불은
  App Store Server Notifications 로 반영. 구매 때 `appAccountToken` 에 서버 계정 id 를 넣어 계정과 묶는다.
- 개인(구글 플레이): Play Billing 으로 구독 → `purchaseToken` 을 서버로 → 서버가 Play Developer API 로 확인, 실시간 개발자 알림(RTDN)으로 갱신.
  `obfuscatedAccountId` 에 서버 계정 id.
- 기관: 계약은 앱 밖(견적·세금계산서). 서버에 기관과 좌석 수·기간을 넣고, 구성원은 초대 또는 기관 메일 도메인으로 계정에 붙는다.
  **앱 안에 라이선스 키 입력 화면을 두지 않는다**(Apple 3.1.1). 기관 구성원에게는 앱이 구매 화면을 보이지 않는다
  (Google "consumption-only" 와 같은 모양, Apple 3.1.3(c)).
- 둘이 겹칠 때: 기관 권한이 있으면 개인 구매 버튼을 숨긴다. 이미 개인 구독이 있는 사람이 기관에 들어오면 스토어 구독 해지 방법을 안내한다
  (앱이 대신 해지할 수 없다).
- 결정이 필요한 해석: 3.1.3(c)는 "only sold directly by you to organizations"라고 쓴다. **개인 구독과 기관 판매를 한 앱에 함께 두는 구성이
  이 조항에 그대로 들어가는지는 원문만으로 확정할 수 없다.** 등록 전에 Apple 에 문의한다(11절). 안 된다면 대안은
  (가) 기관 사용자도 앱 안 구독(기관이 대신 결제하지 못함), (나) 기관용 앱을 따로.
- 한국 대체 결제: Apple 은 한국 전용 별도 바이너리·새 번들 ID 가 필요해서 첫 출시에는 권하지 않는다. Google 은 같은 앱에서 대체 결제를
  더할 수 있지만 PCI DSS 인증과 대체 결제 API 가 필요하다. 첫 출시는 스토어 결제만으로 하고, 매출이 생긴 뒤 다시 본다.

## 7. 오프라인 — 음성 안내를 끊긴 동안 이어 가려면

지금 끊기면 할 수 있는 것: **기기에 예약된 타이머 알림이 울린다**(이 줄의 다리). 그 밖에는 아무것도 하지 않는다 —
듣기(STT)·말하기(TTS)·판단(LLM 라우터)·상태 전이가 모두 서버에 있다.

필요한 것과 서버 권한과의 관계:

| 단계 | 끊긴 동안 되는 것 | 필요한 것 | 서버 권한과의 관계 |
|---|---|---|---|
| 0 (이 줄) | 타이머 1분 전·끝 알림 | 로컬 알림 | 서버 시각의 사본. 상태 변화 없음 |
| 1 (줄 NET 이 웹에서 만듦) | 끊김을 3초 안에 알리고 화면을 읽기 전용으로, 마지막 단계·다음 단계·안전 주의를 그대로 둠, 화면 타이머 사본(화면이 켜져 있을 때 1분 전·끝을 소리로), 1·2·4·8·15초마다 다시 연결해 같은 실험을 이어 감 | 앱에서 더할 것: 앱을 새로 열었을 때도 보이게 마지막 서버 상태를 기기에 보관(읽기 전용) | 상태 변화 없음. 다시 연결되면 서버 상태로 덮어씀 |
| 2 | "지금 단계 다시 들려줘" | 분석 통과한 프로토콜의 단계 글과, **연결돼 있을 때 서버 TTS 로 미리 만든 단계 음성**을 기기에 내려받기. 질문을 알아듣는 것은 화면 버튼 또는 기기 안 STT 의 작은 명령 어휘 | 읽기만 한다. 단계를 넘기지 않는다 |
| 3 | 끊긴 동안 "다음 단계"를 말해도 진행 | 기기 안 STT·TTS, 기기 안 상태 기록 | **두 번째 상태 권한이 생긴다.** 지금 규칙(AGENTS.md 규칙 2–3, "끊긴 동안 앱은 상태를 바꾸지 않는다")에 어긋난다. 하려면 "끊긴 동안의 요청을 기록만 하고, 다시 연결되면 서버가 검증하고 사람이 확인해서 반영"하는 식의 설계를 사람이 먼저 정해야 한다 |

기기 안 음성 기술(확인 2026-10-18):

- Android: `SpeechRecognizer.createOnDeviceSpeechRecognizer`(API 31+), 지원 여부는 `checkRecognitionSupport`(API 33+)로 실행 중 확인
  ([문서](https://developer.android.com/reference/android/speech/SpeechRecognizer)). TTS 는 `TextToSpeech`.
- iOS: `requiresOnDeviceRecognition` — "on-device requests won't be as accurate"
  ([문서](https://developer.apple.com/documentation/speech/sfspeechrecognitionrequest/requiresondevicerecognition)), iOS 26 `SpeechTranscriber`
  (`supportedLocales` 로 확인). TTS 는 `AVSpeechSynthesizer`.
- **한국어가 기기 안에서 되는지는 두 플랫폼 모두 공식 문서로 확인하지 못했다** — 실행 중 조회해야 한다. 서버 STT 에 주는 프로토콜
  용어(keyterms)도 기기 안 인식에는 없으므로 실험 용어 정확도는 서버보다 낮을 것으로 본다(측정하지 않음).

권하는 길: 0 → 1 → 2. 3은 서버 권한 설계를 사람이 정한 뒤에만.

## 8. 확인 못 한 것

공식 문서로 확인하지 못해 실기기·문의로 정해야 하는 것.

1. Android 웹뷰 `getUserMedia` 가 백그라운드·화면 꺼짐(포그라운드 서비스 중 포함)에서 계속 녹음하는지.
2. Android 12+/14 에서 정확한 알람 권한을 가진 앱의 Doze 할당량("nine minutes" / "such as 15 minutes")이 바뀌었는지.
   마이크 포그라운드 서비스 중에도 Doze 에 들어가는지.
3. `setCommunicationDevice` 에 `BLUETOOTH_CONNECT` 가 필요한지, 웹뷰가 블루투스 마이크를 고르는지.
4. iOS 에서 백그라운드 상태에서 녹음을 새로 시작할 수 있는지. WKWebView 의 백그라운드 녹음(버그 보고가 엇갈림).
5. iOS 시간 민감 알림 entitlement 의 공식 레퍼런스 쪽, UNUserNotificationCenter 문서 안의 64개 한도(옛 UILocalNotification 문서에만 있음).
6. 기기 안 STT·TTS 의 한국어 지원.
7. Apple 이 이 앱의 백그라운드 오디오(녹음)를 2.5.4 에 맞다고 볼지, Google 이 정확한 알람 사용을 받아들일지.
8. Apple 3.1.3(c)가 개인 구독 + 기관 판매 혼합 앱에 그대로 적용되는지.
9. Apple 한국 외부 결제 26% 에 Small Business·구독 2년차 감면이 있는지. Google 미국·EEA·영국 새 요율표의 칸별 값.
   전기통신사업법 시행령의 세부(보도자료 본문이 첨부 파일에만 있음).
10. Google 조직 계정이 신규 개인 계정의 "12명·14일" 비공개 테스트 요건에서 빠지는지(그 요건은 개인 계정 문장에만 있다).
11. (줄 APP1) 화면이 꺼진 채 웹뷰가 네이티브 마이크 프레임을 받아 소켓으로 보내는지(Capacitor 는 웹뷰 타이머를 멈추지 않지만 Chromium 의 숨은 페이지 처리는 확인 못 함),
    화면이 꺼진 채 웹뷰의 `AudioContext` 재생(TTS)이 나오는지.
12. (줄 APP1) 알람 시계를 건 채 Doze 강제(`force-idle`) 때 1분 전과 끝이 둘 다 제시각에 오는지, 정확한 알람을 거둔 뒤 다시 허용했을 때 저장된 알람이 다시 걸리는지.
13. (줄 APP1) 기기 회사(삼성 등)의 배터리 최적화가 마이크 포그라운드 서비스나 알람 시계를 막는지.

## 9. 앱 뼈대와 기기 기능 다리

### 9.1 뼈대 (`mobile/`)

| 경로 | 무엇 |
|---|---|
| `mobile/package.json` | Capacitor 8.5.3, `@capacitor/local-notifications` 8.3.1, `@capacitor/network` 8.0.1, `@capacitor/app` 8.1.2, `@capacitor-community/keep-awake` 8.0.1 (정확한 버전으로 고정). 줄 APP1 은 npm 패키지를 더하지 않았다 — 플러그인 셋은 앱 안 코드다 |
| `mobile/capacitor.config.js` | 앱 id `com.voiney.lab`(자리표시 — 11절), 이름 VoineyLab. 기본은 화면 묶기, `VOINEY_APP_SERVER_URL=https://…` 이면 서버 주소, `VOINEY_APP_START_PATH` 로 첫 쪽. **`loggingBehavior: "none"`**(줄 APP1): Capacitor 의 기본값 "debug" 는 디버그 빌드에서 모든 플러그인 호출의 인자와 답을 기기 로그(logcat)에 적는다 — 로그인 토큰과 마이크 소리가 거기 남는다 |
| `mobile/scripts/prepare-web.mjs` | `mobile/www/`(추적 안 함)를 서버 정적 화면 + `mobile/www-src/` 로 만들고, 줄 APP1 부터 `www/app-config.js`(`window.VOINEY_APP_CONFIG = {serverUrl}`)를 쓴다. 주소는 빌드할 때 `VOINEY_APP_API_URL=https://…`(`mobile/scripts/app-config.mjs`, 시험 `tests/native_bridge/app_config.test.js`). 공개 주소만 — 토큰·키는 넣지 않는다 |
| `mobile/www-src/offline.html` | 서버 주소 모드에서 서버를 못 열 때의 화면 |
| `mobile/www-src/device-check.html` | 디버그 빌드의 기기 시험 화면(11.3절): 알림(어느 방식이 쓰이는지 표시), 정확한 알람 설정, 화면 켜 두기, 네트워크, 화면 꺼도 듣기(받은 프레임 수·소리 크기), 마이크 권한 설정, 앱 로그인·웹소켓 첫 메시지·로그아웃 |
| `mobile/android/app/src/main/java/com/voiney/lab/alarm/` | **VoineyAlarm**(줄 APP1 결정 ①): `TimerAlarmPlugin`(schedule·cancel·pending·exactAlarmStatus·openExactAlarmSettings), `TimerAlarms`(setAlarmClock·게시·재부팅 복원), `TimerAlarmStore`(앱 전용 설정 파일), `TimerAlarmReceiver`·`TimerAlarmBootReceiver`, `TimerAlarmPolicy`(안드로이드 없는 규칙 — JDK 로 시험) |
| `mobile/android/app/src/main/java/com/voiney/lab/mic/` | **VoineyMicrophone**(결정 ②): `BackgroundMicPlugin`(start·stop·status·openAppSettings, 이벤트 frames·state), `ListeningService`(마이크 포그라운드 서비스), `MicEvents` |
| `mobile/android/app/src/main/java/com/voiney/lab/session/` | **VoineySessionStore**(결정 ③): `SessionStorePlugin`(set·get·clear) — Android Keystore 의 AES-256-GCM 키로 토큰을 봉한다 |
| `mobile/android/app/src/main/java/com/voiney/lab/MainActivity.java` | 위 세 플러그인을 `super.onCreate` 전에 `registerPlugin` |
| `mobile/android/app/src/main/AndroidManifest.xml` | APP0: `RECORD_AUDIO`·`MODIFY_AUDIO_SETTINGS`, `allowBackup="false"`. APP1: `FOREGROUND_SERVICE`·`FOREGROUND_SERVICE_MICROPHONE`, `SCHEDULE_EXACT_ALARM`·`RECEIVE_BOOT_COMPLETED`·`POST_NOTIFICATIONS`(알림 플러그인도 합쳐 넣지만 앱 플러그인이 쓰므로 직접 적음), 수신기 둘, 서비스 하나(`foregroundServiceType="microphone"`). `USE_EXACT_ALARM` 은 넣지 않는다(Play 정책상 알람·타이머 앱만) |

### 9.2 다리 `src/voiney_lab/static/native-bridge.js` 와 연결 지점 (다음 줄에 넘김)

번들러 없이 `<script>` 로 읽히고(`window.VoineyNativeBridge`), node 에서는 `require` 로 읽힌다. 시험:
`node --test tests/native_bridge/*.test.js`(pytest 에서는 `tests/test_native_bridge.py` 가 모두 돌린다. 줄 APP1 끝에 43개).
**아직 `index.html` 에 연결하지 않았다** — 이번 차례에 화면 구조를 줄 IA 가 바꾸고 있어서다. 아래 연결 지점은 **함수 이름**으로
적었다(줄 번호는 IA 가 합쳐진 뒤 연결하는 줄이 다시 잰다. 2026-10-19 `dev`(515421f) 와 그때의 IA 작업 폴더 모두 아래 함수가 그대로 있었다).

다리가 하는 일:

| 무엇 | 함수 | 시험 |
|---|---|---|
| 서버 타이머 → 기기 알림 두 개(1분 전·끝), 다음 서버 상태에 맞춰 취소·변경 (APP0) | `planTimerAlerts`, `diffAlerts`, `createTimerAlerts(notifier).sync(state, receivedAt, {offsetMs})`, `setRule`, `connectionLost()`(일부러 아무것도 안 함) | `timer_alerts.test.js` |
| 알림 방식 고르기 (APP1 ①): 앱에 VoineyAlarm 이 있고 정확한 알람이 허용이면 알람 시계, 아니면 공식 플러그인. 안 쓰는 쪽에 남은 우리 알림은 다음 `sync` 에서 지운다(두 번 울리지 않음) | `chooseNotifier(cap)` → `{kind, exactAlarm, notifier}`, `createTimerAlerts(...).setNotifier(next)`, `openExactAlarmSettings(cap)`, `prepareNotifications(cap)` | `alarm_clock.test.js` |
| 화면 꺼도 듣기 (APP1 ②): 플러그인의 100 ms 묶음(base64)을 웹 화면 worklet 이 지금 보내는 20 ms·640바이트 프레임으로 자른다. 거절·끄기는 문구로 | `createNativeMicrophone(cap, {onFrame, onState})` → `start/stop/status/openAppSettings/stats`, `MIC_FORMAT`, `micStateWords`, `nativeMicrophoneAvailable` | `background_mic.test.js` |
| 앱 로그인 (APP1 ③): 토큰은 보안 저장소와 이 함수 안에만. Bearer, 소켓 첫 메시지, 서버가 끝낸 세션은 잊음 | `createAppSession(cap, {serverUrl})` → `load/restore/login/signup/logout/fetch/socketUrl/socketHello` | `app_session.test.js` |
| 앱 안에서 페이지의 `fetch` 와 그림·내려받기 | `createAppFetch(session, fetch)`(서버 경로 `/api/…`·`/readyz`·`/healthz` 만 서버로, 나머지는 묶은 파일), `resourceObjectUrl(session, path)`(`blob:` 주소) | `app_session.test.js` |

연결 지점 — 앱일 때만(`VoineyNativeBridge.isNativeApp(window)` 참) 하고, 웹에서는 지금 동작 그대로 둔다:

| # | 자리 (함수로 찾는다) | 할 일 |
|---|---|---|
| ① | 끝의 `<script src="/connection.js">` 옆 | `<script src="/app-config.js">`(앱 빌드에만 있음 — 웹에서는 404 로 아무 일 없음, 또는 앱일 때만 동적으로 읽기) 와 `<script src="/native-bridge.js">`. 정적 파일은 절대 경로(`/connection.js`, `/mic-capture-worklet.js`)도 묶은 화면의 뿌리에서 그대로 열린다 — 고칠 것은 서버로 가는 것뿐이다 |
| ② | 페이지 시작(첫 `fetch` 보다 먼저) | `session=createAppSession(cap,{serverUrl:VOINEY_APP_CONFIG.serverUrl})`, `await session.restore()`, 그리고 **`window.fetch=createAppFetch(session, window.fetch.bind(window))`** 한 줄로 `index.html`·`connection.js`·`feedback.js` 의 `fetch("/api/…")` 26여 곳과 `/readyz` 를 모두 서버로 보낸다(각 줄을 고치지 않는다) |
| ③ | 시작 화면(wizard)의 로그인·가입 단계 — 지금 `fetch("/api/auth/login")`·`/api/auth/signup` 을 부르는 곳 | 앱이면 `session.login(name,password)`·`session.signup(invite,name,password)`. 결과 `reason` 이 `app_origin_not_listed` 면 서버의 `VOINEY_LAB_APP_ORIGINS` 가 비었다는 뜻(관리자 문구). 로그아웃은 `session.logout()` |
| ④ | `init()` 의 `new WebSocket(scheme+"://"+location.host+"/ws")` 와 그 `onopen` | 앱이면 주소를 `session.socketUrl()` 로, `onopen` 에서 **다른 무엇보다 먼저** `const hello=await session.socketHello(); if(hello) socket.send(hello)` 를 보낸 뒤 `resolve()`. 서버는 앱 출처의 소켓에서 첫 메시지를 토큰으로 읽으므로 `session.start` 가 먼저 가면 1008 로 닫힌다. 서버가 dev 모드면 `socketHello()` 가 `null` 이다. 줄 NET 의 다시 연결(`connection.js`)도 새 소켓마다 같은 순서 |
| ⑤ | 서버가 준 그림 주소(`image.src=figure.url`·`asset.url`·`diagram.url`·`photo.url`, 원문 쪽 값 상자 `src:`)와 내려받기 링크(`.href=\`/api/experiment-reports/…\``, 개정본 비교, 원문 쪽 그림, 증거 파일 — 넷) | `<img>`·`<a>` 는 Bearer 를 실을 수 없다. 앱이면 `resourceObjectUrl(session, url)` 로 `blob:` 주소를 받아 쓰고, 다 쓰면 `URL.revokeObjectURL` |
| ⑥ | 시작할 때 한 번, 그리고 앱이 앞으로 올 때(`document` 의 `resume` 이벤트 또는 `visibilitychange` 가 visible) | `prepareNotifications(cap)` → `chooseNotifier(cap)` → 처음이면 `timerAlerts=createTimerAlerts(chosen.notifier)`, 다음부터는 `timerAlerts.setNotifier(chosen.notifier)`. `kind==="local_notifications"` 이고 `exactAlarm==="denied"` 면 "타이머 알림이 몇 분 늦을 수 있어요 — 정확한 알람 허용" 과 `openExactAlarmSettings(cap)` 단추 |
| ⑦ | `renderCuratedProtocolState` 에서 상태를 받아들인 직후(`curatedProtocolState={...value}`) | `timerAlerts.sync(value, Date.now(), {offsetMs: Date.now()-voineyConnection.serverNow()})`. **서버가 보낸 상태를 받아들인 이 자리에서만** |
| ⑧ | `invalidateCuratedProtocolState`, `init()` 의 `onclose` | `sync` 를 부르지 **않는다**(여기서 상태가 `{attached:false}` 가 되므로 부르면 끊길 때 알림이 지워진다). `connectionLost()` 만 |
| ⑨ | `showExperimentEnded` | `timerAlerts.sync(null)` 로 남은 알림을 지운다 |
| ⑩ | `ensureAudio(generation)` 의 마이크 부분(`openMicStream` → `AudioWorkletNode` → `port.onmessage` 의 `socket.send(p.buffer)`) | 앱이고 `nativeMicrophoneAvailable(cap)` 이면 `getUserMedia`·worklet 을 열지 **않고**(두 녹음이 겹치면 한쪽이 무음이 된다) `mic=createNativeMicrophone(cap,{onFrame,onState})`, `await mic.start()`. `onFrame` 은 worklet 처리기와 같은 조건(`generation===sessionGeneration && sessionActive && socket.readyState===OPEN`)에서 `socket.send(frame)` — 프레임 모양이 같으므로 서버는 차이를 모른다. worklet 쪽의 첫 소리 감지(`micSignalSeen`)는 `onFrame` 에서 Int16 로 같은 계산. `start()` 는 화면이 보일 때만 된다(실험 시작 단추를 누른 순간). `onState` 의 `user_stopped`(알림 줄 끄기)·`permission_denied`·`mic_unavailable` 은 지금의 마이크 복구 띠(`showMicRecovery`)에 `words` 와 [다시 듣기]·[권한 설정 열기] 단추(`mic.openAppSettings()`)로. 실험을 끝내거나 나갈 때 `mic.stop()`. `closeAudio` 도 같은 자리 |
| ⑪ | `keepScreenOn` | 앱에서는 `navigator.wakeLock` 대신(또는 함께) `keepAwake(cap, on)`. 화면 꺼도 듣기가 되면 화면을 켜 둘 필요가 줄어든다 — 화면 담당과 정한다 |
| ⑫ | `connection.js` 의 하트비트(`server.heartbeat`)와 화면 타이머 | 하트비트의 규칙을 `timerAlerts.setRule(lastMinuteRule(message))` 로. 줄 NET 의 화면 타이머 소리와 기기 알림이 같은 순간에 함께 울리므로, 앱일 때 화면 타이머 소리를 끌지 화면 담당과 정한다. 기기의 연결 변화는 `onNetworkChange(cap, cb)` |

연결하는 줄이 지킬 것: 앱이 하는 일은 기기 기능뿐이다. 알림·녹음·로그인 어느 것도 단계·타이머·실험 상태를 바꾸지 않는다. 듣기를 꺼도
서버는 소리를 못 받을 뿐 상태는 그대로다.

### 9.3 정시 알람 (결정 ①) — 설계

- **왜**: `@capacitor/local-notifications` 8.3.1 은 `setExactAndAllowWhileIdle` 를 쓴다. Doze 에서 이것은 "앱마다 9분에 한 번"
  ([Doze](https://developer.android.com/training/monitoring-device-state/doze-standby))이라 1분 전 알림 60초 뒤의 끝 알림이 늦을 수 있다.
  같은 문서: "Alarms set with setAlarmClock() continue to fire normally".
- **어떻게**: `VoineyAlarm.schedule({alarms:[{id, at, kind, key, title, body, channelId}]})` 를 받으면 앱 전용 설정 파일(`voiney_timer_alarms`,
  백업 안 됨)에 문구째 적고 `AlarmManager.setAlarmClock(AlarmClockInfo(at, 앱 열기), 우리 수신기)` 로 건다. 울리면 수신기가 `voiney-timer`
  채널(다리의 `CHANNEL_ID`, 없으면 만듦: 중요도 높음·진동)에 알림을 내고 기록을 지운다. 앱이 닫혀 있어도 된다. 알람 시계가 걸려 있는 동안
  상태 표시줄에 알람 아이콘이 보인다(안드로이드가 그렇게 보인다).
- **정확한 알람이 허용 안 됨**(Android 12+ 설정, 14부터 새 설치는 기본 거부): 다리가 공식 플러그인을 쓴다(늦을 수 있음 — 화면이 알린다).
  플러그인도 그때는 `setAndAllowWhileIdle` 로 내려간다(`schedule` 의 답 `exact:false`).
- **재부팅·앱 업데이트·정확한 알람이 다시 허용됨**(`BOOT_COMPLETED`, `MY_PACKAGE_REPLACED`, `SCHEDULE_EXACT_ALARM_PERMISSION_STATE_CHANGED`):
  저장된 알람마다 `TimerAlarmPolicy.afterBoot` — 아직 앞이면 다시 걸고, 꺼져 있는 동안 지난 **끝**은 한 번 늦게 알린다("휴대폰이 꺼져 있던 14:05에
  울렸어야 할 알림이에요. …"), 지난 **1분 전**은 버린다(늦게 "1분 남았어요"는 틀린 말). 공식 플러그인도 재부팅 때 지난 알림을 곧바로 낸다 — 같은 쪽이다.
- **`pending`** 은 저장된 알람과 함께 시스템이 아직 쥐고 있는지(`armed`, 그 PendingIntent 가 있는지 — 최선의 추정)를 돌려준다. 시스템이 버린 알람은
  다리가 다음 `sync` 에서 다시 건다.
- 앱이 하지 않는 것: 시각을 만들거나 옮기기. 시각과 문구는 서버 상태에서 다리가 만든 것 그대로다.

### 9.4 화면 꺼도 듣기 (결정 ②) — 설계

- **형식(코드에서 확인)**: 웹 화면은 `AudioWorklet` 이 기기 표본율로 약 20 ms 씩 모은 것을 `resamplePcm16` 으로 **16 kHz·모노·PCM16(리틀 엔디언)**
  으로 바꿔 `/ws` 에 **바이너리 메시지 하나에 약 20 ms(640바이트)** 씩 보낸다(`index.html` `ensureAudio`). 서버는 `voiney_lab.audio`
  (`SAMPLE_RATE=16000`, `FRAME_MS=20`, `FRAME_BYTES=640`)로 받고 `FrameBuffer` 가 20 ms 로 다시 자른다. 서버의 `ready` 도 같은 값을 알린다.
- **네이티브**: `ListeningService` 가 `AudioRecord(VOICE_COMMUNICATION, 16000, MONO, PCM_16BIT)` 로 640바이트 × 5(100 ms)씩 읽어 플러그인에 넘기고,
  플러그인이 `frames` 이벤트(base64, 번호)로 페이지에 보낸다. 다리가 640바이트 프레임으로 잘라 페이지가 **자기 소켓 하나로** 보낸다.
  두 번째 소켓을 열지 않는 이유: 서버의 세션(`ListenerSession`)은 연결마다 하나라서 두 번째 소켓은 두 번째 세션이 된다.
  `VOICE_COMMUNICATION` 은 기기의 에코 제거·잡음 억제를 켠다(웹 화면이 `getUserMedia` 에 `echoCancellation`·`noiseSuppression` 을 청하는 것과 같은 뜻).
  세 곳(서버·다리·서비스)의 값이 같은지 `tests/test_lane_app1_background_mic.py` 가 못 박는다.
- **시작**: 앱 화면이 보일 때만(`Lifecycle.STARTED` 이상, 아니면 `not_visible`). 마이크 권한이 없으면 묻고, 거절이면 `permission_denied`
  (설정 열기는 `openAppSettings`). Android 가 화면 밖에서 마이크 서비스를 시작하지 못하게 하므로("you cannot create a microphone foreground service
  while your app is in the background", [문서](https://developer.android.com/develop/background-work/services/fgs/service-types)) 실험 시작 단추를 누른 순간에 시작한다.
- **알림 줄**: 채널 `voiney-listening`(조용함), "VoineyLab 이 듣는 중 · 화면을 꺼도 실험 안내를 계속 들어요." + **[끄기]**. 끄기를 누르면 서비스가 멈추고
  페이지는 `user_stopped` 를 받는다. 마이크를 잃으면 `mic_unavailable`.
- **아무도 받지 않으면 녹음하지 않는다**: 화면의 액티비티가 사라지면(앱 종료) 서비스도 멈춘다. 휴대폰에 소리를 저장하지 않는다. 페이지가 바빠 묶음이
  빠지면 다리가 `missedBatches` 로 센다(숨기지 않음).
- **화면이 꺼진 동안 웹뷰 JS 가 도는가**: Capacitor 8 은 `KeepRunning` 기본값이 참이라 액티비티가 멈춰도 웹뷰 타이머를 멈추지 않는다(`Bridge.onPause` →
  `MockCordovaWebViewImpl.handlePause(keepRunning)` — 코드로 확인). 그래도 Chromium 이 숨은 페이지를 늦출 수 있고, 화면이 꺼진 채 **재생(TTS)이 나오는지**는
  확인하지 못했다 — 11.3절 시험 항목. 안 되면 재생도 네이티브로 옮기는 것이 다음 단계다.
- Play 선언: 마이크 포그라운드 서비스는 설명문과 시연 영상이 필요하다(3.3절).

### 9.5 앱 로그인과 출처 허용 (결정 ③·④) — 설계와 보안 판단

- **문제**: 앱에 묶은 화면의 출처는 `https://localhost`(Android, `androidScheme:"https"`)·`capacitor://localhost`(iOS)라 서버와 다르다. 로그인 쿠키
  `voiney_session`(HttpOnly, SameSite=Lax)은 다른 사이트의 요청에 실리지 않는다.
- **방식**: 서버 설정 `VOINEY_LAB_APP_ORIGINS`(쉼표로 정확한 출처, 기본 빈 값). 그 출처에서 온 요청에만:
  1. `POST /api/auth/login`·`/api/auth/signup` 의 답에 `app_session: {token, expires_in_seconds}`(쿠키는 보내지 않음).
  2. `/api` 는 `Authorization: Bearer <token>` 을 받는다(그 밖의 출처에서 온 Bearer 는 읽지 않는다 — 계정 모드에서 Bearer 는 앱의 문일 뿐이다).
  3. `/ws` 는 쿠키가 없으면 첫 메시지 `{"type":"client.auth","token":…}` 를 최대 10초 기다린다. 다른 첫 메시지·잘못된 토큰·시간 초과는 `authentication_required` 후 1008.
  4. `/api`·`/readyz`·`/healthz` 에 CORS: 사전 요청(OPTIONS)은 세션을 묻기 전에 204, 모든 답(401·429 거절 포함)에 `Access-Control-Allow-Origin`(받은 출처),
     `Vary: Origin`, 노출 머리글 `Retry-After`·`Content-Disposition`. 허용 머리글 `Authorization, Content-Type, Accept`. **`Allow-Credentials` 는 보내지 않는다.**
  5. `/ws` 출처 검사: 목록이 비면 지금처럼 검사 없음. 목록이 있으면 브라우저 소켓은 서버 자신의 호스트(`Host` 또는 프록시의 `X-Forwarded-Host`), 목록의 앱 출처,
     또는 Origin 없음(브라우저가 아님 — 로그인은 여전히 필요)만 받고 그 밖은 `origin_not_allowed` 로 1008. **조사 결과: 줄 APP1 전까지 `/ws` 에는 출처 검사가
     전혀 없었다**(결정 문장의 "출처 검사"는 새로 만든 것이다). 쿠키 소켓의 교차 사이트 도용은 지금 SameSite=Lax 가 막는다.
  6. 목록이 잘못되면(`*`, 경로, `null`) `/readyz` 가 503, CORS 없음, 소켓은 `configuration_invalid` 로 닫힌다.
- **같은 세션**: 토큰은 쿠키와 같은 세션 표(`accounts.sqlite` 의 `sessions`, 토큰의 SHA-256 만)다. 수명 30일, 로그아웃·비밀번호 바꾸기(다른 기기 세션 끝)·
  관리자가 계정 끄기로 끝난다. 스키마는 바뀌지 않았다.
- **앱 쪽**: `VoineySessionStore` 가 Android Keystore 의 AES-256-GCM 키(키는 Keystore 밖으로 나오지 않음)로 봉해 앱 전용 설정 파일에 암호문만 둔다
  (`allowBackup="false"`). 다리는 토큰을 그 저장소와 함수 안에만 두고 URL·로그·돌려주는 값에 싣지 않는다. 401 이면 잊는다.
- **보안 판단**:
  - 웹의 HttpOnly 쿠키와 달리 **앱의 토큰은 웹뷰 JS 가 읽는다**. 앱의 화면은 묶은 파일뿐이고 외부 스크립트를 읽지 않으므로 XSS 위험은 웹과 같은 크기다.
    대신 토큰이 기기의 다른 앱·백업·로그로 새지 않게 했다(Keystore, 백업 끔, Capacitor 로그 끔). 토큰을 아예 JS 밖에 두려면 모든 HTTP·소켓을
    네이티브가 맡아야 하는데, 소켓이 웹뷰의 것이라 이번 범위 밖이다.
  - 토큰을 받는 문은 비밀번호(또는 초대 링크)다 — 이 경로가 새 권한을 주지 않는다. 비밀번호를 가진 사람은 이미 쿠키를 받을 수 있다.
  - `Origin` 은 브라우저가 붙이고 페이지가 바꿀 수 없다. 브라우저가 아닌 프로그램은 바꿀 수 있지만, 그 프로그램은 비밀번호로 쿠키 값도 받을 수 있으므로
    더 열리는 것이 없다. Bearer 는 자동으로 실리지 않으므로 CSRF 대상이 아니다.
  - `Allow-Credentials` 가 없으므로 목록에 있는 출처라도 브라우저 쿠키 세션을 쓸 수 없다. `https://localhost` 를 넣으면 같은 컴퓨터의 다른 로컬 웹 서버도
    그 출처가 되지만, 토큰 없이 할 수 있는 것은 로그인 화면과 같다.
  - 토큰은 URL·쿼리에 싣지 않는다(프록시·접속 기록에 남지 않게). 서버 로그도 토큰을 적지 않는다(테스트가 확인).
  - **파일럿의 Caddy 기본 인증과 함께 쓸 수 없다**: 기본 인증도 `Authorization` 머리글을 쓰고, CORS 사전 요청(OPTIONS)은 인증 정보를 싣지 않아 Caddy 가
    401 로 막는다. 앱을 실제 서버에 붙이는 날에는 PILOT_GUIDE 7-3절의 열린 결정("기본 인증을 둘지, 계정 로그인으로 갈음할지")을 **계정 로그인으로**
    정해야 한다. 서버 주소 모드(`VOINEY_APP_SERVER_URL`)는 같은 출처라 쿠키·기본 인증이 그대로 된다.

## 10. 빌드

### 10.1 Android 디버그 APK — 이 서버에서 만들었다 (줄 APP0 2026-10-18, 줄 APP1 2026-10-19 에 다시)

| 항목 | 값 |
|---|---|
| 결과 | 줄 APP1: `mobile/scripts/build-android-debug.sh` 로 `BUILD SUCCESSFUL in 1m 12s`(Gradle 캐시, 내려받기 없음 — 플러그인 셋은 새 의존성 없이 앱 안 코드다). 줄 APP0 의 첫 빌드는 5분 37초 |
| 파일 | `mobile/android/app/build/outputs/apk/debug/app-debug.apk`(추적 안 함). 같은 파일을 `~/app-build/out/voineylab-app1-debug.apk` 에 복사(줄 APP0 의 것은 `voineylab-app0-debug.apk` 로 남김) |
| 크기 | 4,702,879 바이트(4.5 MiB), sha256 `77eda0f172173a31676bae0d7f789241f589d8d0bea9663164ae2fd3e3e50fff`. 줄 APP0 마지막 빌드는 4,525,666 바이트 |
| 들어 있는 것 | 플러그인 `VoineyAlarm`·`VoineyMicrophone`·`VoineySessionStore`(dex 에서 확인), 수신기 둘·마이크 서비스 하나(`foregroundServiceType=microphone`), `assets/public/app-config.js`(이 빌드는 `VOINEY_APP_API_URL` 없이 만들어 `serverUrl:null` — 기기 시험 화면에 서버 주소를 적는다), `capacitor.config.json` 의 `loggingBehavior: "none"` |
| 패키지 | `com.voiney.lab`, versionCode 1, compile/targetSdk 36 |
| 권한(APK 안, `aapt2 dump badging`) | INTERNET, RECORD_AUDIO, MODIFY_AUDIO_SETTINGS, FOREGROUND_SERVICE, FOREGROUND_SERVICE_MICROPHONE, SCHEDULE_EXACT_ALARM, RECEIVE_BOOT_COMPLETED, POST_NOTIFICATIONS, WAKE_LOCK, ACCESS_NETWORK_STATE (+ AndroidX 내부 권한 하나) |
| 첫 화면 | `device-check.html`(묶기 모드, `appStartPath`) |
| 서명 | 디버그 키(Gradle 이 만든 것, 저장소 밖). **스토어에 올릴 수 없다** — 출시용은 사람이 만든 업로드 키로 `bundleRelease` |
| 도구 | Temurin JDK 21.0.12.1+1, Android 명령줄 도구 23.0, platforms;android-36, build-tools;36.0.0, Gradle 8.14.3(래퍼), AGP 8.13.0, Node 22.22.1 — 모두 `~/app-build/` 아래, sudo 없이 |
| 디스크 | `~/app-build/` 합계 2.7 GB(줄 APP1 에서 늘지 않음). 저장소 안(추적 안 함): `mobile/node_modules` 40 MB(줄 APP1 에서 새로 설치), 빌드 산출물 약 23 MB |

다시 만들기: `mobile/scripts/build-android-debug.sh`(도구를 내려받는 법은 `mobile/README.md`). 묶은 화면이 붙을 서버를 넣어 만들려면
`VOINEY_APP_API_URL=https://pilot.<도메인> mobile/scripts/build-android-debug.sh`. 기기 시험 순서는 11.3절.

### 10.2 맥에서 iOS 빌드하는 순서

EC2 에는 Xcode 가 없어서 iOS 는 이 줄에서 만들지 않았다. 맥에서:

1. Xcode 26 이상과 명령줄 도구(`xcode-select --install`), Node 22 이상, CocoaPods 또는 Swift Package Manager(어느 쪽이 기본인지는 이 줄에서 확인하지 않았다 —
   `npx cap add ios` 가 묻거나 고르는 것을 따른다).
2. `cd mobile && npm ci && node scripts/prepare-web.mjs && npm install @capacitor/ios@8.5.3 --save-exact && npx cap add ios`
   (`@capacitor/ios` 와 `ios/` 폴더는 그 줄에서 커밋).
3. `ios/App/App/Info.plist` 에 `NSMicrophoneUsageDescription`(11.2절 문구), 백그라운드 듣기를 만들 때 `UIBackgroundModes` 에 `audio`.
4. `ios/App/App/PrivacyInfo.xcprivacy` — 플러그인이 쓰는 required-reason API 를 적는다(Capacitor 안내: <https://capacitorjs.com/docs/ios/privacy-manifest>).
5. `npx cap sync ios && npx cap open ios` → Xcode 에서 Signing & Capabilities 에 팀(Apple Developer Program 계정)을 고르고, Push 가 아닌
   로컬 알림만 쓰므로 Push Notifications 능력은 넣지 않는다. 시간 민감 알림을 쓰면 Time Sensitive Notifications 능력.
6. 실제 아이폰을 연결해 Run. 11.3절의 순서를 iOS 에서도 같은 번호로(정확한 알람 항목 2·3·6 은 iOS 에 없음).
7. 배포: Product → Archive → App Store Connect 에 올려 TestFlight 로 내부 시험.

## 11. 사람이 할 일

### 11.1 계정과 도구

| # | 할 일 | 비용·조건 (확인 2026-10-18) | 비고 |
|---|---|---|---|
| 1 | **Apple Developer Program** 등록 — 회사(조직)로 | "99 USD per membership year" ([링크](https://developer.apple.com/programs/whats-included/)). 수수료 면제는 비영리·교육기관·정부만 | 조직 등록에는 D-U-N-S 번호가 필요하다(Apple 쪽 요건 문장은 이 줄에서 확인하지 않았다 — 등록 화면에서 확인) |
| 2 | **Google Play Console** 등록 — 회사(조직) 계정 권장 | "US$25 one-time registration fee" ([링크](https://support.google.com/googleplay/android-developer/answer/6112435)). 조직 계정은 D-U-N-S 번호 필요([링크](https://support.google.com/googleplay/android-developer/answer/13634885)) | 2023-11-13 이후 만든 **개인** 계정은 "a minimum of 12 testers … at least 14 days" 비공개 테스트 뒤에야 출시([링크](https://support.google.com/googleplay/android-developer/answer/14151465)). 조직 계정이 빠지는지는 8절 10 |
| 3 | 앱 id(패키지 이름) 확정 | — | 지금은 자리표시 `com.voiney.lab`. **스토어에 처음 올린 뒤에는 바꿀 수 없다.** 회사 도메인을 거꾸로 쓴 이름으로 정해 `mobile/capacitor.config.js`, Android `app/build.gradle` 의 `namespace`·`applicationId`, `MainActivity.java` 의 패키지 폴더를 함께 바꾼다 |
| 4 | 맥과 Xcode 26 이상 | "Capacitor 8 requires Xcode 26.0+." ([링크](https://capacitorjs.com/docs/updating/8-0)) | EC2 에는 Xcode 가 없다. iOS 빌드·서명·제출은 맥에서만 |
| 5 | 서명 키 보관 | — | Android 업로드 키(keystore)와 Apple 인증서는 **저장소 밖** 비밀 보관소에. 잃으면 업데이트를 못 올린다(Google Play 앱 서명을 쓰면 업로드 키는 재설정 가능 — 등록 때 확인) |
| 6 | Apple 문의: 결제 구성 | — | 개인 앱 안 구독 + 기관 직접 판매를 한 앱에 둘 수 있는지(3.1.3(c) 해석, 6.2절). 답에 따라 결제 설계를 정한다 |

### 11.2 스토어 등록에 필요한 것

| 항목 | Apple | Google |
|---|---|---|
| 개인정보 처리방침 URL | 필수, 앱 안에서도 링크(5.1.1(i)) | 필수(데이터 보안 양식과 함께) |
| 외부 AI 로 데이터가 간다는 고지와 동의 | 5.1.2(i) "including with third-party AI, and obtain explicit permission" | 데이터 보안 양식에 "공유" 로 적는다 |
| 마이크 권한 문구 | `NSMicrophoneUsageDescription` 예: "실험 단계를 음성으로 안내하고 말로 진행하려고 마이크를 씁니다. 들은 소리는 음성 인식을 위해 서버와 외부 음성 인식 공급자로 보냅니다." | 실행 중 권한 요청 앞에 같은 뜻의 설명 화면 |
| 백그라운드 사유 | 심사 메모: 화면을 끈 채 음성으로 실험을 진행하는 영상, `audio` 모드 사유(2.5.4) | 마이크 포그라운드 서비스 선언: 설명 + 시연 영상 링크 |
| 알림·알람 | 알림 권한 요청 시점(실험 시작 때) | `SCHEDULE_EXACT_ALARM` 허용 안내 화면, Android 13+ 알림 권한 |
| 계정 삭제 | 앱 안에서(5.1.1(v)) — 줄 ON | Play 의 계정 삭제 요구(데이터 삭제 URL) — 등록 때 확인 |
| 시험 계정 | 심사용 로그인과 실험 하나를 끝까지 해 볼 수 있는 프로토콜 | 같음 |
| 스크린샷·설명·아이콘 | 기기 크기별 스크린샷 | 기능 그래픽·스크린샷 |
| 상태 표현 | "MVP 시제품 — 현장 미검증", 의료·GLP 검증 제품이 아니라는 문장 | 같음 |

### 11.3 실제 기기 시험 순서 (Android 디버그 APK, 줄 APP1 판)

준비: Android 8 이상 휴대폰 한 대(가능하면 **Android 14 이상** 한 대 더 — 정확한 알람 기본 거부, 마이크 서비스 규칙이 다르다), USB 디버깅을 켜고
`adb`(Android SDK platform-tools)가 있는 컴퓨터, 블루투스 헤드셋. 결과는 기기 모델·Android 버전·번호마다 결과·늦은 초를 적어 다음 줄에 넘긴다.

**A. 설치**

1. 파일: `~/app-build/out/voineylab-app1-debug.apk`(10.1절의 크기·sha256 으로 확인: `sha256sum`). 컴퓨터로 옮긴다.
2. 휴대폰: 설정 → 휴대전화 정보 → 빌드 번호 7번 → 개발자 옵션 → USB 디버깅 켬. USB 로 잇고 `adb devices` 에 보이는지.
3. `adb install -r voineylab-app1-debug.apk`. 줄 APP0 의 앱이 있으면 같은 패키지라 덮어쓴다(`-r`). 서명 충돌이면 `adb uninstall com.voiney.lab` 뒤 다시.
   (USB 없이: 파일을 휴대폰에 옮겨 열고 "출처를 알 수 없는 앱" 허용.) 앱을 열면 기기 시험 화면이 뜬다.

**B. 정시 알람 (결정 ①)**

4. "1. 알림 권한·채널 준비" → 알림 허용. 결과 줄에 `알림 표시: granted` 와 **`타이머 알림 방식`**. Android 14 새 설치면 `정확한 알람: denied`,
   방식은 "공식 알림 플러그인" 이 정상이다.
5. "2. 정확한 알람 설정 열기" → 허용 → 돌아와 1번을 다시 → `정확한 알람: granted`, 방식 **"알람 시계(setAlarmClock…)"**.
6. "3. 시험 타이머 예약" → 예약 2건(지금+5분, 지금+6분). 상태 표시줄에 **알람 시계 아이콘**이 보이는지. "5. 예약된 알림 보기" 가 두 건.
7. **화면 끔**: 화면을 끄고 둔다. 5분 뒤 "시험단계 · 1분 전", 6분 뒤 "시험단계 · 타이머 끝" — 소리·진동, 늦은 초.
8. **Doze 강제**: 3번을 누르고 화면을 끈 뒤 컴퓨터에서 `adb shell dumpsys deviceidle force-idle`(답이 `Now forced in to deep idle mode`).
   두 알림이 **둘 다 제시각**에 오는지 — 줄 APP0 의 걱정(두 번째가 9분 넘게 늦음)이 알람 시계로 풀렸는지의 증거다. 끝나면 `adb shell dumpsys deviceidle unforce`.
   비교하려면 정확한 알람을 끄고(방식이 공식 플러그인) 같은 시험을 한 번 더.
9. **취소**: 3번 → 곧바로 "4. 시험 타이머 취소" → 5번이 `없음`, 알람 아이콘이 사라지고 알림이 오지 않는지.
10. **재부팅**: 3번 → 휴대폰을 껐다 켠다(잠금 해제까지). 알림이 제시각에 오는지(앱을 열지 않은 채). 그리고 3번 → 바로 끄고 **6분 넘게 꺼 둔 뒤** 켠다 →
    "휴대폰이 꺼져 있던 HH:MM에 울렸어야 할 알림이에요. …" 가 한 번 오고, "1분 전" 은 오지 않는지.
11. **비행기 모드**: 3번 → 비행기 모드 켜고 화면 끔 → 두 알림이 제시각에 오는지(알림은 네트워크가 필요 없다).

**C. 화면 꺼도 듣기 (결정 ②)**

12. "9. 화면 꺼도 듣기 시작" → 마이크 권한 허용 → 알림 줄에 **"VoineyLab 이 듣는 중"** 과 **[끄기]**. 10초마다 "받은 프레임 … 가장 큰 소리 …%" 가 찍힌다
    (10초에 약 500프레임). 말하면 % 가 오르는지.
13. **화면 끔**: 1분 동안 화면을 끄고 말한다 → 켜서 로그를 본다: 그 사이에도 10초마다 줄이 찍혔는지, 프레임 수가 줄지 않았는지, "빠진 묶음" 이 0 인지.
    (이것이 8절 11 — 웹뷰가 화면 꺼진 채 프레임을 받는지의 첫 증거다.) 다른 앱을 연 채로도 한 번.
14. 알림 줄의 **[끄기]** → 로그에 "알림 줄의 '끄기'로 듣기를 껐어요…", 알림 줄이 사라지는지. "10. 듣기 끄기" 로도 한 번.
15. **권한 거절**: 앱 정보 → 권한 → 마이크 "허용 안함" → 9번 → "마이크 권한이 거절됐어요…" 문구. "11. 마이크 권한 설정 열기" 로 설정이 열리는지.
16. **받을 화면이 없으면 녹음하지 않음**: 듣는 중에 최근 앱 목록에서 앱을 쓸어 닫는다 → 알림 줄이 사라지는지.
17. 블루투스 헤드셋을 이은 채 12번 — 헤드셋 마이크로 들어오는지(% 가 헤드셋에 말할 때 오르는지). 안 되면 `setCommunicationDevice` 가 다음 일이다.

**D. 앱 로그인 (결정 ③④) — 서버 준비가 필요하다**

18. 서버(내부 시험 서버, 기본 인증 **없는** 것 — 9.5절 끝): `.env` 에 `VOINEY_LAB_AUTH_MODE=accounts`, `VOINEY_LAB_APP_ORIGINS=https://localhost`,
    작업공간 켬, `scripts/pilot_invite.py` 로 계정 하나. HTTPS 주소가 있어야 한다.
19. 기기 시험 화면 아래쪽에 서버 주소·이름·비밀번호 → "12. 로그인" → "로그인됨: 이름". 틀린 비밀번호면 "로그인 안 됨: login_refused (401)".
    `VOINEY_LAB_APP_ORIGINS` 를 비우고 다시 하면 "app_origin_not_listed" — 서버 목록이 문이라는 확인.
20. "14. 웹소켓 열기" → "서버: ready — 앱 로그인으로 연결 성공". 앱을 완전히 닫았다가 다시 열고 "13. 저장된 로그인 확인" → 로그인됨(토큰이 보안 저장소에서 돌아옴).
21. "15. 로그아웃" → 13번이 "로그인 안 됨", 14번이 `1008`.
22. **토큰이 기기 로그에 없는지**: 19–21번을 한 뒤 `adb logcat -d | grep -ci -e bearer -e client.auth` 가 `0`(Capacitor 로그를 끈 확인).

**E. 그 밖 (줄 APP0 항목)**

23. "6. 화면 켜 두기" 켬/끔, "7. 네트워크 상태" 변화 기록, "8. 앱 화면 열기" → 지금의 화면이 뜨지만 묶기 모드라 서버에 붙지 못하는 것이 정상(9.2절 연결 전).
24. 서버 주소 모드(내부 시험): `VOINEY_APP_SERVER_URL=https://pilot.<도메인>/ mobile/scripts/build-android-debug.sh` → 기본 인증 창이 뜨는지, 웹 화면으로
    음성으로 한 단계를 진행할 수 있는지, **화면을 끄면 웹 마이크가 멈추는지**(멈추는 것이 예상 — 그래서 결정 ②).
