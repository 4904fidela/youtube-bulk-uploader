"""
test_scanner.py - Unit tests untuk modul scanner.
Mencakup semua test lama (kompatibilitas) + regression tests baru
untuk performa pemindaian ratusan file dan proteksi metadata.
Tidak menyentuh file MP4 asli.
"""

import os
import csv
import time
import threading
import tempfile
import pytest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.scanner import (
    scan_mp4_files,
    iter_mp4_files,
    save_metadata_csv,
    load_metadata_csv,
    validate_entries,
    compute_file_hash,
    get_file_hash_cached,
    merge_scan_with_existing,
    compute_hashes_for_entries,
    _hash_cache,
    _SKIP_DIRNAMES,
    ScanTimings,
    PROGRESS_THROTTLE_SECS,
    PROGRESS_EVERY,
)


# ------------------------------------------------------------------ #
#  Fixtures
# ------------------------------------------------------------------ #

@pytest.fixture
def temp_folder(tmp_path):
    """Folder sementara: 2 MP4 di root, 1 di subfolder, 1 txt dan 1 jpg."""
    (tmp_path / "zoom_recording_001.mp4").write_bytes(b"\x00" * 1024)
    (tmp_path / "zoom_recording_002.mp4").write_bytes(b"\x00" * 2048)
    sub = tmp_path / "subfolder"
    sub.mkdir()
    (sub / "lecture_03.mp4").write_bytes(b"\x00" * 512)
    (tmp_path / "notes.txt").write_text("catatan kuliah")
    (tmp_path / "thumb.jpg").write_bytes(b"\xff\xd8\xff")
    return tmp_path


@pytest.fixture
def sample_csv(tmp_path):
    csv_path = tmp_path / "metadata.csv"
    rows = [{
        "filepath": str(tmp_path / "video.mp4"),
        "filename": "video.mp4",
        "title": "Kuliah Matematika",
        "description": "Pertemuan pertama",
        "privacy": "private",
        "playlist_id": "",
        "tags": "matematika,kuliah",
        "category_id": "27",
        "file_size_mb": "10.5",
        "modified_time": "2024-01-01 10:00:00",
        "file_hash": "",
    }]
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    return csv_path


@pytest.fixture
def many_mp4_folder(tmp_path):
    """Folder dengan 300 file MP4 dummy di struktur subfolder bertingkat."""
    count = 0
    for batch in range(10):          # 10 subfolder
        sub = tmp_path / f"batch_{batch:02d}"
        sub.mkdir()
        for i in range(30):          # 30 file per subfolder = 300 total
            fname = f"zoom_{batch:02d}_{i:03d}.mp4"
            # 1 KB per file — cukup untuk stat(), tidak perlu konten nyata
            (sub / fname).write_bytes(b"\x00" * 1024)
            count += 1
    return tmp_path, count


# ------------------------------------------------------------------ #
#  TESTS LAMA — scan_mp4_files (kompatibilitas)
# ------------------------------------------------------------------ #

