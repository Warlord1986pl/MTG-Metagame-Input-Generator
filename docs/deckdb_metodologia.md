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

## (e) Przedział ufności konwersji: bootstrap po pilotach

**Metoda.** Listy jednego pilota nie są niezależne (ten sam gracz, ta sama umiejętność, często ta
sama lista), dlatego jednostką losowania jest pilot, nie lista. Dla talii z P pilotami w okresie:

1. dla każdego pilota policz jego listy talii n_p i listy Top 8 t_p;
2. wylosuj ze zwracaniem P pilotów (pilot może wystąpić kilka razy, wtedy wnosi wszystkie swoje
   listy tyle razy);
3. konwersja próby = Σ t_p / Σ n_p po wylosowanych pilotach;
4. powtórz B = 2000 razy; przedział 95% = percentyle 2,5 i 97,5 rozkładu konwersji prób.

Ziarno generatora jest stałe (`--seed`, domyślnie 20261021), więc wynik jest powtarzalny. Wariant
procentylowy, bez korekty BCa. Eventy nie są losowane (polecenie: piloci).

**Czas obliczenia** (okres 2026-05-19 … 2026-10-18, 8387 list, 32 talie z co najmniej 30 listami,
ten komputer): wczytanie eksportu i wybór okresu 0,13 s; bootstrap B = 2000 dla 32 talii
0,13–0,14 s; B = 10 000: 0,28 s. Koszt jest pomijalny także dla wszystkich talii i archetypów.

**Porównanie z Wilsonem** (Wilson 95% na listach, czyli dzisiejszy przedział z adnotacją
„zaniżony”). Iloraz szerokości bootstrap / Wilson przy B = 2000: mediana 1,07, zakres 0,87–1,42;
w 12 z 32 talii bootstrap jest szerszy o ponad 20%, w 9 węższy. Szerszy jest tam, gdzie piloci są
skoncentrowani: Living End (61 pilotów, 1,42), Mono-Black Midrange (5 pilotów, 1,42), Goryo's
Vengeance (1,42), Control (1,33), Izzet Murktide (1,30). W taliach z setkami pilotów i małym
udziałem najczęstszego (Prowess, Affinity, Tron, Devoted Combo) oba przedziały są praktycznie
równe — zaniżenie Wilsona jest tam niewielkie. Kontrola stabilności: inne ziarno (B = 2000) daje
medianę 1,08 (zakres 0,90–1,44), B = 10 000 medianę 1,08 (0,89–1,47); pojedyncze ilorazy wahają się
o kilka setnych, więc talie z ilorazem około 1 nie różnią się istotnie.

Ograniczenie: przy bardzo małej liczbie pilotów bootstrap jest zawodny. Mono-Black Midrange (5
pilotów) dostaje przedział 0,0–31,6%, wyznaczony głównie przez to, czy w próbie znalazł się
najczęstszy pilot (lub jego brak); to nie jest wiarygodny przedział, tylko sygnał, że konwersja
talii to konwersja jednego gracza. Patrz „nierozstrzygnięte” niżej.

Tabela (B = 2000, ziarno 20261021; konwersja i granice w %, szerokości w pp):

| Talia | Listy | Piloci | Konwersja_pct | Bootstrap_dol | Bootstrap_gora | Wilson_dol | Wilson_gora | Szer_bootstrap_pp | Szer_Wilson_pp | Iloraz_szerokosci |
|---|---|---|---|---|---|---|---|---|---|---|
| Broodscale Combo | 787 | 262 | 26.43 | 22.74 | 30.19 | 23.47 | 29.62 | 7.45 | 6.15 | 1.21 |
| Energy | 704 | 294 | 26.28 | 22.11 | 30.40 | 23.16 | 29.65 | 8.29 | 6.49 | 1.28 |
| Prowess | 625 | 268 | 24.48 | 21.17 | 27.87 | 21.27 | 28.00 | 6.70 | 6.73 | 1.00 |
| Goryo's Vengeance | 523 | 222 | 24.86 | 19.63 | 30.13 | 21.34 | 28.73 | 10.50 | 7.39 | 1.42 |
| Blink | 504 | 211 | 26.19 | 21.77 | 30.24 | 22.54 | 30.20 | 8.47 | 7.66 | 1.11 |
| Affinity | 473 | 178 | 24.31 | 20.75 | 28.06 | 20.66 | 28.38 | 7.31 | 7.71 | 0.95 |
| Tron | 385 | 149 | 24.42 | 20.16 | 28.53 | 20.39 | 28.95 | 8.37 | 8.55 | 0.98 |
| Ruby Storm | 344 | 133 | 25.00 | 19.10 | 28.83 | 20.72 | 29.83 | 9.73 | 9.12 | 1.07 |
| Eldrazi Ramp | 310 | 145 | 28.39 | 23.27 | 33.54 | 23.66 | 33.65 | 10.27 | 9.99 | 1.03 |
| Devoted Combo | 293 | 116 | 25.26 | 20.52 | 30.32 | 20.62 | 30.53 | 9.80 | 9.91 | 0.99 |
| Prison | 273 | 96 | 27.11 | 21.72 | 32.43 | 22.18 | 32.67 | 10.71 | 10.49 | 1.02 |
| Land Destruction | 270 | 124 | 26.30 | 19.68 | 32.34 | 21.40 | 31.85 | 12.67 | 10.45 | 1.21 |
| Reanimator | 260 | 104 | 23.08 | 16.67 | 29.24 | 18.37 | 28.57 | 12.57 | 10.20 | 1.23 |
| Dimir Midrange | 248 | 107 | 26.21 | 20.47 | 32.08 | 21.13 | 32.02 | 11.62 | 10.89 | 1.07 |
| Living End | 245 | 61 | 27.76 | 17.87 | 33.68 | 22.52 | 33.67 | 15.81 | 11.15 | 1.42 |
| Zoo | 242 | 105 | 26.45 | 19.62 | 31.80 | 21.29 | 32.34 | 12.18 | 11.05 | 1.10 |
| Amulet Titan | 194 | 62 | 28.35 | 19.08 | 34.91 | 22.48 | 35.07 | 15.83 | 12.59 | 1.26 |
| Neobrand | 157 | 88 | 22.29 | 16.20 | 28.17 | 16.49 | 29.42 | 11.97 | 12.93 | 0.93 |
| Belcher | 152 | 49 | 28.29 | 18.55 | 36.12 | 21.73 | 35.92 | 17.58 | 14.18 | 1.24 |
| Hollow One | 133 | 64 | 21.80 | 15.20 | 29.79 | 15.63 | 29.56 | 14.58 | 13.93 | 1.05 |
| Yawgmoth | 104 | 42 | 29.81 | 21.92 | 37.98 | 21.86 | 39.19 | 16.06 | 17.33 | 0.93 |
| Control | 103 | 48 | 26.21 | 15.25 | 37.50 | 18.69 | 35.45 | 22.25 | 16.77 | 1.33 |
| Esper Ketra | 99 | 47 | 22.22 | 14.70 | 28.74 | 15.16 | 31.36 | 14.03 | 16.20 | 0.87 |
| Emry | 98 | 54 | 20.41 | 12.77 | 28.42 | 13.62 | 29.43 | 15.66 | 15.81 | 0.99 |
| Burn | 97 | 36 | 15.46 | 10.00 | 26.56 | 9.60 | 23.96 | 16.57 | 14.36 | 1.15 |
| Rakdos Moonshadow | 83 | 50 | 34.94 | 23.61 | 44.09 | 25.56 | 45.66 | 20.47 | 20.10 | 1.02 |
| Necrodominance | 79 | 44 | 24.05 | 14.77 | 33.80 | 15.97 | 34.54 | 19.03 | 18.56 | 1.03 |
| Plunge into Cosmogoyf | 63 | 23 | 17.46 | 9.76 | 26.57 | 10.04 | 28.62 | 16.81 | 18.58 | 0.90 |
| Mono-Black Midrange | 59 | 5 | 27.12 | 0.00 | 31.58 | 17.44 | 39.60 | 31.58 | 22.16 | 1.42 |
| Izzet Murktide | 37 | 18 | 27.03 | 7.69 | 43.48 | 15.40 | 42.98 | 35.79 | 27.58 | 1.30 |
| Mill | 33 | 16 | 12.12 | 0.00 | 23.68 | 4.82 | 27.33 | 23.68 | 22.51 | 1.05 |
| Hammer Time | 32 | 17 | 12.50 | 0.00 | 29.17 | 4.97 | 28.07 | 29.17 | 23.10 | 1.26 |

