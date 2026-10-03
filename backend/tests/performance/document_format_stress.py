"""Bounded opt-in ingestion stress profile; run from backend/ when validating M19."""
from __future__ import annotations

import io
import sys
import time
import tracemalloc
import zipfile
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND_ROOT))

from openpyxl import Workbook

from app.config import settings
from app.services.parsing import parse_document

CSV_DATA_ROWS = 25_000
XLSX_DATA_ROWS = 5_000
NEAR_LIMIT_DOCX_PAYLOAD = 23 * 1024 * 1024


def near_limit_docx() -> bytes:
    source = (BACKEND_ROOT / "tests/fixtures/sample.docx").read_bytes()
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(source)) as original, zipfile.ZipFile(output, "w") as target:
        for entry in original.infolist():
            target.writestr(entry, original.read(entry.filename))
        target.writestr("word/media/synthetic-stress.bin", b"x" * NEAR_LIMIT_DOCX_PAYLOAD, compress_type=zipfile.ZIP_STORED)
    return output.getvalue()


def large_csv() -> bytes:
    lines = ["Project,Value"]
    lines.extend(f"Project {row},{row}" for row in range(1, CSV_DATA_ROWS + 1))
    return ("\n".join(lines) + "\n").encode("utf-8")


def large_xlsx() -> bytes:
    workbook = Workbook(write_only=True)
    sheet = workbook.create_sheet("Data")
    sheet.append(["Project", "Value"])
    for row in range(1, XLSX_DATA_ROWS + 1):
        sheet.append([f"Project {row}", row])
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def measure(name: str, filename: str, data: bytes, expected_rows: int | None = None) -> None:
    if len(data) >= settings.max_upload_bytes:
        raise AssertionError(f"{name} fixture must remain below the configured upload cap")
    tracemalloc.start()
    started = time.perf_counter()
    parsed = parse_document(filename, data)
    elapsed_ms = (time.perf_counter() - started) * 1_000
    _current, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    if expected_rows is not None and parsed.metadata.get("row_count") != expected_rows:
        raise AssertionError(f"{name} parsed {parsed.metadata.get('row_count')} rows, expected {expected_rows}")
    if not parsed.blocks or peak_bytes > 1024 * 1024 * 1024:
        raise AssertionError(f"{name} exceeded the bounded parse profile")
    print(
        f"{name}: input={len(data):,} bytes, blocks={len(parsed.blocks):,}, "
        f"rows={parsed.metadata.get('row_count', 'n/a')}, elapsed={elapsed_ms:.1f} ms, "
        f"peak_python_alloc={peak_bytes:,} bytes"
    )


def main() -> None:
    measure("near-upload-cap DOCX", "near-limit.docx", near_limit_docx())
    measure("large CSV", "large.csv", large_csv(), CSV_DATA_ROWS)
    measure("large XLSX", "large.xlsx", large_xlsx(), XLSX_DATA_ROWS)


if __name__ == "__main__":
    main()
