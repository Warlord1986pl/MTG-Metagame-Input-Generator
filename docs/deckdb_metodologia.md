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

## (c) Konwersja w okresie formatu i oczekiwana konwersja

Materiał poziomu „okres formatu” podaje dla każdej talii (i archetypu jako agregatu list jego
talii): listy, listy Top 8, konwersję z przedziałem ufności (punkt e), oczekiwaną konwersję
(Σ 8/L po listach talii, przez liczbę list) i różnicę konwersja − oczekiwana konwersja w punktach
procentowych. Wykres konwersji koloruje tę różnicę skalą rozbieżną wokół zera (zamiast dzisiejszej
skali wokół bazy wspólnej). Baza wspólna jest podana w podpisie dla orientacji.

**Zmierzone na okresie 2026-05-19 … 2026-10-18** (263 eventy, 8387 list, pokrycie uczestników
39,3%; talie z co najmniej 30 listami, bez wiersza „bez talii”; skrypt
`scripts/deckdb_methodology_stats.py --since 2026-05-19`):

- baza wspólna: 2104 listy Top 8 / 8387 list = **25,086%** (każdy event ma 8 list Top 8; L od 24
  do 32);
- oczekiwana konwersja różni się od bazy wspólnej o co najwyżej **0,59 pp** (Izzet Murktide
  +0,59, Rakdos Moonshadow +0,58 — talie skupione w ostatnich tygodniach, w których częściej są
  eventy z L < 32); w 28 z 32 talii różnica mieści się w ±0,09 pp; pozostałe dwie: Hammer Time
  +0,13, Plunge into Cosmogoyf +0,12;
- różnica jest więc o rząd wielkości mniejsza niż przedział ufności konwersji którejkolwiek talii
  (punkt e). Oczekiwana konwersja zostaje w materiale, bo w krótszym okresie albo przy większej
  liczbie małych eventów może się oddalić od bazy, a koszt jej podania jest zerowy.

Konwersja, oczekiwana konwersja i różnice (kolumny w %, różnice w pp):

| Talia | Listy | Top8 | Konwersja_pct | Oczekiwane_Top8 | Oczekiwana_konwersja_pct | Oczekiwana_minus_baza_pp | Konwersja_minus_oczekiwana_pp |
|---|---|---|---|---|---|---|---|
| Broodscale Combo | 787 | 208 | 26.43 | 197.29 | 25.07 | -0.02 | 1.36 |
| Energy | 704 | 185 | 26.28 | 176.41 | 25.06 | -0.03 | 1.22 |
| Prowess | 625 | 153 | 24.48 | 156.90 | 25.10 | 0.02 | -0.62 |
| Goryo's Vengeance | 523 | 130 | 24.86 | 130.97 | 25.04 | -0.04 | -0.19 |
| Blink | 504 | 132 | 26.19 | 126.44 | 25.09 | 0.00 | 1.10 |
| Affinity | 473 | 115 | 24.31 | 118.83 | 25.12 | 0.04 | -0.81 |
| Tron | 385 | 94 | 24.42 | 96.45 | 25.05 | -0.03 | -0.64 |
| Ruby Storm | 344 | 86 | 25.00 | 86.17 | 25.05 | -0.04 | -0.05 |
| Eldrazi Ramp | 310 | 88 | 28.39 | 77.64 | 25.05 | -0.04 | 3.34 |
| Devoted Combo | 293 | 74 | 25.26 | 73.62 | 25.13 | 0.04 | 0.13 |
| Prison | 273 | 74 | 27.11 | 68.71 | 25.17 | 0.08 | 1.94 |
| Land Destruction | 270 | 71 | 26.30 | 67.53 | 25.01 | -0.07 | 1.28 |
| Reanimator | 260 | 60 | 23.08 | 65.13 | 25.05 | -0.04 | -1.97 |
| Dimir Midrange | 248 | 65 | 26.21 | 62.09 | 25.03 | -0.05 | 1.17 |
| Living End | 245 | 68 | 27.76 | 61.37 | 25.05 | -0.04 | 2.71 |
| Zoo | 242 | 64 | 26.45 | 60.60 | 25.04 | -0.04 | 1.40 |
| Amulet Titan | 194 | 55 | 28.35 | 48.74 | 25.12 | 0.04 | 3.23 |
| Neobrand | 157 | 35 | 22.29 | 39.44 | 25.12 | 0.03 | -2.83 |
| Belcher | 152 | 43 | 28.29 | 38.05 | 25.03 | -0.05 | 3.26 |
| Hollow One | 133 | 29 | 21.80 | 33.39 | 25.10 | 0.02 | -3.30 |
| Yawgmoth | 104 | 31 | 29.81 | 26.08 | 25.08 | -0.01 | 4.73 |
| Control | 103 | 27 | 26.21 | 25.85 | 25.10 | 0.01 | 1.12 |
| Esper Ketra | 99 | 22 | 22.22 | 24.86 | 25.11 | 0.02 | -2.89 |
| Emry | 98 | 20 | 20.41 | 24.52 | 25.02 | -0.07 | -4.61 |
| Burn | 97 | 15 | 15.46 | 24.36 | 25.11 | 0.03 | -9.65 |
| Rakdos Moonshadow | 83 | 29 | 34.94 | 21.30 | 25.66 | 0.58 | 9.28 |
| Necrodominance | 79 | 19 | 24.05 | 19.76 | 25.01 | -0.08 | -0.96 |
| Plunge into Cosmogoyf | 63 | 11 | 17.46 | 15.88 | 25.20 | 0.12 | -7.74 |
| Mono-Black Midrange | 59 | 16 | 27.12 | 14.85 | 25.16 | 0.08 | 1.96 |
| Izzet Murktide | 37 | 10 | 27.03 | 9.50 | 25.68 | 0.59 | 1.35 |
| Mill | 33 | 4 | 12.12 | 8.25 | 25.00 | -0.09 | -12.88 |
| Hammer Time | 32 | 4 | 12.50 | 8.07 | 25.22 | 0.13 | -12.72 |

