# CHANGELOG

Changes of the deckdb input mode and its presentation materials. Each entry: date, what, why, output files changed,
DeckDB rules version of the export the change was checked on. Newest first.

## 2026-10-30 - Deck "Moonshadow" with colour variants (DeckDB entry 332); deck rename register

- What: DeckDB rules 1e0f7f3c023d574b (entry 332): "Rakdos Moonshadow" is now the deck "Moonshadow" (id
  rakdos-moonshadow unchanged) with colour variants Rakdos, Dimir, Grixis, Mono-Black, Golgari, Jund and "other";
  exports `assignments_1e0f7f3c023d574b_2026-10-29.csv` / `cards_1e0f7f3c023d574b_2026-10-29.csv` in
  `docs/deckdb_config.csv`. New `docs/deckdb_renames.csv` (old,new,rules_version,decision) and
  `deckdb_folder.load_renames()`: "Classification changes since the previous week" compares the previous export's deck
  names through it, so a rename is one Status line "renamed: Rakdos Moonshadow -> Moonshadow (N lists)" and the moves
  below are counted after the renames; a missing table, a blank name, a double rename or a chain is a DeckdbInputError.
  `docs/deckdb_colours.csv`: the same colour (#3d5afe) under the name "Moonshadow", so the trend line keeps its colour.
  Tests `tests/test_deckdb_renames.py`.
- Why: Karol's decision of 2026-10-30 (W44 triage group 1 is Moonshadow in another colour).
- Output files: from the next folder on, deck "Moonshadow" in every material, its variants in `variants_week`, the
  renamed line in `notes.md`. Checked on W43 regenerated with the new export (outputs/_working/moonshadow_check/):
  list counts of all 69 ISO weeks unchanged; the moves equal the simulation of the accepted report (24 lists up to
  2026-10-25: 22 into Moonshadow, 1 Rakdos Moonshadow -> Rogue by K35, plus Dimir / Mono-Black Midrange); W43
  Moonshadow 54 = the 54 Rakdos Moonshadow lists, none moved in; variants_week Rakdos Moonshadow 54 / Top 8 15 /
  trophies 3 = the deck row; no Death's Shadow or Birthing Ritual list moved. W43 notes.md: "renamed: Rakdos Moonshadow
  -> Moonshadow (89 lists)" (the common lists with the export of 2026-10-18); the moves section still lists the 24
  moves of earlier weeks (all common lists), none of them a W43 list.
- DeckDB rules version: 1e0f7f3c023d574b (was ded3d7dba99e38d7).

## 2026-10-30 - Test fixtures: synthetic LoginIDs

- What: every real MTGO LoginID in `tests/` replaced by a synthetic one (900000000 + rank in numeric order, one mapping
  for all files, 1026 ids): 6181 CSV cells (`LoginID`, `WinnerLoginID`, `LoserLoginID`, `loginid`, `pilot_id`), 4720
  JSON values / keys (`id:<n>`, login fields), 2 mentions in fixture READMEs, 33 quoted literals in `tests/*.py` plus the
  unquoted occurrences in `test_repair_missing_loginids.py` and `test_identity_location.py`. Pilot names unchanged
  (not in scope). Event ids in the saved mtgo.com listing HTML are not LoginIDs and stay.
- Why: privacy decision of 2026-10-30 (point 3c).
- Output files: none; 166 tests pass, including the zero-regression identity test against its expected outputs.
- DeckDB rules version: ded3d7dba99e38d7 (unchanged).

## 2026-10-30 - Privacy: pilot_identity.csv and pilot_merge_log.csv out of the public repo

- What: the two files (which MTGO accounts belong to one person, and why) are removed from `data/` and read from a
  private directory: `$PILOT_IDENTITY_DIR` or the first line of the gitignored `.pilot_identity_dir`
  (`src/identity.py`: `private_data_dir()`, `identity_csv_path()`, `merge_log_csv_path()`). Not configured, a missing
  directory or a missing file is an `IdentityLocationError` -- a silently empty overlay would un-merge every recorded
  identity on the league site. Explicit paths (`set_data_dir`, CLI `--data-dir`, tests) keep "missing file = no
  overlay". `pilot_profile.csv` stays in `data/`. `pilot_identity_cli.py`: `--data-dir` optional, prints where the
  merge log is and that the private copies must be committed / copied to the VPS. Tests: `tests/conftest.py` (empty
  overlay unless `$PILOT_IDENTITY_DIR` is set; 163 passed with the empty and with the real overlay before),
  `tests/test_identity_location.py`. This machine: `E:/github/DeckDB/pilot_identity/` (private DeckDB repo,
  5c0ea58); VPS: `/root/pilot_identity/`.
- Why: Karol's privacy decision of 2026-10-30 (point 3a); the merge log names the source of each merge.
- Output files: none -- season tables with the new location are identical to the ones from `data/` (MeninoNey 56).
- DeckDB rules version: ded3d7dba99e38d7 (unchanged).

## 2026-10-30 - Backup of the VPS-only league files: variant A of docs/backup_plan.md

- What: the collector (repo Meta-Challange-2.0_Mikrus) now commits `outputs/league/snapshots/*.csv` and
  `outputs/league/season_config.csv` with its hourly push (`ALLOWED_PUSH_GLOBS`, test `tests/test_push_globs.py`).
  Columns checked before: snapshots are `LoginID, Rank, Points, PremierPoints, Wins, Top2, Top4, Top8, Top16, Starts`,
  the season registry is season names and dates -- all already published by the league site. The 8 locally computed,
  untracked snapshots of this machine were moved to `outputs/_working/snapshots_local_2026-10-30/` so that the local
  `git pull` does not refuse the bot's snapshot files.
- Why: the snapshots (the RankChange baseline) and the Autumn 2026 row of the registry existed only on the VPS disk.
- Output files: none here; from the next bot run on, `outputs/league/snapshots/` and `season_config.csv` in main.
- DeckDB rules version: ded3d7dba99e38d7 (unchanged).

## 2026-10-30 - Merge of the bot's sync of 2026-10-28; main safe for the VPS `git pull`

- What: origin/main 79f1a59 (`mikrus: sync modern through 2026-10-28`, events 12857015 and 12857018, 64 rows) merged
  the same way as the merge of 2026-10-29: Broodscale 10, Moonshadow 6, WBG -> Devoted Combo 2 (check-alias against
  the 151 Devoted Combo lists of 2026-09-01 .. 10-28: SAFE, median 0.958), league deck columns from the history,
  season tables / site / results export rebuilt (invariants and validate_league pass). Autumn 2026: 110 events, 1057
  pilots, 3503 points = origin's 3441 + the 62 points of the RC Super Qualifier 12855444. For the hourly
  `git pull -q` of the VPS (root crontab, collector `history-sync` / `league-rebuild` / `push`), which keeps three
  kinds of local changes: `outputs/league/season_config.csv` (Autumn 2026 upserted, never committed) and the two
  `docs/data/pilot_league_results_*_manifest.json` (`generated_at`) stay at origin's version, and
  `outputs/league/snapshots/*.csv` are no longer tracked here (`git rm --cached`; the files stay on disk) because the
  VPS has its own, untracked snapshots w36 .. w44 of the same names -- a tracked or modified copy of any of those
  paths makes `git pull` refuse and stops the whole collector chain (checked on a test repository). Every collector
  import binds to the merged signatures; no new third-party package is imported by the modules it loads.
- Why: Karol's decision to merge after checking the bot (2026-10-30).
- Output files: history CSVs, card-rule ledgers, `outputs/league/` (results, matches, pilot_league_Autumn_2026.csv,
  ingestion_log.csv), `docs/data/` (season, pilots, results export).
- Open: the VPS snapshots (irreplaceable per `.gitignore`) live only on the VPS disk; the collector's push should
  commit them and `season_config.csv` (repo Meta-Challange-2.0_Mikrus, not changed here).
- DeckDB rules version: ded3d7dba99e38d7 (unchanged).

## 2026-10-29 - Merge of origin/main (VPS bot data + PRs #6-#16) into the local main

- What: local main (75 commits since 2026-09-14: deckdb mode, card rules, runner, build/) and origin/main (61 commits:
  daily `mikrus: sync` data of the VPS bot up to 2026-10-27 and PRs #6-#16) merged; 129 conflicts. Code: the only
  conflict, `src/challenge_mtgo_source.py`, takes origin's structure (MTGGoldfish rows joined to mtgo.com by Place,
  never a row without LoginID, quarantine, MTGGoldfish grace period, no durable cache of a blob without final_rank,
  an event lost to an earlier window is an ERROR) with the local Broodscale / Moonshadow card rule inside
  `ingest_labeled_event`. Data: history CSVs keep the local deck labels (card rules, WBG -> Devoted Combo, review
  decisions) and take Pilot (first capture by the bot: 2 rows), LoginID and PlayerCount (integer text) from origin;
  the bot's 523 rows of 2026-10-19 .. 10-27 are added and relabelled with the local rules (Broodscale 58, Moonshadow
  50, WBG -> Devoted Combo 9 after check-alias SAFE, median 0.871). League results / matches: rows from origin, deck
  columns from the merged history (every row matched; also fixes 92 + 47 local rows that lagged behind the local
  history); RC Super Qualifier 12855444 (2026-10-03, missing on origin) kept. Season tables, site JSON and the
  results export rebuilt from scratch (invariants and validate_league pass). History writers: LoginID and PlayerCount
  go through `_clean_history_df_before_write` like EventID; the GUI archetype patch reads the history as text (its
  plain `read_csv` was the source of "1942357.0").
