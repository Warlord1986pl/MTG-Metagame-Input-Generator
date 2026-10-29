# Runner tygodnia: rozpoznanie i plan (krok 0)

> Plan zaakceptowany 2026-10-29 ze zmianami: wzorzec kontroli 1 w `outputs/_working/reference/` (archiwum
> `outputs/presentation/2026-W43/` bez zmian), flaga `--test-monthly`, stop przy każdym `failures` z `backfill-raw`,
> config z ostatniego commitu (niezacommitowany = stop), weryfikacja mtgo.com z ograniczeniem w raporcie, wiersz w
> `weekly/runs.csv` zamiast wpisu w `decisions.md`; dodatkowo piątkowy alarm `triage_check.py`. Stan wdrożony:
> `docs/runner.md`.

Stan na 2026-10-29: pipeline `becb31b`, DeckDB `6a335d4`, reguły DeckDB `ded3d7dba99e38d7`, ostatni Challenge w bazie
2026-10-25 (754 eventy Challenge). Dokument opisuje przebieg tygodnia bez zmian w kodzie i plan runnera do akceptacji.

## 1. Jak dziś wygląda pełny przebieg tygodnia

Dwa repozytoria, dwa interpretery:

| Repo | Katalog | Python | Uwagi |
|---|---|---|---|
| DeckDB | `E:\github\DeckDB` | `C:/Python/python.exe` | `PYTHONIOENCODING=utf-8`, cwd = katalog repo (`config.json` czytany względem cwd) |
| pipeline | `E:\github\MTG-Metagame-Input-Generator` | `E:/github/.venv/Scripts/python.exe` | goły `python` może nie mieć `yaml` |

### 1a. DeckDB: pobranie i import (dziś: wpisy 306 i 312 w `docs/decisions.md`)

```
python -m deckdb backfill-raw --start <S> --end <E> --report data/raw_mtgo/backfill_<S>_<E>.json
python -m deckdb import --raw-window <S>:<E>
python -m deckdb recompute-spell-colours
python -m deckdb recompute-sb-splash
python -m deckdb recompute-packages
python -m deckdb recompute-derived          # dziś bez zmian (had_bye / rekord liczone przy imporcie)
```

- `backfill-raw` pobiera listingi `https://www.mtgo.com/decklists/<YYYY>/<MM>` każdego miesiąca okna, wybiera z nich
  Modern Challenge (slug `modern-challenge-<16|32|64>-...`) z datą w `[S, E]` i zapisuje strony eventów do
  `data/raw_mtgo/modern/<event_id>.json`; event już zapisany nie jest pobierany ponownie. Walidacja, kwarantanna,
  6 prób, 1 żądanie na 5 s. Błąd pojedynczego eventu NIE przerywa komendy (kod 0), trafia do `failures` w raporcie
  JSON i do linii `FAILED ...` na stdout.
- `import --raw-window` przenosi zapisane eventy okna do `data/deckdb.sqlite` (append-only).
- Klasyfikacja nie jest osobnym krokiem zapisu: przypisanie liczy się przy eksporcie (`classify_all` na regułach
  `rules/registry.yaml` i zamrożonych centroidach).

Decyzje ręczne dziś:
- okno `S..E`: od dnia po ostatnim imporcie do niedzieli; przypadek 12857053 (event z 2026-10-21 16:00 nie był
  jeszcze na listingu w chwili importu tego dnia) wyłapano ręcznie ponownym listingiem `2026-10-21..25`;
- wpis do `docs/decisions.md` z liczbą eventów i list oraz commit w DeckDB.

### 1b. DeckDB: triage (K43, wpisy 316, 322, 330)

```
python scripts/weekly_triage.py [--report] [--record <ENTRY>]
```

- Tylko odczyt bazy; zapisuje `data/exports/triage_<data_version>.json` i drukuje
  `<status>: <n> group(s) awaiting decision (<x>% of the <N> lists of <ISO week>)` plus grupy oczekujące.
- Status: `complete` / `pending_notice` / `pending_blocking` (grupy oczekujące >= 3% list tygodnia albo grupa czeka
  > 28 dni). Tydzień triage'u = tydzień ISO ostatniego Challenge'a w bazie (nie argument).
- Ręcznie (Karol + Claude Code): `--record ENTRY` (numer wpisu w `decisions.md`), nowe grupy do kolejki
  (`build-review-queue`, `export-review-bundle`, wdrożenie na mikrus), cykl decyzji (`log_check --pull`,
  `apply-decisions`, `freeze_cores.py`, `assignment_diff.py`). Tego runner nie robi (patrz 3).

### 1c. DeckDB: eksporty z jednego stanu bazy

```
python -m deckdb export-assignments --out data/exports/assignments_<rules_version>_<ostatni Challenge>.csv
python -m deckdb export-cards       --out data/exports/cards_<rules_version>_<ostatni Challenge>.csv
python scripts/export_diff.py <poprzedni eksport> <nowy> --since <pon> --until <niedz>   # opcjonalnie
```