## (f) Obecne materiały: ocena

Stan na commit `7f933c2` (katalog `outputs/deckdb_robocze/ROBOCZE_<okno>/`). „Zostaje” = bez
zmian w treści, najwyżej z poprawionym okresem; „zmienia się” = opis zmiany; „odpada” = materiał
nie jest generowany.

### Wykresy

| Materiał | Ocena | Jak |
|---|---|---|
| `udzial_top32_talie.png`, `udzial_top32_archetypy.png` | zmienia się | jeden wykres na tydzień ISO i jeden na miesiąc zamiast dowolnego okna; udział + obecność bez zmian w definicji; dochodzi Top 8 (liczba list, % eventów) |
| `konwersja_talie.png`, `konwersja_archetypy.png` | zmienia się | tylko poziom okresu formatu, narastająco; kolumny: listy, listy Top 8, konwersja [przedział bootstrap po pilotach], oczekiwana konwersja, konwersja − oczekiwana (skala rozbieżna wokół 0), trofea, trofea / listy, zwycięstwa % eventów, piloci w Top 32 / Top 8, udział najczęstszego pilota; udział i obecność przechodzą do wykresów tygodnia i miesiąca; adnotacja o zaniżonym przedziale Wilsona znika razem z Wilsonem (o ile bootstrap zostanie przyjęty) |
| `winrate_szwajcarski_talie.png`, `winrate_szwajcarski_archetypy.png` | odpada | decyzja: winrate nie jest podawany |
| `trend_udzial_talie_last12w.png`, `trend_udzial_archetypy_last12w.png` | zmienia się | 12 rozłącznych tygodni ISO zamiast nakładających się okien 14-dniowych; oś X = tydzień ISO (np. 2026-W42) |
| — (nowy) | dochodzi | wykres pilotów okresu formatu: dla talii z co najmniej 30 listami konwersja najczęstszego pilota obok konwersji reszty (punkt d) — do decyzji, czy wykres, czy tylko arkusz |

### Arkusze skoroszytu `deckdb_statistics_ROBOCZE_*.xlsx`

| Arkusz | Ocena | Jak |
|---|---|---|
| STATUS | zmienia się | okres i poziom czasu, eventy, listy, pokrycie, rules_version, sha256 bez zmian; bazę wspólną i datę początku okresu formatu (z `format_events.yaml`, z kluczem wpisu) podaje się na poziomie okresu; wpisy o winrate i bye odpadają; dochodzi opis bootstrapu (B, ziarno) |
| Talie, Archetypy | zmienia się | rozdzielone na trzy poziomy: `Talie_tydzien` / `Archetypy_tydzien`, `Talie_miesiac` / `Archetypy_miesiac` (udział, obecność, Top 8) i `Talie_okres` / `Archetypy_okres` (konwersja, oczekiwana konwersja, przedziały, trofea, miary pilotów); kolumny `Wygrane_szw`, `Przegrane_szw`, `Mecze_szw`, `Winrate_szw_pct`, `Wilson95_*`, `Listy_z_bye` odpadają; `Konwersja_Wilson95_*` zastępuje bootstrap |
| Warianty | zostaje | już liczony per miesiąc według D2; przechodzi do poziomu miesiąca, z oznaczeniem miesiąca niepełnego |
| Bez_talii_rozbicie | zmienia się | jak Talie: wiersze Rogue i nieprzypisane osobno na każdym poziomie czasu; bez winrate |
| Eventy | zostaje | dochodzi kolumna tygodnia ISO i miesiąca |
| Trend_talie, Trend_archetypy | zmienia się | tygodnie ISO rozłączne (jak wykres trendu) |
| Pominiete | zmienia się | dochodzą: winrate szwajcarski (decyzja 2026-10-21) i rekord szwajcarski / bye; pozostałe pozycje bez zmian |
| — (nowy) `Piloci_okres` | dochodzi | miary z punktu d dla wszystkich talii okresu (próg 30 list tylko dla tabeli w tym dokumencie, w materiale do ustalenia), bez identyfikatorów pilotów |

### Pliki CSV

| Plik | Ocena | Jak |
|---|---|---|
| `talie_ROBOCZE_*.csv`, `archetypy_ROBOCZE_*.csv` | zmienia się | jeden plik na poziom czasu, kolumny jak odpowiednie arkusze; bez kolumn winrate i bye |
| `finalisci_ROBOCZE_*.csv` | zostaje | talie miejsca 1 i 2 na event, bez pilotów |
| `udzial_top32_historia_ROBOCZE_*.csv` | zmienia się | tygodnie ISO rozłączne |

## Nierozstrzygnięte (do decyzji przy akceptacji)

1. Przedział konwersji przy małej liczbie pilotów: od jakiej liczby pilotów bootstrap jest
   podawany (np. co najmniej 10), i co pokazać poniżej progu — sam Wilson z adnotacją, brak
   przedziału czy tylko informację o koncentracji pilotów.
2. Który „udział najczęstszego pilota w wejściach do Top 8” jest miarą materiału: tego samego
   pilota co w Top 32, czy pilota z największą liczbą list Top 8 (w tym dokumencie oba).
3. Trofea i zwycięstwa jako % eventów: polecenie przypisuje do poziomów udział, obecność, Top 8,
   konwersję i miary pilotów; trofea są tu umieszczone w okresie formatu obok konwersji —
   do potwierdzenia, czy mają być też tygodniowo i miesięcznie.
4. Miary pilotów dla archetypów (agregat list talii) — czy podawać.
5. Tydzień ISO przecinający granicę miesiąca należy do obu poziomów niezależnie; miesiąc i
   okres formatu liczą się z dat eventów, nie z tygodni. Do potwierdzenia, że to zamierzone.
6. Okres formatu zaczyna się od `effective_mtgo` (2026-05-19), nie od `announced`
   (2026-05-18). Eksport ma 64 listy z 2026-05-18, rozegrane jeszcze przed zmianą na MTGO, więc
   start od `effective_mtgo` wydaje się właściwy; do potwierdzenia.

