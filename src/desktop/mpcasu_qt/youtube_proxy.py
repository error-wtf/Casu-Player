# SPDX-License-Identifier: LicenseRef-CASU-AntiCapitalist-1.4
# SPDX-FileCopyrightText: 2026 Lino Casu
"""Loopback byte/range transport for resolved YouTube media in MPCASU.

Architecture (mirrors the proven web-casu path):

    YouTube URL
        -> casu.locations.resolve_media_location()   (shared with web-casu)
        -> direct googlevideo URL
        -> this proxy (transport only, no player)
        -> LibVLCBackend.open_source(loopback_url)
        -> PlaybackController -> VideoSurface

web-casu hands the same resolved URL to a browser, which fetches it with a
plain browser request (browser User-Agent, no Referer, no cookies — the web
app sends Referrer-Policy: no-referrer). libVLC cannot always fetch
googlevideo directly, so MPCASU inserts this thin loopback transport that
mirrors exactly that request profile.

The proxy never resolves YouTube itself and never touches yt-dlp. On
HTTP 403/410 (expired CDN URL) it invokes the supplied refresh callback —
which re-runs the shared resolver — once per request and retries.

It is not a player: no HTML, no <video>, no iframe, no playback state.
"""
from __future__ import annotations

import secrets
import threading
import urllib.error
import urllib.request
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class YouTubeProxyError(RuntimeError):
    pass


class _LoopbackHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


# The request profile web-casu's browser sends for the resolved URL: a plain
# browser User-Agent, no Referer (Referrer-Policy: no-referrer), no cookies.
_BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:142.0) "
                   "Gecko/20100101 Firefox/142.0"),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "identity",
}

_CHUNK = 256 * 1024