Oba pliki niosą ten sam `data_version` (`challenge_lists=...;last_challenge=...;scryfall_dump=...;name_match=...`);
plik triage'u musi mieć ten sam `data_version` i leżeć obok eksportu przypisań. `data/exports/` jest poza gitem.

### 1d. Pipeline: folder tygodnia

Ręcznie: edycja `docs/deckdb_config.csv` (`export_path`, `cards_path` na nowe pliki; `format_events_path` bez
zmian) i commit. Potem:

```
E:/github/.venv/Scripts/python.exe src/metagame_input_generator.py --input-mode deckdb --as-of <niedziela> --trend-weeks 12
```

- Pisze `outputs/_working/deckdb/WORKING_<as-of>/` (materiały robocze) i płaski `outputs/presentation/<rok>-W<nn>/`
  (01..09 PNG, `data.xlsx`, `notes.md`, przy materiale miesięcznym `m01`, `m02`). `outputs/` jest poza gitem.
- Materiał miesięczny: reguła `month_for` z `eb0df0b` (miesiąc M w tygodniu ISO z 1. dniem M+1, okres do ostatniego
  dnia M); `--presentation-month` tylko do materiału testowego. Obecny folder `2026-W43` powstał jeszcze z
  `--presentation-month 2026-10` (październik niepełny), więc nie jest wzorcem dla kontroli 1 (patrz 5).
- `pending_blocking` odmawia pracy (`DeckdbInputError`, kod 2) bez `--allow-pending-triage`.
- „Classification changes since the previous week” szuka w katalogu eksportu pliku `assignments_*.csv` z ostatnią
  datą <= poprzednia niedziela (remis: najnowszy) — nazwy i katalog eksportów muszą zostać takie jak dziś.
- Opcjonalnie `scripts/legacy_w42_control.py` przy zmianie kodu pipeline'u (runner tego nie robi).

## 2. Kroki, które dziś wymagają decyzji, i co z nimi robi runner

| Krok | Dziś | Runner |
|---|---|---|
| okno importu | ręcznie | `S` = poniedziałek tygodnia `--week`, `E` = niedziela; listing pobierany zawsze od nowa, więc spóźniony event (12857053) zostanie dociągnięty; event już zapisany nie jest pobierany ponownie |
| błąd pobrania eventu | czytanie raportu | `failures` w raporcie `backfill-raw` niepuste = stop z listą ID |
| przeliczenie pól pochodnych | ręcznie | zawsze trzy `recompute-*` po imporcie (idempotentne) |
| triage `pending_blocking` | rozstrzygnięcie w aplikacji | stop: liczba grup, udział w listach tygodnia, lista grup (`item_key`, listy, dni) i instrukcja |
| `--record`, kolejka, wdrożenie, decyzje | Claude Code | poza runnerem (zostaje w `/tydzien`) |
| wpis do `decisions.md`, commit | Claude Code | poza runnerem: runner niczego nie commituje; wszystko jest w `run_report.md` |
| `docs/deckdb_config.csv` | edycja + commit | runner pisze własny config do `outputs/_working/runner/<tydzień>/deckdb_config.csv` i podaje go przez `--deckdb-config`; plik w `docs/` bez zmian |
| `--presentation-month` | wybór | nigdy; zawsze reguła `month_for` |

## 3. Plan runnera

Pliki: `metagame_week.py` i `metagame_week.bat` w katalogu głównym, ścieżki w `docs/runner_config.csv`
(`deckdb_dir`, `deckdb_python`, `pipeline_python`; brak klucza = stop). Kod pomocniczy w `src/weekly_runner/`
(preflight, kroki DeckDB, weryfikacja mtgo.com, raport), testy w `tests/test_weekly_runner.py`.

Argumenty: `--week 2026-W44` (domyślnie ostatni zakończony tydzień ISO względem dzisiaj), `--stage data|build|all`
(domyślnie `data`), `--force`. Każdy błąd = komunikat `STOP [kod]: ...` i kod wyjścia != 0; `run_report.md` jest
zapisywany także przy stopie (do miejsca błędu), jeśli folder tygodnia już powstał.

Etap DATA:

1. **Preflight.** Python obu interpreterów; pakiety pipeline'u (`pandas`, `matplotlib`, `openpyxl`, `yaml`,
   `requests`) i DeckDB (import `deckdb`, `yaml`); HEAD do `https://www.mtgo.com/decklists`; istnienie
   `deckdb_dir`, `config.json`, bazy, `rules/format_events.yaml`; `git status --porcelain` obu repo (niepusty =
   ostrzeżenie z listą plików, nie stop); zapis commitów obu repo i `rules_version` (`deckdb.review.rules`).
   Folder `weekly/<tydzień>/` istnieje i brak `--force` = stop.
2. **Pobranie i import:** komendy z 1a dla okna tygodnia, stdout do `outputs/_working/runner/<tydzień>/logs/`.
3. **Triage:** `weekly_triage.py` (bez `--record`); status z JSON; `pending_blocking` = stop z komunikatem.
   Dla tygodnia z przeszłości (w bazie są już późniejsze Challenge) triage opisuje stan bazy, nie ten tydzień —
   ostrzeżenie w raporcie.
