# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Does

Fetches MTG metagame and matchup data from the Videre Project API (`https://api.videreproject.com`), normalizes deck names/archetypes using rule CSV files in `docs/`, tracks Challenge tournament history scraped from MTGGoldfish, and exports CSV/XLSX/XML outputs and charts to weekly-dated folders under `outputs/`.

It also runs a separate **league/season standings** pipeline (`outputs/league/`) built from the same per-event results, and publishes a static site (`docs/index.html` + `docs/data/*.json`) showing season leaderboards and per-pilot profiles.

## Commands

### Install dependencies
```sh
pip install -r requirements.txt
```

### Run weekly generation (CLI)
```sh
python src/metagame_input_generator.py --format Modern --history-points 1 --metagame-window-days 14 --my-deck "Domain Zoo" --my-window-days 90 --my-fallback-window-days 180 --rogue-threshold 0.5 --include-challenge-decklist
```

### PowerShell launchers
```powershell
# Single weekly snapshot
./run_weekly.ps1 -Format Modern -MyDeck "Domain Zoo"

# 4 history points anchored to a specific Sunday
./run_history_4.ps1 -AnchorSunday 2026-03-15

# 18-point history run
./run_history_18.ps1

# Interactive preset-based CLI
./run_preset_cli.ps1
```

### Desktop GUI (PySide6)
```bat
run_gui.bat
```

The PowerShell scripts default to `E:/github/.venv/Scripts/python.exe` and fall back to `python` on PATH.

### Pilot identity CLI

```sh
# read-only lookup, by loginid or display name
python src/pilot_identity_cli.py --show 2903591

# merge one or more alias loginids into a primary pilot -- dry-run by default, prints the full
# before/after report (leaderboard, validations a-e); nothing is written without --apply
python src/pilot_identity_cli.py merge --primary 2903591 --alias 3263693 \
    --source self_request_x --evidence "DM 2026-08-25" --primary-name MeninoNey
python src/pilot_identity_cli.py merge --primary 2903591 --alias 3263693 \
    --source self_request_x --evidence "DM 2026-08-25" --primary-name MeninoNey --apply
```

## Architecture

### Source modules (`src/`)

| Module | Role |
|---|---|
| `metagame_input_generator.py` | Core entry point. API fetching, deck name normalization, dataset building, all file export. |
| `challenge_history_engine.py` | Persistent challenge history CSV management, chart generation for Challenge Analytics. |
| `statistics_engine.py` | Metagame statistics charts (encounter probability, performance quadrant, trend lines). |
| `league_engine.py` | Rebuilds the league/season standings table from `outputs/league/results/*.csv` (`build_season_table`), identity-aware grouping (`_identity_key`), invariant checks. |
| `league_site_export.py` | Turns the season table + per-event results/matches into `docs/data/*.json` for the static site (`export_league_site`). |
| `identity.py` | Read/write overlay for `data/pilot_identity.csv` / `pilot_profile.csv` / `pilot_merge_log.csv` — see "Pilot identity overlay" below. Pure; never reads event data itself. |
| `pilot_identity_cli.py` | Dry-run-by-default CLI (`merge`, `--show`) for recording an identity merge and rewriting the league/site outputs from it. The only intended way to edit `data/*.csv`. |
| `gui_app.py` | PySide6 desktop GUI wrapping all of the above. QSettings persists backup URL between sessions. |
| `change_model.py` | Reads/writes the `docs/` CSV config files (aliases, archetype rules, mappings, catalog). |
| `preset_cli.py` | Interactive terminal CLI with saved state in `.preset_cli_state.json`. |

### Data flow

1. `run_generation()` in `metagame_input_generator.py` is the single orchestration entry point used by both the CLI (`main()`) and the GUI (via `QThread`).
2. API calls use a three-tier fallback: **primary API → backup VPS (configured via `--backup-url` or `MTG_BACKUP_URL` env var) → local JSON cache** (`outputs/cache/`).
3. Deck names are normalized via a pipeline: raw API name → `user_deck_mapping.csv` override → `deck_aliases.csv` canonicalization → archetype assignment from `archetype_rules.csv` (with `heuristic_archetype()` as final fallback).
4. Challenge data is parsed from MTGGoldfish HTML. Discovery uses the MTGO calendar API first, falling back to scraping the `/decklists` index page.
5. Outputs are written to `outputs/YYYY/MM/WNN_YYYY-MM-DD_to_YYYY-MM-DD/`. The persistent challenge history CSV lives at `outputs/challenge_history_<format>.csv` (not inside weekly run dirs).

