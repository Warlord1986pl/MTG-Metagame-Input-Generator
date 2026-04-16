# MTG Metagame Input Generator

Standalone data-generation tool for MTG metagame analysis.

Fetches metagame and matchup data from the Videre Project API, normalizes deck names and archetypes, tracks Challenge tournament history, and exports ready-to-analyze files and charts for local workflows.

## What This Project Does

- Fetches metagame data from primary API with automatic fallback to a configurable backup server
- Builds `Deck`, `Meta`, `Winrate`, `Archetype`, `My Deck Winrate` columns
- Adds sample-size columns: `Winrate Game Count`, `My Deck Winrate Game Count`
- Applies canonical name and archetype mapping rules from `docs/`
- Supports configurable Rogue threshold (`--rogue-threshold`)
- Exports both standard and Rogue-grouped outputs
- **Challenge Analytics** – fetches all Challenge events in the selected date window, builds a persistent history CSV, and generates statistics + charts (see below)
- Organizes all outputs into weekly date-range folders under `outputs/`

## Quick Start

### 1) Install dependencies

```sh
pip install -r requirements.txt
```

### 2) Run weekly generation

```sh
python src/metagame_input_generator.py \
  --format Modern \
  --history-points 1 \
  --metagame-window-days 14 \
  --my-deck "Domain Zoo" \
  --my-window-days 90 \
  --my-fallback-window-days 180 \
  --rogue-threshold 0.5 \
  --include-challenge-decklist
```

Or use one-command launchers (PowerShell):

```powershell
./run_weekly.ps1
./run_history_4.ps1 -AnchorSunday 2026-03-15
```

Preset-based interactive mode (recommended for daily use):

```powershell
./run_preset_cli.ps1
```

Desktop GUI (PySide6):

```bat
run_gui.bat
```

## GUI Overview

### Generation tab

- Form for format, date range, deck name, and core generator parameters
- One-click weekly snapshot generation
- Live execution log inside the window
- Quick open for output folder and main XLSX
- Clickable list of generated files

### Backup server

The GUI header contains a **Backup URL** field. If the primary Videre API is unreachable, the generator falls back to the configured backup server automatically. The backup URL is saved in QSettings (persists between sessions). On startup, the app silently tests the backup server and shows the result in the status label — no need to click "Test Backup" manually every time.

### Editor tab

- Loads the selected `metagame_input.csv` or Challenge decklist into an editable table
- Reclassify deck → archetype via dropdown
- Rename canonical deck names; changes persist as exact alias + mapping rules in `docs/`
- Source selector lets you switch between Meta and Challenge CSV sources from any run

### Review Queue

- Automatically surfaces rows with unknown archetype, unrecognized deck names, or first-seen names
- Save individual rows or all rows at once
- Saved rows disappear from the queue immediately (no revert)
- Works for both Meta and Challenge sources with separate mapping files

### Challenge Analytics tab

- Generates charts and an Excel workbook from the persistent challenge history
- Uses the **same selected date window** as meta stats
- **Challenge vs Meta** – compares Top32/Top8 event frequency against meta share; same encounter-probability cutoff as Encounter Probability charts (only decks above the threshold are shown)
- **Delta Ranking** – shows Top32 event frequency minus meta share for all decks that meet the encounter-probability threshold (same deck set as Challenge vs Meta — no arbitrary top-8/bottom-8 split)
- **Conversion Matrix** – Top32 / Top8 / Winner event frequency heatmap
- All charts use the same filtered deck set for consistency

## Outputs

Standard weekly output is organized as:

```
outputs/
  YYYY/MM/WYYYY-MM-DD_to_YYYY-MM-DD/
    metagame_input.csv
    metagame_input.xlsx
    metagame_input_rogue_grouped.xlsx
    unknown_archetypes.csv
    alias_suggestions.csv
    challenge_C64_YYYY-MM-DD_decklist.csv   (one per event)
    challenge_C32_YYYY-MM-DD_decklist.csv
    challenge_statistics.xlsx
    challenge_vs_meta_decks.png
    challenge_vs_meta_archetypes.png
    challenge_delta_ranking_decks.png
    challenge_delta_ranking_archetypes.png
    challenge_conversion_matrix_decks.png
    challenge_conversion_matrix_archetypes.png
  challenge_history_modern.csv              (persistent, all time)
```

## Configuration Files

| File | Purpose |
|---|---|
| `docs/archetype_rules.csv` | Rules mapping deck names → archetypes |
| `docs/deck_aliases.csv` | Alias rules for canonicalizing deck names |
| `docs/user_deck_mapping.csv` | Manual overrides for Meta deck names |
| `docs/challenge_deck_mapping.csv` | Manual overrides for Challenge deck names (separate from Meta) |
| `docs/archetype_catalog.csv` | Known archetype list |

## User Guide

Guide for non-technical users:

- `docs/DATA_GENERATOR_GUIDE.md`

## License

MIT (see `LICENSE`).
