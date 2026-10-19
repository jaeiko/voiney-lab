package com.voiney.lab.alarm;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/** An alarm came due: post its notification once and forget it. */
public class TimerAlarmReceiver extends BroadcastReceiver {

    @Override
    public void onReceive(Context context, Intent intent) {
        if (intent == null || !TimerAlarms.ACTION_FIRE.equals(intent.getAction())) return;
        int id = intent.getIntExtra(TimerAlarms.EXTRA_ID, 0);
        TimerAlarmStore store = new TimerAlarmStore(context);
        TimerAlarm alarm = store.get(id);
        // Cancelled after it was set (a later server state): nothing to say.
        if (alarm == null) return;
        store.remove(id);
        TimerAlarms.disarm(context, id);
        TimerAlarms.post(context, alarm, alarm.body);
    }
}
