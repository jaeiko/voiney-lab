package com.voiney.lab.mic;

/**
 * Where the listening service hands its sound and its state: the plugin of
 * the screen's activity while there is one (lane APP1, decision 2). With no
 * listener the batches are dropped, not kept: nothing is stored on the phone.
 */
final class MicEvents {

    interface Listener {
        /** One batch of 16 kHz mono PCM16 (FRAMES_PER_BATCH frames), numbered from 1. */
        void onFrames(byte[] pcm, long sequence);

        /** "listening", or "off" with why (user_stopped, stopped, permission_denied, not_visible, mic_unavailable). */
        void onState(String state, String reason);
    }

    static volatile Listener listener;

    private MicEvents() {}

    static void frames(byte[] pcm, long sequence) {
        Listener current = listener;
        if (current != null) current.onFrames(pcm, sequence);
    }

    static void state(String state, String reason) {
        Listener current = listener;
        if (current != null) current.onState(state, reason);
    }
}
