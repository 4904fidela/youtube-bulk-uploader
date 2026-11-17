"""
test_preflight.py - Unit tests untuk modul preflight.
Semua panggilan YouTube API di-mock. Tidak ada upload nyata.
"""

import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.preflight import (
    check_file,
    check_auth,
    check_duplicate,
    run_preflight,
    PreflightResult,
)
from src.database import UploadDatabase


# ------------------------------------------------------------------ #
#  Fixtures
# ------------------------------------------------------------------ #

@pytest.fixture
def valid_video(tmp_path):
    """File MP4 valid dengan isi nyata (bukan zero-byte)."""
    f = tmp_path / "kuliah_matematika_01.mp4"
    f.write_bytes(b"\x00\x00\x00\x20ftyp" + b"\x00" * (1024 * 512))  # 512 KB
    return f


@pytest.fixture
def zero_byte_video(tmp_path):
    """File MP4 zero-byte."""
    f = tmp_path / "kosong.mp4"
    f.write_bytes(b"")
    return f


@pytest.fixture
def db(tmp_path):
    """Database SQLite sementara."""
    database = UploadDatabase(str(tmp_path / "test.db"))
    yield database
    database.close()


@pytest.fixture
def mock_youtube_service():
    """Mock YouTube service yang mengembalikan channel info valid."""
    service = MagicMock()
    service.channels().list().execute.return_value = {
        "items": [
            {
                "id": "UC_test_channel_id_12345",
                "snippet": {
                    "title": "Channel Kuliah Online",
                },
            }
        ]
    }
    return service


@pytest.fixture
def mock_youtube_no_channel():
    """Mock YouTube service tanpa channel terdaftar."""
    service = MagicMock()
    service.channels().list().execute.return_value = {"items": []}
    return service


@pytest.fixture
def sample_metadata(valid_video):
    return {
        "filepath": str(valid_video),
        "filename": valid_video.name,
        "title": "Kuliah Matematika Pertemuan 1",
        "description": "Deskripsi kuliah",
        "privacy": "private",
        "playlist_id": "",
        "tags": "kuliah,matematika",
        "category_id": "27",
        "file_hash": "",
    }


# ------------------------------------------------------------------ #
#  Tests: check_file
# ------------------------------------------------------------------ #

class TestCheckFile:
    def test_file_valid_ok(self, valid_video):
        result = check_file(str(valid_video))
        assert result["ok"] is True
        assert result["error"] is None
        assert result["size_bytes"] > 0
        assert result["size_mb"] > 0

    def test_file_tidak_ada(self, tmp_path):
        result = check_file(str(tmp_path / "tidak_ada.mp4"))
        assert result["ok"] is False
        assert "tidak ditemukan" in result["error"].lower()
        assert result["size_bytes"] == 0

    def test_file_zero_byte(self, zero_byte_video):
        result = check_file(str(zero_byte_video))
        assert result["ok"] is False
        assert "0 byte" in result["error"] or "kosong" in result["error"].lower()
        assert result["size_bytes"] == 0

    def test_path_adalah_folder(self, tmp_path):
        result = check_file(str(tmp_path))
        assert result["ok"] is False
        assert result["error"] is not None

    def test_ukuran_mb_dihitung_benar(self, tmp_path):
        # File 2 MB
        f = tmp_path / "test.mp4"
        f.write_bytes(b"\x00" * 2 * 1024 * 1024)
        result = check_file(str(f))
        assert result["ok"] is True
        assert abs(result["size_mb"] - 2.0) < 0.01

    def test_size_bytes_terisi_untuk_file_valid(self, valid_video):
        result = check_file(str(valid_video))
        assert result["size_bytes"] == valid_video.stat().st_size


# ------------------------------------------------------------------ #
#  Tests: check_auth
# ------------------------------------------------------------------ #

class TestCheckAuth:
    def test_auth_sukses_dengan_mock_channel(self, mock_youtube_service):
        result = check_auth(mock_youtube_service)
        assert result["ok"] is True
        assert result["error"] is None
        assert result["channel_id"] == "UC_test_channel_id_12345"
        assert result["channel_title"] == "Channel Kuliah Online"
        assert "UC_test_channel_id_12345" in result["channel_url"]

    def test_auth_gagal_tanpa_channel(self, mock_youtube_no_channel):
        result = check_auth(mock_youtube_no_channel)
        assert result["ok"] is False
        assert result["error"] is not None
        assert result["channel_id"] == ""
        assert result["channel_title"] == ""

    def test_auth_gagal_saat_api_error(self):
        service = MagicMock()
        service.channels().list().execute.side_effect = Exception("Network error")
        result = check_auth(service)
        assert result["ok"] is False
        assert result["error"] is not None
        assert result["channel_id"] == ""

    def test_url_channel_terbentuk_benar(self, mock_youtube_service):
        result = check_auth(mock_youtube_service)
        assert result["channel_url"] == "https://www.youtube.com/channel/UC_test_channel_id_12345"

    def test_channel_id_dan_title_diisi(self, mock_youtube_service):
        result = check_auth(mock_youtube_service)
        assert len(result["channel_id"]) > 0
        assert len(result["channel_title"]) > 0


