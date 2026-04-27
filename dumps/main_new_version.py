from __future__ import annotations

import asyncio
from pathlib import Path

from playwright.async_api import Browser, BrowserContext, Page, async_playwright
from tenderplan_login import login

SCRIPT_DIR = Path(__file__).resolve().parent
AUTH_FILE = SCRIPT_DIR / "auth.json"
MAIN_PAGE_DOM_FILE = SCRIPT_DIR / "main_page_dom.html"

BASE_APP_URL = "https://tenderplan.ru/app"
TARGET_MAIN_URL = "https://tenderplan.ru/app?key=0"
ROUTER_LINK_TIMEOUT_MS = 120_000


async def ensure_session(page: Page, context: BrowserContext, had_auth_file: bool) -> None:
    """
    Проверяет сессию. Если невалидна, выполняет авто-вход по .env и сохраняет auth.json.
    """
    await page.goto(BASE_APP_URL, timeout=120_000)
    await page.wait_for_load_state("domcontentloaded")
    await asyncio.sleep(1.5)

    if "/app" in page.url:
        if not had_auth_file:
            await context.storage_state(path=str(AUTH_FILE))
            print(f"Первичный вход: сохранён {AUTH_FILE.resolve()}")
        return

    print("Сессия отсутствует или недействительна. Выполняю авто-логин по .env...")
    await login(page)
    await wait_router_link(page, "После авто-логина")
    await context.storage_state(path=str(AUTH_FILE))
    print(f"Новая сессия сохранена: {AUTH_FILE.resolve()}")


async def wait_router_link(page: Page, stage_name: str) -> None:
    """
    Ожидает появления любого элемента с class='router-link' (или содержащего этот класс).
    """
    router_link = page.locator(".router-link").first
    await router_link.wait_for(state="visible", timeout=ROUTER_LINK_TIMEOUT_MS)
    print(f"{stage_name}: найден .router-link")


async def open_main_page(page: Page) -> None:
    """
    Переходит на главную страницу key=0 и ждёт загрузку.
    """
    await page.goto(TARGET_MAIN_URL, timeout=120_000)
    await page.wait_for_load_state("domcontentloaded")
    try:
        await page.wait_for_load_state("networkidle", timeout=45_000)
    except Exception:
        # На динамических страницах networkidle может не наступить стабильно.
        pass
    await asyncio.sleep(1.0)
    print(f"Главная страница открыта: {page.url}")


async def dump_dom(page: Page) -> None:
    html = await page.content()
    MAIN_PAGE_DOM_FILE.write_text(html, encoding="utf-8")
    print(f"DOM выгружен в: {MAIN_PAGE_DOM_FILE.resolve()}")


async def create_context(browser: Browser) -> BrowserContext:
    if AUTH_FILE.is_file():
        return await browser.new_context(storage_state=str(AUTH_FILE.resolve()))
    return await browser.new_context()


async def main() -> None:
    had_auth_file = AUTH_FILE.is_file()

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
            slow_mo=250,
        )
        context = await create_context(browser)
        page = await context.new_page()
        page.set_default_timeout(90_000)

        await ensure_session(page, context, had_auth_file)
        await wait_router_link(page, "После входа")
        await open_main_page(page)
        await wait_router_link(page, "На главной")
        await dump_dom(page)

        print("Авторизация, ожидание и выгрузка DOM выполнены.")
        input("Нажмите Enter для закрытия браузера...")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
