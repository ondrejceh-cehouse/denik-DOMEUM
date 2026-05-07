"""
domeum_client.py – Automatizace domeum.app pomocí Playwright
Přihlásí se, najde projekt, vytvoří záznamy ve stavebním deníku.
"""

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from playwright.async_api import (
    async_playwright,
    Page,
    Browser,
    BrowserContext,
    TimeoutError as PlaywrightTimeoutError,
)

logger = logging.getLogger(__name__)

DOMEUM_URL = "https://domeum.app"
DEFAULT_TIMEOUT = 15_000   # 15 sekund
UPLOAD_TIMEOUT  = 60_000   # 60 sekund pro nahrávání fotek
DEBUG_SCREENSHOT_DIR = "/tmp"


class DomeumClient:
    """Asynchronní klient pro ovládání domeum.app přes Playwright."""

    def __init__(self):
        self.email        = os.environ["DOMEUM_EMAIL"]
        self.password     = os.environ["DOMEUM_PASSWORD"]
        self.project_name = os.environ.get("DOMEUM_PROJECT_NAME", "RD Cehovi")
        self.dry_run      = os.environ.get("DRY_RUN", "false").lower() == "true"

        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None

    # ─────────────────────────────── Context manager ──────────────────────────

    async def __aenter__(self):
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        self._context = await self._browser.new_context(
            viewport={"width": 1280, "height": 900},
            locale="cs-CZ",
            timezone_id="Europe/Prague",
        )
        self.page = await self._context.new_page()
        self.page.set_default_timeout(DEFAULT_TIMEOUT)
        return self

    async def __aexit__(self, *_):
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    # ─────────────────────────────── Helpers ──────────────────────────────────

    async def _screenshot(self, name: str) -> None:
        """Uloží debug screenshot."""
        path = f"{DEBUG_SCREENSHOT_DIR}/domeum_debug_{name}.png"
        try:
            await self.page.screenshot(path=path, full_page=False)
            logger.debug(f"Screenshot uložen: {path}")
        except Exception:
            pass

    async def _wait_idle(self) -> None:
        """Počká na ukončení síťové aktivity."""
        try:
            await self.page.wait_for_load_state("networkidle", timeout=20_000)
        except PlaywrightTimeoutError:
            pass  # OK – stránka je SPA, může se pohybovat

    # ─────────────────────────────── Login ────────────────────────────────────

    async def login(self) -> bool:
        """Přihlásí se na domeum.app."""
        logger.info(f"Přihlašuji se jako {self.email}")

        await self.page.goto(DOMEUM_URL, wait_until="domcontentloaded")
        await self.page.wait_for_timeout(2_000)
        await self._screenshot("login_homepage")

        try:
            # Krok 1: kliknout na "Sign In" na homepage
            signin_btn = self.page.locator('a:has-text("Sign In"), button:has-text("Sign In"), a:has-text("Přihlásit"), button:has-text("Přihlásit")').first
            if await signin_btn.count() > 0:
                await signin_btn.click()
                await self.page.wait_for_timeout(2_000)
                logger.info("Kliknuto na 'Sign In'")
            await self._screenshot("login_after_signin_click")

            # Krok 2: počkat na login formulář a vyplnit email + heslo
            await self.page.wait_for_selector('input[type="email"], input[name="email"]', timeout=10_000)

            email_input = self.page.locator('input[type="email"], input[name="email"]').first
            await email_input.fill(self.email)
            logger.info("Email vyplněn")

            pwd_input = self.page.locator('input[type="password"], input[name="password"]').first
            if await pwd_input.count() > 0:
                await pwd_input.fill(self.password)
                logger.info("Heslo vyplněno")

            await self._screenshot("login_after_fill")

            # Krok 3: odeslat formulář tlačítkem "Sign in with Email"
            for sel in [
                'button:has-text("Sign in with Email")',
                'button:has-text("Přihlásit se pomocí e-mailu")',
                'button[type="submit"]',
            ]:
                btn = self.page.locator(sel).first
                if await btn.count() > 0:
                    await btn.click()
                    logger.info(f"Submit: {sel}")
                    break

            await self._wait_idle()
            await self._screenshot("login_done")
            logger.info("Přihlášení úspěšné")
            return True

        except Exception as e:
            logger.error(f"Přihlášení selhalo: {e}")
            await self._screenshot("login_error")
            return False

    # ─────────────────────────────── Projekt ──────────────────────────────────

    async def get_all_projects(self) -> list[dict]:
        """
        Vrátí seznam všech projektů dostupných přihlášenému uživateli.
        Každý projekt: {"name": "RD Cehovi", "element": locator}
        """
        logger.info("Načítám seznam všech projektů...")
        try:
            # Čekat na stránku projektů (anglická i česká verze)
            projects_heading = self.page.locator("text=Your Projects").or_(
                self.page.locator("text=Vaše projekty")
            )
            await projects_heading.first.wait_for(timeout=15_000)
            await self._wait_idle()
            await self._screenshot("projects_page")

            projects = []

            # Stránka zobrazuje karty projektů – každá karta má nadpis s názvem projektu
            # Hledáme heading elementy uvnitř klikatelných karet
            headings = await self.page.locator("h2, h3, h4").all_text_contents()
            card_headings = [h.strip() for h in headings if h.strip() and len(h.strip()) > 2]
            logger.info(f"Nalezené nadpisy na stránce: {card_headings}")

            # Použijeme nadpisy jako názvy projektů (přeskočíme systémové texty)
            skip = {"your projects", "vaše projekty", "create project", "vytvořit projekt", "cookie consent"}
            for name in card_headings:
                if name.lower() not in skip:
                    projects.append({"name": name})

            logger.info(f"Nalezeno {len(projects)} projektů: {[p['name'] for p in projects]}")
            return projects

        except Exception as e:
            logger.error(f"Chyba při načítání projektů: {e}")
            await self._screenshot("projects_error")
            return []

    async def select_project(self) -> bool:
        """Vybere projekt dle DOMEUM_PROJECT_NAME (fallback pro single-project mode)."""
        logger.info(f"Hledám projekt: {self.project_name}")
        try:
            await self.page.locator("text=Your Projects").or_(
                self.page.locator("text=Vaše projekty")
            ).first.wait_for(timeout=10_000)
            project_card = self.page.locator(f"text={self.project_name}").first
            await project_card.click()
            await self._wait_idle()
            logger.info(f"Projekt '{self.project_name}' vybrán")
            return True
        except Exception as e:
            logger.error(f"Projekt nenalezen: {e}")
            await self._screenshot("project_error")
            return False

    async def select_project_by_name(self, project_name: str) -> bool:
        """Vybere konkrétní projekt podle jména – používá se v multi-project módu."""
        logger.info(f"Přepínám na projekt: {project_name}")
        try:
            projects_locator = self.page.locator("text=Your Projects").or_(
                self.page.locator("text=Vaše projekty")
            )

            # Pokud nejsme na stránce projektů, navigovat na ni
            if await projects_locator.count() == 0:
                # Nejprve zkusit link na domovskou stránku v sidebaru (ikonka domu)
                home_link = self.page.locator('a[href="/account/personal"]').first
                if await home_link.count() > 0:
                    await home_link.click()
                    await self._wait_idle()
                    await self.page.wait_for_timeout(2_000)

                # Pokud stále nejsme na projects stránce, navigovat přímo
                if await projects_locator.count() == 0:
                    await self.page.goto("https://domeum.app/account/personal", wait_until="domcontentloaded")
                    await self._wait_idle()
                    await self.page.wait_for_timeout(2_000)

            await self._screenshot(f"before_project_click_{project_name[:10]}")

            # Kliknout na projekt (s fallbackem – text může být součástí karty nebo sidebaru)
            project_card = self.page.locator(f"text={project_name}").first
            await project_card.wait_for(timeout=10_000)
            await project_card.click()
            await self._wait_idle()
            logger.info(f"Projekt '{project_name}' vybrán")
            return True
        except Exception as e:
            logger.error(f"Projekt '{project_name}' nenalezen: {e}")
            await self._screenshot(f"project_{project_name}_error")
            return False

    # ─────────────────────────────── Stavební deník ───────────────────────────

    async def navigate_to_diary(self) -> bool:
        """Přejde do sekce Build Records (stavební deník)."""
        logger.info("Přecházím na Build Records")
        await self.page.wait_for_timeout(2_000)
        await self._screenshot("diary_nav_start")
        try:
            current_url = self.page.url
            logger.info(f"Aktuální URL: {current_url}")

            # JS debug: zjistit strukturu DOM
            await self.page.wait_for_timeout(3_000)
            dom_info = await self.page.evaluate("""() => {
                const ce = Array.from(document.querySelectorAll('[contenteditable]')).map(el => ({
                    tag: el.tagName, ce: el.getAttribute('contenteditable'),
                    class: el.className.substring(0, 60)
                }));
                const hasText = document.body.innerText.includes('New record');
                let foundEl = null;
                for (const el of document.querySelectorAll('*')) {
                    if (el.children.length === 0 && el.textContent.trim() === 'New record...') {
                        foundEl = {tag: el.tagName, class: el.className.substring(0, 60),
                                   role: el.getAttribute('role'), html: el.outerHTML.substring(0, 150)};
                        break;
                    }
                }
                return {ceElements: ce, hasText, foundEl};
            }""")
            logger.info(f"DOM debug: {dom_info}")

            # Pokud jsme na /records stránce
            if "/records" in current_url:
                # Zkusit Playwright get_by_placeholder
                try:
                    nr = self.page.get_by_placeholder("New record...")
                    if await nr.count() > 0:
                        logger.info("Deník nalezen přes get_by_placeholder")
                        return True
                except Exception:
                    pass
                # Zkusit JS detekci textu
                if dom_info.get("hasText") or dom_info.get("foundEl"):
                    logger.info("Deník nalezen přes JS text detekci")
                    return True
                # Zkusit [contenteditable] bez hodnoty
                for sel in ['[contenteditable]', 'textarea', '[placeholder*="record" i]']:
                    locator = self.page.locator(sel).first
                    if await locator.count() > 0:
                        logger.info(f"Deník nalezen: {sel}")
                        return True

            # Kliknout na "Build Records" v levém postranním menu
            build_rec = self.page.locator('text=Build Records').first
            if await build_rec.count() > 0:
                await build_rec.click()
                await self._wait_idle()
                await self.page.wait_for_timeout(3_000)
                await self._screenshot("diary_nav_after_click")
                dom_info2 = await self.page.evaluate("() => document.body.innerText.includes('New record')")
                if dom_info2:
                    logger.info("Deník nalezen po kliknutí Build Records")
                    return True

            await self._screenshot("diary_nav_error")
            raise RuntimeError("Build Records stránka nedostupná")

        except Exception as e:
            logger.error(f"Nelze přejít na Build Records: {e}")
            await self._screenshot("diary_nav_error")
            return False

    # ─────────────────────────────── Nový záznam ──────────────────────────────

    async def create_diary_entry(
        self,
        text: str,
        date: str,
        photo_paths: List[str],
    ) -> bool:
        """
        Vytvoří nový záznam v stavebním deníku.

        Args:
            text:        Text zápisu vygenerovaný AI
            date:        Datum ve formátu YYYY-MM-DD
            photo_paths: Cesty ke staženým fotkám
        """
        if self.dry_run:
            logger.info(f"[DRY RUN] Zápis pro {date} by byl vytvořen ({len(photo_paths)} fotek)")
            logger.info(f"[DRY RUN] Text: {text[:200]}...")
            return True

        logger.info(f"Vytvářím zápis pro {date} ({len(photo_paths)} fotek)")

        try:
            # 0. Navigovat na deník těsně před zápisem – stránka se mohla změnit
            #    během generování AI textu (Gemini ~20s) a 'New record...' zmizel
            await self.navigate_to_diary()

            # 1. Otevřít modal nového záznamu
            await self._open_new_entry_modal()

            # 2. Vyplnit text
            await self._fill_text(text)

            # 3. Nastavit datum (pokud není dnes)
            today = datetime.now().strftime("%Y-%m-%d")
            if date != today:
                await self._set_entry_date(date)

            # 4. Nahrát fotky
            if photo_paths:
                await self._upload_photos(photo_paths)

            # 5. Odeslat
            await self._submit_entry()

            logger.info(f"✅ Zápis pro {date} vytvořen")
            return True

        except Exception as e:
            logger.error(f"❌ Chyba při vytváření zápisu pro {date}: {e}")
            await self._screenshot(f"entry_error_{date}")
            return False

    async def _open_new_entry_modal(self) -> None:
        """Klikne na pole 'New record...' pro zahájení záznamu."""
        # Scrollovat na začátek stránky – placeholder může být mimo viewport
        await self.page.evaluate("window.scrollTo(0, 0)")
        await self.page.wait_for_timeout(500)

        # Najít souřadnice rodičovského kontejneru a scrollovat do view
        coords = await self.page.evaluate("""() => {
            for (const el of document.querySelectorAll('*')) {
                if (el.children.length === 0 && el.textContent.trim() === 'New record...') {
                    let target = el.parentElement;
                    while (target && target !== document.body) {
                        const rect = target.getBoundingClientRect();
                        if (rect.width > 200 && rect.height > 30) {
                            // Scrollovat do view pokud je mimo viewport
                            if (rect.y < 0 || rect.y > window.innerHeight) {
                                target.scrollIntoView({ behavior: 'instant', block: 'center' });
                            }
                            const r2 = target.getBoundingClientRect();
                            return {x: r2.x + r2.width / 2, y: r2.y + r2.height / 2,
                                    tag: target.tagName, cls: target.className.substring(0, 80)};
                        }
                        target = target.parentElement;
                    }
                    const r = el.getBoundingClientRect();
                    if (r.y < 0 || r.y > window.innerHeight) {
                        el.scrollIntoView({ behavior: 'instant', block: 'center' });
                    }
                    const r2 = el.getBoundingClientRect();
                    return {x: r2.x + r2.width/2, y: r2.y + r2.height/2, tag: el.tagName, cls: 'placeholder'};
                }
            }
            return null;
        }""")

        if coords:
            logger.info(f"Klikám na kontejner: {coords}")
            await self.page.mouse.click(coords['x'], coords['y'])
            await self.page.wait_for_timeout(2_000)
            await self._screenshot("new_record_opened")
            return

        raise RuntimeError("Nelze najít 'New record...' kontejner")

    async def _fill_text(self, text: str) -> None:
        """Vyplní text záznamu do aktivního vstupního pole."""
        # Metoda 1: Playwright keyboard (funguje s contenteditable i textarea)
        # Po kliknutí na "New record..." je element fokusovaný – stačí psát
        await self.page.keyboard.type(text, delay=10)
        await self._screenshot("text_filled")
        logger.debug("Text vyplněn přes keyboard.type()")

    async def _set_entry_date(self, date: str) -> None:
        """Nastaví datum záznamu."""
        logger.info(f"Nastavuji datum: {date}")
        try:
            # Klikneme na datum picker (zobrazuje "Dnes")
            date_btn_selectors = [
                "text=Dnes",
                "text=Today",
                "[aria-label*='datum']",
                "[aria-label*='date']",
                "button:has-text('Dnes')",
                "button:has-text('Today')",
                ".date-picker",
            ]
            for sel in date_btn_selectors:
                locator = self.page.locator(sel).first
                if await locator.count() > 0:
                    await locator.click(force=True)
                    await self.page.wait_for_timeout(800)
                    break

            # Zkusíme input[type="date"]
            date_input = self.page.locator('input[type="date"]').first
            if await date_input.count() > 0:
                await date_input.fill(date)
                await self.page.keyboard.press("Escape")
                logger.debug(f"Datum nastaveno přes input[type=date]: {date}")
                return

            # Fallback: zadání data v českém formátu do textového pole
            dt = datetime.strptime(date, "%Y-%m-%d")
            czech_date = dt.strftime("%d.%m.%Y")
            date_text_input = self.page.locator('input[placeholder*="DD"]').first
            if await date_text_input.count() > 0:
                await date_text_input.fill(czech_date)
                logger.debug(f"Datum nastaveno jako text: {czech_date}")

        except Exception as e:
            logger.warning(f"Nepodařilo se nastavit datum {date}: {e}. Použije se dnešní datum.")

    async def _upload_photos(self, photo_paths: List[str]) -> None:
        """Nahraje fotky k záznamu."""
        logger.info(f"Nahrávám {len(photo_paths)} fotek...")
        await self._screenshot("before_upload")

        # Metoda 1: přímý input[type="file"] (hidden)
        file_input = self.page.locator('input[type="file"]').first
        if await file_input.count() > 0:
            await file_input.set_input_files(photo_paths)
            await self.page.wait_for_timeout(min(2_000 * len(photo_paths), UPLOAD_TIMEOUT))
            logger.debug("Fotky nahrány přes file input")
            return

        # Metoda 2: kliknout na ikonku obrázku vedle "New record..." a file chooser
        photo_btn_selectors = [
            '[aria-label*="photo" i]',
            '[aria-label*="image" i]',
            '[aria-label*="foto" i]',
            '[title*="photo" i]',
            '[title*="image" i]',
            'button:has-text("Add photo")',
            'button:has-text("Photo")',
        ]
        for sel in photo_btn_selectors + ['button']:
            locator = self.page.locator(sel).first
            if await locator.count() > 0:
                try:
                    async with self.page.expect_file_chooser(timeout=5_000) as fc_info:
                        await locator.click()
                    file_chooser = await fc_info.value
                    await file_chooser.set_files(photo_paths)
                    await self.page.wait_for_timeout(min(3_000 * len(photo_paths), UPLOAD_TIMEOUT))
                    logger.debug(f"Fotky nahrány přes file chooser ({sel})")
                    return
                except Exception as e:
                    logger.debug(f"File chooser selhal ({sel}): {e}")
                    continue

        logger.warning("Nepodařilo se nahrát fotky")

    async def _submit_entry(self) -> None:
        """Odešle formulář záznamu."""
        submit_selectors = [
            'button[type="submit"]',
            'button:has-text("Uložit")',
            'button:has-text("Přidat")',
            'button:has-text("Vytvořit")',
            'button:has-text("Potvrdit")',
            'button:has-text("Save")',
            'button:has-text("Add")',
            'button:has-text("Create")',
            'button:has-text("Confirm")',
            'button:has-text("Submit")',
            'button:has-text("OK")',
        ]
        for sel in submit_selectors:
            locator = self.page.locator(sel).last  # Poslední submit button v modalu
            if await locator.count() > 0:
                await locator.click(force=True)
                await self._wait_idle()
                await self.page.wait_for_timeout(2_000)
                logger.debug(f"Záznam odeslán přes: {sel}")
                return

        # Fallback: Enter
        await self.page.keyboard.press("Enter")
        await self._wait_idle()
        logger.debug("Záznam odeslán přes Enter")
