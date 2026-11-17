"""
scanner.py - Modul pemindaian file MP4 yang cepat dan non-blocking.

Perubahan v1.3.0 (bugfix GUI blocking):
- on_progress di-throttle: hanya dikirim ke GUI setiap N file atau setiap
  THROTTLE_INTERVAL detik, bukan setiap file. 291 self.after() calls berurutan
  memblokir Tkinter event loop karena semua harus diproses sebelum
  _on_scan_complete bisa berjalan.
- merge_scan_with_existing() kini O(n) bukan O(n²): pakai dict lookup,
  bukan nested loop.
- validate_entries() tidak lagi memanggil Path.exists() + os.stat() per file
  dari main thread saat populate tabel.
- Instrumentasi timestamp di setiap tahap untuk diagnosa.
- Semua perubahan lain dari v1.2.0 dipertahankan.
"""

import os
import csv
import hashlib
import logging
import threading
import time
from pathlib import Path
from datetime import datetime
from typing import Callable, Dict, Iterator, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
#  Konstanta
# ------------------------------------------------------------------ #

_SKIP_DIRNAMES: frozenset = frozenset({
    "__pycache__",
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "venv",
    ".venv",
    "env",
    ".env",
    "site-packages",
    ".pytest_cache",
    "logs",
    "reports",
    "$RECYCLE.BIN",
    "System Volume Information",
})

# Throttle: kirim progress callback ke GUI maksimal setiap interval ini (detik)
# atau setiap PROGRESS_EVERY file, mana yang lebih dulu.
PROGRESS_THROTTLE_SECS: float = 0.25
PROGRESS_EVERY: int = 10

# ------------------------------------------------------------------ #
#  Cache hash
# ------------------------------------------------------------------ #

_hash_cache: Dict[Tuple[str, int, float], str] = {}
_hash_cache_lock = threading.Lock()


def _cache_key(filepath: str, size: int, mtime: float) -> Tuple[str, int, float]:
    return (filepath, size, mtime)


# ------------------------------------------------------------------ #
#  Instrumentasi
# ------------------------------------------------------------------ #

class ScanTimings:
    """Rekam waktu tiap tahap untuk diagnosa."""

    def __init__(self):
        self._t0 = time.perf_counter()
        self.stages: List[Tuple[str, float]] = []

    def mark(self, label: str) -> float:
        """Catat waktu sejak t0 untuk label ini. Kembalikan elapsed."""
        elapsed = time.perf_counter() - self._t0
        self.stages.append((label, elapsed))
        logger.info("[TIMING] %-40s %.3fs", label, elapsed)
        return elapsed

    def report(self) -> str:
        lines = ["=== SCAN TIMING REPORT ==="]
        prev = 0.0
        for label, t in self.stages:
            delta = t - prev
            lines.append(f"  {label:<40} total={t:.3f}s  Δ={delta:.3f}s")
            prev = t
        return "\n".join(lines)


# ------------------------------------------------------------------ #
#  Core: iteratif, non-blocking, dengan throttled progress
# ------------------------------------------------------------------ #

