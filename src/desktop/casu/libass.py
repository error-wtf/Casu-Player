"""Small bounded ctypes adapter for the stable libass rendering ABI."""
from __future__ import annotations

import ctypes

import numpy as np


class LibassError(RuntimeError):
    pass


class _AssImage(ctypes.Structure):
    pass


_AssImagePointer = ctypes.POINTER(_AssImage)
_AssImage._fields_ = [
    ("w", ctypes.c_int), ("h", ctypes.c_int), ("stride", ctypes.c_int),
    ("bitmap", ctypes.POINTER(ctypes.c_ubyte)), ("color", ctypes.c_uint32),
    ("dst_x", ctypes.c_int), ("dst_y", ctypes.c_int),
    ("next", _AssImagePointer), ("type", ctypes.c_int),
]


class LibassRenderer:
    """Render one bounded ASS/SSA document to transparent RGBA frames."""

    def __init__(self, document: bytes, width: int, height: int, *,
                 fonts: tuple[tuple[str, bytes], ...] = (),
                 max_document_bytes: int = 64 * 1024 * 1024,
                 max_frame_bytes: int = 256 * 1024 * 1024):
        raw = bytes(document)
        self.width, self.height = int(width), int(height)
        if (not raw or len(raw) > max_document_bytes or self.width <= 0
                or self.height <= 0 or self.width > 32768 or self.height > 32768
                or self.width * self.height * 4 > max_frame_bytes):
            raise LibassError("ASS document/frame exceeds resource limits")
        try:
            self.lib = ctypes.CDLL("libass.so.9")
        except OSError as exc:
            raise LibassError("libass runtime is unavailable") from exc
        self._declare()
        self.library = self.lib.ass_library_init()
        if not self.library:
            raise LibassError("libass library initialization failed")
        total_fonts = 0
        for name, data in fonts:
            font = bytes(data)
            total_fonts += len(font)
            encoded_name = str(name).encode("utf-8")
            if (not encoded_name or len(encoded_name) > 255 or not font
                    or len(font) > 64 * 1024 * 1024
                    or total_fonts > 128 * 1024 * 1024):
                self.lib.ass_library_done(self.library)
                self.library = None
                raise LibassError("ASS fonts exceed resource limits")
            buffer = ctypes.create_string_buffer(font)
            self.lib.ass_add_font(self.library, encoded_name, buffer, len(font))
        self.renderer = self.lib.ass_renderer_init(self.library)
        if not self.renderer:
            self.lib.ass_library_done(self.library)
            self.library = None
            raise LibassError("libass renderer initialization failed")
        self.track = None
        self.lib.ass_set_frame_size(self.renderer, self.width, self.height)
        self.lib.ass_set_storage_size(self.renderer, self.width, self.height)
        self.lib.ass_set_cache_limits(self.renderer, 10_000, 64)
        self.lib.ass_set_fonts(self.renderer, None, b"sans-serif", 1, None, 1)
        self._document = ctypes.create_string_buffer(raw)
        self.track = self.lib.ass_read_memory(
            self.library, self._document, len(raw), b"UTF-8")
        if not self.track:
            self.close()
            raise LibassError("libass rejected subtitle document")

    def _declare(self) -> None:
        lib = self.lib
        lib.ass_library_init.restype = ctypes.c_void_p
        lib.ass_library_done.argtypes = [ctypes.c_void_p]
        lib.ass_add_font.argtypes = [ctypes.c_void_p, ctypes.c_char_p,
                                     ctypes.c_void_p, ctypes.c_int]
        lib.ass_renderer_init.argtypes = [ctypes.c_void_p]
        lib.ass_renderer_init.restype = ctypes.c_void_p
        lib.ass_renderer_done.argtypes = [ctypes.c_void_p]
        lib.ass_set_frame_size.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
        lib.ass_set_storage_size.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
        lib.ass_set_cache_limits.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
        lib.ass_set_fonts.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p,
                                      ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
        lib.ass_read_memory.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                        ctypes.c_size_t, ctypes.c_char_p]
        lib.ass_read_memory.restype = ctypes.c_void_p
        lib.ass_free_track.argtypes = [ctypes.c_void_p]
        lib.ass_render_frame.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                         ctypes.c_longlong, ctypes.POINTER(ctypes.c_int)]
        lib.ass_render_frame.restype = _AssImagePointer

    def render(self, milliseconds: int) -> np.ndarray:
        if not self.track or not self.renderer:
            raise LibassError("libass renderer is closed")
        changed = ctypes.c_int()
        image = self.lib.ass_render_frame(self.renderer, self.track,
                                          int(milliseconds), ctypes.byref(changed))
        rgba = np.zeros((self.height, self.width, 4), dtype=np.uint8)
        count = 0
        while image:
            count += 1
            if count > 100_000:
                raise LibassError("libass returned excessive image fragments")
            value = image.contents
            if value.w > 0 and value.h > 0:
                self._blend(rgba, value)
            image = value.next
        return rgba

    def _blend(self, target: np.ndarray, image: _AssImage) -> None:
        if image.stride < image.w or not image.bitmap:
            raise LibassError("libass returned invalid image geometry")
        left, top = max(0, image.dst_x), max(0, image.dst_y)
        right = min(self.width, image.dst_x + image.w)
        bottom = min(self.height, image.dst_y + image.h)
        if right <= left or bottom <= top:
            return
        source_x, source_y = left - image.dst_x, top - image.dst_y
        raw_size = image.stride * (image.h - 1) + image.w
        bitmap = np.ctypeslib.as_array(image.bitmap, shape=(raw_size,))
        rows = np.empty((bottom - top, right - left), dtype=np.uint8)
        for row in range(bottom - top):
            start = (source_y + row) * image.stride + source_x
            rows[row] = bitmap[start:start + right - left]
        color = int(image.color)
        rgb = np.array([(color >> 24) & 255, (color >> 16) & 255,
                        (color >> 8) & 255], dtype=np.uint32)
        source_alpha = (rows.astype(np.uint32) * (255 - (color & 255)) + 127) // 255
        destination = target[top:bottom, left:right]
        destination_alpha = destination[..., 3].astype(np.uint32)
        inverse = 255 - source_alpha
        output_alpha = source_alpha + (destination_alpha * inverse + 127) // 255
        destination_premultiplied = (destination[..., :3].astype(np.uint32)
                                     * destination_alpha[..., None])
        output_premultiplied = (rgb * source_alpha[..., None]
                                + (destination_premultiplied * inverse[..., None] + 127) // 255)
        nonzero = output_alpha > 0
        destination[..., :3] = 0
        destination[..., :3][nonzero] = np.clip(
            (output_premultiplied[nonzero] + output_alpha[nonzero, None] // 2)
            // output_alpha[nonzero, None], 0, 255).astype(np.uint8)
        destination[..., 3] = output_alpha.astype(np.uint8)

    def close(self) -> None:
        track = getattr(self, "track", None)
        if track:
            self.lib.ass_free_track(track)
            self.track = None
        renderer = getattr(self, "renderer", None)
        if renderer:
            self.lib.ass_renderer_done(renderer)
            self.renderer = None
        library = getattr(self, "library", None)
        if library:
            self.lib.ass_library_done(library)
            self.library = None

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()
