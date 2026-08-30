#!/usr/bin/env python3
"""Rank live open-source bounties on two tracks: money, and hiring signal.

Read-only by construction. There is no code path in this repo that comments,
claims, assigns, or opens a pull request, and there should never be one — every
board worth targeting bans automated claiming, two of them by name and with
permanent bans as the penalty. The scout finds work; a human does it.

Usage:
    python3 scripts/scout.py                    # rank what you can actually reach
    python3 scripts/scout.py --all-hardware     # include gated work
    python3 scripts/scout.py --json             # machine-readable
    python3 scripts/scout.py --no-model         # never call Claude for estimates
    python3 scripts/scout.py --new-only         # only bounties not seen before
"""

import argparse
import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import estimate  # noqa: E402
import score  # noqa: E402
from bounty import HARDWARE_NONE, HARDWARE_UNKNOWN  # noqa: E402
from sources import SourceError, comma, expensify, tenstorrent, tinygrad  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATE = ROOT / "state" / "seen.json"
RUNS = ROOT / "runs"

ADAPTERS = [comma, tinygrad, tenstorrent, expensify]

# What you own. Everything else is reported separately rather than ranked, so a
# board's headline number never implies work you cannot start.
DEFAULT_OWNED = {HARDWARE_NONE}
ALL_HARDWARE = {"none", "gpu", "device", "car", "silicon"}