## (g) Warstwa kart — tylko specyfikacja

Nic z tej sekcji nie jest zaimplementowane. Jedyne liczby to ilustracja dla jednej talii
(g.6), policzona jednorazowo poza repozytorium, bez korekty na wielokrotne porównania.

### g.1 Jakich danych o kartach to wymaga

Dla każdej listy z eksportu przypisań (klucz `event_id`, `decktournamentid`) i każdej strefy
(`main`, `side`): lista kart jako `oracle_id` z liczbą kopii, przy czym:

- **normalizacja po `oracle_id`:** liczba kopii karty w strefie to suma `qty` po wszystkich
  wierszach z tym samym `oracle_id`, nigdy `qty` jednego wiersza i nigdy suma po `docid`. `docid`
  MTGO identyfikuje druk, a gracz potrafi mieć tę samą kartę w kilku drukach w jednej strefie
  (DeckDB `docs/schema.md`: 4504 z 154 624 grup (lista, strefa, nazwa) ma więcej niż jeden
  `docid`, np. Watery Grave pod trzema);
- **druki pod inną nazwą:** karty z setu MTGO Through the Omenpaths (OM1) to cyfrowe odpowiedniki
  kart Marvel's Spider-Man pod innymi nazwami. Dopasowanie po samej nazwie MTGO traktuje je jako
  inne karty niż odpowiednik; dopiero mapowanie nazw → `oracle_id` (DeckDB `card_name_map`,
  `name_match_version` 2, 21 nazw OM1) je łączy. To samo dotyczy zapisu kart dzielonych i
  dwustronnych (np. „Fire/Ice” → „Fire // Ice”);
- **lądy podstawowe:** rozpoznawane po typie karty (supertyp Basic i typ Land, z danych Scryfalla
  dla `oracle_id`), nie po liście nazw. Domyślnie wyłączone ze wszystkich miar warstwy kart
  (obecność, zmiana, pierwsze pojawienie, zmiany list pilota), bo odzwierciedlają bazę many, nie
  wybór kart. Rozbieżność do rozstrzygnięcia: obecny pipeline wyłącza tylko Plains, Island, Swamp,
  Mountain, Forest i Wastes (`challenge_mtgo_source.BASIC_LAND_NAMES`), a definicja po typie
  wyłącza także lądy Snow-Covered, które bywają świadomym wyborem;
- **wersje:** każda tabela kart musi mieć wersję mapowania nazw (`name_match_version`) i zrzut
  Scryfalla, z którego pochodzi `oracle_id`; materiał podaje je obok `rules_version` i sha256;
- z eksportu przypisań, bez zmian: `deck`, `variant`, `player`, `event_date`, `final_rank`.
  Klucz listy musi się zgadzać 1:1 między plikiem kart a plikiem przypisań z tego samego
  przebiegu; niedopasowania w obie strony są raportowane, a nie pomijane.

### g.2 Obecność karty w talii

Dla talii D, okresu T, strefy z ∈ {main, side} i karty c (`oracle_id`), na listach D w T:

- **obecność** p(c, z) = listy D z co najmniej jedną kopią c w strefie z / wszystkie listy D;
- **średnia liczba kopii** m(c, z) = suma kopii c w strefie z / listy D z co najmniej jedną kopią
  (średnia tylko po listach z kartą, nie po wszystkich listach);
- maindeck i sideboard zawsze osobno; karta w obu strefach jednej listy liczy się w każdej z nich.
  Zbiorcza obecność „w dowolnej strefie” nie jest miarą podstawową;
- materiał podaje też liczbę list i pilotów D w T oraz liczbę pilotów z kartą (koncentracja:
  karta grana przez jednego pilota to jego wybór, nie obecność w talii).

Okres T to poziom miesiąca z punktu (b) (cykl analizy zmian list jest miesięczny według
metodologii DeckDB), z możliwością okresu formatu jako całości.

### g.3 Zmiana obecności między okresami

- **zmiana** Δ = p_T2 − p_T1 w punktach procentowych (najpierw dla kolejnych miesięcy; przy
  dużym zdarzeniu formatu porównanie nie przekracza jego daty, okresy są do niej wyrównane);
- **przedział:** bootstrap po pilotach, osobno w każdym okresie: w T1 losuje się ze zwracaniem
  tylu pilotów D, ilu D ma w T1, i bierze wszystkie ich listy; tak samo w T2; Δ próby = różnica
  obecności; B = 2000 (ziarno stałe), przedział 95% = percentyle 2,5 i 97,5. Pilot grający D w obu
  okresach jest losowany niezależnie w każdym (para nie jest zachowywana — do decyzji, g.8);
- **rozkład na warianty:** z wariantów eksportu (pusty → „bez wariantu”; wszystkie etykiety, nie
  tylko raportowane według D2, bo inaczej rozkład nie sumuje się do Δ): p_T = Σ_v w_v,T · p_v,T,
  gdzie w_v,T to udział wariantu v w listach D w T. Rozkład symetryczny (średnie z obu okresów):
  - zmiana wewnątrz wariantów = Σ_v ((w_v,T1 + w_v,T2) / 2) · (p_v,T2 − p_v,T1);
  - zmiana proporcji wariantów = Σ_v ((p_v,T1 + p_v,T2) / 2) · (w_v,T2 − w_v,T1);
  - obie części sumują się dokładnie do Δ. Wariant obecny tylko w jednym okresie ma w drugim
    nieokreśloną obecność; przyjmuje się tę z okresu, w którym jest, więc cały jego wkład trafia do
    zmiany proporcji. Materiał podaje obie części jako dwie liczby; rozbicie na pojedyncze warianty
    tylko dla wariantów raportowanych według D2.

### g.4 Próg raportowania zmiany

Zmiana karty jest raportowana, gdy jednocześnie:

1. talia ma w **każdym** z dwóch okresów co najmniej **30 list** i co najmniej **10 różnych
   pilotów** (z tabel (d) i (e): poniżej około 10 pilotów bootstrap po pilotach jest zawodny);
2. |Δ| ≥ **10 pp**;
3. zmiana przechodzi kontrolę wielokrotnych porównań: **Benjamini–Hochberg, FDR q = 0,10**.
   Rodzina = wszystkie pary (talia, strefa, karta) spełniające warunek 1 w danym porównaniu
   okresów (we wszystkich taliach naraz), niezależnie od wielkości Δ, żeby filtr wielkości nie
   zmieniał rodziny. Wartość p dwustronna z bootstrapu przez przybliżenie normalne
   (z = Δ / błąd standardowy z prób bootstrapu), a nie z liczenia prób po drugiej stronie zera:
   rodzina ma rząd kilku tysięcy porównań (około 30 talii × 200 par strefa–karta), a przy B = 2000
   najmniejsza wartość p z liczenia prób to 1/2000, za mało na próg BH przy takiej rodzinie.

Karty spełniające 1–2, ale nie 3, mogą trafić do pliku roboczego jako niepotwierdzone, nigdy do
materiału.

### g.5 Pierwsze pojawienie się karty i zmiany list pilota

**Pierwsze pojawienie się karty c w talii D** (osobno main i side): najwcześniejsza `event_date`
listy D (status `assigned`) z co najmniej jedną kopią c. Materiał podaje datę, miejsce tej listy
oraz liczbę list i pilotów D z c w 30 dniach od tej daty (żeby odróżnić pojedynczy eksperyment od
przyjęcia karty). Zastrzeżenia:

- data zależy od wersji reguł (lista może zmienić talię przy nowych regułach), więc jest
  podawana z `rules_version` i liczona od nowa przy każdym eksporcie;
- pierwsze pojawienie w pierwszych 30 dniach danych (od 2025-07-01) oznacza „przed początkiem
  danych”, nie datę debiutu;
- dla kart z nowego setu obok podaje się datę wydania setu na MTGO z `rules/format_events.yaml`.

**Zmiany list u tego samego pilota:** dla pilota i talii D kolejne opublikowane listy D tego
pilota, w porządku daty i eventu. Dla pary kolejnych list: suma |różnic liczby kopii| po
(strefa, `oracle_id`), osobno main i side, plus liczba kart dodanych i usuniętych oraz liczba
przeniesień main ↔ side (karta, której ubyło w jednej strefie i przybyło w drugiej). Zastrzeżenia:
widać tylko listy z Top 32, więc „kolejne” znaczy kolejne opublikowane, nie kolejne rozegrane;
podaje się odstęp w dniach. Para, w której druga lista jest inną talią, to zmiana talii, nie
zmiana listy, i liczy się osobno. Materiał podaje tylko rozkład dla talii (mediana, kwartyle,
odsetek par bez zmian), nigdy wiersze poszczególnych pilotów.

### g.6 ILUSTRACJA (bez korekty na wielokrotne porównania): Blink, wrzesień vs październik 2026

**To jest ilustracja, nie materiał.** Okresy: 2026-09-01 … 09-30 i 2026-10-01 … 10-18 (październik
niepełny); karty z cache'u MTGO po nazwie MTGO (bez `oracle_id`, kopie zsumowane po drukach tej
samej nazwy), lądy podstawowe wyłączone razem ze Snow-Covered; B = 2000. Wszystkie 181 list Blink
z obu okresów ma karty w cache'u.

