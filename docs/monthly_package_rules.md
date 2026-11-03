# Zasady danych dla miesięcznego artykułu na metagame.info

Wersja z 2 listopada 2026. Do wklejenia w instrukcje projektu metagame.info. Opisuje, czym są dane z paczki miesięcznej i czego z nich nie wolno wnioskować. Styl i forma artykułu należą do instrukcji tego projektu, nie do tych zasad.

## Źródło i populacja

Dane pochodzą z DeckDB: opublikowane listy Top 32 Modern Challenge'ów na MTGO, przypisane do talii na podstawie kart według wersjonowanych reguł. MTGO publikuje min(32, liczba graczy) list z eventu, więc populacją są opublikowane listy, nie wszyscy uczestnicy i nie cały metagame MTGO. Udział talii to jej listy podzielone przez wszystkie opublikowane listy okresu i nazywa się „share of Challenge Top 32”. Nie jest prawdopodobieństwem spotkania talii. Przy pierwszym użyciu podaje się pokrycie uczestników z sekcji Status.

Premier eventy (Showcase Challenge, RC Super Qualifier, Qualifier) nie wchodzą do żadnej metryki. Dane z turniejów papierowych to osobne źródło i nie łączy się ich z danymi MTGO.

## Paczka miesięczna

Folder monthly/<RRRR-MM>/ z runnera zawiera data.xlsx (arkusze m_deck_results, m_results_bands, m_list_changes, monthly_metrics, contents), wykresy m01_deck_results.png i m02_list_changes.png oraz notes.md (Status, Glossary, speaker notes). Każdy arkusz niesie wersję reguł DeckDB, sha256 eksportu i data_version; wszystkie muszą być identyczne.

Przed pisaniem sprawdzasz: okres miesiąca kończy się ostatnim dniem miesiąca (nie końcem tygodnia), Status mówi „complete month”, liczba eventów i list miesiąca zgadza się między Status a arkuszami. Rozbieżność zgłaszasz i nie piszesz na takich danych.

## Wyniki talii

Liczone na okresie formatu: od ostatniej dużej zmiany formatu (obecnie 19 maja 2026) do ostatniego dnia zamkniętego miesiąca, narastająco.

Średni percentyl miejsca: dla każdej listy p = 100 × (1 − (miejsce − 1) / (L − 1)), gdzie L to liczba opublikowanych list eventu (100 = zwycięzca, 0 = ostatnia opublikowana lista); dla talii średnia p jej list. Skala 0–100, jedno miejsce po przecinku. To główna miara wyników, nadal testowana (potwierdzenie na danych z listopada 2026).

Konwersja Top 32 → Top 8: listy talii w Top 8 podzielone przez listy talii w Top 32, z przedziałem. Miara pomocnicza.

Punktem odniesienia jest reszta pola (baseline_rest: średnia wszystkich list okresu bez list tej talii), nigdy mediana pokazanych talii ani założone 25%.

Przedziały ufności: bootstrap po pilotach (2000 losowań), tylko dla talii z co najmniej 10 pilotami. Talia z mniejszą liczbą pilotów ma etykietę „low pilot count” i nie dostaje oceny względem przeciętnej.

Istotność: talia jest „above average” albo „below average” tylko wtedy, gdy test przechodzi korektę Benjaminiego-Hochberga (q = 0.1) w rodzinie danej miary (kolumny Percentile_BH_rejects, Conversion_BH_rejects). Każda inna talia jest „indistinguishable from average” i nie wolno jej opisać jako lepszej ani gorszej, także gdy jej przedział przed korektą wyklucza przeciętną. Na wykresie m01 kolor zielony lub czerwony oznacza właśnie odrzucenie po korekcie; szary pas pokazuje, jak daleko talia musiałaby odejść od reszty pola, żeby się od niej różnić.

Percentyl ma ograniczenie: jego rzetelność obejmuje stałość pilotów talii, więc jest górną granicą sygnału samej talii. Talia z etykietą „concentration” (top pilot ma co najmniej 1/3 list albo 1/2 wejść do Top 8) mówi więcej o tym pilocie niż o talii; trzeba to napisać przy jej wyniku.

## Zmiany list

Zmiana list to zmiana udziału list wariantu talii grających kartę, miesiąc do miesiąca: w obrębie wariantu, co najmniej 10 pp, istotna po korekcie BH (q = 0.1). Lądy dające wyłącznie manę są pominięte. Podaje się dwa poziomy („8% to 69% of lists”) i liczbę pilotów z kartą.

## Czego nie wolno

Winrate'u, matchupów, encounter probability, delty, expected wins ani etykiet Overperformer, Underperformer i Needs Attention. Powód, jeśli trzeba go wyjaśnić czytelnikom: opublikowane listy weszły do Top 32 dzięki dobremu rekordowi, więc ich winrate jest zawyżony (średnio około dwóch trzecich wygranych meczów) i nie mówi, jak talia radzi sobie z całym polem.

Nazw graczy. Rankingów „najlepszej talii”. Opisywania talii nieodróżnialnej od przeciętnej jako lepszej albo gorszej. Opisywania zmian udziału z kilku miesięcy jako trendu bez testu; podaje się wtedy dwa poziomy. Porównywania z liczbami sprzed 2026-W43 (inne źródło i inna populacja).

Nazwy talii pochodzą wyłącznie z DeckDB; talia nazywa się po planie gry, kolory są wariantami. Nie scala się, nie dzieli i nie przemianowuje talii.