class YouTubeMediaProxy:
    """Serve one already-resolved media URL over loopback with Range support."""

    RETRYABLE_HTTP = {403, 410}

    def __init__(self, *, upstream_timeout: float = 20.0) -> None:
        self.upstream_timeout = float(upstream_timeout)
        self._upstream_url = ""
        self._refresh_callback: Callable[[], str] | None = None
        self._token = ""
        self._server: _LoopbackHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._state_lock = threading.RLock()
        self._resolve_lock = threading.Lock()

    def start(self, media_url: str, *,
              refresh: Callable[[], str] | None = None) -> str:
        """Serve *media_url* and return the loopback URL for LibVLCBackend.

        *media_url* must already be resolved (the shared web-casu resolver,
        ``casu.locations.resolve_media_location``). *refresh* is called once
        per request when the CDN answers 403/410 so an expired URL can be
        re-resolved transparently.
        """
        media_url = str(media_url).strip()
        if not media_url.startswith(("http://", "https://")):
            raise YouTubeProxyError("resolved YouTube media URL is empty or not HTTP")
        self.stop()
        self._refresh_callback = refresh
        with self._state_lock:
            self._upstream_url = media_url
        self._preflight()
        self._token = secrets.token_urlsafe(18)
        self._server = _LoopbackHTTPServer(("127.0.0.1", 0), self._make_handler())
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="mpcasu-youtube-proxy",
            daemon=True,
        )
        self._thread.start()
        port = int(self._server.server_address[1])
        print(f"[YT-PROXY] started port={port}", flush=True)
        return f"http://127.0.0.1:{port}/{self._token}/media"

    def stop(self, *, reason: str = "stop") -> None:
        server, self._server = self._server, None
        thread, self._thread = self._thread, None
        if server is not None:
            try:
                server.shutdown()
            finally:
                server.server_close()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        with self._state_lock:
            self._upstream_url = ""
        self._refresh_callback = None
        self._token = ""
        if server is not None:
            print(f"[YT-PROXY] stopped reason={reason}", flush=True)

    # ------------------------------------------------------------------
    # upstream transport
    # ------------------------------------------------------------------
    def _snapshot(self) -> str:
        with self._state_lock:
            if not self._upstream_url:
                raise YouTubeProxyError("YouTube proxy has no resolved upstream")
            return self._upstream_url

    def _refresh(self, stale_url: str) -> None:
        callback = self._refresh_callback
        if callback is None:
            return
        with self._resolve_lock:
            with self._state_lock:
                if self._upstream_url and self._upstream_url != stale_url:
                    return  # another request already refreshed it
            try:
                fresh = str(callback()).strip()
            except Exception as exc:  # noqa: BLE001 - surface as proxy error
                raise YouTubeProxyError(f"YouTube re-resolve failed: {exc}") from exc
            if not fresh.startswith(("http://", "https://")):
                raise YouTubeProxyError("YouTube re-resolve returned no HTTP URL")
            with self._state_lock:
                self._upstream_url = fresh

    def _open_upstream(self, *, method: str, range_header: str | None):
        last_error: Exception | None = None
        for attempt in range(2):
            url = self._snapshot()
            headers = dict(_BROWSER_HEADERS)
            if range_header:
                headers["Range"] = range_header
            request = urllib.request.Request(url, headers=headers, method=method)
            try:
                return urllib.request.urlopen(request, timeout=self.upstream_timeout)
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code in self.RETRYABLE_HTTP and attempt == 0:
                    try:
                        exc.close()
                    except Exception:
                        pass
                    self._refresh(url)
                    continue
                raise
            except urllib.error.URLError as exc:
                last_error = exc
                raise
        raise YouTubeProxyError(str(last_error or "upstream request failed"))

    def _preflight(self) -> None:
        """Prove the resolved URL plays before libVLC ever sees it.

        One byte-range probe mirrors the real media request; a 403/410 here
        triggers the transparent re-resolve inside _open_upstream.
        """
        try:
            upstream = self._open_upstream(method="GET", range_header="bytes=0-0")
        except urllib.error.HTTPError as exc:
            raise YouTubeProxyError(
                f"YouTube CDN rejected the media request (HTTP {exc.code})") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise YouTubeProxyError(f"YouTube media unreachable: {exc}") from exc
        try:
            upstream.read(1)
        finally:
            try:
                upstream.close()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # loopback HTTP
    # ------------------------------------------------------------------
    def _trusted_host(self, handler: BaseHTTPRequestHandler) -> bool:
        if self._server is None:
            return False
        port = int(self._server.server_address[1])
        host = handler.headers.get("Host", "").strip().lower()
        return host in {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}

    def _make_handler(self):
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            # Never block forever on a silent peer: without a socket timeout
            # handle_one_request waits indefinitely for the next keep-alive
            # request, and a libVLC teardown that still sees the open socket
            # can block libvlc_media_player_stop — the whole GUI freezes.
            timeout = 10

            def do_HEAD(self):
                self._dispatch(head=True)

            def do_GET(self):
                self._dispatch(head=False)

            def _dispatch(self, *, head: bool) -> None:
                if not proxy._trusted_host(self):
                    self.send_error(421, "untrusted loopback host")
                    return
                prefix = f"/{proxy._token}/"
                if self.path != f"{prefix}media":
                    self.send_error(404)
                    return
                self._media(head=head)

            def _media(self, *, head: bool) -> None:
                range_header = self.headers.get("Range")
                print(f"[YT-PROXY-IN] {self.command} leaf=media "
                      f"Range={range_header!r} "
                      f"UA={self.headers.get('User-Agent', '')[:40]!r} "
                      f"Connection={self.headers.get('Connection', '')!r}",
                      flush=True)
                try:
                    upstream = proxy._open_upstream(
                        method="HEAD" if head else "GET",
                        range_header=range_header,
                    )
                except urllib.error.HTTPError as exc:
                    self.send_error(exc.code, "YouTube upstream rejected the media request")
                    return
                except (urllib.error.URLError, OSError, YouTubeProxyError) as exc:
                    self.send_error(502, str(exc))
                    return

                try:
                    status = int(getattr(upstream, "status", 200) or 200)
                    self.send_response(status)
                    forwarded: dict[str, str] = {}
                    for name in (
                        "Content-Type",
                        "Content-Length",
                        "Content-Range",
                        "Accept-Ranges",
                        "ETag",
                        "Last-Modified",
                    ):
                        value = upstream.headers.get(name)
                        if value:
                            forwarded[name] = value
                            self.send_header(name, value)
                    if not upstream.headers.get("Accept-Ranges"):
                        self.send_header("Accept-Ranges", "bytes")
                        forwarded.setdefault("Accept-Ranges", "bytes")
                    self.send_header("Cache-Control", "no-store")
                    # One-shot connections only. A persistent connection would
                    # keep this handler thread blocked in readline() after the
                    # body until the player happens to send another request;
                    # libVLC's synchronous stop can then wait on that socket
                    # state and freeze playback teardown (observed as a full
                    # GUI hang). Closing after each response keeps every
                    # teardown path bounded; the loopback connect cost is
                    # irrelevant next to CDN latency.
                    self.send_header("Connection", "close")
                    self.close_connection = True
                    self.end_headers()
                    print(f"[YT-PROXY-OUT] {status} "
                          f"CT={forwarded.get('Content-Type')!r} "
                          f"CL={forwarded.get('Content-Length')!r} "
                          f"CR={forwarded.get('Content-Range')!r} "
                          f"AR={forwarded.get('Accept-Ranges')!r}",
                          flush=True)
                    if head:
                        return
                    sent = 0
                    while True:
                        try:
                            chunk = upstream.read(_CHUNK)
                        except Exception as exc:
                            print(f"[YT-PROXY-BODY] upstream read error at {sent}: {exc!r}", flush=True)
                            break
                        if not chunk:
                            break
                        try:
                            self.wfile.write(chunk)
                            sent += len(chunk)
                        except (BrokenPipeError, ConnectionResetError, OSError) as exc:
                            print(f"[YT-PROXY-BODY] client closed at {sent}: {exc!r}", flush=True)
                            break
                    print(f"[YT-PROXY-BODY] done sent={sent}", flush=True)
                finally:
                    try:
                        upstream.close()
                    except Exception:
                        pass

            def log_message(self, *_args) -> None:
                pass

        return Handler
