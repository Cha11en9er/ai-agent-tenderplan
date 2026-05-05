from __future__ import annotations

import argparse
import asyncio
import json
import os
from urllib.parse import parse_qs, urlparse
from pathlib import Path

from dotenv import load_dotenv
from playwright.async_api import Browser, BrowserContext, Page, TimeoutError, async_playwright
from recognize_files import recognize_tender_downloads
from runtime_config import (
    list_plate_row_px,
    load_runtime_settings,
    sidebar_key_text,
    tender_done_mark_text,
    tenders_to_process_default,
)
from tenderplan_login import login

load_dotenv()

_RUNTIME_SETTINGS = load_runtime_settings()

SCRIPT_DIR = Path(__file__).resolve().parent
AUTH_FILE = SCRIPT_DIR / "auth.json"
MAIN_PAGE_DOM_FILE = SCRIPT_DIR / "main_page_dom.html"
DOWNLOADS_DIR = SCRIPT_DIR / "downloads"
DOWNLOADS_RECOGNIZE_DIR = SCRIPT_DIR / "downloads_recognize"
TENDERS_JSON_FILE = SCRIPT_DIR / "tenders_data.json"
TENDERS_DOM_DIR = SCRIPT_DIR / "tenders_dom"

BASE_APP_URL = "https://tenderplan.ru/app"
ROUTER_LINK_TIMEOUT_MS = 120_000

# Из runtime_settings.json (агент может править на лету); fallback — .env — см. runtime_config.py
TARGET_KEY_TEXT = sidebar_key_text(_RUNTIME_SETTINGS)
TENDER_DONE_MARK_TEXT = tender_done_mark_text(_RUNTIME_SETTINGS)
WAIT_AFTER_CLICK_SEC = 4
KEY_FIND_TIMEOUT_MS = 2_000
KEY_FIND_TOTAL_TIMEOUT_MS = 20_000
PLATE_WAIT_TIMEOUT_MS = 15_000
TENDERS_TO_PROCESS = tenders_to_process_default(_RUNTIME_SETTINGS)
LIST_PLATE_ROW_PX = list_plate_row_px(_RUNTIME_SETTINGS)


async def _reset_list_scroll_top(page: Page) -> None:
    inner = page.locator(".ReactVirtualized__Grid__innerScrollContainer").first
    if await inner.count() == 0:
        return
    try:
        await inner.evaluate("el => { el.scrollTop = 0; }")
        await asyncio.sleep(0.35)
    except Exception:
        pass


async def _scroll_list_for_plate_index(page: Page, plate_index: int) -> None:
    if plate_index <= 0:
        return
    inner = page.locator(".ReactVirtualized__Grid__innerScrollContainer").first
    delta = plate_index * LIST_PLATE_ROW_PX
    if await inner.count():
        try:
            await inner.evaluate("(el, d) => { el.scrollTop = d; }", delta)
            await asyncio.sleep(0.4)
            return
        except Exception:
            pass
    for _ in range(min(plate_index * 3, 80)):
        await page.mouse.wheel(0, 240)
        await asyncio.sleep(0.1)


async def ensure_tender_plate_visible(page: Page, containers, index: int) -> None:
    """
    Виртуализированный список: без прокрутки nth(index) часто указывает не на ту карточку.
    """
    await _reset_list_scroll_top(page)
    await asyncio.sleep(0.2)
    await _scroll_list_for_plate_index(page, index)
    await asyncio.sleep(0.35)

    plate = containers.nth(index).locator('[class*="TenderPlate__plate"]').first
    for step in range(48):
        try:
            if await plate.count() and await plate.is_visible():
                try:
                    await plate.scroll_into_view_if_needed(timeout=3_000)
                except Exception:
                    pass
                return
        except Exception:
            pass
        inner = page.locator(".ReactVirtualized__Grid__innerScrollContainer").first
        if await inner.count():
            try:
                await inner.evaluate("el => { el.scrollTop += 320; }")
            except Exception:
                pass
        else:
            await page.mouse.wheel(0, 400)
        await asyncio.sleep(0.18)

    print(f"[DEBUG] Предупреждение: плитка index={index} так и не стала видимой")


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


async def dump_tender_dom(page: Page, tender_id: str) -> Path:
    TENDERS_DOM_DIR.mkdir(parents=True, exist_ok=True)
    safe_tender_id = "".join(ch for ch in tender_id if ch.isalnum() or ch in ("-", "_")) or "unknown"
    dom_file = TENDERS_DOM_DIR / f"{safe_tender_id}.html"
    html = await page.content()
    dom_file.write_text(html, encoding="utf-8")
    print(f"  DOM тендера выгружен: {dom_file.resolve()}")
    return dom_file


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
        tender_dir.mkdir(parents=True, exist_ok=True)
        dest = tender_dir / filename
        await dl.save_as(str(dest.resolve()))
        print(f"  скачан архив: {dest.name}")
        return [dest.name]
    except Exception as e:
        print(f"  не удалось скачать архив: {e}")
        return []