# ------------------------------------------------------------------ #
#  Tests: check_duplicate
# ------------------------------------------------------------------ #

class TestCheckDuplicate:
    def test_bukan_duplikat_jika_belum_ada(self, db, valid_video):
        result = check_duplicate(str(valid_video), "", db)
        assert result["is_duplicate"] is False
        assert result["youtube_id"] == ""
        assert result["warning"] is None

    def test_duplikat_terdeteksi_via_filepath(self, db, valid_video):
        # Daftarkan sebagai sukses
        uid = db.add_upload({
            "filepath": str(valid_video),
            "filename": valid_video.name,
            "title": "Kuliah 1",
            "privacy": "private",
        })
        db.mark_success(uid, "yt_existing_vid_001")

        result = check_duplicate(str(valid_video), "", db)
        assert result["is_duplicate"] is True
        assert result["youtube_id"] == "yt_existing_vid_001"
        assert result["warning"] is not None
        assert "yt_existing_vid_001" in result["warning"]

    def test_duplikat_terdeteksi_via_hash(self, db, valid_video, tmp_path):
        # Upload dengan hash tertentu
        file_hash = "abcdef1234567890" * 4  # 64 hex chars
        uid = db.add_upload({
            "filepath": str(valid_video),
            "filename": valid_video.name,
            "file_hash": file_hash,
            "title": "Kuliah 1",
            "privacy": "private",
        })
        db.mark_success(uid, "yt_hash_match_002")

        # File beda tapi hash sama
        other_file = tmp_path / "copy.mp4"
        other_file.write_bytes(b"\x00" * 512)
        result = check_duplicate(str(other_file), file_hash, db)
        assert result["is_duplicate"] is True
        assert result["youtube_id"] == "yt_hash_match_002"

    def test_pending_bukan_duplikat(self, db, valid_video):
        # Status pending bukan dianggap duplikat
        db.add_upload({
            "filepath": str(valid_video),
            "filename": valid_video.name,
            "title": "Kuliah 1",
            "privacy": "private",
        })
        result = check_duplicate(str(valid_video), "", db)
        assert result["is_duplicate"] is False

    def test_failed_bukan_duplikat(self, db, valid_video):
        uid = db.add_upload({
            "filepath": str(valid_video),
            "filename": valid_video.name,
            "title": "Kuliah 1",
            "privacy": "private",
        })
        db.mark_failed(uid, "Network timeout")
        result = check_duplicate(str(valid_video), "", db)
        assert result["is_duplicate"] is False

    def test_database_none_aman(self, valid_video):
        # Tidak crash jika database None
        result = check_duplicate(str(valid_video), "", database=None)
        assert result["is_duplicate"] is False

    def test_warning_berisi_info_youtube_id(self, db, valid_video):
        uid = db.add_upload({
            "filepath": str(valid_video),
            "filename": valid_video.name,
            "title": "Kuliah 1",
            "privacy": "private",
        })
        db.mark_success(uid, "yt_warn_check_id")
        result = check_duplicate(str(valid_video), "", db)
        assert "yt_warn_check_id" in result["warning"]


# ------------------------------------------------------------------ #
#  Tests: run_preflight (integrasi semua checks)
# ------------------------------------------------------------------ #

