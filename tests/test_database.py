"""
test_database.py - Unit tests untuk modul database SQLite.
"""

import pytest
import tempfile
import os
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.database import (
    UploadDatabase,
    STATUS_PENDING,
    STATUS_SUCCESS,
    STATUS_FAILED,
    STATUS_UNCERTAIN,
    STATUS_IN_PROGRESS,
    STATUS_SKIPPED,
)


# ------------------------------------------------------------------ #
#  Fixtures
# ------------------------------------------------------------------ #

@pytest.fixture
def db(tmp_path):
    """Buat database sementara untuk setiap test."""
    db_path = str(tmp_path / "test.db")
    database = UploadDatabase(db_path)
    yield database
    database.close()


@pytest.fixture
def sample_metadata():
    return {
        "filepath": "/test/video.mp4",
        "filename": "video.mp4",
        "file_hash": "abc123def456",
        "title": "Video Kuliah 01",
        "description": "Deskripsi video",
        "privacy": "private",
        "playlist_id": "",
        "tags": "kuliah,online",
        "category_id": "27",
    }


# ------------------------------------------------------------------ #
#  Tests: add_upload dan read
# ------------------------------------------------------------------ #

class TestAddUpload:
    def test_add_upload_kembalikan_id(self, db, sample_metadata):
        upload_id = db.add_upload(sample_metadata)
        assert isinstance(upload_id, int)
        assert upload_id > 0

    def test_status_awal_pending(self, db, sample_metadata):
        upload_id = db.add_upload(sample_metadata)
        row = db.find_by_filepath(sample_metadata["filepath"])
        assert row is not None
        assert row["status"] == STATUS_PENDING

    def test_find_by_filepath_ada(self, db, sample_metadata):
        db.add_upload(sample_metadata)
        row = db.find_by_filepath(sample_metadata["filepath"])
        assert row is not None
        assert row["title"] == "Video Kuliah 01"

    def test_find_by_filepath_tidak_ada(self, db):
        row = db.find_by_filepath("/tidak/ada.mp4")
        assert row is None

    def test_multiple_uploads(self, db):
        for i in range(5):
            db.add_upload({
                "filepath": f"/test/video_{i}.mp4",
                "filename": f"video_{i}.mp4",
                "title": f"Video {i}",
                "privacy": "private",
            })
        rows = db.get_all_uploads()
        assert len(rows) == 5


# ------------------------------------------------------------------ #
#  Tests: get_or_create_upload
# ------------------------------------------------------------------ #

class TestGetOrCreate:
    def test_buat_baru(self, db, sample_metadata):
        uid, is_new = db.get_or_create_upload(sample_metadata)
        assert is_new is True
        assert uid > 0

    def test_tidak_duplikasi_jika_sudah_ada(self, db, sample_metadata):
        uid1, is_new1 = db.get_or_create_upload(sample_metadata)
        uid2, is_new2 = db.get_or_create_upload(sample_metadata)
        assert uid1 == uid2
        assert is_new1 is True
        assert is_new2 is False


# ------------------------------------------------------------------ #
#  Tests: update_status
# ------------------------------------------------------------------ #

class TestUpdateStatus:
    def test_mark_success(self, db, sample_metadata):
        uid = db.add_upload(sample_metadata)
        db.mark_success(uid, "yt_abc123")
        row = db.find_by_filepath(sample_metadata["filepath"])
        assert row["status"] == STATUS_SUCCESS
        assert row["youtube_video_id"] == "yt_abc123"
        assert row["completed_at"] is not None

    def test_mark_failed(self, db, sample_metadata):
        uid = db.add_upload(sample_metadata)
        db.mark_failed(uid, "Network error")
        row = db.find_by_filepath(sample_metadata["filepath"])
        assert row["status"] == STATUS_FAILED
        assert "Network error" in row["error_message"]

    def test_mark_uncertain(self, db, sample_metadata):
        uid = db.add_upload(sample_metadata)
        db.mark_uncertain(uid, "Koneksi terputus")
        row = db.find_by_filepath(sample_metadata["filepath"])
        assert row["status"] == STATUS_UNCERTAIN

    def test_save_resume_uri(self, db, sample_metadata):
        uid = db.add_upload(sample_metadata)
        db.save_resume_uri(uid, "https://example.com/resume/session123", offset=1024000)
        row = db.find_by_filepath(sample_metadata["filepath"])
        assert row["resume_uri"] == "https://example.com/resume/session123"
        assert row["upload_offset"] == 1024000
        assert row["status"] == STATUS_IN_PROGRESS

    def test_increment_retry(self, db, sample_metadata):
        uid = db.add_upload(sample_metadata)
        db.increment_retry(uid)
        db.increment_retry(uid)
        row = db.find_by_filepath(sample_metadata["filepath"])
        assert row["retry_count"] == 2

    def test_reset_to_pending(self, db, sample_metadata):
        uid = db.add_upload(sample_metadata)
        db.mark_failed(uid, "Error")
        db.reset_to_pending(uid)
        row = db.find_by_filepath(sample_metadata["filepath"])
        assert row["status"] == STATUS_PENDING
        assert row["error_message"] is None
        assert row["resume_uri"] is None