## (d) Miary pilotów w okresie formatu

Pilot = wartość kolumny `player`; nic innego (żadne `loginid`, żadne nicki) nie jest czytane.
Materiał podaje tylko liczby i udziały, nigdy identyfikatora pilota. Miary dla każdej talii:

- **Piloci_Top32 / Piloci_Top8** — liczba różnych pilotów z co najmniej jedną listą talii w Top 32 /
  w Top 8;
- **najczęstszy pilot** talii = pilot z największą liczbą list talii w Top 32 okresu; remis
  rozstrzyga większa liczba list Top 8, potem identyfikator `player` (tylko po to, żeby wynik był
  powtarzalny; liczba remisów jest w kolumnie `Remisy_najcz_Top32`);
- **udział najczęstszego pilota w listach Top 32** = jego listy / listy talii;
- **udział w wejściach do Top 8** podany na dwa sposoby, bo polecenie nie przesądza, o którego
  pilota chodzi: `Ten_pilot_udzial_Top8_pct` — ten sam pilot (najczęstszy w Top 32), jego listy Top 8
  / listy Top 8 talii; `Najcz_pilot_Top8_udzial_pct` — pilot z największą liczbą list Top 8 talii,
  niezależnie od Top 32. Różnią się w 7 z 32 talii (np. Broodscale Combo 1,4% vs 4,3%);
- **konwersja talii bez najczęstszego pilota** (listy pozostałych pilotów) obok **konwersji samego
  najczęstszego pilota** (`Najcz_pilot_konwersja_pct`, na `Najcz_pilot_listy` listach).

**Zmierzone na okresie 2026-05-19 … 2026-10-18**, talie z co najmniej 30 listami (32 talie, bez
„bez talii”), skrypt `scripts/deckdb_methodology_stats.py --since 2026-05-19`:

- Talie najczęściej grane mają rozproszonych pilotów: Broodscale Combo 262 pilotów, najczęstszy
  ma 3,2% list; Energy 294 i 3,6%; Prowess 268 i 5,1%.
- Skrajna koncentracja: **Mono-Black Midrange** — 5 pilotów, najczęstszy ma 84,7% list (50 z 59)
  i 100% wejść do Top 8 (16 z 16); konwersja pozostałych pilotów 0,0% (9 list). Konwersja tej talii
  jest w praktyce konwersją jednego pilota. Dalej Burn (41,2% list; jego konwersja 10,0% przy 19,3%
  reszty), Izzet Murktide (27,0% list, 60% wejść do Top 8), Esper Ketra (22,2%), Ruby Storm
  (20,1%), Living End (19,2% list, 27,9% wejść do Top 8).
- Najczęstszy pilot wyraźnie podnosi konwersję talii m.in. w Goryo's Vengeance (48,8% vs 22,8%
  reszty), Control (50,0% vs 20,5%), Belcher (50,0% vs 23,8%), Amulet Titan (48,1% vs 25,1%),
  Reanimator (47,4% vs 21,2%).
- Remis najczęstszego pilota w Top 32: Control i Mill (po 2 piloci z równą liczbą list).

Tabela (udziały i konwersje w %):

