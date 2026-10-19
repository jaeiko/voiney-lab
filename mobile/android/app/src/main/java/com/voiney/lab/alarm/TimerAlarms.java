package com.voiney.lab.alarm;

import android.app.AlarmManager;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.os.Build;
import androidx.core.app.NotificationCompat;
import androidx.core.app.NotificationManagerCompat;
import com.voiney.lab.R;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;

/**
 * Setting, cancelling and posting the timer alarms (lane APP1, decision 1).
 *
 * An exact alarm is an alarm clock (AlarmManager.setAlarmClock): Android's
 * Doze documentation says alarms set with it "continue to fire normally",
 * where setExactAndAllowWhileIdle -- what @capacitor/local-notifications
 * 8.3.1 uses -- fires at most about once per nine minutes per app in Doze.
 * The system shows an alarm-clock icon in the status bar while one is set.
 * Without the exact-alarm permission (Android 12+, off by default on new
 * installs from Android 14) the alarm is set with setAndAllowWhileIdle and
 * may be late; the bridge then prefers the official plugin anyway.
 */
final class TimerAlarms {

    /** The bridge's CHANNEL_ID (native-bridge.js); tests pin the two together. */
    static final String CHANNEL_ID = "voiney-timer";
    static final String ACTION_FIRE = "com.voiney.lab.alarm.FIRE";
    static final String EXTRA_ID = "id";

    private TimerAlarms() {}

    static boolean canScheduleExact(Context context) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.S) return true;
        AlarmManager manager = context.getSystemService(AlarmManager.class);
        return manager != null && manager.canScheduleExactAlarms();
    }

    private static PendingIntent fireIntent(Context context, int id, int flags) {
        Intent intent = new Intent(context, TimerAlarmReceiver.class)
            .setAction(ACTION_FIRE)
            .setData(Uri.parse("voiney-timer://alarm/" + id))
            .putExtra(EXTRA_ID, id);
        return PendingIntent.getBroadcast(context, id, intent, PendingIntent.FLAG_IMMUTABLE | flags);
    }

    /** Opens the app (the alarm-clock icon's target and the notification's tap). */
    private static PendingIntent showIntent(Context context, int id) {
        Intent launch = context.getPackageManager().getLaunchIntentForPackage(context.getPackageName());
        if (launch == null) launch = new Intent().setPackage(context.getPackageName());
        launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        return PendingIntent.getActivity(
            context, id, launch, PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
    }

    /** Sets one alarm; whether it is exact (an alarm clock). */
    static boolean arm(Context context, TimerAlarm alarm) {
        AlarmManager manager = context.getSystemService(AlarmManager.class);
        if (manager == null) return false;
        PendingIntent operation = fireIntent(context, alarm.id, PendingIntent.FLAG_UPDATE_CURRENT);
        if (canScheduleExact(context)) {
            try {
                manager.setAlarmClock(new AlarmManager.AlarmClockInfo(alarm.at, showIntent(context, alarm.id)), operation);
                return true;
            } catch (SecurityException takenBack) {
                // The permission went between the check and the call.
            }
        }
        manager.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, alarm.at, operation);
        return false;
    }

    static void disarm(Context context, int id) {
        PendingIntent operation = fireIntent(context, id, PendingIntent.FLAG_NO_CREATE);
        if (operation == null) return;
        AlarmManager manager = context.getSystemService(AlarmManager.class);
        if (manager != null) manager.cancel(operation);
        operation.cancel();
    }

    /** Whether the system still holds the alarm (best effort: its PendingIntent exists). */
    static boolean isArmed(Context context, int id) {
        return fireIntent(context, id, PendingIntent.FLAG_NO_CREATE) != null;
    }

    static void ensureChannel(Context context) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return;
        NotificationManager manager = context.getSystemService(NotificationManager.class);
        if (manager == null || manager.getNotificationChannel(CHANNEL_ID) != null) return;
        // The same channel the bridge asks the official plugin for.
        NotificationChannel channel = new NotificationChannel(CHANNEL_ID, "타이머", NotificationManager.IMPORTANCE_HIGH);
        channel.setDescription("단계 타이머 1분 전과 끝");
        channel.enableVibration(true);
        channel.setLockscreenVisibility(Notification.VISIBILITY_PUBLIC);
        manager.createNotificationChannel(channel);
    }

    private static String channelFor(Context context, TimerAlarm alarm) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O || CHANNEL_ID.equals(alarm.channelId)) return CHANNEL_ID;
        NotificationManager manager = context.getSystemService(NotificationManager.class);
        return manager != null && manager.getNotificationChannel(alarm.channelId) != null ? alarm.channelId : CHANNEL_ID;
    }

    static void post(Context context, TimerAlarm alarm, String body) {
        ensureChannel(context);
        NotificationManagerCompat notifications = NotificationManagerCompat.from(context);
        if (!notifications.areNotificationsEnabled()) return;
        NotificationCompat.Builder builder = new NotificationCompat.Builder(context, channelFor(context, alarm))
            .setSmallIcon(R.drawable.ic_stat_voiney_timer)
            .setContentTitle(alarm.title)
            .setContentText(body)
            .setStyle(new NotificationCompat.BigTextStyle().bigText(body))
            .setCategory(NotificationCompat.CATEGORY_ALARM)
            .setPriority(NotificationCompat.PRIORITY_MAX)
            .setDefaults(NotificationCompat.DEFAULT_ALL)
            .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
            .setAutoCancel(true)
            .setContentIntent(showIntent(context, alarm.id));
        try {
            notifications.notify(alarm.id, builder.build());
        } catch (SecurityException notAllowed) {
            // Notifications were switched off after the check.
        }
    }

    /** After a reboot, an app update or exact alarms allowed again: TimerAlarmPolicy.afterBoot for each. */
    static void restore(Context context, long nowMs) {
        TimerAlarmStore store = new TimerAlarmStore(context);
        for (TimerAlarm alarm : store.all()) {
            switch (TimerAlarmPolicy.afterBoot(alarm.kind, alarm.at, nowMs)) {
                case SET_AGAIN:
                    arm(context, alarm);
                    break;
                case POST_LATE:
                    store.remove(alarm.id);
                    disarm(context, alarm.id);
                    post(context, alarm, TimerAlarmPolicy.lateBody(alarm.body, clock(alarm.at)));
                    break;
                default:
                    store.remove(alarm.id);
                    disarm(context, alarm.id);
            }
        }
    }

    static String clock(long atMs) {
        return new SimpleDateFormat("HH:mm", Locale.KOREA).format(new Date(atMs));
    }
}
