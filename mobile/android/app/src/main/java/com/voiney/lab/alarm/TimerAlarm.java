package com.voiney.lab.alarm;

import org.json.JSONException;
import org.json.JSONObject;

/**
 * One alarm the page asked for (lane APP1, decision 1): a copy of a time the
 * server gave, with the words to show, so the alarm can be posted -- and set
 * again after a reboot -- without the page.
 */
final class TimerAlarm {

    final int id;
    final long at;
    final String kind;
    final String key;
    final String title;
    final String body;
    final String channelId;

    TimerAlarm(int id, long at, String kind, String key, String title, String body, String channelId) {
        this.id = id;
        this.at = at;
        this.kind = kind;
        this.key = key == null ? "" : key;
        this.title = title;
        this.body = body;
        this.channelId = channelId == null || channelId.isEmpty() ? TimerAlarms.CHANNEL_ID : channelId;
    }

    JSONObject toJson() throws JSONException {
        JSONObject json = new JSONObject();
        json.put("id", id);
        json.put("at", at);
        json.put("kind", kind);
        json.put("key", key);
        json.put("title", title);
        json.put("body", body);
        json.put("channelId", channelId);
        return json;
    }

    /** The alarm a JSON object describes, or null when one of its fields is refused. */
    static TimerAlarm fromJson(JSONObject json) {
        if (json == null) return null;
        int id = json.optInt("id", 0);
        long at = json.optLong("at", 0L);
        String kind = json.optString("kind", "");
        String title = json.optString("title", "");
        String body = json.optString("body", "");
        if (TimerAlarmPolicy.refusal(id, at, kind, title, body) != null) return null;
        return new TimerAlarm(id, at, kind, json.optString("key", ""), title, body, json.optString("channelId", ""));
    }
}
