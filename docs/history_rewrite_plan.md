# Przepisanie historii: usunięcie dwóch plików tożsamości (plan, bez wykonywania)

Stan na 2026-10-30. Od commitu `88a06b3` pliki `data/pilot_identity.csv` i `data/pilot_merge_log.csv` nie są już w
`main` (leżą w katalogu prywatnym, `src/identity.py`). Plan dotyczy usunięcia ich z całej historii publicznego repo.

## Zakres

| plik | wiersze danych | commity, w których występuje |
|---|---|---|
| `data/pilot_identity.csv` | 2 (jedno konto główne, jeden alias) | `47d7df0` (2026-08-26, dodanie), `c81b2eb` (2026-09-03, zmiana), `88a06b3` (usunięcie) |
| `data/pilot_merge_log.csv` | 1 (jedno scalenie) | jak wyżej |

Repo: ok. 300 commitów, 17 gałęzi na GitHubie (`main`, `mikrus-sync` bota, 15 gałęzi PR), 16 PR-ów, 0 forków (sprawdzone
2026-10-30 przez API GitHuba).

**Ograniczenie, które trzeba znać przed decyzją.** Treść tych plików jest publiczna także poza nimi, a przepisanie
historii tylko dla dwóch plików tego nie usuwa:
- wiadomość commitu `47d7df0` („data: merge … (3263693) into … (2903591) pilot identity”);
- przykład w `CLAUDE.md` („Pilot identity CLI”: `--primary 2903591 --alias 3263693 --source self_request_x --evidence "DM 2026-08-25"`);
- notatka w `data/pilot_profile.csv` (wymienia drugą nazwę konta);
- strona ligi: `priorNames` w `docs/data/pilots_*.json` i `season_*.json` (z założenia pokazuje dawne nazwy scalonego pilota).

Pełne ukrycie wymaga więc także `--replace-message` (wiadomości commitów), `--replace-text` (te dwa miejsca w
dokumentacji, w całej historii) i decyzji, czy strona dalej pokazuje dawne nazwy. Bez tego przepisanie usuwa pliki, ale
nie informację.

## Kroki

0. **Narzędzie:** `pip install git-filter-repo` (dziś nie jest zainstalowane). Działa na świeżym klonie, nie w roboczym
   katalogu.
1. **Zatrzymanie bota** (VPS, root): zakomentować linię crona `15 * * * *` (kolektor) i `0 5 * * *`
   (`api_backup_bot.py fetch`, robi `git pull`); poczekać na koniec bieżącego przebiegu (`flock -n /tmp/mikrus-collector.lock`).
   Bez tego bot może wypchnąć commit na starą historię w trakcie operacji.
2. **Kopia:** `git clone --mirror https://github.com/Warlord1986pl/MTG-Metagame-Input-Generator.git backup.git` i
   archiwum `backup.git` poza repo (to jedyna droga powrotu).
3. **Przepisanie** w drugim, świeżym klonie mirror:
   `git filter-repo --invert-paths --path data/pilot_identity.csv --path data/pilot_merge_log.csv`
   (opcjonalnie w tym samym przebiegu `--replace-message` i `--replace-text` z pliku wyrażeń, patrz ograniczenie).
4. **Sprawdzenie:** `git log --all -- data/pilot_identity.csv data/pilot_merge_log.csv` puste; `git grep` po
   identyfikatorach w `$(git rev-list --all)` puste (jeśli wybrano też zamianę tekstów); liczba commitów i drzewo
   `main` bez zmian poza tymi plikami (`git diff <stary main> <nowy main> --stat` pokazuje tylko je, o ile `main` ich
   już nie miał: pusto).
5. **Wypchnięcie:** `git push --force origin --all` i `git push --force origin --tags`. Gałęzie PR (`refs/pull/*`) są
   na GitHubie tylko do odczytu i nadal wskazują stare commity; stare commity zostają też w widokach podręcznych.
   Usunięcie ich wymaga zgłoszenia do GitHub Support („Removing sensitive data from a repository”: lista commitów i
   PR-ów do usunięcia).
6. **Komputer lokalny** (`E:\github\MTG-Metagame-Input-Generator`): po zatwierdzeniu lokalnych zmian
   `git fetch origin && git reset --hard origin/main` (pliki nieśledzone i ignorowane, np. `weekly/`, `outputs/`,
   `.pilot_identity_dir`, zostają). Każda inna kopia repo musi zrobić to samo albo zostać sklonowana od nowa; zwykły
   `git pull` na starej kopii wprowadziłby starą historię z powrotem przy następnym pushu.
7. **VPS** (`/root/MTG-Metagame-Input-Generator`): `git fetch origin && git reset --hard origin/main`. Lokalne zmiany
   serwera są do odtworzenia: `season_config.csv` i migawki są od 2026-10-30 commitowane przez bota (wariant A
   `docs/backup_plan.md`), manifesty bot przepisuje co godzinę. Nieśledzone `scripts/backup/` zostaje. Repo
   kolektora (`Meta-Challange-2.0_Mikrus`) nie jest dotknięte. Potem przywrócić cron i sprawdzić log pierwszego
   przebiegu (`git pull`, `push` bez błędu, nowy commit „mikrus: sync …” na nowej historii).
8. **Forki:** dziś 0. Sprawdzić ponownie tuż przed krokiem 5 (`gh api repos/<repo>/forks`); fork założony przed
   przepisaniem zachowuje starą historię i można go usunąć tylko prośbą do właściciela albo do GitHub Support.
9. **GitHub Pages** przebuduje się sam z nowego `main/docs`.

## Skutki uboczne

- Zmieniają się skróty wszystkich commitów od 2026-08-26: odwołania w `CHANGELOG.md`, `docs/*.md`, notatkach i w
  DeckDB (np. „pipeline `becb31b`” w raportach runnera, `weekly/runs.csv`) przestaną wskazywać istniejące commity.
- Otwarte gałęzie i PR-y oparte na starej historii trzeba odtworzyć na nowej (dziś żadna praca w toku od nich nie zależy).
- Operacja jest nieodwracalna dla wszystkich, którzy pobiorą nową historię; jedyną kopią starej jest archiwum z kroku 2.

## Rekomendacja

Przy jednym scaleniu i informacji, która i tak jest publiczna na stronie i w dokumentacji, przepisanie samych dwóch
plików daje niewiele przy dużym koszcie (wszystkie skróty commitów, GitHub Support dla PR-ów). Jeśli celem jest
prywatność przyszłych scaleń, wystarcza zmiana z 2026-10-30 (pliki poza repo). Jeśli celem jest usunięcie tego
konkretnego scalenia z publicznego zasięgu, trzeba w jednej operacji objąć też wiadomości commitów, przykład w
`CLAUDE.md`, notatkę w `pilot_profile.csv` i zdecydować o `priorNames` na stronie.
