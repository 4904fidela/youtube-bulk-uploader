"""
test_uploader.py - Unit tests untuk modul uploader.
Semua panggilan Google API di-mock. TIDAK ada upload nyata.
"""

import threading
import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.uploader import (
    YouTubeUploader,
    BulkUploadManager,
    UploadError,
    UncertainUploadError,
    QuotaExceededError,
    UploadCancelled,
    _exponential_backoff,
)
from src.database import UploadDatabase, STATUS_SUCCESS, STATUS_FAILED, STATUS_UNCERTAIN


# ------------------------------------------------------------------ #
#  Fixtures
# ------------------------------------------------------------------ #

@pytest.fixture
def mock_youtube():
    """Mock lengkap untuk YouTube API service."""
    service = MagicMock()
    return service


@pytest.fixture
def db(tmp_path):
    """Database SQLite sementara."""
    database = UploadDatabase(str(tmp_path / "test.db"))
    yield database
    database.close()


@pytest.fixture
def sample_video(tmp_path):
    """Buat file video MP4 dummy."""
    video_path = tmp_path / "zoom_recording.mp4"
    video_path.write_bytes(b"\x00" * 1024 * 1024)  # 1 MB dummy
    return video_path


@pytest.fixture
def sample_metadata(sample_video):
    return {
        "filepath": str(sample_video),
        "filename": sample_video.name,
        "title": "Kuliah Online Pertemuan 1",
        "description": "Rekaman kuliah pertemuan pertama",
        "privacy": "private",
        "playlist_id": "",
        "tags": "kuliah,online,zoom",
        "category_id": "27",
        "resume_uri": None,
        "file_hash": "",
    }


# ------------------------------------------------------------------ #
#  Helper: buat mock request yang sukses
# ------------------------------------------------------------------ #

def make_mock_request(video_id="yt_vid_abc123"):
    """Buat mock request yang mensimulasikan upload sukses."""
    request = MagicMock()

    # Simulasi: chunk pertama belum selesai, chunk kedua selesai
    call_count = [0]

    def next_chunk_side_effect():
        call_count[0] += 1
        if call_count[0] == 1:
            # Progress 50%
            status = MagicMock()
            status.resumable_progress = 512 * 1024
            return status, None
        else:
            # Selesai
            response = {"id": video_id, "kind": "youtube#video"}
            return None, response

    request.next_chunk.side_effect = next_chunk_side_effect
    return request


# ------------------------------------------------------------------ #
#  Tests: _exponential_backoff
# ------------------------------------------------------------------ #

class TestExponentialBackoff:
    def test_meningkat_dengan_attempt(self):
        d1 = _exponential_backoff(1, base_delay=2.0)
        d2 = _exponential_backoff(2, base_delay=2.0)
        d3 = _exponential_backoff(3, base_delay=2.0)
        assert d1 < d2 < d3

    def test_tidak_melebihi_max_delay(self):
        for attempt in range(1, 20):
            delay = _exponential_backoff(attempt, max_delay=10.0)
            assert delay <= 10.0 * 1.2  # toleransi jitter 20%

    def test_delay_positif(self):
        for attempt in range(1, 10):
            assert _exponential_backoff(attempt) > 0


# ------------------------------------------------------------------ #
#  Tests: YouTubeUploader._build_request_body
# ------------------------------------------------------------------ #

