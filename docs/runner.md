# Runner tygodnia i piątkowy alarm triage'u

Dwa skrypty w katalogu głównym repo, uruchamiane bez Claude Code:

| skrypt | kiedy | co robi |
|---|---|---|
| `metagame_week.bat` / `metagame_week.py` | poniedziałek | import tygodnia do DeckDB, triage, eksporty, weryfikacja z mtgo.com, materiały tygodnia w `weekly/<tydzień>/`, opcjonalnie build prezentacji i dokumentów |
| `triage_check.bat` / `triage_check.py` | piątek 18:00 | import bieżącego tygodnia do dziś, triage DeckDB, raport i powiadomienie Windows; niczego nie eksportuje |

Zasada obu: każdy błąd zatrzymuje przebieg z komunikatem `STOP [kod]: ...` i niezerowym kodem wyjścia. Nie ma
wartości zastępczych ani pomijania kroków. Runner niczego nie commituje i nie pisze do `docs/decisions.md`
(decyzje są ludzkie); każdy przebieg dopisuje jeden wiersz do `weekly/runs.csv`.

## Uruchomienie runnera

Podwójne kliknięcie `metagame_week.bat`: etap DATA dla ostatniego zakończonego tygodnia ISO (w niedzielę bieżący
tydzień nie jest zakończony, więc to tydzień poprzedni). Okno zostaje otwarte (`pause`), na końcu jest kod wyjścia i
ścieżka raportu. Z wiersza poleceń (argumenty `.bat` przechodzą dalej):

```
metagame_week.bat --week 2026-W44
metagame_week.bat --week 2026-W44 --stage build
metagame_week.bat --week 2026-W44 --stage all --force
E:\github\.venv\Scripts\python.exe metagame_week.py --week 2026-W43 --test-monthly --force
```

