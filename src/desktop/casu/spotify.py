# SPDX-License-Identifier: LicenseRef-CASU-AntiCapitalist-1.4
# SPDX-FileCopyrightText: 2026 Lino Casu
"""Spotify provider via spotDL.

MPCASU never touches Spotify's DRM/API-bound streams directly.  Instead it
uses spotDL, which reads Spotify metadata through the Spotify Web API and
matches each track to an audio source at an open provider (usually YouTube).
The playable audio is therefore a spotDL-matched external source, never a
Spotify stream.  The UI always labels it that way.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

SPOTIFY_TRACK_RE = re.compile(
    r"^(?:https?://)?open\.spotify\.com/(track|album|playlist|episode|show|artist)/([a-zA-Z0-9]{22})(?:\?.*)?$"
)


class SpotifyError(ValueError):
    pass


@dataclass(frozen=True)
class SpotifyMetadata:
    kind: str
    title: str
    url: str


def is_spotify_url(url: str) -> bool:
    return bool(SPOTIFY_TRACK_RE.match((url or "").strip()))


def spotify_id(url: str) -> str | None:
    match = SPOTIFY_TRACK_RE.match((url or "").strip())
    return match.group(2) if match else None


def fetch_spotify_metadata(url: str, *, timeout: float = 15.0) -> SpotifyMetadata:
    """Fetch public oEmbed metadata (title/kind) for a Spotify URL.

    Requires open.spotify.com to be reachable; on blocked networks this fails
    with a clear SpotifyError instead of fake data.
    """
    clean = (url or "").strip()
    match = SPOTIFY_TRACK_RE.match(clean)
    if not match:
        raise SpotifyError("Invalid Spotify URL")
    if not clean.startswith("http"):
        clean = "https://" + clean
    endpoint = "https://open.spotify.com/oembed?url=" + urllib.parse.quote(clean, safe="")
    request = urllib.request.Request(endpoint, headers={"User-Agent": "MPCASU/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=max(3.0, float(timeout))) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise SpotifyError(
            f"Spotify metadata fetch failed: {exc} — open.spotify.com may be "
            "blocked on this network") from exc
    title = str(data.get("title") or "").strip()
    if not title:
        raise SpotifyError("Spotify returned no title for this URL")
    return SpotifyMetadata(kind=match.group(1), title=title[:300], url=clean)


def youtube_handoff_query(metadata: SpotifyMetadata) -> str:
    """Search YouTube for the fetched human title (explicit handoff)."""
    return metadata.title


@dataclass(frozen=True)
class SpotifySearchResult:
    title: str
    artist: str
    url: str
    duration: float | None = None


def spotdl_binary() -> str | None:
    """Locate spotDL (system PATH first, then the product venv)."""
    found = shutil.which("spotdl")
    if found:
        return found
    venv = "/opt/casu-spotdl/bin/spotdl"
    return venv if os.path.exists(venv) else None


def open_spotify_web(query: str = "", url: str = "") -> bool:
    """Open the official Spotify Web Player in a system Chromium browser.

    Spotify's DRM audio can only be played by the official player, which
    requires a normal account login. This launches Chromium at the Spotify
    Web Player (or at a track/search URL) so the user's own login plays the
    real Spotify audio — spotDL is not involved in playback.
    """
    from .webproviders import open_web_player
    return open_web_player("spotify", query=query, url=url)


def spotify_kind(url: str) -> str | None:
    """Return the resource kind (track/album/playlist/...) or None."""
    match = SPOTIFY_TRACK_RE.match((url or "").strip())
    return match.group(1) if match else None


def _spotdl_save(query: str, *, timeout: float) -> list[SpotifySearchResult]:
    """Run ``spotdl save QUERY --save-file FILE`` and parse the JSON songs.

    This spotDL version flushes the JSON to a named save file reliably, then
    hangs on shutdown (a curl_cffi callback bug) instead of exiting. The save
    file is therefore polled until it parses as JSON and the process is killed
    once the data is available or the deadline passes.
    """
    binary = spotdl_binary()

    if not binary:
        raise SpotifyError("spotDL is not installed")

    deadline = time.monotonic() + max(30.0, float(timeout))
    fd, temporary = tempfile.mkstemp(prefix="casu-spotify-", suffix=".spotdl")
    os.close(fd)
    output = Path(temporary)
    proc = None
    try:
        proc = subprocess.Popen(
            [binary, "save", query, "--save-file", str(output)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True,
        )
        payload = None
        while time.monotonic() < deadline:
            if proc.poll() is not None or output.stat().st_size > 4:
                try:
                    payload = json.loads(output.read_text(encoding="utf-8"))
                    break
                except (json.JSONDecodeError, OSError, ValueError):
                    pass
            time.sleep(0.4)
        if payload is None and proc.poll() is not None and proc.returncode:
            raise SpotifyError("spotDL search failed")
        if payload is None:
            raise SpotifyError("spotDL search timed out")
    except OSError as exc:
        raise SpotifyError(f"spotDL search failed: {exc}") from exc
    finally:
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass
        try:
            output.unlink()
        except OSError:
            pass

    if isinstance(payload, dict):
        payload = payload.get("songs", [])

    if not isinstance(payload, list):
        raise SpotifyError(
            "spotDL returned an unexpected save document"
        )

    results = []

    for data in payload:
        if not isinstance(data, dict):
            continue

        url = str(
            data.get("url")
            or data.get("spotify_url")
            or ""
        ).strip()

        if "spotify.com/" not in url:
            continue

        artists = data.get("artists") or data.get("artist") or []

        if isinstance(artists, str):
            artist = artists
        elif isinstance(artists, list):
            names = []
            for value in artists:
                if isinstance(value, dict):
                    name = str(value.get("name") or "")
                    if name:
                        names.append(name)
                elif value is not None:
                    names.append(str(value))
            artist = ", ".join(names)
        else:
            artist = ""

        duration = data.get("duration")

        try:
            duration = float(duration) if duration is not None else None
        except (TypeError, ValueError):
            duration = None

        results.append(
            SpotifySearchResult(
                title=str(
                    data.get("name")
                    or data.get("title")
                    or "Spotify track"
                )[:300],
                artist=artist[:200],
                url=url,
                duration=duration,
            )
        )

    return results


def search_spotify(query: str, *, limit: int = 12,
                   timeout: float = 90.0) -> list[SpotifySearchResult]:
    """Search Spotify via spotDL (metadata only, no audio downloads).

    Runs ``spotdl save QUERY --save-file -`` and parses the JSON song array
    spotDL writes to stdout.  Returns results carrying the real Spotify track
    URLs.  Requires spotDL and a reachable Spotify API.
    """
    query = (query or "").strip()

    if not query:
        raise SpotifyError("search query must not be empty")

    results = _spotdl_save(query, timeout=timeout)

    if not results:
        raise SpotifyError("spotDL found no Spotify results")

    return results[:max(1, min(int(limit), 25))]


def expand_spotify(url: str, *, limit: int = 100,
                   timeout: float = 120.0) -> list[SpotifySearchResult]:
    """Expand a Spotify album/playlist (or single track) into its tracks.

    Uses the same ``spotdl save <url> --save-file -`` interface, which returns
    one song entry per track.  Artists, episodes and shows cannot be expanded
    to a playable track list and raise SpotifyError.
    """
    clean = (url or "").strip()
    kind = spotify_kind(clean)

    if not kind:
        raise SpotifyError("Invalid Spotify URL")

    if kind not in ("track", "album", "playlist"):
        raise SpotifyError(
            f"Spotify {kind} cannot be expanded into tracks before playback")

    results = _spotdl_save(clean, timeout=timeout)

    if not results:
        raise SpotifyError("spotDL found no Spotify results")

    return results[:max(1, min(int(limit), 200))]


def _spotdl_url_resolve(clean: str, *, timeout: float) -> str | None:
    """Run ``spotdl url TRACK``; returns the first playable URL or None."""
    binary = spotdl_binary()
    if not binary:
        return None
    deadline = time.monotonic() + max(8.0, min(15.0, float(timeout)))
    fd, temporary = tempfile.mkstemp(prefix="casu-spotify-", suffix=".url")
    os.close(fd)
    output = Path(temporary)
    proc = None
    urls: list[str] = []
    try:
        with output.open("w", encoding="utf-8") as stream:
            proc = subprocess.Popen(
                [binary, "url", clean],
                stdout=stream, stderr=subprocess.DEVNULL, text=True,
            )
        while time.monotonic() < deadline:
            if proc.poll() is not None or output.stat().st_size > 4:
                urls = [
                    line.strip()
                    for line in output.read_text(encoding="utf-8").splitlines()
                    if line.strip().startswith(("http://", "https://"))
                ]
                if urls or proc.poll() is not None:
                    break
            time.sleep(0.4)
    except OSError:
        pass
    finally:
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass
        try:
            output.unlink()
        except OSError:
            pass
    return urls[0] if urls else None


def _resolve_via_ytdlp(title: str, artist: str, *, timeout: float) -> str:
    """Match a Spotify track on YouTube (spotDL's model) and return a URL."""
    executable = shutil.which("yt-dlp")
    if not executable:
        raise SpotifyError("spotDL could not resolve and yt-dlp is not installed")
    query = f"{title} {artist}".strip()
    command = [
        executable, "--no-warnings", "--no-playlist", "--get-url",
        "--format", "bestaudio", f"ytsearch1:{query}",
    ]
    try:
        proc = subprocess.run(command, check=False, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=max(20.0, float(timeout)))
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SpotifyError(f"spotDL YouTube match failed: {exc}") from exc
    urls = [
        line.strip()
        for line in proc.stdout.splitlines()
        if line.strip().startswith(("http://", "https://"))
    ]
    if proc.returncode or not urls:
        detail = proc.stderr.strip().splitlines()
        raise SpotifyError(
            detail[-1][:300] if detail else "spotDL returned no playable URL")
    return urls[0]


def resolve_spotify_url(url: str, *, timeout: float = 60.0,
                        title: str = "", artist: str = "") -> str:
    """Resolve a Spotify TRACK to a matched playable audio URL.

    ``spotdl url`` is tried first. Some spotDL builds fail against the current
    Spotify API (SpotipyFree KeyError), so the documented spotDL model is used
    as a fallback: the track title (from the caller or Spotify oEmbed) is
    matched on YouTube with yt-dlp and the matched audio URL is returned.
    """
    clean = (url or "").strip()
    match = SPOTIFY_TRACK_RE.match(clean)

    if not match:
        raise SpotifyError("Invalid Spotify URL")

    kind = match.group(1)

    if kind != "track":
        raise SpotifyError(
            f"Spotify {kind} must be expanded into tracks before playback"
        )

    if not spotdl_binary():
        raise SpotifyError("spotDL is not installed")

    direct = _spotdl_url_resolve(clean, timeout=timeout)
    if direct:
        return direct

    if not title:
        meta = fetch_spotify_metadata(clean, timeout=min(10.0, float(timeout)))
        title = meta.title
        if not artist:
            artist = ""
    return _resolve_via_ytdlp(title, artist, timeout=timeout)


def download_spotify_track(title: str, artist: str = "", *,
                           timeout: float = 180.0) -> Path:
    """Download the matched audio via yt-dlp to a temp file and return it.

    YouTube's direct stream URLs return HTTP 403 to plain HTTP clients (and
    to libVLC), so the local player cannot open them. yt-dlp's authenticated
    ``ytsearch`` download works, so the Spotify track (title + artist) is
    downloaded once to a temporary file that the player opens natively.
    """
    executable = shutil.which("yt-dlp")
    if not executable:
        raise SpotifyError("yt-dlp is not installed")
    query = f"{title} {artist}".strip()
    temp = Path(tempfile.gettempdir()) / (
        f"casu-spotify-{os.getpid()}-{int(time.monotonic()*1000)}.m4a")
    command = [
        executable, "--no-warnings", "--no-playlist",
        "-f", "bestaudio[ext=m4a]/bestaudio[ext=mp4]/bestaudio",
        "-o", str(temp), f"ytsearch1:{query}",
    ]
    try:
        proc = subprocess.run(command, check=False, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL,
                              timeout=max(60.0, float(timeout)))
    except (OSError, subprocess.TimeoutExpired) as exc:
        temp.unlink(missing_ok=True)
        raise SpotifyError(f"Spotify audio download failed: {exc}") from exc
    if proc.returncode != 0 or not temp.is_file() or temp.stat().st_size <= 0:
        temp.unlink(missing_ok=True)
        raise SpotifyError("Spotify audio download failed")
    return temp
