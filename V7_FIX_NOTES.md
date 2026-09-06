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
