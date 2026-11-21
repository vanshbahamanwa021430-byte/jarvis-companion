package app.jarviscompanion.hud;

import android.content.Context;
import android.content.Intent;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.speech.RecognitionListener;
import android.speech.RecognizerIntent;
import android.speech.SpeechRecognizer;

import org.json.JSONObject;

import java.util.ArrayList;
import java.util.Locale;

/**
 * Wake-word loop using Android SpeechRecognizer (offline preferred).
 * No Porcupine. A Vosk model is not bundled. Transcripts are sent to the desk
 * parser; this class only detects "Hey Jarvis" / Confirm / Cancel locally.
 */
final class VoiceCoordinator implements RecognitionListener {
    enum Mode { WAKE, COMMAND, CONFIRM }

    private final Context ctx;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final Speaker speaker;
    private final Listener listener;
    private SpeechRecognizer recognizer;
    private Mode mode = Mode.WAKE;
    private boolean running;
    private boolean restarting;

    interface Listener {
        void onVoiceState(String label);
        void onUtterance(String text, JSONObject deskReply);
    }

    VoiceCoordinator(Context ctx, Speaker speaker, Listener listener) {
        this.ctx = ctx.getApplicationContext();
        this.speaker = speaker;
        this.listener = listener;
    }

    void start() {
        running = true;
        main.post(this::ensureRecognizer);
    }

    void stop() {
        running = false;
        main.post(() -> {
            if (recognizer != null) {
                try { recognizer.cancel(); recognizer.destroy(); } catch (Exception ignored) {}
                recognizer = null;
            }
        });
    }

    private void ensureRecognizer() {
        if (!running || Prefs.muted(ctx) || Prefs.killed(ctx)) return;
        if (!SpeechRecognizer.isRecognitionAvailable(ctx)) {
            listener.onVoiceState("Voice unavailable on this device");
            return;
        }
        if (recognizer == null) {
            recognizer = SpeechRecognizer.createSpeechRecognizer(ctx);
            recognizer.setRecognitionListener(this);
        }
        listen();
    }

    private void listen() {
        if (!running || recognizer == null || Prefs.muted(ctx) || Prefs.killed(ctx)) return;
        Intent intent = new Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH);
        intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM);
        intent.putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true);
        intent.putExtra(RecognizerIntent.EXTRA_PREFER_OFFLINE, true);
        intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE, Locale.UK.toString());
        intent.putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 3);
        if (mode == Mode.WAKE) {
            listener.onVoiceState("Listening for Hey Jarvis");
        } else if (mode == Mode.CONFIRM) {
            listener.onVoiceState("Waiting for Confirm or Cancel");
        } else {
            listener.onVoiceState("Listening for a command");
        }
        try {
            recognizer.startListening(intent);
        } catch (Exception e) {
            scheduleRestart(800);
        }
    }

    private void scheduleRestart(int delayMs) {
        if (!running || restarting) return;
        restarting = true;
        main.postDelayed(() -> {
            restarting = false;
            ensureRecognizer();
        }, delayMs);
    }

    @Override public void onReadyForSpeech(Bundle params) {}
    @Override public void onBeginningOfSpeech() {}
    @Override public void onRmsChanged(float rmsdB) {}
    @Override public void onBufferReceived(byte[] buffer) {}
    @Override public void onEndOfSpeech() {}
    @Override public void onEvent(int eventType, Bundle params) {}

    @Override
    public void onError(int error) {
        scheduleRestart(error == SpeechRecognizer.ERROR_RECOGNIZER_BUSY ? 1200 : 600);
    }

    @Override
    public void onPartialResults(Bundle partialResults) {
        String text = first(partialResults);
        if (text.isEmpty()) return;
        String norm = text.toLowerCase(Locale.US);
        if (mode == Mode.WAKE && isWake(norm)) {
            mode = Mode.COMMAND;
            speaker.speak("Yes?");
            listener.onVoiceState("Yes?");
        }
    }

    @Override
    public void onResults(Bundle results) {
        String text = first(results);
        if (!text.isEmpty()) handleTranscript(text);
        scheduleRestart(350);
    }

    private void handleTranscript(String text) {
        String norm = text.toLowerCase(Locale.US).trim();
        if (mode == Mode.WAKE) {
            if (isWake(norm)) {
                String rest = stripWake(norm);
                if (rest.isEmpty()) {
                    mode = Mode.COMMAND;
                    speaker.speak("Yes?");
                    return;
                }
                send(rest);
            }
            return;
        }
        send(text);
    }

    private void send(String text) {
        new Thread(() -> {
            try {
                JSONObject body = new JSONObject().put("text", text).put("source", "voice");
                JSONObject reply = DeskClient.request(ctx, "POST", "/api/voice/utterance", body);
                String speak = reply.optString("speak", "");
                boolean confirm = reply.optBoolean("needs_spoken_confirm", false);
                main.post(() -> {
                    if (confirm) mode = Mode.CONFIRM;
                    else mode = Mode.WAKE;
                    if (!speak.isEmpty()) speaker.speak(speak);
                    listener.onUtterance(text, reply);
                });
            } catch (Exception e) {
                main.post(() -> {
                    mode = Mode.WAKE;
                    speaker.speak("The desk is not connected.");
                });
            }
        }, "jarvis-voice").start();
    }

    private static boolean isWake(String norm) {
        return norm.contains("hey jarvis") || norm.contains("ok jarvis") || norm.contains("okay jarvis");
    }

    private static String stripWake(String norm) {
        for (String p : new String[]{"hey jarvis", "okay jarvis", "ok jarvis"}) {
            if (norm.equals(p)) return "";
            if (norm.startsWith(p + " ")) return norm.substring(p.length()).trim();
        }
        return norm;
    }

    private static String first(Bundle bundle) {
        if (bundle == null) return "";
        ArrayList<String> list = bundle.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION);
        if (list == null || list.isEmpty() || list.get(0) == null) return "";
        return list.get(0).trim();
    }
}
