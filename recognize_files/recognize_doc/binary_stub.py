from __future__ import annotations

from pathlib import Path


def recognize_binary_stub(path: Path) -> str:
    suf = path.suffix.lower() or "(без расширения)"
    return (
        f"[Тип «{suf}»: отдельный распознаватель не подключён. "
        f"Файл: {path.name}, размер: {path.stat().st_size} байт.]"
    )
