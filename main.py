"""
Агент Tenderplan (Playwright): сессия auth.json → список тендеров → за один заход
по каждому тендеру: открыть карточку → скачать вложения (fileviewer → реальный link) → метка в шапке.

Если auth.json нет или сессия недействительна — открывается сайт, в консоли просьба
вручную войти в ЛК; после входа сессия сохраняется в auth.json.

Анализ содержимого файлов не выполняется — только скачивание и учёт в tenders_data.json.
"""
from __future__ import annotations
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import asyncio
import json
import os
import random
import re
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlencode, urlparse
from dotenv import load_dotenv
from playwright.async_api import Browser, BrowserContext, Locator, Page, async_playwright
load_dotenv()
_SCRIPT_DIR = Path(__file__).resolve().parent
AUTH_FILE = _SCRIPT_DIR / "auth.json"
DOWNLOADS_DIR = _SCRIPT_DIR / "downloads"
TENDERS_DATA_FILE = _SCRIPT_DIR / "tenders_data.json"
DEBUG_TENDER_HTML = _SCRIPT_DIR / "debug_tender_view.html"
DEBUG_LIST_FILTERED_HTML = _SCRIPT_DIR / "debug_list_after_search.html"
TENDER_COUNT = 2
APP_URL = "https://tenderplan.ru/app"
# Поиск в ленте (строка в поле Searchbar + ожидание подгрузки списка)
SEARCH_QUERY = os.getenv("TENDER_SEARCH_QUERY", "кровля").strip() or "кровля"
SEARCH_WAIT_SEC = float(os.getenv("TENDER_SEARCH_WAIT_SEC", "10"))
# После go_back() из карточки список может ненадолго показать «старый» DOM — повтор Enter и пауза
SEARCH_WAIT_AFTER_BACK_SEC = float(os.getenv("SEARCH_WAIT_AFTER_BACK_SEC", "3"))
# Дополнительно: перед кликом по карточке снова Enter в поиске (если список всё ещё «плывёт»)
REAPPLY_SEARCH_BEFORE_PLATE = os.getenv("REAPPLY_SEARCH_BEFORE_PLATE", "0").strip().lower() in (
    "1",
    "true",
    "yes",
)
# Оценка высоты строки карточки для scrollTop (ReactVirtualized)
LIST_PLATE_ROW_PX = int(os.getenv("LIST_PLATE_ROW_PX", "130"))
# Максимум ожидания ручного входа (мс)
MANUAL_LOGIN_TIMEOUT_MS = 600_000
# Если 20 секунд ничего не происходит (капча, элемент не найден и т.д.) — ошибка и переход к следующему
INACTIVITY_TIMEOUT_SEC = 60
# Кнопка «скачать все документы архивом» — старт загрузки (большие архивы)
ARCHIVE_DOWNLOAD_TIMEOUT_MS = int(os.getenv("TENDER_ARCHIVE_DOWNLOAD_TIMEOUT_MS", "600000"))
def _safe_filename(name: str) -> str:
    name = (name or "file").strip() or "file"
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
def _filename_from_content_disposition(cd: str | None) -> str | None:
    if not cd:
        return None
    # RFC 5987: filename*=UTF-8''%D0%9F%D0%...
    m = re.search(r"filename\*=(?:UTF-8''|utf-8'')([^;\s]+)", cd, re.I)
    if m:
        return unquote(m.group(1))
    m = re.search(r'filename\*=([^;]+)', cd, re.I)
    if m and "''" in m.group(1):
        return unquote(m.group(1).split("''", 1)[-1].strip())
    m = re.search(r'filename=("?)([^";\n]+)\1', cd, re.I)
    if m:
        raw = m.group(2).strip()
        if raw.startswith("=?") and raw.endswith("?="):
            return None
        return raw
    return None
def _guess_filename_from_href(href: str, fallback: str) -> str:
    path = urlparse(href).path
    base = Path(path).name
    if base and "." in base:
        return base
    if base:
        return base
    return _safe_filename(fallback or "file") + ".bin"
def resolve_fileviewer_download_url(href: str) -> tuple[str, str]:
    """
    Ссылки вида tenderplan.ru/fileviewer?tender=...&link=https%3A%2F%2Fzakupki.gov.ru%2F...
    ведут на HTML-просмотрщик; реальный файл — по параметру link (URL zakupki и т.п.).
    Возвращает (url_для_GET, подсказка_имени).
    """
    if not href or "fileviewer" not in href.lower():
        return href, ""
    try:
        parsed = urlparse(href)
        qs = parse_qs(parsed.query)
        raw_links = qs.get("link") or []
        if not raw_links:
            return href, ""
        real = unquote(raw_links[0]).strip()
        if not real.startswith("http"):
            return href, ""
        rp = urlparse(real)
        rq = parse_qs(rp.query)
        hint = ""
        if "uid" in rq and rq["uid"]:
            hint = rq["uid"][0]
        elif "id" in rq and rq["id"]:
            hint = f"zakupki_{rq['id'][0]}"
        else:
            seg = [s for s in rp.path.rstrip("/").split("/") if s]
            hint = seg[-1] if seg else "file"
        return real, hint
    except Exception:
        return href, ""