| Talia | Listy | Top8 | Piloci_Top32 | Piloci_Top8 | Najcz_pilot_udzial_Top32_pct | Remisy_najcz_Top32 | Ten_pilot_udzial_Top8_pct | Najcz_pilot_Top8_udzial_pct | Konwersja_pct | Najcz_pilot_listy | Najcz_pilot_konwersja_pct | Bez_najcz_pilota_konwersja_pct |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Broodscale Combo | 787 | 208 | 262 | 113 | 3.2 | 1 | 1.4 | 4.3 | 26.4 | 25 | 12.0 | 26.9 |
| Energy | 704 | 185 | 294 | 98 | 3.6 | 1 | 4.3 | 7.0 | 26.3 | 25 | 32.0 | 26.1 |
| Prowess | 625 | 153 | 268 | 97 | 5.1 | 1 | 5.9 | 5.9 | 24.5 | 32 | 28.1 | 24.3 |
| Goryo's Vengeance | 523 | 130 | 222 | 74 | 7.8 | 1 | 15.4 | 15.4 | 24.9 | 41 | 48.8 | 22.8 |
| Blink | 504 | 132 | 211 | 76 | 4.8 | 1 | 8.3 | 8.3 | 26.2 | 24 | 45.8 | 25.2 |
| Affinity | 473 | 115 | 178 | 68 | 5.5 | 1 | 4.3 | 4.3 | 24.3 | 26 | 19.2 | 24.6 |
| Tron | 385 | 94 | 149 | 55 | 8.8 | 1 | 10.6 | 10.6 | 24.4 | 34 | 29.4 | 23.9 |
| Ruby Storm | 344 | 86 | 133 | 43 | 20.1 | 1 | 24.4 | 24.4 | 25.0 | 69 | 30.4 | 23.6 |
| Eldrazi Ramp | 310 | 88 | 145 | 49 | 17.1 | 1 | 17.0 | 17.0 | 28.4 | 53 | 28.3 | 28.4 |
| Devoted Combo | 293 | 74 | 116 | 48 | 6.1 | 1 | 4.1 | 6.8 | 25.3 | 18 | 16.7 | 25.8 |
| Prison | 273 | 74 | 96 | 41 | 14.7 | 1 | 14.9 | 14.9 | 27.1 | 40 | 27.5 | 27.0 |
| Land Destruction | 270 | 71 | 124 | 39 | 7.8 | 1 | 14.1 | 14.1 | 26.3 | 21 | 47.6 | 24.5 |
| Reanimator | 260 | 60 | 104 | 32 | 7.3 | 1 | 15.0 | 15.0 | 23.1 | 19 | 47.4 | 21.2 |
| Dimir Midrange | 248 | 65 | 107 | 38 | 7.7 | 1 | 6.2 | 12.3 | 26.2 | 19 | 21.1 | 26.6 |
| Living End | 245 | 68 | 61 | 20 | 19.2 | 1 | 27.9 | 27.9 | 27.8 | 47 | 40.4 | 24.7 |
| Zoo | 242 | 64 | 105 | 32 | 8.7 | 1 | 15.6 | 15.6 | 26.4 | 21 | 47.6 | 24.4 |
| Amulet Titan | 194 | 55 | 62 | 22 | 13.9 | 1 | 23.6 | 23.6 | 28.4 | 27 | 48.1 | 25.1 |
| Neobrand | 157 | 35 | 88 | 28 | 9.6 | 1 | 14.3 | 14.3 | 22.3 | 15 | 33.3 | 21.1 |
| Belcher | 152 | 43 | 49 | 19 | 17.1 | 1 | 30.2 | 30.2 | 28.3 | 26 | 50.0 | 23.8 |
| Hollow One | 133 | 29 | 64 | 24 | 11.3 | 1 | 3.4 | 6.9 | 21.8 | 15 | 6.7 | 23.7 |
| Yawgmoth | 104 | 31 | 42 | 20 | 15.4 | 1 | 19.4 | 19.4 | 29.8 | 16 | 37.5 | 28.4 |
| Control | 103 | 27 | 48 | 16 | 19.4 | 2 | 37.0 | 37.0 | 26.2 | 20 | 50.0 | 20.5 |
| Esper Ketra | 99 | 22 | 47 | 14 | 22.2 | 1 | 22.7 | 22.7 | 22.2 | 22 | 22.7 | 22.1 |
| Emry | 98 | 20 | 54 | 15 | 9.2 | 1 | 10.0 | 20.0 | 20.4 | 9 | 22.2 | 20.2 |
| Burn | 97 | 15 | 36 | 10 | 41.2 | 1 | 26.7 | 26.7 | 15.5 | 40 | 10.0 | 19.3 |
| Rakdos Moonshadow | 83 | 29 | 50 | 19 | 12.0 | 1 | 17.2 | 17.2 | 34.9 | 10 | 50.0 | 32.9 |
| Necrodominance | 79 | 19 | 44 | 15 | 13.9 | 1 | 15.8 | 15.8 | 24.1 | 11 | 27.3 | 23.5 |
| Plunge into Cosmogoyf | 63 | 11 | 23 | 8 | 15.9 | 1 | 18.2 | 18.2 | 17.5 | 10 | 20.0 | 17.0 |
| Mono-Black Midrange | 59 | 16 | 5 | 1 | 84.7 | 1 | 100.0 | 100.0 | 27.1 | 50 | 32.0 | 0.0 |
| Izzet Murktide | 37 | 10 | 18 | 5 | 27.0 | 1 | 60.0 | 60.0 | 27.0 | 10 | 60.0 | 14.8 |
| Mill | 33 | 4 | 16 | 3 | 18.2 | 2 | 50.0 | 50.0 | 12.1 | 6 | 33.3 | 7.4 |
| Hammer Time | 32 | 4 | 17 | 3 | 21.9 | 1 | 0.0 | 50.0 | 12.5 | 7 | 0.0 | 16.0 |
