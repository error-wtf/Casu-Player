# Changelog

All notable changes to the CASU/MPCASU project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project maintains a product version train that is independent of the
CASU container format version (see RELEASE_POLICY.md).

## [7.8.0] — unreleased

### Fixed
- **iOS (Erscheinungsbild)**: Asset-Katalog (`Assets.xcassets`) mit 1024px
  App-Icon (aus `assets/mpcasu_player_icon.png`) und CASU-Rot als AccentColor
  ergänzt — vorher weißes Platzhalter-Icon auf dem Homescreen; Launch-Screen
  zeigt jetzt Icon + Markenfarbe; `.tint(.accentColor)` faerbt die UI.
- **iOS (fünf echte Mängel)**:
  - AVAudioSession wird jetzt mit `.playback`-Category konfiguriert — ohne das
    starb Hintergrund-Audio trotz `UIBackgroundModes: audio` und der
    Stummschalter tötete die Wiedergabe.
  - Video nutzt die echte Videoabmessung (AVAsset-Track via KVO + async load)
    statt erzwungenem 16:9 — 4:3- und vertikale Videos sind nicht mehr
    beschnitten oder mit Balken.
  - Shuffle wiederholt nicht mehr sofort denselben Track.
  - "Previous" startet bei >3 s Position den aktuellen Track neu (Standard-
    verhalten wie Apple Music/Spotify) statt sofort zurückzuspringen.
  - mm:ss-Zeitanzeige unter dem Seek-Slider; changePlaybackRateCommand für den
    Sperrbildschirm; Version überall 7.0.0 → 7.8.0 (project.yml, About-Text).
- **macOS**: Build-Script liest die Version zentral aus pyproject.toml statt
  hartkodierter 7.0.0 in zip/dmg-Dateinamen und Volume-Label; die App-Bundle-
  Info.plist bekommt CFBundleShortVersionString/CFBundleVersion aus derselben
  Quelle (vorher PyInstaller-Platzhalter 0.0.0 im Finder).
- **Library-Metadaten**: Ordner abgeleitete Album-/Artist-Namen setzen nicht
  mehr System-/Temp-Verzeichnisse als Album (z.B. album="tmp" bei
  /tmp/music/song.mp3) — Generische Verzeichnisse werden gefiltert.
- **Android (Video-Responsiveness, alle gemeldeten Fehler)**:
  TV/Landscape-Layout-Weiche in `MainActivity` — auf FireTV/Android TV und im
  Landschaftsmodus werden Meta/Seek/Transport/Aufnahme/Volume als
  halbtransparentes Overlay in die Bühne gelegt (10-ft-UI: größere
  Treffflächen, Overscan-Padding); das Video erhält die gesamte Fläche statt
  in der Ecke zu verkommen.
  `onVideoSizeChanged` speichert jetzt die Videoabmessungen und passt die
  TextureView per Aspect-Fit zentriert an (vorher streckte das MediaPlayer-
  Backend Frames auf die Surface; VLC letterboxte — inkonsistent je Backend).
  `onSurfaceTextureSizeChanged` (Rotation) berechnet die Geometrie neu.
  Immersive Fullscreen während der Videowiedergabe auf TV/Landscape.
  `windowSoftInputMode="adjustNothing"` + erweiterte `configChanges` +
  `supportsPictureInPicture` in beiden Manifests — die On-Screen-Tastatur
  verkleinerte/verschob zuvor das Video (User-Report).
- **Web (mobil)**: `100dvh` im Mobile-Layout (URL-Bar/Tastatur quetschten die
  Bühne), `:fullscreen`-CSS (echtes Edge-to-Edge), Landscape-Phone-Query,
  Canvas-Resize/Orientation-Sync für den Visualizer.
- **iOS**: `VideoPlayer` mit `aspectRatio(16/9)`-Slot, Tap-zum-Vollbild via
  `fullScreenCover` + `ignoresSafeArea` (Video war im Control-Stapel winzig).
- **Windows (Qt-Port)**: Fullscreen-Exit restauriert die exakte vorherige
  Sichtbarkeit von Sidebar/Topbar/Transport/Diagnostics (versteckte Bars
  erschienen zuvor nach Fullscreen wieder).
- **Qt**: Mindestfenstergröße 700×400 (vorher 980×620 klemmte auf
  Netbooks), Video-Bühne 160×90 Minimum — kein Clipping auf kleinen Screens.
