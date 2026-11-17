"""
diagnose_scan.py - Skrip diagnostik untuk mengukur waktu tiap tahap scan.

Jalankan dari folder aplikasi:
    python diagnose_scan.py "D:\\SEVIMA\\Record Zoom"

Output menunjukkan waktu tiap tahap sehingga bottleneck bisa diidentifikasi
tanpa memerlukan GUI.
"""

import sys
import os
import time
import csv
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


def fmt(seconds: float) -> str:
    if seconds < 1:
        return f"{seconds * 1000:.1f}ms"
    return f"{seconds:.3f}s"


def run_diagnostic(folder: str, csv_path: str = "metadata.csv"):
    print(f"\n{'='*60}")
    print(f"  DIAGNOSA SCAN: {folder}")
    print(f"{'='*60}\n")

    # ------------------------------------------------------------------ #
    #  Tahap 0: Verifikasi path
    # ------------------------------------------------------------------ #
    t0 = time.perf_counter()
    if not os.path.exists(folder):
        print(f"❌ Folder tidak ditemukan: {folder}")
        sys.exit(1)
    print(f"[0] Path check: {fmt(time.perf_counter() - t0)}")

    # ------------------------------------------------------------------ #
    #  Tahap 1: Discovery os.scandir (tanpa callback)
    # ------------------------------------------------------------------ #
    t1 = time.perf_counter()
    from src.scanner import iter_mp4_files, ScanTimings, PROGRESS_THROTTLE_SECS, PROGRESS_EVERY

    cb_calls = [0]
    entries = []
    for entry in iter_mp4_files(folder, progress_callback=lambda fp, n: cb_calls.__setitem__(0, n)):
        entries.append(entry)

    t1_elapsed = time.perf_counter() - t1
    print(f"[1] Discovery        : {fmt(t1_elapsed)}  ({len(entries)} file, {cb_calls[0]} callback calls)")
    print(f"    Rate             : {len(entries)/t1_elapsed:.0f} file/s")
    print(f"    Throttle setting : setiap {PROGRESS_THROTTLE_SECS}s atau {PROGRESS_EVERY} file")

    # ------------------------------------------------------------------ #
    #  Tahap 2: Merge dengan CSV
    # ------------------------------------------------------------------ #
    t2 = time.perf_counter()
    from src.scanner import merge_scan_with_existing
    merged = merge_scan_with_existing(entries, csv_path)
    t2_elapsed = time.perf_counter() - t2
    print(f"[2] Merge CSV        : {fmt(t2_elapsed)}  ({len(merged)} entri)")

    # ------------------------------------------------------------------ #
    #  Tahap 3: Tulis CSV
    # ------------------------------------------------------------------ #
    t3 = time.perf_counter()
    from src.scanner import save_metadata_csv
    csv_out = "_diagnose_output.csv"
    save_metadata_csv(merged, csv_out)
    t3_elapsed = time.perf_counter() - t3
    print(f"[3] Tulis CSV        : {fmt(t3_elapsed)}  ({csv_out})")
    try:
        os.remove(csv_out)
    except OSError:
        pass

    # ------------------------------------------------------------------ #
    #  Tahap 4: SQLite batch query
    # ------------------------------------------------------------------ #
    db_path = "upload_history.db"
    if os.path.exists(db_path):
        t4 = time.perf_counter()
        from src.database import UploadDatabase
        db = UploadDatabase(db_path)
        all_rows = db.get_all_uploads()
        db_lookup = {dict(r)["filepath"]: dict(r) for r in all_rows}
        matched = sum(1 for e in merged if e["filepath"] in db_lookup)
        db.close()
        t4_elapsed = time.perf_counter() - t4
        print(f"[4] DB batch query   : {fmt(t4_elapsed)}  ({len(db_lookup)} rows, {matched} matched)")
    else:
        print(f"[4] DB batch query   : SKIP (tidak ada upload_history.db)")

    # ------------------------------------------------------------------ #
    #  Tahap 5: Simulasi Treeview insert (tanpa GUI)
    # ------------------------------------------------------------------ #
    t5 = time.perf_counter()
    # Simulasikan apa yang dilakukan _populate_table: iterate + format
    rows_to_insert = []
    for i, entry in enumerate(merged, 1):
        rows_to_insert.append((
            i,
            entry.get("filename", ""),
            entry.get("title", "")[:60],
            f"{entry.get('file_size_mb', 0):.1f} MB",
            "Menunggu",
            entry.get("privacy", "private"),
        ))
    t5_elapsed = time.perf_counter() - t5
    print(f"[5] Treeview prep    : {fmt(t5_elapsed)}  ({len(rows_to_insert)} baris)")

    # ------------------------------------------------------------------ #
    #  Tahap 6: Simulasi progress_callback ke Tkinter after() (tanpa GUI)
    # ------------------------------------------------------------------ #
    # Hitung berapa callback yang akan dikirim dengan throttle ON vs OFF
    t6 = time.perf_counter()
    throttled_calls = 0
    last_cb = 0.0
    for i in range(len(entries)):
        now = time.perf_counter()
        if now - last_cb >= PROGRESS_THROTTLE_SECS or (i + 1) % PROGRESS_EVERY == 0:
            throttled_calls += 1
            last_cb = now
    t6_elapsed = time.perf_counter() - t6
    print(f"[6] self.after() calls: {throttled_calls} dengan throttle "
          f"(vs {len(entries)} tanpa throttle)")

    # ------------------------------------------------------------------ #
    #  Ringkasan
    # ------------------------------------------------------------------ #
    total = time.perf_counter() - t0
    print(f"\n{'='*60}")
    print(f"  TOTAL WAKTU: {fmt(total)}")
    print(f"  Bottleneck: ", end="")
    timings = [
        ("Discovery", t1_elapsed),
        ("Merge CSV", t2_elapsed),
        ("Tulis CSV", t3_elapsed),
    ]
    bottleneck_name, bottleneck_time = max(timings, key=lambda x: x[1])
    pct = bottleneck_time / total * 100
    print(f"{bottleneck_name} ({fmt(bottleneck_time)}, {pct:.0f}% dari total)")
    print(f"{'='*60}\n")

    if total > 10:
        print("⚠️  Total > 10 detik — masih lambat di lingkungan ini.")
        print("   Periksa apakah folder berada di WSL path (/mnt/d/) vs native Windows.")
    else:
        print("✅ Semua tahap dalam batas normal.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Penggunaan: python {sys.argv[0]} <folder_sumber> [metadata.csv]")
        print(f"Contoh   : python {sys.argv[0]} \"D:\\SEVIMA\\Record Zoom\"")
        sys.exit(1)
    folder_arg = sys.argv[1]
    csv_arg = sys.argv[2] if len(sys.argv) > 2 else "metadata.csv"
    run_diagnostic(folder_arg, csv_arg)
