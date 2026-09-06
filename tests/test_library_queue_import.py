from pathlib import Path
from types import SimpleNamespace
import json
import pytest
from PySide6.QtWidgets import QApplication
from casu.playlist import PlaylistModel, load_playlist_file, save_playlist_file
from mpcasu_qt.main_window import LibraryPage

@pytest.fixture
def page(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'data'))
    settings = SimpleNamespace(load=lambda: SimpleNamespace(watched_folders=[str(tmp_path / 'lists')]))
    library = SimpleNamespace(items=lambda **kw: [], get=lambda p: None)
    widget = LibraryPage(library, tmp_path / 'thumbs', settings)
    yield widget
    widget.close()
    widget.deleteLater()
    app.processEvents()

@pytest.mark.parametrize('extension', ['m3u','m3u8','pls','json','xspf','wpl','jspf','asx','ram','rmp'])
def test_library_import_uses_queue_parser_and_preserves_urls(page, tmp_path, extension):
    directory = tmp_path / 'lists'; directory.mkdir()
    media = directory / 'überall.mp3'; media.touch()
    stream = 'https://example.org/music.mp3?a=1&b=2'
    playlist = directory / ('mix.' + extension)
    save_playlist_file(playlist, PlaylistModel([media, stream]))
    page._mode_combo.setCurrentIndex(list(page.MODES).index('playlists'))
    page._refresh()
    assert page._tracks == [media, stream]
    assert page._tracks_list.count() == 2
    received=[];page.addRequested.connect(received.append)
    page._add_playlist_btn.click()
    assert received[-1] == [playlist]
    page._tracks_list.item(1).setSelected(True)
    page._add_selected()
    assert received[-1] == [stream]


def test_relative_files_duplicate_names_and_multi_playlist_selection(page, tmp_path):
    directory=tmp_path/'lists';directory.mkdir()
    files=[]
    for folder in ['first','second']:
        root=directory/folder;root.mkdir()
        (root/'local.mp3').touch()
        p=root/'same.m3u';p.write_text('#EXTM3U\nlocal.mp3\n')
        files.append(p)
    page._mode_combo.setCurrentIndex(list(page.MODES).index('playlists'))
    page._refresh()
    assert page._groups_list.count()==2
    assert page._tracks[0] == files[0].parent/'local.mp3'
    for i in range(2):page._groups_list.item(i).setSelected(True)
    received=[];page.addRequested.connect(received.append)
    page._add_playlist_groups()
    assert received[-1]==files


def test_mobile_json_and_cue_use_relative_paths(tmp_path):
    mp3=tmp_path/'space name.mp3';mp3.touch()
    cue=tmp_path/'album.cue';cue.write_text('FILE "space name.mp3" MP3\n  TRACK 01 AUDIO\n')
    mobile=tmp_path/'android.json';mobile.write_text(json.dumps({'type':'mpcasu-playlist','items':[{'url':'space name.mp3','title':'Tagged title'}]}))
    assert load_playlist_file(cue).items==(mp3,)
    assert load_playlist_file(mobile).items==(mp3,)