- Why: the two lines of work had diverged since 2026-09-14; the bot keeps writing to origin/main daily.
- Output files: `outputs/challenge_history_modern.csv`, `outputs/premier_history_modern.csv`, the card-rule ledgers,
  `outputs/league/` (results, matches, pilot_league_Autumn_2026.csv, ingestion_log.csv), `docs/data/` (season,
  pilots, results export, manifests). Summer 2026 unchanged in substance (91 events, 1032 pilots, 2945 points); Autumn
  2026: 108 events, 1049 pilots, 3441 points = origin's 3379 + the 62 points of the RC Super Qualifier, every other
  pilot's points identical. Deckdb materials unchanged (W43 equal to `outputs/_working/reference/2026-W43/`).
- Tests: 163 passed.
- DeckDB rules version: ded3d7dba99e38d7 (unchanged).

## 2026-10-29 - Runner: build/ under version control, source of the Rogue & unclassified alarm thresholds

- What: the scripts of `build/` (analysis.py, build_deck.js, gen_dossier.py, build_docs.py, qa.py,
  README_workflow.md, sha256 as in the package of 2026-10-29) are committed, so every version the runner reports is in
  the history; the package files (PRZECZYTAJ.md, SHA256SUMS.txt, runner_paczka_w43.zip) removed from the repo root;
  the test rows of 2026-10-29 removed from `weekly/runs.csv` (header kept). `docs/runner.md` and
  `src/weekly_runner/triage_check.py`: the thresholds of the Friday alarm (Rogue & unclassified > 5% of the lists or
  a rise > 2 pp) come from the master prompt of the metagame analysis, part 2, test 8 (was: "order of 2026-10-29").
  `docs/runner.md`: the junction `E:\mnt\skills\public\pptx` stays until a `qa.py` with a validator path parameter
  (`--validator` or `PPTX_VALIDATE`) arrives; then the runner passes the local pptx skill and the junction goes.