class TestBuildRequestBody:
    def setup_method(self):
        self.uploader = YouTubeUploader(youtube_service=MagicMock())

    def test_body_berisi_snippet_dan_status(self, sample_metadata):
        body = self.uploader._build_request_body(sample_metadata)
        assert "snippet" in body
        assert "status" in body

    def test_judul_maks_100_karakter(self):
        metadata = {
            "title": "X" * 150,
            "description": "",
            "privacy": "private",
            "tags": "",
            "category_id": "27",
        }
        body = self.uploader._build_request_body(metadata)
        assert len(body["snippet"]["title"]) <= 100

    def test_privacy_masuk_ke_status(self, sample_metadata):
        body = self.uploader._build_request_body(sample_metadata)
        assert body["status"]["privacyStatus"] == "private"

    def test_privacy_default_private(self):
        metadata = {
            "title": "Test",
            "description": "",
            "privacy": "",
            "tags": "",
            "category_id": "27",
        }
        body = self.uploader._build_request_body(metadata)
        assert body["status"]["privacyStatus"] == "private"

    def test_tags_dipisah_koma(self):
        metadata = {
            "title": "Test",
            "description": "",
            "privacy": "private",
            "tags": "tag1, tag2, tag3",
            "category_id": "27",
        }
        body = self.uploader._build_request_body(metadata)
        assert "tag1" in body["snippet"]["tags"]
        assert "tag2" in body["snippet"]["tags"]
        assert len(body["snippet"]["tags"]) == 3

    def test_category_id_invalid_fallback_27(self):
        metadata = {
            "title": "Test",
            "description": "",
            "privacy": "private",
            "tags": "",
            "category_id": "BUKAN_ANGKA",
        }
        body = self.uploader._build_request_body(metadata)
        assert body["snippet"]["categoryId"] == "27"


# ------------------------------------------------------------------ #
#  Tests: YouTubeUploader.upload_video (dengan mock)
# ------------------------------------------------------------------ #

class TestUploadVideo:
    def test_upload_sukses(self, mock_youtube, sample_metadata, sample_video):
        """Test upload sukses mengembalikan video ID."""
        with patch("src.uploader.GOOGLE_LIBS_AVAILABLE", True), \
             patch("src.uploader.MediaFileUpload") as MockMedia:

            mock_request = make_mock_request("yt_test_id_001")
            mock_youtube.videos().insert.return_value = mock_request

            uploader = YouTubeUploader(youtube_service=mock_youtube)
            video_id = uploader.upload_video(1, sample_metadata)

            assert video_id == "yt_test_id_001"

    def test_upload_file_tidak_ada_raise_error(self, mock_youtube, tmp_path):
        """Test bahwa upload file yang tidak ada melempar UploadError."""
        metadata = {
            "filepath": str(tmp_path / "tidak_ada.mp4"),
            "filename": "tidak_ada.mp4",
            "title": "Test",
            "description": "",
            "privacy": "private",
            "tags": "",
            "category_id": "27",
        }
        with patch("src.uploader.GOOGLE_LIBS_AVAILABLE", True):
            uploader = YouTubeUploader(youtube_service=mock_youtube)
            with pytest.raises(UploadError, match="File tidak ditemukan"):
                uploader.upload_video(1, metadata)

    def test_library_tidak_ada_raise_error(self, mock_youtube, sample_metadata):
        """Test error jika library Google tidak terinstall."""
        with patch("src.uploader.GOOGLE_LIBS_AVAILABLE", False):
            uploader = YouTubeUploader(youtube_service=mock_youtube)
            with pytest.raises(UploadError, match="Library"):
                uploader.upload_video(1, sample_metadata)

    def test_upload_bisa_dibatalkan(self, mock_youtube, sample_metadata):
        """Test bahwa upload bisa dibatalkan via cancel_event."""
        cancel_event = threading.Event()
        cancel_event.set()  # Set sebelum upload dimulai

        with patch("src.uploader.GOOGLE_LIBS_AVAILABLE", True), \
             patch("src.uploader.MediaFileUpload"):
            uploader = YouTubeUploader(
                youtube_service=mock_youtube,
                cancel_event=cancel_event,
            )
            with pytest.raises(UploadCancelled):
                uploader.upload_video(1, sample_metadata)

    def test_progress_callback_dipanggil(self, mock_youtube, sample_metadata):
        """Test bahwa progress callback dipanggil saat upload."""
        progress_calls = []

        def on_progress(uid, sent, total, pct):
            progress_calls.append((uid, sent, total, pct))

        with patch("src.uploader.GOOGLE_LIBS_AVAILABLE", True), \
             patch("src.uploader.MediaFileUpload"):
            mock_request = make_mock_request()
            mock_youtube.videos().insert.return_value = mock_request

            uploader = YouTubeUploader(
                youtube_service=mock_youtube,
                progress_callback=on_progress,
            )
            uploader.upload_video(1, sample_metadata)

        assert len(progress_calls) > 0

    def test_response_tanpa_id_raise_uncertain(self, mock_youtube, sample_metadata):
        """Test bahwa response tanpa video ID melempar UncertainUploadError."""
        with patch("src.uploader.GOOGLE_LIBS_AVAILABLE", True), \
             patch("src.uploader.MediaFileUpload"):

            request = MagicMock()
            request.next_chunk.return_value = (None, {"kind": "youtube#video"})  # tanpa 'id'
            mock_youtube.videos().insert.return_value = request

            uploader = YouTubeUploader(youtube_service=mock_youtube)
            with pytest.raises(UncertainUploadError):
                uploader.upload_video(1, sample_metadata)


