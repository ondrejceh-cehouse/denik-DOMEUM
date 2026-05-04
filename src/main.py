"""
main.py – Hlavní orchestrátor stavebního deníku
Spouštěn GitHub Actions každý večer. Koordinuje Drive → AI → domeum.app pipeline.
"""

import asyncio
import logging
import os
import sys
import tempfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path

# Přidáme src do cesty
sys.path.insert(0, str(Path(__file__).parent))

from google_drive_client import (
    get_drive_service,
    get_subfolders,
    get_photos_in_folder,
    download_photo,
    get_photo_date,
    format_date_czech,
)
from domeum_client import DomeumClient
from ai_analyzer import init_gemini, analyze_photos_for_diary
from state_manager import (
    load_state,
    save_state,
    is_photo_processed,
    mark_photo_processed,
    has_diary_entry,
    mark_diary_entry,
    get_stats,
)

# ─────────────────────────────── Logging ──────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s – %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("main")

# ─────────────────────────────── Konfigurace ──────────────────────────────────

MAIN_FOLDER_ID   = os.environ["GOOGLE_DRIVE_FOLDER_ID"]
PROJECT_NAME     = os.environ.get("DOMEUM_PROJECT_NAME", "RD Cehovi")
DRY_RUN          = os.environ.get("DRY_RUN", "false").lower() == "true"

# Maximální počet fotek na jeden den (ochrana před obrovskými batchy)
MAX_PHOTOS_PER_DAY = 30


# ─────────────────────────────── Zpracování složky ────────────────────────────

async def process_folder(
    drive_service,
    gemini_model,
    domeum: DomeumClient,
    folder: dict,
    state: dict,
    temp_dir: str,
) -> int:
    """
    Zpracuje jednu podsložku (= jednu akci stavby).
    Vrátí počet vytvořených zápisů.
    """
    folder_id   = folder["id"]
    folder_name = folder["name"]

    logger.info(f"")
    logger.info(f"═══════════════════════════════════════════")
    logger.info(f"📁 Složka: {folder_name}")
    logger.info(f"═══════════════════════════════════════════")

    # Stáhnout seznam všech fotek
    all_photos = get_photos_in_folder(drive_service, folder_id)
    if not all_photos:
        logger.info("  Žádné fotky v složce, přeskakuji.")
        return 0

    # Filtrovat nové (nezpracované) fotky
    new_photos = [p for p in all_photos if not is_photo_processed(state, folder_id, p["id"])]
    logger.info(f"  Celkem fotek: {len(all_photos)} | Nových: {len(new_photos)}")

    if not new_photos:
        logger.info("  Žádné nové fotky, přeskakuji.")
        return 0

    # ── Stáhnout fotky a přečíst datum ──────────────────────────────────────
    photos_by_date: dict[str, list] = defaultdict(list)
    download_errors = 0

    for photo in new_photos:
        dest = os.path.join(temp_dir, f"{photo['id']}_{photo['name']}")
        try:
            fallback = photo.get("createdTime", "")[:10]
            local_path = download_photo(drive_service, photo["id"], photo["mimeType"], dest)
            date = get_photo_date(local_path, fallback)
            photos_by_date[date].append({
                "id":    photo["id"],
                "name":  photo["name"],
                "path":  local_path,
                "date":  date,
            })
            logger.debug(f"  ✓ {photo['name']} → {date}")
        except Exception as e:
            logger.warning(f"  ✗ Chyba stahování {photo['name']}: {e}")
            download_errors += 1

    if download_errors:
        logger.warning(f"  Chyby stahování: {download_errors}/{len(new_photos)}")

    # ── Zpracovat každé datum ─────────────────────────────────────────────────
    entries_created = 0

    for date in sorted(photos_by_date.keys()):
        date_photos = photos_by_date[date]
        czech_date  = format_date_czech(date)

        logger.info(f"")
        logger.info(f"  📅 Datum: {czech_date} ({len(date_photos)} fotek)")

        # Kontrola duplikátů
        if has_diary_entry(state, folder_id, date):
            logger.info(f"  ℹ️  Zápis pro {czech_date} již existuje, označuji fotky a přeskakuji.")
            for p in date_photos:
                mark_photo_processed(state, folder_id, p["id"], date)
            continue

        # Omezení počtu fotek na den
        if len(date_photos) > MAX_PHOTOS_PER_DAY:
            logger.warning(f"  Příliš mnoho fotek ({len(date_photos)}), beru prvních {MAX_PHOTOS_PER_DAY}")
            date_photos = date_photos[:MAX_PHOTOS_PER_DAY]

        photo_paths = [p["path"] for p in date_photos]

        # ── AI analýza ────────────────────────────────────────────────────────
        logger.info(f"  🤖 Generuji AI zápis pro '{folder_name}' – {czech_date}...")
        diary_text = analyze_photos_for_diary(
            gemini_model,
            photo_paths,
            folder_name,
            czech_date,
        )
        logger.info(f"  📝 Vygenerován text: {len(diary_text)} znaků")

        # ── Vytvoření záznamu v domeum.app ────────────────────────────────────
        logger.info(f"  🏗️  Vytvářím záznam v domeum.app...")
        success = await domeum.create_diary_entry(
            text=diary_text,
            date=date,
            photo_paths=photo_paths,
        )

        if success:
            # Označit fotky a záznam jako zpracované
            for p in date_photos:
                mark_photo_processed(state, folder_id, p["id"], date)
            mark_diary_entry(state, folder_id, date)
            entries_created += 1
            logger.info(f"  ✅ Záznam vytvořen: {czech_date}")
        else:
            logger.error(f"  ❌ Vytvoření záznamu selhalo: {czech_date}")

        # Pauza mezi záznamy (netlačit na server)
        await asyncio.sleep(3)

    return entries_created


