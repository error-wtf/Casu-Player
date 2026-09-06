# SPDX-License-Identifier: LicenseRef-CASU-AntiCapitalist-1.4
# SPDX-FileCopyrightText: 2026 Lino Casu
"""Legal web-player integrations.

These services (Spotify, Hearthis.at, Tidal, Netflix) only allow their DRM /
authenticated audio/video to be played inside their own player with a normal
account login. MPCASU integrates them by opening the official web player in a
supported system browser at the relevant URL (home / search / item). No
streams are scraped, downloaded or replayed.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import urllib.parse

WEB_PLAYERS: dict[str, dict] = {
    "spotify": {
        "label": "SPOTIFY",
        "home": "https://open.spotify.com/",
        "search": lambda q: "https://open.spotify.com/search/" + urllib.parse.quote(q),
        "item": lambda url: url,
        "icon": "♪",
    },
    "hearthis": {
        "label": "HEARTHIS",
        "home": "https://hearthis.at/",
        "search": lambda q: "https://hearthis.at/search/?q=" + urllib.parse.quote(q),
        "item": lambda url: url,
        "icon": "↗",
    },
    "tidal": {
        "label": "TIDAL",
        "home": "https://tidal.com/",
        "search": lambda q: "https://tidal.com/search?q=" + urllib.parse.quote(q),
        "item": lambda url: url,
        "icon": "▤",
    },
    "netflix": {
        "label": "NETFLIX",
        "home": "https://www.netflix.com/browse",
        "search": lambda q: "https://www.netflix.com/search?q=" + urllib.parse.quote(q),
        "item": lambda url: url,
        "icon": "▣",
    },
}

# Full web players need a maintained browser with its own DRM installation.
# A Chromium executable alone does not establish Widevine availability.
EXTERNAL_PROVIDERS = frozenset({"spotify", "tidal", "netflix"})


def browser_command(url: str) -> list[str] | None:
    """Prefer Chrome/Edge app windows; Firefox uses a normal browser window.

    Keep the browser's regular profile, sandbox and component updater so login
    and Widevine are managed by the browser. Do not spoof a browser identity.
    """
    if sys.platform == "darwin":
        return ["/usr/bin/open", "-a", "Safari", url]
    for name in ("google-chrome", "google-chrome-stable", "microsoft-edge",
                 "microsoft-edge-stable", "firefox"):
        binary = shutil.which(name)
        if binary:
            if name == "firefox":
                return [binary, "--new-window", url]
            return [binary, "--app=" + url]
    return None


def chromium_binary() -> str | None:
    """Compatibility helper for callers requiring an installed Chromium family."""
    for name in ("google-chrome", "google-chrome-stable", "microsoft-edge",
                 "microsoft-edge-stable", "chromium-browser", "chromium"):
        binary = shutil.which(name)
        if binary:
            return binary
    return None


_PROVIDER_DOMAINS = {
    "spotify": "spotify.com",
    "hearthis": "hearthis.at",
    "tidal": "tidal.com",
    "netflix": "netflix.com",
}


def provider_for_url(url: str) -> str | None:
    """Return the web-player provider a URL belongs to, or None."""
    try:
        parsed = urllib.parse.urlsplit(url or "")
        if parsed.scheme not in ("http", "https"):
            return None
        host = (parsed.hostname or "").lower().rstrip(".")
    except ValueError:
        return None
    for key, domain in _PROVIDER_DOMAINS.items():
        if host == domain or host.endswith("." + domain):
            return key
    return None


_SPOTIFY_ITEM_RE = re.compile(
    r"^(https?://open\.spotify\.com)/(track|album|playlist|artist|show|episode)/([a-zA-Z0-9]+)(?:[?&#].*)?$"
)


def spotify_embed_url(url: str) -> str:
    """Convert a Spotify item URL to its official embed URL.

    Spotify blocks embedding the full web app, but provides official embed
    players at ``open.spotify.com/embed/<type>/<id>`` which are iframe-safe.
    Non-convertible URLs are returned unchanged.
    """
    match = _SPOTIFY_ITEM_RE.match((url or "").strip())
    if match:
        return f"{match.group(1)}/embed/{match.group(2)}/{match.group(3)}"
    return (url or "").strip()


def web_player_url(provider: str, *, query: str = "", url: str = "") -> str:
    """Return the web-player URL for a provider (home/search/item)."""
    spec = WEB_PLAYERS.get(provider, WEB_PLAYERS["spotify"])
    if url:
        return str(url)
    if query:
        return str(spec["search"](query))
    return str(spec["home"])


def open_web_player(provider: str, *, query: str = "", url: str = "") -> bool:
    """Launch the official player. Success means launch, not verified playback."""
    target = web_player_url(provider, query=query, url=url)
    try:
        parsed = urllib.parse.urlsplit(target)
        if parsed.scheme not in ("https", "http") or not parsed.hostname:
            return False
    except ValueError:
        return False
    # Old saved/embed links must reach the full Spotify web player.
    if parsed.hostname == "open.spotify.com" and parsed.path.startswith("/embed/"):
        target = urllib.parse.urlunsplit(parsed._replace(path=parsed.path[6:]))
    command = browser_command(target)
    if command is None:
        return False
    try:
        subprocess.Popen(command, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False
