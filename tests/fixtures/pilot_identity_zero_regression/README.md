# pilot_identity_zero_regression fixture

**Structural/behavioral reference only. Never publish or treat as a real Summer 2026 standing.**

## What it is

A frozen snapshot taken 2026-08-26, immediately before the `identity.py`/`_identity_key` wiring
described in `.claude/plans/delightful-soaring-sedgewick.md` was tested for the first time:

- `league_input/results/*.csv`, `league_input/matches/*.csv`, `league_input/season_config.csv` --
  exact copy of `outputs/league/{results,matches}` and `season_config.csv` as they stood on
  2026-08-26, i.e. the real input `league_engine.build_season_table` /
  `league_site_export.export_league_site` consume.
- `expected/pilot_league_Summer_2026.csv`, `expected/season_Summer_2026.json`,
  `expected/pilots_Summer_2026.json`, `expected/seasons.json` -- the real output
  `outputs/league/pilot_league_Summer_2026.csv` and `docs/data/*.json` produced **from that same
  input**, generated 2026-08-24 (`as_of=2026-08-24`), i.e. *before* `identity.py` existed at all --
  no identity resolution, no `profileHidden`/`xHandle` fields.

## Why frozen, not live

`outputs/league/results` grows every week. A test that recomputes from the live directory and
compares against the live `docs/data/*.json` would start failing the moment new events are synced
in, for reasons that have nothing to do with pilot identity. Freezing both sides here means the
test result depends only on the code (`identity.py` + the four `_identity_key` call sites +
`league_site_export.py`/`aggregate_pilot_table` display-name wiring), never on which week it is.

## What the test checks

`tests/test_pilot_identity.py::test_zero_regression_no_identity_file` recomputes
`league_input/` -> season table + site JSON with `as_of=date(2026, 8, 24)` (must match the
`expected/` files' generation date -- `PrevRank`/`RankChange` depend on
`as_of - prevrank_cutoff_days`, so using any other date would introduce a difference unrelated to
identity) and with no `data/pilot_identity.csv` on the resolution path (so `identity.resolve()` is
a no-op for every loginid). It asserts:

- `pilot_league_Summer_2026.csv` and `seasons.json` are byte-identical to `expected/` (no schema
  change touches either file).
- For `season_Summer_2026.json` / `pilots_Summer_2026.json`: every field present in **both** the
  recomputed and `expected/` pilot record must be equal. Fields that exist only in the recomputed
  side (`profileHidden`, `xHandle` -- added by this same session's `league_site_export.py`/
  `docs/index.html` changes, independent of whether any identity merge ever happens) are expected
  and are checked separately for their default value (`profileHidden == False`, `xHandle is None`
  for every pilot, since no `data/pilot_profile.csv` exists in this fixture).

This is deliberately *not* a whole-file byte comparison for the JSON files, so a future, unrelated
field addition to the export doesn't break this test -- only a *value* change (identity resolving
two loginids together when it shouldn't, a display name changing, a pilot appearing/disappearing,
a `profileHidden`/`xHandle` value drifting from its no-merge default) should ever fail it.

## Format

UTF-8, no BOM. CSVs are LF line endings (see `.gitattributes`); the JSON files are as written by
`league_site_export._write_json` (LF, sorted keys, no trailing newline oddities).
