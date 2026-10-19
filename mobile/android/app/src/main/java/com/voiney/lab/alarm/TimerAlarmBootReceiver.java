package com.voiney.lab.alarm;

import android.app.AlarmManager;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/**
 * Alarms do not survive a reboot or an app update; the stored ones are set
 * again here (and an end that passed while the phone was off is told late,
 * once). Also when exact alarms are allowed again, so the alarms set
 * inexactly in between become alarm clocks.
 */
public class TimerAlarmBootReceiver extends BroadcastReceiver {

    @Override
    public void onReceive(Context context, Intent intent) {
        String action = intent == null ? null : intent.getAction();
        if (
            Intent.ACTION_BOOT_COMPLETED.equals(action) ||
            Intent.ACTION_MY_PACKAGE_REPLACED.equals(action) ||
            AlarmManager.ACTION_SCHEDULE_EXACT_ALARM_PERMISSION_STATE_CHANGED.equals(action)
        ) {
            TimerAlarms.restore(context, System.currentTimeMillis());
        }
    }
}
