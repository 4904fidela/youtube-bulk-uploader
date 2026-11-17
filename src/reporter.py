"""
reporter.py - Modul untuk membuat laporan upload dalam format CSV dan HTML.
"""

import csv
import logging
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

STATUS_LABELS = {
    "pending": "⏳ Menunggu",
    "in_progress": "⬆️ Sedang Upload",
    "success": "✅ Berhasil",
    "failed": "❌ Gagal",
    "uncertain": "⚠️ Tidak Pasti",
    "skipped": "⏭️ Dilewati",
}

STATUS_COLORS = {
    "pending": "#FFF9C4",
    "in_progress": "#E3F2FD",
    "success": "#E8F5E9",
    "failed": "#FFEBEE",
    "uncertain": "#FFF3E0",
    "skipped": "#F5F5F5",
}


def generate_csv_report(rows: List[Dict], output_path: str) -> str:
    """
    Buat laporan upload dalam format CSV.

    Args:
        rows: List dict dari database uploads.
        output_path: Path file CSV output.

    Returns:
        Path file yang dibuat.
    """
    fieldnames = [
        "id",
        "filename",
        "title",
        "status",
        "youtube_video_id",
        "privacy",
        "retry_count",
        "error_message",
        "created_at",
        "completed_at",
    ]

    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()

        for row in rows:
            row_dict = dict(row)
            row_dict["status"] = STATUS_LABELS.get(
                row_dict.get("status", ""), row_dict.get("status", "")
            )
            writer.writerow(row_dict)

    logger.info("Laporan CSV disimpan: %s", output_path)
    return str(out_path.resolve())


def generate_html_report(rows: List[Dict], output_path: str, stats: Optional[Dict] = None) -> str:
    """
    Buat laporan upload dalam format HTML yang bisa dibuka di browser.

    Args:
        rows: List dict dari database uploads.
        output_path: Path file HTML output.
        stats: Statistik ringkasan upload.

    Returns:
        Path file yang dibuat.
    """
    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    generated_at = datetime.now().strftime("%d %B %Y, %H:%M:%S")

    # Hitung statistik dari rows jika tidak disediakan
    if stats is None:
        stats = {}
        for row in rows:
            status = dict(row).get("status", "unknown")
            stats[status] = stats.get(status, 0) + 1
        stats["total"] = len(rows)

    # Buat baris tabel HTML
    table_rows = []
    for row in rows:
        r = dict(row)
        status = r.get("status", "")
        status_label = STATUS_LABELS.get(status, status)
        bg_color = STATUS_COLORS.get(status, "#FFFFFF")

        yt_id = r.get("youtube_video_id") or ""
        yt_link = (
            f'<a href="https://youtu.be/{yt_id}" target="_blank">{yt_id}</a>'
            if yt_id
            else "-"
        )

        error = r.get("error_message") or ""
        error_cell = (
            f'<span title="{error[:200]}">{error[:50]}{"..." if len(error) > 50 else ""}</span>'
            if error
            else "-"
        )

        table_rows.append(
            f"""
            <tr style="background-color:{bg_color}">
                <td>{r.get('id', '')}</td>
                <td title="{r.get('filepath', '')}">{r.get('filename', '')}</td>
                <td>{r.get('title', '')[:60]}</td>
                <td><strong>{status_label}</strong></td>
                <td>{yt_link}</td>
                <td>{r.get('privacy', '')}</td>
                <td>{r.get('retry_count', 0)}</td>
                <td>{error_cell}</td>
                <td>{r.get('created_at', '')}</td>
                <td>{r.get('completed_at') or '-'}</td>
            </tr>"""
        )

    table_html = "\n".join(table_rows)

    html = f"""<!DOCTYPE html>
<html lang="id">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Laporan YouTube Bulk Uploader</title>
    <style>
        body {{ font-family: 'Segoe UI', Arial, sans-serif; margin: 20px; color: #333; }}
        h1 {{ color: #c00; }}
        .stats {{ display: flex; gap: 15px; flex-wrap: wrap; margin-bottom: 20px; }}
        .stat-card {{
            padding: 15px 20px; border-radius: 8px; min-width: 120px;
            text-align: center; box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        .stat-card .number {{ font-size: 2em; font-weight: bold; }}
        .stat-card .label {{ font-size: 0.85em; color: #666; }}
        .total {{ background: #e8eaf6; }}
        .success {{ background: #e8f5e9; }}
        .failed {{ background: #ffebee; }}
        .uncertain {{ background: #fff3e0; }}
        .pending {{ background: #fff9c4; }}
        .skipped {{ background: #f5f5f5; }}
        table {{ border-collapse: collapse; width: 100%; font-size: 0.9em; }}
        th {{ background: #c00; color: white; padding: 10px; text-align: left; position: sticky; top: 0; }}
        td {{ padding: 8px 10px; border-bottom: 1px solid #ddd; }}
        tr:hover {{ filter: brightness(0.97); }}
        .footer {{ margin-top: 20px; color: #999; font-size: 0.85em; }}
        a {{ color: #c00; }}
    </style>
</head>
<body>
    <h1>📊 Laporan YouTube Bulk Uploader</h1>
    <p>Dibuat pada: <strong>{generated_at}</strong></p>

    <div class="stats">
        <div class="stat-card total">
            <div class="number">{stats.get('total', 0)}</div>
            <div class="label">Total</div>
        </div>
        <div class="stat-card success">
            <div class="number">{stats.get('success', 0)}</div>
            <div class="label">✅ Berhasil</div>
        </div>
        <div class="stat-card failed">
            <div class="number">{stats.get('failed', 0)}</div>
            <div class="label">❌ Gagal</div>
        </div>
        <div class="stat-card uncertain">
            <div class="number">{stats.get('uncertain', 0)}</div>
            <div class="label">⚠️ Tidak Pasti</div>
        </div>
        <div class="stat-card pending">
            <div class="number">{stats.get('pending', 0)}</div>
            <div class="label">⏳ Menunggu</div>
        </div>
        <div class="stat-card skipped">
            <div class="number">{stats.get('skipped', 0)}</div>
            <div class="label">⏭️ Dilewati</div>
        </div>
    </div>

    <table>
        <thead>
            <tr>
                <th>ID</th>
                <th>File</th>
                <th>Judul</th>
                <th>Status</th>
                <th>YouTube ID</th>
                <th>Privasi</th>
                <th>Retry</th>
                <th>Error</th>
                <th>Dibuat</th>
                <th>Selesai</th>
            </tr>
        </thead>
        <tbody>
            {table_html}
        </tbody>
    </table>

    <div class="footer">
        YouTube Bulk Uploader &bull; Laporan otomatis &bull; {generated_at}
    </div>
</body>
</html>"""

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    logger.info("Laporan HTML disimpan: %s", output_path)
    return str(out_path.resolve())


def print_summary(stats: Dict):
    """Cetak ringkasan upload ke konsol."""
    print("\n" + "=" * 50)
    print("  RINGKASAN UPLOAD")
    print("=" * 50)
    print(f"  Total     : {stats.get('total', 0)}")
    print(f"  Berhasil  : {stats.get('success', 0)}")
    print(f"  Gagal     : {stats.get('failed', 0)}")
    print(f"  Tidak Pasti: {stats.get('uncertain', 0)}")
    print(f"  Menunggu  : {stats.get('pending', 0)}")
    print(f"  Dilewati  : {stats.get('skipped', 0)}")
    print("=" * 50)
