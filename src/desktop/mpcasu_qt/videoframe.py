# SPDX-License-Identifier: LicenseRef-CASU-AntiCapitalist-1.4 | SPDX-FileCopyrightText: 2026 Lino Casu
"""Qt video surface and the adapter that embeds libVLC output into it.

``LibVLCBackend`` was written against a Tk widget and calls ``winfo_id()`` to
obtain the native window handle. Rather than modify that tested backend, this
module supplies a tiny adapter object exposing the same single method backed by
``QWidget.winId()``. The backend therefore embeds into Qt unchanged.
"""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QSizePolicy, QWidget


class NativeHandleAdapter:
    """Expose ``winfo_id()`` for a Qt widget so libVLC can embed into it.

    ``LibVLCBackend`` only ever needs the native window id, so mirroring that
    one method keeps the backend completely unaware of the toolkit in use.
    """

    __slots__ = ("_widget",)

    def __init__(self, widget: QWidget) -> None:
        self._widget = widget

    def winfo_id(self) -> int:
        """Return the platform window handle of the wrapped widget."""
        return int(self._widget.winId())

    @property
    def widget(self) -> QWidget:
        return self._widget


class VideoSurface(QWidget):
    """Native window that libVLC renders into directly.

    The widget uses a native window id and paints nothing itself while video is
    active, avoiding flicker from Qt repainting over the video overlay. When no
    video is present it shows either cover art or the MPCASU wordmark.
    """

    doubleClicked = Signal()
    clicked = Signal()
    wheelScrolled = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("VideoSurface")
        self.setAttribute(Qt.WA_NativeWindow, True)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(320, 180)
        self._video_active = False
        self._cover: QPixmap | None = None
        self._native_frame: QPixmap | None = None
        self._native_subtitle: str | None = None
        self._placeholder = "MPCASU"
        self.handle = NativeHandleAdapter(self)

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------
    def set_video_active(self, active: bool) -> None:
        """Mark whether libVLC is currently drawing into this surface."""
        if self._video_active != bool(active):
            self._video_active = bool(active)
            self.update()

    def is_video_active(self) -> bool:
        return self._video_active

    def set_cover(self, pixmap: QPixmap | None) -> None:
        """Display cover art for audio-only playback."""
        self._cover = pixmap
        self.update()

    def cover(self) -> QPixmap | None:
        return self._cover

    def clear(self) -> None:
        """Reset to the idle placeholder state."""
        self._video_active = False
        self._cover = None
        self._native_frame = None
        self._native_subtitle = None
        self.update()

    # ------------------------------------------------------------------
    # Native CASU presentation (Qt-rendered frames, no libVLC involved)
    # ------------------------------------------------------------------
    def set_native_frame(self, pixmap: QPixmap | None) -> None:
        self._native_frame = pixmap
        self.update()

    def set_native_subtitle(self, text: str | None) -> None:
        self._native_subtitle = text
        self.update()

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if self._native_frame is not None and not self._native_frame.isNull():
            painter = QPainter(self)
            try:
                painter.fillRect(self.rect(), QColor("#000000"))
                scaled = self._native_frame.scaled(
                    self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
                x = (self.width() - scaled.width()) // 2
                y = (self.height() - scaled.height()) // 2
                painter.drawPixmap(x, y, scaled)
                if self._native_subtitle:
                    painter.setPen(QPen(QColor("#000000"), 4))
                    font = painter.font()
                    font.setPointSize(13)
                    font.setBold(True)
                    painter.setFont(font)
                    rect = self.rect().adjusted(30, 0, -30, -18)
                    painter.drawText(rect, Qt.AlignHCenter | Qt.AlignBottom,
                                     self._native_subtitle)
                    painter.setPen(QColor("#ffffff"))
                    painter.drawText(rect, Qt.AlignHCenter | Qt.AlignBottom,
                                     self._native_subtitle)
            finally:
                painter.end()
            return
        if self._video_active:
            # libVLC owns the surface; painting would flicker over the video.
            return
        painter = QPainter(self)
        try:
            painter.fillRect(self.rect(), QColor("#000000"))
            painter.setPen(QColor("#2a2a32"))
            font = painter.font()
            font.setPointSize(max(16, min(46, self.width() // 16)))
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(self.rect(), Qt.AlignCenter, self._placeholder)
        finally:
            painter.end()

    # ------------------------------------------------------------------
    # Input
    # ------------------------------------------------------------------
    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.button() == Qt.LeftButton:
            self.doubleClicked.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt naming
        delta = event.angleDelta().y()
        if delta:
            self.wheelScrolled.emit(1 if delta > 0 else -1)
            event.accept()
            return
        super().wheelEvent(event)


class QtVideoSurfaceSink(QObject):
    """VideoSink implementation that paints decoded CASUNAT2 frames into Qt.

    Implements the ``mpcasu_native_backend.VideoSink`` protocol. Decoding runs
    on a worker thread, so every presentation is marshalled onto the Qt event
    loop through queued signals; QImage objects are built in the worker thread
    (QPixmap construction is GUI-thread-only) and converted on delivery.
    """

    _frameReady = Signal(object)
    _coverReady = Signal(object)
    _subtitleReady = Signal(object)
    _cleared = Signal()

    def __init__(self, surface: VideoSurface) -> None:
        super().__init__()
        self._surface = surface
        self._generation = 0
        self._frameReady.connect(self._apply_frame, Qt.QueuedConnection)
        self._coverReady.connect(self._apply_cover, Qt.QueuedConnection)
        self._subtitleReady.connect(self._apply_subtitle, Qt.QueuedConnection)
        self._cleared.connect(self._apply_clear, Qt.QueuedConnection)

    # --- VideoSink protocol -------------------------------------------

    def present(self, frame, pts_seconds: float) -> None:
        from mpcasu_native_backend import canonical_to_rgb
        rgb = canonical_to_rgb(frame)
        height, width, _ = rgb.shape
        stride = 3 * width
        image = QImage(rgb.tobytes(), width, height, stride,
                       QImage.Format_RGB888).copy()
        self._frameReady.emit((self._generation, image))

    def present_cover(self, data: bytes, media_type: str) -> None:
        image = QImage()
        if not image.loadFromData(data):
            return
        self._coverReady.emit((self._generation, image.copy()))

    def present_subtitle(self, text, pts_seconds: float) -> None:
        self._subtitleReady.emit((self._generation, str(text) if text else None))

    def present_subtitle_rgba(self, rgba: np.ndarray, pts_seconds: float) -> None:
        # Bitmap subtitles arrive as full-frame RGBA; render the text-free
        # overlay as a frame so bitmap content stays visible.
        if rgba.dtype != np.uint8 or rgba.ndim != 3 or rgba.shape[2] != 4:
            return
        height, width, _ = rgba.shape
        image = QImage(rgba.tobytes(), width, height, 4 * width,
                       QImage.Format_RGBA8888).copy()
        self._frameReady.emit((self._generation, image))

    def clear_subtitle(self) -> None:
        self._subtitleReady.emit((self._generation, None))

    def invalidate(self) -> None:
        self._generation += 1
        self._cleared.emit()

    def close(self) -> None:
        self.invalidate()

    # --- GUI-thread delivery ------------------------------------------

    def _apply_frame(self, payload) -> None:
        generation, image = payload
        if generation != self._generation:
            return
        self._surface.set_native_frame(QPixmap.fromImage(image))

    def _apply_cover(self, payload) -> None:
        generation, image = payload
        if generation != self._generation:
            return
        self._surface.set_native_frame(QPixmap.fromImage(image))

    def _apply_subtitle(self, payload) -> None:
        generation, text = payload
        if generation != self._generation:
            return
        self._surface.set_native_subtitle(text)

    def _apply_clear(self) -> None:
        self._surface.set_native_frame(None)
        self._surface.set_native_subtitle(None)
