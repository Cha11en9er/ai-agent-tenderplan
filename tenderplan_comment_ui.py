"""
Общие шаги UI Tenderplan: поле комментария (Rich Text / Draft.js) и кнопка «Отправить».
Используются в mark_tender.py и test_tender_comment_send.py.
"""

from __future__ import annotations

import asyncio

from playwright.async_api import Page


async def focus_editor_and_type(page: Page, text: str) -> None:
    wrapper = page.locator(
        '[class*="RichTextEditor__wrapper"], '
        '[aria-label="rdw-wrapper"], '
        '[id^="rdw-wrapper"]'
    ).first
    await wrapper.wait_for(state="visible", timeout=45_000)

    editable = wrapper.locator('[contenteditable="true"]').first
    if await editable.count() == 0:
        editable = page.locator(
            '[class*="DraftEditor-root"] [contenteditable="true"], '
            '.public-DraftEditor-content[contenteditable="true"]'
        ).first

    await editable.wait_for(state="visible", timeout=15_000)
    await editable.click(timeout=10_000)
    await asyncio.sleep(0.25)
    await page.keyboard.press("Control+A")
    await page.keyboard.press("Backspace")
    await page.keyboard.type(text, delay=25)


async def submit_comment(page: Page) -> None:
    btn = page.locator("button.blue-button-primary", has_text="Отправить").first
    await btn.wait_for(state="visible", timeout=15_000)
    await btn.click(timeout=10_000)
