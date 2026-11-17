"""
gui.py - Antarmuka grafis (GUI) menggunakan tkinter untuk Windows.
Tombol: Pilih Folder, Pindai, Preview, Upload, Laporan.
Upload nyata memerlukan konfirmasi eksplisit dari pengguna.
"""

import os
import sys
import threading
import logging
import subprocess
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict

# Tambahkan parent directory ke path agar modul src bisa diimport
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.scanner import (
    scan_mp4_files, save_metadata_csv, load_metadata_csv,
    validate_entries, merge_scan_with_existing,
)
from src.database import UploadDatabase
from src.reporter import generate_csv_report, generate_html_report
from src.preflight import run_preflight, PreflightResult

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
#  Konstanta
# ------------------------------------------------------------------ #
APP_TITLE = "YouTube Bulk Uploader"
APP_VERSION = "1.1.0"
WINDOW_WIDTH = 1000
WINDOW_HEIGHT = 700
YOUTUBE_RED = "#FF0000"
YOUTUBE_DARK = "#282828"
BG_LIGHT = "#F9F9F9"
BG_WHITE = "#FFFFFF"


class BulkUploaderApp(tk.Tk):
    """Aplikasi utama YouTube Bulk Uploader."""

    def __init__(self):
        super().__init__()
        self.title(f"{APP_TITLE} v{APP_VERSION}")
        self.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}")
        self.configure(bg=BG_LIGHT)
        self.minsize(800, 550)

        # State aplikasi
        self.source_folder = tk.StringVar()
        self.scanned_entries: List[Dict] = []
        self.db: Optional[UploadDatabase] = None
        self.upload_thread: Optional[threading.Thread] = None
        self.cancel_event = threading.Event()
        self._is_uploading = False

        # State pengaturan bulk upload
        self.bulk_privacy = tk.StringVar(value="unlisted")   # default UNLISTED
        self.bulk_batch   = tk.StringVar(value="semua")      # default semua
        self._cached_service = None                          # service OAuth di-cache

        # Inisialisasi database
        self._init_database()

        # Bangun UI
        self._build_ui()
        self._update_button_states()

        # Periksa client_secret.json
        self.after(500, self._check_oauth_setup)

    # ------------------------------------------------------------------ #
    #  INISIALISASI
    # ------------------------------------------------------------------ #

    def _init_database(self):
        """Inisialisasi database SQLite."""
        db_path = Path.cwd() / "upload_history.db"
        try:
            self.db = UploadDatabase(str(db_path))
            logger.info("Database siap: %s", db_path)
        except Exception as e:
            messagebox.showerror(
                "Error Database",
                f"Tidak bisa membuka database:\n{e}\n\nAplikasi akan tetap berjalan.",
            )

    def _check_oauth_setup(self):
        """Periksa apakah client_secret.json sudah ada."""
        secret_path = Path.cwd() / "client_secret.json"
        if not secret_path.exists():
            self._log(
                "⚠️  PERHATIAN: File client_secret.json belum ditemukan!\n"
                "   Lihat menu Help > Panduan OAuth untuk instruksi setup.\n",
                "warning",
            )

    def _on_privacy_changed(self, *_):
        """Update label indikator privasi aktif di toolbar."""
        if not hasattr(self, "lbl_privacy_indicator"):
            return
        val = self.bulk_privacy.get()
        if val == "unlisted":
            self.lbl_privacy_indicator.config(
                text="✅ Bulk upload: UNLISTED", fg="#1B5E20"
            )
        else:
            self.lbl_privacy_indicator.config(
                text="🔒 Bulk upload: PRIVATE", fg="#B71C1C"
            )

    # ------------------------------------------------------------------ #
    #  PEMBANGUNAN UI
    # ------------------------------------------------------------------ #

    def _build_ui(self):
        """Bangun seluruh komponen UI."""
        self._build_header()
        self._build_toolbar()
        self._build_main_area()
        self._build_progress_bar()
        self._build_log_area()
        self._build_status_bar()
        self._build_menu()

    def _build_header(self):
        header = tk.Frame(self, bg=YOUTUBE_DARK, height=60)
        header.pack(fill=tk.X)
        header.pack_propagate(False)

        logo_label = tk.Label(
            header,
            text="▶  YouTube Bulk Uploader",
            font=("Segoe UI", 16, "bold"),
            fg=YOUTUBE_RED,
            bg=YOUTUBE_DARK,
        )
        logo_label.pack(side=tk.LEFT, padx=20, pady=10)

        version_label = tk.Label(
            header,
            text=f"v{APP_VERSION}",
            font=("Segoe UI", 9),
            fg="#999999",
            bg=YOUTUBE_DARK,
        )
        version_label.pack(side=tk.RIGHT, padx=20, pady=10)

    def _build_toolbar(self):
        toolbar = tk.Frame(self, bg="#EEEEEE", pady=8)
        toolbar.pack(fill=tk.X, padx=5, pady=2)

        # --- Baris 1: pilih folder ---
        folder_frame = tk.Frame(toolbar, bg="#EEEEEE")
        folder_frame.pack(fill=tk.X, padx=10, pady=3)

        tk.Label(
            folder_frame, text="📁 Folder Sumber:", bg="#EEEEEE",
            font=("Segoe UI", 10)
        ).pack(side=tk.LEFT)

        self.folder_entry = tk.Entry(
            folder_frame,
            textvariable=self.source_folder,
            width=55,
            font=("Consolas", 9),
        )
        self.folder_entry.pack(side=tk.LEFT, padx=(5, 5))

        self.btn_browse = tk.Button(
            folder_frame,
            text="Pilih Folder",
            command=self._browse_folder,
            bg="#2196F3",
            fg="white",
            font=("Segoe UI", 9, "bold"),
            relief=tk.FLAT,
            padx=10,
            cursor="hand2",
        )
        self.btn_browse.pack(side=tk.LEFT)

        # --- Baris 2: tombol aksi utama ---
        btn_frame = tk.Frame(toolbar, bg="#EEEEEE")
        btn_frame.pack(fill=tk.X, padx=10, pady=3)

        button_configs = [
            ("🔍 Pindai", self._scan_files, "#4CAF50", "btn_scan"),
            ("📋 Preview CSV", self._open_csv, "#FF9800", "btn_preview"),
            ("🧪 Uji Upload 1 Video", self._test_upload_selected, "#1565C0", "btn_test_upload"),
            ("⬆️  Mulai Upload", self._confirm_upload, YOUTUBE_RED, "btn_upload"),
            ("⏹  Batalkan", self._cancel_upload, "#9E9E9E", "btn_cancel"),
            ("📊 Laporan", self._generate_report, "#9C27B0", "btn_report"),
            ("🔑 Koneksi OAuth", self._run_oauth, "#00BCD4", "btn_oauth"),
        ]

        for text, cmd, color, attr in button_configs:
            btn = tk.Button(
                btn_frame,
                text=text,
                command=cmd,
                bg=color,
                fg="white",
                font=("Segoe UI", 9, "bold"),
                relief=tk.FLAT,
                padx=8,
                pady=4,
                cursor="hand2",
            )
            btn.pack(side=tk.LEFT, padx=4)
            setattr(self, attr, btn)

        # --- Baris 3: pengaturan bulk upload ---
        settings_frame = tk.Frame(toolbar, bg="#E0E0E0", padx=10, pady=5)
        settings_frame.pack(fill=tk.X, padx=10, pady=(0, 3))

        tk.Label(
            settings_frame,
            text="⚙️  Pengaturan Bulk Upload:",
            font=("Segoe UI", 9, "bold"),
            bg="#E0E0E0",
        ).pack(side=tk.LEFT, padx=(0, 12))

        # Privacy selector
        tk.Label(
            settings_frame, text="Privasi:", bg="#E0E0E0", font=("Segoe UI", 9)
        ).pack(side=tk.LEFT)

        privacy_options = [
            ("🔒 PRIVATE", "private"),
            ("🔗 UNLISTED", "unlisted"),
        ]
        for label, value in privacy_options:
            rb = tk.Radiobutton(
                settings_frame,
                text=label,
                variable=self.bulk_privacy,
                value=value,
                bg="#E0E0E0",
                font=("Segoe UI", 9),
                activebackground="#E0E0E0",
                cursor="hand2",
            )
            rb.pack(side=tk.LEFT, padx=4)

        tk.Label(
            settings_frame, text="   |", bg="#E0E0E0", font=("Segoe UI", 9), fg="#999"
        ).pack(side=tk.LEFT)

        # Batch size selector
        tk.Label(
            settings_frame, text="  Ukuran Batch:", bg="#E0E0E0", font=("Segoe UI", 9)
        ).pack(side=tk.LEFT)

        batch_choices = ["1", "5", "10", "25", "semua"]
        self.batch_menu = ttk.Combobox(
            settings_frame,
            textvariable=self.bulk_batch,
            values=batch_choices,
            state="readonly",
            width=6,
            font=("Segoe UI", 9),
        )
        self.batch_menu.pack(side=tk.LEFT, padx=(4, 2))

        tk.Label(
            settings_frame,
            text="video per sesi",
            bg="#E0E0E0",
            font=("Segoe UI", 9),
        ).pack(side=tk.LEFT)

        # Indikator privasi aktif (update otomatis)
        self.lbl_privacy_indicator = tk.Label(
            settings_frame,
            text="",
            font=("Segoe UI", 9, "bold"),
            bg="#E0E0E0",
        )
        self.lbl_privacy_indicator.pack(side=tk.RIGHT, padx=10)
        self.bulk_privacy.trace_add("write", self._on_privacy_changed)
        self._on_privacy_changed()  # inisialisasi tampilan

    def _build_main_area(self):
        """Area utama: tabel preview file."""
        main_frame = tk.Frame(self, bg=BG_LIGHT)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=3)

        # Header area
        header_frame = tk.Frame(main_frame, bg=BG_LIGHT)
        header_frame.pack(fill=tk.X, padx=5)

        tk.Label(
            header_frame,
            text="Daftar File:",
            font=("Segoe UI", 10, "bold"),
            bg=BG_LIGHT,
        ).pack(side=tk.LEFT)

        self.lbl_count = tk.Label(
            header_frame,
            text="0 file",
            font=("Segoe UI", 9),
            fg="#666666",
            bg=BG_LIGHT,
        )
        self.lbl_count.pack(side=tk.LEFT, padx=10)

        # Tabel dengan scrollbar
        table_frame = tk.Frame(main_frame)
        table_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=3)

        columns = ("no", "filename", "title", "size", "status", "privacy")
        self.tree = ttk.Treeview(
            table_frame,
            columns=columns,
            show="headings",
            selectmode="extended",
        )

        col_configs = [
            ("no", "No", 40),
            ("filename", "Nama File", 280),
            ("title", "Judul", 260),
            ("size", "Ukuran", 80),
            ("status", "Status", 100),
            ("privacy", "Privasi", 80),
        ]
        for col, heading, width in col_configs:
            self.tree.heading(col, text=heading)
            self.tree.column(col, width=width, minwidth=40)

        # Scrollbar vertikal
        v_scroll = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.tree.yview)
        h_scroll = ttk.Scrollbar(table_frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=v_scroll.set, xscrollcommand=h_scroll.set)

        v_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        h_scroll.pack(side=tk.BOTTOM, fill=tk.X)
        self.tree.pack(fill=tk.BOTH, expand=True)

        # Tag warna baris
        self.tree.tag_configure("success", background="#E8F5E9")
        self.tree.tag_configure("failed", background="#FFEBEE")
        self.tree.tag_configure("uncertain", background="#FFF3E0")
        self.tree.tag_configure("in_progress", background="#E3F2FD")
        self.tree.tag_configure("skipped", background="#F5F5F5")

        # Update tombol saat seleksi berubah
        self.tree.bind("<<TreeviewSelect>>", lambda _: self._update_button_states())

    def _build_progress_bar(self):
        progress_frame = tk.Frame(self, bg=BG_LIGHT)
        progress_frame.pack(fill=tk.X, padx=10, pady=3)

        self.lbl_progress = tk.Label(
            progress_frame,
            text="Siap",
            font=("Segoe UI", 9),
            bg=BG_LIGHT,
            anchor="w",
        )
        self.lbl_progress.pack(fill=tk.X)

        self.progress_bar = ttk.Progressbar(
            progress_frame,
            mode="determinate",
            length=400,
        )
        self.progress_bar.pack(fill=tk.X, pady=2)

    def _build_log_area(self):
        log_frame = tk.Frame(self, bg=BG_LIGHT)
        log_frame.pack(fill=tk.X, padx=5, pady=3)

        tk.Label(
            log_frame,
            text="Log Aktivitas:",
            font=("Segoe UI", 9, "bold"),
            bg=BG_LIGHT,
        ).pack(anchor="w", padx=5)

        self.log_text = scrolledtext.ScrolledText(
            log_frame,
            height=6,
            wrap=tk.WORD,
            font=("Consolas", 8),
            bg="#1E1E1E",
            fg="#CCCCCC",
            state=tk.DISABLED,
        )
        self.log_text.pack(fill=tk.X, padx=5, pady=2)

        # Tag warna log
        self.log_text.tag_configure("warning", foreground="#FFA500")
        self.log_text.tag_configure("error", foreground="#FF6B6B")
        self.log_text.tag_configure("success", foreground="#6BCB77")
        self.log_text.tag_configure("info", foreground="#CCCCCC")

    def _build_status_bar(self):
        status_bar = tk.Frame(self, bg="#DDDDDD", height=22)
        status_bar.pack(fill=tk.X, side=tk.BOTTOM)
        status_bar.pack_propagate(False)

        self.lbl_status = tk.Label(
            status_bar,
            text="Siap. Pilih folder rekaman Zoom untuk memulai.",
            font=("Segoe UI", 8),
            bg="#DDDDDD",
            anchor="w",
        )
        self.lbl_status.pack(side=tk.LEFT, padx=10)

        self.lbl_time = tk.Label(
            status_bar,
            text="",
            font=("Segoe UI", 8),
            bg="#DDDDDD",
            anchor="e",
        )
        self.lbl_time.pack(side=tk.RIGHT, padx=10)
        self._update_clock()

    def _build_menu(self):
        menubar = tk.Menu(self)
        self.config(menu=menubar)

        # Menu File
        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(label="Pilih Folder Sumber", command=self._browse_folder)
        file_menu.add_command(label="Muat Ulang Database", command=self._refresh_from_db)
        file_menu.add_separator()
        file_menu.add_command(label="Keluar", command=self.quit)

        # Menu Upload
        upload_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Upload", menu=upload_menu)
        upload_menu.add_command(label="Pindai File MP4", command=self._scan_files)
        upload_menu.add_command(label="🧪 Uji Upload 1 Video (Pilihan)", command=self._test_upload_selected)
        upload_menu.add_command(label="Mulai Upload (Konfirmasi)", command=self._confirm_upload)
        upload_menu.add_command(label="Batalkan Upload", command=self._cancel_upload)
        upload_menu.add_separator()
        upload_menu.add_command(label="Reset Gagal ke Pending", command=self._reset_failed)

        # Menu Laporan
        report_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Laporan", menu=report_menu)
        report_menu.add_command(label="Buat Laporan HTML", command=self._generate_report)
        report_menu.add_command(label="Lihat Riwayat", command=self._refresh_from_db)

        # Menu Help
        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Bantuan", menu=help_menu)
        help_menu.add_command(label="Panduan OAuth Setup", command=self._show_oauth_guide)
        help_menu.add_command(label="Tentang Aplikasi", command=self._show_about)

    # ------------------------------------------------------------------ #
    #  AKSI TOMBOL
    # ------------------------------------------------------------------ #

    def _browse_folder(self):
        """Buka dialog pilih folder."""
        folder = filedialog.askdirectory(
            title="Pilih Folder Rekaman Zoom",
            initialdir=self.source_folder.get() or Path.home(),
        )
        if folder:
            self.source_folder.set(folder)
            self._log(f"Folder dipilih: {folder}")
            self._update_status(f"Folder: {folder}")
            self._update_button_states()

    def _scan_files(self):
        """
        Pindai file MP4 menggunakan iter_mp4_files() yang non-blocking.

        Perbaikan v1.3.0:
        - Progress callback di-throttle di scanner (maks setiap 0.25s atau 10 file)
          sehingga Tkinter event queue tidak dibanjiri ratusan self.after() calls.
        - Setiap tahap dicatat dengan time.perf_counter() untuk diagnosa.
        - _populate_table menggunakan batch DB query (satu query, bukan 291).
        - Watchdog: jika tidak ada progress selama 30 detik, kirim peringatan.
        - Semua GUI updates dijamin berjalan di main thread via self.after().
        """
        folder = self.source_folder.get().strip()
        if not folder:
            messagebox.showwarning(
                "Folder Belum Dipilih",
                "Silakan pilih folder rekaman Zoom terlebih dahulu.",
            )
            return

        self._log(f"Memindai folder: {folder}...")
        self._update_status("Memindai file MP4...")
        self.btn_scan.config(state=tk.DISABLED)
        self.cancel_event.clear()
        self.progress_bar.config(mode="indeterminate")
        self.progress_bar.start(15)

        import time as _time
        scan_t0 = _time.perf_counter()
        # Gunakan list untuk state yang bisa diubah dari closure
        last_progress_time = [_time.perf_counter()]

        def on_progress(filepath: str, count: int):
            """
            Dipanggil dari background thread — HANYA mengantre satu self.after().
            Throttling sudah dilakukan di scanner sebelum callback ini dipanggil.
            """
            last_progress_time[0] = _time.perf_counter()
            elapsed = last_progress_time[0] - scan_t0
            fname = Path(filepath).name if filepath else f"({count} file)"
            # self.after() aman dipanggil dari thread manapun
            self.after(
                0,
                lambda f=fname, c=count, e=elapsed: self._on_scan_progress(f, c, e),
            )

        def watchdog():
            """
            Periksa setiap 10 detik apakah ada progress.
            Jika tidak ada progress selama 30 detik, tampilkan peringatan di log.
            """
            import threading as _threading
            while True:
                _time.sleep(10)
                # Cek apakah ScanThread masih hidup
                scan_alive = any(
                    t.name == "ScanThread" and t.is_alive()
                    for t in _threading.enumerate()
                )
                if not scan_alive:
                    break
                stale = _time.perf_counter() - last_progress_time[0]
                if stale > 30:
                    self.after(
                        0,
                        lambda s=stale: self._log(
                            f"⚠️  WATCHDOG: Tidak ada progress selama {s:.0f}s. "
                            f"Scan mungkin macet di suatu folder.",
                            "warning",
                        ),
                    )

        def do_scan():
            import time as _t
            try:
                from src.scanner import (
                    iter_mp4_files, merge_scan_with_existing, save_metadata_csv, ScanTimings
                )
                timings = ScanTimings()

                # ---- Tahap 1: Discovery ----
                self.after(0, lambda: self._log("  [1/4] Discovery file MP4..."))
                entries = list(
                    iter_mp4_files(
                        folder,
                        cancel_event=self.cancel_event,
                        progress_callback=on_progress,
                    )
                )
                t_discovery = timings.mark(f"[1/4] Discovery ({len(entries)} file)")

                if self.cancel_event.is_set():
                    self.after(0, lambda: self._on_scan_cancelled(len(entries)))
                    return

                self.after(0, lambda n=len(entries), t=t_discovery: self._log(
                    f"  ✓ Discovery selesai: {n} file dalam {t:.2f}s"
                ))

                # ---- Tahap 2: Merge dengan CSV yang sudah ada ----
                self.after(0, lambda: self._log("  [2/4] Merge dengan metadata.csv..."))
                csv_path = str(Path.cwd() / "metadata.csv")
                merged = merge_scan_with_existing(entries, csv_path)
                t_merge = timings.mark(f"[2/4] Merge CSV ({len(merged)} entri)")

                self.after(0, lambda n=len(merged), t=t_merge: self._log(
                    f"  ✓ Merge selesai: {n} entri dalam {t:.2f}s total"
                ))

                # ---- Tahap 3: Tulis CSV ----
                self.after(0, lambda: self._log("  [3/4] Menyimpan metadata.csv..."))
                save_metadata_csv(merged, csv_path)
                t_csv = timings.mark("[3/4] Tulis CSV")

                self.after(0, lambda t=t_csv: self._log(
                    f"  ✓ CSV ditulis dalam {t:.2f}s total"
                ))

                # ---- Tahap 4: Kirim ke GUI ----
                self.after(0, lambda: self._log("  [4/4] Memperbarui tampilan GUI..."))
                total_elapsed = _t.perf_counter() - scan_t0
                timings.mark("[4/4] Kirim ke GUI (self.after)")

                # Log timing report lengkap
                self.after(0, lambda r=timings.report(): logger.info(r))

                self.scanned_entries = merged
                # Kirim SATU self.after() untuk completion — bukan ratusan
                self.after(
                    0,
                    lambda: self._on_scan_complete(merged, csv_path, total_elapsed),
                )

            except Exception as e:
                import traceback
                tb = traceback.format_exc()
                logger.error("Scan error: %s\n%s", e, tb)
                self.after(0, lambda err=str(e): self._on_scan_error(err))

        import threading as _thr
        _thr.Thread(target=do_scan, daemon=True, name="ScanThread").start()
        _thr.Thread(target=watchdog, daemon=True, name="ScanWatchdog").start()

    def _on_scan_progress(self, filename: str, count: int, elapsed: float):
        """Update status bar dan label saat tiap file ditemukan."""
        self.lbl_count.config(text=f"{count} file ditemukan...")
        self.lbl_progress.config(
            text=f"Memindai ({elapsed:.0f}s): {filename}"
        )
        self._update_status(f"Memindai... {count} file MP4 ditemukan")

    def _on_scan_cancelled(self, count: int):
        """Dipanggil jika scan dibatalkan pengguna."""
        self.progress_bar.stop()
        self.progress_bar.config(mode="determinate")
        self.progress_bar["value"] = 0
        self.btn_scan.config(state=tk.NORMAL)
        self.lbl_count.config(text=f"{count} file (scan dibatalkan)")
        self._log(f"⏹ Scan dibatalkan. {count} file ditemukan sebelum dibatalkan.", "warning")
        self._update_status("Scan dibatalkan.")
        self._update_button_states()

    def _on_scan_complete(self, entries: List[Dict], csv_path: str, elapsed: float):
        """Dipanggil setelah pemindaian selesai."""
        self.progress_bar.stop()
        self.progress_bar.config(mode="determinate")
        self.progress_bar["value"] = 100
        self.btn_scan.config(state=tk.NORMAL)
        self._populate_table(entries)
        self.lbl_count.config(text=f"{len(entries)} file ditemukan")
        self.lbl_progress.config(text=f"Pindai selesai dalam {elapsed:.1f} detik")
        self._log(
            f"✅ Ditemukan {len(entries)} file MP4 dalam {elapsed:.1f} detik.\n"
            f"   Metadata disimpan ke: {csv_path}\n"
            f"   Edit file tersebut di Excel untuk mengubah judul/deskripsi.",
            "success",
        )
        self._update_status(f"Pindai selesai: {len(entries)} file ({elapsed:.1f}s)")
        self._update_button_states()

        if entries:
            messagebox.showinfo(
                "Pindai Selesai",
                f"Ditemukan {len(entries)} file MP4 dalam {elapsed:.1f} detik.\n\n"
                f"File metadata.csv telah diperbarui di:\n{csv_path}\n\n"
                f"Metadata yang sudah Anda edit sebelumnya tetap dipertahankan.\n"
                f"Anda bisa mengedit judul, deskripsi, dan privasi di Excel\n"
                f"sebelum memulai upload.",
            )

    def _on_scan_error(self, error: str):
        """Dipanggil jika pemindaian gagal."""
        self.btn_scan.config(state=tk.NORMAL)
        self._log(f"❌ Error pemindaian: {error}", "error")
        messagebox.showerror("Error Pemindaian", f"Gagal memindai folder:\n{error}")

    def _open_csv(self):
        """Buka file metadata.csv di Excel atau program default."""
        csv_path = Path.cwd() / "metadata.csv"
        if not csv_path.exists():
            messagebox.showwarning(
                "CSV Belum Ada",
                "File metadata.csv belum dibuat.\nLakukan Pindai terlebih dahulu.",
            )
            return
        try:
            os.startfile(str(csv_path))
        except AttributeError:
            subprocess.Popen(["xdg-open", str(csv_path)])
        self._log(f"Membuka metadata.csv: {csv_path}")

    def _confirm_upload(self):
        """
        Langkah 1: validasi CSV, OAuth, dan hitung statistik.
        Jika semua siap, buka dialog konfirmasi detail di background thread
        (untuk juga mengambil channel info sekaligus).
        Upload TIDAK dilakukan tanpa persetujuan eksplisit di dialog.
        """
        csv_path = Path.cwd() / "metadata.csv"
        if not csv_path.exists():
            messagebox.showwarning(
                "CSV Belum Ada",
                "Silakan pindai folder terlebih dahulu untuk membuat metadata.csv.",
            )
            return

        secret_path = Path.cwd() / "client_secret.json"
        if not secret_path.exists():
            messagebox.showerror(
                "OAuth Belum Dikonfigurasi",
                "File client_secret.json tidak ditemukan!\n\n"
                "Silakan:\n"
                "1. Buka Bantuan > Panduan OAuth Setup\n"
                "2. Ikuti instruksi mendapatkan credentials\n"
                "3. Klik 'Koneksi OAuth' untuk login",
            )
            return

        try:
            entries = load_metadata_csv(str(csv_path))
            valid_entries = validate_entries(entries)
        except Exception as e:
            messagebox.showerror("Error CSV", f"Gagal membaca metadata.csv:\n{e}")
            return

        if not valid_entries:
            messagebox.showwarning(
                "Tidak Ada File Valid",
                "Tidak ada file MP4 valid di metadata.csv.",
            )
            return

        # Ambil pengaturan saat ini dari GUI
        selected_privacy = self.bulk_privacy.get() or "private"
        batch_raw = self.bulk_batch.get().strip().lower()
        batch_size = None if batch_raw in ("semua", "all", "0", "") else int(batch_raw)

        # Hitung berapa video yang akan diproses
        new_entries = []
        already_done = 0
        uncertain_count = 0
        if self.db:
            for entry in valid_entries:
                db_row = self.db.find_by_filepath(entry["filepath"])
                if db_row and db_row["status"] == "success":
                    already_done += 1
                elif db_row and db_row["status"] == "uncertain":
                    uncertain_count += 1
                    new_entries.append(entry)  # tetap masuk antrian, akan dicek manual
                else:
                    new_entries.append(entry)
        else:
            new_entries = valid_entries

        # Terapkan batch_size ke antrian yang akan ditampilkan
        entries_for_session = new_entries[:batch_size] if batch_size else new_entries

        if not entries_for_session:
            messagebox.showinfo(
                "Semua Sudah Diupload",
                f"Semua {already_done} video sudah diupload sebelumnya.\n"
                "Gunakan menu 'Reset Gagal ke Pending' jika ingin mengulang.",
            )
            return

        # Hitung total ukuran file
        total_size_bytes = 0
        for entry in entries_for_session:
            try:
                total_size_bytes += Path(entry["filepath"]).stat().st_size
            except OSError:
                pass
        total_size_gb = total_size_bytes / (1024 ** 3)

        # Ambil channel info di background (tidak mau blok GUI)
        self._log(
            f"🔍 Menyiapkan dialog konfirmasi upload "
            f"({len(entries_for_session)} video, privasi: {selected_privacy.upper()})..."
        )
        self.btn_upload.config(state=tk.DISABLED)

        def fetch_channel_then_show():
            channel_title = ""
            channel_id = ""
            channel_url = ""
            try:
                from src.auth import get_credentials, build_youtube_service, get_channel_info
                creds = get_credentials()
                svc = build_youtube_service(creds)
                info = get_channel_info(svc)
                channel_title = info["channel_title"]
                channel_id = info["channel_id"]
                channel_url = info["channel_url"]
                # Simpan service untuk dipakai saat upload
                self._cached_service = svc
            except Exception as e:
                logger.warning("Tidak bisa ambil channel info: %s", e)

            self.after(
                0,
                lambda: self._show_bulk_confirmation_dialog(
                    entries=entries_for_session,
                    all_entries=valid_entries,
                    selected_privacy=selected_privacy,
                    batch_size=batch_size,
                    already_done=already_done,
                    uncertain_count=uncertain_count,
                    total_size_gb=total_size_gb,
                    channel_title=channel_title,
                    channel_id=channel_id,
                    channel_url=channel_url,
                ),
            )

        threading.Thread(target=fetch_channel_then_show, daemon=True).start()

    def _show_bulk_confirmation_dialog(
        self,
        entries: List[Dict],
        all_entries: List[Dict],
        selected_privacy: str,
        batch_size: Optional[int],
        already_done: int,
        uncertain_count: int,
        total_size_gb: float,
        channel_title: str,
        channel_id: str,
        channel_url: str,
    ):
        """
        Dialog konfirmasi detail sebelum bulk upload.
        Menampilkan: channel, jumlah video, ukuran total, privasi, estimasi waktu.
        Upload hanya dimulai jika pengguna klik konfirmasi.
        """
        self.btn_upload.config(state=tk.NORMAL)

        # Estimasi waktu: ~15 menit per GB rata-rata koneksi 10 Mbps
        est_minutes = int(total_size_gb * 15)
        if est_minutes < 1:
            est_str = "< 1 menit"
        elif est_minutes < 60:
            est_str = f"~{est_minutes} menit"
        else:
            h, m = divmod(est_minutes, 60)
            est_str = f"~{h} jam {m} menit"

        privacy_label = {
            "private": "🔒 PRIVATE — hanya Anda yang bisa melihat",
            "unlisted": "🔗 UNLISTED — siapapun dengan link bisa melihat",
            "public": "🌐 PUBLIC — terlihat oleh semua orang",
        }.get(selected_privacy, selected_privacy.upper())

        privacy_color = {
            "private": "#B71C1C",
            "unlisted": "#1B5E20",
            "public": "#E65100",
        }.get(selected_privacy, "#212121")

        batch_label = str(batch_size) if batch_size else f"Semua ({len(entries)})"

        # --- Bangun dialog ---
        dialog = tk.Toplevel(self)
        dialog.title("Konfirmasi Bulk Upload")
        dialog.geometry("600x560")
        dialog.resizable(False, False)
        dialog.grab_set()
        dialog.focus_set()

        # Header
        hdr_color = "#C62828" if selected_privacy == "public" else "#2E7D32" if selected_privacy == "unlisted" else "#37474F"
        hdr = tk.Frame(dialog, bg=hdr_color, height=58)
        hdr.pack(fill=tk.X)
        hdr.pack_propagate(False)
        tk.Label(
            hdr,
            text="⬆️  Konfirmasi Bulk Upload ke YouTube",
            font=("Segoe UI", 13, "bold"),
            fg="white",
            bg=hdr_color,
        ).pack(side=tk.LEFT, padx=16, pady=12)

        # Body
        body = tk.Frame(dialog, bg="white", padx=22, pady=14)
        body.pack(fill=tk.BOTH, expand=True)

        label_fg = "#555555"

        def row(label: str, value: str, vc: str = "#212121", bold: bool = False):
            f = tk.Frame(body, bg="white")
            f.pack(fill=tk.X, pady=2)
            tk.Label(f, text=label, width=20, anchor="w",
                     font=("Segoe UI", 9), fg=label_fg, bg="white").pack(side=tk.LEFT)
            tk.Label(f, text=value, anchor="w",
                     font=("Segoe UI", 9, "bold" if bold else "normal"),
                     fg=vc, bg="white", wraplength=360, justify="left").pack(side=tk.LEFT)

        def sep():
            tk.Frame(body, bg="#E0E0E0", height=1).pack(fill=tk.X, pady=5)

        # Channel
        tk.Label(body, text="CHANNEL TUJUAN", font=("Segoe UI", 9, "bold"),
                 fg="#37474F", bg="white").pack(anchor="w", pady=(0, 2))
        if channel_title:
            row("Nama Channel:", channel_title, "#1B5E20", bold=True)
            row("Channel ID:", channel_id)
            row("URL:", channel_url, "#1565C0")
        else:
            row("Channel:", "⚠️ Tidak terdeteksi — periksa Koneksi OAuth", "#B71C1C")

        sep()

        # Statistik video
        tk.Label(body, text="STATISTIK VIDEO", font=("Segoe UI", 9, "bold"),
                 fg="#37474F", bg="white").pack(anchor="w", pady=(0, 2))
        row("Total di CSV:", str(len(all_entries)))
        row("Sudah diupload:", str(already_done), "#757575")
        row("Status tidak pasti:", str(uncertain_count),
            "#E65100" if uncertain_count else "#757575")
        row("Sesi ini (batch):", f"{len(entries)} video   [ukuran batch: {batch_label}]",
            "#212121", bold=True)
        row("Total ukuran file:", f"{total_size_gb:.2f} GB")
        row("Estimasi waktu:", est_str, "#1565C0")

        sep()

        # Privasi
        tk.Label(body, text="PENGATURAN PRIVASI", font=("Segoe UI", 9, "bold"),
                 fg="#37474F", bg="white").pack(anchor="w", pady=(0, 2))
        row("Privasi upload:", privacy_label, privacy_color, bold=True)

        # Kotak peringatan khusus untuk UNLISTED/PUBLIC
        if selected_privacy in ("unlisted", "public"):
            notice_bg = "#E8F5E9" if selected_privacy == "unlisted" else "#FFF3E0"
            notice_border = "#2E7D32" if selected_privacy == "unlisted" else "#E65100"
            notice_text = (
                "Video UNLISTED hanya bisa ditemukan oleh orang yang memiliki link.\n"
                "Cocok untuk dibagikan secara terbatas."
                if selected_privacy == "unlisted"
                else "⚠️  Video PUBLIC langsung terlihat oleh semua orang di YouTube!\n"
                     "Pastikan Anda sudah yakin sebelum melanjutkan."
            )
            notice = tk.Label(
                body, text=notice_text, font=("Segoe UI", 8, "italic"),
                fg=notice_border, bg=notice_bg,
                wraplength=520, justify="left", relief="solid", bd=1,
                padx=10, pady=6,
            )
            notice.pack(fill=tk.X, pady=4)

        sep()

        # Peringatan uncertain
        if uncertain_count > 0:
            warn = tk.Label(
                body,
                text=(
                    f"⚠️  {uncertain_count} video memiliki status 'Tidak Pasti' (upload mungkin sudah berhasil).\n"
                    "Disarankan periksa YouTube Studio sebelum melanjutkan untuk menghindari duplikat."
                ),
                font=("Segoe UI", 8), fg="#E65100", bg="#FFF8E1",
                wraplength=520, justify="left", relief="solid", bd=1,
                padx=8, pady=5,
            )
            warn.pack(fill=tk.X, pady=(0, 4))

        # Disclaimer
        tk.Label(
            body,
            text="Upload berjalan satu per satu secara berurutan. Anda bisa membatalkan kapan saja.",
            font=("Segoe UI", 8, "italic"), fg="#757575", bg="white",
            wraplength=520, justify="left",
        ).pack(anchor="w")

        # Tombol
        btn_frame = tk.Frame(dialog, bg="#F5F5F5", pady=12)
        btn_frame.pack(fill=tk.X, side=tk.BOTTOM)

        clicked = [False]

        def on_confirm():
            if clicked[0]:
                return
            clicked[0] = True
            dialog.destroy()
            self._start_upload_with_settings(
                entries=entries,
                override_privacy=selected_privacy,
                batch_size=batch_size,
            )

        def on_cancel():
            dialog.destroy()
            self._log("⛔ Bulk upload dibatalkan oleh pengguna.")
            self._update_status("Upload dibatalkan.")

        tk.Button(
            btn_frame, text="⛔  Batal", command=on_cancel,
            bg="#9E9E9E", fg="white", font=("Segoe UI", 10, "bold"),
            relief=tk.FLAT, padx=18, pady=6, cursor="hand2",
        ).pack(side=tk.RIGHT, padx=(8, 16))

        confirm_text = f"✅  Ya, Upload {len(entries)} Video ({selected_privacy.upper()})"
        tk.Button(
            btn_frame, text=confirm_text, command=on_confirm,
            bg=hdr_color, fg="white", font=("Segoe UI", 10, "bold"),
            relief=tk.FLAT, padx=18, pady=6, cursor="hand2",
        ).pack(side=tk.RIGHT, padx=4)

        dialog.bind("<Escape>", lambda e: on_cancel())
        # Enter tidak langsung konfirmasi — harus klik tombol secara eksplisit

    def _start_upload_with_settings(
        self,
        entries: List[Dict],
        override_privacy: str,
        batch_size: Optional[int],
    ):
        """
        Mulai bulk upload dengan privacy override dan batch size yang sudah dikonfirmasi.
        Privacy dikirim sebagai parameter ke BulkUploadManager.process_queue(),
        bukan hanya disimpan di GUI — ini memastikan YouTube API menerima nilai yang benar.
        """
        if self._is_uploading:
            messagebox.showwarning("Upload Berjalan", "Upload lain sedang berjalan.")
            return

        self._is_uploading = True
        self.cancel_event.clear()
        self._update_button_states()
        self._log(
            f"⬆️  Memulai bulk upload: {len(entries)} video | "
            f"Privasi: {override_privacy.upper()} | "
            f"Batch: {batch_size or 'semua'}",
            "info",
        )

        # Gunakan service yang di-cache dari saat ambil channel info
        cached_svc = getattr(self, "_cached_service", None)

        def run():
            try:
                from src.uploader import BulkUploadManager

                if cached_svc:
                    service = cached_svc
                else:
                    from src.auth import get_credentials, build_youtube_service
                    creds = get_credentials()
                    service = build_youtube_service(creds)

                self.after(0, lambda: self._log("✅ Koneksi YouTube OK, memulai upload...", "success"))

                def on_progress(upload_id, bytes_sent, total, percent):
                    self.after(0, lambda: self._on_progress(upload_id, bytes_sent, total, percent))

                def on_status(upload_id, msg):
                    self.after(0, lambda: self._log(msg))

                def on_queue(upload_id, status):
                    self.after(0, lambda: self._refresh_from_db())

                manager = BulkUploadManager(
                    youtube_service=service,
                    database=self.db,
                    progress_callback=on_progress,
                    status_callback=on_status,
                    queue_callback=on_queue,
                    cancel_event=self.cancel_event,
                )

                stats = manager.process_queue(
                    entries=entries,
                    override_privacy=override_privacy,
                    batch_size=batch_size,
                )
                self.after(0, lambda: self._on_upload_complete(stats))

            except Exception as e:
                self.after(0, lambda: self._on_upload_error(str(e)))

        self.upload_thread = threading.Thread(target=run, daemon=True)
        self.upload_thread.start()

    def _start_upload(self, entries: List[Dict]):
        """Mulai proses upload di background thread."""
        if self._is_uploading:
            messagebox.showwarning("Upload Sedang Berjalan", "Upload sudah berjalan.")
            return

        self._is_uploading = True
        self.cancel_event.clear()
        self._update_button_states()
        self._log("⬆️  Memulai proses upload...", "info")

        def run():
            try:
                from src.auth import get_credentials, build_youtube_service, AuthError
                from src.uploader import BulkUploadManager

                self.after(0, lambda: self._update_status("Menghubungkan ke YouTube..."))
                self.after(0, lambda: self._log("Memuat credentials OAuth..."))

                try:
                    creds = get_credentials()
                    service = build_youtube_service(creds)
                    self.after(0, lambda: self._log("✅ Koneksi YouTube berhasil!", "success"))
                except Exception as e:
                    self.after(0, lambda: self._on_upload_error(str(e)))
                    return

                def on_progress(upload_id, bytes_sent, total, percent):
                    self.after(
                        0,
                        lambda: self._on_progress(upload_id, bytes_sent, total, percent),
                    )

                def on_status(upload_id, msg):
                    self.after(0, lambda: self._log(msg))

                def on_queue(upload_id, status):
                    self.after(0, lambda: self._refresh_from_db())

                manager = BulkUploadManager(
                    youtube_service=service,
                    database=self.db,
                    progress_callback=on_progress,
                    status_callback=on_status,
                    queue_callback=on_queue,
                    cancel_event=self.cancel_event,
                )

                stats = manager.process_queue(entries)
                self.after(0, lambda: self._on_upload_complete(stats))

            except Exception as e:
                self.after(0, lambda: self._on_upload_error(str(e)))

        self.upload_thread = threading.Thread(target=run, daemon=True)
        self.upload_thread.start()

    def _on_progress(self, upload_id: int, bytes_sent: int, total: int, percent: float):
        """Update progress bar."""
        self.progress_bar["value"] = percent
        mb_sent = bytes_sent / 1024 / 1024
        mb_total = total / 1024 / 1024
        self.lbl_progress.config(
            text=f"Upload ID {upload_id}: {mb_sent:.1f} MB / {mb_total:.1f} MB ({percent:.1f}%)"
        )

    def _on_upload_complete(self, stats: Dict):
        """Dipanggil setelah semua upload selesai."""
        self._is_uploading = False
        self.progress_bar["value"] = 100
        self._update_button_states()
        self._refresh_from_db()

        msg = (
            f"UPLOAD SELESAI\n\n"
            f"✅ Berhasil    : {stats.get('success', 0)}\n"
            f"❌ Gagal       : {stats.get('failed', 0)}\n"
            f"⚠️  Tidak Pasti : {stats.get('uncertain', 0)}\n"
            f"⏭️  Dilewati    : {stats.get('skipped', 0)}\n"
            f"⛔ Dibatalkan  : {stats.get('cancelled', 0)}\n\n"
            f"Lihat laporan untuk detail lengkap."
        )
        self._log(
            f"✅ Upload selesai! Berhasil: {stats.get('success', 0)}, "
            f"Gagal: {stats.get('failed', 0)}",
            "success",
        )
        messagebox.showinfo("Upload Selesai", msg)

    def _on_upload_error(self, error: str):
        """Dipanggil jika terjadi error fatal."""
        self._is_uploading = False
        self._update_button_states()
        self._log(f"❌ Error upload: {error}", "error")
        messagebox.showerror("Error Upload", f"Upload gagal:\n\n{error}")

    def _cancel_upload(self):
        """Batalkan upload yang sedang berjalan, atau scan yang sedang berjalan."""
        if not self._is_uploading and not self.cancel_event.is_set():
            # Coba batalkan scan
            scan_thread_alive = any(
                t.name == "ScanThread" and t.is_alive()
                for t in threading.enumerate()
            )
            if scan_thread_alive:
                self.cancel_event.set()
                self._log("⏹  Pembatalan scan diminta...", "warning")
            return
        if self._is_uploading:
            if messagebox.askyesno(
                "Batalkan Upload",
                "Yakin ingin membatalkan upload?\n\nUpload yang sudah berjalan akan disimpan statusnya.",
            ):
                self.cancel_event.set()
                self._log("⏹  Pembatalan upload diminta...", "warning")

    # ------------------------------------------------------------------ #
    #  UJI UPLOAD 1 VIDEO
    # ------------------------------------------------------------------ #

    def _test_upload_selected(self):
        """
        Entry point tombol "🧪 Uji Upload 1 Video".
        Memastikan tepat satu baris dipilih, lalu menjalankan semua
        pre-flight checks sebelum menampilkan dialog konfirmasi.
        Upload nyata TIDAK dilakukan tanpa persetujuan eksplisit di dialog.
        """
        # --- Validasi seleksi: harus tepat 1 baris ---
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning(
                "Pilih Video",
                "Silakan pilih tepat satu video dari daftar terlebih dahulu.\n\n"
                "Klik satu baris di tabel, lalu klik tombol 'Uji Upload 1 Video'.",
            )
            return

        if len(selected) > 1:
            messagebox.showwarning(
                "Terlalu Banyak Pilihan",
                f"Anda memilih {len(selected)} video.\n\n"
                "Fitur uji upload hanya untuk 1 video.\n"
                "Silakan pilih satu baris saja.",
            )
            return

        # --- Ambil metadata baris yang dipilih ---
        item_values = self.tree.item(selected[0], "values")
        # values: (no, filename, title, size, status, privacy)
        filename_in_table = item_values[1] if len(item_values) > 1 else ""

        # Cari metadata lengkap — prioritas dari scanned_entries, fallback dari database
        metadata = self._find_metadata_for_filename(filename_in_table)
        if metadata is None:
            messagebox.showerror(
                "Data Tidak Ditemukan",
                f"Tidak bisa menemukan data lengkap untuk:\n{filename_in_table}\n\n"
                "Pastikan Anda sudah melakukan Pindai atau metadata.csv ada.",
            )
            return

        # --- Periksa OAuth ---
        secret_path = Path.cwd() / "client_secret.json"
        if not secret_path.exists():
            messagebox.showerror(
                "OAuth Belum Dikonfigurasi",
                "File client_secret.json tidak ditemukan!\n\n"
                "Silakan:\n"
                "1. Buka Bantuan > Panduan OAuth Setup\n"
                "2. Ikuti instruksi mendapatkan credentials\n"
                "3. Taruh client_secret.json di folder aplikasi\n"
                "4. Klik 'Koneksi OAuth' untuk login",
            )
            return

        # --- Jalankan pre-flight checks di background thread ---
        self._log(
            f"🔍 Menjalankan pemeriksaan pra-upload untuk: {filename_in_table}..."
        )
        self._update_status("Memeriksa file dan koneksi OAuth...")
        self.btn_test_upload.config(state=tk.DISABLED)

        def do_preflight():
            try:
                from src.auth import get_credentials, build_youtube_service, AuthError

                # Dapatkan service YouTube (atau gunakan yang sudah ada)
                try:
                    creds = get_credentials()
                    service = build_youtube_service(creds)
                except Exception as e:
                    self.after(0, lambda: self._on_preflight_error(
                        filename_in_table,
                        f"Gagal terhubung ke YouTube:\n{e}\n\n"
                        "Klik 'Koneksi OAuth' untuk memperbarui login."
                    ))
                    return

                preflight = run_preflight(
                    metadata=metadata,
                    youtube_service=service,
                    database=self.db,
                    compute_hash=True,
                )
                self.after(
                    0,
                    lambda: self._on_preflight_complete(preflight, service, metadata),
                )
            except Exception as e:
                self.after(
                    0,
                    lambda: self._on_preflight_error(filename_in_table, str(e)),
                )

        threading.Thread(target=do_preflight, daemon=True).start()

    def _find_metadata_for_filename(self, filename: str) -> Optional[Dict]:
        """
        Cari metadata lengkap berdasarkan nama file.
        Cari di: (1) scanned_entries, (2) metadata.csv, (3) database.
        """
        # 1. Cari di scanned_entries (paling up-to-date)
        for entry in self.scanned_entries:
            if entry.get("filename") == filename:
                return dict(entry)

        # 2. Cari di metadata.csv
        csv_path = Path.cwd() / "metadata.csv"
        if csv_path.exists():
            try:
                entries = load_metadata_csv(str(csv_path))
                for entry in entries:
                    if entry.get("filename") == filename:
                        return dict(entry)
            except Exception:
                pass

        # 3. Cari di database
        if self.db:
            try:
                rows = self.db.get_all_uploads()
                for row in rows:
                    r = dict(row)
                    if r.get("filename") == filename:
                        return r
            except Exception:
                pass

        return None

    def _on_preflight_error(self, filename: str, error: str):
        """Dipanggil jika pre-flight check gagal dengan error teknis."""
        self.btn_test_upload.config(state=tk.NORMAL)
        self._update_status("Pemeriksaan pra-upload gagal.")
        self._log(f"❌ Pre-flight error untuk '{filename}': {error}", "error")
        messagebox.showerror(
            "Pemeriksaan Gagal",
            f"Pemeriksaan pra-upload gagal untuk:\n{filename}\n\n{error}",
        )

    def _on_preflight_complete(
        self,
        preflight: "PreflightResult",
        youtube_service,
        metadata: Dict,
    ):
        """
        Dipanggil setelah pre-flight checks selesai.
        Jika ada error, tampilkan pesan.
        Jika lolos, tampilkan dialog konfirmasi detail sebelum upload.
        """
        self.btn_test_upload.config(state=tk.NORMAL)
        self._update_status("Pemeriksaan selesai.")

        # Log semua warnings
        for w in preflight.warnings:
            self._log(f"⚠️  {w}", "warning")

        # Jika ada error, tampilkan dan hentikan
        if not preflight.ok:
            error_msg = "\n\n".join(preflight.errors)
            self._log(f"❌ Pre-flight gagal: {error_msg}", "error")
            messagebox.showerror(
                "Pemeriksaan Pra-Upload Gagal",
                f"File tidak bisa diupload karena:\n\n{error_msg}",
            )
            return

        # Tampilkan dialog konfirmasi detail
        self._show_test_upload_confirmation(preflight, youtube_service, metadata)

    def _show_test_upload_confirmation(
        self,
        preflight: "PreflightResult",
        youtube_service,
        metadata: Dict,
    ):
        """
        Tampilkan dialog konfirmasi lengkap sebelum upload.
        Dialog berisi: filename, judul, channel name+ID, privasi, ukuran file,
        dan peringatan duplikat jika ada.
        Upload HANYA dilakukan jika pengguna klik "Ya, Upload Sekarang".
        """
        dialog = tk.Toplevel(self)
        dialog.title("Konfirmasi Uji Upload")
        dialog.geometry("560x540")
        dialog.resizable(False, False)
        dialog.grab_set()  # modal
        dialog.focus_set()

        # Warna untuk teks berdasarkan status
        warn_color = "#E65100"  # oranye gelap
        ok_color = "#1B5E20"    # hijau gelap
        label_color = "#555555"

        # --- Header ---
        header_frame = tk.Frame(dialog, bg="#1565C0", height=55)
        header_frame.pack(fill=tk.X)
        header_frame.pack_propagate(False)
        tk.Label(
            header_frame,
            text="🧪  Konfirmasi Uji Upload ke YouTube",
            font=("Segoe UI", 13, "bold"),
            fg="white",
            bg="#1565C0",
        ).pack(side=tk.LEFT, padx=16, pady=12)

        # --- Body ---
        body = tk.Frame(dialog, bg="white", padx=20, pady=15)
        body.pack(fill=tk.BOTH, expand=True)

        def add_row(label: str, value: str, value_color: str = "#212121", bold: bool = False):
            row = tk.Frame(body, bg="white")
            row.pack(fill=tk.X, pady=3)
            tk.Label(
                row, text=label, width=18, anchor="w",
                font=("Segoe UI", 9), fg=label_color, bg="white",
            ).pack(side=tk.LEFT)
            font_weight = "bold" if bold else "normal"
            tk.Label(
                row, text=value, anchor="w",
                font=("Segoe UI", 9, font_weight), fg=value_color, bg="white",
                wraplength=350, justify="left",
            ).pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Pemisah
        def separator():
            tk.Frame(body, bg="#E0E0E0", height=1).pack(fill=tk.X, pady=4)

        # Informasi file
        tk.Label(
            body, text="INFORMASI FILE", font=("Segoe UI", 9, "bold"),
            fg="#1565C0", bg="white",
        ).pack(anchor="w", pady=(0, 2))

        add_row("Nama File:", preflight.filename, bold=True)
        add_row("Judul Video:", preflight.title, bold=True)
        add_row("Ukuran File:", f"{preflight.file_size_mb:.2f} MB ({preflight.file_size_bytes:,} bytes)")
        add_row("Path:", preflight.filepath, label_color)

        separator()

        # Informasi channel tujuan
        tk.Label(
            body, text="CHANNEL TUJUAN", font=("Segoe UI", 9, "bold"),
            fg="#1565C0", bg="white",
        ).pack(anchor="w", pady=(0, 2))

        if preflight.channel_title:
            add_row("Nama Channel:", preflight.channel_title, ok_color, bold=True)
            add_row("Channel ID:", preflight.channel_id)
            add_row("URL Channel:", preflight.channel_url, "#1565C0")
        else:
            add_row("Channel:", "⚠️ Tidak terdeteksi", warn_color)

        separator()

        # Privasi
        tk.Label(
            body, text="PENGATURAN UPLOAD", font=("Segoe UI", 9, "bold"),
            fg="#1565C0", bg="white",
        ).pack(anchor="w", pady=(0, 2))

        add_row("Privasi:", "🔒 PRIVATE (terkunci)", ok_color, bold=True)
        add_row("Jenis Upload:", "Uji upload — 1 video saja")

        # Peringatan duplikat
        if preflight.is_duplicate:
            separator()
            tk.Label(
                body, text="⚠️  PERINGATAN DUPLIKAT", font=("Segoe UI", 9, "bold"),
                fg=warn_color, bg="white",
            ).pack(anchor="w", pady=(0, 2))
            warn_text = "\n".join(preflight.warnings) if preflight.warnings else "File ini terdeteksi sebagai duplikat."
            tk.Label(
                body, text=warn_text, anchor="w",
                font=("Segoe UI", 9), fg=warn_color, bg="#FFF8E1",
                wraplength=480, justify="left", relief="solid", bd=1,
                padx=8, pady=6,
            ).pack(fill=tk.X, pady=4)

        separator()

        # Disclaimer
        disclaimer = (
            "Video akan diupload sebagai PRIVATE — tidak terlihat oleh publik.\n"
            "Anda bisa mengubah privasi kapan saja di YouTube Studio."
        )
        tk.Label(
            body, text=disclaimer, font=("Segoe UI", 8, "italic"),
            fg="#757575", bg="white", wraplength=480, justify="left",
        ).pack(anchor="w", pady=(4, 0))

        # --- Tombol aksi ---
        btn_frame = tk.Frame(dialog, bg="#F5F5F5", pady=12)
        btn_frame.pack(fill=tk.X, side=tk.BOTTOM)

        # State untuk mencegah double-click
        confirmed = [False]

        def on_confirm():
            if confirmed[0]:
                return
            confirmed[0] = True
            dialog.destroy()
            self._execute_test_upload(preflight, youtube_service, metadata)

        def on_cancel():
            dialog.destroy()
            self._log(
                f"⛔ Uji upload dibatalkan oleh pengguna: {preflight.filename}"
            )
            self._update_status("Uji upload dibatalkan.")

        tk.Button(
            btn_frame,
            text="⛔  Batal",
            command=on_cancel,
            bg="#9E9E9E",
            fg="white",
            font=("Segoe UI", 10, "bold"),
            relief=tk.FLAT,
            padx=18,
            pady=6,
            cursor="hand2",
        ).pack(side=tk.RIGHT, padx=(8, 16))

        btn_upload = tk.Button(
            btn_frame,
            text="✅  Ya, Upload Sekarang",
            command=on_confirm,
            bg="#1565C0",
            fg="white",
            font=("Segoe UI", 10, "bold"),
            relief=tk.FLAT,
            padx=18,
            pady=6,
            cursor="hand2",
        )
        # Jika duplikat: warnakan tombol upload
        if preflight.is_duplicate:
            btn_upload.config(
                text="⚠️  Upload Meski Duplikat",
                bg=warn_color,
            )
        btn_upload.pack(side=tk.RIGHT, padx=4)

        # Fokus ke tombol Batal secara default (lebih aman)
        dialog.bind("<Return>", lambda e: on_cancel())
        dialog.bind("<Escape>", lambda e: on_cancel())

    def _execute_test_upload(
        self,
        preflight: "PreflightResult",
        youtube_service,
        metadata: Dict,
    ):
        """
        Jalankan upload nyata untuk 1 video setelah konfirmasi pengguna.
        Upload dijalankan di background thread, sama persis dengan _start_upload.
        """
        if self._is_uploading:
            messagebox.showwarning("Upload Berjalan", "Upload lain sedang berjalan.")
            return

        self._is_uploading = True
        self.cancel_event.clear()
        self._update_button_states()
        self._log(
            f"⬆️  Memulai uji upload: {preflight.filename} → "
            f"Channel: {preflight.channel_title} ({preflight.channel_id})",
            "info",
        )

        # Pastikan privacy selalu private
        upload_metadata = dict(metadata)
        upload_metadata["privacy"] = "private"

        def run():
            try:
                from src.uploader import BulkUploadManager

                def on_progress(upload_id, bytes_sent, total, percent):
                    self.after(
                        0,
                        lambda: self._on_progress(upload_id, bytes_sent, total, percent),
                    )

                def on_status(upload_id, msg):
                    self.after(0, lambda: self._log(msg))

                def on_queue(upload_id, status):
                    self.after(0, lambda: self._refresh_from_db())

                manager = BulkUploadManager(
                    youtube_service=youtube_service,
                    database=self.db,
                    progress_callback=on_progress,
                    status_callback=on_status,
                    queue_callback=on_queue,
                    cancel_event=self.cancel_event,
                )

                # Proses hanya 1 video ini
                stats = manager.process_queue([upload_metadata])
                self.after(0, lambda: self._on_test_upload_complete(
                    stats, preflight.filename, preflight.channel_title
                ))

            except Exception as e:
                self.after(0, lambda: self._on_upload_error(str(e)))

        threading.Thread(target=run, daemon=True).start()

    def _on_test_upload_complete(self, stats: Dict, filename: str, channel: str):
        """Dipanggil setelah uji upload selesai."""
        self._is_uploading = False
        self.progress_bar["value"] = 100 if stats.get("success") else 0
        self._update_button_states()
        self._refresh_from_db()

        if stats.get("success", 0) == 1:
            # Ambil video ID dari database
            yt_id = ""
            if self.db:
                rows = self.db.get_all_uploads()
                for row in rows:
                    r = dict(row)
                    if r.get("filename") == filename and r.get("status") == "success":
                        yt_id = r.get("youtube_video_id", "")
                        break

            msg = (
                f"✅ UJI UPLOAD BERHASIL!\n\n"
                f"File     : {filename}\n"
                f"Channel  : {channel}\n"
                f"Video ID : {yt_id or '(lihat riwayat)'}\n"
                f"URL      : {'https://youtu.be/' + yt_id if yt_id else '(lihat YouTube Studio)'}\n\n"
                f"Video tersimpan sebagai PRIVATE di YouTube Studio.\n"
                f"Anda bisa mengubah privasi kapan saja di studio.youtube.com"
            )
            self._log(
                f"✅ Uji upload berhasil! File: {filename}, YouTube ID: {yt_id}",
                "success",
            )
            messagebox.showinfo("Uji Upload Berhasil", msg)
        else:
            msg = (
                f"Uji upload selesai dengan hasil:\n\n"
                f"✅ Berhasil    : {stats.get('success', 0)}\n"
                f"❌ Gagal       : {stats.get('failed', 0)}\n"
                f"⚠️  Tidak Pasti : {stats.get('uncertain', 0)}\n"
                f"⏭️  Dilewati    : {stats.get('skipped', 0)}\n\n"
                f"Lihat laporan untuk detail lengkap."
            )
            self._log(
                f"Upload selesai. Berhasil: {stats.get('success', 0)}, "
                f"Gagal: {stats.get('failed', 0)}",
                "warning" if stats.get("failed", 0) else "info",
            )
            messagebox.showinfo("Hasil Uji Upload", msg)

    def _run_oauth(self):
        """Jalankan proses otorisasi OAuth."""
        secret_path = Path.cwd() / "client_secret.json"
        if not secret_path.exists():
            messagebox.showerror(
                "File Tidak Ada",
                "client_secret.json tidak ditemukan.\n\n"
                "Silakan buka Bantuan > Panduan OAuth Setup.",
            )
            return

        self._log("Memulai proses otorisasi OAuth...")
        self._log("Browser akan terbuka, ikuti instruksi di browser...")

        def do_oauth():
            try:
                from src.auth import get_credentials, build_youtube_service
                creds = get_credentials(force_refresh=True)
                service = build_youtube_service(creds)
                # Test koneksi
                service.channels().list(part="id", mine=True).execute()
                self.after(
                    0,
                    lambda: self._on_oauth_success(),
                )
            except Exception as e:
                self.after(0, lambda: self._on_oauth_error(str(e)))

        threading.Thread(target=do_oauth, daemon=True).start()

    def _on_oauth_success(self):
        self._log("✅ Koneksi OAuth berhasil! Token disimpan.", "success")
        messagebox.showinfo(
            "OAuth Berhasil",
            "Koneksi ke YouTube berhasil!\n\nToken tersimpan, Anda sudah bisa upload.",
        )

    def _on_oauth_error(self, error: str):
        self._log(f"❌ OAuth gagal: {error}", "error")
        messagebox.showerror("OAuth Gagal", f"Koneksi gagal:\n\n{error}")

    def _generate_report(self):
        """Buat laporan HTML dan buka di browser."""
        if not self.db:
            messagebox.showwarning("Database", "Database tidak tersedia.")
            return

        try:
            rows = self.db.get_all_uploads()
            stats = self.db.get_summary()

            if not rows:
                messagebox.showinfo(
                    "Laporan",
                    "Belum ada riwayat upload untuk dilaporkan.",
                )
                return

            report_dir = Path.cwd() / "reports"
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            html_path = report_dir / f"laporan_{timestamp}.html"

            report_path = generate_html_report(list(rows), str(html_path), stats)
            self._log(f"📊 Laporan dibuat: {report_path}", "success")

            # Buka di browser default
            try:
                os.startfile(report_path)
            except AttributeError:
                subprocess.Popen(["xdg-open", report_path])

        except Exception as e:
            self._log(f"❌ Error laporan: {e}", "error")
            messagebox.showerror("Error Laporan", f"Gagal membuat laporan:\n{e}")

    def _reset_failed(self):
        """Reset semua upload gagal ke pending untuk dicoba ulang."""
        if not self.db:
            return
        if messagebox.askyesno(
            "Reset Upload Gagal",
            "Reset semua upload GAGAL ke status PENDING?\n\nMereka akan dicoba ulang saat upload berikutnya.",
        ):
            conn = self.db._get_conn()
            failed_ids = conn.execute(
                "SELECT id FROM uploads WHERE status = 'failed'"
            ).fetchall()
            for row in failed_ids:
                self.db.reset_to_pending(row["id"])
            count = len(failed_ids)
            self._log(f"✅ {count} upload gagal direset ke pending.", "success")
            self._refresh_from_db()

    # ------------------------------------------------------------------ #
    #  HELPER UI
    # ------------------------------------------------------------------ #

    def _populate_table(self, entries: List[Dict]):
        """
        Isi tabel dengan hasil scan.

        Perbaikan v1.3.0: satu query DB untuk semua filepaths (bukan N query),
        lalu batch insert ke Treeview dengan tree.delete() sekali di awal.
        """
        import time as _t
        t0 = _t.perf_counter()

        # Hapus semua baris sekaligus
        self.tree.delete(*self.tree.get_children())

        # ---- Satu query batch untuk semua status ----
        # Ambil status dari DB dalam satu panggilan, bukan 291 query terpisah
        status_map: Dict[str, tuple] = {}  # filepath → (status_label, tag)
        if self.db and entries:
            try:
                # Ambil semua upload sekaligus
                all_rows = self.db.get_all_uploads()
                db_status_lookup = {
                    dict(r)["filepath"]: dict(r)
                    for r in all_rows
                }
                label_map = {
                    "success": ("Berhasil", "success"),
                    "failed": ("Gagal", "failed"),
                    "uncertain": ("Tidak Pasti", "uncertain"),
                    "in_progress": ("Uploading", "in_progress"),
                    "skipped": ("Dilewati", "skipped"),
                    "pending": ("Menunggu", "pending"),
                }
                for entry in entries:
                    fp = entry.get("filepath", "")
                    db_row = db_status_lookup.get(fp)
                    if db_row:
                        st = db_row.get("status", "")
                        status_map[fp] = label_map.get(st, (st, ""))
            except Exception as e:
                logger.warning("Tidak bisa muat status DB untuk tabel: %s", e)

        # ---- Batch insert ke Treeview ----
        for i, entry in enumerate(entries, 1):
            fp = entry.get("filepath", "")
            status_label, tag = status_map.get(fp, ("Menunggu", ""))
            size_str = f"{entry.get('file_size_mb', 0):.1f} MB"
            self.tree.insert(
                "",
                tk.END,
                values=(
                    i,
                    entry.get("filename", ""),
                    entry.get("title", ""),
                    size_str,
                    status_label,
                    entry.get("privacy", "private"),
                ),
                tags=(tag,) if tag else (),
            )

        elapsed = _t.perf_counter() - t0
        logger.info(
            "[TIMING] _populate_table: %d baris dalam %.3fs", len(entries), elapsed
        )

    def _refresh_from_db(self):
        """Muat ulang tabel dari database."""
        if not self.db:
            return
        try:
            rows = self.db.get_all_uploads()
            for item in self.tree.get_children():
                self.tree.delete(item)

            for row in rows:
                r = dict(row)
                status_map = {
                    "success": "✅ Berhasil",
                    "failed": "❌ Gagal",
                    "uncertain": "⚠️ Tdk Pasti",
                    "in_progress": "⬆️ Uploading",
                    "skipped": "⏭️ Dilewati",
                    "pending": "⏳ Menunggu",
                }
                status = status_map.get(r.get("status", ""), r.get("status", ""))
                self.tree.insert(
                    "",
                    tk.END,
                    values=(
                        r.get("id"),
                        r.get("filename"),
                        r.get("title", "")[:60],
                        "",
                        status,
                        r.get("privacy"),
                    ),
                    tags=(r.get("status", ""),),
                )
            stats = self.db.get_summary()
            self.lbl_count.config(text=f"{stats.get('total', 0)} total | {stats.get('success', 0)} berhasil")
        except Exception as e:
            logger.error("Refresh DB error: %s", e)

    def _log(self, message: str, level: str = "info"):
        """Tambahkan pesan ke area log."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        full_msg = f"[{timestamp}] {message}\n"

        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, full_msg, level)
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)

    def _update_status(self, message: str):
        """Update status bar bawah."""
        self.lbl_status.config(text=message)

    def _update_button_states(self):
        """Enable/disable tombol sesuai kondisi."""
        has_folder = bool(self.source_folder.get().strip())
        has_csv = (Path.cwd() / "metadata.csv").exists()
        has_selection = bool(self.tree.selection()) if hasattr(self, "tree") else False

        self.btn_scan.config(state=tk.NORMAL if has_folder and not self._is_uploading else tk.DISABLED)
        self.btn_preview.config(state=tk.NORMAL if has_csv else tk.DISABLED)
        self.btn_test_upload.config(
            state=tk.NORMAL if has_selection and not self._is_uploading else tk.DISABLED
        )
        self.btn_upload.config(state=tk.NORMAL if has_csv and not self._is_uploading else tk.DISABLED)
        self.btn_cancel.config(state=tk.NORMAL if self._is_uploading else tk.DISABLED)
        self.btn_report.config(state=tk.NORMAL)

    def _update_clock(self):
        """Update jam di status bar setiap detik."""
        now = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        self.lbl_time.config(text=now)
        self.after(1000, self._update_clock)

    # ------------------------------------------------------------------ #
    #  DIALOG INFORMASI
    # ------------------------------------------------------------------ #

    def _show_oauth_guide(self):
        """Tampilkan panduan setup OAuth."""
        guide = """PANDUAN SETUP GOOGLE OAUTH 2.0