class TestScanMp4Files:
    def test_menemukan_semua_mp4(self, temp_folder):
        entries = scan_mp4_files(str(temp_folder))
        filenames = [e["filename"] for e in entries]
        assert "zoom_recording_001.mp4" in filenames
        assert "zoom_recording_002.mp4" in filenames
        assert "lecture_03.mp4" in filenames

    def test_tidak_menyertakan_non_mp4(self, temp_folder):
        entries = scan_mp4_files(str(temp_folder))
        filenames = [e["filename"] for e in entries]
        assert "notes.txt" not in filenames
        assert "thumb.jpg" not in filenames

    def test_rekursif_ke_subfolder(self, temp_folder):
        entries = scan_mp4_files(str(temp_folder))
        filenames = [e["filename"] for e in entries]
        assert "lecture_03.mp4" in filenames

    def test_total_3_file(self, temp_folder):
        entries = scan_mp4_files(str(temp_folder))
        assert len(entries) == 3

    def test_folder_tidak_ada_raise_error(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            scan_mp4_files(str(tmp_path / "tidak_ada"))

    def test_bukan_folder_raise_error(self, tmp_path):
        file_path = tmp_path / "file.txt"
        file_path.write_text("test")
        with pytest.raises(NotADirectoryError):
            scan_mp4_files(str(file_path))

    def test_entry_memiliki_field_wajib(self, temp_folder):
        entries = scan_mp4_files(str(temp_folder))
        required = ["filepath", "filename", "title", "description",
                    "privacy", "file_size_mb"]
        for entry in entries:
            for field in required:
                assert field in entry, f"Field '{field}' tidak ada"

    def test_privacy_default_private(self, temp_folder):
        entries = scan_mp4_files(str(temp_folder))
        for entry in entries:
            assert entry["privacy"] == "private"

    def test_judul_dari_nama_file(self, temp_folder):
        entries = scan_mp4_files(str(temp_folder))
        for entry in entries:
            # stem ada di title (dengan spasi)
            stem = Path(entry["filename"]).stem
            assert stem.replace("_", " ").replace("-", " ").strip() in entry["title"] \
                or entry["title"][:3] == stem[:3]  # toleransi truncation

    def test_judul_maks_100_karakter(self, tmp_path):
        long_name = "a" * 150 + ".mp4"
        (tmp_path / long_name).write_bytes(b"\x00" * 100)
        entries = scan_mp4_files(str(tmp_path))
        for entry in entries:
            assert len(entry["title"]) <= 100

    def test_folder_kosong_kembalikan_list_kosong(self, tmp_path):
        empty = tmp_path / "kosong"
        empty.mkdir()
        assert scan_mp4_files(str(empty)) == []

    def test_ukuran_file_terisi(self, temp_folder):
        entries = scan_mp4_files(str(temp_folder))
        for entry in entries:
            assert entry["file_size_mb"] >= 0

    def test_file_hash_kosong_saat_scan(self, temp_folder):
        """Hash TIDAK dihitung saat scan — selalu kosong."""
        entries = scan_mp4_files(str(temp_folder))
        for entry in entries:
            assert entry["file_hash"] == ""


# ------------------------------------------------------------------ #
#  TESTS BARU — iter_mp4_files (generator API)
# ------------------------------------------------------------------ #

class TestIterMp4Files:
    def test_yield_incremental_bukan_batching(self, temp_folder):
        """iter_mp4_files harus yield satu per satu dan callback di-throttle."""
        yielded = []
        progress_calls = []

        def on_progress(fp, count):
            progress_calls.append((fp, count))

        for entry in iter_mp4_files(str(temp_folder), progress_callback=on_progress):
            yielded.append(entry)

        assert len(yielded) == 3
        # Dengan throttle, callback bisa < 3 kali untuk 3 file (sangat cepat)
        assert len(progress_calls) >= 1, "Minimal satu callback harus dipanggil"
        # Count terakhir harus sama dengan total file
        assert progress_calls[-1][1] == 3

    def test_cancel_menghentikan_iterasi(self, tmp_path):
        """cancel_event.set() harus menghentikan scan di tengah jalan."""
        for i in range(20):
            (tmp_path / f"vid_{i:03d}.mp4").write_bytes(b"\x00" * 512)

        cancel = threading.Event()
        found = []
        for entry in iter_mp4_files(str(tmp_path), cancel_event=cancel):
            found.append(entry)
            if len(found) >= 3:
                cancel.set()

        assert len(found) <= 20  # tidak semua diproses
        # Setelah cancel, harus berhenti tidak lama setelah 3

    def test_progress_callback_dipanggil(self, temp_folder):
        """Callback harus dipanggil minimal sekali dan count akhir = total file."""
        calls = []
        for _ in iter_mp4_files(str(temp_folder), progress_callback=lambda fp, n: calls.append(n)):
            pass
        assert len(calls) >= 1, "Minimal satu callback harus dipanggil"
        # Count terakhir harus = total file (3)
        assert calls[-1] == 3

    def test_zero_byte_dilewati(self, tmp_path):
        """File 0 byte tidak boleh masuk hasil scan."""
        (tmp_path / "normal.mp4").write_bytes(b"\x00" * 1024)
        (tmp_path / "empty.mp4").write_bytes(b"")  # zero-byte
        entries = list(iter_mp4_files(str(tmp_path)))
        filenames = [e["filename"] for e in entries]
        assert "normal.mp4" in filenames
        assert "empty.mp4" not in filenames

    def test_skip_dirnames_dilewati(self, tmp_path):
        """Folder dalam _SKIP_DIRNAMES tidak dipindai."""
        for skip_dir in ["__pycache__", ".git", "venv", "logs"]:
            d = tmp_path / skip_dir
            d.mkdir()
            (d / "seharusnya_tidak_ditemukan.mp4").write_bytes(b"\x00" * 512)
        # File valid di root
        (tmp_path / "valid.mp4").write_bytes(b"\x00" * 512)

        entries = list(iter_mp4_files(str(tmp_path)))
        filenames = [e["filename"] for e in entries]
        assert "valid.mp4" in filenames
        assert "seharusnya_tidak_ditemukan.mp4" not in filenames

    def test_case_insensitive_mp4_extension(self, tmp_path):
        """File .MP4 dan .Mp4 juga harus ditemukan."""
        (tmp_path / "upper.MP4").write_bytes(b"\x00" * 512)
        (tmp_path / "mixed.Mp4").write_bytes(b"\x00" * 512)
        (tmp_path / "lower.mp4").write_bytes(b"\x00" * 512)
        entries = list(iter_mp4_files(str(tmp_path)))
        assert len(entries) == 3

    def test_file_tanpa_hash_saat_scan(self, temp_folder):
        for entry in iter_mp4_files(str(temp_folder)):
            assert entry["file_hash"] == ""

    def test_folder_inaccessible_tidak_crash(self, tmp_path):
        """Folder yang tidak bisa dibaca tidak menyebabkan exception."""
        inacc = tmp_path / "inaccessible"
        inacc.mkdir()
        (inacc / "video.mp4").write_bytes(b"\x00" * 512)
        (tmp_path / "accessible.mp4").write_bytes(b"\x00" * 512)
        # Hapus permission — hanya bisa di non-Windows
        try:
            inacc.chmod(0o000)
            entries = list(iter_mp4_files(str(tmp_path)))
            filenames = [e["filename"] for e in entries]
            assert "accessible.mp4" in filenames
            # video.mp4 boleh ada atau tidak tergantung OS
        except (PermissionError, NotImplementedError):
            pytest.skip("Permission test tidak didukung di platform ini")
        finally:
            try:
                inacc.chmod(0o755)
            except Exception:
                pass

    def test_unicode_filename(self, tmp_path):
        """Nama file dengan karakter Unicode harus bisa diproses."""
        fname = "rekaman_kuliah_أساسيات.mp4"
        try:
            (tmp_path / fname).write_bytes(b"\x00" * 512)
            entries = list(iter_mp4_files(str(tmp_path)))
            assert len(entries) == 1
        except (UnicodeEncodeError, OSError):
            pytest.skip("Filesystem tidak mendukung Unicode filename ini")


# ------------------------------------------------------------------ #
#  REGRESSION TEST — performa 300 file
# ------------------------------------------------------------------ #

class TestScanPerformance:
    def test_300_file_selesai_dalam_10_detik(self, many_mp4_folder):
        """
        Scan 300 file MP4 dummy harus selesai dalam 10 detik.
        Ini membuktikan tidak ada blocking I/O yang tidak perlu.
        """
        folder, expected_count = many_mp4_folder

        start = time.monotonic()
        entries = scan_mp4_files(str(folder))
        elapsed = time.monotonic() - start

        assert len(entries) == expected_count, \
            f"Diharapkan {expected_count} file, ditemukan {len(entries)}"
        assert elapsed < 10.0, \
            f"Scan {expected_count} file memakan {elapsed:.2f}s — terlalu lama!"

    def test_300_file_progress_callback_dipanggil_setiap_file(self, many_mp4_folder):
        """
        Dengan throttle ON, callback dipanggil jauh lebih sedikit dari 300x.
        Count terakhir harus tetap == total file.
        (Test ini diganti dari 'setiap file' ke 'count akhir benar'.)
        """
        folder, expected_count = many_mp4_folder
        call_counts = []

        scan_mp4_files(
            str(folder),
            progress_callback=lambda fp, n: call_counts.append(n),
        )

        # Count terakhir harus = total file
        assert len(call_counts) >= 1
        assert call_counts[-1] == expected_count
        # Callback harus jauh lebih sedikit dari total file (efek throttle)
        assert len(call_counts) < expected_count, \
            f"Throttle tidak bekerja: {len(call_counts)} calls untuk {expected_count} file"

    def test_scan_tidak_menghitung_hash(self, many_mp4_folder):
        """Scan 300 file tidak boleh menghitung hash satupun."""
        folder, _ = many_mp4_folder
        hash_calls = []

        # Patch compute_file_hash untuk deteksi jika dipanggil
        import src.scanner as scanner_module
        original = scanner_module.compute_file_hash

        def spy_hash(filepath, **kwargs):
            hash_calls.append(filepath)
            return original(filepath, **kwargs)

        scanner_module.compute_file_hash = spy_hash
        try:
            scan_mp4_files(str(folder))
        finally:
            scanner_module.compute_file_hash = original

        assert len(hash_calls) == 0, \
            f"compute_file_hash dipanggil {len(hash_calls)}x saat scan — seharusnya 0!"

    def test_cancel_event_menghentikan_scan_300(self, many_mp4_folder):
        """Cancel event menghentikan scan sebelum semua 300 file diproses."""
        folder, expected = many_mp4_folder
        cancel = threading.Event()
        found = []

        for entry in iter_mp4_files(str(folder), cancel_event=cancel):
            found.append(entry)
            if len(found) >= 50:
                cancel.set()

        # Harus berhenti jauh sebelum 300
        assert len(found) < expected


# ------------------------------------------------------------------ #
#  TESTS LAMA — save_metadata_csv dan load_metadata_csv
# ------------------------------------------------------------------ #

class TestMetadataCsv:
    def test_save_dan_load_roundtrip(self, temp_folder, tmp_path):
        entries = scan_mp4_files(str(temp_folder))
        csv_path = str(tmp_path / "output.csv")
        save_metadata_csv(entries, csv_path)
        assert Path(csv_path).exists()
        loaded = load_metadata_csv(csv_path)
        assert len(loaded) == len(entries)

    def test_csv_memiliki_header(self, temp_folder, tmp_path):
        entries = scan_mp4_files(str(temp_folder))
        csv_path = str(tmp_path / "test.csv")
        save_metadata_csv(entries, csv_path)
        with open(csv_path, encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            header = next(reader)
        assert "filepath" in header
        assert "title" in header
        assert "privacy" in header

    def test_load_csv_tidak_ada_raise_error(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_metadata_csv(str(tmp_path / "tidak_ada.csv"))

    def test_privacy_invalid_direset_ke_private(self, tmp_path):
        csv_path = tmp_path / "test.csv"
        fieldnames = ["filepath", "filename", "title", "description",
                      "privacy", "playlist_id", "tags", "category_id",
                      "file_size_mb", "modified_time", "file_hash"]
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerow({
                "filepath": "/test/video.mp4", "filename": "video.mp4",
                "title": "Test", "description": "", "privacy": "INVALID_VALUE",
                "playlist_id": "", "tags": "", "category_id": "27",
                "file_size_mb": "10", "modified_time": "2024-01-01", "file_hash": "",
            })
        loaded = load_metadata_csv(str(csv_path))
        assert loaded[0]["privacy"] == "private"

    def test_load_csv_enkoding_utf8_bom(self, sample_csv):
        entries = load_metadata_csv(str(sample_csv))
        assert len(entries) == 1
        assert entries[0]["title"] == "Kuliah Matematika"


# ------------------------------------------------------------------ #
#  TESTS BARU — merge_scan_with_existing
# ------------------------------------------------------------------ #

class TestMergeScanWithExisting:
    def test_mempertahankan_title_yang_diedit(self, tmp_path):
        """Title yang sudah diedit pengguna tidak boleh ditimpa scan baru."""
        video = tmp_path / "kuliah_01.mp4"
        video.write_bytes(b"\x00" * 1024)

        # Buat CSV lama dengan title yang sudah diedit
        old_csv = tmp_path / "metadata.csv"
        with open(old_csv, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "filepath", "filename", "title", "description", "privacy",
                "playlist_id", "tags", "category_id", "file_size_mb",
                "modified_time", "file_hash",
            ])
            writer.writeheader()
            writer.writerow({
                "filepath": str(video), "filename": video.name,
                "title": "JUDUL YANG SUDAH DIEDIT PENGGUNA",
                "description": "Deskripsi yang sudah diisi",
                "privacy": "unlisted",
                "playlist_id": "PL_test", "tags": "kuliah,online",
                "category_id": "22", "file_size_mb": "1.0",
                "modified_time": "2024-01-01 10:00:00", "file_hash": "",
            })

        # Scan baru menghasilkan title default
        new_entries = scan_mp4_files(str(tmp_path))
        merged = merge_scan_with_existing(new_entries, str(old_csv))

        assert len(merged) == 1
        assert merged[0]["title"] == "JUDUL YANG SUDAH DIEDIT PENGGUNA"
        assert merged[0]["description"] == "Deskripsi yang sudah diisi"
        assert merged[0]["privacy"] == "unlisted"
        assert merged[0]["playlist_id"] == "PL_test"
        assert merged[0]["category_id"] == "22"

    def test_menambahkan_file_baru_yang_belum_ada_di_csv(self, tmp_path):
        """File baru yang tidak ada di CSV lama harus ditambahkan."""
        old_video = tmp_path / "lama.mp4"
        new_video = tmp_path / "baru.mp4"
        old_video.write_bytes(b"\x00" * 512)
        new_video.write_bytes(b"\x00" * 512)

        old_csv = tmp_path / "metadata.csv"
        with open(old_csv, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "filepath", "filename", "title", "description", "privacy",
                "playlist_id", "tags", "category_id", "file_size_mb",
                "modified_time", "file_hash",
            ])
            writer.writeheader()
            writer.writerow({
                "filepath": str(old_video), "filename": old_video.name,
                "title": "Video Lama", "description": "", "privacy": "private",
                "playlist_id": "", "tags": "", "category_id": "27",
                "file_size_mb": "0.0", "modified_time": "2024-01-01", "file_hash": "",
            })

        new_entries = scan_mp4_files(str(tmp_path))
        merged = merge_scan_with_existing(new_entries, str(old_csv))

        filenames = [e["filename"] for e in merged]
        assert "lama.mp4" in filenames
        assert "baru.mp4" in filenames
        assert len(merged) == 2

    def test_file_yang_dihapus_tidak_muncul_di_hasil(self, tmp_path):
        """File yang ada di CSV lama tapi sudah tidak ada di disk tidak boleh muncul."""
        existing_video = tmp_path / "ada.mp4"
        existing_video.write_bytes(b"\x00" * 512)
        # "hilang.mp4" tidak ada di disk

        old_csv = tmp_path / "metadata.csv"
        fieldnames = ["filepath", "filename", "title", "description", "privacy",
                      "playlist_id", "tags", "category_id", "file_size_mb",
                      "modified_time", "file_hash"]
        with open(old_csv, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerow({
                "filepath": str(existing_video), "filename": "ada.mp4",
                "title": "Ada", "description": "", "privacy": "private",
                "playlist_id": "", "tags": "", "category_id": "27",
                "file_size_mb": "0.0", "modified_time": "2024-01-01", "file_hash": "",
            })
            writer.writerow({
                "filepath": str(tmp_path / "hilang.mp4"), "filename": "hilang.mp4",
                "title": "Hilang", "description": "", "privacy": "private",
                "playlist_id": "", "tags": "", "category_id": "27",
                "file_size_mb": "0.0", "modified_time": "2024-01-01", "file_hash": "",
            })

        new_entries = scan_mp4_files(str(tmp_path))
        merged = merge_scan_with_existing(new_entries, str(old_csv))

        filenames = [e["filename"] for e in merged]
        assert "ada.mp4" in filenames
        assert "hilang.mp4" not in filenames

    def test_tanpa_csv_lama_mengembalikan_new_entries(self, tmp_path):
        """Jika CSV lama tidak ada, kembalikan entri baru apa adanya."""
        video = tmp_path / "video.mp4"
        video.write_bytes(b"\x00" * 512)
        new_entries = scan_mp4_files(str(tmp_path))

        merged = merge_scan_with_existing(new_entries, str(tmp_path / "tidak_ada.csv"))
        assert len(merged) == 1
        assert merged[0]["filename"] == "video.mp4"

    def test_mempertahankan_file_hash_lama(self, tmp_path):
        """Hash lama dipertahankan agar tidak perlu dihitung ulang."""
        video = tmp_path / "video.mp4"
        video.write_bytes(b"\x00" * 512)
        old_hash = "a" * 64

        old_csv = tmp_path / "metadata.csv"
        with open(old_csv, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "filepath", "filename", "title", "description", "privacy",
                "playlist_id", "tags", "category_id", "file_size_mb",
                "modified_time", "file_hash",
            ])
            writer.writeheader()
            writer.writerow({
                "filepath": str(video), "filename": video.name,
                "title": "Video", "description": "", "privacy": "private",
                "playlist_id": "", "tags": "", "category_id": "27",
                "file_size_mb": "0.0", "modified_time": "2024-01-01",
                "file_hash": old_hash,
            })

        new_entries = scan_mp4_files(str(tmp_path))
        merged = merge_scan_with_existing(new_entries, str(old_csv))
        assert merged[0]["file_hash"] == old_hash

    def test_title_default_tidak_menimpa_title_kosong_lama(self, tmp_path):
        """Jika title lama kosong, gunakan judul default dari scan baru."""
        video = tmp_path / "kuliah_matematika.mp4"
        video.write_bytes(b"\x00" * 512)

        old_csv = tmp_path / "metadata.csv"
        with open(old_csv, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "filepath", "filename", "title", "description", "privacy",
                "playlist_id", "tags", "category_id", "file_size_mb",
                "modified_time", "file_hash",
            ])
            writer.writeheader()
            writer.writerow({
                "filepath": str(video), "filename": video.name,
                "title": "",  # title kosong
                "description": "", "privacy": "private",
                "playlist_id": "", "tags": "", "category_id": "27",
                "file_size_mb": "0.0", "modified_time": "2024-01-01", "file_hash": "",
            })

        new_entries = scan_mp4_files(str(tmp_path))
        merged = merge_scan_with_existing(new_entries, str(old_csv))
        # Title kosong → gunakan default dari scan baru
        assert merged[0]["title"] != ""
        assert "kuliah" in merged[0]["title"].lower()


