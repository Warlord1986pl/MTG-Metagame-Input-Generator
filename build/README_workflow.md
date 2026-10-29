# Cotygodniowy przebieg (od 2026-W43, dane DeckDB)

Kod jest staly i lezy w plikach projektu. Tresc tygodnia pisze sie od nowa w dwoch
plikach w katalogu roboczym, tak jak teksty md. Skrypty zmieniaja sie tylko przy
zmianie logiki, a kazda taka zmiana jest zglaszana przed jej zrobieniem.

## Pliki

| plik | rola | co zmienia sie co tydzien |
| --- | --- | --- |
| `analysis.py` | walidacja paczki DeckDB (testy z czesci 2), `stats.json` | nic; argumenty `--week --src --out` |
| `build_deck.js` | renderer prezentacji PPTX | nic; argument: numer tygodnia |
| `gen_dossier.py` | preprocesor dossier `src.md -> md`, kontrola prozy | nic; argument: numer tygodnia |
| `build_docs.py` | cztery pliki docx z md | nic; argument `--week` |
| `qa.py` | kontrola po buildzie | nic; `--events` z raportu |
| `deck_w<N>.json` | tresc prezentacji: CFG, slajdy, callouty, notki | piszesz od nowa (katalog roboczy) |
| `dossier_w<N>.src.md` | zrodlo dossier z blokami i odwolaniami | piszesz od nowa (katalog roboczy) |
| `reddit/youtube/x_w<N>.md` | teksty | piszesz od nowa (katalog roboczy) |

`crop.py` i `stats_json_spec.md` sa nieaktualne (stary format) i mozna je usunac.

## Kolejnosc

```bash
mkdir -p /home/claude/w44/src && cd /home/claude/w44
cp /mnt/user-data/uploads/{data.xlsx,notes.md} src/ && cp /mnt/user-data/uploads/*.png . && cp src/notes.md .
cp /mnt/project/*.py /mnt/project/*.js .

python3 analysis.py --week 44 --src src --out .   # FAIL = stop, stats.json nie powstaje
# reczna weryfikacja ID eventow na mtgo.com/decklists (lista ID w raporcie)
# napisz deck_w44.json, dossier_w44.src.md, reddit/youtube/x_w44.md
NODE_PATH=$(npm root -g) node build_deck.js 44
python3 gen_dossier.py 44
python3 build_docs.py --week 44 --src . --png . --out /mnt/user-data/outputs
python3 qa.py --week 44 --events <liczba z raportu> --src .
```

## Odwolania do danych

Liczby w `deck_w<N>.json` i `dossier_w<N>.src.md` zapisuje sie jako odwolania do
`stats.json`, nie jako gotowy tekst. Nierozwiazane odwolanie przerywa build.

    {fmt:prefix.nazwa.pole}
    fmt:    pp (+3.9 pp), pct (9%), n (32), f1 (3.4), var (within/beyond normal variation), raw
    prefix: d talia, a archetyp, s stats ({n:s.lists}, bez nazwy), h historia udzialu talii
            (pole = tydzien ISO, np. {pct:h.Prowess.2026-W40}), v wariant ('Zoo|Zoo: Frog'),
            c nowa karta ('Zoo|Force of Despair'), l zmiana listy ('Zoo|Brotherhood's End'),
            w listy talii w tygodniu ({n:w.Tron.lists})

Zaokraglenia: polowki w gore w obu skryptach. Gotowy tekst tylko tam, gdzie liczby nie
ma w `stats.json` (np. poziomy archetypow z historii, liczby list top pilota).

W dossier blok talii to jedna linia `{{BLOCK: Talia}}` (archetyp: `{{ABLOCK: Aggro}}`);
naglowek ze strzalka i szesc linii danych generuje skrypt. Rownowazniki pod spodem
jako `- ...`.

Slajdy w `deck_w<N>.json` maja typ: title, cards, snapshot, chart, top8, zoo, two.
Pole `"if": "listChanges"` wlacza slajd tylko przy fladze w `cfg`.

## Polityka QA

Pelny przeglad wizualny wszystkich slajdow po zmianie szablonu albo raz na kilka
tygodni. W zwykly tydzien: `qa.py`, podglad slajdow ze zmienionym szablonem i slajdow
z przypisaniem talia -> archetyp, w dossier pierwsze strony i zmienione sekcje. Reszta
kontrola tekstowa. W podsumowaniu w czacie zawsze pada, co bylo ogladane, a co
sprawdzone tekstem.