================================

1. Buka: https://console.cloud.google.com/

2. Buat project baru (atau pilih yang ada)
   - Klik "New Project"
   - Beri nama, misalnya "YouTube Uploader"

3. Aktifkan YouTube Data API v3:
   - APIs & Services > Library
   - Cari "YouTube Data API v3"
   - Klik Enable

4. Buat OAuth Credentials:
   - APIs & Services > Credentials
   - Create Credentials > OAuth 2.0 Client ID
   - Application type: Desktop App
   - Beri nama, klik Create

5. Download file JSON:
   - Klik tombol Download (ikon unduh)
   - Rename file menjadi: client_secret.json
   - Pindahkan ke folder aplikasi ini

6. Tambahkan akun YouTube Anda sebagai Test User:
   - OAuth consent screen > Test users
   - Add your YouTube account email

7. Klik tombol "🔑 Koneksi OAuth" di aplikasi
   - Browser akan terbuka
   - Login dengan akun Google/YouTube Anda
   - Izinkan akses yang diminta

CATATAN PENTING:
• Jangan bagikan client_secret.json kepada siapapun
• Token tersimpan di token.pickle (jangan dihapus)
• Kuota upload: 6 video/hari per project (default)
• Untuk meningkatkan kuota, ajukan verifikasi OAuth di Google"""

        dialog = tk.Toplevel(self)
        dialog.title("Panduan OAuth Setup")
        dialog.geometry("600x500")

        text = scrolledtext.ScrolledText(dialog, wrap=tk.WORD, font=("Consolas", 10))
        text.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        text.insert("1.0", guide)
        text.config(state=tk.DISABLED)

        tk.Button(
            dialog,
            text="Buka Google Cloud Console",
            command=lambda: (
                subprocess.Popen(["start", "https://console.cloud.google.com/"], shell=True)
                if sys.platform == "win32"
                else subprocess.Popen(["xdg-open", "https://console.cloud.google.com/"])
            ),
            bg="#4285F4",
            fg="white",
            font=("Segoe UI", 10, "bold"),
        ).pack(pady=5)

    def _show_about(self):
        messagebox.showinfo(
            "Tentang Aplikasi",
            f"{APP_TITLE} v{APP_VERSION}\n\n"
            "Upload massal video Zoom ke YouTube\n"
            "menggunakan YouTube Data API v3\n\n"
            "Fitur:\n"
            "• Pindai MP4 rekursif\n"
            "• Resume upload yang terputus\n"
            "• Retry otomatis\n"
            "• SQLite untuk riwayat\n"
            "• Laporan HTML\n\n"
            "Semua video default: PRIVATE\n"
            "Tidak ada upload tanpa konfirmasi.",
        )


def run_gui():
    """Entry point untuk GUI."""
    from src.logger_config import setup_logging
    setup_logging(console=True)
    app = BulkUploaderApp()
    app.mainloop()


if __name__ == "__main__":
    run_gui()
