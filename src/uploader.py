"""
uploader.py - Engine upload YouTube dengan resumable upload, retry,
dan exponential backoff. TIDAK melakukan upload nyata tanpa izin eksplisit.
"""

import os
import time
import logging
import threading
from pathlib import Path
from typing import Optional, Callable, Dict, Any

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
#  Import opsional
# ------------------------------------------------------------------ #
try:
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload
    GOOGLE_LIBS_AVAILABLE = True
except ImportError:
    GOOGLE_LIBS_AVAILABLE = False
    # Buat stub agar kode bisa dijalankan tanpa library
    class HttpError(Exception):
        def __init__(self, resp=None, content=b"", uri=None):
            self.resp = resp or {}
            self.status = getattr(resp, "status", 0) if resp else 0
            self.content = content
            super().__init__(f"HttpError {self.status}: {content}")

    class MediaFileUpload:
        pass

# ------------------------------------------------------------------ #
#  Konstanta
# ------------------------------------------------------------------ #
RETRIABLE_EXCEPTIONS = (IOError, OSError)
RETRIABLE_STATUS_CODES = {500, 502, 503, 504}
# Error 403 dengan reason tertentu bisa di-retry
QUOTA_ERROR_REASONS = {"quotaExceeded", "userRateLimitExceeded"}

MAX_RETRIES = 10
CHUNK_SIZE = 256 * 1024 * 10  # 2.5 MB per chunk


class UploadError(Exception):
    """Error upload yang tidak bisa di-retry."""
    pass


class UncertainUploadError(Exception):
    """Upload mungkin berhasil tapi tidak ada konfirmasi."""
    pass


class QuotaExceededError(Exception):
    """Kuota API YouTube habis untuk hari ini."""
    pass


class UploadCancelled(Exception):
    """Upload dibatalkan oleh pengguna."""
    pass


def _exponential_backoff(attempt: int, base_delay: float = 2.0, max_delay: float = 300.0) -> float:
    """Hitung waktu tunggu exponential backoff."""
    delay = min(base_delay ** attempt, max_delay)
    # Tambah jitter agar tidak semua request retry bersamaan
    import random
    jitter = random.uniform(0, delay * 0.2)
    return delay + jitter