- wrzesień: 141 list, 78 pilotów, warianty Esper Blink 112, Jeskai Blink 21, Blink: inne 6,
  Azorius Blink 2; październik: 40 list, 26 pilotów, Jeskai Blink 21, Esper Blink 14, Blink: inne
  3, Azorius Blink 2;
- 199 par (strefa, karta); 46 z |Δ| ≥ 10 pp, z czego 43 mają przedział bootstrapu bez zera
  (**bez korekty BH**, więc nie wiadomo, ile przeszłoby próg g.4); czas obliczenia 1,3 s;
- obraz zmiany to przede wszystkim przesunięcie proporcji wariantów (Esper → Jeskai), nie zmiana
  list wewnątrz wariantów: karty pakietu Esper tracą około 40 pp prawie wyłącznie przez proporcje,
  karty Jeskai zyskują 35–40 pp tak samo. Zmiany wewnątrz wariantów widać w kilku kartach, np.
  Ashiok, Dream Render w sideboardzie (+53,7 pp, z czego +44,9 wewnątrz wariantów), Elesh Norn,
  Mother of Machines w sideboardzie (−24,5; wewnątrz −19,5), Rest in Peace w maindecku i Test of
  Talents w sideboardzie (po +42,5; wewnątrz +27,3), Ephemerate w maindecku (−37,5: −16,0 wewnątrz,
  −21,5 proporcje).

| Strefa | Karta | Obecność IX % | Obecność X % | Śr. kopii IX | Śr. kopii X | Δ pp | Bootstrap 95% | Wewnątrz wariantów pp | Proporcje wariantów pp |
|---|---|---|---|---|---|---|---|---|---|
| main | Watery Grave | 78,7 | 35,0 | 1,0 | 1,0 | −43,7 | −63,4 … −21,3 | 0,5 | −44,2 |
| main | Overlord of the Balemurk | 80,9 | 37,5 | 4,0 | 4,0 | −43,4 | −63,0 … −20,9 | 0,0 | −43,4 |
| main | Ephemerate | 95,0 | 57,5 | 2,6 | 2,5 | −37,5 | −57,3 … −14,2 | −16,0 | −21,5 |
| side | Elesh Norn, Mother of Machines | 27,0 | 2,5 | 1,0 | 1,0 | −24,5 | −35,2 … −13,7 | −19,5 | −5,0 |
| side | Nihil Spellbomb | 29,1 | 12,5 | 1,6 | 1,4 | −16,6 | −31,4 … 1,9 | −3,1 | −13,5 |
| side | Containment Priest | 17,7 | 5,0 | 1,4 | 1,5 | −12,7 | −22,9 … −2,4 | −21,7 | 9,0 |
| main | Ragavan, Nimble Pilferer | 14,2 | 47,5 | 3,7 | 3,2 | 33,3 | 8,1 … 54,7 | −1,6 | 34,9 |
| main | Galvanic Discharge | 11,3 | 52,5 | 3,9 | 4,0 | 41,2 | 18,1 … 61,3 | 8,0 | 33,1 |
| main | Rest in Peace | 0,0 | 42,5 | — | 1,0 | 42,5 | 20,0 … 61,5 | 27,3 | 15,2 |
| side | Test of Talents | 0,0 | 42,5 | — | 1,1 | 42,5 | 21,0 … 61,4 | 27,3 | 15,2 |
| main | Fable of the Mirror-Breaker | 5,0 | 50,0 | 2,0 | 2,2 | 45,0 | 22,7 … 64,4 | 20,9 | 24,2 |
| side | Ashiok, Dream Render | 16,3 | 70,0 | 1,2 | 1,2 | 53,7 | 32,4 … 72,7 | 44,9 | 8,8 |

Ta sama ilustracja dla pozostałych definicji, na listach Blink z kartami w cache'u (cache zaczyna
się 2026-07-01, eksport 2025-07-01: karty ma tylko 365 z 2166 list Blink w eksporcie):

- pierwsze pojawienie: 23 karty mają pierwszą listę Blink w październiku (np. Test of Talents
  2026-10-04, Teyo, Lightshield Expert 2026-10-11, Path to Exile 2026-10-17). Przez brak kart dla
  list sprzed lipca 2026 to nie są daty debiutu — to właśnie cenzurowanie, które usuwa pełne
  źródło (g.7);
- listy pilota: 75 pilotów ma co najmniej 2 listy Blink, 205 par kolejnych list; suma |zmian
  kopii| (main + side, bez lądów podstawowych): mediana 5, kwartyle 0 i 12; 65 par bez zmian.

Wnioski z ilustracji dla specyfikacji: (1) bez rozkładu na warianty zmiana obecności kart myli
zmianę proporcji wariantów ze zmianą list i w Blink przypisałaby kilkudziesięciu kartom zmianę,
której wewnątrz wariantów nie ma; (2) przy 40 listach i 26 pilotach przedziały mają szerokość
około 40 pp, więc próg 10 pp jest minimum, a nie typowym rozmiarem wykrywalnej zmiany; (3) 46
kandydatów z jednej talii pokazuje, że bez korekty na wielokrotne porównania materiał byłby
listą fałszywych alarmów.

