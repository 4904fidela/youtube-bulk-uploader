@echo off
chcp 65001 > nul
title YouTube Bulk Uploader - Setup

echo.
echo ============================================================
echo   YouTube Bulk Uploader - Instalasi
echo ============================================================
echo.

REM Periksa apakah Python tersedia
python --version > nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Python tidak ditemukan!
    echo.
    echo Silakan install Python 3.9+ dari:
    echo   https://www.python.org/downloads/
    echo.
    echo PENTING: Centang "Add Python to PATH" saat instalasi!
    echo.
    pause
    exit /b 1
)

echo [OK] Python ditemukan:
python --version
echo.

REM Periksa pip
pip --version > nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [ERROR] pip tidak ditemukan. Reinstall Python dengan pip.
    pause
    exit /b 1
)

REM Upgrade pip
echo [1/3] Mengupgrade pip...
python -m pip install --upgrade pip --quiet
echo [OK] pip siap.
echo.

REM Install dependencies
echo [2/3] Menginstall library yang diperlukan...
echo       Proses ini mungkin memakan beberapa menit...
echo.
pip install -r requirements.txt
if %ERRORLEVEL% neq 0 (
    echo.
    echo [ERROR] Gagal menginstall library!
    echo Pastikan koneksi internet aktif, lalu jalankan setup.bat lagi.
    pause
    exit /b 1
)
echo.
echo [OK] Library berhasil diinstall.
echo.

REM Verifikasi tkinter
echo [3/3] Memeriksa tkinter (GUI)...
python -c "import tkinter; print('tkinter OK')" 2>&1
if %ERRORLEVEL% neq 0 (
    echo [PERINGATAN] tkinter tidak tersedia.
    echo Gunakan Python dari python.org (bukan conda/pypy) untuk GUI.
    echo Aplikasi tetap bisa digunakan dalam mode CLI.
    echo.
)

REM Buat direktori yang dibutuhkan
echo Menyiapkan folder aplikasi...
if not exist "logs" mkdir logs
if not exist "reports" mkdir reports
echo [OK] Folder siap.
echo.

REM Periksa client_secret.json
if not exist "client_secret.json" (
    echo.
    echo ============================================================
    echo   LANGKAH BERIKUTNYA: Setup OAuth
    echo ============================================================
    echo.
    echo   File client_secret.json BELUM ADA.
    echo   Anda perlu mendapatkan credentials dari Google Cloud:
    echo.
    echo   1. Buka: https://console.cloud.google.com/
    echo   2. Buat project baru
    echo   3. Aktifkan YouTube Data API v3
    echo   4. Buat OAuth 2.0 Client ID (tipe: Desktop App)
    echo   5. Download JSON dan rename jadi: client_secret.json
    echo   6. Taruh di folder ini: %CD%
    echo.
    echo   Setelah itu, jalankan start.bat dan klik "Koneksi OAuth"
    echo ============================================================
) else (
    echo [OK] client_secret.json ditemukan.
)

echo.
echo ============================================================
echo   Instalasi selesai!
echo   Jalankan aplikasi dengan: start.bat
echo ============================================================
echo.
pause