### League + site export

- `outputs/league/season_config.csv` (`Season,StartDate,EndDate`) lists every season on record. `outputs/league/results/<EventID>.csv` and `matches/<EventID>.csv` hold per-event data (one row per pilot / per bracket match); these are the only inputs, read fresh on every rebuild — there is no incremental/running total.
- `league_engine.build_season_table(results_dir, season_start, season_end, as_of, ...)` rebuilds one season's standings from scratch every call, `write_season_league_csv()` writes `outputs/league/pilot_league_<slug>.csv`.
- `league_site_export.export_league_site(league_dir, docs_data_dir, as_of, ...)` rebuilds `docs/data/season_<slug>.json` + `pilots_<slug>.json` for **every** season on record (not just the one a given run touched) plus the `docs/data/seasons.json` manifest, so `docs/data/` always mirrors the current state of `outputs/league/results`.
- Grouping is by identity (`_identity_key(login_id, pilot)`, duplicated on purpose in both `league_engine.py` and `challenge_history_engine.py` rather than shared — see "Pilot identity overlay" below), not raw `Pilot` string, so a mid-season rename doesn't split one pilot into two rows.
- **`outputs/league/pilot_league_Summer_2026.csv` is deliberately excluded from git** (see `.gitignore`) even though every other season's `pilot_league_*.csv` is tracked: Summer 2026's results only start 2026-07-13, six weeks into a season that starts 2026-06-01, so the file is a known-partial table and publishing it under a season name that promises more than it contains was judged worse than not tracking it. It still exists on disk and is rebuilt on every run/apply, it just never enters git for that one season.

### Pilot identity overlay (`data/`)

Resolves one or more MTGO loginids to a single canonical `pilot_id`, so a real person who is renamed on mtgo.com (already handled by the "frozen at first capture" pilot-name rule below) *or* plays under two different accounts can be shown as one identity in the league table and in `challenge_history_engine`'s `BestPilots`/`DistinctPilots` tables.

