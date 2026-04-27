from __future__ import annotations

from pathlib import Path


def recognize_image_ocr(path: Path) -> str:
    try:
        import pytesseract
        from PIL import Image

        img = Image.open(path)
        try:
            return (pytesseract.image_to_string(img, lang="rus+eng") or "").strip()
        finally:
            img.close()
    except Exception as e:
        return (
            f"[OCR: не удалось распознать изображение. "
            f"Нужны пакеты Pillow и pytesseract, в системе — Tesseract OCR. Ошибка: {e}]"
        )
