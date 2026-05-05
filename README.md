# 🏗️ Stavební Deník Bot – domeum.app

Automatické vytváření zápisů do stavebního deníku na [domeum.app](https://domeum.app).

Každý večer bot:
1. **Projde Google Drive** – najde nové fotky v podsložkách
2. **Přečte EXIF datum** každé fotky
3. **Vygeneruje AI zápis** (Google Gemini) dle zásad stavebního deníku
4. **Vloží záznam** do domeum.app s fotkami a správným datem
5. **Zapamatuje si**, které fotky již zpracoval (žádné duplikáty)

---

## 📋 Přehled architektury

```
Google Drive (složky s fotkami)
    ↓
GitHub Actions (cron, každý večer 22:00 CEST)
    ↓
Python Bot
    ├── Google Drive API → stáhne nové fotky
    ├── Gemini 1.5 Flash → AI analýza fotek → text zápisu
    └── Playwright → přihlásí se na domeum.app → vytvoří záznam
    ↓
data/processed_photos.json (paměť bota – co již bylo zpracováno)
```

---

## 🚀 Nastavení krok za krokem

### KROK 1 – GitHub Repository

1. Jděte na [github.com](https://github.com) → **New repository**
2. Název: `stavebni-denik-bot` (nebo libovolný)
3. **Private** ✅ (důležité – obsahuje citlivé nastavení)
4. Zkopírujte všechny soubory z tohoto projektu do nového repozitáře

### KROK 2 – Google Cloud projekt

> Pokud již máte Google Workspace, přihlaste se firemním účtem.

1. Jděte na [console.cloud.google.com](https://console.cloud.google.com)
2. Vytvořte nový projekt: **Stavební Deník Bot**
3. **Aktivujte API:**
   - Vyhledejte „Google Drive API" → **Enable**
   - Vyhledejte „Generative Language API" → **Enable** (pro Gemini)

### KROK 3 – Service Account (pro Google Drive)

1. V Google Cloud Console: **IAM & Admin** → **Service Accounts**
2. Klikněte **Create Service Account**
   - Název: `stavebni-denik-bot`
   - Popis: `Bot pro automatické zápisy stavebního deníku`
3. Klikněte **Create and Continue** → **Done**
4. Klikněte na nový service account → záložka **Keys**
5. **Add Key** → **Create new key** → **JSON**
6. Stáhne se soubor `xxx.json` – **USCHOVEJTE HO BEZPEČNĚ!**

### KROK 4 – Sdílení Google Drive složky

1. Otevřete stažený JSON soubor a zkopírujte hodnotu `client_email`
   - Vypadá takto: `stavebni-denik-bot@projekt.iam.gserviceaccount.com`
2. Jděte na [drive.google.com](https://drive.google.com)
3. Klikněte pravým tlačítkem na **hlavní složku s fotkami**
4. **Sdílet** → vložte zkopírovaný email → role **Prohlížeč** → **Hotovo**

> ⚠️ Struktura složek musí být:
> ```
> 📁 Hlavní složka (sdílena se service account)
>   📁 Betonáž základů          ← název = název akce v deníku
>   📁 Zdění 1NP
>   📁 Střecha
>       📷 IMG_001.jpg
>       📷 IMG_002.heic
> ```

### KROK 5 – Gemini API klíč

1. Jděte na [aistudio.google.com](https://aistudio.google.com)
2. Klikněte **Get API key** → **Create API key**
3. Vyberte svůj Google Cloud projekt
4. **Zkopírujte API klíč** – uložíte ho jako GitHub Secret

> ✅ Gemini 1.5 Flash je **zdarma** do 15 dotazů/minutu a 1500/den – pro tento use case více než dostačující.

### KROK 6 – domeum.app – přidání AI účtu do projektu

1. Přihlaste se na [domeum.app](https://domeum.app) jako **Ondrej Ceh**
2. Otevřete projekt **RD Cehovi**
3. V levém menu: **Členové** → **Přidat člena**
4. Zadejte: `ai.asistent@cehouse.cz`
5. Role: **Editor** (musí moci vytvářet záznamy)

> ⚠️ Ujistěte se, že `ai.klient@cehouse.cz` může přijmout pozvánku a přihlásit se.

### KROK 7 – Najít ID složky v URL

ID složky je v URL Google Drive:
```
https://drive.google.com/drive/folders/1fOX2D-kf_9Kj3ZFgE_ICKfT1jmn0Jl4K
                                        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
                                        toto je GOOGLE_DRIVE_FOLDER_ID
```

### KROK 8 – GitHub Secrets

1. V GitHub repozitáři: **Settings** → **Secrets and variables** → **Actions**
2. Klikněte **New repository secret** a přidejte tyto hodnoty:

| Secret | Hodnota |
|--------|---------|
| `DOMEUM_EMAIL` | `ai.asistent@cehouse.cz` |
| `DOMEUM_PASSWORD` | *(heslo k ai.klient účtu)* |
| `DOMEUM_PROJECT_NAME` | `RD Cehovi` |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | *(celý obsah staženeho JSON souboru)* |
| `GEMINI_API_KEY` | *(API klíč z kroku 5)* |
| `GOOGLE_DRIVE_FOLDER_ID` | `1fOX2D-kf_9Kj3ZFgE_ICKfT1jmn0Jl4K` |

> ⚠️ Pro `GOOGLE_SERVICE_ACCOUNT_JSON` – zkopírujte **celý obsah** JSON souboru (vše od `{` do `}`).

---

## ✅ Testování před prvním spuštěním

Po nastavení secrets spusťte **manuálně**:

1. GitHub → záložka **Actions**
2. Vlevo: **🏗️ Stavební deník – Automatický zápis**
3. **Run workflow** → zaškrtněte **Dry run (pouze výpis)** → **Run workflow**

Tím ověříte, že vše funguje, aniž by se skutečně vytvářely záznamy.

Pokud vše proběhne bez chyb, příště spusťte **bez dry run** nebo počkejte na automatické spuštění večer.

---

## 📅 Automatické spouštění

Bot se spouští **každý den ve 22:00 CEST** (léto) / **21:00 CET** (zima).

Spuštění lze sledovat: GitHub → **Actions** → výběr běhu

---

## 🔍 Schvalování zápisů

Záznamy jsou vytvořeny s příznakem, že je nutné je zkontrolovat.

Po vytvoření záznamu:
1. Přihlaste se na [domeum.app](https://domeum.app) jako **Ondrej Ceh**
2. Otevřete Stavební deník
3. Zkontrolujte nový záznam – přidejte/opravte:
   - Přesný počet pracovníků
   - Dodané materiály s množstvím
   - Klimatické podmínky
   - Případné mimořádné události
4. **Schválte záznam** ✅

---

## ⚠️ Bezpečnost

- **NIKDY** neukládejte hesla přímo do kódu ani do repozitáře
- Repozitář musí být **Private**
- Heslo `inflames1` bylo sdíleno v chatu – **doporučujeme ho okamžitě změnit**
- Rotujte API klíče alespoň jednou ročně

---

## 🐛 Řešení problémů

**Bot se nepřihlásí na domeum.app:**
- Zkontrolujte, zda `ai.klient@cehouse.cz` má přístup k projektu
- Podívejte se na debug screenshot v záložce Actions → Artifacts

**Žádné složky na Google Drive:**
- Zkontrolujte, zda je složka sdílena se service account emailem
- Email service accountu najdete v záložce Actions → krok "Run diary update"

**Gemini nefunguje:**
- Ověřte, že je aktivován "Generative Language API" v Google Cloud
- Zkontrolujte API klíč v aistudio.google.com

---

## 📁 Struktura projektu

```
stavebni-denik-bot/
├── .github/workflows/
│   └── diary-update.yml     # GitHub Actions cron job
├── src/
│   ├── main.py              # Hlavní orchestrátor
│   ├── google_drive_client.py  # Google Drive integrace
│   ├── domeum_client.py     # Playwright automation domeum.app
│   ├── ai_analyzer.py       # Gemini AI analýza fotek
│   └── state_manager.py     # Správa stavu (deduplikace)
├── data/
│   └── processed_photos.json  # Paměť bota
├── scripts/
│   └── test_setup.py        # Ověření nastavení
├── requirements.txt
└── README.md
```