4. **Eksport:** `export-assignments` i `export-cards` pod dzisiejszymi nazwami. Gdy plik o tej nazwie już
   istnieje: eksport do pliku tymczasowego i porównanie sha256 — równe = użyty istniejący, różne = stop (inny
   stan bazy przy tej samej nazwie; `--force` nadpisuje). Kontrola, że `data_version` obu plików i triage'u są
   równe. sha256 do raportu.
5. **Weryfikacja mtgo.com:** własny parser w runnerze (nie kod DeckDB), świeże pobranie listingów miesięcy
   pokrywających okno (przy przełomie oba), zapis `mtgo_snapshot_<YYYY-MM>.html`. Wyciąg: wszystkie Modern Challenge
   (ID, data, rozmiar 16/32/64 ze slugu). Porównanie zbioru ID okna z eksportem w obie strony; rozbieżność = stop z
   listą ID. Sąsiedzi: ostatni Challenge przed oknem i pierwszy po oknie z listingów (gdy pierwszego po oknie
   jeszcze nie ma: „brak na listingu”, bez błędu); kontrola, że są poza oknem. Data listingu różna od
   `event_date` eksportu o 1 dzień = ostrzeżenie z tygodniem, do którego event trafił; o więcej = stop. HTTP != 200,
   brak linków albo brak wzorca `/decklists/<slug>-<YYYY>-<MM>-<DD><ID>` = stop („zmiana struktury strony”).
   Uwaga: `backfill-raw` czyta ten sam listing, więc kontrola wyłapuje braki importu i eksportu (np. 12857053),
   a nie braki samego mtgo.com.
6. **Pipeline:** komenda z 1d z configiem runnera, `--as-of <niedziela>`, `--trend-weeks 12`, bez
   `--presentation-month` i bez `--allow-pending-triage`.
7. **Folder** `weekly/<tydzień>/` (katalog główny repo, dopisany do `.gitignore`): kopia plików z
   `outputs/presentation/<tydzień>/`, snapshoty, `run_report.md`. Kontrola: zestaw plików kopii = zestaw źródła.

`run_report.md` po polsku: tydzień i okno, commity obu repo (+ ostrzeżenia git), `rules_version`, sha256 obu
eksportów, eventy i listy okna, tabela weryfikacji (ID / plik / mtgo.com / data DeckDB / data mtgo.com / rozmiar),
sąsiedzi, triage (status, grupy), materiał miesięczny (tak/nie, okres), ostrzeżenia, czas kroków.

Etap BUILD: preflight (`node`, `pptxgenjs` przez `node -e "require('pptxgenjs')"`, `python-docx`), obecność
5 plików treści (`deck_w<N>.json`, `dossier_w<N>.src.md`, `reddit_w<N>.md`, `youtube_w<N>.md`, `x_w<N>.md`), brak = stop
z listą; skrypty w kolejności z `build/README_workflow.md`, wyniki do `weekly/<tydzień>/out/`; kod != 0 albo `FAIL`
w wyjściu `qa.py` = stop; sha256 każdego pliku `build/` w raporcie. Runner nie modyfikuje plików w `build/`.
**Argumenty wywołań poznam dopiero z `README_workflow.md`** — katalogu `build/` jeszcze nie ma w repo.

## 4. Kontrole (krok 2)

1. W43: wzorzec = `outputs/presentation/2026-W43/` wygenerowany bieżącym kodem tą samą komendą co runner (bez
   `--presentation-month`, więc bez `m01`/`m02`; obecny folder z październikiem testowym zostanie nadpisany).
   Porównanie: PNG i `notes.md` bajtowo, `data.xlsx` arkusz po arkuszu (openpyxl wpisuje datę utworzenia do
   właściwości pliku, więc bajty xlsx różnią się przy każdym zapisie — to jedyne „generated_at” w tym folderze).
   MTGO: 12 ID, sąsiedzi 12856599 (2026-10-18) i 12856997 (2026-10-26) poza oknem.
2. W40 (`--week 2026-W40`): listingi 2026-09 i 2026-10, wrzesień jako materiał miesięczny jak w kontroli części 4.
3. Testy syntetyczne (pytest, bez sieci przez podstawione odpowiedzi): blokada triage'u, brakujący i nadmiarowy
   event, brak sieci, istniejący folder bez `--force`.
4. Build W43 z Twoimi plikami treści.

## 5. Pytania przed implementacją

1. Import w kontrolach 1 i 2 jest operacją zapisu w bazie DeckDB (dla W43 i W40 nie doda nic, bo baza ma już dane do
   2026-10-25, ale `backfill-raw` pobierze listingi). Czy runner ma też dopisywać wpis do `docs/decisions.md` przy
   imporcie z nowymi eventami (dziś robi to Claude Code), czy wystarczy `run_report.md` i log w
   `outputs/_working/runner/`?
2. `weekly/` w katalogu głównym repo i poza gitem — zgoda?
3. Skrypty `build/` i pliki treści W43: proszę o wgranie, zanim zaimplementuję etap BUILD.