async def ensure_documents_attachments_visible(page: Page) -> None:
    for text in ("Документы закупки", "Документация"):
        try:
            h = page.get_by_text(text, exact=False).first
            if await h.count() == 0:
                continue
            await h.scroll_into_view_if_needed(timeout=5_000)
            try:
                await h.click(timeout=2_000)
            except Exception:
                pass
            await asyncio.sleep(0.35)
        except Exception:
            continue

    root = page.locator('[class*="Attachments__container"]').first
    if await root.count() == 0:
        return
    try:
        await root.scroll_into_view_if_needed(timeout=5_000)
    except Exception:
        pass
    for _ in range(28):
        try:
            at_bottom = await root.evaluate(
                """el => {
                const sh = el.scrollHeight, st = el.scrollTop, ch = el.clientHeight || 1;
                return st + ch >= sh - 6;
            }"""
            )
            await root.evaluate("el => { el.scrollTop += 360; }")
            await asyncio.sleep(0.12)
            if at_bottom:
                break
        except Exception:
            break


async def count_attachment_items(page: Page) -> int:
    await ensure_documents_attachments_visible(page)
    return await page.locator('li[class*="Attachments__attachment"]').count()


async def has_attachments_section(page: Page) -> bool:
    if await page.locator('[class*="Attachments__container"]').count() > 0:
        return True
    return await page.locator('ul[class*="Attachments__container"]').count() > 0


async def download_single_attachments(page: Page, tender_dir: Path) -> list[str]:
    files: list[str] = []
    await ensure_documents_attachments_visible(page)
    items = page.locator('li[class*="Attachments__attachment"]')
    total = await items.count()
    if total == 0:
        return files

    seen_download_href: set[str] = set()
    for i in range(total):
        item = items.nth(i)
        name_link = item.locator('a[class*="Attachments__attachment__name"][href]').first
        button_link = item.locator(
            'div[class*="Attachments__buttons--small"] a[href], '
            'a[class*="Attachments__button___"][href]'
        ).first

        try:
            click_target = name_link if await name_link.count() else button_link
            if await click_target.count() == 0:
                continue
            download_href = (await click_target.get_attribute("href") or "").strip()
            if not download_href or download_href in seen_download_href:
                continue
            seen_download_href.add(download_href)

            try:
                await item.scroll_into_view_if_needed(timeout=4_000)
            except Exception:
                pass
            try:
                if await click_target.is_visible():
                    await click_target.scroll_into_view_if_needed(timeout=4_000)
            except Exception:
                pass

            async with page.expect_download(timeout=90_000) as dl_info:
                await click_target.click(timeout=15_000, force=True)
            dl = await dl_info.value
            suggested = (dl.suggested_filename or "").strip()

            # Если сервер не вернул имя, берём название из строки вложения.
            if not suggested:
                ui_name = ""
                if await name_link.count() > 0:
                    ui_name = " ".join(((await name_link.inner_text()) or "").split())
                suggested = ui_name or f"file_{i + 1}.bin"

            filename = suggested
            tender_dir.mkdir(parents=True, exist_ok=True)
            dest = tender_dir / filename
            if dest.exists():
                dest = tender_dir / f"{dest.stem}_{i + 1}{dest.suffix}"
            await dl.save_as(str(dest.resolve()))
            files.append(dest.name)
            print(f"  скачан файл: {dest.name}")
        except Exception as e:
            print(f"  не удалось скачать файл #{i + 1}: {e}")
            continue
    return files