def iter_mp4_files(
    source_folder: str,
    cancel_event: Optional[threading.Event] = None,
    progress_callback: Optional[Callable[[str, int], None]] = None,
) -> Iterator[Dict]:
    """
    Iterasi file MP4 secara rekursif menggunakan os.scandir().

    Progress callback di-throttle: dipanggil maksimal setiap
    PROGRESS_THROTTLE_SECS detik ATAU setiap PROGRESS_EVERY file.
    Ini mencegah penumpukan ratusan self.after() di Tkinter event queue
    yang memblokir GUI setelah discovery selesai.

    Yields:
        Dict metadata ringan per file MP4 (tanpa file_hash).
    """
    source = Path(source_folder)
    if not source.exists():
        raise FileNotFoundError(f"Folder tidak ditemukan: {source_folder}")
    if not source.is_dir():
        raise NotADirectoryError(f"Bukan folder: {source_folder}")

    count = 0
    last_callback_time = time.perf_counter()
    stack: List[str] = [str(source)]

    while stack:
        if cancel_event and cancel_event.is_set():
            logger.info("Pemindaian dibatalkan setelah %d file.", count)
            return

        current_dir = stack.pop()

        try:
            with os.scandir(current_dir) as it:
                entries = list(it)
        except PermissionError as e:
            logger.warning("Akses ditolak ke folder: %s — %s", current_dir, e)
            continue
        except OSError as e:
            logger.warning("Error membaca folder: %s — %s", current_dir, e)
            continue

        subdirs: List[str] = []

        for entry in entries:
            if cancel_event and cancel_event.is_set():
                return

            try:
                if entry.is_dir(follow_symlinks=False):
                    if entry.name not in _SKIP_DIRNAMES and not entry.name.startswith("."):
                        subdirs.append(entry.path)
                    continue

                if not entry.name.lower().endswith(".mp4"):
                    continue

                stat = entry.stat(follow_symlinks=False)
                file_size_bytes = stat.st_size
                file_mtime = stat.st_mtime

                if file_size_bytes == 0:
                    logger.warning("File kosong (0 byte), dilewati: %s", entry.path)
                    continue

                file_size_mb = round(file_size_bytes / (1024 * 1024), 2)
                modified_str = datetime.fromtimestamp(file_mtime).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )

                stem = Path(entry.name).stem
                default_title = stem.replace("_", " ").replace("-", " ").strip()
                if len(default_title) > 100:
                    default_title = default_title[:97] + "..."
                if not default_title:
                    default_title = stem

                filepath = entry.path

                metadata = {
                    "filepath": filepath,
                    "filename": entry.name,
                    "title": default_title,
                    "description": "",
                    "privacy": "private",
                    "playlist_id": "",
                    "tags": "",
                    "category_id": "27",
                    "file_size_mb": file_size_mb,
                    "file_size_bytes": file_size_bytes,
                    "modified_time": modified_str,
                    "file_hash": "",
                }

                count += 1

                # ---- THROTTLED progress callback ----
                # Kirim ke GUI hanya jika: sudah cukup lama sejak callback terakhir
                # ATAU kelipatan PROGRESS_EVERY. Ini mencegah banjir self.after()
                # yang memblokir Tkinter event loop setelah scan selesai.
                if progress_callback:
                    now = time.perf_counter()
                    if (now - last_callback_time >= PROGRESS_THROTTLE_SECS
                            or count % PROGRESS_EVERY == 0):
                        progress_callback(filepath, count)
                        last_callback_time = now

                yield metadata

            except (OSError, PermissionError) as e:
                logger.warning("Tidak bisa membaca file %s: %s", entry.path, e)
                continue
            except (UnicodeDecodeError, ValueError) as e:
                logger.warning("Nama file bermasalah di %s: %s", current_dir, e)
                continue

        stack.extend(sorted(subdirs, reverse=True))

    # Callback akhir untuk memastikan count terakhir dikirim
    if progress_callback and count > 0:
        progress_callback("", count)


def scan_mp4_files(
    source_folder: str,
    cancel_event: Optional[threading.Event] = None,
    progress_callback: Optional[Callable[[str, int], None]] = None,
) -> List[Dict]:
    """
    Pindai semua file MP4 secara rekursif, kembalikan sebagai list.
    Wrapper di atas iter_mp4_files() dengan logging timing.
    """
    t0 = time.perf_counter()
    results = list(
        iter_mp4_files(
            source_folder,
            cancel_event=cancel_event,
            progress_callback=progress_callback,
        )
    )
    elapsed = time.perf_counter() - t0
    logger.info(
        "[TIMING] scan_mp4_files selesai: %d file dalam %.3fs (%.1f file/s)",
        len(results), elapsed,
        len(results) / elapsed if elapsed > 0 else 0,
    )
    return results


# ------------------------------------------------------------------ #
#  Hashing: lazy dengan cache
# ------------------------------------------------------------------ #