# ------------------------------------------------------------------ #
#  Tests: get_pending_uploads
# ------------------------------------------------------------------ #

class TestGetPending:
    def test_ambil_semua_pending(self, db):
        for i in range(3):
            db.add_upload({
                "filepath": f"/test/video_{i}.mp4",
                "filename": f"video_{i}.mp4",
                "title": f"Video {i}",
                "privacy": "private",
            })
        pending = db.get_pending_uploads()
        assert len(pending) == 3

    def test_sukses_tidak_muncul_di_pending(self, db, sample_metadata):
        uid = db.add_upload(sample_metadata)
        db.mark_success(uid, "yt_id_123")
        pending = db.get_pending_uploads()
        assert len(pending) == 0

    def test_in_progress_muncul_di_pending(self, db, sample_metadata):
        uid = db.add_upload(sample_metadata)
        db.update_status(uid, STATUS_IN_PROGRESS)
        pending = db.get_pending_uploads()
        assert len(pending) == 1


# ------------------------------------------------------------------ #
#  Tests: is_duplicate
# ------------------------------------------------------------------ #

class TestIsDuplicate:
    def test_bukan_duplikat_jika_belum_ada(self, db):
        result = db.is_duplicate("/test/video.mp4", "hash123")
        assert result is False

    def test_bukan_duplikat_jika_status_pending(self, db, sample_metadata):
        db.add_upload(sample_metadata)
        result = db.is_duplicate(sample_metadata["filepath"])
        assert result is False  # pending bukan duplikat

    def test_duplikat_jika_sudah_sukses(self, db, sample_metadata):
        uid = db.add_upload(sample_metadata)
        db.mark_success(uid, "yt_id_xyz")
        result = db.is_duplicate(sample_metadata["filepath"])
        assert result is True

    def test_duplikat_berdasarkan_hash(self, db, sample_metadata):
        uid = db.add_upload(sample_metadata)
        db.mark_success(uid, "yt_id_xyz")
        # File berbeda tapi hash sama
        result = db.is_duplicate("/test/video_copy.mp4", sample_metadata["file_hash"])
        assert result is True


# ------------------------------------------------------------------ #
#  Tests: get_summary
# ------------------------------------------------------------------ #

class TestGetSummary:
    def test_summary_kosong(self, db):
        stats = db.get_summary()
        assert stats.get("total", 0) == 0

    def test_summary_dengan_berbagai_status(self, db):
        m = {"filepath": "", "filename": "", "title": "", "privacy": "private"}

        for i in range(3):
            m2 = {**m, "filepath": f"/f/{i}.mp4", "filename": f"{i}.mp4"}
            uid = db.add_upload(m2)
            if i == 0:
                db.mark_success(uid, f"yt_{i}")
            elif i == 1:
                db.mark_failed(uid, "err")

        stats = db.get_summary()
        assert stats["success"] == 1
        assert stats["failed"] == 1
        assert stats["pending"] == 1
        assert stats["total"] == 3


# ------------------------------------------------------------------ #
#  Tests: find_by_hash
# ------------------------------------------------------------------ #

class TestFindByHash:
    def test_tidak_ada_jika_belum_sukses(self, db, sample_metadata):
        db.add_upload(sample_metadata)
        result = db.find_by_hash(sample_metadata["file_hash"])
        assert result is None  # status masih pending

    def test_ditemukan_setelah_sukses(self, db, sample_metadata):
        uid = db.add_upload(sample_metadata)
        db.mark_success(uid, "yt_123")
        result = db.find_by_hash(sample_metadata["file_hash"])
        assert result is not None
        assert result["youtube_video_id"] == "yt_123"

    def test_hash_kosong_kembalikan_none(self, db):
        result = db.find_by_hash("")
        assert result is None
