# SPDX-License-Identifier: LicenseRef-CASU-AntiCapitalist-1.4
# SPDX-FileCopyrightText: 2026 Lino Casu
"""Legal web-player integrations.

These services (Spotify, Hearthis.at, Tidal, Netflix) only allow their DRM /
authenticated audio/video to be played inside their own player with a normal
account login. MPCASU integrates them by opening the official web player in a
system Chromium browser at the relevant URL (home / search / item). No
streams are scraped, downloaded or replayed.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
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

# Spotify and Tidal encrypt their audio with Widevine DRM, which the embedded
# QtWebEngine build does not bundle; the system Chromium does. Those providers
# therefore open in system Chromium (guaranteed playback with the user login),
# while non-DRM providers stay embedded in the player.
EXTERNAL_PROVIDERS = frozenset({"spotify", "tidal"})


def chromium_binary() -> str | None:
    candidates = (shutil.which("chromium-browser"),
                  shutil.which("chromium"),
                  shutil.which("google-chrome"),
                  "/snap/bin/chromium")
    return next((candidate for candidate in candidates if candidate
                 and os.path.exists(candidate)), None)


_PROVIDER_DOMAINS = {
    "spotify": "spotify.com",
    "hearthis": "hearthis.at",
    "tidal": "tidal.com",
    "netflix": "netflix.com",
}


def provider_for_url(url: str) -> str | None:
    """Return the web-player provider a URL belongs to, or None."""
    low = (url or "").lower()
    for key, domain in _PROVIDER_DOMAINS.items():
        if domain in low:
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
    """Open a provider's official web player in a system Chromium browser."""
    binary = chromium_binary()
    if not binary:
        return False
    target = web_player_url(provider, query=query, url=url)
    try:
        subprocess.Popen([binary, target], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False
