"""Small transactional SQLite media library shared by player front ends."""
from __future__ import annotations

import json
import math
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .playlist import PlaylistError, PlaylistModel
from .tags import metadata_for


MAX_LIBRARY_METADATA_BYTES = 1024 * 1024
MAX_LIBRARY_SCAN_FILES = 100_000
MAX_PLAYLIST_NAME_BYTES = 255

# Only these suffixes are indexed (skipping them keeps the scan fast and
# avoids probing every non-media file in a watched folder).
MEDIA_EXTENSIONS = frozenset({
    ".mp3", ".mp4", ".m4a", ".m4v", ".mov", ".mkv", ".webm", ".flac",
    ".wav", ".ogg", ".opus", ".aac", ".aiff", ".alac", ".wma", ".mpg",
    ".mpeg", ".ts", ".m2ts", ".avi", ".casu", ".mp5",
})


@dataclass(frozen=True)
class LibraryItem:
    path: Path
    size_bytes: int
    modified_ns: int
    favorite: bool
    resume_seconds: float
    duration_seconds: float | None
    metadata: dict


@dataclass(frozen=True)
class PlaybackPreferences:
    audio_track: int | None = None
    video_track: int | None = None
    subtitle_track: int | None = None
    audio_delay_ms: float = 0.0
    subtitle_delay_ms: float = 0.0


@dataclass(frozen=True)
class MediaBookmark:
    identifier: int
    path: Path
    position_seconds: float
    label: str


class _LockedConnection:
    """Proxy that serializes every statement on the shared sqlite connection."""

    def __init__(self, connection, lock):
        self._connection = connection
        self._lock = lock

    def __getattr__(self, name):
        attribute = getattr(self._connection, name)
        if callable(attribute):
            def locked(*args, **kwargs):
                with self._lock:
                    return attribute(*args, **kwargs)
            return locked
        return attribute

    def __enter__(self):
        self._lock.acquire()
        try:
            return self._connection.__enter__()
        except BaseException:
            self._lock.release()
            raise

    def __exit__(self, exc_type, exc, tb):
        try:
            return self._connection.__exit__(exc_type, exc, tb)
        finally:
            self._lock.release()

    # Direct attributes used heavily in hot paths (rows, lastrowid)
    @property
    def row_factory(self):
        return self._connection.row_factory

    @row_factory.setter
    def row_factory(self, value):
        self._connection.row_factory = value

    @property
    def total_changes(self):
        return self._connection.total_changes