- **Android**: `windowSoftInputMode="adjustNothing"` + erweiterte
  `configChanges` + `supportsPictureInPicture` in beiden Manifests — die
  On-Screen-Tastatur verkleinerte/verschob zuvor das Video (User-Report).
- **pure-web-release**: `index.html`/`styles.css` resynchronized with
  `win-release/web/pure/` — the deployed player on error.wtf crashed on every
  queue render (missing `iptv-filters`, `iptv-groups`, `epg-search`,
  `epg-groups`, `epg-count`, `epg-prev`, `epg-next-page` element IDs).
- **Security (pure-web-release)**: `php/catalog.php` now resolves every
  catalog hostname and rejects private/loopback/link-local/reserved addresses
  (IPv4+IPv6) before fetching — the endpoint was an open SSRF proxy
  (`Access-Control-Allow-Origin: *` + arbitrary URL fetch). Redirects are no
  longer followed.
- **Windows web backend**: `win-release/apps/web-backend/web/index.html`
  synced with `web/index.html` — the search dialog was silently missing its
  source selector (YouTube/Playlists/Spotify).
- **web/app.js**: `"use strict"` moved to line 1 (it was appended after the
  version fetch and had no effect); `formatTime` now renders hours
  (>59:59 previously displayed "75:30").
- **mpcasu_player.py (Tk)**: `remove_selected_queue` and the queue context
  menu referenced the nonexistent `self.playlist` attribute and raised
  `AttributeError`; they now read `PlaylistModel.items`.
- **MediaLibrary (casu/library.py)**: the shared sqlite connection
  (`check_same_thread=False`) is now serialized through an RLock
  (`_LockedConnection` proxy) — UI thread and scan thread no longer interleave
  statements.

### Added
- **CUE-Writer**: Playlists als CUE-Sheet speichern (Ergänzung zum bestehenden
  CUE-Reader; 11 Playlist-Formate jetzt in beide Richtungen).
- **Tastatur-Steuerung (Qt)**: Space/F/M/Pfeile/N/P im Player-Tab
  (VLC/mpv-Parität, Textfelder bleiben unberührt) + „?"-Button mit
  Shortcut-Übersicht.
- **Equalizer-Dialog (Qt)**: 10 vertikale Bänder + Preamp, Reset-Button —
  erreichbar über den neuen „EQ"-Button in der Transportleiste (beide Repos).
- **Equalizer-Band-API**: `set_equalizer_bands()` (10 Bänder, ±20 dB,
  Preamp) + `equalizer_band_frequencies()` neben den bestehenden Presets —
  Grundlage für einen EQ-Editor in der UI (7.9).
- **Audio-Block-Coalescing**: der CASUNAT2-Konverter fasst Decoder-Frames zu
  ≥1-s-Blöcken zusammen (vorher ein Chunk pro ~1024-Sample-Frame = Millionen
  Chunks bei langen Medien — Header + Hash pro Chunk).
- **Gapless-Playback (Phase 1)**: das Backend kann das nächste Medienobjekt
  vor-auflösen (`preload()`); die Qt-UI triggert das bei 85 % der Tracklänge
  bzw. <15 s Restlaufzeit. Beim Trackwechsel wird das bereits geparste Objekt
  wiederverwendet statt URL/Probe erneut auszuführen — die hörbare Lücke am
  Trackende entsteht durch dieses Re-Resolve. (Volles Dekoder-Gapless via
  dauerhaft offener Pipeline folgt in 7.9.)

### Added
- **Pitch-preserving time-stretch** (`time_stretch_audio_block`, OLA mit
  50 %-Overlap): Geschwindigkeit 0,25×–4× ändert die Tonhöhe nicht mehr
  (gemessene Blocklängen-Abweichung 0,3–2,9 % gegenüber Soll). Lineares
  Resampling bleibt als Fallback für kurze Blöcke.

### Performance
- **Native frame-step**: `next_frame()` springt per Binärsuche über die
  vorsortierte Video-Timeline statt alle Events jedes Mal linear zu
  durchsuchen (Single-Step in langen Medien war O(n) pro Tastendruck).
- **Library-Suche**: indizierte Suchspalten (`search_title/artist/album`)
  statt Full-Table-Scan über das JSON-Blob; automatische Migration bestehender
  Datenbanken. **pw-dump-Cache** (5 s TTL) — das Audio-Device-Menü parste
  vorher bei jedem Öffnen ~4 MiB PipeWire-JSON. **Waveform-Decode auf
  11.025 kHz** — 4× längere Medien im sicheren Budget.
