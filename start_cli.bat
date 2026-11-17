@echo off
chcp 65001 > nul
title YouTube Bulk Uploader - CLI Mode
cd /d "%~dp0"

echo ============================================================
echo   YouTube Bulk Uploader - Mode CLI
echo ============================================================
echo.
echo Perintah yang tersedia:
echo.
echo   Pindai folder:
echo     python main.py --cli --scan "D:\Zoom\Recordings"
echo.
echo   Upload dari CSV:
echo     python main.py --cli --csv metadata.csv
echo.
echo   Buat laporan:
echo     python main.py --cli --report
echo.
echo   Bantuan lengkap:
echo     python main.py --help
echo.
echo ============================================================
echo.

REM Buka cmd di folder ini agar pengguna bisa ketik perintah manual
cmd /k "echo Ketik perintah di atas atau 'exit' untuk keluar. && echo."
