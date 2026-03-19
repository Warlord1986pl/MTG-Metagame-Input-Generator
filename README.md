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

## License

MIT (see `LICENSE`).