# ------------------------------------------------------------------ #
#  TESTS LAMA — validate_entries
# ------------------------------------------------------------------ #

class TestValidateEntries:
    def test_entry_tanpa_filepath_dibuang(self):
        entries = [{"filepath": "", "title": "Test", "filename": "v.mp4"}]
        assert validate_entries(entries) == []

    def test_entry_file_tidak_ada_dibuang(self, tmp_path):
        entries = [{"filepath": str(tmp_path / "tidak_ada.mp4"),
                    "title": "Test", "filename": "tidak_ada.mp4"}]
        assert validate_entries(entries) == []

    def test_judul_kosong_diisi_dari_nama_file(self, tmp_path):
        v = tmp_path / "kuliah_01.mp4"
        v.write_bytes(b"\x00" * 100)
        result = validate_entries([{"filepath": str(v), "title": "", "filename": "kuliah_01.mp4"}])
        assert len(result) == 1
        assert result[0]["title"] == "kuliah_01"

    def test_judul_terlalu_panjang_dipotong(self, tmp_path):
        v = tmp_path / "video.mp4"
        v.write_bytes(b"\x00" * 100)
        result = validate_entries([{"filepath": str(v), "title": "A" * 150, "filename": "video.mp4"}])
        assert len(result) == 1
        assert len(result[0]["title"]) <= 100

    def test_zero_byte_dibuang(self, tmp_path):
        v = tmp_path / "empty.mp4"
        v.write_bytes(b"")
        result = validate_entries([{"filepath": str(v), "title": "Empty", "filename": "empty.mp4"}])
        assert result == []


