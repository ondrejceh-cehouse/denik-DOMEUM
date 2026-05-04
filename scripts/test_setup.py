"""
test_setup.py – Ověření nastavení před prvním spuštěním
Spusťte lokálně: python scripts/test_setup.py
"""

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def check_env():
    """Zkontroluje přítomnost všech potřebných environment variables."""
    print("\n" + "═" * 50)
    print("1️⃣  KONTROLA ENVIRONMENT VARIABLES")
    print("═" * 50)

    required = {
        "DOMEUM_EMAIL":               "Email do domeum.app",
        "DOMEUM_PASSWORD":            "Heslo do domeum.app",
        "DOMEUM_PROJECT_NAME":        "Název projektu v domeum.app",
        "GOOGLE_SERVICE_ACCOUNT_JSON":"Google Service Account JSON",
        "GEMINI_API_KEY":             "Google Gemini API klíč",
        "GOOGLE_DRIVE_FOLDER_ID":     "ID hlavní Google Drive složky",
    }

    all_ok = True
    for key, description in required.items():
        value = os.environ.get(key)
        if value:
            preview = value[:20] + "..." if len(value) > 20 else value
            print(f"  ✅ {key:<35} = {preview}")
        else:
            print(f"  ❌ {key:<35} – CHYBÍ! ({description})")
            all_ok = False

    return all_ok


def check_google_drive():
    """Otestuje připojení k Google Drive a vypíše nalezené složky."""
    print("\n" + "═" * 50)
    print("2️⃣  TEST GOOGLE DRIVE PŘIPOJENÍ")
    print("═" * 50)

    try:
        from google_drive_client import get_drive_service, get_subfolders

        service = get_drive_service()
        folder_id = os.environ["GOOGLE_DRIVE_FOLDER_ID"]

        print(f"  Skenuji složku: {folder_id}")
        subfolders = get_subfolders(service, folder_id)

        if subfolders:
            print(f"  ✅ Nalezeno {len(subfolders)} podsložek:")
            for f in subfolders:
                print(f"     📁 {f['name']} (ID: {f['id']})")
        else:
            print("  ⚠️  Žádné podsložky nenalezeny!")
            print("     Zkontrolujte: je složka sdílena se service account emailem?")
            sa_info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
            print(f"     Service account email: {sa_info.get('client_email', 'N/A')}")

        return True
    except Exception as e:
        print(f"  ❌ CHYBA: {e}")
        return False


def check_gemini():
    """Otestuje připojení ke Gemini API."""
    print("\n" + "═" * 50)
    print("3️⃣  TEST GEMINI API")
    print("═" * 50)

    try:
        import google.generativeai as genai
        genai.configure(api_key=os.environ["GEMINI_API_KEY"])
        model = genai.GenerativeModel("gemini-1.5-flash")

        response = model.generate_content("Odpověz pouze: 'OK'")
        print(f"  ✅ Gemini API funguje. Odpověď: {response.text.strip()}")
        return True
    except Exception as e:
        print(f"  ❌ CHYBA: {e}")
        return False


async def check_domeum():
    """Otestuje přihlášení do domeum.app."""
    print("\n" + "═" * 50)
    print("4️⃣  TEST DOMEUM.APP PŘIHLÁŠENÍ")
    print("═" * 50)

    try:
        from domeum_client import DomeumClient

        async with DomeumClient() as domeum:
            login_ok = await domeum.login()
            if login_ok:
                print(f"  ✅ Přihlášení úspěšné jako {os.environ.get('DOMEUM_EMAIL')}")

                project_ok = await domeum.select_project()
                if project_ok:
                    print(f"  ✅ Projekt '{os.environ.get('DOMEUM_PROJECT_NAME')}' nalezen")

                    diary_ok = await domeum.navigate_to_diary()
                    if diary_ok:
                        print("  ✅ Stavební deník nalezen")
                        print("  ℹ️  Screenshot uložen: /tmp/domeum_debug_*.png")
                    else:
                        print("  ❌ Stavební deník nenalezen")
                else:
                    print(f"  ❌ Projekt nenalezen")
            else:
                print("  ❌ Přihlášení selhalo")

        return login_ok
    except Exception as e:
        print(f"  ❌ CHYBA: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    print("\n🔍 TEST NASTAVENÍ – Stavební Deník Bot")
    print("Tento skript ověří všechna připojení před prvním spuštěním.\n")

    results = {}

    results["env"]   = check_env()
    if results["env"]:
        results["drive"] = check_google_drive()
        results["ai"]    = check_gemini()
        results["web"]   = await check_domeum()
    else:
        print("\n❌ Nejdříve nastavte všechny environment variables!")

    print("\n" + "═" * 50)
    print("VÝSLEDEK")
    print("═" * 50)
    all_ok = all(results.values())
    for check, ok in results.items():
        icon = "✅" if ok else "❌"
        print(f"  {icon} {check.upper()}")

    if all_ok:
        print("\n🎉 Vše v pořádku! Bot je připraven ke spuštění.")
    else:
        print("\n⚠️  Některé testy selhaly. Zkontrolujte výše uvedené chyby.")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