| argument | znaczenie |
|---|---|
| `--week RRRR-Wnn` | tydzień ISO; domyślnie ostatni zakończony; tydzień niezakończony = stop |
| `--stage data\|build\|all` | domyślnie `data`; `all` = DATA, potem BUILD |
| `--force` | zastępuje materiały istniejącego folderu tygodnia (stare pliki znikają dopiero po udanym pipeline'ie); bez niego folder z materiałami = stop. Pliki treści (`deck_w<N>.json` itd.) i `triage_check_*.md` nie liczą się jako materiały i nigdy nie są usuwane |
| `--test-monthly` | testowy materiał miesięczny za bieżący, niepełny miesiąc tygodnia (`incomplete month`, dawne `--presentation-month`); w tygodniu zamykającym miesiąc = stop, bo ma już prawdziwy materiał miesięczny |

Ścieżki są w `docs/runner_config.csv` (`deckdb_dir`, `deckdb_python`, `pipeline_python`, `trend_weeks` = 12).

## Etap DATA

1. **Preflight.** Python i pakiety pipeline'u, interpreter DeckDB z `yaml` i bazą, sieć (`mtgo.com/decklists`),
   `git status` obu repo (zmienione pliki = ostrzeżenie w raporcie), commity obu repo, wersja reguł DeckDB.
   Niezacommitowane zmiany w `docs/deckdb_config.csv` = stop: folder tygodnia nie może powstać na konfiguracji spoza
   historii.
2. **Pobranie i import** (DeckDB, jak wpisy 306 i 312): `backfill-raw` od poniedziałku do niedzieli tygodnia,
   `import --raw-window`, `recompute-spell-colours`, `recompute-sb-splash`, `recompute-packages`. Każdy nieudany
   event z raportu `backfill-raw` = stop z listą ID, także gdy `backfill-raw` zakończył się kodem 0. Każde
   odrzucenie albo odmowa zapisu w `import` = stop. Eventy już zapisane nie są pobierane drugi raz; import nigdy
   nie nadpisuje istniejących wierszy.
3. **Triage**: `scripts/weekly_triage.py` (bez `--record`). `pending_blocking` = stop z liczbą grup, ich udziałem w
   listach tygodnia i listą grup. `pending_notice` przechodzi (oznaczenie w `notes.md`).
4. **Eksport**: `export-assignments` i `export-cards` do `DeckDB/data/exports/` pod nazwami
   `<rodzaj>_<reguły>_<ostatni Challenge>.csv`. Plik o tej nazwie już istnieje: użyty, jeśli ma ten sam sha256 co
   nowy eksport; inny sha256 = stop. Kontrola jednego `data_version` w obu plikach i w pliku triage'u.
5. **Weryfikacja mtgo.com**: strony miesięczne `mtgo.com/decklists/<RRRR>/<MM>` dla okna (przy przełomie miesiąca
   obie, a także miesiąc dnia przed i po oknie), zapisane jako `mtgo_snapshot_<RRRR-MM>.html`. Wszystkie Modern
   Challenge (ID, data, rozmiar) porównane z eksportem w obie strony; sąsiedzi (ostatni przed oknem, pierwszy po
   oknie) w raporcie. Ograniczenie: DeckDB importuje z tych samych stron mtgo.com, więc kontrola wyłapuje braki
   importu i eksportu, nie braki samego mtgo.com. Data różna o 1 dzień = ostrzeżenie z tygodniem, do którego event
   trafił; o więcej = stop. Obcięta strona (mtgo.com czasem oddaje stronę bez linków) jest pobierana ponownie po
   15, 30 i 60 s.
6. **Pipeline**: `src/metagame_input_generator.py --input-mode deckdb --as-of <niedziela> --trend-weeks 12` z kopią
   roboczą configu (`docs/deckdb_config.csv` z ostatniego commitu + ścieżki eksportów tygodnia) i własnym
   `--outputs-base` w `outputs/_working/runner/<tydzień>/pipeline/`. Materiał miesięczny według reguły `month_for`
   (miesiąc M w tygodniu ISO z 1. dniem M+1). `outputs/presentation/` (archiwum opublikowanych folderów) nie jest
   ruszany.
7. **Folder** `weekly/<tydzień>/` (płaski): `data.xlsx`, `notes.md`, PNG, `mtgo_snapshot_*.html`, `run_report.md`.

`run_report.md`: tydzień i okno, commity obu repo i configu, wersja reguł, sha256 eksportów, eventy i listy,
tabela weryfikacji (ID / plik / mtgo.com / daty / rozmiar), sąsiedzi, triage, materiał miesięczny, ostrzeżenia,
czasy kroków, komenda pipeline'u. Przy stopie raport jest w `outputs/_working/runner/<tydzień>/run_report_STOP.md`
(folder tygodnia nie powstaje albo zostaje bez zmian). Logi komend: `outputs/_working/runner/<tydzień>/logs/`.

## Etap BUILD

Warunki: w `build/` są `analysis.py`, `build_deck.js`, `gen_dossier.py`, `build_docs.py`, `qa.py`,
`README_workflow.md`; w `weekly/<tydzień>/` są materiały etapu DATA i pliki treści `deck_w<N>.json`,
`dossier_w<N>.src.md`, `reddit_w<N>.md`, `youtube_w<N>.md`, `x_w<N>.md` (N = numer tygodnia bez zera, np. 43).
Brak któregoś = stop z listą.

Przebieg jak w `build/README_workflow.md`, z `weekly/<tydzień>/out/` jako katalogiem roboczym README (tworzony od
nowa przy każdym buildzie: `src/data.xlsx`, `src/notes.md`, `notes.md`, PNG i pliki treści jako kopie, obok
`stats.json`, `dossier_w<N>.md`, `modern_metagame_w<N>.pptx` i cztery `.docx`):

```
analysis.py --week N --src src --out .
node build_deck.js N                      (NODE_PATH = npm root -g)
gen_dossier.py N
build_docs.py --week N --src . --png . --out .
qa.py --week N --events <Events z notes.md> --src . --dir .
```

Kod wyjścia różny od 0 albo linia `FAIL` w `qa.py` = stop (kod 10); linie `UWAGA` idą do raportu do ręcznej
oceny. Skrypty Pythona działają w trybie UTF-8 (`PYTHONUTF8=1`), bo otwierają pliki bez podania kodowania, a
domyślne kodowanie Windows (cp1250) nie zna np. strzałki. Sekcja BUILD jest dopisywana do `run_report.md`, z sha256
każdego pliku `build/` do porównania z wersją w projekcie Claude.

Wymagania jednorazowe:

```
npm install -g pptxgenjs
E:\github\.venv\Scripts\python.exe -m pip install python-docx python-pptx pillow defusedxml lxml
```

`qa.py` wywołuje walidator pptx ścieżką `/mnt/skills/public/pptx/scripts/office/validate.py` (środowisko projektu
Claude). Na Windows ta ścieżka oznacza `E:\mnt\...` (dysk katalogu roboczego). Walidator to skrypt skilla pptx
Claude, zsynchronizowany lokalnie; jednorazowo (PowerShell):

```
New-Item -ItemType Directory -Force E:\mnt\skills\public
New-Item -ItemType Junction -Path E:\mnt\skills\public\pptx -Target "<katalog skilla pptx>"
```

(`<katalog skilla pptx>` = folder `pptx` w `C:\Users\kmalo\.claude\skills\synced\...`; usunięcie: `rmdir
E:\mnt\skills\public\pptx`). Bez walidatora etap zatrzymuje się w preflighcie. Junction jest tymczasowy: gdy przyjdzie wersja `qa.py` z parametrem ścieżki walidatora (`--validator` albo zmienna `PPTX_VALIDATE`), runner poda jej ścieżkę lokalnego skilla pptx, a junction zostanie usunięty. `markitdown` jest opcjonalny
(`qa.py` czyta wtedy tekst przez `python-pptx`).

**Podmiana skryptów w `build/`:** skopiuj nowe pliki z projektu Claude do `build/` (nadpisanie), uruchom
`metagame_week.bat --week <tydzień> --stage build` i porównaj sha256 w sekcji „Skrypty build/” raportu z wersją w
projekcie. Claude Code nie edytuje plików w `build/`; zmiany logiki przychodzą z projektu Claude.

## Komunikaty błędów

| kod | etap | przykład komunikatu | co zrobić |
|---|---|---|---|
| 1 | dowolny | `nieoczekiwany błąd` + traceback | błąd kodu runnera: zgłoś z `run_report_STOP.md` |
| 2 | argumenty | `nie jest tygodniem ISO`, `nie jest zakończony`, `już zawiera materiały ... użyj --force`, `--test-monthly: tydzień ... zawiera` | popraw `--week`; `--force`, jeśli folder ma być zastąpiony |
| 3 | preflight | `brak pakietu`, `brak ... (docs/runner_config.csv)`, `brak dostępu do sieci`, `docs/deckdb_config.csv ma niezacommitowane zmiany` | doinstaluj pakiet / popraw ścieżkę / sprawdź sieć / zacommituj albo cofnij config |
| 4 | import | `backfill-raw: nieudane pobrania` z listą ID; `import: odrzucone eventy lub odmowy zapisu` | pobrania: uruchom ponownie później (mtgo.com bywa niedostępne); odmowy: rozstrzygnij w DeckDB (`data/quarantine`), runner nie nadpisuje |
| 5 | triage | `pending_blocking, N grup(a) ... X% z M list` z listą grup | rozstrzygnij grupy w aplikacji przeglądu DeckDB, zastosuj decyzje (cykl `/tydzien`), uruchom runner ponownie |
| 6 | eksport | `już istnieje z inną zawartością`, `data_version różne`, `stan bazy zmienił się po triage'u` | porównaj pliki (`scripts/export_diff.py` w DeckDB), przenieś stary plik; przy zmianie stanu uruchom ponownie |
| 7 | mtgo.com | `brak w eksporcie: <ID>`, `brak na mtgo.com: <ID>`, `data różna o więcej niż 1 dzień`, `brak linków ... (zmiana struktury strony?)`, `po 4 próbach` | brak w eksporcie: sprawdź import (event mógł wystartować po listingu, uruchom ponownie); nadmiarowy: sprawdź event na mtgo.com; struktura strony: obejrzyj `mtgo_snapshot_*.html` w `outputs/_working/runner/<tydzień>/snapshots/` |
| 8 | pipeline | `pipeline (input mode deckdb): kod wyjścia 2` z końcem logu | błąd danych w pipeline'ie (np. `DeckdbInputError`): log w `logs/pipeline.log` |
| 9 | folder | `kopia ... różni się`, `ma podkatalogi` | błąd dysku albo zmiana pipeline'u: zgłoś |
| 10 | build | `brak skryptów buildu`, `brak plików treści`, `pptxgenjs niedostępny`, `brak ... validate.py`, `build/<skrypt>: kod wyjścia`, `qa.py: N FAIL` | wgraj brakujące pliki / wymagania jednorazowe wyżej; FAIL: popraw treść tygodnia |

## Piątkowy alarm triage'u

Cel: w piątek wiedzieć, czy w bieżącym tygodniu są nowe listy, których reguły DeckDB nie klasyfikują, i poprawić
reguły przed poniedziałkowym runnerem.

1. Import Modern Challenge od poniedziałku bieżącego tygodnia ISO do dziś (te same komendy i ta sama obsługa
   nieudanych pobrań co w runnerze).
2. `scripts/weekly_triage.py` (plik JSON w `outputs/_working/triage_check/<data>/`, nie w `data/exports/`).
3. Raport `weekly/<tydzień>/triage_check_<data>.md`: status; grupy do rozstrzygnięcia (listy, listy w tygodniu, udział
   w listach tygodnia, 2 przykłady `event_id / decktournamentid`, karty wspólne grupy); udział „Rogue &
   unclassified” w tygodniu do dziś i zmiana wobec poprzedniego pełnego tygodnia; flaga przy udziale > 5% albo
   wzroście > 2 pp. Progi pochodzą z master promptu analizy metagame (projekt Claude), część 2, test 8.
4. Powiadomienie Windows: `triage complete, nothing to do` albo `triage <status>: <n> group(s) awaiting decision`;
   przy błędzie `triage check error (code 3): ...`.
5. Kod wyjścia: 0 `complete`, 1 `pending_notice`, 2 `pending_blocking`, 3 błąd (także brak sieci i każdy
   nieoczekiwany wyjątek). Nic nie jest eksportowane; reguły i decyzje bez zmian.

`--as-of RRRR-MM-DD` (przebieg kontrolny na dzień z przeszłości): triage liczy na kopii bazy bez eventów po tym dniu
(decyzje i okna triage'u w stanie bieżącym). `--no-toast`: bez powiadomienia.

### Harmonogram zadań Windows (piątek 18:00)

Gotowy plik: `docs/triage_check_task.xml` (zadanie „MTG triage check”: co tydzień w piątek o 18:00, tylko przy
zalogowanym użytkowniku, bo powiadomienie potrzebuje sesji; `E:\github\.venv\Scripts\python.exe triage_check.py` w
katalogu repo, bez `pause`; zadanie pominięte, gdy komputer był wyłączony, rusza po włączeniu). Zadanie nie jest
zakładane automatycznie. Import:

1. Harmonogram zadań (`taskschd.msc`) → Akcja → Importuj zadanie… → `docs\triage_check_task.xml` → OK.
2. albo w wierszu poleceń: `schtasks /Create /TN "MTG triage check" /XML docs\triage_check_task.xml`

Ostatni wynik zadania (kolumna „Wynik ostatniego uruchomienia”) to kod wyjścia: 0x0 complete, 0x1 pending_notice,
0x2 pending_blocking, 0x3 błąd. Usunięcie: `schtasks /Delete /TN "MTG triage check"`.

## Folder wyjściowy i pliki robocze

- `weekly/<tydzień>/`: materiały tygodnia (poza gitem, `.gitignore`), pliki treści, `out/` z buildu,
  `triage_check_*.md`;
- `weekly/runs.csv`: data, tydzień, etap, status (`OK` / `STOP <kod>`), sha256 eksportu, wersja reguł, eventy, listy,
  status triage'u (stop z kodem 2 nie jest zapisywany);
- `outputs/_working/runner/<tydzień>/`: logi, raport `backfill-raw`, kopia configu, eksporty tymczasowe, snapshoty,
  wyjście pipeline'u;
- `outputs/_working/reference/<tydzień>/`: wzorzec do porównań (kontrola runnera),
  `scripts/compare_week_folders.py <folder A> <folder B>` porównuje dwa foldery (PNG, MD bajtowo, `data.xlsx` arkusz
  po arkuszu).

Testy syntetyczne: `E:\github\.venv\Scripts\python.exe -m pytest tests/test_weekly_runner.py`.