### g.7 Źródło danych o kartach: cache MTGO w pipelinie albo eksport kart z DeckDB

| | A. Łączenie z cache'em MTGO w pipelinie | B. Eksport kart z DeckDB |
|---|---|---|
| Zasięg | cache od 2026-07-01 (dla Blink karty ma 365 z 2166 list eksportu); starsze eventy wymagałyby ponownego pobrania z mtgo.com | wszystkie listy eksportu przypisań (742 Challenge od 2025-07-01, 23 715 list) |
| Tożsamość karty | nazwa MTGO z `card_attributes.card_name`, bez `oracle_id`; OM1 i zapis kart dzielonych wymagałyby własnego mapowania w pipelinie | `oracle_id` z wersjonowanego `card_name_map` (`name_match_version` 2, w tym 21 nazw OM1) |
| Druki | suma `qty` po wierszach o tej samej nazwie (działa dla zwykłych druków, nie dla OM1) | suma po `oracle_id` — reguła agregacji DeckDB, ta sama, na której stoi klasyfikator |
| Lądy podstawowe | lista nazw (dziś 6 nazw, bez Snow-Covered) | typ karty ze zrzutu Scryfalla |
| Spójność z przypisaniami | dwa niezależne odczyty tych samych list; łączenie po (`event_id`, `decktournamentid`) działa (październik: 903/903 w obie strony), ale nic nie gwarantuje, że lista w cache'u jest tą samą wersją, z której DeckDB liczył przypisanie | ta sama baza i ten sam przebieg co przypisania; jedna wersja reguł i mapowania nazw w obu plikach |
| Koszt | łączenie gotowe (`deckdb_input.match_mtgo_cache`), ale mapowanie nazw → `oracle_id` byłoby drugą, niezależną implementacją | nie istnieje: w dokumentacji DeckDB nie znalazłem polecenia eksportu kart; wymaga pracy po stronie DeckDB i specyfikacji formatu; rząd wielkości około 790 tys. wierszy (około 33 wiersze na listę według `docs/schema.md`) |
| Identyfikacja pilota | `loginid` i nick są w cache'u — ryzyko, że trafią do obliczeń | tylko `player` (ta sama sól co w eksporcie przypisań) |

**Propozycja:** źródło B jako jedyne źródło warstwy kart. Plik obok eksportu przypisań, z tego
samego przebiegu, kolumny: `event_id`, `decktournamentid`, `zone` (`main`/`side`), `oracle_id`,
`card_name` (nazwa kanoniczna), `qty` (suma po drukach), `is_basic_land`, `name_match_version`,
`scryfall_dump_id`, `rules_version`. Źródło A tylko jako kontrola spójności (liczba niedopasowanych
list i różnic liczby kopii po nazwach), tak jak dziś dla przypisań. Pipeline odmawia pracy, gdy
wersje reguł albo mapowania nazw w obu plikach się różnią.

### g.8 Nierozstrzygnięte w warstwie kart

1. Czy lądy Snow-Covered (i inne lądy z supertypem Basic) wyłączać razem z podstawowymi.
2. Bootstrap zmiany: niezależne losowanie pilotów w obu okresach (jak wyżej) czy losowanie
   pilotów wspólnych w parach, co zawęziłoby przedział dla pilotów grających w obu okresach.
3. Rodzina BH: jedno porównanie okresów we wszystkich taliach (propozycja) czy osobno dla każdej
   talii; q = 0,10 czy 0,05.
4. Porównanie z niepełnym miesiącem (październik do 18 dnia): porównywać czy czekać na pełny.
5. Rozkład na warianty zależy od etykiet wariantów eksportu, a te od wersji reguł; zmiana reguł
   wariantów między okresami musi być wykluczona albo oznaczona.

## (h) Materiały tygodniowe i miesięczne (DeckDB K41, polecenie 2026-10-26 a)

Ta sekcja opisuje obecny kod (`src/deckdb_input.py`, `src/deckdb_materials.py`,
`src/deckdb_cards.py`), nie projekt. `--deckdb-level week|month|all` (domyślnie `all`) wybiera zestaw.

**Główna miara wyników talii (K41).** Średni percentyl miejsca: dla listy
p = 100 × (1 − (`final_rank` − 1) / (L − 1)), L = liczba opublikowanych list eventu (100 = zwycięzca,
0 = ostatnia opublikowana lista); dla talii średnia p jej list. Event, którego miejsca nie są
dokładnie 1..L, nie ma p (w eksporcie z 2026-10-25: 0 takich eventów z 754). Baza = średnia p
wszystkich list okresu; przy pełnych eventach to z konstrukcji 50. Konwersja Top 32 → Top 8 jest
miarą pomocniczą. Obie mają przedział bootstrap po pilotach na tych samych losowaniach (2000,
ziarno 20261021, tylko przy ≥ 10 pilotach); przedział konwersji jest liczbowo taki sam jak przed
dodaniem percentyla. Na macierzy wyników (`results_matrix_*.png`) komórka percentyla albo konwersji
ma kolor (czerwony poniżej, niebieski powyżej bazy) tylko wtedy, gdy jej przedział wyklucza bazę;
pozostałe są szare, także bez przedziału i zawsze w wierszu „no deck”, który nie jest talią.
Wyniki talii są wyłącznie w materiałach miesięcznych.

**Zestaw tygodniowy** (`week_<YYYY-Www>`, ostatni pełny tydzień ISO, okna rozłączne):

- udział w Top 32 Challenge'ów: 10 talii z największą liczbą list tygodnia (remisy po nazwie),
  wiersz „other decks” (suma pozostałych talii, z liczbą talii w kolumnie `Decks_in_row`) i wiersz
  „no deck” (Rogue + nieprzypisane); archetypy wszystkie i „no deck”;
- zmiana wobec poprzedniego tygodnia ISO w pp, z wierszami złożonymi tak samo w obu tygodniach;
  przedział 95%: eventy każdego tygodnia losowane ze zwracaniem niezależnie (wszystkie listy
  wylosowanego eventu), 2000 losowań, ziarno 20261026, te same losowania dla wszystkich wierszy;
  zmiana wyróżniona kolorem tylko przy przedziale wykluczającym 0, pozostałe szare
  (`change_vs_previous_week_*.png`, legenda na wykresie). Bez korekty na wielokrotne porównania:
  przy 12 wierszach talii i 7 archetypów przypadkiem wyróżnia się średnio około 1 zmiana;
- obecność: eventy z co najmniej jedną listą / eventy tygodnia;
- piloci: liczba różnych `player` przy każdym wierszu;
- trend udziałów z ostatnich N rozłącznych tygodni (`--trend-weeks`, publikowane 12) dla 10 talii
  bieżącego tygodnia i dla archetypów;
- Top 8 tygodnia (`top8_*.csv`): dla każdego eventu miejsca 1–8 z talią, wariantem (K16) i
  archetypem, zwycięzca = miejsce 1; bez nazw graczy i bez odsetków;
- nowe karty (`new_cards_*.csv`): (talia, strefa, karta) z pierwszą listą talii z tą kartą w całym
  eksporcie przypadającą na tydzień, raportowane przy ≥ 2 pilotach talii z kartą w tygodniu.

Tygodniowo nie ma winrate'u, matchupów, prawdopodobieństwa spotkania, percentyla, konwersji, liczby
list Top 8 na talię, trofeów ani rankingów wyników talii.