class MediaLibrary:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # v7.8: the UI thread and the library scan thread share one connection
        # (check_same_thread=False); serialize every access through this lock
        # so sqlite never sees interleaved statements from both threads.
        self._connection_lock = threading.RLock()
        raw_connection = sqlite3.connect(self.path, check_same_thread=False)
        raw_connection.row_factory = sqlite3.Row
        raw_connection.execute("PRAGMA foreign_keys=ON")
        raw_connection.execute("PRAGMA journal_mode=WAL")
        self.connection = _LockedConnection(raw_connection, self._connection_lock)
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS media (
                path TEXT PRIMARY KEY,
                size_bytes INTEGER NOT NULL,
                modified_ns INTEGER NOT NULL,
                favorite INTEGER NOT NULL DEFAULT 0,
                resume_seconds REAL NOT NULL DEFAULT 0,
                duration_seconds REAL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                audio_track INTEGER,
                video_track INTEGER,
                subtitle_track INTEGER,
                audio_delay_ms REAL NOT NULL DEFAULT 0,
                subtitle_delay_ms REAL NOT NULL DEFAULT 0,
                last_seen_ns INTEGER NOT NULL,
                last_played_ns INTEGER
            );
            CREATE TABLE IF NOT EXISTS playlists (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL UNIQUE
            );
            CREATE TABLE IF NOT EXISTS playlist_items (
                playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
                position INTEGER NOT NULL,
                media_path TEXT NOT NULL,
                PRIMARY KEY (playlist_id, position)
            );
            CREATE TABLE IF NOT EXISTS bookmarks (
                id INTEGER PRIMARY KEY,
                media_path TEXT NOT NULL,
                position_seconds REAL NOT NULL,
                label TEXT NOT NULL,
                created_ns INTEGER NOT NULL,
                UNIQUE(media_path, position_seconds)
            );
        """)
        existing = {row[1] for row in self.connection.execute("PRAGMA table_info(media)")}
        migrations = {
            "audio_track": "INTEGER",
            "video_track": "INTEGER",
            "subtitle_track": "INTEGER",
            "audio_delay_ms": "REAL NOT NULL DEFAULT 0",
            "subtitle_delay_ms": "REAL NOT NULL DEFAULT 0",
            "search_title": "TEXT NOT NULL DEFAULT ''",
            "search_artist": "TEXT NOT NULL DEFAULT ''",
            "search_album": "TEXT NOT NULL DEFAULT ''",
        }
        for column, declaration in migrations.items():
            if column not in existing:
                self.connection.execute(
                    f"ALTER TABLE media ADD COLUMN {column} {declaration}"
                )
        # v7.8: fast search columns — backfill from stored metadata, then
        # keep them indexed. Falls back gracefully when json_extract is
        # unavailable on ancient sqlite builds.
        for field, column in (("title", "search_title"),
                              ("artist", "search_artist"),
                              ("album", "search_album")):
            try:
                self.connection.execute(
                    f"UPDATE media SET {column}=LOWER(json_extract(metadata_json,'$.{field}')) "
                    f"WHERE {column}='' AND json_extract(metadata_json,'$.{field}') IS NOT NULL"
                )
            except sqlite3.OperationalError:
                pass
        try:
            self.connection.execute(
                "CREATE INDEX IF NOT EXISTS media_search_title ON media(search_title)")
            self.connection.execute(
                "CREATE INDEX IF NOT EXISTS media_search_artist ON media(search_artist)")
            self.connection.execute(
                "CREATE INDEX IF NOT EXISTS media_search_album ON media(search_album)")
        except sqlite3.OperationalError:
            pass
        self.connection.commit()

    def upsert(self, path: str | Path, *, duration_seconds: float | None = None,
               metadata: dict | None = None) -> LibraryItem:
        item = self._upsert(path, duration_seconds=duration_seconds,
                            metadata=metadata)
        self.connection.commit()
        return item

    def _upsert(self, path: str | Path, *, duration_seconds: float | None = None,
                metadata: dict | None = None) -> LibraryItem:
        source = Path(path).expanduser().resolve()
        if metadata is None:
            existing = self.get(source)
            metadata = (existing.metadata if existing is not None and existing.metadata
                        else metadata_for(source))
        stat = source.stat()
        now = time.time_ns()
        try:
            values = json.dumps(metadata or {}, sort_keys=True, separators=(",", ":"),
                                ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("media metadata must be finite JSON") from exc
        if len(values.encode("utf-8")) > MAX_LIBRARY_METADATA_BYTES:
            raise ValueError("media metadata exceeds its 1 MiB safety limit")
        meta = metadata or {}
        st_title = str(meta.get("title") or "").strip().casefold()
        st_artist = str(meta.get("artist") or "").strip().casefold()
        st_album = str(meta.get("album") or "").strip().casefold()
        self.connection.execute("""
            INSERT INTO media(path,size_bytes,modified_ns,duration_seconds,metadata_json,last_seen_ns,
                              search_title,search_artist,search_album)
            VALUES(?,?,?,?,?,?,?,?,?)
            ON CONFLICT(path) DO UPDATE SET
              size_bytes=excluded.size_bytes, modified_ns=excluded.modified_ns,
              duration_seconds=COALESCE(excluded.duration_seconds,media.duration_seconds),
              metadata_json=CASE WHEN excluded.metadata_json='{}' THEN media.metadata_json ELSE excluded.metadata_json END,
              last_seen_ns=excluded.last_seen_ns,
              search_title=CASE WHEN excluded.search_title!='' THEN excluded.search_title ELSE media.search_title END,
              search_artist=CASE WHEN excluded.search_artist!='' THEN excluded.search_artist ELSE media.search_artist END,
              search_album=CASE WHEN excluded.search_album!='' THEN excluded.search_album ELSE media.search_album END
        """, (str(source), stat.st_size, stat.st_mtime_ns, duration_seconds, values, now,
              st_title, st_artist, st_album))
        item = self.get(source)
        assert item is not None
        return item

    def upsert_many(self, paths: Iterable[str | Path]) -> tuple[LibraryItem, ...]:
        """Index a bounded batch with one durable SQLite transaction."""
        items: list[LibraryItem] = []
        try:
            for index, path in enumerate(paths):
                if index >= MAX_LIBRARY_SCAN_FILES:
                    break
                items.append(self._upsert(path))
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return tuple(items)

    def get(self, path: str | Path) -> LibraryItem | None:
        source = str(Path(path).expanduser().resolve())
        row = self.connection.execute("SELECT * FROM media WHERE path=?", (source,)).fetchone()
        return self._item(row) if row else None

    def items(self, *, favorites_only: bool = False) -> tuple[LibraryItem, ...]:
        query = "SELECT * FROM media" + (" WHERE favorite=1" if favorites_only else "") + " ORDER BY path"
        return tuple(self._item(row) for row in self.connection.execute(query))

    def search(self, query: str = "", *, favorites_only: bool = False,
               limit: int = 500) -> tuple[LibraryItem, ...]:
        """Search persistent paths/metadata with a hard result bound."""
        maximum = max(1, min(5000, int(limit)))
        needle = str(query).strip().casefold()
        clauses: list[str] = []
        parameters: list[object] = []
        if favorites_only:
            clauses.append("favorite=1")
        if needle:
            # v7.8: indexed search columns (title/artist/album) instead of a
            # full-table scan over the metadata JSON blob; path stays as a
            # LIKE for filename matching.
            clauses.append(
                "(LOWER(path) LIKE ? ESCAPE '\\'"
                " OR search_title LIKE ? ESCAPE '\\'"
                " OR search_artist LIKE ? ESCAPE '\\'"
                " OR search_album LIKE ? ESCAPE '\\')")
            escaped = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            parameters.extend((f"%{escaped}%",) * 4)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        parameters.append(maximum)
        rows = self.connection.execute(
            f"SELECT * FROM media{where} ORDER BY COALESCE(last_played_ns,0) DESC,path LIMIT ?",
            parameters,
        )
        return tuple(self._item(row) for row in rows)

    def field_values(self, key: str) -> tuple[str, ...]:
        """Distinct non-empty metadata values for *key* (artist/album/genre…)."""
        values: set[str] = set()
        for item in self.items():
            value = str((item.metadata or {}).get(key) or "").strip()
            if value:
                values.add(value)
        return tuple(sorted(values, key=str.casefold))

    def by_field(self, key: str, value: str) -> tuple[LibraryItem, ...]:
        """Items whose metadata *key* equals *value* (case-insensitive)."""
        needle = str(value or "").strip().casefold()
        if not needle:
            return ()
        matches = []
        for item in self.items():
            if str((item.metadata or {}).get(key) or "").strip().casefold() == needle:
                matches.append(item)
        return tuple(matches)

    def scan(self, roots: Iterable[str | Path]) -> tuple[LibraryItem, ...]:
        """Index all media files under *roots*, probing tags in parallel.

        Subfolders are recursed into; only known media suffixes are indexed;
        files that already carry metadata in the database are not re-probed.
        """
        import concurrent.futures

        candidates: list[Path] = []
        for root in roots:
            base = Path(root).expanduser().resolve()
            sources = base.rglob("*") if base.is_dir() else (base,)
            for candidate in sources:
                if not candidate.is_file():
                    continue
                candidate = candidate.resolve()
                if candidate == self.path or candidate.name in {
                    f"{self.path.name}-wal", f"{self.path.name}-shm"
                }:
                    continue
                if candidate.suffix.lower() not in MEDIA_EXTENSIONS:
                    continue
                candidates.append(candidate)

        # Re-probe all files so embedded tags (ID3/Vorbis/MP4) always replace
        # stale filename-derived metadata.  ffprobe is fast enough for this.
        to_probe: list[Path] = []
        metas: dict[Path, dict] = {}
        for candidate in candidates:
            to_probe.append(candidate)

        def probe(path: Path):
            try:
                return metadata_for(path)
            except Exception:  # noqa: BLE001 - metadata is best effort
                return {}

        if to_probe:
            with concurrent.futures.ThreadPoolExecutor(
                    max_workers=min(8, max(1, len(to_probe)))) as executor:
                for candidate, meta in zip(to_probe,
                                           executor.map(probe, to_probe)):
                    metas[candidate] = meta or {}

        found: list[LibraryItem] = []
        for candidate in candidates:
            try:
                found.append(self._upsert(candidate,
                                          metadata=metas.get(candidate) or {}))
                if len(found) >= MAX_LIBRARY_SCAN_FILES:
                    break
            except OSError:
                continue
        self.connection.commit()
        return tuple(found)

    def record_progress(self, path: str | Path, seconds: float,
                        duration_seconds: float | None = None) -> None:
        source = Path(path).expanduser().resolve()
        if self.get(source) is None:
            self.upsert(source, duration_seconds=duration_seconds)
        position = float(seconds)
        if not math.isfinite(position):
            raise ValueError("playback position must be finite")
        position = max(0.0, position)
        if duration_seconds and position >= max(0.0, duration_seconds - 5.0):
            position = 0.0
        self.connection.execute(
            "UPDATE media SET resume_seconds=?,duration_seconds=COALESCE(?,duration_seconds),last_played_ns=? WHERE path=?",
            (position, duration_seconds, time.time_ns(), str(source)))
        self.connection.commit()

    def set_favorite(self, path: str | Path, favorite: bool) -> None:
        source = Path(path).expanduser().resolve()
        if self.get(source) is None:
            self.upsert(source)
        self.connection.execute("UPDATE media SET favorite=? WHERE path=?",
                                (int(bool(favorite)), str(source)))
        self.connection.commit()

    def playback_preferences(self, path: str | Path) -> PlaybackPreferences:
        source = str(Path(path).expanduser().resolve())
        row = self.connection.execute(
            "SELECT audio_track,video_track,subtitle_track,audio_delay_ms,subtitle_delay_ms "
            "FROM media WHERE path=?", (source,)).fetchone()
        if row is None:
            return PlaybackPreferences()
        return PlaybackPreferences(
            int(row["audio_track"]) if row["audio_track"] is not None else None,
            int(row["video_track"]) if row["video_track"] is not None else None,
            int(row["subtitle_track"]) if row["subtitle_track"] is not None else None,
            float(row["audio_delay_ms"]), float(row["subtitle_delay_ms"]),
        )

    def set_playback_preferences(self, path: str | Path,
                                 preferences: PlaybackPreferences) -> None:
        source = Path(path).expanduser().resolve()
        if self.get(source) is None:
            self.upsert(source)
        audio_delay = max(-5000.0, min(5000.0, float(preferences.audio_delay_ms)))
        subtitle_delay = max(-5000.0, min(5000.0, float(preferences.subtitle_delay_ms)))
        self.connection.execute("""
            UPDATE media SET audio_track=?,video_track=?,subtitle_track=?,
              audio_delay_ms=?,subtitle_delay_ms=? WHERE path=?
        """, (preferences.audio_track, preferences.video_track,
              preferences.subtitle_track, audio_delay, subtitle_delay,
              str(source)))
        self.connection.commit()

    def save_playlist(self, name: str, paths: Iterable[str | Path]) -> None:
        title = str(name).strip()
        if not title:
            raise ValueError("playlist name cannot be empty")
        if "\0" in title or len(title.encode("utf-8")) > MAX_PLAYLIST_NAME_BYTES:
            raise ValueError("playlist name must be at most 255 UTF-8 bytes without NUL")
        try:
            items = PlaylistModel(paths).items
        except PlaylistError as exc:
            raise ValueError(str(exc)) from exc
        with self.connection:
            self.connection.execute("INSERT INTO playlists(name) VALUES(?) ON CONFLICT(name) DO NOTHING", (title,))
            playlist_id = self.connection.execute("SELECT id FROM playlists WHERE name=?", (title,)).fetchone()[0]
            self.connection.execute("DELETE FROM playlist_items WHERE playlist_id=?", (playlist_id,))
            self.connection.executemany(
                "INSERT INTO playlist_items(playlist_id,position,media_path) VALUES(?,?,?)",
                ((playlist_id, index, str(path)) for index, path in enumerate(items)))

    def load_playlist(self, name: str) -> tuple[Path, ...]:
        rows = self.connection.execute("""
            SELECT i.media_path FROM playlist_items i
            JOIN playlists p ON p.id=i.playlist_id WHERE p.name=? ORDER BY i.position
        """, (name,))
        return tuple(Path(row[0]) for row in rows)

    def add_bookmark(self, path: str | Path, position_seconds: float,
                     label: str = "") -> MediaBookmark:
        source = Path(path).expanduser().resolve()
        position = float(position_seconds)
        if not math.isfinite(position) or position < 0:
            raise ValueError("bookmark position must be finite and non-negative")
        title = str(label).strip() or f"{position:.1f} s"
        if "\0" in title or len(title.encode("utf-8")) > 255:
            raise ValueError("bookmark label must be at most 255 UTF-8 bytes without NUL")
        with self.connection:
            self.connection.execute(
                "INSERT INTO bookmarks(media_path,position_seconds,label,created_ns) "
                "VALUES(?,?,?,?) ON CONFLICT(media_path,position_seconds) "
                "DO UPDATE SET label=excluded.label",
                (str(source), position, title, time.time_ns()))
        row = self.connection.execute(
            "SELECT id,media_path,position_seconds,label FROM bookmarks "
            "WHERE media_path=? AND position_seconds=?", (str(source), position)).fetchone()
        return MediaBookmark(int(row["id"]), Path(row["media_path"]),
                             float(row["position_seconds"]), str(row["label"]))

    def bookmarks(self, path: str | Path, *, limit: int = 500) -> tuple[MediaBookmark, ...]:
        source = str(Path(path).expanduser().resolve())
        maximum = max(1, min(5000, int(limit)))
        rows = self.connection.execute(
            "SELECT id,media_path,position_seconds,label FROM bookmarks "
            "WHERE media_path=? ORDER BY position_seconds,id LIMIT ?",
            (source, maximum))
        return tuple(MediaBookmark(int(row["id"]), Path(row["media_path"]),
                                   float(row["position_seconds"]), str(row["label"]))
                     for row in rows)

    def remove_bookmark(self, identifier: int) -> None:
        with self.connection:
            self.connection.execute("DELETE FROM bookmarks WHERE id=?",
                                    (int(identifier),))

    @staticmethod
    def _item(row: sqlite3.Row) -> LibraryItem:
        return LibraryItem(Path(row["path"]), int(row["size_bytes"]), int(row["modified_ns"]),
                           bool(row["favorite"]), float(row["resume_seconds"]),
                           float(row["duration_seconds"]) if row["duration_seconds"] is not None else None,
                           json.loads(row["metadata_json"]))

    def close(self) -> None:
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()
