from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from dotenv import load_dotenv
from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from tenderplan_comment_ui import focus_editor_and_type, submit_comment
from tenderplan_login import login

load_dotenv()

SCRIPT_DIR = Path(__file__).resolve().parent
AUTH_FILE = SCRIPT_DIR / "auth.json"
ACCEPT_JSON_FILE = SCRIPT_DIR / "tenders_accept.json"
REJECT_JSON_FILE = SCRIPT_DIR / "tenders_reject.json"
BASE_APP_URL = "https://tenderplan.ru/app"

OPEN_MARK_TIMEOUT_MS = 15_000
SET_MARK_TIMEOUT_MS = 20_000

# Текст пунктов в выпадающем списке меток на tenderplan.ru (как на сайте).
MARK_UI_ACCEPT = "Подходит"
MARK_UI_REJECT = "Не подходит"
SUPPORTED_MARK_UI = {MARK_UI_ACCEPT, MARK_UI_REJECT}

COMMENT_SUBMIT_SETTLE_SEC = 1.5


def comment_text_for_row(row: dict) -> str:
    """
    Текст вердикта для поля комментария на Tenderplan.
    Приоритет: platform_comment → verdict_message → reason_short.
    """
    for key in ("platform_comment", "verdict_message"):
        raw = row.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    rs = row.get("reason_short")
    if isinstance(rs, str) and rs.strip():
        return rs.strip()
    return ""


async def create_context(browser: Browser) -> BrowserContext:
    if AUTH_FILE.is_file():
        return await browser.new_context(storage_state=str(AUTH_FILE.resolve()))
    return await browser.new_context()


async def ensure_session(page: Page, context: BrowserContext) -> None:
    await page.goto(BASE_APP_URL, timeout=120_000)
    await page.wait_for_load_state("domcontentloaded")
    await asyncio.sleep(1.0)

    if "/app" in page.url:
        if not AUTH_FILE.is_file():
            await context.storage_state(path=str(AUTH_FILE))
        return

    print("Сессия отсутствует/истекла. Выполняю логин...")
    await login(page)
    await page.wait_for_load_state("domcontentloaded")
    await asyncio.sleep(1.2)
    await context.storage_state(path=str(AUTH_FILE))
    print(f"Сессия обновлена: {AUTH_FILE.resolve()}")


