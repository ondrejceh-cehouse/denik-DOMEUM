"""
ai_analyzer.py – Google Gemini AI analýza fotek
Pošle fotografie do Gemini 1.5 Flash a vygeneruje odborný zápis do stavebního deníku.
"""

import logging
import os
import time
from pathlib import Path
from typing import List, Optional

import google.generativeai as genai
from PIL import Image

logger = logging.getLogger(__name__)

# Maximální počet fotek v jednom Gemini požadavku
MAX_PHOTOS_PER_REQUEST = 10

# Počet opakování při chybě Gemini API
MAX_RETRIES = 3
RETRY_DELAY = 10  # sekund


def init_gemini():
    """Inicializuje Gemini API klienta."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("Chybí environment variable GEMINI_API_KEY")

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-1.5-flash")
    logger.info("Gemini 1.5 Flash inicializován")
    return model


def _build_prompt(action_name: str, date: str, photo_count: int) -> str:
    """Sestaví prompt pro Gemini."""
    return f"""Jsi odborný asistent pro vedení stavebního deníku. Pracuješ na stavbě rodinného domu v České republice.

Analyzuj přiložené fotografie a vytvoř **profesionální zápis do stavebního deníku** dle českých stavebních norem a zvyklostí.

═══════════════════════════════════════
Název akce / prováděné práce: {action_name}
Datum: {date}
Počet fotografií: {photo_count}
═══════════════════════════════════════

**Struktura zápisu (použij přesně toto pořadí):**

1. **Popis prováděných prací:**
   Věcný popis co se provádělo, v jaké etapě, na které části stavby.

2. **Pracovníci na stavbě:**
   Počet a případně profese viditelných pracovníků. Pokud nejsou viditelní, napiš "Dle prezenční listiny".

3. **Nasazené stroje a mechanizace:**
   Konkrétní stroje, nářadí, mechanizmy. Pokud nejsou viditelné, napiš "Ruční práce, bez mechanizace" nebo vynech.

4. **Použité materiály:**
   Identifikované materiály, výrobky, prvky. Pokud není možné identifikovat, napiš "Dle dodacích listů".

5. **Klimatické podmínky:**
   Pokud jsou patrné z fotek (světlo, stíny, sníh, déšť), jinak: "Dle záznamu – doplňte ručně".

6. **Bezpečnost práce:**
   Viditelné OOPP (helmy, vesty, rukavice), zabezpečení staveniště. Pokud není patrné: "Dle plánu BOZP".

7. **Postup a stav prací:**
   Jak pokročily práce, co bylo dokončeno, co zbývá.

---
⚠️ *Automaticky vygenerováno AI na základě fotografií ze dne {date}. Před schválením zápisu prosím zkontrolujte a doplňte: přesný počet pracovníků, dodané materiály s množstvím a dodacími listy, klimatické podmínky, případné mimořádné události.*

---

**Pokyny pro styl:**
- Piš v minulém čase, věcně a stručně
- Používej odbornou stavební terminologii  
- Nepište "Na fotografiích vidím..." – piš přímo jako záznamy do deníku
- Zápis by měl mít 150–400 slov
- Piš výhradně česky"""


def analyze_photos_for_diary(
    model,
    photo_paths: List[str],
    action_name: str,
    date: str
) -> str:
    """
    Analyzuje fotky a vygeneruje zápis do stavebního deníku.
    Pokud je fotek více než MAX_PHOTOS_PER_REQUEST, zpracuje je po skupinách.
    """
    if not photo_paths:
        logger.warning("Žádné fotky k analýze")
        return _fallback_entry(action_name, date, "Žádné fotky k dispozici")

    # Načtení fotek
    loaded_images = []
    for path in photo_paths:
        try:
            img = Image.open(path)
            # Zmenšení pro API (max 1920px na delší straně)
            img.thumbnail((1920, 1920), Image.LANCZOS)
            loaded_images.append(img)
        except Exception as e:
            logger.warning(f"Nelze načíst obrázek {path}: {e}")

    if not loaded_images:
        return _fallback_entry(action_name, date, "Fotky se nepodařilo načíst")

    # Zpracování po skupinách
    if len(loaded_images) > MAX_PHOTOS_PER_REQUEST:
        logger.info(f"Dělím {len(loaded_images)} fotek do skupin po {MAX_PHOTOS_PER_REQUEST}")
        groups = [
            loaded_images[i:i + MAX_PHOTOS_PER_REQUEST]
            for i in range(0, len(loaded_images), MAX_PHOTOS_PER_REQUEST)
        ]
        partial_texts = []
        for i, group in enumerate(groups):
            logger.info(f"Zpracovávám skupinu {i+1}/{len(groups)} ({len(group)} fotek)")
            text = _call_gemini(model, group, action_name, date, len(loaded_images))
            if text:
                partial_texts.append(text)
            time.sleep(2)  # Rate limiting

        if partial_texts:
            # Pokud máme více částí, vezmi první kompletní zápis
            return partial_texts[0]
        return _fallback_entry(action_name, date, "Analýza selhala")
    else:
        return _call_gemini(model, loaded_images, action_name, date, len(loaded_images))


def _call_gemini(model, images: List, action_name: str, date: str, total_photos: int) -> str:
    """Volání Gemini API s retry logikou."""
    prompt = _build_prompt(action_name, date, total_photos)

    for attempt in range(MAX_RETRIES):
        try:
            response = model.generate_content([prompt] + images)
            text = response.text.strip()
            logger.info(f"Gemini vygeneroval zápis ({len(text)} znaků)")
            return text

        except Exception as e:
            logger.warning(f"Gemini chyba (pokus {attempt+1}/{MAX_RETRIES}): {e}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY * (attempt + 1))

    return _fallback_entry(action_name, date, "Chyba API")


def _fallback_entry(action_name: str, date: str, reason: str) -> str:
    """Nouzový zápis když AI selže."""
    return (
        f"**Automatický zápis – {action_name}**\n\n"
        f"Datum: {date}\n\n"
        f"Na stavbě probíhaly práce v rámci akce „{action_name}". "
        f"Automatická analýza fotografií nebyla dostupná ({reason}). "
        f"Fotografie jsou přiloženy k záznamu.\n\n"
        f"⚠️ *Tento zápis vyžaduje ruční doplnění: popis prací, pracovníci, "
        f"materiály, klimatické podmínky, bezpečnost práce.*"
    )