def _is_probably_html(body: bytes) -> bool:
    if not body or len(body) > 500_000:
        return False
    start = body[:200].lstrip().lower()
    return start.startswith(b"<") or start.startswith(b"<!doctype")
async def wait_manual_login(page: Page, context: BrowserContext) -> None:
    """Ждём появления ЛК (/app), затем сохраняем storage_state в auth.json."""
    print(
        "\n>>> Нет действующей сессии. В открывшемся браузере войдите в Тендерплан "
        f"(ожидание до {MANUAL_LOGIN_TIMEOUT_MS // 60000} мин.). После входа скрипт продолжит сам.\n"
    )
    if "/app" not in page.url:
        await page.goto("https://tenderplan.ru/", timeout=120000)
    try:
        await page.wait_for_url(re.compile(r".*/app.*"), timeout=MANUAL_LOGIN_TIMEOUT_MS)
    except Exception:
        try:
            await page.wait_for_selector('[class*="Sidebar"]', timeout=MANUAL_LOGIN_TIMEOUT_MS)
        except Exception as e:
            raise RuntimeError(
                "Не дождались входа в личный кабинет. Закройте скрипт и попробуйте снова."
            ) from e
    await asyncio.sleep(2)
    await context.storage_state(path=str(AUTH_FILE))
    print(f">>> Сессия сохранена: {AUTH_FILE.resolve()}")
async def _ensure_session(page: Page, context: BrowserContext, had_auth_file: bool) -> None:
    """При необходимости — ручной вход и запись auth.json."""
    await page.goto(APP_URL, timeout=120000)
    await page.wait_for_load_state("domcontentloaded")
    await asyncio.sleep(2)
    if "/app" in page.url:
        if not had_auth_file:
            await context.storage_state(path=str(AUTH_FILE))
            print(f">>> Первый вход: сохранён {AUTH_FILE.resolve()}")
        return
    print("Сессия недействительна или auth.json отсутствует.")
    await wait_manual_login(page, context)
async def apply_search_query(page: Page, url_state: dict | None = None) -> None:
    """
    Ввод текста в поле поиска (класс Searchbar__searchInput___…) и пауза, пока подгрузится список.
    После паузы в URL появляется ?key=… — сохраняем в url_state для возврата из карточки.
    """
    inp = page.locator('input[class*="Searchbar__searchInput"]').first
    if await inp.count() == 0:
        inp = page.locator("input.Searchbar__searchInput___31vPU").first
    if await inp.count() == 0:
        inp = page.locator(
            'form[class*="Searchbar"] input[type="text"], '
            'input[placeholder*="Поиск"], input[placeholder*="ключ"]'
        ).first
    try:
        if await inp.count() == 0:
            print("Поле поиска не найдено — пропуск фильтра")
            return
        await inp.wait_for(state="visible", timeout=20000)
        await inp.click()
        await inp.fill("")
        await inp.fill(SEARCH_QUERY)
        await inp.press("Enter")
        print(f"В поиск введено: «{SEARCH_QUERY}», ожидание {SEARCH_WAIT_SEC:.0f} с (подгрузка списка)…")
        await asyncio.sleep(SEARCH_WAIT_SEC)
        print("Поиск: пауза завершена, можно обходить список.")
        if url_state is not None:
            sync_list_key_from_url(url_state, page.url)
            lk = url_state.get("list_key")
            if lk is not None:
                print(f"Поиск: зафиксирован key={lk!r} из URL ленты")
    except Exception as e:
        print(f"Поиск: {e}")
async def reapply_search_context(page: Page, *, long_pause: bool = False) -> None:
    """
    После go_back() виртуальный список иногда на мгновение показывает «старую» ленту.
    Повторный Enter в поле поиска подтягивает отфильтрованный DOM.
    """
    inp = page.locator('input[class*="Searchbar__searchInput"]').first
    if await inp.count() == 0:
        inp = page.locator("input.Searchbar__searchInput___31vPU").first
    if await inp.count() == 0:
        inp = page.locator(
            'form[class*="Searchbar"] input[type="text"], '
            'input[placeholder*="Поиск"], input[placeholder*="ключ"]'
        ).first
    if await inp.count() == 0:
        print("  стабилизация списка: поле поиска не найдено")
        return
    try:
        await inp.wait_for(state="visible", timeout=20000)
        cur = (await inp.input_value() or "").strip()
        if cur != SEARCH_QUERY:
            await inp.click()
            await inp.fill("")
            await inp.fill(SEARCH_QUERY)
        await inp.press("Enter")
    except Exception as e:
        print(f"  стабилизация списка: {e}")
        return
    pause = SEARCH_WAIT_SEC if long_pause else SEARCH_WAIT_AFTER_BACK_SEC
    await asyncio.sleep(pause)
def extract_app_list_key(url: str) -> str | None:
    """Параметр key в /app — идентификатор сохранённого поиска; key=0 тоже валиден."""
    try:
        vals = parse_qs(urlparse(url).query).get("key")
        if not vals:
            return None
        return vals[0]
    except Exception:
        return None
