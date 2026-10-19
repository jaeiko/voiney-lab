package com.voiney.lab.alarm;

import android.content.Context;
import android.content.SharedPreferences;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import org.json.JSONException;
import org.json.JSONObject;

/**
 * The alarms set now, kept in the app's own preferences (not backed up:
 * android:allowBackup="false") so the receiver can post one and the boot
 * receiver can set them again. Keyed by alarm id.
 */
final class TimerAlarmStore {

    private static final String FILE = "voiney_timer_alarms";

    private final SharedPreferences preferences;

    TimerAlarmStore(Context context) {
        preferences = context.getApplicationContext().getSharedPreferences(FILE, Context.MODE_PRIVATE);
    }

    synchronized void put(TimerAlarm alarm) {
        try {
            preferences.edit().putString(String.valueOf(alarm.id), alarm.toJson().toString()).commit();
        } catch (JSONException ignored) {
            // Every field is a string or a number; toJson does not fail.
        }
    }

    synchronized TimerAlarm get(int id) {
        return parse(preferences.getString(String.valueOf(id), null));
    }

    synchronized void remove(int id) {
        preferences.edit().remove(String.valueOf(id)).commit();
    }

    synchronized List<TimerAlarm> all() {
        List<TimerAlarm> alarms = new ArrayList<>();
        for (Map.Entry<String, ?> entry : preferences.getAll().entrySet()) {
            TimerAlarm alarm = entry.getValue() instanceof String ? parse((String) entry.getValue()) : null;
            if (alarm != null) alarms.add(alarm);
        }
        return alarms;
    }

    private static TimerAlarm parse(String raw) {
        if (raw == null) return null;
        try {
            return TimerAlarm.fromJson(new JSONObject(raw));
        } catch (JSONException error) {
            return null;
        }
    }
}
