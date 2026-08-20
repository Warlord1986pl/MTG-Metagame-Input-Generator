# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Session recovery notes (2026-07-23)

**Status: Phase 2a done (dedup + C96 visibility) and verified. Paused here by user choice — resume with
live end-to-end testing on Monday 2026-07-27. Phase 2b (full tier-agnostic table/sheet refactor) not
started. Nothing committed yet.**

**Why paused:** user doesn't want to pull fresh live data / touch output files today (risk of making a
mess in the data before a planned break). Explicitly asked to defer running `run_gui.bat` /
`run_weekly.ps1` (which hit the Videre API + MTGGoldfish live and update the real
`outputs/challenge_history_modern.csv`) until Monday 2026-07-27. Everything up to and including the
real rebuild + smoke test below was already done and verified safely (backed up first); it's only the
*next* live network run (a fresh weekly generation) that's being deferred.

**Resume-here checklist for Monday 2026-07-27:**
1. Run `run_gui.bat` or `./run_weekly.ps1 -Format Modern -MyDeck "Domain Zoo"` (or the CLI form) for a
   current-week window, with `--include-challenge-decklist`.
2. Watch the log for `[challenge-dedup] Merged ...` / `[WARN] [challenge-dedup] ...` lines and confirm
   `[OK] Fetched N challenge event(s)` counts look right (no double-counted duplicates).
3. Confirm C96 events show up (`[OK] Challenge C96_...`) if any fall in that week's window.
4. Check the new weekly `challenge_statistics.xlsx` — C96 rows should appear in `ALL_Decks`/`ALL_Archetypes`
   (no dedicated `C96_Decks` sheet yet — that's Phase 2b).
5. If all good: commit the Phase 2a code changes (see "Still open" below), then decide whether to start
   Phase 2b.

**Task:** Audit and fix the Challenge-stats pipeline, which miscounts events/trophies in weekly
reports because the code hardcodes the assumption that Challenges only come in size 32 or 64.
In practice sizes like "Modern Challenge 96" have existed on MTGGoldfish since week W26, and they
fall through the cracks.

**Root cause confirmed (2026-07-23):** `_build_mtggoldfish_challenge_url()` (metagame_input_generator.py)
builds the scrape URL from `(format, size, date)` only — no tournament id. When MTGO's calendar lists
two `tournament_id`s for the same `(date, size)` (confirmed for 2026-07-04, 07-11, 07-18 Challenge 64),
both get scraped from the identical MTGGoldfish page — same 32-of-32 roster, counted as two trophies
instead of one.

**Done in this session (Phase 2a):**
- `src/metagame_input_generator.py` — added `roster_content_signature(df)` (hash of sorted
  pilot+deck pairs) and rewired `fetch_challenges_in_window_from_mtggoldfish()` to parse first, then
  group by `(event_date, challenge_size)` and dedup by content signature before the alias/archetype
  mapping step. Matching signatures = MTGO republish, merged silently with a log line. Differing
  signatures = same-day/same-tier collision MTGGoldfish itself can't disambiguate (known source
  limitation, per the accepted decision below) — still merged to 1, but flagged with a `[WARN]` log line.
  Verified live against MTGGoldfish for 2026-07-04: `[challenge-dedup] Merged 2 duplicate publication(s)
  of C64 2026-07-04` fires correctly, output list has exactly 1 C64 entry for that date.
- `src/challenge_history_engine.py` — `RECON_DECKLIST_RE` widened from `C(32|64)` to `C(\d+)`, so
  `rebuild_challenge_history_from_dirs()` now ingests `challenge_C96_*_decklist.csv` files. Added
  `find_same_day_tier_collisions(history_csv, format_name=None)` — read-only audit scanning the full
  history for any `(EventDate, Tier)` group (any tier) with >1 `EventSlug`, flagging content-hash
  match/mismatch, for manual review.
- Ran `rebuild_challenge_history_from_dirs()` for real on `outputs/challenge_history_modern.csv`
  (backed up first to `challenge_history_modern.csv.bak_20260723_112953`, that file is *not*
  git-tracked). Result: 240 → 247 unique events, +7 new unique C96 event-dates, C32 (115) and C64 (125)
  unchanged. Window 2026-07-06..2026-07-19 now shows exactly 6×C32 + 12×C64 + 4×C96 = 22 events,
  matching the manually-confirmed ground truth below. Post-rebuild collision audit: none found.
  Smoke-tested `run_challenge_statistics()` against the rebuilt file — no crash, `events_processed=22`
  for that window; C96 rows currently flow only into the unfiltered `ALL_Decks`/`ALL_Archetypes`
  sheets (expected — they don't get their own `C96_Decks` sheet yet, that's Phase 2b).

