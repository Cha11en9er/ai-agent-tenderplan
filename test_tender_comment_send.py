"""
Тест: открыть карточку тендера по прямой ссылке, ввести текст в поле комментария
(Rich Text / Draft.js) и нажать «Отправить».

Авторизация как в mian_parse_exactly_key.py (auth.json + при необходимости login из .env).

Запуск из корня репозитория с активированным venv:
  python test_tender_comment_send.py
  python test_tender_comment_send.py --url "https://tenderplan.ru/app?tender=..."
"""

from __future__ import annotations

import argparse
import asyncio
import os

from dotenv import load_dotenv
from playwright.async_api import async_playwright

from mian_parse_exactly_key import AUTH_FILE, create_context, ensure_session
from tenderplan_comment_ui import focus_editor_and_type, submit_comment

load_dotenv()

DEFAULT_URL = "https://tenderplan.ru/app?tender=69fa5ccf735a1aaca4bcb8c0"
WAIT_BEFORE_CLOSE_SEC = 10


async def run(url: str, comment_text: str) -> None:
    had_auth = AUTH_FILE.is_file()

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
            slow_mo=150,
        )
        context = await create_context(browser)
        page = await context.new_page()
        page.set_default_timeout(90_000)

        await ensure_session(page, context, had_auth)

        print(f"Открываю карточку: {url}")
        await page.goto(url, timeout=120_000)
        await page.wait_for_load_state("domcontentloaded")
        await asyncio.sleep(2.0)

        print("Ищу редактор и ввожу текст...")
        await focus_editor_and_type(page, comment_text)

        print("Нажимаю «Отправить»...")
        await submit_comment(page)

        print(f"Жду {WAIT_BEFORE_CLOSE_SEC} с перед закрытием браузера...")
        await asyncio.sleep(WAIT_BEFORE_CLOSE_SEC)

        await browser.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Тест отправки комментария на карточке тендера.")
    ap.add_argument("--url", default=DEFAULT_URL, help="URL карточки (?tender=...)")
    ap.add_argument(
        "--text",
        default=os.getenv("TEST_COMMENT_TEXT", "Тестовое сообщение (test_tender_comment_send.py)"),
        help="Текст комментария",
    )
    args = ap.parse_args()
    asyncio.run(run(args.url.strip(), args.text))


if __name__ == "__main__":
    main()
