# Third-party components — development runtime audit 2026-08-09

CASU does not copy source code from the research repositories listed below.
Linux packages use distribution shared libraries; their exact licensing and
plugin set are properties of the installed distribution build.

| Component | Exact inspected version | Purpose/mode | License boundary | Modified/bundled |
|---|---|---|---|---|
| VLC/libVLC | Ubuntu `3.0.23-1` (`libvlc5`, `libvlccore9`, `vlc-plugin-base`) | in-process legacy playback, dynamic system linking | libVLC upstream states LGPL; individual modules/build options require separate audit | unmodified system packages; not bundled |
| FFmpeg/libav | Ubuntu `8.0.1-3ubuntu2`, libavcodec 62.11.100, libavformat 62.3.100 | probe/decode fallback, implemented optional PyAV adapter, and decoded PGS/DVB/DVD/XSub subtitle-video boundary | installed build reports `--enable-gpl`; configuration-sensitive | unmodified system package; not bundled |
| NumPy | Ubuntu `2.3.5+ds-3ubuntu1` | canonical planes, hashes, RGB conversion, PCM scaling | BSD-3-Clause upstream | Python dependency; not copied |
| Tk | Ubuntu Python Tk `3.14.3-0ubuntu2` | development GUI/video surface | Tcl/Tk terms from distribution | system dependency |
| PulseAudio client | Ubuntu `libpulse0 17.0+dfsg1-2ubuntu4` | direct CASUNAT2 s16le output through `libpulse-simple` | LGPL-family upstream; verify package copyright when redistributing | dynamic system linking; not bundled |
| libass | Debian/Ubuntu `0.17.4` ABI 9 (`libass9`) | native ASS/SSA RGBA subtitle rendering through the documented C ABI | ISC upstream; distribution package also records file-specific notices | unmodified dynamic system package; not bundled |
| zlib (Python stdlib binding) | Python runtime-provided | lossless key/tile/audio compression | zlib license upstream | not bundled separately |
| PyAV | optional, not installed in the inspected environment; project extra requires `av>=14` | preferred library-level libav frame adapter | BSD upstream plus linked FFmpeg build obligations | optional dependency; not bundled |

Research-only, not linked, copied or shipped: Webamp embed, LAME, libde265,
`ggrandes-clones/mp3_codec`, GStreamer, libmpv and LibHunt candidates. Their
URLs and intended research boundaries are recorded in `SOURCE_PROVENANCE.md`.

This inventory is technical provenance, not legal advice. A bundled Windows or
macOS build requires a new artifact-specific audit and source-offer review.