**Zestaw miesięczny** (`month_<YYYY-MM>[_incomplete]`, `format_period_<start>_to_<as-of>`,
`cards_<M1>_vs_<M2>[_incomplete]`): udział, obecność, Top 8 i trofea talii i archetypów w miesiącu
z trendem z 12 tygodni; warianty według D2; miary pilotów talii w miesiącu (liczba pilotów, udział
najczęstszego w listach Top 32 i w wejściach do Top 8, etykiety „low pilot count” i
„concentration”); skupienie wyników w miesiącu i w okresie formatu (`results_concentration_*.csv`:
próg = liczba wejść do Top 8 pilota na miejscu ceil(0,10 × piloci), co najmniej 1; w grupie są wszyscy
piloci z co najmniej tyloma wejściami, bez rozstrzygania remisów identyfikatorem ani losowaniem, więc podaje się
faktyczny odsetek pilotów i próg, np. „top 12% of pilots (all with at least 3 Top 8 entries)”; udział w listach,
wejściach do Top 8 i trofeach; uzupełnienie do poleceń 2026-10-26, punkt 2); wyniki talii z okresu formatu (percentyl i konwersja jak wyżej); warstwa kart
miesiąc do poprzedniego miesiąca według (g).

Wykresy rysują funkcje trybu dotychczasowego przez parametry opcjonalne (domyślnie bez zmian):
`_chart_compare_presence(keep_order=)`, `_chart_delta_ranking(highlight_col=, highlight_legend=)`,
`_chart_conversion_matrix(grey_cell=)`, `_create_trend_chart`. Kolor szary wspólny:
`challenge_history_engine.GREY_CELL`.

Kontrola trybu dotychczasowego po tej zmianie (2026-10-26, W42 2026-10-05 … 2026-10-18,
`--include-challenge-decklist --trend-weeks 12 --skip-league`, kopia plików trwałych `outputs/` w
katalogu tymczasowym, porównanie `compare_outputs.py` z punktem odniesienia
`outputs/_working/deckdb_robocze/_punkt_odniesienia_legacy_W42_2026-10-21/przed/`, do 2026-10-27 w `outputs/deckdb_robocze/`): 83 pliki katalogu
tygodnia, 82 identyczne, 1 różny (`metagame_input_grouped.xml`, wyłącznie `generated_at`).

## (i) Zestaw prezentacyjny (polecenie 2026-10-26 b, punkt 2)

`src/deckdb_presentation.py`; każdy pakiet ma obok wykresów roboczych (bez zmian) katalog `presentation/`:
tydzień `decks_share.png`, `archetypes_share.png` (udział, obecność i zmiana w jednym wykresie),
`trend_decks.png`, `trend_archetypes.png`; miesiąc `trend_decks.png`, `trend_archetypes.png`; okres formatu
`deck_results.png` (12 talii z największą liczbą list: listy, średni percentyl miejsca, konwersja Top 8); warstwa
kart `list_changes.png` (najwyżej 12 zmian list według składowej wewnątrz wariantów, bez kart z
`mana_only_land` = 1 z eksportu kart DeckDB, K42). Zmiany składu talii nie mają wersji prezentacyjnej.
Zasady: paleta trybu dotychczasowego (niebieski domyślny matplotlib dla słupków, kolory trendu „classic”
zielony / czerwony / szary dla zmian, końce skali RdBu dla wyników, `GREY_CELL` dla „nieodróżnialne”), krótki
tytuł, jedna linia stopki „MTGO Modern Challenge Top 32 · <okno> · <N> events, <M> lists”, bez wąsów (przedział
decyduje tylko o kolorze), poziome słupki malejąco, nazwy z lewej, liczby poza słupkiem na białym tle, slajd 16:9
(1920 × 1080, ≥ 14 pt poza znacznikiem SB). Wiersze zbiorcze („other decks”, „no deck”) zostają na dole w
jaśniejszym kolorze. W `presentation/` nie ma definicji, ziaren, sha ani wersji reguł; `speaker_notes.md` ma 1–3
zdania po angielsku na wykres. Trend rysuje `_create_trend_chart` trybu dotychczasowego z nowym parametrem
opcjonalnym `font_sizes` (domyślnie bez zmian).

Kontrola trybu dotychczasowego po zestawie prezentacyjnym (2026-10-26 b, W42, jak wyżej): pierwszy przebieg dał
2 różne wykresy trendu trybu dotychczasowego (`fontsize=None` w `set_title` zmniejszał tytuł), po poprawce
83 pliki, 82 identyczne, 1 różny (`metagame_input_grouped.xml`, wyłącznie `generated_at`).

## (j) Triage DeckDB przed materiałami (K43, polecenie 2026-10-26 e, punkt 3)

Tryb deckdb czyta obok pliku eksportu przypisań plik `triage_<data_version>.json` (DeckDB
`scripts/weekly_triage.py`) dla `data_version` eksportu. Brak pliku, inny `data_version` w pliku albo status
`pending` przerywa przebieg przed zapisem czegokolwiek (`DeckdbInputError`). Flaga `--allow-pending-triage` pozwala
wygenerować materiały przy statusie `pending`; wtedy każdy materiał ma oznaczenie „triage pending: <n> list group(s)
awaiting decision”: stopki wykresów roboczych (linia wielkimi literami) i prezentacyjnych (dopisek do jedynej linii),
arkusz STATUS, nowy plik `status.md` w każdym pakiecie i `presentation/speaker_notes.md`.

## (k) Folder prezentacji, statusy triage'u, etykieta i kolory (polecenie 2026-10-26 k, część B)

**Triage (B1, DeckDB K43 uzupełnienie, wpis 330).** Plik triage'u ma status `complete`, `pending_notice` albo
`pending_blocking` (starszy `pending` traktowany jak `pending_blocking`). Tryb deckdb pracuje przy `complete` i
`pending_notice`, odmawia przy `pending_blocking` (wypisuje czekające grupy) chyba że z `--allow-pending-triage`; wtedy
oznaczenia jak w (j). Przy `pending_notice` żaden wykres nie ma oznaczenia; monit „<N> list group(s) awaiting review,
<X>% of this week's lists, shown as Rogue & unclassified” (X = `waiting_share_of_week` z pliku triage'u) jest tylko w
`status.md` (także w arkuszu STATUS jako „Triage notice”) i w `speaker_notes.md`.

**Etykieta (B2).** Wiersz list bez talii (Rogue i nieprzypisane razem) nazywa się wszędzie „Rogue & unclassified”
(`NO_DECK_LABEL`), arkusz rozbicia `Rogue_unclassified_split`, kolumna eventów `Rogue_unclassified_lists`. Słownik:
„lists that match no defined deck: original builds (Rogue) and lists close to a known deck that its rules don't cover
(unclassified)”. Statusy eksportu bez zmian.

