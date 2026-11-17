# 📹 YouTube Bulk Uploader

Upload massal video rekaman Zoom ke YouTube secara otomatis menggunakan YouTube Data API v3.

---

## ✨ Fitur Utama

| Fitur | Keterangan |
|---|---|
| 🔍 Pindai Rekursif | Temukan semua file MP4 secara otomatis dari folder manapun |
| 📋 Metadata CSV | Edit judul, deskripsi, privasi di Excel sebelum upload |
| ⬆️ Resumable Upload | Upload dilanjutkan dari titik terputus jika koneksi gagal |
| 🔄 Retry Otomatis | Coba ulang dengan exponential backoff hingga 10x |
| 🗄️ Riwayat SQLite | Semua upload tersimpan, tidak ada duplikat |
| ⛔ Cegah Duplikat | Deteksi via filepath dan SHA-256 hash |
| 📊 Laporan HTML | Laporan lengkap yang bisa dibuka di browser |
| 🖥️ GUI Windows | Antarmuka grafis sederhana menggunakan tkinter |
| 🔒 Default Private | Semua video diupload sebagai PRIVATE secara default |

---

## 📋 Persyaratan

- **Windows 10/11** (64-bit)
- **Python 3.9+** — Download dari [python.org](https://www.python.org/downloads/)
  - Saat instalasi: **centang "Add Python to PATH"**
- **Koneksi internet** aktif
- **Akun Google/YouTube**

---

## 🚀 Cara Install

### Langkah 1: Download Aplikasi

Simpan seluruh folder aplikasi ini di komputer Anda, misalnya:
```
D:\YouTube-Uploader\
```

### Langkah 2: Jalankan Setup

Klik dua kali file **`setup.bat`** dan tunggu hingga selesai.

Setup akan:
- Memeriksa instalasi Python
- Menginstall semua library yang diperlukan
- Membuat folder `logs/` dan `reports/`

### Langkah 3: Setup OAuth Google (WAJIB)

Ini adalah langkah yang perlu dilakukan **satu kali saja**:

1. Buka [Google Cloud Console](https://console.cloud.google.com/)

2. **Buat Project Baru:**
   - Klik "New Project" → Beri nama, misalnya `YouTube Uploader`
   - Klik "Create"

3. **Aktifkan YouTube Data API v3:**
   - Menu kiri: "APIs & Services" → "Library"
   - Cari "YouTube Data API v3" → Klik "Enable"

4. **Buat OAuth Credentials:**
   - Menu kiri: "APIs & Services" → "Credentials"
   - Klik "Create Credentials" → "OAuth 2.0 Client ID"
   - Konfigurasi consent screen jika diminta (External, nama bebas)
   - Application type: **Desktop app**
   - Name: `YouTube Uploader` → Klik "Create"

5. **Download file credentials:**
   - Klik ikon Download (⬇️) di samping credentials yang baru dibuat
   - **Rename file menjadi: `client_secret.json`**
   - Pindahkan file tersebut ke folder aplikasi ini

6. **Tambahkan akun sebagai Test User** (penting!):
   - Menu: "OAuth consent screen" → "Test users"
   - Klik "Add Users" → Masukkan email akun YouTube Anda

7. **Jalankan Koneksi OAuth:**
   - Klik dua kali `start.bat`
   - Klik tombol **"🔑 Koneksi OAuth"**
   - Browser akan terbuka → Login dengan akun Google Anda
   - Izinkan akses yang diminta
   - Token tersimpan otomatis di `token.pickle`

---

## 📖 Cara Penggunaan

### Menggunakan GUI (Rekomendasi)

1. **Jalankan aplikasi:** Klik dua kali `start.bat`

2. **Pilih Folder:**
   - Klik tombol **"Pilih Folder"**
   - Navigasi ke folder rekaman Zoom Anda
   - Contoh: `D:\Users\Nama\Documents\Zoom`

3. **Pindai File:**
   - Klik tombol **"🔍 Pindai"**
   - Aplikasi akan mencari semua file MP4 secara rekursif
   - File `metadata.csv` otomatis dibuat

4. **Edit Metadata (Opsional):**
   - Klik tombol **"📋 Preview CSV"** untuk membuka di Excel
   - Edit kolom: `title`, `description`, `privacy`, `playlist_id`, `tags`
   - **Privacy options:** `private` | `unlisted` | `public`
   - Simpan file CSV setelah selesai mengedit

5. **Mulai Upload:**
   - Klik tombol **"⬆️ Mulai Upload"**
   - Baca dialog konfirmasi dengan teliti
   - Klik **"Ya"** untuk melanjutkan
   - Upload berjalan satu per satu secara berurutan

6. **Lihat Laporan:**
   - Klik tombol **"📊 Laporan"**
   - Laporan HTML terbuka di browser

### Menggunakan CLI (Tingkat Lanjut)

Buka `start_cli.bat` atau Command Prompt di folder aplikasi:

```cmd
# Pindai folder rekaman
python main.py --cli --scan "D:\Zoom\Recordings"

# Upload dari metadata.csv
python main.py --cli --csv metadata.csv

# Buat laporan
python main.py --cli --report
```

---

## 📁 Struktur File

```
youtube-bulk-uploader/
│
├── main.py                 # Entry point (GUI atau CLI)
├── requirements.txt        # Library Python yang diperlukan
├── setup.bat               # Script instalasi Windows
├── start.bat               # Script jalankan GUI
├── start_cli.bat           # Script jalankan CLI
│
├── client_secret.json      # ⚠️ WAJIB diisi dari Google Cloud Console
├── token.pickle            # Token OAuth (otomatis dibuat setelah login)
├── metadata.csv            # Metadata video (otomatis dibuat setelah scan)
├── upload_history.db       # Database riwayat upload (otomatis dibuat)
│
├── src/
│   ├── scanner.py          # Pindai file MP4 rekursif
│   ├── database.py         # Manajemen SQLite
│   ├── auth.py             # OAuth 2.0 Google
│   ├── uploader.py         # Engine upload YouTube
│   ├── reporter.py         # Generate laporan CSV & HTML
│   ├── gui.py              # Antarmuka tkinter
│   └── logger_config.py    # Konfigurasi logging
│
├── tests/
│   ├── test_scanner.py     # Tests untuk scanner
│   ├── test_database.py    # Tests untuk database
│   ├── test_uploader.py    # Tests untuk uploader (mock API)
│   └── test_reporter.py    # Tests untuk reporter
│
├── logs/
│   └── uploader.log        # Log aktivitas (otomatis dibuat)
│
└── reports/
    └── laporan_*.html      # Laporan upload (otomatis dibuat)
```

---

## 📊 Format metadata.csv

| Kolom | Keterangan | Contoh |
|---|---|---|
| `filepath` | Path lengkap file MP4 | `D:\Zoom\kuliah.mp4` |
| `filename` | Nama file | `kuliah.mp4` |
| `title` | Judul video di YouTube (maks 100 karakter) | `Kuliah Matematika 01` |
| `description` | Deskripsi video (maks 5000 karakter) | `Pertemuan pertama...` |
| `privacy` | Privasi video | `private` / `unlisted` / `public` |
| `playlist_id` | ID playlist YouTube (opsional) | `PLxxxxxxxxxx` |
| `tags` | Tag dipisah koma (opsional) | `kuliah,matematika` |
| `category_id` | ID kategori YouTube | `27` (Education) |

---

## ⚠️ Hal Penting

### Keamanan
- **Jangan bagikan** `client_secret.json` atau `token.pickle` kepada siapapun
- Tambahkan kedua file ini ke `.gitignore` jika menggunakan Git
- Credentials tidak pernah disimpan di dalam kode

### Kuota API YouTube
- Default kuota: **10.000 unit/hari** per project
- Upload 1 video mengonsumsi sekitar **1.600 unit**
- Artinya: sekitar **6 video per hari** per project
- Reset kuota: setiap hari pukul **00:00 UTC (07:00 WIB)**
- Untuk kuota lebih besar: ajukan [verifikasi OAuth](https://support.google.com/youtube/contact/yt_api_form)

### Status Upload
| Status | Arti |
|---|---|
| ⏳ Menunggu | Belum diproses |
| ⬆️ Sedang Upload | Sedang berjalan |
| ✅ Berhasil | Upload berhasil, ada YouTube ID |
| ❌ Gagal | Error permanen, perlu dicek |
| ⚠️ Tidak Pasti | Kemungkinan berhasil, cek YouTube Studio |
| ⏭️ Dilewati | Sudah diupload sebelumnya |

### Status "Tidak Pasti" (Uncertain)
Jika upload menunjukkan status ⚠️ **Tidak Pasti**, buka [YouTube Studio](https://studio.youtube.com/) dan periksa apakah video sudah ada. Jika sudah ada, update status di database secara manual atau biarkan saja (tidak akan diupload ulang jika file ditemukan).

---

## 🔧 Menjalankan Tests

```cmd
# Di Command Prompt, dari folder aplikasi:
python -m pytest tests/ -v
```

---

## ❓ Troubleshooting

**"Python tidak ditemukan"**
→ Install Python dari python.org, centang "Add Python to PATH"

**"client_secret.json tidak ditemukan"**
→ Ikuti Langkah 3 di bagian Setup OAuth

**"Quota exceeded"**
→ Kuota harian habis, coba lagi besok pukul 07:00 WIB

**"Token rusak/expired"**
→ Hapus file `token.pickle`, lalu klik "Koneksi OAuth" lagi

**"Upload status Tidak Pasti"**
→ Periksa YouTube Studio, kemungkinan video sudah terupload

**Browser tidak terbuka saat OAuth**
→ Buka URL yang muncul di log secara manual di browser

---

## 📞 Catatan Teknis

- Aplikasi menggunakan **Resumable Upload** — upload dilanjutkan dari titik terputus
- Chunk size default: **2.5 MB** per permintaan
- Retry maksimum: **10x** dengan exponential backoff
- Database SQLite tersimpan di `upload_history.db` (tidak perlu diinstall terpisah)
- Log tersimpan di `logs/uploader.log` dengan rotasi otomatis (maks 5 MB × 5 file)

---

*Dibuat untuk membantu upload massal rekaman Zoom ke YouTube secara efisien dan aman.*