- **Web**: Media Session API (Lockscreen/Medientasten in allen Browser-
  Builds), graduiertes HLS-Recovery (network→startLoad, media→
  recoverMediaError, erst dann destroy), CSP-Header, hls.js SRI-Pinning,
  Spotify-Klassifizierung vor dem http-Branch in `detect_entry_type`,
  doppeltes `_backend_event` in der Qt-UI entfernt.
- **CASUNAT2 native playback** (`casu/native_v2/reader.py`):
  `reconstruct_video` now keeps a persistent replay cache per stream.
  Sequential playback applies only newly reached chunks instead of
  rebuilding the frame from the last key state for every frame
  (measured: first frame 0.52 ms full rebuild → subsequent frame 0.05 ms
  incremental, ~10x; backward seeks still do a full rebuild).
- **Qt/MPRIS** (`mpcasu_qt/main_window.py`, both repos): `_display_title`
  results are memoized per path — MPRIS metadata polling previously spawned
  an ffprobe subprocess several times per second during playback.

### Changed
- **Architektur**: QueueWidgets-Split — QueueTree + PlaylistPane (~655 Zeilen)
  in `mpcasu_qt/playlist_widget.py`; main_window.py jetzt ~4.660 (CODEC) /
  ~5.140 (Player) Zeilen. SourcesView-Split + threads.py — SourcesView (~360 Zeilen)
  in `mpcasu_qt/sources.py`, `_ThreadBridge` in `mpcasu_qt/threads.py`;
  main_window.py jetzt ~5.320 (CODEC) / ~5.820 (Player) Zeilen.
  Pages-Split — LibraryPage/OptionsPage/EpgPage/AboutPage
  (~930 bzw. ~1.000 Zeilen) aus `main_window.py` in neues `mpcasu_qt/pages.py`
  extrahiert (nach MPRIS der zweite Schritt des Modularisierungsplans);
  main_window.py jetzt ~5.670 (CODEC) / ~6.185 (Player) Zeilen.
- **CI**: alle 4 Workflows nutzen `PRODUCT_VERSION` als zentrale env-Variable
  (vorher 7.8.0 hart in 25 Stellen kodiert); `v7-publish-macos.yml` wählt die
  letzte erfolgreiche Apple-Build-Run dynamisch statt einer festen Run-ID.
- **Transcode**: optionale `loudness_normalize` (EBU R128, loudnorm
  I=-16/LRA=11/TP=-1.5) — die Voraussetzung für einheitliche Lautstärke über
  die Bibliothek (Wiedergabe-Gain folgt in 7.9).
- **Export**: CASUNAT2-Frames als MJPEG (q95) statt unkomprimiertem PPM —
  ~90 % weniger temporärer Speicherplatz, gleiche ffconcat-Pipeline.
- **Android**: `getParcelableExtra` versionssicher (API-33-Deprecation),
  `networkSecurityConfig` dokumentiert den HTTP-Radio-Kompromiss offiziell.
- Test-Infrastruktur: `CASU_SKIP_HEADLESS_UNSTABLE=1` skippt die 6 libVLC-
  Codecs, die in headless dummy-vout-Umgebungen segfaulten (rawvideo/mjpeg/
  x265/vpx/vp9/av1) — die volle Suite läuft damit ohne Prozess-Abbruch durch
  (651 passed). Auf Desktop-Umgebungen bleiben alle Codecs aktiv.
- Backported Casu-Player library playlist CRUD context into this repo's
  planning documents (code backport pending, see ROADMAP_V7_8.md).
- `casu/library.py` synchronized into Casu-Player (hash-identical shared
  modules are now pinned by tests).

## [7.0.0] — 2026-09-03

See RELEASE_NOTES and V7_FIX_NOTES.md for the complete picture; condensed:

### Added
- In-app provider browsing (QtWebEngine/Widevine on Linux/macOS, WebView2 on
  Windows, native views on Android/iOS).
- YouTube playlist expansion, queue groups, IPTV catalogs with guide search,
  filter and pagination, mobile TV remote focus.
- Library queue import across 10 playlist formats with URL/relative-path
  preservation; mobile MP3 metadata and embedded covers.
- Android 5 and Fire OS 5 compatibility (minSdk 21, library desugaring).

(Older versions 1.0.0 – 6.0.0: see RELEASE_NOTES_v*.md in this repository —
kept as historical snapshots rather than duplicated here.)
