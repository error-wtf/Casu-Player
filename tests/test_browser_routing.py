"""Regression tests for provider tabs, pasted links and browser selection."""
import sys

import pytest

from casu import webproviders as providers


@pytest.mark.parametrize('url,expected', [
    ('https://www.netflix.com/watch/123', 'netflix'),
    ('https://open.spotify.com/track/abc', 'spotify'),
    ('https://accounts.spotify.com/login', 'spotify'),
    ('https://listen.tidal.com/', 'tidal'),
    ('https://notnetflix.com/', None),
    ('https://netflix.com.example.org/', None),
    ('https://example.org/?next=https://netflix.com', None),
    ('https://netflix.com@example.org/', None),
    ('javascript:netflix.com', None),
])
def test_provider_domain_boundaries(url, expected):
    assert providers.provider_for_url(url) == expected


def test_linux_browser_selection_avoids_unverified_chromium(monkeypatch):
    monkeypatch.setattr(sys, 'platform', 'linux')
    monkeypatch.setattr(providers.shutil, 'which', lambda name: '/bin/' + name if name in {'chromium', 'firefox'} else None)
    assert providers.browser_command('https://www.netflix.com/') == ['/bin/firefox', '--new-window', 'https://www.netflix.com/']
    monkeypatch.setattr(providers.shutil, 'which', lambda name: '/bin/chromium' if name == 'chromium' else None)
    assert providers.browser_command('https://www.netflix.com/') is None


def test_chrome_uses_app_window(monkeypatch):
    monkeypatch.setattr(sys, 'platform', 'linux')
    monkeypatch.setattr(providers.shutil, 'which', lambda name: '/bin/chrome' if name == 'google-chrome' else None)
    assert providers.browser_command('https://open.spotify.com/') == ['/bin/chrome', '--app=https://open.spotify.com/']


def test_macos_uses_safari(monkeypatch):
    monkeypatch.setattr(sys, 'platform', 'darwin')
    assert providers.browser_command('https://www.netflix.com/') == ['/usr/bin/open', '-a', 'Safari', 'https://www.netflix.com/']


def test_old_embed_link_launches_full_player(monkeypatch):
    calls = []
    monkeypatch.setattr(providers, 'browser_command', lambda url: ['browser', url])
    monkeypatch.setattr(providers.subprocess, 'Popen', lambda args, **kw: calls.append(args))
    assert providers.open_web_player('spotify', url='https://open.spotify.com/embed/track/abc?utm=x')
    assert calls == [['browser', 'https://open.spotify.com/track/abc?utm=x']]
    assert not providers.open_web_player('spotify', url='file:///tmp/test')
    assert len(calls) == 1


@pytest.fixture
def tabs(monkeypatch):
    from PySide6.QtWidgets import QApplication
    from mpcasu_qt import webplayers
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(webplayers, '_HAVE_WEBENGINE', False)
    widget = webplayers.WebPlayerTabs()
    calls = []
    monkeypatch.setattr(webplayers, 'open_web_player', lambda provider, **kw: calls.append((provider, kw['url'])) or True)
    yield widget, calls, webplayers
    widget.close()
    widget.deleteLater()
    app.processEvents()


@pytest.mark.parametrize('key', ['spotify', 'netflix', 'tidal'])
def test_every_drm_tab_has_no_embedded_view(tabs, key):
    widget, calls, _ = tabs
    assert widget._views[key] is None
    widget._tab_clicked(list(providers.WEB_PLAYERS).index(key))
    assert calls == [(key, providers.web_player_url(key))]


def test_direct_spotify_submission_preserves_full_url(tabs):
    widget, calls, _ = tabs
    widget._entries['spotify'].setText('https://open.spotify.com/track/abc')
    widget._submit('spotify')
    assert calls == [('spotify', 'https://open.spotify.com/track/abc')]


def test_browse_cannot_bypass_drm_routing(tabs):
    widget, calls, _ = tabs
    widget.open('browse', url='https://www.netflix.com/watch/123')
    widget._entries['browse'].setText('https://open.spotify.com/album/abc')
    widget._submit_browse()
    assert calls == [('netflix', 'https://www.netflix.com/watch/123'), ('spotify', 'https://open.spotify.com/album/abc')]


def test_launch_failure_is_visible_and_signalled(tabs, monkeypatch):
    widget, calls, module = tabs
    monkeypatch.setattr(module, 'open_web_player', lambda *args, **kw: False)
    results = []
    widget.browser_launched.connect(lambda key, ok: results.append((key, ok)))
    assert widget.open('netflix') is False
    assert results == [('netflix', False)]
    assert 'konnte nicht' in widget._messages['netflix'].text()
