"""
Один раз: вход по .env и сохранение storage_state в auth.json (куки, localStorage и т.д.).
Дальше main.py может работать без повторного ввода пароля.
"""

import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

from tenderplan_login import login

AUTH_PATH = Path("auth.json")


async def main() -> None:
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
            slow_mo=500,
        )
        context = await browser.new_context(accept_downloads=True)
        page = await context.new_page()
        page.set_default_timeout(90000)

        print("Открываю tenderplan.ru…")
        await login(page)

        await context.storage_state(path=str(AUTH_PATH))
        print("✅ auth.json успешно сохранён (куки + JWT сохранены)")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