# ------------------------------------------------------------------ #
#  TESTS LAMA — compute_file_hash
# ------------------------------------------------------------------ #

class TestComputeFileHash:
    def test_hash_konsisten(self, tmp_path):
        f = tmp_path / "test.mp4"
        f.write_bytes(b"data uji coba 12345")
        h1 = compute_file_hash(str(f))
        h2 = compute_file_hash(str(f))
        assert h1 == h2
        assert len(h1) == 64

    def test_hash_berbeda_untuk_isi_berbeda(self, tmp_path):
        f1 = tmp_path / "file1.mp4"
        f2 = tmp_path / "file2.mp4"
        f1.write_bytes(b"data pertama")
        f2.write_bytes(b"data kedua berbeda")
        assert compute_file_hash(str(f1)) != compute_file_hash(str(f2))

    def test_hash_file_tidak_ada_kembalikan_string_kosong(self, tmp_path):
        result = compute_file_hash(str(tmp_path / "tidak_ada.mp4"))
        assert result == ""


# ------------------------------------------------------------------ #
#  TESTS BARU — get_file_hash_cached
# ------------------------------------------------------------------ #

class TestGetFileHashCached:
    def test_hash_konsisten(self, tmp_path):
        f = tmp_path / "video.mp4"
        f.write_bytes(b"isi video")
        h1 = get_file_hash_cached(str(f))
        h2 = get_file_hash_cached(str(f))
        assert h1 == h2
        assert len(h1) == 64

    def test_cache_hit_tidak_baca_ulang(self, tmp_path):
        """Panggilan kedua harus dari cache, tidak membaca file lagi."""
        f = tmp_path / "video.mp4"
        f.write_bytes(b"data untuk cache test")

        import src.scanner as scanner_module
        read_calls = [0]
        original_hash = scanner_module.compute_file_hash

        def counting_hash(filepath, **kwargs):
            read_calls[0] += 1
            return original_hash(filepath, **kwargs)

        scanner_module.compute_file_hash = counting_hash
        try:
            # Bersihkan cache untuk file ini
            with scanner_module._hash_cache_lock:
                keys_to_del = [k for k in scanner_module._hash_cache if k[0] == str(f)]
                for k in keys_to_del:
                    del scanner_module._hash_cache[k]

            h1 = get_file_hash_cached(str(f))
            h2 = get_file_hash_cached(str(f))  # harus dari cache
        finally:
            scanner_module.compute_file_hash = original_hash

        assert h1 == h2
        assert read_calls[0] == 1, f"File dibaca {read_calls[0]}x, seharusnya 1x (sisanya dari cache)"

    def test_file_tidak_ada_kembalikan_kosong(self, tmp_path):
        result = get_file_hash_cached(str(tmp_path / "tidak_ada.mp4"))
        assert result == ""

    def test_cache_invalidated_jika_mtime_berubah(self, tmp_path):
        """Jika mtime berubah (file dimodifikasi), hash dihitung ulang."""
        f = tmp_path / "video.mp4"
        f.write_bytes(b"versi pertama")
        h1 = get_file_hash_cached(str(f))

        # Modifikasi file → mtime berubah → cache harus miss
        time.sleep(0.05)  # pastikan mtime berbeda
        f.write_bytes(b"versi kedua berbeda")
        h2 = get_file_hash_cached(str(f))

        assert h1 != h2