# ------------------------------------------------------------------ #
#  Tests: BulkUploadManager
# ------------------------------------------------------------------ #

class TestBulkUploadManager:
    def test_proses_antrian_sukses(self, mock_youtube, db, sample_metadata, sample_video):
        """Test proses antrian dengan satu upload sukses."""
        status_messages = []

        with patch("src.uploader.GOOGLE_LIBS_AVAILABLE", True), \
             patch("src.uploader.MediaFileUpload"):

            mock_request = make_mock_request("yt_bulk_001")
            mock_youtube.videos().insert.return_value = mock_request

            manager = BulkUploadManager(
                youtube_service=mock_youtube,
                database=db,
                status_callback=lambda uid, msg: status_messages.append(msg),
            )
            stats = manager.process_queue([sample_metadata])

        assert stats["success"] == 1
        assert stats["failed"] == 0

    def test_skip_duplikat_yang_sudah_sukses(self, mock_youtube, db, sample_metadata):
        """Test bahwa file yang sudah sukses dilewati."""
        # Tambahkan ke db sebagai sukses
        uid = db.add_upload(sample_metadata)
        db.mark_success(uid, "yt_existing_id")

        with patch("src.uploader.GOOGLE_LIBS_AVAILABLE", True):
            manager = BulkUploadManager(
                youtube_service=mock_youtube,
                database=db,
            )
            stats = manager.process_queue([sample_metadata])

        # Tidak ada yang diupload (sudah ada di db sebagai sukses)
        assert stats["success"] == 0

    def test_cancel_menghentikan_antrian(self, mock_youtube, db, tmp_path):
        """Test bahwa cancel_event menghentikan antrian."""
        cancel_event = threading.Event()

        entries = []
        for i in range(5):
            video = tmp_path / f"video_{i}.mp4"
            video.write_bytes(b"\x00" * 1024)
            entries.append({
                "filepath": str(video),
                "filename": video.name,
                "title": f"Video {i}",
                "description": "",
                "privacy": "private",
                "tags": "",
                "category_id": "27",
                "file_hash": "",
            })

        def mock_upload(*args, **kwargs):
            cancel_event.set()  # Batalkan setelah upload pertama dimulai
            raise UploadCancelled("Dibatalkan")

        with patch("src.uploader.GOOGLE_LIBS_AVAILABLE", True), \
             patch("src.uploader.MediaFileUpload"):

            manager = BulkUploadManager(
                youtube_service=mock_youtube,
                database=db,
                cancel_event=cancel_event,
            )
            manager.uploader.upload_video = mock_upload
            stats = manager.process_queue(entries)

        assert stats["cancelled"] > 0

    def test_gagal_tidak_menghentikan_upload_lain(self, mock_youtube, db, tmp_path):
        """Test bahwa satu upload gagal tidak menghentikan upload berikutnya."""
        entries = []
        for i in range(3):
            video = tmp_path / f"video_{i}.mp4"
            video.write_bytes(b"\x00" * 1024)
            entries.append({
                "filepath": str(video),
                "filename": video.name,
                "title": f"Video {i}",
                "description": "",
                "privacy": "private",
                "tags": "",
                "category_id": "27",
                "file_hash": "",
            })

        call_count = [0]

        def mock_upload(upload_id, metadata, resume_uri=None):
            call_count[0] += 1
            if call_count[0] == 1:
                raise UploadError("Gagal upload pertama")
            return f"yt_video_{call_count[0]}"

        with patch("src.uploader.GOOGLE_LIBS_AVAILABLE", True):
            manager = BulkUploadManager(
                youtube_service=mock_youtube,
                database=db,
            )
            manager.uploader.upload_video = mock_upload
            stats = manager.process_queue(entries)

        assert stats["failed"] == 1
        assert stats["success"] == 2


