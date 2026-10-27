---
description: Aktualizacja w tygodniu (DeckDB): import, triage, nowe decyzje; bez zmian reguł i bez materiałów
---

Aktualizacja w tygodniu (DeckDB E:\github\DeckDB), bez zmian reguł i bez materiałów:

1. import nowych Modern Challenge'ów od ostatniego importu (backfill-raw i import), liczba eventów i list, pokrycie;
2. scripts/weekly_triage.py dla nowych list, nowe grupy (co najmniej 2 listy od różnych pilotów) do kolejki, pakiet i wdrożenie według docs/deploy_mikrus.md;
3. jeśli są nowe decyzje w dzienniku aplikacji, standardowy cykl (log_check --pull, apply-decisions, freeze_cores, build-review-queue --revise-changed, export-review-bundle, wdrożenie);
4. raport krótko: nowe listy, nowe grupy w kolejce (plan z kart, liczba list i pilotów, propozycja), zastosowane decyzje, status triage'u.

Commit na punkt.
