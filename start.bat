@echo off
chcp 65001 > nul
title YouTube Bulk Uploader

REM Pindah ke direktori skrip ini berada
cd /d "%~dp0"

REM Periksa Python
python --version > nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Python tidak ditemukan!
    echo Jalankan setup.bat terlebih dahulu.
    pause
    exit /b 1
)

REM Periksa apakah library sudah terinstall
python -c "import googleapiclient" > nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [PERINGATAN] Library belum terinstall. Menjalankan setup...
    call setup.bat
)

REM Jalankan aplikasi GUI
echo Memulai YouTube Bulk Uploader...
python main.py

if %ERRORLEVEL% neq 0 (
    echo.
    echo [ERROR] Aplikasi berhenti dengan error.
    echo Periksa file logs\uploader.log untuk detail.
    pause
)
