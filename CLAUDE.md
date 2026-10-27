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
python src/metagame_input_generator.py --format Modern --history-points 1 --metagame-window-days 14 --rogue-threshold 0.5 --include-challenge-decklist
```

### PowerShell launchers
```powershell
# Single weekly snapshot
./run_weekly.ps1 -Format Modern

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
| `alias_check.py` | Card-list gate for a deck-alias merge (`check_alias`, `check_window_against_baseline`, `run_alias_checks_for_window`) — see "Deck-alias merge gate" below. |
| `check_alias_cli.py` | `python src/check_alias_cli.py check-alias --source X --target Y --window YYYY-MM-DD:YYYY-MM-DD` — the manual pre-merge gate, run before adding a row to `deck_aliases.csv`. |
| `broodscale_rule.py` | TEMPORARY card rule: maindeck with >=1 Basking Broodscale -> Deck `Broodscale Combo` / Archetype `Combo`, over every name-based source. See "Broodscale card rule" below. |
| `broodscale_rule_cli.py` | `apply [--apply]` (relabel history from cached JSON, dry-run default), `reverse-alias [--apply]` (undo a removed name alias) and `recompute --since YYYY-MM-DD` (rebuild run dirs' challenge + metagame outputs). |
| `moonshadow_rule.py` | TEMPORARY second card rule: maindeck >=1 Moonshadow + >=1 Dark Matter Manipulator, no Death's Shadow, no blue card -> `Rakdos Midrange` / `Midrange`, applied after Broodscale. See "Moonshadow card rule" below. |
| `challenge_meta.py` | Meta = share of Challenge Top 32 entries after the card rule; builds the run's metagame-statistics input. See "Challenge-based Meta" below. |
| `deckdb_input.py` / `deckdb_materials.py` | Input mode `deckdb`: DeckDB export as the only source (K33), working materials. See "Input mode deckdb" below. |

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

**TEMPORARY, expires after 2026-08-31 — review/remove this note then:** because that file is gitignored, it only ever exists on this one machine's disk. Since a computer reset mid-operation on 2026-08-26 made that risk concrete, until the Summer 2026 season closes (2026-08-31) it's manually copied to OneDrive after each recompute, pasted by hand (no pipeline hook — not worth touching core code for something that expires in 5 days):
  ```powershell
  Copy-Item "E:\github\MTG-Metagame-Input-Generator\outputs\league\pilot_league_Summer_2026.csv" "C:\Users\kmalo\OneDrive\pilot_league_Summer_2026_$(Get-Date -Format yyyyMMdd_HHmmss).csv"
  ```
  Once Autumn 2026 (or any season after Summer 2026) is the current season, its `pilot_league_*.csv` is tracked in git normally (see above) and this whole note — and the timestamped copies already sitting in OneDrive — stop being relevant.

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
| `deck_aliases.csv` | `pattern, canonical_name, match_type, priority, archetype, comment` | Canonicalizes raw deck names. Same matching semantics as archetype rules. `archetype`/`comment` are optional trailing columns (added 2026-09 for the WBG→Devoted Combo merge) — blank on every pre-existing row, documentation/audit only (not read as an override; actual archetype resolution still runs through `user_deck_mapping.csv`/`challenge_deck_mapping.csv`/`archetype_rules.csv`, which must independently agree — see below). `change_model.py`'s `ALIASES_COLUMNS` includes both so a GUI-driven rewrite of this file never silently truncates them. |
| `user_deck_mapping.csv` | `raw_name, canonical_name, archetype` | Exact overrides for Meta deck names (applied before alias rules). |
| `challenge_deck_mapping.csv` | `raw_name, canonical_name, archetype` | Additive overrides for Challenge-only deck names (merged with user mapping at runtime). **Takes precedence over `deck_aliases.csv` for Challenge data** — a raw name present here resolves via `mapped_deck or canonicalize_deck_name(...)`, so editing `deck_aliases.csv` alone does not merge a Challenge deck name; both files must agree (see "Deck-alias merge gate" below). |
| `alias_check_config.csv` | `key, value` | `min_similarity` (default `0.55`): the maindeck-multiset-similarity cutoff `alias_check.py` uses to verdict a merge safe/partial/dangerous. Config, not code, so a verdict stays inspectable after the fact. |
| `archetype_catalog.csv` | `archetype` | Known archetype list shown in the GUI's Review Queue dropdown. |

### Deck-alias merge gate

A deck-name alias that collapses one raw name into another canonical deck (e.g. an MTGO color-code
placeholder like `WBG` folded into `Devoted Combo`) must never be applied on the strength of the
name alone — the name gives no guarantee every list filed under it is actually the same deck, and a
bad merge inflates that deck's share and skews the whole field's rogue/median cuts.

- `python src/check_alias_cli.py check-alias --source WBG --target "Devoted Combo" --window 2026-08-31:2026-09-13`
  is the **manual pre-merge gate** — run it against current (not-yet-merged) history before adding
  a row to `deck_aliases.csv`. It compares every source-name list's maindeck card multiset (basic
  lands excluded — the same scope `challenge_mtgo_source.multiset_similarity()` uses for its own
  nearest-neighbor classifier, reused here rather than a second mechanism) against every
  target-name list and prints median/min/max similarity, the cards common to every list compared,
  and an explicit verdict (`safe` / `partial` / `dangerous`) against `docs/alias_check_config.csv`'s
  `min_similarity`. Exit code is non-zero unless the verdict is `safe`.