def sync_list_key_from_url(url_state: dict, url: str) -> None:
    k = extract_app_list_key(url)
    if k is not None:
        url_state["list_key"] = k
def app_list_url(url_state: dict) -> str | None:
    k = url_state.get("list_key")
    if k is None:
        return None
    return f"{APP_URL}?{urlencode({'key': k})}"
async def navigate_to_app_list(page: Page, url_state: dict) -> None:
    """
    Возврат к ленте. История (go_back) часто отдаёт key=0 и «первый глобальный» тендер.
    Если известен key отфильтрованного поиска — открываем /app?key=… без повторного Enter.
    """
    await asyncio.sleep(0.35)
    dest = app_list_url(url_state)
    if dest:
        print(f"  возврат к списку по URL: {dest}")
        await page.goto(dest, timeout=120_000)
    else:
        print("  возврат: go_back (key в URL ещё не зафиксирован)")
        await page.go_back()
    await page.wait_for_load_state("domcontentloaded")
    await asyncio.sleep(0.45)
    await _reset_list_scroll_top(page)
def load_tenders_state() -> tuple[list[dict], set[str]]:
    """Уже сохранённые тендеры из tenders_data.json и множество id."""
    if not TENDERS_DATA_FILE.is_file():
        return [], set()
    try:
        raw = json.loads(TENDERS_DATA_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return [], set()
    if not isinstance(raw, list):
        return [], set()
    ids = {str(x["id"]) for x in raw if isinstance(x, dict) and x.get("id")}
    return raw, ids
async def get_header_selected_mark_text(page: Page) -> str:
    """
    Текст метки в шапке: span[class*='TenderHeader__selectedMark__inner'] (несколько span — «Выберите» + «метку»).
    """
    mark_button = page.locator('[class*="TenderHeader__updatingMarkButton"]').first
    if await mark_button.count() == 0:
        mark_button = page.locator('[class*="TenderHeader__tenderHeaderItem"][class*="TenderHeader__updatingMarkButton"]').first
    spans = mark_button.locator('[class*="TenderHeader__selectedMark__inner"]')
    parts: list[str] = []
    try:
        n = await spans.count()
        for i in range(n):
            t = (await spans.nth(i).inner_text() or "").strip()
            if t:
                parts.append(t)
    except Exception:
        pass
    return " ".join(parts).strip()
def classify_header_mark(label_text: str) -> str:
    """
    choose_label — в UI «Выберите метку» (ещё не обработан на сайте).
    verify — в шапке уже «Проверить» (раньше открывали / поставили метку).
    """
    t = re.sub(r"\s+", " ", (label_text or "").strip())
    low = t.lower()
    if "проверить" in low and "выберите" not in low:
        return "verify"
    if "выберите" in low and "метк" in low:
        return "choose_label"
    if "выберите" in low:
        return "choose_label"
    return "unknown"
async def _reset_list_scroll_top(page: Page) -> None:
    """Виртуализированный список без сброса scrollTop даёт «чужие» карточки на nth(0)."""
    inner = page.locator(".ReactVirtualized__Grid__innerScrollContainer").first
    if await inner.count() == 0:
        return
    try:
        await inner.evaluate("el => { el.scrollTop = 0; }")
        await asyncio.sleep(0.45)
        print("Прокрутка списка: в начало (scrollTop=0)")
    except Exception:
        pass
async def _ensure_plate_index_in_view(page: Page, plate_skip: int) -> tuple[Locator, int] | None:
    """
    Сброс в начало ленты, прокрутка к plate_skip; при малом числе DOM-узлов RV — докрутка вниз.
    """
    await _reset_list_scroll_top(page)
    await asyncio.sleep(0.25)
    await _scroll_list_for_plate_index(page, plate_skip)
    await asyncio.sleep(0.35)
    for _ in range(42):
        containers = await _plate_container_locators(page)
        cnt = await containers.count()
        if cnt > 0 and plate_skip < cnt:
            return containers, cnt
        inner = page.locator(".ReactVirtualized__Grid__innerScrollContainer").first
        if await inner.count():
            try:
                await inner.evaluate("el => { el.scrollTop += 340; }")
            except Exception:
                pass
        else:
            await page.mouse.wheel(0, 420)
        await asyncio.sleep(0.2)
    return None
async def _scroll_list_for_plate_index(page: Page, plate_skip: int) -> None:
    """После сброса в начало — прокрутка к примерной позиции карточки plate_skip."""
    if plate_skip <= 0:
        return
    inner = page.locator(".ReactVirtualized__Grid__innerScrollContainer").first
    delta = plate_skip * LIST_PLATE_ROW_PX
    if await inner.count():
        try:
            await inner.evaluate("(el, d) => { el.scrollTop = d; }", delta)
            await asyncio.sleep(0.4)
            print(f"Прокрутка списка: к карточке с индексом ~{plate_skip} (≈{delta}px)")
            return
        except Exception:
            pass
    for _ in range(min(plate_skip * 3, 80)):
        await page.mouse.wheel(0, 240)
        await asyncio.sleep(0.1)
async def _plate_container_locators(page: Page):
    """Карточки тендеров внутри виртуализированного списка (или запасной селектор)."""
    inner = page.locator(".ReactVirtualized__Grid__innerScrollContainer").first
    if await inner.count() > 0:
        loc = inner.locator('[class*="TenderPlate__container"]')
        if await loc.count() > 0:
            return loc
    return page.locator('[class*="TenderPlate__container"]')
async def _read_plate_meta(plate_root) -> tuple[str, str, str]:
    """С карточки (корневой локатор): title, price, deadline."""
    title = ""
    price = ""
    deadline = ""
    try:
        ne = plate_root.locator('[class*="TenderPlate__tenderName"]').first
        if await ne.count():
            title = (await ne.inner_text()).strip()
        ie = plate_root.locator('[class*="TenderPlate__tenderInfo"]').first
        if await ie.count():
            de = ie.locator('[class*="TenderPlate__tenderCloseDate"]').first
            if await de.count():
                deadline = (await de.inner_text()).strip()
                t = await de.get_attribute("title")
                if t and t.strip():
                    deadline = t.strip()
            pe = ie.locator('[class*="TenderPlate__tenderPrice"]').first
            if await pe.count():
                price = (await pe.inner_text()).strip()
    except Exception:
        pass
    return title, price, deadline
async def collect_attachment_hrefs(page: Page) -> list[tuple[str, str]]:
    """
    Пары (href, подпись) для вложений: tenderplan fileviewer, zakupki, прямые https (fsk.ru и т.д.).
    Несколько проходов с прокруткой блока Attachments — ссылки ниже по странице не теряются.
    """
    seen_href: set[str] = set()
    seen_real: set[str] = set()
    out: list[tuple[str, str]] = []
    def take(href: str, label: str) -> None:
        if not href or href in seen_href:
            return
        if not (href.startswith("http://") or href.startswith("https://")):
            return
        real_u, _ = resolve_fileviewer_download_url(href)
        if real_u in seen_real:
            return
        seen_href.add(href)
        seen_real.add(real_u)
        out.append((href, label))
    selectors = [
        'a[class*="Attachments__attachment__name"][href]',
        'a[class*="Attachments__attachment__hoverblock"][href]',
        'a[class*="Attachments__attachment"][href]',
    ]
    async def sweep_once() -> None:
        for sel in selectors:
            loc = page.locator(sel)
            n = await loc.count()
            for i in range(n):
                el = loc.nth(i)
                try:
                    await el.scroll_into_view_if_needed()
                    await asyncio.sleep(0.04)
                    href = await el.get_attribute("href")
                    label = ((await el.inner_text()) or "").strip().replace("\n", " ")[:240]
                    if href:
                        take(href, label)
                except Exception:
                    continue
    attach = page.locator('[class*="Attachments"]').first
    max_sweeps = 24
    stable = 0
    prev_count = -1
    for sweep in range(max_sweeps):
        await sweep_once()
        if len(out) == prev_count:
            stable += 1
            if stable >= 2:
                break
        else:
            stable = 0
        prev_count = len(out)
        try:
            if await attach.count():
                at_bottom = await attach.evaluate(
                    """el => {
                    const sh = el.scrollHeight, st = el.scrollTop, ch = el.clientHeight || 1;
                    return st + ch >= sh - 4;
                }"""
                )
                await attach.evaluate("el => { el.scrollTop += 420; }")
                await asyncio.sleep(0.12)
                if at_bottom:
                    await sweep_once()
                    break
            else:
                await page.mouse.wheel(0, 450)
                await asyncio.sleep(0.12)
        except Exception:
            await page.mouse.wheel(0, 450)
            await asyncio.sleep(0.12)
    return out
async def ensure_documents_section_visible(page: Page) -> bool:
    try:
        h = page.get_by_text("Документы закупки", exact=False).first
        await h.wait_for(state="visible", timeout=20000)
        await h.scroll_into_view_if_needed()
        await asyncio.sleep(0.4)
        try:
            await h.click(timeout=2000)
            await asyncio.sleep(0.4)
        except Exception:
            pass
        return True
    except Exception:
        try:
            await page.get_by_text("Документация", exact=False).first.wait_for(
                state="visible", timeout=6000
            )
            return True
        except Exception:
            return False
def _allocate_dest_path(tender_dir: Path, tender_id: str, filename: str) -> Path:
    """Уникальное имя {tender_id}-{filename} при коллизиях."""
    fn = _safe_filename(filename)
    base = tender_dir / f"{tender_id}-{fn}"
    if not base.exists():
        return base
    stem = f"{tender_id}-{Path(fn).stem}"
    suf = Path(fn).suffix or ""
    for n in range(2, 5000):
        cand = tender_dir / f"{stem}_{n}{suf}"
        if not cand.exists():
            return cand
    return base
def _absolute_tenderplan_href(href: str | None) -> str | None:
    if not href:
        return None
    h = href.strip()
    if h.startswith("http://") or h.startswith("https://"):
        return h
    if h.startswith("//"):
        return "https:" + h
    if h.startswith("/"):
        return f"https://tenderplan.ru{h}"
    return None
async def try_download_tender_documents_archive(
    page: Page, tender_id: str
) -> tuple[list[str], bool]:
    """
    Кнопка «одной загрузкой»: a.TenderView__oneLoad* href=/fileviewer/api/documents/archive?tenderId=...
    Второй элемент кортежа — skip_individual: True, если кнопка была и мы уже кликнули по «архивом».
    В этом случае поштучные вложения не качаем (даже если save_as не положил файл в ./downloads —
    иначе дублируется трафик и ловится «чужой» первый download).
    """
    loc = page.locator(
        'a[class*="TenderView__oneLoad"][href*="documents/archive"]'
    ).first
    if await loc.count() == 0:
        loc = page.locator('a[href*="/fileviewer/api/documents/archive"]').first
    if await loc.count() == 0:
        return [], False
    try:
        raw_href = await loc.get_attribute("href")
    except Exception:
        return [], False
    abs_href = _absolute_tenderplan_href(raw_href)
    if not abs_href or tender_id not in abs_href:
        return [], False
    try:
        await loc.scroll_into_view_if_needed()
        await asyncio.sleep(0.35)
        if not await loc.is_visible():
            return [], False
    except Exception:
        return [], False
    tender_dir = (DOWNLOADS_DIR / tender_id).resolve()
    tender_dir.mkdir(parents=True, exist_ok=True)
    clicked = False
    try:
        async with page.expect_download(timeout=ARCHIVE_DOWNLOAD_TIMEOUT_MS) as di:
            await loc.click(timeout=30_000)
            clicked = True
        dl = await di.value
        suggested = _safe_filename(dl.suggested_filename or f"documents-{tender_id}.zip")
        dest = _allocate_dest_path(tender_dir, tender_id, suggested)
        abs_dest = dest.resolve()
        await dl.save_as(str(abs_dest))
        for _ in range(50):
            if abs_dest.is_file() and abs_dest.stat().st_size > 0:
                print(f"  скачан общий архив документов: {dest.name} ({abs_dest.stat().st_size} байт)")
                return [dest.name], True
            await asyncio.sleep(0.15)
        print(
            f"  общий архив: после save_as файл не найден или пуст — {abs_dest} "
            "(поштучно не повторяем)"
        )
        return [], True
    except Exception as e:
        if clicked:
            print(
                f"  общий архив документов: ошибка при сохранении — {e} "
                "(поштучно не повторяем)"
            )
            return [], True
        print(f"  общий архив документов: клик/ожидание download — {e} (пробуем поштучно)")
        return [], False
async def download_attachments_for_tender(
    page: Page,
    tender_id: str,
    href_rows: list[tuple[str, str]],
) -> list[str]:
    """
    Сохраняет в downloads/{tender_id}/ (рядом с main.py) файлы {tender_id}-{имя.расширение}.
    Ссылки fileviewer — GET по параметру link (реальный zakupki.gov.ru), не по HTML-просмотрщику.
    Сначала GET (куки из сессии); при неудаче — клик по исходной ссылке + expect_download.
    """
    tender_dir = (DOWNLOADS_DIR / tender_id).resolve()
    tender_dir.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    request = page.context.request
    seen_fetch: set[str] = set()
    for idx, (href, label) in enumerate(href_rows):
        fetch_url, hint = resolve_fileviewer_download_url(href)
        if fetch_url in seen_fetch:
            continue
        seen_fetch.add(fetch_url)
        base_guess = _guess_filename_from_href(
            fetch_url, (hint or label or f"file_{idx}").strip() or f"file_{idx}"
        )
        dest_name: str | None = None
        try:
            resp = await request.get(
                fetch_url,
                timeout=120000,
                max_redirects=20,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                    "Accept": "*/*",
                },
            )
            if resp.ok:
                body = await resp.body()
                if body:
                    if _is_probably_html(body):
                        print(
                            f"  пропуск (ответ похож на HTML, не файл): "
                            f"{fetch_url[:96]}…"
                        )
                    else:
                        cd = resp.headers.get("content-disposition")
                        fn = _filename_from_content_disposition(cd) or base_guess
                        dest = _allocate_dest_path(tender_dir, tender_id, fn)
                        dest = dest.resolve()
                        dest.write_bytes(body)
                        dest_name = dest.name
        except Exception:
            dest_name = None
        if dest_name:
            print(f"  скачано (GET): {dest_name}")
            saved.append(dest_name)
            continue
        try:
            async with page.expect_download(timeout=120000) as di:
                clicked = await page.evaluate(
                    """(url) => {
                        const nodes = Array.from(document.querySelectorAll('a[href]'));
                        const a = nodes.find(n => n.href === url);
                        if (a) { a.click(); return true; }
                        return false;
                    }""",
                    href,
                )
                if not clicked:
                    raise RuntimeError("ссылка для клика не найдена")
            dl = await di.value
            suggested = _safe_filename(dl.suggested_filename or base_guess)
            dest2 = _allocate_dest_path(tender_dir, tender_id, suggested).resolve()
            await dl.save_as(str(dest2))
            print(f"  скачано (download): {dest2.name}")
            saved.append(dest2.name)
        except Exception as e:
            print(f"  не удалось: {fetch_url[:88]}… — {e}")
    return saved
