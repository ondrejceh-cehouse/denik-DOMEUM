"""
main.py – Hlavní orchestrátor stavebního deníku
Podporuje VÍCE projektů najednou:
  - Přihlásí se na domeum.app
  - Zjistí seznam všech projektů
  - Pro každý projekt hledá složku na Google Drive se SHODNÝM názvem
  - Zpracuje nové fotky a vytvoří záznamy v příslušném stavebním deníku
"""

import asyncio
import logging
import os
import sys
import tempfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path

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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s – %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("main")

MAIN_FOLDER_ID = os.environ["GOOGLE_DRIVE_FOLDER_ID"]
DRY_RUN        = os.environ.get("DRY_RUN", "false").lower() == "true"
MAX_PHOTOS_PER_DAY = 30


def match_projects_to_folders(domeum_projects, drive_folders):
    """
    Spáruje domeum projekty s Google Drive složkami podle shodného názvu.
    Porovnání je case-insensitive.
    """
    matches = []
    drive_map = {f["name"].strip().lower(): f for f in drive_folders}

    for project in domeum_projects:
        project_name = project["name"].strip()
        key = project_name.lower()

        if key in drive_map:
            folder = drive_map[key]
            matches.append({
                "project_name": project_name,
                "folder_id":    folder["id"],
                "folder_name":  folder["name"],
            })
            logger.info(f"  ✅ Spárováno: '{project_name}' ↔ Drive složka '{folder['name']}'")
        else:
            logger.info(f"  ⏭️  Bez párování: '{project_name}' – složka na Drive nenalezena")

    return matches


async def process_project_folder(drive_service, gemini_model, domeum, project_name, folder_id, state, temp_dir):
    """Zpracuje fotky z Google Drive a zapíše do stavebního deníku projektu."""
    logger.info("")
    logger.info("═══════════════════════════════════════════════════")
    logger.info(f"🏗️  PROJEKT: {project_name}")
    logger.info("═══════════════════════════════════════════════════")

    if not await domeum.select_project_by_name(project_name):
        logger.error(f"  Nelze přepnout na projekt '{project_name}', přeskakuji.")
        return 0

    if not await domeum.navigate_to_diary():
        logger.error(f"  Stavební deník nenalezen, přeskakuji.")
        return 0

    all_photos = get_photos_in_folder(drive_service, folder_id)
    if not all_photos:
        logger.info("  Žádné fotky v složce.")
        return 0

    new_photos = [p for p in all_photos if not is_photo_processed(state, folder_id, p["id"])]
    logger.info(f"  Celkem fotek: {len(all_photos)} | Nových: {len(new_photos)}")

    if not new_photos:
        logger.info("  Žádné nové fotky, přeskakuji.")
        return 0

    photos_by_date = defaultdict(list)
    for photo in new_photos:
        dest = os.path.join(temp_dir, f"{photo['id']}_{photo['name']}")
        try:
            # Primární zdroj data: imageMediaMetadata.time (Drive parsuje EXIF za nás)
            # Formát: "YYYY:MM:DD HH:MM:SS"
            meta_time = (photo.get("imageMediaMetadata") or {}).get("time", "")
            if meta_time and len(meta_time) >= 10:
                try:
                    dt = datetime.strptime(meta_time[:19], "%Y:%m:%d %H:%M:%S")
                    fallback = dt.strftime("%Y-%m-%d")
                except ValueError:
                    fallback = photo.get("createdTime", "")[:10]
            else:
                fallback = photo.get("createdTime", "")[:10]

            local_path = download_photo(drive_service, photo["id"], photo["mimeType"], dest)
            date = get_photo_date(local_path, fallback)
            logger.debug(f"  Fotka {photo['name']}: meta_time={meta_time!r} → datum={date}")
            photos_by_date[date].append({"id": photo["id"], "name": photo["name"], "path": local_path, "date": date})
        except Exception as e:
            logger.warning(f"  ✗ {photo['name']}: {e}")

    entries_created = 0

    for date in sorted(photos_by_date.keys()):
        date_photos = photos_by_date[date]
        czech_date  = format_date_czech(date)
        logger.info(f"\n  📅 {czech_date} ({len(date_photos)} fotek)")

        if has_diary_entry(state, folder_id, date):
            logger.info(f"  ℹ️  Zápis již existuje, označuji fotky.")
            for p in date_photos:
                mark_photo_processed(state, folder_id, p["id"], date)
            continue

        if len(date_photos) > MAX_PHOTOS_PER_DAY:
            date_photos = date_photos[:MAX_PHOTOS_PER_DAY]

        photo_paths = [p["path"] for p in date_photos]

        logger.info(f"  🤖 Generuji AI zápis...")
        diary_text = analyze_photos_for_diary(gemini_model, photo_paths, project_name, czech_date)

        logger.info(f"  📝 Vytvářím záznam v domeum.app...")
        success = await domeum.create_diary_entry(text=diary_text, date=date, photo_paths=photo_paths)

        if success:
            for p in date_photos:
                mark_photo_processed(state, folder_id, p["id"], date)
            mark_diary_entry(state, folder_id, date)
            entries_created += 1
            logger.info(f"  ✅ Záznam vytvořen: {czech_date}")
        else:
            logger.error(f"  ❌ Zápis selhal: {czech_date}")

        await asyncio.sleep(3)

    return entries_created


