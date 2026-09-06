import os
from pathlib import Path
from types import SimpleNamespace
import pytest
from mpcasu_qt import browser_runtime


def test_installed_cdm_is_configured_without_disabling_security(tmp_path, monkeypatch):
    cdm = tmp_path / 'libwidevinecdm.so'
    cdm.write_bytes(b'probe')
    monkeypatch.setattr(browser_runtime, 'widevine_candidates', lambda: iter([cdm]))
    monkeypatch.setenv('QTWEBENGINE_CHROMIUM_FLAGS', '--enable-logging')
    assert browser_runtime.configure_widevine() == str(cdm)
    flags = os.environ['QTWEBENGINE_CHROMIUM_FLAGS']
    assert '--enable-logging' in flags and '--widevine-path=' in flags
    assert '--no-sandbox' not in flags and '--disable-web-security' not in flags


def test_explicit_cdm_setting_is_preserved(tmp_path, monkeypatch):
    cdm = tmp_path / 'libwidevinecdm.so'
    cdm.touch()
    value = '--widevine-path=' + str(cdm)
    monkeypatch.setenv('QTWEBENGINE_CHROMIUM_FLAGS', value)
    assert browser_runtime.configure_widevine() == str(cdm)
    assert os.environ['QTWEBENGINE_CHROMIUM_FLAGS'] == value


def test_missing_cdm_is_reported(monkeypatch):
    monkeypatch.delenv('QTWEBENGINE_CHROMIUM_FLAGS', raising=False)
    monkeypatch.setattr(browser_runtime, 'widevine_candidates', lambda: iter([]))
    assert browser_runtime.configure_widevine() is None


@pytest.fixture
def browser(monkeypatch):
    from PySide6.QtWidgets import QApplication
    from mpcasu_qt import webplayers
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(webplayers, '_HAVE_WEBENGINE', False)
    widget = webplayers.WebPlayerTabs()
    loads = []
    for key in widget._views:
        widget._views[key] = SimpleNamespace(load=lambda url, k=key: loads.append((k, url.toString())))
    yield widget, loads
    widget.close()
    widget.deleteLater()
    app.processEvents()


@pytest.mark.parametrize('provider', ['spotify', 'netflix', 'tidal', 'hearthis'])
def test_providers_load_in_their_own_view(browser, provider, monkeypatch):
    from casu import webproviders
    monkeypatch.setattr(webproviders, 'open_web_player', lambda *a, **kw: pytest.fail('External browser is forbidden'))
    widget, loads = browser
    widget.open(provider)
    assert loads == [(provider, webproviders.web_player_url(provider))]


def test_spotify_full_player_not_embed_preview(browser):
    widget, loads = browser
    widget._entries['spotify'].setText('https://open.spotify.com/track/abc')
    widget._submit('spotify')
    widget.open('spotify', url='https://open.spotify.com/embed/album/def')
    assert loads == [('spotify', 'https://open.spotify.com/track/abc'), ('spotify', 'https://open.spotify.com/album/def')]


def test_browse_remains_embedded_after_popup_tabs(browser):
    from PySide6.QtWidgets import QWidget
    widget, loads = browser
    widget.tabs.addTab(QWidget(), 'Login popup')
    widget.open('browse', url='https://www.netflix.com/')
    assert widget.tabs.currentIndex() == widget._browse_index
    assert loads == [('browse', 'https://www.netflix.com/')]