# ─────────────────────────────── Main ─────────────────────────────────────────

async def main():
    logger.info("═══════════════════════════════════════════════════════")
    logger.info("🏗️  STAVEBNÍ DENÍK BOT – START")
    logger.info(f"⏰ Čas: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if DRY_RUN:
        logger.info("🔍 DRY RUN MODE – záznamy se nevytvářejí!")
    logger.info("═══════════════════════════════════════════════════════")

    # Stav (deduplikace)
    state = load_state()
    stats = get_stats(state)
    logger.info(f"📊 Stav: {stats['total_photos_processed']} fotek zpracováno, "
                f"{stats['total_diary_entries']} zápisů vytvořeno")

    # Inicializace služeb
    logger.info("\n🔧 Inicializace služeb...")
    try:
        drive_service = get_drive_service()
        gemini_model  = init_gemini()
    except Exception as e:
        logger.critical(f"Inicializace selhala: {e}")
        sys.exit(1)

    # Složky v Google Drive
    logger.info(f"\n📂 Skenuji Google Drive: {MAIN_FOLDER_ID}")
    subfolders = get_subfolders(drive_service, MAIN_FOLDER_ID)

    if not subfolders:
        logger.warning(
            "Nenalezeny žádné podsložky! Zkontrolujte:\n"
            "  1. GOOGLE_DRIVE_FOLDER_ID\n"
            "  2. Sdílení složky se service account emailem"
        )
        sys.exit(0)

    logger.info(f"Nalezeno {len(subfolders)} akce: {[f['name'] for f in subfolders]}")

    # Celkový počet vytvořených zápisů
    total_entries = 0

    with tempfile.TemporaryDirectory() as temp_dir:
        logger.info(f"\n🔐 Přihlašuji se na domeum.app jako {os.environ.get('DOMEUM_EMAIL')}")

        async with DomeumClient() as domeum:
            if not await domeum.login():
                logger.critical("Přihlášení selhalo!")
                sys.exit(1)

            if not await domeum.select_project():
                logger.critical(f"Projekt '{PROJECT_NAME}' nenalezen!")
                sys.exit(1)

            if not await domeum.navigate_to_diary():
                logger.critical("Stavební deník nenalezen!")
                sys.exit(1)

            # Zpracovat každou složku (akci)
            for folder in subfolders:
                try:
                    count = await process_folder(
                        drive_service, gemini_model, domeum, folder, state, temp_dir
                    )
                    total_entries += count

                    # Průběžné ukládání stavu (bezpečnost)
                    save_state(state)

                except KeyboardInterrupt:
                    logger.warning("Přerušeno uživatelem")
                    break
                except Exception as e:
                    logger.error(f"Chyba při zpracování '{folder['name']}': {e}", exc_info=True)
                    # Uložit stav i při chybě
                    save_state(state)
                    continue

    # Finální uložení
    save_state(state)

    logger.info("")
    logger.info("═══════════════════════════════════════════════════════")
    logger.info(f"✅ HOTOVO! Vytvořeno zápisů: {total_entries}")
    logger.info("═══════════════════════════════════════════════════════")


if __name__ == "__main__":
    asyncio.run(main())
