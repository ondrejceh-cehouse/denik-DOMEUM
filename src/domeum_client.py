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
            # Pokud už jsme na stránce projektů, přeskočit navigaci
            projects_locator = self.page.locator("text=Your Projects").or_(
                self.page.locator("text=Vaše projekty")
            )
            if await projects_locator.count() == 0:
                # Zkusíme sidebar home button (ikona domečku)
                home_btn = self.page.locator('a[href="/account/personal"], a[href="/"], [aria-label*="home" i], [aria-label*="Home"]').first
                if await home_btn.count() > 0:
                    await home_btn.click()
                    await self._wait_idle()
                else:
                    # Fallback: zkusit různé URL dokud nenajdeme projekty
                    for url in [
                        "https://domeum.app/account/personal",
                        "https://domeum.app/account",
                        "https://domeum.app/projects",
                        "https://domeum.app",
                    ]:
                        await self.page.goto(url, wait_until="domcontentloaded")
                        await self._wait_idle()
                        if await projects_locator.count() > 0:
                            logger.info(f"Projekty nalezeny na: {url}")
                            break

            await self._screenshot(f"before_project_click_{project_name[:10]}")
            await projects_locator.first.wait_for(timeout=10_000)

            project_card = self.page.locator(f"text={project_name}").first
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
        """Přejde do sekce Stavební deník."""
        logger.info("Přecházím na Stavební deník")
        await self.page.wait_for_timeout(3_000)
        await self._screenshot("diary_nav_start")
        try:
            # Logovat všechny linky pro debug
            all_links = await self.page.locator("a").all()
            link_texts = []
            link_hrefs = []
            for link in all_links[:30]:
                try:
                    t = (await link.text_content() or "").strip()
                    h = await link.get_attribute("href") or ""
                    if t or h:
                        link_texts.append(t)
                        link_hrefs.append(h)
                except Exception:
                    pass
            logger.info(f"Všechny linky – texty: {link_texts}")
            logger.info(f"Všechny linky – href: {link_hrefs}")

            # Klíčová slova pro stavební deník (česky i anglicky)
            diary_keywords = ["stavební deník", "construction diary", "site diary", "diary", "deník", "denik"]

            # Hledáme odkaz podle textu
            for link_el, text, href in zip(all_links[:30], link_texts, link_hrefs):
                t_low = text.lower()
                h_low = href.lower()
                if any(kw in t_low or kw in h_low for kw in diary_keywords):
                    logger.info(f"Nalezen deník link: text='{text}' href='{href}'")
                    await link_el.click()
                    await self._wait_idle()
                    await self._screenshot("diary_nav_after_click")
                    logger.info("Stavební deník nalezen")
                    return True

            # Fallback: zkusit URL manipulaci
            current_url = self.page.url
            logger.info(f"Aktuální URL: {current_url}")
            for suffix in ["/diary", "/construction-diary", "/stavebni-denik", "/denik"]:
                base = current_url.rstrip("/")
                # Zkusit připojit suffix k base URL projektu
                if "/account" in base:
                    parts = base.split("/")
                    # Najít index projektu a sestavit URL
                    diary_url = base + suffix
                    await self.page.goto(diary_url, wait_until="domcontentloaded")
                    await self.page.wait_for_timeout(2_000)
                    if "diary" in self.page.url.lower() or "denik" in self.page.url.lower():
                        await self._screenshot("diary_nav_url_success")
                        logger.info(f"Deník nalezen přes URL: {self.page.url}")
                        return True
                    break

            await self._screenshot("diary_nav_error")
            raise RuntimeError("Deník nenalezen – zkontrolujte screenshoty")

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
            "text=New record...",
            "text=New record",
            "text=New entry",
            "[placeholder*='záznam']",
            "[placeholder*='Popište']",
            "[placeholder*='Describe']",
            "[placeholder*='describe']",
            "[placeholder*='record']",
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
            'textarea[placeholder*="Describe"]',
            'textarea[placeholder*="describe"]',
            'textarea[placeholder*="record"]',
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
                await locator.click()
                await self._wait_idle()
                await self.page.wait_for_timeout(2_000)
                logger.debug(f"Záznam odeslán přes: {sel}")
                return

        # Fallback: Enter
        await self.page.keyboard.press("Enter")
        await self._wait_idle()
        logger.debug("Záznam odeslán přes Enter")