class YouTubeUploader:
    """
    Mengelola upload video ke YouTube satu per satu secara berurutan.
    Mendukung resumable upload dan retry dengan exponential backoff.
    """

    def __init__(
        self,
        youtube_service,
        progress_callback: Optional[Callable] = None,
        status_callback: Optional[Callable] = None,
        cancel_event: Optional[threading.Event] = None,
    ):
        """
        Args:
            youtube_service: YouTube API service dari auth.build_youtube_service().
            progress_callback: Dipanggil dengan (upload_id, bytes_sent, total_bytes, percent).
            status_callback: Dipanggil dengan (upload_id, message).
            cancel_event: threading.Event yang di-set untuk membatalkan upload.
        """
        self.service = youtube_service
        self.progress_callback = progress_callback
        self.status_callback = status_callback
        self.cancel_event = cancel_event or threading.Event()

    def _notify(self, upload_id: int, message: str):
        """Kirim notifikasi status."""
        logger.info("[Upload %d] %s", upload_id, message)
        if self.status_callback:
            self.status_callback(upload_id, message)

    def _notify_progress(
        self, upload_id: int, bytes_sent: int, total_bytes: int
    ):
        """Kirim notifikasi progres."""
        percent = (bytes_sent / total_bytes * 100) if total_bytes > 0 else 0
        if self.progress_callback:
            self.progress_callback(upload_id, bytes_sent, total_bytes, percent)

    def _check_cancelled(self):
        """Lempar UploadCancelled jika pengguna membatalkan."""
        if self.cancel_event.is_set():
            raise UploadCancelled("Upload dibatalkan oleh pengguna.")

    def upload_video(
        self,
        upload_id: int,
        metadata: Dict[str, Any],
        resume_uri: Optional[str] = None,
    ) -> str:
        """
        Upload satu video ke YouTube.

        Args:
            upload_id: ID dari database untuk tracking.
            metadata: Dict berisi filepath, title, description, privacy, dll.
            resume_uri: URI untuk melanjutkan upload yang terputus.

        Returns:
            YouTube video ID jika berhasil.

        Raises:
            UploadError: Error permanen.
            UncertainUploadError: Upload mungkin berhasil tapi tidak ada konfirmasi.
            QuotaExceededError: Kuota API habis.
            UploadCancelled: Dibatalkan pengguna.
        """
        if not GOOGLE_LIBS_AVAILABLE:
            raise UploadError(
                "Library Google API belum terinstall. "
                "Jalankan: pip install -r requirements.txt"
            )

        filepath = metadata["filepath"]
        if not Path(filepath).exists():
            raise UploadError(f"File tidak ditemukan: {filepath}")

        file_size = Path(filepath).stat().st_size
        self._notify(upload_id, f"Mempersiapkan upload: {metadata['filename']}")

        # Susun body request YouTube
        body = self._build_request_body(metadata)

        # Buat media upload object
        media = MediaFileUpload(
            filepath,
            mimetype="video/mp4",
            chunksize=CHUNK_SIZE,
            resumable=True,
        )

        # Buat insert request
        request = self.service.videos().insert(
            part=",".join(body.keys()),
            body=body,
            media_body=media,
        )

        # Jika ada resume_uri, set di request
        if resume_uri:
            request.resumable_uri = resume_uri
            self._notify(upload_id, "Melanjutkan upload yang terputus...")

        return self._execute_resumable_upload(
            upload_id, request, file_size, metadata["filename"]
        )

    def _build_request_body(self, metadata: Dict) -> Dict:
        """Susun body untuk YouTube videos.insert."""
        # Proses tags
        tags = []
        raw_tags = metadata.get("tags", "")
        if raw_tags:
            tags = [t.strip() for t in raw_tags.split(",") if t.strip()]

        # Validasi category_id
        try:
            cat_id = str(int(metadata.get("category_id", "27")))
        except (ValueError, TypeError):
            cat_id = "27"

        body = {
            "snippet": {
                "title": metadata.get("title", "")[:100],
                "description": metadata.get("description", "")[:5000],
                "tags": tags[:500],  # Maks 500 tag
                "categoryId": cat_id,
            },
            "status": {
                "privacyStatus": metadata.get("privacy", "private") or "private",
                "selfDeclaredMadeForKids": False,
            },
        }
        return body

    def _execute_resumable_upload(
        self,
        upload_id: int,
        request,
        file_size: int,
        filename: str,
    ) -> str:
        """
        Jalankan resumable upload dengan retry dan exponential backoff.

        Returns:
            YouTube video ID.
        """
        response = None
        error = None
        retry = 0

        while response is None:
            self._check_cancelled()

            try:
                self._notify(upload_id, f"Mengupload chunk... (percobaan {retry + 1})")
                status, response = request.next_chunk()

                if status:
                    bytes_sent = status.resumable_progress
                    self._notify_progress(upload_id, bytes_sent, file_size)
                    self._notify(
                        upload_id,
                        f"Progress: {bytes_sent / 1024 / 1024:.1f} MB / "
                        f"{file_size / 1024 / 1024:.1f} MB "
                        f"({bytes_sent / file_size * 100:.1f}%)",
                    )

                if response is not None:
                    # Upload selesai
                    video_id = response.get("id", "")
                    if not video_id:
                        raise UncertainUploadError(
                            "Upload selesai tapi YouTube tidak mengembalikan video ID."
                        )
                    self._notify_progress(upload_id, file_size, file_size)
                    self._notify(
                        upload_id,
                        f"Upload berhasil! YouTube Video ID: {video_id}",
                    )
                    return video_id

            except HttpError as e:
                if e.status in RETRIABLE_STATUS_CODES:
                    error = f"HTTP {e.status}: {e.content}"
                    logger.warning("[Upload %d] Error server: %s", upload_id, error)
                elif e.status == 403:
                    # Periksa apakah quota exceeded
                    try:
                        import json as _json
                        err_body = _json.loads(e.content)
                        reasons = [
                            d.get("reason", "")
                            for d in err_body.get("error", {}).get("errors", [])
                        ]
                        if any(r in QUOTA_ERROR_REASONS for r in reasons):
                            raise QuotaExceededError(
                                "Kuota YouTube API habis untuk hari ini. "
                                "Coba lagi besok (sekitar pukul 08:00 WIB / 00:00 UTC)."
                            ) from e
                    except (ValueError, KeyError):
                        pass
                    raise UploadError(
                        f"Akses ditolak (403). Periksa izin OAuth: {e.content}"
                    ) from e
                elif e.status == 400:
                    raise UploadError(
                        f"Request tidak valid (400). Periksa metadata: {e.content}"
                    ) from e
                elif e.status == 404:
                    # Upload session expired, harus mulai ulang
                    raise UploadError(
                        "Sesi upload expired (404). Upload akan dimulai ulang."
                    ) from e
                else:
                    raise UploadError(
                        f"HTTP error tidak bisa di-retry: {e.status} {e.content}"
                    ) from e

            except RETRIABLE_EXCEPTIONS as e:
                error = str(e)
                logger.warning("[Upload %d] Network error: %s", upload_id, error)

            except (UploadCancelled, QuotaExceededError, UploadError):
                raise

            except Exception as e:
                # Error tidak terduga — tandai sebagai uncertain
                raise UncertainUploadError(
                    f"Error tidak terduga selama upload: {e}"
                ) from e

            # Retry dengan backoff
            if error:
                retry += 1
                if retry > MAX_RETRIES:
                    raise UploadError(
                        f"Melebihi batas retry ({MAX_RETRIES}x). Error terakhir: {error}"
                    )
                wait_time = _exponential_backoff(retry)
                self._notify(
                    upload_id,
                    f"Menunggu {wait_time:.1f} detik sebelum retry ke-{retry}...",
                )
                # Tunggu dengan cek cancel setiap detik
                for _ in range(int(wait_time)):
                    self._check_cancelled()
                    time.sleep(1)
                time.sleep(wait_time % 1)
                error = None

        raise UncertainUploadError("Loop selesai tanpa response atau error yang jelas.")

    def add_to_playlist(self, video_id: str, playlist_id: str) -> bool:
        """
        Tambahkan video ke playlist.

        Args:
            video_id: YouTube video ID.
            playlist_id: YouTube playlist ID.

        Returns:
            True jika berhasil.
        """
        if not playlist_id or not playlist_id.strip():
            return False

        try:
            self.service.playlistItems().insert(
                part="snippet",
                body={
                    "snippet": {
                        "playlistId": playlist_id.strip(),
                        "resourceId": {
                            "kind": "youtube#video",
                            "videoId": video_id,
                        },
                    }
                },
            ).execute()
            logger.info("Video %s ditambahkan ke playlist %s", video_id, playlist_id)
            return True
        except HttpError as e:
            logger.warning(
                "Gagal menambahkan ke playlist %s: %s", playlist_id, e
            )
            return False


