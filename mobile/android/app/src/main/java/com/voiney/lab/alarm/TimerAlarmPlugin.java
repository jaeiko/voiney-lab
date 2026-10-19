package com.voiney.lab.alarm;

import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.os.Build;
import android.provider.Settings;
import com.getcapacitor.JSArray;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/**
 * VoineyAlarm: the step timer's "1분 전" and end as Android alarm clocks
 * (lane APP1, decision 1). Called by static/native-bridge.js
 * (alarmClockNotifier, chooseNotifier).
 *
 * The page sends what the server's timer means (times and words); this
 * plugin only keeps, sets and cancels them. It never starts, moves or ends a
 * timer: a later server state is what cancels or replaces an alarm, through
 * the bridge.
 */
@CapacitorPlugin(name = "VoineyAlarm")
public class TimerAlarmPlugin extends Plugin {

    /** {alarms: [{id, at, kind, key, title, body, channelId}]} -> {scheduled, exact} */
    @PluginMethod
    public void schedule(PluginCall call) {
        JSArray given = call.getArray("alarms");
        if (given == null) {
            call.reject("alarms is required", "invalid_alarm");
            return;
        }
        List<TimerAlarm> alarms = new ArrayList<>();
        for (int index = 0; index < given.length(); index++) {
            TimerAlarm alarm = TimerAlarm.fromJson(given.optJSONObject(index));
            if (alarm == null) {
                call.reject("alarm " + index + " is not valid", "invalid_alarm");
                return;
            }
            alarms.add(alarm);
        }
        Context context = getContext();
        TimerAlarmStore store = new TimerAlarmStore(context);
        long now = System.currentTimeMillis();
        boolean exact = true;
        int scheduled = 0;
        for (TimerAlarm alarm : alarms) {
            // A time already gone is not set (the bridge does not send one).
            if (alarm.at <= now) continue;
            store.put(alarm);
            exact &= TimerAlarms.arm(context, alarm);
            scheduled++;
        }
        JSObject result = new JSObject();
        result.put("scheduled", scheduled);
        result.put("exact", exact);
        call.resolve(result);
    }

    /** {ids: [id]} -> {cancelled} */
    @PluginMethod
    public void cancel(PluginCall call) {
        JSArray ids = call.getArray("ids");
        if (ids == null) {
            call.reject("ids is required", "invalid_alarm");
            return;
        }
        Context context = getContext();
        TimerAlarmStore store = new TimerAlarmStore(context);
        int cancelled = 0;
        for (int index = 0; index < ids.length(); index++) {
            int id = ids.optInt(index, 0);
            if (id <= 0) continue;
            TimerAlarms.disarm(context, id);
            store.remove(id);
            cancelled++;
        }
        JSObject result = new JSObject();
        result.put("cancelled", cancelled);
        call.resolve(result);
    }

    /** -> {alarms: [{id, at, kind, key, armed}]}, earliest first. */
    @PluginMethod
    public void pending(PluginCall call) {
        Context context = getContext();
        List<TimerAlarm> stored = new TimerAlarmStore(context).all();
        Collections.sort(stored, (left, right) -> Long.compare(left.at, right.at));
        JSArray alarms = new JSArray();
        for (TimerAlarm alarm : stored) {
            JSObject item = new JSObject();
            item.put("id", alarm.id);
            item.put("at", alarm.at);
            item.put("kind", alarm.kind);
            item.put("key", alarm.key);
            item.put("armed", TimerAlarms.isArmed(context, alarm.id));
            alarms.put(item);
        }
        JSObject result = new JSObject();
        result.put("alarms", alarms);
        call.resolve(result);
    }

    /** -> {exact_alarm: "granted" | "denied"} (the official plugin's field name). */
    @PluginMethod
    public void exactAlarmStatus(PluginCall call) {
        JSObject result = new JSObject();
        result.put("exact_alarm", TimerAlarms.canScheduleExact(getContext()) ? "granted" : "denied");
        call.resolve(result);
    }

    /** Android 12+: the system page that allows exact alarms for this app. -> {opened} */
    @PluginMethod
    public void openExactAlarmSettings(PluginCall call) {
        JSObject result = new JSObject();
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.S || getActivity() == null) {
            result.put("opened", false);
            call.resolve(result);
            return;
        }
        Intent intent = new Intent(
            Settings.ACTION_REQUEST_SCHEDULE_EXACT_ALARM,
            Uri.parse("package:" + getContext().getPackageName()));
        getActivity().startActivity(intent);
        result.put("opened", true);
        call.resolve(result);
    }
}