def load_seen() -> dict:
    if STATE.exists():
        try:
            return json.loads(STATE.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def save_seen(seen: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(seen, indent=2, sort_keys=True))


def collect(warnings: list) -> list:
    """Fetch every board. A broken adapter degrades the run, never fakes it."""
    out = []
    for mod in ADAPTERS:
        try:
            out.extend(mod.fetch(warn=warnings.append))
        except SourceError as exc:
            warnings.append(f"{mod.SOURCE}: FETCH FAILED — {exc}")
    return out


def enrich(bounties, today, allow_model=True):
    """Attach first-seen dates and effort, then both scores.

    Returns (rows, known_before) where known_before is the set of refs the state
    file held BEFORE this run. `--new-only` must diff against that, not against
    today's date: two runs on the same day would otherwise both call the whole
    board new, which is precisely the noise a scheduled loop cannot tolerate.
    """
    seen = load_seen()
    known_before = set(seen)
    first_run = not seen        # nothing is comparatively new on run one
    stamp = today.isoformat()
    cache = estimate.load_cache()
    rows = []
    for b in bounties:
        seen.setdefault(b.ref, stamp)
        b = b.with_first_seen(seen[b.ref])
        hours, prov = estimate.effort_hours(b, cache=cache, allow_model=allow_model)
        mult, why = score.bonuses(b, today)
        if first_run and "new" in why:
            # Every bounty is "first seen" on the first run. Awarding the
            # freshness bonus to all of them ranks nothing and reads as noise.
            why = [w for w in why if w != "new"]
            mult /= score.FRESH_BONUS
        money = score.money_score(b, hours, today)
        rows.append({
            "b": b, "hours": hours, "prov": prov, "why": why,
            # money is the real rate and is what gets printed; rank carries the
            # freshness and stack multipliers and is only ever sorted on.
            "money": money,
            "money_rank": None if money is None else money * mult,
            "signal": score.signal_score(b, today) * mult,
        })
    save_seen(seen)
    estimate.save_cache(cache)
    return rows, known_before


def _line(r) -> str:
    b = r["b"]
    amt = f"${b.amount_usd:,}" if b.amount_usd is not None else "unpriced"
    money = f"${r['money']:,.0f}/h" if r["money"] is not None else "—"
    flags = " ".join(f"[{w}]" for w in r["why"])
    star = "*" if r["prov"] != estimate.STATED else " "
    return (f"  {amt:>9}{star} {money:>9}  comp={b.competitors:<3} "
            f"{b.ref:<22} {b.title[:52]}{(' ' + flags) if flags else ''}")


def report(rows, warnings, owned, today) -> str:
    reachable = [r for r in rows if r["b"].hardware in owned]
    gated = [r for r in rows if r["b"].hardware not in owned
             and r["b"].hardware != HARDWARE_UNKNOWN]
    unknown = [r for r in rows if r["b"].hardware == HARDWARE_UNKNOWN]

    L = [f"# bounty scout — {today.isoformat()}", ""]
    if warnings:
        L.append("## Warnings")
        L += [f"- {w}" for w in warnings]
        L.append("")

    claimable = [r for r in reachable if score.is_claimable(r["b"], today)]
    priced = [r for r in claimable if r["money"] is not None]
    L.append(f"{len(rows)} bounties across {len(ADAPTERS)} boards · "
             f"{len(reachable)} reachable · {len(claimable)} claimable now · "
             f"{len(gated)} hardware-gated · {len(unknown)} gate unknown")
    L.append("")

    L.append("## MONEY — expected $/hour, discounted by who is already in")
    if priced:
        for r in sorted(priced, key=lambda r: -r["money_rank"])[:12]:
            L.append(_line(r))
    else:
        L.append("  (nothing priced and claimable)")
    unpriced = [r for r in claimable if r["money"] is None]
    if unpriced:
        L.append(f"  … plus {len(unpriced)} unpriced (price lives off-issue; "
                 f"not ranked here)")
    L.append("")

    L.append("## SIGNAL — what a merged PR here is worth as a hiring credential")
    for r in sorted(claimable, key=lambda r: -r["signal"])[:12]:
        L.append(_line(r))
    L.append("")

    lapsed = [r for r in rows if score.lock_is_stale(r["b"], today)]
    if lapsed:
        L.append("## LOCK LAPSED — reads as taken, is not")
        for r in sorted(lapsed, key=lambda r: -(r["b"].amount_usd or 0)):
            L.append(_line(r))
        L.append("")

    if unknown:
        L.append(f"## GATE UNKNOWN — {len(unknown)} bounties whose hardware "
                 f"requirement could not be read")
        for r in sorted(unknown, key=lambda r: -(r["b"].amount_usd or 0))[:8]:
            L.append(_line(r))
        L.append("")

    L.append("`*` = effort estimated, not published by the board.")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--all-hardware", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-model", action="store_true")
    ap.add_argument("--new-only", action="store_true",
                    help="report only bounties first seen this run; exit 3 when "
                         "there are none. Intended for a scheduled loop, where "
                         "the whole board every run is noise and a newly posted, "
                         "uncontested bounty is the only thing worth waking for.")
    args = ap.parse_args()

    today = dt.date.today()
    owned = ALL_HARDWARE if args.all_hardware else set(DEFAULT_OWNED)

    warnings = []
    bounties = collect(warnings)
    if not bounties:
        print("every board failed to fetch — refusing to report an empty queue",
              file=sys.stderr)
        for w in warnings:
            print(f"  {w}", file=sys.stderr)
        return 1

    rows, known_before = enrich(bounties, today, allow_model=not args.no_model)

    if args.json:
        print(json.dumps([{
            "source": r["b"].source, "ref": r["b"].ref, "title": r["b"].title,
            "url": r["b"].url, "amount_usd": r["b"].amount_usd,
            "amount_is_stated": r["b"].amount_is_stated,
            "hardware": r["b"].hardware, "status": r["b"].status,
            "competitors": r["b"].competitors, "hours": r["hours"],
            "effort_provenance": r["prov"], "money_per_hour": r["money"],
            "signal": r["signal"], "first_seen": r["b"].first_seen,
        } for r in rows], indent=2))
        return 0

    if args.new_only:
        fresh = [r for r in rows if r["b"].ref not in known_before]
        if not fresh:
            print(f"no new bounties across {len(ADAPTERS)} boards "
                  f"({len(rows)} tracked, unchanged)")
            return 3
        rows = fresh

    text = report(rows, warnings, owned, today)
    print(text)
    RUNS.mkdir(parents=True, exist_ok=True)
    log = RUNS / f"{today.isoformat()}-{dt.datetime.now():%H%M}.md"
    log.write_text(text + "\n")
    print(f"\nrun log: {log.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
