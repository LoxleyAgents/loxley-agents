"""Paper trading: positions, exits and a journal.

Nothing here signs anything or moves any funds. An entry is a row in a JSON
file. Prices come from the same swap logs the scanner reads, so a position is
marked against real trades — but the fill itself is assumed, and a real fill
would face slippage, the snipe tax and the creator fee this model ignores.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .rules import Agent, Decision


@dataclass
class Position:
    agent: str
    token: str
    quote: str
    entry_price: float
    opened_at: float
    take_profit: float
    stop_loss: float
    max_hold: int
    exit_price: float | None = None
    closed_at: float | None = None
    exit_kind: str | None = None      # "take profit" | "stop loss" | "time exit"

    @property
    def open(self) -> bool:
        return self.closed_at is None

    def pnl_pct(self, price: float | None = None) -> float:
        mark = price if price is not None else (self.exit_price or self.entry_price)
        if not self.entry_price:
            return 0.0
        return (mark / self.entry_price - 1) * 100

    def held_minutes(self, now: float | None = None) -> float:
        return ((now or time.time()) - self.opened_at) / 60

    def check(self, price: float, now: float | None = None) -> str | None:
        """Would this position close at that price? Returns the exit kind."""
        now = now or time.time()
        move = self.pnl_pct(price)
        if move >= self.take_profit:
            return "take profit"
        if move <= -self.stop_loss:
            return "stop loss"
        if self.held_minutes(now) >= self.max_hold:
            return "time exit"
        return None


@dataclass
class Book:
    """Every position an agent has taken, open and closed."""

    path: Path
    positions: list[Position] = field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path) -> "Book":
        p = Path(path)
        if not p.exists():
            return cls(path=p)
        raw = json.loads(p.read_text())
        return cls(path=p, positions=[Position(**r) for r in raw])

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps([asdict(p) for p in self.positions], indent=2))

    # --- the two things a run does ---------------------------------------

    def enter(self, decision: Decision, agent: Agent, now: float | None = None) -> Position:
        pos = Position(
            agent=decision.agent,
            token=decision.token,
            quote=decision.quote,
            entry_price=decision.price,
            opened_at=now or time.time(),
            take_profit=agent.take_profit,
            stop_loss=agent.stop_loss,
            max_hold=agent.max_hold,
        )
        self.positions.append(pos)
        return pos

    def mark(self, token: str, price: float, now: float | None = None) -> list[Position]:
        """Mark open positions in a token and close the ones that hit a rule."""
        closed = []
        for pos in self.positions:
            if not pos.open or pos.token.lower() != token.lower():
                continue
            kind = pos.check(price, now)
            if kind:
                pos.exit_price = price
                pos.closed_at = now or time.time()
                pos.exit_kind = kind
                closed.append(pos)
        return closed

    def holding(self, agent: str, token: str) -> bool:
        return any(p.open and p.agent == agent and p.token.lower() == token.lower()
                   for p in self.positions)

    def entries_today(self, agent: str, now: float | None = None) -> int:
        cutoff = (now or time.time()) - 86400
        return sum(1 for p in self.positions if p.agent == agent and p.opened_at >= cutoff)

    def summary(self, agent: str | None = None) -> dict:
        rows = [p for p in self.positions if agent is None or p.agent == agent]
        closed = [p for p in rows if not p.open]
        wins = [p for p in closed if p.pnl_pct() > 0]
        return {
            "positions": len(rows),
            "open": sum(1 for p in rows if p.open),
            "closed": len(closed),
            "win_rate": round(len(wins) / len(closed) * 100, 1) if closed else None,
            "best": round(max((p.pnl_pct() for p in closed), default=0), 1),
            "worst": round(min((p.pnl_pct() for p in closed), default=0), 1),
        }
