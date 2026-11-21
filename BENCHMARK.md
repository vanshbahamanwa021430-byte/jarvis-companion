# Jarvis Companion comparison plan

Status: documented reference behaviors only. No alternative or Android end-to-end run has been measured.

## Reference products

| Reference | Documented strength | Design decision for Jarvis |
|---|---|---|
| [Gemini on Android](https://support.google.com/android/answer/15729107?hl=en) | An obvious voice entry point and system-assistant shortcuts. | Make push-to-talk and resume easy to find first. Don't assume system privilege or always-on microphone. |
| [OpenClaw Android](https://docs.openclaw.ai/platforms/android) | A mobile app pairs with a separate gateway; devices can be revoked. | Give the user clear named-device pairing and revocation, but keep device identity separate from authorization for each data access. |
| [Home Assistant Assist](https://www.home-assistant.io/voice_control/voice_remote_local_assistant/) | Separates speech recognition, intent and speech output; can run a local pipeline. | Keep voice replaceable and don't require it for approval, recovery or basic text use. |

## Same-task measures

1. **Phone-only setup:** On the same phone and network, record prerequisites, elapsed setup time, manual steps and whether a desktop or payment was required. Separate fresh setup and already signed-in results.
2. **Responsiveness:** Measure time to first visible response and complete response, separately for text and voice, using the same synthetic tasks. Record warm/cold conditions, device, network, runtime/model/version, attempt counts, p50 and p95, and failures. Don't compare unlike network or hosting conditions without labeling them.
3. **Session resume:** Open the same synthetic conversation on the paired Android device. Record steps, delivery/tap success and time to visible session. Check foreground, locked-screen notification, manually opening the app with notifications off, and a brief offline period.
4. **Approvals:** Required for each new personal read/action. Check no pre-approval contents, exactly one approved execution, and no access after denial, pause, expiry, retry, or device revocation. Use only fictional accounts/files for tests. Treat this as a release gate, not a score that speed can outweigh.
5. **Model quality:** Separately compare available engines/models on identical synthetic prompts (e.g. English/Hinglish commands, context retention, invalid intent clarification). Record task success and latency independently of the Android product flow. Use only permitted runtimes and inputs.

Record stronger competing patterns and implement the one highest-impact gap at a time. Report unavailable features and unmeasured results explicitly. Don't infer a best-in-class ranking from documentation or test count.
