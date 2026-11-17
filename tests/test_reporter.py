"""
test_reporter.py - Unit tests untuk modul reporter.
"""

import csv
import pytest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.reporter import generate_csv_report, generate_html_report, print_summary


# ------------------------------------------------------------------ #
#  Fixtures
# ------------------------------------------------------------------ #

@pytest.fixture
def sample_rows():
    """Simulasi data dari database (sebagai dict biasa)."""
    class FakeRow(dict):
        """Simulasi sqlite3.Row yang bisa diakses seperti dict."""
        pass

    rows = [
        FakeRow({
            "id": 1,
            "filename": "zoom_kuliah_01.mp4",
            "filepath": "/zoom/zoom_kuliah_01.mp4",
            "title": "Kuliah Matematika 01",
            "status": "success",
            "youtube_video_id": "yt_abc123",
            "privacy": "private",
            "retry_count": 0,
            "error_message": None,
            "created_at": "2024-01-01 09:00:00",
            "completed_at": "2024-01-01 09:30:00",
        }),
        FakeRow({
            "id": 2,
            "filename": "zoom_kuliah_02.mp4",
            "filepath": "/zoom/zoom_kuliah_02.mp4",
            "title": "Kuliah Matematika 02",
            "status": "failed",
            "youtube_video_id": None,
            "privacy": "private",
            "retry_count": 3,
            "error_message": "Network timeout setelah 10 retry",
            "created_at": "2024-01-01 10:00:00",
            "completed_at": "2024-01-01 10:05:00",
        }),
        FakeRow({
            "id": 3,
            "filename": "zoom_kuliah_03.mp4",
            "filepath": "/zoom/zoom_kuliah_03.mp4",
            "title": "Kuliah Matematika 03",
            "status": "uncertain",
            "youtube_video_id": None,
            "privacy": "private",
            "retry_count": 1,
            "error_message": "Upload selesai tapi tidak ada konfirmasi ID",
            "created_at": "2024-01-01 11:00:00",
            "completed_at": None,
        }),
        FakeRow({
            "id": 4,
            "filename": "zoom_kuliah_04.mp4",
            "filepath": "/zoom/zoom_kuliah_04.mp4",
            "title": "Kuliah Matematika 04",
            "status": "pending",
            "youtube_video_id": None,
            "privacy": "private",
            "retry_count": 0,
            "error_message": None,
            "created_at": "2024-01-01 12:00:00",
            "completed_at": None,
        }),
    ]
    return rows


@pytest.fixture
def sample_stats():
    return {
        "total": 4,
        "success": 1,
        "failed": 1,
        "uncertain": 1,
        "pending": 1,
        "skipped": 0,
    }


# ------------------------------------------------------------------ #
#  Tests: generate_csv_report
# ------------------------------------------------------------------ #

class TestGenerateCsvReport:
    def test_file_csv_dibuat(self, sample_rows, tmp_path):
        output = str(tmp_path / "laporan.csv")
        result = generate_csv_report(sample_rows, output)
        assert Path(result).exists()

    def test_csv_memiliki_header(self, sample_rows, tmp_path):
        output = str(tmp_path / "laporan.csv")
        generate_csv_report(sample_rows, output)
        with open(output, encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            header = next(reader)
        assert "filename" in header
        assert "status" in header
        assert "youtube_video_id" in header

    def test_csv_berisi_semua_baris(self, sample_rows, tmp_path):
        output = str(tmp_path / "laporan.csv")
        generate_csv_report(sample_rows, output)
        with open(output, encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 4

    def test_folder_dibuat_otomatis(self, sample_rows, tmp_path):
        output = str(tmp_path / "nested" / "dir" / "laporan.csv")
        generate_csv_report(sample_rows, output)
        assert Path(output).exists()

    def test_status_diterjemahkan_ke_label(self, sample_rows, tmp_path):
        output = str(tmp_path / "laporan.csv")
        generate_csv_report(sample_rows, output)
        with open(output, encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        statuses = [r["status"] for r in rows]
        # Status harus berupa label Indonesia, bukan kode mentah
        assert "✅ Berhasil" in statuses
        assert "❌ Gagal" in statuses


# ------------------------------------------------------------------ #
#  Tests: generate_html_report
# ------------------------------------------------------------------ #

class TestGenerateHtmlReport:
    def test_file_html_dibuat(self, sample_rows, sample_stats, tmp_path):
        output = str(tmp_path / "laporan.html")
        result = generate_html_report(sample_rows, output, sample_stats)
        assert Path(result).exists()

    def test_html_valid(self, sample_rows, sample_stats, tmp_path):
        output = str(tmp_path / "laporan.html")
        generate_html_report(sample_rows, output, sample_stats)
        with open(output, encoding="utf-8") as f:
            content = f.read()
        assert "<!DOCTYPE html>" in content
        assert "<html" in content
        assert "YouTube Bulk Uploader" in content

    def test_html_berisi_data(self, sample_rows, sample_stats, tmp_path):
        output = str(tmp_path / "laporan.html")
        generate_html_report(sample_rows, output, sample_stats)
        with open(output, encoding="utf-8") as f:
            content = f.read()
        assert "zoom_kuliah_01.mp4" in content
        assert "yt_abc123" in content
        assert "Kuliah Matematika 02" in content

    def test_html_berisi_statistik(self, sample_rows, sample_stats, tmp_path):
        output = str(tmp_path / "laporan.html")
        generate_html_report(sample_rows, output, sample_stats)
        with open(output, encoding="utf-8") as f:
            content = f.read()
        assert "4" in content  # total
        assert "1" in content  # success

    def test_tanpa_stats_tetap_berjalan(self, sample_rows, tmp_path):
        """Test bahwa generate_html_report bisa berjalan tanpa stats."""
        output = str(tmp_path / "laporan.html")
        result = generate_html_report(sample_rows, output, stats=None)
        assert Path(result).exists()

    def test_folder_dibuat_otomatis(self, sample_rows, sample_stats, tmp_path):
        output = str(tmp_path / "reports" / "nested" / "laporan.html")
        generate_html_report(sample_rows, output, sample_stats)
        assert Path(output).exists()

    def test_link_youtube_untuk_video_sukses(self, sample_rows, sample_stats, tmp_path):
        output = str(tmp_path / "laporan.html")
        generate_html_report(sample_rows, output, sample_stats)
        with open(output, encoding="utf-8") as f:
            content = f.read()
        assert "https://youtu.be/yt_abc123" in content


# ------------------------------------------------------------------ #
#  Tests: print_summary
# ------------------------------------------------------------------ #

class TestPrintSummary:
    def test_print_summary_tidak_error(self, capsys, sample_stats):
        """Test bahwa print_summary berjalan tanpa error."""
        print_summary(sample_stats)
        captured = capsys.readouterr()
        assert "RINGKASAN" in captured.out
        assert "4" in captured.out  # total

    def test_print_summary_stats_kosong(self, capsys):
        """Test dengan stats kosong."""
        print_summary({})
        captured = capsys.readouterr()
        assert "0" in captured.out
