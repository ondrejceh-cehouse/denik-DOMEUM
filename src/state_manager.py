"""
state_manager.py – Správa stavu zpracovaných fotek
Ukládá JSON soubor do data/processed_photos.json aby nedocházelo k duplikátům.
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

STATE_FILE = Path(__file__).parent.parent / "data" / "processed_photos.json"


def load_state() -> dict:
    """Načte stav ze souboru. Vrátí prázdný stav pokud soubor neexistuje."""
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)
                logger.info(f"Stav načten: {STATE_FILE}")
                return state
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Nelze načíst stav: {e}. Používám prázdný stav.")

    return {
        "processed_photos": {},   # {folder_id: {photo_id: "YYYY-MM-DD"}}
        "diary_entries": {}        # {folder_id: {"YYYY-MM-DD": "created"}}
    }


def save_state(state: dict) -> None:
    """Uloží stav do souboru."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    logger.info(f"Stav uložen: {STATE_FILE}")


def is_photo_processed(state: dict, folder_id: str, photo_id: str) -> bool:
    """Vrátí True pokud fotka již byla zpracována."""
    return photo_id in state.get("processed_photos", {}).get(folder_id, {})


def mark_photo_processed(state: dict, folder_id: str, photo_id: str, date: str) -> None:
    """Označí fotku jako zpracovanou."""
    state.setdefault("processed_photos", {}).setdefault(folder_id, {})[photo_id] = date


def has_diary_entry(state: dict, folder_id: str, date: str) -> bool:
    """Vrátí True pokud pro danou složku a datum již existuje zápis v deníku."""
    return date in state.get("diary_entries", {}).get(folder_id, {})


def mark_diary_entry(state: dict, folder_id: str, date: str) -> None:
    """Označí zápis v deníku jako vytvořený."""
    state.setdefault("diary_entries", {}).setdefault(folder_id, {})[date] = "created"


def get_stats(state: dict) -> dict:
    """Vrátí statistiky stavu."""
    total_photos = sum(
        len(photos)
        for photos in state.get("processed_photos", {}).values()
    )
    total_entries = sum(
        len(entries)
        for entries in state.get("diary_entries", {}).values()
    )
    return {
        "total_photos_processed": total_photos,
        "total_diary_entries": total_entries,
        "folders": list(state.get("processed_photos", {}).keys())
    }
