from __future__ import annotations

import os
import shutil
import tempfile
import zipfile
from pathlib import Path

from recognize_files.registry import pick_recognizer
from runtime_config import load_runtime_settings, recognize_max_zip_depth


def _write_text(out_path: Path, text: str) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")


def _out_txt_path(out_root: Path, rel: Path) -> Path:
    if rel.suffix.lower() == ".txt":
        stem = rel.with_suffix("")
    else:
        stem = rel
    return out_root / f"{stem.as_posix()}.txt"


def _safe_member_rel(member: str) -> Path | None:
    p = Path(member)
    if member.endswith("/") or not member or p.is_absolute() or ".." in p.parts:
        return None
    return p


def _recognize_file_path(src: Path, out_root: Path, rel: Path, items: list[dict]) -> None:
    name, fn = pick_recognizer(src)
    out = _out_txt_path(out_root, rel)
    try:
        text = fn(src)
        _write_text(out, text)
        ok = True
    except Exception as e:
        _write_text(out, f"[Ошибка распознавания ({name}): {e}]")
        ok = False
    items.append(
        {
            "source": rel.as_posix(),
            "output": out.relative_to(out_root).as_posix(),
            "recognizer": name,
            "ok": ok,
        }
    )


def _process_zip_file(
    src_zip: Path,
    out_root: Path,
    rel: Path,
    items: list[dict],
    *,
    zip_depth: int,
    max_zip_depth: int,
) -> None:
    if zip_depth >= max_zip_depth:
        out = _out_txt_path(out_root, rel)
        _write_text(
            out,
            f"[ZIP: превышена глубина вложенных архивов ({max_zip_depth})] {src_zip.name}",
        )
        items.append(
            {
                "source": rel.as_posix(),
                "output": out.relative_to(out_root).as_posix(),
                "recognizer": "zip_skipped",
                "ok": False,
            }
        )
        return

    with zipfile.ZipFile(src_zip, "r") as zf:
        for member in zf.namelist():
            member_rel = _safe_member_rel(member)
            if member_rel is None:
                continue
            data = zf.read(member)
            nested_rel = rel / member_rel
            suffix = Path(member).suffix or ".bin"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(data)
                tmp_path = Path(tmp.name)
            try:
                if suffix.lower() == ".zip":
                    _process_zip_file(
                        tmp_path,
                        out_root,
                        nested_rel,
                        items,
                        zip_depth=zip_depth + 1,
                        max_zip_depth=max_zip_depth,
                    )
                else:
                    _recognize_file_path(tmp_path, out_root, nested_rel, items)
            finally:
                try:
                    tmp_path.unlink(missing_ok=True)
                except Exception:
                    pass


def recognize_tender_downloads(
    tender_id: str,
    tender_download_dir: Path,
    downloads_recognize_root: Path,
) -> dict:
    """
    Читает всё из downloads/<tender_id>/, пишет .txt в downloads_recognize/<tender_id>/.
    ZIP обрабатывается без выгрузки исходных файлов в папку результата:
    путь в результате: <архив.zip>/<файл>.txt
    """
    out_root = downloads_recognize_root / tender_id
    items: list[dict] = []

    if not tender_download_dir.is_dir():
        return {
            "tender_id": tender_id,
            "output_dir": str(out_root.resolve()),
            "skipped": True,
            "reason": "нет папки downloads для тендера",
            "items": [],
        }

    files = sorted(
        [p for p in tender_download_dir.iterdir() if p.is_file()],
        key=lambda p: p.name.lower(),
    )
    if not files:
        return {
            "tender_id": tender_id,
            "output_dir": str(out_root.resolve()),
            "skipped": True,
            "reason": "нет скачанных файлов",
            "items": [],
        }

    if out_root.exists():
        shutil.rmtree(out_root, ignore_errors=True)
    out_root.mkdir(parents=True, exist_ok=True)

    max_zip_depth = recognize_max_zip_depth(load_runtime_settings())
    for src in files:
        rel = Path(src.name)
        if src.suffix.lower() == ".zip":
            _process_zip_file(
                src,
                out_root,
                rel,
                items,
                zip_depth=0,
                max_zip_depth=max_zip_depth,
            )
        else:
            _recognize_file_path(src, out_root, rel, items)

    return {
        "tender_id": tender_id,
        "output_dir": str(out_root.resolve()),
        "skipped": False,
        "items": items,
    }
