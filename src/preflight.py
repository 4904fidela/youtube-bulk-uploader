"""
preflight.py - Pemeriksaan pra-upload untuk fitur "Uji Upload 1 Video".

Semua pemeriksaan dilakukan sebelum upload nyata dimulai:
  1. Validasi file: keberadaan, bukan zero-byte, bisa dibaca
  2. Autentikasi: token OAuth valid dan bisa di-refresh
  3. Identitas channel: ambil nama dan ID channel yang terhubung
  4. Deteksi duplikat: cek filepath dan SHA-256 hash di database
  5. Paksa privasi PRIVATE (tidak bisa diubah dari sini)

Tidak ada upload nyata yang dilakukan di modul ini.
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Dict

# Import compute_file_hash dan get_file_hash_cached di level modul agar bisa di-patch dalam tests
from src.scanner import compute_file_hash, get_file_hash_cached

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ #
#  Data classes
# ------------------------------------------------------------------ #

@dataclass
class PreflightResult:
    """
    Hasil lengkap semua pemeriksaan pra-upload.

    Attributes:
        ok: True hanya jika SEMUA pemeriksaan wajib lulus.
        errors: List pesan error yang memblokir upload.
        warnings: List pesan peringatan (tidak memblokir upload).
        filename: Nama file yang diperiksa.
        filepath: Path lengkap file.
        title: Judul video dari metadata.
        file_size_bytes: Ukuran file dalam bytes.
        file_size_mb: Ukuran file dalam MB.
        privacy: Privasi yang akan digunakan (selalu "private").
        is_duplicate: True jika file sudah pernah sukses diupload.
        duplicate_youtube_id: YouTube video ID dari upload sebelumnya (jika duplikat).
        channel_id: ID channel YouTube yang terhubung.
        channel_title: Nama channel YouTube yang terhubung.
        channel_url: URL channel YouTube.
        auth_ok: True jika autentikasi OAuth berhasil diverifikasi.
    """
    ok: bool = False
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    filename: str = ""
    filepath: str = ""
    title: str = ""
    file_size_bytes: int = 0
    file_size_mb: float = 0.0
    privacy: str = "private"
    is_duplicate: bool = False
    duplicate_youtube_id: str = ""
    channel_id: str = ""
    channel_title: str = ""
    channel_url: str = ""
    auth_ok: bool = False


# ------------------------------------------------------------------ #
#  Individual checks (masing-masing bisa di-test sendiri)
# ------------------------------------------------------------------ #

def check_file(filepath: str) -> Dict:
    """
    Periksa keberadaan file, bukan zero-byte, dan bisa dibaca.

    Returns:
        Dict berisi 'ok', 'error' (str atau None), 'size_bytes', 'size_mb'.
    """
    path = Path(filepath)

    if not path.exists():
        return {
            "ok": False,
            "error": f"File tidak ditemukan: {filepath}",
            "size_bytes": 0,
            "size_mb": 0.0,
        }

    if not path.is_file():
        return {
            "ok": False,
            "error": f"Path bukan file: {filepath}",
            "size_bytes": 0,
            "size_mb": 0.0,
        }

    try:
        size = path.stat().st_size
    except OSError as e:
        return {
            "ok": False,
            "error": f"Tidak bisa membaca info file: {e}",
            "size_bytes": 0,
            "size_mb": 0.0,
        }

    if size == 0:
        return {
            "ok": False,
            "error": f"File berukuran 0 byte (kosong): {path.name}",
            "size_bytes": 0,
            "size_mb": 0.0,
        }

    # Coba buka file untuk memastikan bisa dibaca
    try:
        with open(filepath, "rb") as f:
            f.read(16)  # baca 16 byte pertama saja
    except (OSError, PermissionError) as e:
        return {
            "ok": False,
            "error": f"File tidak bisa dibaca (mungkin terkunci): {e}",
            "size_bytes": size,
            "size_mb": round(size / 1024 / 1024, 2),
        }

    return {
        "ok": True,
        "error": None,
        "size_bytes": size,
        "size_mb": round(size / 1024 / 1024, 2),
    }


def check_auth(youtube_service) -> Dict:
    """
    Verifikasi autentikasi OAuth dengan memanggil channel info.
    Ini sekaligus mengambil identitas channel.

    Args:
        youtube_service: YouTube API service yang sudah dibuat.

    Returns:
        Dict berisi 'ok', 'error', 'channel_id', 'channel_title', 'channel_url'.
    """
    try:
        from src.auth import get_channel_info, AuthError
        info = get_channel_info(youtube_service)
        return {
            "ok": True,
            "error": None,
            "channel_id": info["channel_id"],
            "channel_title": info["channel_title"],
            "channel_url": info["channel_url"],
        }
    except Exception as e:
        return {
            "ok": False,
            "error": str(e),
            "channel_id": "",
            "channel_title": "",
            "channel_url": "",
        }


def check_duplicate(filepath: str, file_hash: str, database) -> Dict:
    """
    Periksa apakah file sudah pernah berhasil diupload.

    Args:
        filepath: Path lengkap file.
        file_hash: SHA-256 hash file (boleh kosong).
        database: Instance UploadDatabase.

    Returns:
        Dict berisi 'is_duplicate', 'youtube_id', 'warning' (atau None).
    """
    if database is None:
        return {"is_duplicate": False, "youtube_id": "", "warning": None}

    try:
        # Cek berdasarkan filepath
        existing = database.find_by_filepath(filepath)
        if existing and existing["status"] == "success":
            yt_id = existing["youtube_video_id"] or ""
            return {
                "is_duplicate": True,
                "youtube_id": yt_id,
                "warning": (
                    f"File ini sudah pernah diupload (berdasarkan path).\n"
                    f"YouTube ID sebelumnya: {yt_id or '(tidak tercatat)'}"
                ),
            }

        # Cek berdasarkan hash
        if file_hash:
            hash_match = database.find_by_hash(file_hash)
            if hash_match:
                yt_id = hash_match["youtube_video_id"] or ""
                prev_file = hash_match["filename"] or hash_match["filepath"]
                return {
                    "is_duplicate": True,
                    "youtube_id": yt_id,
                    "warning": (
                        f"Konten file ini sama dengan file yang sudah diupload:\n"
                        f"File sebelumnya: {prev_file}\n"
                        f"YouTube ID: {yt_id or '(tidak tercatat)'}"
                    ),
                }
    except Exception as e:
        logger.warning("Gagal cek duplikat: %s", e)

    return {"is_duplicate": False, "youtube_id": "", "warning": None}


# ------------------------------------------------------------------ #
#  Fungsi utama: jalankan semua pemeriksaan
# ------------------------------------------------------------------ #

def run_preflight(
    metadata: Dict,
    youtube_service,
    database,
    compute_hash: bool = True,
) -> PreflightResult:
    """
    Jalankan semua pemeriksaan pra-upload untuk satu file.

    Args:
        metadata: Dict dengan minimal 'filepath', 'filename', 'title'.
        youtube_service: YouTube API service (atau None untuk skip auth check).
        database: UploadDatabase instance (atau None untuk skip duplikat check).
        compute_hash: Hitung hash file jika belum ada (bisa lambat untuk file besar).

    Returns:
        PreflightResult lengkap.
    """
    result = PreflightResult(
        filepath=metadata.get("filepath", ""),
        filename=metadata.get("filename", ""),
        title=metadata.get("title", ""),
        privacy="private",  # selalu private, tidak bisa diubah
    )

    # ---- 1. Periksa file ----
    file_check = check_file(result.filepath)
    result.file_size_bytes = file_check["size_bytes"]
    result.file_size_mb = file_check["size_mb"]

    if not file_check["ok"]:
        result.errors.append(file_check["error"])
        result.ok = False
        return result  # tidak perlu lanjut jika file tidak valid

    # ---- 2. Hitung hash (opsional, untuk deteksi duplikat) ----
    file_hash = metadata.get("file_hash", "")
    if compute_hash and not file_hash:
        try:
            # Gunakan cached hash — jika file tidak berubah, tidak perlu baca ulang
            file_hash = get_file_hash_cached(result.filepath)
            logger.debug("Hash dihitung/dicache: %s", file_hash[:16] + "..." if file_hash else "(gagal)")
        except Exception as e:
            logger.warning("Gagal hitung hash: %s", e)

    # ---- 3. Periksa duplikat ----
    dup_check = check_duplicate(result.filepath, file_hash, database)
    result.is_duplicate = dup_check["is_duplicate"]
    result.duplicate_youtube_id = dup_check["youtube_id"]
    if dup_check["warning"]:
        result.warnings.append(dup_check["warning"])

    # ---- 4. Verifikasi OAuth dan ambil info channel ----
    if youtube_service is not None:
        auth_check = check_auth(youtube_service)
        result.auth_ok = auth_check["ok"]
        result.channel_id = auth_check["channel_id"]
        result.channel_title = auth_check["channel_title"]
        result.channel_url = auth_check["channel_url"]

        if not auth_check["ok"]:
            result.errors.append(
                f"Verifikasi OAuth gagal: {auth_check['error']}"
            )
    else:
        result.errors.append(
            "YouTube service tidak tersedia. Lakukan Koneksi OAuth terlebih dahulu."
        )

    # ---- Tentukan hasil keseluruhan ----
    result.ok = len(result.errors) == 0
    return result
