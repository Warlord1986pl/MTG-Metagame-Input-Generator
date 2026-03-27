# MTG Metagame Input Generator

Standalone data-generation tool for MTG metagame analysis.

This project fetches metagame and matchup data, normalizes deck names/archetypes, and exports ready-to-analyze files for local workflows.

## What This Project Does

- Fetches metagame data from API
- Builds `Deck`, `Meta`, `Winrate`, `Archetype`, `My Deck Winrate`
- Adds sample-size columns:
  - `Winrate Game Count`
  - `My Deck Winrate Game Count`
- Applies canonical name and archetype mapping rules
- Supports configurable Rogue threshold (`--rogue-threshold`)
- Exports both standard and Rogue-grouped outputs
- Organizes outputs into date-range folders

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
  --rogue-threshold 0.5
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

What preset mode gives you:

- asks where to create workspace folder structure,
- lets you create presets like `Modern_Domain_Zoo` or `Modern_Jeskai_Control`,
- keeps separate `configs`, `outputs`, and `history` per preset,
- on next run, lets you pick an existing preset and generate weekly/history snapshots quickly.

What GUI mode gives you:

- form for format, date range, deck name, and core generator parameters,
- one-click weekly snapshot generation,
- live execution log inside the window,
- quick open for output folder and main XLSX,
- clickable list of generated files.
- built-in editor for canonical deck names and archetype assignment.
- one-click "Regenerate Grouped Now" from the editor tab.

GUI editor mode gives you:

- loads the latest generated `metagame_input.csv` into a table,
- lets you reclassify a deck into an archetype from a dropdown,
- lets you type your own custom archetype and save it into a separate catalog,
- lets you rename canonical deck names and persist exact alias + mapping rules,
- writes changes back to config CSVs in `docs/`.

Default GUI generation profile is now streamlined for analysis:

- input file: `metagame_input.csv` (clean, per-deck rows),
- final grouped output: `metagame_input_grouped.xlsx`.

Use CLI `--output-profile full` when you want all auxiliary reports/files.

### 3) Check outputs

Standard weekly output:

- `outputs/YYYY-MM-DD_to_YYYY-MM-DD/`

History output:

- `outputs/history/YYYY-MM-DD_to_YYYY-MM-DD/`

Generated files include:

- `metagame_input.xlsx`
- `metagame_input.csv`
- `metagame_input_rogue_grouped.xlsx`
- `metagame_input_rogue_grouped.csv`
- `unknown_archetypes.csv`
- `alias_suggestions.csv`

## User Guide

Guide for non-technical users:

- `docs/DATA_GENERATOR_GUIDE.md`

## Configuration Files

- `docs/archetype_rules.csv`
- `docs/deck_aliases.csv`
- `docs/user_deck_mapping.csv`
- `docs/archetype_catalog.csv`

## License

MIT (see `LICENSE`).
