"""
auth.py - Autentikasi OAuth 2.0 untuk YouTube Data API v3.
Credentials TIDAK pernah di-hardcode. Disimpan di client_secret.json
yang diisi oleh pengguna sendiri dari Google Cloud Console.
"""

import os
import json
import logging
import pickle
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Nama file untuk menyimpan token OAuth (dienkripsi oleh library Google)
TOKEN_FILE = "token.pickle"
# Nama file client secret yang harus disediakan pengguna
CLIENT_SECRET_FILE = "client_secret.json"

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
]

# ------------------------------------------------------------------ #
#  Import opsional — tangkap error jika library belum terinstall
# ------------------------------------------------------------------ #
try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    GOOGLE_LIBS_AVAILABLE = True
except ImportError:
    GOOGLE_LIBS_AVAILABLE = False
    logger.warning(
        "Library Google Auth tidak ditemukan. "
        "Jalankan: pip install google-auth google-auth-oauthlib"
    )


class AuthError(Exception):
    """Error autentikasi OAuth."""
    pass


def get_client_secret_path(base_dir: Optional[str] = None) -> Path:
    """Kembalikan path ke file client_secret.json."""
    if base_dir:
        return Path(base_dir) / CLIENT_SECRET_FILE
    # Cari di direktori kerja, lalu di direktori skrip
    cwd_path = Path.cwd() / CLIENT_SECRET_FILE
    if cwd_path.exists():
        return cwd_path
    script_dir = Path(__file__).parent.parent / CLIENT_SECRET_FILE
    return script_dir


def get_token_path(base_dir: Optional[str] = None) -> Path:
    """Kembalikan path ke file token.pickle."""
    if base_dir:
        return Path(base_dir) / TOKEN_FILE
    return Path.cwd() / TOKEN_FILE


def load_credentials(token_path: Optional[str] = None) -> Optional[object]:
    """
    Muat credentials dari token yang tersimpan.

    Returns:
        Credentials object atau None jika belum ada.
    """
    if not GOOGLE_LIBS_AVAILABLE:
        raise AuthError(
            "Library Google Auth belum terinstall. "
            "Jalankan setup.bat atau: pip install -r requirements.txt"
        )

    token_file = Path(token_path) if token_path else get_token_path()
    if not token_file.exists():
        return None

    try:
        with open(token_file, "rb") as f:
            creds = pickle.load(f)
        logger.info("Token OAuth dimuat dari: %s", token_file)
        return creds
    except (pickle.UnpicklingError, EOFError, Exception) as e:
        logger.warning("Token rusak atau tidak valid, akan dibuat ulang: %s", e)
        return None


def save_credentials(creds: object, token_path: Optional[str] = None):
    """Simpan credentials OAuth ke disk."""
    token_file = Path(token_path) if token_path else get_token_path()
    token_file.parent.mkdir(parents=True, exist_ok=True)
    with open(token_file, "wb") as f:
        pickle.dump(creds, f)
    logger.info("Token OAuth disimpan ke: %s", token_file)


