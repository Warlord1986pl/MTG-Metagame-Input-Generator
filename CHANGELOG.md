# CHANGELOG

Changes of the deckdb input mode and its presentation materials. Each entry: date, what, why, output files changed,
DeckDB rules version of the export the change was checked on. Newest first.

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