- Once an alias is live, ingestion resolves the raw name away immediately, so no row ever has
  `Deck == "WBG"` again — the *auto* gate (`run_alias_checks_for_window`, wired into
  `run_generation()` right after the Challenge history sync) therefore checks differently: for
  every alias in `deck_aliases.csv` whose `pattern != canonical_name` (a true merge, not a
  self-mapping), it compares **this window's** lists under the canonical name against a reference
  library of that canonical name's lists from **before** the window (`check_window_against_baseline`
  in `alias_check.py`) — catching drift going forward without needing the vanished raw label. This
  always writes `alias_report_<window>.csv` into the run dir, even when empty (empty = every
  checked list matched; a non-empty file is never "nothing to see", and a list below threshold must
  never be silently absorbed).
- Applying a merge touches **three** files that must agree, not one: `docs/deck_aliases.csv` (the
  canonicalization rule + `archetype`/`comment` documentation), and — because
  `user_deck_mapping.csv`/`challenge_deck_mapping.csv` are applied *before* `deck_aliases.csv` and
  set canonical name + archetype directly — `docs/user_deck_mapping.csv` and
  `docs/challenge_deck_mapping.csv`, if the raw name already has a row there (as `WBG` did, mapping
  to itself with archetype `Midrange`). Editing `deck_aliases.csv` alone silently does nothing for a
  raw name that already has a higher-precedence mapping row.
- Retroactively relabeling an **already-synced** history window (so a trend line doesn't have a raw
  name still sitting mid-history) cannot go through a normal resync: `sync_challenge_history_window`
  treats any already-resolved `Deck` value as sticky and will not overwrite it with a fresh
  classification, on purpose (see the Pilot-freezing note below for why). The sanctioned path is the
  same one used for a human Review Queue correction, `apply_challenge_review_selections()` (one
  `{event_id, pilot, resolved_deck}` selection per affected row) — it re-derives `Archetype` through
  the normal alias/mapping/rules pipeline and is safe to run across the full history (not just the
  window under test), since it only touches rows matched by `(EventID, Pilot)` and never re-fetches
  anything over the network.

### Broodscale card rule (TEMPORARY, until the card classifier exists)

- Since 2026-10-12, any Challenge/Premier entry whose **maindeck** has >= 1 Basking Broodscale is
  `Broodscale Combo` / `Combo`, whatever the submitted name (Eldrazi Ramp, Eldrazi, color codes...).
  Sideboard copies never count. It beats mappings, aliases, the classifier, a Review Queue decision
  and the sticky resolved value in history.
- Applied in `build_challenge_dataset` (fresh rows + classifier library) and re-asserted on the
  persisted history right after every sync in `run_generation` (`apply_broodscale_rule_to_history`,
  cached JSON only, touches Deck/Archetype only, never Pilot/LoginID).
- The pre-rule label is frozen in `outputs/broodscale_override_ledger_<format>.csv` on first sighting;
  each run writes `statistics/challenges/broodscale_override_<window>.csv` (OVERRIDE rows, SUMMARY per
  submitted name, and two report-only CONTROL lists: Blade of the Bloodchief without Broodscale, and
  `Broodscale Combo` without Broodscale -- the latter are name-mapped, e.g. `Eldrazi`, and are NOT moved).
- The `Eldrazi` -> `Broodscale Combo` name alias was removed from `docs/*.csv` (2026-10-12); the 14
  history entries it had put there without a Broodscale were relabeled `Eldrazi` (12 by their
  MTGGoldfish name, 2 collision-event rows by the classifier) via `broodscale_rule_cli.py reverse-alias`,
  recorded in `outputs/broodscale_reverse_alias_ledger_<format>.csv` and the log's
  `REVERSE_REMOVED_NAME_ALIAS` section.
