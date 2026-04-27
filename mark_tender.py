from __future__ import annotations

import asyncio
import json
from pathlib import Path

from dotenv import load_dotenv
from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from tenderplan_login import login

load_dotenv()

SCRIPT_DIR = Path(__file__).resolve().parent
AUTH_FILE = SCRIPT_DIR / "auth.json"
ANALYZED_JSON_FILE = SCRIPT_DIR / "tenders_data_analyzed.json"
BASE_APP_URL = "https://tenderplan.ru/app"

OPEN_MARK_TIMEOUT_MS = 15_000
SET_MARK_TIMEOUT_MS = 20_000

SUPPORTED_STATUSES = {"подходит нам", "не подходит нам"}


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


def load_analyzed_rows() -> list[dict]:
    if not ANALYZED_JSON_FILE.is_file():
        raise FileNotFoundError(f"Не найден файл: {ANALYZED_JSON_FILE}")
    # Поддержка файлов с BOM (часто после записи через PowerShell Out-File -Encoding UTF8).
    raw = json.loads(ANALYZED_JSON_FILE.read_text(encoding="utf-8-sig"))
    if not isinstance(raw, list):
        raise ValueError("tenders_data_analyzed.json должен быть JSON-массивом")
    return [x for x in raw if isinstance(x, dict)]


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


async def process_one_tender(page: Page, tender_row: dict, idx: int, total: int) -> bool:
    tender_id = str(tender_row.get("id", "")).strip()
    tender_url = str(tender_row.get("url", "")).strip()
    target_status = str(tender_row.get("status", "")).strip().lower()

    if not tender_url:
        print(f"[{idx}/{total}] skip id={tender_id}: нет url")
        return False
    if target_status not in SUPPORTED_STATUSES:
        print(f"[{idx}/{total}] skip id={tender_id}: status={target_status!r} не поддержан")
        return False

    print(f"[{idx}/{total}] open id={tender_id} -> {target_status}")
    await page.goto(tender_url, timeout=120_000)
    await page.wait_for_load_state("domcontentloaded")
    await asyncio.sleep(1.0)

    await open_mark_dropdown(page)
    await set_mark_by_text(page, target_status)
    print(f"[{idx}/{total}] ok id={tender_id}: метка «{target_status}» установлена")
    return True


async def main() -> None:
    rows = load_analyzed_rows()
    if not rows:
        print("В tenders_data_analyzed.json нет записей для обработки.")
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
        for i, row in enumerate(rows, start=1):
            try:
                changed = await process_one_tender(page, row, i, len(rows))
                if changed:
                    ok += 1
            except Exception as e:
                failed += 1
                rid = row.get("id", "?")
                print(f"[{i}/{len(rows)}] error id={rid}: {e}")
                try:
                    await page.keyboard.press("Escape")
                except Exception:
                    pass
                await asyncio.sleep(0.5)

        await browser.close()
        print(f"Готово. Установлено меток: {ok}, ошибок: {failed}, всего записей: {len(rows)}")


if __name__ == "__main__":
    asyncio.run(main())
