# v7.0.0 – Browser- und Playlist-Korrektur

Diese Dateien ersetzen die bisherigen v7.0.0-Pakete; die Produktversion bleibt 7.0.0.

- Provider bleiben innerhalb der App: QtWebEngine mit automatisch erkanntem installiertem Widevine auf Linux/macOS, WebView2 unter Windows, interne WebView unter Android und Safari-Ansicht innerhalb der iOS-App.
- Der Spotify-Tab öffnet den vollständigen Webplayer. Browser-Anmeldefenster bleiben in der Anwendung.
- YouTube-Playlists erscheinen als eigene aufklappbare Queue-Gruppen. Einzelvideos und mehrere Playlists können gemeinsam eingereiht werden.
- Playlist-Export: M3U/M3U8, PLS, XSPF und JSON auf dem Desktop; zusätzlich JSPF auf Android; M3U/M3U8 und PLS auf iOS.
- Mobile YouTube-Playlists speichern dauerhafte YouTube-Links und lösen die Wiedergabeadresse beim Abspielen auf.
- Windows-Setup enthält den offiziellen Microsoft-WebView2-Bootstrapper. Die Runtime-Installation benötigt bei fehlender Runtime Internet.

Linux-Webfunktionen wurden vom Anwender bestätigt. Zusätzlich lief ein Widevine-Testfilm im integrierten Linux-Browser über 30 Sekunden mit 764 gerenderten Bildern. Windows-Player/Codec/Konverter-Tests unter Wine, Android-Gerätetests sowie macOS-Builds und iPhone/iPad-Simulatortests bestanden. Die zuletzt ergänzte mobile Auflösung gespeicherter YouTube-Links wurde erneut gebaut; ein erneuter vollständiger Gerätetest aller Provider war nicht Teil des abschließenden Paketbaus.

Netflix und Spotify prüfen Konto, DRM-Komponente und Gerät selbst. Der DRM-Test ist kein Nachweis angemeldeter Netflix-/Spotify-Wiedergabe auf jeder Plattform. Widevine wird nicht mitverteilt; auf Linux/macOS wird eine vorhandene Installation verwendet.

Die iOS-IPA ist wie bisher unsigniert und benötigt eine eigene Apple-Signierung. macOS-Pakete sind ad-hoc signiert, nicht mit einer Apple Developer ID notarisiert.

Die verbindlichen Download-Prüfsummen stehen in `SHA256SUMS`.

## Library, playlist import and mobile metadata refresh

- Add whole playlists or selected tracks from Library to the queue; keep URLs and relative file paths intact and distinguish same-named playlists.
- Support desktop/Android JSON interchange and CUE file references; desktop export also writes WPL, JSPF, ASX and RAM in their actual formats.
- Android Library multi-select expands playlists and album/artist groups without interrupting playback.
- Android and iOS read MP3 title, artist, album and embedded covers, display Library thumbnails and playback artwork, and prevent stale artwork after track changes.
- iOS keeps imported documents readable after relaunch, preserves queue metadata, supports additional playlist formats and exposes Library queue actions.
- The product version remains 7.0.0; in-app provider browsing remains enabled.

## IPTV and mobile remote navigation

- Desktop IPTV adds channel search, group filters and 100-channel pages with readable channel rows.
- Web IPTV adds group filters and searchable, paginated programme guides; channel playlists work without XMLTV IDs.
- Android and iOS add dedicated, persistent IPTV catalogs with M3U file/URL import, groups, search and favorites.
- Mobile controls show distinct remote-focus and mouse-hover highlights. Android adds TV launcher support and Back navigation to the player. Desktop remote navigation is unchanged.


## Android / Fire TV installation compatibility

The refreshed 7.0.0 APK uses internal versionCode 70001, minSdk 21 (Android 5.0+), targetSdk 34 and ARMv7, ARM64, x86 and x86_64 native libraries. Fire OS 5 (API 22) is included. Release signing preserves the existing certificate and enables both v1/JAR and v2 signatures. Core library desugaring (desugar_jdk_libs 2.1.5) supplies Java date/time and collection APIs on older Android versions. Audio focus, service/widget lifecycle and notification actions use compatible APIs. A TV launcher banner is included.

Vega OS uses VPKG, not Android APK, and is outside this APK compatibility range. Provider playback still depends on the device WebView, DRM support and service requirements.
