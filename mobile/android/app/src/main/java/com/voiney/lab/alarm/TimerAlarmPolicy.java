package com.voiney.lab.alarm;

/**
 * The plugin's rules that need no Android (lane APP1, decision 1), kept in
 * plain Java so tests/native_android/TimerAlarmPolicyCheck.java runs them
 * with a JDK alone.
 *
 * Every alarm is a copy of a time the server gave (the step timer's last
 * minute or its end). Nothing here makes a time up or moves one.
 */
public final class TimerAlarmPolicy {

    public static final String KIND_WARNING = "warning";
    public static final String KIND_END = "end";

    /** What a stored alarm becomes when the phone has started again. */
    public enum AfterBoot {
        /** Still ahead: set it again. */
        SET_AGAIN,
        /** The end passed while the phone was off: say so once, now. */
        POST_LATE,
        /** A "1분 전" that passed: telling it late would be wrong. */
        DROP,
    }

    private TimerAlarmPolicy() {}

    public static AfterBoot afterBoot(String kind, long atMs, long nowMs) {
        if (atMs > nowMs) return AfterBoot.SET_AGAIN;
        return KIND_END.equals(kind) ? AfterBoot.POST_LATE : AfterBoot.DROP;
    }

    /** Why an alarm the page sent cannot be kept, or null when it can. */
    public static String refusal(int id, long atMs, String kind, String title, String body) {
        if (id <= 0) return "id";
        if (atMs <= 0) return "at";
        if (!KIND_WARNING.equals(kind) && !KIND_END.equals(kind)) return "kind";
        if (title == null || title.trim().isEmpty()) return "title";
        if (body == null || body.trim().isEmpty()) return "body";
        return null;
    }

    /** The words of an end that is told late, with the time it was due ("14:05"). */
    public static String lateBody(String body, String dueClock) {
        return "휴대폰이 꺼져 있던 " + dueClock + "에 울렸어야 할 알림이에요. " + body;
    }
}
