from __future__ import annotations

from pathlib import Path


def recognize_pdf(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    chunks: list[str] = []
    for i, page in enumerate(reader.pages):
        t = page.extract_text() or ""
        chunks.append(f"--- страница {i + 1} ---\n{t}")
    return "\n\n".join(chunks).strip()
