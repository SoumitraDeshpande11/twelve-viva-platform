from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path
from typing import Any

from docx import Document
from pypdf import PdfReader


TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".py", ".js", ".ts", ".tsx", ".java", ".c", ".cpp"}


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
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        for name in archive.namelist()[:50]:
            if name.endswith("/"):
                continue
            suffix = Path(name).suffix.lower()
            if suffix not in TEXT_EXTENSIONS:
                snippets.append(f"\n--- {name} ---\n[Binary or unsupported file]")
                continue
            with archive.open(name) as file:
                snippets.append(f"\n--- {name} ---\n{file.read(20000).decode('utf-8', errors='replace')}")
    return "\n".join(snippets)[:80000]