def get_credentials(
    client_secret_path: Optional[str] = None,
    token_path: Optional[str] = None,
    force_refresh: bool = False,
) -> object:
    """
    Dapatkan credentials OAuth yang valid.
    Jika token sudah ada dan valid, gunakan langsung.
    Jika expired, refresh otomatis.
    Jika belum ada, buka browser untuk otorisasi.

    Args:
        client_secret_path: Path ke client_secret.json dari Google Cloud Console.
        token_path: Path untuk menyimpan/membaca token.
        force_refresh: Paksa proses otorisasi ulang.

    Returns:
        google.oauth2.credentials.Credentials yang valid.

    Raises:
        AuthError: Jika autentikasi gagal.
        FileNotFoundError: Jika client_secret.json tidak ditemukan.
    """
    if not GOOGLE_LIBS_AVAILABLE:
        raise AuthError(
            "Library Google Auth belum terinstall. "
            "Jalankan: pip install -r requirements.txt"
        )

    secret_path = Path(client_secret_path) if client_secret_path else get_client_secret_path()

    if not secret_path.exists():
        raise FileNotFoundError(
            f"File client_secret.json tidak ditemukan di: {secret_path}\n\n"
            "LANGKAH SETUP OAUTH:\n"
            "1. Buka https://console.cloud.google.com/\n"
            "2. Buat proyek baru atau pilih proyek yang ada\n"
            "3. Aktifkan YouTube Data API v3\n"
            "4. Buka Credentials > Create Credentials > OAuth 2.0 Client ID\n"
            "5. Pilih tipe 'Desktop App'\n"
            "6. Download file JSON dan rename menjadi 'client_secret.json'\n"
            "7. Taruh file tersebut di folder aplikasi ini\n"
        )

    creds = None
    if not force_refresh:
        creds = load_credentials(token_path)

    # Refresh token jika expired
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            save_credentials(creds, token_path)
            logger.info("Token OAuth berhasil di-refresh.")
            return creds
        except Exception as e:
            logger.warning("Gagal refresh token: %s. Akan otorisasi ulang.", e)
            creds = None

    # Token valid, langsung pakai
    if creds and creds.valid:
        return creds

    # Butuh otorisasi baru — buka browser
    logger.info("Memulai proses otorisasi OAuth baru...")
    try:
        flow = InstalledAppFlow.from_client_secrets_file(
            str(secret_path), SCOPES
        )
        # Gunakan port acak agar tidak konflik
        creds = flow.run_local_server(
            port=0,
            prompt="consent",
            access_type="offline",
        )
        save_credentials(creds, token_path)
        logger.info("Otorisasi OAuth berhasil.")
        return creds
    except Exception as e:
        raise AuthError(f"Gagal melakukan otorisasi OAuth: {e}") from e


def build_youtube_service(credentials: object):
    """
    Buat YouTube API service object.

    Args:
        credentials: Credentials OAuth yang valid.

    Returns:
        googleapiclient.discovery Resource untuk YouTube v3.
    """
    try:
        from googleapiclient.discovery import build
        service = build("youtube", "v3", credentials=credentials)
        logger.info("YouTube API service berhasil dibuat.")
        return service
    except ImportError:
        raise AuthError(
            "Library google-api-python-client belum terinstall. "
            "Jalankan: pip install google-api-python-client"
        )
    except Exception as e:
        raise AuthError(f"Gagal membuat YouTube service: {e}") from e


def get_channel_info(youtube_service) -> dict:
    """
    Ambil nama dan ID channel YouTube yang terhubung.

    Args:
        youtube_service: YouTube API service dari build_youtube_service().

    Returns:
        Dict berisi 'channel_id', 'channel_title', 'channel_url'.

    Raises:
        AuthError: Jika channel tidak bisa diambil atau tidak ada channel yang terhubung.
    """
    try:
        response = youtube_service.channels().list(
            part="snippet,id",
            mine=True,
            maxResults=1,
        ).execute()

        items = response.get("items", [])
        if not items:
            raise AuthError(
                "Tidak ada channel YouTube yang terhubung ke akun ini.\n"
                "Pastikan akun Google yang digunakan memiliki channel YouTube aktif."
            )

        channel = items[0]
        channel_id = channel.get("id", "")
        channel_title = channel.get("snippet", {}).get("title", "")

        return {
            "channel_id": channel_id,
            "channel_title": channel_title,
            "channel_url": f"https://www.youtube.com/channel/{channel_id}",
        }

    except AuthError:
        raise
    except Exception as e:
        raise AuthError(f"Gagal mengambil informasi channel: {e}") from e


def revoke_credentials(token_path: Optional[str] = None):
    """Cabut dan hapus credentials OAuth."""
    token_file = Path(token_path) if token_path else get_token_path()
    if token_file.exists():
        try:
            creds = load_credentials(token_path)
            if creds:
                from google.auth.transport.requests import Request as GRequest
                import requests
                requests.post(
                    "https://oauth2.googleapis.com/revoke",
                    params={"token": creds.token},
                    headers={"content-type": "application/x-www-form-urlencoded"},
                    timeout=10,
                )
        except Exception as e:
            logger.warning("Tidak bisa mencabut token online: %s", e)
        token_file.unlink()
        logger.info("Credentials dihapus: %s", token_file)
    else:
        logger.info("Tidak ada token untuk dihapus.")
