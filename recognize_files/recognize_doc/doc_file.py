from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path


def _decode_text_bytes(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16", "utf-16-le", "cp1251", "cp866", "latin-1"):
        try:
            return raw.decode(enc).strip()
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace").strip()


def _recognize_doc_via_word_com(path: Path) -> str:
    import win32com.client  # type: ignore
    from recognize_files.recognize_doc.docx_file import recognize_docx

    tmp_docx_fd, tmp_docx = tempfile.mkstemp(suffix=".docx")
    tmp_txt_fd, tmp_txt = tempfile.mkstemp(suffix=".txt")
    os.close(tmp_docx_fd)
    os.close(tmp_txt_fd)
    Path(tmp_docx).unlink(missing_ok=True)
    Path(tmp_txt).unlink(missing_ok=True)
    word = None
    doc = None
    try:
        word = win32com.client.Dispatch("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0
        doc = word.Documents.Open(str(path.resolve()), ReadOnly=True)

        # Сначала пытаемся получить структуру (включая таблицы) через DOCX.
        # wdFormatXMLDocument = 12
        doc.SaveAs(str(tmp_docx), FileFormat=12)
        table_rich_text = recognize_docx(Path(tmp_docx))
        if table_rich_text.strip():
            try:
                doc.Close(False)
            except Exception:
                pass
            doc = None
            try:
                word.Quit()
            except Exception:
                pass
            word = None
            return table_rich_text

        # wdFormatUnicodeText = 7 (UTF-16 text)
        doc.SaveAs(str(tmp_txt), FileFormat=7)
        try:
            doc.Close(False)
        except Exception:
            pass
        doc = None
        try:
            word.Quit()
        except Exception:
            pass
        word = None
        return _decode_text_bytes(Path(tmp_txt).read_bytes())
    finally:
        try:
            if doc is not None:
                doc.Close(False)
        except Exception:
            pass
        try:
            if word is not None:
                word.Quit()
        except Exception:
            pass
        try:
            Path(tmp_docx).unlink(missing_ok=True)
        except Exception:
            pass
        try:
            Path(tmp_txt).unlink(missing_ok=True)
        except Exception:
            pass


def _recognize_doc_via_antiword(path: Path) -> str:
    antiword = shutil.which("antiword")
    if not antiword:
        raise RuntimeError("antiword не найден в PATH")
    result = subprocess.run(
        [antiword, str(path)],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        err = _decode_text_bytes(result.stderr or b"")
        raise RuntimeError(f"antiword завершился с кодом {result.returncode}: {err}")
    return _decode_text_bytes(result.stdout or b"")


def recognize_doc(path: Path) -> str:
    errors: list[str] = []

    try:
        return _recognize_doc_via_word_com(path)
    except Exception as e:
        errors.append(f"Word COM: {e}")

    try:
        return _recognize_doc_via_antiword(path)
    except Exception as e:
        errors.append(f"antiword: {e}")

    details = "; ".join(errors) if errors else "неизвестная ошибка"
    return (
        "[DOC: не удалось распознать файл. "
        "Нужен установленный MS Word (pywin32) или antiword в PATH. "
        f"Детали: {details}]"
    )

