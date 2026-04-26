from __future__ import annotations

import asyncio
import json
import os
from urllib.parse import parse_qs, urlparse
from pathlib import Path

from dotenv import load_dotenv
from playwright.async_api import Browser, BrowserContext, Page, TimeoutError, async_playwright
from tenderplan_login import login

load_dotenv()

SCRIPT_DIR = Path(__file__).resolve().parent
AUTH_FILE = SCRIPT_DIR / "auth.json"
MAIN_PAGE_DOM_FILE = SCRIPT_DIR / "main_page_dom.html"
DOWNLOADS_DIR = SCRIPT_DIR / "downloads"
TENDERS_JSON_FILE = SCRIPT_DIR / "tenders_data.json"

BASE_APP_URL = "https://tenderplan.ru/app"
ROUTER_LINK_TIMEOUT_MS = 120_000
TARGET_KEY_TEXT = os.getenv("TARGET_KEY_TEXT", "ремонт маленький").strip() or "ремонт маленький"
WAIT_AFTER_CLICK_SEC = 4
KEY_FIND_TIMEOUT_MS = 2_000
KEY_FIND_TOTAL_TIMEOUT_MS = 20_000
PLATE_WAIT_TIMEOUT_MS = 15_000


async def ensure_session(page: Page, context: BrowserContext, had_auth_file: bool) -> None:
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
    router_link = page.locator(".router-link").first
    await router_link.wait_for(state="visible", timeout=ROUTER_LINK_TIMEOUT_MS)
    print(f"{stage_name}: найден .router-link")