**Kolory (B3).** Archetypy: Aggro #EF4444, Graveyard #6366F1, Combo #3B82F6, Midrange #F59E0B, Ramp #14B8A6, Control
#60A5FA, Rogue & unclassified #6B7280. Słupki talii w kolorze archetypu z legendą archetypów. Na trendzie talii każda
talia ma stały kolor z `docs/deckdb_colours.csv` (16 barw, sprawdzonych walidatorem palety: jasność, nasycenie,
rozróżnialność sąsiednich par przy zaburzeniach widzenia barw ≥ 9 i przy normalnym ≥ 19; przydział 2026-10-27 według
liczby list z 2026-08-03 … 2026-10-25). Talia spoza pliku dostaje barwę palety według skrótu nazwy, a `status.md`
prosi o dopisanie. 16 barw na 42 talie: barwa powtórzona na jednym wykresie trendu rysowana jest linią przerywaną z
rombem przy podpisie, `status.md` wymienia takie pary (W43: Amulet Titan / Prowess).

**Zasady wykresów prezentacyjnych (B4–B6)** jak w (i), oraz: zmiana wobec poprzedniego tygodnia pogrubiona i
kolorowa (zielona / czerwona) tylko poza wahaniami; „other decks” nie jest słupkiem, tylko linią pod wykresem
„other decks: <udział>% of lists across <n> decks”; na Top 8 przy ponad 14 taliach talie z jedną listą w Top 8 i bez
trofeum są wymienione w linii pod wykresem; trend talii i archetypów z podpisami na końcu linii; macierz tygodnia
(Top 32 lists, events present, Top 8 lists, events with Top 8, trophies; skala YlGnBu osobna dla kolumny, bez
konwersji; 12 talii z największą liczbą list i wiersz Rogue & unclassified; archetypy wszystkie); piloci: 10 talii z
wykresu udziałów, liczba różnych pilotów i udział najczęstszego pilota, oznaczenie przy co najmniej 1/3 list; nowe
karty jako tabela (talia, karta, piloci, SB tylko dla sideboardu, najwyżej 12, bez kart `mana_only_land`). Wykresy
miesięczne: wyniki talii (12 talii z największą liczbą list w miesiącu; percentyl i konwersja liczone na liście
miesiąca z bazą miesiąca i przedziałem bootstrap po pilotach od 10 pilotów) i zmiany list (miesiąc wobec
poprzedniego, bez kart `mana_only_land`, najwyżej 12, added / cut, etykieta „<talia> · <karta>”).

**Jeden folder (B7, `src/deckdb_folder.py`).** Obok katalogów roboczych (bez zmian) przebieg na poziomie `week` albo
`all` tworzy płaski folder `outputs/presentation/<rok>-W<nn>/` dla ostatniego pełnego tygodnia ISO do `--as-of`:
wykresy `01_archetypes_share.png` … `09_new_cards.png`, dane `data_*.csv` (z kolumnami `rules_version`,
`export_sha256`, `data_version`), `status.md`, `speaker_notes.md`, `GLOSSARY.md`, `README.md`. Ponowny przebieg
zastępuje pliki folderu. Materiał miesięczny (`m01_deck_results.png`, `m02_list_changes.png`, `m_data_*.csv`) za
poprzedni miesiąc trafia do folderu, gdy tydzień zawiera pierwszą niedzielę miesiąca (niedziela tygodnia ma dzień
≤ 7); `--presentation-month YYYY-MM` wymusza miesiąc (niepełny z dopiskiem „incomplete month” w tytule i stopce).
`status.md`: okno, eventy, listy, pokrycie, Rogue & unclassified (Rogue / nieprzypisane), wersja reguł, sha256 i
`data_version` eksportu, status triage'u, monit `pending_notice`, zmiany klasyfikacji od poprzedniego tygodnia
(porównanie z eksportem tego samego katalogu o najpóźniejszej ostatniej dacie eventu nie późniejszej niż niedziela
poprzedniego tygodnia: wszystkie wspólne listy i listy od początku miesiąca tygodnia; listy tygodnia są nowe), lista
ID eventów tygodnia z datami do weryfikacji na mtgo.com.

Przebieg 2026-10-27 (polecenie 2026-10-26 k, punkt B8): eksport `assignments_ded3d7dba99e38d7_2026-10-25.csv`
(sha256 `31b8ca14…`, reguły `ded3d7dba99e38d7`), karty `cards_ded3d7dba99e38d7_2026-10-25.csv`, triage `complete`;
`--as-of 2026-10-25 --trend-weeks 12 --presentation-month 2026-10` → `outputs/presentation/2026-W43/` (24 pliki: 9
wykresów tygodnia, 2 wykresy października jako niepełnego miesiąca, 9 plików danych, status, notatki, słownik,
README). W43: 12 eventów, 371 list, pokrycie 58,2%, Rogue & unclassified 13 list (Rogue 9, nieprzypisane 4); zmiany
klasyfikacji wobec eksportu poprzedniego tygodnia (`assignments_ee3ed9fbbbbd6ad6_2026-10-18_dv.csv`): Control →
Prison 23 i Rogue & unclassified → Prison 11 na wspólnych listach. Każdy wykres obejrzany pod kątem slajdu 16:9;
poprawione przed zapisem: legenda archetypów nachodząca na podtytuł, powtórzone barwy talii na trendzie (paleta 16
barw, linia przerywana przy powtórzeniu), Top 8 z 23 wierszami (talie z jedną listą w linii pod wykresem), legenda
added / cut na długim tytule. Kontrola trybu dotychczasowego W42 (jak w (i)): 83 pliki, 82 identyczne, 1 różny
(`metagame_input_grouped.xml`, wyłącznie `generated_at`). Testy: 98 przechodzi, 1 nie przechodzi (znany test ligi
`test_run_league_update_raises_league_blocking_error_on_frozen_week`).

**Poprawka do (k), punkt B7 (2026-10-27): najwyżej 13 plików w folderze** (projekt prezentacji przyjmuje najwyżej
24 załączniki). Wykresy PNG zostają osobno (01–09, m01–m02). Wszystkie tabele są w jednym `data.xlsx`: pierwszy arkusz
`contents` (arkusz i jedno zdanie o nim), potem `decks`, `archetypes`, `top8`, `new_cards`, `pilots`,
`share_history_decks`, `share_history_archetypes`, a w tygodniu z materiałem miesięcznym także `m_deck_results` i
`m_list_changes`; każdy arkusz z kolumnami `rules_version`, `export_sha256`, `data_version`. README.md, status.md,
speaker_notes.md i GLOSSARY.md są połączone w `notes.md` z sekcjami Status, Files, Speaker notes, Glossary (w tej
kolejności). Przebieg odmawia (`DeckdbInputError`), gdy folder miałby więcej niż 13 plików.

**Porządek katalogów (2026-10-27).** Materiały robocze trybu deckdb są w `outputs/_working/deckdb/WORKING_<as-of>/`
(wcześniej `outputs/deckdb_working/`), starsze katalogi robocze i punkt odniesienia kontroli W42 w
`outputs/_working/deckdb_robocze/` (wcześniej `outputs/deckdb_robocze/`); na wierzchu `outputs/` zostaje tylko
`presentation/` z folderami do prezentacji oraz pliki trybu dotychczasowego.

**Poprawka do (k), B6 (2026-10-27): wyniki talii w materiale miesięcznym na okresie formatu.** `m01_deck_results.png`
i arkusz `m_deck_results` liczą średni percentyl miejsca i konwersję na listach okresu formatu, od daty obowiązywania
ostatniego dużego zdarzenia formatu (`effective_mtgo`, dziś 2026-05-19) do końca miesiąca materiału (przy niepełnym
miesiącu do ostatniej daty eksportu), z bazą tego okresu i przedziałem bootstrap po pilotach (K41); tytuł i stopka
podają okres. Zmiany list (`m02`) zostają porównaniem miesiąca z poprzednim.