async def set_tender_mark(page: Page) -> bool:
    opener = page.locator('[class*="TenderHeader__updatingMarkButton"]').first
    if await opener.count() == 0:
        opener = page.locator(
            'div[class*="TenderHeader__tenderInfoItem"][class*="TenderHeader__updatingMarkButton"]'
        ).first
    if await opener.count() == 0:
        print("  метка: кнопка открытия списка не найдена")
        return False

    try:
        await opener.scroll_into_view_if_needed(timeout=5_000)
        await opener.click(timeout=8_000)
        await asyncio.sleep(0.5)
    except Exception as e:
        print(f"  метка: не удалось открыть список меток: {e}")
        return False

    mark_row = page.locator('[class*="MarkSelector__item___"]').filter(
        has_text=TENDER_DONE_MARK_TEXT
    ).last
    mark_text = page.locator('[class*="MarkSelector__item__text___"]').filter(
        has_text=TENDER_DONE_MARK_TEXT
    ).last

    try:
        if await mark_row.count() > 0 and await mark_row.is_visible():
            await mark_row.click(timeout=8_000)
        elif await mark_text.count() > 0 and await mark_text.is_visible():
            await mark_text.click(timeout=8_000)
        else:
            print(f"  метка: пункт «{TENDER_DONE_MARK_TEXT}» не найден")
            return False
        await asyncio.sleep(0.8)
        print(f"  метка установлена: «{TENDER_DONE_MARK_TEXT}»")
        return True
    except Exception as e:
        print(f"  метка: не удалось выбрать «{TENDER_DONE_MARK_TEXT}»: {e}")
        return False


def load_existing_tenders() -> list[dict]:
    if not TENDERS_JSON_FILE.is_file():
        return []
    try:
        data = json.loads(TENDERS_JSON_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
    except Exception:
        pass
    return []


def collect_analyzed_ids(rows: list[dict]) -> set[str]:
    analyzed: set[str] = set()
    for row in rows:
        if str(row.get("status", "")).strip().lower() == "анализируем" and row.get("id"):
            analyzed.add(str(row["id"]))
    return analyzed


def merge_rows(existing_rows: list[dict], new_rows: list[dict]) -> list[dict]:
    by_id: dict[str, dict] = {}
    for row in existing_rows + new_rows:
        tender_id = str(row.get("id", "")).strip()
        if not tender_id:
            continue
        by_id[tender_id] = row
    return list(by_id.values())


async def collect_one_tender(
    page: Page,
    list_key: str,
    analyzed_ids: set[str],
    processed_ids: set[str],
) -> dict | None:
    print("[DEBUG] collect_one_tender: start")
    print("[DEBUG] Ожидание списка тендеров...")
    try:
        await page.locator('[class*="TenderPlate__container"]').first.wait_for(
            state="visible", timeout=PLATE_WAIT_TIMEOUT_MS
        )
    except Exception as e:
        print(f"[DEBUG] Список тендеров не появился за {PLATE_WAIT_TIMEOUT_MS} мс: {e}")
        return None

    containers = page.locator('[class*="TenderPlate__container"]')
    container_count = await containers.count()
    print(f"[DEBUG] Найдено карточек тендеров: {container_count}")
    if container_count == 0:
        return None

    row: dict | None = None
    for index in range(container_count):
        containers = page.locator('[class*="TenderPlate__container"]')
        container = containers.nth(index)
        mark_indicator = container.locator('[class*="MarkIndicator__indicator"]')
        if await mark_indicator.count() > 0:
            print(f"[DEBUG] Пропуск карточки index={index}: уже отмечена в UI")
            continue

        print(f"[DEBUG] Прокрутка ленты к карточке index={index}")
        await ensure_tender_plate_visible(page, containers, index)

        container = containers.nth(index)
        plate = container.locator('[class*="TenderPlate__plate"]').first
        if await plate.count() == 0:
            continue

        print(f"[DEBUG] Клик по неотмеченному тендеру index={index}")
        await plate.click()
        print("[DEBUG] Клик выполнен, ждём загрузку карточки...")
        await page.wait_for_load_state("domcontentloaded")
        await asyncio.sleep(1.0)
        print(f"[DEBUG] Текущий URL после клика: {page.url}")

        tender_id, tender_url = parse_tender_id_and_url(page.url)
        if not tender_id:
            print("[DEBUG] Не удалось извлечь tender id из URL")
            continue

        if tender_id in processed_ids:
            print(f"[DEBUG] tender_id={tender_id} уже обработан в этом прогоне, пропуск")
            continue

        if tender_id in analyzed_ids:
            print(f"[DEBUG] tender_id={tender_id} уже имеет status=анализируем в JSON, пропуск")
            continue

        print(f"[DEBUG] tender_id={tender_id}")
        print("[DEBUG] Считывание названия и полей карточки...")
        tender_name = await extract_tender_name(page)
        general_header = await extract_general_header(page)
        info_rows = await extract_info_rows(page)
        print(
            f"[DEBUG] Считано полей: general_header={len(general_header)}, info_rows={len(info_rows)}"
        )

        tender_dir = DOWNLOADS_DIR / tender_id
        print(f"[DEBUG] Папка для файлов: {tender_dir.resolve()}")

        attachment_count = await count_attachment_items(page)
        attachments_present = attachment_count > 0
        if not attachments_present:
            attachments_present = await has_attachments_section(page)
        print(f"[DEBUG] Вложений в списке (li): {attachment_count}")

        print("[DEBUG] Попытка скачать архив...")
        downloaded_files = await download_archive_if_exists(page, tender_dir)
        if not downloaded_files:
            print("[DEBUG] Архива нет/не скачан, пробую поштучные файлы...")
            downloaded_files = await download_single_attachments(page, tender_dir)
        print(f"[DEBUG] Скачано файлов: {len(downloaded_files)}")
        await dump_tender_dom(page, tender_id)
        mark_set = await set_tender_mark(page)

        if downloaded_files:
            files_status = "есть"
        elif not attachments_present:
            files_status = "нет"
        else:
            files_status = "не удалось скачать"

        row = {
            "id": tender_id,
            "url": tender_url,
            "tender_name": tender_name,
            "general_header": general_header,
            "info_rows": info_rows,
            "downloaded_files": downloaded_files,
            "files_status": files_status,
        }
        if mark_set:
            row["status"] = "анализируем"

        break

    if row is None:
        print("[DEBUG] Нет подходящих неотмеченных тендеров для обработки")
        return None

    await asyncio.sleep(0.4)
    print("[DEBUG] collect_one_tender: done")
    return row


def save_tenders(rows: list[dict]) -> None:
    TENDERS_JSON_FILE.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"JSON сохранён: {TENDERS_JSON_FILE.resolve()}")