- Why: Karol's decisions after the runner report.
- Output files: none of the materials; `weekly/runs.csv` (outside git).
- DeckDB rules version: ded3d7dba99e38d7 (unchanged).

## 2026-10-29 - Week runner ("one click") and the Friday triage check

- What: `metagame_week.py` / `metagame_week.bat` (root): stage DATA = preflight (Python and packages of both
  interpreters, network, git state of both repos, commits, DeckDB rules version; uncommitted `docs/deckdb_config.csv`
  stops), DeckDB `backfill-raw` + `import --raw-window` + derived columns for the ISO week (every failure of the
  backfill report and every import refusal stops, also at exit code 0), `weekly_triage.py` (`pending_blocking` stops
  with the groups), `export-assignments` / `export-cards` (an existing file of the same name must have the same
  sha256), check of the week's Modern Challenge ids against the monthly pages of mtgo.com/decklists both ways (own
  parser, snapshots `mtgo_snapshot_<YYYY-MM>.html`, neighbours, 1 day off = warning, more = stop, cut pages retried),
  pipeline in input mode deckdb with a working copy of the committed config and its own `--outputs-base`, flat folder
  `weekly/<week>/` with `run_report.md`, one row per run in `weekly/runs.csv`; `--test-monthly` = test monthly
  material of the running incomplete month. Stage BUILD runs the scripts of `build/` (from the Claude project,
  unchanged) in the order of `build/README_workflow.md` with `weekly/<week>/out/` as working directory; any FAIL of
  `qa.py` stops; sha256 of every `build/` file in the report. `triage_check.py` / `triage_check.bat`: import of the
  running week up to today, triage, `weekly/<week>/triage_check_<date>.md` (groups with examples and common cards,
  Rogue & unclassified share and change, flag > 5% or > 2 pp), Windows toast, exit 0 / 1 / 2 / 3;
  `docs/triage_check_task.xml` for the Task Scheduler (Friday 18:00, not installed automatically). Code in
  `src/weekly_runner/` (`deckdb_probe.py` runs read-only under the DeckDB interpreter). Pipeline: new option
  `--deckdb-cache-dir` (deckdb mode; default unchanged). `scripts/compare_week_folders.py`. Docs `docs/runner.md`,
  `docs/runner_plan.md`; tests `tests/test_weekly_runner.py` (20).
