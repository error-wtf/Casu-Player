# Browser compatibility update 7.0.1

Netflix, Spotify and Tidal use a maintained browser or the official mobile app.
The player no longer assumes that QtWebEngine, Android WebView or WebView2
provide a supported DRM playback environment.

- Linux: Chrome/Edge app window, Firefox window as fallback. Chromium alone
  is not accepted as evidence of installed Widevine. Browser DRM must be enabled.
- macOS: Safari uses the system browser and its own DRM implementation.
- Windows: provider pages and Browse open in the configured system browser.
  Use a current Edge, Chrome or Firefox with protected content enabled.
- Android: Netflix opens its official app and explains how to install it when
  absent. Spotify/Tidal use system links. WebView navigation cannot bypass this.
- iPhone: Netflix uses its universal link only in the official app; a missing
  app produces a visible message. iPad and other providers use system links.

Accounts, subscriptions and regional/device availability remain provider
requirements. A successful browser launch is not a playback confirmation.
No DRM is bypassed, browser identity spoofed or account cookies transferred.

Tests cover provider selection, exact domain matching, tab clicks, direct
URLs, Browse handoff, old Spotify embed links and visible launch failures.
A fresh official Chrome runtime on Linux accepted Widevine with AAC/H.264
through requestMediaKeySystemAccess. Account-authenticated Netflix/Spotify
playback still requires a user session on each target device.

Provider references:
- https://help.netflix.com/en/node/30081
- https://help.netflix.com/en/node/23939
- https://help.netflix.com/en/node/23927
- https://support.spotify.com/st-en/article/web-player-help/