async def mark_tender_reviewed(page: Page) -> None:
    """
    Шапка → выпадающий список → «Проверить».
    После выбора метки класс/цвет строки меняются — второй заход не должен зависеть только
    от rgb(206,72,72): сначала проверяем «уже выбрано», иначе цвет, иначе любая видимая
    relation-marks-set с текстом «Проверить» (с конца DOM — обычно попап).
    """
    txt = await get_header_selected_mark_text(page)
    if classify_header_mark(txt) == "verify":
        print("  метка «Проверить» уже в шапке — клик по списку не нужен")
        return
    try:
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.25)
    except Exception:
        pass
    opener = page.locator('[class*="TenderHeader__updatingMarkButton"]').first
    if await opener.count() == 0:
        opener = page.locator(
            'div[class*="TenderHeader__tenderInfoItem"][class*="TenderHeader__updatingMarkButton"]'
        ).first
    if await opener.count() == 0:
        opener = page.locator('[class*="TenderHeader__updatingMarkButton"]').first
    try:
        if not await opener.count() or not await opener.is_visible():
            print("  метка: кнопка в шапке не найдена")
            return
        await opener.scroll_into_view_if_needed()
        await opener.click(timeout=8000)
    except Exception as e:
        print(f"  метка: не удалось открыть список — {e}")
        return
    await asyncio.sleep(0.65)
    rows = page.locator('[class*="MarkSelector__item___"]')
    n = await rows.count()
    async def visible_verify_rows(require_red: bool) -> list:
        out: list = []
        for i in range(n):
            row = rows.nth(i)
            try:
                if not await row.is_visible():
                    continue
                txt = (await row.inner_text() or "").strip()
                if "Проверить" not in txt:
                    continue
                if require_red:
                    color_el = row.locator('[class*="MarkSelector__item__color"]').first
                    if not await color_el.count():
                        continue
                    st = (await color_el.get_attribute("style")) or ""
                    if not ("206" in st and "72" in st):
                        continue
                out.append(row)
            except Exception:
                continue
        return out
    for require_red, label in ((True, "rgb(206,72,72)"), (False, "только текст")):
        candidates = await visible_verify_rows(require_red)
        if candidates:
            try:
                await candidates[-1].click(timeout=8000)
                print(f"  метка: «Проверить» ({label}, relation-marks-set)")
                await asyncio.sleep(1.8)
                return
            except Exception as e:
                print(f"  метка: клик — {e}")
    for i in range(n - 1, -1, -1):
        row = rows.nth(i)
        try:
            if not await row.is_visible():
                continue
            if "Проверить" not in (await row.inner_text() or ""):
                continue
            await row.click(timeout=8000)
            print("  метка: «Проверить» (последняя видимая relation-marks-set)")
            await asyncio.sleep(1.8)
            return
        except Exception:
            continue
    print("  метка: пункт «Проверить» в попапе не найден")
