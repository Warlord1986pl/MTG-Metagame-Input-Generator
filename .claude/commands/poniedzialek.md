---
description: Poniedziałek (DeckDB i pipeline): import do niedzieli, triage, eksporty, folder outputs/presentation/<rok>-W<nn>/
---

Poniedziałek (DeckDB E:\github\DeckDB i pipeline E:\github\MTG-Metagame-Input-Generator):

1. DeckDB: import do niedzieli włącznie, weekly_triage.py, zastosowanie nowych decyzji, jeśli są; nowe grupy z weekendu do kolejki bez rozstrzygania;
2. export-assignments i export-cards z tego samego stanu bazy oraz plik triage'u;
3. pipeline: przy statusie complete albo pending_notice wygeneruj folder outputs/presentation/<rok>-W<nn>/ z --as-of <ostatnia niedziela>, z materiałem miesięcznym za miesiąc M, jeśli tydzień zawiera pierwszy dzień miesiąca M+1 (okres materiału kończy się ostatnim dniem M); przy pending_blocking zatrzymaj się i wypisz grupy blokujące;
4. kontrola trybu dotychczasowego na W42 tylko przy zmianie kodu pipeline'u od ostatniego poniedziałku;
5. raport krótko: liczba eventów i list, status triage'u, ścieżka folderu.

Commit na punkt.

Gotowe skrypty, zamiast pisać je od nowa:
- DeckDB (`C:/Python/python.exe`, `PYTHONIOENCODING=utf-8`): przy decyzjach `scripts/assignment_diff.py save` / `diff` (zmiany przypisań), wpisy do `docs/decisions.md` przez `scripts/add_log_entry.py`; po eksporcie `scripts/export_diff.py <poprzedni eksport> <nowy eksport> --since <poniedziałek> --until <niedziela>` (które kolumny i ile list się zmieniło);
- pipeline (`E:/github/.venv/Scripts/python.exe`): punkt 4 to `scripts/legacy_w42_control.py` (kod wyjścia 0 = tylko `generated_at` w XML różny, 82 z 83 plików identyczne).