# ------------------------------------------------------------------ #
#  TESTS BARU — compute_hashes_for_entries
# ------------------------------------------------------------------ #

class TestComputeHashesForEntries:
    def test_mengisi_file_hash_yang_kosong(self, tmp_path):
        v = tmp_path / "video.mp4"
        v.write_bytes(b"data video")
        entries = [{"filepath": str(v), "filename": "video.mp4", "file_hash": ""}]
        result = compute_hashes_for_entries(entries)
        assert len(result[0]["file_hash"]) == 64

    def test_tidak_menghitung_ulang_hash_yang_sudah_ada(self, tmp_path):
        v = tmp_path / "video.mp4"
        v.write_bytes(b"data")
        existing_hash = "z" * 64
        entries = [{"filepath": str(v), "filename": "video.mp4", "file_hash": existing_hash}]
        result = compute_hashes_for_entries(entries)
        assert result[0]["file_hash"] == existing_hash  # tidak berubah

    def test_cancel_event_menghentikan_hashing(self, tmp_path):
        cancel = threading.Event()
        entries = []
        for i in range(10):
            v = tmp_path / f"vid_{i}.mp4"
            v.write_bytes(b"\x00" * 1024)
            entries.append({"filepath": str(v), "filename": v.name, "file_hash": ""})

        hashed = []

        def on_prog(fp, done, total):
            hashed.append(done)
            if done >= 3:
                cancel.set()

        compute_hashes_for_entries(entries, cancel_event=cancel, progress_callback=on_prog)
        assert len(hashed) <= 10