async def process_one_tender_from_list(
    page: Page,
    plate_skip: int = 0,
    known_ids: set[str] | None = None,
    html_dump_flag: dict | None = None,
    url_state: dict | None = None,
) -> dict | None:
    """
    Карточка с индексом plate_skip в виртуальном списке: метаданные → клик → id из URL →
    вложения → метка → назад в список.
    Если id уже в known_ids (tenders_data.json + текущий прогон) — только go_back, без работы.
    html_dump_flag['done'] — один раз сохранить debug_tender_view.html после открытия карточки.
    url_state — обязателен для корректного key= в URL при возврате к ленте.
    """
    known_ids = known_ids or set()
    url_state = url_state if url_state is not None else {}
    await page.wait_for_load_state("domcontentloaded")
    await asyncio.sleep(0.8)
    # Закрыть любые открытые попапы
    try:
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.3)
    except Exception:
        pass
    if REAPPLY_SEARCH_BEFORE_PLATE:
        await reapply_search_context(page, long_pause=False)
        sync_list_key_from_url(url_state, page.url)
    ensured = await _ensure_plate_index_in_view(page, plate_skip)
    if not ensured:
        print(f"Нет карточки с индексом {plate_skip} (не удалось дождаться DOM).")
        return None
    containers, cnt = ensured
    plate_root = containers.nth(plate_skip)
    title, price, deadline = await _read_plate_meta(plate_root)
    plate = plate_root.locator('[class*="TenderPlate__plate"]').first
    if await plate.count() == 0:
        print("Нет кликабельной плитки у выбранной карточки.")
        return None
    await plate.click()
    await page.wait_for_load_state("domcontentloaded")
    await asyncio.sleep(1.0)
    url = page.url
    tender_id = ""
    if "tender=" in url:
        tender_id = url.split("tender=")[1].split("&")[0].split("#")[0]
    sync_list_key_from_url(url_state, url)
    if not tender_id:
        print("Не удалось извлечь tender= из URL, пропуск.")
        await navigate_to_app_list(page, url_state)
        return None
    if url_state.get("list_key") is not None:
        tender_url = f"{APP_URL}?{urlencode({'key': url_state['list_key'], 'tender': tender_id})}"
    else:
        tender_url = f"{APP_URL}?{urlencode({'tender': tender_id})}"
    print(f"Тендер id={tender_id}: {title[:70]}…")
    try:
        await page.locator('[class*="TenderHeader__selectedMark__inner"]').first.wait_for(
            state="visible", timeout=20000
        )
    except Exception:
        print("  предупреждение: блок метки в шапке не найден за 20 с")
    label_text = await get_header_selected_mark_text(page)
    mark_state = classify_header_mark(label_text)
    print(f"  текст метки в шапке: {label_text!r} → режим: {mark_state}")
    if html_dump_flag is not None and not html_dump_flag.get("done"):
        try:
            await page.wait_for_load_state("networkidle", timeout=45000)
        except Exception:
            pass
        await asyncio.sleep(0.5)
        DEBUG_TENDER_HTML.write_text(await page.content(), encoding="utf-8")
        html_dump_flag["done"] = True
        print(f"  DOM сохранён (один раз): {DEBUG_TENDER_HTML.resolve()}")
    async def go_back_list() -> None:
        await asyncio.sleep(0.4)
        await navigate_to_app_list(page, url_state)
    base_row = {
        "id": tender_id,
        "title": title,
        "url": tender_url,
        "price": price,
        "deadline": deadline,
    }
    # В шапке уже «Проверить» — на сайте тендер ранее отмечен; синхронизируем только JSON.
    if mark_state == "verify":
        if tender_id in known_ids:
            print("  метка «Проверить» в UI и id уже есть в учёте — пропуск")
            await go_back_list()
            return {"id": tender_id, "__skip_known": True}
        print("  метка «Проверить» в UI, в JSON записи не было — добавляю запись без скачивания")
        await go_back_list()
        return {
            **base_row,
            "downloaded_files": [],
            "header_mark": "Проверить",
            "note": "метка уже была в интерфейсе, файлы не скачивались",
        }
    # «Выберите метку» — полный проход: вложения + метка в UI + запись в json.
    if mark_state == "choose_label":
        if tender_id in known_ids:
            print("  id уже в учёте, но в UI «Выберите метку» — пропуск по known_ids")
            await go_back_list()
            return {"id": tender_id, "__skip_known": True}
        downloaded: list[str] = []
        if await ensure_documents_section_visible(page):
            downloaded, skip_individual = await try_download_tender_documents_archive(
                page, tender_id
            )
            if skip_individual:
                if downloaded:
                    print("  режим: общий архив (поштучные вложения пропущены)")
                else:
                    print(
                        "  режим: была кнопка «архивом», поштучные вложения пропущены "
                        "(проверьте папку загрузок браузера при сбое save_as)"
                    )
            else:
                hrefs = await collect_attachment_hrefs(page)
                print(f"  ссылок на файлы: {len(hrefs)}")
                if hrefs:
                    downloaded = await download_attachments_for_tender(page, tender_id, hrefs)
        else:
            print("  блок документов не найден")
        await mark_tender_reviewed(page)
        await go_back_list()
        return {**base_row, "downloaded_files": downloaded, "header_mark": "после_скрипта_Проверить"}
    # Неизвестный текст метки — старое поведение
    if tender_id in known_ids:
        print("  неизвестная метка, id уже в учёте — пропуск")
        await go_back_list()
        return {"id": tender_id, "__skip_known": True}
    downloaded: list[str] = []
    if await ensure_documents_section_visible(page):
        downloaded, skip_individual = await try_download_tender_documents_archive(
            page, tender_id
        )
        if not skip_individual:
            hrefs = await collect_attachment_hrefs(page)
            print(f"  ссылок на файлы: {len(hrefs)}")
            if hrefs:
                downloaded = await download_attachments_for_tender(page, tender_id, hrefs)
    else:
        print("  блок документов не найден")
    if downloaded:
        await mark_tender_reviewed(page)
    else:
        print("  файлов нет — метку в UI не трогаем (режим unknown)")
    await go_back_list()
    return {**base_row, "downloaded_files": downloaded, "header_mark": label_text or "unknown"}

