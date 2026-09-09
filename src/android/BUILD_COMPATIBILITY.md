# Android 7.0.0 build requirements

The refreshed 7.0.0 APK uses internal versionCode 70001, minSdk 21 (Android 5.0+), targetSdk 34 and ARMv7, ARM64, x86 and x86_64 native libraries. Fire OS 5 (API 22) is included. Release signing preserves the existing certificate and enables both v1/JAR and v2 signatures. Core library desugaring (desugar_jdk_libs 2.1.5) supplies Java date/time and collection APIs on older Android versions. Audio focus, service/widget lifecycle and notification actions use compatible APIs. A TV launcher banner is included.

Vega OS uses VPKG, not Android APK, and is outside this APK compatibility range. Provider playback still depends on the device WebView, DRM support and service requirements.

The Java/resource sources here mirror the Android app module in CASU-CODEC. Its Gradle build enables `isCoreLibraryDesugaringEnabled = true`, Java 17 compilation, `minSdk = 21`, `versionCode = 70001` and all four ABIs. Do not package these sources using the previous API-24-only build settings.