- Remove together with this section once a general card classifier replaces it.

### Moonshadow card rule (TEMPORARY, since 2026-10-19)

- `moonshadow_rule.py`: a Challenge/Premier entry whose **maindeck** has >= 1 Moonshadow AND >= 1
  Dark Matter Manipulator, **no** Death's Shadow and **no** blue card is `Rakdos Midrange` /
  `Midrange`, whatever the submitted name (Rakdos Aggro, BR, ...). Death's Shadow lists and blue
  lists keep their name (`EXCLUDED` section of the log); a list with only one of the two cards is
  untouched (`CONTROL_DMM_WITHOUT_MOONSHADOW`). "Blue" = a maindeck card with `COLOR_BLUE` in its
  mtgo.com `colors`; mtgo.com reports every land as COLORLESS, so a blue dual land alone is not seen.
- Always after the Broodscale rule (`card_rule_deck()` for fresh rows; in `run_generation` the
  history pass runs right after `apply_broodscale_rule_to_history`). Own ledger
  `outputs/rakdos_override_ledger_<format>.csv` (tracked), own per-window log
  `statistics/challenges/rakdos_override_<window>.csv`.
- `challenge_meta.py` blanks Videre winrate for `Rakdos Midrange` and `Rakdos Aggro`
  (`label_mixed_by_card_rule`, Importance `meta_only`) in any window where the rule moved >= 1 entry.
- Offline: `python src/broodscale_rule_cli.py apply-moonshadow [--apply]`, then
  `recompute --since 2026-10-05 --trend-only-since 2026-09-21 --trend-weeks 12` (W40/W41 only get
  their trend-history row rebuilt in a temp dir -- the earliest rule list is 2026-10-02 -- their
  published files stay as they were).
- Remove together with the Broodscale section once the card classifier exists.

### Challenge-based Meta (`challenge_meta.py`, since 2026-10-12)

- With `--include-challenge-decklist`, metagame statistics (`deck_analysis*`, encounter, record,
  trend) no longer use the Videre per-name Meta: `Meta` = the deck's share of the window's Challenge
  Top 32 entries after the card rule (`MetaSource = challenge_top32_cardrule`); the Videre value stays
  in `Meta_Videre`, control only. For W41 the two agreed at r = 0.998 before the rule. Input file:
  `<run_dir>/metagame_input_challenge_top32.xlsx` (+ `.csv`, which challenge statistics also read for
  Meta Share % / Universe A). Weekly history: `outputs/metagame_history_challenge_top32_<format>.csv`,
  separate from the Videre `metagame_history_<format>.csv` so trends never mix sources; it starts at
  W30 (2026-07-13, first window with EventIDs/decklists).
- Winrate still comes from Videre (per submitted name, can't be split by cards): for `Broodscale Combo`
  and `Eldrazi Ramp` in any window where the rule moved >= 1 Challenge entry, Winrate/Game Count/record
  probabilities are NaN with `WR_Flag = label_mixed_by_card_rule`; decks with no Videre row get
  `no_videre_winrate`. A NaN winrate gives Importance from Meta alone (weight rescaled to 1,
  `Importance_Flag = meta_only`; Quartile/Prep Priority computed normally from it), leaves Performance
  Label blank, is excluded from the archetype's weighted winrate (named in its
  `WR_Flag`), and is not drawn on the expected-wins chart (footnote instead).
- No silent fallback: if the Challenge-based Meta can't be built (e.g. NEEDS_MANUAL_REVIEW rows),
  metagame statistics are skipped with an ERROR. Without `--include-challenge-decklist` the old Videre
  path is unchanged. `--skip-league` skips the league/site update; `--trend-weeks N` sets the trend
  window (default 4; the published charts use 12).
- `python src/broodscale_rule_cli.py recompute --since 2026-07-13 --trend-weeks 12` rebuilds all of this
  offline for already-generated run dirs.

### Input mode "deckdb" (`deckdb_input.py`, `deckdb_materials.py`, since 2026-10-21, WORKING)

- DeckDB decisions K33 (DeckDB is the only source) and K36 (conversion, not winrate; pilot measures;
  format period). Spec: `docs/deckdb_metodologia.md` (accepted 2026-10-21 with decisions, see there).
  `python src/metagame_input_generator.py --input-mode deckdb --as-of 2026-10-18 --trend-weeks 12`
  reads the paths in `docs/deckdb_config.csv` (`export_path` required; `format_events_path` = DeckDB
  `rules/format_events.yaml`, read only; `cards_path` = `deckdb export-cards` output, enables the card
  layer) and writes only `outputs/deckdb_working/WORKING_<as-of>/` -- no Videre/MTGGoldfish/mtgo.com
  fetch, no mapping/alias/archetype rule, no card rule, no history/league/site write. Default
  `--input-mode legacy` is the unchanged pipeline (W42 rerun identical except the XML `generated_at`).