- Why: the weekly folder without Claude Code, with every manual decision either automated (import window,
  failures, derived columns, config) or turned into a stop with an instruction (triage, exports, mtgo.com check).
- Output files: new `weekly/<week>/` (outside git); `outputs/presentation/` is no longer written by the weekly flow
  and stays the archive of the published folders. No change of any material: `weekly/2026-W43/` equals
  `outputs/_working/reference/2026-W43/` made with the same pipeline command (PNG and notes.md byte for byte,
  data.xlsx sheet by sheet).
- Checked on: 2026-W43 (12 / 12 ids, neighbours 12856599 and 12856997 outside), 2026-W40 (September and October
  pages; September monthly material 59 events, 1884 lists, m01 7484 lists as in part 4), build W43 with the content
  files of the Claude project (qa.py without FAIL), triage check W43 as of 2026-10-23 (complete = triage file of the
  week).
- DeckDB rules version: ded3d7dba99e38d7 (export assignments_ded3d7dba99e38d7_2026-10-25.csv, sha256 d6a107ec).

## 2026-10-28 - Monthly material in the week that closes the month (part 4)

- What: standing rule `month_for`: the monthly material of month M is in the package of the ISO week that contains
  the first day of M + 1 (October 2026 -> 2026-W44); its period ends on the last day of M even when the week goes on
  (1 November 2026 only in the weekly sheets). `m_deck_results`, `m_results_bands`, `m_list_changes` carry
  `Period_start` / `Period_end`. notes.md Status: "Monthly material: <M> .. <end of M>, complete month (<n> events,
  <m> lists)" and "Deck results (m01): format period ..., with multiple-comparison correction (BH q = 0.1, against the
  rest of the field)"; speaker notes of m01 and m02 start with the material's period. New checks that stop the run:
  a complete month ends on its last day, the three sheets end on the material's end, no list after it, the month's
  events / lists equal the export's rows of those dates, `m_deck_results` lists = lists of the period.
  `--presentation-month` (incomplete month, test material) unchanged. CLAUDE.md, `/poniedzialek`, `--help` reworded.
- Why: the rule "the week contains the first Sunday of the month" picked the right week but did not say which month
  the material covers nor that it ends on the last day of that month; the Status line did not state completeness.
- Output files: `outputs/presentation/<week>/notes.md` (Status, speaker notes m01 / m02), `data.xlsx` (Period_start /
  Period_end in m_deck_results, m_results_bands, m_list_changes). Material selection itself unchanged for every week
  (the two rules choose the same ISO week).
- Checked on: 2026-W40 closing September (as of 2026-10-04, current export) and a synthetic 2026-W44 test; the real
  2026-W44 needs the export with data up to 2026-11-01.
- DeckDB rules version: ded3d7dba99e38d7 (export assignments_ded3d7dba99e38d7_2026-10-25.csv).

## 2026-10-28 - BH correction of the deck results against the rest of the field (part 3, DeckDB K41 change)

- What: `conversion_table` (format-period deck results) adds per measure (Percentile, Conversion) `baseline_rest`
  (the period's lists without the deck), the two-sided bootstrap `p` (same 2000 pilot draws as the intervals, >=
  1/2000), `q_BH`, `BH_rejects`, `BH_threshold_T` (= q k / m) and the band at level 1 - T (`band_low`, `band_high`);
  BH q = 0.1 separately for the percentile and the conversion, family = decks with >= 10 pilots. Colour only for BH
  rejections (m01, working `results_matrix_*.png`, `presentation/deck_results.png`). m01 draws the deviation from
  `baseline_rest` with the 1 - T band; geometry colour == `BH_rejects` is asserted; legend "band adjusted for
  multiple comparisons". `*_CI_excludes_baseline` stay for comparison. Glossary: "results colour rule", "common
  baseline", new "baseline_rest" and "deck results BH correction". STATUS of the format period gives the BH counts.
