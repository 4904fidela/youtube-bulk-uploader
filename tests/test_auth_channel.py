"""
test_auth_channel.py - Unit tests untuk fungsi get_channel_info() di src/auth.py.
Semua panggilan YouTube API di-mock. Tidak ada koneksi nyata ke Google.
"""

import pytest
from unittest.mock import MagicMock
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.auth import get_channel_info, AuthError


# ------------------------------------------------------------------ #
#  Helpers
# ------------------------------------------------------------------ #

def make_channel_service(channel_id="UC_abc123", title="Kuliah Online"):
    """Buat mock YouTube service dengan channel valid."""
    service = MagicMock()
    service.channels().list().execute.return_value = {
        "items": [
            {
                "id": channel_id,
                "snippet": {"title": title},
            }
        ]
    }
    return service


def make_empty_channel_service():
    """Mock service tanpa channel terdaftar."""
    service = MagicMock()
    service.channels().list().execute.return_value = {"items": []}
    return service


def make_error_service(exc=Exception("API error")):
    """Mock service yang melempar exception."""
    service = MagicMock()
    service.channels().list().execute.side_effect = exc
    return service


# ------------------------------------------------------------------ #
#  Tests: get_channel_info
# ------------------------------------------------------------------ #

class TestGetChannelInfo:
    def test_mengembalikan_channel_id(self):
        service = make_channel_service(channel_id="UC_xyz789")
        info = get_channel_info(service)
        assert info["channel_id"] == "UC_xyz789"

    def test_mengembalikan_channel_title(self):
        service = make_channel_service(title="Matematika Dasar")
        info = get_channel_info(service)
        assert info["channel_title"] == "Matematika Dasar"

    def test_mengembalikan_channel_url(self):
        service = make_channel_service(channel_id="UC_url_test")
        info = get_channel_info(service)
        assert info["channel_url"] == "https://www.youtube.com/channel/UC_url_test"

    def test_url_berisi_channel_id(self):
        channel_id = "UC_full_id_12345678"
        service = make_channel_service(channel_id=channel_id)
        info = get_channel_info(service)
        assert channel_id in info["channel_url"]

    def test_tanpa_channel_raise_auth_error(self):
        service = make_empty_channel_service()
        with pytest.raises(AuthError, match="channel"):
            get_channel_info(service)

    def test_api_error_raise_auth_error(self):
        service = make_error_service(Exception("quota exceeded"))
        with pytest.raises(AuthError):
            get_channel_info(service)

    def test_dict_memiliki_semua_kunci(self):
        service = make_channel_service()
        info = get_channel_info(service)
        assert "channel_id" in info
        assert "channel_title" in info
        assert "channel_url" in info

    def test_channel_id_tidak_kosong(self):
        service = make_channel_service(channel_id="UC_nonempty")
        info = get_channel_info(service)
        assert len(info["channel_id"]) > 0

    def test_channel_title_tidak_kosong(self):
        service = make_channel_service(title="Nama Channel Tidak Kosong")
        info = get_channel_info(service)
        assert len(info["channel_title"]) > 0

    def test_memanggil_channels_list_dengan_mine_true(self):
        """Verifikasi bahwa API dipanggil dengan parameter mine=True."""
        service = make_channel_service()
        get_channel_info(service)
        service.channels().list.assert_called_with(
            part="snippet,id",
            mine=True,
            maxResults=1,
        )

    def test_items_pertama_yang_digunakan(self):
        """Hanya item pertama yang dipakai meski ada banyak."""
        service = MagicMock()
        service.channels().list().execute.return_value = {
            "items": [
                {"id": "UC_first", "snippet": {"title": "Pertama"}},
                {"id": "UC_second", "snippet": {"title": "Kedua"}},
            ]
        }
        info = get_channel_info(service)
        assert info["channel_id"] == "UC_first"
        assert info["channel_title"] == "Pertama"

    def test_auth_error_diteruskan_tanpa_wrap(self):
        """AuthError yang dilempar dari dalam harus diteruskan langsung."""
        service = MagicMock()
        service.channels().list().execute.return_value = {"items": []}
        # AuthError dilempar secara internal karena items kosong
        with pytest.raises(AuthError):
            get_channel_info(service)