def compute_file_hash(filepath: str, chunk_size: int = 65536) -> str:
    """
    Hitung SHA-256 hash file.

    Ini TIDAK dipanggil saat scan awal. Dipanggil hanya ketika dibutuhkan
    untuk deteksi duplikat di tahap preflight upload.
    """
    sha256 = hashlib.sha256()
    try:
        with open(filepath, "rb") as f:
            while True:
                data = f.read(chunk_size)
                if not data:
                    break
                sha256.update(data)
        return sha256.hexdigest()
    except (OSError, IOError) as e:
        logger.error("Gagal menghitung hash untuk %s: %s", filepath, e)
        return ""


def get_file_hash_cached(filepath: str) -> str:
    """
    Ambil hash file dengan cache berdasarkan (filepath, size, mtime).

    Jika file tidak berubah sejak hash terakhir dihitung, kembalikan
    nilai cache tanpa membaca isi file lagi.

    Returns:
        SHA-256 hex string, atau "" jika file tidak bisa dibaca.
    """
    try:
        st = os.stat(filepath)
        key = _cache_key(filepath, st.st_size, st.st_mtime)
    except OSError:
        return ""

    with _hash_cache_lock:
        if key in _hash_cache:
            logger.debug("Hash cache hit: %s", Path(filepath).name)
            return _hash_cache[key]

    # Hitung di luar lock agar tidak blokir thread lain
    hash_val = compute_file_hash(filepath)

    if hash_val:
        with _hash_cache_lock:
            _hash_cache[key] = hash_val

    return hash_val


def compute_hashes_for_entries(
    entries: List[Dict],
    cancel_event: Optional[threading.Event] = None,
    progress_callback: Optional[Callable[[str, int, int], None]] = None,
) -> List[Dict]:
    """
    Hitung hash untuk semua entri yang belum punya hash.

    Gunakan untuk batch hashing sebelum upload, bukan saat scan awal.

    Args:
        entries: List entry dari scan_mp4_files.
        cancel_event: Set untuk membatalkan.
        progress_callback: Dipanggil dengan (filepath, done, total).

    Returns:
        entries yang sama dengan file_hash diisi.
    """
    total = len(entries)
    for i, entry in enumerate(entries, 1):
        if cancel_event and cancel_event.is_set():
            logger.info("Hashing dibatalkan setelah %d/%d file.", i - 1, total)
            break
        if not entry.get("file_hash"):
            fp = entry.get("filepath", "")
            entry["file_hash"] = get_file_hash_cached(fp)
        if progress_callback:
            progress_callback(entry.get("filepath", ""), i, total)
    return entries


# ------------------------------------------------------------------ #
#  CSV: save dengan proteksi terhadap overwrite metadata yang sudah diedit
# ------------------------------------------------------------------ #

FIELDNAMES = [
    "filepath",
    "filename",
    "title",
    "description",
    "privacy",
    "playlist_id",
    "tags",
    "category_id",
    "file_size_mb",
    "modified_time",
    "file_hash",
]


def save_metadata_csv(entries: List[Dict], output_path: str) -> str:
    """
    Simpan metadata ke CSV yang bisa diedit di Excel.
    Menulis semua entri baru. Gunakan merge_scan_with_existing() terlebih
    dahulu jika ingin mempertahankan metadata yang sudah diedit pengguna.
    """
    t0 = time.perf_counter()
    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    with open(output_file, "w", newline="", encoding="utf-8-sig") as csvfile:
        writer = csv.DictWriter(
            csvfile, fieldnames=FIELDNAMES, extrasaction="ignore"
        )
        writer.writeheader()
        writer.writerows(entries)

    elapsed = time.perf_counter() - t0
    logger.info(
        "[TIMING] save_metadata_csv: %d entri ditulis dalam %.3fs → %s",
        len(entries), elapsed, output_path,
    )
    return str(output_file)


