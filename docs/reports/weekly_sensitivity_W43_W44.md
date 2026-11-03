# Weekly change test: sensitivity, event bootstrap vs pilot bootstrap (report only)

Order of 2026-11-03 d, point 5: 2026-W43 vs the week before, 2026-W44 vs the week before, the tracked decks and archetypes of each week (weekly_metrics of `weekly/2026-W44/data.xlsx`). Export `assignments_9534f600135ceece_2026-11-01.csv` (rules 9534f600135ceece, sha256 1ea02e609f1aaba75d54adae79f75d162fc3b66b04ae1f26a8f92adc7fed611c); every week is computed from this one export, so the W43 numbers can differ from the folder published for W43 (an older export and older rules). Nothing in production changes: the weekly materials keep the event bootstrap.

## Methods

- **Event bootstrap** (the weekly test, weekly_metrics): the events of each week drawn with replacement, independently, 2000 draws, seed 20261026.
- **Pilot bootstrap**: the pilots of each period drawn with replacement, each with all their lists of the period (every deck), the two periods independently, the current one first, 2000 draws, seed 20261026; share = lists of the deck among the drawn lists (`deckdb_input.pilot_share_draws`).
- Both: 95% percentile interval, two-sided p = 2 x min(P(d <= 0), P(d >= 0)) (at least 1/2000), Benjamini-Hochberg q = 0.1 separately for the tracked decks and for the archetypes.

## 2026-W43 vs 2026-W42

Repeat play: 2026-W43: 371 lists from 226 pilots in 12 events (mean 1.64, max 7 lists per pilot; 79 pilots with >= 2 lists, 6 with >= 5); 2026-W42: 363 lists from 240 pilots in 12 events (mean 1.51, max 7 lists per pilot; 70 pilots with >= 2 lists, 7 with >= 5).

| level | deck / archetype | tracked reason | change pp | event CI | event q | event beyond | pilot CI | pilot q | pilot beyond | top pilot W43 | top pilot W42 | pilots W43 / W42 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| deck | Affinity | top10_week | +3.9 | +0.5 .. +7.7 | 0.186 | no | −1.9 .. +9.9 | 0.794 | no | 22% | 18% | 14 / 13 |
| deck | Moonshadow | top10_week | +1.6 | −3.1 .. +6.2 | 0.594 | no | −6.2 .. +9.4 | 0.794 | no | 9% | 15% | 32 / 28 |
| deck | Prison | top10_week | +1.5 | −2.3 .. +4.9 | 0.594 | no | −3.8 .. +6.7 | 0.794 | no | 15% | 24% | 16 / 14 |
| deck | Broodscale Combo | top10_week | +1.4 | −3.1 .. +6.1 | 0.594 | no | −6.0 .. +8.4 | 0.794 | no | 13% | 10% | 23 / 27 |
| deck | Energy | top10_week | +1.0 | −2.4 .. +4.2 | 0.594 | no | −2.9 .. +5.1 | 0.794 | no | 14% | 18% | 16 / 13 |
| deck | Living End | top10_week | +0.5 | −1.2 .. +2.2 | 0.594 | no | −3.0 .. +4.1 | 0.799 | no | 36% | 33% | 6 / 6 |
| deck | Amulet Titan | top10_week | −0.6 | −3.0 .. +1.6 | 0.594 | no | −4.0 .. +2.8 | 0.794 | no | 27% | 15% | 6 / 11 |
| deck | Tron | largest_in_archetype | −0.9 | −3.6 .. +1.7 | 0.594 | no | −4.2 .. +2.1 | 0.794 | no | 20% | 23% | 8 / 9 |
| deck | Ruby Storm | top10_prev | −1.2 | −3.5 .. +1.3 | 0.594 | no | −5.7 .. +2.7 | 0.794 | no | 40% | 43% | 7 / 8 |
| deck | Zoo | top10_week | −1.5 | −4.5 .. +1.8 | 0.594 | no | −5.2 .. +2.4 | 0.794 | no | 12% | 14% | 13 / 19 |
| deck | Blink | top10_week | −1.7 | −4.7 .. +1.2 | 0.594 | no | −4.6 .. +1.4 | 0.794 | no | 9% | 12% | 11 / 15 |
| deck | Prowess | top10_week | −6.1 | −9.4 .. −3.1 | 0.006 | yes | −10.3 .. −2.2 | 0.012 | yes | 10% | 9% | 10 / 23 |
| archetype | Control | archetype | +3.1 | −1.2 .. +7.1 | 0.484 | no | −2.5 .. +8.7 | 0.719 | no | 11% | 22% | 22 / 16 |
| archetype | Aggro | archetype | +2.5 | −3.4 .. +8.4 | 0.484 | no | −7.7 .. +12.9 | 0.719 | no | 5% | 6% | 80 / 83 |
| archetype | Graveyard | archetype | +1.2 | −1.6 .. +4.2 | 0.484 | no | −3.2 .. +5.7 | 0.719 | no | 15% | 14% | 19 / 17 |
| archetype | Rogue & unclassified | archetype | −0.1 | −2.3 .. +2.2 | 0.949 | no | −4.0 .. +3.6 | 0.979 | no | 23% | 38% | 11 / 9 |
| archetype | Ramp | archetype | −1.2 | −4.2 .. +1.7 | 0.484 | no | −5.6 .. +2.8 | 0.719 | no | 11% | 14% | 15 / 15 |
| archetype | Combo | archetype | −2.7 | −7.4 .. +2.4 | 0.484 | no | −12.3 .. +5.9 | 0.719 | no | 7% | 6% | 53 / 67 |
| archetype | Midrange | archetype | −2.8 | −5.8 .. +0.2 | 0.469 | no | −9.0 .. +4.0 | 0.719 | no | 7% | 6% | 43 / 50 |

