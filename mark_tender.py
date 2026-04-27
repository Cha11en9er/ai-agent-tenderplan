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
ACCEPT_JSON_FILE = SCRIPT_DIR / "tenders_accept.json"
REJECT_JSON_FILE = SCRIPT_DIR / "tenders_reject.json"
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
    target_status: str,
    idx: int,
    total: int,
) -> bool:
    tender_id = str(tender_row.get("id", "")).strip()
    tender_url = str(tender_row.get("url", "")).strip()
    target_status = str(target_status or "").strip().lower()

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


def collect_unmarked_rows(accept_rows: list[dict], reject_rows: list[dict]) -> list[dict]:
    queue: list[dict] = []
    for row in accept_rows:
        if bool(row.get("is_marked", False)):
            continue
        queue.append({"bucket": "accept", "target_status": "подходит нам", "row": row})
    for row in reject_rows:
        if bool(row.get("is_marked", False)):
            continue
        queue.append({"bucket": "reject", "target_status": "не подходит нам", "row": row})
    return queue


async def main() -> None:
    accept_rows = load_json_list(ACCEPT_JSON_FILE)
    reject_rows = load_json_list(REJECT_JSON_FILE)
    queue = collect_unmarked_rows(accept_rows, reject_rows)
    if not queue:
        print("Нет тендеров для проставления меток (все уже is_marked=true).")
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
            target_status = item["target_status"]
            try:
                changed = await process_one_tender(page, row, target_status, i, len(queue))
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


if __name__ == "__main__":
    asyncio.run(main())