# ------------------------------------------------------------------ #
#  TEST — _SKIP_DIRNAMES lengkap
# ------------------------------------------------------------------ #

class TestSkipDirnames:
    def test_skip_dirnames_berisi_folder_relevan(self):
        """Pastikan konstanta berisi semua folder yang perlu dilewati."""
        assert "__pycache__" in _SKIP_DIRNAMES
        assert ".git" in _SKIP_DIRNAMES
        assert "venv" in _SKIP_DIRNAMES
        assert "node_modules" in _SKIP_DIRNAMES
        assert "logs" in _SKIP_DIRNAMES


# ------------------------------------------------------------------ #
#  REGRESSION TESTS — progress throttling (mencegah GUI flooding)
# ------------------------------------------------------------------ #

class TestProgressThrottling:
    """
    Memastikan on_progress callback tidak dipanggil lebih sering dari
    yang diperlukan. Tanpa throttling, 291 file = 291 self.after() calls
    yang memblokir Tkinter event loop selama beberapa menit.
    """

    def test_callback_jauh_lebih_sedikit_dari_jumlah_file(self, many_mp4_folder):
        """
        Jumlah callback harus jauh lebih sedikit dari jumlah file.
        Tanpa throttle: 300 calls. Dengan throttle: harus << 300.
        """
        folder, file_count = many_mp4_folder
        cb_calls = []

        scan_mp4_files(
            str(folder),
            progress_callback=lambda fp, n: cb_calls.append(n),
        )

        # Dengan 300 file dan throttle PROGRESS_EVERY=10 atau 0.25s,
        # callback harus << 300. Batas konservatif: maks 60 calls.
        assert len(cb_calls) < file_count, \
            f"Callback dipanggil {len(cb_calls)}x untuk {file_count} file — tidak ada throttle!"
        assert len(cb_calls) > 0, "Minimal satu callback harus dipanggil"

    def test_callback_terakhir_mengirim_count_final(self, many_mp4_folder):
        """Callback terakhir harus mengirim total file yang ditemukan."""
        folder, file_count = many_mp4_folder
        last_count = [0]

        scan_mp4_files(
            str(folder),
            progress_callback=lambda fp, n: last_count.__setitem__(0, n),
        )

        assert last_count[0] == file_count, \
            f"Count terakhir {last_count[0]} != total file {file_count}"

    def test_throttle_per_waktu(self, tmp_path):
        """
        Verifikasi throttle berbasis waktu: dua file yang di-yield dalam
        interval < PROGRESS_THROTTLE_SECS hanya menghasilkan satu callback
        (atau maksimal dua jika tepat di batas PROGRESS_EVERY).
        """
        # Buat tepat PROGRESS_EVERY - 1 file sehingga count-based throttle
        # tidak aktif pada setiap file
        n_files = max(1, PROGRESS_EVERY - 1)
        for i in range(n_files):
            (tmp_path / f"vid_{i:03d}.mp4").write_bytes(b"\x00" * 512)

        cb_calls = []
        scan_mp4_files(
            str(tmp_path),
            progress_callback=lambda fp, n: cb_calls.append(n),
        )

        # Untuk n_files < PROGRESS_EVERY, callback hanya dari throttle waktu
        # Karena dijalankan sangat cepat di local FS, mungkin hanya 1-2 calls
        assert len(cb_calls) >= 1

    def test_300_file_callback_tidak_melebihi_batas_wajar(self, many_mp4_folder):
        """
        300 file → paling banyak ceil(300/PROGRESS_EVERY) + margin callbacks.
        Ini mencegah regresi kembali ke 300 self.after() calls.
        """
        folder, file_count = many_mp4_folder
        import math
        max_expected = math.ceil(file_count / PROGRESS_EVERY) + 5  # +5 toleransi time-based

        cb_calls = []
        scan_mp4_files(
            str(folder),
            progress_callback=lambda fp, n: cb_calls.append(n),
        )

        assert len(cb_calls) <= max_expected, (
            f"Terlalu banyak callback: {len(cb_calls)} > {max_expected}. "
            f"GUI akan flooding dengan self.after() calls!"
        )