def merge_scan_with_existing(
    new_entries: List[Dict],
    existing_csv_path: str,
) -> List[Dict]:
    """
    Gabungkan hasil scan baru dengan CSV yang sudah diedit pengguna.

    O(n) — menggunakan dict lookup, bukan nested loop.

    Aturan merge:
    - filepath sudah ada di CSV lama → pertahankan field user (title, desc, dll).
    - filepath baru → tambahkan dengan nilai default dari scan.
    - filepath di CSV lama tapi tidak ada di disk → tidak disertakan.
    """
    t0 = time.perf_counter()
    existing_map: Dict[str, Dict] = {}
    csv_path = Path(existing_csv_path)

    if csv_path.exists():
        try:
            existing = load_metadata_csv(existing_csv_path)
            # O(n) build dict — tidak ada nested loop
            existing_map = {row["filepath"]: row for row in existing if row.get("filepath")}
            logger.info(
                "[TIMING] CSV lama dimuat: %d entri dalam %.3fs",
                len(existing_map), time.perf_counter() - t0,
            )
        except Exception as e:
            logger.warning("Tidak bisa muat CSV lama untuk merge: %s", e)

    t1 = time.perf_counter()
    USER_FIELDS = ("title", "description", "privacy", "playlist_id", "tags", "category_id")
    merged: List[Dict] = []
    preserved = 0
    added = 0

    for new_entry in new_entries:
        fp = new_entry["filepath"]
        if fp in existing_map:
            old = existing_map[fp]
            merged_entry = dict(new_entry)
            for field in USER_FIELDS:
                old_val = old.get(field, "")
                if old_val and old_val.strip():
                    merged_entry[field] = old_val
            if old.get("file_hash"):
                merged_entry["file_hash"] = old["file_hash"]
            merged.append(merged_entry)
            preserved += 1
        else:
            merged.append(new_entry)
            added += 1

    elapsed_merge = time.perf_counter() - t1
    total_elapsed = time.perf_counter() - t0
    logger.info(
        "[TIMING] merge_scan_with_existing: total=%.3fs, merge=%.3fs "
        "(%d dipertahankan, %d baru, %d dihapus)",
        total_elapsed, elapsed_merge, preserved, added,
        len(existing_map) - preserved,
    )
    return merged


def load_metadata_csv(csv_path: str) -> List[Dict]:
    """
    Muat metadata dari CSV yang sudah diedit pengguna.
    """
    csv_file = Path(csv_path)
    if not csv_file.exists():
        raise FileNotFoundError(f"File CSV tidak ditemukan: {csv_path}")

    entries = []
    with open(csv_file, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not row.get("filepath") or not row.get("title"):
                logger.warning("Baris CSV tidak lengkap, dilewati: %s", dict(row))
                continue
            # Normalisasi privacy
            privacy = row.get("privacy", "private").strip().lower()
            if privacy not in ("public", "unlisted", "private"):
                privacy = "private"
            row["privacy"] = privacy
            entries.append(row)

    logger.info("Dimuat %d entri dari CSV: %s", len(entries), csv_path)
    return entries


def validate_entries(entries: List[Dict]) -> List[Dict]:
    """
    Validasi dan bersihkan entri sebelum upload.
    Hanya kembalikan entri dengan file yang ada dan title tidak kosong.
    """
    valid = []
    for entry in entries:
        filepath = entry.get("filepath", "").strip()
        title = entry.get("title", "").strip()

        if not filepath:
            logger.warning("Entri tanpa filepath dilewati.")
            continue
        if not Path(filepath).exists():
            logger.warning("File tidak ditemukan, dilewati: %s", filepath)
            continue

        # Cek zero-byte
        try:
            if os.stat(filepath).st_size == 0:
                logger.warning("File 0 byte dilewati: %s", filepath)
                continue
        except OSError:
            logger.warning("Tidak bisa stat file, dilewati: %s", filepath)
            continue

        if not title:
            entry["title"] = Path(filepath).stem
        if len(entry.get("title", "")) > 100:
            entry["title"] = entry["title"][:97] + "..."

        valid.append(entry)

    return valid
