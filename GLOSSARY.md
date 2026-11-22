# Glossary

Terms as the code uses them. Source of truth: `scripts/`.

**Bounty**
A normalized record of one paid open-source task (`scripts/bounty.py`). Every board adapter produces this one shape.
_Avoid_: issue, task (a Bounty may come from a spreadsheet row, a project card or an issue).

**Board**
A source of bounties: comma, tinygrad, Tenstorrent or Expensify. Each has one adapter in `scripts/sources/`; `Bounty.source` names it.

**Adapter**
A `scripts/sources/<board>.py` module exposing `fetch()` that returns Bounties and declares `MIN_EXPECTED`.

**MIN_EXPECTED**
The count below which an adapter's result is treated as a fetch failure (`SourceError`) rather than a drained board.

**Amount / amount_is_stated**
`amount_usd` is `None`, never 0, when a board publishes no price. `amount_is_stated` separates a price the board published from one we inferred.

**Hardware gate**
Normalized requirement a bounty has: `none`, `gpu`, `device`, `car`, `silicon`, or `unknown`, ordered least to most gated. The default report shows only `none`; `--all-hardware` shows the rest.

**Status**
`open` or `locked`. A board lock marks a bounty as taken.

**Lock lapse / stale lock**
A locked bounty whose last activity is older than its board's `LOCK_LAPSE_DAYS` window, so it is available in fact while still reading as taken. Shown in the `LOCK LAPSED` section.

**Competitors**
People already in the race for a bounty: PRs, claims or proposals.

**Money track**
`amount / hours / (1 + competitors)`, expected dollars per hour (`money_score`).

**Signal track**
`source weight * amount tier * winnability`, the hiring-signal value (`signal_score`). Source weights live in `SOURCE_SIGNAL`.

**External win rate**
`EXTERNAL_WIN_RATE`: the measured share of a board's payouts that reach an outside contributor.

**Fresh**
A bounty first seen within `FRESH_DAYS`; it gets `FRESH_BONUS`. `first_seen` comes from the scout's own state, not from the board.

**Stack bonus**
`STACK_BONUS` for bounties in `KNOWN_STACK` languages.

**Ladder**
A board's published price-to-hours table (`COMMA_LADDER`, `TINYGRAD_LADDER`), used for effort estimates.

**Effort estimate / provenance**
`effort_hours` returns hours plus provenance: `stated` (board ladder), `modeled` (Claude read the issue; cached by `body_hash`) or `heuristic` (fitted `FALLBACK_LADDER`). Estimated rows are marked `*`.

**body_hash**
Hash of a Bounty's title and body; the cache key for modeled estimates.

**Scout**
The read-only ranking run (`scripts/scout.py`). It never comments, claims, assigns or opens a PR.

**Attempt**
A worked contribution kept under `attempts/<name>/` (currently `tinygrad-gpuocelot`).
