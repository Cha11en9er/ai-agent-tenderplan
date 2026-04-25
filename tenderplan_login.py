"""
Общий сценарий входа на tenderplan.ru (используется main.py и save_auth.py).
"""

import asyncio
import os

from dotenv import load_dotenv
from playwright.async_api import Page

load_dotenv()

LOGIN = os.getenv("LOGIN")
PASSWORD = os.getenv("PASSWORD")


async def login(page: Page) -> None:
    """Открывает сайт, нажимает «Войти», заполняет поля из .env и отправляет форму."""
    if not LOGIN or not PASSWORD:
        raise RuntimeError(
            "В .env должны быть заданы LOGIN и PASSWORD (или используйте готовый auth.json)."
        )

    max_retries = 3
    for attempt in range(max_retries):
        try:
            print(f"Попытка входа {attempt + 1}/{max_retries}...")
            await page.goto("https://tenderplan.ru/", timeout=60000)
            break
        except Exception as e:
            print(f"Попытка {attempt + 1} не удалась: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(5)
            else:
                raise

    try:
        await page.wait_for_load_state("load", timeout=30000)
    except Exception:
        pass
    await asyncio.sleep(3)

    await page.click("text=Войти", timeout=10000)
    print("Нажато «Войти»")

    await page.wait_for_selector("input", timeout=15000)
    print("Форма входа открыта")

    inputs = await page.query_selector_all("input")
    if len(inputs) >= 2:
        await inputs[0].fill(LOGIN)
        await inputs[1].fill(PASSWORD)
        await page.click("button:has-text('Войти')")
        print("Форма отправлена")
    else:
        print(f"Найдено полей ввода: {len(inputs)}, ожидалось минимум 2")

    await asyncio.sleep(5)

    if "/app" in page.url:
        print("Вход выполнен — открыта страница /app")
    else:
        try:
            await page.wait_for_selector('[class*="Sidebar"]', timeout=30000)
            print("Вход выполнен (видна боковая панель)")
        except Exception:
            print(f"Текущий URL после входа: {page.url}")
