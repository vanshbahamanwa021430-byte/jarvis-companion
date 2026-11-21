package app.jarviscompanion.hud;

import android.content.Context;
import android.speech.tts.TextToSpeech;
import android.speech.tts.Voice;

import java.util.Locale;

final class Speaker implements TextToSpeech.OnInitListener {
    private final TextToSpeech tts;
    private boolean ready;

    Speaker(Context ctx) {
        tts = new TextToSpeech(ctx.getApplicationContext(), this);
    }

    @Override
    public void onInit(int status) {
        ready = status == TextToSpeech.SUCCESS;
        if (!ready) return;
        tts.setLanguage(Locale.UK);
        tts.setSpeechRate(0.92f);
        tts.setPitch(0.92f);
        try {
            for (Voice voice : tts.getVoices()) {
                if (voice == null || voice.getLocale() == null) continue;
                String name = voice.getName() == null ? "" : voice.getName().toLowerCase();
                if (voice.getLocale().getLanguage().equals("en")
                        && (name.contains("male") || name.contains("gb") || name.contains("uk"))) {
                    tts.setVoice(voice);
                    break;
                }
            }
        } catch (Exception ignored) {}
    }

    void speak(String text) {
        if (!ready || text == null || text.isEmpty()) return;
        tts.speak(text, TextToSpeech.QUEUE_FLUSH, null, "jarvis");
    }

    void shutdown() {
        try { tts.stop(); tts.shutdown(); } catch (Exception ignored) {}
    }
}