class BulkUploadManager:
    """
    Mengelola antrian upload massal secara berurutan.
    """

    def __init__(
        self,
        youtube_service,
        database,
        progress_callback: Optional[Callable] = None,
        status_callback: Optional[Callable] = None,
        queue_callback: Optional[Callable] = None,
        cancel_event: Optional[threading.Event] = None,
    ):
        """
        Args:
            youtube_service: YouTube API service.
            database: Instance UploadDatabase.
            progress_callback: Dipanggil dengan (upload_id, bytes, total, percent).
            status_callback: Dipanggil dengan (upload_id, message).
            queue_callback: Dipanggil saat item antrian berubah (upload_id, status).
            cancel_event: Event untuk membatalkan semua upload.
        """
        self.db = database
        self.cancel_event = cancel_event or threading.Event()
        self.uploader = YouTubeUploader(
            youtube_service=youtube_service,
            progress_callback=progress_callback,
            status_callback=status_callback,
            cancel_event=self.cancel_event,
        )
        self.queue_callback = queue_callback
        self._is_running = False

    @property
    def is_running(self) -> bool:
        return self._is_running

    def cancel(self):
        """Batalkan upload yang sedang berjalan."""
        self.cancel_event.set()
        logger.info("Pembatalan upload diminta.")

    def process_queue(
        self,
        entries: Optional[list] = None,
        override_privacy: Optional[str] = None,
        batch_size: Optional[int] = None,
    ) -> Dict:
        """
        Proses antrian upload dari database atau list entries baru.

        Args:
            entries: List metadata baru. Jika None, lanjutkan dari database.
            override_privacy: Jika diisi ("private", "unlisted", "public"),
                MENIMPA privacy di setiap entry sebelum dikirim ke YouTube API.
                Ini adalah sumber kebenaran tunggal — bukan nilai display GUI.
                None berarti pakai privacy yang ada di tiap entry metadata.
            batch_size: Jumlah maksimum video yang diproses dalam satu sesi.
                None atau 0 berarti proses semua.

        Returns:
            Dict berisi statistik: success, failed, uncertain, skipped, cancelled.
        """
        # Validasi override_privacy
        VALID_PRIVACY = {"private", "unlisted", "public"}
        if override_privacy is not None:
            override_privacy = override_privacy.strip().lower()
            if override_privacy not in VALID_PRIVACY:
                logger.warning(
                    "override_privacy '%s' tidak valid, diabaikan.", override_privacy
                )
                override_privacy = None

        self._is_running = True
        self.cancel_event.clear()
        stats = {
            "success": 0,
            "failed": 0,
            "uncertain": 0,
            "skipped": 0,
            "cancelled": 0,
        }

        try:
            # Tambahkan entries baru ke database jika ada
            if entries:
                for entry in entries:
                    upload_id, is_new = self.db.get_or_create_upload(entry)
                    if not is_new:
                        existing = self.db.find_by_filepath(entry["filepath"])
                        if existing and existing["status"] == "success":
                            stats["skipped"] += 1
                            logger.info(
                                "Dilewati (sudah sukses): %s", entry["filename"]
                            )
                            if self.queue_callback:
                                self.queue_callback(upload_id, "skipped")

            # Ambil semua pending dari database
            pending = self.db.get_pending_uploads()
            logger.info("Memulai proses %d upload pending...", len(pending))

            # Terapkan batch_size jika diset
            if batch_size and batch_size > 0:
                pending = pending[:batch_size]
                logger.info("Batch size %d diterapkan: memproses %d upload.", batch_size, len(pending))

            for row in pending:
                if self.cancel_event.is_set():
                    stats["cancelled"] += len(pending) - sum(stats.values())
                    break

                upload_id = row["id"]
                metadata = dict(row)
                resume_uri = row["resume_uri"]

                # Terapkan override_privacy — ini yang dikirim ke YouTube API
                if override_privacy is not None:
                    metadata["privacy"] = override_privacy
                    logger.debug(
                        "Privacy override '%s' diterapkan untuk upload ID %d",
                        override_privacy, upload_id,
                    )

                # Cek duplikat berdasarkan hash
                if metadata.get("file_hash") and self.db.find_by_hash(
                    metadata["file_hash"]
                ):
                    logger.info(
                        "Dilewati (duplikat hash): %s", metadata["filename"]
                    )
                    self.db.update_status(upload_id, "skipped")
                    stats["skipped"] += 1
                    if self.queue_callback:
                        self.queue_callback(upload_id, "skipped")
                    continue

                self.db.update_status(upload_id, "in_progress")
                if self.queue_callback:
                    self.queue_callback(upload_id, "in_progress")

                try:
                    video_id = self.uploader.upload_video(
                        upload_id=upload_id,
                        metadata=metadata,
                        resume_uri=resume_uri,
                    )
                    self.db.mark_success(upload_id, video_id)
                    stats["success"] += 1
                    if self.queue_callback:
                        self.queue_callback(upload_id, "success")

                    # Tambahkan ke playlist jika ada
                    playlist_id = metadata.get("playlist_id", "").strip()
                    if playlist_id and video_id:
                        self.uploader.add_to_playlist(video_id, playlist_id)

                except UploadCancelled:
                    # Simpan posisi untuk resume nanti
                    self.db.update_status(upload_id, "pending")
                    stats["cancelled"] += 1
                    if self.queue_callback:
                        self.queue_callback(upload_id, "pending")
                    break

                except QuotaExceededError as e:
                    self.db.mark_failed(upload_id, str(e))
                    stats["failed"] += 1
                    if self.queue_callback:
                        self.queue_callback(upload_id, "failed")
                    logger.error("Kuota habis, menghentikan semua upload: %s", e)
                    break

                except UncertainUploadError as e:
                    self.db.mark_uncertain(upload_id, str(e))
                    stats["uncertain"] += 1
                    if self.queue_callback:
                        self.queue_callback(upload_id, "uncertain")

                except UploadError as e:
                    self.db.mark_failed(upload_id, str(e))
                    self.db.increment_retry(upload_id)
                    stats["failed"] += 1
                    if self.queue_callback:
                        self.queue_callback(upload_id, "failed")

                except Exception as e:
                    logger.exception(
                        "Error tak terduga untuk upload %d: %s", upload_id, e
                    )
                    self.db.mark_uncertain(upload_id, str(e))
                    stats["uncertain"] += 1
                    if self.queue_callback:
                        self.queue_callback(upload_id, "uncertain")

        finally:
            self._is_running = False

        logger.info("Proses upload selesai. Statistik: %s", stats)
        return stats
