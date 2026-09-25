"""Portafoglio: strategie incluse + combinazione cronologica dei loro trade."""

from __future__ import annotations

from dataclasses import dataclass

from .metrics import compute_stats
from .models import LoadedStrategy, TradeSet

COMBINED_KEY = "combined"
COMBINED_NAME = "Portafoglio combinato"


@dataclass
class Entity:
    """Una serie analizzabile: una singola strategia o il portafoglio combinato."""

    key: str
    name: str
    trades: TradeSet
    color_index: int | None  # None = portafoglio combinato (nero)
    stats: dict

    @property
    def is_combined(self) -> bool:
        return self.key == COMBINED_KEY


class Portfolio:
    def __init__(self, strategies: list[LoadedStrategy], capital: float = 0.0):
        self.capital = float(capital)
        self.included = [s for s in strategies if s.included and len(s.trades) > 0]
        self.strategies: list[Entity] = []
        for s in self.included:
            ts = s.labeled_trades()
            self.strategies.append(Entity(str(s.uid), s.name, ts, s.color_index, compute_stats(ts, self.capital)))
        self.combined: Entity | None = None
        if len(self.strategies) >= 2:
            ts = TradeSet.combine([e.trades for e in self.strategies])
            self.combined = Entity(COMBINED_KEY, COMBINED_NAME, ts, None, compute_stats(ts, self.capital))

    @property
    def empty(self) -> bool:
        return not self.strategies

    @property
    def primary(self) -> Entity | None:
        """Il portafoglio combinato se ci sono almeno 2 strategie, altrimenti l'unica strategia."""
        if self.combined is not None:
            return self.combined
        return self.strategies[0] if self.strategies else None

    def entities(self) -> list[Entity]:
        return ([self.combined] if self.combined else []) + self.strategies

    def entity(self, key: str) -> Entity | None:
        for e in self.entities():
            if e.key == key:
                return e
        return None