- Three time levels, one subdirectory each; `--deckdb-level week|month|all` (default `all`) picks the
  set (DeckDB K41, order 2026-10-26 a, spec `docs/deckdb_metodologia.md` section (h)). Weekly set
  `week_<YYYY-Www>` (last complete ISO week): share of the top 10 decks + one pooled "other decks" row
  + "no deck", and of every archetype; change vs the previous week, emphasised only when the 95%
  event-bootstrap interval (events drawn in both weeks independently, 2000 draws, seed 20261026)
  excludes 0; presence, pilots, trend of N disjoint ISO weeks, Top 8 of every event (deck + variant,
  no players, no rates), new cards (first list of the deck with the card, >= 2 pilots in the week).
  No deck result measure weekly (no percentile, conversion, Top 8 rates, trophies). Monthly set:
  `month_<YYYY-MM>[_incomplete]` (+ trophies, D2 variants, pilot measures, results concentration,
  12-week trend), `format_period_<start>_to_<as-of>` (mean place percentile = main measure, conversion
  auxiliary, both vs. their common baseline with a pilot-bootstrap interval only at >= 10 pilots;
  colour only where the interval excludes the baseline, grey otherwise and on "no deck"; trophies,
  pilot measures for decks only), cards. Winrate is gone (K36);
  the expected conversion lives only in `scripts/deckdb_methodology_stats.py` (warns > 1 pp).
