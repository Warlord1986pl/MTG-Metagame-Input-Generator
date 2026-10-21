# Tryb deckdb: specyfikacja materiałów (PROJEKT do akceptacji)

Stan: projekt z 2026-10-21, gałąź `deckdb-input`. Nie opisuje obecnego kodu materiałów
(`src/deckdb_materials.py`), tylko docelowy kształt materiałów do akceptacji; kod się nie zmienia,
dopóki ten dokument nie zostanie przyjęty.

Podstawa: decyzja K33 w DeckDB (analiza metagame opiera się wyłącznie na eksporcie DeckDB),
format eksportu w DeckDB `docs/export.md` („Format eksportu dla Analizy metagame”), metodologia w
DeckDB `docs/master_prompt.md` („Metodologia wyników”), próg wariantów D2 w DeckDB
`docs/decisions.md` (wpis 35). Liczby w tym dokumencie pochodzą z eksportu
`assignments_ee3ed9fbbbbd6ad6_2026-10-18.csv` (reguły `ee3ed9fbbbbd6ad6`, sha256
`2065541b6a65a2f5f868165c40f0cf4465d06320ca5e5fcb90256baa631aff9f`).

**Decyzja 2026-10-21: winrate nie jest podawany w materiałach. Główną miarą wyników jest
konwersja Top 32 → Top 8.** Kolumny `swiss_wins`, `swiss_losses`, `had_bye` nie są używane przez
żaden materiał.

## (a) Słownik pojęć

- **Event** — jeden Modern Challenge z eksportu (`event_id`); eventy premier (Showcase, RC) nie
  występują w eksporcie i nie są eventami w tym sensie. Data eventu to `event_date` (UTC).
  Liczba eventów okresu to liczba różnych `event_id` z datą w okresie (okres zawsze obustronnie
  domknięty).
- **Top 32** — opublikowane listy eventu: `final_rank` = 1 … L, gdzie L = min(32, `player_count`)
  to liczba list eventu. Jedna lista = jeden wiersz eksportu (`event_id`, `decktournamentid`).
  Populacja wszystkich materiałów to listy Top 32, nie wszyscy uczestnicy i nie meta MTGO.
- **Udział** (udział w Top 32 Challenge'ów) — listy talii / wszystkie listy okresu. Rogue i
  nieprzypisane są w materiałach jednym wierszem „bez talii”, osobno tylko w pliku roboczym.
  Nie jest prawdopodobieństwem spotkania talii.
- **Obecność** — liczba różnych eventów okresu z co najmniej jedną listą talii; w wykresach także
  jako odsetek eventów okresu.
- **Top 8** — lista z `final_rank` ≤ 8. Miary: liczba list Top 8 talii oraz Top 8 jako odsetek
  eventów (eventy z co najmniej jedną listą talii w Top 8 / eventy okresu). Każdy event okresu od
  2026-05-19 ma dokładnie 8 list Top 8 (zmierzone).
- **Konwersja** (Top 32 → Top 8) — listy talii w Top 8 / listy talii w Top 32.
- **Oczekiwana konwersja** — konwersja, jakiej należałoby się spodziewać, gdyby każda lista talii
  trafiała do Top 8 z tą samą szansą co losowa lista swojego eventu: Σ 8/L po listach talii
  (oczekiwana liczba list Top 8), podzielona przez liczbę list talii. Uwzględnia, że w evencie z L
  < 32 listami szansa jednej listy na Top 8 jest większa niż 8/32. Bazą wspólną nazywamy
  wszystkie listy Top 8 / wszystkie listy okresu.
- **Trofeum** — lista z `final_rank` = 1; miary: liczba trofeów, trofea / listy talii, zwycięstwa
  jako odsetek eventów okresu.
- **Pilot** — wartość kolumny `player` eksportu (anonimowy, stały identyfikator gracza). Wszystkie
  miary pilotów liczy się wyłącznie z tej kolumny; `loginid` i nicki nie trafiają do materiałów
  ani do obliczeń materiałów. Pilot nie jest wymieniany z identyfikatora, tylko liczony.
- **Pokrycie uczestników** — liczba list okresu / suma `player_count` eventów okresu: jaka część
  uczestników Challenge'ów jest objęta danymi (pozostali nie opublikowali listy, bo nie weszli do
  Top 32).

## (b) Trzy poziomy czasu

Każda miara ma jeden poziom czasu. Archetyp jest zawsze agregatem list swoich talii na tym samym
poziomie. Każdy materiał podaje okres (daty od–do), liczbę eventów, liczbę list, pokrycie
uczestników, wersję reguł i sha256 eksportu.

| Poziom | Okres | Miary |
|---|---|---|
| Tydzień | tydzień ISO (poniedziałek–niedziela), tygodnie rozłączne | udział, obecność, Top 8 (liczba list, % eventów) |
| Miesiąc | miesiąc kalendarzowy | udział, obecność, Top 8; warianty według D2 |
| Okres formatu | od daty ostatniego dużego zdarzenia formatu do ostatniej daty eventu w eksporcie, narastająco | konwersja z oczekiwaną konwersją i przedziałem ufności, trofea, miary pilotów |

- **Tydzień.** Zastępuje dzisiejsze nakładające się okna 14-dniowe przesuwane co 7 dni. Tydzień
  jest raportowany tylko wtedy, gdy eksport obejmuje go w całości (ostatnia data eventu ≥
  niedziela tygodnia); inaczej odmowa, tak jak dziś dla okna.
- **Miesiąc.** Warianty według D2 (wpis 35): wariant jest raportowany w miesiącu, jeśli ma w tym
  miesiącu co najmniej 5% list talii, co najmniej 3 listy i co najmniej 2 różnych pilotów; liczą
  się tylko miesiące z co najmniej 20 listami talii. Wariant nie zmienia metryk talii. Miesiąc
  niepełny (eksport kończy się w jego trakcie) jest oznaczony jako niepełny.
- **Okres formatu.** Początek to `effective_mtgo` najpóźniejszego wpisu z `major: true` i
  `status: confirmed` w DeckDB `rules/format_events.yaml` (czytany tylko do odczytu). Obecnie
  2026-05-19 (ban Phlage, Titan of Fire's Fury i Lotus Field, unban Violent Outburst i Umezawa's
  Jitte, ogłoszenie 2026-05-18). Materiał tego poziomu rośnie z każdym eksportem do następnego
  dużego zdarzenia, które zaczyna nowy okres. Okres od 2026-05-19 do 2026-10-18: 263 eventy,
  8387 list.
