"""Retain YouTube playlist boundaries while using ordinary playlist queue rows."""
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import tempfile
from .search import SearchResult, search_youtube_playlist, split_youtube_input, youtube_playlist_id

@dataclass
class YouTubePlaylistGroup:
    url: str
    title: str
    items: list[SearchResult]


def expand_queue_input(text: str, *, title: str = ''):
    result = []
    for token in split_youtube_input(text):
        playlist_id = youtube_playlist_id(token)
        if playlist_id:
            items = search_youtube_playlist(token)
            if not items:
                continue
            label = title or getattr(items[0], 'playlist_title', '') or 'YouTube ' + playlist_id
            result.append(YouTubePlaylistGroup(token, label, items))
        else:
            result.append(SearchResult(token, token, None, '', 'youtube'))
    return result


def save_youtube_group(group: YouTubePlaylistGroup, directory: Path | None = None) -> Path:
    if directory is None:
        directory = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share')) / 'mpcasu/youtube-playlists'
    directory.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', group.title).strip(' .')[:80] or 'YouTube'
    identity = hashlib.sha256((youtube_playlist_id(group.url) or group.url).encode('utf-8')).hexdigest()[:12]
    target = directory / f'{slug}-{identity}.m3u8'
    clean = lambda value: str(value).replace('\r', ' ').replace('\n', ' ')
    lines = ['#EXTM3U', '#PLAYLIST:' + clean(group.title)]
    for item in group.items:
        lines += ['#EXTINF:-1,' + clean(item.title), clean(item.url)]
    fd, name = tempfile.mkstemp(prefix='.playlist-', dir=directory)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write('\n'.join(lines) + '\n')
        os.replace(name, target)
    finally:
        if os.path.exists(name): os.unlink(name)
    return target
