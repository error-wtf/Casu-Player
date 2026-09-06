"""Discover an installed Widevine CDM before Qt initializes Chromium.

The module stays with the browser that updates it. CASU neither distributes
CDM binaries nor changes authentication or license requests.
"""
from __future__ import annotations
import os
from pathlib import Path
import platform
import shlex
import sys


def widevine_candidates():
    explicit = os.environ.get('CASU_WIDEVINE_PATH', '')
    if explicit:
        yield Path(explicit)
    home = Path.home()
    if sys.platform == 'darwin':
        arch = 'mac_arm64' if platform.machine() == 'arm64' else 'mac_x64'
        for app in ('Google Chrome', 'Microsoft Edge', 'Brave Browser', 'Vivaldi'):
            root = Path('/Applications') / (app + '.app') / 'Contents/Frameworks'
            yield from root.glob(f'*/Versions/*/Libraries/WidevineCdm/_platform_specific/{arch}/libwidevinecdm.dylib')
        configs = [home / 'Library/Application Support' / name for name in ('Google/Chrome', 'Microsoft Edge', 'BraveSoftware/Brave-Browser', 'Vivaldi')]
        library = 'libwidevinecdm.dylib'
    else:
        arch = 'linux_x64' if platform.machine() in ('x86_64', 'AMD64') else 'linux_arm64'
        for root in ('/opt/google/chrome', '/opt/microsoft/msedge', '/opt/vivaldi', '/usr/lib/chromium', '/usr/lib/chromium-browser'):
            yield Path(root) / 'WidevineCdm' / '_platform_specific' / arch / 'libwidevinecdm.so'
        config = Path(os.environ.get('XDG_CONFIG_HOME', home / '.config'))
        configs = [config / name for name in ('google-chrome', 'chromium', 'vivaldi', 'BraveSoftware/Brave-Browser', 'microsoft-edge')]
        library = 'libwidevinecdm.so'
    for config in configs:
        paths = config.glob(f'WidevineCdm/*/_platform_specific/{arch}/{library}')
        yield from sorted(paths, key=lambda p: tuple(int(v) if v.isdigit() else 0 for v in p.parents[2].name.split('.')), reverse=True)


def configure_widevine() -> str | None:
    flags = os.environ.get('QTWEBENGINE_CHROMIUM_FLAGS', '')
    try:
        args = shlex.split(flags)
    except ValueError:
        return None
    for arg in args:
        if arg.startswith('--widevine-path='):
            path = arg.split('=', 1)[1]
            return path if Path(path).is_file() else None
    path = next((p for p in widevine_candidates() if p.is_file()), None)
    if path is not None:
        os.environ['QTWEBENGINE_CHROMIUM_FLAGS'] = (flags + ' ' + shlex.quote('--widevine-path=' + str(path))).strip()
        return str(path)
    return None
