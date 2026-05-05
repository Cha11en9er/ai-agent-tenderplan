"""
Настройки пайплайна из runtime_settings.json — файл можно править агентом без .env.

Приоритет для каждого параметра:
  1) ключ в runtime_settings.json (если задан осмысленное значение)
  2) переменная окружения / .env (после load_dotenv)
  3) значение по умолчанию в коде

Секреты (LOGIN, PASSWORD) не хранятся в JSON — только в .env.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent
RUNTIME_SETTINGS_PATH = ROOT_DIR / "runtime_settings.json"


def load_runtime_settings() -> dict[str, Any]:
    if not RUNTIME_SETTINGS_PATH.is_file():
        return {}
    try:
        data = json.loads(RUNTIME_SETTINGS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def sidebar_key_text(runtime: dict[str, Any] | None = None) -> str:
    """Текст ключа сайдбара Tenderplan (TENDER_KEY_TEXT, затем TARGET_KEY_TEXT)."""
    r = runtime if runtime is not None else load_runtime_settings()
    for key in ("TENDER_KEY_TEXT", "TARGET_KEY_TEXT"):
        if key in r and r[key] is not None:
            s = str(r[key]).strip()
            if s:
                return s
    for env_name in ("TENDER_KEY_TEXT", "TARGET_KEY_TEXT"):
        raw = os.getenv(env_name)
        if raw and str(raw).strip():
            return str(raw).strip()
    return "ремонт маленький"


def tender_done_mark_text(runtime: dict[str, Any] | None = None) -> str:
    r = runtime if runtime is not None else load_runtime_settings()
    if "TENDER_DONE_MARK_TEXT" in r and r["TENDER_DONE_MARK_TEXT"] is not None:
        s = str(r["TENDER_DONE_MARK_TEXT"]).strip()
        if s:
            return s
    return os.getenv("TENDER_DONE_MARK_TEXT", "На анализе").strip() or "На анализе"


def tenders_to_process_default(runtime: dict[str, Any] | None = None) -> int:
    r = runtime if runtime is not None else load_runtime_settings()
    if "TENDERS_TO_PROCESS" in r and r["TENDERS_TO_PROCESS"] is not None:
        try:
            n = int(r["TENDERS_TO_PROCESS"])
            if n >= 1:
                return n
        except (TypeError, ValueError):
            pass
    raw = os.getenv("TENDERS_TO_PROCESS")
    if raw and str(raw).strip():
        try:
            return max(1, int(str(raw).strip()))
        except ValueError:
            pass
    return 3


def list_plate_row_px(runtime: dict[str, Any] | None = None) -> int:
    r = runtime if runtime is not None else load_runtime_settings()
    if "LIST_PLATE_ROW_PX" in r and r["LIST_PLATE_ROW_PX"] is not None:
        try:
            return int(r["LIST_PLATE_ROW_PX"])
        except (TypeError, ValueError):
            pass
    raw = os.getenv("LIST_PLATE_ROW_PX")
    if raw and str(raw).strip():
        try:
            return int(str(raw).strip())
        except ValueError:
            pass
    return 130


def recognize_max_zip_depth(runtime: dict[str, Any] | None = None) -> int:
    r = runtime if runtime is not None else load_runtime_settings()
    if "RECOGNIZE_MAX_ZIP_DEPTH" in r and r["RECOGNIZE_MAX_ZIP_DEPTH"] is not None:
        try:
            return max(1, int(r["RECOGNIZE_MAX_ZIP_DEPTH"]))
        except (TypeError, ValueError):
            pass
    raw = os.getenv("RECOGNIZE_MAX_ZIP_DEPTH")
    if raw and str(raw).strip():
        try:
            return max(1, int(str(raw).strip()))
        except ValueError:
            pass
    return 6