# ------------------------------------------------------------------ #
#  Tests: add_to_playlist
# ------------------------------------------------------------------ #

class TestAddToPlaylist:
    def test_tambah_ke_playlist_sukses(self, mock_youtube):
        mock_youtube.playlistItems().insert().execute.return_value = {}
        uploader = YouTubeUploader(youtube_service=mock_youtube)
        result = uploader.add_to_playlist("yt_vid_123", "PL_playlist_id")
        assert result is True

    def test_playlist_id_kosong_kembalikan_false(self, mock_youtube):
        uploader = YouTubeUploader(youtube_service=mock_youtube)
        result = uploader.add_to_playlist("yt_vid_123", "")
        assert result is False
        mock_youtube.playlistItems().insert.assert_not_called()

    def test_error_playlist_kembalikan_false(self, mock_youtube):
        from src.uploader import HttpError as UploaderHttpError
        # Simulasikan HttpError
        mock_response = MagicMock()
        mock_response.status = 404
        mock_youtube.playlistItems().insert().execute.side_effect = \
            UploaderHttpError(resp=mock_response, content=b"Not found")

        uploader = YouTubeUploader(youtube_service=mock_youtube)
        result = uploader.add_to_playlist("yt_vid_123", "PL_invalid")
        assert result is False


# ------------------------------------------------------------------ #
#  Tests: BulkUploadManager override_privacy dan batch_size
# ------------------------------------------------------------------ #