Of the 12 tracked decks and 7 archetypes, 1 deck and 0 archetypes are beyond normal variation in both methods: Prowess (−6.1 pp). Only in the event bootstrap: 0 decks and 0 archetypes: none. Only in the pilot bootstrap: none. The pilot-bootstrap interval is 1.57 times as wide as the event-bootstrap interval in the median (range 1.01–2.17).

## 2026-W44 vs 2026-W43

Repeat play: 2026-W44: 436 lists from 247 pilots in 14 events (mean 1.77, max 6 lists per pilot; 102 pilots with >= 2 lists, 7 with >= 5); 2026-W43: 371 lists from 226 pilots in 12 events (mean 1.64, max 7 lists per pilot; 79 pilots with >= 2 lists, 6 with >= 5).

| level | deck / archetype | tracked reason | change pp | event CI | event q | event beyond | pilot CI | pilot q | pilot beyond | top pilot W44 | top pilot W43 | pilots W44 / W43 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| deck | Amulet Titan | top10_week | +3.5 | +1.0 .. +6.0 | 0.078 | yes | −1.1 .. +8.5 | 0.598 | no | 21% | 27% | 15 / 6 |
| deck | Tron | top10_week | +3.0 | +0.5 .. +5.7 | 0.087 | yes | −0.6 .. +7.3 | 0.598 | no | 20% | 20% | 17 / 8 |
| deck | Prowess | top10_week | +2.8 | +0.6 .. +5.3 | 0.078 | yes | −0.5 .. +6.6 | 0.598 | no | 17% | 10% | 16 / 10 |
| deck | Dimir Midrange | top10_week | +1.8 | −0.1 .. +3.6 | 0.169 | no | −1.5 .. +5.3 | 0.752 | no | 25% | 43% | 9 / 5 |
| deck | Ruby Storm | top10_week | +0.7 | −1.8 .. +3.1 | 0.798 | no | −2.7 .. +4.4 | 0.908 | no | 33% | 40% | 11 / 7 |
| deck | Broodscale Combo | top10_week | +0.6 | −3.5 .. +5.1 | 0.798 | no | −7.1 .. +8.1 | 0.908 | no | 9% | 13% | 29 / 23 |
| deck | Living End | top10_prev | −0.2 | −2.0 .. +1.4 | 0.798 | no | −3.9 .. +3.3 | 0.908 | no | 33% | 36% | 6 / 6 |
| deck | Energy | top10_week | −0.4 | −3.0 .. +2.0 | 0.798 | no | −4.4 .. +3.6 | 0.908 | no | 13% | 14% | 16 / 16 |
| deck | Blink | top10_prev | −0.4 | −2.5 .. +1.6 | 0.798 | no | −3.1 .. +2.1 | 0.908 | no | 27% | 9% | 8 / 11 |
| deck | Affinity | top10_week | −0.8 | −4.5 .. +2.8 | 0.798 | no | −8.2 .. +5.6 | 0.908 | no | 15% | 22% | 17 / 14 |
| deck | Zoo | top10_prev | −1.8 | −3.7 .. +0.1 | 0.169 | no | −5.0 .. +1.3 | 0.752 | no | 17% | 12% | 10 / 13 |
| deck | Prison | top10_week | −2.2 | −5.1 .. +0.9 | 0.273 | no | −7.2 .. +2.8 | 0.774 | no | 23% | 15% | 12 / 16 |
| deck | Moonshadow | top10_week | −3.3 | −7.0 .. +0.8 | 0.217 | no | −10.1 .. +3.6 | 0.752 | no | 8% | 9% | 33 / 32 |
| archetype | Combo | archetype | +6.5 | +1.1 .. +12.0 | 0.044 | yes | −3.4 .. +15.6 | 0.444 | no | 4% | 7% | 77 / 53 |
| archetype | Ramp | archetype | +3.4 | +0.5 .. +6.5 | 0.044 | yes | −0.7 .. +8.0 | 0.444 | no | 14% | 11% | 26 / 15 |
| archetype | Rogue & unclassified | archetype | +1.8 | −1.0 .. +4.8 | 0.318 | no | −1.7 .. +5.4 | 0.444 | no | 17% | 23% | 17 / 11 |
| archetype | Graveyard | archetype | −1.0 | −4.1 .. +1.6 | 0.470 | no | −5.9 .. +3.8 | 0.659 | no | 15% | 15% | 14 / 19 |
| archetype | Midrange | archetype | −1.9 | −5.5 .. +1.5 | 0.322 | no | −8.1 .. +4.2 | 0.641 | no | 7% | 7% | 36 / 43 |
| archetype | Control | archetype | −3.5 | −6.8 .. −0.1 | 0.079 | yes | −8.8 .. +2.0 | 0.444 | no | 19% | 11% | 16 / 22 |
| archetype | Aggro | archetype | −5.2 | −10.0 .. −0.9 | 0.044 | yes | −15.5 .. +4.4 | 0.444 | no | 4% | 5% | 82 / 80 |

