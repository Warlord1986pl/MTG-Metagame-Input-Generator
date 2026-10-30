# Kopia zapasowa danych, które istnieją tylko na VPS (plan, bez wdrożenia)

Stan na 2026-10-30. **Wdrożony wariant A** (2026-10-30, po sprawdzeniu kolumn: migawki to `LoginID, Rank, Points,
PremierPoints, Wins, Top2, Top4, Top8, Top16, Starts`, `season_config.csv` to nazwy i daty sezonów; wszystko już
publikowane przez stronę). Dotyczy repo kolektora `Meta-Challange-2.0_Mikrus` (serwer VPS mikrus, cron roota co godzinę o :15:
`history-sync`, `league-rebuild`, `push`). Ten dokument jest tylko planem; nic nie zostało zmienione ani na serwerze,
ani w repo kolektora.

## Co jest zagrożone

| plik na VPS (`/root/MTG-Metagame-Input-Generator/`) | stan w gicie | dlaczego nie do odtworzenia |
|---|---|---|
| `outputs/league/snapshots/pilot_league_<Sezon>_w<NN>.csv` (dziś w36 .. w44 dla lata i jesieni) | nieśledzone | migawka tabeli z danego tygodnia; z niej liczy się `RankChange` na stronie ligi (`build_season_table(..., snapshot_dir)`); `.gitignore` (od 2026-09-01, `df7dc39`) mówi wprost: „irreplaceable point-in-time capture” |
| `outputs/league/season_config.csv` | śledzony, lokalnie zmieniony (dopisana jesień 2026) | rejestr sezonów; bez wiersza jesieni czysty klon nie przebuduje jesieni, dopóki `run_league_update` jej nie dopisze |

Przyczyna: `cmd_push` dodaje do commitu tylko ścieżki z `ALLOWED_PUSH_GLOBS` (`collector/mikrus_collector.py`), a ta
lista nie ma `outputs/league/snapshots/*.csv` ani `outputs/league/season_config.csv`. Skutek uboczny (2026-10-30): każdy
commit w `main`, który dotyka tych ścieżek, zatrzymuje `git pull -q` na VPS i cały łańcuch crona.

## Rekomendacja: wariant A (migawki i rejestr sezonów w repo pipeline'u, przez istniejący `push`)

Najmniejsza zmiana, zgodna z zamiarem `.gitignore` i bez nowej infrastruktury.

1. W `collector/mikrus_collector.py` dopisać do `ALLOWED_PUSH_GLOBS`:
   `"outputs/league/snapshots/*.csv"` i `"outputs/league/season_config.csv"`.
2. Kolejność wdrożenia (ważna, inaczej `git pull` na komputerze lokalnym odmówi):
   - lokalnie (`E:\github\MTG-Metagame-Input-Generator`) przenieść nieśledzone migawki `outputs/league/snapshots/`
     (dziś 8 plików liczonych lokalnie, inne niż na VPS) do `outputs/_working/snapshots_local_<data>/`;
   - wdrożyć zmianę kolektora (commit w jego repo; VPS zrobi `git pull` sam o :15);
   - pierwszy `push` po zmianie zacommituje wszystkie migawki VPS i `season_config.csv` do `main`
     (commit „mikrus: sync …”); potem lokalnie zwykły `git pull`.
3. Sprawdzenie: na VPS `git status --short` bez `?? outputs/league/snapshots/` i bez ` M outputs/league/season_config.csv`;
   na GitHubie w `main` pliki `outputs/league/snapshots/*_w44.csv`; w logu kolektora brak błędu `[push]`.
4. Wycofanie: usunąć dwa wpisy z `ALLOWED_PUSH_GLOBS`; zacommitowane pliki zostają w historii (to jest właśnie kopia).

Koszt: jedna migawka na tydzień i sezon, każda kilka–kilkadziesiąt KB (lokalne w37/w38: 8 i 13 KB, rośnie z liczbą
pilotów), czyli najwyżej kilka MB na sezon w historii gita.
Uwaga prywatność: migawki mają te same kolumny co `outputs/league/pilot_league_*.csv` (nazwa pilota, LoginID), które
już są publiczne; wariant A nie dodaje nowego rodzaju danych do publicznego repo, ale dodaje kolejne kopie. Jeśli
decyzja z przeglądu prywatności będzie „mniej identyfikatorów publicznie”, wybrać wariant B.

## Wariant B (prywatne repo kopii, niezależne od publicznego)

1. Prywatne repo `Warlord1986pl/mtg-league-backup` z kluczem wdrożeniowym tylko do zapisu tego repo (deploy key na VPS,
   `~/.ssh/league_backup`, wpis `Host` w `~/.ssh/config`).
2. Nowa komenda kolektora `backup` (albo skrypt `collector/backup_snapshots.sh`), wywoływana raz dziennie z crona
   (np. `30 3 * * *`, poza oknem `:15`, z tym samym `flock`):
   kopia `outputs/league/snapshots/`, `outputs/league/season_config.csv` i `state/watermark.json` do
   `<repo kopii>/<RRRR-MM-DD>/`, `git add`, commit tylko przy zmianie (`git diff --cached --quiet`), `git push`.
3. Błąd kopii nie może zatrzymać synchronizacji: osobna linia crona, wynik w `run_status` (`backup`), alert z
   `collector/alert.py`, gdy ostatnia udana kopia jest starsza niż 48 h.
4. Odtworzenie po awarii: `git clone` repo kopii, skopiowanie najnowszego katalogu daty do
   `outputs/league/snapshots/` i `outputs/league/season_config.csv` na nowym serwerze przed pierwszym `league-rebuild`.

## Wariant C (archiwum z datą poza gitem)

Dzienne `tar czf league_<RRRR-MM-DD>.tar.gz outputs/league/snapshots outputs/league/season_config.csv` wysyłane tym
samym kanałem co `collector/cytrus_mirror.py` (FTP z `/root/.cytrus-ftp-env`), z retencją np. 60 dni. Najmniej
kodu w gicie, ale bez historii zmian i z hasłem FTP jako jedynym zabezpieczeniem; tylko jako druga kopia obok A albo B.

## Do decyzji

- A czy B (zależnie od decyzji o prywatności, punkt 4 raportu z 2026-10-30);
- czy C jako druga, niezależna kopia;
- retencja migawek zakończonych sezonów (lato 2026 jest zamknięte, jego migawki można zamrozić jednorazowo).
