"""
repair_photos.py – Jednorázová oprava záznamů stavebního deníku bez fotek.

Situace: prvních 6 záznamů (2026-05-21 .. 2026-07-01) bylo vytvořeno před
opravou race-condition v uploadu fotek. Záznamy mají text, ale chybí fotky.

Tento skript:
  1. Stáhne fotky z Google Drive pro každé chybějící datum
  2. Přejde na domeum.app a pro každý záznam otevře "Upravit"
  3. Nahraje fotky přes #document-upload-input (networkidle wait)
  4. Uloží změny přes "Zveřejnit"
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
    get_photos_in_folder_recursive,
    download_photo,
    get_photo_date,
)
from domeum_client import DomeumClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s – %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("repair")

# ─── Konfigurace opravy ───────────────────────────────────────────────────────

# Drive složka projektu "RD Cehovi"
REPAIR_FOLDER_ID = "1-6K5QzvLByremulU3sBpXPo7pfFobJOA"
REPAIR_PROJECT_NAME = "RD Cehovi"
MAX_PHOTOS_PER_DAY = 30

# Záznamy, které nemají fotky: datum záznamu → UUID záznamu na domeum.app
RECORDS_TO_REPAIR = [
    {"date": "2026-07-01", "uuid": "0fc9a2c9-0990-4db6-a654-63fbb26ec750"},
    {"date": "2026-06-16", "uuid": "189d1018-d33d-4377-b1db-1f9179ee5de0"},
    {"date": "2026-06-12", "uuid": "28c13dbc-fcc1-424a-87f5-de04663bea02"},
    {"date": "2026-05-31", "uuid": "358421d1-4c0b-4ee4-9c1a-f168bdaf8b98"},
    {"date": "2026-05-30", "uuid": "862762ef-f099-407a-8c6b-3bf13d18633a"},
    {"date": "2026-05-21", "uuid": "9156c956-5ec7-4288-8809-169cd6889050"},
]

# ─────────────────────────────────────────────────────────────────────────────


def get_date_from_photo(photo: dict, local_path: str) -> str:
    """Zjistí datum fotky stejnou logikou jako main.py."""
    folder_date = photo.get("folder_date")
    meta_time = (photo.get("imageMediaMetadata") or {}).get("time", "")

    if folder_date:
        fallback = folder_date
    elif meta_time and len(meta_time) >= 10:
        try:
            dt = datetime.strptime(meta_time[:19], "%Y:%m:%d %H:%M:%S")
            fallback = dt.strftime("%Y-%m-%d")
        except ValueError:
            fallback = photo.get("createdTime", "")[:10]
    else:
        fallback = photo.get("createdTime", "")[:10]

    return get_photo_date(local_path, fallback)


async def main():
    logger.info("═══════════════════════════════════════════════════")
    logger.info("🔧 OPRAVA FOTEK – stavební deník RD Cehovi")
    logger.info("═══════════════════════════════════════════════════")

    repair_dates = {r["date"] for r in RECORDS_TO_REPAIR}
    date_to_uuid = {r["date"]: r["uuid"] for r in RECORDS_TO_REPAIR}
    logger.info(f"Záznamy k opravě ({len(RECORDS_TO_REPAIR)}): {sorted(repair_dates)}")

    # ── Stáhni fotky z Drive ──────────────────────────────────────────────────
    logger.info("\n📥 Stahuji fotky z Google Drive…")
    drive_service = get_drive_service()

    all_photos = get_photos_in_folder_recursive(drive_service, REPAIR_FOLDER_ID)
    logger.info(f"Celkem fotek ve složce: {len(all_photos)}")

    with tempfile.TemporaryDirectory() as temp_dir:
        photos_by_date: dict[str, list[str]] = defaultdict(list)

        for photo in all_photos:
            dest = os.path.join(temp_dir, f"{photo['id']}_{photo['name']}")
            try:
                local_path = download_photo(drive_service, photo["id"], photo["mimeType"], dest)
                date = get_date_from_photo(photo, local_path)

                if date in repair_dates:
                    photos_by_date[date].append(local_path)
                    logger.debug(f"  + {photo['name']} → {date}")
            except Exception as e:
                logger.warning(f"  ✗ {photo['name']}: {e}")

        for d in sorted(repair_dates):
            n = len(photos_by_date.get(d, []))
            logger.info(f"  {d}: {n} fotek nalezeno")

        # ── Opravuj záznamy přes Playwright ──────────────────────────────────
        logger.info("\n🌐 Spouštím Playwright…")

        async with DomeumClient() as domeum:
            if not await domeum.login():
                logger.error("Přihlášení selhalo")
                sys.exit(1)

            if not await domeum.select_project_by_name(REPAIR_PROJECT_NAME):
                logger.error(f"Projekt '{REPAIR_PROJECT_NAME}' nenalezen")
                sys.exit(1)

            if not await domeum.navigate_to_diary():
                logger.error("Stavební deník nenalezen")
                sys.exit(1)

            repaired = 0
            failed = 0

            for record in RECORDS_TO_REPAIR:
                date = record["date"]
                uuid = record["uuid"]

                photo_paths = photos_by_date.get(date, [])
                if not photo_paths:
                    logger.warning(f"⚠️  {date}: žádné fotky – přeskakuji {uuid[:8]}")
                    failed += 1
                    continue

                if len(photo_paths) > MAX_PHOTOS_PER_DAY:
                    logger.info(f"  Omezuji na {MAX_PHOTOS_PER_DAY} fotek (bylo {len(photo_paths)})")
                    photo_paths = photo_paths[:MAX_PHOTOS_PER_DAY]

                logger.info(f"\n── {date} ({len(photo_paths)} fotek) ──")
                success = await domeum.repair_entry_photos(uuid, photo_paths)

                if success:
                    repaired += 1
                else:
                    failed += 1
                    logger.error(f"❌ Oprava selhala: {date} / {uuid}")

                # Krátká pauza mezi záznamy
                await asyncio.sleep(2)

        logger.info("\n═══════════════════════════════════════════════════")
        logger.info(f"Hotovo: {repaired} opraveno, {failed} selhalo")
        logger.info("═══════════════════════════════════════════════════")

        if failed > 0:
            sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
