"""The six agents, and the only reasons any of them will decline a token.

The thresholds here are the same numbers the site publishes for each agent. If
you change one, change it in both places — a rule that reads differently in the
code than on the page is worse than no rule at all.

A refusal is always a statement about our own thresholds. It is never a claim
about the token, its deployer or anyone's intent: this code reads public swap
logs, which cannot tell you that.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path

from .market import Market

# The six refusals, verbatim. Nothing else may be returned as a reason.
BELOW_VOLUME = "below volume floor"
OUTSIDE_MCAP = "outside mcap range"
SHALLOW_DIP = "dip not deep enough"
TOO_OLD = "listed too long ago"
IN_POSITION = "already in position"
CAP_REACHED = "daily cap reached"

REASONS = [BELOW_VOLUME, OUTSIDE_MCAP, SHALLOW_DIP, TOO_OLD, IN_POSITION, CAP_REACHED]


@dataclass(frozen=True)
class Agent:
    key: str
    name: str
    job: str
    min_volume: float   # in the pool's quote asset — see the note in screen()
    dip: float          # how far below the window high an entry has to be, %
    take_profit: float  # %
    stop_loss: float    # %
    max_hold: int       # minutes
    daily_cap: int      # entries per day


def load_overrides(path: str | Path = "agents.json") -> dict:
    """Thresholds are defaults, not truths.

    What counts as thin changes with the market — a floor that is selective on
    a busy day refuses everything on a quiet one. Drop an agents.json next to
    the script to move any field without touching this file:

        {"sheriff": {"min_volume": 40}, "scarlet": {"dip": 8}}
    """
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def apply_overrides(agents: dict[str, "Agent"], overrides: dict) -> dict[str, "Agent"]:
    out = dict(agents)
    for key, fields in (overrides or {}).items():
        if key not in out or not isinstance(fields, dict):
            continue
        out[key] = replace(out[key], **{k: v for k, v in fields.items()
                                        if k in out[key].__dataclass_fields__})
    return out


AGENTS: dict[str, Agent] = {
    "tuck": Agent("tuck", "Friar Tuck", "Takes profit early and parks it.",
                  600, 8, 14, 10, 1440, 4),
    "marian": Agent("marian", "Maid Marian", "Tight stop, capital before profit.",
                    350, 10, 20, 8, 180, 16),
    "sheriff": Agent("sheriff", "The Sheriff", "The hardest screen of the six.",
                     700, 18, 26, 9, 240, 3),
    "robin": Agent("robin", "Robin Hood", "The core of the band.",
                   250, 12, 35, 15, 360, 24),
    "john": Agent("john", "Little John", "Builds patiently into deep dips.",
                  400, 22, 40, 18, 720, 6),
    "scarlet": Agent("scarlet", "Will Scarlet", "Fast in, fast out.",
                     80, 15, 35, 12, 30, 48),
}

# an agents.json sitting next to the script wins over the defaults above
AGENTS = apply_overrides(AGENTS, load_overrides())


@dataclass
class Decision:
    agent: str
    token: str
    passed: bool
    reason: str | None
    price: float
    quote: str
    note: str = ""

    def __str__(self) -> str:
        verdict = "ENTER" if self.passed else f"pass — {self.reason}"
        return f"{self.agent:<8} {self.token[:10]}… {verdict}"


def screen(agent: Agent, market: Market, *, quote_symbol: str,
           age_minutes: float | None = None, holding: bool = False,
           entries_today: int = 0) -> Decision:
    """Run one agent's thresholds against one token's window.

    On the volume floor: chain volume is denominated in whatever the pool is
    quoted in — native, GOOGL, NVDA, SGOV and more all appear. Those are not
    the same unit and this code has no price feed to convert them, so the floor
    is only meaningful against tokens sharing a quote. The quote is carried on
    every decision so a caller can group before comparing, rather than pretend
    one number ranks them all.
    """
    out = lambda passed, reason, note="": Decision(  # noqa: E731
        agent=agent.key, token=market.token, passed=passed, reason=reason,
        price=market.last_price, quote=quote_symbol, note=note,
    )

    if holding:
        return out(False, IN_POSITION)

    if entries_today >= agent.daily_cap:
        return out(False, CAP_REACHED, f"{entries_today}/{agent.daily_cap}")

    if market.quote_volume < agent.min_volume:
        return out(False, BELOW_VOLUME,
                   f"{market.quote_volume:,.2f} < {agent.min_volume:,g} {quote_symbol}")

    if age_minutes is not None and age_minutes > agent.max_hold * 4:
        return out(False, TOO_OLD, f"{age_minutes:,.0f}m old")

    if market.dip_from_high_pct < agent.dip:
        return out(False, SHALLOW_DIP,
                   f"{market.dip_from_high_pct:.1f}% < {agent.dip}%")

    return out(True, None,
               f"dip {market.dip_from_high_pct:.1f}% · tp {agent.take_profit}% · "
               f"sl {agent.stop_loss}% · max {agent.max_hold}m")


def screen_all(market: Market, *, quote_symbol: str,
               age_minutes: float | None = None) -> list[Decision]:
    """What each of the six would do with the same token, right now."""
    return [screen(a, market, quote_symbol=quote_symbol, age_minutes=age_minutes)
            for a in AGENTS.values()]
