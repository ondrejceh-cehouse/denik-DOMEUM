"""
ai_analyzer.py - Google Gemini AI analyza fotek
Posle fotografie do Gemini 1.5 Flash a vygeneruje odborny zapis do stavebniho deniku.
"""

import logging
import os
import time
from pathlib import Path
from typing import List, Optional

import google.generativeai as genai
from PIL import Image

logger = logging.getLogger(__name__)

MAX_PHOTOS_PER_REQUEST = 10
MAX_RETRIES = 3
RETRY_DELAY = 10


GEMINI_MODELS = [
    "gemini-2.5-flash-preview-04-17",
    "gemini-2.0-flash-lite",
    "gemini-2.0-flash-lite-001",
    "gemini-1.5-flash-002",
    "gemini-1.5-flash-8b",
]


def init_gemini():
    """Inicializuje Gemini API klienta – zkusi první dostupný model."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("Chybi environment variable GEMINI_API_KEY")
    genai.configure(api_key=api_key)

    for model_name in GEMINI_MODELS:
        try:
            model = genai.GenerativeModel(model_name)
            # Rychly test dostupnosti
            model.generate_content("test", generation_config={"max_output_tokens": 1})
            logger.info(f"Gemini inicializovan: {model_name}")
            return model
        except Exception as e:
            logger.warning(f"Model {model_name} nedostupny: {e}")

    raise RuntimeError(f"Zadny Gemini model neni dostupny. Zkouseno: {GEMINI_MODELS}")


def _build_prompt(action_name: str, date: str, photo_count: int) -> str:
    """Sestavi prompt pro Gemini."""
    return (
        "Jsi odborny asistent pro vedeni stavebniho deniku. "
        "Pracujes na stavbe rodinneho domu v Ceske republice.\n\n"
        "Analyzuj prilozene fotografie a vytvor profesionalni zapis "
        "do stavebniho deniku dle ceskych stavebnich norem a zvyklosti.\n\n"
        f"Nazev akce: {action_name}\n"
        f"Datum: {date}\n"
        f"Pocet fotografii: {photo_count}\n\n"
        "Struktura zapisu (pouzij presne toto poradi):\n\n"
        "1. Popis provadenych praci:\n"
        "   Vecny popis co se provadelo, v jake etape, na ktere casti stavby.\n\n"
        "2. Pracovnici na stavbe:\n"
        "   Pocet a pripadne profese viditelnych pracovniku. "
        "Pokud nejsou viditelni, napis: Dle prezencni listiny.\n\n"
        "3. Nasazene stroje a mechanizace:\n"
        "   Konkretni stroje, naradi, mechanizmy. "
        "Pokud nejsou viditelne, napis: Rucni prace, bez mechanizace.\n\n"
        "4. Pouzite materialy:\n"
        "   Identifikovane materialy, vyrobky, prvky. "
        "Pokud neni mozne identifikovat, napis: Dle dodacich listu.\n\n"
        "5. Klimaticke podminky:\n"
        "   Pokud jsou patne z fotek, jinak napis: Dle zaznamu - doplnte rucne.\n\n"
        "6. Bezpecnost prace:\n"
        "   Viditelne OOPP (helmy, vesty, rukavice), zabezpeceni stavenis te. "
        "Pokud neni patne: Dle planu BOZP.\n\n"
        "7. Postup a stav praci:\n"
        "   Jak pokrocily prace, co bylo dokonceno, co zbyvá.\n\n"
        "---\n"
        f"POZNAMKA: Automaticky vygenerovano AI na zaklade fotografii ze dne {date}. "
        "Pred schvalenim zapisu prosim zkontrolujte a doplnte: "
        "presny pocet pracovniku, dodane materialy s mnozstvim a dodacimi listy, "
        "klimaticke podminky, pripadne mimoradne udalosti.\n\n"
        "Pokyny pro styl:\n"
        "- Pis v minulem case, vecne a strucne\n"
        "- Pouzivej odbornou stavebni terminologii\n"
        "- Nepis 'Na fotografiich vidim...' - pis primo jako zaznamy do deniku\n"
        "- Zapis by mel mit 150-400 slov\n"
        "- Pis vyhradne cesky (s diakritikou)"
    )


def analyze_photos_for_diary(
    model,
    photo_paths: List[str],
    action_name: str,
    date: str
) -> str:
    """
    Analyzuje fotky a vygeneruje zapis do stavebniho deniku.
    Pokud je fotek vice nez MAX_PHOTOS_PER_REQUEST, zpracuje je po skupinach.
    """
    if not photo_paths:
        logger.warning("Zadne fotky k analyze")
        return _fallback_entry(action_name, date, "Zadne fotky k dispozici")

    loaded_images = []
    for path in photo_paths:
        try:
            img = Image.open(path)
            img.thumbnail((1920, 1920), Image.LANCZOS)
            loaded_images.append(img)
        except Exception as e:
            logger.warning(f"Nelze nacist obrazek {path}: {e}")

    if not loaded_images:
        return _fallback_entry(action_name, date, "Fotky se nepodarilo nacist")

    if len(loaded_images) > MAX_PHOTOS_PER_REQUEST:
        logger.info(f"Delim {len(loaded_images)} fotek do skupin po {MAX_PHOTOS_PER_REQUEST}")
        groups = [
            loaded_images[i:i + MAX_PHOTOS_PER_REQUEST]
            for i in range(0, len(loaded_images), MAX_PHOTOS_PER_REQUEST)
        ]
        for i, group in enumerate(groups):
            logger.info(f"Zpracovavam skupinu {i+1}/{len(groups)}")
            text = _call_gemini(model, group, action_name, date, len(loaded_images))
            if text:
                return text
            time.sleep(2)
        return _fallback_entry(action_name, date, "Analyza selhala")
    else:
        return _call_gemini(model, loaded_images, action_name, date, len(loaded_images))


def _call_gemini(model, images: List, action_name: str, date: str, total_photos: int) -> str:
    """Volani Gemini API s retry logikou."""
    prompt = _build_prompt(action_name, date, total_photos)

    for attempt in range(MAX_RETRIES):
        try:
            response = model.generate_content([prompt] + images)
            text = response.text.strip()
            logger.info(f"Gemini vygeneroval zapis ({len(text)} znaku)")
            return text
        except Exception as e:
            logger.warning(f"Gemini chyba (pokus {attempt+1}/{MAX_RETRIES}): {e}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY * (attempt + 1))

    return _fallback_entry(action_name, date, "Chyba API")


def _fallback_entry(action_name: str, date: str, reason: str) -> str:
    """Nouzovy zapis kdyz AI selze."""
    return (
        f"Automaticky zapis - {action_name}\n\n"
        f"Datum: {date}\n\n"
        f"Na stavbe probihaly prace v ramci akce {action_name}. "
        f"Automaticka analyza fotografii nebyla dostupna ({reason}). "
        f"Fotografie jsou prilozeny k zaznamu.\n\n"
        f"Tento zapis vyzaduje rucni doplneni: popis praci, pracovnici, "
        f"materialy, klimaticke podminky, bezpecnost prace."
    )