**Decision already made with the user:** stick with MTGGoldfish as the data source (mtgo.com's own
JSON has no deck/archetype name field at all, only raw decklists, so it can't replace it). Same-day
same-tier tournament collisions where MTGGoldfish itself can't disambiguate are an accepted source
limitation — count them as 1 event and flag it (implemented above via the content-hash mismatch branch),
rather than trying to disambiguate via `tournament_id`.

**Confirmed ground truth (2026-07-06..2026-07-19 window):** 12×C64 + 6×C32 + 4×C96 = 22 trophies, plus
Showcase Challenge and RC Super Qualifier counted separately as premier events (not Challenges).

**Next step (Phase 2b, not started):** make the per-tier XLSX tables/sheets in
`run_challenge_statistics()` (challenge_history_engine.py, currently hardcoded
`Tier=="64"`/`=="32"` filters and `C64_Decks`/`C32_Decks`/`C64_Archetypes`/`C32_Archetypes`
sheet names) size-agnostic, so C96 (and future tiers) get their own sheets following the existing
`C{N}_Decks`/`C{N}_Archetypes` naming pattern, instead of only showing up in `ALL_*`. Get explicit
go-ahead before touching code, same as Phase 1/2a were scoped read-only/dry-run first.

**Still open:** none of this session's changes are committed yet (git status still shows
`challenge_history_engine.py`/`metagame_input_generator.py` modified, plus the real
`outputs/challenge_history_modern.csv` rebuild and its `.bak` sidecar — the outputs CSVs aren't
git-tracked at all). Consider committing the Phase 2a code changes before starting Phase 2b.

**Unrelated but relevant:** the large uncommitted diff sitting in the working tree since before this
session (`gui_app.py`, `metagame_input_generator.py`, `docs/*.csv`, etc.) is from a *separate*, already-
finished repair job: an accidental "Apply archetype to all visible" bulk action in the GUI had
overwritten dozens of unrelated, previously-correct archetype mappings back to "Aggro" (e.g. Tron,
Grixis Control, Jeskai Control, Hollow One). That was fully swept and fixed (`archetype_rules.csv`
rebuilt, 47 rows fixed in `user_deck_mapping.csv`, 10 in `challenge_deck_mapping.csv`, 2268 rows of
materialized data reconciled). It is not part of the Challenge-stats/tier-size task above, and is
still uncommitted — consider committing it separately to keep diffs clean.

## What This Project Does

Fetches MTG metagame and matchup data from the Videre Project API (`https://api.videreproject.com`), normalizes deck names/archetypes using rule CSV files in `docs/`, tracks Challenge tournament history scraped from MTGGoldfish, and exports CSV/XLSX/XML outputs and charts to weekly-dated folders under `outputs/`.

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

## Architecture

### Source modules (`src/`)

| Module | Role |
|---|---|
| `metagame_input_generator.py` | Core entry point. API fetching, deck name normalization, dataset building, all file export. |
| `challenge_history_engine.py` | Persistent challenge history CSV management, chart generation for Challenge Analytics. |
| `statistics_engine.py` | Metagame statistics charts (encounter probability, performance quadrant, trend lines). |
| `gui_app.py` | PySide6 desktop GUI wrapping all of the above. QSettings persists backup URL between sessions. |
| `change_model.py` | Reads/writes the `docs/` CSV config files (aliases, archetype rules, mappings, catalog). |
| `preset_cli.py` | Interactive terminal CLI with saved state in `.preset_cli_state.json`. |

### Data flow

1. `run_generation()` in `metagame_input_generator.py` is the single orchestration entry point used by both the CLI (`main()`) and the GUI (via `QThread`).
2. API calls use a three-tier fallback: **primary API → backup VPS (configured via `--backup-url` or `MTG_BACKUP_URL` env var) → local JSON cache** (`outputs/cache/`).
3. Deck names are normalized via a pipeline: raw API name → `user_deck_mapping.csv` override → `deck_aliases.csv` canonicalization → archetype assignment from `archetype_rules.csv` (with `heuristic_archetype()` as final fallback).
4. Challenge data is parsed from MTGGoldfish HTML. Discovery uses the MTGO calendar API first, falling back to scraping the `/decklists` index page.
5. Outputs are written to `outputs/YYYY/MM/WNN_YYYY-MM-DD_to_YYYY-MM-DD/`. The persistent challenge history CSV lives at `outputs/challenge_history_<format>.csv` (not inside weekly run dirs).

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
