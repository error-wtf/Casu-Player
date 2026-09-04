"""Resolve page URLs that are not direct libVLC media locations."""
from __future__ import annotations

import shutil
import subprocess
from urllib.parse import urlparse


class LocationResolutionError(ValueError):
    pass


_YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com",
                  "music.youtube.com", "youtu.be", "www.youtu.be",
                  "youtube-nocookie.com", "www.youtube-nocookie.com"}


def is_youtube_url(value: str) -> bool:
    try:
        parsed = urlparse(value.strip())
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and (parsed.hostname or "").lower() in _YOUTUBE_HOSTS


def resolve_media_location(value: str, *, timeout_seconds: float = 30.0) -> str:
    """Return a direct media URL, using yt-dlp for YouTube only.

    Spotify URLs go through spotDL (``casu.spotify``): spotDL matches the
    Spotify track to a playable audio source at an open provider such as
    YouTube.  The result is a matched external stream, never a Spotify stream.
    """
    source = value.strip()
    if not source or "\0" in source:
        raise LocationResolutionError("media URL is empty or invalid")
    from .spotify import is_spotify_url as _is_spotify
    from .spotify import SpotifyError, resolve_spotify_url
    if _is_spotify(source):
        try:
            return resolve_spotify_url(source, timeout=timeout_seconds)
        except SpotifyError as exc:
            raise LocationResolutionError(str(exc)) from exc
    if not is_youtube_url(source):
        return source
    executable = shutil.which("yt-dlp")
    if not executable:
        raise LocationResolutionError(
            "YouTube playback requires yt-dlp; install it or open a direct stream URL")
    try:
        result = subprocess.run([
            executable, "--no-playlist", "--no-warnings", "--no-progress",
            "--socket-timeout", "15",
            # yt-dlp's current automatic client can select android_vr.  Its
            # CDN URL is reproducibly rejected with HTTP 403, including by
            # yt-dlp's own downloader.  The regular Android client returns a
            # byte-range-capable combined MP4 accepted by the MPCASU proxy.
            "--extractor-args", "youtube:player_client=android",
            "--get-url", "--format",
            "best[protocol^=http][vcodec!=none][acodec!=none]/best[protocol^=http]/best",
            source,
        ], check=False, text=True, stdout=subprocess.PIPE,
           stderr=subprocess.PIPE, timeout=max(1.0, float(timeout_seconds)))
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LocationResolutionError("YouTube stream resolution timed out or failed") from exc
    urls = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if result.returncode or len(urls) != 1:
        detail = result.stderr.strip().splitlines()
        message = detail[-1][:300] if detail else "no playable combined stream was found"
        raise LocationResolutionError(f"YouTube stream resolution failed: {message}")
    parsed = urlparse(urls[0])
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise LocationResolutionError("yt-dlp returned an invalid media location")
    return urls[0]
