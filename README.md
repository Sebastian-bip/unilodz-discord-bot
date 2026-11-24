# UniLodz Discord News Bot

Automatyczny scraper w Pythonie 3 (`requests` + `BeautifulSoup4`), który 3 razy dziennie (09:00, 13:00 i 18:00 CET) sprawdza aktualności i wydarzenia na dwóch stronach Uniwersytetu Łódzkiego i publikuje nowe wpisy na kanale Discord jako estetyczne **Discord Embedy**.

## Źródła danych
- **WFiIS UŁ** (pasek niebieski `#005CA9`): https://www.wfis.uni.lodz.pl/strefa-studenta/
- **Uniwersytet Łódzki** (pasek karmazynowy `#C8102E`): https://www.uni.lodz.pl/studiuje-w-unilodz

## Struktura projektu
- `scraper.py` – główny skrypt pobierający wpisy, sprawdzający `seen_posts.json` i wysyłający nowe powiadomienia na Discorda.
- `seen_posts.json` – lokalna baza wysłanych linków (zapobiega duplikatom).
- `requirements.txt` – zależności Pythona (`requests`, `beautifulsoup4`).
- `.github/workflows/scraper.yml` – harmonogram GitHub Actions (3 razy dziennie: 09:00, 13:00, 18:00 CET + ręczne uruchomienie `workflow_dispatch`).
- `.env.example` – wzór lokalnej konfiguracji zmiennych środowiskowych.

## Wdrożenie w chmurze za darmo (GitHub Actions)

1. **Utwórz prywatne repozytorium na GitHubie** (np. `unilodz-discord-bot`).
2. **Wyślij pliki z tego folderu do repozytorium:**
   ```bash
   git init
   git add .
   git commit -m "Initial commit: UniLodz Discord scraper"
   git branch -M main
   git remote add origin https://github.com/TWOJ_LOGIN/unilodz-discord-bot.git
   git push -u origin main
   ```
3. **Dodaj sekret z adresem Webhooka:**
   - Otwórz repozytorium na GitHubie → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**.
   - **Name:** `DISCORD_WEBHOOK_URL`
   - **Secret:** wklej swój adres URL Discord Webhooka (nie zapisuj go w kodzie ani w commitach!).
4. **Sprawdź uprawnienia zapisu dla GitHub Actions (dla aktualizacji `seen_posts.json`):**
   - **Settings** → **Actions** → **General** → sekcja **Workflow permissions** → upewnij się, że zaznaczone jest **Read and write permissions** (workflow ma też wpisane `permissions: contents: write`).
5. **Uruchom pierwsze sprawdzenie ręcznie:**
   - Wejdź w zakładkę **Actions** → **UniLodz Discord News Scraper** → **Run workflow**.
   - Masz do wyboru 3 tryby:
     - `normal` – wyśle bieżące wpisy na kanał i zapisze je w `seen_posts.json`.
     - `init` – zapisze bieżące wpisy jako już znane **bez wysyłania** na Discorda (przydatne, jeśli chcesz dostawać dopiero kolejne, przyszłe aktualności).
     - `dry-run` – testowy podgląd w logach bez wysyłania wiadomości i bez zmian w pliku.

## Uruchomienie lokalne (opcjonalnie)

1. Zainstaluj Pythona 3.10+ (np. ze strony https://www.python.org/downloads/ lub `winget install Python.Python.3.12`).
2. Utwórz plik `.env` na podstawie `.env.example` i wklej swój `DISCORD_WEBHOOK_URL` (plik `.env` jest w `.gitignore`, więc nie trafi do Gita).
3. Zainstaluj biblioteki i uruchom test:
   ```powershell
   pip install -r requirements.txt
   python scraper.py --dry-run
   ```
4. Aby wysłać realne powiadomienia:
   ```powershell
   python scraper.py
   ```
