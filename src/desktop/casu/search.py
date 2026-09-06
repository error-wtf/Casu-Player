# SPDX-License-Identifier: LicenseRef-CASU-AntiCapitalist-1.4
# SPDX-FileCopyrightText: 2026 Lino Casu
"""YouTube search via yt-dlp with structured, bounded results.

All search in this product runs against the YouTube index; the music variant
is a convenience preset for music queries.  Results are always labelled with
their real provider ("youtube") — never as Spotify.  Spotify search goes
through spotDL (casu.spotify), which returns real Spotify track metadata and
resolves each track to a matched playable audio source.  Results are metadata
only — playback resolves each entry on demand and never writes downloads to
disk.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from urllib.parse import quote_plus
from dataclasses import asdict, dataclass

MAX_SEARCH_LIMIT = 25
MAX_PLAYLIST_ENTRIES = 10_000
DEFAULT_TIMEOUT = 30.0


class SearchError(ValueError):
    pass


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    duration: float | None
    uploader: str
    source: str
    thumbnail: str = ""
    playlist_title: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def _run_ytdlp_search(query: str, limit: int, timeout: float) -> list[dict]:
    executable = shutil.which("yt-dlp")
    if not executable:
        raise SearchError("search requires yt-dlp (Debian package: yt-dlp)")
    limit = max(1, min(int(limit), MAX_SEARCH_LIMIT))
    command = [executable, "--no-warnings", "--flat-playlist", "--dump-json",
               "--socket-timeout", "10", f"ytsearch{limit}:{query}"]
    try:
        proc = subprocess.run(command, check=False, text=True,
                              capture_output=True,
                              timeout=max(5.0, float(timeout)))
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SearchError(f"search failed: {exc}") from exc
    entries: list[dict] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    if not entries:
        detail = proc.stderr.strip().splitlines()
        raise SearchError(detail[-1][:300] if detail else "no results found")
    return entries


def _to_results(entries: list[dict], source: str, limit: int) -> list[SearchResult]:
    results: list[SearchResult] = []
    for entry in entries:
        video_id = str(entry.get("id") or "")
        url = str(entry.get("url") or "")
        if not url and video_id:
            url = f"https://www.youtube.com/watch?v={video_id}"
        if not url:
            continue
        duration = entry.get("duration")
        try:
            duration = float(duration) if duration is not None else None
        except (TypeError, ValueError):
            duration = None
        thumbnail = str(entry.get("thumbnail") or "")
        if not thumbnail:
            video_id = str(entry.get("id") or "")
            if video_id:
                thumbnail = f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg"
        results.append(SearchResult(
            title=str(entry.get("title") or video_id or url)[:300],
            url=url,
            duration=duration,
            uploader=str(entry.get("uploader") or entry.get("channel") or "")[:200],
            source=source,
            thumbnail=thumbnail[:500],
            playlist_title=str(entry.get("playlist_title") or "")[:300],
        ))
        if len(results) >= limit:
            break
    if not results:
        raise SearchError("search returned no usable entries")
    return results


def search_youtube(query: str, *, limit: int = 12,
                   timeout: float = DEFAULT_TIMEOUT) -> list[SearchResult]:
    """Search YouTube videos; returns at most `limit` structured results."""
    query = (query or "").strip()
    if not query:
        raise SearchError("search query must not be empty")
    return _to_results(_run_ytdlp_search(query, limit, timeout), "youtube", limit)


def search_music(query: str, *, limit: int = 12,
                 timeout: float = DEFAULT_TIMEOUT) -> list[SearchResult]:
    """Music-oriented YouTube search preset (results labelled "youtube")."""
    query = (query or "").strip()
    if not query:
        raise SearchError("search query must not be empty")
    return _to_results(_run_ytdlp_search(query, limit, timeout), "youtube", limit)


def search_youtube_playlists(query: str, *, limit: int = 12,
                             timeout: float = DEFAULT_TIMEOUT) -> list[SearchResult]:
    """Search YouTube's actual playlist result type, never title guessing."""
    query = (query or "").strip()
    if not query:
        raise SearchError("search query must not be empty")
    executable = shutil.which("yt-dlp")
    if not executable:
        raise SearchError("playlist search requires yt-dlp")
    limit = max(1, min(int(limit), MAX_SEARCH_LIMIT))
    # YouTube's sp=EgIQAw%3D%3D filter selects the Playlist result type.
    url = ("https://www.youtube.com/results?search_query="
           f"{quote_plus(query)}&sp=EgIQAw%253D%253D")
    command = [executable, "--no-warnings", "--flat-playlist", "--dump-json",
               "--playlist-end", str(limit), "--socket-timeout", "10", url]
    try:
        proc = subprocess.run(command, check=False, text=True,
                              capture_output=True,
                              timeout=max(5.0, float(timeout)))
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SearchError(f"playlist search failed: {exc}") from exc
    results: list[SearchResult] = []
    for line in proc.stdout.splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        playlist_id = str(entry.get("id") or "").strip()
        candidate = str(entry.get("webpage_url") or entry.get("url") or "")
        if "list=" not in candidate and playlist_id:
            candidate = f"https://www.youtube.com/playlist?list={playlist_id}"
        if "list=" not in candidate:
            continue
        results.append(SearchResult(
            title=str(entry.get("title") or playlist_id)[:300], url=candidate,
            duration=None,
            uploader=str(entry.get("uploader") or entry.get("channel") or "")[:200],
            source="youtube_playlist",
            thumbnail=str(entry.get("thumbnail") or "")[:500]))
        if len(results) >= limit:
            break
    if not results:
        detail = proc.stderr.strip().splitlines()
        raise SearchError(detail[-1][:300] if detail else "no playlists found")
    return results