class TestRunPreflight:
    def test_preflight_lengkap_lulus(
        self, sample_metadata, mock_youtube_service, db
    ):
        """Semua checks lulus untuk file valid dengan auth OK."""
        result = run_preflight(
            metadata=sample_metadata,
            youtube_service=mock_youtube_service,
            database=db,
            compute_hash=False,  # skip hash untuk tes cepat
        )
        assert result.ok is True
        assert len(result.errors) == 0
        assert result.channel_id == "UC_test_channel_id_12345"
        assert result.channel_title == "Channel Kuliah Online"
        assert result.auth_ok is True
        assert result.is_duplicate is False
        assert result.privacy == "private"

    def test_preflight_gagal_file_tidak_ada(
        self, tmp_path, mock_youtube_service, db
    ):
        metadata = {
            "filepath": str(tmp_path / "tidak_ada.mp4"),
            "filename": "tidak_ada.mp4",
            "title": "Video Tidak Ada",
        }
        result = run_preflight(
            metadata=metadata,
            youtube_service=mock_youtube_service,
            database=db,
            compute_hash=False,
        )
        assert result.ok is False
        assert len(result.errors) >= 1
        assert any("tidak ditemukan" in e.lower() for e in result.errors)

    def test_preflight_gagal_file_zero_byte(
        self, zero_byte_video, mock_youtube_service, db
    ):
        metadata = {
            "filepath": str(zero_byte_video),
            "filename": zero_byte_video.name,
            "title": "Video Kosong",
        }
        result = run_preflight(
            metadata=metadata,
            youtube_service=mock_youtube_service,
            database=db,
            compute_hash=False,
        )
        assert result.ok is False
        assert any("0 byte" in e or "kosong" in e.lower() for e in result.errors)

    def test_preflight_gagal_tanpa_service(self, sample_metadata, db):
        """Jika youtube_service None, harus ada error."""
        result = run_preflight(
            metadata=sample_metadata,
            youtube_service=None,
            database=db,
            compute_hash=False,
        )
        assert result.ok is False
        assert any("service" in e.lower() or "oauth" in e.lower() for e in result.errors)

    def test_preflight_gagal_auth_tanpa_channel(
        self, sample_metadata, mock_youtube_no_channel, db
    ):
        result = run_preflight(
            metadata=sample_metadata,
            youtube_service=mock_youtube_no_channel,
            database=db,
            compute_hash=False,
        )
        assert result.ok is False
        assert any("oauth" in e.lower() or "verifikasi" in e.lower() for e in result.errors)

    def test_preflight_privacy_selalu_private(
        self, sample_metadata, mock_youtube_service, db
    ):
        """Privacy harus selalu 'private', tidak peduli input."""
        sample_metadata["privacy"] = "public"
        result = run_preflight(
            metadata=sample_metadata,
            youtube_service=mock_youtube_service,
            database=db,
            compute_hash=False,
        )
        assert result.privacy == "private"

    def test_preflight_mendeteksi_duplikat_sebagai_warning(
        self, sample_metadata, mock_youtube_service, db
    ):
        """Duplikat menghasilkan warning tapi tidak memblokir (ok tetap True)."""
        # Daftarkan sebagai sukses
        uid = db.add_upload(sample_metadata)
        db.mark_success(uid, "yt_dup_123")

        result = run_preflight(
            metadata=sample_metadata,
            youtube_service=mock_youtube_service,
            database=db,
            compute_hash=False,
        )
        assert result.ok is True  # duplikat tidak memblokir
        assert result.is_duplicate is True
        assert len(result.warnings) >= 1
        assert result.duplicate_youtube_id == "yt_dup_123"

    def test_preflight_mengisi_ukuran_file(
        self, sample_metadata, mock_youtube_service, db
    ):
        result = run_preflight(
            metadata=sample_metadata,
            youtube_service=mock_youtube_service,
            database=db,
            compute_hash=False,
        )
        assert result.file_size_bytes > 0
        assert result.file_size_mb > 0

    def test_preflight_mengisi_filename_dan_title(
        self, sample_metadata, mock_youtube_service, db
    ):
        result = run_preflight(
            metadata=sample_metadata,
            youtube_service=mock_youtube_service,
            database=db,
            compute_hash=False,
        )
        assert result.filename == sample_metadata["filename"]
        assert result.title == sample_metadata["title"]

    def test_preflight_tanpa_database_aman(
        self, sample_metadata, mock_youtube_service
    ):
        """Harus berjalan tanpa crash walau database=None."""
        result = run_preflight(
            metadata=sample_metadata,
            youtube_service=mock_youtube_service,
            database=None,
            compute_hash=False,
        )
        assert result.ok is True
        assert result.is_duplicate is False

    def test_preflight_menghitung_hash_jika_diminta(
        self, sample_metadata, mock_youtube_service, db
    ):
        """compute_hash=True memanggil get_file_hash_cached dari preflight module."""
        with patch("src.preflight.get_file_hash_cached", return_value="a" * 64) as mock_hash:
            result = run_preflight(
                metadata=sample_metadata,
                youtube_service=mock_youtube_service,
                database=db,
                compute_hash=True,
            )
        # hash kosong di metadata → get_file_hash_cached dipanggil
        mock_hash.assert_called_once_with(sample_metadata["filepath"])
        assert result.ok is True


# ------------------------------------------------------------------ #
#  Tests: PreflightResult dataclass
# ------------------------------------------------------------------ #

class TestPreflightResult:
    def test_default_ok_false(self):
        r = PreflightResult()
        assert r.ok is False

    def test_default_privacy_private(self):
        r = PreflightResult()
        assert r.privacy == "private"

    def test_default_errors_kosong(self):
        r = PreflightResult()
        assert r.errors == []
        assert r.warnings == []

    def test_default_is_duplicate_false(self):
        r = PreflightResult()
        assert r.is_duplicate is False

    def test_default_auth_ok_false(self):
        r = PreflightResult()
        assert r.auth_ok is False

    def test_bisa_diisi_manual(self):
        r = PreflightResult(
            ok=True,
            filename="test.mp4",
            channel_id="UC_xyz",
            channel_title="My Channel",
            privacy="private",
        )
        assert r.ok is True
        assert r.filename == "test.mp4"
        assert r.channel_id == "UC_xyz"
        assert r.privacy == "private"
