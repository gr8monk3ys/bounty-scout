# bounty-scout

Ranks live open-source bounties on two tracks — expected dollars per hour, and
hiring-signal value — across comma.ai, tinygrad, Tenstorrent and Expensify.

```
python3 scripts/scout.py                # rank what you can actually reach
python3 scripts/scout.py --all-hardware # include hardware-gated work
python3 scripts/scout.py --json         # machine-readable
python3 scripts/scout.py --no-model     # never call Claude for effort estimates
```

## Read-only, on purpose

There is no code path here that comments, claims, assigns, or opens a pull
request, and there should never be one. Every board worth targeting bans
automated claiming — Tenstorrent's contributing guide threatens permanent
repository bans for it by name, and tinygrad's bounty sheet says the same in
capitals. The scout finds work. A human decides and does it.

## Why two tracks

They rank close to inversely for a PC-only contributor, so collapsing them into
one number averages away the actual choice. Money is `amount / hours /
(1 + competitors)`; signal is a per-board weight times an amount tier times
winnability. Editing what ranks well is a one-line change to the constants at
the top of `scripts/score.py`.

The competitor divisor assumes uniform odds among everyone already in the race.
That is pessimistic — a careful contributor beats the median drive-by PR — but
the alternative ranks a $100 bounty with sixteen open PRs above real work.

## The one signal worth the tool

comma's lock lapses after a week without progress; tinygrad's after five days.
Neither board enforces this automatically, so a bounty reading `Locked` whose
last activity predates its own window **is available and nobody has noticed.**
That is the `LOCK LAPSED` section, and it is the only uncrowded edge here.

## Effort estimates

comma and tinygrad publish price-to-time ladders; those are authoritative and
free, and are never overridden by a guess. Boards that publish none get an
estimate from Claude, cached by content hash so a run finds nothing new and
makes no API calls. With no model available it falls back to a curve fitted from
the two published ladders. Every row's provenance is marked — `*` means the
number was estimated, not published.

## Setup

```
gh auth refresh -s read:project     # required, see below
uv run --with pytest pytest
```

Without `read:project` the comma adapter cannot read the board and falls back to
label search: roughly 10 of 27 bounties, no hardware gates at all, and a price
only where the title repeats one. It warns loudly rather than reporting a short
board as a complete one.

## Measured payout reality

A board's own documentation describes how it *intends* to pay outsiders, which
is not the same as what it does. `EXTERNAL_WIN_RATE` in `scripts/score.py` holds
the measured version.

Expensify sits at 0.05. Its contributor seat is largely automated: MelvinBot
posts the first proposal and is reviewed first, contributors may only propose a
"meaningfully different" approach, and Melvin then implements while the
Contributor+ owns the PR. Across the 60 most recently closed `Help Wanted`
issues on 2026-08-30, seven carried a payment summary, five paid a reviewer or
C+, and none recorded an external contributor as owed. Live queue entries read
`Contributor: @x does not require payment (Contractor)` beside
`Reviewer: @y owed $250`.

Not zero, because only 7 of 60 recorded a summary and Expensify also pays via
Upwork, which leaves no GitHub trace. Re-measure before trusting it.

## Known limits

- **tinygrad lock state is cell colour**, which CSV export drops. The
  `GitHub Owner` column is the proxy, so a claimed-but-unowned row reads as open.
- **Expensify competition is comment count**, inflated by bot traffic. Monotone,
  which is all the ranking needs, but not a headcount.
- **Expensify assignees mean nothing.** All 46 open `Help Wanted` issues are
  assigned; melvin-bot attaches an internal engineer to every one. The label is
  the availability signal.
- **Algora orgs are not covered.** Client-rendered, and the boards are dormant —
  tscircuit and Cal.com have both gone three months without a new bounty.
