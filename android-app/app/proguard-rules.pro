# Production shrink/minify. Keep the app's components and the speech/TTS/accessibility entry points.
-keep class app.jarviscompanion.hud.** { *; }
-keep class * extends android.app.Activity
-keep class * extends android.app.Service
-keep class * extends android.content.BroadcastReceiver
-keep class * extends android.accessibilityservice.AccessibilityService
-keep class * extends android.service.notification.NotificationListenerService
-keepclassmembers class * extends android.webkit.WebViewClient { *; }
-dontwarn javax.lang.model.**
