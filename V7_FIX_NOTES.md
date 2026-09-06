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
