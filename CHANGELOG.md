# CHANGELOG

Changes of the deckdb input mode and its presentation materials. Each entry: date, what, why, output files changed,
DeckDB rules version of the export the change was checked on. Newest first.

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