Of the 13 tracked decks and 7 archetypes, 0 decks and 0 archetypes are beyond normal variation in both methods: none. Only in the event bootstrap: 3 decks and 4 archetypes: Amulet Titan (+3.5 pp), Prowess (+2.8 pp), Tron (+3.0 pp), Aggro (−5.2 pp), Combo (+6.5 pp), Control (−3.5 pp), Ramp (+3.4 pp). Only in the pilot bootstrap: none. The pilot-bootstrap interval is 1.70 times as wide as the event-bootstrap interval in the median (range 1.21–2.19).

## Summary

**2026-W43:** Of the 12 tracked decks and 7 archetypes, 1 deck and 0 archetypes are beyond normal variation in both methods: Prowess (−6.1 pp). Only in the event bootstrap: 0 decks and 0 archetypes: none. Only in the pilot bootstrap: none. The pilot-bootstrap interval is 1.57 times as wide as the event-bootstrap interval in the median (range 1.01–2.17).

**2026-W44:** Of the 13 tracked decks and 7 archetypes, 0 decks and 0 archetypes are beyond normal variation in both methods: none. Only in the event bootstrap: 3 decks and 4 archetypes: Amulet Titan (+3.5 pp), Prowess (+2.8 pp), Tron (+3.0 pp), Aggro (−5.2 pp), Combo (+6.5 pp), Control (−3.5 pp), Ramp (+3.4 pp). Only in the pilot bootstrap: none. The pilot-bootstrap interval is 1.70 times as wide as the event-bootstrap interval in the median (range 1.21–2.19).