- Triage gate (DeckDB K43 + supplement entry 330, orders 2026-10-26 e and k): `triage_<data_version>.json` (DeckDB
  `scripts/weekly_triage.py`) must sit next to the assignment export for its `data_version`. Status `complete` and
  `pending_notice` run (pending_notice: "<N> list group(s) awaiting review, <X>% of this week's lists, shown as Rogue &
  unclassified" in `status.md` / STATUS / speaker notes only, never on a chart); `pending_blocking` (and the older
  `pending`) is refused unless `--allow-pending-triage`, which then marks every material ("triage pending: <n> list
  group(s) awaiting decision" in chart footers, STATUS sheet, `status.md`, speaker notes).
- Rogue and unassigned lists are one row labelled "Rogue & unclassified" (`di.NO_DECK_LABEL`) in every material.
- Presentation folder (`deckdb_folder.py`, order 2026-10-26 k, B7; spec `docs/deckdb_metodologia.md` (k)): a run with
  level `week`/`all` also writes ONE flat folder `outputs/presentation/<year>-W<nn>/` (01..09 charts, `data_*.csv`,
  `status.md`, `speaker_notes.md`, `GLOSSARY.md`, `README.md`); the previous month's `m01`/`m02` charts and `m_data_*`
  join when the week contains the first Sunday of the month, `--presentation-month YYYY-MM` forces a month (incomplete
  marked "incomplete month"). Fixed colours: archetypes in `deckdb_presentation.ARCHETYPE_COLOURS`, decks in
  `docs/deckdb_colours.csv` (16 validated colours; a repeat on one trend chart is dashed and listed in status.md).
  Run with the project venv (`E:/github/.venv/Scripts/python.exe`; the bare `python` may lack yaml).
- Presentation set (`deckdb_presentation.py`, order 2026-10-26 b, spec section (i)): every package also gets
  `presentation/` (week: `decks_share.png`, `archetypes_share.png`, trends; month: trends; format period:
  `deck_results.png`; cards: `list_changes.png` without `mana_only_land` cards -- DeckDB K42 column of
  `export-cards`, read into `CardLayer.mana_only_ids`) plus `speaker_notes.md`. Plain titles, one footer line, no CI
  whiskers (the interval only sets the colour), horizontal bars, 16:9; no definitions, seeds, sha or rules version in
  `presentation/`. The working charts are unchanged.
- Card layer (`deckdb_cards.py`, only when `cards_path` is set): `cards_<M1>_vs_<M2>[_incomplete]` --
  presence per (deck, zone, oracle_id) per month, basics (`is_basic`) excluded; change between the two
  last months for decks with >= 30 lists and >= 10 pilots in both, pilot bootstrap (no pairing), BH
  q = 0.10 over one family (all decks), reported at |change| >= 10 pp; the within-variant component
  has its own bootstrap and its own BH family (q = 0.10; FDR controlled separately per category):
  "zmiana list" = |within| >= 10 pp and significant there, any other reported change = "zmiana
  składu talii". 0% in
  one month is never tested: gone reported if (1 - share of month-1 pilots playing it)^(month-2
  pilots) < 0.05 and >= 3 pilots in month 1; new card / first appearance reported with >= 2 pilots;
  the rest are observations. Pilot list-change distributions. The card file must match the
  assignment lists 1:1, its name-match version and its `data_version`, checked before anything is
  written.
- Refuses (`DeckdbInputError`, exit 2) an assignment export without a single non-empty
  `data_version` (always, card layer or not; exports from DeckDB c4958cb on), a file with > 1
  `rules_version` and any period ending after
  the file's last / starting before its first `event_date`; all periods are checked before writing.
- Every material: `rules_version` + export sha256 (chart footer, STATUS, CSV columns) and the
  glossary (`Glossary` sheet, `GLOSSARY.md`, chart footer); all materials in English, charts drawn by the legacy chart functions (optional parameters, legacy defaults unchanged). No loginid/player in any material.
  Legacy materials without a counterpart: `deckdb_materials.OMITTED_MATERIALS` (`Omitted` sheet).
- Slide mode (`deckdb_slides.py`, since 2026-10-22): a separate renderer, the report charts above stay as
  they are. 1920x1080 at 144 dpi (1 figure pt = 1 slide pt), no text below 14 pt; share bars <= 10 decks +
  one pooled "other decks" row (+ "no deck"), share and Top 8 conversion only; matrices <= 12 rows (deck
  pilot measures on their own slide); card changes <= 8 per slide (list changes by |within-variant
  component|, composition changes by |presence change|); one source line per slide, glossary in the speaker
  notes. `python scripts/deckdb_presentation_test.py --as-of 2026-10-21 --out outputs/deckdb_working/presentation_test`
  renders it, writes `presentation_test.pptx` and checks fonts, cut-off and overlapping texts.
  In a run: `--input-mode deckdb --as-of YYYY-MM-DD --deckdb-slides` adds `WORKING_<as-of>/slides/*.png` and
  `WORKING_<as-of>/deckdb_slides_WORKING_<as-of>.pptx` next to the report materials (refused without `--input-mode deckdb`).

### Key design decisions

- `normalize_name()` lowercases and collapses whitespace — all rule comparisons go through it.
- No "my deck" matchup-winrate column exists anywhere in the pipeline (removed 2026-09; the old `My Deck Winrate` column silently imputed `0.5` for zero-game rows, indistinguishable from a real 50% result). Missing data is `None`/blank, never a value that could also be read as a real result.
- Rogue grouping: decks with `Meta < rogue_threshold` (default 0.5%) are collapsed into a single "Rogue" row in the `_rogue_grouped` outputs.
- `change_model.py` creates `.bak` backups of CSV files before writing to prevent data loss from GUI edits.
- The GUI saves/restores settings (backup URL, form fields) via `QSettings` with org `GitHubCopilot` / app `MTG Metagame Studio`.
- **Pilot names in `challenge_history_modern.csv`/`premier_history_modern.csv` are frozen at first capture.** mtgo.com (and MTGGoldfish, which supplies Pilot for most Challenge rows via `_parse_single_challenge` — the mtgo.com JSON there is used only for the stable `loginid`/deck-signature lookup; premier rows and collision-losing Challenge rows get Pilot straight from the mtgo.com JSON via `_classify_event_decks`) shows whatever an account is **currently** named, not what it was named when the event was played. Re-deriving a historical row from a fresh fetch or from the JSON cache will silently rewrite pilot names throughout the file. Confirmed cases: `justAlice` → `justFeather` (5 events) and `MARZIANO` → `surgetemelo` (1 event), renamed on mtgo.com's side between 2026-07-23 and 2026-07-27 — verify with `outputs/cache/mtgo_json/modern/12847739.json` (place 5, event 2026-07-22) against the corresponding row in history. Idempotency by EventID (a run's window is only ever synced once) is therefore a **correctness property**, not an optimisation — `sync_challenge_history_window` only purges+rebuilds rows for events still in *that run's* fresh window; re-running it over an already-synced window will re-derive Pilot from a fresh/cached fetch and can rename rows silently. Do not add a "refresh existing rows" path without first deciding what happens to a renamed account — nothing currently persists the stable per-account id (`loginid`) that would make that decision safe. `backup_history_file()` in `challenge_history_engine.py` snapshots both history CSVs to `backups/history/` before every write, precisely because this makes them unregenerable-by-design.
