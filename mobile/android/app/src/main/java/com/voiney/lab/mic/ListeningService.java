package com.voiney.lab.mic;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.MediaRecorder;
import android.os.Build;
import android.os.IBinder;
import androidx.core.app.NotificationCompat;
import androidx.core.app.ServiceCompat;
import com.voiney.lab.R;

/**
 * Listening with the screen off (lane APP1, decision 2).
 *
 * A "microphone" foreground service: Android lets an app keep recording in
 * the background only from one, and only when it was started while the app
 * was visible. It records 16 kHz mono PCM16 -- the format the web screen
 * sends on /ws (voiney_lab.audio, pinned by tests/test_lane_app1_background_mic.py)
 * -- from the voice-call source, which turns on the device's echo
 * cancellation and noise suppression where it has them, and hands each
 * 100 ms batch to MicEvents (the page sends it on its own socket).
 *
 * The notification line says "VoineyLab 이 듣는 중" with a "끄기" action that
 * stops this service; the page is told why it stopped. Nothing is written to
 * the phone's storage.
 */
public class ListeningService extends Service {

    static final int SAMPLE_RATE = 16000;
    static final int FRAME_MS = 20;
    static final int FRAME_BYTES = 640;
    static final int FRAMES_PER_BATCH = 5;
    static final String ACTION_START = "com.voiney.lab.mic.START";
    static final String ACTION_STOP = "com.voiney.lab.mic.STOP";
    static final String CHANNEL_ID = "voiney-listening";
    static final int NOTIFICATION_ID = 0x564c;

    private static volatile boolean running = false;
    /** Why the next stop happens; the notification's 끄기 sets user_stopped. */
    private static volatile String stopReason = "stopped";

    private volatile boolean capturing = false;
    private Thread capture;

    static boolean isRunning() {
        return running;
    }

    /** Stop from the app (the page's stop, the activity going away). */
    static void requestStop(Context context, String reason) {
        if (!running) return;
        stopReason = reason;
        context.stopService(new Intent(context, ListeningService.class));
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        String action = intent == null ? null : intent.getAction();
        if (ACTION_STOP.equals(action)) {
            stopReason = "user_stopped";
            stopSelf();
            return START_NOT_STICKY;
        }
        if (running) return START_NOT_STICKY;
        try {
            int type = Build.VERSION.SDK_INT >= Build.VERSION_CODES.R ? ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE : 0;
            ServiceCompat.startForeground(this, NOTIFICATION_ID, notification(), type);
        } catch (RuntimeException refused) {
            // Started from the background, or the microphone permission is
            // missing: Android refuses the microphone service.
            MicEvents.state("off", refused instanceof SecurityException ? "permission_denied" : "not_visible");
            stopSelf();
            return START_NOT_STICKY;
        }
        running = true;
        stopReason = "stopped";
        startCapture();
        MicEvents.state("listening", "listening");
        return START_NOT_STICKY;
    }

    private void startCapture() {
        capturing = true;
        capture = new Thread(this::record, "voiney-listening");
        capture.start();
    }

    private void record() {
        AudioRecord recorder;
        try {
            int minimum = AudioRecord.getMinBufferSize(SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT);
            recorder = new AudioRecord(
                MediaRecorder.AudioSource.VOICE_COMMUNICATION,
                SAMPLE_RATE,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
                Math.max(minimum, FRAME_BYTES * FRAMES_PER_BATCH * 4));
        } catch (SecurityException denied) {
            failCapture("permission_denied");
            return;
        } catch (RuntimeException unavailable) {
            failCapture("mic_unavailable");
            return;
        }
        if (recorder.getState() != AudioRecord.STATE_INITIALIZED) {
            recorder.release();
            failCapture("mic_unavailable");
            return;
        }
        long sequence = 0;
        try {
            recorder.startRecording();
            byte[] batch = new byte[FRAME_BYTES * FRAMES_PER_BATCH];
            while (capturing) {
                int filled = 0;
                while (capturing && filled < batch.length) {
                    int read = recorder.read(batch, filled, batch.length - filled);
                    if (read < 0) {
                        failCapture("mic_unavailable");
                        return;
                    }
                    filled += read;
                }
                if (filled == batch.length) MicEvents.frames(batch.clone(), ++sequence);
            }
        } catch (RuntimeException lost) {
            failCapture("mic_unavailable");
        } finally {
            try {
                recorder.stop();
            } catch (RuntimeException ignored) {
                // Never started.
            }
            recorder.release();
        }
    }

    private void failCapture(String reason) {
        if (!capturing) return;
        stopReason = reason;
        capturing = false;
        stopSelf();
    }

    @Override
    public void onDestroy() {
        capturing = false;
        Thread thread = capture;
        if (thread != null && thread != Thread.currentThread()) {
            try {
                thread.join(500);
            } catch (InterruptedException ignored) {
                Thread.currentThread().interrupt();
            }
        }
        boolean wasRunning = running;
        running = false;
        ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE);
        if (wasRunning) MicEvents.state("off", stopReason);
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    private Notification notification() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationManager manager = getSystemService(NotificationManager.class);
            if (manager != null && manager.getNotificationChannel(CHANNEL_ID) == null) {
                NotificationChannel channel = new NotificationChannel(CHANNEL_ID, "듣기", NotificationManager.IMPORTANCE_LOW);
                channel.setDescription("화면을 꺼도 실험 안내를 듣는 동안 보이는 알림");
                manager.createNotificationChannel(channel);
            }
        }
        PendingIntent stop = PendingIntent.getService(
            this, 1, new Intent(this, ListeningService.class).setAction(ACTION_STOP),
            PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        Intent launch = getPackageManager().getLaunchIntentForPackage(getPackageName());
        if (launch == null) launch = new Intent().setPackage(getPackageName());
        launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        PendingIntent open = PendingIntent.getActivity(
            this, 2, launch, PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        return new NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_stat_voiney_mic)
            .setContentTitle("VoineyLab 이 듣는 중")
            .setContentText("화면을 꺼도 실험 안내를 계속 들어요.")
            .setOngoing(true)
            .setCategory(NotificationCompat.CATEGORY_SERVICE)
            .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
            .setContentIntent(open)
            .addAction(0, "끄기", stop)
            .build();
    }
}