- `data/pilot_identity.csv` (`loginid,pilot_id,role,added_on,source,evidence,note`) — `role` is `primary` or `alias`. A loginid absent from this file resolves to itself: **the whole layer is a no-op until the file exists and has a row for that loginid** (confirmed by `tests/test_pilot_identity.py`'s zero-regression test, which recomputes the full season/site output with no `data/` present and asserts every value-bearing field matches the pre-feature baseline byte-for-byte).
- `data/pilot_profile.csv` (`pilot_id,display_name,x_handle,x_consent,x_confirmed_on,profile_hidden,note`) — optional per-`pilot_id` overrides. `x_handle` is only ever serialized into the site JSON when `x_consent` is true (`league_site_export.py` is the one and only place that reads `x_consent`, so a non-consented handle never crosses the wire); `profile_hidden` keeps the pilot in the season table under their name but omits their key from `pilots_<slug>.json` entirely (a direct profile link 404s, same as an unknown id).
- `data/pilot_merge_log.csv` (`timestamp,action,pilot_id,loginid,source,evidence,operator_note`) — append-only audit trail, one row per loginid↔pilot_id change.
- `src/identity.py` is the only reader/writer of these three files (`resolve()`, `display_name()`, `is_profile_hidden()`, etc.); it never reads event data itself — callers (`league_engine`, `challenge_history_engine`, `league_site_export`) pass in whatever "latest observed name" they already computed as a fallback.
- **The only intended way to edit `data/*.csv` is `src/pilot_identity_cli.py merge`** (never by hand, never via the GUI). It dry-runs by default, always printing the full before/after report (validations a–e, season leaderboard diff, row-count deltas); `--apply` is required to write anything, and on any validation failure — including (e), no `pilot_id` may have two results in the same `EventID`, checked across the **full** history, every season, not just the current window — nothing is written, not even a partial file.
- A `display_name` override in `pilot_profile.csv` is a deliberate choice, not necessarily what "latest event wins" would compute on its own — record *why* in that row's `note` field (e.g. "canonical name is the player's choice; the raw data alone would pick the other account's name, which has a later event"), since nothing else preserves that reasoning.
- **Correctness pitfall already hit once, worth remembering:** any code that computes a pilot's "current" display name from a list of historical `Pilot` strings must key off the actual latest `EventDate`, not "whichever distinct value's first occurrence sorts last" (e.g. `dict.fromkeys` over a date-sorted list) — those two only agree when a name changes once, monotonically, and silently diverge as soon as two merged loginids' event-date ranges interleave (exactly what an identity merge produces). A second, related trap: if a canonical `display_name` override differs from that true latest-dated raw name, any "prior names" / "formerly known as" computation must still include that raw name as a candidate — filtering candidates by "not equal to hist['current']" instead of "not equal to the (possibly-overridden) name actually shown" silently drops a real historical name from site search. Both bugs lived in `league_site_export.py` (`_name_history()` and `build_season_site_data()`'s `prior_names` computation) and only surfaced when the first real merge (`MeninoNey`/`MeninooNey`, 2026-08-26) exercised the interleaved-dates + override case for the first time — see `git log` for both fixes' full reasoning.

### Configuration files (`docs/`)

| File | Columns | Purpose |
|---|---|---|
| `archetype_rules.csv` | `pattern, archetype, match_type, priority` | Maps deck name patterns → archetype labels. `match_type` is `exact`, `contains`, or `regex`. Lower `priority` value = applied first. |
| `deck_aliases.csv` | `pattern, canonical_name, match_type, priority` | Canonicalizes raw deck names. Same matching semantics as archetype rules. |
| `user_deck_mapping.csv` | `raw_name, canonical_name, archetype` | Exact overrides for Meta deck names (applied before alias rules). |
| `challenge_deck_mapping.csv` | `raw_name, canonical_name, archetype` | Additive overrides for Challenge-only deck names (merged with user mapping at runtime). |
| `archetype_catalog.csv` | `archetype` | Known archetype list shown in the GUI's Review Queue dropdown. |

### Key design decisions

- `normalize_name()` lowercases and collapses whitespace — all rule comparisons go through it.
- The `My Deck Winrate` column is imputed to `0.5` when no matchup data exists; imputed rows get a yellow fill in XLSX. 180-day fallback rows get green fill.
- Rogue grouping: decks with `Meta < rogue_threshold` (default 0.5%) are collapsed into a single "Rogue" row in the `_rogue_grouped` outputs.
- `change_model.py` creates `.bak` backups of CSV files before writing to prevent data loss from GUI edits.
- The GUI saves/restores settings (backup URL, form fields) via `QSettings` with org `GitHubCopilot` / app `MTG Metagame Studio`.
- **Pilot names in `challenge_history_modern.csv`/`premier_history_modern.csv` are frozen at first capture.** mtgo.com (and MTGGoldfish, which supplies Pilot for most Challenge rows via `_parse_single_challenge` — the mtgo.com JSON there is used only for the stable `loginid`/deck-signature lookup; premier rows and collision-losing Challenge rows get Pilot straight from the mtgo.com JSON via `_classify_event_decks`) shows whatever an account is **currently** named, not what it was named when the event was played. Re-deriving a historical row from a fresh fetch or from the JSON cache will silently rewrite pilot names throughout the file. Confirmed cases: `justAlice` → `justFeather` (5 events) and `MARZIANO` → `surgetemelo` (1 event), renamed on mtgo.com's side between 2026-07-23 and 2026-07-27 — verify with `outputs/cache/mtgo_json/modern/12847739.json` (place 5, event 2026-07-22) against the corresponding row in history. Idempotency by EventID (a run's window is only ever synced once) is therefore a **correctness property**, not an optimisation — `sync_challenge_history_window` only purges+rebuilds rows for events still in *that run's* fresh window; re-running it over an already-synced window will re-derive Pilot from a fresh/cached fetch and can rename rows silently. Do not add a "refresh existing rows" path without first deciding what happens to a renamed account — nothing currently persists the stable per-account id (`loginid`) that would make that decision safe. `backup_history_file()` in `challenge_history_engine.py` snapshots both history CSVs to `backups/history/` before every write, precisely because this makes them unregenerable-by-design.
