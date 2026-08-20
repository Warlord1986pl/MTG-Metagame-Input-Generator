# pilot_league_Summer_2026_reference.csv

Structural reference only. **Never publish this file or treat its numbers as real Summer 2026
standings.**

## What it is

Output of `league_engine.run_league_update()` for Modern Challenge + premier data covering
**2026-07-13 to 2026-08-16**:

- 59 Challenge events + 3 premier events (62 total)
- 975 total points, of which 90 are premier points

## Why it's incomplete, not wrong

`challenge_history_modern.csv` only has resolved `EventID`s from 2026-07-13 onward (everything
from December 2025 through early July 2026 is legacy/unmigrated data with a blank `EventID`, which
the league pipeline skips by design, same as the Challenge stats pipeline). Summer 2026 as a season
runs 2026-06-01 to 2026-08-31, so this fixture is missing roughly six weeks of the season's start
and the last two weeks of its end. It is not meant to represent a real season standing.

## What it's for

A known-good file to diff a real run against for **column layout, row ordering, and point
distribution shape** (e.g. "does the header still have 11 columns in this order", "does a premier
win still show up as a `PremierPoints` bump on a plausible row") -- not for exact numbers. The first
real season table (Autumn 2026, starting 2026-09-01) will start from zero and accumulate its own
real data; compare its *structure* against this fixture, not its totals.

## Format

UTF-8, no BOM, LF line endings (see `.gitattributes`) -- same format the real
`outputs/league/*.csv` files are written in.