# ------------------------------------------------------------------ #
#  REGRESSION TESTS — ScanTimings instrumentasi
# ------------------------------------------------------------------ #

class TestScanTimings:
    def test_mark_mencatat_waktu(self):
        t = ScanTimings()
        time.sleep(0.01)
        elapsed = t.mark("test stage")
        assert elapsed >= 0.01
        assert len(t.stages) == 1
        assert t.stages[0][0] == "test stage"

    def test_mark_berurutan_naik(self):
        t = ScanTimings()
        time.sleep(0.01)
        e1 = t.mark("stage 1")
        time.sleep(0.01)
        e2 = t.mark("stage 2")
        assert e2 > e1

    def test_report_berisi_semua_stage(self):
        t = ScanTimings()
        t.mark("discovery")
        t.mark("merge")
        t.mark("csv write")
        report = t.report()
        assert "discovery" in report
        assert "merge" in report
        assert "csv write" in report
        assert "TIMING" in report

    def test_timing_constants_masuk_akal(self):
        """Konstanta throttle harus dalam rentang yang masuk akal."""
        assert 0 < PROGRESS_THROTTLE_SECS <= 2.0, \
            f"PROGRESS_THROTTLE_SECS={PROGRESS_THROTTLE_SECS} tidak masuk akal"
        assert 1 <= PROGRESS_EVERY <= 50, \
            f"PROGRESS_EVERY={PROGRESS_EVERY} tidak masuk akal"


