// Lane APP1, decision 1: the alarm plugin's boot and validation rules
// (mobile/android/.../alarm/TimerAlarmPolicy.java) without a device.
// tests/test_lane_app1_alarm_clock.py compiles this with the JDK and runs it.

import com.voiney.lab.alarm.TimerAlarmPolicy;
import com.voiney.lab.alarm.TimerAlarmPolicy.AfterBoot;

public final class TimerAlarmPolicyCheck {

    private static int failures = 0;

    private static void check(boolean condition, String what) {
        if (!condition) {
            failures++;
            System.out.println("FAIL " + what);
        }
    }

    public static void main(String[] args) {
        long now = 1_760_000_000_000L;
        // Ahead: set again, both kinds.
        check(TimerAlarmPolicy.afterBoot("warning", now + 1, now) == AfterBoot.SET_AGAIN, "warning ahead");
        check(TimerAlarmPolicy.afterBoot("end", now + 60_000, now) == AfterBoot.SET_AGAIN, "end ahead");
        // Passed while the phone was off: the end is told late, the warning is not.
        check(TimerAlarmPolicy.afterBoot("end", now, now) == AfterBoot.POST_LATE, "end due now");
        check(TimerAlarmPolicy.afterBoot("end", now - 3_600_000L, now) == AfterBoot.POST_LATE, "end an hour ago");
        check(TimerAlarmPolicy.afterBoot("warning", now - 1, now) == AfterBoot.DROP, "warning passed");
        check(TimerAlarmPolicy.afterBoot("something", now - 1, now) == AfterBoot.DROP, "unknown kind passed");
        // What the page sends.
        check(TimerAlarmPolicy.refusal(7, now, "end", "4단계 · 타이머 끝", "타이머 15분이 끝났어요.") == null, "valid");
        check("id".equals(TimerAlarmPolicy.refusal(0, now, "end", "t", "b")), "id zero");
        check("at".equals(TimerAlarmPolicy.refusal(7, 0, "end", "t", "b")), "no time");
        check("kind".equals(TimerAlarmPolicy.refusal(7, now, "start", "t", "b")), "other kind");
        check("title".equals(TimerAlarmPolicy.refusal(7, now, "warning", " ", "b")), "empty title");
        check("body".equals(TimerAlarmPolicy.refusal(7, now, "warning", "t", null)), "no body");
        check(
            TimerAlarmPolicy.lateBody("타이머 15분이 끝났어요. 앱에서 확인해 주세요.", "14:05")
                .equals("휴대폰이 꺼져 있던 14:05에 울렸어야 할 알림이에요. 타이머 15분이 끝났어요. 앱에서 확인해 주세요."),
            "late words");
        if (failures > 0) {
            System.exit(1);
        }
        System.out.println("ok");
    }
}
