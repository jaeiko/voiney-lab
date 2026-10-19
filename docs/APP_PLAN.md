# 스토어 앱 계획 — 화면이 꺼져도 듣기, 앱 밖 타이머 알림, 끊겨도 울리는 타이머 (줄 APP0)

2026-10-18 작성. 조사와 뼈대만 담았다. 실제 기기에서는 아직 아무것도 시험하지 않았다. 스토어 정책·수수료는 아래에 적은 날짜에
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

이 줄의 뼈대가 실제로 선언한 것: `INTERNET`, `RECORD_AUDIO`, `MODIFY_AUDIO_SETTINGS`(앱), `POST_NOTIFICATIONS`·`SCHEDULE_EXACT_ALARM`·
`RECEIVE_BOOT_COMPLETED`·`WAKE_LOCK`(알림 플러그인이 합쳐 넣음). 포그라운드 서비스 권한은 서비스 코드가 생길 때 넣는다 —
쓰지 않는 유형을 선언하면 Play 선언 대상만 늘어난다.

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

1. 이 줄: 뼈대, 알림 다리(1분 전·끝), Android 디버그 APK, 기기 시험 화면.
2. 다리를 `index.html` 에 연결(9.2절의 연결 지점) + 화면을 앱에 묶기 위한 서버 주소 설정(5절). 화면 담당과 함께.
3. Android 마이크 포그라운드 서비스 플러그인: 실험을 시작할 때(화면이 보일 때) 서비스 시작, 네이티브 `AudioRecord` 로 받아
   같은 `/ws` 로 PCM 을 보냄. 재생(TTS)도 네이티브. 블루투스 마이크는 `setCommunicationDevice`.
4. 끝 알림을 `setAlarmClock` 으로(Doze 에서도 정시).
5. iOS: 맥에서 `npx cap add ios`, `audio` 백그라운드 모드 + AVAudioEngine 캡처, AlarmKit 또는 시간 민감 알림.

## 5. 화면: 앱에 묶을까, 서버 주소로 열까

**권고: 스토어 빌드는 묶는다. 서버 주소로 여는 것은 파일럿 내부 시험만.**