def load_json_list(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(raw, list):
        raise ValueError(f"{path.name} должен быть JSON-массивом")
    return [x for x in raw if isinstance(x, dict)]


def save_json_list(path: Path, rows: list[dict]) -> None:
    path.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


async def open_mark_dropdown(page: Page) -> None:
    opener = page.locator(
        'div[class*="TenderHeader__tenderInfoItem"][class*="TenderHeader__updatingMarkButton"], '
        'div[class*="TenderHeader__updatingMarkButton__inner"]'
    ).first
    await opener.wait_for(state="visible", timeout=OPEN_MARK_TIMEOUT_MS)
    await opener.scroll_into_view_if_needed(timeout=5_000)
    await opener.click(timeout=OPEN_MARK_TIMEOUT_MS)
    await asyncio.sleep(0.35)


async def set_mark_by_text(page: Page, mark_text: str) -> None:
    row = page.locator('[class*="MarkSelector__item___"]').filter(has_text=mark_text).last
    text = page.locator('[class*="MarkSelector__item__text___"]').filter(has_text=mark_text).last

    if await row.count() > 0 and await row.is_visible():
        await row.click(timeout=SET_MARK_TIMEOUT_MS)
    elif await text.count() > 0 and await text.is_visible():
        await text.click(timeout=SET_MARK_TIMEOUT_MS)
    else:
        raise RuntimeError(f"Пункт метки «{mark_text}» не найден в выпадающем списке")
    await asyncio.sleep(0.6)


async def process_one_tender(
    page: Page,
    tender_row: dict,
    mark_ui_text: str,
    idx: int,
    total: int,
) -> bool:
    tender_id = str(tender_row.get("id", "")).strip()
    tender_url = str(tender_row.get("url", "")).strip()
    mark_ui_text = str(mark_ui_text or "").strip()

    if not tender_url:
        print(f"[{idx}/{total}] skip id={tender_id}: нет url")
        return False
    if mark_ui_text not in SUPPORTED_MARK_UI:
        print(f"[{idx}/{total}] skip id={tender_id}: метка={mark_ui_text!r} не поддержана")
        return False

    print(f"[{idx}/{total}] open id={tender_id} -> «{mark_ui_text}»")
    await page.goto(tender_url, timeout=120_000)
    await page.wait_for_load_state("domcontentloaded")
    await asyncio.sleep(1.0)

    comment_text = comment_text_for_row(tender_row)
    if comment_text:
        print(f"[{idx}/{total}] комментарий на платформу ({len(comment_text)} симв.)...")
        await focus_editor_and_type(page, comment_text)
        await submit_comment(page)
        await asyncio.sleep(COMMENT_SUBMIT_SETTLE_SEC)
    else:
        print(
            f"[{idx}/{total}] предупреждение id={tender_id}: "
            "нет platform_comment / verdict_message / reason_short — комментарий пропущен"
        )

    await open_mark_dropdown(page)
    await set_mark_by_text(page, mark_ui_text)
    print(f"[{idx}/{total}] ok id={tender_id}: метка «{mark_ui_text}» установлена")
    return True


def collect_unmarked_rows(
    accept_rows: list[dict],
    reject_rows: list[dict],
    *,
    only_id: str | None = None,
) -> list[dict]:
    want = str(only_id).strip() if only_id else ""

    def pick(rows: list[dict]) -> list[dict]:
        out: list[dict] = []
        for row in rows:
            if bool(row.get("is_marked", False)):
                continue
            rid = str(row.get("id", "")).strip()
            if want and rid != want:
                continue
            out.append(row)
        return out

    queue: list[dict] = []
    for row in pick(accept_rows):
        queue.append({"bucket": "accept", "mark_ui_text": MARK_UI_ACCEPT, "row": row})
    for row in pick(reject_rows):
        queue.append({"bucket": "reject", "mark_ui_text": MARK_UI_REJECT, "row": row})
    return queue


async def main(*, only_id: str | None = None) -> None:
    accept_rows = load_json_list(ACCEPT_JSON_FILE)
    reject_rows = load_json_list(REJECT_JSON_FILE)
    queue = collect_unmarked_rows(accept_rows, reject_rows, only_id=only_id)
    if not queue:
        hint = f" (фильтр --only-id={only_id!r})" if only_id else ""
        print(f"Нет тендеров для проставления меток (все уже is_marked=true или нет совпадений по id){hint}.")
        return

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
            slow_mo=200,
        )
        context = await create_context(browser)
        page = await context.new_page()
        page.set_default_timeout(90_000)

        await ensure_session(page, context)

        ok = 0
        failed = 0
        for i, item in enumerate(queue, start=1):
            row = item["row"]
            mark_ui_text = item["mark_ui_text"]
            try:
                changed = await process_one_tender(page, row, mark_ui_text, i, len(queue))
                if changed:
                    row["is_marked"] = True
                    ok += 1
                    # Сохраняем сразу, чтобы не потерять прогресс.
                    save_json_list(ACCEPT_JSON_FILE, accept_rows)
                    save_json_list(REJECT_JSON_FILE, reject_rows)
            except Exception as e:
                failed += 1
                rid = row.get("id", "?")
                print(f"[{i}/{len(queue)}] error id={rid}: {e}")
                try:
                    await page.keyboard.press("Escape")
                except Exception:
                    pass
                await asyncio.sleep(0.5)

        await browser.close()
        print(
            f"Готово. Установлено меток: {ok}, ошибок: {failed}, "
            f"в очереди было: {len(queue)}"
        )


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Отправить комментарий вердикта на карточку Tenderplan (из JSON), "
            "затем проставить метку «Подходит» / «Не подходит» из tenders_accept/reject.json."
        )
    )
    p.add_argument(
        "--only-id",
        metavar="ID",
        default=None,
        help="Обработать только эту карточку (по полю id в JSON), например для пайплайна «один тендер».",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    asyncio.run(main(only_id=args.only_id))
