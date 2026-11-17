"""
main.py - Entry point untuk YouTube Bulk Uploader.
Bisa dijalankan dengan GUI (default) atau CLI (--cli).
"""

import sys
import os
import argparse
import logging
from pathlib import Path

# Pastikan direktori proyek ada di path
sys.path.insert(0, str(Path(__file__).parent))


def parse_args():
    parser = argparse.ArgumentParser(
        description="YouTube Bulk Uploader - Upload massal video Zoom ke YouTube"
    )
    parser.add_argument(
        "--cli",
        action="store_true",
        help="Jalankan dalam mode CLI tanpa GUI",
    )
    parser.add_argument(
        "--scan",
        type=str,
        metavar="FOLDER",
        help="[CLI] Pindai folder dan buat metadata.csv",
    )
    parser.add_argument(
        "--csv",
        type=str,
        default="metadata.csv",
        help="[CLI] Path ke metadata.csv (default: metadata.csv)",
    )
    parser.add_argument(
        "--db",
        type=str,
        default="upload_history.db",
        help="[CLI] Path ke database SQLite (default: upload_history.db)",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="[CLI] Buat laporan HTML dari riwayat upload",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Level logging",
    )
    return parser.parse_args()


def run_cli(args):
    """Jalankan dalam mode CLI."""
    from src.logger_config import setup_logging
    from src.scanner import scan_mp4_files, save_metadata_csv, load_metadata_csv, validate_entries
    from src.database import UploadDatabase
    from src.reporter import generate_html_report, print_summary

    setup_logging(log_level=args.log_level)
    logger = logging.getLogger(__name__)

    db = UploadDatabase(args.db)

    if args.scan:
        # Mode pindai saja
        logger.info("Memindai folder: %s", args.scan)
        entries = scan_mp4_files(args.scan)
        csv_path = save_metadata_csv(entries, args.csv)
        print(f"\n✅ Ditemukan {len(entries)} file MP4.")
        print(f"   Metadata disimpan ke: {csv_path}")
        print(f"   Edit file tersebut sebelum upload.\n")
        return

    if args.report:
        # Mode laporan saja
        rows = db.get_all_uploads()
        stats = db.get_summary()
        report_path = generate_html_report(
            list(rows),
            f"reports/laporan_{__import__('datetime').datetime.now().strftime('%Y%m%d_%H%M%S')}.html",
            stats,
        )
        print(f"\n📊 Laporan dibuat: {report_path}\n")
        print_summary(stats)
        return

    # Mode upload CLI
    csv_path_obj = Path(args.csv)
    if not csv_path_obj.exists():
        print(f"\n❌ File CSV tidak ditemukan: {args.csv}")
        print("   Jalankan dengan --scan FOLDER untuk membuat metadata.csv\n")
        sys.exit(1)

    from src.auth import get_credentials, build_youtube_service
    from src.uploader import BulkUploadManager

    print("\n🔑 Memuat credentials OAuth...")
    try:
        creds = get_credentials()
        service = build_youtube_service(creds)
        print("✅ Koneksi YouTube berhasil!\n")
    except Exception as e:
        print(f"❌ Gagal konek ke YouTube: {e}")
        sys.exit(1)

    entries = validate_entries(load_metadata_csv(args.csv))
    if not entries:
        print("❌ Tidak ada file valid di CSV.")
        sys.exit(1)

    print(f"📋 {len(entries)} file siap diupload.")
    confirm = input("\nLanjutkan upload? (ya/tidak): ").strip().lower()
    if confirm not in ("ya", "y", "yes"):
        print("Upload dibatalkan.")
        sys.exit(0)

    def on_status(uid, msg):
        print(f"  [{uid}] {msg}")

    def on_progress(uid, sent, total, pct):
        print(f"  [{uid}] {pct:.1f}%", end="\r")

    manager = BulkUploadManager(
        youtube_service=service,
        database=db,
        status_callback=on_status,
        progress_callback=on_progress,
    )

    stats = manager.process_queue(entries)
    print_summary(stats)


def run_gui():
    """Jalankan GUI tkinter."""
    from src.logger_config import setup_logging
    setup_logging(console=False)  # GUI punya log area sendiri

    try:
        import tkinter
    except ImportError:
        print("ERROR: tkinter tidak tersedia. Install Python dengan Tcl/Tk support.")
        sys.exit(1)

    from src.gui import BulkUploaderApp
    app = BulkUploaderApp()
    app.mainloop()


def main():
    args = parse_args()
    if args.cli or args.scan or args.report:
        run_cli(args)
    else:
        run_gui()


if __name__ == "__main__":
    main()