- Why: every deck with >= 10 pilots was tested at 5% without correction, while the card layer uses BH q = 0.1; a
  deck was compared with a baseline that contains its own lists. DeckDB decision register entry 331 (K41 change).
- Output files: `outputs/presentation/<week>/m01_deck_results.png`, `data.xlsx` (m_deck_results: 14 new columns;
  m_results_bands: Baseline = baseline_rest, bands at level 1 - T, Flag_sheet = BH_rejects), `notes.md`; working
  `format_period_*/` (Decks / Archetypes sheets and CSVs, STATUS, results_matrix_*.png, presentation/deck_results.png).
- Effect, 2026-05-19 .. 2026-10-25: percentile significant decks 7 before (8 counting the Rogue & unclassified row),
  5 after BH; conversion 3 before (4 with that row), 1 after. Broodscale Combo stays significant on the percentile
  (p 0.003, q 0.051).
- DeckDB rules version: ded3d7dba99e38d7 (export assignments_ded3d7dba99e38d7_2026-10-25.csv).

## 2026-10-28 - Full weekly / monthly data, tracked decks and BH correction of the weekly changes (part 2)

- What: two new sheets in `data.xlsx` of the presentation folder, recomputed from the export on every run:
  `weekly_metrics` (every ISO week of the format period x every deck and, apart, every archetype, both with Rogue &
  unclassified; counts, share, top pilot share, change, tracked / tracked_reason, interval, p, BH q, change_beyond)
  and `monthly_metrics` (month x deck / archetype). Tracked decks of week w = top 10 of w, top 10 of w-1 and the
  largest deck of every archetype not in them (share >= 1% in w or w-1). The weekly change is tested only for tracked
  decks and archetypes: event bootstrap as before, two-sided bootstrap p (>= 1/2000), Benjamini-Hochberg q = 0.1
  separately in the family of tracked decks and of archetypes. Charts 01 and 03 (and the working
  `change_vs_previous_week_*.png`) emphasise a change only when `change_beyond` = 1; the `decks` / `archetypes` sheets
  keep `Change_CI_excludes_0` with `change_beyond` next to it. Glossary: "tracked decks", "weekly change test (BH)",
  "weekly_metrics", "monthly_metrics", updated "95% CI of the change". Checks that stop the run: lists of the decks
  and of the archetypes sum to the week's lists, trophies <= top8 <= lists, events_with_list <= week_events, the
  `decks` / `archetypes` / `pilots` rows equal `weekly_metrics`.
- Why: pipeline computes everything for every deck, charts keep the top 10, and the weekly changes of up to ~19
  rows per week were each tested at 5% without correction (inconsistent with the BH q = 0.1 of the card layer).
- Output files: `outputs/presentation/<week>/data.xlsx` (sheets weekly_metrics, monthly_metrics; decks, archetypes:
  + change_beyond), `01_archetypes_share.png`, `03_decks_share.png`, `notes.md` (speaker notes, subtitle wording,
  glossary); working `week_*/` sheets, CSVs, STATUS and `change_vs_previous_week_*.png`.
- W43 effect: decks significant before correction Affinity (+3.9 pp, CI 0.5..7.7) and Prowess (-6.1 pp, CI -9.4..-3.1);
  after BH only Prowess (q 0.006); Affinity q 0.186, not emphasised any more. Archetypes: none before or after.
- DeckDB rules version: ded3d7dba99e38d7 (export assignments_ded3d7dba99e38d7_2026-10-25.csv).

## 2026-10-28 - Glossary: results colour rule says green above the baseline (part 1 of the order before 2026-W44)

- What: the glossary term "results colour rule" (notes.md of the presentation folder, GLOSSARY.md / Glossary sheet of
  the working materials) now reads "red below / green above the common baseline" instead of "red below / blue above".
  m01 has a legend row with swatches drawn from the same colour constants as its bars (`BAND_COLOURS`); a bar drawn in a
  colour other than its legend swatch stops the run (`DeckdbInputError`). The working `results_matrix_*.png` keep their
  legacy diverging cell scale, described by its own working-only term "results matrix scale (working)", which is not in
  notes.md.
- Why: m01 (commit b5c848c) draws green above the baseline; the glossary still described the old blue.
- Output files: `outputs/presentation/<week>/notes.md` (Glossary), `m01_deck_results.png` (legend row);
  `outputs/_working/deckdb/WORKING_<as-of>/*/GLOSSARY.md` and the Glossary sheets.
- DeckDB rules version: ded3d7dba99e38d7 (export assignments_ded3d7dba99e38d7_2026-10-25.csv).
