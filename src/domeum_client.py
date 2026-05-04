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
        await self._wait_idle()

        try:
            await self.page.fill('input[type="email"]', self.email)
            await self.page.fill('input[type="password"]', self.password)
            await self.page.click('button:has-text("Přihlásit se pomocí e-mailu")')
            await self._wait_idle()
            logger.info("Přihlášení úspěšné")
            return True
        except Exception as e:
            logger.error(f"Přihlášení selhalo: {e}")
            await self._screenshot("login_error")
            return False

    # ─────────────────────────────── Projekt ──────────────────────────────────

    async def select_project(self) -> bool:
        """Vybere projekt dle DOMEUM_PROJECT_NAME."""
        logger.info(f"Hledám projekt: {self.project_name}")
        try:
            # Počkáme na seznam projektů
            await self.page.wait_for_selector("text=Vaše projekty", timeout=10_000)
            project_card = self.page.locator(f"text={self.project_name}").first
            await project_card.click()
            await self._wait_idle()
            logger.info(f"Projekt '{self.project_name}' vybrán")
            return True
        except Exception as e:
            logger.error(f"Projekt nenalezen: {e}")
            await self._screenshot("project_error")
            return False

    # ─────────────────────────────── Stavební deník ───────────────────────────

    async def navigate_to_diary(self) -> bool:
        """Přejde do sekce Stavební deník."""
        logger.info("Přecházím na Stavební deník")
        try:
            diary_link = self.page.locator("text=Stavební deník").first
            await diary_link.click()
            await self._wait_idle()
            # Ověříme, že jsme v deníku
            await self.page.wait_for_selector("text=Nový záznam", timeout=10_000)
            logger.info("Stavební deník nalezen")
            return True
        except Exception as e:
            logger.error(f"Nelze přejít na Stavební deník: {e}")
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
        """Otevře modal pro nový záznam."""
        # Zkusíme různé selektory (domeum.app může mít různé implementace)
        selectors = [
            "text=Nový záznam...",
            "text=Nový záznam",
            "[placeholder*='záznam']",
            "[placeholder*='Popište']",
        ]

        for sel in selectors:
            locator = self.page.locator(sel).first
            if await locator.count() > 0:
                await locator.click()
                await self.page.wait_for_timeout(1_500)
                logger.debug(f"Modal otevřen přes selektor: {sel}")
                return

        raise RuntimeError("Nelze najít tlačítko 'Nový záznam'")

    async def _fill_text(self, text: str) -> None:
        """Vyplní textové pole v modalu."""
        # Hledáme textarea v modalu
        selectors = [
            'textarea[placeholder*="Popište"]',
            'textarea[placeholder*="popište"]',
            'div[contenteditable="true"]',
            "textarea",
        ]
        for sel in selectors:
            locator = self.page.locator(sel).first
            if await locator.count() > 0:
                await locator.click()
                await locator.fill(text)
                logger.debug("Text vyplněn")
                return

        raise RuntimeError("Nelze najít textové pole v modalu")

    async def _set_entry_date(self, date: str) -> None:
        """Nastaví datum záznamu."""
        logger.info(f"Nastavuji datum: {date}")
        try:
            # Klikneme na datum picker (zobrazuje "Dnes")
            date_btn_selectors = [
                "text=Dnes",
                "[aria-label*='datum']",
                "[aria-label*='date']",
                "button:has-text('Dnes')",
                ".date-picker",
            ]
            for sel in date_btn_selectors:
                locator = self.page.locator(sel).first
                if await locator.count() > 0:
                    await locator.click()
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
        """Nahraje fotky jako přílohy k záznamu."""
        logger.info(f"Nahrávám {len(photo_paths)} fotek...")

        # Metoda 1: přímý input[type="file"]
        file_input = self.page.locator('input[type="file"]').first
        if await file_input.count() > 0:
            await file_input.set_input_files(photo_paths)
            await self.page.wait_for_timeout(2_000 * len(photo_paths))
            logger.debug("Fotky nahrány přes file input")
            return

        # Metoda 2: klik na "Přidat přílohu" a file chooser
        attach_selectors = [
            'button:has-text("Přidat přílohu")',
            'text=Přidat přílohu',
            '[aria-label*="přílohu"]',
            '[aria-label*="photo"]',
            '[aria-label*="foto"]',
        ]
        for sel in attach_selectors:
            locator = self.page.locator(sel).first
            if await locator.count() > 0:
                try:
                    async with self.page.expect_file_chooser(timeout=5_000) as fc_info:
                        await locator.click()
                    file_chooser = await fc_info.value
                    await file_chooser.set_files(photo_paths)
                    # Čekáme na dokončení uploadu (přibližně 3s na fotku)
                    await self.page.wait_for_timeout(min(3_000 * len(photo_paths), UPLOAD_TIMEOUT))
                    logger.debug(f"Fotky nahrány přes file chooser ({sel})")
                    return
                except Exception as e:
                    logger.debug(f"File chooser selhal ({sel}): {e}")
                    continue

        logger.warning("Nepodařilo se nahrát fotky – nepodporovaný upload mechanismus")

    async def _submit_entry(self) -> None:
        """Odešle formulář záznamu."""
        submit_selectors = [
            'button[type="submit"]',
            'button:has-text("Uložit")',
            'button:has-text("Přidat")',
            'button:has-text("Vytvořit")',
            'button:has-text("Potvrdit")',
            'button:has-text("OK")',
        ]
        for sel in submit_selectors:
            locator = self.page.locator(sel).last  # Poslední submit button v modalu
            if await locator.count() > 0:
                await locator.click()
                await self._wait_idle()
                await self.page.wait_for_timeout(2_000)
                logger.debug(f"Záznam odeslán přes: {sel}")
                return

        # Fallback: Enter
        await self.page.keyboard.press("Enter")
        await self._wait_idle()
        logger.debug("Záznam odeslán přes Enter")