async def click_target_key(page: Page) -> bool:
    key_text_selector = 'div[class*="SidebarKeysList__keyText"]'
    key_title_selector = f'div[class*="SidebarKeysList__item"][title="{TARGET_KEY_TEXT}"]'
    attempts = max(1, KEY_FIND_TOTAL_TIMEOUT_MS // KEY_FIND_TIMEOUT_MS)

    for attempt in range(1, attempts + 1):
        key_item = page.locator(key_title_selector).first
        if await key_item.count() == 0:
            key_item = page.locator(key_text_selector).filter(has_text=TARGET_KEY_TEXT).first

        try:
            await key_item.wait_for(state="visible", timeout=KEY_FIND_TIMEOUT_MS)
            await key_item.scroll_into_view_if_needed()
            await key_item.click()
            print(f"Клик по ключу: «{TARGET_KEY_TEXT}»")
            return True
        except Exception:
            print(
                f"Ключ «{TARGET_KEY_TEXT}» пока не найден "
                f"({attempt}/{attempts}), повтор через 2 сек..."
            )

    print(f"Ключ «{TARGET_KEY_TEXT}» не найден за 20 секунд. Закрываю браузер.")
    return False


async def dump_dom(page: Page) -> None:
    html = await page.content()
    MAIN_PAGE_DOM_FILE.write_text(html, encoding="utf-8")
    print(f"DOM выгружен в: {MAIN_PAGE_DOM_FILE.resolve()}")


async def create_context(browser: Browser) -> BrowserContext:
    if AUTH_FILE.is_file():
        return await browser.new_context(
            storage_state=str(AUTH_FILE.resolve()), accept_downloads=True
        )
    return await browser.new_context(accept_downloads=True)


def parse_tender_id_and_url(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    tender_id = (parse_qs(parsed.query).get("tender") or [""])[0]
    return tender_id, url


async def extract_general_header(page: Page) -> dict[str, str]:
    out: dict[str, str] = {}
    rows = page.locator('[class*="TenderModel__generalHeaderItem"]')
    total = await rows.count()
    print(f"[DEBUG] general header rows: {total}")
    for i in range(total):
        row = rows.nth(i)
        title_el = row.locator('[class*="TenderModel__generalHeaderItemText"]').first
        value_el = row.locator(
            '[class*="TenderModel__generalHeaderItemValue"], '
            '[class*="TenderModel__generalHeaderItemValueString"], '
            '[class*="TenderModel__generalHeaderItemLabel"]'
        ).first
        try:
            if await title_el.count() == 0 or await value_el.count() == 0:
                continue
            title_raw = await title_el.inner_text(timeout=1_500)
            value_raw = await value_el.inner_text(timeout=1_500)
            title = (title_raw or "").strip()
            value = (value_raw or "").strip()
        except TimeoutError:
            print(f"[DEBUG] timeout reading general header row {i}")
            continue
        except Exception:
            continue
        if title and value:
            out[" ".join(title.split())] = " ".join(value.split())
    return out


async def extract_info_rows(page: Page) -> dict[str, str]:
    out: dict[str, str] = {}
    names = page.locator('[class*="TenderModel__itemRowName"]')
    total = await names.count()
    print(f"[DEBUG] info table rows: {total}")
    for i in range(total):
        name_el = names.nth(i)
        row = name_el.locator("xpath=ancestor::div[contains(@class,'TenderModel__itemRow')]").first
        try:
            if await name_el.count() == 0 or await row.count() == 0:
                continue
            name = " ".join(((await name_el.inner_text(timeout=1_500)) or "").split())
            val_candidates = row.locator(
                '[class*="TenderModel__itemRowValue"], [class*="TenderModel__itemRowLabel"], [class*="TenderModel__infoTableCell"]'
            )
            vc = await val_candidates.count()
            if vc == 0:
                continue
            val_el = val_candidates.nth(1 if vc > 1 else 0)
            value = " ".join(((await val_el.inner_text(timeout=1_500)) or "").split())
        except TimeoutError:
            print(f"[DEBUG] timeout reading info row {i}")
            continue
        except Exception:
            continue
        if name and value:
            out[name] = value
    return out


async def extract_tender_name(page: Page) -> str:
    name_el = page.locator('[class*="TenderHeader__tenderName__inner"]').first
    try:
        return " ".join(((await name_el.inner_text()) or "").split())
    except Exception:
        return ""


async def download_archive_if_exists(page: Page, tender_dir: Path) -> list[str]:
    archive_btn = page.locator(
        'a[class*="TenderView__oneLoad"][href*="documents/archive"]'
    ).first
    if await archive_btn.count() == 0:
        return []
    try:
        async with page.expect_download(timeout=60_000) as dl_info:
            await archive_btn.click()
        dl = await dl_info.value
        filename = dl.suggested_filename or "documents.zip"
        dest = tender_dir / filename
        await dl.save_as(str(dest.resolve()))
        print(f"  скачан архив: {dest.name}")
        return [dest.name]
    except Exception as e:
        print(f"  не удалось скачать архив: {e}")
        return []


async def download_single_attachments(page: Page, tender_dir: Path) -> list[str]:
    files: list[str] = []
    links = page.locator('a[class*="Attachments__attachment__name"][href]')
    total = await links.count()
    if total == 0:
        return files

    request = page.context.request
    seen: set[str] = set()
    for i in range(total):
        link = links.nth(i)
        href = (await link.get_attribute("href") or "").strip()
        if not href or href in seen:
            continue
        seen.add(href)
        try:
            resp = await request.get(href, timeout=60_000)
            if not resp.ok:
                continue
            body = await resp.body()
            filename = Path(urlparse(href).path).name or f"file_{i + 1}.bin"
            dest = tender_dir / filename
            if dest.exists():
                dest = tender_dir / f"{dest.stem}_{i + 1}{dest.suffix}"
            dest.write_bytes(body)
            files.append(dest.name)
            print(f"  скачан файл: {dest.name}")
        except Exception:
            continue
    return files


async def collect_one_tender(page: Page, list_key: str, index: int) -> dict | None:
    print("[DEBUG] collect_one_tender: start")
    print("[DEBUG] Ожидание списка тендеров...")
    try:
        await page.locator('[class*="TenderPlate__plate"]').first.wait_for(
            state="visible", timeout=PLATE_WAIT_TIMEOUT_MS
        )
    except Exception as e:
        print(f"[DEBUG] Список тендеров не появился за {PLATE_WAIT_TIMEOUT_MS} мс: {e}")
        return None

    plates = page.locator('[class*="TenderPlate__plate"]')
    plate_count = await plates.count()
    print(f"[DEBUG] Найдено плиток тендеров: {plate_count}")
    if plate_count <= index:
        print(f"[DEBUG] Нет тендера с индексом {index}")
        return None

    print(f"[DEBUG] Клик по тендеру index={index}")
    await plates.nth(index).click()
    print("[DEBUG] Клик выполнен, ждём загрузку карточки...")
    await page.wait_for_load_state("domcontentloaded")
    await asyncio.sleep(1.0)
    print(f"[DEBUG] Текущий URL после клика: {page.url}")

    tender_id, tender_url = parse_tender_id_and_url(page.url)
    if not tender_id:
        print("[DEBUG] Не удалось извлечь tender id из URL")
        return None

    print(f"[DEBUG] tender_id={tender_id}")
    print("[DEBUG] Считывание названия и полей карточки...")
    tender_name = await extract_tender_name(page)
    general_header = await extract_general_header(page)
    info_rows = await extract_info_rows(page)
    print(
        f"[DEBUG] Считано полей: general_header={len(general_header)}, info_rows={len(info_rows)}"
    )

    tender_dir = DOWNLOADS_DIR / tender_id
    tender_dir.mkdir(parents=True, exist_ok=True)
    print(f"[DEBUG] Папка для файлов: {tender_dir.resolve()}")

    print("[DEBUG] Попытка скачать архив...")
    downloaded_files = await download_archive_if_exists(page, tender_dir)
    if not downloaded_files:
        print("[DEBUG] Архива нет/не скачан, пробую поштучные файлы...")
        downloaded_files = await download_single_attachments(page, tender_dir)
    print(f"[DEBUG] Скачано файлов: {len(downloaded_files)}")

    row = {
        "id": tender_id,
        "url": tender_url,
        "tender_name": tender_name,
        "general_header": general_header,
        "info_rows": info_rows,
        "downloaded_files": downloaded_files,
    }

    back_url = f"{BASE_APP_URL}?key={list_key}" if list_key else BASE_APP_URL
    print(f"[DEBUG] Возврат в список: {back_url}")
    await page.goto(back_url, timeout=120_000)
    await page.wait_for_load_state("domcontentloaded")
    await asyncio.sleep(1.0)
    print("[DEBUG] collect_one_tender: done")
    return row


def save_tenders(rows: list[dict]) -> None:
    TENDERS_JSON_FILE.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"JSON сохранён: {TENDERS_JSON_FILE.resolve()}")


async def main() -> None:
    had_auth_file = AUTH_FILE.is_file()

    async with async_playwright() as p:
        DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
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
        is_clicked = await click_target_key(page)
        if not is_clicked:
            await browser.close()
            return
        await asyncio.sleep(WAIT_AFTER_CLICK_SEC)
        await dump_dom(page)

        list_key = (parse_qs(urlparse(page.url).query).get("key") or [""])[0]
        print("Обработка первого тендера из списка...")
        row = await collect_one_tender(page, list_key, 0)
        if row:
            save_tenders([row])
        else:
            print("Не удалось открыть первый тендер или извлечь данные.")

        print("Сценарий завершён: обработан 1 тендер и загружены документы.")
        input("Нажмите Enter для закрытия браузера...")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