**m01 jako wykres odchyleń z pasem istotności (2026-10-27).** Panele „mean place percentile” i „Top 8 conversion”
pokazują odchylenie talii od bazy wspólnej okresu (oś zerowa pionowo w środku panelu; jednostki „points vs average”
i „pp vs average”; zakres symetryczny, wspólny dla wierszy panelu). Pas istotności wiersza: od −(CI_high − est) do
+(est − CI_low) z 95% przedziału bootstrap po pilotach, bez symetryzacji i bez wąsów. Kolor słupka z geometrii:
zielony poza prawą krawędzią pasu, czerwony poza lewą, szary w pasie, bez przedziału (< 10 pilotów, adnotacja „low
pilot count”) i dla „Rogue & unclassified”. Kolor z geometrii musi być równy fladze `*_CI_excludes_baseline` arkusza,
inaczej przebieg przerywa się (`DeckdbInputError`). Tabela kontrolna wszystkich wierszy: arkusz `m_results_bands` w
`data.xlsx`. Po zmianie K41 w DeckDB na korektę wielokrotnych porównań (BH) pas trzeba liczyć z przedziału na poziomie
1 − T (T = q·k/m) i porównywać z flagą BH, z dopiskiem „band adjusted for multiple comparisons”; dziś DeckDB tej
flagi nie eksportuje, więc pas jest nieskorygowany.

## (l) Pełne dane tygodniowe i miesięczne, tracked decks, korekta BH zmian tygodniowych (polecenie przed 2026-W44, część 2)

Pipeline liczy wszystko dla wszystkich talii; wykresy dalej pokazują top 10; test zmiany tylko w zbiorze tracked
decks. `data.xlsx` folderu prezentacji ma dwa nowe arkusze, przeliczane od zera z eksportu przy każdym przebiegu
(nigdy dopisywane): `weekly_metrics` (format długi: tydzień ISO okresu formatu × talia, osobno × archetyp, oba
z „Rogue & unclassified”; pierwszy tydzień przycięty do początku okresu formatu; talia nieobecna w tygodniu ma 0
list i udział 0) i `monthly_metrics` (miesiąc kalendarzowy × talia i archetyp; ostatni miesiąc kończy się niedzielą
tygodnia folderu, `complete_month = 0`, gdy przed końcem miesiąca). Tracked decks tygodnia w: top 10 w ∪ top 10 w−1 ∪
największa talia (listy w w) każdego archetypu bez talii w tych dwóch top 10, o ile jej udział ≥ 1% w w albo w−1;
remis zawsze alfabetycznie po nazwie; `tracked_reason` = pierwszy pasujący powód (top10_week, top10_prev,
largest_in_archetype). Test zmiany: event bootstrap jak dotąd (2000 losowań, ziarno 20261026 dla każdego tygodnia,
najpierw tydzień bieżący), przedział 2,5/97,5, dwustronne p = 2·min(P(d ≤ 0), P(d ≥ 0)), co najmniej 1/2000; BH q =
0,1 osobno w rodzinie tracked decks tygodnia i w rodzinie archetypów (wszystkie archetypy i wiersz „Rogue &
unclassified” z listą w w albo w−1); `change_beyond` = odrzucenie BH. Talie nie-tracked mają puste CI, p, q i
`change_beyond`. Wiersz „Rogue & unclassified” na wykresie talii bierze test z rodziny archetypów (ta sama
wielkość, te same losowania); wiersz „other decks” nie jest testowany. Wykresy 01 i 03 pogrubiają zmianę tylko przy
`change_beyond = 1`; arkusze `decks` i `archetypes` mają `change_beyond` obok `Change_CI_excludes_0`. Kontrole
(błąd, bez fallbacku): suma list talii i suma po archetypach = listy tygodnia; trophies ≤ top8 ≤ lists,
events_with_list ≤ week_events; wiersze arkuszy `decks` / `archetypes` / `pilots` identyczne z `weekly_metrics`.

## (m) Korekta BH wyników talii (zmiana K41, DeckDB wpis 331, zadanie przed 2026-W44, część 3)

`deckdb_input.conversion_table` → `results_bh`: dla każdej talii z co najmniej 10 pilotami (bez wiersza „Rogue &
unclassified”, który nie jest talią) dwustronne p = 2·min(P(est* ≤ b), P(est* ≥ b)), co najmniej 1/2000, z tych samych
2000 losowań pilotów co 95% przedziały, osobno dla percentyla i konwersji; b = `baseline_rest`, baza pola bez list tej
talii (percentyl: średnie p pozostałych list; konwersja: Top 8 / listy pozostałych list). BH q = 0,1 osobno w rodzinie
percentyla i w rodzinie konwersji; `BH_threshold_T` = q·k/m (q/m bez odrzuceń). Pas (`*_band_low`, `*_band_high`) to
przedział na poziomie 1 − T z tych samych losowań: statystyki pozycyjne x_(j+1) i x_(N−j), j = ⌊N·T/2⌋, dzięki czemu
`baseline_rest` leży poza pasem dokładnie wtedy, gdy p ≤ T, czyli przy odrzuceniu BH; niezgodność, T < 1/N albo
losowanie bez wartości przy zdefiniowanej mierze przerywa przebieg. Talia bez testu (mniej niż 10 pilotów, miara
nieokreślona dla wszystkich jej list albo brak pozostałych list) ma puste p, q, T i pas, `BH_rejects` 0. Kolumny
`*_CI_excludes_baseline` (95% CI wobec bazy wspólnej, bez korekty) zostają do porównania. m01: odchylenie od
`baseline_rest` („points / pp vs rest of the field”, w tytule panelu baza wspólna „all”), pas z poziomu 1 − T, legenda
z próbkami kolorów słupków i „band adjusted for multiple comparisons”; kolor z geometrii musi być równy `BH_rejects`
(asercja). Ten sam kolor (`BH_rejects`) w roboczych `results_matrix_*.png` i `presentation/deck_results.png`. Zastępuje
zdanie z (k) „dziś DeckDB tej flagi nie eksportuje”: test liczy pipeline, bo tylko on ma losowania bootstrapu; DeckDB
zapisuje zmianę w rejestrze decyzji (wpis 331) i w `docs/master_prompt.md`.

Okres 2026-05-19 … 2026-10-25 (eksport `assignments_ded3d7dba99e38d7_2026-10-25.csv`), 34 talie z testem: percentyl
7 talii z 95% CI poza bazą wspólną (8 z wierszem „Rogue & unclassified”), po BH 5 (Broodscale Combo, Neobrand, Rakdos
Moonshadow, Mill, Death's Shadow; T = 0,0147); konwersja 3 (4 z „Rogue & unclassified”), po BH 1 (Death's Shadow;
T = 0,0029). Broodscale Combo: percentyl 53,39, 95% CI 51,10–55,68, `baseline_rest` 49,64, p 0,003, q 0,051, pas
50,55–56,12, zielony; konwersja 27,7%, p 0,133, szara.
