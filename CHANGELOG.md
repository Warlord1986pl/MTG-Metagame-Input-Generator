# CHANGELOG

Changes of the deckdb input mode and its presentation materials. Each entry: date, what, why, output files changed,
DeckDB rules version of the export the change was checked on. Newest first.

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