| | 묶기 (`webDir`) | 서버 주소 (`server.url`) |
|---|---|---|
| Capacitor 문서 | 기본 방식 | "This is intended for use with live-reload servers. **This is not intended for use in production.**" ([config](https://capacitorjs.com/docs/config)) |
| Apple 4.2 | 앱 안에 화면이 있다 | 원격 웹사이트를 띄우는 앱으로 보일 위험이 더 크다 |
| 오프라인으로 앱을 열 때 | 앱 화면이 뜨고 마지막 상태·예약된 알림을 보여 줄 수 있다 | 웹뷰 오류 → `server.errorPath`(`offline.html`) 만 뜬다. Android 에서는 그 쪽이 플러그인을 못 쓴다("On Android the html file won't have access to Capacitor plugins") |
| 화면 고침 배포 | 앱 업데이트(심사)가 필요 | 서버 배포만으로 바뀐다 |
| 지금 `index.html` 로 되나 | **아직 안 된다.** 화면이 서버를 `location.host` 로 찾는다(9.2절 ⑤). 앱 안에서는 그 값이 `localhost` 다 | 된다 |

그래서 뼈대(`mobile/capacitor.config.js`)는 두 방식을 빌드할 때 고른다. 기본은 묶기, `VOINEY_APP_SERVER_URL=https://…` 를 주면 서버 주소.
묶기로 실제 서버에 붙으려면 9.2절 ⑤의 고침과 서버 쪽 준비(앱 출처 `https://localhost` 를 웹소켓·API 가 받기, 로그인 쿠키 대신 토큰 또는
`SameSite=None`)가 필요하다. 화면 고침을 심사 없이 내보내는 일(라이브 업데이트)은 이 줄에서 다루지 않는다.

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

## 9. 앱 뼈대와 기기 기능 다리

### 9.1 뼈대 (`mobile/`)

| 경로 | 무엇 |
|---|---|
| `mobile/package.json` | Capacitor 8.5.3, `@capacitor/local-notifications` 8.3.1, `@capacitor/network` 8.0.1, `@capacitor/app` 8.1.2, `@capacitor-community/keep-awake` 8.0.1 (정확한 버전으로 고정) |
| `mobile/capacitor.config.js` | 앱 id `com.voiney.lab`(자리표시 — 11절), 이름 VoineyLab. 기본은 화면 묶기, `VOINEY_APP_SERVER_URL=https://…` 이면 서버 주소, `VOINEY_APP_START_PATH` 로 첫 쪽 |
| `mobile/scripts/prepare-web.mjs` | `mobile/www/`(추적 안 함)를 서버 정적 화면 + `mobile/www-src/` 로 만든다 |
| `mobile/www-src/offline.html` | 서버 주소 모드에서 서버를 못 열 때의 화면 |
| `mobile/www-src/device-check.html` | 디버그 빌드의 기기 시험 화면: 6분 시험 타이머(5분 뒤 "1분 전", 6분 뒤 끝)로 알림 예약·취소, 정확한 알람 설정, 화면 켜 두기, 네트워크 변화 |
| `mobile/android/` | `npx cap add android` 결과. 고친 곳은 `AndroidManifest.xml` 뿐: `RECORD_AUDIO`·`MODIFY_AUDIO_SETTINGS`, `allowBackup="false"`(웹뷰 저장소가 클라우드 백업으로 나가지 않게) |

### 9.2 다리 `src/voiney_lab/static/native-bridge.js`

서버가 준 단계 타이머(`timers.step`)로 기기 알림 두 개("1분 전"과 끝)를 예약하고, 다음 서버 상태에 맞춰 취소·변경한다.
번들러 없이 `<script>` 로 읽히고(`window.VoineyNativeBridge`), node 에서는 `require` 로 읽힌다. 시험:
`node --test tests/native_bridge/timer_alerts.test.js`(pytest 에서는 `tests/test_native_bridge.py`).

- `planTimerAlerts(state, {nowMs, offsetMs, rule})`: 실험이 진행 중(`attached`·`active`)이고 단계 타이머가 `running` 이며 `started_at`·`deadline_at` 이
  읽힐 때만 알림을 만든다. 이미 지난 시각은 넣지 않는다. 일시정지는 알림을 지우지 않는다(서버도 단계 타이머를 멈추지 않는다).
- "1분 전"은 **서버가 말하는 규칙 그대로** 정한다: 5분 이상인 타이머에만, 끝 60초 전(`curated_protocol.py` 의 `TIMER_LAST_MINUTE_FROM_SECONDS`·
  `TIMER_LAST_MINUTE_SECONDS`, 줄 NET 부터 서버 하트비트가 `timer_last_minute_from_seconds`·`timer_last_minute_seconds` 로 보낸다).
  기본값은 `LAST_MINUTE_RULE`(300·60)이고, 하트비트를 받으면 `lastMinuteRule(message)` → `setRule(rule)`. 기본값이 서버 상수와 같은지는
  `tests/test_native_bridge.py` 가 못 박는다. 5분보다 짧은 타이머는 끝 알림만 있다 — 줄 NET 의 화면 타이머와 같다.
- 알림 id 는 `step_id|started_at|종류` 의 해시(양의 31비트). 같은 타이머는 같은 id 라서 같은 상태가 여러 번 와도 다시 예약하지 않고,
  다시 시작한 타이머(새 `started_at`)는 옛 쌍을 지우고 새 쌍을 넣는다.
- 기기 시계와 서버 시계의 차이: 화면이 잰 값(줄 NET 의 `voineyConnection.serverNow()` → `Date.now()-serverNow()`)을 `sync` 의 세 번째 인자
  `{offsetMs}` 로 주면 그것을 쓰고, 없으면 `remaining_seconds` 로 어림해 3초 넘게 다를 때만 맞춘다(`clockOffsetMs`).
- 문구는 서버의 끝 문구(`_timer_end_words`)를 따른다: 최소 시간은 "최소 시간 2시간이 지났어요."(끝났다고 하지 않음),
  최대 시간은 "최대 시간 15분이 됐어요.", 그 밖은 "타이머 15분이 끝났어요." — 모두 "앱에서 확인해 주세요."로 맺는다. 단계가 끝났다고 말하지 않는다.
- `createTimerAlerts(notifier).sync(state, receivedAtMs, {offsetMs})` 는 기기의 예약 목록(우리 것만, `extra.tag`)과 비교해 취소·예약하고, 동시에 온 두
  상태가 섞이지 않게 차례로 돈다. `connectionLost()` 는 일부러 아무것도 하지 않는다.
- Capacitor 가 웹뷰에 넣는 다리(`Capacitor.nativePromise`)로 플러그인을 부른다. 알림 채널 `voiney-timer`(중요도 5), `allowWhileIdle: true`.

**연결 지점** (`static/index.html`, 2026-10-19 `dev`(줄 NET #92 합친 뒤) 기준 줄 번호. 이 줄에서는 고치지 않았다):

| # | 자리 | 할 일 |
|---|---|---|
| ① | 4591줄 `<script src="/connection.js"></script>` 옆 | `<script src="/native-bridge.js"></script>`(줄 NET 의 `connection.js` 처럼 따로 읽힘). 앱이 아니면(`isNativeApp` 거짓) 아무것도 하지 않게 |
| ② | 시작할 때 한 번 | `prepareNotifications(cap)`(알림 권한·채널·정확한 알람 여부). 정확한 알람이 거부면 "타이머 알림이 몇 분 늦을 수 있어요 — 설정에서 허용" 안내 |
| ③ | 2370줄 `renderCuratedProtocolState` 에서 상태를 받아들인 직후(`curatedProtocolState={...value}`, 2380줄) | `timerAlerts.sync(value, Date.now(), {offsetMs: Date.now()-voineyConnection.serverNow()})`. **서버가 보낸 상태를 받아들인 이 자리에서만** 부른다 |
| ④ | 2022줄 `invalidateCuratedProtocolState`, 2892줄 웹소켓 `onclose` | `sync` 를 부르지 **않는다**(여기서 상태가 `{attached:false}` 가 되므로 부르면 끊길 때 알림이 지워진다). 줄 NET 이 되살릴 끊김(`voineyConnection.lost()` 가 참)이면 `onclose` 는 거기서 돌아가고, 아니면 상태를 비운다 — 어느 쪽이든 `connectionLost()` 만 |
| ⑤ | 2892줄 `new WebSocket(scheme+"://"+location.host+"/ws")`, `fetch("/api/…")` 16곳·`/readyz`, 3042줄 `/mic-capture-worklet.js`, 9줄 manifest, 4591줄 `/connection.js` | 화면을 앱에 묶으려면(5절) 서버 기준 주소를 설정에서 받아 붙인다. 서버 주소 모드에서는 고칠 것 없음 |
| ⑥ | 1365줄 `showExperimentEnded` | 실험이 끝나면 `sync(null)` 로 남은 알림을 지운다(서버의 끝난 상태가 ③으로 오면 거기서도 지워진다) |
| ⑦ | 1490줄 `keepScreenOn` | 앱에서는 `navigator.wakeLock` 대신(또는 함께) `keepAwake(cap, on)` |
| ⑧ | 2931줄 `getUserMedia`, 4548줄 `visibilitychange` | 화면이 꺼져도 듣기는 네이티브 포그라운드 서비스가 맡는다(4절 다음 단계 3). 그 전까지는 지금처럼 화면이 보일 때만 |
| ⑨ | `connection.js` 의 하트비트(`server.heartbeat`)와 화면 타이머 | 하트비트의 규칙을 `setRule(lastMinuteRule(message))` 로 넘긴다. 줄 NET 의 화면 타이머는 끊긴 동안 화면이 켜져 있을 때 소리·진동으로 알리고 "화면이 꺼지거나 다른 앱으로 가면 화면 타이머 알림이 울리지 않을 수 있어요" 라고 쓴다 — 앱에서는 기기 알림이 같은 순간에 함께 울리므로, 연결하는 줄에서 앱일 때 그 문구와 소리를 어떻게 할지 화면 담당과 정한다. 기기의 연결 변화는 `onNetworkChange(cap, cb)` |

## 10. 빌드

### 10.1 Android 디버그 APK — 이 서버에서 만들었다 (2026-10-18)

| 항목 | 값 |
|---|---|
| 결과 | `BUILD SUCCESSFUL in 5m 37s`(잠금을 얻은 뒤의 Gradle 시간), 214 tasks |
| 파일 | `mobile/android/app/build/outputs/apk/debug/app-debug.apk`(추적 안 함). 같은 파일을 `~/app-build/out/voineylab-app0-debug.apk` 에 복사 |
| 크기 | 4,371,519 바이트(4.2 MiB), sha256 `de3082354dc4830e3ff89e9637135211dd8a81fd1c4958609d65613a5ccfe0f6` |
| 패키지 | `com.voiney.lab`, versionCode 1, compile/targetSdk 36 |
| 권한(APK 안, `aapt2 dump badging`) | INTERNET, RECORD_AUDIO, MODIFY_AUDIO_SETTINGS, RECEIVE_BOOT_COMPLETED, WAKE_LOCK, POST_NOTIFICATIONS, SCHEDULE_EXACT_ALARM, ACCESS_NETWORK_STATE (+ AndroidX 내부 권한 하나) |
| 첫 화면 | `device-check.html`(묶기 모드, `appStartPath`) |
| 서명 | 디버그 키(Gradle 이 만든 것, 저장소 밖). **스토어에 올릴 수 없다** — 출시용은 사람이 만든 업로드 키로 `bundleRelease` |
| 도구 | Temurin JDK 21.0.12.1+1, Android 명령줄 도구 23.0, platforms;android-36, build-tools;36.0.0, Gradle 8.14.3(래퍼), AGP 8.13.0, Node 22.22.1 — 모두 `~/app-build/` 아래, sudo 없이 |
| 디스크 | `~/app-build/` 합계 2.7 GB: Gradle 캐시 1.4 GB, Android SDK 635 MB, 내려받은 원본 371 MB(지워도 됨), JDK 346 MB. 저장소 안: `mobile/node_modules` 40 MB, 빌드 산출물 약 23 MB(모두 추적 안 함) |

다시 만들기: `mobile/scripts/build-android-debug.sh`(도구를 내려받는 법은 `mobile/README.md`). 기기 시험 순서는 11.3절.

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

### 11.3 실제 기기 시험 순서 (Android 디버그 APK)

준비: Android 8 이상 휴대폰 한 대(가능하면 Android 14 이상 한 대 더), USB 디버깅을 켜고 `adb` 가 있는 컴퓨터, 블루투스 헤드셋.

1. 설치: `adb install -r app-debug.apk`(10.1절의 파일). 앱을 열면 기기 시험 화면이 뜬다.
2. "1. 알림 권한·채널 준비" → 알림 허용. 결과 줄에 `알림 표시: granted`. Android 14 새 설치면 `정확한 알람: denied` 가 정상이다.
3. "2. 정확한 알람 설정 열기" → 허용 → 돌아와 1번을 다시 눌러 `granted` 확인.
4. "3. 시험 타이머 예약" → 결과에 예약 2건과 두 시각(지금+5분, 지금+6분 — 서버 규칙대로 5분 이상 타이머만 "1분 전"이 있다).
   **화면을 끄고 와이파이·데이터를 끈다.** 5분 뒤 "시험단계 · 1분 전", 6분 뒤 "시험단계 · 타이머 끝" 알림이 오는지, 소리·진동이 나는지 적는다.
5. 3번을 다시 누르고 곧바로 "4. 시험 타이머 취소" → "5. 예약된 알림 보기" 가 `없음` 이고 알림이 오지 않는지.
6. Doze: 3번을 누른 뒤 컴퓨터에서 `adb shell dumpsys deviceidle force-idle` → 두 알림이 제때 오는지, 늦으면 몇 초 늦는지 적는다
   (3.1절의 걱정 — 두 번째가 9분 넘게 늦으면 `setAlarmClock` 플러그인이 필요하다는 증거). 끝나면 `adb shell dumpsys deviceidle unforce`.
7. 재부팅: 3번을 누르고 바로 휴대폰을 껐다 켠다 → 알림이 그래도 오는지(플러그인이 재부팅 뒤 예약을 되살린다).
8. "6. 화면 켜 두기" 켬 → 화면이 꺼지지 않는지, 끔 → 평소대로 꺼지는지.
9. "7. 네트워크 상태" → 와이파이를 껐다 켤 때 "네트워크 변화" 줄이 찍히는지.
10. "8. 앱 화면 열기" → 지금의 화면이 뜬다. 묶기 모드라 서버에 붙지 못하는 것이 정상이다(5절, 9.2절 ⑤).
11. 서버 주소 모드(내부 시험): `VOINEY_APP_SERVER_URL=https://pilot.<도메인>/` 로 다시 빌드 → 기본 인증 창이 뜨는지(5절), 마이크 권한을 묻는지,
    음성으로 한 단계를 진행할 수 있는지, 블루투스 헤드셋 마이크로 말해도 알아듣는지. **화면을 끄면 듣기가 멈추는지** 적는다 —
    멈추면 4절 다음 단계 3(포그라운드 서비스)이 필요하다는 확인이다.

결과(기기 모델·Android 버전·각 번호의 결과·늦은 초)를 줄 보고서에 적어 다음 줄에 넘긴다.