def _save_tenders_state(existing_rows: list[dict], new_rows: list[dict]) -> None:
    """Saves tenders_data.json incrementally after each tender."""
    merged: list[dict] = []
    seen: set[str] = set()
    for row in existing_rows + new_rows:
        if not isinstance(row, dict) or row.get("__skip_known"):
            continue
        tid = str(row.get("id", ""))
        if not tid or tid in seen:
            continue
        seen.add(tid)
        merged.append(row)
    with open(TENDERS_DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    print(f"  tenders_data.json: {len(merged)} zapisey")

async def run_single_pass_collection(
    page: Page,
    existing_ids: set[str],
    existing_rows: list[dict],
    url_state: dict,
) -> tuple[list[dict], set[str]]:
    """
    До TENDER_COUNT новых записей (id не в existing_ids на старте).
    known_ids растёт в процессе; повтор того же id подряд → следующая карточка.
    После каждого успешного тендера plate_skip увеличивается — лента не начинается с нуля.
    """
    tenders: list[dict] = []
    known_ids: set[str] = set(existing_ids)
    plate_skip = 0
    attempts = 0
    max_attempts = TENDER_COUNT + 5
    html_dump_flag: dict = {"done": False}
    while len(tenders) < TENDER_COUNT and attempts < max_attempts:
        attempts += 1
        print(f"  [{attempts}] Sobrano: {len(tenders)}/{TENDER_COUNT}, plate_skip={plate_skip}")
        try:
            row = await asyncio.wait_for(
                process_one_tender_from_list(
                    page,
                    plate_skip=plate_skip,
                    known_ids=known_ids,
                    html_dump_flag=html_dump_flag,
                    url_state=url_state,
                ),
                timeout=INACTIVITY_TIMEOUT_SEC,
            )
        except asyncio.TimeoutError:
            print(f'  ТАЙМАУТ: тендер[{plate_skip}] не ответил за {INACTIVITY_TIMEOUT_SEC} сек — пропускаем')
            plate_skip += 1
            continue
        if not row:
            print("Список пуст или ошибка — остановка.")
            break
        if row.get("__skip_known"):
            plate_skip += 1
            continue
        if tenders and row["id"] == tenders[-1]["id"]:
            print(
                f"  повтор tender_id={row['id']}: не сдвинулся список, "
                f"карточка[{plate_skip + 1}] вместо [{plate_skip}]."
            )
            plate_skip += 1
            continue
        tenders.append(row)
        known_ids.add(row["id"])
        _save_tenders_state(existing_rows, tenders)
        plate_skip += 1
        if len(tenders) < TENDER_COUNT:
            pause = random.uniform(4, 8)
            print(f"Пауза {pause:.1f} с…")
            await asyncio.sleep(pause)
    if attempts >= max_attempts:
        print("Достигнут лимит попыток (защита от бесконечного цикла).")
    return tenders, known_ids
async def _create_context(browser: Browser, use_storage: bool) -> BrowserContext:
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    if use_storage and AUTH_FILE.is_file():
        return await browser.new_context(
            storage_state=str(AUTH_FILE.resolve()),
            accept_downloads=True,
        )
    return await browser.new_context(accept_downloads=True)
async def main() -> None:
    had_auth = AUTH_FILE.is_file()
    async with async_playwright() as p:
        DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
        browser = await p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
            slow_mo=350,
            downloads_path=str(DOWNLOADS_DIR.resolve()),
        )
        context = await _create_context(browser, use_storage=True)
        page = await context.new_page()
        page.set_default_timeout(90000)
        await _ensure_session(page, context, had_auth)
        await page.goto(APP_URL, timeout=120000)
        await page.wait_for_load_state("domcontentloaded")
        await asyncio.sleep(1.5)
        url_state: dict = {"list_key": None}
        await apply_search_query(page, url_state)
        await _reset_list_scroll_top(page)
        try:
            DEBUG_LIST_FILTERED_HTML.write_text(await page.content(), encoding="utf-8")
            print(f"DOM списка после поиска (один раз за запуск): {DEBUG_LIST_FILTERED_HTML.resolve()}")
        except OSError as e:
            print(f"Не удалось сохранить дамп списка: {e}")
        existing_rows, existing_ids = load_tenders_state()
        print(
            f"\nОдин проход: до {TENDER_COUNT} новых тендеров "
            f"(в json уже {len(existing_ids)} id; они пропускаются)…"
        )
        new_tenders, _ = await run_single_pass_collection(page, existing_ids, existing_rows, url_state)
        merged: list[dict] = []
        seen_merge: set[str] = set()
        for row in existing_rows + new_tenders:
            if not isinstance(row, dict) or row.get("__skip_known"):
                continue
            tid = str(row.get("id", ""))
            if not tid or tid in seen_merge:
                continue
            seen_merge.add(tid)
            merged.append(row)
        with open(TENDERS_DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)
        print(
            f"\nЗаписано: {TENDERS_DATA_FILE.resolve()} "
            f"(всего {len(merged)}, новых за прогон: {len(new_tenders)})"
        )
        await browser.close()
    print("\n" + "=" * 52)
    print("✅ Готово: tenders_data.json обновлён")
    print(f"✅ Файлы: {DOWNLOADS_DIR.resolve()}/<tender_id>/<tender_id>-имя.ext")
    print("✅ Метка в шапке — после успешного скачивания хотя бы одного файла")
    print(f"✅ Один раз при первом открытии карточки: {DEBUG_TENDER_HTML.name}")
    print(f"✅ После поиска: {DEBUG_LIST_FILTERED_HTML.name} (перем. SEARCH_WAIT_AFTER_BACK_SEC)")
    print("=" * 52)
if __name__ == "__main__":
    asyncio.run(main())