def run_post_parse_recognition(rows: list[dict]) -> list[dict]:
    DOWNLOADS_RECOGNIZE_DIR.mkdir(parents=True, exist_ok=True)
    updated_rows: list[dict] = []
    print("[DEBUG] Пост-обработка: распознавание скачанных файлов после закрытия браузера...")
    for row in rows:
        tender_id = str(row.get("id", "")).strip()
        if not tender_id:
            updated_rows.append(row)
            continue
        tender_dir = DOWNLOADS_DIR / tender_id
        recognition = recognize_tender_downloads(
            tender_id=tender_id,
            tender_download_dir=tender_dir,
            downloads_recognize_root=DOWNLOADS_RECOGNIZE_DIR,
        )
        row_copy = dict(row)
        row_copy["recognition"] = recognition
        updated_rows.append(row_copy)
        print(
            f"[DEBUG] Распознавание tender_id={tender_id}: "
            f"{len(recognition.get('items') or [])} файлов"
        )
    return updated_rows


async def main(tenders_to_process_override: int | None = None) -> None:
    limit = (
        int(tenders_to_process_override)
        if tenders_to_process_override is not None
        else TENDERS_TO_PROCESS
    )
    if limit < 1:
        raise ValueError("Число тендеров к обработке должно быть >= 1")

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

        existing_rows = load_existing_tenders()
        analyzed_ids = collect_analyzed_ids(existing_rows)
        processed_ids: set[str] = set()
        new_rows: list[dict] = []

        list_key = (parse_qs(urlparse(page.url).query).get("key") or [""])[0]
        print(f"Обработка до {limit} неотмеченных тендеров из списка...")
        for i in range(limit):
            print(f"[DEBUG] Итерация {i + 1}/{limit}")
            row = await collect_one_tender(page, list_key, analyzed_ids, processed_ids)
            if not row:
                print("[DEBUG] Подходящий тендер не найден, завершаю цикл.")
                break
            tender_id = str(row.get("id", "")).strip()
            if tender_id:
                processed_ids.add(tender_id)
                if str(row.get("status", "")).strip().lower() == "анализируем":
                    analyzed_ids.add(tender_id)
            new_rows.append(row)
            save_tenders(merge_rows(existing_rows, new_rows))

        print(
            f"Сценарий завершён: обработано новых тендеров: {len(new_rows)} "
            f"(план: {limit})."
        )
        print("[DEBUG] Закрываю браузер и перехожу к пост-распознаванию...")
        await browser.close()

    # Важно: распознаём только тендеры, собранные в текущем запуске.
    if new_rows:
        recognized_new_rows = await asyncio.to_thread(run_post_parse_recognition, new_rows)
        save_tenders(merge_rows(existing_rows, recognized_new_rows))
    else:
        print("[DEBUG] Новых тендеров в этой сессии нет — пост-распознавание пропущено.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Парсер Tenderplan (ключ + неотмеченные карточки).")
    ap.add_argument(
        "--process",
        type=int,
        default=None,
        metavar="N",
        help="Сколько неотмеченных тендеров обработать за прогон (перекрывает TENDERS_TO_PROCESS из .env).",
    )
    cli = ap.parse_args()
    asyncio.run(main(tenders_to_process_override=cli.process))