async def main():
    logger.info("═══════════════════════════════════════════════════════════")
    logger.info("🏗️  STAVEBNÍ DENÍK BOT – multi-project mode")
    logger.info(f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if DRY_RUN:
        logger.info("🔍 DRY RUN – záznamy se nevytvářejí!")
    logger.info("═══════════════════════════════════════════════════════════")

    state = load_state()
    stats = get_stats(state)
    logger.info(f"📊 Stav: {stats['total_photos_processed']} fotek, {stats['total_diary_entries']} zápisů")

    logger.info("\n🔧 Inicializace...")
    try:
        drive_service = get_drive_service()
        gemini_model  = init_gemini()
    except Exception as e:
        logger.critical(f"Inicializace selhala: {e}")
        sys.exit(1)

    logger.info(f"\n📂 Google Drive složky...")
    drive_folders = get_subfolders(drive_service, MAIN_FOLDER_ID)

    if not drive_folders:
        logger.warning("Žádné složky na Google Drive! Zkontrolujte sdílení a FOLDER_ID.")
        sys.exit(0)

    logger.info(f"Drive složky: {[f['name'] for f in drive_folders]}")

    total_entries = 0

    with tempfile.TemporaryDirectory() as temp_dir:
        async with DomeumClient() as domeum:

            logger.info(f"\n🔐 Přihlašuji se na domeum.app...")
            if not await domeum.login():
                logger.critical("Přihlášení selhalo!")
                sys.exit(1)

            # Zjistit všechny projekty z domeum.app
            logger.info("\n📋 Načítám projekty z domeum.app...")
            domeum_projects = await domeum.get_all_projects()

            if not domeum_projects:
                # Fallback na DOMEUM_PROJECT_NAME pokud je nastaveno
                project_name = os.environ.get("DOMEUM_PROJECT_NAME", "")
                if project_name:
                    logger.warning(f"Používám fallback projekt: {project_name}")
                    domeum_projects = [{"name": project_name}]
                else:
                    logger.critical("Nelze zjistit projekty a DOMEUM_PROJECT_NAME není nastaveno.")
                    sys.exit(1)

            logger.info(f"Projekty v domeum: {[p['name'] for p in domeum_projects]}")

            # Spárování s Drive složkami
            logger.info("\n🔗 Páruji projekty s Google Drive složkami...")
            pairs = match_projects_to_folders(domeum_projects, drive_folders)

            if not pairs:
                logger.warning(
                    "Žádné párování!\n"
                    f"  Projekty domeum: {[p['name'] for p in domeum_projects]}\n"
                    f"  Složky Drive:    {[f['name'] for f in drive_folders]}\n"
                    "  → Název složky na Drive musí být SHODNÝ s názvem projektu v domeum.app"
                )
                sys.exit(0)

            logger.info(f"\n▶️  Zpracovávám {len(pairs)} projektů...")

            for pair in pairs:
                try:
                    count = await process_project_folder(
                        drive_service, gemini_model, domeum,
                        pair["project_name"], pair["folder_id"],
                        state, temp_dir
                    )
                    total_entries += count
                    save_state(state)
                except KeyboardInterrupt:
                    break
                except Exception as e:
                    logger.error(f"Chyba projektu '{pair['project_name']}': {e}", exc_info=True)
                    save_state(state)

    save_state(state)
    logger.info("")
    logger.info("═══════════════════════════════════════════════════════════")
    logger.info(f"✅ HOTOVO! Vytvořeno zápisů: {total_entries}")
    logger.info("═══════════════════════════════════════════════════════════")


if __name__ == "__main__":
    asyncio.run(main())
