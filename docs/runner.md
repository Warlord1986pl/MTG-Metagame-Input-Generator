# Runner tygodnia i piątkowy alarm triage'u

Dwa skrypty w katalogu głównym repo, uruchamiane bez Claude Code:

| skrypt | kiedy | co robi |
|---|---|---|
| `metagame_week.bat` / `metagame_week.py` | poniedziałek | import tygodnia do DeckDB, triage, eksporty, weryfikacja z mtgo.com, materiały tygodnia w `weekly/<tydzień>/`, w tygodniu zamknięcia miesiąca także paczka miesięczna `monthly/<RRRR-MM>/`, opcjonalnie build prezentacji i dokumentów |
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
metagame_week.bat --month 2026-10
metagame_week.bat --month 2026-10 --force
```

| argument | znaczenie |
|---|---|
| `--week RRRR-Wnn` | tydzień ISO; domyślnie ostatni zakończony; tydzień niezakończony = stop |
| `--stage data\|build\|all` | domyślnie `data`; `all` = DATA, potem BUILD |
| `--force` | zastępuje materiały istniejącego folderu tygodnia (stare pliki znikają dopiero po udanym pipeline'ie); bez niego folder z materiałami = stop. Pliki treści (`deck_w<N>.json` itd.) i `triage_check_*.md` nie liczą się jako materiały i nigdy nie są usuwane |
| `--test-monthly` | testowy materiał miesięczny za bieżący, niepełny miesiąc tygodnia (`incomplete month`, dawne `--presentation-month`); w tygodniu zamykającym miesiąc = stop, bo ma już prawdziwy materiał miesięczny. Zostaje tylko w folderze tygodnia: `monthly/` wtedy nie powstaje |
| `--month RRRR-MM` | tylko paczka miesięczna `monthly/<RRRR-MM>/` za pełny miesiąc, bez etapu tygodniowego (sekcja „Paczka miesięczna”); nie łączy się z `--week`, `--stage build/all` ani `--test-monthly`; istniejący folder bez `--force` = stop |

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
8. **Paczka miesięczna** (tylko w tygodniu zamknięcia miesiąca, przed krokiem 6 zmiany reguł DeckDB miesiąca, w kroku
   6 ten sam przebieg pipeline'u): `monthly/<RRRR-MM>/`, sekcja „Paczka miesięczna” niżej.

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

`qa.py` dostaje walidator pptx parametrem `--validator <ścieżka do validate.py>`; runner podaje ścieżkę z klucza
`pptx_validator` w `docs/runner_config.csv` (skrypt `scripts/office/validate.py` lokalnie zsynchronizowanego skilla
pptx Claude, `C:\Users\kmalo\.claude\skills\synced\...\pptx`). Brak pliku = stop w preflighcie etapu (kod 10);
`qa.py` bez walidatora daje `FAIL brak validate.py`. Junction `E:\mnt\skills\public\pptx` z wersji sprzed
build_w44 jest usunięty (2026-11-02) i niepotrzebny. Po nowej synchronizacji skilla (inny folder) popraw
`pptx_validator`. `markitdown` jest opcjonalny
(`qa.py` czyta wtedy tekst przez `python-pptx`).

**Podmiana skryptów w `build/`:** skopiuj nowe pliki z projektu Claude do `build/` (nadpisanie), uruchom
`metagame_week.bat --week <tydzień> --stage build` i porównaj sha256 w sekcji „Skrypty build/” raportu z wersją w
projekcie. Claude Code nie edytuje plików w `build/`; zmiany logiki przychodzą z projektu Claude.

## Paczka miesięczna

Paczka dla miesięcznego artykułu na metagame.info (polecenie 2026-11-03). Powstaje na dwa sposoby, tym samym kodem
(`deckdb_folder.build_monthly_package` + `src/weekly_runner/monthly.py`):

- **w tygodniu zamknięcia miesiąca** (reguła `month_for` z eb0df0b: tydzień ISO zawierający 1. dzień kolejnego
  miesiąca; W44 -> `monthly/2026-10/`): ten sam przebieg etapu DATA, ten sam eksport i ten sam stan bazy co
  `weekly/<tydzień>/`; w pozostałych tygodniach `monthly/` nie powstaje;
- **osobno**: `metagame_week.bat --month 2026-10` (preflight, triage, eksport z bieżącego stanu bazy, zmiany reguł,
  pipeline z `--deckdb-level none`, paczka; bez importu, weryfikacji mtgo.com i folderu tygodnia). Po poprawce reguł w
  trakcie miesiąca albo do odtworzenia miesiąca, jeśli tydzień zamknięcia już przeszedł. Miesiąc niezakończony albo
  eksport bez ostatniego dnia miesiąca = stop.

Istniejący `monthly/<RRRR-MM>/` z plikami bez `--force` = stop (2), także w przebiegu tygodnia. Paczka jest składana i
sprawdzana w `outputs/_working/runner/<tydzień albo month_RRRR-MM>/monthly_stage/`; folder docelowy jest zastępowany
dopiero po przejściu wszystkich kontroli.

Zawartość (płaski folder):

| plik | treść |
|---|---|
| `data.xlsx` | `contents`, `m_deck_results`, `m_results_bands`, `m_list_changes`, `monthly_metrics` (miesiąc × talia / archetyp, od pierwszego pełnego miesiąca okresu formatu – dziś czerwiec 2026, maj pominięty – do miesiąca paczki: listy, udział, eventy, Top 8, eventy z Top 8, trofea, piloci, udział top pilota, `concentrated`, zmiana vs poprzedni miesiąc, `tracked` / `tracked_reason`, test zmiany dwiema metodami: bootstrap po eventach `*_event` i po pilotach `*_pilot` (przedział, p, q BH, `change_beyond_*`), `change_beyond` = event AND pilot – reguła obu metod, decyzja 2026-11-03), `variants_month` (Deck, Variant, Lists, Top8, Trophies; suma wariantów = talia), `top8_month`, `new_cards_month`, `pilots_month`, `share_history_months_decks`, `share_history_months_archetypes`, `weeks_in_month`; każdy arkusz z `rules_version`, `export_sha256`, `data_version` |
| `m01_deck_results.png`, `m02_list_changes.png` | te same wykresy co w folderze tygodnia (m02 nie powstaje, gdy żadna zmiana list nie przeszła testów); m01 liczy okres formatu od 2026-05-19 |
| `m03`–`m14` | odpowiedniki wykresów tygodniowych w skali miesiąca i wykresy miesiąca: m03 udział archetypów, m04 archetypy miesiąc po miesiącu, m05 10 talii + Rogue & unclassified (other decks linią), m06 te talie miesiąc po miesiącu, m07 te talie tydzień po tygodniu w miesiącu (tygodnie przecięte granicą z `*`, bez testu), m08 / m09 macierze liczb, m10 Top 8 i trofea (15 talii słupkami, reszta linią pod wykresem), m11 oś trofeów (event po evencie, bez graczy), m12 piloci i koncentracja, m13 nowe karty miesiąca, m14 warianty. Moduł wykresów `src/deckdb_presentation.py` (te same funkcje co w tygodniu, parametry z domyślnymi tygodniowymi), wejścia z arkuszy: `src/deckdb_month_checks.py` |
| `notes.md` | Status (miesiąc i jego okres, okres formatu do ostatniego dnia miesiąca, eventy i listy miesiąca i okresu formatu, pokrycie uczestników miesiąca, wersja reguł, sha256, `complete month`, triage, zmiany klasyfikacji od poprzedniego miesięcznego eksportu z linią `renamed`), DeckDB rule changes this month, Files, Speaker notes (m01–m14), Glossary (pełny, z terminami `tracked decks (month)`, `monthly change test (BH)`, `weeks in month`, `trophy timeline`) |
| `README_metagame_info.md` | `docs/monthly_package_rules.md` (zasady danych dla projektu metagame.info) z nagłówkiem: data wygenerowania, commit repo, wersja reguł DeckDB |
| `run_report_monthly.md` | wersje i commity, eksporty, triage (status z tego przebiegu), liczba talii w `m_deck_results` z testem i odrzucenia BH obu miar, kontrole, czasy kroków, komenda pipeline'u |

**Poprzedni miesięczny eksport** (zmiany klasyfikacji): eksport przypisań z katalogu eksportów z ostatnim eventem
najpóźniej w niedzielę tygodnia zamknięcia poprzedniego miesiąca (dla października: 2026-10-04). Brak takiego pliku
(październik: najstarszy eksport kończy się 2026-10-18) = linia „none” zamiast przeniesień.

**DeckDB rule changes this month** (`src/weekly_runner/rule_changes.py`): wpisy `docs/decisions.md` DeckDB z datą
decyzji w miesiącu, których przedmiot (tabela „Przedmioty wpisów”) to talia (`deck:`, `decks:`) albo priorytety
(`priorities:`), i których commit (ten, który dodał wpis do `docs/decisions.md`) zmienił w `rules/registry.yaml` tę
talię (nowa talia, nazwa, archetyp, sygnatura = każdy klucz definicji poza nazwą / archetypem / wariantami / źródłami
/ decyzjami / cechami, warianty, usunięcie) albo priorytety. O tym, czy wpis zmienia talię, decydują dane rejestru,
nie słowa wpisu: odrzucona sygnatura, „wariant D” (bez zmian), pozycja kolejki czy zamrożenie nie trafiają na listę.
Wpisy zastosowane w jednej partii (`apply-decisions`) z tą samą zmianą tej samej talii są jedną linią („48-52”), bo
commit ich nie rozróżnia. Liczba list = listy miesiąca w tej talii teraz (bieżący eksport); talia usunięta ma 0. Bez
nazw graczy. Ograniczenie: zmiana rejestru zrobiona w innym commicie niż wpis nie jest widoczna.

**Kontrole** (rozjazd = stop 11, bez fallbacku), na plikach paczki wobec pliku eksportu:

1. pliki i arkusze jak wyżej; każdy arkusz ma `rules_version`, `export_sha256` i `data_version` eksportu;
2. `Period_end` arkuszy `m_*` = ostatni dzień miesiąca; suma `Lists` w `m_deck_results` = listy eksportu od
   `Period_start` do ostatniego dnia miesiąca; `monthly_metrics` kończy się ostatnim dniem miesiąca, miesiąc ma
   `complete_month = 1` (żadnej listy z kolejnego miesiąca);
3. `notes.md`: linie `Month`, `Monthly material` (eventy, listy) i okresu formatu równe liczbom z eksportu dla dat;
4. w tygodniu zamknięcia: arkusze `m_*` identyczne z arkuszami folderu tygodnia (komórka po komórce), m01 / m02
   identyczne bajtowo;
5. `variants_month`: talie z wariantami = talie z wariantami w eksporcie miesiąca; suma wariantów = listy, Top 8 i
   trofea talii z eksportu;
6. m01: kolor słupka (`Colour_geometry` w `m_results_bands`) = `Flag_sheet` = `*_BH_rejects` talii w `m_deck_results`
   (asercja jak w b5c848c / 81b2556), talie wykresu = talie z największą liczbą list;
7. `README_metagame_info.md` obecny i identyczny z `docs/monthly_package_rules.md` poza czterema liniami nagłówka;
8. miary miesięczne: suma list talii i suma po archetypach = listy eksportu w każdym miesiącu `monthly_metrics`;
   `top8_month`: każdy event miesiąca z 8 listami Top 8 i jednym zwycięzcą, trofea = eventy miesiąca; trophies <= top8
   <= lists, events_with_list <= month_events; `change_beyond`, `change_beyond_event`, `change_beyond_pilot` tylko
   przy `tracked = 1` i `change_beyond` = `change_beyond_event` AND `change_beyond_pilot`; brak maja 2026 (i każdego
   miesiąca przed pierwszym pełnym miesiącem okresu formatu) w `monthly_metrics` i `share_history_months_*`;
   `pilots_month` = `monthly_metrics` (listy, piloci, koncentracja);
9. wykresy m03–m14: każdy wykres zapisuje, co narysował (długości i kolory słupków, kawałki etykiet z pogrubieniem i
   kolorem zmiany, punkty linii, komórki macierzy, wiersze tabeli, punkty osi trofeów, odczytane z obiektów
   matplotlib); pipeline sprawdza to od razu, a runner ponownie na plikach (`<outputs-base>/monthly/<RRRR-MM>.charts.json`
   poza folderem paczki, arkusze z `data.xlsx`): kolejność i liczby = arkusz, kolory archetypów / stałe kolory talii,
   Rogue & unclassified szary, pogrubiona zielona / czerwona zmiana tylko przy `change_beyond = 1` (obie metody); m11: liczba
   punktów = eventy miesiąca, każdy zwycięzca = `top8_month`.

Rozdzielczość wykresów m03–m14 jak tygodniowych i m01 / m02: 1920 × 1080 px (144 dpi); polecenie podawało „dpi 200,
1920x1080 jak tygodniowe”, a przy 200 dpi ten sam kadr miałby 2667 × 1500 px, więc zostało 1920 × 1080 jak w tygodniu.

Testy: `E:\github\.venv\Scripts\python.exe -m pytest tests/test_monthly_package.py` (przebieg syntetyczny W44 z
oboma folderami, W45 tylko z tygodniowym, `--month 2026-10` odtwarza paczkę W44 arkusz po arkuszu i PNG bajtowo,
kontrole zatrzymują przy rozjeździe).

## Komunikaty błędów

| kod | etap | przykład komunikatu | co zrobić |
|---|---|---|---|
| 1 | dowolny | `nieoczekiwany błąd` + traceback | błąd kodu runnera: zgłoś z `run_report_STOP.md` |
| 2 | argumenty | `nie jest tygodniem ISO`, `nie jest zakończony`, `już zawiera materiały ... użyj --force`, `folder monthly/... już istnieje`, `--test-monthly: tydzień ... zawiera`, `--month nie łączy się` | popraw `--week` / `--month`; `--force`, jeśli folder ma być zastąpiony |
| 3 | preflight | `brak pakietu`, `brak ... (docs/runner_config.csv)`, `brak dostępu do sieci`, `docs/deckdb_config.csv ma niezacommitowane zmiany` | doinstaluj pakiet / popraw ścieżkę / sprawdź sieć / zacommituj albo cofnij config |
| 4 | import | `backfill-raw: nieudane pobrania` z listą ID; `import: odrzucone eventy lub odmowy zapisu` | pobrania: uruchom ponownie później (mtgo.com bywa niedostępne); odmowy: rozstrzygnij w DeckDB (`data/quarantine`), runner nie nadpisuje |
| 5 | triage | `pending_blocking, N grup(a) ... X% z M list` z listą grup | rozstrzygnij grupy w aplikacji przeglądu DeckDB, zastosuj decyzje (cykl `/tydzien`), uruchom runner ponownie |
| 6 | eksport | `już istnieje z inną zawartością`, `data_version różne`, `stan bazy zmienił się po triage'u` | porównaj pliki (`scripts/export_diff.py` w DeckDB), przenieś stary plik; przy zmianie stanu uruchom ponownie |
| 7 | mtgo.com | `brak w eksporcie: <ID>`, `brak na mtgo.com: <ID>`, `data różna o więcej niż 1 dzień`, `brak linków ... (zmiana struktury strony?)`, `po 4 próbach` | brak w eksporcie: sprawdź import (event mógł wystartować po listingu, uruchom ponownie); nadmiarowy: sprawdź event na mtgo.com; struktura strony: obejrzyj `mtgo_snapshot_*.html` w `outputs/_working/runner/<tydzień>/snapshots/` |
| 8 | pipeline | `pipeline (input mode deckdb): kod wyjścia 2` z końcem logu | błąd danych w pipeline'ie (np. `DeckdbInputError`): log w `logs/pipeline.log` |
| 9 | folder | `kopia ... różni się`, `ma podkatalogi` | błąd dysku albo zmiana pipeline'u: zgłoś |
| 11 | paczka miesięczna | `kontrola paczki miesięcznej: ...` (np. `notes.md: miesiąc ...`, `m01 <talia>: kolor ...`, `README_metagame_info.md różny`), `nie znaleziono commitu, który dodał wpis` | rozjazd danych albo zmiana kodu: raport `run_report_monthly_STOP.md` w `outputs/_working/runner/...`; folder `monthly/` zostaje bez zmian |
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
- `monthly/<RRRR-MM>/`: paczka miesięczna (poza gitem, `.gitignore`);
- `weekly/runs.csv`: data, tydzień (przy `--month`: miesiąc), etap (`data` / `build` / `all` / `month`), status
  (`OK` / `STOP <kod>`), sha256 eksportu, wersja reguł, eventy, listy (tygodnia; przy `--month` miesiąca), status
  triage'u, `monthly_folder` (ścieżka paczki albo pusta; plik bez tej kolumny dostaje ją przy następnym przebiegu)
  (stop z kodem 2 nie jest zapisywany);
- `outputs/_working/runner/<tydzień>/`: logi, raport `backfill-raw`, kopia configu, eksporty tymczasowe, snapshoty,
  wyjście pipeline'u;
- `outputs/_working/reference/<tydzień>/`: wzorzec do porównań (kontrola runnera),
  `scripts/compare_week_folders.py <folder A> <folder B>` porównuje dwa foldery (PNG, MD bajtowo, `data.xlsx` arkusz
  po arkuszu).

Testy syntetyczne: `E:\github\.venv\Scripts\python.exe -m pytest tests/test_weekly_runner.py`.