def search_youtube_playlist(url: str, *, limit: int = MAX_PLAYLIST_ENTRIES,
                            timeout: float = 60.0) -> list[SearchResult]:
    """Expand a YouTube playlist URL into its individual videos.

    Uses ``yt-dlp --flat-playlist --dump-json <url>`` (one JSON line per
    video) so a playlist becomes a queue of playable entries.
    """
    url = (url or "").strip()
    if not url:
        raise SearchError("playlist URL must not be empty")
    executable = shutil.which("yt-dlp")
    if not executable:
        raise SearchError("YouTube playlist expansion requires yt-dlp")
    limit = max(1, min(int(limit), MAX_PLAYLIST_ENTRIES))
    command = [executable, "--flat-playlist", "--no-warnings",
               "--dump-json", "--socket-timeout", "15", url]
    try:
        proc = subprocess.run(command, check=False, text=True,
                              capture_output=True,
                              timeout=max(10.0, float(timeout)))
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SearchError(f"YouTube playlist expansion failed: {exc}") from exc
    entries: list[dict] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    if not entries:
        detail = proc.stderr.strip().splitlines()
        raise SearchError(
            detail[-1][:300] if detail else "playlist returned no videos")
    if len(entries) > limit and limit >= MAX_PLAYLIST_ENTRIES:
        raise SearchError(
            f"playlist exceeds safety ceiling of {MAX_PLAYLIST_ENTRIES} entries")
    return _to_results(entries, "youtube", limit)


def _has_list_param(url: str) -> bool:
    """True if a YouTube URL carries a playlist (``list=``) parameter."""
    from .locations import is_youtube_url
    if not is_youtube_url(url):
        return False
    return "list=" in url


def youtube_playlist_id(url: str) -> str:
    """Return the YouTube playlist id (``list=`` value) or an empty string."""
    try:
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(url.strip())
        values = parse_qs(parsed.query).get("list")
        return values[0].strip() if values else ""
    except ValueError:
        return ""


def split_youtube_input(text: str) -> list[str]:
    """Split a free-form YouTube field into individual URL tokens.

    Accepts commas, semicolons and line breaks as separators so a user can
    paste several YouTube videos and/or playlist links at once.  Each token is
    trimmed; empty tokens are dropped.
    """
    raw = (text or "").strip()
    if not raw:
        return []
    tokens = []
    for piece in re.split(r"[\n,;]+", raw):
        piece = piece.strip()
        if not piece:
            continue
        tokens.append(piece)
    return tokens


def expand_youtube_input(text: str, *, limit: int = MAX_PLAYLIST_ENTRIES,
                         timeout: float = 60.0) -> list[SearchResult]:
    """Expand a free-form YouTube field into a flat list of individual videos.

    The field may contain several single video URLs and/or complete playlist
    URLs, separated by commas, semicolons or line breaks.  Single video URLs
    are kept as-is (one queue entry each); playlist URLs are expanded into
    their individual videos via ``yt-dlp --flat-playlist``.  The result is a
    flat, ordered list that the caller can drop straight into the queue so
    shuffle/repeat act per-video.
    """
    results: list[SearchResult] = []
    seen_urls: set[str] = set()
    for token in split_youtube_input(text):
        if youtube_playlist_id(token):
            found = search_youtube_playlist(token, limit=limit, timeout=timeout)
        else:
            found = [
                SearchResult(title=str(token)[:300], url=token,
                             duration=None, uploader="", source="youtube",
                             thumbnail="")
            ]
        for result in found:
            url = result.url.strip()
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            results.append(result)
        if len(results) >= limit:
            break
    if not results:
        raise SearchError("no YouTube videos or playlists were recognised")
    return results
