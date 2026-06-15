from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path
from typing import Any

from docx import Document
from pypdf import PdfReader


TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".py", ".js", ".ts", ".tsx", ".java", ".c", ".cpp"}

# Zip-bomb guards: cap per-entry and total uncompressed size.
ZIP_MAX_ENTRY_BYTES = 5 * 1024 * 1024
ZIP_MAX_TOTAL_BYTES = 50 * 1024 * 1024


def parse_students_csv(content: bytes) -> list[dict[str, Any]]:
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    students: list[dict[str, Any]] = []
    for index, row in enumerate(reader, start=1):
        roll = row.get("roll_number") or row.get("roll") or row.get("id") or row.get("student_id")
        name = row.get("name") or row.get("student_name") or f"Student {index}"
        if not roll:
            roll = f"ROLL-{index:03d}"
        students.append(
            {
                "roll_number": str(roll).strip(),
                "name": str(name).strip(),
                "email": str(row.get("email") or "").strip(),
                "metadata": {k: v for k, v in row.items() if k not in {"roll_number", "roll", "id", "student_id", "name", "student_name", "email"}},
            }
        )
    return students


def parse_students_csv_report(content: bytes) -> dict[str, Any]:
    """Like parse_students_csv but also reports how many data rows were missing a
    usable roll_number (those rows still get a synthesised ROLL-### above; this
    surfaces a count so callers can warn the operator). Additive helper — does
    not change parse_students_csv's contract.
    """
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    skipped_rows = 0
    for row in reader:
        roll = row.get("roll_number") or row.get("roll") or row.get("id") or row.get("student_id")
        if not (roll and str(roll).strip()):
            skipped_rows += 1
    return {"students": parse_students_csv(content), "skipped_rows": skipped_rows}


def extract_text_from_upload(filename: str, content: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    try:
        if suffix == ".pdf":
            return extract_pdf_text(content)
        if suffix == ".docx":
            return extract_docx_text(content)
        if suffix == ".zip":
            return extract_zip_text(content)
        if suffix in TEXT_EXTENSIONS:
            return content.decode("utf-8", errors="replace")[:50000]
    except Exception as exc:
        return f"[Extraction failed for {filename}: {exc}]"
    return f"[Stored binary file: {filename}, {len(content)} bytes]"


def extract_pdf_text(content: bytes) -> str:
    reader = PdfReader(io.BytesIO(content))
    pages = [(page.extract_text() or "") for page in reader.pages[:20]]
    return "\n\n".join(pages)[:50000]


def extract_docx_text(content: bytes) -> str:
    document = Document(io.BytesIO(content))
    return "\n".join(paragraph.text for paragraph in document.paragraphs)[:50000]


def extract_zip_text(content: bytes) -> str:
    snippets: list[str] = []
    total_uncompressed = 0
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        for info in archive.infolist()[:50]:
            name = info.filename
            if name.endswith("/") or info.is_dir():
                continue
            suffix = Path(name).suffix.lower()
            if suffix not in TEXT_EXTENSIONS:
                snippets.append(f"\n--- {name} ---\n[Binary or unsupported file]")
                continue
            # Zip-bomb guard: reject entries whose declared uncompressed size is
            # implausibly large, and abort once the running total is exceeded.
            if info.file_size > ZIP_MAX_ENTRY_BYTES:
                snippets.append(f"\n--- {name} ---\n[Skipped: entry too large]")
                continue
            if total_uncompressed + info.file_size > ZIP_MAX_TOTAL_BYTES:
                snippets.append("\n[Aborted: archive exceeds total size limit]")
                break
            total_uncompressed += info.file_size
            with archive.open(info) as file:
                snippets.append(f"\n--- {name} ---\n{file.read(20000).decode('utf-8', errors='replace')}")
    return "\n".join(snippets)[:80000]
