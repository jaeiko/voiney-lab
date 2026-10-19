package com.voiney.lab.mic;

import android.Manifest;
import android.content.Intent;
import android.net.Uri;
import android.provider.Settings;
import android.util.Base64;
import androidx.core.content.ContextCompat;
import androidx.lifecycle.Lifecycle;
import com.getcapacitor.JSObject;
import com.getcapacitor.PermissionState;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.getcapacitor.annotation.Permission;
import com.getcapacitor.annotation.PermissionCallback;

/**
 * VoineyMicrophone: listening with the screen off (lane APP1, decision 2).
 * Called by static/native-bridge.js (createNativeMicrophone).
 *
 * start: asks for the microphone permission when needed, refuses unless the
 * app is on screen (Android allows a microphone foreground service only
 * then), and starts ListeningService. Events: "frames" {data: base64 PCM16,
 * sequence, frames} and "state" {state, reason}. The sound goes only to the
 * page; this plugin keeps nothing.
 */
@CapacitorPlugin(
    name = "VoineyMicrophone",
    permissions = { @Permission(alias = "microphone", strings = { Manifest.permission.RECORD_AUDIO }) }
)
public class BackgroundMicPlugin extends Plugin implements MicEvents.Listener {

    @Override
    public void load() {
        MicEvents.listener = this;
    }

    @Override
    protected void handleOnDestroy() {
        if (MicEvents.listener == this) MicEvents.listener = null;
        // No page, no socket: nothing records for no one.
        ListeningService.requestStop(getContext(), "stopped");
    }

    /** {sampleRate, frameMs} (the bridge's MIC_FORMAT) -> {listening: true}, or rejected with a code. */
    @PluginMethod
    public void start(PluginCall call) {
        int sampleRate = call.getInt("sampleRate", ListeningService.SAMPLE_RATE);
        int frameMs = call.getInt("frameMs", ListeningService.FRAME_MS);
        if (sampleRate != ListeningService.SAMPLE_RATE || frameMs != ListeningService.FRAME_MS) {
            call.reject("only 16 kHz, 20 ms frames", "mic_unavailable");
            return;
        }
        if (getPermissionState("microphone") != PermissionState.GRANTED) {
            requestPermissionForAlias("microphone", call, "afterMicrophonePermission");
            return;
        }
        begin(call);
    }

    @PermissionCallback
    private void afterMicrophonePermission(PluginCall call) {
        if (getPermissionState("microphone") != PermissionState.GRANTED) {
            call.reject("마이크 권한이 거절됐어요", "permission_denied");
            return;
        }
        begin(call);
    }

    private void begin(PluginCall call) {
        if (
            getActivity() == null ||
            !getActivity().getLifecycle().getCurrentState().isAtLeast(Lifecycle.State.STARTED)
        ) {
            call.reject("앱 화면이 보일 때만 시작할 수 있어요", "not_visible");
            return;
        }
        if (!ListeningService.isRunning()) {
            Intent intent = new Intent(getContext(), ListeningService.class).setAction(ListeningService.ACTION_START);
            try {
                ContextCompat.startForegroundService(getContext(), intent);
            } catch (RuntimeException refused) {
                call.reject("앱 화면이 보일 때만 시작할 수 있어요", "not_visible");
                return;
            }
        }
        JSObject result = new JSObject();
        result.put("listening", true);
        call.resolve(result);
    }

    @PluginMethod
    public void stop(PluginCall call) {
        ListeningService.requestStop(getContext(), "stopped");
        JSObject result = new JSObject();
        result.put("listening", false);
        call.resolve(result);
    }

    /** -> {listening, permission: granted | denied | prompt | prompt-with-rationale} */
    @PluginMethod
    public void status(PluginCall call) {
        JSObject result = new JSObject();
        result.put("listening", ListeningService.isRunning());
        result.put("permission", getPermissionState("microphone").toString());
        call.resolve(result);
    }

    /** The app's system settings, where a refused microphone permission is allowed. */
    @PluginMethod
    public void openAppSettings(PluginCall call) {
        if (getActivity() == null) {
            call.reject("no screen", "not_visible");
            return;
        }
        Intent intent = new Intent(
            Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:" + getContext().getPackageName()));
        getActivity().startActivity(intent);
        call.resolve();
    }

    @Override
    public void onFrames(byte[] pcm, long sequence) {
        JSObject event = new JSObject();
        event.put("data", Base64.encodeToString(pcm, Base64.NO_WRAP));
        event.put("sequence", sequence);
        event.put("frames", pcm.length / ListeningService.FRAME_BYTES);
        notifyListeners("frames", event);
    }

    @Override
    public void onState(String state, String reason) {
        JSObject event = new JSObject();
        event.put("state", state);
        event.put("reason", reason);
        notifyListeners("state", event);
    }
}