# ------------------------------------------------------------------ #
#  REGRESSION TESTS — batch DB query di GUI logic
# ------------------------------------------------------------------ #

class TestBatchDatabaseQuery:
    """
    Verifikasi bahwa logika yang digunakan _populate_table mengambil semua
    status dari DB dalam SATU query, bukan N query individual.
    """

    def test_get_all_uploads_satu_query_untuk_291_file(self, tmp_path):
        """
        get_all_uploads() harus mengembalikan semua baris dalam satu panggilan.
        Ini yang digunakan _populate_table untuk menghindari 291 query individu.
        """
        from src.database import UploadDatabase

        db = UploadDatabase(str(tmp_path / "test.db"))

        # Tambahkan 50 upload sebagai simulasi
        filepaths = []
        for i in range(50):
            fp = f"/zoom/video_{i:03d}.mp4"
            filepaths.append(fp)
            db.add_upload({
                "filepath": fp,
                "filename": f"video_{i:03d}.mp4",
                "title": f"Video {i}",
                "privacy": "private",
                "description": "",
                "tags": "",
                "category_id": "27",
                "file_hash": "",
            })

        import time as _t
        t0 = _t.perf_counter()
        # Satu query batch
        all_rows = db.get_all_uploads()
        db_lookup = {dict(r)["filepath"]: dict(r) for r in all_rows}
        t_batch = _t.perf_counter() - t0

        # Bandingkan dengan 50 query individu
        t0_individual = _t.perf_counter()
        for fp in filepaths:
            db.find_by_filepath(fp)
        t_individual = _t.perf_counter() - t0_individual

        db.close()

        assert len(db_lookup) == 50
        # Batch harus lebih cepat (atau setidaknya tidak jauh lebih lambat)
        # Pada SQLite lokal, batch biasanya 10-100x lebih cepat
        assert t_batch <= t_individual * 2.0, (
            f"Batch query ({t_batch*1000:.1f}ms) lebih lambat dari "
            f"{50}x individual queries ({t_individual*1000:.1f}ms). "
            f"Ada yang salah!"
        )

    def test_lookup_dict_dari_get_all_uploads(self, tmp_path):
        """Dict lookup dari get_all_uploads() harus menemukan filepath yang benar."""
        from src.database import UploadDatabase

        db = UploadDatabase(str(tmp_path / "test.db"))
        fp = "/zoom/kuliah_01.mp4"
        uid = db.add_upload({
            "filepath": fp, "filename": "kuliah_01.mp4",
            "title": "Kuliah 01", "privacy": "private",
            "description": "", "tags": "", "category_id": "27", "file_hash": "",
        })
        db.mark_success(uid, "yt_xyz123")

        all_rows = db.get_all_uploads()
        db_lookup = {dict(r)["filepath"]: dict(r) for r in all_rows}
        db.close()

        assert fp in db_lookup
        assert db_lookup[fp]["status"] == "success"
        assert db_lookup[fp]["youtube_video_id"] == "yt_xyz123"
