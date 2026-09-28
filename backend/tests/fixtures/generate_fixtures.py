from __future__ import annotations

import csv
from io import BytesIO, StringIO
from pathlib import Path

from docx import Document

FIXTURE_DIR = Path(__file__).parent


def _make_text_pdf() -> bytes:
    lines = [
        "Project: Document Checker Sample.",
        "Purpose: validate text extraction and source page links.",
        "Author: Alex Example. Reviewer: Morgan Example.",
        "Technology: Python, FastAPI, and PostgreSQL.",
        "Decision: keep the local archive for seven years.",
        "Due date: 2026-11-30.",
    ]
    commands = ["BT", "/F1 12 Tf", "72 720 Td"]
    for index, line in enumerate(lines):
        if index:
            commands.append("0 -18 Td")
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        commands.append(f"({escaped}) Tj")
    commands.append("ET")
    content = "\n".join(commands).encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(content)} >>\nstream\n".encode("ascii") + content + b"\nendstream",
    ]

    document = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for number, value in enumerate(objects, start=1):
        offsets.append(len(document))
        document.extend(f"{number} 0 obj\n".encode("ascii"))
        document.extend(value)
        document.extend(b"\nendobj\n")

    xref_offset = len(document)
    document.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    document.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        document.extend(f"{offset:010} 00000 n \n".encode("ascii"))
    document.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    )
    return bytes(document)


def _make_docx() -> bytes:
    document = Document()
    document.add_heading("Тестовый проект", level=1)
    document.add_paragraph("Цель: проверить обработку документа и ссылки на источники.")
    document.add_paragraph("Автор: Алексей Пример. Проверяющий: Мария Пример.")
    document.add_paragraph("Технологии: Python, FastAPI и PostgreSQL.")
    document.add_paragraph("Срок: 30 ноября 2026 года.")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "Этап"
    table.cell(0, 1).text = "Ответственный"
    cells = table.add_row().cells
    cells[0].text = "Проверка"
    cells[1].text = "Алексей Пример"
    stream = BytesIO()
    document.save(stream)
    return stream.getvalue()


def _make_csv() -> bytes:
    stream = StringIO(newline="")
    writer = csv.writer(stream, delimiter=";", lineterminator="\n")
    writer.writerows([
        ["Проект", "Часы", "Стоимость"],
        ["Альфа", "10", "125.25"],
        ["Бета", "20", "80.25"],
        ["Гамма", "30", "94.50"],
    ])
    return ("\ufeff" + stream.getvalue()).encode("utf-8")


FIXTURE_CONTENTS = {
    "sample.pdf": _make_text_pdf(),
    "sample.docx": _make_docx(),
    "sample.txt": (
        "Проект: Проверка документов\n"
        "Цель: найти автора и сроки в тексте.\n"
        "Автор: Алексей Пример; проверяющий: Мария Пример.\n"
        "Технологии: Python и PostgreSQL.\n"
        "Срок: 30 ноября 2026 года.\n"
    ).encode(),
    "sample.md": (
        "# Тестовый проект\n\n"
        "## Цель\nПроверить извлечение Markdown и диапазоны строк.\n\n"
        "## Участники\nАвтор — Алексей Пример; исполнитель — команда разработки.\n\n"
        "## Срок\n30 ноября 2026 года.\n"
    ).encode(),
    "sample.csv": _make_csv(),
    "sample.xml": (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<project id="DEMO-001">\n'
        "  <title>Тестовый проект</title>\n"
        '  <owner role="executor">Алексей Пример</owner>\n'
        "  <technology>Python и PostgreSQL</technology>\n"
        '  <milestone due="2026-11-30">Проверка</milestone>\n'
        "</project>\n"
    ).encode(),
}


def main() -> None:
    for name, content in FIXTURE_CONTENTS.items():
        (FIXTURE_DIR / name).write_bytes(content)
        print(f"{name}: {len(content):,} bytes")


if __name__ == "__main__":
    main()