class TestOverridePrivacy:
    """
    Memastikan override_privacy BENAR-BENAR dikirim ke YouTube API,
    bukan hanya disimpan di GUI atau metadata.
    """

    @pytest.fixture
    def db_with_pending(self, tmp_path):
        """Database dengan 3 video pending."""
        database = UploadDatabase(str(tmp_path / "test.db"))
        for i in range(3):
            v = tmp_path / f"vid_{i}.mp4"
            v.write_bytes(b"\x00" * 512)
            database.add_upload({
                "filepath": str(v),
                "filename": v.name,
                "title": f"Video {i}",
                "description": "",
                "privacy": "private",   # CSV mengatakan private
                "tags": "",
                "category_id": "27",
                "file_hash": "",
            })
        yield database
        database.close()

    def _captured_privacy_from_body(self, mock_youtube) -> list:
        """Ambil semua nilai privacyStatus dari panggilan videos().insert()."""
        results = []
        for call in mock_youtube.videos().insert.call_args_list:
            body = call.kwargs.get("body") or (call.args[1] if len(call.args) > 1 else {})
            if isinstance(body, dict):
                privacy = body.get("status", {}).get("privacyStatus", "")
                if privacy:
                    results.append(privacy)
        return results

    def test_override_unlisted_dikirim_ke_api(self, mock_youtube, db_with_pending, tmp_path):
        """override_privacy='unlisted' harus masuk ke body YouTube API."""
        bodies_sent = []

        def capture_insert(**kwargs):
            bodies_sent.append(kwargs.get("body", {}))
            return make_mock_request(f"yt_id_{len(bodies_sent)}")

        with patch("src.uploader.GOOGLE_LIBS_AVAILABLE", True), \
             patch("src.uploader.MediaFileUpload"):
            mock_youtube.videos().insert.side_effect = capture_insert

            manager = BulkUploadManager(
                youtube_service=mock_youtube,
                database=db_with_pending,
            )
            manager.process_queue(override_privacy="unlisted")

        privacy_values = [b.get("status", {}).get("privacyStatus") for b in bodies_sent]
        assert all(p == "unlisted" for p in privacy_values), \
            f"Semua upload harus 'unlisted', dapat: {privacy_values}"

    def test_override_private_dikirim_ke_api(self, mock_youtube, db_with_pending):
        """override_privacy='private' harus masuk ke body YouTube API."""
        bodies_sent = []

        def capture_insert(**kwargs):
            bodies_sent.append(kwargs.get("body", {}))
            return make_mock_request(f"yt_id_{len(bodies_sent)}")

        with patch("src.uploader.GOOGLE_LIBS_AVAILABLE", True), \
             patch("src.uploader.MediaFileUpload"):
            mock_youtube.videos().insert.side_effect = capture_insert

            manager = BulkUploadManager(
                youtube_service=mock_youtube,
                database=db_with_pending,
            )
            manager.process_queue(override_privacy="private")

        privacy_values = [b.get("status", {}).get("privacyStatus") for b in bodies_sent]
        assert all(p == "private" for p in privacy_values), \
            f"Semua upload harus 'private', dapat: {privacy_values}"

    def test_tanpa_override_pakai_privacy_dari_metadata(self, mock_youtube, tmp_path):
        """Tanpa override, privacy dari metadata yang dipakai."""
        db = UploadDatabase(str(tmp_path / "test.db"))
        v = tmp_path / "vid.mp4"
        v.write_bytes(b"\x00" * 512)
        db.add_upload({
            "filepath": str(v),
            "filename": v.name,
            "title": "Video",
            "description": "",
            "privacy": "unlisted",   # sudah unlisted di metadata
            "tags": "",
            "category_id": "27",
            "file_hash": "",
        })

        bodies_sent = []

        def capture_insert(**kwargs):
            bodies_sent.append(kwargs.get("body", {}))
            return make_mock_request("yt_no_override")

        with patch("src.uploader.GOOGLE_LIBS_AVAILABLE", True), \
             patch("src.uploader.MediaFileUpload"):
            mock_youtube.videos().insert.side_effect = capture_insert
            manager = BulkUploadManager(youtube_service=mock_youtube, database=db)
            manager.process_queue()   # tanpa override_privacy

        db.close()
        assert bodies_sent[0].get("status", {}).get("privacyStatus") == "unlisted"

    def test_override_invalid_diabaikan(self, mock_youtube, tmp_path):
        """override_privacy tidak valid diabaikan, metadata dipakai."""
        db = UploadDatabase(str(tmp_path / "test.db"))
        v = tmp_path / "vid.mp4"
        v.write_bytes(b"\x00" * 512)
        db.add_upload({
            "filepath": str(v),
            "filename": v.name,
            "title": "Video",
            "privacy": "private",
            "description": "",
            "tags": "",
            "category_id": "27",
            "file_hash": "",
        })

        bodies_sent = []

        def capture_insert(**kwargs):
            bodies_sent.append(kwargs.get("body", {}))
            return make_mock_request("yt_invalid_override")

        with patch("src.uploader.GOOGLE_LIBS_AVAILABLE", True), \
             patch("src.uploader.MediaFileUpload"):
            mock_youtube.videos().insert.side_effect = capture_insert
            manager = BulkUploadManager(youtube_service=mock_youtube, database=db)
            manager.process_queue(override_privacy="TIDAK_VALID")

        db.close()
        # override tidak valid → metadata asli dipakai → "private"
        assert bodies_sent[0].get("status", {}).get("privacyStatus") == "private"

    def test_override_none_tidak_mengubah_metadata(self, mock_youtube, tmp_path):
        """override_privacy=None tidak mengubah privacy apapun."""
        db = UploadDatabase(str(tmp_path / "test.db"))
        v = tmp_path / "vid.mp4"
        v.write_bytes(b"\x00" * 512)
        db.add_upload({
            "filepath": str(v),
            "filename": v.name,
            "title": "Video",
            "privacy": "unlisted",
            "description": "",
            "tags": "",
            "category_id": "27",
            "file_hash": "",
        })

        bodies_sent = []

        def capture_insert(**kwargs):
            bodies_sent.append(kwargs.get("body", {}))
            return make_mock_request("yt_none_override")

        with patch("src.uploader.GOOGLE_LIBS_AVAILABLE", True), \
             patch("src.uploader.MediaFileUpload"):
            mock_youtube.videos().insert.side_effect = capture_insert
            manager = BulkUploadManager(youtube_service=mock_youtube, database=db)
            manager.process_queue(override_privacy=None)

        db.close()
        assert bodies_sent[0].get("status", {}).get("privacyStatus") == "unlisted"


