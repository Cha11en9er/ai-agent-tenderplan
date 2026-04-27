from __future__ import annotations

from pathlib import Path

from recognize_files.recognize_doc import (
    recognize_binary_stub,
    recognize_doc,
    recognize_docx,
    recognize_image_ocr,
    recognize_pdf,
    recognize_plain_text,
    recognize_xls,
    recognize_xlsx,
)


def pick_recognizer(path: Path):
    suf = path.suffix.lower()
    if suf == ".pdf":
        return "pdf", recognize_pdf
    if suf in (".doc",):
        return "doc", recognize_doc
    if suf in (".docx",):
        return "docx", recognize_docx
    if suf in (".xls",):
        return "xls", recognize_xls
    if suf in (".xlsx", ".xlsm"):
        return "xlsx", recognize_xlsx
    if suf in (
        ".txt",
        ".csv",
        ".tsv",
        ".json",
        ".xml",
        ".html",
        ".htm",
        ".md",
        ".log",
        ".rtf",
    ):
        return "plain", recognize_plain_text
    if suf in (".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp", ".gif"):
        return "image", recognize_image_ocr
    return "unknown", recognize_binary_stub
