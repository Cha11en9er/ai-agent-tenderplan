from __future__ import annotations

import argparse
import json
from pathlib import Path

from recognize_files import recognize_tender_downloads

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_DOWNLOADS = SCRIPT_DIR / "downloads"
DEFAULT_OUT_ROOT = SCRIPT_DIR / "downloads_recognize_test"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Тестовый прогон распознавания по уже скачанным файлам",
    )
    parser.add_argument(
        "--downloads-dir",
        type=Path,
        default=DEFAULT_DOWNLOADS,
        help=f"Папка с исходными файлами (по умолчанию: {DEFAULT_DOWNLOADS})",
    )
    parser.add_argument(
        "--out-root",
        type=Path,
        default=DEFAULT_OUT_ROOT,
        help=f"Корень для тестовых результатов (по умолчанию: {DEFAULT_OUT_ROOT})",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    downloads_dir = args.downloads_dir.resolve()
    out_root = args.out_root.resolve()
    out_root.mkdir(parents=True, exist_ok=True)

    if not downloads_dir.exists():
        print(f"[ERROR] Папка downloads не найдена: {downloads_dir}")
        return

    summary: list[dict] = []
    tender_dirs = [p for p in downloads_dir.iterdir() if p.is_dir()]
    tender_dirs.sort(key=lambda p: p.name)

    print(f"[INFO] Источник: {downloads_dir}")
    print(f"[INFO] Вывод теста: {out_root}")
    print(f"[INFO] Найдено папок тендеров: {len(tender_dirs)}")

    for td in tender_dirs:
        print(f"[RUN] Распознаю tender_id={td.name}")
        result = recognize_tender_downloads(
            tender_id=td.name,
            tender_download_dir=td,
            downloads_recognize_root=out_root,
        )
        summary.append(result)
        print(
            f"      -> items={len(result.get('items') or [])}, "
            f"skipped={result.get('skipped', False)}"
        )

    summary_path = out_root / "_summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[DONE] Сводка: {summary_path}")


if __name__ == "__main__":
    main()

