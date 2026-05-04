"""
google_drive_client.py – Google Drive integrace
Prochází složky, stahuje fotky a čte EXIF metadata pro datum pořízení.
"""

import io
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from PIL import Image
from PIL.ExifTags import TAGS

try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
    HEIF_SUPPORTED = True
except ImportError:
    HEIF_SUPPORTED = False

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

# Podporované MIME typy obrázků
IMAGE_MIME_TYPES = [
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/heic",
    "image/heif",
    "image/webp",
]


def get_drive_service():
    """Vytvoří Google Drive service z Service Account JSON."""
    sa_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not sa_json:
        raise ValueError("Chybí environment variable GOOGLE_SERVICE_ACCOUNT_JSON")

    service_account_info = json.loads(sa_json)
    credentials = service_account.Credentials.from_service_account_info(
        service_account_info, scopes=SCOPES
    )
    service = build("drive", "v3", credentials=credentials)
    logger.info("Google Drive service inicializován")
    return service


def get_subfolders(service, parent_folder_id: str) -> List[Dict]:
    """Vrátí seznam podsložek v dané složce."""
    query = (
        f"'{parent_folder_id}' in parents "
        f"and mimeType='application/vnd.google-apps.folder' "
        f"and trashed=false"
    )
    results = service.files().list(
        q=query,
        fields="files(id, name)",
        pageSize=100,
        orderBy="name"
    ).execute()

    folders = results.get("files", [])
    logger.info(f"Nalezeno {len(folders)} podsložek v {parent_folder_id}")
    return folders


def get_photos_in_folder(service, folder_id: str) -> List[Dict]:
    """Vrátí seznam fotek v dané složce (bez rekurze do podsložek)."""
    mime_filter = " or ".join([f"mimeType='{m}'" for m in IMAGE_MIME_TYPES])
    query = f"'{folder_id}' in parents and ({mime_filter}) and trashed=false"

    all_photos = []
    page_token = None

    while True:
        params = {
            "q": query,
            "fields": "nextPageToken, files(id, name, createdTime, modifiedTime, mimeType, size)",
            "pageSize": 1000,
            "orderBy": "createdTime",
        }
        if page_token:
            params["pageToken"] = page_token

        results = service.files().list(**params).execute()
        all_photos.extend(results.get("files", []))

        page_token = results.get("nextPageToken")
        if not page_token:
            break

    logger.info(f"Nalezeno {len(all_photos)} fotek v složce {folder_id}")
    return all_photos


def download_photo(service, file_id: str, mime_type: str, dest_path: str) -> str:
    """
    Stáhne fotku z Google Drive.
    HEIC soubory převede na JPEG pokud pillow-heif není k dispozici.
    """
    # Pro HEIC bez pillow-heif: požádáme Drive o konverzi na JPEG
    if mime_type in ("image/heic", "image/heif") and not HEIF_SUPPORTED:
        logger.info(f"HEIC→JPEG konverze přes Drive API pro {file_id}")
        try:
            request = service.files().export_media(fileId=file_id, mimeType="image/jpeg")
            dest_path = str(Path(dest_path).with_suffix(".jpg"))
        except Exception:
            request = service.files().get_media(fileId=file_id)
    else:
        request = service.files().get_media(fileId=file_id)

    Path(dest_path).parent.mkdir(parents=True, exist_ok=True)

    with open(dest_path, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()

    logger.debug(f"Staženo: {dest_path}")
    return dest_path


def get_photo_date(image_path: str, fallback_date: Optional[str] = None) -> str:
    """
    Přečte datum pořízení z EXIF metadat.
    Pokud EXIF datum není dostupné, použije fallback_date nebo dnešní datum.
    Vrací datum ve formátu YYYY-MM-DD.
    """
    try:
        img = Image.open(image_path)

        # Pokus o EXIF
        exif_data = None
        if hasattr(img, "_getexif"):
            exif_data = img._getexif()
        elif hasattr(img, "getexif"):
            exif_data = img.getexif()

        if exif_data:
            for tag_id, value in exif_data.items():
                tag = TAGS.get(tag_id, tag_id)
                if tag in ("DateTimeOriginal", "DateTimeDigitized", "DateTime"):
                    if isinstance(value, str) and value.strip():
                        try:
                            dt = datetime.strptime(value.strip(), "%Y:%m:%d %H:%M:%S")
                            return dt.strftime("%Y-%m-%d")
                        except ValueError:
                            continue

        logger.debug(f"EXIF datum nenalezeno v {image_path}, použiji fallback")

    except Exception as e:
        logger.warning(f"Chyba při čtení EXIF z {image_path}: {e}")

    # Fallback: datum z Google Drive metadat
    if fallback_date and len(fallback_date) >= 10:
        return fallback_date[:10]

    # Poslední záchrana: dnešní datum
    return datetime.now().strftime("%Y-%m-%d")


def format_date_czech(date_str: str) -> str:
    """Převede YYYY-MM-DD na český formát DD.MM.YYYY."""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.strftime("%d.%m.%Y")
    except ValueError:
        return date_str