class TestBatchSize:
    """Memastikan batch_size membatasi jumlah video yang diproses."""

    @pytest.fixture
    def db_5_pending(self, tmp_path):
        """Database dengan 5 video pending."""
        database = UploadDatabase(str(tmp_path / "batch.db"))
        for i in range(5):
            v = tmp_path / f"batch_{i}.mp4"
            v.write_bytes(b"\x00" * 256)
            database.add_upload({
                "filepath": str(v),
                "filename": v.name,
                "title": f"Batch Video {i}",
                "privacy": "private",
                "description": "",
                "tags": "",
                "category_id": "27",
                "file_hash": "",
            })
        yield database
        database.close()

    def test_batch_size_1_memproses_1_video(self, mock_youtube, db_5_pending):
        with patch("src.uploader.GOOGLE_LIBS_AVAILABLE", True), \
             patch("src.uploader.MediaFileUpload"):
            mock_youtube.videos().insert.return_value = make_mock_request("yt_b1")
            manager = BulkUploadManager(youtube_service=mock_youtube, database=db_5_pending)
            stats = manager.process_queue(batch_size=1)
        assert stats["success"] == 1

    def test_batch_size_3_memproses_3_video(self, mock_youtube, db_5_pending):
        call_n = [0]

        def make_req(**kwargs):
            call_n[0] += 1
            return make_mock_request(f"yt_b3_{call_n[0]}")

        with patch("src.uploader.GOOGLE_LIBS_AVAILABLE", True), \
             patch("src.uploader.MediaFileUpload"):
            mock_youtube.videos().insert.side_effect = make_req
            manager = BulkUploadManager(youtube_service=mock_youtube, database=db_5_pending)
            stats = manager.process_queue(batch_size=3)
        assert stats["success"] == 3

    def test_batch_size_none_memproses_semua(self, mock_youtube, db_5_pending):
        call_n = [0]

        def make_req(**kwargs):
            call_n[0] += 1
            return make_mock_request(f"yt_all_{call_n[0]}")

        with patch("src.uploader.GOOGLE_LIBS_AVAILABLE", True), \
             patch("src.uploader.MediaFileUpload"):
            mock_youtube.videos().insert.side_effect = make_req
            manager = BulkUploadManager(youtube_service=mock_youtube, database=db_5_pending)
            stats = manager.process_queue(batch_size=None)
        assert stats["success"] == 5

    def test_batch_size_lebih_besar_dari_pending(self, mock_youtube, db_5_pending):
        """batch_size lebih besar dari yang pending: semua diproses."""
        call_n = [0]

        def make_req(**kwargs):
            call_n[0] += 1
            return make_mock_request(f"yt_big_{call_n[0]}")

        with patch("src.uploader.GOOGLE_LIBS_AVAILABLE", True), \
             patch("src.uploader.MediaFileUpload"):
            mock_youtube.videos().insert.side_effect = make_req
            manager = BulkUploadManager(youtube_service=mock_youtube, database=db_5_pending)
            stats = manager.process_queue(batch_size=100)
        assert stats["success"] == 5

    def test_batch_size_0_memproses_semua(self, mock_youtube, db_5_pending):
        """batch_size=0 diperlakukan sebagai 'semua'."""
        call_n = [0]

        def make_req(**kwargs):
            call_n[0] += 1
            return make_mock_request(f"yt_z_{call_n[0]}")

        with patch("src.uploader.GOOGLE_LIBS_AVAILABLE", True), \
             patch("src.uploader.MediaFileUpload"):
            mock_youtube.videos().insert.side_effect = make_req
            manager = BulkUploadManager(youtube_service=mock_youtube, database=db_5_pending)
            stats = manager.process_queue(batch_size=0)
        assert stats["success"] == 5
