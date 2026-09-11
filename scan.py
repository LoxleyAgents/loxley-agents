#!/usr/bin/env python3
"""Run the agents over Robinhood Chain and record what they would have done.

    export RPC_URL=https://your-node.example
    python3 scan.py --blocks 4000 --limit 10

Every entry is on paper. Nothing is signed, no key is read, no funds move.
"""

from __future__ import annotations

import argparse
import time

from loxley.erc20 import Erc20
from loxley.market import recent_launches, stats_for
from loxley.paper import Book
from loxley.rules import AGENTS, screen
from loxley.rpc import Rpc

def block_seconds(rpc: Rpc, head: int, sample: int = 5000) -> float:
    """Measure the chain's block time instead of assuming it.

    Robinhood Chain runs near a tenth of a second a block, so a hardcoded
    guess of one or two seconds misreads a token's age by more than an order
    of magnitude — and age is what the `listed too long ago` refusal turns on.
    """
    lo = max(1, head - sample)
    first = int(rpc.block(lo)["timestamp"], 16)
    last = int(rpc.block(head)["timestamp"], 16)
    span = max(1, head - lo)
    return max((last - first) / span, 1e-6)


def main() -> None:
    ap = argparse.ArgumentParser(description="Scan Robinhood Chain with the Loxley agents")
    ap.add_argument("--blocks", type=int, default=4000, help="how far back to look")
    ap.add_argument("--limit", type=int, default=10, help="how many tokens to screen")
    ap.add_argument("--agent", help="only run one agent (tuck, marian, sheriff, robin, john, scarlet)")
    ap.add_argument("--book", default="book.json", help="where the paper journal lives")
    args = ap.parse_args()

    agents = {args.agent: AGENTS[args.agent]} if args.agent else AGENTS
    if args.agent and args.agent not in AGENTS:
        raise SystemExit(f"unknown agent {args.agent!r}; pick from {', '.join(AGENTS)}")

    rpc = Rpc()
    if rpc.chain_id() != 4663:
        raise SystemExit(f"RPC_URL points at chain {rpc.chain_id()}, not Robinhood Chain (4663)")

    head = rpc.block_number()
    book = Book.load(args.book)
    now = time.time()
    secs_per_block = block_seconds(rpc, head)

    launches = recent_launches(rpc, blocks=args.blocks, head=head)
    window_min = args.blocks * secs_per_block / 60
    print(f"block {head:,} · {len(launches)} launches in the last {args.blocks:,} blocks "
          f"({window_min:,.1f} min at {secs_per_block:.3f}s/block)")
    print(f"screening up to {args.limit} of them with {len(agents)} agent(s)\n")

    screened = entered = 0
    for launch in reversed(launches):
        if screened >= args.limit:
            break

        market, phase = stats_for(rpc, launch, blocks=args.blocks, head=head)
        if market is None:
            continue
        screened += 1

        token = Erc20(rpc, launch.token)
        symbol = token.symbol() or launch.token[:10]
        quote = (Erc20(rpc, launch.quote).symbol() if int(launch.quote, 16) else "native") or "?"
        age = (head - launch.block) * secs_per_block / 60

        print(f"{symbol:<14} {phase:<10} vol {market.quote_volume:>10,.2f} {quote:<7}"
              f" dip {market.dip_from_high_pct:5.1f}%  {market.trades} trades")

        for closed in book.mark(launch.token, market.last_price, now):
            print(f"     ← {closed.agent} closed on {closed.exit_kind}: "
                  f"{closed.pnl_pct():+.1f}% after {closed.held_minutes(now):,.0f}m")

        for key, agent in agents.items():
            decision = screen(
                agent, market, quote_symbol=quote, age_minutes=age,
                holding=book.holding(key, launch.token),
                entries_today=book.entries_today(key, now),
            )
            if decision.passed:
                book.enter(decision, agent, now)
                entered += 1
                print(f"     → {key} ENTER at {decision.price:.3e} · {decision.note}")
            else:
                print(f"     · {key} declines — {decision.reason}"
                      f"{'  (' + decision.note + ')' if decision.note else ''}")
        print()

    book.save()
    print(f"screened {screened} · entries this run {entered} · journal {args.book}")
    for key in agents:
        s = book.summary(key)
        if s["positions"]:
            print(f"  {key:<8} {s}")


if __name__ == "__main__":
    main()
