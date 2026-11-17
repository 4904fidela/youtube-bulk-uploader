"""
database.py - Modul SQLite untuk riwayat upload dan manajemen state.
Mencegah upload duplikat dan memungkinkan resume antrian yang terputus.
"""

import sqlite3
import logging
import threading
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Tuple

logger = logging.getLogger(__name__)

# Status yang mungkin untuk setiap upload
STATUS_PENDING = "pending"
STATUS_IN_PROGRESS = "in_progress"
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"
STATUS_UNCERTAIN = "uncertain"  # Upload mungkin berhasil tapi tidak ada konfirmasi
STATUS_SKIPPED = "skipped"

DB_VERSION = 2


class UploadDatabase:
    """
    Mengelola riwayat upload menggunakan SQLite.
    Thread-safe dengan connection per thread.
    """

    def __init__(self, db_path: str = "upload_history.db"):
        self.db_path = str(Path(db_path).resolve())
        self._local = threading.local()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        """Dapatkan koneksi SQLite untuk thread saat ini."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(
                self.db_path,
                detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
                timeout=30,
            )
            self._local.conn.row_factory = sqlite3.Row
            # Aktifkan WAL mode untuk performa lebih baik
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA foreign_keys=ON")
        return self._local.conn

    def _init_db(self):
        """Buat tabel dan indeks jika belum ada."""
        conn = self._get_conn()
        with conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS uploads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    filepath TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    file_hash TEXT,
                    title TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    privacy TEXT DEFAULT 'private',
                    playlist_id TEXT DEFAULT '',
                    tags TEXT DEFAULT '',
                    category_id TEXT DEFAULT '27',
                    youtube_video_id TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    resume_uri TEXT,
                    upload_offset INTEGER DEFAULT 0,
                    error_message TEXT,
                    retry_count INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT (datetime('now')),
                    updated_at TEXT DEFAULT (datetime('now')),
                    completed_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_uploads_filepath
                    ON uploads(filepath);
                CREATE INDEX IF NOT EXISTS idx_uploads_file_hash
                    ON uploads(file_hash);
                CREATE INDEX IF NOT EXISTS idx_uploads_status
                    ON uploads(status);
                CREATE INDEX IF NOT EXISTS idx_uploads_youtube_id
                    ON uploads(youtube_video_id);
            """)

            # Periksa dan set versi skema
            version = conn.execute("SELECT version FROM schema_version").fetchone()
            if version is None:
                conn.execute(
                    "INSERT INTO schema_version(version) VALUES (?)", (DB_VERSION,)
                )
            elif version["version"] < DB_VERSION:
                self._migrate(conn, version["version"])

        logger.info("Database diinisialisasi: %s", self.db_path)

    def _migrate(self, conn: sqlite3.Connection, from_version: int):
        """Migrasi skema database ke versi terbaru."""
        if from_version < 2:
            # Tambah kolom baru jika belum ada
            try:
                conn.execute(
                    "ALTER TABLE uploads ADD COLUMN upload_offset INTEGER DEFAULT 0"
                )
            except sqlite3.OperationalError:
                pass
            conn.execute(
                "UPDATE schema_version SET version = ?", (DB_VERSION,)
            )
        logger.info("Database dimigrasikan ke versi %d", DB_VERSION)

    # ------------------------------------------------------------------ #
    #  CREATE / ADD
    # ------------------------------------------------------------------ #

    def add_upload(self, metadata: Dict) -> int:
        """
        Tambahkan entri upload baru dengan status pending.

        Returns:
            ID baris yang dibuat.
        """
        conn = self._get_conn()
        with conn:
            cursor = conn.execute(
                """
                INSERT INTO uploads
                    (filepath, filename, file_hash, title, description,
                     privacy, playlist_id, tags, category_id, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    metadata.get("filepath", ""),
                    metadata.get("filename", ""),
                    metadata.get("file_hash", ""),
                    metadata.get("title", ""),
                    metadata.get("description", ""),
                    metadata.get("privacy", "private"),
                    metadata.get("playlist_id", ""),
                    metadata.get("tags", ""),
                    metadata.get("category_id", "27"),
                    STATUS_PENDING,
                ),
            )
            return cursor.lastrowid

    def get_or_create_upload(self, metadata: Dict) -> Tuple[int, bool]:
        """
        Dapatkan upload yang ada atau buat baru.

        Returns:
            (upload_id, is_new)
        """
        existing = self.find_by_filepath(metadata["filepath"])
        if existing:
            return existing["id"], False
        new_id = self.add_upload(metadata)
        return new_id, True

    # ------------------------------------------------------------------ #
    #  READ
    # ------------------------------------------------------------------ #

    def find_by_filepath(self, filepath: str) -> Optional[sqlite3.Row]:
        """Cari upload berdasarkan filepath."""
        conn = self._get_conn()
        return conn.execute(
            "SELECT * FROM uploads WHERE filepath = ? ORDER BY id DESC LIMIT 1",
            (filepath,),
        ).fetchone()

    def find_by_hash(self, file_hash: str) -> Optional[sqlite3.Row]:
        """Cari upload berdasarkan hash file (deteksi duplikat)."""
        if not file_hash:
            return None
        conn = self._get_conn()
        return conn.execute(
            """SELECT * FROM uploads
               WHERE file_hash = ? AND status = ?
               ORDER BY id DESC LIMIT 1""",
            (file_hash, STATUS_SUCCESS),
        ).fetchone()

    def get_pending_uploads(self) -> List[sqlite3.Row]:
        """Ambil semua upload yang belum selesai (pending atau in_progress)."""
        conn = self._get_conn()
        return conn.execute(
            """SELECT * FROM uploads
               WHERE status IN (?, ?)
               ORDER BY id ASC""",
            (STATUS_PENDING, STATUS_IN_PROGRESS),
        ).fetchall()

    def get_uncertain_uploads(self) -> List[sqlite3.Row]:
        """Ambil upload dengan status uncertain yang perlu dicek manual."""
        conn = self._get_conn()
        return conn.execute(
            "SELECT * FROM uploads WHERE status = ? ORDER BY id ASC",
            (STATUS_UNCERTAIN,),
        ).fetchall()

    def get_all_uploads(self) -> List[sqlite3.Row]:
        """Ambil semua riwayat upload."""
        conn = self._get_conn()
        return conn.execute(
            "SELECT * FROM uploads ORDER BY id DESC"
        ).fetchall()

    def get_summary(self) -> Dict:
        """Statistik ringkasan upload."""
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT status, COUNT(*) as count
               FROM uploads GROUP BY status"""
        ).fetchall()
        summary = {r["status"]: r["count"] for r in rows}
        summary["total"] = sum(summary.values())
        return summary

    # ------------------------------------------------------------------ #
    #  UPDATE
    # ------------------------------------------------------------------ #

    def update_status(
        self,
        upload_id: int,
        status: str,
        youtube_video_id: Optional[str] = None,
        error_message: Optional[str] = None,
        resume_uri: Optional[str] = None,
        upload_offset: Optional[int] = None,
    ):
        """Update status dan data upload."""
        conn = self._get_conn()
        completed_at = (
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if status in (STATUS_SUCCESS, STATUS_FAILED, STATUS_UNCERTAIN)
            else None
        )
        with conn:
            conn.execute(
                """UPDATE uploads SET
                    status = ?,
                    youtube_video_id = COALESCE(?, youtube_video_id),
                    error_message = COALESCE(?, error_message),
                    resume_uri = COALESCE(?, resume_uri),
                    upload_offset = COALESCE(?, upload_offset),
                    completed_at = COALESCE(?, completed_at),
                    updated_at = datetime('now')
                   WHERE id = ?""",
                (
                    status,
                    youtube_video_id,
                    error_message,
                    resume_uri,
                    upload_offset,
                    completed_at,
                    upload_id,
                ),
            )

    def increment_retry(self, upload_id: int):
        """Tambah counter percobaan ulang."""
        conn = self._get_conn()
        with conn:
            conn.execute(
                "UPDATE uploads SET retry_count = retry_count + 1, updated_at = datetime('now') WHERE id = ?",
                (upload_id,),
            )

    def save_resume_uri(self, upload_id: int, resume_uri: str, offset: int = 0):
        """Simpan URI untuk melanjutkan upload yang terputus."""
        conn = self._get_conn()
        with conn:
            conn.execute(
                """UPDATE uploads SET
                    resume_uri = ?,
                    upload_offset = ?,
                    status = ?,
                    updated_at = datetime('now')
                   WHERE id = ?""",
                (resume_uri, offset, STATUS_IN_PROGRESS, upload_id),
            )

    def mark_success(self, upload_id: int, youtube_video_id: str):
        """Tandai upload sebagai berhasil."""
        self.update_status(
            upload_id,
            STATUS_SUCCESS,
            youtube_video_id=youtube_video_id,
        )
        logger.info("Upload ID %d berhasil. YouTube ID: %s", upload_id, youtube_video_id)

    def mark_failed(self, upload_id: int, error: str):
        """Tandai upload sebagai gagal."""
        self.update_status(upload_id, STATUS_FAILED, error_message=error)
        logger.warning("Upload ID %d gagal: %s", upload_id, error)

    def mark_uncertain(self, upload_id: int, error: str):
        """
        Tandai upload sebagai uncertain — mungkin sudah terupload di YouTube
        tapi tidak ada konfirmasi. Harus dicek manual.
        """
        self.update_status(upload_id, STATUS_UNCERTAIN, error_message=error)
        logger.warning(
            "Upload ID %d status uncertain: %s. Periksa YouTube Studio.", upload_id, error
        )

    def reset_to_pending(self, upload_id: int):
        """Reset upload ke pending untuk diulang."""
        conn = self._get_conn()
        with conn:
            conn.execute(
                """UPDATE uploads SET
                    status = ?,
                    resume_uri = NULL,
                    upload_offset = 0,
                    error_message = NULL,
                    updated_at = datetime('now')
                   WHERE id = ?""",
                (STATUS_PENDING, upload_id),
            )

    # ------------------------------------------------------------------ #
    #  DUPLICATE DETECTION
    # ------------------------------------------------------------------ #

    def is_duplicate(self, filepath: str, file_hash: str = "") -> bool:
        """
        Periksa apakah file sudah pernah berhasil diupload.
        Menggunakan filepath sebagai pengecekan utama, hash sebagai tambahan.
        """
        # Cek berdasarkan filepath
        existing = self.find_by_filepath(filepath)
        if existing and existing["status"] == STATUS_SUCCESS:
            logger.info("Duplikat terdeteksi (filepath): %s", filepath)
            return True

        # Cek berdasarkan hash jika tersedia
        if file_hash:
            hash_match = self.find_by_hash(file_hash)
            if hash_match:
                logger.info("Duplikat terdeteksi (hash): %s", filepath)
                return True

        return False

    def close(self):
        """Tutup koneksi database untuk thread saat ini."""
        if hasattr(self._local, "conn") and self._local.conn:
            self._local.conn.close()
            self._local.conn = None
