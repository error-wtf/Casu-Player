# Third-party license collection policy

No third-party codec library is currently copied into this repository or its
Linux packages. System dependencies retain the copyright/license files shipped
by the distribution (normally under `/usr/share/doc/<package>/copyright`).

Before producing a self-contained/bundled artifact, copy the exact license
texts for every included binary/plugin into this directory, record exact
versions and hashes in `THIRD_PARTY_COMPONENTS.md`, and document any source
offer, relinking, notice, patent or export obligations. Research-only links are
not license grants and are not substitutes for this artifact audit.

## Bundled player runtimes

- Qt 6: `Qt/LGPL-3.0.txt`, `Qt/GPL-3.0.txt`, `Qt/NOTICE.txt`
- VLC/libVLC: `VLC/LGPL-2.1.txt`, `VLC/GPL-2.0.txt`, `VLC/NOTICE.txt`
- FFmpeg/ffprobe: `FFmpeg/LGPL-2.1.txt`, `FFmpeg/GPL-2.0.txt`,
  `FFmpeg/CONFIGURATION.txt`, `FFmpeg/NOTICE.txt`
- yt-dlp: `yt-dlp/UNLICENSE.txt`, `yt-dlp/NOTICE.txt`

No third-party component is relicensed under Anticapitalist License 1.4.
