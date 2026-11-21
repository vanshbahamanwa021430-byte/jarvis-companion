# Android shell

A self-signed WebView app (`app.jarviscompanion.hud`) that opens the Jarvis Companion HUD served by the Python broker on your computer. It does not run the broker on the phone, does not add a JavaScript bridge, and does not change CSP or CORS.

Build (JDK 17+ and the Android SDK):

```sh
# from the repository root
scripts/build-apk.sh
```

The debug keystore `keystore/jarvis-debug.keystore` is for this project only (password `android`, alias `jarvis`). It is not a store key and is not the Prvlar keystore.

See the root README for how to connect the phone to the server.
