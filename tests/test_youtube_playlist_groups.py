from types import SimpleNamespace
from pathlib import Path
import pytest
from casu import youtube_groups as groups
from casu.search import SearchResult
from casu.playlist import PlaylistModel, load_playlist_file, save_playlist_file


def test_youtube_import_retains_two_groups_and_single_video(monkeypatch, tmp_path):
    shared = SearchResult('Song ä', 'https://www.youtube.com/watch?v=abcdefghijk', None, '', 'youtube')
    monkeypatch.setattr(groups, 'search_youtube_playlist', lambda url: [shared])
    result = groups.expand_queue_input('https://www.youtube.com/playlist?list=PLone;https://youtu.be/12345678901;https://www.youtube.com/playlist?list=PLtwo')
    assert len(result) == 3
    assert isinstance(result[0], groups.YouTubePlaylistGroup)
    assert isinstance(result[1], SearchResult)
    assert isinstance(result[2], groups.YouTubePlaylistGroup)
    paths = [groups.save_youtube_group(result[i], tmp_path) for i in (0, 2)]
    assert paths[0] != paths[1]
    for path in paths:
        assert load_playlist_file(path).items == (shared.url,)
        assert '#EXTINF:-1,Song ä' in path.read_text()


def test_empty_youtube_playlist_does_not_add_empty_group(monkeypatch):
    monkeypatch.setattr(groups, 'search_youtube_playlist', lambda url: [])
    assert groups.expand_queue_input('https://www.youtube.com/playlist?list=PLempty') == []


@pytest.mark.parametrize('extension', ['m3u', 'm3u8', 'pls', 'xspf', 'json'])
def test_export_formats_round_trip(extension, tmp_path):
    urls = ('https://example.org/ä.mp3?x=1&y=2', 'https://example.org/b.mp4')
    target = tmp_path / ('queue.' + extension)
    save_playlist_file(target, PlaylistModel(urls))
    assert load_playlist_file(target).items == urls


def test_save_dialog_offers_all_formats(monkeypatch):
    from PySide6.QtWidgets import QApplication, QFileDialog
    from mpcasu_qt.main_window import MainWindow
    app = QApplication.instance() or QApplication([])
    captured = []
    def cancel(dialog):
        captured.extend(dialog.nameFilters())
        return 0
    monkeypatch.setattr(QFileDialog, 'exec', cancel)
    # Real Qt dialog without initializing unrelated playback devices.
    from PySide6.QtWidgets import QWidget
    owner = QWidget()
    owner.playlist_model = [1]
    MainWindow.save_playlist(owner)
    assert len(captured) == 5
    assert any('*.m3u8' in item for item in captured)
    assert any('*.m3u)' in item for item in captured)
