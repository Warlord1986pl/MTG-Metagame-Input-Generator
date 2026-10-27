---
description: Poniedziałek (DeckDB i pipeline): import do niedzieli, triage, eksporty, folder outputs/presentation/<rok>-W<nn>/
---

Poniedziałek (DeckDB E:\github\DeckDB i pipeline E:\github\MTG-Metagame-Input-Generator):

1. DeckDB: import do niedzieli włącznie, weekly_triage.py, zastosowanie nowych decyzji, jeśli są; nowe grupy z weekendu do kolejki bez rozstrzygania;
2. export-assignments i export-cards z tego samego stanu bazy oraz plik triage'u;
3. pipeline: przy statusie complete albo pending_notice wygeneruj folder outputs/presentation/<rok>-W<nn>/ z --as-of <ostatnia niedziela>, z materiałem miesięcznym, jeśli ta niedziela należy do pierwszego tygodnia miesiąca; przy pending_blocking zatrzymaj się i wypisz grupy blokujące;
4. kontrola trybu dotychczasowego na W42 tylko przy zmianie kodu pipeline'u od ostatniego poniedziałku;
5. raport krótko: liczba eventów i list, status triage'u, ścieżka folderu.

Commit na punkt.
